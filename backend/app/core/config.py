
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

    # SQL echo is opt-in. It must not follow DEBUG because echoed statements
    # include bound parameters (user prompts, memory and task content).
    DB_ECHO: bool = os.getenv("DB_ECHO", "false").lower() == "true"

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

    # Single-owner authentication configuration.
    # Owner setup is disabled while this token is empty. Set a long random
    # value, create the owner, then remove it and restart the backend.
    AUTH_OWNER_SETUP_TOKEN: str = os.getenv(
        "AUTH_OWNER_SETUP_TOKEN",
        ""
    )

    AUTH_SESSION_LIFETIME_HOURS: int = int(
        os.getenv("AUTH_SESSION_LIFETIME_HOURS", "168")
    )

    AUTH_SESSION_COOKIE_NAME: str = os.getenv(
        "AUTH_SESSION_COOKIE_NAME",
        "asta_session"
    )

    AUTH_CSRF_COOKIE_NAME: str = os.getenv(
        "AUTH_CSRF_COOKIE_NAME",
        "asta_csrf"
    )

    # Secure by default. Only disable for local loopback HTTP development.
    AUTH_COOKIE_SECURE: bool = (
        os.getenv("AUTH_COOKIE_SECURE", "true").lower() == "true"
    )

    AUTH_COOKIE_SAMESITE: str = os.getenv(
        "AUTH_COOKIE_SAMESITE",
        "lax"
    ).lower()

    AUTH_COOKIE_PATH: str = os.getenv("AUTH_COOKIE_PATH", "/api")

    # The CSRF token must be readable by frontend JavaScript even when the SPA
    # is served from "/", while the session cookie stays restricted to /api.
    AUTH_CSRF_COOKIE_PATH: str = os.getenv(
        "AUTH_CSRF_COOKIE_PATH",
        "/"
    )

    # Throttle last-used writes so a busy session does not write on every
    # request.
    AUTH_SESSION_LAST_USED_UPDATE_SECONDS: int = int(
        os.getenv("AUTH_SESSION_LAST_USED_UPDATE_SECONDS", "300")
    )

    AUTH_PASSWORD_MIN_LENGTH: int = int(
        os.getenv("AUTH_PASSWORD_MIN_LENGTH", "12")
    )

    AUTH_PASSWORD_MAX_LENGTH: int = int(
        os.getenv("AUTH_PASSWORD_MAX_LENGTH", "128")
    )

    # Argon2id cost parameters. Defaults follow argon2-cffi's RFC 9106
    # low-memory profile; raise them only if the laptop can afford the cost.
    AUTH_ARGON2_TIME_COST: int = int(
        os.getenv("AUTH_ARGON2_TIME_COST", "3")
    )

    AUTH_ARGON2_MEMORY_COST: int = int(
        os.getenv("AUTH_ARGON2_MEMORY_COST", "65536")
    )

    AUTH_ARGON2_PARALLELISM: int = int(
        os.getenv("AUTH_ARGON2_PARALLELISM", "4")
    )

    # Login rate limiting. State lives in PostgreSQL so a backend restart does
    # not clear an active cooldown. Cooldowns always expire; the owner account
    # is never permanently locked.
    AUTH_LOGIN_RATE_LIMIT_ENABLED: bool = (
        os.getenv("AUTH_LOGIN_RATE_LIMIT_ENABLED", "true").lower() == "true"
    )

    AUTH_LOGIN_MAX_FAILED_ATTEMPTS: int = int(
        os.getenv("AUTH_LOGIN_MAX_FAILED_ATTEMPTS", "5")
    )

    AUTH_LOGIN_INITIAL_COOLDOWN_MINUTES: int = int(
        os.getenv("AUTH_LOGIN_INITIAL_COOLDOWN_MINUTES", "15")
    )

    AUTH_LOGIN_COOLDOWN_MULTIPLIER: int = int(
        os.getenv("AUTH_LOGIN_COOLDOWN_MULTIPLIER", "2")
    )

    AUTH_LOGIN_MAX_COOLDOWN_MINUTES: int = int(
        os.getenv("AUTH_LOGIN_MAX_COOLDOWN_MINUTES", "1440")
    )

    # Attempt rows whose last failure is older than this are purged. Keep this
    # comfortably larger than the maximum cooldown.
    AUTH_LOGIN_ATTEMPT_RETENTION_HOURS: int = int(
        os.getenv("AUTH_LOGIN_ATTEMPT_RETENTION_HOURS", "48")
    )

    # Comma-separated reverse-proxy addresses whose X-Forwarded-For header may
    # be trusted. Empty means the header is always ignored. Never add a public
    # or client-controlled address here.
    AUTH_TRUSTED_PROXIES: str = os.getenv(
        "AUTH_TRUSTED_PROXIES",
        ""
    )

    # Comma-separated explicit frontend origins. Empty means no CORS headers
    # are emitted, which is correct for a same-origin deployment.
    CORS_ALLOWED_ORIGINS: str = os.getenv(
        "CORS_ALLOWED_ORIGINS",
        ""
    )

    # Comma-separated Host header allowlist. Includes loopback and the test
    # client hosts; add the laptop's LAN IP or mDNS name for phone access.
    TRUSTED_HOSTS: str = os.getenv(
        "TRUSTED_HOSTS",
        "localhost,127.0.0.1,::1,testserver,test"
    )

    @property
    def cors_origins(self) -> list[str]:
        origins = [
            origin.strip()
            for origin in self.CORS_ALLOWED_ORIGINS.split(",")
            if origin.strip()
        ]

        # A wildcard is never valid together with credentialed requests.
        if "*" in origins:
            return []

        return origins

    @property
    def trusted_hosts(self) -> list[str]:
        hosts = [
            host.strip()
            for host in self.TRUSTED_HOSTS.split(",")
            if host.strip() and host.strip() != "*"
        ]

        return hosts or ["localhost", "127.0.0.1"]

    @property
    def auth_cookie_samesite(self) -> str:
        value = (self.AUTH_COOKIE_SAMESITE or "lax").lower()

        if value not in {"lax", "strict", "none"}:
            return "lax"

        return value

    @property
    def auth_trusted_proxies(self) -> set[str]:
        return {
            proxy.strip()
            for proxy in self.AUTH_TRUSTED_PROXIES.split(",")
            if proxy.strip()
        }

    # Sources are listed from highest to lowest priority. The current user
    # message always has the highest priority and is never truncated.
    CONTEXT_PRIORITY_ORDER: str = os.getenv(
        "CONTEXT_PRIORITY_ORDER",
        "history,preference,memory,task,deadline"
    )

# Create a shared settings object
settings = Settings()
