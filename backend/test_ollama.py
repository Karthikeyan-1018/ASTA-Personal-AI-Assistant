import asyncio
import ollama


async def main():

    client = ollama.AsyncClient(
        host="http://127.0.0.1:11434"
    )

    try:
        print("Checking Ollama health...")

        result = await client.list()

        print("\nOllama health check successful!")
        print("Installed models:")

        for model in result.models:
            print(model.model)

    except Exception as error:
        print("\nHealth check failed!")
        print(repr(error))

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())