"""Token-aware budgeting helpers for Phase 9 context management.

ASTA deliberately avoids loading a large tokenizer at request time. A
deterministic, conservative heuristic is used instead. Operators can override
model context windows through configuration, so the same code works for any
provider without hardcoding model names here.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass

from app.core.config import settings


logger = logging.getLogger(__name__)


TOKEN_COUNTING_METHOD = "heuristic"
MIN_SAFE_TOKENS = 128

_WORD_PATTERN = re.compile(r"\S+", re.UNICODE)
_CJK_RANGES = (
    (0x2E80, 0x9FFF),
    (0xAC00, 0xD7AF),
    (0xF900, 0xFAFF),
    (0xFE30, 0xFE4F),
    (0xFF00, 0xFFEF),
)


@dataclass(frozen=True)
class TokenBudget:
    """Resolved input-token budget for one model request."""

    model: str | None
    provider: str | None
    context_window: int
    response_reserve: int
    safety_margin: int
    available: int

    @property
    def method(self) -> str:
        return TOKEN_COUNTING_METHOD


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return any(start <= codepoint <= end for start, end in _CJK_RANGES)


def estimate_tokens(text: str | None) -> int:
    """Estimate the number of tokens in ``text``.

    The estimate is intentionally conservative: it never returns zero for
    non-empty text and it counts CJK characters separately because those
    scripts typically use far fewer characters per token. The estimate is not
    a substitute for a provider's reported usage; it is only used to keep a
    prompt inside a safe budget.
    """

    if not text:
        return 0

    value = str(text)
    chars_per_token = max(1.0, float(settings.AI_CHARS_PER_TOKEN))

    char_estimate = math.ceil(len(value) / chars_per_token)

    word_estimate = 0
    cjk_count = 0

    for match in _WORD_PATTERN.finditer(value):
        word = match.group()
        word_estimate += max(1, math.ceil(len(word) / 4))

        for character in word:
            if _is_cjk(character):
                cjk_count += 1

    # CJK characters are close to one token each. Counting them separately
    # avoids a large underestimate for languages without spaces.
    word_estimate += cjk_count

    return max(1, char_estimate, word_estimate)


def safe_estimate_tokens(text: str | None) -> int | None:
    """Return an estimate or ``None`` when counting fails.

    Callers must treat ``None`` as "token budgeting unavailable" and fall back
    to the existing character budget rather than failing the request.
    """

    try:
        return estimate_tokens(text)

    except Exception:
        logger.warning(
            "Token estimation failed; falling back to character budgets."
        )
        return None


def parse_model_context_windows(raw: str | None) -> dict[str, int]:
    """Parse ``model:tokens`` pairs from configuration.

    Malformed entries are ignored. Model names are matched case-sensitively
    because provider model identifiers are case-sensitive.
    """

    windows: dict[str, int] = {}

    for item in (raw or "").split(","):
        entry = item.strip()

        if not entry or ":" not in entry:
            continue

        model, _, raw_tokens = entry.rpartition(":")
        model = model.strip()
        raw_tokens = raw_tokens.strip()

        if not model:
            continue

        try:
            tokens = int(raw_tokens)
        except (TypeError, ValueError):
            continue

        if tokens > 0:
            windows[model] = tokens

    return windows


def provider_default_context_window(provider: str | None) -> int:
    """Return the configured default context window for a provider."""

    normalised = (provider or "").strip().lower()

    if normalised == "groq":
        return max(
            MIN_SAFE_TOKENS,
            int(settings.AI_GROQ_CONTEXT_WINDOW_TOKENS),
        )

    if normalised == "ollama":
        return max(
            MIN_SAFE_TOKENS,
            int(settings.AI_OLLAMA_CONTEXT_WINDOW_TOKENS),
        )

    return max(
        MIN_SAFE_TOKENS,
        int(settings.AI_DEFAULT_CONTEXT_WINDOW_TOKENS),
    )


def resolve_context_window(
    model: str | None = None,
    provider: str | None = None,
) -> int:
    """Resolve the context window for a model, then provider, then default."""

    windows = parse_model_context_windows(settings.AI_MODEL_CONTEXT_WINDOWS)

    if model and model in windows:
        return max(MIN_SAFE_TOKENS, windows[model])

    return provider_default_context_window(provider)


def resolve_token_budget(
    model: str | None = None,
    provider: str | None = None,
    context_window_tokens: int | None = None,
    response_reserve_tokens: int | None = None,
) -> TokenBudget:
    """Compute the maximum number of input tokens available for context."""

    context_window = (
        max(MIN_SAFE_TOKENS, int(context_window_tokens))
        if context_window_tokens is not None
        else resolve_context_window(model=model, provider=provider)
    )

    reserve = max(
        0,
        int(
            settings.AI_RESPONSE_RESERVE_TOKENS
            if response_reserve_tokens is None
            else response_reserve_tokens
        ),
    )
    safety = max(0, int(settings.AI_CONTEXT_SAFETY_TOKENS))

    available = max(
        MIN_SAFE_TOKENS,
        context_window - reserve - safety,
    )

    return TokenBudget(
        model=model,
        provider=provider,
        context_window=context_window,
        response_reserve=reserve,
        safety_margin=safety,
        available=available,
    )
