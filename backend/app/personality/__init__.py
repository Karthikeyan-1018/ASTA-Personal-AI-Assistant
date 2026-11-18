"""Shared, provider-independent ASTA personality engine.

The module exposes one canonical identity/prompt builder that is consumed by
text chat, streaming chat, voice chat, and both LLM providers.
"""

from app.personality.config import (
    DEFAULT_PHILOSOPHY,
    DEFAULT_TRAITS,
    MAX_MOTIVATION_INTENSITY,
    MIN_MOTIVATION_INTENSITY,
    PersonalityConfig,
    PersonalityMode,
    load_personality_config,
    normalise_intensity,
    normalise_mode,
    parse_personality_bool,
)
from app.personality.engine import (
    BASE_IDENTITY_SUMMARY,
    build_persona_instructions,
    personality_fingerprint,
)
from app.personality.motivation import MotivationDecision, detect_motivation
from app.personality.service import PersonalityState, personality_service

__all__ = [
    "BASE_IDENTITY_SUMMARY",
    "DEFAULT_PHILOSOPHY",
    "DEFAULT_TRAITS",
    "MAX_MOTIVATION_INTENSITY",
    "MIN_MOTIVATION_INTENSITY",
    "MotivationDecision",
    "PersonalityConfig",
    "PersonalityMode",
    "PersonalityState",
    "build_persona_instructions",
    "detect_motivation",
    "load_personality_config",
    "normalise_intensity",
    "normalise_mode",
    "parse_personality_bool",
    "personality_fingerprint",
    "personality_service",
]
