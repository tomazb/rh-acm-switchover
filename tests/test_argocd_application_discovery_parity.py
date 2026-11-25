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
from lib.constants import STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES, STATE_KEY_ARGOCD_RUN_ID
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
@pytest.mark.parametrize("case", ["discovery-503", "verified-absence", "empty", "missing-name", "blank-namespace"])
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
                }.get(case, "Unable to complete Argo CD check")
                assert expected_message in caplog.text
                assert ("Unable to complete Argo CD check" in caplog.text) is error
            elif error:
                with pytest.raises(RuntimeError, match="Argo CD Application discovery failed") as exc:
                    ArgocdPauseRegister(state).pause_hubs([(client, "secondary")])
                assert _SECRET not in str(exc.value)
            else:
                summary = ArgocdPauseRegister(state).pause_hubs([(client, "secondary")])
                assert summary.applications_crd_visible is (case == "empty")
                assert summary.newly_paused == 0
                assert bool(summary.run_id) is (case == "empty")

        assert hub.requests == [{"method": "GET", "path": path} for path in expected_paths]
        assert _SECRET not in caplog.text
        if advisory or case != "empty":
            run_id_or_new.assert_not_called()
            assert state._get_config(STATE_KEY_ARGOCD_RUN_ID) is None
        if error:
            assert not state._get_config(STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES)
    finally:
        hub.close()
