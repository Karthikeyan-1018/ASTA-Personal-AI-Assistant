import asyncio

from app.llm.groq_provider import groq_provider


async def main():

    print("Testing ASTA Groq Provider...")

    try:
        response = await groq_provider.generate_response(
            "Explain Machine Learning in simple words with an example."
        )

        print("\nGroq Provider is working!")
        print("\nAI Response:")
        print(response)

    except Exception as error:
        print("\nGroq Provider test failed!")
        print(error)

    finally:
        await groq_provider.client.close()


if __name__ == "__main__":
    asyncio.run(main())