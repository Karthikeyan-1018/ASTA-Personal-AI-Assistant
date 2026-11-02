
import ollama

from app.core.config import settings


class OllamaProvider:

    def __init__(self):
        # Connect to the local Ollama server
        self.client = ollama.AsyncClient(
            host=settings.OLLAMA_BASE_URL
        )

        # Select the configured model
        self.model = settings.OLLAMA_MODEL

    async def generate_response(self, prompt: str) -> str:
        """
        Send a prompt to Qwen3 and return its response.
        """

        try:
            response = await self.client.chat(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                options={
                    "num_ctx": settings.OLLAMA_NUM_CTX
                }
            )

            return response.message.content

        except Exception as error:
            raise RuntimeError(
                f"Ollama API error: {error}"
            ) from error


# Create a reusable provider instance
ollama_provider = OllamaProvider()
