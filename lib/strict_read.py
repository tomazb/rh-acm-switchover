"""Shared strict Kubernetes read outcome algebra (R4-03).

Owned by R4-03 and consumed by R4-04. The vocabulary is deliberately small:
exactly one of five outcomes, three of which are positive absence proofs.
`error` is never absence and never an inventory.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class StrictReadStatus(Enum):
    ITEMS = "items"
    CRD_ABSENT = "crd_absent"
    NAMESPACE_ABSENT = "namespace_absent"
    OBJECT_ABSENT = "object_absent"
    ERROR = "error"


_ABSENCE_STATUSES = frozenset(
    {StrictReadStatus.CRD_ABSENT, StrictReadStatus.NAMESPACE_ABSENT, StrictReadStatus.OBJECT_ABSENT}
)


@dataclass(frozen=True)
class StrictReadOutcome:
    """One completed strict read. Exactly one status; items only on ITEMS."""

    status: StrictReadStatus
    items: List[Dict[str, Any]] = field(default_factory=list)
    resource: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
    resource_version: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status is not StrictReadStatus.ITEMS and self.items:
            raise ValueError(f"{self.status.value} outcome must not carry items")
        if self.status is not StrictReadStatus.ITEMS and self.resource is not None:
            raise ValueError(f"{self.status.value} outcome must not carry a resource")
        # An absence proof and an error return no revision. Enforcing it here means no producer
        # can synthesize one, which is what §10.2.1b's provenance rule depends on.
        if self.status is not StrictReadStatus.ITEMS and self.resource_version is not None:
            raise ValueError(f"{self.status.value} outcome must not carry a resource_version")
        if self.resource_version is not None and not self.resource_version:
            raise ValueError("resource_version must be a non-empty string when present")

    @property
    def is_success(self) -> bool:
        return self.status is StrictReadStatus.ITEMS

    @property
    def proves_absence(self) -> bool:
        return self.status in _ABSENCE_STATUSES

    # Named constructors keep call sites explicit about which proof they hold.
    # They cannot be called `items`/`resource`: those names are fields.
    @classmethod
    def from_items(cls, items, resource_version=None) -> "StrictReadOutcome":
        return cls(status=StrictReadStatus.ITEMS, items=list(items), resource_version=resource_version)

    @classmethod
    def from_resource(cls, resource, resource_version=None) -> "StrictReadOutcome":
        return cls(
            status=StrictReadStatus.ITEMS,
            items=[resource],
            resource=resource,
            resource_version=resource_version,
        )

    @classmethod
    def crd_absent(cls, reason: str) -> "StrictReadOutcome":
        return cls(status=StrictReadStatus.CRD_ABSENT, reason=reason)

    @classmethod
    def namespace_absent(cls, reason: str) -> "StrictReadOutcome":
        return cls(status=StrictReadStatus.NAMESPACE_ABSENT, reason=reason)

    @classmethod
    def object_absent(cls, reason: str) -> "StrictReadOutcome":
        return cls(status=StrictReadStatus.OBJECT_ABSENT, reason=reason)

    @classmethod
    def error(cls, reason: str) -> "StrictReadOutcome":
        return cls(status=StrictReadStatus.ERROR, reason=reason)
