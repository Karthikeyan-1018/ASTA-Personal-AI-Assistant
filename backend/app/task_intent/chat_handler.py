"""Chat-integrated natural-language task management.

The handler returns a ``ChatResponse``-compatible dictionary when it handles a
message, or ``None`` so the normal context/LLM pipeline can continue unchanged.
It never mutates a task directly: every mutation becomes a pending action that
the user must explicitly confirm in a later turn (or through the task-actions
API).
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.exceptions.task_exceptions import (
    TaskNotFoundError,
    TaskValidationError,
)
from app.exceptions.task_intent_exceptions import (
    PendingActionAlreadyResolvedError,
    PendingActionExpiredError,
    PendingActionNotFoundError,
)
from app.models.task import Task, TaskCategory, TaskPriority, TaskStatus
from app.models.task_action import (
    PendingActionType,
    TaskPendingAction,
)
from app.services.task_service import task_service
from app.task_intent import clock
from app.task_intent.detector import (
    is_confirmation,
    is_rejection,
    task_intent_detector,
)
from app.task_intent.enums import QUERY_INTENTS, TaskIntent
from app.task_intent.executor import task_action_executor
from app.task_intent.pending_service import pending_action_service
from app.task_intent.schemas import TaskChanges, TaskDraft, TaskFilters


logger = logging.getLogger(__name__)

PROVIDER = "task_engine"
MODEL = "deterministic"

_CONFIRM_HINT = 'Reply "yes" to confirm or "no" to cancel.'
_STOPWORDS = {
    "a",
    "an",
    "the",
    "my",
    "our",
    "task",
    "tasks",
    "to",
    "for",
    "of",
    "please",
}


def _clip(value: str, limit: int = 80) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()

    if len(cleaned) <= limit:
        return cleaned

    return cleaned[: limit - 1].rstrip() + "…"


def _format_deadline(value: date | None, due_time: time | None,
                     timezone_name: str | None) -> str | None:
    if value is None:
        return None

    text = value.isoformat()

    if due_time is not None:
        text += f" at {due_time.strftime('%H:%M')}"

        if timezone_name:
            text += f" {timezone_name}"

    return text


def _format_task_line(task: Task) -> str:
    parts = [
        f"- {_clip(task.title)} (id {task.id})",
        f"status {task.status.value}",
        f"{task.priority.value} priority",
        f"{task.category.value}",
    ]

    deadline = _format_deadline(
        task.due_date,
        task.due_time,
        task.timezone,
    )

    if deadline:
        parts.append(f"due {deadline}")

    if task.status == TaskStatus.COMPLETED and task.completed_at:
        parts.append("completed")

    return "; ".join(parts)


def _format_draft(draft: TaskDraft) -> str:
    lines = [
        "I understood the following task:",
        "",
        f"Title: {draft.title}",
        f"Category: {draft.category.value.title()}",
        f"Priority: {draft.priority.value.title()}",
    ]

    deadline = _format_deadline(
        draft.due_date,
        draft.due_time,
        draft.timezone,
    )

    if deadline:
        lines.append(f"Deadline: {deadline}")
    else:
        lines.append("Deadline: none")

    if draft.description:
        lines.append(f"Description: {_clip(draft.description, 200)}")

    return "\n".join(lines)


def _format_changes(changes: TaskChanges) -> list[str]:
    lines: list[str] = []

    if changes.title is not None:
        lines.append(f"- Title: {_clip(changes.title)}")

    if changes.description is not None:
        lines.append(f"- Description: {_clip(changes.description, 200)}")

    if changes.category is not None:
        lines.append(f"- Category: {changes.category.value.title()}")

    if changes.priority is not None:
        lines.append(f"- Priority: {changes.priority.value.title()}")

    if changes.due_date is not None:
        deadline = _format_deadline(
            changes.due_date,
            changes.due_time,
            changes.timezone,
        )
        lines.append(f"- Deadline: {deadline}")

    return lines


def _normalise_hint(hint: str) -> str:
    return re.sub(r"\s+", " ", hint.lower()).strip()


def _hint_tokens(hint: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", hint.lower())
        if token not in _STOPWORDS
    ]


class TaskIntentChatHandler:
    """Route task-related chat turns before the normal LLM pipeline."""

    def _reply(self, text: str) -> dict:
        return {
            "provider": PROVIDER,
            "model": MODEL,
            "response": text,
        }

    async def handle(
        self,
        session: AsyncSession,
        user_id: int,
        conversation,
        message: str,
        private: bool = False,
    ) -> dict | None:

        if not settings.TASK_INTENT_ENABLED:
            return None

        now = clock.utcnow()
        active = await pending_action_service.get_active(
            session=session,
            user_id=user_id,
            conversation_id=conversation.id,
        )

        if active is not None:
            if is_confirmation(message):
                return await self._confirm(
                    session=session,
                    user_id=user_id,
                    action=active,
                )

            if is_rejection(message):
                return await self._reject(
                    session=session,
                    user_id=user_id,
                    action=active,
                )

            # Any other message supersedes the stale proposal. A later "yes"
            # can never confirm an action the user has moved on from.
            await pending_action_service.supersede_active(
                session=session,
                user_id=user_id,
                conversation_id=conversation.id,
            )

        result = await task_intent_detector.detect(
            message,
            private=private,
            now=now,
            default_timezone=settings.TASK_DEFAULT_TIMEZONE or None,
        )

        if result.intent == TaskIntent.NO_TASK_ACTION:
            if result.invalid_reason:
                return self._reply(result.invalid_reason)

            if result.clarification:
                return self._reply(result.clarification)

            return None

        if result.invalid_reason:
            return self._reply(result.invalid_reason)

        if result.clarification:
            return self._reply(result.clarification)

        if result.intent in QUERY_INTENTS:
            return await self._handle_query(
                session=session,
                user_id=user_id,
                result=result,
            )

        return await self._handle_mutation(
            session=session,
            user_id=user_id,
            conversation=conversation,
            result=result,
            now=now,
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def _handle_query(
        self,
        session: AsyncSession,
        user_id: int,
        result,
    ) -> dict:

        limit = max(1, min(int(settings.TASK_INTENT_MAX_RESULTS), 100))

        if result.intent == TaskIntent.UPCOMING_TASKS:
            tasks = await task_service.get_upcoming(
                session=session,
                user_id=user_id,
                limit=limit,
            )
            return self._reply(self._list_reply(
                "Here are your upcoming deadlines:",
                tasks,
                "You have no upcoming deadlines.",
            ))

        if result.intent == TaskIntent.OVERDUE_TASKS:
            tasks = await task_service.get_overdue(
                session=session,
                user_id=user_id,
                limit=limit,
            )
            return self._reply(self._list_reply(
                "Here are your overdue tasks:",
                tasks,
                "You have no overdue tasks.",
            ))

        if result.intent == TaskIntent.TODAY_TASKS:
            today = await task_service.get_due_today(
                session=session,
                user_id=user_id,
                limit=limit,
            )
            overdue = await task_service.get_overdue(
                session=session,
                user_id=user_id,
                limit=limit,
            )

            sections = []

            sections.append(self._list_reply(
                "Tasks due today:",
                today,
                "Nothing is due today.",
            ))

            if overdue:
                sections.append(self._list_reply(
                    "Overdue tasks:",
                    overdue,
                    "",
                ))

            return self._reply("\n\n".join(sections))

        if result.intent == TaskIntent.TOMORROW_TASKS:
            tasks = await task_service.get_due_tomorrow(
                session=session,
                user_id=user_id,
                limit=limit,
            )
            return self._reply(self._list_reply(
                "Tasks due tomorrow:",
                tasks,
                "Nothing is due tomorrow.",
            ))

        if result.intent == TaskIntent.LIST_TASKS:
            tasks = await self._list_filtered(
                session=session,
                user_id=user_id,
                filters=result.filters or TaskFilters(),
                limit=limit,
            )
            return self._reply(self._list_reply(
                "Here are your tasks:",
                tasks,
                "No tasks match those filters.",
            ))

        # GET_TASK and TASK_STATUS_QUERY
        return await self._status_reply(
            session=session,
            user_id=user_id,
            hint=result.target_hint,
        )

    async def _list_filtered(
        self,
        session: AsyncSession,
        user_id: int,
        filters: TaskFilters,
        limit: int,
    ) -> list[Task]:

        if filters.status is not None:
            tasks, _ = await task_service.list_tasks(
                session=session,
                user_id=user_id,
                category=filters.category,
                priority=filters.priority,
                status=filters.status,
                limit=limit,
                sort_by="due_date",
                sort_order="asc",
            )
            return tasks

        tasks = await task_service.get_open_tasks(
            session=session,
            user_id=user_id,
        )

        if filters.category is not None:
            tasks = [
                task for task in tasks if task.category == filters.category
            ]

        if filters.priority is not None:
            tasks = [
                task for task in tasks if task.priority == filters.priority
            ]

        tasks.sort(
            key=lambda task: (
                task.due_date is None,
                task.due_date or date.max,
                task.id,
            )
        )

        return tasks[:limit]

    async def _status_reply(
        self,
        session: AsyncSession,
        user_id: int,
        hint: str | None,
    ) -> dict:

        if not hint:
            return self._reply(
                "Which task would you like me to check? "
                "Please include its title."
            )

        tasks = await self._all_tasks(session, user_id)
        matches = self._match_tasks(tasks, hint)

        if not matches:
            return self._reply(
                f"I couldn't find a task matching \"{_clip(hint)}\"."
            )

        if len(matches) > 1:
            return self._reply(self._clarify_multiple(matches))

        task = matches[0]
        deadline = _format_deadline(
            task.due_date,
            task.due_time,
            task.timezone,
        )

        text = (
            f"\"{_clip(task.title)}\" is currently "
            f"{task.status.value.replace('_', ' ')}."
        )

        if deadline:
            text += f" Its deadline is {deadline}."

        if task.status == TaskStatus.COMPLETED and task.completed_at:
            text += " It is marked as completed."

        return self._reply(text)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def _handle_mutation(
        self,
        session: AsyncSession,
        user_id: int,
        conversation,
        result,
        now: datetime,
    ) -> dict:

        if result.intent == TaskIntent.CREATE_TASK:
            draft = result.draft

            if draft is None:
                return self._reply(
                    "I couldn't build a valid task from that request. "
                    "Please include a clear title."
                )

            summary = _format_draft(draft)

            action = await pending_action_service.create_pending(
                session=session,
                user_id=user_id,
                conversation_id=conversation.id,
                action_type=PendingActionType.CREATE,
                payload=draft.model_dump(mode="json"),
                summary=summary,
            )

            return self._reply(
                f"{summary}\n\nWould you like me to create this task? "
                f"{_CONFIRM_HINT}"
            )

        hint = result.target_hint

        if not hint:
            return self._reply(
                "Which task do you mean? Please include its title."
            )

        tasks = await self._all_tasks(session, user_id)
        matches = self._match_tasks(tasks, hint)

        if not matches:
            return self._reply(
                f"I couldn't find a task matching \"{_clip(hint)}\". "
                "Please give the exact title."
            )

        if len(matches) > 1:
            return self._reply(self._clarify_multiple(matches))

        task = matches[0]

        if result.intent == TaskIntent.UPDATE_TASK:
            changes = result.changes

            if changes is None:
                return self._reply(
                    "What would you like to change about that task?"
                )

            lines = _format_changes(changes)

            if not lines:
                return self._reply(
                    "I couldn't determine what to change. Please state the "
                    "new deadline, priority, category, or title."
                )

            summary = (
                f"I found this task:\n\n{_format_task_line(task)}\n\n"
                "Proposed changes:\n" + "\n".join(lines)
            )

            await pending_action_service.create_pending(
                session=session,
                user_id=user_id,
                conversation_id=conversation.id,
                action_type=PendingActionType.UPDATE,
                payload=changes.model_dump(mode="json", exclude_none=True),
                summary=summary,
                target_task_id=task.id,
            )

            return self._reply(
                f"{summary}\n\nWould you like me to apply this change? "
                f"{_CONFIRM_HINT}"
            )

        action_type = {
            TaskIntent.COMPLETE_TASK: PendingActionType.COMPLETE,
            TaskIntent.REOPEN_TASK: PendingActionType.REOPEN,
            TaskIntent.CANCEL_TASK: PendingActionType.CANCEL,
            TaskIntent.DELETE_TASK: PendingActionType.DELETE,
        }.get(result.intent)

        if action_type is None:
            return self._reply("I couldn't understand that task request.")

        summary = f"Target task:\n\n{_format_task_line(task)}"

        payload: dict = {}

        if action_type == PendingActionType.DELETE:
            payload = {"title": task.title}

        await pending_action_service.create_pending(
            session=session,
            user_id=user_id,
            conversation_id=conversation.id,
            action_type=action_type,
            payload=payload,
            summary=summary,
            target_task_id=task.id,
        )

        prompts = {
            PendingActionType.COMPLETE: (
                "Would you like me to mark this task as completed?"
            ),
            PendingActionType.REOPEN: (
                "Would you like me to reopen this task?"
            ),
            PendingActionType.CANCEL: (
                "Would you like me to cancel this task? This cannot be "
                "undone from chat."
            ),
            PendingActionType.DELETE: (
                "Would you like me to permanently delete this task? "
                "This cannot be undone."
            ),
        }

        return self._reply(
            f"{summary}\n\n{prompts[action_type]} {_CONFIRM_HINT}"
        )

    # ------------------------------------------------------------------
    # Confirmation
    # ------------------------------------------------------------------

    async def _confirm(
        self,
        session: AsyncSession,
        user_id: int,
        action: TaskPendingAction,
    ) -> dict:

        try:
            claimed = await pending_action_service.claim(
                session=session,
                user_id=user_id,
                action_id=action.id,
            )

        except PendingActionExpiredError:
            return self._reply(
                "That confirmation has expired. Please send the request "
                "again."
            )

        except PendingActionAlreadyResolvedError:
            return self._reply(
                "That action has already been handled."
            )

        except PendingActionNotFoundError:
            return self._reply(
                "I couldn't find that pending action."
            )

        try:
            task = await task_action_executor.execute(
                session=session,
                user_id=user_id,
                action=claimed,
            )

        except TaskNotFoundError:
            await pending_action_service.mark_failed(
                session=session,
                user_id=user_id,
                action_id=claimed.id,
            )
            return self._reply(
                "That task no longer exists, so I didn't change anything."
            )

        except (TaskValidationError, ValueError) as error:
            await pending_action_service.mark_failed(
                session=session,
                user_id=user_id,
                action_id=claimed.id,
            )
            return self._reply(
                "I couldn't apply that change: "
                f"{_clip(str(error), 200)}"
            )

        except Exception:
            await pending_action_service.mark_failed(
                session=session,
                user_id=user_id,
                action_id=claimed.id,
            )
            logger.exception(
                "Failed to execute a pending task action for user %s.",
                user_id,
            )
            return self._reply(
                "Something went wrong while applying that change. "
                "No partial update was kept."
            )

        await pending_action_service.mark_result(
            session=session,
            user_id=user_id,
            action_id=claimed.id,
            result_task_id=task.id if task is not None else None,
        )

        title = None

        if task is not None:
            title = task.title
        else:
            title = (claimed.payload or {}).get("title")

        label = _clip(title) if title else "the task"

        messages = {
            PendingActionType.CREATE: f"Done. I created \"{label}\".",
            PendingActionType.UPDATE: f"Done. I updated \"{label}\".",
            PendingActionType.COMPLETE: (
                f"Done. I marked \"{label}\" as completed."
            ),
            PendingActionType.REOPEN: f"Done. I reopened \"{label}\".",
            PendingActionType.CANCEL: f"Done. I cancelled \"{label}\".",
            PendingActionType.DELETE: f"Done. I deleted \"{label}\".",
        }

        return self._reply(
            messages[PendingActionType(claimed.action_type)]
        )

    async def _reject(
        self,
        session: AsyncSession,
        user_id: int,
        action: TaskPendingAction,
    ) -> dict:

        try:
            await pending_action_service.reject(
                session=session,
                user_id=user_id,
                action_id=action.id,
            )

        except PendingActionExpiredError:
            return self._reply(
                "That confirmation has already expired."
            )

        except (PendingActionAlreadyResolvedError,
                PendingActionNotFoundError):
            return self._reply(
                "That action has already been handled."
            )

        return self._reply(
            "Okay, I won't make any changes."
        )

    # ------------------------------------------------------------------
    # Task matching
    # ------------------------------------------------------------------

    async def _all_tasks(
        self,
        session: AsyncSession,
        user_id: int,
    ) -> list[Task]:

        tasks, _ = await task_service.list_tasks(
            session=session,
            user_id=user_id,
            limit=100,
            offset=0,
            sort_by="updated_at",
            sort_order="desc",
        )

        return tasks

    def _match_tasks(
        self,
        tasks: list[Task],
        hint: str,
    ) -> list[Task]:

        hint_text = _normalise_hint(hint)
        tokens = _hint_tokens(hint)

        exact = [
            task
            for task in tasks
            if _normalise_hint(task.title) == hint_text
        ]

        if exact:
            return exact

        if not tokens:
            return []

        full = [
            task
            for task in tasks
            if all(
                token in _normalise_hint(task.title)
                for token in tokens
            )
        ]

        if full:
            return full

        return [
            task
            for task in tasks
            if any(
                token in _normalise_hint(task.title)
                for token in tokens
            )
        ]

    def _clarify_multiple(self, tasks: list[Task]) -> str:
        lines = [
            "I found more than one matching task. "
            "Which one did you mean?"
        ]
        lines.extend(_format_task_line(task) for task in tasks[:10])
        lines.append("Please repeat the request with a more specific title.")

        return "\n".join(lines)

    @staticmethod
    def _list_reply(header: str, tasks: list[Task], empty: str) -> str:
        if not tasks:
            return empty

        lines = [header]
        lines.extend(_format_task_line(task) for task in tasks)

        return "\n".join(lines)


task_intent_chat_handler = TaskIntentChatHandler()


__all__ = ["TaskIntentChatHandler", "task_intent_chat_handler"]
