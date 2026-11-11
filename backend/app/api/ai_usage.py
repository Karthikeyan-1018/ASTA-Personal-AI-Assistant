"""Owner-only AI usage and performance endpoints (Phase 9F)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id, require_csrf
from app.database.connection import get_db
from app.services.usage_tracking_service import usage_tracking_service


router = APIRouter(
    prefix="/api/ai/usage",
    tags=["AI Usage"],
    dependencies=[Depends(require_csrf)],
)


@router.get("/summary")
async def usage_summary(
    days: int | None = Query(default=None, ge=1, le=3650),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Return aggregated usage metadata for the authenticated owner."""

    return await usage_tracking_service.summary(
        session=session,
        user_id=user_id,
        days=days,
    )


@router.get("/recent")
async def recent_usage(
    limit: int | None = Query(default=None, ge=1, le=500),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Return recent metadata-only usage rows for the authenticated owner."""

    return {
        "items": await usage_tracking_service.recent(
            session=session,
            user_id=user_id,
            limit=limit,
        )
    }
