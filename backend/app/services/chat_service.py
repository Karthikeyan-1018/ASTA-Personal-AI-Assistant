from app.llm.llm_router import llm_router


class ChatService:

    async def get_response(
        self,
        prompt: str,
        private: bool = False
    ) -> dict:

        if not prompt or not prompt.strip():
            raise ValueError("Prompt cannot be empty")

        result = await llm_router.generate_response(
            prompt=prompt.strip(),
            private=private
        )

        return result


# Create a reusable service instance
chat_service = ChatService()