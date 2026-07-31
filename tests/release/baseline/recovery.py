"""Recovery policy and budget records for release validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class RecoveryPolicy:
    total_budget_minutes: int
    allowed_resources: tuple[str, ...]
    rbac_actions: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryAttempt:
    action: str
    scope: str
    resource: str | None
    started_at: str
    ended_at: str
    status: str
    allowed_by_profile: bool
    evidence_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence_paths"] = list(self.evidence_paths)
        return payload


@dataclass
class RecoveryBudget:
    policy: RecoveryPolicy
    attempts: list[RecoveryAttempt] = field(default_factory=list)
    hard_stops: list[dict[str, Any]] = field(default_factory=list)

    def record_attempt(
        self,
        *,
        action: str,
        scope: str,
        resource: str | None,
        status: str,
        evidence_paths: tuple[str, ...],
    ) -> RecoveryAttempt:
        now = datetime.now(timezone.utc).isoformat()
        allowed = resource is None or resource in self.policy.allowed_resources
        attempt = RecoveryAttempt(
            action=action,
            scope=scope,
            resource=resource,
            started_at=now,
            ended_at=now,
            status=status,
            allowed_by_profile=allowed,
            evidence_paths=evidence_paths,
        )
        self.attempts.append(attempt)
        return attempt

    def to_artifact(self) -> dict[str, Any]:
        failed = any(item.status == "failed" for item in self.attempts) or bool(self.hard_stops)
        return {
            "schema_version": 1,
            "budget_minutes": self.policy.total_budget_minutes,
            "budget_consumed_seconds": 0,
            "pre_run": [item.to_dict() for item in self.attempts],
            "post_failure": [],
            "hard_stops": self.hard_stops,
            "status": "failed" if failed else "passed",
        }


def plan_recovery_actions(*, drift: dict[str, Any], policy: RecoveryPolicy) -> list[dict[str, Any]]:
    candidates = []
    if drift.get("stale_restore"):
        candidates.append({"action": "cleanup", "resource": "Restore"})
    if drift.get("stale_backup_schedule"):
        candidates.append({"action": "cleanup", "resource": "BackupSchedule"})
    planned = []
    for candidate in candidates:
        planned.append({**candidate, "allowed_by_profile": candidate["resource"] in policy.allowed_resources})
    return planned
