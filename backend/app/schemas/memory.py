from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.memory import MemoryStatus


def _strip_not_blank(value: str) -> str:
    cleaned = value.strip()

    if not cleaned:
        raise ValueError("must not be blank")

    return cleaned


class MemoryCreate(BaseModel):
    """Payload for creating a memory."""

    content: str = Field(
        ...,
        min_length=1,
        max_length=10000
    )

    category: str = Field(
        default="general",
        min_length=1,
        max_length=50
    )

    @field_validator("content", "category")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return _strip_not_blank(value)


class MemoryUpdate(BaseModel):
    """Payload for updating a memory's content or category."""

    content: str | None = Field(
        default=None,
        min_length=1,
        max_length=10000
    )

    category: str | None = Field(
        default=None,
        min_length=1,
        max_length=50
    )

    @field_validator("content", "category")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return value

        return _strip_not_blank(value)


class MemoryStatusUpdate(BaseModel):
    """Payload for changing a memory's status."""

    status: MemoryStatus


class MemoryResponse(BaseModel):
    """A memory returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    content: str
    category: str
    status: MemoryStatus
    created_at: datetime
    updated_at: datetime
