"""Pydantic schemas for structured user preferences and extraction."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.preference import (
    PreferenceCategory,
    PreferenceSource,
    PreferenceStatus,
)


MAX_KEY_LENGTH = 100
MAX_VALUE_LENGTH = 1000
MAX_CATEGORY_LENGTH = 50
MAX_CONVERSATION_LENGTH = 50000


def _strip_not_blank(value: str) -> str:
    cleaned = value.strip()

    if not cleaned:
        raise ValueError("must not be blank")

    return cleaned


class PreferenceCreate(BaseModel):
    """Payload for explicitly creating an active preference."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(
        ...,
        min_length=1,
        max_length=MAX_KEY_LENGTH
    )

    value: str = Field(
        ...,
        min_length=1,
        max_length=MAX_VALUE_LENGTH
    )

    category: PreferenceCategory = PreferenceCategory.GENERAL

    @field_validator("key", "value")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return _strip_not_blank(value)


class PreferenceUpdate(BaseModel):
    """Payload for updating an explicit preference."""

    model_config = ConfigDict(extra="forbid")

    key: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_KEY_LENGTH
    )

    value: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_VALUE_LENGTH
    )

    category: PreferenceCategory | None = None

    # Only active/inactive are accepted through the explicit update endpoint.
    # Pending and rejected states belong to the suggestion workflow.
    status: Literal["active", "inactive"] | None = None

    @field_validator("key", "value")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return value

        return _strip_not_blank(value)


class PreferenceResponse(BaseModel):
    """A stored user preference."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    key: str
    value: str
    category: str
    status: PreferenceStatus
    source: PreferenceSource
    created_at: datetime
    updated_at: datetime


class PreferenceExtractionRequest(BaseModel):
    """Conversation text to analyse for possible preferences."""

    model_config = ConfigDict(extra="forbid")

    conversation: str = Field(
        ...,
        min_length=1,
        max_length=MAX_CONVERSATION_LENGTH
    )

    private: bool = False

    @field_validator("conversation")
    @classmethod
    def strip_conversation(cls, value: str) -> str:
        return _strip_not_blank(value)


class ExtractedPreferenceCandidate(BaseModel):
    """An inferred preference that requires explicit user approval."""

    key: str = Field(
        ...,
        min_length=1,
        max_length=MAX_KEY_LENGTH
    )

    value: str = Field(
        ...,
        min_length=1,
        max_length=MAX_VALUE_LENGTH
    )

    category: PreferenceCategory

    explanation: str = Field(
        ...,
        min_length=1,
        max_length=1000
    )

    @field_validator("key", "value", "explanation")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return _strip_not_blank(value)

    @field_validator("category", mode="before")
    @classmethod
    def normalise_category(cls, value):
        if isinstance(value, str):
            cleaned = value.strip().lower()

            aliases = {
                "communication style": PreferenceCategory.COMMUNICATION,
                "communication_style": PreferenceCategory.COMMUNICATION,
                "learning style": PreferenceCategory.LEARNING,
                "learning_style": PreferenceCategory.LEARNING,
                "coding": PreferenceCategory.PROGRAMMING,
                "technical": PreferenceCategory.PROGRAMMING,
                "work": PreferenceCategory.PRODUCTIVITY,
                "productivity_style": PreferenceCategory.PRODUCTIVITY,
                "other": PreferenceCategory.GENERAL,
            }

            return aliases.get(cleaned, cleaned)

        return value


class PreferenceExtractionLLMResponse(BaseModel):
    """The strict shape expected from the LLM's JSON response."""

    preferences: list[ExtractedPreferenceCandidate]


class PreferenceExtractionResponse(BaseModel):
    """Candidate preferences returned by the extraction endpoint."""

    candidates: list[ExtractedPreferenceCandidate]


class PreferenceSuggestionListResponse(BaseModel):
    """Pending inferred preferences awaiting user confirmation."""

    suggestions: list[PreferenceResponse]


class PreferenceListResponse(BaseModel):
    """A user's preferences, optionally filtered."""

    preferences: list[PreferenceResponse]
