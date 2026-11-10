"""Intent detection and structured information extraction.

Deterministic keyword and pattern rules run first. The LLM is only consulted
for messages that look task-related but cannot be classified confidently, and
its output is strictly validated. The model never receives IDs, user IDs, or
database fields, and it never executes anything.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from app.core.config import settings
from app.llm.llm_router import llm_router
from app.models.task import TaskCategory, TaskPriority, TaskStatus
from app.task_intent.date_resolver import (
    DeadlineResolution,
    resolve_deadline,
    strip_deadline_phrases,
)
from app.task_intent.enums import TaskIntent
from app.task_intent.schemas import (
    LLMTaskIntent,
    TaskChanges,
    TaskDraft,
    TaskFilters,
    TaskIntentResult,
)


logger = logging.getLogger(__name__)


_WEEKDAYS = (
    "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
)
_MONTHS = (
    "january|february|march|april|may|june|july|august|september|"
    "october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|"
    "oct|nov|dec"
)

TASK_NOUN_RE = re.compile(
    r"\b(?:task|tasks|assignment|assignments|homework|exam|exams|quiz|"
    r"quizzes|project|projects|deadline|deadlines|to-?do|coursework)\b",
    re.IGNORECASE,
)

_MUTATION_VERB_RE = re.compile(
    r"\b(?:create|add|make|set|schedule|remind|mark|complete|completed|"
    r"finish|finished|update|change|move|reschedule|rename|cancel|delete|"
    r"remove|purge|reopen|handle|need|want|must|should)\b",
    re.IGNORECASE,
)

_STATUS_QUERY_PATTERNS = (
    re.compile(
        r"\bdid\s+i\s+(?:complete|completed|finish|finished|do|submit|"
        r"submitted)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bhave\s+i\s+(?:completed|finished|submitted)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bstatus\s+of\b", re.IGNORECASE),
    re.compile(
        r"\bwhat(?:'s|\s+is)\s+the\s+status\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bis\s+(?:my|the)\b.*\b(?:done|completed|finished)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bhow\s+is\s+(?:my|the)\b.*\b(?:task|assignment|project)\b",
        re.IGNORECASE,
    ),
)

_QUERY_START_RE = re.compile(
    r"^(?:(?:please|kindly)\s+)?"
    r"(?:show|list|display|tell)\b",
    re.IGNORECASE,
)

_FIND_START_RE = re.compile(
    r"^(?:(?:please|kindly)\s+)?(?:find|look\s+up)\b",
    re.IGNORECASE,
)

_QUERY_WORD_RE = re.compile(
    r"\b(?:what|which|how\s+many)\b.*"
    r"\b(?:task|tasks|assignment|assignments|homework|exam|exams|"
    r"project|projects|deadline|deadlines|to-?do)\b"
    r"|\bwhat\b.*\b(?:should\s+i|do\s+i\s+have\s+to|"
    r"am\s+i\s+supposed\s+to)\s+(?:complete|finish|do|work\s+on|"
    r"study|submit)\b.*\b(?:today|tomorrow)\b"
    r"|\bwhat(?:'s|\s+is|\s+are)\b.*\b(?:due|overdue|upcoming)\b",
    re.IGNORECASE,
)

_HAVE_QUERY_RE = re.compile(
    r"\bdo\s+i\s+have\b.*"
    r"(?:\b(?:task|tasks|assignment|assignments|deadline|deadlines)\b"
    r"|\b(?:today|tomorrow|due)\b)",
    re.IGNORECASE,
)

_EXPLICIT_COMPLETE_PATTERNS = (
    re.compile(
        r"\bmark\b.*\bas\s+(?:completed|complete|done|finished)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:i|i've|i\s+have|we)\s+(?:completed|finished|done|submitted)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:i'm|im|i\s+am)\s+done\s+with\b", re.IGNORECASE),
    re.compile(r"\bdone\s+with\b", re.IGNORECASE),
)

_REOPEN_PATTERNS = (
    re.compile(r"\breopen\b", re.IGNORECASE),
    re.compile(r"\buncomplete\b", re.IGNORECASE),
    re.compile(
        r"\bmark\b.*\bas\s+(?:pending|incomplete|not\s+done|open)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:not\s+completed\s+yet|still\s+pending)\b",
        re.IGNORECASE,
    ),
)

_CANCEL_RE = re.compile(r"\bcancel\b", re.IGNORECASE)
_DELETE_RE = re.compile(r"\b(?:delete|remove|purge)\b", re.IGNORECASE)

_CREATE_PATTERNS = (
    re.compile(
        r"\b(?:create|add|set\s+up|schedule)\b.*\b(?:task|reminder)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bremind\s+me\b", re.IGNORECASE),
)

_OBLIGATION_CREATE_RE = re.compile(
    r"\bi\s+(?:have|need|want|must|should)\s+to\b",
    re.IGNORECASE,
)

_FUTURE_COMPLETE_RE = re.compile(
    rf"\b(?:complete|finish|submit|do)\b.*"
    rf"(?:\b(?:by|before|due)\b"
    rf"|\b(?:today|tomorrow|day\s+after\s+tomorrow)\b"
    rf"|\b(?:this|next|coming)\s+(?:{_WEEKDAYS})\b"
    rf"|\bin\s+\w+\s+(?:days?|weeks?)\b"
    rf"|\b\d{{1,2}}\s+(?:{_MONTHS})\b)",
    re.IGNORECASE,
)

_UPDATE_RE = re.compile(
    r"\b(?:update|change|move|reschedule|rename|set|make)\b.*"
    r"(?:\b(?:deadline|due\s+date|priority|status|category|title|"
    r"description)\b"
    r"|\b(?:to|as)\s+(?:next|this|coming)\s+(?:"
    rf"{_WEEKDAYS})\b"
    r"|\b(?:to|as)\s+(?:today|tomorrow)\b"
    r"|\b(?:to|for)\s+(?:\d{1,2}\s+(?:"
    rf"{_MONTHS})|(?:{_MONTHS})\s+\d{{1,2}})"
    r"|\bto\s+\d{4}-\d{1,2}-\d{1,2}\b"
    r"|\brename\b.*\bto\b"
    r"|\b(?:low|medium|high|urgent)\s+priority\b)",
    re.IGNORECASE,
)

_GENERIC_COMPLETE_RE = re.compile(
    r"\b(?:complete|finish|submit)\b",
    re.IGNORECASE,
)

_POSSESSIVE_ACTION_RE = re.compile(
    r"^\s*(?:(?:please|kindly)\s+)?"
    r"(complete|finish|submit)\s+(?:my|our)\b",
    re.IGNORECASE,
)

_STOPWORDS = {
    "a",
    "an",
    "the",
    "my",
    "our",
    "task",
    "tasks",
    "to",
    "for",
    "of",
    "please",
}

_GENERIC_TITLES = {
    "",
    "task",
    "tasks",
    "reminder",
    "it",
    "this",
    "that",
    "something",
    "thing",
    "stuff",
}

_LEADING_COMMAND_RE = re.compile(
    r"^(?:(?:please|kindly)\s+)?(?:"
    r"(?:can|could|would|will)\s+you\s+"
    r"|(?:create|add|make|set\s+up|schedule)\s+(?:a|an|the)?\s*"
    r"(?:new\s+)?(?:(?:high|low|medium|urgent)[- ]priority\s+)?"
    r"(?:task|reminder)\s*(?:to|for|about|that)?\s*"
    r"|remind\s+me\s+(?:to|about|that)\s+"
    r"|i\s+(?:have|need|want|must|should)\s+to\s+"
    r"|i\s+(?:have|need|want|must|should)\s+"
    r")",
    re.IGNORECASE,
)

_PRIORITY_PHRASE_RE = re.compile(
    r"\b(?:with\s+)?(?:an?\s+)?"
    r"(?:high|low|medium|urgent)[- ]priority\b",
    re.IGNORECASE,
)
_PRIORITY_VALUE_RE = re.compile(
    r"\bpriority\s*(?:is|:)?\s*(?:low|medium|high|urgent)\b",
    re.IGNORECASE,
)
_URGENT_TAIL_RE = re.compile(r"\b(?:with\s+)?urgent\b\s*$", re.IGNORECASE)

_ACTION_VERB_RE = re.compile(
    r"^(?:complete|finish|do|submit|work\s+on|study\s+for|prepare\s+for|"
    r"revise|review)\s+(?:my\s+|our\s+|the\s+|a\s+|an\s+)?",
    re.IGNORECASE,
)

_TARGET_PATTERNS = (
    re.compile(
        r"\b(?:set|change|update|move|reschedule)\s+(?:the\s+)?"
        r"(?:deadline|due\s+date)\s+(?:of|for)\s+"
        r"(?:my|our|the)?\s*(.+?)\s+(?:to|as)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bmake\s+(?:my|our|the)?\s*(.+?)\s+"
        r"(?:high|medium|low|urgent)\s+priority\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bmark\s+(?:my|our|the)?\s*(.+?)\s+as\s+"
        r"(?:completed|complete|done|finished|pending|incomplete|"
        r"not\s+done|open)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:complete|completed|finish|finished|reopen|reopened|cancel|"
        r"delete|remove|purge|update|change|move|reschedule|rename|set|"
        r"make|find|get|look\s+up)\s+(?:my|our|the)?\s*(.+?)"
        r"(?:\s+(?:as|to|by|before|on|for|with|from|at)\b.*)?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bi\s+(?:completed|finished|did|submitted)\s+"
        r"(?:my|our|the)?\s*(.+?)"
        r"(?:\s+(?:yesterday|today|last\s+week|earlier)\b.*)?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bstatus\s+of\s+(?:my|our|the)?\s*(.+?)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bdid\s+i\s+(?:complete|finish|do|submit)\s+"
        r"(?:my|our|the)?\s*(.+?)$",
        re.IGNORECASE,
    ),
)

_RENAME_RE = re.compile(
    r"\brename\s+(?:my|our|the)?\s*.+?\s+to\s+(.+?)$",
    re.IGNORECASE,
)
_DESCRIPTION_RE = re.compile(
    r"\b(?:with\s+)?description\s*(?:to|:|-)\s*(.+?)$",
    re.IGNORECASE,
)
_EXPLICIT_CATEGORY_RE = re.compile(
    r"\bcategory\s*(?:to|as|:)?\s*"
    r"(college|career|personal|fitness|project|other)\b",
    re.IGNORECASE,
)
_CATEGORY_SUFFIX_RE = re.compile(
    r"\b(college|career|personal|fitness|project|other)\s+category\b",
    re.IGNORECASE,
)

_CATEGORY_KEYWORDS = (
    (
        TaskCategory.PROJECT,
        re.compile(
            r"\b(?:project|portfolio|deploy|deployment|code|coding|build|"
            r"app|application|repository|repo)\b",
            re.IGNORECASE,
        ),
    ),
    (
        TaskCategory.COLLEGE,
        re.compile(
            r"\b(?:assignment|homework|exam|quiz|semester|college|"
            r"university|class|course|study|studies|lab|lecture|revision|"
            r"thesis|dissertation|coursework)\b",
            re.IGNORECASE,
        ),
    ),
    (
        TaskCategory.CAREER,
        re.compile(
            r"\b(?:interview|resume|cv|job|internship|career|application|"
            r"meeting|conference|linkedin)\b",
            re.IGNORECASE,
        ),
    ),
    (
        TaskCategory.FITNESS,
        re.compile(
            r"\b(?:gym|workout|exercise|run|jog|yoga|fitness|diet|"
            r"training|walk)\b",
            re.IGNORECASE,
        ),
    ),
    (
        TaskCategory.PERSONAL,
        re.compile(
            r"\b(?:personal|grocery|groceries|laundry|clean|buy|pay|bill|"
            r"birthday|call|errand)\b",
            re.IGNORECASE,
        ),
    ),
)

_CONFIRMATION_PHRASES = {
    "yes",
    "yep",
    "yeah",
    "yes please",
    "confirm",
    "i confirm",
    "confirmed",
    "proceed",
    "do it",
    "go ahead",
    "ok",
    "okay",
    "sure",
    "please do",
    "yes do it",
    "yes please do",
}

_REJECTION_PHRASES = {
    "no",
    "nope",
    "no thanks",
    "no thank you",
    "cancel",
    "cancel it",
    "don't",
    "do not",
    "reject",
    "stop",
    "never mind",
    "nevermind",
    "leave it",
}


def normalise_message(message: str) -> str:
    """Normalise a short confirmation value."""

    cleaned = re.sub(r"[^a-z0-9'\s]", " ", message.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return cleaned


def is_confirmation(message: str) -> bool:
    """Return whether ``message`` is an explicit, short confirmation."""

    return normalise_message(message) in _CONFIRMATION_PHRASES


def is_rejection(message: str) -> bool:
    """Return whether ``message`` is an explicit, short rejection."""

    return normalise_message(message) in _REJECTION_PHRASES


def extract_priority(text: str) -> TaskPriority | None:
    """Return an explicitly stated priority, if any."""

    match = _PRIORITY_VALUE_RE.search(text) or re.search(
        r"\b(low|medium|high|urgent)[- ]priority\b",
        text,
        re.IGNORECASE,
    )

    if match:
        return TaskPriority(match.group(1).lower())

    if re.search(r"\burgent\b", text, re.IGNORECASE):
        return TaskPriority.URGENT

    return None


def infer_category(text: str) -> TaskCategory:
    """Infer a category from explicit vocabulary, defaulting to ``other``."""

    for category, pattern in _CATEGORY_KEYWORDS:
        if pattern.search(text):
            return category

    return TaskCategory.OTHER


def extract_category(text: str) -> TaskCategory | None:
    """Return a category only when the user states it explicitly."""

    match = _EXPLICIT_CATEGORY_RE.search(text) or _CATEGORY_SUFFIX_RE.search(
        text
    )

    if not match:
        return None

    return TaskCategory(match.group(1).lower())


def extract_status_filter(text: str) -> TaskStatus | None:
    """Return a status explicitly mentioned in a query."""

    lowered = text.lower()

    if re.search(r"\bin[- ]progress\b", lowered):
        return TaskStatus.IN_PROGRESS

    if re.search(r"\bcancell?ed\b", lowered):
        return TaskStatus.CANCELLED

    if re.search(r"\b(?:pending|open|unfinished)\b", lowered):
        return TaskStatus.PENDING

    if re.search(r"\b(?:completed|done|finished)\b", lowered):
        return TaskStatus.COMPLETED

    return None


def extract_filters(text: str) -> TaskFilters:
    """Extract query filters without inventing values.

    ``other`` is a fallback for *creation*, not a filter a user asked for, so a
    query that mentions no category keyword must not be restricted to it.
    """

    explicit = extract_category(text)

    if explicit is None and TASK_NOUN_RE.search(text):
        inferred = infer_category(text)
        explicit = None if inferred == TaskCategory.OTHER else inferred

    return TaskFilters(
        category=explicit,
        priority=extract_priority(text),
        status=extract_status_filter(text),
    )


def _clean_target(value: str) -> str | None:
    cleaned = value.strip().strip(" .,;:!?-")

    cleaned = re.sub(
        r"^(?:my|our|the|a|an)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:task|tasks|reminder|deadline|due\s+date|priority|status|"
        r"category|title|description)\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:yesterday|today|tomorrow|please)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" .,;:!?-")

    if not cleaned or cleaned.lower() in _GENERIC_TITLES:
        return None

    return cleaned[:255]


def extract_target_hint(text: str) -> str | None:
    """Extract the task phrase a mutation refers to."""

    for pattern in _TARGET_PATTERNS:
        match = pattern.search(text)

        if not match:
            continue

        cleaned = _clean_target(match.group(1))

        if cleaned:
            return cleaned

    return None


def _extract_create_title(text: str) -> str | None:
    cleaned = text.strip().rstrip(".!?")

    for _ in range(4):
        updated = _LEADING_COMMAND_RE.sub("", cleaned, count=1)

        if updated == cleaned:
            break

        cleaned = updated

    cleaned = _PRIORITY_PHRASE_RE.sub(" ", cleaned)
    cleaned = _PRIORITY_VALUE_RE.sub(" ", cleaned)
    cleaned = _URGENT_TAIL_RE.sub(" ", cleaned)
    cleaned = _DESCRIPTION_RE.sub(" ", cleaned)
    cleaned = strip_deadline_phrases(cleaned)
    cleaned = _ACTION_VERB_RE.sub("", cleaned, count=1)
    cleaned = re.sub(
        r"^(?:my|our|the|a|an)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:task|reminder)\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,.;:-")

    if not cleaned or cleaned.lower() in _GENERIC_TITLES:
        return None

    return cleaned[0].upper() + cleaned[1:]


def _deadline_result(
    resolution: DeadlineResolution,
    intent: TaskIntent,
) -> TaskIntentResult | None:
    if resolution.invalid_reason:
        return TaskIntentResult(
            intent=intent,
            invalid_reason=resolution.invalid_reason,
        )

    if resolution.clarification:
        return TaskIntentResult(
            intent=intent,
            clarification=resolution.clarification,
        )

    return None


def _build_draft(
    text: str,
    now: datetime,
    default_timezone: str | None,
    *,
    title: str | None = None,
    category: TaskCategory | None = None,
    priority: TaskPriority | None = None,
    description: str | None = None,
) -> TaskIntentResult:
    resolution = resolve_deadline(text, now, default_timezone)
    problem = _deadline_result(resolution, TaskIntent.CREATE_TASK)

    if problem is not None:
        return problem

    resolved_title = title or _extract_create_title(text)

    if not resolved_title:
        return TaskIntentResult(
            intent=TaskIntent.CREATE_TASK,
            clarification=(
                "What should I name this task? "
                "Please give a short title."
            ),
        )

    resolved_description = description

    if resolved_description is None:
        description_match = _DESCRIPTION_RE.search(text)

        if description_match:
            resolved_description = description_match.group(1).strip()

    try:
        draft = TaskDraft(
            title=resolved_title,
            description=resolved_description,
            category=category or infer_category(text),
            priority=(
                priority
                or extract_priority(text)
                or TaskPriority.MEDIUM
            ),
            due_date=resolution.due_date,
            due_time=resolution.due_time,
            timezone=resolution.timezone,
        )

    except ValueError as error:
        return TaskIntentResult(
            intent=TaskIntent.NO_TASK_ACTION,
            invalid_reason=str(error),
        )

    return TaskIntentResult(
        intent=TaskIntent.CREATE_TASK,
        draft=draft,
    )


def _build_changes(
    text: str,
    now: datetime,
    default_timezone: str | None,
    *,
    target_hint: str | None = None,
    title: str | None = None,
    description: str | None = None,
    category: TaskCategory | None = None,
    priority: TaskPriority | None = None,
) -> TaskIntentResult:
    resolution = resolve_deadline(text, now, default_timezone)
    problem = _deadline_result(resolution, TaskIntent.UPDATE_TASK)

    if problem is not None:
        return problem

    changes: dict = {}

    if resolution.found:
        changes["due_date"] = resolution.due_date

        if resolution.due_time is not None:
            changes["due_time"] = resolution.due_time
            changes["timezone"] = resolution.timezone
        elif resolution.timezone is not None:
            changes["timezone"] = resolution.timezone

    resolved_priority = priority or extract_priority(text)

    if resolved_priority is not None:
        changes["priority"] = resolved_priority

    resolved_category = category or extract_category(text)

    if resolved_category is not None:
        changes["category"] = resolved_category

    rename_match = _RENAME_RE.search(text)

    if rename_match:
        changes["title"] = rename_match.group(1).strip().strip(" .,;:!?")

    elif title:
        changes["title"] = title

    description_match = _DESCRIPTION_RE.search(text)

    if description_match:
        changes["description"] = description_match.group(1).strip()

    elif description:
        changes["description"] = description

    if not changes:
        return TaskIntentResult(
            intent=TaskIntent.UPDATE_TASK,
            target_hint=target_hint or extract_target_hint(text),
            clarification=(
                "What would you like to change about that task? "
                "For example its deadline, priority, category, or title."
            ),
        )

    try:
        validated = TaskChanges(**changes)

    except ValueError as error:
        return TaskIntentResult(
            intent=TaskIntent.NO_TASK_ACTION,
            invalid_reason=str(error),
        )

    return TaskIntentResult(
        intent=TaskIntent.UPDATE_TASK,
        changes=validated,
        target_hint=target_hint or extract_target_hint(text),
    )


def detect_deterministic(
    message: str,
    now: datetime,
    default_timezone: str | None = None,
) -> TaskIntentResult:
    """Classify and extract a message without calling an LLM."""

    text = message.strip()
    lowered = text.lower()

    if not text:
        return TaskIntentResult()

    # 1. Status questions must beat completion verbs.
    for pattern in _STATUS_QUERY_PATTERNS:
        if pattern.search(text):
            return TaskIntentResult(
                intent=TaskIntent.TASK_STATUS_QUERY,
                target_hint=extract_target_hint(text),
            )

    # 2. Deterministic task queries.
    is_query = bool(
        _QUERY_START_RE.search(text)
        or _FIND_START_RE.search(text)
        or _QUERY_WORD_RE.search(text)
        or _HAVE_QUERY_RE.search(text)
    )

    if is_query:
        find_query = bool(_FIND_START_RE.search(text))

        if re.search(r"\bupcoming\b", lowered):
            intent = TaskIntent.UPCOMING_TASKS
        elif re.search(r"\boverdue\b", lowered):
            intent = TaskIntent.OVERDUE_TASKS
        elif re.search(r"\btomorrow\b", lowered):
            intent = TaskIntent.TOMORROW_TASKS
        elif re.search(r"\btoday\b", lowered):
            intent = TaskIntent.TODAY_TASKS
        elif find_query:
            intent = TaskIntent.GET_TASK
        else:
            intent = TaskIntent.LIST_TASKS

        return TaskIntentResult(
            intent=intent,
            target_hint=(
                extract_target_hint(text)
                if intent == TaskIntent.GET_TASK
                else None
            ),
            filters=(
                extract_filters(text)
                if intent == TaskIntent.LIST_TASKS
                else None
            ),
        )

    has_task_noun = bool(TASK_NOUN_RE.search(text))

    # 3. Explicit completion markers.
    for pattern in _EXPLICIT_COMPLETE_PATTERNS:
        if pattern.search(text):
            return TaskIntentResult(
                intent=TaskIntent.COMPLETE_TASK,
                target_hint=extract_target_hint(text),
            )

    # 4. Explicit reopen markers.
    for pattern in _REOPEN_PATTERNS:
        if pattern.search(text):
            return TaskIntentResult(
                intent=TaskIntent.REOPEN_TASK,
                target_hint=extract_target_hint(text),
            )

    # 5. Destructive operations require a task noun.
    if has_task_noun and _CANCEL_RE.search(text):
        return TaskIntentResult(
            intent=TaskIntent.CANCEL_TASK,
            target_hint=extract_target_hint(text),
        )

    if has_task_noun and _DELETE_RE.search(text):
        return TaskIntentResult(
            intent=TaskIntent.DELETE_TASK,
            target_hint=extract_target_hint(text),
        )

    # 6. Explicit creation language.
    create_match = any(
        pattern.search(text) for pattern in _CREATE_PATTERNS
    )

    if create_match:
        return _build_draft(text, now, default_timezone)

    # 7. Updates beat obligation phrases such as "I have to update...".
    if has_task_noun and (
        _UPDATE_RE.search(text) or _RENAME_RE.search(text)
    ):
        return _build_changes(text, now, default_timezone)

    # 8. "I have to finish my assignment by Friday" creates a task, but only
    #    when the action is not a destructive or state-changing verb.
    if _OBLIGATION_CREATE_RE.search(text) and not (
        _CANCEL_RE.search(text) or _DELETE_RE.search(text)
    ):
        return _build_draft(text, now, default_timezone)

    # 9. A completion verb attached to a future deadline creates a task.
    if _FUTURE_COMPLETE_RE.search(text):
        return _build_draft(text, now, default_timezone)

    # 10. Explicit "complete my ..." style actions do not need a task noun.
    possessive = _POSSESSIVE_ACTION_RE.search(text)

    if possessive:
        return TaskIntentResult(
            intent=TaskIntent.COMPLETE_TASK,
            target_hint=extract_target_hint(text),
        )

    # 11. Generic completion of an existing task.
    if has_task_noun and _GENERIC_COMPLETE_RE.search(text):
        return TaskIntentResult(
            intent=TaskIntent.COMPLETE_TASK,
            target_hint=extract_target_hint(text),
        )

    return TaskIntentResult()


def _looks_task_related(message: str) -> bool:
    """Conservatively decide whether an LLM fallback is worth trying."""

    return bool(
        _MUTATION_VERB_RE.search(message)
        and (
            TASK_NOUN_RE.search(message)
            or re.search(
                r"\b(?:deadline|due|priority|schedule)\b",
                message,
                re.IGNORECASE,
            )
        )
    )


def _llm_prompt(message: str, now: datetime) -> str:
    intents = ", ".join(intent.value for intent in TaskIntent)
    categories = ", ".join(item.value for item in TaskCategory)
    priorities = ", ".join(item.value for item in TaskPriority)
    statuses = ", ".join(item.value for item in TaskStatus)

    return (
        "You classify task-management requests for ASTA.\n"
        "Return ONLY a JSON object and no prose.\n"
        "Never invent task IDs, user IDs, owners, or database fields.\n"
        "If the message is ordinary discussion or a question that is not a "
        "task-management request, use intent \"no_task_action\".\n"
        "Do not treat a general statement about having many assignments as "
        "a request to create a task.\n"
        f"Current UTC datetime: {now.isoformat()}\n"
        f"Allowed intents: {intents}\n"
        f"Allowed categories: {categories}\n"
        f"Allowed priorities: {priorities}\n"
        f"Allowed statuses: {statuses}\n"
        "JSON fields (all optional except intent): intent, title, "
        "description, category, priority, due_expression, time_expression, "
        "timezone, target_hint, category_filter, priority_filter, "
        "status_filter.\n"
        "Use due_expression for the exact date phrase (for example "
        "\"next Monday\") and time_expression for the exact time phrase "
        "(for example \"6 PM\"). Never resolve dates yourself.\n"
        "User message:\n"
        f"{message}\n"
        "JSON:"
    )


def _parse_json_object(raw: str) -> dict | None:
    if not raw:
        return None

    start = raw.find("{")
    end = raw.rfind("}")

    if start == -1 or end <= start:
        return None

    try:
        parsed = json.loads(raw[start:end + 1])

    except (TypeError, ValueError):
        return None

    return parsed if isinstance(parsed, dict) else None


def _from_llm(
    llm: LLMTaskIntent,
    original: str,
    now: datetime,
    default_timezone: str | None,
) -> TaskIntentResult:
    if llm.intent == TaskIntent.NO_TASK_ACTION:
        return TaskIntentResult()

    target_hint = llm.target_hint or extract_target_hint(original)

    if llm.intent == TaskIntent.CREATE_TASK:
        date_text = " ".join(
            part
            for part in (
                llm.due_expression or "",
                llm.time_expression or "",
                llm.timezone or "",
            )
            if part
        )

        if not date_text:
            date_text = original

        return _build_draft(
            date_text,
            now,
            default_timezone,
            title=llm.title,
            category=llm.category,
            priority=llm.priority,
            description=llm.description,
        )

    if llm.intent == TaskIntent.UPDATE_TASK:
        date_text = " ".join(
            part
            for part in (
                llm.due_expression or "",
                llm.time_expression or "",
                llm.timezone or "",
            )
            if part
        )

        return _build_changes(
            date_text or original,
            now,
            default_timezone,
            target_hint=target_hint,
            title=llm.title,
            description=llm.description,
            category=llm.category,
            priority=llm.priority,
        )

    filters = None

    if (
        llm.category_filter is not None
        or llm.priority_filter is not None
        or llm.status_filter is not None
    ):
        filters = TaskFilters(
            category=llm.category_filter,
            priority=llm.priority_filter,
            status=llm.status_filter,
        )

    return TaskIntentResult(
        intent=llm.intent,
        target_hint=target_hint,
        filters=filters,
    )


class TaskIntentDetector:
    """Deterministic-first intent detector with a guarded LLM fallback."""

    async def detect(
        self,
        message: str,
        *,
        private: bool = False,
        now: datetime,
        default_timezone: str | None = None,
    ) -> TaskIntentResult:
        deterministic = detect_deterministic(
            message,
            now=now,
            default_timezone=default_timezone,
        )

        if deterministic.intent != TaskIntent.NO_TASK_ACTION:
            return deterministic

        if not settings.TASK_INTENT_LLM_ENABLED:
            return deterministic

        if not _looks_task_related(message):
            return deterministic

        try:
            result = await llm_router.generate_response(
                prompt=_llm_prompt(message, now),
                private=private,
            )

        except Exception:
            # Task understanding must never break normal chat. Do not log the
            # message content or any extracted values.
            logger.warning(
                "LLM task-intent interpretation was unavailable."
            )
            return deterministic

        parsed = _parse_json_object(result.get("response") or "")

        if parsed is None:
            return deterministic

        try:
            llm_intent = LLMTaskIntent.model_validate(parsed)

        except ValueError:
            logger.warning("LLM task-intent output failed validation.")
            return deterministic

        try:
            return _from_llm(
                llm_intent,
                message,
                now=now,
                default_timezone=default_timezone,
            )

        except ValueError:
            logger.warning("LLM task-intent extraction failed validation.")
            return deterministic


task_intent_detector = TaskIntentDetector()


__all__ = [
    "TaskIntentDetector",
    "detect_deterministic",
    "extract_category",
    "extract_filters",
    "extract_priority",
    "extract_status_filter",
    "extract_target_hint",
    "infer_category",
    "is_confirmation",
    "is_rejection",
    "normalise_message",
    "task_intent_detector",
]
