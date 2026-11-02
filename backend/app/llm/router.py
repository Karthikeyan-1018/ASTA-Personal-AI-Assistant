from fastapi import FastAPI
from app.api.chat import router as chat_router

app = FastAPI(
    title="ASTA",
    description="Your Personal AI Assistant",
    version="1.0.0"
)

app.include_router(chat_router)


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