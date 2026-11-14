
from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from contextlib import asynccontextmanager
import logging

from app.api.ai_usage import router as ai_usage_router
from app.api.auth import router as auth_router
from app.api.background_tasks import router as background_tasks_router
from app.api.health import router as health_router
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.memory import router as memory_router
from app.api.notifications import router as notifications_router
from app.api.preferences import router as preferences_router
from app.api.recurrences import router as recurrences_router
from app.api.reminders import router as reminders_router
from app.api.task_actions import router as task_actions_router
from app.api.tasks import router as tasks_router
from app.api.tools import router as tools_router
from app.core.config import settings
from app.database.connection import AsyncSessionLocal
from app.scheduler.runner import BackgroundScheduler
from app.services.background_task_service import background_task_service
from app.services.tool_history_service import tool_history_service
from app.tools import os_security


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start and stop the database-backed background scheduler.

    Worker failures are isolated from request handling. When the scheduler is
    disabled the API still operates normally; due work remains in the database
    and is picked up when a scheduler is enabled again.

    Stale in-flight tool executions from a previous process are marked
    cancelled so they cannot remain ``running`` forever. Recovery is best
    effort and never prevents the API from starting.
    """

    scheduler: BackgroundScheduler | None = None

    # Phase 12F: surface OS automation configuration problems at startup.
    # Validation never enables a capability; invalid limits fall back to the
    # conservative defaults at use time.
    try:
        for problem in os_security.validate_configuration():
            logger.warning("OS automation configuration: %s", problem)

    except Exception:
        logger.exception("OS automation configuration validation failed.")

    if settings.TOOL_RECOVERY_ON_STARTUP:
        try:
            async with AsyncSessionLocal() as session:
                recovered = await tool_history_service.recover_interrupted(
                    session=session
                )

                if recovered:
                    logger.warning(
                        "Marked %s interrupted tool execution(s) as cancelled "
                        "on startup.",
                        recovered,
                    )

        except Exception:
            logger.exception(
                "Tool execution recovery failed on startup; continuing."
            )

    # Phase 13: resolve durable task rows left behind by a previous process.
    # Running work cannot be resumed safely, so it becomes failed; queued work
    # becomes cancelled. Nothing is retried automatically.
    if (
        settings.BACKGROUND_TASKS_ENABLED
        and settings.BACKGROUND_TASK_RECOVERY_ON_STARTUP
    ):
        try:
            async with AsyncSessionLocal() as session:
                recovered = await background_task_service.recover_interrupted(
                    session
                )

                if recovered["failed"] or recovered["cancelled"]:
                    logger.warning(
                        "Background task recovery: %s running task(s) marked "
                        "failed and %s queued task(s) marked cancelled.",
                        recovered["failed"],
                        recovered["cancelled"],
                    )

                purged = await background_task_service.purge_expired(session)

                if purged:
                    logger.info(
                        "Purged %s expired background task(s).",
                        purged,
                    )

        except Exception:
            logger.exception(
                "Background task recovery failed on startup; continuing."
            )

    if settings.REMINDER_SCHEDULER_ENABLED:
        scheduler = BackgroundScheduler()
        await scheduler.start()

    try:
        yield

    finally:
        if scheduler is not None:
            await scheduler.stop()

        if settings.BACKGROUND_TASKS_ENABLED:
            try:
                await background_task_service.shutdown()

            except Exception:
                logger.exception(
                    "Background task shutdown failed; continuing."
                )


app = FastAPI(
    title="ASTA",
    description="Your Personal AI Assistant",
    version="1.0.0",
    lifespan=lifespan,
)

# Reject arbitrary Host headers. Add the laptop's LAN IP or mDNS name to
# TRUSTED_HOSTS before accessing ASTA from the phone.
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.trusted_hosts,
    www_redirect=False,
)

# CORS is opt-in and uses an explicit origin allowlist. Credentials are never
# combined with a wildcard origin.
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS", "HEAD"],
        allow_headers=[
            "Content-Type",
            "X-CSRF-Token",
            "X-Setup-Token",
            "Idempotency-Key",
        ],
        max_age=600,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError
):
    # Invalid chat request bodies should return HTTP 400, not 422.
    if request.url.path.startswith("/api/chat"):
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid chat request body."}
        )

    # Keep FastAPI's default validation response for all other routes.
    return await request_validation_exception_handler(request, exc)


# Register chat API
app.include_router(chat_router)

app.include_router(auth_router)

app.include_router(health_router)

app.include_router(ai_usage_router)

app.include_router(memory_router)

app.include_router(conversations_router)

app.include_router(preferences_router)

app.include_router(tasks_router)

app.include_router(reminders_router)

app.include_router(notifications_router)

app.include_router(recurrences_router)

app.include_router(task_actions_router)
app.include_router(tools_router)
app.include_router(background_tasks_router)


@app.get("/")
async def home():
    return {
        "application": "ASTA",
        "message": "Welcome to ASTA!",
        "status": "Running"
    }


@app.get("/health")
async def health_check():
    return {
        "application": "ASTA",
        "status": "Healthy"
    }
