"""Owner-scoped recurring-task rules and idempotent occurrence generation.

Occurrences are ordinary tasks. The recurrence rule stores only schedule
metadata; title, description, category, and priority are copied from the
template task at generation time. Generated tasks are never rewritten or
deleted by later generation runs.

Generation is bounded by an end date, a horizon, and a per-run cap. The unique
occurrence date link prevents duplicates after retries, restarts, or concurrent
workers. Completed, cancelled, and overdue occurrences do not stop future
generation.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.exceptions.recurrence_exceptions import (
    RecurrenceConflictError,
    RecurrenceNotFoundError,
    RecurrenceValidationError,
)
from app.exceptions.task_exceptions import TaskValidationError
from app.models.recurrence import (
    RecurrenceFrequency,
    TaskRecurrence,
    TaskRecurrenceOccurrence,
)
from app.models.task import Task, TaskCategory, TaskPriority, TaskStatus
from app.services.task_deadlines import (
    OPEN_STATUSES,
    canonical_timezone,
    resolve_timezone,
    utcnow,
)
from app.services.task_service import task_service


logger = logging.getLogger(__name__)

UTC = timezone.utc
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50
MAX_INTERVAL = 365
MAX_HORIZON_DAYS = 3660


def _utcnow() -> datetime:
    """Clock hook used by the service; tests can patch this function."""

    return utcnow()


def _normalise_frequency(value: Any) -> RecurrenceFrequency:
    if isinstance(value, RecurrenceFrequency):
        return value

    try:
        return RecurrenceFrequency(str(value).strip().lower())

    except (TypeError, ValueError) as error:
        allowed = ", ".join(item.value for item in RecurrenceFrequency)
        raise RecurrenceValidationError(
            f"Unsupported recurrence frequency. Allowed: {allowed}."
        ) from error


def _normalise_weekdays(value: Any) -> list[int]:
    if value is None:
        return []

    if isinstance(value, str):
        value = [item for item in value.split(",") if item.strip()]

    try:
        days = sorted({int(item) for item in value})

    except (TypeError, ValueError) as error:
        raise RecurrenceValidationError(
            "weekdays must contain integers from 0 (Monday) to 6 (Sunday)."
        ) from error

    if any(day < 0 or day > 6 for day in days):
        raise RecurrenceValidationError(
            "weekdays must contain integers from 0 (Monday) to 6 (Sunday)."
        )

    return days


def _serialise_weekdays(days: list[int]) -> str:
    return ",".join(str(day) for day in days)


def _normalise_interval(value: Any) -> int:
    try:
        interval = int(value)

    except (TypeError, ValueError) as error:
        raise RecurrenceValidationError(
            "interval must be an integer."
        ) from error

    if interval < 1 or interval > MAX_INTERVAL:
        raise RecurrenceValidationError(
            f"interval must be between 1 and {MAX_INTERVAL}."
        )

    return interval


def _normalise_timezone(name: str | None, fallback: str | None) -> str | None:
    raw = name if name not in (None, "") else fallback

    try:
        return canonical_timezone(raw)

    except TaskValidationError as error:
        raise RecurrenceValidationError(
            "timezone must be a recognised IANA timezone name."
        ) from error


def _validate_page(limit: int, offset: int) -> None:
    if limit < 1 or limit > MAX_PAGE_SIZE:
        raise RecurrenceValidationError(
            f"limit must be between 1 and {MAX_PAGE_SIZE}."
        )

    if offset < 0:
        raise RecurrenceValidationError("offset cannot be negative.")


def _month_length(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _matches_date(
    rule: TaskRecurrence,
    candidate: date,
    weekdays: list[int],
) -> bool:
    if candidate < rule.start_date:
        return False

    if rule.end_date is not None and candidate > rule.end_date:
        return False

    frequency = rule.frequency

    if frequency == RecurrenceFrequency.DAILY:
        return (candidate - rule.start_date).days % rule.interval == 0

    if frequency == RecurrenceFrequency.WEEKLY:
        selected = weekdays or [rule.start_date.weekday()]

        if candidate.weekday() not in selected:
            return False

        anchor_monday = rule.start_date - timedelta(
            days=rule.start_date.weekday()
        )
        week_index = (candidate - anchor_monday).days // 7

        return week_index >= 0 and week_index % rule.interval == 0

    if frequency == RecurrenceFrequency.MONTHLY:
        month_index = (
            (candidate.year - rule.start_date.year) * 12
            + (candidate.month - rule.start_date.month)
        )

        return (
            month_index >= 0
            and month_index % rule.interval == 0
            and candidate.day == rule.start_date.day
        )

    return False


def iter_occurrence_dates(
    rule: TaskRecurrence,
    after: date | None,
    until: date,
    limit: int = 1000,
) -> Iterator[date]:
    """Yield matching dates strictly after ``after`` and up to ``until``."""

    if limit <= 0:
        return

    weekdays = _normalise_weekdays(rule.weekdays)
    start = max(
        rule.start_date,
        (after + timedelta(days=1)) if after is not None else rule.start_date,
    )

    emitted = 0
    candidate = start

    while candidate <= until:
        if _matches_date(rule, candidate, weekdays):
            yield candidate
            emitted += 1

            if emitted >= limit:
                return

        candidate += timedelta(days=1)


class RecurrenceService:
    """Asynchronous recurrence CRUD and occurrence generation."""

    async def create_recurrence(
        self,
        session: AsyncSession,
        user_id: int,
        task_id: int,
        frequency: RecurrenceFrequency | str,
        interval: int = 1,
        weekdays: list[int] | str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        timezone_name: str | None = None,
        now: datetime | None = None,
    ) -> TaskRecurrence:
        """Create a recurrence rule for an owned open task with a deadline."""

        task = await task_service.get_task(
            session=session,
            user_id=user_id,
            task_id=task_id,
        )

        if task.status not in OPEN_STATUSES:
            raise RecurrenceValidationError(
                "Recurring tasks can only be created for pending or "
                "in-progress tasks."
            )

        if task.due_date is None:
            raise RecurrenceValidationError(
                "A recurring task requires a due date."
            )

        normalised_frequency = _normalise_frequency(frequency)
        normalised_interval = _normalise_interval(interval)
        normalised_weekdays = _normalise_weekdays(weekdays)

        if (
            normalised_frequency != RecurrenceFrequency.WEEKLY
            and normalised_weekdays
        ):
            raise RecurrenceValidationError(
                "weekdays can only be supplied for weekly recurrence."
            )

        resolved_start = start_date or task.due_date

        if resolved_start < task.due_date:
            raise RecurrenceValidationError(
                "start_date cannot be before the task's due date."
            )

        if end_date is not None and end_date < resolved_start:
            raise RecurrenceValidationError(
                "end_date cannot be before start_date."
            )

        resolved_timezone = _normalise_timezone(timezone_name, task.timezone)

        if normalised_frequency == RecurrenceFrequency.WEEKLY:
            if not normalised_weekdays:
                normalised_weekdays = [resolved_start.weekday()]
            stored_weekdays: str | None = _serialise_weekdays(
                normalised_weekdays
            )
        else:
            stored_weekdays = None

        existing = await session.scalar(
            select(TaskRecurrence).where(
                TaskRecurrence.task_id == task.id,
            )
        )

        if existing is not None:
            raise RecurrenceConflictError(
                "A recurrence rule already exists for this task."
            )

        current = (now or _utcnow()).astimezone(UTC)

        linked_template = resolved_start == task.due_date

        rule = TaskRecurrence(
            user_id=user_id,
            task_id=task.id,
            frequency=normalised_frequency,
            interval=normalised_interval,
            weekdays=stored_weekdays,
            start_date=resolved_start,
            end_date=end_date,
            timezone=resolved_timezone,
            anchor_time=task.due_time,
            is_active=True,
            last_generated_date=(
                resolved_start if linked_template else task.due_date
            ),
            next_generation_at=current,
        )

        try:
            session.add(rule)
            await session.flush()

            if linked_template:
                session.add(
                    TaskRecurrenceOccurrence(
                        user_id=user_id,
                        recurrence_id=rule.id,
                        task_id=task.id,
                        occurrence_date=resolved_start,
                    )
                )
                await session.flush()

            await session.commit()
            await session.refresh(rule)

        except IntegrityError as error:
            await session.rollback()
            raise RecurrenceConflictError(
                "A recurrence rule already exists for this task."
            ) from error

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to create a recurrence rule for task %s and user %s.",
                task_id,
                user_id,
            )
            raise

        return rule

    async def get_recurrence(
        self,
        session: AsyncSession,
        user_id: int,
        recurrence_id: int,
    ) -> TaskRecurrence:
        result = await session.execute(
            select(TaskRecurrence).where(
                TaskRecurrence.id == recurrence_id,
                TaskRecurrence.user_id == user_id,
            )
        )

        rule = result.scalar_one_or_none()

        if rule is None:
            raise RecurrenceNotFoundError("Recurrence not found.")

        return rule

    async def get_for_task(
        self,
        session: AsyncSession,
        user_id: int,
        task_id: int,
    ) -> TaskRecurrence | None:
        # Ownership is validated even when no rule exists.
        await task_service.get_task(
            session=session,
            user_id=user_id,
            task_id=task_id,
        )

        return await session.scalar(
            select(TaskRecurrence).where(
                TaskRecurrence.task_id == task_id,
                TaskRecurrence.user_id == user_id,
            )
        )

    async def list_recurrences(
        self,
        session: AsyncSession,
        user_id: int,
        task_id: int | None = None,
        include_inactive: bool = True,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> list[TaskRecurrence]:
        _validate_page(limit, offset)

        if task_id is not None:
            await task_service.get_task(
                session=session,
                user_id=user_id,
                task_id=task_id,
            )

        conditions = [TaskRecurrence.user_id == user_id]

        if task_id is not None:
            conditions.append(TaskRecurrence.task_id == task_id)

        if not include_inactive:
            conditions.append(TaskRecurrence.is_active.is_(True))

        result = await session.execute(
            select(TaskRecurrence)
            .where(*conditions)
            .order_by(TaskRecurrence.id.asc())
            .limit(limit)
            .offset(offset)
        )

        return list(result.scalars().all())

    async def update_recurrence(
        self,
        session: AsyncSession,
        user_id: int,
        recurrence_id: int,
        changes: dict[str, Any],
        now: datetime | None = None,
    ) -> TaskRecurrence:
        """Partially update an owned recurrence rule."""

        allowed = {
            "frequency",
            "interval",
            "weekdays",
            "end_date",
            "timezone",
            "is_active",
        }

        unknown = set(changes) - allowed

        if unknown:
            raise RecurrenceValidationError(
                "Unsupported recurrence field(s): "
                + ", ".join(sorted(unknown))
                + "."
            )

        rule = await self.get_recurrence(
            session=session,
            user_id=user_id,
            recurrence_id=recurrence_id,
        )

        current = (now or _utcnow()).astimezone(UTC)

        if "frequency" in changes:
            rule.frequency = _normalise_frequency(changes["frequency"])

        if "interval" in changes:
            rule.interval = _normalise_interval(changes["interval"])

        if "weekdays" in changes:
            supplied = _normalise_weekdays(changes["weekdays"])

            if rule.frequency != RecurrenceFrequency.WEEKLY and supplied:
                raise RecurrenceValidationError(
                    "weekdays can only be supplied for weekly recurrence."
                )

            rule.weekdays = (
                _serialise_weekdays(
                    supplied or [rule.start_date.weekday()]
                )
                if rule.frequency == RecurrenceFrequency.WEEKLY
                else None
            )

        elif (
            "frequency" in changes
            and rule.frequency == RecurrenceFrequency.WEEKLY
            and not _normalise_weekdays(rule.weekdays)
        ):
            rule.weekdays = _serialise_weekdays(
                [rule.start_date.weekday()]
            )

        if "end_date" in changes:
            end_date = changes["end_date"]

            if end_date is not None and end_date < rule.start_date:
                raise RecurrenceValidationError(
                    "end_date cannot be before start_date."
                )

            rule.end_date = end_date

        if "timezone" in changes:
            rule.timezone = _normalise_timezone(
                changes["timezone"],
                rule.timezone,
            )

        if "is_active" in changes:
            rule.is_active = bool(changes["is_active"])

        # Any rule change re-opens generation. A user-disabled rule stays idle.
        rule.claim_token = None
        rule.processing_started_at = None
        rule.last_error = None
        rule.next_generation_at = current if rule.is_active else None

        try:
            await session.commit()
            await session.refresh(rule)

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to update recurrence %s for user %s.",
                recurrence_id,
                user_id,
            )
            raise

        return rule

    async def deactivate_recurrence(
        self,
        session: AsyncSession,
        user_id: int,
        recurrence_id: int,
    ) -> TaskRecurrence:
        """Stop future generation while preserving the rule and history."""

        rule = await self.get_recurrence(
            session=session,
            user_id=user_id,
            recurrence_id=recurrence_id,
        )

        if not rule.is_active and rule.next_generation_at is None:
            return rule

        rule.is_active = False
        rule.next_generation_at = None
        rule.claim_token = None
        rule.processing_started_at = None

        try:
            await session.commit()
            await session.refresh(rule)

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to deactivate recurrence %s for user %s.",
                recurrence_id,
                user_id,
            )
            raise

        return rule

    async def list_occurrences(
        self,
        session: AsyncSession,
        user_id: int,
        recurrence_id: int,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> list[TaskRecurrenceOccurrence]:
        _validate_page(limit, offset)

        await self.get_recurrence(
            session=session,
            user_id=user_id,
            recurrence_id=recurrence_id,
        )

        result = await session.execute(
            select(TaskRecurrenceOccurrence)
            .where(
                TaskRecurrenceOccurrence.user_id == user_id,
                TaskRecurrenceOccurrence.recurrence_id == recurrence_id,
            )
            .order_by(
                TaskRecurrenceOccurrence.occurrence_date.asc(),
                TaskRecurrenceOccurrence.id.asc(),
            )
            .limit(limit)
            .offset(offset)
        )

        return list(result.scalars().all())

    async def generate_due(
        self,
        session: AsyncSession,
        rule: TaskRecurrence,
        now: datetime | None = None,
        horizon_days: int | None = None,
        max_generations: int | None = None,
    ) -> list[Task]:
        """Generate bounded future occurrences for one claimed rule.

        Each task and its occurrence link are committed together. The rule's
        ``last_generated_date`` advances in the same transaction, so a restart
        resumes without regenerating completed work.
        """

        current = (now or _utcnow()).astimezone(UTC)

        if not rule.is_active:
            return []

        template = await session.get(Task, rule.task_id)

        if template is None:
            rule.is_active = False
            rule.next_generation_at = None
            rule.last_error = "template task no longer exists"
            await session.commit()
            return []

        days = horizon_days or settings.RECURRENCE_GENERATION_HORIZON_DAYS
        days = max(1, min(int(days), MAX_HORIZON_DAYS))
        cap = max_generations or settings.RECURRENCE_MAX_GENERATIONS_PER_RUN
        cap = max(1, int(cap))

        zone = resolve_timezone(rule.timezone)
        local_today = current.astimezone(zone).date()
        until = local_today + timedelta(days=days)

        if rule.end_date is not None and until > rule.end_date:
            until = rule.end_date

        after = rule.last_generated_date

        if after is not None and after >= until:
            self._schedule_next_generation(rule, current, template_exists=True)
            await session.commit()
            return []

        generated: list[Task] = []
        active_rule: TaskRecurrence | None = rule

        for occurrence_date in iter_occurrence_dates(
            rule,
            after=after,
            until=until,
            limit=cap * 3,
        ):
            if len(generated) >= cap:
                break

            if active_rule is None:
                break

            existing = await session.scalar(
                select(TaskRecurrenceOccurrence.id).where(
                    TaskRecurrenceOccurrence.recurrence_id
                    == active_rule.id,
                    TaskRecurrenceOccurrence.occurrence_date
                    == occurrence_date,
                )
            )

            if existing is not None:
                active_rule.last_generated_date = occurrence_date
                await session.commit()
                continue

            try:
                task = await task_service.create_task_in_session(
                    session=session,
                    user_id=active_rule.user_id,
                    title=template.title,
                    description=template.description,
                    category=template.category,
                    priority=template.priority,
                    status=TaskStatus.PENDING,
                    due_date=occurrence_date,
                    due_time=active_rule.anchor_time,
                    timezone=active_rule.timezone,
                    now=current,
                )

                session.add(
                    TaskRecurrenceOccurrence(
                        user_id=active_rule.user_id,
                        recurrence_id=active_rule.id,
                        task_id=task.id,
                        occurrence_date=occurrence_date,
                    )
                )

                active_rule.last_generated_date = occurrence_date
                await session.commit()
                generated.append(task)

            except IntegrityError:
                await session.rollback()
                active_rule = await session.get(
                    TaskRecurrence,
                    rule.id,
                )

                if active_rule is None:
                    break

                # Another worker or a previous partial run already linked this
                # date. Advance past it without duplicating the task.
                active_rule.last_generated_date = occurrence_date
                await session.commit()

            except Exception:
                await session.rollback()
                raise

        if active_rule is None:
            return generated

        last_date = active_rule.last_generated_date

        if (
            active_rule.end_date is not None
            and last_date is not None
            and last_date >= active_rule.end_date
        ):
            active_rule.is_active = False
            active_rule.next_generation_at = None
        else:
            self._schedule_next_generation(
                active_rule,
                current,
                template_exists=True,
            )

        await session.commit()

        return generated

    def _schedule_next_generation(
        self,
        rule: TaskRecurrence,
        current: datetime,
        *,
        template_exists: bool,
    ) -> None:
        if not template_exists:
            rule.is_active = False
            rule.next_generation_at = None
            return

        if rule.end_date is not None:
            start = (
                rule.last_generated_date
                if rule.last_generated_date is not None
                else rule.start_date - timedelta(days=1)
            )
            has_future = next(
                iter_occurrence_dates(rule, start, rule.end_date, limit=1),
                None,
            )

            if has_future is None:
                rule.is_active = False
                rule.next_generation_at = None
                return

        rule.next_generation_at = current + timedelta(
            seconds=max(1, settings.RECURRENCE_GENERATION_INTERVAL_SECONDS)
        )


recurrence_service = RecurrenceService()
