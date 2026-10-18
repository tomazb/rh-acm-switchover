"""Production-generated pre-enable cleanup failures through the real CLI retry.

Cleanup strict-read/delete outcomes are explicit non-live boundaries; persistence,
identity discovery, workflow, cleanup decisions and Backup reads remain real.
"""

import copy
import json
from datetime import timezone
from unittest.mock import Mock, patch

import pytest

import acm_switchover as cli
import modules.backup_continuity as proof_module
import modules.finalization as finalization_module
from lib.exceptions import FatalError
from lib.run_record import RunRecord
from lib.strict_read import StrictReadOutcome
from lib.utils import StateManager
from tests.test_backup_continuity_lifecycle import attempt  # noqa: F401
from tests.test_finalization import ABSENT, CLEANUP_INTENT, REPAIR_REQUEST, _accepted, _cleanup_fin, _live


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("failure", ["poll-timeout", "recovery-required"])
@pytest.mark.parametrize("backup", ["fresh", "stale", "no-schedule"])
def test_generated_pre_enable_cleanup_retry_requires_new_proof(attempt, restore, failure, backup, monkeypatch, caplog):
    args, path, raw, primary, secondary, pt, st, logger = attempt(restore=restore, label="none", good=True)
    initial = json.loads(raw)
    for key in ("backup_schedule_enabled_at", "post_switchover_backup_name", "new_backup_detected"):
        initial["config"].pop(key, None)
    initial["completed_steps"] = []
    initial["config"]["migration_backups"] = copy.deepcopy(CLEANUP_INTENT)
    path.write_text(json.dumps(initial))
    state = StateManager(str(path))
    producer = Mock(name="explicit-cleanup-transport")
    producer.dry_run = False
    controller = StrictReadOutcome.from_items(
        [{"metadata": {"name": "multiclusterhub"}, "status": {"currentVersion": "2.14.3"}}]
    )
    producer.list_custom_resources_strict.return_value = controller
    producer.delete_restore_guarded.return_value = _accepted()
    if failure == "poll-timeout":
        producer.get_custom_resource_strict.return_value = _live()
    else:
        producer.get_custom_resource_strict.side_effect = [_live(), ABSENT, _live(uid="replacement", rv="3003")]
    with patch.object(finalization_module.time, "sleep", lambda *_: None), pytest.raises(FatalError) as generated:
        _cleanup_fin(state, producer)._run_restore_cleanup_transaction()
    production = RunRecord(state).migration_backups()
    expected = "delete_accepted" if failure == "poll-timeout" else "recovery_required"
    assert production["cleanup"]["state"] == expected
    assert "backup_schedule_enabled_at" not in state.state["config"]
    cli._fail_phase(state, str(generated.value), logger)
    if failure == "recovery-required":
        request = dict(REPAIR_REQUEST, run_id=production["run_id"], operation_id=production["cleanup"]["operation_id"])
        args.repair_migration_cleanup = True
        for field, value in request.items():
            setattr(args, "cleanup_repair_" + ("evidence" if field == "inspected_evidence" else field), value)

    original_get, original_list = secondary.get_custom_resource_strict, secondary.list_custom_resources_strict
    cleanup_reads = iter([_live(), ABSENT, ABSENT] if failure == "poll-timeout" else [])

    def read(**kwargs):
        if kwargs["plural"] == "restores":
            return next(cleanup_reads, ABSENT)
        return original_get(**kwargs)

    def inventory(**kwargs):
        return controller if kwargs["plural"] == "multiclusterhubs" else original_list(**kwargs)

    monkeypatch.setattr(secondary, "get_custom_resource_strict", read)
    monkeypatch.setattr(secondary, "list_custom_resources_strict", inventory)
    monkeypatch.setattr(secondary, "delete_restore_guarded", lambda *_a, **_k: _accepted())
    if backup != "no-schedule":
        st.schedules = [
            {"metadata": {"name": "retry-schedule"}, "spec": {"paused": False, "veleroSchedule": "*/5 * * * *"}}
        ]
    if backup == "fresh":
        now = proof_module.datetime.now(timezone.utc).isoformat()
        for obj in (st.effects[0]["items"][0], st.objects[0]):
            obj["metadata"]["creationTimestamp"] = now
            obj["status"]["completionTimestamp"] = now
    monkeypatch.setattr(cli, "parse_args", lambda: args)
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    after = json.loads(path.read_bytes())
    # This is the first assertion that fails under the old phase-only guard.
    assert after["config"]["migration_backups"]["cleanup"]["state"] == (
        "completed" if failure == "poll-timeout" else "repaired"
    )
    assert "missing_enable_boundary" not in caplog.text
    assert stopped.value.code == (0 if backup == "fresh" else 1)
    assert (after["current_phase"] == "completed") == (backup == "fresh")
    assert after["hub_identities"] == initial["hub_identities"]
    if backup == "fresh":
        assert any(request[0] == "GET" for request in st.requests)
        assert after["config"]["post_switchover_backup_name"] == st.objects[0]["metadata"]["name"]
    else:
        assert "post_switchover_backup_name" not in after["config"]


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize(
    "history", ["boundary", "name", "detected", "enable", "verify", "collision", "malformed-journal"]
)
def test_unfinished_cleanup_does_not_override_present_or_contradictory_history(attempt, restore, history, monkeypatch):
    args, path, raw, primary, secondary, pt, st, logger = attempt(restore=restore, label="none", good=True)
    initial = json.loads(raw)
    for key in ("backup_schedule_enabled_at", "post_switchover_backup_name", "new_backup_detected"):
        initial["config"].pop(key, None)
    initial["completed_steps"] = []
    initial["config"]["migration_backups"] = copy.deepcopy(CLEANUP_INTENT)
    if history in ("boundary", "name", "detected"):
        key = {
            "boundary": "backup_schedule_enabled_at",
            "name": "post_switchover_backup_name",
            "detected": "new_backup_detected",
        }[history]
        initial["config"][key] = None if history != "detected" else False
    elif history == "malformed-journal":
        initial["config"]["migration_backups"]["cleanup"]["uid"] = "another-owner"
    else:
        name = {
            "enable": "enable_backup_schedule",
            "verify": "verify_new_backups",
            "collision": "fix_backup_collision",
        }[history]
        initial["completed_steps"] = [{"name": name, "timestamp": "invalid"}]
    path.write_text(json.dumps(initial))
    monkeypatch.setattr(cli, "parse_args", lambda: args)
    with patch.object(finalization_module.Finalization, "_run_restore_cleanup_transaction") as cleanup:
        with pytest.raises(SystemExit) as stopped:
            cli.main()
    assert stopped.value.code == 1
    cleanup.assert_not_called()
    after = json.loads(path.read_bytes())
    assert after["config"]["migration_backups"] == initial["config"]["migration_backups"]
    assert after["hub_identities"] == initial["hub_identities"]
