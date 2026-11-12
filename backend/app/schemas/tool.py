"""API schemas for the tool registry, execution, and history."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ToolDefinitionResponse(BaseModel):
    name: str
    description: str
    category: str
    enabled: bool
    requires_confirmation: bool
    allowed_in_private: bool
    parameters: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)


class ToolListResponse(BaseModel):
    tools: list[ToolDefinitionResponse]


class ToolExecuteRequest(BaseModel):
    tool: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResultResponse(BaseModel):
    success: bool
    status: str
    tool: str | None = None
    data: Any = None
    error_code: str | None = None
    error_message: str | None = None
    error_category: str | None = None
    confirmation_required: bool = False
    confirmation_id: int | None = None
    confirmation_status: str | None = None
    truncated: bool = False
    duration_ms: float | None = None
    result_bytes: int | None = None
    provider: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionResponse(BaseModel):
    id: int
    tool_name: str
    category: str
    status: str
    confirmation_status: str
    duration_ms: int | None
    error_category: str | None
    error_code: str | None
    provider: str | None
    argument_keys: list[str] | None
    result_bytes: int | None
    conversation_id: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ToolExecutionListResponse(BaseModel):
    executions: list[ToolExecutionResponse]
    total: int
    limit: int
    offset: int


class ToolStatsResponse(BaseModel):
    total: int
    by_status: dict[str, int]
    average_duration_ms: float | None
    top_tools: list[dict[str, Any]]


class ToolConfirmationResponse(BaseModel):
    id: int
    tool_name: str
    category: str
    status: str
    arguments_preview: str
    created_at: datetime
    expires_at: datetime
    resolved_at: datetime | None
    conversation_id: int | None
    execution_id: int | None
    is_expired: bool = False

    model_config = {"from_attributes": True}


class ToolConfirmationListResponse(BaseModel):
    confirmations: list[ToolConfirmationResponse]


class ToolConfirmationActionResponse(BaseModel):
    confirmation: ToolConfirmationResponse
    result: ToolResultResponse | None = None


__all__ = [
    "ToolConfirmationActionResponse",
    "ToolConfirmationListResponse",
    "ToolConfirmationResponse",
    "ToolDefinitionResponse",
    "ToolExecuteRequest",
    "ToolExecutionListResponse",
    "ToolExecutionResponse",
    "ToolListResponse",
    "ToolResultResponse",
    "ToolStatsResponse",
]
