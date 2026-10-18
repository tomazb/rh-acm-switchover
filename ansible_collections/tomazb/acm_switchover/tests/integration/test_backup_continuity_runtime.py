"""Real shipped completed entrypoints and module against local Kubernetes HTTP.

Checkpoint fixtures supply unrelated completed migration/phase prerequisites. API reads,
Ansible action/module dispatch and checkpoint loading are real; no live certification.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.tests.conftest import _ansible_env
from ansible_collections.tomazb.acm_switchover.tests.integration.argocd_fake_api import (
    FakeArgoCDHub,
    application,
    write_kubeconfig,
)
from ansible_collections.tomazb.acm_switchover.tests.integration.migration_fake_api import FakeMigrationHub

ROOT = Path(__file__).resolve().parents[5]
COLLECTION = ROOT / "ansible_collections/tomazb/acm_switchover"
BACKUPS = "/apis/velero.io/v1/namespaces/open-cluster-management-backup/backups"


class BackupLifecycleHub(FakeMigrationHub):
    """Supply unrelated healthy MCH/Pod prerequisites for full-role controls."""

    def _discovery(self):
        result = super()._discovery()
        result["/api/v1"]["resources"].append(
            {"name": "pods", "kind": "Pod", "namespaced": True, "verbs": ["get", "list"]}
        )
        return result

    def _list_payload(self, path):
        if path == BACKUPS and getattr(self, "complete_backup_after_enable", False) and self.schedules:
            for backup in self.backups.values():
                backup["metadata"]["creationTimestamp"] = datetime.now(timezone.utc).isoformat()
                backup["status"] = {"phase": "Completed", "errors": 0}
            self.complete_backup_after_enable = False
        if path == "/api/v1/namespaces/open-cluster-management/pods":
            return {
                "apiVersion": "v1",
                "kind": "PodList",
                "metadata": {"resourceVersion": "10"},
                "items": [
                    {
                        "apiVersion": "v1",
                        "kind": "Pod",
                        "metadata": {"name": "fixture-acm", "uid": "fixture-pod", "resourceVersion": "10"},
                        "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]},
                    }
                ],
            }
        result = super()._list_payload(path)
        if result and result.get("kind") == "MultiClusterHubList":
            for item in result["items"]:
                item["status"]["phase"] = "Running"
        return result


class BackupRetryHub(FakeArgoCDHub):
    """Real Application PATCHes and strict Backup reads on the same physical hub."""

    def __init__(self, *, cluster_uid, backups=None):
        self.backups = copy.deepcopy(backups or [])
        super().__init__(cluster_uid=cluster_uid, applications=[application("argocd", "acm-owned", automated=True)])

    @property
    def writes(self):
        return [r for r in self.requests if r["method"] in {"POST", "PATCH", "PUT", "DELETE"}]

    def _handler(self):
        base = super()._handler()
        hub = self

        # Existing fake transport constructs its request-handler type at runtime.
        class Handler(base):  # type: ignore[misc,valid-type]
            def _get_payload(self, path):
                if path == "/apis":
                    payload = super()._get_payload(path)
                    payload["groups"].append(
                        {
                            "name": "velero.io",
                            "versions": [{"groupVersion": "velero.io/v1", "version": "v1"}],
                            "preferredVersion": {"groupVersion": "velero.io/v1", "version": "v1"},
                        }
                    )
                    return payload
                if path == "/apis/velero.io/v1":
                    return {
                        "kind": "APIResourceList",
                        "groupVersion": "velero.io/v1",
                        "resources": [
                            {"name": "backups", "kind": "Backup", "namespaced": True, "verbs": ["get", "list"]}
                        ],
                    }
                if path == BACKUPS:
                    return {
                        "apiVersion": "velero.io/v1",
                        "kind": "BackupList",
                        "metadata": {"resourceVersion": "42"},
                        "items": copy.deepcopy(hub.backups),
                    }
                if path.startswith(BACKUPS + "/"):
                    return next(
                        (copy.deepcopy(b) for b in hub.backups if b["metadata"]["name"] == path.rsplit("/", 1)[1]), None
                    )
                return super()._get_payload(path)

        return Handler


@pytest.fixture
def run_backup_entry(tmp_path):
    hubs = []

    def run(
        *,
        route="role",
        restore=False,
        good=False,
        reset=None,
        wrong_identity=False,
        boundary="valid",
        mode="execute",
        check=False,
        extra=None,
        completed=True,
        state_edit=None,
        trace=False,
        interrupt=False,
        old_action=None,
        actual_argocd=False,
        complete_after_enable=False,
    ):
        now = datetime.now(timezone.utc)
        enabled = (now - timedelta(minutes=2)).isoformat()
        created = (now - timedelta(minutes=1 if good else 10)).isoformat()
        backup: dict = {
            "apiVersion": "velero.io/v1",
            "kind": "Backup",
            "metadata": {
                "name": "acm-managed-clusters-current",
                "uid": "backup-uid",
                "resourceVersion": "12",
                "creationTimestamp": created,
            },
            "status": {"phase": "Completed", "errors": 0},
        }
        if not good:
            backup["metadata"].pop("creationTimestamp")
        hub_class = BackupRetryHub if actual_argocd else BackupLifecycleHub
        primary = hub_class(cluster_uid="uid-primary")
        secondary = hub_class(cluster_uid="uid-secondary", backups=[backup])
        if complete_after_enable:
            assert isinstance(secondary, BackupLifecycleHub)
            secondary.complete_backup_after_enable = True
        hubs.extend([primary, secondary])
        contexts = {"primary": "primary-hub", "secondary": "secondary-hub"}
        config = {}
        for role, hub in [("primary", primary), ("secondary", secondary)]:
            path = tmp_path / (role + ".kubeconfig")
            write_kubeconfig(path, context=contexts[role], server=hub.url, token="fixture-token")
            config[role] = {"context": contexts[role], "kubeconfig": str(path)}
        if restore:
            config.pop("primary")
        operation = {
            "restore_only": restore,
            "method": "full" if restore else "passive",
            "activation_method": "patch",
            "old_hub_action": "none" if restore else (old_action or "secondary"),
        }
        identity = {
            "primary_context": "" if restore else "primary-hub",
            "secondary_context": "secondary-hub",
            "primary_cluster_uid": "" if restore else "uid-primary",
            "secondary_cluster_uid": "wrong-uid" if wrong_identity else "uid-secondary",
            **operation,
            "collection_version": "",
        }
        # Exact existing format, without any new proof/cache key.
        vectors = json.loads((ROOT / "tests/fixtures/r4_04_migration_evidence_vectors.json").read_text())
        journal = next(c["input"]["candidate"] for c in vectors["cases"] if c["id"] == "journal-cleanup-completed")
        data = {
            "backup_schedule_enabled_at": enabled,
            "expected_managed_cluster_names": ["cluster-a"],
            "expected_managed_cluster_count": 1,
            "primary_has_observability": False,
            "secondary_has_observability": False,
            "migration_backups": journal,
        }
        if boundary == "missing":
            data.pop("backup_schedule_enabled_at")
        elif boundary != "valid":
            data["backup_schedule_enabled_at"] = boundary
        state: dict = {
            "schema_version": "2.0",
            "phase": "finalization",
            "phase_status": "pass",
            "completed_phases": ["preflight", "primary_prep", "activation", "post_activation", "finalization"],
            "operational_data": data,
            "operation_identity": identity,
            "errors": [],
            "report_refs": [],
        }
        if not completed:
            state["completed_phases"].remove("finalization")
            state["phase_status"] = "enter"
        if state_edit:
            state_edit(state)
        checkpoint = tmp_path / "checkpoint.json"
        checkpoint.write_text(json.dumps(state))
        before = checkpoint.read_bytes()
        (tmp_path / "checkpoint-original.json").write_bytes(before)
        stat = checkpoint.stat()
        cp = {"enabled": True, "backend": "file", "path": str(checkpoint)}
        if reset is not None:
            cp.update(reset)
        variables = {
            "acm_switchover_hubs": config,
            "acm_switchover_operation": operation,
            "acm_switchover_execution": {"mode": mode, "checkpoint": cp, "report_dir": str(tmp_path / "artifacts")},
            "acm_switchover_features": {"argocd": {"manage": False, "resume_on_failure": False}},
            **(extra or {}),
        }
        vf = tmp_path / "vars.yml"
        vf.write_text(yaml.safe_dump(variables))
        if route == "role":
            playbook = tmp_path / "role.yml"
            playbook.write_text(
                yaml.safe_dump(
                    [
                        {
                            "hosts": "localhost",
                            "connection": "local",
                            "gather_facts": False,
                            "tasks": [{"ansible.builtin.include_role": {"name": "tomazb.acm_switchover.finalization"}}],
                        }
                    ]
                )
            )
        else:
            playbook = COLLECTION / "playbooks" / ("restore_only.yml" if restore else "switchover.yml")
        command = ["ansible-playbook", str(playbook), "-i", "localhost,", "-e", "@" + str(vf)]
        if check:
            command.append("--check")
        env = _ansible_env(ROOT, tmp_path)
        if trace:
            command = [
                sys.executable,
                str(COLLECTION / "tests/support/backup_continuity_trace_runner.py"),
                *command[1:],
            ]
            env["ACM_BACKUP_TRACE_PATH"] = str(tmp_path / "backup-action-trace.jsonl")
            env["ACM_BACKUP_INTERRUPT_AFTER_PROOF"] = "1" if interrupt else "0"
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, env=env, timeout=90)
        (tmp_path / "api-requests.json").write_text(
            json.dumps({"primary": primary.requests, "secondary": secondary.requests}, indent=2)
        )
        (tmp_path / "ansible.stdout").write_text(completed.stdout)
        (tmp_path / "ansible.stderr").write_text(completed.stderr)
        if actual_argocd:
            assert isinstance(primary, BackupRetryHub) and isinstance(secondary, BackupRetryHub)
            (tmp_path / "argocd-patches.json").write_text(
                json.dumps({"primary": primary.patches, "secondary": secondary.patches}, indent=2)
            )
        return completed, primary, secondary, checkpoint, before, stat

    yield run
    for hub in hubs:
        hub.close()


@pytest.mark.parametrize("route", ["role", "playbook"])
@pytest.mark.parametrize("restore", [False, True])
def test_shipped_completed_skip_requires_current_backup_proof(run_backup_entry, route, restore):
    result, p, s, path, before, stat = run_backup_entry(route=route, restore=restore)
    output = result.stdout + result.stderr
    assert result.returncode != 0, "Historical completed skip falsely succeeded: " + output
    assert "Backup continuity refused" in output, output
    assert any(r["path"] == BACKUPS for r in s.requests), s.requests
    assert path.read_bytes() == before and path.stat().st_mtime_ns == stat.st_mtime_ns
    assert not p.writes and not s.writes


@pytest.mark.parametrize("route", ["role", "playbook"])
@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("mode,check", [("execute", False), ("execute", True), ("dry_run", False), ("validate", False)])
def test_completed_positive_is_current_read_only_noop(run_backup_entry, route, restore, mode, check):
    result, p, s, path, before, stat = run_backup_entry(route=route, restore=restore, good=True, mode=mode, check=check)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert (
        path.read_bytes() == before
        and path.stat().st_mtime_ns == stat.st_mtime_ns
        and path.stat().st_ino == stat.st_ino
    )
    assert any(r["path"] == "/api/v1/namespaces/kube-system" for r in s.requests), s.requests
    assert any(r["path"] == BACKUPS for r in s.requests), s.requests
    assert any(r["path"] == BACKUPS + "/acm-managed-clusters-current" for r in s.requests), s.requests
    assert not p.writes and not s.writes
    assert "rescued=0" in output


@pytest.mark.parametrize("route", ["role", "playbook"])
@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("reset", [None, {"reset": True}, {"reset_from": "preflight"}])
def test_wrong_retained_identity_and_reset_controls_never_replay(run_backup_entry, route, restore, reset):
    result, p, s, path, before, stat = run_backup_entry(
        route=route,
        restore=restore,
        good=True,
        wrong_identity=True,
        reset=reset,
        extra={
            "acm_switchover_hub_identities": {
                "primary": {"cluster_uid": "preseed"},
                "secondary": {"cluster_uid": "preseed"},
            },
            "acm_switchover_backup_completed_assessed": True,
            "acm_switchover_features": {"argocd": {"manage": True, "resume_on_failure": True}},
        },
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert ("cannot reset or rewind" if reset else "identity") in output.lower(), output
    assert (
        path.read_bytes() == before
        and path.stat().st_mtime_ns == stat.st_mtime_ns
        and path.stat().st_ino == stat.st_ino
    )
    assert not p.writes and not s.writes
    assert not any(r["path"] == BACKUPS for r in s.requests)
    assert "rescued=0" in output
    if reset is None:
        assert any(r["path"] == "/api/v1/namespaces/kube-system" for r in s.requests)


@pytest.mark.parametrize("boundary", ["missing", None, False, 0, "", "not-a-time", "2024-01-01T00:00:00"])
def test_completed_raw_boundary_refusal_survives_native_templating(run_backup_entry, boundary):
    result, p, s, path, before, stat = run_backup_entry(
        good=True,
        boundary=boundary,
        extra={
            "acm_switchover_verify_backups_result": {"status": "pass"},
            "acm_switchover_backup_schedule_enabled_at": "2024-01-01T00:00:00Z",
        },
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0 and "enable_boundary" in output, output
    assert path.read_bytes() == before and path.stat().st_mtime_ns == stat.st_mtime_ns
    assert not p.writes and not s.writes


@pytest.mark.parametrize(
    "case",
    [
        "good",
        "old",
        "404",
        "403",
        "discovery",
        "replacement",
        "partial",
        "errors",
        "disappearance",
        "older_failed_latest_healthy",
        "older_healthy_latest_failed",
    ],
)
def test_real_module_transport_and_creation_health_contract(tmp_path, case):
    now = datetime.now(timezone.utc)
    enabled = (now - timedelta(minutes=2)).isoformat()
    backup: dict = {
        "apiVersion": "velero.io/v1",
        "kind": "Backup",
        "metadata": {
            "name": "acm-managed-clusters-proof",
            "uid": "uid-proof",
            "resourceVersion": "12",
            "creationTimestamp": (now - timedelta(minutes=1)).isoformat(),
        },
        "status": {"phase": "Completed", "errors": 0},
    }
    if case == "old":
        backup["metadata"]["creationTimestamp"] = (now - timedelta(days=1)).isoformat()
    if case == "partial":
        backup["status"]["phase"] = "PartiallyFailed"
    if case == "errors":
        backup["status"]["errors"] = 1
    hub = FakeMigrationHub(backups=[backup])
    if case in ("older_failed_latest_healthy", "older_healthy_latest_failed"):
        older = copy.deepcopy(backup)
        older["metadata"].update(name="acm-managed-clusters-older", uid="uid-older")
        older["status"]["phase"] = "Failed" if case == "older_failed_latest_healthy" else "Completed"
        latest = copy.deepcopy(backup)
        latest["metadata"].update(creationTimestamp=(now - timedelta(seconds=30)).isoformat())
        latest["status"]["phase"] = "Completed" if case == "older_failed_latest_healthy" else "Failed"
        hub.backups = {b["metadata"]["name"]: b for b in (older, latest)}
    listed = copy.deepcopy(backup)
    if case == "replacement":
        hub.backups[backup["metadata"]["name"]]["metadata"]["uid"] = "replacement"
    if case in ("replacement", "disappearance"):
        hub.backup_list_items = [listed]
    if case == "disappearance":
        hub.backups.clear()
    if case in ("404", "403"):
        hub.failures[("GET", BACKUPS)] = int(case)
    if case == "discovery":
        hub.failures[("GET", "/apis/velero.io/v1")] = 403
    kubeconfig = tmp_path / "module.kubeconfig"
    write_kubeconfig(kubeconfig, context="destination", server=hub.url, token="fixture-token")
    playbook = tmp_path / "module.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "tasks": [
                        {
                            "tomazb.acm_switchover.acm_backup_continuity": {
                                "inputs": {"enabled_at_present": True, "enabled_at": enabled},
                                "kubeconfig": str(kubeconfig),
                                "context": "destination",
                                "timeout": 0.2,
                                "poll_interval": 0.01,
                            },
                            "register": "result",
                        },
                        {
                            "ansible.builtin.assert": {
                                "that": ["not result.changed", 'result.proof.phase == "Completed"']
                            }
                        },
                    ],
                }
            ]
        )
    )
    try:
        result = subprocess.run(
            ["ansible-playbook", str(playbook), "-i", "localhost,"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            env=_ansible_env(ROOT, tmp_path),
            timeout=40,
        )
    finally:
        hub.close()
    output = result.stdout + result.stderr
    assert (result.returncode == 0) == (case in ("good", "older_failed_latest_healthy")), output
    assert not hub.writes
    if case not in ("good", "older_failed_latest_healthy"):
        assert "Backup continuity refused" in output, output
    if case not in ("discovery",):
        assert any(r["path"] == BACKUPS for r in hub.requests), hub.requests


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("seeded", [False, True])
def test_active_history_proof_precedes_cleanup_and_preserves_original_facts(run_backup_entry, restore, seeded):
    result, p, s, path, before, stat = run_backup_entry(
        restore=restore,
        completed=False,
        extra={"acm_switchover_backup_schedule_enabled_at": "2020-01-01T00:00:00Z"} if seeded else None,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0 and "Backup continuity refused" in output, output
    old, new = json.loads(before), json.loads(path.read_bytes())
    assert (
        new["operational_data"]["backup_schedule_enabled_at"] == old["operational_data"]["backup_schedule_enabled_at"]
    )
    assert new["operational_data"]["migration_backups"] == old["operational_data"]["migration_backups"]
    assert new["operation_identity"] == old["operation_identity"]
    assert not p.writes and not s.writes
    assert not any("/restores" in r["path"] or "/backupschedules" in r["path"] for r in s.requests), s.requests


@pytest.mark.parametrize("route", ["role", "playbook"])
@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize(
    "fault", ["missing_identity", "missing_uid", "missing_context", "invalid_data", "invalid_completed"]
)
def test_completed_loader_and_identity_refusals_preserve_exact_bytes(run_backup_entry, route, restore, fault):
    def edit(state):
        if fault == "missing_identity":
            state.pop("operation_identity")
        elif fault == "missing_uid":
            state["operation_identity"].pop("secondary_cluster_uid")
        elif fault == "missing_context":
            state["operation_identity"].pop("secondary_context")
        elif fault == "invalid_data":
            state["operational_data"] = []
        elif fault == "invalid_completed":
            state["completed_phases"] = "finalization"

    result, p, s, path, before, stat = run_backup_entry(route=route, restore=restore, good=True, state_edit=edit)
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert path.read_bytes() == before and path.stat().st_mtime_ns == stat.st_mtime_ns
    assert not p.writes and not s.writes
    assert not any(r["path"] == BACKUPS for r in s.requests)


@pytest.mark.parametrize("route", ["role", "playbook"])
@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("phase_status", ["enter", "fail"])
@pytest.mark.parametrize("seeded", [False, True])
def test_historical_finalization_missing_boundary_refuses_before_mutation(
    run_backup_entry, route, restore, phase_status, seeded
):
    def historical(state):
        state["phase_status"] = phase_status
        state["operational_data"]["saved_backup_schedule"] = {
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "BackupSchedule",
            "metadata": {"name": "fixture-schedule", "namespace": "open-cluster-management-backup"},
            "spec": {"paused": True, "veleroSchedule": "*/5 * * * *", "veleroTTL": "24h0m0s"},
        }

    result, p, s, path, before, stat = run_backup_entry(
        route=route,
        restore=restore,
        completed=False,
        boundary="missing",
        state_edit=historical,
        old_action="none",
        extra={"acm_switchover_backup_schedule_enabled_at": "2020-01-01T00:00:00Z"} if seeded else None,
    )
    output = result.stdout + result.stderr
    assert "backup_schedule_enabled_at" not in json.loads(before)["operational_data"]
    assert not p.writes and not s.writes, ("Historical absent boundary reached mutation", p.writes, s.writes)
    assert result.returncode != 0 and "missing_enable_boundary" in output, output
    assert "backup_schedule_enabled_at" not in json.loads(path.read_bytes())["operational_data"]
    assert not any("/restores" in r["path"] or "/backupschedules" in r["path"] for r in p.requests + s.requests), (
        p.requests + s.requests
    )
    assert not p.writes and not s.writes


@pytest.mark.parametrize("restore", [False, True])
def test_fresh_predecessor_missing_boundary_still_allows_shipped_readonly_preview(run_backup_entry, restore):
    result, p, s, path, before, stat = run_backup_entry(
        restore=restore,
        completed=False,
        boundary="missing",
        mode="dry_run",
        old_action="none",
        state_edit=lambda state: state.update(phase="post_activation", phase_status="pass"),
        extra={
            "acm_finalization_backup_schedules_info": {
                "resources": [
                    {
                        "apiVersion": "cluster.open-cluster-management.io/v1beta1",
                        "kind": "BackupSchedule",
                        "metadata": {"name": "fixture-schedule", "namespace": "open-cluster-management-backup"},
                        "spec": {"paused": True, "veleroSchedule": "*/5 * * * *", "veleroTTL": "24h0m0s"},
                    }
                ]
            }
        },
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert path.read_bytes() == before and path.stat().st_mtime_ns == stat.st_mtime_ns
    assert not p.writes and not s.writes


@pytest.mark.parametrize("route", ["role", "playbook"])
def test_genuinely_fresh_restore_only_no_schedule_keeps_existing_exception(run_backup_entry, route):
    result, p, s, path, before, stat = run_backup_entry(
        route=route,
        restore=True,
        completed=False,
        boundary="missing",
        state_edit=lambda state: state.update(phase="post_activation", phase_status="pass"),
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    after = json.loads(path.read_bytes())
    assert after["phase"] == "finalization" and after["phase_status"] == "pass"
    assert "finalization" in after["completed_phases"]
    assert "backup_schedule_enabled_at" not in json.loads(before)["operational_data"]
    assert after["operational_data"]["backup_schedule_enabled_at"]
    assert not any(r["path"] == BACKUPS for r in s.requests), s.requests
    assert not p.writes and not s.writes


@pytest.mark.parametrize("route", ["role", "playbook"])
@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("boundary", ["valid", "missing"])
def test_active_retry_repauses_before_backup_refusal(run_backup_entry, route, restore, boundary):
    def failed_retry(state):
        state["phase_status"] = "fail"
        state["operational_data"].update(
            argocd_repause_required={"failed_phase": "finalization", "recorded_at": "2026-10-01T00:00:00Z"},
            argocd_run_id="backup-retry-fixture",
            argocd_discovery_namespaces={"primary": ["argocd"], "secondary": ["argocd"]},
        )

    result, primary, secondary, path, before, stat = run_backup_entry(
        route=route, restore=restore, completed=False, boundary=boundary, state_edit=failed_retry
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0 and "Backup continuity refused" in output, output
    assert output.index("Clear the Argo CD re-pause marker after the re-pause") < output.index(
        "Prove historical Backup continuity before finalization mutations"
    )
    original, saved = json.loads(before), json.loads(path.read_bytes())
    assert "argocd_repause_required" not in saved["operational_data"]
    for key in ("migration_backups", "backup_schedule_enabled_at"):
        assert saved["operational_data"].get(key) == original["operational_data"].get(key)
        assert (key in saved["operational_data"]) == (key in original["operational_data"])
    assert saved["operation_identity"] == original["operation_identity"]
    assert saved["phase"] == "finalization" and saved["phase_status"] == "fail"
    assert not any(
        "/restores" in r["path"] or "/backupschedules" in r["path"] for r in primary.requests + secondary.requests
    )
    # The real re-pause discovers Argo CD absent on these fake hubs. Existing
    # journal-rescue integration tests separately cover actual Application PATCHes.
    assert not primary.writes and not secondary.writes


@pytest.mark.parametrize("route", ["role", "playbook"])
@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("boundary", ["valid", "missing"])
def test_actual_application_repause_patch_precedes_backup_refusal(run_backup_entry, route, restore, boundary):
    def failed_retry(state):
        state["phase_status"] = "fail"
        state["operational_data"].update(
            argocd_repause_required={"failed_phase": "finalization", "recorded_at": "2026-10-01T00:00:00Z"},
            argocd_run_id="backup-retry-fixture",
            argocd_discovery_namespaces={"primary": ["argocd"], "secondary": ["argocd"]},
        )

    result, primary, secondary, path, before, stat = run_backup_entry(
        route=route,
        restore=restore,
        completed=False,
        boundary=boundary,
        state_edit=failed_retry,
        actual_argocd=True,
        extra={"acm_switchover_argocd": {"namespace": "argocd", "run_id": "backup-retry-fixture"}},
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0 and "Backup continuity refused" in output, output
    original, saved = json.loads(before), json.loads(path.read_bytes())
    assert "argocd_repause_required" not in saved["operational_data"]
    assert output.index("Clear the Argo CD re-pause marker after the re-pause") < output.index(
        "Prove historical Backup continuity before finalization mutations"
    )
    for hub in ([secondary] if restore else [primary, secondary]):
        assert len(hub.patches) == 1, hub.patches
        patch = hub.patches[0]
        assert (
            patch["body"]["metadata"]["annotations"]["acm-switchover.argoproj.io/paused-by"] == "backup-retry-fixture"
        )
        assert "automated" not in hub.get_application("argocd", "acm-owned")["spec"].get("syncPolicy", {})
        assert all(r["path"].startswith("/apis/argoproj.io/") for r in hub.writes), hub.writes
    requests = secondary.requests
    if boundary == "valid":
        assert next(i for i, r in enumerate(requests) if r["method"] == "PATCH") < next(
            i for i, r in enumerate(requests) if r["path"] == BACKUPS
        )
    else:
        assert not any(r["path"] == BACKUPS for r in requests)
    if restore:
        assert not primary.requests and not primary.writes
    assert saved["operation_identity"] == original["operation_identity"]
    assert saved["operational_data"]["migration_backups"] == original["operational_data"]["migration_backups"]
    assert saved["operational_data"].get("backup_schedule_enabled_at") == original["operational_data"].get(
        "backup_schedule_enabled_at"
    )
    assert ("backup_schedule_enabled_at" in saved["operational_data"]) == (
        "backup_schedule_enabled_at" in original["operational_data"]
    )
    assert saved["phase"] == "finalization" and saved["phase_status"] == "fail"
    assert not any("/restores" in r["path"] or "/backupschedules" in r["path"] for r in primary.requests + requests)


@pytest.mark.parametrize("route", ["role", "playbook"])
@pytest.mark.parametrize("restore", [False, True])
def test_shipped_fresh_execute_enables_reads_proves_and_publishes_pass(run_backup_entry, route, restore):
    """BC15 exercises shipped cadence and proof after actual schedule creation."""

    def fresh_predecessor(state):
        state.update(phase="post_activation", phase_status="pass")
        state["operational_data"]["saved_backup_schedule"] = {
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "BackupSchedule",
            "metadata": {"name": "fixture-schedule", "namespace": "open-cluster-management-backup"},
            "spec": {"paused": True, "veleroSchedule": "*/5 * * * *", "veleroTTL": "24h0m0s"},
        }

    result, primary, secondary, checkpoint, _, _ = run_backup_entry(
        route=route,
        restore=restore,
        good=True,
        completed=False,
        boundary="missing",
        state_edit=fresh_predecessor,
        old_action="none",
        complete_after_enable=True,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    requests = secondary.requests
    schedule_writes = [
        index
        for index, request in enumerate(requests)
        if request["method"] in {"POST", "PATCH"} and "backupschedules" in request["path"]
    ]
    backup_reads = [
        index
        for index, request in enumerate(requests)
        if request["method"] == "GET" and (request["path"] == BACKUPS or request["path"].startswith(BACKUPS + "/"))
    ]
    assert schedule_writes and backup_reads and min(schedule_writes) < min(backup_reads), requests
    assert any(request["path"] == BACKUPS + "/acm-managed-clusters-current" for request in requests)
    saved = json.loads(checkpoint.read_bytes())
    assert saved["phase"] == "finalization" and saved["phase_status"] == "pass", saved
    assert "finalization" in saved["completed_phases"]
    assert saved["operational_data"]["backup_schedule_enabled_at"]
    assert "Publish backup verification result" in output
    assert "rescued=0" in output
    assert not primary.writes


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("reset", [None, {"reset": True}, {"reset_from": "finalization"}])
def test_active_direct_entry_wrong_identity_refuses_before_backup_or_mutation(run_backup_entry, restore, reset):
    result, primary, secondary, path, before, stat = run_backup_entry(
        route="role",
        restore=restore,
        completed=False,
        good=True,
        wrong_identity=True,
        reset=reset,
        old_action="none",
    )
    output = result.stdout + result.stderr
    # Old active entry reads Backup proof without establishing its physical hub.
    assert not any(r["path"] == BACKUPS for r in secondary.requests), output
    assert result.returncode != 0, output
    assert not primary.writes and not secondary.writes
    assert path.read_bytes() == before
    assert any(r["path"] == "/api/v1/namespaces/kube-system" for r in secondary.requests)
