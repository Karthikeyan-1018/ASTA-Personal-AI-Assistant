"""Per-user personality settings backed by the existing preference store.

No new table or migration is introduced. Fixed, validated keys are stored in
``user_preferences`` through the existing user-scoped preference service.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.preference import PreferenceCategory, PreferenceStatus
from app.personality.config import (
    PersonalityConfig,
    load_personality_config,
    normalise_intensity,
    normalise_mode,
    parse_personality_bool,
)
from app.services.preference_service import preference_service


PERSONALITY_MODE_KEY = "personality_mode"
PROACTIVE_MOTIVATION_KEY = "personality_proactive_motivation"
MOTIVATION_INTENSITY_KEY = "personality_motivation_intensity"

PERSONALITY_KEYS = (
    PERSONALITY_MODE_KEY,
    PROACTIVE_MOTIVATION_KEY,
    MOTIVATION_INTENSITY_KEY,
)


@dataclass(frozen=True)
class PersonalityState:
    """Resolved config plus whether the user has stored any overrides."""

    config: PersonalityConfig
    has_overrides: bool


class PersonalityService:
    """Resolve and update the authenticated user's personality settings."""

    async def _rows(
        self,
        session: AsyncSession | None,
        user_id: int | None,
    ) -> dict[str, Any]:
        if session is None or user_id is None:
            return {}

        preferences = await preference_service.list_preferences(
            session=session,
            user_id=user_id,
        )

        return {
            str(item.key): item
            for item in preferences
            if str(item.key) in PERSONALITY_KEYS
        }

    @staticmethod
    def _active_value(rows: dict[str, Any], key: str) -> str | None:
        row = rows.get(key)

        if row is None:
            return None

        raw_status = getattr(row, "status", "") or ""
        status = str(
            getattr(raw_status, "value", raw_status)
        ).strip().casefold()

        if status and status != PreferenceStatus.ACTIVE.value:
            return None

        return str(getattr(row, "value", "") or "").strip()

    async def get_state(
        self,
        session: AsyncSession | None = None,
        user_id: int | None = None,
    ) -> PersonalityState:
        """Return effective settings with safe fallback for invalid rows."""

        defaults = load_personality_config()

        rows = await self._rows(session, user_id)
        values = {
            key: self._active_value(rows, key)
            for key in PERSONALITY_KEYS
        }

        config = defaults.model_copy(
            update={
                "mode": normalise_mode(
                    values[PERSONALITY_MODE_KEY],
                    defaults.mode,
                ),
                "proactive_motivation_enabled": parse_personality_bool(
                    values[PROACTIVE_MOTIVATION_KEY],
                    defaults.proactive_motivation_enabled,
                ),
                "motivation_intensity": normalise_intensity(
                    values[MOTIVATION_INTENSITY_KEY],
                    defaults.motivation_intensity,
                ),
            }
        )

        return PersonalityState(
            config=config,
            has_overrides=any(value is not None for value in values.values()),
        )

    async def _upsert(
        self,
        session: AsyncSession,
        user_id: int,
        rows: dict[str, Any],
        key: str,
        value: str,
    ) -> None:
        row = rows.get(key)

        if row is not None:
            await preference_service.update_preference(
                session=session,
                user_id=user_id,
                preference_id=int(row.id),
                value=value,
                status=PreferenceStatus.ACTIVE,
            )
            return

        created = await preference_service.create_preference(
            session=session,
            user_id=user_id,
            key=key,
            value=value,
            category=PreferenceCategory.COMMUNICATION,
            source="explicit",
            status=PreferenceStatus.ACTIVE,
        )
        rows[key] = created

    async def update(
        self,
        session: AsyncSession,
        user_id: int,
        updates: dict[str, Any],
    ) -> PersonalityState:
        """Validate and persist only the supplied personality fields."""

        state = await self.get_state(session=session, user_id=user_id)
        rows = await self._rows(session, user_id)
        changed = False

        if "mode" in updates and updates["mode"] is not None:
            mode = normalise_mode(updates["mode"], state.config.mode)
            await self._upsert(
                session,
                user_id,
                rows,
                PERSONALITY_MODE_KEY,
                mode.value,
            )
            changed = True

        if (
            "proactive_motivation_enabled" in updates
            and updates["proactive_motivation_enabled"] is not None
        ):
            enabled = parse_personality_bool(
                updates["proactive_motivation_enabled"],
                state.config.proactive_motivation_enabled,
            )
            await self._upsert(
                session,
                user_id,
                rows,
                PROACTIVE_MOTIVATION_KEY,
                "true" if enabled else "false",
            )
            changed = True

        if (
            "motivation_intensity" in updates
            and updates["motivation_intensity"] is not None
        ):
            intensity = normalise_intensity(
                updates["motivation_intensity"],
                state.config.motivation_intensity,
            )
            await self._upsert(
                session,
                user_id,
                rows,
                MOTIVATION_INTENSITY_KEY,
                str(intensity),
            )
            changed = True

        if not changed:
            return state

        return await self.get_state(session=session, user_id=user_id)

    async def reset(
        self,
        session: AsyncSession,
        user_id: int,
    ) -> PersonalityState:
        """Remove user overrides and restore validated configured defaults."""

        rows = await self._rows(session, user_id)

        for key in PERSONALITY_KEYS:
            row = rows.get(key)

            if row is None:
                continue

            try:
                await preference_service.delete_preference(
                    session=session,
                    user_id=user_id,
                    preference_id=int(row.id),
                )

            except Exception:
                # A stale preference must not prevent an explicit reset from
                # returning the effective defaults.
                continue

        return await self.get_state(session=session, user_id=user_id)


personality_service = PersonalityService()
