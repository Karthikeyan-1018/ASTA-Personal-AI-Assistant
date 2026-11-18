"""Validated personality configuration for ASTA.

Settings in this module are deliberately separate from Groq/Ollama provider
configuration. No credentials or secrets belong here.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings


class PersonalityMode(str, Enum):
    """Supported ASTA behaviour modes."""

    NORMAL = "normal"
    MOTIVATIONAL = "motivational"
    FOCUS = "focus"
    CASUAL = "casual"


SUPPORTED_MODES = tuple(mode.value for mode in PersonalityMode)

MIN_MOTIVATION_INTENSITY = 1
MAX_MOTIVATION_INTENSITY = 5

DEFAULT_TRAITS = (
    "friendly",
    "confident",
    "determined",
    "supportive",
    "intelligent",
    "curious",
    "patient",
    "occasionally playful",
    "honest",
    "trustworthy",
)

DEFAULT_PHILOSOPHY = "Never give up. I'll keep moving forward with you."
DEFAULT_RESPONSE_STYLE = "natural, clear, concise, and conversational"

_MODE_ALIASES = {
    "default": PersonalityMode.NORMAL.value,
    "standard": PersonalityMode.NORMAL.value,
    "balanced": PersonalityMode.NORMAL.value,
    "motivate": PersonalityMode.MOTIVATIONAL.value,
    "motivated": PersonalityMode.MOTIVATIONAL.value,
    "encouraging": PersonalityMode.MOTIVATIONAL.value,
    "technical": PersonalityMode.FOCUS.value,
    "precise": PersonalityMode.FOCUS.value,
    "relaxed": PersonalityMode.CASUAL.value,
    "chill": PersonalityMode.CASUAL.value,
}


def normalise_mode(
    value: object,
    default: PersonalityMode = PersonalityMode.NORMAL,
) -> PersonalityMode:
    """Return a supported mode, falling back safely for unknown values."""

    if isinstance(value, PersonalityMode):
        return value

    cleaned = str(value or "").strip().casefold().replace("-", "_")
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    cleaned = _MODE_ALIASES.get(cleaned, cleaned)

    try:
        return PersonalityMode(cleaned)

    except ValueError:
        return default


def normalise_intensity(
    value: object,
    default: int = 3,
) -> int:
    """Clamp motivational intensity to the supported 1-5 range."""

    try:
        parsed = int(str(value).strip())

    except (TypeError, ValueError):
        return default

    return max(MIN_MOTIVATION_INTENSITY, min(parsed, MAX_MOTIVATION_INTENSITY))


def parse_personality_bool(value: object, default: bool) -> bool:
    """Parse a persisted or configured boolean without raising."""

    if isinstance(value, bool):
        return value

    cleaned = str(value or "").strip().casefold()

    if cleaned in {"true", "1", "yes", "on", "enabled"}:
        return True

    if cleaned in {"false", "0", "no", "off", "disabled"}:
        return False

    return default


class PersonalityConfig(BaseModel):
    """A complete, validated personality configuration."""

    model_config = ConfigDict(extra="forbid")

    assistant_name: str = Field(min_length=1, max_length=64)
    mode: PersonalityMode = PersonalityMode.NORMAL
    personality_traits: list[str] = Field(
        default_factory=lambda: list(DEFAULT_TRAITS)
    )
    motivational_philosophy: str = Field(max_length=240)
    proactive_motivation_enabled: bool = True
    motivation_intensity: int = Field(
        ge=MIN_MOTIVATION_INTENSITY,
        le=MAX_MOTIVATION_INTENSITY,
    )
    response_style: str = Field(max_length=240)
    humor_enabled: bool = True

    @field_validator("assistant_name", mode="before")
    @classmethod
    def _clean_name(cls, value: object) -> str:
        cleaned = str(value or "").strip()
        return cleaned or "ASTA"

    @field_validator("mode", mode="before")
    @classmethod
    def _clean_mode(cls, value: object) -> PersonalityMode:
        return normalise_mode(value)

    @field_validator("personality_traits", mode="before")
    @classmethod
    def _clean_traits(cls, value: object) -> list[str]:
        if isinstance(value, str):
            candidates = value.split(",")

        elif isinstance(value, (list, tuple, set)):
            candidates = list(value)

        else:
            candidates = []

        traits = [
            " ".join(str(item or "").split())
            for item in candidates
            if str(item or "").strip()
        ]
        traits = [item for item in traits if item][:12]

        return traits or list(DEFAULT_TRAITS)

    @field_validator("motivational_philosophy", mode="before")
    @classmethod
    def _clean_philosophy(cls, value: object) -> str:
        cleaned = " ".join(str(value or "").split())
        return cleaned or DEFAULT_PHILOSOPHY

    @field_validator("response_style", mode="before")
    @classmethod
    def _clean_response_style(cls, value: object) -> str:
        cleaned = " ".join(str(value or "").split())
        return cleaned or DEFAULT_RESPONSE_STYLE

    @field_validator("motivation_intensity", mode="before")
    @classmethod
    def _clean_intensity(cls, value: object) -> int:
        return normalise_intensity(value)


def load_personality_config() -> PersonalityConfig:
    """Build the canonical configuration from validated environment values."""

    return PersonalityConfig(
        assistant_name=getattr(
            settings,
            "PERSONALITY_ASSISTANT_NAME",
            "ASTA",
        ),
        mode=getattr(settings, "PERSONALITY_DEFAULT_MODE", "normal"),
        personality_traits=getattr(
            settings,
            "PERSONALITY_TRAITS",
            ", ".join(DEFAULT_TRAITS),
        ),
        motivational_philosophy=getattr(
            settings,
            "PERSONALITY_MOTIVATIONAL_PHILOSOPHY",
            DEFAULT_PHILOSOPHY,
        ),
        proactive_motivation_enabled=getattr(
            settings,
            "PERSONALITY_PROACTIVE_MOTIVATION",
            True,
        ),
        motivation_intensity=getattr(
            settings,
            "PERSONALITY_MOTIVATION_INTENSITY",
            3,
        ),
        response_style=getattr(
            settings,
            "PERSONALITY_RESPONSE_STYLE",
            DEFAULT_RESPONSE_STYLE,
        ),
        humor_enabled=getattr(
            settings,
            "PERSONALITY_HUMOR_ENABLED",
            True,
        ),
    )
