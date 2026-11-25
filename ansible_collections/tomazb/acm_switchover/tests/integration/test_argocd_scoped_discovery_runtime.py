"""Executable Argo CD discovery-result contract tests."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.tests.integration.argocd_fake_api import (
    FakeArgoCDHub,
    application,
    write_kubeconfig,
)

_DISCOVERY_PLAYBOOK = "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/argocd_discovery_role.yml"
_DISCOVERY_FAILURE = "Argo CD discovery failed; verify controller access and input, then retry."
_APPLICATION_DISCOVERY = {"method": "GET", "path": "/apis/argoproj.io/v1alpha1"}
_APPLICATION_LIST = {"method": "GET", "path": "/apis/argoproj.io/v1alpha1/applications"}


def _run_unscoped_role(tmp_path, server, *, advisory, namespace=None):
    kubeconfig = tmp_path / "protected-discovery-kubeconfig"
    write_kubeconfig(kubeconfig, context="primary-hub", server=server)
    return _run_playbook(
        tmp_path,
        _DISCOVERY_PLAYBOOK,
        {
            "acm_switchover_hubs": {"primary": {"kubeconfig": str(kubeconfig), "context": "primary-hub"}},
            "acm_switchover_execution": {"mode": "execute"},
            "acm_switchover_argocd_mode_override": "discover" if advisory else "pause",
            "acm_switchover_argocd_advisory": advisory,
            **({"acm_switchover_argocd": {"namespace": namespace}} if namespace else {}),
        },
    )


@pytest.mark.parametrize("advisory", [False, True], ids=["strict", "advisory"])
@pytest.mark.parametrize(
    ("status", "reason"),
    [(503, "ServiceUnavailable"), (403, "Forbidden"), (404, "NotFound"), (200, None)],
    ids=["unavailable", "forbidden", "advertised-version-404", "malformed"],
)
def test_unscoped_application_discovery_error_fails_closed(tmp_path, advisory, status, reason):
    response = {"message": "seeded-secret-token"}
    if reason is not None:
        response.update({"apiVersion": "v1", "kind": "Status", "status": "Failure", "reason": reason, "code": status})
    hub = FakeArgoCDHub(
        cluster_uid="primary-uid",
        applications=[application("argocd", "acm-app", automated=True)],
        application_discovery_response=(status, response),
    )
    try:
        completed = _run_unscoped_role(tmp_path, hub.url, advisory=advisory)
        output = completed.stdout + completed.stderr

        assert {"method": "GET", "path": "/apis/argoproj.io/v1alpha1"} in hub.requests
        assert all(request["method"] == "GET" for request in hub.requests)
        assert not any(request["path"].endswith("/applications") for request in hub.requests)
        assert "protected-discovery-kubeconfig" not in output
        assert "seeded-secret-token" not in output
        if advisory:
            assert completed.returncode == 0, output
            assert "DISCOVERY_STATUS=error INSTALLED=False DISCOVERED=0 HAS_RUN_ID=False" in output
        else:
            assert completed.returncode == 2, output
            assert _DISCOVERY_FAILURE in output
        assert "Argo CD not detected" not in output
    finally:
        hub.close()


@pytest.mark.parametrize("advisory", [False, True], ids=["strict", "advisory"])
@pytest.mark.parametrize("group_version_available", [False, True], ids=["missing-version", "missing-kind"])
def test_unscoped_missing_application_api_is_absent(tmp_path, advisory, group_version_available):
    hub = FakeArgoCDHub(
        cluster_uid="primary-uid",
        applications=[],
        application_api_available=group_version_available,
        application_discovery_response=(
            (
                200,
                {
                    "kind": "APIResourceList",
                    "groupVersion": "argoproj.io/v1alpha1",
                    "resources": [{"name": "appprojects", "kind": "AppProject", "namespaced": True}],
                },
            )
            if group_version_available
            else None
        ),
    )
    try:
        completed = _run_unscoped_role(tmp_path, hub.url, advisory=advisory)
        output = completed.stdout + completed.stderr

        assert completed.returncode == 0, output
        assert "DISCOVERY_STATUS=absent INSTALLED=False DISCOVERED=0 HAS_RUN_ID=False" in output
        expected = [_APPLICATION_DISCOVERY]
        if not group_version_available:
            expected.append({"method": "GET", "path": "/apis"})
        assert hub.requests == expected
        assert "protected-discovery-kubeconfig" not in output
    finally:
        hub.close()


@pytest.mark.parametrize("advisory", [False, True], ids=["strict", "advisory"])
@pytest.mark.parametrize("populated", [False, True], ids=["empty", "populated"])
def test_unscoped_application_list_is_present(tmp_path, advisory, populated):
    apps = [application("team-gitops", "acm-app.example", automated=False)] if populated else []
    hub = FakeArgoCDHub(cluster_uid="primary-uid", applications=apps)
    try:
        completed = _run_unscoped_role(tmp_path, hub.url, advisory=advisory)
        output = completed.stdout + completed.stderr

        assert completed.returncode == 0, output
        assert f"DISCOVERY_STATUS=ok INSTALLED=True DISCOVERED={len(apps)} HAS_RUN_ID={not advisory}" in output
        assert hub.requests.index(_APPLICATION_DISCOVERY) < hub.requests.index(_APPLICATION_LIST)
        assert all(request["method"] == "GET" for request in hub.requests)
    finally:
        hub.close()


@pytest.mark.parametrize("advisory", [False, True], ids=["strict", "advisory"])
@pytest.mark.parametrize("bad_identity", [{"namespace": "argocd"}, {"name": "app", "namespace": " "}])
def test_unscoped_application_list_rejects_malformed_member(tmp_path, advisory, bad_identity):
    valid = application("argocd", "acm-app", automated=True)
    malformed = {
        **application("argocd", "other-app", automated=True),
        "metadata": {**bad_identity, "annotations": {"fixture": "seeded-secret-token"}},
    }
    hub = FakeArgoCDHub(
        cluster_uid="primary-uid",
        applications=[valid],
        cluster_application_list_response=(
            200,
            {
                "apiVersion": "argoproj.io/v1alpha1",
                "kind": "ApplicationList",
                "metadata": {"resourceVersion": "1"},
                "items": [valid, malformed],
            },
        ),
    )
    try:
        completed = _run_unscoped_role(tmp_path, hub.url, advisory=advisory)
        output = completed.stdout + completed.stderr

        assert hub.requests[:2] == [_APPLICATION_DISCOVERY, _APPLICATION_LIST], output
        assert hub.writes == []
        assert "seeded-secret-token" not in output
        if advisory:
            assert completed.returncode == 0, output
            assert "DISCOVERY_STATUS=error INSTALLED=False DISCOVERED=0 HAS_RUN_ID=False" in output
        else:
            assert completed.returncode == 2, output
            assert _DISCOVERY_FAILURE in output
            assert "Generate run_id if not provided" not in output
    finally:
        hub.close()


def test_advisory_discovery_preserves_explicit_namespace(tmp_path):
    hub = FakeArgoCDHub(
        cluster_uid="primary-uid",
        applications=[
            application("argocd", "acm-app", automated=True),
            application("other", "acm-app", automated=True),
        ],
    )
    try:
        completed = _run_unscoped_role(tmp_path, hub.url, advisory=True, namespace="argocd")
        output = completed.stdout + completed.stderr

        assert completed.returncode == 0, output
        assert "DISCOVERY_STATUS=ok INSTALLED=True DISCOVERED=1 HAS_RUN_ID=False" in output
        assert hub.requests == [
            _APPLICATION_DISCOVERY,
            {
                "method": "GET",
                "path": "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications",
            },
        ]
    finally:
        hub.close()


@pytest.mark.parametrize("advisory", [False, True], ids=["strict", "advisory"])
@pytest.mark.parametrize(
    "failure",
    [
        "authorization",
        "transport",
        "malformed",
        "list_400",
        "list_404",
        "list_missing_items",
    ],
)
def test_unscoped_discovery_errors_remain_sanitized(tmp_path, advisory, failure):
    protected_detail = "seeded-secret-token: could not find the requested resource"
    if failure == "transport":
        # A bound, non-listening loopback port fails the connection without a race
        # or reaching a real cluster.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            server = f"http://127.0.0.1:{listener.getsockname()[1]}"
            completed = _run_unscoped_role(tmp_path, server, advisory=advisory)
    else:
        http_errors = {
            "authorization": (403, "Forbidden"),
            "list_400": (400, "BadRequest"),
            "list_404": (404, "NotFound"),
        }
        if failure in http_errors:
            status, reason = http_errors[failure]
            response = (
                status,
                {
                    "apiVersion": "v1",
                    "kind": "Status",
                    "status": "Failure",
                    "reason": reason,
                    "message": protected_detail,
                    "code": status,
                },
            )
        elif failure == "list_missing_items":
            response = (
                200,
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "ApplicationList",
                    "metadata": {"resourceVersion": "1"},
                    "unexpected": protected_detail,
                },
            )
        else:
            response = (200, {"unexpected": protected_detail})
        hub = FakeArgoCDHub(
            cluster_uid="primary-uid",
            applications=[],
            cluster_application_list_response=response,
        )
        try:
            completed = _run_unscoped_role(tmp_path, hub.url, advisory=advisory)
            assert hub.requests.index(_APPLICATION_DISCOVERY) < hub.requests.index(_APPLICATION_LIST)
            assert all(request["method"] == "GET" for request in hub.requests)
        finally:
            hub.close()

    output = completed.stdout + completed.stderr
    assert "protected-discovery-kubeconfig" not in output
    assert "seeded-secret-token" not in output
    if advisory:
        assert completed.returncode == 0, output
        assert "DISCOVERY_STATUS=error INSTALLED=False DISCOVERED=0 HAS_RUN_ID=False" in output
    else:
        assert completed.returncode == 2, output
        assert _DISCOVERY_FAILURE in output
    assert "Argo CD not detected" not in output


@pytest.mark.parametrize(
    "overrides",
    [{"kind": "AppProject"}, {"namespaced": False}, {"namespaced": None}],
    ids=["wrong-kind", "cluster-scoped", "missing-scope"],
)
def test_unscoped_discovery_refuses_inconsistent_application_route(tmp_path, overrides):
    hub = FakeArgoCDHub(
        cluster_uid="primary-uid",
        applications=[],
        application_discovery_response=(
            200,
            {
                "kind": "APIResourceList",
                "groupVersion": "argoproj.io/v1alpha1",
                "resources": [
                    {
                        "name": "applications",
                        "kind": "Application",
                        "namespaced": True,
                        **overrides,
                    }
                ],
            },
        ),
    )
    try:
        completed = _run_unscoped_role(tmp_path, hub.url, advisory=False)
        output = completed.stdout + completed.stderr

        assert completed.returncode == 2, output
        assert _DISCOVERY_FAILURE in output
        assert hub.requests == [_APPLICATION_DISCOVERY]
    finally:
        hub.close()


def _present(namespace: str, *, resources: object | None = None, **extra) -> dict:
    result = {
        "changed": False,
        "failed": False,
        "skipped": False,
        "unreachable": False,
        "api_found": True,
        "resources": resources if resources is not None else [],
        "item": namespace,
    }
    result.update(extra)
    return result


def _absent(namespace: str, *, include_resources: bool = True, resources=None, **extra) -> dict:
    result = {
        "changed": False,
        "failed": False,
        "skipped": False,
        "unreachable": False,
        "api_found": False,
        "item": namespace,
    }
    if include_resources:
        result["resources"] = [] if resources is None else resources
    result.update(extra)
    return result


def _envelope(results: list[dict], **extra) -> dict:
    envelope = {
        "changed": False,
        "failed": False,
        "skipped": False,
        "unreachable": False,
        "results": results,
    }
    envelope.update(extra)
    return envelope


@pytest.mark.parametrize(
    ("query", "namespaces", "expected_status", "expected_count"),
    [
        (
            _envelope(
                [
                    _present("argocd", resources=[{"metadata": {"name": "one"}}]),
                    _present("team-gitops", resources=[{"metadata": {"name": "two"}}]),
                ]
            ),
            ["argocd", "team-gitops"],
            "ok",
            2,
        ),
        (
            _envelope(
                [
                    _present(
                        "argocd",
                        resources=[{"metadata": {"name": "one"}}],
                        documented_optional_metadata={"accepted": True},
                    )
                ],
                documented_optional_metadata={"accepted": True},
            ),
            ["argocd"],
            "ok",
            1,
        ),
        (
            _envelope([_absent("argocd"), _absent("team-gitops", include_resources=False)]),
            ["argocd", "team-gitops"],
            "absent",
            0,
        ),
        ({}, ["argocd"], "error", 0),
        ("not-a-mapping", ["argocd"], "error", 0),
        ({"results": "not-a-list"}, ["argocd"], "error", 0),
        (_envelope([]), ["argocd"], "error", 0),
        (_envelope([_present("argocd"), _present("team-gitops")]), ["argocd"], "error", 0),
        (_envelope([_present("wrong")]), ["argocd"], "error", 0),
        (_envelope([_present("argocd", failed=True)]), ["argocd"], "error", 0),
        (_envelope([_present("argocd", failed=True, msg="protected failure")]), ["argocd"], "error", 0),
        (_envelope([_present("argocd", skipped=True)]), ["argocd"], "error", 0),
        (_envelope([_present("argocd", unreachable=True)]), ["argocd"], "error", 0),
        (_envelope([{"item": "argocd", "api_found": True}]), ["argocd"], "error", 0),
        (_envelope([{"item": "argocd", "resources": []}]), ["argocd"], "error", 0),
        (_envelope([_present("argocd", resources={"not": "a-list"})]), ["argocd"], "error", 0),
        (_envelope([_present("argocd"), _absent("team-gitops")]), ["argocd", "team-gitops"], "error", 0),
        (_envelope([_present("argocd"), _present("team-gitops", failed=True)]), ["argocd", "team-gitops"], "error", 0),
        (_envelope([_absent("argocd", resources=[{"contradictory": True}])]), ["argocd"], "error", 0),
        (_envelope([_absent("argocd", resources={"contradictory": True})]), ["argocd"], "error", 0),
        (_envelope([_absent("argocd", failed=True)]), ["argocd"], "error", 0),
        (_envelope([_absent("argocd", api_found=1)]), ["argocd"], "error", 0),
        (_envelope([_present("argocd", api_found=1)]), ["argocd"], "error", 0),
        (_envelope([_present("argocd", failed=1)]), ["argocd"], "error", 0),
        (_envelope([_present("argocd")], failed=True), ["argocd"], "error", 0),
        (_envelope([_present("argocd")], skipped=True), ["argocd"], "error", 0),
        (_envelope([_present("argocd")], unreachable=True), ["argocd"], "error", 0),
    ],
    ids=[
        "multiple-present",
        "unknown-optional-fields-accepted",
        "all-absent",
        "missing-results",
        "top-level-not-mapping",
        "results-not-list",
        "too-few-results",
        "too-many-results",
        "namespace-position-mismatch",
        "failed-without-msg",
        "failed-with-msg",
        "skipped",
        "unreachable",
        "missing-resources",
        "missing-api-found",
        "resources-not-list",
        "mixed-present-absent",
        "mixed-present-failed",
        "absent-non-empty-resources",
        "absent-non-list-resources",
        "absent-failed",
        "absent-non-boolean-api-found",
        "present-non-boolean-api-found",
        "non-boolean-failure-flag",
        "top-level-failed",
        "top-level-skipped",
        "top-level-unreachable",
    ],
)
def test_scoped_result_contract_fails_closed(
    run_argocd_scoped_validation,
    query,
    namespaces,
    expected_status,
    expected_count,
):
    completed = run_argocd_scoped_validation(query=query, namespaces=namespaces)
    output = completed.stdout + completed.stderr

    assert completed.returncode == 0, output
    assert f"VALIDATION_STATUS={expected_status}" in output
    assert f"RESOURCE_COUNT={expected_count}" in output
    assert f"DOWNSTREAM_ALLOWED={expected_status == 'ok'}" in output
    assert "protected failure" not in output


@pytest.mark.parametrize(
    ("mode", "advisory", "expected_returncode"),
    [
        ("pause", False, 2),
        # Resume with a known run_id must fail closed when discovery cannot see
        # Applications (ADR-0001): a paused app with a matching marker exists,
        # so exiting 0 would silently drop an unresolved resume obligation.
        ("resume", True, 2),
    ],
)
def test_mixed_live_namespace_result_never_reaches_pause_or_resume_and_stays_sanitized(
    tmp_path,
    mode,
    advisory,
    expected_returncode,
):
    protected_detail = "seeded-secret-token /sensitive/kubeconfig"
    run_id = "expected-run"
    primary = FakeArgoCDHub(
        cluster_uid="primary-uid",
        applications=[
            application(
                "argocd",
                "partial-result-app",
                automated=mode == "pause",
                run_id=run_id if mode == "resume" else None,
            )
        ],
        namespace_list_failures={"team-gitops": protected_detail},
    )
    secondary = FakeArgoCDHub(cluster_uid="secondary-uid", applications=[])
    try:
        hubs = _write_hub_inputs(tmp_path, primary, secondary)
        completed = _run_playbook(
            tmp_path,
            _DISCOVERY_PLAYBOOK,
            {
                "acm_switchover_hubs": hubs,
                "acm_switchover_execution": {"mode": "execute"},
                "acm_switchover_argocd": {
                    "mode": mode,
                    "run_id": run_id,
                },
                "acm_switchover_argocd_advisory": advisory,
                "acm_switchover_argocd_discovery_namespaces": {
                    "primary": ["argocd", "team-gitops"],
                },
            },
        )
        output = completed.stdout + completed.stderr

        assert completed.returncode == expected_returncode, output
        assert protected_detail not in output
        assert "seeded-secret-token" not in output
        assert "/sensitive/kubeconfig" not in output
        assert primary.patches == []
        if mode == "resume":
            # The obligation gate aborts the play before the post-role debug
            # task, so the sanitized discovery status surfaces in its message.
            assert "Cannot verify Argo CD resume obligations" in output, output
            assert "reported status" in output and "'error'" in output, output
        else:
            assert "Argo CD discovery failed; verify controller access and input, then retry." in output
    finally:
        primary.close()
        secondary.close()


def test_explicit_empty_namespace_fails_before_discovery_or_patch(tmp_path):
    primary = FakeArgoCDHub(
        cluster_uid="primary-uid",
        applications=[application("argocd", "must-not-patch", automated=True)],
    )
    secondary = FakeArgoCDHub(cluster_uid="secondary-uid", applications=[])
    try:
        hubs = _write_hub_inputs(tmp_path, primary, secondary)
        completed = _run_playbook(
            tmp_path,
            _DISCOVERY_PLAYBOOK,
            {
                "acm_switchover_hubs": hubs,
                "acm_switchover_execution": {"mode": "execute"},
                "acm_switchover_argocd": {
                    "mode": "pause",
                    "run_id": "expected-run",
                    "namespace": "",
                },
            },
        )
        output = completed.stdout + completed.stderr

        assert completed.returncode == 2, output
        assert "Explicit Argo CD discovery namespace must be a non-empty namespace name." in output
        assert primary.patches == []
    finally:
        primary.close()
        secondary.close()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _ansible_environment(tmp_path: Path) -> dict[str, str]:
    from ansible_collections.tomazb.acm_switchover.tests.conftest import _ansible_env

    return _ansible_env(_repo_root(), tmp_path)


def _write_hub_inputs(tmp_path: Path, primary: FakeArgoCDHub, secondary: FakeArgoCDHub) -> dict:
    primary_kubeconfig = tmp_path / "primary.kubeconfig"
    secondary_kubeconfig = tmp_path / "secondary.kubeconfig"
    write_kubeconfig(primary_kubeconfig, context="primary-hub", server=primary.url)
    write_kubeconfig(secondary_kubeconfig, context="secondary-hub", server=secondary.url)
    return {
        "primary": {
            "kubeconfig": str(primary_kubeconfig),
            "context": "primary-hub",
            "cluster_uid": primary.cluster_uid,
        },
        "secondary": {
            "kubeconfig": str(secondary_kubeconfig),
            "context": "secondary-hub",
            "cluster_uid": secondary.cluster_uid,
        },
    }


def _run_playbook(tmp_path: Path, playbook: str, variables: dict) -> subprocess.CompletedProcess[str]:
    vars_file = tmp_path / f"{Path(playbook).stem}-vars.yml"
    playbook_variables = {
        **variables,
        "ansible_python_interpreter": sys.executable,
    }
    vars_file.write_text(yaml.safe_dump(playbook_variables, sort_keys=False), encoding="utf-8")
    return subprocess.run(
        [
            "ansible-playbook",
            playbook,
            "-i",
            "ansible_collections/tomazb/acm_switchover/examples/inventory.yml",
            "-e",
            f"@{vars_file}",
        ],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
        env=_ansible_environment(tmp_path),
        timeout=300,
    )


def _primary_prep_variables(hubs: dict, checkpoint_path: Path, *, acm_version: str) -> dict:
    return {
        "acm_switchover_hubs": hubs,
        "acm_switchover_operation": {
            "method": "passive",
            "activation_method": "patch",
            "restore_only": False,
            "old_hub_action": "secondary",
        },
        "acm_switchover_execution": {
            "mode": "execute",
            "run_id": "workflow-run",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_path),
            },
        },
        "acm_switchover_features": {
            "argocd": {"manage": True, "resume_on_failure": False},
            "skip_observability_checks": True,
            "manage_auto_import_strategy": False,
        },
        "acm_primary_prep_mch_info": {
            "resources": [{"status": {"currentVersion": acm_version}}],
        },
        "acm_primary_prep_backup_schedules_info": {"resources": []},
    }


def test_primary_prep_retry_rehydrates_scoped_discovery_and_repauses_reconciled_apps(tmp_path):
    primary = FakeArgoCDHub(
        cluster_uid="primary-uid",
        applications=[
            application("argocd", "acm-one", automated=True),
            application("team-gitops", "acm-two", automated=True),
            application("argocd", "acm-stays-paused", automated=True),
            application("argocd", "unrelated", automated=True, acm_touching=False),
        ],
    )
    secondary = FakeArgoCDHub(cluster_uid="secondary-uid", applications=[])
    try:
        hubs = _write_hub_inputs(tmp_path, primary, secondary)
        checkpoint_path = tmp_path / "primary-prep-checkpoint.json"
        playbook = (
            "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/" "argocd_primary_prep_retry.yml"
        )

        first = _run_playbook(
            tmp_path,
            playbook,
            _primary_prep_variables(hubs, checkpoint_path, acm_version="invalid-version"),
        )
        assert first.returncode != 0
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        run_id = checkpoint["operational_data"]["argocd_run_id"]
        assert run_id
        assert checkpoint["operational_data"]["argocd_discovery_namespaces"]["primary"] == [
            "argocd",
            "team-gitops",
        ]
        first_patch_names = [entry["name"] for entry in primary.patches if entry["changed"]]
        assert sorted(first_patch_names) == ["acm-one", "acm-stays-paused", "acm-two"]

        primary.set_automated("argocd", "acm-one", True)
        primary.set_automated("team-gitops", "acm-two", True)
        patches_before_retry = len(primary.patches)

        retry = _run_playbook(
            tmp_path,
            playbook,
            _primary_prep_variables(hubs, checkpoint_path, acm_version="2.14.3"),
        )
        output = retry.stdout + retry.stderr
        assert retry.returncode == 0, output
        assert "PRIMARY_PREP_PAUSED=2" in output
        assert "PRIMARY_NAMESPACES=2" in output

        retry_patches = primary.patches[patches_before_retry:]
        assert [(entry["namespace"], entry["name"]) for entry in retry_patches if entry["changed"]] == [
            ("argocd", "acm-one"),
            ("team-gitops", "acm-two"),
        ]
        assert all(entry["name"] not in {"acm-stays-paused", "unrelated"} for entry in retry_patches)
        assert primary.get_application("argocd", "unrelated")["spec"]["syncPolicy"]["automated"]
    finally:
        primary.close()
        secondary.close()


def _checkpoint(run_id: str, hubs: dict) -> dict:
    return {
        "schema_version": "2.0",
        "phase": "primary_prep",
        "completed_phases": ["primary_prep"],
        "operational_data": {
            "argocd_run_id": run_id,
            "argocd_discovery_namespaces": {
                "primary": ["argocd"],
                "secondary": ["team-gitops"],
            },
        },
        "operation_identity": {
            "primary_context": hubs["primary"]["context"],
            "secondary_context": hubs["secondary"]["context"],
            "primary_cluster_uid": hubs["primary"]["cluster_uid"],
            "secondary_cluster_uid": hubs["secondary"]["cluster_uid"],
            "method": "passive",
            "activation_method": "patch",
            "restore_only": False,
            "old_hub_action": "secondary",
            "collection_version": "",
        },
        "errors": [],
        "report_refs": [],
        "created_at": "2026-07-26T00:00:00+00:00",
        "updated_at": "2026-07-26T00:00:00+00:00",
    }


def test_standalone_resume_discovers_both_hubs_and_reports_each_changed_patch_once(tmp_path):
    run_id = "expected-run"
    primary = FakeArgoCDHub(
        cluster_uid="primary-uid",
        applications=[
            application("argocd", "primary-owned", automated=False, run_id=run_id),
            application("argocd", "primary-foreign", automated=False, run_id="foreign-run"),
        ],
    )
    secondary = FakeArgoCDHub(
        cluster_uid="secondary-uid",
        applications=[
            application("team-gitops", "secondary-owned", automated=False, run_id=run_id),
            application("team-gitops", "secondary-foreign", automated=False, run_id="foreign-run"),
        ],
    )
    try:
        hubs = _write_hub_inputs(tmp_path, primary, secondary)
        checkpoint_path = tmp_path / "standalone-resume-checkpoint.json"
        checkpoint_path.write_text(json.dumps(_checkpoint(run_id, hubs)), encoding="utf-8")
        variables = {
            "acm_switchover_hubs": hubs,
            "acm_switchover_operation": {
                "method": "passive",
                "activation_method": "patch",
                "restore_only": False,
                "old_hub_action": "secondary",
            },
            "acm_switchover_execution": {
                "mode": "execute",
                "checkpoint": {
                    "enabled": True,
                    "backend": "file",
                    "path": str(checkpoint_path),
                },
            },
        }

        completed = _run_playbook(
            tmp_path,
            "ansible_collections/tomazb/acm_switchover/playbooks/argocd_resume.yml",
            variables,
        )
        output = completed.stdout + completed.stderr
        assert completed.returncode == 0, output
        assert "Argo CD standalone resume summary: restored=2 primary=1 secondary=1" in output
        assert [(entry["namespace"], entry["name"]) for entry in primary.patches] == [("argocd", "primary-owned")]
        assert [(entry["namespace"], entry["name"]) for entry in secondary.patches] == [
            ("team-gitops", "secondary-owned")
        ]
        assert "automated" in primary.get_application("argocd", "primary-owned")["spec"]["syncPolicy"]
        assert "automated" in secondary.get_application("team-gitops", "secondary-owned")["spec"]["syncPolicy"]
        assert (
            primary.get_application("argocd", "primary-foreign")["metadata"]["annotations"][
                "acm-switchover.argoproj.io/paused-by"
            ]
            == "foreign-run"
        )
        assert (
            secondary.get_application("team-gitops", "secondary-foreign")["metadata"]["annotations"][
                "acm-switchover.argoproj.io/paused-by"
            ]
            == "foreign-run"
        )
    finally:
        primary.close()
        secondary.close()
