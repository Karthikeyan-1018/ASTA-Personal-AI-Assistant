
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

    # PostgreSQL database configuration
    DB_HOST: str = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT: int = int(os.getenv("DB_PORT", "5432"))
    DB_NAME: str = os.getenv("DB_NAME", "asta_db")
    DB_USER: str = os.getenv("DB_USER", "postgres")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")

    # Semantic memory configuration
    EMBEDDING_MODEL_NAME: str = os.getenv(
        "EMBEDDING_MODEL_NAME",
        "intfloat/multilingual-e5-base"
    )

    EMBEDDING_DIMENSION: int = int(
        os.getenv("EMBEDDING_DIMENSION", "768")
    )

    EMBEDDING_DEVICE: str = os.getenv(
        "EMBEDDING_DEVICE",
        "cpu"
    )

    EMBEDDING_BATCH_SIZE: int = int(
        os.getenv("EMBEDDING_BATCH_SIZE", "16")
    )

    SEMANTIC_SIMILARITY_THRESHOLD: float = float(
        os.getenv("SEMANTIC_SIMILARITY_THRESHOLD", "0.75")
    )

    SEMANTIC_RETRIEVAL_ENABLED: bool = (
        os.getenv("SEMANTIC_RETRIEVAL_ENABLED", "true").lower() == "true"
    )

# Create a shared settings object
settings = Settings()
