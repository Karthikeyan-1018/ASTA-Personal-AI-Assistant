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
    retry_count: int = 0
    execution_id: int | None = None
    idempotent_replay: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionResponse(BaseModel):
    id: int
    tool_name: str
    category: str
    status: str
    lifecycle_status: str
    success: bool
    confirmation_status: str
    duration_ms: int | None
    retry_count: int = 0
    truncated: bool = False
    error_category: str | None
    error_code: str | None
    provider: str | None
    argument_keys: list[str] | None
    result_bytes: int | None
    conversation_id: int | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ToolExecutionStatusResponse(BaseModel):
    """Metadata-only status view for one execution attempt."""

    id: int
    tool_name: str
    category: str
    status: str
    lifecycle_status: str
    success: bool
    confirmation_status: str
    duration_ms: int | None = None
    retry_count: int = 0
    truncated: bool = False
    error_code: str | None = None
    error_category: str | None = None
    conversation_id: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ToolExecutionSummaryResponse(BaseModel):
    """Compact, safe summary without arguments, results, or payloads."""

    id: int
    tool_name: str
    category: str
    status: str
    lifecycle_status: str
    success: bool
    duration_ms: int | None = None
    retry_count: int = 0
    truncated: bool = False
    error_code: str | None = None
    created_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class ToolExecutionListResponse(BaseModel):
    executions: list[ToolExecutionResponse]
    total: int
    limit: int
    offset: int


class ToolStatsResponse(BaseModel):
    total: int
    by_status: dict[str, int]
    by_lifecycle: dict[str, int] = Field(default_factory=dict)
    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    cancelled_count: int = 0
    provider_failure_count: int = 0
    retry_count: int = 0
    confirmation_counts: dict[str, int] = Field(default_factory=dict)
    confirmation_approvals: int = 0
    confirmation_rejections: int = 0
    average_duration_ms: float | None
    top_tools: list[dict[str, Any]]


class ToolMetricsResponse(BaseModel):
    """Process-local operational metrics; never includes payload data."""

    executions_total: int = 0
    success_total: int = 0
    failure_total: int = 0
    timeout_total: int = 0
    cancelled_total: int = 0
    provider_failure_total: int = 0
    retry_total: int = 0
    confirmation_approved_total: int = 0
    confirmation_rejected_total: int = 0
    duplicate_prevented_total: int = 0
    recovery_total: int = 0
    duration_samples: int = 0
    average_duration_ms: float | None = None
    max_duration_ms: float | None = None
    by_tool: dict[str, dict[str, int]] = Field(default_factory=dict)
    since: str | None = None


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


class EmergencyStopResponse(BaseModel):
    """Safe, metadata-only view of the OS automation emergency stop."""

    engaged: bool
    engaged_at: datetime | None = None
    engaged_by_user_id: int | None = None
    recovery_configured: bool = False


__all__ = [
    "EmergencyStopResponse",
    "ToolConfirmationActionResponse",
    "ToolConfirmationListResponse",
    "ToolConfirmationResponse",
    "ToolDefinitionResponse",
    "ToolExecuteRequest",
    "ToolExecutionListResponse",
    "ToolExecutionResponse",
    "ToolExecutionStatusResponse",
    "ToolExecutionSummaryResponse",
    "ToolListResponse",
    "ToolMetricsResponse",
    "ToolResultResponse",
    "ToolStatsResponse",
]
