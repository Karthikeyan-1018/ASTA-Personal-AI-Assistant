"""Prepare a spoken rendering without changing the stored chat response."""

from __future__ import annotations

import re


_CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\((?:https?://)[^)]+\)")
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_MARKDOWN_PREFIX = re.compile(r"^\s{0,3}(?:#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)", re.MULTILINE)
_ABBREVIATIONS = {
    "API": "A P I",
    "CPU": "C P U",
    "GPU": "G P U",
    "HTTP": "H T T P",
    "HTTPS": "H T T P S",
    "JSON": "JAY-son",
    "SQL": "S Q L",
    "URL": "U R L",
    "UI": "U I",
    "UX": "U X",
    "VS": "versus",
}
_MEASUREMENTS = {
    "ms": "millisecond",
    "millisecond": "millisecond",
    "milliseconds": "millisecond",
    "kb": "kilobyte",
    "kilobyte": "kilobyte",
    "kilobytes": "kilobyte",
    "mb": "megabyte",
    "megabyte": "megabyte",
    "megabytes": "megabyte",
    "gb": "gigabyte",
    "gigabyte": "gigabyte",
    "gigabytes": "gigabyte",
    "tb": "terabyte",
    "terabyte": "terabyte",
    "terabytes": "terabyte",
    "hz": "hertz",
    "hertz": "hertz",
    "khz": "kilohertz",
    "kilohertz": "kilohertz",
    "mhz": "megahertz",
    "megahertz": "megahertz",
    "ghz": "gigahertz",
    "gigahertz": "gigahertz",
}
_MEASUREMENT_PATTERN = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(milliseconds?|ms|kilobytes?|kb|megabytes?|mb|"
    r"gigabytes?|gb|terabytes?|tb|kilohertz|khz|megahertz|mhz|gigahertz|"
    r"ghz|hertz|hz)\b",
    re.IGNORECASE,
)
_PERCENT_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\s*%")
_ABBREVIATION_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(_ABBREVIATIONS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def normalize_speech_text(text: str) -> str:
    """Remove presentation syntax and expand a small, explicit tech lexicon."""

    value = _CODE_BLOCK.sub("The code example is shown in the written response.", text)
    value = _MARKDOWN_LINK.sub(r"\1", value)
    value = _URL.sub("", value)
    value = _INLINE_CODE.sub(r"\1", value)
    value = _MARKDOWN_PREFIX.sub("", value)
    value = re.sub(r"(\*\*|__)(.*?)\1", r"\2", value, flags=re.DOTALL)
    value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)|_([^_]+)_", lambda m: m.group(1) or m.group(2), value)
    value = _PERCENT_PATTERN.sub(r"\1 percent", value)

    def expand_measurement(match: re.Match[str]) -> str:
        amount, raw_unit = match.groups()
        unit = _MEASUREMENTS[raw_unit.casefold()]
        return f"{amount} {unit if amount == '1' else unit + 's'}"

    value = _MEASUREMENT_PATTERN.sub(expand_measurement, value)
    value = _ABBREVIATION_PATTERN.sub(
        lambda match: _ABBREVIATIONS[match.group(0).upper()], value
    )
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()
