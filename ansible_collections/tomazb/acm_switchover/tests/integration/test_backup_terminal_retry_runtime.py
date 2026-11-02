"""Approved terminal-journal recovery through the shipped role and real API writes."""

import copy
import json
from datetime import datetime

import pytest

from ansible_collections.tomazb.acm_switchover.tests.integration.test_backup_cleanup_retry_runtime import (  # noqa: F401
    hub,
    run_activation,
    run_finalization,
)
from ansible_collections.tomazb.acm_switchover.tests.integration.test_backup_continuity_runtime import BACKUPS
from ansible_collections.tomazb.acm_switchover.tests.integration.test_finalization_cleanup_runtime import (
    REPAIR,
    completed_journal,
)

RESTORES = "/apis/cluster.open-cluster-management.io/v1beta1/namespaces/open-cluster-management-backup/restores"


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("terminal", ["completed", "repaired"])
@pytest.mark.parametrize("window", ["inventory", "after-enable"])
@pytest.mark.parametrize("evidence", ["fresh", "no-schedule"])
def test_generated_terminal_retry_requires_new_backup(
    run_activation, run_finalization, hub, restore, terminal, window, evidence
):  # noqa: F811
    journal = completed_journal(run_activation, "full_restore", hub)
    options = {"restore": restore, "old_action": "none", "entry": "main"}
    extra = {"acm_switchover_features": {"skip_observability_checks": True}}
    repair = None
    if terminal == "repaired":
        pending = run_finalization(
            "full_restore", hub, journal, extra={"_acm_cleanup_poll_delays": []}, restore=restore, old_action="none"
        )
        assert pending.journal["cleanup"]["state"] == "delete_accepted", pending.output
        recovery = run_finalization("full_restore", hub, None, restore=restore, old_action="none")
        assert recovery.journal["cleanup"]["state"] == "recovery_required", recovery.output
        repair = dict(REPAIR, run_id=journal["run_id"], operation_id=recovery.journal["cleanup"]["operation_id"])
        path = recovery.path
    else:
        # First run starts from the supported post-activation predecessor.
        pending = run_finalization("full_restore", hub, journal, tasks=(), restore=restore, old_action="none")
        path = pending.path
    raw = json.loads(path.read_bytes())
    if terminal == "completed":
        raw.update(phase="post_activation", phase_status="pass")
    raw["operational_data"].update(
        primary_has_observability=False,
        saved_backup_schedule={
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "BackupSchedule",
            "metadata": {"name": "recovery-schedule", "namespace": "open-cluster-management-backup"},
            "spec": {"paused": True, "veleroSchedule": "*/5 * * * *", "veleroTTL": "24h0m0s"},
        },
    )
    path.write_text(json.dumps(raw))
    faults = []
    original_failure = hub._failure

    def deny_after_terminal(method, request_path):
        state = (hub.journal_probe() or {}).get("cleanup", {}).get("state")
        if window == "inventory" and method == "GET" and request_path == RESTORES and state == terminal:
            faults.append({"path": request_path, "cleanup": state, "status": 503})
            return 503
        return original_failure(method, request_path)

    hub._failure = deny_after_terminal
    failed = run_finalization(
        "full_restore",
        hub,
        None,
        extra=extra,
        repair=repair,
        interrupt_after_enable=window == "after-enable",
        **options
    )
    hub._failure = original_failure
    before = json.loads(failed.path.read_bytes())
    assert failed.completed.returncode != 0, failed.output
    assert before["operational_data"]["migration_backups"]["cleanup"]["state"] == terminal, failed.output
    assert "backup_schedule_enabled_at" not in before["operational_data"]
    if window == "after-enable":
        trace = json.loads((path.parent / "enable-interruption.json").read_bytes())
        assert trace["result"]["changed"] and trace["durable_checkpoint"] == before
        assert hub.schedules and any(
            r["method"] in ("POST", "PATCH") and "backupschedules" in r["path"] for r in hub.requests
        )
        faults.append({"event": trace["event"], "cleanup": terminal, "boundary_present": False})
    else:
        assert faults
    original_backups = copy.deepcopy(hub.backups)
    if evidence == "fresh":
        hub.complete_backup_after_enable = True
    else:
        hub.schedules.clear()
        before["operational_data"].pop("saved_backup_schedule", None)
        path.write_text(json.dumps(before))

        def deny_proof(method, request_path):
            if method == "GET" and request_path == BACKUPS:
                faults.append({"path": request_path, "cleanup": terminal, "status": 403})
                return 403
            return original_failure(method, request_path)

        hub._failure = deny_proof
    request_count = len(hub.requests)
    retry = run_finalization("full_restore", hub, None, extra=extra, **options)
    after = json.loads(retry.path.read_bytes())
    (path.parent / "terminal-retry-evidence.json").write_text(
        json.dumps(
            {
                "restore_only": restore,
                "terminal": terminal,
                "window": window,
                "evidence": evidence,
                "faults": faults,
                "before_retry": before,
                "after_retry": after,
                "requests": hub.requests[request_count:],
                "original_backups": original_backups,
                "final_backups": hub.backups,
                "returncode": retry.completed.returncode,
                "output": retry.output,
            },
            indent=2,
            default=str,
        )
    )
    assert "missing_enable_boundary" not in retry.output, retry.output
    assert retry.journal == before["operational_data"]["migration_backups"]
    assert all(hub.backups[name] == obj for name, obj in original_backups.items())
    if evidence == "fresh":
        assert retry.completed.returncode == 0, retry.output
        assert "finalization" in after["completed_phases"]
        new = hub.backups["acm-managed-clusters-after-enable"]
        assert new["metadata"]["uid"] not in {b["metadata"]["uid"] for b in original_backups.values()}
        assert datetime.fromisoformat(new["metadata"]["creationTimestamp"]) >= datetime.fromisoformat(
            after["operational_data"]["backup_schedule_enabled_at"].replace("Z", "+00:00")
        )
        assert any(r["path"] == BACKUPS + "/" + new["metadata"]["name"] for r in hub.requests[request_count:])
    else:
        assert retry.completed.returncode != 0 and "finalization" not in after["completed_phases"], retry.output
        assert ("backup_inventory_unavailable" if restore else "No BackupSchedule") in retry.output
