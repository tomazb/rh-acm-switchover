"""Reusable fake Kubernetes API for non-live R3-02 integration tests."""

from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

SENTINEL = "R302-SENTINEL-HTTP-BODY"
_BACKUP_SCHEDULE_PREFIX = (
    "/apis/cluster.open-cluster-management.io/v1beta1/namespaces/open-cluster-management-backup/backupschedules/"
)


def status_payload(status: int, *, message: str = "fixture request failed") -> dict:
    reasons = {
        400: "BadRequest",
        403: "Forbidden",
        404: "NotFound",
        500: "InternalError",
    }
    return {
        "apiVersion": "v1",
        "kind": "Status",
        "status": "Failure",
        "message": message,
        "reason": reasons.get(status, "Unknown"),
        "code": status,
    }


class FakeR302API:
    """Serve selectable discovery/read responses and record all requests."""

    def __init__(
        self,
        *,
        pod_list_status: int = 200,
        pod_list_body: dict | None = None,
        pod_transport_error: bool = False,
        close_after_scale: bool = False,
        configmap_status: int = 200,
        configmap_body: dict | None = None,
        configmap_transport_error: bool = False,
        core_resources: list[dict[str, Any]] | None = None,
        managed_clusters: list[dict[str, Any]] | None = None,
        managed_cluster_discovery_status: int = 200,
        backup_schedules: list[dict[str, Any]] | None = None,
        pods_follow_replicas: bool = False,
        honor_dry_run: bool = False,
    ):
        # Served BackupSchedules (cluster.open-cluster-management.io/v1beta1); None serves
        # no v1beta1 group at all, exactly as before.
        self.backup_schedules: list[dict[str, Any]] | None = copy.deepcopy(backup_schedules)
        # When set, the compactor pod list holds one running pod while the StatefulSet has
        # replicas and none once it is scaled to 0, as a real controller converges.
        self.pods_follow_replicas = pods_follow_replicas
        # When set, a write carrying dryRun=All changes nothing, as on a real API server.
        self.honor_dry_run = honor_dry_run
        self.pod_list_status = pod_list_status
        self.pod_list_body = copy.deepcopy(pod_list_body)
        self.pod_transport_error = pod_transport_error
        self._pod_transport_errors_remaining = 1 if pod_transport_error else 0
        self.close_after_scale = close_after_scale
        self.configmap_status = configmap_status
        self.configmap_body = copy.deepcopy(configmap_body)
        self.configmap_transport_error = configmap_transport_error
        self._configmap_transport_errors_remaining = 1 if configmap_transport_error else 0
        self.core_resources: list[dict[str, Any]] | None = copy.deepcopy(core_resources)
        # A status other than 200 makes the ManagedCluster group/version discovery read fail.
        self.managed_cluster_discovery_status = managed_cluster_discovery_status
        default_managed_clusters: list[dict[str, Any]] = [
            {
                "apiVersion": "cluster.open-cluster-management.io/v1",
                "kind": "ManagedCluster",
                "metadata": {
                    "name": "cluster-a",
                    "resourceVersion": "1",
                    "annotations": {},
                },
            }
        ]
        self.managed_clusters: list[dict[str, Any]] = copy.deepcopy(
            managed_clusters if managed_clusters is not None else default_managed_clusters
        )
        self.statefulset_replicas = 1
        self._scaled_statefulset_reads = 0
        self._shutdown_scheduled = False
        self._requests: list[dict[str, str]] = []
        self._queries: list[dict[str, str]] = []
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    @property
    def requests(self) -> list[dict[str, str]]:
        with self._lock:
            return copy.deepcopy(self._requests)

    @property
    def writes(self) -> list[dict[str, str]]:
        return [request for request in self.requests if request["method"] in {"POST", "PUT", "PATCH", "DELETE"}]

    @property
    def queries(self) -> list[dict[str, str]]:
        """Every request with its raw query string, which `requests` deliberately omits."""
        with self._lock:
            return copy.deepcopy(self._queries)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _record(self, method: str, path: str, query: str = "") -> None:
        with self._lock:
            self._requests.append({"method": method, "path": path})
            self._queries.append({"method": method, "path": path, "query": query})

    def _handler(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def do_GET(self):
                path = unquote(urlsplit(self.path).path)
                api._record("GET", path, urlsplit(self.path).query)
                if path == "/version":
                    self._write_json({"major": "1", "minor": "28", "gitVersion": "v1.28.0"})
                    return
                if path == "/api":
                    self._write_json(
                        {
                            "kind": "APIVersions",
                            "versions": ["v1"],
                            "serverAddressByClientCIDRs": [],
                        }
                    )
                    return
                if path == "/api/v1":
                    self._write_json(
                        {
                            "kind": "APIResourceList",
                            "groupVersion": "v1",
                            "resources": (
                                copy.deepcopy(api.core_resources)
                                if api.core_resources is not None
                                else [
                                    {
                                        "name": "pods",
                                        "singularName": "pod",
                                        "namespaced": True,
                                        "kind": "Pod",
                                        "verbs": ["get", "list"],
                                    },
                                    {
                                        "name": "configmaps",
                                        "singularName": "configmap",
                                        "namespaced": True,
                                        "kind": "ConfigMap",
                                        "verbs": ["get", "list"],
                                    },
                                ]
                            ),
                        }
                    )
                    return
                if path == "/apis":
                    self._write_json(
                        {
                            "kind": "APIGroupList",
                            "groups": [
                                {
                                    "name": "apps",
                                    "versions": [
                                        {
                                            "groupVersion": "apps/v1",
                                            "version": "v1",
                                        }
                                    ],
                                    "preferredVersion": {
                                        "groupVersion": "apps/v1",
                                        "version": "v1",
                                    },
                                },
                                {
                                    "name": "cluster.open-cluster-management.io",
                                    "versions": [
                                        {
                                            "groupVersion": "cluster.open-cluster-management.io/v1",
                                            "version": "v1",
                                        }
                                    ]
                                    + (
                                        [
                                            {
                                                "groupVersion": "cluster.open-cluster-management.io/v1beta1",
                                                "version": "v1beta1",
                                            }
                                        ]
                                        if api.backup_schedules is not None
                                        else []
                                    ),
                                    "preferredVersion": {
                                        "groupVersion": "cluster.open-cluster-management.io/v1",
                                        "version": "v1",
                                    },
                                },
                            ],
                        }
                    )
                    return
                if path == "/apis/cluster.open-cluster-management.io/v1":
                    if api.managed_cluster_discovery_status != 200:
                        status = api.managed_cluster_discovery_status
                        self._write_json(status_payload(status), status=status)
                        return
                    self._write_json(
                        {
                            "kind": "APIResourceList",
                            "groupVersion": "cluster.open-cluster-management.io/v1",
                            "resources": [
                                {
                                    "name": "managedclusters",
                                    "singularName": "managedcluster",
                                    "namespaced": False,
                                    "kind": "ManagedCluster",
                                    "verbs": ["get", "list", "patch"],
                                }
                            ],
                        }
                    )
                    return
                if path == "/apis/cluster.open-cluster-management.io/v1beta1" and api.backup_schedules is not None:
                    self._write_json(
                        {
                            "kind": "APIResourceList",
                            "groupVersion": "cluster.open-cluster-management.io/v1beta1",
                            "resources": [
                                {
                                    "name": "backupschedules",
                                    "singularName": "backupschedule",
                                    "namespaced": True,
                                    "kind": "BackupSchedule",
                                    "verbs": ["get", "list", "patch"],
                                }
                            ],
                        }
                    )
                    return
                if path.startswith(_BACKUP_SCHEDULE_PREFIX) and api.backup_schedules is not None:
                    schedule = self._backup_schedule(path.removeprefix(_BACKUP_SCHEDULE_PREFIX))
                    if schedule is None:
                        self._write_json(status_payload(404), status=404)
                    else:
                        self._write_json(schedule)
                    return
                if path == "/apis/apps/v1":
                    self._write_json(
                        {
                            "kind": "APIResourceList",
                            "groupVersion": "apps/v1",
                            "resources": [
                                {
                                    "name": "statefulsets",
                                    "singularName": "statefulset",
                                    "namespaced": True,
                                    "kind": "StatefulSet",
                                    "verbs": ["get", "list", "patch"],
                                },
                                {
                                    "name": "statefulsets/scale",
                                    "singularName": "",
                                    "namespaced": True,
                                    "kind": "Scale",
                                    "group": "apps",
                                    "version": "v1",
                                    "verbs": ["get", "patch"],
                                },
                            ],
                        }
                    )
                    return
                if path == (
                    "/apis/apps/v1/namespaces/" "open-cluster-management-observability/statefulsets/thanos-compactor"
                ):
                    self._write_json(self._statefulset())
                    if api._shutdown_scheduled:
                        Thread(target=self._close_server, daemon=True).start()
                    return
                if path.endswith("/pods"):
                    if api.pod_transport_error:
                        with api._lock:
                            should_drop = api._pod_transport_errors_remaining > 0
                            if should_drop:
                                api._pod_transport_errors_remaining -= 1
                        if should_drop:
                            self.close_connection = True
                            return
                        self._write_json(
                            status_payload(500),
                            status=500,
                        )
                        return
                    payload = api.pod_list_body
                    if payload is None and api.pods_follow_replicas:
                        with api._lock:
                            running = api.statefulset_replicas > 0
                        payload = {
                            "apiVersion": "v1",
                            "kind": "PodList",
                            "metadata": {"resourceVersion": "1"},
                            "items": (
                                [
                                    {
                                        "apiVersion": "v1",
                                        "kind": "Pod",
                                        "metadata": {
                                            "name": "thanos-compactor-0",
                                            "namespace": "open-cluster-management-observability",
                                        },
                                    }
                                ]
                                if running
                                else []
                            ),
                        }
                    if payload is None:
                        payload = {
                            "apiVersion": "v1",
                            "kind": "PodList",
                            "metadata": {"resourceVersion": "1"},
                            "items": [],
                        }
                    self._write_json(
                        copy.deepcopy(payload),
                        status=api.pod_list_status,
                    )
                    return
                if path in {
                    "/api/v1/namespaces/test-ns/configmaps/test-config",
                    "/api/v1/namespaces/multicluster-engine/configmaps/" "import-controller-config",
                }:
                    if api.configmap_transport_error:
                        with api._lock:
                            should_drop = api._configmap_transport_errors_remaining > 0
                            if should_drop:
                                api._configmap_transport_errors_remaining -= 1
                        if should_drop:
                            self.close_connection = True
                            return
                        self._write_json(
                            status_payload(500),
                            status=500,
                        )
                        return
                    payload = api.configmap_body
                    if payload is None:
                        namespace = "multicluster-engine" if "multicluster-engine" in path else "test-ns"
                        name = "import-controller-config" if "import-controller-config" in path else "test-config"
                        payload = {
                            "apiVersion": "v1",
                            "kind": "ConfigMap",
                            "metadata": {
                                "name": name,
                                "namespace": namespace,
                                "resourceVersion": "1",
                            },
                            "data": {},
                        }
                    self._write_json(
                        copy.deepcopy(payload),
                        status=api.configmap_status,
                    )
                    return
                if path == ("/apis/cluster.open-cluster-management.io/v1/" "managedclusters"):
                    with api._lock:
                        items = copy.deepcopy(api.managed_clusters)
                    self._write_json(
                        {
                            "apiVersion": "cluster.open-cluster-management.io/v1",
                            "kind": "ManagedClusterList",
                            "metadata": {"resourceVersion": "1"},
                            "items": items,
                        }
                    )
                    return
                managed_cluster_prefix = "/apis/cluster.open-cluster-management.io/v1/" "managedclusters/"
                if path.startswith(managed_cluster_prefix):
                    name = path.removeprefix(managed_cluster_prefix)
                    with api._lock:
                        cluster = next(
                            (
                                copy.deepcopy(item)
                                for item in api.managed_clusters
                                if item.get("metadata", {}).get("name") == name
                            ),
                            None,
                        )
                    if cluster is None:
                        self._write_json(status_payload(404), status=404)
                    else:
                        self._write_json(cluster)
                    return
                self._write_json(status_payload(404), status=404)

            def do_POST(self):
                self._unsupported_write("POST")

            def do_PUT(self):
                self._unsupported_write("PUT")

            def do_PATCH(self):
                path = unquote(urlsplit(self.path).path)
                query = urlsplit(self.path).query
                api._record("PATCH", path, query)
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                if api.honor_dry_run and "All" in parse_qs(query).get("dryRun", []):
                    # A server-side dry run: answer, store nothing.
                    self._write_json(body or {"kind": "Status", "status": "Success"})
                    return
                if path.startswith(_BACKUP_SCHEDULE_PREFIX) and api.backup_schedules is not None:
                    schedule = self._backup_schedule(path.removeprefix(_BACKUP_SCHEDULE_PREFIX), patch=body)
                    if schedule is None:
                        self._write_json(status_payload(404), status=404)
                    else:
                        self._write_json(schedule)
                    return
                if path == (
                    "/apis/apps/v1/namespaces/"
                    "open-cluster-management-observability/statefulsets/"
                    "thanos-compactor/scale"
                ):
                    with api._lock:
                        api.statefulset_replicas = int((body.get("spec") or {}).get("replicas", 0))
                    self._write_json(
                        {
                            "apiVersion": "autoscaling/v1",
                            "kind": "Scale",
                            "metadata": {
                                "name": "thanos-compactor",
                                "namespace": "open-cluster-management-observability",
                            },
                            "spec": {"replicas": api.statefulset_replicas},
                            "status": {"replicas": api.statefulset_replicas},
                        }
                    )
                    return
                prefix = "/apis/cluster.open-cluster-management.io/v1/" "managedclusters/"
                if path.startswith(prefix):
                    name = path.removeprefix(prefix)
                    with api._lock:
                        cluster = next(
                            (item for item in api.managed_clusters if item.get("metadata", {}).get("name") == name),
                            None,
                        )
                        if cluster is not None:
                            annotations = (body.get("metadata") or {}).get("annotations") or {}
                            current_annotations = cluster.setdefault(
                                "metadata",
                                {},
                            ).setdefault("annotations", {})
                            for key, value in annotations.items():
                                if value is None:
                                    current_annotations.pop(key, None)
                                else:
                                    current_annotations[key] = value
                            cluster["metadata"]["resourceVersion"] = str(
                                int(
                                    cluster["metadata"].get(
                                        "resourceVersion",
                                        "0",
                                    )
                                )
                                + 1
                            )
                            response = copy.deepcopy(cluster)
                        else:
                            response = None
                    if response is None:
                        self._write_json(status_payload(404), status=404)
                    else:
                        self._write_json(response)
                    return
                self._write_json(status_payload(404), status=404)

            def do_DELETE(self):
                self._unsupported_write("DELETE")

            def _unsupported_write(self, method: str) -> None:
                path = unquote(urlsplit(self.path).path)
                api._record(method, path, urlsplit(self.path).query)
                length = int(self.headers.get("Content-Length", "0"))
                if length:
                    self.rfile.read(length)
                self._write_json(status_payload(405), status=405)

            def _write_json(self, payload: dict, status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            @staticmethod
            def _backup_schedule(name: str, patch: dict | None = None) -> dict | None:
                with api._lock:
                    for schedule in api.backup_schedules or []:
                        if schedule.get("metadata", {}).get("name") != name:
                            continue
                        if patch:
                            schedule.setdefault("spec", {}).update((patch.get("spec") or {}))
                            metadata = schedule.setdefault("metadata", {})
                            metadata["resourceVersion"] = str(int(metadata.get("resourceVersion", "1")) + 1)
                        return copy.deepcopy(schedule)
                return None

            @staticmethod
            def _close_server() -> None:
                api._server.shutdown()
                api._server.server_close()

            def _statefulset(self) -> dict:
                with api._lock:
                    replicas = api.statefulset_replicas
                    if replicas == 0:
                        api._scaled_statefulset_reads += 1
                    if api.close_after_scale and api._scaled_statefulset_reads >= 2:
                        api._shutdown_scheduled = True
                return {
                    "apiVersion": "apps/v1",
                    "kind": "StatefulSet",
                    "metadata": {
                        "name": "thanos-compactor",
                        "namespace": "open-cluster-management-observability",
                        "resourceVersion": "1",
                        "generation": 1,
                    },
                    "spec": {
                        "replicas": replicas,
                        "selector": {"matchLabels": {"app": "thanos-compactor"}},
                        "serviceName": "thanos-compactor",
                        "updateStrategy": {"type": "RollingUpdate"},
                    },
                    "status": {
                        "replicas": replicas,
                        "readyReplicas": replicas,
                        "currentReplicas": replicas,
                        "updatedReplicas": replicas,
                        "observedGeneration": 1,
                        "currentRevision": "fixture-revision",
                        "updateRevision": "fixture-revision",
                    },
                }

        return Handler
