"""Shared detection patterns for credential-like values.

These patterns are owned by the core layer because they are used by both the
memory service (write-time validation) and the memory retrieval/context
modules (read-time filtering). Keeping them here avoids a dependency from
``app.services`` back into ``app.memory`` and prevents import cycles.
"""

import re


# Conservative patterns for values that look like credentials. Memory
# content matching these patterns is rejected instead of being stored.
SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|gsk|pk|rk)[-_][A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|password|passwd|secret|token|"
        r"access[_-]?token|auth[_-]?token)\s*[:=]\s*\S+"
    ),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{8,}=*"),
)


def contains_secret(value: str) -> bool:
    """Return True when text looks like it contains a credential."""

    return any(pattern.search(value) for pattern in SECRET_PATTERNS)
