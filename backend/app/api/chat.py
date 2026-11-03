from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.chat_service import chat_service


router = APIRouter(prefix="/api/chat", tags=["Chat"])


class ChatRequest(BaseModel):
    message: str
    private: bool = False


class ChatResponse(BaseModel):
    provider: str
    model: str
    response: str


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest):

    try:
        result = await chat_service.get_response(
            prompt=request.message,
            private=request.private
        )

        return result

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )

    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail=str(error)
        ) from error