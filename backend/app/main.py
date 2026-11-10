
from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.memory import router as memory_router
from app.api.preferences import router as preferences_router
from app.api.task_actions import router as task_actions_router
from app.api.tasks import router as tasks_router
from app.core.config import settings

app = FastAPI(
    title="ASTA",
    description="Your Personal AI Assistant",
    version="1.0.0"
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
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Setup-Token"],
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

app.include_router(memory_router)

app.include_router(conversations_router)

app.include_router(preferences_router)

app.include_router(tasks_router)

app.include_router(task_actions_router)


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
