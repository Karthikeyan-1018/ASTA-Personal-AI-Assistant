"""User-scoped persistence and lifecycle operations for preferences."""

import logging
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.secret_patterns import contains_secret
from app.exceptions.preference_exceptions import (
    PreferenceConflictError,
    PreferenceNotFoundError,
    PreferenceValidationError,
)
from app.models.preference import (
    PreferenceCategory,
    PreferenceSource,
    PreferenceStatus,
    UserPreference,
)


logger = logging.getLogger(__name__)


MAX_KEY_LENGTH = 100
MAX_CATEGORY_LENGTH = 50


def _normalise_key(key: str) -> str:
    """Return a stable, lowercase key suitable for uniqueness checks."""

    if contains_secret(key or ""):
        raise PreferenceValidationError(
            "Preference key appears to contain a secret and was not stored."
        )

    cleaned = re.sub(r"[^a-z0-9]+", "_", (key or "").strip().casefold())
    cleaned = cleaned.strip("_")

    if not cleaned:
        raise PreferenceValidationError(
            "Preference key cannot be empty."
        )

    if len(cleaned) > MAX_KEY_LENGTH:
        raise PreferenceValidationError(
            "Preference key cannot exceed "
            f"{MAX_KEY_LENGTH} characters."
        )

    return cleaned


def _normalise_value(value: str) -> str:
    cleaned = (value or "").strip()

    if not cleaned:
        raise PreferenceValidationError(
            "Preference value cannot be empty."
        )

    if len(cleaned) > settings.PREFERENCE_MAX_VALUE_CHARS:
        raise PreferenceValidationError(
            "Preference value cannot exceed "
            f"{settings.PREFERENCE_MAX_VALUE_CHARS} characters."
        )

    if contains_secret(cleaned):
        raise PreferenceValidationError(
            "Preference value appears to contain a secret and was not stored."
        )

    return cleaned


def _normalise_category(category: PreferenceCategory | str) -> str:
    if isinstance(category, PreferenceCategory):
        return category.value

    cleaned = (category or "").strip().lower()

    if not cleaned:
        return PreferenceCategory.GENERAL.value

    if len(cleaned) > MAX_CATEGORY_LENGTH:
        raise PreferenceValidationError(
            "Preference category cannot exceed "
            f"{MAX_CATEGORY_LENGTH} characters."
        )

    try:
        return PreferenceCategory(cleaned).value

    except ValueError as error:
        allowed = ", ".join(item.value for item in PreferenceCategory)
        raise PreferenceValidationError(
            f"Unsupported preference category. Allowed: {allowed}."
        ) from error


def _normalise_status(status: PreferenceStatus | str) -> PreferenceStatus:
    if isinstance(status, PreferenceStatus):
        return status

    try:
        return PreferenceStatus((status or "").strip().lower())

    except ValueError as error:
        raise PreferenceValidationError(
            "Unsupported preference status."
        ) from error


class PreferenceService:
    """Asynchronous CRUD and lifecycle operations for preferences."""

    async def _get_by_key(
        self,
        session: AsyncSession,
        user_id: int,
        key: str
    ) -> UserPreference | None:

        result = await session.execute(
            select(UserPreference).where(
                UserPreference.user_id == user_id,
                UserPreference.key == key
            )
        )

        return result.scalar_one_or_none()

    async def create_preference(
        self,
        session: AsyncSession,
        user_id: int,
        key: str,
        value: str,
        category: PreferenceCategory | str = PreferenceCategory.GENERAL,
        source: PreferenceSource | str = PreferenceSource.EXPLICIT,
        status: PreferenceStatus | str = PreferenceStatus.ACTIVE
    ) -> UserPreference:
        """Create a preference.

        Explicit user operations use ``EXPLICIT``/``ACTIVE``. Extracted
        suggestions use ``INFERRED``/``PENDING`` and require approval.
        """

        normalised_key = _normalise_key(key)
        normalised_value = _normalise_value(value)
        normalised_category = _normalise_category(category)

        if isinstance(source, PreferenceSource):
            normalised_source = source
        else:
            try:
                normalised_source = PreferenceSource(
                    (source or "").strip().lower()
                )
            except ValueError as error:
                raise PreferenceValidationError(
                    "Unsupported preference source."
                ) from error

        normalised_status = _normalise_status(status)

        existing = await self._get_by_key(
            session=session,
            user_id=user_id,
            key=normalised_key
        )

        if existing is not None:
            raise PreferenceConflictError(
                "A preference with this key already exists."
            )

        preference = UserPreference(
            user_id=user_id,
            key=normalised_key,
            value=normalised_value,
            category=normalised_category,
            status=normalised_status,
            source=normalised_source
        )

        try:
            session.add(preference)
            await session.commit()
            await session.refresh(preference)

        except IntegrityError as error:
            await session.rollback()
            raise PreferenceConflictError(
                "A preference with this key already exists."
            ) from error

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to create a preference for user %s.",
                user_id
            )
            raise

        return preference

    async def create_suggestion(
        self,
        session: AsyncSession,
        user_id: int,
        key: str,
        value: str,
        category: PreferenceCategory | str = PreferenceCategory.GENERAL
    ) -> UserPreference | None:
        """Persist an inferred preference as a pending suggestion.

        Returns ``None`` when an equivalent preference or suggestion already
        exists, so extraction cannot create duplicates.
        """

        try:
            normalised_key = _normalise_key(key)

            existing = await self._get_by_key(
                session=session,
                user_id=user_id,
                key=normalised_key
            )

            if existing is not None:
                return None

            return await self.create_preference(
                session=session,
                user_id=user_id,
                key=normalised_key,
                value=value,
                category=category,
                source=PreferenceSource.INFERRED,
                status=PreferenceStatus.PENDING
            )

        except PreferenceConflictError:
            return None

    async def get_preference(
        self,
        session: AsyncSession,
        user_id: int,
        preference_id: int
    ) -> UserPreference:
        """Return an owned preference or raise ``PreferenceNotFoundError``."""

        result = await session.execute(
            select(UserPreference).where(
                UserPreference.id == preference_id,
                UserPreference.user_id == user_id
            )
        )

        preference = result.scalar_one_or_none()

        if preference is None:
            raise PreferenceNotFoundError("Preference not found.")

        return preference

    async def list_preferences(
        self,
        session: AsyncSession,
        user_id: int,
        category: PreferenceCategory | str | None = None,
        status: PreferenceStatus | str | None = None,
        active_only: bool = False
    ) -> list[UserPreference]:

        query = select(UserPreference).where(
            UserPreference.user_id == user_id
        )

        if category is not None:
            query = query.where(
                UserPreference.category == _normalise_category(category)
            )

        if active_only:
            query = query.where(
                UserPreference.status == PreferenceStatus.ACTIVE
            )

        elif status is not None:
            query = query.where(
                UserPreference.status == _normalise_status(status)
            )

        query = query.order_by(
            UserPreference.created_at.desc(),
            UserPreference.id.desc()
        )

        result = await session.execute(query)

        return list(result.scalars().all())

    async def get_active_preferences(
        self,
        session: AsyncSession,
        user_id: int
    ) -> list[UserPreference]:

        return await self.list_preferences(
            session=session,
            user_id=user_id,
            active_only=True
        )

    async def list_suggestions(
        self,
        session: AsyncSession,
        user_id: int
    ) -> list[UserPreference]:

        result = await session.execute(
            select(UserPreference)
            .where(
                UserPreference.user_id == user_id,
                UserPreference.source == PreferenceSource.INFERRED,
                UserPreference.status == PreferenceStatus.PENDING
            )
            .order_by(
                UserPreference.created_at.desc(),
                UserPreference.id.desc()
            )
        )

        return list(result.scalars().all())

    async def update_preference(
        self,
        session: AsyncSession,
        user_id: int,
        preference_id: int,
        key: str | None = None,
        value: str | None = None,
        category: PreferenceCategory | str | None = None,
        status: PreferenceStatus | str | None = None
    ) -> UserPreference:

        if all(
            item is None
            for item in (key, value, category, status)
        ):
            raise PreferenceValidationError(
                "Provide at least one field to update."
            )

        preference = await self.get_preference(
            session=session,
            user_id=user_id,
            preference_id=preference_id
        )

        if key is not None:
            normalised_key = _normalise_key(key)

            if normalised_key != preference.key:
                existing = await self._get_by_key(
                    session=session,
                    user_id=user_id,
                    key=normalised_key
                )

                if existing is not None:
                    raise PreferenceConflictError(
                        "A preference with this key already exists."
                    )

                preference.key = normalised_key

        if value is not None:
            preference.value = _normalise_value(value)

        if category is not None:
            preference.category = _normalise_category(category)

        if status is not None:
            preference.status = _normalise_status(status)

        try:
            await session.commit()
            await session.refresh(preference)

        except IntegrityError as error:
            await session.rollback()
            raise PreferenceConflictError(
                "A preference with this key already exists."
            ) from error

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to update preference %s for user %s.",
                preference_id,
                user_id
            )
            raise

        return preference

    async def deactivate_preference(
        self,
        session: AsyncSession,
        user_id: int,
        preference_id: int
    ) -> UserPreference:

        return await self.update_preference(
            session=session,
            user_id=user_id,
            preference_id=preference_id,
            status=PreferenceStatus.INACTIVE
        )

    async def delete_preference(
        self,
        session: AsyncSession,
        user_id: int,
        preference_id: int
    ) -> None:

        preference = await self.get_preference(
            session=session,
            user_id=user_id,
            preference_id=preference_id
        )

        try:
            await session.delete(preference)
            await session.commit()

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to delete preference %s for user %s.",
                preference_id,
                user_id
            )
            raise

    async def _resolve_suggestion(
        self,
        session: AsyncSession,
        user_id: int,
        suggestion_id: int
    ) -> UserPreference:

        suggestion = await self.get_preference(
            session=session,
            user_id=user_id,
            preference_id=suggestion_id
        )

        if suggestion.source != PreferenceSource.INFERRED:
            raise PreferenceValidationError(
                "Only inferred preferences can be approved or rejected."
            )

        if suggestion.status != PreferenceStatus.PENDING:
            raise PreferenceValidationError(
                "This suggestion has already been resolved."
            )

        return suggestion

    async def approve_suggestion(
        self,
        session: AsyncSession,
        user_id: int,
        suggestion_id: int
    ) -> UserPreference:

        await self._resolve_suggestion(
            session=session,
            user_id=user_id,
            suggestion_id=suggestion_id
        )

        return await self.update_preference(
            session=session,
            user_id=user_id,
            preference_id=suggestion_id,
            status=PreferenceStatus.ACTIVE
        )

    async def reject_suggestion(
        self,
        session: AsyncSession,
        user_id: int,
        suggestion_id: int
    ) -> UserPreference:

        await self._resolve_suggestion(
            session=session,
            user_id=user_id,
            suggestion_id=suggestion_id
        )

        return await self.update_preference(
            session=session,
            user_id=user_id,
            preference_id=suggestion_id,
            status=PreferenceStatus.REJECTED
        )


preference_service = PreferenceService()
