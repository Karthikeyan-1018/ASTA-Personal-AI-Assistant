
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

    # Local wall-clock anchor used when a reminder is expressed as an offset
    # from a date-only task. Timed tasks anchor on their exact deadline; this
    # setting prevents date-only reminders from resolving to end-of-day.
    REMINDER_DEFAULT_LOCAL_TIME: str = os.getenv(
        "REMINDER_DEFAULT_LOCAL_TIME",
        "09:00"
    )

    # ------------------------------------------------------------------
    # Phase 10B reminder scheduler
    # ------------------------------------------------------------------

    REMINDER_SCHEDULER_ENABLED: bool = (
        os.getenv("REMINDER_SCHEDULER_ENABLED", "true").lower() == "true"
    )

    REMINDER_SCHEDULER_POLL_SECONDS: int = int(
        os.getenv("REMINDER_SCHEDULER_POLL_SECONDS", "30")
    )

    REMINDER_SCHEDULER_BATCH_SIZE: int = int(
        os.getenv("REMINDER_SCHEDULER_BATCH_SIZE", "25")
    )

    # A claimed reminder is returned to the pending queue when its worker
    # disappears without finishing. The lease must comfortably exceed normal
    # delivery time.
    REMINDER_PROCESSING_LEASE_SECONDS: int = int(
        os.getenv("REMINDER_PROCESSING_LEASE_SECONDS", "300")
    )

    REMINDER_MAX_ATTEMPTS: int = int(
        os.getenv("REMINDER_MAX_ATTEMPTS", "5")
    )

    REMINDER_RETRY_BACKOFF_SECONDS: int = int(
        os.getenv("REMINDER_RETRY_BACKOFF_SECONDS", "60")
    )

    REMINDER_RETRY_BACKOFF_MULTIPLIER: float = float(
        os.getenv("REMINDER_RETRY_BACKOFF_MULTIPLIER", "2")
    )

    REMINDER_RETRY_BACKOFF_MAX_SECONDS: int = int(
        os.getenv("REMINDER_RETRY_BACKOFF_MAX_SECONDS", "3600")
    )

    REMINDER_SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS: int = int(
        os.getenv("REMINDER_SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS", "10")
    )

    # ------------------------------------------------------------------
    # Phase 10C/10E notification retention and recovery
    # ------------------------------------------------------------------

    NOTIFICATION_RETENTION_DAYS: int = int(
        os.getenv("NOTIFICATION_RETENTION_DAYS", "365")
    )

    NOTIFICATION_MAX_ATTEMPTS: int = int(
        os.getenv("NOTIFICATION_MAX_ATTEMPTS", "5")
    )

    # Snoozing re-arms the reminder that produced a notification. The counter is
    # cumulative per notification; a value of 0 or less means unlimited snoozes.
    NOTIFICATION_SNOOZE_MAX_COUNT: int = int(
        os.getenv("NOTIFICATION_SNOOZE_MAX_COUNT", "10")
    )

    # Upper bound for a single snooze or custom snooze instant (365 days).
    NOTIFICATION_MAX_SNOOZE_MINUTES: int = int(
        os.getenv("NOTIFICATION_MAX_SNOOZE_MINUTES", "525600")
    )

    # ------------------------------------------------------------------
    # Phase 10D recurring tasks
    # ------------------------------------------------------------------

    RECURRENCE_GENERATION_ENABLED: bool = (
        os.getenv("RECURRENCE_GENERATION_ENABLED", "true").lower() == "true"
    )

    RECURRENCE_GENERATION_HORIZON_DAYS: int = int(
        os.getenv("RECURRENCE_GENERATION_HORIZON_DAYS", "30")
    )

    RECURRENCE_GENERATION_BATCH_SIZE: int = int(
        os.getenv("RECURRENCE_GENERATION_BATCH_SIZE", "25")
    )

    RECURRENCE_MAX_GENERATIONS_PER_RUN: int = int(
        os.getenv("RECURRENCE_MAX_GENERATIONS_PER_RUN", "10")
    )

    RECURRENCE_GENERATION_INTERVAL_SECONDS: int = int(
        os.getenv("RECURRENCE_GENERATION_INTERVAL_SECONDS", "3600")
    )

    RECURRENCE_RETRY_BACKOFF_SECONDS: int = int(
        os.getenv("RECURRENCE_RETRY_BACKOFF_SECONDS", "300")
    )

    RECURRENCE_PROCESSING_LEASE_SECONDS: int = int(
        os.getenv("RECURRENCE_PROCESSING_LEASE_SECONDS", "300")
    )

    # ------------------------------------------------------------------
    # Phase 10F notification preferences and quiet hours
    # ------------------------------------------------------------------

    NOTIFICATION_QUIET_HOURS_DEFAULT_ENABLED: bool = (
        os.getenv(
            "NOTIFICATION_QUIET_HOURS_DEFAULT_ENABLED",
            "false",
        ).lower() == "true"
    )

    NOTIFICATION_QUIET_HOURS_DEFAULT_START: str = os.getenv(
        "NOTIFICATION_QUIET_HOURS_DEFAULT_START",
        "22:00"
    )

    NOTIFICATION_QUIET_HOURS_DEFAULT_END: str = os.getenv(
        "NOTIFICATION_QUIET_HOURS_DEFAULT_END",
        "07:00"
    )

    NOTIFICATION_QUIET_HOURS_DEFAULT_TIMEZONE: str = os.getenv(
        "NOTIFICATION_QUIET_HOURS_DEFAULT_TIMEZONE",
        "UTC"
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
    # Phase 9 raises conversation history and structured task/deadline context
    # above preferences and long-term memories, so less relevant stored context
    # is removed first when a token budget is tight.
    CONTEXT_PRIORITY_ORDER: str = os.getenv(
        "CONTEXT_PRIORITY_ORDER",
        "history,task,deadline,preference,memory"
    )

    # ------------------------------------------------------------------
    # Phase 9 AI optimization
    # ------------------------------------------------------------------

    # Token-aware context budgeting. Counting uses a deterministic heuristic
    # unless a model-specific tokenizer is added in future; no tokenizer model
    # is downloaded at request time.
    AI_TOKEN_BUDGET_ENABLED: bool = (
        os.getenv("AI_TOKEN_BUDGET_ENABLED", "true").lower() == "true"
    )

    AI_CHARS_PER_TOKEN: float = float(
        os.getenv("AI_CHARS_PER_TOKEN", "4.0")
    )

    AI_DEFAULT_CONTEXT_WINDOW_TOKENS: int = int(
        os.getenv("AI_DEFAULT_CONTEXT_WINDOW_TOKENS", "8192")
    )

    # Provider defaults are used when no model-specific entry exists. The
    # values are conservative and can be lowered for smaller deployments.
    AI_GROQ_CONTEXT_WINDOW_TOKENS: int = int(
        os.getenv("AI_GROQ_CONTEXT_WINDOW_TOKENS", "131072")
    )

    AI_OLLAMA_CONTEXT_WINDOW_TOKENS: int = int(
        os.getenv("AI_OLLAMA_CONTEXT_WINDOW_TOKENS", "40960")
    )

    # Optional comma-separated "model:context_window" overrides. Example:
    # openai/gpt-oss-20b:131072,qwen3:8b:40960
    AI_MODEL_CONTEXT_WINDOWS: str = os.getenv(
        "AI_MODEL_CONTEXT_WINDOWS",
        ""
    )

    AI_RESPONSE_RESERVE_TOKENS: int = int(
        os.getenv("AI_RESPONSE_RESERVE_TOKENS", "1024")
    )

    AI_CONTEXT_SAFETY_TOKENS: int = int(
        os.getenv("AI_CONTEXT_SAFETY_TOKENS", "256")
    )

    # Deterministic query classification runs first. An optional LLM fallback
    # is disabled by default because it adds latency and is never allowed to
    # decide authorization or execute a task mutation.
    AI_QUERY_CLASSIFIER_ENABLED: bool = (
        os.getenv("AI_QUERY_CLASSIFIER_ENABLED", "true").lower() == "true"
    )

    AI_QUERY_CLASSIFIER_LLM_FALLBACK_ENABLED: bool = (
        os.getenv(
            "AI_QUERY_CLASSIFIER_LLM_FALLBACK_ENABLED",
            "false",
        ).lower() == "true"
    )

    # A deterministic result at or above this confidence is kept as-is. Lower
    # confidence results may use the optional LLM fallback when it is enabled.
    AI_QUERY_CLASSIFIER_LLM_FALLBACK_THRESHOLD: float = float(
        os.getenv(
            "AI_QUERY_CLASSIFIER_LLM_FALLBACK_THRESHOLD",
            "0.6",
        )
    )

    # groq_first preserves the existing non-private Groq-first behaviour.
    # local_first sends non-private requests to Ollama first. cost_aware uses
    # the per-complexity provider mapping below.
    AI_ROUTING_POLICY: str = os.getenv(
        "AI_ROUTING_POLICY",
        "groq_first"
    ).strip().lower()

    # cost_aware provider mapping. Private requests ignore this mapping and
    # always use Ollama.
    AI_ROUTING_SIMPLE_PROVIDER: str = os.getenv(
        "AI_ROUTING_SIMPLE_PROVIDER",
        "ollama"
    ).strip().lower()

    AI_ROUTING_MODERATE_PROVIDER: str = os.getenv(
        "AI_ROUTING_MODERATE_PROVIDER",
        "groq"
    ).strip().lower()

    AI_ROUTING_COMPLEX_PROVIDER: str = os.getenv(
        "AI_ROUTING_COMPLEX_PROVIDER",
        "groq"
    ).strip().lower()

    AI_ROUTING_DYNAMIC_PROVIDER: str = os.getenv(
        "AI_ROUTING_DYNAMIC_PROVIDER",
        "groq"
    ).strip().lower()

    # Optional comma-separated "complexity:provider:model" overrides. Model
    # names otherwise come from the provider configuration, so they are not
    # duplicated throughout the codebase.
    AI_ROUTING_MODEL_OVERRIDES: str = os.getenv(
        "AI_ROUTING_MODEL_OVERRIDES",
        ""
    )

    # Configured prices are optional. Zero means "unknown"; usage summaries
    # report costs only when a real configured price is available.
    AI_GROQ_INPUT_COST_PER_MILLION: float = float(
        os.getenv("AI_GROQ_INPUT_COST_PER_MILLION", "0")
    )

    AI_GROQ_OUTPUT_COST_PER_MILLION: float = float(
        os.getenv("AI_GROQ_OUTPUT_COST_PER_MILLION", "0")
    )

    AI_OLLAMA_INPUT_COST_PER_MILLION: float = float(
        os.getenv("AI_OLLAMA_INPUT_COST_PER_MILLION", "0")
    )

    AI_OLLAMA_OUTPUT_COST_PER_MILLION: float = float(
        os.getenv("AI_OLLAMA_OUTPUT_COST_PER_MILLION", "0")
    )

    # Semantic response cache. Only stateless, non-private, non-sensitive,
    # non-dynamic eligible requests may be cached.
    AI_CACHE_ENABLED: bool = (
        os.getenv("AI_CACHE_ENABLED", "true").lower() == "true"
    )

    AI_CACHE_SIMILARITY_THRESHOLD: float = float(
        os.getenv("AI_CACHE_SIMILARITY_THRESHOLD", "0.95")
    )

    AI_CACHE_TTL_SECONDS: int = int(
        os.getenv("AI_CACHE_TTL_SECONDS", "3600")
    )

    AI_CACHE_MAX_ENTRIES_PER_USER: int = int(
        os.getenv("AI_CACHE_MAX_ENTRIES_PER_USER", "500")
    )

    AI_CACHE_PROMPT_VERSION: str = os.getenv(
        "AI_CACHE_PROMPT_VERSION",
        "phase9-v1"
    )

    AI_CACHE_CONFIG_VERSION: str = os.getenv(
        "AI_CACHE_CONFIG_VERSION",
        "1"
    )

    AI_CACHE_ALLOWED_COMPLEXITIES: str = os.getenv(
        "AI_CACHE_ALLOWED_COMPLEXITIES",
        "simple,moderate"
    )

    AI_CACHE_STATELESS_ONLY: bool = (
        os.getenv("AI_CACHE_STATELESS_ONLY", "true").lower() == "true"
    )

    AI_CACHE_SEMANTIC_ENABLED: bool = (
        os.getenv("AI_CACHE_SEMANTIC_ENABLED", "true").lower() == "true"
    )

    # Structured AI usage tracking. Records never contain prompt or response
    # content, credentials, or provider error details.
    AI_USAGE_TRACKING_ENABLED: bool = (
        os.getenv("AI_USAGE_TRACKING_ENABLED", "true").lower() == "true"
    )

    AI_USAGE_RETENTION_DAYS: int = int(
        os.getenv("AI_USAGE_RETENTION_DAYS", "90")
    )

    AI_USAGE_RECENT_LIMIT: int = int(
        os.getenv("AI_USAGE_RECENT_LIMIT", "50")
    )

    # Hybrid memory retrieval blends semantic and keyword signals. Keyword
    # retrieval remains the fallback when semantic retrieval is unavailable.
    AI_MEMORY_HYBRID_ENABLED: bool = (
        os.getenv("AI_MEMORY_HYBRID_ENABLED", "true").lower() == "true"
    )

    AI_MEMORY_SEMANTIC_WEIGHT: float = float(
        os.getenv("AI_MEMORY_SEMANTIC_WEIGHT", "0.7")
    )

    AI_MEMORY_KEYWORD_WEIGHT: float = float(
        os.getenv("AI_MEMORY_KEYWORD_WEIGHT", "0.3")
    )

    AI_MEMORY_CANDIDATE_LIMIT: int = int(
        os.getenv("AI_MEMORY_CANDIDATE_LIMIT", "20")
    )

    AI_MEMORY_MIN_HYBRID_SCORE: float = float(
        os.getenv("AI_MEMORY_MIN_HYBRID_SCORE", "0.05")
    )

    # ------------------------------------------------------------------
    # Phase 11 function calling and tool execution
    # ------------------------------------------------------------------

    # Chat tool-calling is opt-in. The registry, executor, and API remain
    # available when disabled, but the normal chat path is unchanged.
    TOOL_CALLING_ENABLED: bool = (
        os.getenv("TOOL_CALLING_ENABLED", "false").lower() == "true"
    )

    TOOL_MAX_CALLS_PER_REQUEST: int = int(
        os.getenv("TOOL_MAX_CALLS_PER_REQUEST", "5")
    )

    # Bound the number of calls accepted from a single assistant turn. A
    # provider can return several calls at once; extras are rejected with a
    # explicit tool result instead of being executed silently.
    TOOL_MAX_CALLS_PER_TURN: int = int(
        os.getenv("TOOL_MAX_CALLS_PER_TURN", "8")
    )

    # One initial provider turn plus one final response turn. This bound
    # prevents a model from looping indefinitely.
    TOOL_MAX_ROUNDS: int = int(
        os.getenv("TOOL_MAX_ROUNDS", "2")
    )

    # Total bytes of tool-result content fed back to the model across all
    # rounds. Individual results are already bounded by TOOL_MAX_RESULT_BYTES.
    TOOL_MAX_RESULT_CONTEXT_BYTES: int = int(
        os.getenv("TOOL_MAX_RESULT_CONTEXT_BYTES", "50000")
    )

    # Refuse to re-execute an identical tool call within one request. This
    # complements the hard round and call limits.
    TOOL_LOOP_PREVENTION_ENABLED: bool = (
        os.getenv("TOOL_LOOP_PREVENTION_ENABLED", "true").lower() == "true"
    )

    TOOL_EXECUTION_TIMEOUT_SECONDS: int = int(
        os.getenv("TOOL_EXECUTION_TIMEOUT_SECONDS", "15")
    )

    TOOL_MAX_ARGUMENT_BYTES: int = int(
        os.getenv("TOOL_MAX_ARGUMENT_BYTES", "16384")
    )

    TOOL_MAX_RESULT_BYTES: int = int(
        os.getenv("TOOL_MAX_RESULT_BYTES", "20000")
    )

    TOOL_CONFIRMATION_TTL_SECONDS: int = int(
        os.getenv("TOOL_CONFIRMATION_TTL_SECONDS", "600")
    )

    TOOL_CONFIRMATION_MAX_PENDING: int = int(
        os.getenv("TOOL_CONFIRMATION_MAX_PENDING", "10")
    )

    TOOL_EXECUTION_RETENTION_DAYS: int = int(
        os.getenv("TOOL_EXECUTION_RETENTION_DAYS", "90")
    )

    TOOL_HISTORY_ENABLED: bool = (
        os.getenv("TOOL_HISTORY_ENABLED", "true").lower() == "true"
    )

    # Retries are opt-in and apply only to tools that explicitly declare
    # themselves idempotent. Confirmation-requiring tools are never retried.
    TOOL_RETRY_ENABLED: bool = (
        os.getenv("TOOL_RETRY_ENABLED", "false").lower() == "true"
    )

    TOOL_MAX_RETRIES: int = int(
        os.getenv("TOOL_MAX_RETRIES", "1")
    )

    TOOL_RETRY_BACKOFF_MS: int = int(
        os.getenv("TOOL_RETRY_BACKOFF_MS", "250")
    )

    # On startup, stale in-flight rows from a previous process are marked
    # cancelled. The cutoff protects a concurrently running worker in a
    # multi-process deployment; ASTA normally runs one process.
    TOOL_RECOVERY_ON_STARTUP: bool = (
        os.getenv("TOOL_RECOVERY_ON_STARTUP", "true").lower() == "true"
    )

    TOOL_STALE_EXECUTION_SECONDS: int = int(
        os.getenv("TOOL_STALE_EXECUTION_SECONDS", "300")
    )

    # Sensitive tools (arbitrary HTTP mutations, Python execution, future
    # destructive filesystem operations) are denied unless this is enabled.
    TOOL_ALLOW_SENSITIVE: bool = (
        os.getenv("TOOL_ALLOW_SENSITIVE", "false").lower() == "true"
    )

    # Web search. The provider credential never leaves the server.
    WEB_SEARCH_ENABLED: bool = (
        os.getenv("WEB_SEARCH_ENABLED", "false").lower() == "true"
    )

    WEB_SEARCH_PROVIDER: str = os.getenv(
        "WEB_SEARCH_PROVIDER",
        "searxng"
    ).strip().lower()

    WEB_SEARCH_BASE_URL: str = os.getenv(
        "WEB_SEARCH_BASE_URL",
        ""
    ).strip()

    WEB_SEARCH_API_KEY: str = os.getenv(
        "WEB_SEARCH_API_KEY",
        ""
    )

    WEB_SEARCH_TIMEOUT_SECONDS: int = int(
        os.getenv("WEB_SEARCH_TIMEOUT_SECONDS", "10")
    )

    WEB_SEARCH_MAX_RESULTS: int = int(
        os.getenv("WEB_SEARCH_MAX_RESULTS", "5")
    )

    # Bound the provider response before JSON parsing so a malformed or
    # hostile endpoint cannot exhaust process memory.
    WEB_SEARCH_MAX_RESPONSE_BYTES: int = int(
        os.getenv("WEB_SEARCH_MAX_RESPONSE_BYTES", "262144")
    )

    # Local filesystem tools. Access is limited to explicitly approved roots.
    TOOL_FILESYSTEM_ENABLED: bool = (
        os.getenv("TOOL_FILESYSTEM_ENABLED", "false").lower() == "true"
    )

    TOOL_WORKSPACE_ROOTS: str = os.getenv(
        "TOOL_WORKSPACE_ROOTS",
        ""
    ).strip()

    TOOL_FILE_MAX_READ_BYTES: int = int(
        os.getenv("TOOL_FILE_MAX_READ_BYTES", "200000")
    )

    TOOL_FILE_MAX_WRITE_BYTES: int = int(
        os.getenv("TOOL_FILE_MAX_WRITE_BYTES", "100000")
    )

    TOOL_FILE_MAX_LIST_ENTRIES: int = int(
        os.getenv("TOOL_FILE_MAX_LIST_ENTRIES", "500")
    )

    TOOL_FILE_MAX_SEARCH_RESULTS: int = int(
        os.getenv("TOOL_FILE_MAX_SEARCH_RESULTS", "100")
    )

    # Read-only system information tools. Disabled by default. Only scalar
    # hardware and runtime facts are collected; files, environment variables,
    # process lists, and user data are never read. Disk usage is limited to an
    # explicit allowlist of local roots, or the system drive when no allowlist
    # is configured. Volumes are never enumerated.
    TOOL_SYSTEM_INFO_ENABLED: bool = (
        os.getenv("TOOL_SYSTEM_INFO_ENABLED", "false").lower() == "true"
    )

    TOOL_SYSTEM_INFO_DISK_ROOTS: str = os.getenv(
        "TOOL_SYSTEM_INFO_DISK_ROOTS",
        ""
    ).strip()

    TOOL_SYSTEM_INFO_MAX_DISKS: int = int(
        os.getenv("TOOL_SYSTEM_INFO_MAX_DISKS", "8")
    )

    # Read-only process and active-window inspection (Phase 12D). Disabled by
    # default. Only bounded process identity and resource measurements are
    # collected; command lines, environment variables, memory contents,
    # executable paths, and owners are never read or returned.
    TOOL_PROCESS_INSPECTION_ENABLED: bool = (
        os.getenv("TOOL_PROCESS_INSPECTION_ENABLED", "false").lower()
        == "true"
    )

    TOOL_PROCESS_MAX_RESULTS: int = int(
        os.getenv("TOOL_PROCESS_MAX_RESULTS", "25")
    )

    TOOL_PROCESS_MAX_MATCHES: int = int(
        os.getenv("TOOL_PROCESS_MAX_MATCHES", "20")
    )

    TOOL_PROCESS_MAX_NAME_CHARS: int = int(
        os.getenv("TOOL_PROCESS_MAX_NAME_CHARS", "128")
    )

    TOOL_PROCESS_MAX_TITLE_CHARS: int = int(
        os.getenv("TOOL_PROCESS_MAX_TITLE_CHARS", "300")
    )

    TOOL_PROCESS_CPU_SAMPLE_MS: int = int(
        os.getenv("TOOL_PROCESS_CPU_SAMPLE_MS", "100")
    )

    # Secure clipboard, keyboard, and mouse interaction (Phase 12E). Each
    # capability is disabled by default and separately configurable. Clipboard
    # reads and writes and every state-changing desktop action require explicit
    # confirmation. Only plain clipboard text and an allowlisted set of single
    # key presses are supported.
    TOOL_CLIPBOARD_ENABLED: bool = (
        os.getenv("TOOL_CLIPBOARD_ENABLED", "false").lower() == "true"
    )

    TOOL_CLIPBOARD_MAX_READ_CHARS: int = int(
        os.getenv("TOOL_CLIPBOARD_MAX_READ_CHARS", "4000")
    )

    TOOL_CLIPBOARD_MAX_WRITE_CHARS: int = int(
        os.getenv("TOOL_CLIPBOARD_MAX_WRITE_CHARS", "4000")
    )

    TOOL_KEYBOARD_ENABLED: bool = (
        os.getenv("TOOL_KEYBOARD_ENABLED", "false").lower() == "true"
    )

    TOOL_MOUSE_ENABLED: bool = (
        os.getenv("TOOL_MOUSE_ENABLED", "false").lower() == "true"
    )

    # Phase 12F central OS automation controls. The master switch is an
    # additional, deployment-wide gate: when it is false every built-in OS
    # automation tool is denied even if its per-capability flag is true. The
    # model can never change these values. The emergency stop is a separate
    # authenticated-owner control, not a registered tool. Rate limits are
    # enforced server-side and are owner-isolated. The limiter is process-local
    # and adds no database migration; see docs/os_automation_security.md.
    TOOL_OS_AUTOMATION_ENABLED: bool = (
        os.getenv("TOOL_OS_AUTOMATION_ENABLED", "false").lower() == "true"
    )

    TOOL_OS_RATE_LIMIT_ENABLED: bool = (
        os.getenv("TOOL_OS_RATE_LIMIT_ENABLED", "true").lower() == "true"
    )

    TOOL_OS_MAX_CALLS_PER_MINUTE: int = int(
        os.getenv("TOOL_OS_MAX_CALLS_PER_MINUTE", "30")
    )

    TOOL_OS_MAX_STATE_CHANGES_PER_MINUTE: int = int(
        os.getenv("TOOL_OS_MAX_STATE_CHANGES_PER_MINUTE", "10")
    )

    TOOL_OS_MAX_CALLS_PER_HOUR: int = int(
        os.getenv("TOOL_OS_MAX_CALLS_PER_HOUR", "300")
    )

    # Trusted recovery secret for the emergency stop. It is never returned to
    # the model or written to logs. When it is empty, recovery is disabled and
    # only a process restart clears an engaged stop.
    TOOL_OS_EMERGENCY_RECOVERY_TOKEN: str = os.getenv(
        "TOOL_OS_EMERGENCY_RECOVERY_TOKEN",
        "",
    )

    # Secure application launching (Phase 12B). Disabled by default. The model
    # supplies only an alias; the executable path always comes from this
    # server-side allowlist. Launching is a sensitive action and additionally
    # requires TOOL_ALLOW_SENSITIVE plus per-call confirmation.
    TOOL_APPLICATION_LAUNCH_ENABLED: bool = (
        os.getenv("TOOL_APPLICATION_LAUNCH_ENABLED", "false").lower() == "true"
    )

    # Comma- or newline-separated ``alias=absolute-path`` entries. Example:
    # vscode=C:\Program Files\Microsoft VS Code\Code.exe
    TOOL_APPLICATION_ALLOWLIST: str = os.getenv(
        "TOOL_APPLICATION_ALLOWLIST",
        ""
    ).strip()

    # Allowlisted HTTP integration framework. Arbitrary LLM-generated URLs are
    # never accepted, and redirects are not followed.
    TOOL_HTTP_ENABLED: bool = (
        os.getenv("TOOL_HTTP_ENABLED", "false").lower() == "true"
    )

    TOOL_HTTP_ALLOWED_HOSTS: str = os.getenv(
        "TOOL_HTTP_ALLOWED_HOSTS",
        ""
    ).strip()

    TOOL_HTTP_ALLOW_HTTP: bool = (
        os.getenv("TOOL_HTTP_ALLOW_HTTP", "false").lower() == "true"
    )

    TOOL_HTTP_TIMEOUT_SECONDS: int = int(
        os.getenv("TOOL_HTTP_TIMEOUT_SECONDS", "10")
    )

    TOOL_HTTP_MAX_RESPONSE_BYTES: int = int(
        os.getenv("TOOL_HTTP_MAX_RESPONSE_BYTES", "200000")
    )

    # JSON object mapping host names to server-side header objects. The values
    # are never returned to the LLM or included in execution history.
    TOOL_HTTP_CREDENTIALS: str = os.getenv(
        "TOOL_HTTP_CREDENTIALS",
        ""
    ).strip()

    # Python execution sandbox (Windows AppContainer + Job Object). Disabled by
    # default and requires TOOL_ALLOW_SENSITIVE plus per-call confirmation.
    # The deprecated PYTHON_EXECUTION_* names are still read as fallbacks so an
    # existing deployment keeps its limits when it migrates; the old
    # PYTHON_EXECUTION_ALLOW_UNSAFE flag no longer enables an unsafe runner.
    PYTHON_SANDBOX_ENABLED: bool = (
        os.getenv(
            "PYTHON_SANDBOX_ENABLED",
            os.getenv("PYTHON_EXECUTION_ENABLED", "false"),
        ).lower()
        == "true"
    )

    PYTHON_SANDBOX_TIMEOUT_SECONDS: int = int(
        os.getenv(
            "PYTHON_SANDBOX_TIMEOUT_SECONDS",
            os.getenv("PYTHON_EXECUTION_TIMEOUT_SECONDS", "5"),
        )
    )

    # Wall-clock timeout is clamped below TOOL_EXECUTION_TIMEOUT_SECONDS so the
    # sandbox can terminate its process tree before the executor cancels.
    PYTHON_SANDBOX_CPU_SECONDS: int = int(
        os.getenv("PYTHON_SANDBOX_CPU_SECONDS", "5")
    )

    PYTHON_SANDBOX_MEMORY_MB: int = int(
        os.getenv("PYTHON_SANDBOX_MEMORY_MB", "256")
    )

    PYTHON_SANDBOX_MAX_SOURCE_BYTES: int = int(
        os.getenv(
            "PYTHON_SANDBOX_MAX_SOURCE_BYTES",
            os.getenv("PYTHON_EXECUTION_MAX_CODE_BYTES", "20000"),
        )
    )

    PYTHON_SANDBOX_MAX_OUTPUT_BYTES: int = int(
        os.getenv(
            "PYTHON_SANDBOX_MAX_OUTPUT_BYTES",
            os.getenv("PYTHON_EXECUTION_MAX_OUTPUT_BYTES", "20000"),
        )
    )

    # Network access is denied by the AppContainer token. This flag exists so
    # an operator can request it explicitly, but a true value fails closed
    # because the backend cannot grant network capabilities.
    PYTHON_SANDBOX_ALLOW_NETWORK: bool = (
        os.getenv("PYTHON_SANDBOX_ALLOW_NETWORK", "false").lower() == "true"
    )

    # Optional existing directory that will contain per-execution workspaces.
    # Empty means the system temporary directory.
    PYTHON_SANDBOX_WORKSPACE_PARENT: str = os.getenv(
        "PYTHON_SANDBOX_WORKSPACE_PARENT",
        "",
    ).strip()

    # ------------------------------------------------------------------
    # Phase 13 streaming responses
    # ------------------------------------------------------------------

    # Streaming is additive: the historical non-streaming chat route is never
    # affected by these values.
    STREAMING_ENABLED: bool = (
        os.getenv("STREAMING_ENABLED", "true").lower() == "true"
    )

    # Overall wall-clock limit for one streamed response.
    STREAM_TIMEOUT_SECONDS: float = float(
        os.getenv("STREAM_TIMEOUT_SECONDS", "120")
    )

    # Maximum time to wait for the next token before treating the provider as
    # stalled. This protects against a connection that stays open silently.
    STREAM_IDLE_TIMEOUT_SECONDS: float = float(
        os.getenv("STREAM_IDLE_TIMEOUT_SECONDS", "30")
    )

    # Bounded accumulation protects memory and prevents an unbounded provider
    # response from being persisted. A response over this limit is treated as
    # a failure and is never stored as a complete assistant answer.
    STREAM_MAX_RESPONSE_CHARS: int = int(
        os.getenv("STREAM_MAX_RESPONSE_CHARS", "50000")
    )

    # Process-local concurrency guard for the streaming endpoint. This is
    # intentionally in-process; ASTA normally runs a single application
    # process. Requests that cannot obtain a slot within the queue timeout are
    # rejected with HTTP 429.
    STREAM_MAX_CONCURRENT: int = int(
        os.getenv("STREAM_MAX_CONCURRENT", "4")
    )

    STREAM_QUEUE_TIMEOUT_SECONDS: float = float(
        os.getenv("STREAM_QUEUE_TIMEOUT_SECONDS", "5")
    )

    # ------------------------------------------------------------------
    # Phase 13 in-process background tasks
    # ------------------------------------------------------------------

    BACKGROUND_TASKS_ENABLED: bool = (
        os.getenv("BACKGROUND_TASKS_ENABLED", "true").lower() == "true"
    )

    BACKGROUND_TASK_MAX_CONCURRENT: int = int(
        os.getenv("BACKGROUND_TASK_MAX_CONCURRENT", "2")
    )

    BACKGROUND_TASK_MAX_QUEUED_PER_USER: int = int(
        os.getenv("BACKGROUND_TASK_MAX_QUEUED_PER_USER", "10")
    )

    BACKGROUND_TASK_RETENTION_DAYS: int = int(
        os.getenv("BACKGROUND_TASK_RETENTION_DAYS", "30")
    )

    BACKGROUND_TASK_SHUTDOWN_TIMEOUT_SECONDS: float = float(
        os.getenv("BACKGROUND_TASK_SHUTDOWN_TIMEOUT_SECONDS", "10")
    )

    BACKGROUND_TASK_MAX_EXECUTION_SECONDS: float = float(
        os.getenv("BACKGROUND_TASK_MAX_EXECUTION_SECONDS", "300")
    )

    # Bound the conversation text handed to the extraction handler.
    BACKGROUND_TASK_MAX_CONVERSATION_CHARS: int = int(
        os.getenv("BACKGROUND_TASK_MAX_CONVERSATION_CHARS", "20000")
    )

    # On startup, stale running rows become failed and queued rows become
    # cancelled. No non-idempotent work is retried automatically.
    BACKGROUND_TASK_RECOVERY_ON_STARTUP: bool = (
        os.getenv("BACKGROUND_TASK_RECOVERY_ON_STARTUP", "true").lower()
        == "true"
    )

    @property
    def tool_workspace_roots(self) -> list[str]:
        return [
            root.strip()
            for root in self.TOOL_WORKSPACE_ROOTS.split(",")
            if root.strip()
        ]

    @property
    def tool_system_info_disk_roots(self) -> list[str]:
        return [
            root.strip()
            for root in self.TOOL_SYSTEM_INFO_DISK_ROOTS.split(",")
            if root.strip()
        ]

    @property
    def tool_application_allowlist(self) -> dict[str, str]:
        """Parse ``alias=path`` entries without validating the filesystem.

        The launcher performs the authoritative normalization and path
        validation. This property only splits the configured value, keeps the
        first occurrence of an exact alias, and never raises for malformed
        input.
        """

        entries: dict[str, str] = {}

        for item in (self.TOOL_APPLICATION_ALLOWLIST or "").replace(
            "\n",
            ",",
        ).split(","):
            entry = item.strip()

            if not entry or "=" not in entry:
                continue

            alias, path = entry.split("=", 1)
            alias = alias.strip()
            path = path.strip()

            if alias and path:
                entries.setdefault(alias, path)

        return entries

    @property
    def tool_http_allowed_hosts(self) -> set[str]:
        return {
            host.strip().lower().rstrip(".")
            for host in self.TOOL_HTTP_ALLOWED_HOSTS.split(",")
            if host.strip()
        }

    @property
    def tool_http_credentials(self) -> dict[str, dict[str, str]]:
        """Parse per-host server-side headers without ever logging them."""

        if not self.TOOL_HTTP_CREDENTIALS:
            return {}

        import json

        try:
            parsed = json.loads(self.TOOL_HTTP_CREDENTIALS)

        except (TypeError, ValueError):
            return {}

        if not isinstance(parsed, dict):
            return {}

        result: dict[str, dict[str, str]] = {}

        for host, headers in parsed.items():
            if not isinstance(host, str) or not isinstance(headers, dict):
                continue

            cleaned = {
                str(key): str(value)
                for key, value in headers.items()
                if str(key).lower() not in {"host", "content-length"}
            }

            if cleaned:
                result[host.strip().lower().rstrip(".")] = cleaned

        return result

    @property
    def ai_cache_allowed_complexities(self) -> set[str]:
        return {
            value.strip().lower()
            for value in (self.AI_CACHE_ALLOWED_COMPLEXITIES or "").split(",")
            if value.strip()
        }

    @property
    def ai_routing_model_overrides(self) -> dict[tuple[str, str], str]:
        """Return ``(complexity, provider) -> model`` overrides."""

        overrides: dict[tuple[str, str], str] = {}

        for item in (self.AI_ROUTING_MODEL_OVERRIDES or "").split(","):
            entry = item.strip()

            if not entry:
                continue

            parts = entry.split(":", 2)

            if len(parts) != 3:
                continue

            complexity, provider, model = (
                part.strip() for part in parts
            )

            if complexity and provider and model:
                overrides[
                    (complexity.lower(), provider.lower())
                ] = model

        return overrides

# Create a shared settings object
settings = Settings()
