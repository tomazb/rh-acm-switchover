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

    def read_namespace(self, name, **kwargs):
        assert name == "kube-system"
        self.requests.append(("physical_uid", self.role, self.uid))
        return k8s.V1Namespace(metadata=k8s.V1ObjectMeta(name=name, uid=self.uid))

    def list_namespaced_custom_object(self, **kwargs):
        if kwargs["plural"] == "backupschedules":
            return page([])
        return super().list_namespaced_custom_object(**kwargs)

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
                            }
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
        assert any(
            r.get("fault") == 404 for r in MANIFEST["traces"][name[:-5]]["secondary"]
        )
        path = tmp_path / name
        path.write_bytes(raw)
        saved = json.loads(raw)
        observed = datetime.fromisoformat(
            saved["last_updated"].replace("Z", "+00:00")
        ) + timedelta(seconds=1)

        class Observed(datetime):
            @classmethod
            def now(cls, tz=None):
                return (
                    observed.astimezone(tz)
                    if tz is not None
                    else observed.replace(tzinfo=None)
                )

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
            backup["metadata"].update(
                creationTimestamp=timestamp, uid="current-qualifying-uid"
            )
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
        monkeypatch.setattr(
            cli, "_initialize_clients", lambda *a, **k: (primary, secondary)
        )
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
def test_completed_stale_history_refuses_through_real_outer_main(
    attempt, restore, monkeypatch, caplog
):
    args, path, raw, primary, secondary, pt, st, logger = attempt(restore=restore)
    monkeypatch.setattr(cli, "parse_args", lambda: args)
    with caplog.at_level(logging.INFO), pytest.raises(SystemExit) as stopped:
        cli.main()
    assert any(r[0] == "physical_uid" for r in st.requests), st.requests
    assert (
        stopped.value.code != 0
    ), f"Historical COMPLETED returned success without fresh Backup proof: {st.requests!r}"
    assert path.read_bytes() == raw
    assert "Operation completed successfully" not in caplog.text
    assert any(r[0] == "physical_uid" for r in st.requests)
    assert any(
        r[0] == "LIST" for r in st.requests
    ), "Completed assessment skipped the actual Backup reader"


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("label", ["none", "continuity", "integrity", "both"])
def test_historical_markers_do_not_bypass_entry_proof(
    attempt, restore, label, monkeypatch
):
    args, path, raw, primary, secondary, pt, st, logger = attempt(
        restore=restore, label=label
    )
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
    assert (
        after["config"]["post_switchover_backup_name"]
        == before["config"]["post_switchover_backup_name"]
    )
    assert (
        after["config"]["backup_schedule_enabled_at"]
        == before["config"]["backup_schedule_enabled_at"]
    )
    assert after["config"]["migration_backups"] == before["config"]["migration_backups"]
    assert after["hub_identities"] == before["hub_identities"]
    assert [
        s
        for s in after["completed_steps"]
        if s["name"] in ("verify_new_backups", "verify_backup_integrity")
    ] == [
        s
        for s in before["completed_steps"]
        if s["name"] in ("verify_new_backups", "verify_backup_integrity")
    ]


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("good", [False, True])
@pytest.mark.parametrize("route", ["outcome", "mode", "main"])
@pytest.mark.parametrize(
    "flags", [{}, {"force": True}, {"dry_run": True}, {"validate_only": True}]
)
def test_completed_attempt_is_byte_preserving_and_never_writes(
    attempt, restore, good, route, flags, monkeypatch, caplog
):
    args, path, raw, primary, secondary, pt, st, logger = attempt(
        restore=restore, good=good
    )
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
            function = (
                run_operation_outcome if route == "outcome" else run_operation_mode
            )
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
        assert (
            "RESTORE ALREADY COMPLETED" if restore else "SWITCHOVER ALREADY COMPLETED"
        ) in caplog.text
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
def test_completed_bootstrap_and_assessment_refuse_without_writes(
    attempt, restore, flags, fault, monkeypatch, caplog
):
    args, path, _, primary, secondary, pt, st, logger = attempt(
        restore=restore, good=True
    )
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
@pytest.mark.parametrize(
    "fault", [RuntimeError("unexpected after real proof"), KeyboardInterrupt()]
)
def test_completed_outer_exception_after_actual_proof_is_read_only(
    attempt, restore, fault, monkeypatch
):
    args, path, raw, primary, secondary, pt, st, logger = attempt(
        restore=restore, good=True
    )
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
def test_failed_finalization_retry_repauses_before_history_proof(
    attempt, restore, monkeypatch
):
    args, path, _, primary, secondary, pt, st, logger = attempt(
        restore=restore, label="both"
    )
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
    with patch.object(
        finalization_module.Finalization, "_run_restore_cleanup_transaction"
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
    assert result == 1 and events == ["repause", "proof"]
    cleanup.assert_not_called()
    assert (
        runtime.state.state["config"]["backup_schedule_enabled_at"]
        == raw["config"]["backup_schedule_enabled_at"]
    )
    assert (
        runtime.state.state["config"]["post_switchover_backup_name"]
        == raw["config"]["post_switchover_backup_name"]
    )
