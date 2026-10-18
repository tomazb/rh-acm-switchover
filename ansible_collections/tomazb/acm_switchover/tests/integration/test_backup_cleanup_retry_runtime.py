"""Generated pre-enable cleanup failures through the shipped finalization role."""

import copy
import json

import pytest

from ansible_collections.tomazb.acm_switchover.tests.integration.test_activation_transaction_runtime import (  # noqa: F401
    complete_on_mutation,
    passive_restore,
    run_activation,
)
from ansible_collections.tomazb.acm_switchover.tests.integration.test_backup_continuity_runtime import (
    BACKUPS,
    BackupLifecycleHub,
)
from ansible_collections.tomazb.acm_switchover.tests.integration.test_finalization_cleanup_runtime import (  # noqa: F401
    REPAIR,
    completed_journal,
    run_finalization,
)


@pytest.fixture
def hub():
    fake = BackupLifecycleHub(
        cluster_uid="uid-secondary", restores=[passive_restore()], controller=complete_on_mutation
    )
    yield fake
    fake.close()


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("failure", ["poll-timeout", "recovery-required"])
@pytest.mark.parametrize("evidence", ["fresh", "no-schedule", "malformed-boundary", "wrong-uid"])
def test_generated_cleanup_retry_reaches_repair_and_requires_new_backup_proof(
    run_activation, run_finalization, hub, restore, failure, evidence  # noqa: F811
):
    journal = completed_journal(run_activation, "full_restore", hub)
    original_restore = copy.deepcopy(hub.acm[journal["restore"]["name"]])
    options = {"restore": restore, "old_action": "none"}
    failed = run_finalization("full_restore", hub, journal, extra={"_acm_cleanup_poll_delays": []}, **options)
    assert failed.journal["cleanup"]["state"] == "delete_accepted", failed.output
    if failure == "recovery-required":
        failed = run_finalization("full_restore", hub, None, **options)
    generated = copy.deepcopy(failed.journal)
    assert generated["cleanup"]["state"] == ("delete_accepted" if failure == "poll-timeout" else "recovery_required")
    raw = json.loads(failed.path.read_bytes())
    assert "backup_schedule_enabled_at" not in raw["operational_data"]
    if evidence == "malformed-boundary":
        raw["operational_data"]["backup_schedule_enabled_at"] = None
        failed.path.write_text(json.dumps(raw))
    if failure == "poll-timeout":
        # An accepted asynchronous DELETE can leave the original UID present.
        # Retry must perform its own guarded DELETE and uninterrupted absence proof.
        hub.acm[journal["restore"]["name"]] = original_restore
    request = dict(REPAIR, run_id=journal["run_id"], operation_id=generated["cleanup"]["operation_id"])
    extra = {"acm_switchover_features": {"skip_observability_checks": True}}
    if evidence == "fresh":
        raw = json.loads(failed.path.read_bytes())
        raw["operational_data"]["primary_has_observability"] = False
        raw["operational_data"]["saved_backup_schedule"] = {
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "BackupSchedule",
            "metadata": {
                "name": "retry-schedule",
                "namespace": "open-cluster-management-backup",
                "uid": "schedule-uid",
                "resourceVersion": "42",
            },
            "spec": {"paused": True, "veleroSchedule": "*/5 * * * *", "veleroTTL": "24h0m0s"},
        }
        failed.path.write_text(json.dumps(raw))
        hub.complete_backup_after_enable = True
    elif evidence == "no-schedule":
        # Prove that historical no-schedule does not silently skip the reader;
        # denied discovery refuses immediately without substituting a shorter poll.
        hub.failures[("GET", BACKUPS)] = 403
    elif evidence == "wrong-uid":
        hub.cluster_uid = "wrong-live-secondary"
    writes = len(hub.writes)
    retry = run_finalization(
        "full_restore",
        hub,
        None,
        entry="main",
        extra=extra,
        **({"repair": request} if failure == "recovery-required" else {}),
        **options,
    )
    after = json.loads(retry.path.read_bytes())
    (retry.path.parent / "backup-cleanup-retry-evidence.json").write_text(
        json.dumps(
            {
                "restore_only": restore,
                "failure": failure,
                "evidence": evidence,
                "generated_journal": generated,
                "before_retry": raw,
                "after_retry": after,
                "requests": hub.requests,
                "returncode": retry.completed.returncode,
                "output": retry.output,
            },
            indent=2,
            default=str,
        )
    )
    if evidence in ("malformed-boundary", "wrong-uid"):
        assert retry.completed.returncode != 0, retry.output
        assert retry.journal == generated and len(hub.writes) == writes
        if evidence == "malformed-boundary":
            assert after["operational_data"]["backup_schedule_enabled_at"] is None
        else:
            assert "backup_schedule_enabled_at" not in after["operational_data"]
            assert after == raw
            assert "identity" in retry.output.lower()
        return
    assert retry.journal["cleanup"]["state"] == ("completed" if failure == "poll-timeout" else "repaired"), retry.output
    assert "missing_enable_boundary" not in retry.output
    assert "backup_schedule_enabled_at" in after["operational_data"]
    if evidence == "fresh":
        assert retry.completed.returncode == 0, retry.output
        assert "finalization" in after["completed_phases"]
        assert any(r["path"] == BACKUPS and r["method"] == "GET" for r in hub.requests)
        assert any(r["path"].startswith(BACKUPS + "/") and r["method"] == "GET" for r in hub.requests)
    else:
        assert retry.completed.returncode != 0, retry.output
        assert ("backup_inventory_unavailable" if restore else "No BackupSchedule") in retry.output, retry.output
        assert "finalization" not in after["completed_phases"]
