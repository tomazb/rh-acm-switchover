"""Runtime tests for the shipped acm_k8s_read_outcome module."""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path

import yaml

from ansible_collections.tomazb.acm_switchover.tests.conftest import _ansible_env
from ansible_collections.tomazb.acm_switchover.tests.integration.argocd_fake_api import (
    write_kubeconfig,
)
from ansible_collections.tomazb.acm_switchover.tests.integration.r3_02_fake_api import (
    SENTINEL,
    FakeR302API,
    status_payload,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _run_module(
    tmp_path: Path,
    *,
    server: str,
    read_mode: str,
    kind: str,
    resource_name: str,
    name: str = "",
    api_version: str = "v1",
    namespace: str = "test-ns",
    check_mode: bool = False,
    env: dict | None = None,
) -> subprocess.CompletedProcess[str]:
    repo_root = _repo_root()
    kubeconfig = tmp_path / "r3-02.kubeconfig"
    write_kubeconfig(
        kubeconfig,
        context="r3-02",
        server=server,
        token="fixture-token",
    )
    vars_file = tmp_path / "r3-02-vars.yml"
    vars_file.write_text(
        yaml.safe_dump(
            {
                "r3_02_kubeconfig": str(kubeconfig),
                "r3_02_context": "r3-02",
                "r3_02_read_mode": read_mode,
                "r3_02_kind": kind,
                "r3_02_resource_name": resource_name,
                "r3_02_name": name,
                "r3_02_api_version": api_version,
                "r3_02_namespace": namespace,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    command = [
        "ansible-playbook",
        "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/" "run_k8s_read_outcome.yml",
        "-i",
        "localhost,",
        "-e",
        f"@{vars_file}",
    ]
    if check_mode:
        command.append("--check")
    return subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else _ansible_env(repo_root, tmp_path),
        timeout=60,
    )


def _output(completed: subprocess.CompletedProcess[str]) -> str:
    return completed.stdout + completed.stderr


def test_runtime_list_empty_is_ok(tmp_path):
    api = FakeR302API()
    try:
        completed = _run_module(
            tmp_path,
            server=api.url,
            read_mode="list",
            kind="Pod",
            resource_name="pods",
        )
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "READ_STATUS=ok COUNT=0 CHANGED=False" in output, api.requests


def test_runtime_named_get_is_ok(tmp_path):
    api = FakeR302API()
    try:
        completed = _run_module(
            tmp_path,
            server=api.url,
            read_mode="get",
            kind="ConfigMap",
            resource_name="configmaps",
            name="test-config",
        )
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "READ_STATUS=ok COUNT=1 CHANGED=False" in output


def test_runtime_named_404_is_not_found(tmp_path):
    api = FakeR302API(
        configmap_status=404,
        configmap_body=status_payload(404),
    )
    try:
        completed = _run_module(
            tmp_path,
            server=api.url,
            read_mode="get",
            kind="ConfigMap",
            resource_name="configmaps",
            name="test-config",
        )
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "READ_STATUS=not_found COUNT=0 CHANGED=False" in output


def test_runtime_list_404_is_error(tmp_path):
    api = FakeR302API(
        pod_list_status=404,
        pod_list_body=status_payload(404),
    )
    try:
        completed = _run_module(
            tmp_path,
            server=api.url,
            read_mode="list",
            kind="Pod",
            resource_name="pods",
        )
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "READ_STATUS=error COUNT=0 CHANGED=False" in output


def test_runtime_bad_request_is_error(tmp_path):
    api = FakeR302API(
        pod_list_status=400,
        pod_list_body=status_payload(400),
    )
    try:
        completed = _run_module(
            tmp_path,
            server=api.url,
            read_mode="list",
            kind="Pod",
            resource_name="pods",
        )
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "READ_STATUS=error COUNT=0 CHANGED=False" in output


def test_runtime_forbidden_is_sanitized_error(tmp_path):
    api = FakeR302API(
        pod_list_status=403,
        pod_list_body=status_payload(403, message=f"token={SENTINEL}"),
    )
    try:
        completed = _run_module(
            tmp_path,
            server=api.url,
            read_mode="list",
            kind="Pod",
            resource_name="pods",
        )
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "READ_STATUS=error COUNT=0 CHANGED=False" in output
    assert SENTINEL not in output


def test_runtime_connection_failure_is_error(tmp_path):
    # Keep the port bound but never listening for the whole run: connections are refused, and no
    # concurrently started fake API server (pytest-xdist workers) can be handed the same port.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        unavailable_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        completed = _run_module(
            tmp_path,
            server=unavailable_url,
            read_mode="list",
            kind="Pod",
            resource_name="pods",
        )

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "READ_STATUS=error COUNT=0 CHANGED=False" in output


def test_runtime_discovery_failure_is_kind_not_served(tmp_path):
    api = FakeR302API()
    try:
        completed = _run_module(
            tmp_path,
            server=api.url,
            read_mode="list",
            kind="NotARealKind",
            resource_name="notarealkinds",
        )
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "READ_STATUS=kind_not_served COUNT=0 CHANGED=False" in output


def test_runtime_check_mode_still_reads_without_writes(tmp_path):
    api = FakeR302API()
    try:
        completed = _run_module(
            tmp_path,
            server=api.url,
            read_mode="list",
            kind="Pod",
            resource_name="pods",
            check_mode=True,
        )
        requests = api.requests
        writes = api.writes
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "READ_STATUS=ok COUNT=0 CHANGED=False" in output
    assert {"method": "GET", "path": "/api/v1/namespaces/test-ns/pods"} in requests
    assert writes == []
    assert "changed=0" in output


def test_runtime_runs_when_tmp_path_is_too_deep_for_a_unix_socket(tmp_path):
    """A deep pytest tmp_path must not break ansible-playbook on ansible-core 2.21 (#314).

    ansible-core 2.21 starts a multiprocessing manager at the start of every playbook, and
    its Unix socket is created beneath TMPDIR. Linux socket paths hold at most 107 bytes
    (``sun_path`` is 108 bytes including the terminating NUL), and pytest-xdist tmp_paths on
    CI are deep enough that a TMPDIR under them failed every run with "Local RPC server did
    not start". This tmp_path alone is at least 108 bytes, so no socket fits beneath it.
    Earlier ansible-core versions start no such server, so only 2.21 can fail here.
    """
    deep = tmp_path / ("d" * max(1, 108 - len(str(tmp_path))))
    deep.mkdir()
    api = FakeR302API()
    try:
        completed = _run_module(
            deep,
            server=api.url,
            read_mode="get",
            kind="ConfigMap",
            resource_name="configmaps",
            name="test-config",
        )
    finally:
        api.close()

    assert completed.returncode == 0, _output(completed)
    assert "READ_STATUS=ok COUNT=1 CHANGED=False" in _output(completed)


def test_runtime_runs_sharing_an_api_endpoint_each_perform_their_own_discovery(tmp_path):
    """Module runs whose fake APIs share a host:port must not share discovery (#314).

    kubernetes.core caches API discovery in ``tempfile.gettempdir()``, keyed by the API
    server host:port and the login user. Fake APIs bind ephemeral ports the OS recycles, so a later run, in
    the same test or another one, can reach the host:port of an earlier fake API. One
    fake API reproduces that deterministically: between the runs it stops serving
    ConfigMaps, and the second run must discover that itself rather than load the first
    run's cache, which still lists them.
    """
    api = FakeR302API()

    def read_configmap() -> subprocess.CompletedProcess[str]:
        return _run_module(
            tmp_path,
            server=api.url,
            read_mode="get",
            kind="ConfigMap",
            resource_name="configmaps",
            name="test-config",
        )

    try:
        first = read_configmap()
        first_requests = api.requests
        api.core_resources = [
            {"name": "pods", "singularName": "pod", "namespaced": True, "kind": "Pod", "verbs": ["get", "list"]}
        ]
        second = read_configmap()
        second_requests = api.requests[len(first_requests) :]
    finally:
        api.close()

    assert first.returncode == 0, _output(first)
    assert "READ_STATUS=ok COUNT=1 CHANGED=False" in _output(first)
    assert second.returncode == 0, _output(second)
    # Only the discoverer requests /apis; strict_read's own served-kind probe requests /api/v1.
    discovery = {"method": "GET", "path": "/apis"}
    assert discovery in first_requests, first_requests
    assert discovery in second_requests, second_requests
    assert "READ_STATUS=kind_not_served COUNT=0 CHANGED=False" in _output(second)


def test_runtime_stale_shared_discovery_never_turns_an_unserved_kind_into_not_found(tmp_path):
    """A named 404 is an absence proof only when live discovery serves the kind (#317).

    Production controllers share one ``tempfile.gettempdir()``, so kubernetes.core loads the
    discovery cache an earlier run wrote for the same API host and user. Here both runs share
    one TMPDIR. Between them the API stops serving ConfigMaps and, as a real API server does
    for an unserved kind, answers the object route with 404. The second run resolves the kind
    from the first run's cache; it must still report ``kind_not_served``, never ``not_found``.
    """
    api = FakeR302API()
    shared_env = _ansible_env(_repo_root(), tmp_path)

    def read_configmap() -> subprocess.CompletedProcess[str]:
        return _run_module(
            tmp_path,
            server=api.url,
            read_mode="get",
            kind="ConfigMap",
            resource_name="configmaps",
            name="test-config",
            env=shared_env,
        )

    try:
        first = read_configmap()
        first_requests = api.requests
        api.core_resources = [
            {"name": "pods", "singularName": "pod", "namespaced": True, "kind": "Pod", "verbs": ["get", "list"]}
        ]
        api.configmap_status = 404
        api.configmap_body = status_payload(404)
        second = read_configmap()
        second_requests = api.requests[len(first_requests) :]
    finally:
        api.close()

    assert first.returncode == 0, _output(first)
    assert "READ_STATUS=ok COUNT=1 CHANGED=False" in _output(first)
    # The precondition this regression exists for: the second run's kind resolution came from
    # the first run's shared cache, so no discoverer group listing was requested again.
    assert {"method": "GET", "path": "/apis"} not in second_requests, second_requests
    assert {"method": "GET", "path": "/api"} not in second_requests, second_requests
    # The cached route was actually read: the named object GET was sent (and the fake 404s it),
    # and a live /api/v1 discovery read followed it.
    named_get = {"method": "GET", "path": "/api/v1/namespaces/test-ns/configmaps/test-config"}
    assert named_get in second_requests, second_requests
    after_named_get = second_requests[second_requests.index(named_get) + 1 :]
    assert {"method": "GET", "path": "/api/v1"} in after_named_get, second_requests
    assert second.returncode == 0, _output(second)
    assert "READ_STATUS=not_found" not in _output(second)
    assert "READ_STATUS=kind_not_served COUNT=0 CHANGED=False" in _output(second)


_MANAGED_CLUSTER_API = "cluster.open-cluster-management.io/v1"


def test_runtime_stale_cluster_scoped_cache_never_publishes_a_namespaced_list(tmp_path):
    """A namespaced LIST is never routed cluster-wide by a stale cached scope (#320).

    Both runs share one TMPDIR, so kubernetes.core's second resolution loads the discovery cache
    the first run wrote. Run 1 reads a ConfigMap while `/api/v1` lists Pods as cluster-scoped, and
    kubernetes.core caches that whole group/version document. The server then serves Pods as
    namespaced. Run 2's namespaced Pod LIST resolves Pods from the stale cache; the dynamic client
    would drop the namespace and LIST `/api/v1/pods`, whose answer holds another namespace's Pod.
    """
    stale_core = [
        {"name": "pods", "singularName": "pod", "namespaced": False, "kind": "Pod", "verbs": ["get", "list"]},
        {
            "name": "configmaps",
            "singularName": "configmap",
            "namespaced": True,
            "kind": "ConfigMap",
            "verbs": ["get", "list"],
        },
    ]
    foreign = {
        "apiVersion": "v1",
        "kind": "PodList",
        "metadata": {"resourceVersion": "3"},
        "items": [{"metadata": {"name": "foreign", "namespace": "elsewhere", "resourceVersion": "3"}}],
    }
    api = FakeR302API(core_resources=stale_core, pod_list_body=foreign)
    shared_env = _ansible_env(_repo_root(), tmp_path)
    try:
        first = _run_module(
            tmp_path,
            server=api.url,
            read_mode="get",
            kind="ConfigMap",
            resource_name="configmaps",
            name="test-config",
            env=shared_env,
        )
        first_requests = api.requests
        api.core_resources = None  # the live server now serves Pods as namespaced
        second = _run_module(
            tmp_path,
            server=api.url,
            read_mode="list",
            kind="Pod",
            resource_name="pods",
            env=shared_env,
        )
        second_requests = api.requests[len(first_requests) :]
    finally:
        api.close()

    assert first.returncode == 0, _output(first)
    assert "READ_STATUS=ok COUNT=1 CHANGED=False" in _output(first)
    # The precondition this regression exists for: run 2 resolved Pods from run 1's cache.
    assert {"method": "GET", "path": "/api"} not in second_requests, second_requests
    assert {"method": "GET", "path": "/apis"} not in second_requests, second_requests
    # No Pod request of any scope was sent: the route was refused before the LIST.
    assert not [request for request in second_requests if request["path"].endswith("/pods")], second_requests
    assert second.returncode == 0, _output(second)
    assert "READ_STATUS=error COUNT=0 CHANGED=False" in _output(second)


def test_runtime_custom_resource_get_with_unreadable_discovery_is_error(tmp_path):
    """An existing custom resource is not `ok` while its live discovery cannot be read (#322).

    Run 1 reads the ManagedCluster and leaves its discovery in the shared cache, so run 2
    resolves the kind without a live discovery read. Run 2's live discovery for the
    group/version answers 503; Python reports ERROR without an object GET, and so must the
    collection.
    """
    api = FakeR302API()
    shared_env = _ansible_env(_repo_root(), tmp_path)

    def read_managed_cluster() -> subprocess.CompletedProcess[str]:
        return _run_module(
            tmp_path,
            server=api.url,
            read_mode="get",
            kind="ManagedCluster",
            resource_name="managedclusters",
            name="cluster-a",
            api_version=_MANAGED_CLUSTER_API,
            namespace="",
            env=shared_env,
        )

    try:
        first = read_managed_cluster()
        first_requests = api.requests
        api.managed_cluster_discovery_status = 503
        second = read_managed_cluster()
        second_requests = api.requests[len(first_requests) :]
    finally:
        api.close()

    assert first.returncode == 0, _output(first)
    assert "READ_STATUS=ok COUNT=1 CHANGED=False" in _output(first)
    assert {"method": "GET", "path": "/apis"} not in second_requests, second_requests
    assert {"method": "GET", "path": f"/apis/{_MANAGED_CLUSTER_API}"} in second_requests, second_requests
    object_get = {"method": "GET", "path": f"/apis/{_MANAGED_CLUSTER_API}/managedclusters/cluster-a"}
    assert object_get not in second_requests, second_requests
    assert second.returncode == 0, _output(second)
    assert "READ_STATUS=error COUNT=0 CHANGED=False" in _output(second)


def test_runtime_custom_resource_list_reads_live_discovery_before_the_list(tmp_path):
    """Positive control: a confirmed custom-resource LIST is `ok` and sends no write, in check mode too."""
    api = FakeR302API()
    shared_env = _ansible_env(_repo_root(), tmp_path)

    def list_managed_clusters(check_mode: bool) -> subprocess.CompletedProcess[str]:
        return _run_module(
            tmp_path,
            server=api.url,
            read_mode="list",
            kind="ManagedCluster",
            resource_name="managedclusters",
            api_version=_MANAGED_CLUSTER_API,
            namespace="",
            check_mode=check_mode,
            env=shared_env,
        )

    try:
        first = list_managed_clusters(check_mode=False)
        first_requests = api.requests
        second = list_managed_clusters(check_mode=True)
        second_requests = api.requests[len(first_requests) :]
        writes = api.writes
    finally:
        api.close()

    for completed in (first, second):
        assert completed.returncode == 0, _output(completed)
        assert "READ_STATUS=ok COUNT=1 CHANGED=False" in _output(completed)
    # Run 2 resolved from the cache, so its only group/version discovery read is the strict
    # read's own live proof, and it precedes the LIST.
    assert {"method": "GET", "path": "/apis"} not in second_requests, second_requests
    discovery = {"method": "GET", "path": f"/apis/{_MANAGED_CLUSTER_API}"}
    listing = {"method": "GET", "path": f"/apis/{_MANAGED_CLUSTER_API}/managedclusters"}
    assert second_requests.count(discovery) == 1, second_requests
    assert second_requests.index(discovery) < second_requests.index(listing), second_requests
    assert writes == []
