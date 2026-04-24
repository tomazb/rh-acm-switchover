"""Structured validation result types for collection modules."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    """A single validation finding with stable schema for report artifacts."""

    id: str
    severity: str
    status: str
    message: str
    details: dict = field(default_factory=dict)
    recommended_action: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "severity": self.severity,
            "status": self.status,
            "message": self.message,
            "details": self.details,
            "recommended_action": self.recommended_action,
        }
