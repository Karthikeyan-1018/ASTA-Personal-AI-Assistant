
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

    # Conversation persistence configuration
    CONVERSATION_HISTORY_LIMIT: int = int(
        os.getenv("CONVERSATION_HISTORY_LIMIT", "20")
    )

    CONVERSATION_MESSAGE_PAGE_SIZE: int = int(
        os.getenv("CONVERSATION_MESSAGE_PAGE_SIZE", "50")
    )

    # User preference configuration
    PREFERENCE_RETRIEVAL_LIMIT: int = int(
        os.getenv("PREFERENCE_RETRIEVAL_LIMIT", "8")
    )

    PREFERENCE_RELEVANCE_THRESHOLD: float = float(
        os.getenv("PREFERENCE_RELEVANCE_THRESHOLD", "0.15")
    )

    PREFERENCE_MAX_VALUE_CHARS: int = int(
        os.getenv("PREFERENCE_MAX_VALUE_CHARS", "1000")
    )

    # Unified context configuration
    CONTEXT_MAX_CHARS: int = int(
        os.getenv("CONTEXT_MAX_CHARS", "12000")
    )

    CONTEXT_PREFERENCE_SECTION_CHARS: int = int(
        os.getenv("CONTEXT_PREFERENCE_SECTION_CHARS", "2500")
    )

    # Defaults match the pre-Phase 7C renderer constants so existing
    # deployments keep their current behaviour while allowing tuning.
    CONTEXT_MEMORY_SECTION_CHARS: int = int(
        os.getenv("CONTEXT_MEMORY_SECTION_CHARS", "5000")
    )

    CONTEXT_HISTORY_SECTION_CHARS: int = int(
        os.getenv("CONTEXT_HISTORY_SECTION_CHARS", "6000")
    )

    CONTEXT_TASK_SECTION_CHARS: int = int(
        os.getenv("CONTEXT_TASK_SECTION_CHARS", "2000")
    )

    CONTEXT_DEADLINE_SECTION_CHARS: int = int(
        os.getenv("CONTEXT_DEADLINE_SECTION_CHARS", "2000")
    )

    # Task and deadline configuration
    TASK_DEFAULT_UPCOMING_DAYS: int = int(
        os.getenv("TASK_DEFAULT_UPCOMING_DAYS", "7")
    )

    TASK_CONTEXT_UPCOMING_DAYS: int = int(
        os.getenv("TASK_CONTEXT_UPCOMING_DAYS", "7")
    )

    TASK_CONTEXT_LIMIT: int = int(
        os.getenv("TASK_CONTEXT_LIMIT", "5")
    )

    DEADLINE_CONTEXT_LIMIT: int = int(
        os.getenv("DEADLINE_CONTEXT_LIMIT", "5")
    )

    # Natural-language task management configuration
    TASK_INTENT_ENABLED: bool = (
        os.getenv("TASK_INTENT_ENABLED", "true").lower() == "true"
    )

    TASK_INTENT_LLM_ENABLED: bool = (
        os.getenv("TASK_INTENT_LLM_ENABLED", "true").lower() == "true"
    )

    TASK_ACTION_CONFIRMATION_TTL_SECONDS: int = int(
        os.getenv("TASK_ACTION_CONFIRMATION_TTL_SECONDS", "600")
    )

    # Empty means "ask for a timezone" for timed deadlines.
    TASK_DEFAULT_TIMEZONE: str = os.getenv(
        "TASK_DEFAULT_TIMEZONE",
        ""
    )

    TASK_INTENT_MAX_RESULTS: int = int(
        os.getenv("TASK_INTENT_MAX_RESULTS", "20")
    )

    # Sources are listed from highest to lowest priority. The current user
    # message always has the highest priority and is never truncated.
    CONTEXT_PRIORITY_ORDER: str = os.getenv(
        "CONTEXT_PRIORITY_ORDER",
        "history,preference,memory,task,deadline"
    )

# Create a shared settings object
settings = Settings()
