
import os
from pathlib import Path
from dotenv import load_dotenv

# Locate the root ASTA directory
BASE_DIR = Path(__file__).resolve().parents[3]

# Load environment variables from C:\Asta\.env
load_dotenv(BASE_DIR / ".env")


class Settings:

    # Application configuration
    APP_NAME: str = os.getenv("APP_NAME", "ASTA")
    DEBUG: bool = os.getenv("DEBUG", "True").lower() == "true"

    # Groq configuration
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv(
        "GROQ_MODEL",
        "openai/gpt-oss-20b"
    )

    # Ollama configuration
    OLLAMA_BASE_URL: str = os.getenv(
        "OLLAMA_BASE_URL",
        "http://localhost:11434"
    )

    OLLAMA_MODEL: str = os.getenv(
        "OLLAMA_MODEL",
        "qwen3:8b"
    )

    OLLAMA_NUM_CTX: int = int(
        os.getenv("OLLAMA_NUM_CTX", "4096")
    )
    
    # Provider timeout configuration (seconds)
    GROQ_TIMEOUT_SECONDS: int = int(
        os.getenv("GROQ_TIMEOUT_SECONDS", "30")
    )

    OLLAMA_TIMEOUT_SECONDS: int = int(
        os.getenv("OLLAMA_TIMEOUT_SECONDS", "120")
    )

# Create a shared settings object
settings = Settings()
