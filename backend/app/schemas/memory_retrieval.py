"""Pydantic schemas for context-aware memory retrieval."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _strip_not_blank(value: str) -> str:
    cleaned = value.strip()

    if not cleaned:
        raise ValueError("must not be blank")

    return cleaned


class MemoryRetrievalRequest(BaseModel):
    """Payload for retrieving memories relevant to a query."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        ...,
        max_length=10000
    )

    category: str | None = Field(
        default=None,
        max_length=50
    )

    limit: int = Field(
        default=5,
        ge=1,
        le=50
    )

    mode: Literal["keyword", "semantic"] = "keyword"

    @field_validator("category")
    @classmethod
    def strip_category(cls, value: str | None) -> str | None:
        if value is None:
            return value

        return _strip_not_blank(value)


class RetrievedMemoryItem(BaseModel):
    """A single ranked memory returned by the retrieval API."""

    content: str

    category: str

    relevance_score: float = Field(
        ...,
        ge=0.0,
        le=1.0
    )


class MemoryRetrievalResponse(BaseModel):
    """Ranked memories returned by the retrieval API."""

    memories: list[RetrievedMemoryItem]

    total: int
