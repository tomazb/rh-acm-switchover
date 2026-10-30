"""Approved active recovery uses new enable/proof, never historical certification."""

import copy
import json
from datetime import timezone
from unittest.mock import Mock, patch

import pytest

import acm_switchover as cli
import modules.backup_continuity as proof_module
import modules.finalization as finalization_module
from lib.exceptions import SwitchoverError
from lib.run_record import RunRecord
from lib.strict_read import StrictReadOutcome
from lib.utils import StateManager
from modules.backup_schedule import BackupScheduleManager
from tests.test_backup_continuity_lifecycle import attempt  # noqa: F401
from tests.test_finalization import ABSENT, CLEANUP_INTENT, REPAIR_REQUEST, _accepted, _cleanup_fin, _live
from tests.test_finalization_backup_strict import backup, page


class InterruptedAfterEnable(BaseException):
    """Simulate process loss before any boundary or step save can run."""


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("terminal", ["completed", "repaired"])
@pytest.mark.parametrize("window", ["inventory", "after-enable"])
@pytest.mark.parametrize("evidence", ["fresh", "no-schedule"])
def test_generated_terminal_retry_requires_a_new_enable_and_backup(
    attempt, restore, terminal, window, evidence, monkeypatch, caplog
):
    args, path, raw, primary, secondary, pt, st, logger = attempt(restore=restore, label="none")
    initial = json.loads(raw)
    for key in ("backup_schedule_enabled_at", "post_switchover_backup_name", "new_backup_detected"):
        initial["config"].pop(key, None)
    initial["completed_steps"] = []
    initial["config"]["migration_backups"] = copy.deepcopy(CLEANUP_INTENT)
    initial["config"]["saved_backup_schedule"] = {
        "apiVersion": "cluster.open-cluster-management.io/v1beta1",
        "kind": "BackupSchedule",
        "metadata": {"name": "recovery-schedule", "namespace": "open-cluster-management-backup"},
        "spec": {"paused": True, "veleroSchedule": "*/5 * * * *"},
    }
    path.write_text(json.dumps(initial))
    state = StateManager(str(path))
    producer = Mock(name="explicit-cleanup-transport")
    producer.dry_run = False
    controller = StrictReadOutcome.from_items(
        [{"metadata": {"name": "multiclusterhub"}, "status": {"currentVersion": "2.14.3"}}]
    )
    producer.list_custom_resources_strict.return_value = controller
    producer.delete_restore_guarded.return_value = _accepted()
    if terminal == "completed":
        producer.get_custom_resource_strict.return_value = _live()
    else:
        producer.get_custom_resource_strict.side_effect = [_live(), ABSENT, _live(uid="replacement", rv="3003")]
    with patch.object(finalization_module.time, "sleep", lambda *_: None), pytest.raises(SwitchoverError) as failure:
        _cleanup_fin(state, producer)._run_restore_cleanup_transaction()
    generated = RunRecord(state).migration_backups()
    assert generated["cleanup"]["state"] == ("delete_accepted" if terminal == "completed" else "recovery_required")
    cli._fail_phase(state, str(failure.value), logger)
    if terminal == "repaired":
        args.repair_migration_cleanup = True
        request = dict(REPAIR_REQUEST, run_id=generated["run_id"], operation_id=generated["cleanup"]["operation_id"])
        for field, value in request.items():
            setattr(args, "cleanup_repair_" + ("evidence" if field == "inspected_evidence" else field), value)

    get, listing = secondary.get_custom_resource_strict, secondary.list_custom_resources_strict
    restore_reads = iter([_live(), ABSENT, ABSENT] if terminal == "completed" else [])
    fault = {"enabled": True}
    faults = []
    old = backup("acm-managed-clusters-before-recovery", "2020-01-01T00:00:00Z")
    st.effects, st.objects = [page([old])], [old]
    events = []

    def read(**kwargs):
        return next(restore_reads, ABSENT) if kwargs["plural"] == "restores" else get(**kwargs)

    def inventory(**kwargs):
        persisted = json.loads(path.read_bytes())
        if kwargs["plural"] == "multiclusterhubs":
            return controller
        if kwargs["plural"] == "restores" and window == "inventory" and fault["enabled"]:
            assert persisted["config"]["migration_backups"]["cleanup"]["state"] == terminal
            faults.append({"plural": "restores", "cleanup": terminal, "fault": "transport_error"})
            return StrictReadOutcome.error("transport_error")
        if kwargs["plural"] == "backups" and not fault["enabled"]:
            if evidence == "no-schedule":
                return StrictReadOutcome.error("forbidden")
            if not events:
                # A distinct controller event after the real boundary write. The old
                # object's immutable metadata is never changed into new proof.
                boundary = persisted["config"]["backup_schedule_enabled_at"]
                new = backup("acm-managed-clusters-after-recovery", proof_module.datetime.now(timezone.utc).isoformat())
                assert new["metadata"]["creationTimestamp"] >= boundary
                events.append({"boundary": boundary, "new": copy.deepcopy(new), "old": copy.deepcopy(old)})
                st.effects, st.objects = [page([old, new])], [new]
        return listing(**kwargs)

    ensure = BackupScheduleManager.ensure_enabled

    def interrupt(manager, version):
        ensure(manager, version)
        persisted = json.loads(path.read_bytes())
        assert persisted["config"]["migration_backups"]["cleanup"]["state"] == terminal
        assert "backup_schedule_enabled_at" not in persisted["config"]
        assert any(r[0] == "schedule_create" for r in st.requests)
        faults.append({"event": "after-actual-enable", "cleanup": terminal, "boundary_present": False})
        raise InterruptedAfterEnable()

    monkeypatch.setattr(secondary, "get_custom_resource_strict", read)
    monkeypatch.setattr(secondary, "list_custom_resources_strict", inventory)
    monkeypatch.setattr(secondary, "delete_restore_guarded", lambda *_a, **_k: _accepted())
    monkeypatch.setattr(cli, "parse_args", lambda: copy.copy(args))
    if window == "after-enable":
        with patch.object(BackupScheduleManager, "ensure_enabled", interrupt), pytest.raises(InterruptedAfterEnable):
            cli.main()
    else:
        with pytest.raises(SystemExit) as stopped:
            cli.main()
        assert stopped.value.code == 1
    before_retry = json.loads(path.read_bytes())
    assert faults and before_retry["config"]["migration_backups"]["cleanup"]["state"] == terminal
    assert "backup_schedule_enabled_at" not in before_retry["config"]
    assert not before_retry["completed_steps"]
    fault["enabled"] = False
    args.repair_migration_cleanup = False
    for field in ("actor", "reason", "evidence", "run_id", "operation_id"):
        setattr(args, "cleanup_repair_" + field, None)
    if evidence == "no-schedule":
        st.schedules = []
        before_retry["config"].pop("saved_backup_schedule", None)
        path.write_text(json.dumps(before_retry))
    caplog.clear()
    caplog.set_level("INFO")
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    after = json.loads(path.read_bytes())
    (path.parent / "terminal-retry-evidence.json").write_text(
        json.dumps(
            {
                "terminal": terminal,
                "window": window,
                "evidence": evidence,
                "restore_only": restore,
                "faults": faults,
                "before_retry": before_retry,
                "after_retry": after,
                "new_events": events,
                "requests": st.requests,
                "returncode": stopped.value.code,
            },
            indent=2,
            default=str,
        )
    )
    assert "missing_enable_boundary" not in caplog.text
    assert stopped.value.code == (0 if evidence == "fresh" else 1)
    assert after["config"]["migration_backups"] == before_retry["config"]["migration_backups"]
    assert after["hub_identities"] == before_retry["hub_identities"]
    assert old == backup("acm-managed-clusters-before-recovery", "2020-01-01T00:00:00Z")
    if evidence == "fresh":
        assert len(events) == 1 and after["current_phase"] == "completed"
        assert after["config"]["post_switchover_backup_name"] == events[0]["new"]["metadata"]["name"]
    else:
        assert after["current_phase"] != "completed" and not events
