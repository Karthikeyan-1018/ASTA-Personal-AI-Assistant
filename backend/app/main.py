
from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.api.chat import router as chat_router

app = FastAPI(
    title="ASTA",
    description="Your Personal AI Assistant",
    version="1.0.0"
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

app.include_router(health_router)
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
