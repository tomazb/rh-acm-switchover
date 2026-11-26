"""Unscoped Application discovery in both runtimes against the same loopback API."""

import logging
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from kubernetes import config
from kubernetes.dynamic import DynamicClient

from acm_switchover import _run_phase_preflight
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.k8s_read import strict_optional_namespaced_list
from ansible_collections.tomazb.acm_switchover.tests.integration.argocd_fake_api import (
    FakeArgoCDHub,
    application,
    write_kubeconfig,
)
from lib import argocd
from lib.argocd import list_argocd_applications
from lib.argocd_register import ArgocdPauseRegister
from lib.constants import (
    STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES,
    STATE_KEY_ARGOCD_PAUSED_APPS,
    STATE_KEY_ARGOCD_RUN_ID,
)
from lib.exceptions import ValidationError
from lib.kube_client import KubeClient
from tests.argocd_register_helpers import _make_real_state

_GROUP_VERSION = "argoproj.io/v1alpha1"
_DISCOVERY = f"/apis/{_GROUP_VERSION}"
_LIST = f"{_DISCOVERY}/applications"
_SECRET = "private-api-diagnostic"


def _status(code):
    return code, {"kind": "Status", "apiVersion": "v1", "code": code, "message": _SECRET}


def _discovery(*resources):
    return 200, {"kind": "APIResourceList", "groupVersion": _GROUP_VERSION, "resources": list(resources)}


def _inventory(**fields):
    return {"kind": "ApplicationList", "apiVersion": _GROUP_VERSION, "metadata": {"resourceVersion": "1"}, **fields}


@pytest.mark.parametrize(
    ("hub_options", "applications", "expected", "paths"),
    [
        pytest.param({"application_api_available": False}, [], "absent", [_DISCOVERY, "/apis"], id="missing-version"),
        pytest.param({"application_discovery_response": _discovery()}, [], "absent", [_DISCOVERY], id="missing-kind"),
        pytest.param(
            {"application_discovery_response": _status(404)}, [], "error", [_DISCOVERY, "/apis"], id="advertised-404"
        ),
        pytest.param({"application_discovery_response": _status(503)}, [], "error", [_DISCOVERY], id="discovery-503"),
        pytest.param({"application_discovery_response": _status(403)}, [], "error", [_DISCOVERY], id="discovery-403"),
        pytest.param(
            {"application_discovery_response": (200, {"message": _SECRET})},
            [],
            "error",
            [_DISCOVERY],
            id="malformed-discovery",
        ),
        pytest.param(
            {
                "application_discovery_response": _discovery(
                    {"name": "applications", "kind": "Application", "namespaced": False}
                )
            },
            [],
            "error",
            [_DISCOVERY],
            id="wrong-scope",
        ),
        pytest.param(
            {"cluster_application_list_response": (400, _inventory(items=[]))},
            [],
            "error",
            [_DISCOVERY, _LIST],
            id="list-400-with-list-body",
        ),
        pytest.param(
            {"cluster_application_list_response": _status(403)}, [], "error", [_DISCOVERY, _LIST], id="list-403"
        ),
        pytest.param(
            {"cluster_application_list_response": _status(404)}, [], "error", [_DISCOVERY, _LIST], id="list-404"
        ),
        pytest.param(
            {"cluster_application_list_response": (200, _inventory(message=_SECRET))},
            [],
            "error",
            [_DISCOVERY, _LIST],
            id="missing-items",
        ),
        pytest.param(
            {"cluster_application_list_response": (200, {**_inventory(items=[]), "kind": "AppProjectList"})},
            [],
            "error",
            [_DISCOVERY, _LIST],
            id="wrong-list-kind",
        ),
        pytest.param({}, [], "ok", [_DISCOVERY, _LIST], id="empty"),
        pytest.param({}, [application("argocd", "acm-app", automated=True)], "ok", [_DISCOVERY, _LIST], id="populated"),
    ],
)
def test_cluster_wide_application_discovery(tmp_path, monkeypatch, caplog, hub_options, applications, expected, paths):
    hub = FakeArgoCDHub(cluster_uid="primary-uid", applications=applications, **hub_options)
    kubeconfig = tmp_path / "fixture-kubeconfig"
    write_kubeconfig(kubeconfig, context="fixture-hub", server=hub.url)
    monkeypatch.setattr(config.kube_config, "KUBE_CONFIG_DEFAULT_LOCATION", str(kubeconfig))
    try:
        python_client = KubeClient(context="fixture-hub")
        with caplog.at_level(logging.DEBUG, logger="acm_switchover"):
            if expected == "error":
                with pytest.raises(RuntimeError, match="Argo CD Application discovery failed") as exc:
                    list_argocd_applications(python_client)
                assert _SECRET not in str(exc.value)
            else:
                result = list_argocd_applications(python_client)
                if expected == "absent":
                    assert result is None
                else:
                    assert result is not None
                    assert [app["metadata"]["name"] for app in result] == [
                        app["metadata"]["name"] for app in applications
                    ]
        python_requests = hub.requests

        with config.new_client_from_config(config_file=str(kubeconfig), persist_config=False) as sdk:
            collection_client = SimpleNamespace(client=DynamicClient(sdk, discoverer=lambda *_: None))
            status, items, _ = strict_optional_namespaced_list(
                collection_client, api_version=_GROUP_VERSION, kind="Application", resource_name="applications"
            )
        assert status == ("kind_not_served" if expected == "absent" else expected)
        assert [app["metadata"]["name"] for app in items] == [app["metadata"]["name"] for app in applications]
        expected_requests = [{"method": "GET", "path": path} for path in paths]
        assert python_requests == expected_requests
        assert hub.requests[len(python_requests) :] == expected_requests
        assert _SECRET not in caplog.text
    finally:
        hub.close()


@pytest.mark.parametrize("advisory", [False, True], ids=["managed-pause", "automatic-preflight"])
@pytest.mark.parametrize(
    "case", ["discovery-503", "verified-absence", "empty", "digit-namespace", "missing-name", "blank-namespace"]
)
def test_unscoped_application_inventory_at_python_entrypoints(tmp_path, monkeypatch, caplog, advisory, case):
    hub_options = {}
    expected_paths = [_DISCOVERY, _LIST]
    error = case in ("discovery-503", "missing-name", "blank-namespace")
    if case == "discovery-503":
        # Both CRD object routes return 404, but the Application discovery route returns 503.
        hub_options["application_discovery_response"] = _status(503)
        expected_paths = [_DISCOVERY]
    elif case == "verified-absence":
        hub_options["application_api_available"] = False
        expected_paths = [_DISCOVERY, "/apis"]
    elif case == "digit-namespace":
        app = application("9-team", "acm-app.example", automated=False)
        hub_options["cluster_application_list_response"] = (200, _inventory(items=[app]))
    elif case in ("missing-name", "blank-namespace"):
        valid = application("argocd", "acm-app", automated=True)
        malformed = application("argocd", "other-app", automated=True)
        if case == "missing-name":
            del malformed["metadata"]["name"]
        else:
            malformed["metadata"]["namespace"] = " "
        malformed["metadata"]["annotations"]["fixture"] = _SECRET
        hub_options.update(
            application_crd_present=True,
            cluster_application_list_response=(200, _inventory(items=[valid, malformed])),
        )

    hub = FakeArgoCDHub(cluster_uid="secondary-uid", applications=[], **hub_options)
    kubeconfig = tmp_path / "fixture-kubeconfig"
    write_kubeconfig(kubeconfig, context="fixture-hub", server=hub.url)
    monkeypatch.setattr(config.kube_config, "KUBE_CONFIG_DEFAULT_LOCATION", str(kubeconfig))
    state = _make_real_state(tmp_path)
    run_id_or_new = Mock(wraps=argocd.run_id_or_new)
    monkeypatch.setattr(argocd, "run_id_or_new", run_id_or_new)
    logger = logging.getLogger("acm_switchover")
    try:
        client = KubeClient(context="fixture-hub")
        with caplog.at_level(logging.INFO, logger=logger.name):
            if advisory:
                validator = Mock()
                validator.validate_all.return_value = (
                    True,
                    {"secondary_version": "2.14.0", "secondary_observability_detected": False},
                )
                validator.reporter.results = []
                validator.reporter.critical_failures.return_value = []
                monkeypatch.setattr("acm_switchover.PreflightValidator", lambda *_args, **_kwargs: validator)
                args = Namespace(
                    method="passive",
                    skip_rbac_validation=True,
                    validate_only=True,
                    restore_only=True,
                )
                assert _run_phase_preflight(args, state, None, client, logger)
                expected_message = {
                    "verified-absence": "Application API not served; check skipped",
                    "empty": "0 ACM-touching Application(s) detected",
                    "digit-namespace": "1 ACM-touching Application(s) detected",
                }.get(case, "Unable to complete Argo CD check")
                assert expected_message in caplog.text
                assert ("Unable to complete Argo CD check" in caplog.text) is error
            elif error:
                with pytest.raises(RuntimeError, match="Argo CD Application discovery failed") as exc:
                    ArgocdPauseRegister(state).pause_hubs([(client, "secondary")])
                assert _SECRET not in str(exc.value)
            else:
                summary = ArgocdPauseRegister(state).pause_hubs([(client, "secondary")])
                assert summary.applications_crd_visible is (case in ("empty", "digit-namespace"))
                assert summary.newly_paused == 0
                assert bool(summary.run_id) is (case in ("empty", "digit-namespace"))

        assert hub.requests == [{"method": "GET", "path": path} for path in expected_paths]
        assert _SECRET not in caplog.text
        if advisory or case not in ("empty", "digit-namespace"):
            run_id_or_new.assert_not_called()
            assert state._get_config(STATE_KEY_ARGOCD_RUN_ID) is None
        if error:
            assert not state._get_config(STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES)
    finally:
        hub.close()


def test_digit_leading_namespace_pause_scoped_retry_and_resume(tmp_path, monkeypatch):
    app = application("9-team", "acm-app.example", automated=True)
    hub = FakeArgoCDHub(
        cluster_uid="secondary-uid",
        applications=[app],
        application_crd_present=True,
    )
    kubeconfig = tmp_path / "fixture-kubeconfig"
    write_kubeconfig(kubeconfig, context="fixture-hub", server=hub.url)
    monkeypatch.setattr(config.kube_config, "KUBE_CONFIG_DEFAULT_LOCATION", str(kubeconfig))
    state = _make_real_state(tmp_path)
    try:
        client = KubeClient(context="fixture-hub")

        pause_summary = ArgocdPauseRegister(state).pause_hubs([(client, "secondary")])
        assert pause_summary.newly_paused == 1
        assert pause_summary.failed == 0
        assert pause_summary.blocked == 0
        assert pause_summary.run_id
        assert len(hub.patches) == 1
        assert hub.patches[0]["namespace"] == "9-team"
        assert hub.patches[0]["name"] == "acm-app.example"
        assert (
            hub.patches[0]["body"]["metadata"]["annotations"][argocd.ARGOCD_PAUSED_BY_ANNOTATION]
            == pause_summary.run_id
        )
        assert state._get_config(STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES) == {"secondary": ["9-team"]}
        paused_entries = state._get_config(STATE_KEY_ARGOCD_PAUSED_APPS)
        assert len(paused_entries) == 1
        assert paused_entries[0]["namespace"] == "9-team"
        assert paused_entries[0]["pause_applied"] is True

        req_start = len(hub.requests)
        patch_start = len(hub.patches)
        retry_summary = ArgocdPauseRegister(state).pause_hubs([(client, "secondary")])
        assert retry_summary.failed == 0
        assert retry_summary.newly_paused == 0
        assert retry_summary.already_paused == 1
        assert {"method": "GET", "path": f"{_DISCOVERY}/namespaces/9-team/applications"} in hub.requests[req_start:]
        assert hub.patches[patch_start:] == []

        patch_start = len(hub.patches)
        resume_summary = ArgocdPauseRegister(state).resume(None, client)
        assert resume_summary.restored == 1
        assert resume_summary.failed == 0
        assert resume_summary.remaining_in_register == 0
        assert len(hub.patches) == patch_start + 1
        resume_patch = hub.patches[-1]
        assert resume_patch["namespace"] == "9-team"
        assert resume_patch["name"] == "acm-app.example"
        assert resume_patch["body"]["spec"]["syncPolicy"] == {"automated": {"prune": True, "selfHeal": True}}
        assert resume_patch["body"]["metadata"]["annotations"][argocd.ARGOCD_PAUSED_BY_ANNOTATION] is None
    finally:
        hub.close()


@pytest.mark.parametrize(
    "namespaces",
    [
        ["bad.ns"],
        ["-bad"],
        ["Bad"],
        ["9-team-"],
        ["9-team\n"],
        ["a-team\n"],
        [""],
        ["9-team", "bad.ns"],
    ],
)
def test_invalid_namespace_rejected_before_scoped_list(tmp_path, monkeypatch, namespaces):
    hub = FakeArgoCDHub(cluster_uid="secondary-uid", applications=[])
    kubeconfig = tmp_path / "fixture-kubeconfig"
    write_kubeconfig(kubeconfig, context="fixture-hub", server=hub.url)
    monkeypatch.setattr(config.kube_config, "KUBE_CONFIG_DEFAULT_LOCATION", str(kubeconfig))
    try:
        client = KubeClient(context="fixture-hub")
        with pytest.raises(ValidationError):
            list_argocd_applications(client, namespaces=namespaces)
        assert hub.requests == []
    finally:
        hub.close()


@pytest.mark.parametrize("invalid_namespace", ["bad.ns", "-bad", "Bad", "9-team-", "9-team\n", "a-team\n", ""])
def test_invalid_namespace_rejected_before_pause_or_resume_mutation(tmp_path, monkeypatch, invalid_namespace):
    hub = FakeArgoCDHub(cluster_uid="secondary-uid", applications=[])
    kubeconfig = tmp_path / "fixture-kubeconfig"
    write_kubeconfig(kubeconfig, context="fixture-hub", server=hub.url)
    monkeypatch.setattr(config.kube_config, "KUBE_CONFIG_DEFAULT_LOCATION", str(kubeconfig))
    try:
        client = KubeClient(context="fixture-hub")
        app = application(invalid_namespace, "acm-app", automated=True)

        pause_result = argocd.pause_autosync(client, app, "run-1")
        assert pause_result.patched is False
        assert pause_result.patch_applied is False
        assert pause_result.error

        resume_result = argocd.resume_autosync(client, invalid_namespace, "acm-app", {"automated": {}}, "run-1")
        assert resume_result.restored is False
        assert resume_result.skip_reason

        assert hub.requests == []
        assert hub.patches == []
    finally:
        hub.close()


@pytest.mark.parametrize("invalid_namespace", ["bad.ns", "-bad", "Bad", "9-team-", "9-team\n", "a-team\n"])
def test_invalid_namespace_rejected_in_unscoped_discovery(tmp_path, monkeypatch, invalid_namespace):
    malformed = application(invalid_namespace, "acm-app", automated=True)
    hub = FakeArgoCDHub(
        cluster_uid="secondary-uid",
        applications=[],
        cluster_application_list_response=(200, _inventory(items=[malformed])),
    )
    kubeconfig = tmp_path / "fixture-kubeconfig"
    write_kubeconfig(kubeconfig, context="fixture-hub", server=hub.url)
    monkeypatch.setattr(config.kube_config, "KUBE_CONFIG_DEFAULT_LOCATION", str(kubeconfig))
    try:
        client = KubeClient(context="fixture-hub")
        with pytest.raises(RuntimeError, match="malformed Application identity"):
            list_argocd_applications(client)
        assert hub.patches == []
    finally:
        hub.close()


@pytest.mark.parametrize(
    "persisted_scope",
    [
        {"secondary": ["9-team\n"]},
        {"secondary": [""]},
        {"secondary": [" 9-team "]},
        {"secondary": ["9-team", "bad.ns"]},
        {"secondary": "9-team"},
        "not-a-mapping",
    ],
)
def test_malformed_persisted_discovery_scope_rejected_before_pause_hubs_calls(tmp_path, monkeypatch, persisted_scope):
    app = application("9-team", "acm-app.example", automated=True)
    hub = FakeArgoCDHub(
        cluster_uid="secondary-uid",
        applications=[app],
        application_crd_present=True,
    )
    kubeconfig = tmp_path / "fixture-kubeconfig"
    write_kubeconfig(kubeconfig, context="fixture-hub", server=hub.url)
    monkeypatch.setattr(config.kube_config, "KUBE_CONFIG_DEFAULT_LOCATION", str(kubeconfig))
    state = _make_real_state(tmp_path)
    state._set_config(STATE_KEY_ARGOCD_RUN_ID, "run-1")
    state._set_config(STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES, persisted_scope)
    try:
        client = KubeClient(context="fixture-hub")
        with pytest.raises(ValidationError):
            ArgocdPauseRegister(state).pause_hubs([(client, "secondary")])
        assert hub.requests == []
        assert hub.patches == []
    finally:
        hub.close()


def test_malformed_secondary_persisted_discovery_scope_aborts_before_primary_calls(tmp_path, monkeypatch):
    primary_hub = FakeArgoCDHub(
        cluster_uid="primary-uid",
        applications=[application("9-team", "acm-primary", automated=True)],
        application_crd_present=True,
    )
    secondary_hub = FakeArgoCDHub(
        cluster_uid="secondary-uid",
        applications=[application("argocd", "acm-secondary", automated=True)],
        application_crd_present=True,
    )
    kubeconfig = tmp_path / "fixture-kubeconfig"
    kubeconfig.write_text(
        f"""apiVersion: v1
kind: Config
clusters:
- name: primary-cluster
  cluster:
    server: {primary_hub.url}
    insecure-skip-tls-verify: true
- name: secondary-cluster
  cluster:
    server: {secondary_hub.url}
    insecure-skip-tls-verify: true
contexts:
- name: primary-ctx
  context:
    cluster: primary-cluster
    user: primary-user
- name: secondary-ctx
  context:
    cluster: secondary-cluster
    user: secondary-user
current-context: primary-ctx
users:
- name: primary-user
  user:
    username: fixture
    password: fixture
- name: secondary-user
  user:
    username: fixture
    password: fixture
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(config.kube_config, "KUBE_CONFIG_DEFAULT_LOCATION", str(kubeconfig))
    state = _make_real_state(tmp_path)
    state._set_config(STATE_KEY_ARGOCD_RUN_ID, "run-1")
    state._set_config(
        STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES,
        {"primary": ["9-team"], "secondary": ["bad.ns"]},
    )
    try:
        primary_client = KubeClient(context="primary-ctx")
        secondary_client = KubeClient(context="secondary-ctx")
        with pytest.raises(ValidationError):
            ArgocdPauseRegister(state).pause_hubs([(primary_client, "primary"), (secondary_client, "secondary")])
        assert primary_hub.requests == []
        assert primary_hub.patches == []
        assert secondary_hub.requests == []
        assert secondary_hub.patches == []
    finally:
        primary_hub.close()
        secondary_hub.close()
