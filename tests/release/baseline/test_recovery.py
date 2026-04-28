from __future__ import annotations

from tests.release.baseline.recovery import RecoveryBudget, RecoveryPolicy


def test_recovery_budget_records_allowed_action() -> None:
    budget = RecoveryBudget(policy=RecoveryPolicy(total_budget_minutes=30, allowed_resources=("Restore",), rbac_actions=("revalidate",)))

    attempt = budget.record_attempt(
        action="cleanup",
        scope="secondary",
        resource="Restore",
        status="passed",
        evidence_paths=("recovery.json",),
    )

    assert attempt.allowed_by_profile is True
    assert budget.to_artifact()["status"] == "passed"


def test_recovery_budget_rejects_disallowed_resource() -> None:
    budget = RecoveryBudget(policy=RecoveryPolicy(total_budget_minutes=30, allowed_resources=(), rbac_actions=("revalidate",)))

    attempt = budget.record_attempt(
        action="cleanup",
        scope="secondary",
        resource="BackupSchedule",
        status="skipped",
        evidence_paths=(),
    )

    assert attempt.allowed_by_profile is False
