"""Actual historical disk reload through CLI preparation, runners and outcomes.

Fixtures were generated with c197's actual LIST404 fault and real StepContext,
then consumed by059's real CLI adapter/runner. See the blob/hash/trace manifest.
Only API transport and the observation clock are replaced here.
"""

import copy
import hashlib
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from kubernetes import client as k8s

import acm_switchover as cli
import lib.utils as state_module
import modules.backup_continuity as proof_module
import modules.finalization as finalization_module
from lib.cli_outcomes import run_operation_mode, run_operation_outcome
from lib.kube_client import KubeClient
from lib.run_record import RunRecord
from lib.utils import Phase, StateManager
from tests.test_finalization_backup_strict import BackupTransport, Clock, page

HISTORY = Path(__file__).parent / "fixtures/backup_continuity_history"
MANIFEST = json.loads((HISTORY / "manifest.json").read_text())


class LifecycleTransport(BackupTransport):
    def __init__(self, role, backup):
        super().__init__([page([backup])], [backup])
        self.role = role
        self.uid = "cluster-uid-" + role
        self.schedules = []

    def read_namespace(self, name, **kwargs):
        assert name == "kube-system"
        self.requests.append(("physical_uid", self.role, self.uid))
        return k8s.V1Namespace(metadata=k8s.V1ObjectMeta(name=name, uid=self.uid))

    def get_cluster_custom_object(self, **kwargs):
        from kubernetes.client.rest import ApiException

        assert kwargs["group"] == "apiextensions.k8s.io" and kwargs["plural"] == "customresourcedefinitions"
        self.requests.append(("argocd_crd", kwargs["name"]))
        raise ApiException(status=404)

    def list_namespaced_custom_object(self, **kwargs):
        if kwargs["plural"] in ("backupschedules", "restores"):
            self.requests.append(("resource_list", kwargs["plural"]))
            return page(self.schedules if kwargs["plural"] == "backupschedules" else [])
        return super().list_namespaced_custom_object(**kwargs)

    def get_namespaced_custom_object(self, **kwargs):
        if kwargs["plural"] == "backupschedules":
            from kubernetes.client.rest import ApiException

            self.requests.append(("schedule_get", kwargs["name"]))
            if not self.schedules:
                raise ApiException(status=404)
            return copy.deepcopy(self.schedules[0])
        if kwargs["plural"] == "multiclusterhubs":
            self.requests.append(("mch", kwargs["name"]))
            return {"metadata": {"name": "multiclusterhub"}, "status": {"phase": "Running"}}
        if kwargs["plural"] == "restores":
            from kubernetes.client.rest import ApiException

            self.requests.append(("restore_get", kwargs["name"]))
            raise ApiException(status=404)
        return super().get_namespaced_custom_object(**kwargs)

    def delete_namespaced_custom_object(self, **kwargs):
        assert kwargs["plural"] == "backupschedules"
        self.requests.append(("schedule_delete", kwargs["name"]))
        self.schedules = []
        return {"status": "Success"}

    def create_namespaced_custom_object(self, **kwargs):
        assert kwargs["plural"] == "backupschedules"
        schedule = copy.deepcopy(kwargs["body"])
        schedule["metadata"].update(uid="recreated-schedule-uid", resourceVersion="42")
        self.requests.append(("schedule_create", schedule["metadata"]["name"]))
        self.schedules = [schedule]
        return schedule

    def list_namespaced_pod(self, **kwargs):
        self.requests.append(("pods", kwargs["namespace"]))
        return k8s.V1PodList(items=[])

    def read_namespaced_config_map(self, **kwargs):
        from kubernetes.client.rest import ApiException

        self.requests.append(("configmap", kwargs["name"]))
        raise ApiException(status=404)

    def call_api(self, path, method, **kwargs):
        if path.startswith("/apis/cluster.open-cluster-management.io/"):
            from types import SimpleNamespace

            return SimpleNamespace(
                data=json.dumps(
                    {
                        "kind": "APIResourceList",
                        "groupVersion": "cluster.open-cluster-management.io/v1beta1",
                        "resources": [
                            {
                                "name": "backupschedules",
                                "kind": "BackupSchedule",
                                "namespaced": True,
                            },
                            {"name": "restores", "kind": "Restore", "namespaced": True},
                        ],
                    }
                ).encode()
            )
        return super().call_api(path, method, **kwargs)


def client(transport):
    result = object.__new__(KubeClient)
    result.context = "iv-" + transport.role
    result.request_timeout = 1
    result.dry_run = False
    result.core_v1 = result.custom_api = result._api_client = transport
    return result


@pytest.fixture
def attempt(tmp_path, monkeypatch):
    def build(restore=False, label="completed", good=False):
        name = ("restore-" if restore else "switchover-") + label + ".json"
        raw = (HISTORY / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == MANIFEST["files"][name]
        assert any(r.get("fault") == 404 for r in MANIFEST["traces"][name[:-5]]["secondary"])
        path = tmp_path / name
        path.write_bytes(raw)
        saved = json.loads(raw)
        observed = datetime.fromisoformat(saved["last_updated"].replace("Z", "+00:00")) + timedelta(seconds=1)

        class Observed(datetime):
            @classmethod
            def now(cls, tz=None):
                return observed.astimezone(tz) if tz is not None else observed.replace(tzinfo=None)

        monkeypatch.setattr(state_module, "datetime", Observed)
        monkeypatch.setattr(proof_module, "datetime", Observed)
        monkeypatch.setattr(finalization_module, "datetime", Observed)
        monkeypatch.setattr(proof_module, "time", Clock())
        monkeypatch.setattr(finalization_module, "BACKUP_POLL_INTERVAL", 1)
        backup = next(
            r["body"]["items"][0]
            for r in MANIFEST["traces"][name[:-5]]["secondary"]
            if r["kind"] == "LIST" and "body" in r and r["body"]["items"]
        )
        backup = copy.deepcopy(backup)
        if good:
            timestamp = (observed - timedelta(microseconds=1)).isoformat()
            backup["metadata"].update(creationTimestamp=timestamp, uid="current-qualifying-uid")
            backup["status"].update(completionTimestamp=timestamp, errors=0)
        primary_transport = LifecycleTransport("primary", backup)
        secondary_transport = LifecycleTransport("secondary", backup)
        primary = None if restore else client(primary_transport)
        secondary = client(secondary_transport)
        argv = [
            "acm_switchover.py",
            "--secondary-context",
            "iv-secondary",
            "--state-file",
            str(path),
        ]
        argv += (
            ["--restore-only"]
            if restore
            else [
                "--primary-context",
                "iv-primary",
                "--old-hub-action",
                "none",
                "--method",
                "passive",
            ]
        )
        with patch.object(sys, "argv", argv):
            args = cli.parse_args()
        logger = logging.getLogger("backup-lifecycle")
        monkeypatch.setattr(cli, "_initialize_clients", lambda *a, **k: (primary, secondary))
        monkeypatch.setattr(cli, "setup_logging", lambda *a, **k: logger)
        return (
            args,
            path,
            raw,
            primary,
            secondary,
            primary_transport,
            secondary_transport,
            logger,
        )

    return build


@pytest.mark.parametrize("restore", [False, True])
def test_completed_stale_history_refuses_through_real_outer_main(attempt, restore, monkeypatch, caplog):
    args, path, raw, primary, secondary, pt, st, logger = attempt(restore=restore)
    monkeypatch.setattr(cli, "parse_args", lambda: args)
    with caplog.at_level(logging.INFO), pytest.raises(SystemExit) as stopped:
        cli.main()
    assert any(r[0] == "physical_uid" for r in st.requests), st.requests
    assert stopped.value.code != 0, f"Historical COMPLETED returned success without fresh Backup proof: {st.requests!r}"
    assert path.read_bytes() == raw
    assert "Operation completed successfully" not in caplog.text
    assert any(r[0] == "physical_uid" for r in st.requests)
    assert any(r[0] == "LIST" for r in st.requests), "Completed assessment skipped the actual Backup reader"


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("label", ["none", "continuity", "integrity", "both"])
def test_historical_markers_do_not_bypass_entry_proof(attempt, restore, label, monkeypatch):
    args, path, raw, primary, secondary, pt, st, logger = attempt(restore=restore, label=label)
    runtime = cli._prepare_runtime(args, logger, str(path))
    before = runtime.state.capture_state_snapshot()
    original_cleanup = finalization_module.Finalization._run_restore_cleanup_transaction
    with patch.object(
        finalization_module.Finalization,
        "_run_restore_cleanup_transaction",
        autospec=True,
        side_effect=lambda fin: original_cleanup(fin),
    ) as cleanup:
        result = run_operation_mode(
            args,
            runtime.state,
            primary,
            secondary,
            logger,
            should_bind_state=True,
            should_record_state_errors=True,
            hooks=cli._build_cli_operation_hooks(),
            exit_success=0,
            exit_failure=1,
            exit_interrupt=130,
        )
    assert result == 1
    cleanup.assert_not_called()
    after = runtime.state.capture_state_snapshot()
    assert after["config"]["post_switchover_backup_name"] == before["config"]["post_switchover_backup_name"]
    assert after["config"]["backup_schedule_enabled_at"] == before["config"]["backup_schedule_enabled_at"]
    assert after["config"]["migration_backups"] == before["config"]["migration_backups"]
    assert after["hub_identities"] == before["hub_identities"]
    assert [s for s in after["completed_steps"] if s["name"] in ("verify_new_backups", "verify_backup_integrity")] == [
        s for s in before["completed_steps"] if s["name"] in ("verify_new_backups", "verify_backup_integrity")
    ]


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("good", [False, True])
@pytest.mark.parametrize("route", ["outcome", "mode", "main"])
@pytest.mark.parametrize("flags", [{}, {"force": True}, {"dry_run": True}, {"validate_only": True}])
def test_completed_attempt_is_byte_preserving_and_never_writes(
    attempt, restore, good, route, flags, monkeypatch, caplog
):
    args, path, raw, primary, secondary, pt, st, logger = attempt(restore=restore, good=good)
    for key, value in flags.items():
        setattr(args, key, value)
    mtime = path.stat().st_mtime_ns
    writes = []

    def forbid(*args, **kwargs):
        writes.append((args, kwargs))
        raise AssertionError("Completed checkpoint write attempted")

    for method in (
        "save_state",
        "flush_state",
        "_write_state",
        "_set_config",
        "mark_step_completed",
        "add_error",
        "set_phase",
        "reset",
        "restore_state_snapshot",
    ):
        monkeypatch.setattr(StateManager, method, forbid)
    with caplog.at_level(logging.INFO):
        if route == "main":
            monkeypatch.setattr(cli, "parse_args", lambda: args)
            with pytest.raises(SystemExit) as stopped:
                cli.main()
            result = stopped.value.code
        else:
            runtime = cli._prepare_runtime(args, logger, str(path))
            function = run_operation_outcome if route == "outcome" else run_operation_mode
            returned = function(
                args,
                runtime.state,
                primary,
                secondary,
                logger,
                should_bind_state=True,
                should_record_state_errors=True,
                hooks=cli._build_cli_operation_hooks(),
                exit_success=0,
                exit_failure=1,
                exit_interrupt=130,
            )
            result = returned.exit_code if route == "outcome" else returned
            assert runtime.state.capture_state_snapshot() == json.loads(raw)
    assert writes == []
    assert result == (0 if good else 1)
    assert path.read_bytes() == raw and path.stat().st_mtime_ns == mtime
    if not good:
        assert "Operation completed successfully" not in caplog.text
    else:
        assert ("RESTORE ALREADY COMPLETED" if restore else "SWITCHOVER ALREADY COMPLETED") in caplog.text
        assert "No phases were executed on this run" in caplog.text


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize(
    "flags",
    [
        {},
        {"force": True},
        {"dry_run": True},
        {"validate_only": True},
        {"force": True, "dry_run": True},
    ],
)
@pytest.mark.parametrize(
    "fault",
    [
        "contexts_missing",
        "contexts_wrong",
        "identities_missing",
        "identity_wrong",
        "identity_incomplete",
        "same_hubs",
        "bad_boundary",
        "marker_no_name",
        "corrupt",
    ],
)
def test_completed_bootstrap_and_assessment_refuse_without_writes(attempt, restore, flags, fault, monkeypatch, caplog):
    args, path, _, primary, secondary, pt, st, logger = attempt(restore=restore, good=True)
    state = json.loads(path.read_text())
    if fault == "contexts_missing":
        state.pop("contexts")
    elif fault == "contexts_wrong":
        state["contexts"]["secondary"] = "other-context"
    elif fault == "identities_missing":
        state.pop("hub_identities")
    elif fault == "identity_wrong":
        state["hub_identities"]["secondary"]["cluster_uid"] = "other-uid"
    elif fault == "identity_incomplete":
        state["hub_identities"]["secondary"].pop("cluster_uid")
    elif fault == "same_hubs":
        if restore:
            st.uid = ""
        else:
            pt.uid = st.uid
    elif fault == "bad_boundary":
        state["config"]["backup_schedule_enabled_at"] = None
    elif fault == "marker_no_name":
        state["config"].pop("post_switchover_backup_name")
    path.write_text("{broken" if fault == "corrupt" else json.dumps(state))
    raw = path.read_bytes()
    mtime = path.stat().st_mtime_ns
    for key, value in flags.items():
        setattr(args, key, value)
    writes = []

    def forbid(*a, **k):
        writes.append((a, k))
        raise AssertionError("Completed mutation attempted")

    for method in (
        "save_state",
        "flush_state",
        "_write_state",
        "_set_config",
        "mark_step_completed",
        "add_error",
        "set_phase",
        "reset",
        "restore_state_snapshot",
    ):
        monkeypatch.setattr(StateManager, method, forbid)
    for method in (
        "patch_custom_resource",
        "create_custom_resource",
        "delete_custom_resource",
        "delete_restore_guarded",
        "create_restore_guarded",
        "patch_managed_cluster",
        "delete_configmap",
        "create_or_patch_configmap",
    ):
        monkeypatch.setattr(KubeClient, method, forbid)
    monkeypatch.setattr(cli, "parse_args", lambda: args)
    with caplog.at_level(logging.INFO), pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 1
    assert not writes
    assert path.read_bytes() == raw and path.stat().st_mtime_ns == mtime
    assert "Operation completed successfully" not in caplog.text


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("fault", [RuntimeError("unexpected after real proof"), KeyboardInterrupt()])
def test_completed_outer_exception_after_actual_proof_is_read_only(attempt, restore, fault, monkeypatch):
    args, path, raw, primary, secondary, pt, st, logger = attempt(restore=restore, good=True)
    args.dry_run = True
    original = cli._verify_backup_continuity_entry

    def prove_then_fail(*a, **k):
        assert original(*a, **k) is True
        assert any(r[0] == "GET" for r in st.requests)
        raise fault

    monkeypatch.setattr(cli, "_verify_backup_continuity_entry", prove_then_fail)

    def forbid(*a, **k):
        raise AssertionError("Completed write attempted")

    for method in (
        "save_state",
        "flush_state",
        "_write_state",
        "_set_config",
        "mark_step_completed",
        "add_error",
        "set_phase",
        "reset",
        "restore_state_snapshot",
    ):
        monkeypatch.setattr(StateManager, method, forbid)
    monkeypatch.setattr(cli, "parse_args", lambda: args)
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == (130 if isinstance(fault, KeyboardInterrupt) else 1)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("restore", [False, True])
def test_failed_finalization_retry_repauses_before_history_proof(attempt, restore, monkeypatch):
    args, path, _, primary, secondary, pt, st, logger = attempt(restore=restore, label="both")
    raw = json.loads(path.read_text())
    raw["current_phase"] = "failed"
    raw["errors"] = [{"phase": "finalization", "error": "fixture interruption"}]
    path.write_text(json.dumps(raw))
    runtime = cli._prepare_runtime(args, logger, str(path))
    events = []
    original = cli._verify_backup_continuity_entry

    def proof(*a, **k):
        events.append("proof")
        return original(*a, **k)

    def repause(*a, **k):
        events.append("repause")
        return True

    monkeypatch.setattr(cli, "_verify_backup_continuity_entry", proof)
    monkeypatch.setattr(cli, "_repause_argocd_after_rescue", repause)
    with patch.object(finalization_module.Finalization, "_run_restore_cleanup_transaction") as cleanup:
        result = run_operation_mode(
            args,
            runtime.state,
            primary,
            secondary,
            logger,
            should_bind_state=True,
            should_record_state_errors=True,
            hooks=cli._build_cli_operation_hooks(),
            exit_success=0,
            exit_failure=1,
            exit_interrupt=130,
        )
    assert result == 1 and events == ["repause", "proof"]
    cleanup.assert_not_called()
    assert runtime.state.state["config"]["backup_schedule_enabled_at"] == raw["config"]["backup_schedule_enabled_at"]
    assert runtime.state.state["config"]["post_switchover_backup_name"] == raw["config"]["post_switchover_backup_name"]


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("label", ["none", "continuity", "integrity", "both"])
def test_good_current_backup_all_historical_marker_retries_complete(attempt, restore, label):
    args, path, raw, primary, secondary, pt, st, logger = attempt(restore=restore, label=label, good=True)
    runtime = cli._prepare_runtime(args, logger, str(path))
    before = runtime.state.capture_state_snapshot()
    result = run_operation_mode(
        args,
        runtime.state,
        primary,
        secondary,
        logger,
        should_bind_state=True,
        should_record_state_errors=True,
        hooks=cli._build_cli_operation_hooks(),
        exit_success=0,
        exit_failure=1,
        exit_interrupt=130,
    )
    assert result == 0
    after = json.loads(path.read_bytes())
    assert after["current_phase"] == "completed"
    for key in ("backup_schedule_enabled_at", "post_switchover_backup_name", "migration_backups"):
        assert after["config"][key] == before["config"][key]
    assert after["hub_identities"] == before["hub_identities"]
    assert any(r[0] == "GET" for r in st.requests)


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("later_valid", [False, True])
def test_disk_retry_after_real_finalization_return_reproves_before_completion(
    attempt, restore, later_valid, monkeypatch
):
    import lib.operation_runners as runners

    args, path, raw, primary, secondary, pt, st, logger = attempt(restore=restore, label="none", good=True)
    runtime = cli._prepare_runtime(args, logger, str(path))
    original = runners.log_operation_completion

    def crash(*a, **k):
        assert runtime.state.get_current_phase() == Phase.FINALIZATION
        assert runtime.state.is_step_completed("verify_new_backups")
        assert runtime.state.is_step_completed("verify_backup_integrity")
        raise KeyboardInterrupt()

    monkeypatch.setattr(runners, "log_operation_completion", crash)
    code = run_operation_mode(
        args,
        runtime.state,
        primary,
        secondary,
        logger,
        should_bind_state=True,
        should_record_state_errors=True,
        hooks=cli._build_cli_operation_hooks(),
        exit_success=0,
        exit_failure=1,
        exit_interrupt=130,
    )
    assert code == 130
    disk = json.loads(path.read_bytes())
    assert disk["current_phase"] == "finalization"
    previous_reads = len(st.requests)
    if not later_valid:
        stale = copy.deepcopy(st.objects[0])
        stale["metadata"]["creationTimestamp"] = "2020-01-01T00:00:00Z"
        st.effects = [page([stale])]
        st.objects = [stale]
    monkeypatch.setattr(runners, "log_operation_completion", original)
    reloaded = StateManager(str(path))
    code = run_operation_mode(
        args,
        reloaded,
        primary,
        secondary,
        logger,
        should_bind_state=True,
        should_record_state_errors=True,
        hooks=cli._build_cli_operation_hooks(),
        exit_success=0,
        exit_failure=1,
        exit_interrupt=130,
    )
    assert code == (0 if later_valid else 1)
    assert len(st.requests) > previous_reads
    after = json.loads(path.read_bytes())
    assert (after["current_phase"] == "completed") is later_valid
    for key in ("backup_schedule_enabled_at", "post_switchover_backup_name", "migration_backups"):
        assert after["config"][key] == disk["config"][key]


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("label", ["continuity", "integrity", "both"])
def test_active_marker_without_recorded_name_refuses_before_real_cleanup(attempt, restore, label):
    args, path, _, primary, secondary, pt, st, logger = attempt(restore=restore, label=label, good=True)
    saved = json.loads(path.read_bytes())
    saved["config"].pop("post_switchover_backup_name")
    path.write_text(json.dumps(saved))
    runtime = cli._prepare_runtime(args, logger, str(path))
    assert "post_switchover_backup_name" not in runtime.state.capture_state_snapshot()["config"]
    with patch.object(finalization_module.Finalization, "_run_restore_cleanup_transaction") as cleanup:
        code = run_operation_mode(
            args,
            runtime.state,
            primary,
            secondary,
            logger,
            should_bind_state=True,
            should_record_state_errors=True,
            hooks=cli._build_cli_operation_hooks(),
            exit_success=0,
            exit_failure=1,
            exit_interrupt=130,
        )
    assert code == 1
    cleanup.assert_not_called()
    after = json.loads(path.read_bytes())
    assert "post_switchover_backup_name" not in after["config"]
    assert after["completed_steps"] == saved["completed_steps"]
    assert after["config"]["migration_backups"] == saved["config"]["migration_backups"]


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("boundary", ["absent", None, False, 0, "", "invalid", "2024-01-01T00:00:00"])
def test_completed_raw_boundary_through_actual_loader_is_absent_only_fallback(attempt, restore, boundary, monkeypatch):
    args, path, _, primary, secondary, pt, st, logger = attempt(restore=restore, good=True)
    saved = json.loads(path.read_bytes())
    if boundary == "absent":
        saved["config"].pop("backup_schedule_enabled_at")
    else:
        saved["config"]["backup_schedule_enabled_at"] = boundary
    path.write_text(json.dumps(saved))
    before = path.read_bytes()
    mtime = path.stat().st_mtime_ns

    def forbid(*a, **k):
        raise AssertionError("Completed state write attempted")

    for method in (
        "save_state",
        "flush_state",
        "_write_state",
        "_set_config",
        "mark_step_completed",
        "add_error",
        "set_phase",
        "reset",
        "restore_state_snapshot",
    ):
        monkeypatch.setattr(StateManager, method, forbid)
    monkeypatch.setattr(cli, "parse_args", lambda: args)
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == (0 if boundary == "absent" else 1)
    assert path.read_bytes() == before and path.stat().st_mtime_ns == mtime


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("label", ["none", "continuity", "integrity", "both"])
def test_real_failed_retry_disposes_repause_obligation_before_backup_proof(attempt, restore, label, monkeypatch):
    args, path, _, primary, secondary, pt, st, logger = attempt(restore=restore, label=label)
    raw = json.loads(path.read_bytes())
    raw["current_phase"] = "failed"
    raw["errors"] = [{"phase": "finalization", "error": "fixture interruption"}]
    path.write_text(json.dumps(raw))
    runtime = cli._prepare_runtime(args, logger, str(path))
    record = RunRecord(runtime.state)
    record.record_argocd_repause_required("finalization", raw["last_updated"])
    original = cli._verify_backup_continuity_entry

    def prove(*a, **k):
        assert record.argocd_repause_required() is None
        assert len([r for r in st.requests if r[0] == "argocd_crd"]) == 2
        if not restore:
            assert len([r for r in pt.requests if r[0] == "argocd_crd"]) == 2
        return original(*a, **k)

    monkeypatch.setattr(cli, "_verify_backup_continuity_entry", prove)
    with patch.object(finalization_module.Finalization, "_run_restore_cleanup_transaction") as cleanup:
        code = run_operation_mode(
            args,
            runtime.state,
            primary,
            secondary,
            logger,
            should_bind_state=True,
            should_record_state_errors=True,
            hooks=cli._build_cli_operation_hooks(),
            exit_success=0,
            exit_failure=1,
            exit_interrupt=130,
        )
    assert code == 1
    cleanup.assert_not_called()
    assert any(r[0] == "LIST" for r in st.requests)
    for key in ("backup_schedule_enabled_at", "post_switchover_backup_name", "migration_backups"):
        assert runtime.state.state["config"][key] == raw["config"][key]


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("phase", ["finalization", "failed"])
@pytest.mark.parametrize("route", ["mode", "main"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_historical_finalization_without_any_boundary_or_marker_refuses_before_cleanup(
    attempt, restore, phase, route, dry_run, monkeypatch, caplog
):
    args, path, raw, primary, secondary, pt, st, logger = attempt(restore=restore, label="none", good=True)
    args.dry_run = dry_run
    saved = json.loads(raw)
    saved["current_phase"] = phase
    if phase == "failed":
        saved["errors"] = [{"phase": "finalization", "error": "fixture interruption before any proof was retained"}]
    for key in ("backup_schedule_enabled_at", "post_switchover_backup_name", "new_backup_detected"):
        saved["config"].pop(key, None)
    saved["completed_steps"] = [
        step
        for step in saved["completed_steps"]
        if step["name"] not in ("enable_backup_schedule", "verify_new_backups", "verify_backup_integrity")
    ]
    path.write_text(json.dumps(saved))
    original = path.read_bytes()
    runtime = cli._prepare_runtime(args, logger, str(path))
    inputs = RunRecord(runtime.state).backup_continuity_inputs()
    assert not inputs["enabled_at_present"] and not inputs["recorded_name_present"]
    assert inputs["legacy_enable_steps"] == inputs["verification_steps"] == []
    original_cleanup = finalization_module.Finalization._run_restore_cleanup_transaction
    original_write = StateManager._write_state
    with caplog.at_level(logging.INFO), patch.object(
        StateManager,
        "_write_state",
        autospec=True,
        side_effect=lambda state, data: original_write(state, data),
    ) as writes, patch.object(
        finalization_module.Finalization,
        "_run_restore_cleanup_transaction",
        autospec=True,
        side_effect=lambda fin: original_cleanup(fin),
    ) as cleanup:
        if route == "main":
            monkeypatch.setattr(cli, "parse_args", lambda: args)
            with pytest.raises(SystemExit) as stopped:
                cli.main()
            code = stopped.value.code
        else:
            code = run_operation_mode(
                args,
                runtime.state,
                primary,
                secondary,
                logger,
                should_bind_state=True,
                should_record_state_errors=True,
                hooks=cli._build_cli_operation_hooks(),
                exit_success=0,
                exit_failure=1,
                exit_interrupt=130,
            )
    cleanup.assert_not_called()
    if dry_run:
        writes.assert_not_called()
        assert path.read_bytes() == original
    assert code == 1 and "missing_enable_boundary" in caplog.text
    after = json.loads(path.read_bytes())
    assert "backup_schedule_enabled_at" not in after["config"]
    assert "post_switchover_backup_name" not in after["config"]
    assert after["config"]["migration_backups"] == saved["config"]["migration_backups"]
    assert after["hub_identities"] == saved["hub_identities"]
    assert after["completed_steps"] == saved["completed_steps"]
    assert not any(r[0] == "restore_get" or r == ("resource_list", "restores") for r in st.requests), st.requests


@pytest.mark.parametrize(
    "restore,dry_run,no_schedule",
    [(False, False, False), (True, False, False), (False, True, False), (True, True, False), (True, False, True)],
)
def test_fresh_predecessor_without_backup_history_runs_actual_cli(attempt, restore, dry_run, no_schedule, monkeypatch):
    args, path, raw, primary, secondary, pt, st, logger = attempt(restore=restore, label="none", good=True)
    saved = json.loads(raw)
    saved["current_phase"] = Phase.POST_ACTIVATION.value
    for key in ("backup_schedule_enabled_at", "post_switchover_backup_name", "new_backup_detected"):
        saved["config"].pop(key, None)
    saved["completed_steps"] = []
    path.write_text(json.dumps(saved))
    args.dry_run = dry_run
    if not no_schedule:
        st.schedules = [
            {
                "metadata": {"name": "fixture-schedule", "namespace": "open-cluster-management-backup"},
                "spec": {"paused": False, "veleroSchedule": "*/5 * * * *"},
            }
        ]
    current = proof_module.datetime.now(timezone.utc).isoformat()
    for backup in [st.effects[0]["items"][0], st.objects[0]]:
        backup["metadata"]["creationTimestamp"] = current
        backup["status"]["completionTimestamp"] = current

    def completed_predecessor(args, state, primary, secondary, logger):
        from lib.migration_post_activation import require_post_activation_completed

        require_post_activation_completed(RunRecord(state).migration_backups())
        return True

    monkeypatch.setattr(cli, "_run_phase_post_activation", completed_predecessor)
    monkeypatch.setattr(cli, "parse_args", lambda: args)
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 0
    assert ("resource_list", "restores") in st.requests
    after = json.loads(path.read_bytes())
    if dry_run:
        assert after == saved
    else:
        assert after["current_phase"] == "completed"
        assert after["config"]["backup_schedule_enabled_at"] == current
        assert after["config"]["migration_backups"] == saved["config"]["migration_backups"]
        assert after["hub_identities"] == saved["hub_identities"]
        assert any(r[0] == "GET" for r in st.requests) == (not no_schedule)


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("crash_at", ["name", "verify_new_backups", "verify_backup_integrity"])
@pytest.mark.parametrize("later_valid", [False, True])
def test_disk_interrupt_after_each_backup_save_requires_fresh_retry_proof(
    attempt, restore, crash_at, later_valid, monkeypatch
):
    args, path, raw, primary, secondary, pt, st, logger = attempt(restore=restore, label="none", good=True)
    # Explicit non-live input with a retained enable boundary and no selected name.
    # The real current finalizer must select and durably record the current Backup.
    initial = json.loads(raw)
    initial["config"].pop("post_switchover_backup_name")
    st.schedules = [
        {
            "metadata": {"name": "fixture-schedule", "namespace": "open-cluster-management-backup"},
            "spec": {"paused": False, "veleroSchedule": "*/5 * * * *"},
        }
    ]
    path.write_text(json.dumps(initial))
    runtime = cli._prepare_runtime(args, logger, str(path))
    original_save = StateManager.save_state
    interrupted = []

    def crash_after_actual_save(state, *a, **k):
        result = original_save(state, *a, **k)
        persisted = json.loads(path.read_bytes())
        markers = {step["name"] for step in persisted["completed_steps"]}
        reached = "post_switchover_backup_name" in persisted["config"] if crash_at == "name" else crash_at in markers
        if reached and not interrupted:
            interrupted.append(persisted)
            raise KeyboardInterrupt()
        return result

    monkeypatch.setattr(StateManager, "save_state", crash_after_actual_save)
    options = dict(
        should_bind_state=True,
        should_record_state_errors=True,
        hooks=cli._build_cli_operation_hooks(),
        exit_success=0,
        exit_failure=1,
        exit_interrupt=130,
    )
    assert run_operation_mode(args, runtime.state, primary, secondary, logger, **options) == 130
    assert len(interrupted) == 1
    disk = json.loads(path.read_bytes())
    assert disk["current_phase"] == "finalization"
    assert disk["config"]["post_switchover_backup_name"] == st.objects[0]["metadata"]["name"]
    if crash_at != "name":
        assert crash_at in {step["name"] for step in disk["completed_steps"]}
    monkeypatch.setattr(StateManager, "save_state", original_save)
    previous_reads = len(st.requests)
    if not later_valid:
        stale = copy.deepcopy(st.objects[0])
        stale["metadata"]["creationTimestamp"] = "2020-01-01T00:00:00Z"
        st.effects, st.objects = [page([stale])], [stale]
    reloaded = StateManager(str(path))
    assert run_operation_mode(args, reloaded, primary, secondary, logger, **options) == (0 if later_valid else 1)
    assert len(st.requests) > previous_reads
    after = json.loads(path.read_bytes())
    assert (after["current_phase"] == "completed") is later_valid
    for key in ("backup_schedule_enabled_at", "post_switchover_backup_name", "migration_backups"):
        assert after["config"][key] == disk["config"][key]
    assert after["hub_identities"] == disk["hub_identities"]
    assert all(marker in after["completed_steps"] for marker in disk["completed_steps"])
