"""Integration tests for the preflight role contract."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

ARGOCD_FAILURE_SENTINELS = (
    "nested-bearer-token",
    "api.nested-secret.example",
    "nested-production-context",
    "nested-system-admin",
    "INJECTED-NESTED-DISCOVERY-LINE",
    "\x1b",
    "arbitrary nested discovery detail",
)
ARGOCD_FAILURE_MESSAGE = (
    "Authorization: Bearer nested-bearer-token "
    "api=https://api.nested-secret.example:6443 "
    "context=nested-production-context user=nested-system-admin\n"
    "INJECTED-NESTED-DISCOVERY-LINE\x1b[31m arbitrary nested discovery detail"
)


class _FixtureKubernetesHandler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        path = self.path.split("?")[0]
        requests = getattr(self.server, "fixture_requests", None)
        if isinstance(requests, list):
            requests.append({"method": "GET", "path": path})
        if path == "/api/v1":
            advertise_namespace = getattr(
                self.server,
                "fixture_advertise_namespace",
                True,
            )
            self._write_json(
                {
                    "kind": "APIResourceList",
                    "groupVersion": "v1",
                    "resources": (
                        [
                            {
                                "name": "namespaces",
                                "singularName": "",
                                "namespaced": False,
                                "kind": "Namespace",
                                "verbs": ["get", "list"],
                            }
                        ]
                        if advertise_namespace
                        else []
                    ),
                }
            )
            return
        if path == "/api/v1/namespaces/default":
            status = getattr(
                self.server,
                "fixture_default_namespace_status",
                200,
            )
            payload = getattr(
                self.server,
                "fixture_default_namespace_body",
                None,
            )
            if payload is None:
                payload = {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {"name": "default"},
                }
            self._write_json(payload, status=status)
            return
        responses = {
            "/version": {
                "major": "1",
                "minor": "28",
                "gitVersion": "v1.28.0",
            },
            "/api": {
                "kind": "APIVersions",
                "versions": ["v1"],
                "serverAddressByClientCIDRs": [],
            },
            "/apis": {
                "kind": "APIGroupList",
                "groups": [
                    {
                        "name": "authorization.k8s.io",
                        "versions": [
                            {
                                "groupVersion": "authorization.k8s.io/v1",
                                "version": "v1",
                            }
                        ],
                        "preferredVersion": {
                            "groupVersion": "authorization.k8s.io/v1",
                            "version": "v1",
                        },
                    }
                ],
            },
            "/apis/authorization.k8s.io/v1": {
                "kind": "APIResourceList",
                "groupVersion": "authorization.k8s.io/v1",
                "resources": [
                    {
                        "name": "selfsubjectaccessreviews",
                        "singularName": "",
                        "namespaced": False,
                        "kind": "SelfSubjectAccessReview",
                        "verbs": ["create"],
                    }
                ],
            },
        }
        payload = responses.get(path)
        if payload is None:
            self.send_response(404)
            self.end_headers()
            return
        self._write_json(payload)

    def do_POST(self):
        if self.path != "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews":
            self.send_response(404)
            self.end_headers()
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length:
            self.rfile.read(content_length)
        self._write_json(
            {
                "apiVersion": "authorization.k8s.io/v1",
                "kind": "SelfSubjectAccessReview",
                "metadata": {"name": "fixture-denial"},
                "status": {
                    "allowed": False,
                    "reason": "fixture denied permission",
                },
            },
            status=201,
        )

    def _write_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _ConnectivityHTTPServer(ThreadingHTTPServer):
    fixture_default_namespace_status: int
    fixture_default_namespace_body: dict | None
    fixture_advertise_namespace: bool
    fixture_requests: list[dict[str, str]]


class _ConnectivityAPIServer:
    def __init__(
        self,
        *,
        status: int = 200,
        body: dict | None = None,
        advertise_namespace: bool = True,
    ):
        self._server = _ConnectivityHTTPServer(
            ("127.0.0.1", 0),
            _FixtureKubernetesHandler,
        )
        self._server.fixture_default_namespace_status = status
        self._server.fixture_default_namespace_body = body
        self._server.fixture_advertise_namespace = advertise_namespace
        self._server.fixture_requests = []
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    @property
    def requests(self) -> list[dict[str, str]]:
        return list(self._server.fixture_requests)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class _ArgoFailureKubernetesHandler(_FixtureKubernetesHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/apis":
            self._write_json(
                {
                    "kind": "APIGroupList",
                    "groups": [
                        {
                            "name": "argoproj.io",
                            "versions": [
                                {
                                    "groupVersion": "argoproj.io/v1alpha1",
                                    "version": "v1alpha1",
                                }
                            ],
                            "preferredVersion": {
                                "groupVersion": "argoproj.io/v1alpha1",
                                "version": "v1alpha1",
                            },
                        }
                    ],
                }
            )
            return
        if path == "/apis/argoproj.io/v1alpha1":
            self._write_json(
                {
                    "kind": "APIResourceList",
                    "groupVersion": "argoproj.io/v1alpha1",
                    "resources": [
                        {
                            "name": "applications",
                            "singularName": "application",
                            "namespaced": True,
                            "kind": "Application",
                            "verbs": ["get", "list"],
                        }
                    ],
                }
            )
            return
        if path == "/apis/argoproj.io/v1alpha1/applications":
            self._write_json(
                {
                    "apiVersion": "v1",
                    "kind": "Status",
                    "status": "Failure",
                    "message": ARGOCD_FAILURE_MESSAGE,
                    "reason": "Forbidden",
                    "code": 403,
                },
                status=403,
            )
            return
        super().do_GET()


@pytest.fixture
def fixture_kubernetes_api_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureKubernetesHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def fixture_argocd_failure_api_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ArgoFailureKubernetesHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def connectivity_api_factory():
    servers: list[_ConnectivityAPIServer] = []

    def _create(**kwargs) -> _ConnectivityAPIServer:
        server = _ConnectivityAPIServer(**kwargs)
        servers.append(server)
        return server

    try:
        yield _create
    finally:
        for server in servers:
            server.close()


def test_preflight_input_failure_writes_report_and_fails(run_preflight_fixture):
    completed, report = run_preflight_fixture("input_failure.yml")
    assert completed.returncode != 0
    assert report["phase"] == "preflight"
    assert report["status"] == "fail"
    assert any(item["id"] == "preflight-input-secondary-context" for item in report["results"])


def test_preflight_success_fixture_passes(
    run_preflight_fixture,
    fixture_kubernetes_api_server,
):
    completed, report = run_preflight_fixture(
        "passive_success.yml",
        overrides={
            "acm_switchover_test_overrides": {
                "fixture_kubeconfig_server": fixture_kubernetes_api_server,
            },
        },
    )
    assert completed.returncode == 0
    assert report["status"] == "pass"
    assert any(item["id"] == "preflight-version-compatibility" for item in report["results"])


def _connectivity_results(report: dict) -> dict[str, dict]:
    return {
        item["id"]: item
        for item in report["results"]
        if item["id"].startswith("preflight-kubeconfig-") and item["id"].endswith("-connectivity")
    }


@pytest.mark.parametrize("failed_hub", ["primary", "secondary"])
def test_connectivity_bad_request_fails_only_the_intended_hub_and_reaches_report(
    run_preflight_fixture,
    connectivity_api_factory,
    failed_hub,
):
    primary = connectivity_api_factory(
        status=400 if failed_hub == "primary" else 200,
        body=(
            {
                "apiVersion": "v1",
                "kind": "Status",
                "status": "Failure",
                "reason": "BadRequest",
                "message": "fixture bad request",
                "code": 400,
            }
            if failed_hub == "primary"
            else None
        ),
    )
    secondary = connectivity_api_factory(
        status=400 if failed_hub == "secondary" else 200,
        body=(
            {
                "apiVersion": "v1",
                "kind": "Status",
                "status": "Failure",
                "reason": "BadRequest",
                "message": "fixture bad request",
                "code": 400,
            }
            if failed_hub == "secondary"
            else None
        ),
    )
    completed, report = run_preflight_fixture(
        "passive_success.yml",
        overrides={
            "acm_switchover_test_overrides": {
                "fixture_kubeconfig_servers": {
                    "primary": primary.url,
                    "secondary": secondary.url,
                },
            },
        },
    )

    assert completed.returncode != 0
    assert report["status"] == "fail"
    connectivity = _connectivity_results(report)
    assert connectivity[f"preflight-kubeconfig-{failed_hub}-connectivity"]["status"] == "fail"
    passing_hub = "secondary" if failed_hub == "primary" else "primary"
    assert connectivity[f"preflight-kubeconfig-{passing_hub}-connectivity"]["status"] == "pass"
    assert "See the structured preflight report artifact for details." in (completed.stdout + completed.stderr)


def test_connectivity_forbidden_is_sanitized_in_callback_and_report(
    run_preflight_fixture,
    connectivity_api_factory,
):
    sentinel = "R302-SENTINEL-HTTP-BODY"
    primary = connectivity_api_factory(
        status=403,
        body={
            "apiVersion": "v1",
            "kind": "Status",
            "status": "Failure",
            "reason": "Forbidden",
            "message": f"token={sentinel}",
            "code": 403,
        },
    )
    secondary = connectivity_api_factory()
    completed, report = run_preflight_fixture(
        "passive_success.yml",
        overrides={
            "acm_switchover_test_overrides": {
                "fixture_kubeconfig_servers": {
                    "primary": primary.url,
                    "secondary": secondary.url,
                },
            },
        },
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert _connectivity_results(report)["preflight-kubeconfig-primary-connectivity"]["status"] == "fail"
    assert sentinel not in output
    assert sentinel not in json.dumps(report)


@pytest.mark.parametrize(
    "primary_kwargs",
    [
        {"status": 404},
        {"advertise_namespace": False},
        {
            "body": {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": "wrong-name"},
            }
        },
        {
            "body": {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "default"},
            }
        },
        {
            "body": {
                "apiVersion": "v1",
                "kind": "NamespaceList",
                "items": [
                    {
                        "apiVersion": "v1",
                        "kind": "Namespace",
                        "metadata": {"name": "default"},
                    },
                    {
                        "apiVersion": "v1",
                        "kind": "Namespace",
                        "metadata": {"name": "extra"},
                    },
                ],
            }
        },
    ],
    ids=[
        "not-found",
        "api-unmappable",
        "wrong-name",
        "wrong-kind",
        "wrong-cardinality",
    ],
)
def test_connectivity_rejects_missing_or_wrong_namespace_evidence(
    run_preflight_fixture,
    connectivity_api_factory,
    primary_kwargs,
):
    primary = connectivity_api_factory(**primary_kwargs)
    secondary = connectivity_api_factory()
    completed, report = run_preflight_fixture(
        "passive_success.yml",
        overrides={
            "acm_switchover_test_overrides": {
                "fixture_kubeconfig_servers": {
                    "primary": primary.url,
                    "secondary": secondary.url,
                },
            },
        },
    )

    assert completed.returncode != 0
    connectivity = _connectivity_results(report)
    assert connectivity["preflight-kubeconfig-primary-connectivity"]["status"] == "fail"
    assert connectivity["preflight-kubeconfig-secondary-connectivity"]["status"] == "pass"


def test_preflight_version_mismatch_fails(run_preflight_fixture):
    completed, report = run_preflight_fixture("version_mismatch.yml")
    assert completed.returncode != 0
    assert report["status"] == "fail"
    assert any(
        item["id"] == "preflight-version-compatibility" and item["status"] == "fail" for item in report["results"]
    )


def test_preflight_backup_failure_is_reported(run_preflight_fixture):
    completed, report = run_preflight_fixture("backup_failure.yml")
    assert completed.returncode != 0
    assert report["status"] == "fail"
    result_ids = {item["id"] for item in report["results"]}
    assert "preflight-backup-latest" in result_ids
    assert "preflight-backup-schedule" in result_ids
    assert "preflight-backup-storage-location-primary" in result_ids
    assert "preflight-backup-storage-location-secondary" in result_ids
    assert "preflight-passive-restore-secondary" in result_ids
    assert "preflight-clusterdeployments" in result_ids
    assert "preflight-managed-cluster-backups" in result_ids


def test_preflight_rbac_failure_still_reports_backup_findings(
    run_preflight_fixture,
    fixture_kubernetes_api_server,
):
    completed, report = run_preflight_fixture(
        "rbac_and_backup_failure.yml",
        overrides={
            "acm_switchover_test_overrides": {
                "fixture_kubeconfig_server": fixture_kubernetes_api_server,
            },
        },
    )
    assert completed.returncode != 0
    assert report, completed.stdout + completed.stderr
    assert report["status"] == "fail"
    results_by_id = {item["id"]: item for item in report["results"]}
    assert results_by_id["preflight-rbac-primary"]["status"] == "fail"
    assert results_by_id["preflight-rbac-secondary"]["status"] == "fail"
    assert results_by_id["preflight-backup-latest"]["status"] == "fail"
    assert results_by_id["preflight-backup-schedule"]["status"] == "fail"
    assert results_by_id["preflight-backup-storage-location-primary"]["status"] == "fail"
    assert results_by_id["preflight-passive-restore-secondary"]["status"] == "fail"


def test_restore_only_rbac_with_secondary_only_hub_reports_secondary_validation(
    run_preflight_fixture,
    fixture_kubernetes_api_server,
):
    completed, report = run_preflight_fixture(
        "restore_only_rbac_secondary_only.yml",
        overrides={
            "acm_switchover_test_overrides": {
                "fixture_kubeconfig_server": fixture_kubernetes_api_server,
            },
        },
    )
    assert completed.returncode != 0
    assert report, completed.stdout + completed.stderr
    results_by_id = {item["id"]: item for item in report["results"]}
    assert "preflight-rbac-primary" not in results_by_id
    assert results_by_id["preflight-rbac-secondary"]["status"] == "fail"
    assert "preflight-kubeconfig-primary-connectivity" not in results_by_id
    assert results_by_id["preflight-kubeconfig-secondary-connectivity"]["status"] == "pass"


def test_preflight_fixture_without_execution_block_defaults_to_execute_identity_reads(
    run_preflight_fixture,
):
    completed, report = run_preflight_fixture("missing_execution_block.yml")
    output = completed.stdout + completed.stderr

    assert completed.returncode != 0
    assert (
        "Unable to verify the primary hub physical identity from the live kube-system Namespace UID. "
        "Refusing the normal two-hub switchover."
    ) in output
    assert report == {}


def test_preflight_invalid_report_dir_fails_without_writing_report(
    run_preflight_fixture,
):
    completed, report = run_preflight_fixture("invalid_report_dir.yml")
    assert completed.returncode != 0
    assert report == {}
    assert "Path traversal attempt" in completed.stdout or "Path traversal attempt" in completed.stderr


def test_preflight_nested_argocd_failure_is_callback_safe_and_advisory(
    run_preflight_fixture,
    fixture_argocd_failure_api_server,
):
    completed, report = run_preflight_fixture(
        "passive_success.yml",
        overrides={
            "acm_switchover_test_overrides": {
                "fixture_kubeconfig_server": fixture_argocd_failure_api_server,
            },
        },
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert report["status"] == "pass"
    assert "[primary] Unable to complete Argo CD check" in output
    assert "[secondary] Unable to complete Argo CD check" in output
    assert not any(
        sentinel in output for sentinel in ARGOCD_FAILURE_SENTINELS
    ), "callback output disclosed protected sentinel data"


def test_strict_argocd_discovery_failure_is_callback_safe_and_fails(
    run_argocd_discovery_fixture,
    fixture_argocd_failure_api_server,
):
    completed = run_argocd_discovery_fixture(
        server=fixture_argocd_failure_api_server,
        advisory=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "Argo CD discovery failed; verify controller access and input, then retry." in output
    assert not any(
        sentinel in output for sentinel in ARGOCD_FAILURE_SENTINELS
    ), "callback output disclosed protected sentinel data"


@pytest.mark.parametrize(
    ("advisory", "expected_returncode", "expected_status"),
    [
        (True, 0, "error"),
        (False, 2, None),
    ],
)
def test_mock_filter_failure_preserves_advisory_and_strict_semantics(
    run_argocd_discovery_fixture,
    advisory,
    expected_returncode,
    expected_status,
):
    completed = run_argocd_discovery_fixture(
        advisory=advisory,
        mock_apps=[
            {
                "metadata": {"name": "mock-filter-secret"},
                "status": {"resources": ["Bearer mock-filter-secret\n\x1b[31m"]},
            }
        ],
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == expected_returncode, output
    assert "mock-filter-secret" not in output
    assert "\x1b" not in output
    if expected_status is None:
        assert "Argo CD discovery failed; verify controller access and input, then retry." in output
    else:
        assert f"DISCOVERY_STATUS={expected_status} COUNT=0" in output
        assert "changed=0" in output


@pytest.mark.parametrize("check_mode", [False, True])
def test_argocd_discovery_success_preserves_facts_without_changed_reporting(
    run_argocd_discovery_fixture,
    check_mode,
):
    completed = run_argocd_discovery_fixture(
        advisory=True,
        mock_apps=[
            {
                "metadata": {
                    "namespace": "openshift-gitops",
                    "name": "acm-policy",
                },
                "spec": {"syncPolicy": {"automated": {"prune": True}}},
                "status": {
                    "resources": [
                        {
                            "kind": "Policy",
                            "namespace": "open-cluster-management",
                        }
                    ]
                },
            }
        ],
        check_mode=check_mode,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "DISCOVERY_STATUS=ok COUNT=1" in output
    assert "changed=0" in output
