"""Pydantic schemas for conversation persistence."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.message import MessageRole


MAX_TITLE_LENGTH = 255


def _strip_title(value: str) -> str:
    cleaned = value.strip()

    if not cleaned:
        raise ValueError("must not be blank")

    return cleaned


class ConversationCreate(BaseModel):
    """Payload for creating a conversation."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(
        default=None,
        max_length=MAX_TITLE_LENGTH
    )

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str | None) -> str | None:
        if value is None:
            return value

        return _strip_title(value)


class ConversationUpdate(BaseModel):
    """Payload for renaming a conversation."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        ...,
        min_length=1,
        max_length=MAX_TITLE_LENGTH
    )

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        return _strip_title(value)


class MessageResponse(BaseModel):
    """A single persisted message."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    role: MessageRole
    content: str
    provider: str | None = None
    is_private: bool = False
    created_at: datetime


class ConversationResponse(BaseModel):
    """Conversation metadata returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    title: str
    created_at: datetime
    updated_at: datetime


class MessageListResponse(BaseModel):
    """A chronological page of messages plus pagination metadata."""

    messages: list[MessageResponse]
    total: int
    limit: int
    offset: int
