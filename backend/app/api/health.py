
from fastapi import APIRouter

from app.llm.llm_router import llm_router


router = APIRouter(
    prefix="/api/health",
    tags=["Health"]
)


@router.get("/providers")
async def provider_health():

    status = await llm_router.check_providers()

    groq_status = (
        "available" if status["groq"] else "unavailable"
    )

    ollama_status = (
        "available" if status["ollama"] else "unavailable"
    )

    overall_status = (
        "healthy"
        if status["groq"] and status["ollama"]
        else "degraded"
    )

    return {
        "groq": groq_status,
        "ollama": ollama_status,
        "overall_status": overall_status
    }
