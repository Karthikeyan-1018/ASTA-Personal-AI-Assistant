import asyncio

from app.llm.groq_provider import groq_provider
from app.llm.ollama_provider import ollama_provider


async def main():

    print("Checking ASTA AI providers...\n")

    groq_status = await groq_provider.health_check()
    ollama_status = await ollama_provider.health_check()

    print("Groq status:", "Available" if groq_status else "Unavailable")

    print("Ollama status:", "Available" if ollama_status else "Unavailable")

    await groq_provider.client.close()
    await ollama_provider.client.close()


if __name__ == "__main__":
    asyncio.run(main())