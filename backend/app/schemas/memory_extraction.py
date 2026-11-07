"""Pydantic schemas for intelligent memory extraction."""

import enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _strip_not_blank(value: str) -> str:
    cleaned = value.strip()

    if not cleaned:
        raise ValueError("must not be blank")

    return cleaned


class MemoryCategory(str, enum.Enum):
    """Categories a candidate long-term memory can belong to."""

    PERSONAL = "personal"
    EDUCATION = "education"
    CAREER = "career"
    PROJECTS = "projects"
    PREFERENCES = "preferences"
    GOALS = "goals"


# Common category names models may produce instead of the canonical ones.
_CATEGORY_ALIASES = {
    "profile": MemoryCategory.PERSONAL,
    "background": MemoryCategory.PERSONAL,
    "about": MemoryCategory.PERSONAL,
    "school": MemoryCategory.EDUCATION,
    "academics": MemoryCategory.EDUCATION,
    "learning": MemoryCategory.EDUCATION,
    "work": MemoryCategory.CAREER,
    "professional": MemoryCategory.CAREER,
    "job": MemoryCategory.CAREER,
    "project": MemoryCategory.PROJECTS,
    "preference": MemoryCategory.PREFERENCES,
    "interests": MemoryCategory.PREFERENCES,
    "interest": MemoryCategory.PREFERENCES,
    "goal": MemoryCategory.GOALS,
    "aspirations": MemoryCategory.GOALS,
}


class MemoryExtractionRequest(BaseModel):
    """Payload for extracting candidate memories from a conversation."""

    model_config = ConfigDict(extra="forbid")

    conversation: str = Field(
        ...,
        max_length=50000
    )

    private: bool = False


class ExtractedMemoryCandidate(BaseModel):
    """A potential long-term memory suggested by the extractor.

    Candidates are never persisted automatically. The user must approve
    one explicitly before it becomes a stored memory.
    """

    content: str = Field(
        ...,
        min_length=1,
        max_length=10000
    )

    category: MemoryCategory

    explanation: str = Field(
        ...,
        min_length=1,
        max_length=1000
    )

    @field_validator("content", "explanation")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return _strip_not_blank(value)

    @field_validator("category", mode="before")
    @classmethod
    def normalise_category(cls, value):
        if isinstance(value, str):
            cleaned = value.strip().lower()

            return _CATEGORY_ALIASES.get(cleaned, cleaned)

        return value


class MemoryExtractionLLMResponse(BaseModel):
    """The strict shape expected from the LLM's JSON response."""

    memories: list[ExtractedMemoryCandidate]


class MemoryExtractionResponse(BaseModel):
    """Candidate memories returned by the extraction endpoint."""

    candidates: list[ExtractedMemoryCandidate]
