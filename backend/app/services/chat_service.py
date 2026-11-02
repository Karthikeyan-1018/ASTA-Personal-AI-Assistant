
from app.llm.ollama_provider import ollama_provider


class ChatService:

    async def get_response(self, prompt: str) -> dict:
        """
        Generate an AI response using the Ollama provider.
        """

        if not prompt or not prompt.strip():
            raise ValueError("Prompt cannot be empty")

        response = await ollama_provider.generate_response(
            prompt.strip()
        )

        return {
            "provider": "ollama",
            "model": "qwen3:8b",
            "response": response
        }


# Create a reusable service instance
chat_service = ChatService()
