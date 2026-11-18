"""Conservative, context-aware proactive motivation detection.

Motivation is inferred only from explicit requests or strong conversational
signals. Short neutral messages and ordinary technical questions never trigger
an unsolicited motivational message.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.personality.config import PersonalityConfig, PersonalityMode


@dataclass(frozen=True)
class MotivationDecision:
    """What the shared prompt should tell the model about motivation."""

    kind: str
    should_encourage: bool
    reason: str
    repeated_recent_encouragement: bool = False


_OPT_OUT_PATTERNS = (
    re.compile(
        r"\b(?:stop|quit|drop|skip)\b.{0,40}"
        r"\b(?:motivat\w*|encourag\w*|cheer\w*|pep talk)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:don'?t|do not|no more|never)\b.{0,30}"
        r"\b(?:motivate|encourage|cheer me on|pep talks?)\b",
        re.IGNORECASE,
    ),
)

_EXPLICIT_PATTERNS = (
    re.compile(r"\bmotivate me\b", re.IGNORECASE),
    re.compile(r"\bencourage me\b", re.IGNORECASE),
    re.compile(r"\b(?:give me|need|want)\b.{0,24}\bpep talk\b", re.IGNORECASE),
    re.compile(r"\bcheer me on\b", re.IGNORECASE),
    re.compile(
        r"\bkeep me (?:going|motivated|encouraged)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bhelp me stay (?:motivated|focused|encouraged)\b",
        re.IGNORECASE,
    ),
)

_STRONG_STRUGGLE_PATTERNS = (
    re.compile(
        r"\b(?:stuck|trying|working|fighting)\b.{0,100}"
        r"\bfor\s+(?:\d+|a|an|two|three|four|several|many)\s+"
        r"(?:hour|hours|day|days|week|weeks|month|months)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:falling behind|behind schedule|behind on)\b.{0,80}"
        r"\b(?:goal|deadline|plan|target|task|project)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:no|zero|without any)\s+progress\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:lost|losing)\s+(?:my\s+)?(?:momentum|motivation)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bkeep(?:s|ing)?\s+failing\b|\bfailed again\b|\bfailing repeatedly\b",
        re.IGNORECASE,
    ),
)

_EMOTIONAL_STRUGGLE_PATTERNS = (
    re.compile(
        r"\b(?:frustrated|frustrating|discouraged|overwhelmed|exhausted)\b",
        re.IGNORECASE,
    ),
)

_GENERIC_FAILURE_PATTERNS = (
    re.compile(r"\b(?:stuck|struggling|struggled)\b", re.IGNORECASE),
    re.compile(r"\b(?:failed|failing|failure|rejected)\b", re.IGNORECASE),
    re.compile(r"\bcan'?t (?:solve|fix|finish|get it)\b", re.IGNORECASE),
    re.compile(r"\bnot working\b|\bdoesn'?t work\b", re.IGNORECASE),
)

_TECHNICAL_TASK_PATTERNS = (
    re.compile(
        r"\b(?:bug|error|exception|stack trace|traceback|api|endpoint|sql|"
        r"database|schema|query|python|javascript|typescript|java|code|"
        r"coding|script|function|class|test|tests|compile|build|deploy|"
        r"server|request|response|http|json|git)\b",
        re.IGNORECASE,
    ),
)

_STRONG_PROGRESS_PATTERNS = (
    re.compile(
        r"\b(?:finally|at last)\b.{0,60}"
        r"\b(?:solved|fixed|passed|finished|completed|working|shipped)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:made|making)\s+(?:real\s+)?progress\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:passed|cleared|shipped|launched)\b.{0,40}"
        r"\b(?:test|exam|review|release|milestone|deadline)\b",
        re.IGNORECASE,
    ),
)

_ENCOURAGEMENT_MARKERS = (
    "never give up",
    "keep moving forward",
    "you're not giving up",
    "you are not giving up",
    "you've got this",
    "you can do this",
    "one step at a time",
    "we'll solve it together",
    "great progress",
    "well done",
    "keep going",
    "proud of you",
)


def _matches_any(text: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _history_entries(history: Iterable | None) -> Iterable[tuple[str, str]]:
    for item in history or ():
        if isinstance(item, dict):
            role = str(item.get("role") or "").strip().casefold()
            content = str(item.get("content") or "")

        else:
            role = ""
            content = str(item or "")

        if content.strip():
            yield role, content


def _recent_assistant_text(history: Iterable | None) -> str:
    recent = list(_history_entries(history))[-8:]
    return "\n".join(
        content
        for role, content in recent
        if role in {"assistant", ""}
    ).casefold()


def detect_motivation(
    config: PersonalityConfig,
    user_message: str,
    history: Iterable | None = None,
) -> MotivationDecision:
    """Decide whether this turn warrants a brief motivational nudge."""

    message = str(user_message or "").strip()

    if _matches_any(message, _OPT_OUT_PATTERNS):
        return MotivationDecision(
            kind="opt_out",
            should_encourage=False,
            reason="The user asked to stop encouragement.",
        )

    explicit = _matches_any(message, _EXPLICIT_PATTERNS)
    recent_text = _recent_assistant_text(history)
    repeated = any(marker in recent_text for marker in _ENCOURAGEMENT_MARKERS)

    strong_struggle = _matches_any(message, _STRONG_STRUGGLE_PATTERNS)
    emotional_struggle = _matches_any(
        message, _EMOTIONAL_STRUGGLE_PATTERNS
    )
    generic_failure_hits = sum(
        1 for pattern in _GENERIC_FAILURE_PATTERNS if pattern.search(message)
    )
    technical_task = _matches_any(message, _TECHNICAL_TASK_PATTERNS)

    struggle = (
        strong_struggle
        or emotional_struggle
        or (generic_failure_hits >= 2 and not technical_task)
    )
    suppressed_technical = (
        not strong_struggle
        and not emotional_struggle
        and technical_task
        and generic_failure_hits >= 1
    )

    progress = _matches_any(message, _STRONG_PROGRESS_PATTERNS)

    if explicit:
        return MotivationDecision(
            kind="explicit",
            should_encourage=True,
            reason="The user explicitly requested motivation.",
            repeated_recent_encouragement=repeated,
        )

    if config.mode is PersonalityMode.FOCUS and (struggle or progress):
        return MotivationDecision(
            kind="suppressed_focus",
            should_encourage=False,
            reason="Focus mode avoids motivational interruptions.",
            repeated_recent_encouragement=repeated,
        )

    if not config.proactive_motivation_enabled and (struggle or progress):
        return MotivationDecision(
            kind="suppressed_disabled",
            should_encourage=False,
            reason="Proactive motivation is disabled.",
            repeated_recent_encouragement=repeated,
        )

    if repeated and (struggle or progress):
        return MotivationDecision(
            kind="suppressed_repeated",
            should_encourage=False,
            reason="Recent encouragement should not be repeated.",
            repeated_recent_encouragement=True,
        )

    if suppressed_technical:
        return MotivationDecision(
            kind="suppressed_technical",
            should_encourage=False,
            reason="Ordinary technical troubleshooting should stay task-focused.",
            repeated_recent_encouragement=repeated,
        )

    if struggle:
        return MotivationDecision(
            kind="struggle",
            should_encourage=True,
            reason="The user described sustained difficulty.",
        )

    if progress:
        return MotivationDecision(
            kind="progress",
            should_encourage=True,
            reason="The user described meaningful progress.",
        )

    return MotivationDecision(
        kind="none",
        should_encourage=False,
        reason="No clear struggle, progress, or explicit request was present.",
        repeated_recent_encouragement=repeated,
    )
