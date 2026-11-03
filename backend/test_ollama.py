import asyncio
import ollama


async def main():

    client = ollama.AsyncClient(
        host="http://127.0.0.1:11434"
    )

    try:
        print("Connecting to Ollama...")

        response = await client.chat(
            model="qwen3:8b",
            messages=[
                {
                    "role": "user",
                    "content": "Explain AI in one sentence."
                }
            ]
        )

        print("\nOllama connection successful!")
        print(response.message.content)

    except Exception as error:
        print("\nConnection failed!")
        print(repr(error))

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())