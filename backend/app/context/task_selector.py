"""Relevance selection for task and deadline context.

Only structured tasks owned by the requesting user are considered. Tasks with a
deadline that is overdue, due today/tomorrow, or upcoming are owned by the
deadline section; the task section covers actionable, high-priority, and
explicitly mentioned tasks. This keeps the two prompt sections separate and
prevents duplicate task information.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.task import Task, TaskCategory, TaskPriority, TaskStatus
from app.services.task_deadlines import (
    RELEVANT_STATES,
    STATE_RANK,
    DeadlineState,
    classify_deadline,
    deadline_instant,
    utcnow as _utcnow,
)
from app.services.task_service import task_service


_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "about",
    "what", "when", "where", "which", "while", "have", "has", "had", "are",
    "was", "were", "will", "would", "could", "should", "your", "yours",
    "our", "ours", "their", "them", "they", "you", "can", "may", "need",
    "today", "tomorrow", "please", "task", "tasks", "deadline", "deadlines",
    "show", "list", "tell", "give", "get", "make", "help", "doing", "done",
})

# Deterministic category hints used when a query clearly asks about a domain.
# A match makes an otherwise medium/low-priority open task relevant without
# injecting every stored task into the prompt.
CATEGORY_INTENT_KEYWORDS = {
    TaskCategory.COLLEGE: frozenset({
        "study", "studies", "studying", "assignment", "assignments",
        "homework", "exam", "exams", "test", "tests", "quiz", "quizzes",
        "class", "classes", "lecture", "lectures", "college", "university",
        "campus", "semester", "academic", "course", "courses", "thesis",
        "dissertation", "revision", "revise", "learn", "learning", "research",
    }),
    TaskCategory.CAREER: frozenset({
        "career", "job", "jobs", "interview", "interviews", "resume", "cv",
        "internship", "internships", "professional", "promotion", "application",
        "applications", "employment", "recruiter", "portfolio",
    }),
    TaskCategory.PROJECT: frozenset({
        "project", "projects", "build", "building", "implement",
        "implementing", "implementation", "develop", "developing",
        "development", "feature", "features", "bug", "bugs", "debug",
        "deploy", "deployment", "release", "codebase", "repository",
        "sprint", "milestone", "working",
    }),
    TaskCategory.PERSONAL: frozenset({
        "personal", "home", "family", "shopping", "groceries", "grocery",
        "errand", "errands", "bill", "bills", "appointment", "appointments",
        "birthday", "cleaning", "laundry",
    }),
    TaskCategory.FITNESS: frozenset({
        "fitness", "gym", "workout", "workouts", "exercise", "exercising",
        "run", "running", "jog", "jogging", "health", "training", "diet",
        "yoga", "swim", "swimming",
    }),
}


@dataclass(frozen=True)
class DeadlineContextItem:
    """A deadline-bearing task selected for the deadline prompt section."""

    task: Task
    state: DeadlineState
    deadline_at: datetime
    relevance: float


def _tokens(value: str | None) -> set[str]:
    return {
        token
        for token in _TOKEN_PATTERN.findall((value or "").casefold())
        if len(token) >= 3 and token not in STOPWORDS
    }


def _infer_category_intents(query_tokens: set[str]) -> set[TaskCategory]:
    """Return task categories clearly implied by the current query."""

    return {
        category
        for category, keywords in CATEGORY_INTENT_KEYWORDS.items()
        if query_tokens & keywords
    }


def _enum_value(value) -> str:
    return str(getattr(value, "value", value))


def _is_mentioned(task: Task, query_tokens: set[str]) -> bool:
    if not query_tokens:
        return False

    task_tokens = _tokens(
        f"{task.title} {task.description or ''}"
    )

    return bool(query_tokens & task_tokens)


def _priority_score(priority: TaskPriority) -> float:
    return {
        TaskPriority.URGENT: 4.0,
        TaskPriority.HIGH: 3.0,
        TaskPriority.MEDIUM: 1.0,
        TaskPriority.LOW: 0.0,
    }.get(priority, 0.0)


def _state_score(state: DeadlineState) -> float:
    return {
        DeadlineState.OVERDUE: 1.0,
        DeadlineState.TODAY: 0.9,
        DeadlineState.TOMORROW: 0.8,
        DeadlineState.UPCOMING: 0.6,
    }.get(state, 0.3)


def _positive_limit(value: int | None, fallback: int) -> int:
    try:
        chosen = int(value if value is not None else fallback)

    except (TypeError, ValueError):
        chosen = fallback

    return max(1, min(chosen, 20))


async def select_deadline_items(
    session: AsyncSession,
    user_id: int,
    query: str,
    now: datetime | None = None,
    horizon_days: int | None = None,
    limit: int | None = None,
) -> list[DeadlineContextItem]:
    """Select overdue, today, tomorrow, and upcoming deadlines."""

    current = now or _utcnow()
    horizon = int(
        horizon_days
        if horizon_days is not None
        else settings.TASK_CONTEXT_UPCOMING_DAYS
    )
    max_items = _positive_limit(limit, settings.DEADLINE_CONTEXT_LIMIT)

    tasks = await task_service.get_open_tasks(
        session=session,
        user_id=user_id,
    )

    query_tokens = _tokens(query)
    category_intents = _infer_category_intents(query_tokens)
    items: list[DeadlineContextItem] = []

    for task in tasks:
        if task.due_date is None:
            continue

        state = classify_deadline(task, current, horizon)

        if state not in RELEVANT_STATES:
            continue

        instant = deadline_instant(task)

        if instant is None:
            continue

        relevance = _state_score(state)

        if _is_mentioned(task, query_tokens):
            relevance = min(1.0, relevance + 0.2)

        if task.category in category_intents:
            relevance = min(1.0, relevance + 0.1)

        items.append(
            DeadlineContextItem(
                task=task,
                state=state,
                deadline_at=instant,
                relevance=relevance,
            )
        )

    items.sort(
        key=lambda item: (
            STATE_RANK[item.state],
            item.deadline_at,
            item.task.id,
        )
    )

    return items[:max_items]


async def select_task_items(
    session: AsyncSession,
    user_id: int,
    query: str,
    now: datetime | None = None,
    horizon_days: int | None = None,
    limit: int | None = None,
) -> list[tuple[Task, float]]:
    """Select actionable, relevant tasks for the task prompt section.

    Deadline-bearing tasks in a relevant deadline state are deliberately
    excluded because the deadline provider owns them.
    """

    current = now or _utcnow()
    horizon = int(
        horizon_days
        if horizon_days is not None
        else settings.TASK_CONTEXT_UPCOMING_DAYS
    )
    max_items = _positive_limit(limit, settings.TASK_CONTEXT_LIMIT)

    tasks = await task_service.get_open_tasks(
        session=session,
        user_id=user_id,
    )

    query_tokens = _tokens(query)
    category_intents = _infer_category_intents(query_tokens)
    selected: list[tuple[Task, float]] = []

    for task in tasks:
        mentioned = _is_mentioned(task, query_tokens)
        category_match = task.category in category_intents

        if task.due_date is not None:
            state = classify_deadline(task, current, horizon)

            if state in RELEVANT_STATES:
                continue

        is_high_priority = task.priority in {
            TaskPriority.URGENT,
            TaskPriority.HIGH,
        }

        if not (
            mentioned
            or is_high_priority
            or task.status == TaskStatus.IN_PROGRESS
            or category_match
        ):
            continue

        relevance = _priority_score(task.priority)

        if mentioned:
            relevance += 10.0

        if task.status == TaskStatus.IN_PROGRESS:
            relevance += 2.0

        if category_match:
            relevance += 2.5

        selected.append((task, relevance))

    selected.sort(key=lambda item: (-item[1], item[0].id))

    return selected[:max_items]


def render_task(task: Task) -> str:
    """Render a task as one safe, concise context line."""

    parts = [
        f"Task: {task.title}",
        f"category: {_enum_value(task.category)}",
        f"priority: {_enum_value(task.priority)}",
        f"status: {_enum_value(task.status)}",
    ]

    if task.due_date is not None:
        due = task.due_date.isoformat()

        if task.due_time is not None:
            due += " " + task.due_time.isoformat(timespec="minutes")

        parts.append(f"due: {due}")

    return " | ".join(parts)


def render_deadline(item: DeadlineContextItem) -> str:
    """Render a deadline as one safe, concise context line."""

    task = item.task
    due = task.due_date.isoformat() if task.due_date else "unknown"

    if task.due_time is None:
        due += " (date only, end of local day)"
    else:
        due += " " + task.due_time.isoformat(timespec="minutes")

    parts = [
        f"Task: {task.title}",
        f"due: {due}",
        f"timezone: {task.timezone or 'UTC'}",
        f"priority: {_enum_value(task.priority)}",
        f"status: {_enum_value(task.status)}",
        f"deadline_state: {item.state.value}",
    ]

    return " | ".join(parts)
