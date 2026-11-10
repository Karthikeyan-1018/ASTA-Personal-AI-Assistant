"""Deterministic relative date and time resolution.

The resolver never guesses a timezone. A timed deadline requires an explicit
timezone in the message or a configured ``TASK_DEFAULT_TIMEZONE``. Date-only
deadlines may be resolved against UTC and stored without a timezone, matching
the existing deadline semantics in :mod:`app.services.task_deadlines`.

All calculations receive an injected ``now`` instant, which keeps the behaviour
testable. The same phrases are also used by the detector to strip deadline text
from a proposed task title.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo

from app.exceptions.task_exceptions import TaskValidationError
from app.services.task_deadlines import canonical_timezone


UTC = timezone.utc

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

NUMBER_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

AMBIGUOUS_DATE_PHRASES = (
    "sometime",
    "some time",
    "soon",
    "later",
    "asap",
    "next weekend",
    "this weekend",
    "end of the month",
    "end of month",
    "in a while",
    "in a bit",
)

_WEEKDAY_PATTERN = "|".join(WEEKDAYS)
_MONTH_PATTERN = "|".join(sorted(MONTHS, key=len, reverse=True))
_NUMBER_PATTERN = "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))

_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_SLASH_DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
_DAY_MONTH_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?"
    rf"({_MONTH_PATTERN})\b(?:\s*,?\s*(\d{{4}}))?",
    re.IGNORECASE,
)
_MONTH_DAY_RE = re.compile(
    rf"\b({_MONTH_PATTERN})\s+(\d{{1,2}})(?:st|nd|rd|th)?"
    rf"(?:\s*,?\s*(\d{{4}}))?\b",
    re.IGNORECASE,
)
_ORDINAL_DAY_RE = re.compile(
    r"\b(?:on\s+)?the\s+\d{1,2}(?:st|nd|rd|th)\b",
    re.IGNORECASE,
)
_RELATIVE_RE = re.compile(
    rf"\bin\s+(\d+|{_NUMBER_PATTERN})\s+(day|days|week|weeks)\b",
    re.IGNORECASE,
)
_WEEKDAY_RE = re.compile(
    rf"\b(this|next|coming|on)\s+({_WEEKDAY_PATTERN})\b",
    re.IGNORECASE,
)
_BARE_WEEKDAY_RE = re.compile(rf"\b({_WEEKDAY_PATTERN})\b", re.IGNORECASE)

_TIME_COLON_RE = re.compile(
    r"\b(?:at\s+)?(\d{1,2}):(\d{2})\s*(a\.?m\.?|p\.?m\.?)?\b",
    re.IGNORECASE,
)
_TIME_AT_RE = re.compile(
    r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\b",
    re.IGNORECASE,
)
_TIME_MERIDIEM_RE = re.compile(
    r"\b(\d{1,2})\s*(a\.?m\.?|p\.?m\.?)\b",
    re.IGNORECASE,
)
_TIME_OCLOCK_RE = re.compile(
    r"\b(\d{1,2})\s*o'?clock\b",
    re.IGNORECASE,
)

_TIMEZONE_RE = re.compile(
    r"\b(?:timezone|tz)\s*(?:is|=|:)?\s*"
    r"([A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)?)\b",
    re.IGNORECASE,
)
_IANA_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_+\-]*/[A-Za-z0-9_+\-]+)\b")
_UTC_RE = re.compile(r"\b(UTC|GMT)\b", re.IGNORECASE)


@dataclass(frozen=True)
class DeadlineResolution:
    """Result of resolving a natural-language deadline expression."""

    due_date: date | None = None
    due_time: time | None = None
    timezone: str | None = None
    found: bool = False
    clarification: str | None = None
    invalid_reason: str | None = None

    @property
    def has_problem(self) -> bool:
        return self.clarification is not None or self.invalid_reason is not None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def _reference_date(now: datetime, zone: tzinfo) -> date:
    return _as_utc(now).astimezone(zone).date()


def _configured_zone(name: str | None) -> str | None:
    if not name:
        return None

    try:
        return canonical_timezone(name)
    except TaskValidationError:
        return None


def extract_timezone(text: str) -> str | None:
    """Return a canonical IANA timezone mentioned in ``text``, if any."""

    match = _TIMEZONE_RE.search(text) or _IANA_RE.search(text)

    if match:
        candidate = match.group(1)
    else:
        utc_match = _UTC_RE.search(text)

        if not utc_match:
            return None

        candidate = utc_match.group(1)

    try:
        return canonical_timezone(candidate)

    except TaskValidationError as error:
        raise TaskValidationError(
            "Timezone must be a recognised IANA timezone name."
        ) from error


def _number(value: str) -> int:
    lowered = value.lower()

    if lowered.isdigit():
        return int(lowered)

    return NUMBER_WORDS.get(lowered, 0)


def _weekday_date(
    target: int,
    today: date,
    mode: str,
) -> date:
    """Resolve a weekday deterministically.

    ``this``/``coming``/bare weekdays mean the next occurrence including today.
    ``next`` follows a documented next-week rule: the weekday in the following
    ISO week, which is never the same day as ``this`` for the same weekday.
    """

    if mode == "next":
        next_week_start = today - timedelta(days=today.weekday())
        next_week_start += timedelta(days=7)

        return next_week_start + timedelta(days=target)

    offset = (target - today.weekday()) % 7

    return today + timedelta(days=offset)


def _month_name_date(
    day: int,
    month_name: str,
    year: str | None,
    today: date,
) -> date:
    month = MONTHS[month_name.lower()]

    if year:
        return date(int(year), month, day)

    candidate = date(today.year, month, day)

    # A date without a year normally refers to the upcoming occurrence.
    if candidate < today:
        candidate = date(today.year + 1, month, day)

    return candidate


def _parse_time(text: str) -> tuple[time | None, str | None]:
    """Return ``(time, problem)`` for a time expression.

    ``problem`` is ``"ambiguous"`` when a 12-hour value has no AM/PM marker.
    """

    for pattern in (
        _TIME_COLON_RE,
        _TIME_AT_RE,
        _TIME_MERIDIEM_RE,
        _TIME_OCLOCK_RE,
    ):
        match = pattern.search(text)

        if not match:
            continue

        groups = match.groups()

        if pattern is _TIME_COLON_RE:
            hour_text, minute_text, meridiem = groups
        elif pattern is _TIME_AT_RE:
            hour_text, minute_text, meridiem = groups
        else:
            hour_text = groups[0]
            minute_text = None
            meridiem = groups[1] if len(groups) > 1 else None

        try:
            hour = int(hour_text)
            minute = int(minute_text) if minute_text else 0

        except (TypeError, ValueError):
            return None, "invalid"

        if minute < 0 or minute > 59 or hour < 0 or hour > 23:
            return None, "invalid"

        marker = (meridiem or "").lower().replace(".", "")

        if marker in {"am", "pm"}:
            if hour < 1 or hour > 12:
                return None, "invalid"

            if marker == "pm" and hour != 12:
                hour += 12

            if marker == "am" and hour == 12:
                hour = 0

        elif hour <= 12:
            # "at 6" could be 06:00 or 18:00.
            return None, "ambiguous"

        return time(hour=hour, minute=minute), None

    return None, None


def resolve_deadline(
    text: str,
    now: datetime,
    default_timezone: str | None = None,
) -> DeadlineResolution:
    """Resolve a deadline expression relative to ``now``.

    The function reports ambiguity or invalid values instead of guessing.
    """

    if not text or not text.strip():
        return DeadlineResolution()

    lowered = text.lower()
    now_utc = _as_utc(now)

    try:
        explicit_timezone = extract_timezone(text)

    except TaskValidationError:
        return DeadlineResolution(
            found=True,
            invalid_reason=(
                "That timezone is not a recognised IANA timezone."
            ),
        )

    default_zone = _configured_zone(default_timezone)

    if explicit_timezone:
        reference_zone = _zone(explicit_timezone)
    elif default_zone:
        reference_zone = _zone(default_zone)
    else:
        reference_zone = UTC

    today = _reference_date(now_utc, reference_zone)

    due_date: date | None = None
    matched_date = False
    ambiguous_expression: str | None = None

    if "day after tomorrow" in lowered:
        due_date = today + timedelta(days=2)
        matched_date = True

    elif "tomorrow" in lowered:
        due_date = today + timedelta(days=1)
        matched_date = True

    elif "today" in lowered:
        due_date = today
        matched_date = True

    else:
        relative = _RELATIVE_RE.search(text)

        if relative:
            amount = _number(relative.group(1))
            unit = relative.group(2).lower()

            if amount <= 0:
                return DeadlineResolution(
                    found=True,
                    invalid_reason="The relative date must be in the future.",
                )

            days = amount * (7 if unit.startswith("week") else 1)
            due_date = today + timedelta(days=days)
            matched_date = True

    if not matched_date:
        iso = _ISO_DATE_RE.search(text)

        if iso:
            try:
                due_date = date(
                    int(iso.group(1)),
                    int(iso.group(2)),
                    int(iso.group(3)),
                )
            except ValueError:
                return DeadlineResolution(
                    found=True,
                    invalid_reason="That date is not valid.",
                )

            matched_date = True

    if not matched_date:
        day_month = _DAY_MONTH_RE.search(text)

        if day_month:
            try:
                due_date = _month_name_date(
                    int(day_month.group(1)),
                    day_month.group(2),
                    day_month.group(3),
                    today,
                )
            except ValueError:
                return DeadlineResolution(
                    found=True,
                    invalid_reason="That date is not valid.",
                )

            matched_date = True

    if not matched_date:
        month_day = _MONTH_DAY_RE.search(text)

        if month_day:
            try:
                due_date = _month_name_date(
                    int(month_day.group(2)),
                    month_day.group(1),
                    month_day.group(3),
                    today,
                )
            except ValueError:
                return DeadlineResolution(
                    found=True,
                    invalid_reason="That date is not valid.",
                )

            matched_date = True

    if not matched_date:
        weekday = _WEEKDAY_RE.search(text)

        if weekday:
            mode = weekday.group(1).lower()
            due_date = _weekday_date(
                WEEKDAYS[weekday.group(2).lower()],
                today,
                "next" if mode == "next" else "this",
            )
            matched_date = True

    if not matched_date:
        bare_weekday = _BARE_WEEKDAY_RE.search(text)

        if bare_weekday:
            due_date = _weekday_date(
                WEEKDAYS[bare_weekday.group(1).lower()],
                today,
                "this",
            )
            matched_date = True

    # Slash dates are locale-dependent and must never be guessed.
    if not matched_date and _SLASH_DATE_RE.search(text):
        return DeadlineResolution(
            found=True,
            clarification=(
                "That date format is ambiguous. Please use a day, month and "
                "year (for example 15 November 2026) or ISO format "
                "(2026-11-15)."
            ),
        )

    if not matched_date and _ORDINAL_DAY_RE.search(text):
        return DeadlineResolution(
            found=True,
            clarification=(
                "Which month and year did you mean for that day?"
            ),
        )

    if not matched_date:
        for phrase in AMBIGUOUS_DATE_PHRASES:
            if phrase in lowered:
                ambiguous_expression = phrase
                break

    if ambiguous_expression:
        return DeadlineResolution(
            found=True,
            clarification=(
                "That deadline is ambiguous. Please give a specific date, "
                "for example tomorrow, Friday, or 15 November 2026."
            ),
        )

    due_time, time_problem = _parse_time(text)

    if time_problem == "invalid":
        return DeadlineResolution(
            found=True,
            invalid_reason="That time is not valid.",
        )

    if time_problem == "ambiguous":
        return DeadlineResolution(
            found=True,
            clarification=(
                "Did you mean AM or PM? Please give the time explicitly "
                "(for example 6 PM or 18:00)."
            ),
        )

    if due_date is None and due_time is None:
        return DeadlineResolution()

    if due_date is None:
        # A time without a date means today.
        due_date = today

    if due_date < today:
        return DeadlineResolution(
            found=True,
            invalid_reason=(
                "That deadline is in the past. Please choose a future date."
            ),
        )

    timezone_name: str | None = None

    if due_time is not None:
        timezone_name = explicit_timezone or default_zone

        if timezone_name is None:
            return DeadlineResolution(
                found=True,
                clarification=(
                    "What timezone should I use for that deadline? "
                    "Please give an IANA name such as Asia/Kolkata or "
                    "America/New_York."
                ),
            )

        # A time earlier today has already passed.
        zone = _zone(timezone_name)
        local_now = now_utc.astimezone(zone)
        local_candidate = datetime.combine(due_date, due_time).replace(
            tzinfo=zone
        )

        if local_candidate < local_now:
            return DeadlineResolution(
                found=True,
                clarification=(
                    "That time has already passed today. Did you mean "
                    "tomorrow? Please give an explicit date and time."
                ),
            )

    else:
        # Date-only deadlines keep an explicitly supplied timezone but never
        # inherit the configured default, which would silently change meaning.
        timezone_name = explicit_timezone

    return DeadlineResolution(
        due_date=due_date,
        due_time=due_time,
        timezone=timezone_name,
        found=True,
    )


def _zone(name: str) -> tzinfo:
    """Return a tzinfo for an already-validated timezone name."""

    if name.upper() in {"UTC", "GMT", "ETC/UTC"}:
        return UTC

    from zoneinfo import ZoneInfo

    return ZoneInfo(name)


# ---------------------------------------------------------------------------
# Phrase stripping (used for title extraction)
# ---------------------------------------------------------------------------

_STRIP_PATTERNS = (
    re.compile(r"\bday after tomorrow\b", re.IGNORECASE),
    re.compile(r"\btomorrow\b", re.IGNORECASE),
    re.compile(r"\btoday\b", re.IGNORECASE),
    re.compile(
        rf"\bin\s+(?:\d+|{_NUMBER_PATTERN})\s+(?:day|days|week|weeks)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:this|next|coming|on)\s+({_WEEKDAY_PATTERN})\b",
        re.IGNORECASE,
    ),
    re.compile(rf"\b({_WEEKDAY_PATTERN})\b", re.IGNORECASE),
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b"),
    re.compile(
        rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?"
        rf"({_MONTH_PATTERN})\b(?:\s*,?\s*\d{{4}})?",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b({_MONTH_PATTERN})\s+\d{{1,2}}(?:st|nd|rd|th)?"
        rf"(?:\s*,?\s*\d{{4}})?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:on\s+)?the\s+\d{1,2}(?:st|nd|rd|th)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:at\s+)?\d{1,2}:\d{2}\s*(?:a\.?m\.?|p\.?m\.?)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bat\s+\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?)\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}\s*o'?clock\b", re.IGNORECASE),
    re.compile(
        r"\b(?:timezone|tz)\s*(?:is|=|:)?\s*"
        r"[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b[A-Za-z][A-Za-z0-9_+\-]*/[A-Za-z0-9_+\-]+\b"),
    re.compile(r"\b(?:UTC|GMT)\b", re.IGNORECASE),
)


def strip_deadline_phrases(text: str) -> str:
    """Remove date, time, and timezone expressions from ``text``."""

    cleaned = text

    for pattern in _STRIP_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)

    cleaned = re.sub(r"\b(?:by|before|due(?:\s+on)?|on|at)\s*$", " ", cleaned,
                     flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,.;:-")

    return cleaned


__all__ = [
    "DeadlineResolution",
    "extract_timezone",
    "resolve_deadline",
    "strip_deadline_phrases",
]
