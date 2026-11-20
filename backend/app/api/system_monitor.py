"""Authenticated, read-only live telemetry for the System Monitor page."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_current_user_id, require_csrf
from app.schemas.system_monitor import SystemMonitorSnapshotResponse
from app.services.system_monitor import collect_snapshot
from app.tools.implementations.process_inspection import SystemResourceSummaryTool
from app.tools.implementations.system_info import SystemSummaryTool
from app.tools.permissions import permission_policy


logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/system-monitor",
    tags=["System Monitor"],
    dependencies=[Depends(require_csrf)],
)


@router.get("/snapshot", response_model=SystemMonitorSnapshotResponse)
async def get_system_monitor_snapshot(
    _user_id: int = Depends(get_current_user_id),
) -> SystemMonitorSnapshotResponse:
    """Collect real host counters, honoring existing read-only OS policies."""

    system_decision = permission_policy.evaluate(
        SystemSummaryTool(), private=False
    )
    process_decision = permission_policy.evaluate(
        SystemResourceSummaryTool(), private=False
    )

    try:
        snapshot = await asyncio.to_thread(
            collect_snapshot,
            include_system_info=system_decision.allowed,
            include_processes=process_decision.allowed,
        )
    except Exception:
        logger.exception("System Monitor snapshot collection failed.")
        raise HTTPException(
            status_code=503,
            detail="System measurements could not be collected.",
        ) from None

    if not system_decision.allowed:
        reason = system_decision.reason or "Disabled by deployment policy."
        for section in ("cpu", "memory", "disk", "network", "gpu", "system"):
            snapshot[section] = {"available": False, "reason": reason}
    if not process_decision.allowed:
        snapshot["processes"] = {
            "available": False,
            "reason": process_decision.reason or "Disabled by deployment policy.",
        }

    return SystemMonitorSnapshotResponse(**snapshot)
