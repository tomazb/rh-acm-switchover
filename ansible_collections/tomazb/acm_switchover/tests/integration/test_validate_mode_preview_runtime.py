"""#284 runtime contract: validate and dry_run write nothing and publish preview values.

Every case runs the shipped roles and playbooks against local fake Kubernetes APIs that
record every request. A preview (validate or dry_run) must issue no write, and every
result it publishes must report what was planned, never a skipped write as applied.
Execute keeps its result keys exactly as before. ``rbac_bootstrap`` and the standalone
Argo CD resume playbook refuse validate and unknown modes before any request.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.tests.conftest import _ansible_env
from ansible_collections.tomazb.acm_switchover.tests.integration.argocd_fake_api import (
    FakeArgoCDHub,
    application,
    write_kubeconfig,
)
from ansible_collections.tomazb.acm_switchover.tests.integration.r3_02_fake_api import FakeR302API
from ansible_collections.tomazb.acm_switchover.tests.integration.rbac_fake_api import FakeRBACAPI

PLAYBOOKS = "ansible_collections/tomazb/acm_switchover/playbooks"
HARNESS = "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks"
PREVIEW_MODES = ("validate", "dry_run")
RUN_ID = "run-284"
SCHEDULE = {
    "apiVersion": "cluster.open-cluster-management.io/v1beta1",
    "kind": "BackupSchedule",
    "metadata": {"name": "schedule-acm", "namespace": "open-cluster-management-backup", "resourceVersion": "1"},
    "spec": {"veleroSchedule": "0 */2 * * *", "veleroTtl": "120h", "paused": False},
}
COMPACTOR = {
    "apiVersion": "apps/v1",
    "kind": "StatefulSet",
    "metadata": {"name": "thanos-compactor", "namespace": "open-cluster-management-observability"},
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _sleep_shim(tmp_path: Path) -> Path:
    """Shrink Ansible retry delays so a bounded wait is observable in seconds."""
    shim_dir = tmp_path / "sleep-shim"
    shim_dir.mkdir(exist_ok=True)
    (shim_dir / "sitecustomize.py").write_text(
        "\n".join(
            [
                "import ast",
                "import time",
                "for _legacy_name in ('Str', 'Num', 'Bytes', 'NameConstant', 'Ellipsis'):",
                "    if not hasattr(ast, _legacy_name):",
                "        setattr(ast, _legacy_name, ast.Constant)",
                "_original_sleep = time.sleep",
                "time.sleep = lambda seconds: _original_sleep(min(float(seconds), 0.001))",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return shim_dir


def _run(
    tmp_path: Path,
    playbook: str,
    variables: dict,
    *,
    check: bool = False,
    fast_sleep: bool = False,
) -> subprocess.CompletedProcess[str]:
    vars_file = tmp_path / f"{Path(playbook).stem}-vars.yml"
    vars_file.write_text(
        yaml.safe_dump({**variables, "ansible_python_interpreter": sys.executable}, sort_keys=False),
        encoding="utf-8",
    )
    env = _ansible_env(_repo_root(), tmp_path)
    if fast_sleep:
        env["PYTHONPATH"] = f"{_sleep_shim(tmp_path)}:{env['PYTHONPATH']}"
    # Not the examples inventory: its group_vars pin acm_switchover_execution.mode to execute,
    # and these cases must control (or leave unset) the mode themselves.
    command = ["ansible-playbook", playbook, "-i", "localhost,", "-c", "local", "-e", f"@{vars_file}"]
    if check:
        command.append("--check")
    return subprocess.run(command, cwd=_repo_root(), capture_output=True, text=True, check=False, env=env, timeout=300)


def _output(completed: subprocess.CompletedProcess[str]) -> str:
    return completed.stdout + completed.stderr


def _hub(tmp_path: Path, name: str, api) -> dict:
    kubeconfig = tmp_path / f"{name}.kubeconfig"
    write_kubeconfig(kubeconfig, context=f"{name}-hub", server=api.url, token="fixture-token")
    hub = {"kubeconfig": str(kubeconfig), "context": f"{name}-hub"}
    if hasattr(api, "cluster_uid"):
        hub["cluster_uid"] = api.cluster_uid
    return hub


def _operation() -> dict:
    return {"method": "passive", "activation_method": "patch", "restore_only": False, "old_hub_action": "secondary"}


# --- primary_prep -----------------------------------------------------------------------------


def _primary_prep_variables(hubs: dict, mode: str, result_path: Path, **extra) -> dict:
    variables = {
        "acm_switchover_hubs": hubs,
        "acm_switchover_operation": _operation(),
        "acm_switchover_execution": {"mode": mode, "run_id": "workflow-run", "checkpoint": {"enabled": False}},
        "acm_switchover_features": {
            "argocd": {"manage": False, "resume_on_failure": False},
            "skip_observability_checks": False,
            "manage_auto_import_strategy": False,
        },
        "acm_switchover_collection_version": "",
        "acm_primary_prep_mch_info": {"resources": [{"status": {"currentVersion": "2.14.3"}}]},
        "acm_primary_prep_backup_schedules_info": {"resources": [SCHEDULE]},
        "acm_primary_compactor_info": {"resources": [COMPACTOR]},
        "acm_switchover_primary_has_observability": True,
        "harness_result_path": str(result_path),
    }
    variables.update(extra)
    return variables


@pytest.mark.parametrize("mode", PREVIEW_MODES)
def test_primary_prep_preview_writes_nothing_and_publishes_the_plan(tmp_path, mode):
    primary = FakeArgoCDHub(
        cluster_uid="primary-uid",
        applications=[
            application("argocd", "acm-one", automated=True),
            application("argocd", "acm-two", automated=True),
            application("argocd", "acm-already-paused", automated=False),
            application("argocd", "unrelated", automated=True, acm_touching=False),
        ],
    )
    secondary = FakeArgoCDHub(
        cluster_uid="secondary-uid", applications=[application("team-gitops", "acm-secondary", automated=True)]
    )
    try:
        hubs = {"primary": _hub(tmp_path, "primary", primary), "secondary": _hub(tmp_path, "secondary", secondary)}
        checkpoint = tmp_path / "primary-prep-checkpoint.json"
        result_path = tmp_path / "result.json"
        variables = _primary_prep_variables(
            hubs,
            mode,
            result_path,
            harness_identity_barrier=True,
        )
        variables["acm_switchover_execution"]["checkpoint"] = {
            "enabled": True,
            "backend": "file",
            "path": str(checkpoint),
        }
        variables["acm_switchover_features"]["argocd"]["manage"] = True
        completed = _run(tmp_path, f"{HARNESS}/run_primary_prep_modes.yml", variables)
        primary_writes, secondary_writes = primary.writes, secondary.writes
        primary_paths = [request["path"] for request in primary.requests]
    finally:
        primary.close()
        secondary.close()

    # Writes first: on the unfixed role this is where a preview fails.
    assert primary_writes == [] and secondary_writes == [], _output(completed)
    assert completed.returncode == 0, _output(completed)
    assert not checkpoint.exists(), "a preview must persist no checkpoint transition"
    # The planned-only reads stay execute-only, like #335's activation preview.
    assert not any("managedclusters" in path for path in primary_paths), primary_paths
    assert not any("/pods" in path or "statefulsets" in path for path in primary_paths), primary_paths

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["phase"] == {
        "phase": "primary_prep",
        "status": "pass",
        "changed": True,
        "preview": True,
        "reason": mode,
    }
    assert result["pause_backups"]["applied"] is False
    assert result["pause_backups"]["changed"] is True  # would-change, as documented
    assert result["pause_backups"]["operation"]["action"] == "patch"
    assert result["pause_backups"]["reason"] == mode
    assert result["auto_import"] == {
        "changed": False,
        "disabled_clusters": [],
        "planned": True,
        "status": "skipped",
        "reason": mode,
        "target_strategy": "default",
    }
    assert result["observability"] == {"changed": False, "scaled": False, "planned": True, "reason": mode}
    assert result["argocd_summary"] == {"paused": 0, "restored": 0, "planned_pause": 3, "planned_resume": 0}
    assert result["argocd_summary_by_hub"] == {
        "primary": {"paused": 0, "restored": 0, "planned_pause": 2, "planned_resume": 0},
        "secondary": {"paused": 0, "restored": 0, "planned_pause": 1, "planned_resume": 0},
    }


def test_primary_prep_execute_writes_and_keeps_its_result_keys(tmp_path):
    api = FakeR302API(backup_schedules=[SCHEDULE], pods_follow_replicas=True)
    try:
        hubs = {"primary": _hub(tmp_path, "primary", api), "secondary": {"kubeconfig": "", "context": ""}}
        result_path = tmp_path / "result.json"
        completed = _run(
            tmp_path,
            f"{HARNESS}/run_primary_prep_modes.yml",
            _primary_prep_variables(hubs, "execute", result_path),
            fast_sleep=True,
        )
        writes = api.writes
        cluster = next(item for item in api.managed_clusters if item["metadata"]["name"] == "cluster-a")
    finally:
        api.close()

    assert completed.returncode == 0, _output(completed)
    assert sorted(request["path"] for request in writes) == sorted(
        [
            "/apis/cluster.open-cluster-management.io/v1beta1/namespaces/open-cluster-management-backup"
            "/backupschedules/schedule-acm",
            "/apis/cluster.open-cluster-management.io/v1/managedclusters/cluster-a",
            "/apis/apps/v1/namespaces/open-cluster-management-observability/statefulsets/thanos-compactor/scale",
        ]
    )
    assert "import.open-cluster-management.io/disable-auto-import" in cluster["metadata"]["annotations"]
    result = json.loads(result_path.read_text(encoding="utf-8"))
    # Execute output is unchanged: the same keys, applied values, no preview keys.
    assert result["phase"] == {"phase": "primary_prep", "status": "pass", "changed": True}
    assert set(result["pause_backups"]) == {"changed", "operation"}
    assert result["pause_backups"]["changed"] is True
    assert result["auto_import"] == {
        "changed": True,
        "disabled_clusters": ["cluster-a"],
        "status": "pass",
        "target_strategy": "default",
    }
    assert result["observability"] == {"changed": True, "scaled": True}


@pytest.mark.parametrize(("mode", "check"), [("Execute", False), ("preview", True)])
def test_primary_prep_refuses_an_unknown_mode_before_any_checkpoint_action(tmp_path, mode, check):
    api = FakeR302API(backup_schedules=[SCHEDULE])
    try:
        hubs = {"primary": _hub(tmp_path, "primary", api), "secondary": {"kubeconfig": "", "context": ""}}
        checkpoint = tmp_path / "checkpoint.json"
        variables = _primary_prep_variables(hubs, mode, tmp_path / "result.json")
        variables["acm_switchover_execution"]["checkpoint"] = {
            "enabled": True,
            "backend": "file",
            "path": str(checkpoint),
        }
        completed = _run(tmp_path, f"{HARNESS}/run_primary_prep_modes.yml", variables, check=check)
        requests = api.requests
    finally:
        api.close()

    assert requests == []
    assert completed.returncode != 0
    assert "must be execute, dry_run or validate" in _output(completed)
    assert not checkpoint.exists()


# --- argocd_manage ----------------------------------------------------------------------------


def _argocd_hubs():
    primary = FakeArgoCDHub(
        cluster_uid="primary-uid",
        applications=[
            application("argocd", "acm-one", automated=True),
            application("argocd", "acm-two", automated=True),
            application("argocd", "acm-owned-paused", automated=False, run_id=RUN_ID),
            application("argocd", "acm-foreign-paused", automated=False, run_id="foreign-run"),
        ],
    )
    secondary = FakeArgoCDHub(
        cluster_uid="secondary-uid",
        applications=[
            application("team-gitops", "acm-three", automated=True),
            application("team-gitops", "acm-owned-one", automated=False, run_id=RUN_ID),
            application("team-gitops", "acm-owned-two", automated=False, run_id=RUN_ID),
        ],
    )
    return primary, secondary


_ALL_STEPS = [
    {"mode": "pause", "hub": "primary"},
    {"mode": "pause", "hub": "secondary"},
    {"mode": "resume", "hub": "primary"},
    {"mode": "resume", "hub": "secondary"},
]


def _argocd_variables(hubs: dict, mode: str | None, result_path: Path) -> dict:
    variables = {
        "acm_switchover_hubs": hubs,
        "acm_switchover_argocd": {"mode": "pause", "run_id": RUN_ID},
        "harness_argocd_steps": _ALL_STEPS,
        "harness_result_path": str(result_path),
    }
    if mode is not None:
        variables["acm_switchover_execution"] = {"mode": mode}
    return variables


@pytest.mark.parametrize("mode", [*PREVIEW_MODES, None], ids=[*PREVIEW_MODES, "unset-mode"])
def test_argocd_manage_preview_writes_nothing_and_counts_the_plan_per_hub(tmp_path, mode):
    primary, secondary = _argocd_hubs()
    try:
        hubs = {"primary": _hub(tmp_path, "primary", primary), "secondary": _hub(tmp_path, "secondary", secondary)}
        result_path = tmp_path / "result.json"
        completed = _run(tmp_path, f"{HARNESS}/run_argocd_manage_modes.yml", _argocd_variables(hubs, mode, result_path))
        primary_writes, secondary_writes = primary.writes, secondary.writes
    finally:
        primary.close()
        secondary.close()

    assert primary_writes == [] and secondary_writes == [], _output(completed)
    assert completed.returncode == 0, _output(completed)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    # Applied counts are zero; the plan counts what each hub's discovery read would change.
    assert result["argocd_summary"] == {"paused": 0, "restored": 0, "planned_pause": 3, "planned_resume": 3}
    assert result["argocd_summary_by_hub"] == {
        "primary": {"paused": 0, "restored": 0, "planned_pause": 2, "planned_resume": 1},
        "secondary": {"paused": 0, "restored": 0, "planned_pause": 1, "planned_resume": 2},
    }


def test_argocd_manage_execute_patches_and_keeps_its_summary_keys(tmp_path):
    primary, secondary = _argocd_hubs()
    try:
        hubs = {"primary": _hub(tmp_path, "primary", primary), "secondary": _hub(tmp_path, "secondary", secondary)}
        result_path = tmp_path / "result.json"
        completed = _run(
            tmp_path, f"{HARNESS}/run_argocd_manage_modes.yml", _argocd_variables(hubs, "execute", result_path)
        )
        primary_patches = [entry["name"] for entry in primary.patches if entry["changed"]]
        secondary_patches = [entry["name"] for entry in secondary.patches if entry["changed"]]
    finally:
        primary.close()
        secondary.close()

    assert completed.returncode == 0, _output(completed)
    assert sorted(primary_patches) == ["acm-one", "acm-one", "acm-owned-paused", "acm-two", "acm-two"]
    assert sorted(secondary_patches) == ["acm-owned-one", "acm-owned-two", "acm-three", "acm-three"]
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["argocd_summary"] == {"paused": 3, "restored": 6}
    assert result["argocd_summary_by_hub"] == {
        "primary": {"paused": 2, "restored": 3},
        "secondary": {"paused": 1, "restored": 3},
    }


def test_shipped_argocd_manage_test_playbook_previews_validate_against_live_hubs(tmp_path):
    primary, secondary = _argocd_hubs()
    try:
        hubs = {"primary": _hub(tmp_path, "primary", primary), "secondary": _hub(tmp_path, "secondary", secondary)}
        summary_path = tmp_path / "summary.json"
        variables = {
            "acm_switchover_hubs": hubs,
            "acm_switchover_argocd": {"mode": "pause", "run_id": RUN_ID},
            "acm_switchover_execution": {"mode": "validate"},
            "summary_path": str(summary_path),
        }
        completed = _run(tmp_path, f"{PLAYBOOKS}/argocd_manage_test.yml", variables)
        writes = primary.writes + secondary.writes
    finally:
        primary.close()
        secondary.close()

    assert writes == [], _output(completed)
    assert completed.returncode == 0, _output(completed)
    assert json.loads(summary_path.read_text(encoding="utf-8")) == {
        "paused": 0,
        "restored": 0,
        "planned_pause": 2,
        "planned_resume": 2,
    }


# --- refusing entries: argocd_resume.yml and rbac_bootstrap ----------------------------------


@pytest.mark.parametrize(("mode", "check"), [("validate", False), ("validate", True), ("Execute", False)])
def test_standalone_argocd_resume_refuses_validate_and_unknown_modes_before_any_request(tmp_path, mode, check):
    primary, secondary = _argocd_hubs()
    try:
        hubs = {"primary": _hub(tmp_path, "primary", primary), "secondary": _hub(tmp_path, "secondary", secondary)}
        variables = {
            "acm_switchover_hubs": hubs,
            "acm_switchover_operation": _operation(),
            "acm_switchover_argocd": {"run_id": RUN_ID},
            "acm_switchover_execution": {"mode": mode},
        }
        completed = _run(tmp_path, f"{PLAYBOOKS}/argocd_resume.yml", variables, check=check)
        requests = primary.requests + secondary.requests
    finally:
        primary.close()
        secondary.close()

    assert requests == [], _output(completed)
    assert completed.returncode != 0
    assert "Argo CD resume refuses execution mode" in _output(completed)


def test_standalone_argocd_resume_dry_run_previews_without_writing(tmp_path):
    primary, secondary = _argocd_hubs()
    try:
        hubs = {"primary": _hub(tmp_path, "primary", primary), "secondary": _hub(tmp_path, "secondary", secondary)}
        variables = {
            "acm_switchover_hubs": hubs,
            "acm_switchover_operation": _operation(),
            "acm_switchover_argocd": {"run_id": RUN_ID},
            "acm_switchover_execution": {"mode": "dry_run"},
        }
        completed = _run(tmp_path, f"{PLAYBOOKS}/argocd_resume.yml", variables)
        writes = primary.writes + secondary.writes
    finally:
        primary.close()
        secondary.close()

    assert writes == [], _output(completed)
    assert completed.returncode == 0, _output(completed)
    assert "Argo CD standalone resume summary: restored=0 primary=0 secondary=0" in _output(completed)


def _rbac_variables(api, tmp_path: Path, mode: str, *, generate: bool) -> dict:
    return {
        "acm_switchover_hubs": {"primary": _hub(tmp_path, "primary", api)},
        "acm_switchover_execution": {"mode": mode},
        "acm_switchover_rbac_bootstrap": {
            "role": "operator",
            "include_decommission": False,
            "generate_kubeconfigs": generate,
            "validate_permissions": False,
            "token_duration": "1h",
            "output_dir": str(tmp_path / "generated-kubeconfigs"),
        },
        "summary_path": str(tmp_path / "rbac-summary.json"),
    }


@pytest.mark.parametrize(("mode", "check"), [("validate", False), ("validate", True), ("dryrun", False)])
def test_rbac_bootstrap_refuses_validate_and_unknown_modes_before_any_request(tmp_path, mode, check):
    api = FakeRBACAPI()
    try:
        completed = _run(
            tmp_path,
            f"{PLAYBOOKS}/rbac_bootstrap.yml",
            _rbac_variables(api, tmp_path, mode, generate=True),
            check=check,
        )
        requests = api.requests
    finally:
        api.close()

    assert requests == [], _output(completed)
    assert completed.returncode != 0
    assert "rbac_bootstrap refuses execution mode" in _output(completed)
    assert not (tmp_path / "generated-kubeconfigs").exists()
    assert not (tmp_path / "rbac-summary.json").exists()


def test_rbac_bootstrap_dry_run_reports_the_plan_and_applies_nothing(tmp_path):
    api = FakeRBACAPI()
    try:
        completed = _run(
            tmp_path, f"{PLAYBOOKS}/rbac_bootstrap.yml", _rbac_variables(api, tmp_path, "dry_run", generate=True)
        )
        requests = api.requests
    finally:
        api.close()

    assert requests == [], _output(completed)
    assert completed.returncode == 0, _output(completed)
    assert not (tmp_path / "generated-kubeconfigs").exists()
    summary = json.loads((tmp_path / "rbac-summary.json").read_text(encoding="utf-8"))
    planned_path = str(tmp_path / "generated-kubeconfigs" / "primary-hub-operator.yaml")
    assert summary["mode"] == "dry_run"
    assert summary["status"] == "pass"
    assert summary["assets_applied"] == []
    assert summary["planned_assets"] and all(isinstance(asset, str) for asset in summary["planned_assets"])
    assert summary["generated_kubeconfig"] == ""
    assert summary["generated_kubeconfigs"] == {}
    assert summary["planned_kubeconfigs"] == {"operator": planned_path}


def test_rbac_bootstrap_execute_applies_manifests_and_keeps_its_result_keys(tmp_path):
    api = FakeRBACAPI()
    try:
        completed = _run(
            tmp_path, f"{PLAYBOOKS}/rbac_bootstrap.yml", _rbac_variables(api, tmp_path, "execute", generate=False)
        )
        writes = api.writes
        created = sorted(api.objects)
    finally:
        api.close()

    assert completed.returncode == 0, _output(completed)
    assert writes and all(request["method"] == "POST" for request in writes)
    assert "/api/v1/namespaces/acm-switchover/serviceaccounts/acm-switchover-operator" in created
    summary = json.loads((tmp_path / "rbac-summary.json").read_text(encoding="utf-8"))
    assert set(summary) == {
        "phase",
        "mode",
        "role",
        "assets_applied",
        "generated_kubeconfig",
        "generated_kubeconfigs",
        "status",
    }
    assert summary["mode"] == "execute"
    assert summary["assets_applied"] and summary["generated_kubeconfig"] == ""


# --- decision 5: native --check on the execute branch ------------------------------------------


def test_argocd_pause_under_native_check_mode_sends_a_server_dry_run_and_does_not_fail(tmp_path):
    primary = FakeArgoCDHub(
        cluster_uid="primary-uid",
        applications=[application("argocd", "acm-one", automated=True)],
        honor_dry_run=True,
    )
    try:
        hubs = {"primary": _hub(tmp_path, "primary", primary)}
        result_path = tmp_path / "result.json"
        variables = _argocd_variables(hubs, "execute", result_path)
        variables["harness_argocd_steps"] = [{"mode": "pause", "hub": "primary"}]
        completed = _run(tmp_path, f"{HARNESS}/run_argocd_manage_modes.yml", variables, check=True)
        queries = [entry for entry in primary.queries if entry["method"] != "GET"]
        app = primary.get_application("argocd", "acm-one")
    finally:
        primary.close()

    assert [entry["query"] for entry in queries] == ["dryRun=All"], queries
    assert app["spec"]["syncPolicy"]["automated"], "a server-side dry run must store nothing"
    assert completed.returncode == 0, _output(completed)


def _compactor_variables(tmp_path: Path, api: FakeR302API) -> dict:
    # The shipped execute-mode harness of the R3-02 compactor tests (mode and the discovered
    # compactor are pinned in the playbook itself).
    kubeconfig = tmp_path / "r3-02-primary.kubeconfig"
    write_kubeconfig(kubeconfig, context="r3-02-primary", server=api.url, token="fixture-token")
    return {"r3_02_kubeconfig": str(kubeconfig)}


def test_compactor_scale_under_native_check_mode_does_not_wait_for_pods_that_never_stop(tmp_path):
    api = FakeR302API(pods_follow_replicas=True, honor_dry_run=True)
    try:
        variables = _compactor_variables(tmp_path, api)
        completed = _run(tmp_path, f"{HARNESS}/run_r3_02_primary_prep.yml", variables, check=True, fast_sleep=True)
        writes = api.writes
        replicas = api.statefulset_replicas
    finally:
        api.close()

    assert writes == [], "k8s_scale sends no scale request under --check"
    assert replicas == 1
    assert completed.returncode == 0, _output(completed)


def test_compactor_fake_models_convergence_in_execute(tmp_path):
    """The fake is faithful: in execute the pods go away once the scale lands."""
    api = FakeR302API(pods_follow_replicas=True, honor_dry_run=True)
    try:
        variables = _compactor_variables(tmp_path, api)
        completed = _run(tmp_path, f"{HARNESS}/run_r3_02_primary_prep.yml", variables, fast_sleep=True)
        replicas = api.statefulset_replicas
    finally:
        api.close()

    assert completed.returncode == 0, _output(completed)
    assert replicas == 0


def test_rbac_kubeconfig_generation_under_native_check_mode_writes_nothing_and_does_not_fail(tmp_path):
    output_dir = tmp_path / "generated-kubeconfigs"
    variables = {
        "acm_switchover_hubs": {"primary": {"kubeconfig": str(tmp_path / "unused"), "context": "primary-hub"}},
        "acm_switchover_execution": {"mode": "execute"},
        "acm_switchover_rbac_bootstrap": {"role": "operator", "output_dir": str(output_dir), "token_duration": "1h"},
    }
    completed = _run(tmp_path, f"{HARNESS}/run_rbac_generate_kubeconfigs.yml", variables, check=True)

    assert not output_dir.exists()
    assert completed.returncode == 0, _output(completed)
    assert "RBAC_KUBECONFIG_GENERATION=done" in _output(completed)
