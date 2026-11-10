"""Phase 7D natural-language task management tests.

Covers intent detection, structured extraction, deterministic relative dates,
confirmation-gated mutations, pending-action lifecycle, ownership, privacy, and
the minimal task-action API. External LLM providers are always mocked.
"""

import unittest
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all model tables)
from app.api.dependencies import (
    get_current_user_id,
    get_optional_user_id,
)
from app.core.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.llm.llm_router import llm_router
from app.main import app
from app.memory.memory_retriever import memory_retriever
from app.models.task import Task, TaskCategory, TaskPriority, TaskStatus
from app.models.task_action import (
    PendingActionStatus,
    PendingActionType,
    TaskPendingAction,
)
from app.models.user import User
from app.services.task_service import task_service
from app.task_intent import clock
from app.task_intent.date_resolver import resolve_deadline
from app.task_intent.detector import (
    TaskIntentDetector,
    detect_deterministic,
    is_confirmation,
    is_rejection,
)
from app.task_intent.enums import TaskIntent
from app.task_intent.pending_service import pending_action_service


FIXED_NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Deterministic date resolution
# ---------------------------------------------------------------------------


class DateResolverTests(unittest.TestCase):
    """Relative dates are deterministic, testable, and never guessed."""

    def test_today_tomorrow_and_day_after_tomorrow(self):

        self.assertEqual(
            resolve_deadline("today", FIXED_NOW).due_date,
            date(2026, 11, 9),
        )
        self.assertEqual(
            resolve_deadline("tomorrow", FIXED_NOW).due_date,
            date(2026, 11, 10),
        )
        self.assertEqual(
            resolve_deadline("day after tomorrow", FIXED_NOW).due_date,
            date(2026, 11, 11),
        )

    def test_this_and_next_weekday_rule(self):

        self.assertEqual(
            resolve_deadline("this Friday", FIXED_NOW).due_date,
            date(2026, 11, 13),
        )
        # ``next`` means the same weekday in the following ISO week.
        self.assertEqual(
            resolve_deadline("next Monday", FIXED_NOW).due_date,
            date(2026, 11, 16),
        )
        self.assertEqual(
            resolve_deadline("Friday", FIXED_NOW).due_date,
            date(2026, 11, 13),
        )

    def test_relative_offsets_in_days_and_weeks(self):

        self.assertEqual(
            resolve_deadline("in 3 days", FIXED_NOW).due_date,
            date(2026, 11, 12),
        )
        self.assertEqual(
            resolve_deadline("in two weeks", FIXED_NOW).due_date,
            date(2026, 11, 23),
        )

    def test_iso_and_named_dates(self):

        self.assertEqual(
            resolve_deadline("2026-12-01", FIXED_NOW).due_date,
            date(2026, 12, 1),
        )
        self.assertEqual(
            resolve_deadline("15 November 2026", FIXED_NOW).due_date,
            date(2026, 11, 15),
        )
        self.assertEqual(
            resolve_deadline("November 20, 2026", FIXED_NOW).due_date,
            date(2026, 11, 20),
        )

    def test_date_only_has_no_time_or_timezone(self):

        result = resolve_deadline("tomorrow", FIXED_NOW)

        self.assertTrue(result.found)
        self.assertIsNone(result.due_time)
        self.assertIsNone(result.timezone)
        self.assertFalse(result.has_problem)

    def test_time_requires_timezone(self):

        result = resolve_deadline("tomorrow at 18:00", FIXED_NOW)

        self.assertIsNotNone(result.clarification)
        self.assertIsNone(result.due_time)

    def test_explicit_timezone_with_time(self):

        result = resolve_deadline(
            "tomorrow at 18:00 Asia/Kolkata",
            FIXED_NOW,
        )

        self.assertFalse(result.has_problem)
        self.assertEqual(result.due_time, time(18, 0))
        self.assertEqual(result.timezone, "Asia/Kolkata")

    def test_configured_default_timezone_applies_to_timed_deadline(self):

        result = resolve_deadline(
            "tomorrow at 18:00",
            FIXED_NOW,
            default_timezone="Asia/Kolkata",
        )

        self.assertFalse(result.has_problem)
        self.assertEqual(result.timezone, "Asia/Kolkata")

    def test_configured_default_timezone_does_not_change_date_only(self):

        result = resolve_deadline(
            "tomorrow",
            FIXED_NOW,
            default_timezone="Asia/Kolkata",
        )

        self.assertFalse(result.has_problem)
        self.assertIsNone(result.timezone)

    def test_slash_date_requires_clarification(self):

        result = resolve_deadline("11/15/2026", FIXED_NOW)

        self.assertTrue(result.found)
        self.assertIsNotNone(result.clarification)

    def test_ambiguous_vague_phrase_requires_clarification(self):

        result = resolve_deadline("sometime soon", FIXED_NOW)

        self.assertTrue(result.found)
        self.assertIsNotNone(result.clarification)

    def test_past_date_is_rejected(self):

        result = resolve_deadline("2020-01-01", FIXED_NOW)

        self.assertIsNotNone(result.invalid_reason)

    def test_invalid_calendar_date_is_rejected(self):

        result = resolve_deadline("30 February 2026", FIXED_NOW)

        self.assertIsNotNone(result.invalid_reason)

    def test_invalid_time_is_rejected(self):

        result = resolve_deadline("tomorrow at 25:00 UTC", FIXED_NOW)

        self.assertIsNotNone(result.invalid_reason)

    def test_twelve_hour_time_without_meridiem_is_ambiguous(self):

        result = resolve_deadline("tomorrow at 6 UTC", FIXED_NOW)

        self.assertIsNotNone(result.clarification)

    def test_time_only_defaults_to_today(self):

        result = resolve_deadline("18:00 UTC", FIXED_NOW)

        self.assertFalse(result.has_problem)
        self.assertEqual(result.due_date, date(2026, 11, 9))


# ---------------------------------------------------------------------------
# Deterministic intent detection and extraction
# ---------------------------------------------------------------------------


class DetectorTests(unittest.TestCase):
    """Every supported intent and the no-action guard."""

    def _detect(self, message):
        return detect_deterministic(message, FIXED_NOW)

    def test_create_explicit_task(self):

        result = self._detect(
            "Create a high-priority task to finish my Python project "
            "tomorrow."
        )

        self.assertEqual(result.intent, TaskIntent.CREATE_TASK)
        self.assertIsNotNone(result.draft)
        self.assertEqual(result.draft.title, "Python project")
        self.assertEqual(result.draft.priority, TaskPriority.HIGH)
        self.assertEqual(result.draft.category, TaskCategory.PROJECT)
        self.assertEqual(result.draft.due_date, date(2026, 11, 10))

    def test_create_from_obligation_with_deadline(self):

        result = self._detect(
            "I have to complete my Machine Learning assignment by Friday."
        )

        self.assertEqual(result.intent, TaskIntent.CREATE_TASK)
        self.assertIsNotNone(result.draft)
        self.assertEqual(result.draft.category, TaskCategory.COLLEGE)
        self.assertEqual(result.draft.due_date, date(2026, 11, 13))

    def test_create_from_reminder(self):

        result = self._detect("Remind me to call mom tomorrow.")

        self.assertEqual(result.intent, TaskIntent.CREATE_TASK)
        self.assertEqual(result.draft.title, "Call mom")

    def test_create_extracts_an_explicit_description(self):

        result = self._detect(
            "Add a task to prepare slides with description: cover phases "
            "one and two."
        )

        self.assertEqual(result.intent, TaskIntent.CREATE_TASK)
        self.assertEqual(result.draft.title, "Prepare slides")
        self.assertIn("cover phases", result.draft.description.lower())

    def test_create_does_not_invent_optional_fields(self):
        result = self._detect("Add a task to read a book.")

        self.assertEqual(result.intent, TaskIntent.CREATE_TASK)
        self.assertEqual(result.draft.title, "Read a book")
        self.assertIsNone(result.draft.description)
        self.assertIsNone(result.draft.due_date)
        self.assertIsNone(result.draft.due_time)
        self.assertIsNone(result.draft.timezone)
        self.assertEqual(result.draft.category, TaskCategory.OTHER)
        self.assertEqual(result.draft.priority, TaskPriority.MEDIUM)

    def test_list_tasks_with_explicit_filters(self):

        result = self._detect("Show my high priority college tasks.")

        self.assertEqual(result.intent, TaskIntent.LIST_TASKS)
        self.assertIsNotNone(result.filters)
        self.assertEqual(result.filters.category, TaskCategory.COLLEGE)
        self.assertEqual(result.filters.priority, TaskPriority.HIGH)

    def test_pending_tasks_query_is_not_filtered_to_other(self):

        result = self._detect("What are my pending tasks?")

        self.assertEqual(result.intent, TaskIntent.LIST_TASKS)
        self.assertIsNone(result.filters.category)
        self.assertEqual(result.filters.status, TaskStatus.PENDING)

    def test_upcoming_query(self):

        self.assertEqual(
            self._detect("What are my upcoming deadlines?").intent,
            TaskIntent.UPCOMING_TASKS,
        )

    def test_overdue_query(self):

        self.assertEqual(
            self._detect("What is overdue?").intent,
            TaskIntent.OVERDUE_TASKS,
        )

    def test_today_query(self):

        self.assertEqual(
            self._detect("What do I have to finish today?").intent,
            TaskIntent.TODAY_TASKS,
        )

    def test_tomorrow_query(self):

        self.assertEqual(
            self._detect("What tasks are due tomorrow?").intent,
            TaskIntent.TOMORROW_TASKS,
        )

    def test_get_single_task(self):

        result = self._detect("Find my Python project task.")

        self.assertEqual(result.intent, TaskIntent.GET_TASK)
        self.assertEqual(result.target_hint, "Python project")

    def test_status_question_is_not_a_completion(self):

        result = self._detect("Did I complete my Java assignment?")

        self.assertEqual(result.intent, TaskIntent.TASK_STATUS_QUERY)
        self.assertEqual(result.target_hint, "Java assignment")

    def test_explicit_completion(self):

        result = self._detect("Mark my ML assignment as completed.")

        self.assertEqual(result.intent, TaskIntent.COMPLETE_TASK)
        self.assertEqual(result.target_hint, "ML assignment")

    def test_completion_statement(self):

        result = self._detect("I finished my Java assignment.")

        self.assertEqual(result.intent, TaskIntent.COMPLETE_TASK)
        self.assertEqual(result.target_hint, "Java assignment")

    def test_reopen_task(self):

        result = self._detect("Reopen my database assignment.")

        self.assertEqual(result.intent, TaskIntent.REOPEN_TASK)
        self.assertEqual(result.target_hint, "database assignment")

    def test_cancel_task(self):

        result = self._detect("Cancel my old project task.")

        self.assertEqual(result.intent, TaskIntent.CANCEL_TASK)
        self.assertEqual(result.target_hint, "old project")

    def test_delete_task(self):

        result = self._detect("Delete my old task.")

        self.assertEqual(result.intent, TaskIntent.DELETE_TASK)
        self.assertEqual(result.target_hint, "old")

    def test_update_priority_and_category(self):

        result = self._detect("Make my Python project high priority.")

        self.assertEqual(result.intent, TaskIntent.UPDATE_TASK)
        self.assertEqual(result.changes.priority, TaskPriority.HIGH)
        self.assertEqual(result.target_hint, "Python project")

    def test_update_deadline(self):

        result = self._detect(
            "Set the deadline of my project task to next Monday."
        )

        self.assertEqual(result.intent, TaskIntent.UPDATE_TASK)
        self.assertEqual(result.changes.due_date, date(2026, 11, 16))
        self.assertEqual(result.target_hint, "project")

    def test_reschedule_to_named_date(self):

        result = self._detect("Move my exam to 20 November 2026.")

        self.assertEqual(result.intent, TaskIntent.UPDATE_TASK)
        self.assertEqual(result.changes.due_date, date(2026, 11, 20))
        self.assertEqual(result.target_hint, "exam")

    def test_update_title(self):

        result = self._detect(
            "Rename my Python project to Final project."
        )

        self.assertEqual(result.intent, TaskIntent.UPDATE_TASK)
        self.assertEqual(result.changes.title, "Final project")
        self.assertEqual(result.target_hint, "Python project")

    def test_update_description(self):

        result = self._detect(
            "Change my Python project description to Cover testing."
        )

        self.assertEqual(result.intent, TaskIntent.UPDATE_TASK)
        self.assertEqual(result.changes.description, "Cover testing.")
        self.assertEqual(result.target_hint, "Python project")

    def test_update_category(self):

        result = self._detect(
            "Change my Python project category to college."
        )

        self.assertEqual(result.intent, TaskIntent.UPDATE_TASK)
        self.assertEqual(result.changes.category, TaskCategory.COLLEGE)
        self.assertEqual(result.target_hint, "Python project")

    def test_ordinary_conversation_is_not_a_task_action(self):

        for message in (
            "Hello",
            "What should I study next?",
            "Explain Python like I am a beginner.",
            "Can you help me with Python?",
        ):
            with self.subTest(message=message):
                self.assertEqual(
                    self._detect(message).intent,
                    TaskIntent.NO_TASK_ACTION,
                )

    def test_general_statement_is_not_a_task_action(self):

        self.assertEqual(
            self._detect("I have many assignments this semester.").intent,
            TaskIntent.NO_TASK_ACTION,
        )

    def test_non_task_destructive_language_is_ignored(self):

        for message in (
            "Cancel my subscription.",
            "Delete my account.",
            "Remove my email address.",
        ):
            with self.subTest(message=message):
                self.assertEqual(
                    self._detect(message).intent,
                    TaskIntent.NO_TASK_ACTION,
                )

    def test_confirmation_phrases_are_strict(self):

        for phrase in ("yes", "Yes, please.", "confirm"):
            with self.subTest(phrase=phrase):
                self.assertTrue(is_confirmation(phrase))

        for phrase in ("yes I also want pizza", "yesterday", "no problem"):
            with self.subTest(phrase=phrase):
                self.assertFalse(is_confirmation(phrase))

        for phrase in ("no", "No thanks.", "cancel it"):
            with self.subTest(phrase=phrase):
                self.assertTrue(is_rejection(phrase))

        self.assertFalse(is_rejection("no I changed my mind"))


# ---------------------------------------------------------------------------
# LLM fallback (strictly validated, never authoritative)
# ---------------------------------------------------------------------------


class LLMFallbackTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):

        self.llm_enabled = patch.object(
            settings, "TASK_INTENT_LLM_ENABLED", True
        )
        self.llm_enabled.start()
        self.addCleanup(self.llm_enabled.stop)

        self.generate = AsyncMock(
            return_value={
                "provider": "groq",
                "model": "test",
                "response": "{}",
            }
        )
        self.router_patch = patch.object(
            llm_router, "generate_response", new=self.generate
        )
        self.router_patch.start()
        self.addCleanup(self.router_patch.stop)

        self.detector = TaskIntentDetector()

    async def test_llm_fallback_uses_validated_structured_output(self):

        self.generate.return_value = {
            "provider": "groq",
            "model": "test",
            "response": (
                '{"intent": "create_task", "title": "Reorganize tasks"}'
            ),
        }

        result = await self.detector.detect(
            "I should reorganize my tasks.",
            now=FIXED_NOW,
            default_timezone=None,
        )

        self.assertEqual(result.intent, TaskIntent.CREATE_TASK)
        self.assertEqual(result.draft.title, "Reorganize tasks")
        self.assertIsNone(result.draft.due_date)

    async def test_llm_invalid_json_is_ignored(self):

        self.generate.return_value = {
            "provider": "groq",
            "model": "test",
            "response": "this is not json",
        }

        result = await self.detector.detect(
            "I should reorganize my tasks.",
            now=FIXED_NOW,
        )

        self.assertEqual(result.intent, TaskIntent.NO_TASK_ACTION)

    async def test_llm_cannot_smuggle_ids_or_fields(self):

        self.generate.return_value = {
            "provider": "groq",
            "model": "test",
            "response": (
                '{"intent": "delete_task", "task_id": 5, "user_id": 2}'
            ),
        }

        result = await self.detector.detect(
            "I should reorganize my tasks.",
            now=FIXED_NOW,
        )

        self.assertEqual(result.intent, TaskIntent.NO_TASK_ACTION)

    async def test_llm_fallback_preserves_private_routing(self):

        self.generate.return_value = {
            "provider": "ollama",
            "model": "test",
            "response": '{"intent": "no_task_action"}',
        }

        await self.detector.detect(
            "I should reorganize my tasks.",
            private=True,
            now=FIXED_NOW,
        )

        self.generate.assert_awaited()
        self.assertTrue(self.generate.await_args.kwargs["private"])

    async def test_deterministic_intents_never_call_the_llm(self):

        result = await self.detector.detect(
            "Add a task to call mom.",
            now=FIXED_NOW,
        )

        self.assertEqual(result.intent, TaskIntent.CREATE_TASK)
        self.generate.assert_not_awaited()


# ---------------------------------------------------------------------------
# Chat integration and confirmation safety
# ---------------------------------------------------------------------------


class NaturalLanguageChatTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):

        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        self.SessionLocal = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

        async with self.SessionLocal() as session:
            alice = User(username="nl_alice")
            bob = User(username="nl_bob")
            session.add_all([alice, bob])
            await session.commit()
            await session.refresh(alice)
            await session.refresh(bob)
            self.alice_id = alice.id
            self.bob_id = bob.id

        self._start_patch(
            "app.task_intent.clock.utcnow",
            return_value=FIXED_NOW,
        )
        self._start_patch(
            "app.services.task_service._utcnow",
            return_value=FIXED_NOW,
        )
        self._start_patch(
            "app.context.task_selector._utcnow",
            return_value=FIXED_NOW,
        )

        for name, value in (
            ("TASK_INTENT_ENABLED", True),
            ("TASK_INTENT_LLM_ENABLED", False),
            ("TASK_DEFAULT_TIMEZONE", ""),
            ("TASK_INTENT_MAX_RESULTS", 20),
            ("TASK_ACTION_CONFIRMATION_TTL_SECONDS", 600),
        ):
            self._start_patch(None, value, target=name)

        self.llm_generate = AsyncMock(
            return_value={
                "provider": "groq",
                "model": "test",
                "response": "Normal answer.",
            }
        )
        self.router_patch = patch.object(
            llm_router, "generate_response", new=self.llm_generate
        )
        self.router_patch.start()
        self.addCleanup(self.router_patch.stop)

        self.memory_patch = patch.object(
            memory_retriever,
            "retrieve",
            new=AsyncMock(return_value=[]),
        )
        self.memory_patch.start()
        self.addCleanup(self.memory_patch.stop)

        async def override_get_db():
            async with self.SessionLocal() as session:
                yield session

        async def override_alice():
            return self.alice_id

        async def override_bob():
            return self.bob_id

        self.override_alice = override_alice
        self.override_bob = override_bob

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user_id] = override_alice
        app.dependency_overrides[get_optional_user_id] = override_alice

        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )

    def _start_patch(self, import_path, value=None, target=None,
                     return_value=None):
        if target is not None:
            patcher = patch.object(settings, target, value)
        else:
            patcher = patch(import_path, return_value=return_value)

        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):

        await self.client.aclose()
        app.dependency_overrides.clear()
        await self.engine.dispose()

    # -- helpers ------------------------------------------------------

    def _as_alice(self):
        app.dependency_overrides[get_current_user_id] = self.override_alice
        app.dependency_overrides[get_optional_user_id] = self.override_alice

    def _as_bob(self):
        app.dependency_overrides[get_current_user_id] = self.override_bob
        app.dependency_overrides[get_optional_user_id] = self.override_bob

    async def _chat(self, message, private=False, conversation_id=None):

        payload = {"message": message, "private": private}

        if conversation_id is not None:
            payload["conversation_id"] = conversation_id

        response = await self.client.post("/api/chat/", json=payload)

        self.assertEqual(response.status_code, 200, response.text)

        return response.json()

    async def _create_task(self, title, user_id=None, **kwargs):

        async with self.SessionLocal() as session:
            return await task_service.create_task(
                session=session,
                user_id=self.alice_id if user_id is None else user_id,
                title=title,
                **kwargs,
            )

    async def _task_count(self, user_id=None):

        async with self.SessionLocal() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(Task)
                    .where(
                        Task.user_id == (
                            self.alice_id
                            if user_id is None
                            else user_id
                        )
                    )
                ) or 0
            )

    async def _actions(self, user_id=None):

        async with self.SessionLocal() as session:
            result = await session.execute(
                select(TaskPendingAction)
                .where(
                    TaskPendingAction.user_id == (
                        self.alice_id
                        if user_id is None
                        else user_id
                    )
                )
                .order_by(TaskPendingAction.id.asc())
            )
            return list(result.scalars().all())

    async def _latest_action(self, user_id=None):

        actions = await self._actions(user_id=user_id)

        self.assertTrue(actions, "expected a pending action")

        return actions[-1]

    async def _get_task(self, task_id):

        async with self.SessionLocal() as session:
            return await session.get(Task, task_id)

    # -- creation -----------------------------------------------------

    async def test_create_requires_confirmation_and_does_not_mutate(self):

        reply = await self._chat(
            "Create a high-priority task to finish my Python project "
            "tomorrow."
        )

        self.assertEqual(reply["provider"], "task_engine")
        self.assertEqual(reply["model"], "deterministic")
        self.assertIn("Would you like me to create this task?", reply["response"])
        self.assertEqual(await self._task_count(), 0)

        action = await self._latest_action()
        self.assertEqual(action.action_type, PendingActionType.CREATE)
        self.assertEqual(action.status, PendingActionStatus.PENDING)

    async def test_confirmation_executes_creation_once(self):

        first = await self._chat("Add a task to call mom tomorrow.")
        conversation_id = first["conversation_id"]

        reply = await self._chat("yes", conversation_id=conversation_id)

        self.assertIn("Done.", reply["response"])
        self.assertEqual(await self._task_count(), 1)

        action = await self._latest_action()
        self.assertEqual(action.status, PendingActionStatus.EXECUTED)
        self.assertIsNotNone(action.result_task_id)

        again = await self._chat("yes", conversation_id=conversation_id)

        self.assertNotIn("Done.", again["response"])
        self.assertEqual(await self._task_count(), 1)

    async def test_rejection_never_mutates(self):

        first = await self._chat("Add a task to call mom tomorrow.")
        conversation_id = first["conversation_id"]

        reply = await self._chat("no thanks", conversation_id=conversation_id)

        self.assertIn("won't make any changes", reply["response"])
        self.assertEqual(await self._task_count(), 0)

        action = await self._latest_action()
        self.assertEqual(action.status, PendingActionStatus.REJECTED)

    async def test_unrelated_message_supersedes_pending_action(self):

        first = await self._chat("Add a task to call mom tomorrow.")
        conversation_id = first["conversation_id"]

        await self._chat("What is the weather?", conversation_id=conversation_id)

        action = await self._latest_action()
        self.assertEqual(action.status, PendingActionStatus.SUPERSEDED)

        await self._chat("yes", conversation_id=conversation_id)

        self.assertEqual(await self._task_count(), 0)

    async def test_yes_without_pending_action_never_mutates(self):

        reply = await self._chat("yes")

        self.assertEqual(reply["response"], "Normal answer.")
        self.assertEqual(await self._task_count(), 0)
        self.assertEqual(await self._actions(), [])

    async def test_ambiguous_creation_deadline_creates_no_pending_action(self):

        reply = await self._chat("Add a task to buy groceries sometime soon.")

        self.assertIn("ambiguous", reply["response"])
        self.assertEqual(await self._actions(), [])

    async def test_timed_deadline_without_timezone_asks_clarification(self):

        reply = await self._chat("Finish my report tomorrow at 6 PM.")

        self.assertIn("timezone", reply["response"].lower())
        self.assertEqual(await self._actions(), [])

    # -- lifecycle actions --------------------------------------------

    async def test_complete_requires_confirmation_then_executes(self):

        task = await self._create_task("Java assignment")

        first = await self._chat("Mark my Java assignment as completed.")
        conversation_id = first["conversation_id"]

        self.assertIn("Would you like me to mark", first["response"])
        self.assertEqual((await self._get_task(task.id)).status,
                         TaskStatus.PENDING)

        await self._chat("yes", conversation_id=conversation_id)

        refreshed = await self._get_task(task.id)
        self.assertEqual(refreshed.status, TaskStatus.COMPLETED)

    async def test_reopen_requires_confirmation_then_executes(self):

        task = await self._create_task(
            "Database assignment",
            status=TaskStatus.COMPLETED,
            now=FIXED_NOW,
        )

        first = await self._chat("Reopen my database assignment.")
        conversation_id = first["conversation_id"]

        self.assertIn("reopen", first["response"].lower())
        self.assertEqual(
            (await self._get_task(task.id)).status,
            TaskStatus.COMPLETED,
        )

        await self._chat("yes", conversation_id=conversation_id)

        self.assertEqual(
            (await self._get_task(task.id)).status,
            TaskStatus.PENDING,
        )

    async def test_cancel_requires_confirmation_then_executes(self):

        task = await self._create_task("Old project")

        first = await self._chat("Cancel my old project task.")
        conversation_id = first["conversation_id"]

        self.assertEqual(
            (await self._get_task(task.id)).status,
            TaskStatus.PENDING,
        )

        await self._chat("yes", conversation_id=conversation_id)

        self.assertEqual(
            (await self._get_task(task.id)).status,
            TaskStatus.CANCELLED,
        )

    async def test_delete_requires_confirmation_then_executes(self):

        task = await self._create_task("Old task")

        first = await self._chat("Delete my old task.")
        conversation_id = first["conversation_id"]

        self.assertIsNotNone(await self._get_task(task.id))

        await self._chat("yes", conversation_id=conversation_id)

        self.assertIsNone(await self._get_task(task.id))

    async def test_update_requires_confirmation_then_executes(self):

        task = await self._create_task("Python project")

        first = await self._chat(
            "Set the deadline of my Python project to next Monday."
        )
        conversation_id = first["conversation_id"]

        self.assertIn("Proposed changes", first["response"])
        self.assertIsNone((await self._get_task(task.id)).due_date)

        await self._chat("yes", conversation_id=conversation_id)

        self.assertEqual(
            (await self._get_task(task.id)).due_date,
            date(2026, 11, 16),
        )

    async def test_update_title_requires_confirmation(self):

        task = await self._create_task("Python project")

        first = await self._chat("Rename my Python project to Final project.")
        conversation_id = first["conversation_id"]

        self.assertEqual((await self._get_task(task.id)).title, "Python project")

        await self._chat("yes", conversation_id=conversation_id)

        self.assertEqual(
            (await self._get_task(task.id)).title,
            "Final project",
        )

    async def test_update_priority_requires_confirmation(self):
        task = await self._create_task("Python project")

        first = await self._chat("Make my Python project high priority.")
        conversation_id = first["conversation_id"]

        self.assertEqual(
            (await self._get_task(task.id)).priority,
            TaskPriority.MEDIUM,
        )

        await self._chat("confirm", conversation_id=conversation_id)

        self.assertEqual(
            (await self._get_task(task.id)).priority,
            TaskPriority.HIGH,
        )

    # -- safety -------------------------------------------------------

    async def test_ambiguous_task_target_creates_no_pending_action(self):

        await self._create_task("Math assignment")
        await self._create_task("Math project")

        reply = await self._chat("Complete my Math.")

        self.assertIn("more than one", reply["response"])
        self.assertEqual(await self._actions(), [])

    async def test_missing_target_asks_for_a_title(self):

        reply = await self._chat("Complete my task.")

        self.assertIn("Which task", reply["response"])
        self.assertEqual(await self._actions(), [])

    async def test_unknown_task_creates_no_pending_action(self):

        reply = await self._chat("Complete my nonexistent assignment.")

        self.assertIn("couldn't find", reply["response"])
        self.assertEqual(await self._actions(), [])

    async def test_deleted_target_fails_safely_on_confirmation(self):

        task = await self._create_task("Old task")

        first = await self._chat("Delete my old task.")
        conversation_id = first["conversation_id"]

        async with self.SessionLocal() as session:
            direct = await task_service.get_task(
                session=session,
                user_id=self.alice_id,
                task_id=task.id,
            )
            await task_service.delete_task(
                session=session,
                user_id=self.alice_id,
                task_id=direct.id,
            )

        reply = await self._chat("yes", conversation_id=conversation_id)

        self.assertIn("no longer exists", reply["response"])

        action = await self._latest_action()
        self.assertEqual(action.status, PendingActionStatus.FAILED)

    async def test_expired_confirmation_cannot_execute(self):

        first = await self._chat("Add a task to call mom tomorrow.")
        conversation_id = first["conversation_id"]
        action = await self._latest_action()

        later = FIXED_NOW + timedelta(seconds=601)

        with patch("app.task_intent.clock.utcnow", return_value=later):
            reply = await self._chat(
                "yes",
                conversation_id=conversation_id,
            )

        self.assertNotIn("Done.", reply["response"])
        self.assertEqual(await self._task_count(), 0)

        async with self.SessionLocal() as session:
            stored = await session.get(TaskPendingAction, action.id)

        self.assertEqual(stored.status, PendingActionStatus.EXPIRED)

    async def test_query_does_not_create_a_pending_action(self):

        await self._create_task("Visible task")

        reply = await self._chat("Show my tasks.")

        self.assertIn("Visible task", reply["response"])
        self.assertEqual(await self._actions(), [])
        self.assertEqual(await self._task_count(), 1)

    async def test_today_query_includes_due_and_overdue(self):

        await self._create_task(
            "Today task",
            due_date=date(2026, 11, 9),
        )
        await self._create_task(
            "Overdue task",
            due_date=date(2026, 11, 8),
        )

        reply = await self._chat("What do I have to finish today?")

        self.assertIn("Today task", reply["response"])
        self.assertIn("Overdue task", reply["response"])
        self.assertEqual(await self._actions(), [])

    async def test_upcoming_query_lists_future_tasks(self):

        await self._create_task(
            "Upcoming task",
            due_date=date(2026, 11, 12),
        )

        reply = await self._chat("Show my upcoming tasks.")

        self.assertIn("Upcoming task", reply["response"])

    async def test_overdue_query_lists_past_tasks(self):

        await self._create_task(
            "Overdue only",
            due_date=date(2026, 11, 8),
        )

        reply = await self._chat("Show overdue tasks.")

        self.assertIn("Overdue only", reply["response"])

    async def test_tomorrow_query_lists_tomorrow_tasks(self):

        await self._create_task(
            "Tomorrow task",
            due_date=date(2026, 11, 10),
        )

        reply = await self._chat("What tasks are due tomorrow?")

        self.assertIn("Tomorrow task", reply["response"])

    async def test_get_task_and_status_query(self):

        task = await self._create_task("Java assignment")

        reply = await self._chat("Find my Java assignment task.")
        self.assertIn("Java assignment", reply["response"])

        reply = await self._chat("Did I complete my Java assignment?")
        self.assertIn("pending", reply["response"])
        self.assertIsNotNone(task)

    async def test_private_mutation_never_calls_the_llm(self):

        reply = await self._chat(
            "Add a task to call mom tomorrow.",
            private=True,
        )

        self.assertIn("Would you like me to create", reply["response"])
        self.llm_generate.assert_not_awaited()

    # -- normal chat and feature flag ---------------------------------

    async def test_normal_chat_is_unaffected(self):

        for message in (
            "Hello",
            "What should I study next?",
            "Describe a binary tree.",
        ):
            with self.subTest(message=message):
                reply = await self._chat(message)
                self.assertEqual(reply["response"], "Normal answer.")
                self.assertEqual(reply["provider"], "groq")

        self.assertEqual(await self._actions(), [])

    async def test_disabled_feature_falls_through_to_normal_chat(self):

        with patch.object(settings, "TASK_INTENT_ENABLED", False):
            reply = await self._chat("Add a task to call mom tomorrow.")

        self.assertEqual(reply["response"], "Normal answer.")
        self.assertEqual(await self._actions(), [])
        self.assertEqual(await self._task_count(), 0)


# ---------------------------------------------------------------------------
# Minimal confirmation API and ownership
# ---------------------------------------------------------------------------


class TaskActionAPITests(NaturalLanguageChatTests):
    """Reuse the chat fixture to exercise the confirmation endpoints."""

    async def test_pending_list_confirm_and_reject(self):

        first = await self._chat("Add a task to call mom tomorrow.")
        self.assertIsNotNone(first["conversation_id"])
        action = await self._latest_action()

        response = await self.client.get("/api/task-actions/pending")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["id"], action.id)
        self.assertNotIn("payload", body[0])

        response = await self.client.post(
            f"/api/task-actions/{action.id}/confirm"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["action"]["status"], "executed")
        self.assertEqual(response.json()["task"]["title"], "Call mom")

        response = await self.client.post(
            f"/api/task-actions/{action.id}/confirm"
        )
        self.assertEqual(response.status_code, 409)

        self.assertEqual(await self._task_count(), 1)

    async def test_reject_endpoint_and_pending_cleanup(self):

        await self._chat("Add a task to call mom tomorrow.")
        action = await self._latest_action()

        response = await self.client.post(
            f"/api/task-actions/{action.id}/reject"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "rejected")

        response = await self.client.get("/api/task-actions/pending")
        self.assertEqual(response.json(), [])
        self.assertEqual(await self._task_count(), 0)

    async def test_other_user_cannot_confirm_an_action(self):

        await self._chat("Add a task to call mom tomorrow.")
        action = await self._latest_action()

        self._as_bob()

        response = await self.client.get("/api/task-actions/pending")
        self.assertEqual(response.json(), [])

        response = await self.client.post(
            f"/api/task-actions/{action.id}/confirm"
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(await self._task_count(), 0)

        self._as_alice()

        response = await self.client.post(
            f"/api/task-actions/{action.id}/confirm"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(await self._task_count(), 1)

    async def test_expired_action_returns_gone(self):

        await self._chat("Add a task to call mom tomorrow.")
        action = await self._latest_action()

        later = FIXED_NOW + timedelta(seconds=601)

        with patch("app.task_intent.clock.utcnow", return_value=later):
            response = await self.client.post(
                f"/api/task-actions/{action.id}/confirm"
            )

        self.assertEqual(response.status_code, 410)
        self.assertEqual(await self._task_count(), 0)

    async def test_missing_action_returns_not_found(self):

        response = await self.client.post(
            "/api/task-actions/999999/confirm"
        )
        self.assertEqual(response.status_code, 404)

        response = await self.client.post(
            "/api/task-actions/999999/reject"
        )
        self.assertEqual(response.status_code, 404)

    async def test_cancelled_task_cannot_be_completed(self):

        task = await self._create_task(
            "Cancelled task",
            status=TaskStatus.CANCELLED,
            now=FIXED_NOW,
        )

        await self._chat("Complete my Cancelled task.")
        action = await self._latest_action()

        response = await self.client.post(
            f"/api/task-actions/{action.id}/confirm"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            (await self._get_task(task.id)).status,
            TaskStatus.CANCELLED,
        )

        async with self.SessionLocal() as session:
            stored = await session.get(TaskPendingAction, action.id)

        self.assertEqual(stored.status, PendingActionStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
