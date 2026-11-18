"""Validation schemas for the user's personality settings."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.personality.config import (
    MAX_MOTIVATION_INTENSITY,
    MIN_MOTIVATION_INTENSITY,
    PersonalityMode,
)


class PersonalitySettingsResponse(BaseModel):
    """Effective personality settings for the authenticated user."""

    assistant_name: str
    mode: PersonalityMode
    personality_traits: list[str]
    motivational_philosophy: str
    proactive_motivation_enabled: bool
    motivation_intensity: int
    response_style: str
    humor_enabled: bool
    has_overrides: bool


class PersonalitySettingsUpdate(BaseModel):
    """Partial update. Unknown fields and invalid values are rejected."""

    model_config = ConfigDict(extra="forbid")

    mode: PersonalityMode | None = None
    proactive_motivation_enabled: bool | None = None
    motivation_intensity: int | None = Field(
        default=None,
        ge=MIN_MOTIVATION_INTENSITY,
        le=MAX_MOTIVATION_INTENSITY,
    )
