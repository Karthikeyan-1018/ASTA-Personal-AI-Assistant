"""Validated response models for authenticated host monitoring."""

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class MetricSection(BaseModel):
    """Common validated status fields; metric-specific values remain typed by source."""

    model_config = ConfigDict(extra="allow")

    available: bool
    reason: str | None = None


class SystemMonitorSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collected_at: datetime
    refresh_interval_seconds: float = Field(ge=1, le=10)
    cpu: MetricSection
    memory: MetricSection
    disk: MetricSection
    network: MetricSection
    gpu: MetricSection
    system: MetricSection
    processes: MetricSection
