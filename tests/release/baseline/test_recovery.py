from __future__ import annotations

from tests.release.baseline.recovery import RecoveryBudget, RecoveryPolicy, plan_recovery_actions


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


def test_plan_recovery_actions_allows_only_profile_resources() -> None:
    actions = plan_recovery_actions(
        drift={"stale_restore": True, "stale_backup_schedule": True},
        policy=RecoveryPolicy(total_budget_minutes=30, allowed_resources=("Restore",), rbac_actions=("revalidate",)),
    )

    assert [item["resource"] for item in actions if item["allowed_by_profile"]] == ["Restore"]
    assert any(item["resource"] == "BackupSchedule" and not item["allowed_by_profile"] for item in actions)
