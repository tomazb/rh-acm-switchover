"""Redaction scanner and sanitized text types for release artifact persistence."""

from __future__ import annotations

import re
from dataclasses import dataclass


class RedactionError(ValueError):
    """Raised when sensitive material must be rejected instead of redacted."""

    def __init__(self, rejected_class: str) -> None:
        super().__init__(rejected_class)
        self.rejected_class = rejected_class


@dataclass(frozen=True)
class SanitizedText:
    text: str
    redacted_counts_by_class: dict[str, int]


REDACT_PATTERNS = [
    (
        "authorization-header",
        re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
        "Authorization: Bearer [REDACTED]",
    ),
    ("api-token", re.compile(r"(?i)(api[_-]?token=)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    ("pem-block", re.compile(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", re.DOTALL), "[REDACTED PEM BLOCK]"),
]
REJECT_PATTERNS = [
    ("kubeconfig-client-key", re.compile(r"client-key-data\s*:")),
    ("kubeconfig-token", re.compile(r"\btoken\s*:")),
    ("kubernetes-secret-data", re.compile(r"(?m)^\s*(data|stringData)\s*:")),
]


def sanitize_text(text: str) -> SanitizedText:
    counts: dict[str, int] = {}
    sanitized = text
    for klass, pattern in REJECT_PATTERNS:
        if pattern.search(sanitized):
            raise RedactionError(klass)
    for klass, pattern, replacement in REDACT_PATTERNS:
        sanitized, count = pattern.subn(replacement, sanitized)
        if count:
            counts[klass] = count
    return SanitizedText(text=sanitized, redacted_counts_by_class=counts)
