from groq import AsyncGroq

from app.core.config import settings


class GroqProvider:

    def __init__(self):

        self.client = AsyncGroq(
            api_key=settings.GROQ_API_KEY
        )

        self.model = settings.GROQ_MODEL

    async def generate_response(self, prompt: str) -> str:

        try:

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )

            return response.choices[0].message.content

        except Exception as error:

            raise RuntimeError(
                f"Groq API error: {error}"
            ) from error


groq_provider = GroqProvider()