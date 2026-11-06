
import asyncio

from app.llm.llm_router import llm_router
from app.llm.groq_provider import groq_provider


# Simulate Groq being unavailable
async def mock_groq_unavailable():
    return False


async def main():

    print("===== ASTA LLM ROUTER TEST =====")

    # Check provider availability
    print("\nChecking providers...")

    status = await llm_router.check_providers()

    print("Groq:", "Available" if status["groq"] else "Unavailable")
    print("Ollama:", "Available" if status["ollama"] else "Unavailable")

    # Test normal request
    print("\nTesting normal request...")

    result = await llm_router.generate_response(
        prompt="Explain Python in simple words.",
        private=False
    )

    print("Selected provider:", result["provider"])
    print("Selected model:", result["model"])

    # Test private request
    print("\nTesting private request...")

    result = await llm_router.generate_response(
        prompt="Explain data structures in simple words.",
        private=True
    )

    print("Selected provider:", result["provider"])
    print("Selected model:", result["model"])

    # Test automatic fallback
    print("\nTesting automatic fallback...")

    original_health_check = groq_provider.health_check

    try:
        groq_provider.health_check = mock_groq_unavailable

        result = await llm_router.generate_response(
            prompt="Explain machine learning in simple words.",
            private=False
        )

        print("Groq: Simulated unavailable")
        print("Selected provider:", result["provider"])
        print("Selected model:", result["model"])

        assert result["provider"] == "ollama", (
            "Fallback test failed: Ollama was not selected."
        )

        print("Fallback test: PASSED")

    finally:
        # Restore original Groq health check
        groq_provider.health_check = original_health_check


if __name__ == "__main__":
    asyncio.run(main())
