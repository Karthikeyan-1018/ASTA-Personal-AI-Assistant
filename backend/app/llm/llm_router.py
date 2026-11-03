from app.llm.groq_provider import groq_provider
from app.llm.ollama_provider import ollama_provider


class LLMRouter:

    async def generate_response(
        self,
        prompt: str,
        private: bool = False
    ) -> dict:

        # Private requests must use the local model
        if private:

            response = await ollama_provider.generate_response(prompt)

            return {
                "provider": "ollama",
                "model": "qwen3:8b",
                "response": response
            }

        # Normal requests use Groq first
        try:

            response = await groq_provider.generate_response(prompt)

            return {
                "provider": "groq",
                "model": groq_provider.model,
                "response": response
            }

        except Exception:

            # Fall back to local Ollama if Groq fails
            response = await ollama_provider.generate_response(prompt)

            return {
                "provider": "ollama",
                "model": "qwen3:8b",
                "response": response
            }


# Create a reusable router
llm_router = LLMRouter()