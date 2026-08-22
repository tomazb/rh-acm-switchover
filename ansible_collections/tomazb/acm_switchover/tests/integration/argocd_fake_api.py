"""Stateful fake Kubernetes API for non-live Argo CD role integration tests."""

from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import unquote, urlsplit

import yaml


def application(
    namespace: str,
    name: str,
    *,
    automated: bool,
    run_id: str | None = None,
    acm_touching: bool = True,
    resource_version: str = "1",
) -> dict:
    annotations = {}
    sync_policy: dict = {}
    if automated:
        sync_policy["automated"] = {"prune": True, "selfHeal": True}
    if run_id is not None:
        annotations["acm-switchover.argoproj.io/paused-by"] = run_id
        annotations["acm-switchover.argoproj.io/original-sync-policy"] = json.dumps(
            {"automated": {"prune": True, "selfHeal": True}}
        )
    touched_namespace = "open-cluster-management-backup" if acm_touching else "unrelated"
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "resourceVersion": resource_version,
            "annotations": annotations,
        },
        "spec": {"syncPolicy": sync_policy},
        "status": {
            "resources": [
                {
                    "kind": "BackupSchedule" if acm_touching else "ConfigMap",
                    "namespace": touched_namespace,
                    "name": f"{name}-resource",
                }
            ]
        },
    }


class FakeArgoCDHub:
    """Own mutable Application state and a local Kubernetes-compatible API."""

    def __init__(
        self,
        *,
        cluster_uid: str,
        applications: list[dict],
        namespace_list_failures: dict[str, str] | None = None,
        kube_system_status: int = 200,
        kube_system_body: dict | None = None,
    ):
        self.cluster_uid = cluster_uid
        self._applications = {
            (item["metadata"]["namespace"], item["metadata"]["name"]): copy.deepcopy(item) for item in applications
        }
        self._namespace_list_failures = dict(namespace_list_failures or {})
        self._kube_system_status = kube_system_status
        self._kube_system_body = copy.deepcopy(kube_system_body)
        self._requests: list[dict[str, str]] = []
        self.patches: list[dict] = []
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

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def get_application(self, namespace: str, name: str) -> dict:
        with self._lock:
            return copy.deepcopy(self._applications[(namespace, name)])

    def set_automated(self, namespace: str, name: str, enabled: bool) -> None:
        with self._lock:
            app = self._applications[(namespace, name)]
            sync_policy = app.setdefault("spec", {}).setdefault("syncPolicy", {})
            if enabled:
                sync_policy["automated"] = {"prune": True, "selfHeal": True}
            else:
                sync_policy.pop("automated", None)
            app["metadata"]["resourceVersion"] = str(int(app["metadata"]["resourceVersion"]) + 1)

    def _record_request(self, method: str, path: str) -> None:
        with self._lock:
            self._requests.append({"method": method, "path": path})

    def _configured_kube_system_response(self, path: str) -> tuple[int, dict] | None:
        if path != "/api/v1/namespaces/kube-system":
            return None
        if self._kube_system_status == 200 and self._kube_system_body is None:
            return None
        payload = self._kube_system_body
        if payload is None:
            payload = {
                "apiVersion": "v1",
                "kind": "Status",
                "status": "Failure",
                "message": "fixture kube-system identity request failed",
                "reason": "InternalError",
                "code": self._kube_system_status,
            }
        return self._kube_system_status, copy.deepcopy(payload)

    def _handler(self):  # noqa: C901 - one local handler keeps the fake API stateful and auditable
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def do_GET(self):
                path = unquote(urlsplit(self.path).path)
                hub._record_request("GET", path)
                kube_system_response = hub._configured_kube_system_response(path)
                if kube_system_response is not None:
                    status, payload = kube_system_response
                    self._write_json(payload, status=status)
                    return
                parts = path.strip("/").split("/")
                if (
                    len(parts) == 6
                    and parts[:4] == ["apis", "argoproj.io", "v1alpha1", "namespaces"]
                    and parts[5] == "applications"
                    and parts[4] in hub._namespace_list_failures
                ):
                    self._status(
                        500,
                        message=hub._namespace_list_failures[parts[4]],
                        reason="InternalError",
                    )
                    return
                payload = self._get_payload(path)
                if payload is None:
                    self._status(
                        404,
                        message="requested fixture resource was not found",
                        reason="NotFound",
                    )
                    return
                self._write_json(payload)

            def do_PATCH(self):
                path = unquote(urlsplit(self.path).path)
                hub._record_request("PATCH", path)
                parts = path.strip("/").split("/")
                if (
                    len(parts) == 7
                    and parts[:4] == ["apis", "argoproj.io", "v1alpha1", "namespaces"]
                    and parts[5] == "applications"
                ):
                    namespace = parts[4]
                    name = parts[6]
                else:
                    self._status(404, message="fixture patch path was not found", reason="NotFound")
                    return

                body = self._read_json()
                with hub._lock:
                    key = (namespace, name)
                    if key not in hub._applications:
                        self._status(404, message="fixture Application was not found", reason="NotFound")
                        return
                    current = hub._applications[key]
                    requested_rv = (body.get("metadata") or {}).get("resourceVersion")
                    if requested_rv and requested_rv != current["metadata"]["resourceVersion"]:
                        self._status(409, message="fixture resource version conflict", reason="Conflict")
                        return

                    before = copy.deepcopy(current)
                    self._apply_application_patch(current, body)
                    changed = before != current
                    if changed:
                        current["metadata"]["resourceVersion"] = str(int(current["metadata"]["resourceVersion"]) + 1)
                    hub.patches.append(
                        {
                            "namespace": namespace,
                            "name": name,
                            "changed": changed,
                            "body": copy.deepcopy(body),
                        }
                    )
                    payload = copy.deepcopy(current)
                self._write_json(payload)

            def do_POST(self):
                self._unsupported_write("POST")

            def do_PUT(self):
                self._unsupported_write("PUT")

            def do_DELETE(self):
                self._unsupported_write("DELETE")

            def _unsupported_write(self, method: str) -> None:
                path = unquote(urlsplit(self.path).path)
                hub._record_request(method, path)
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                self._status(
                    405,
                    message=f"fixture does not implement {method} for this path",
                    reason="MethodNotAllowed",
                )

            def _get_payload(self, path: str):
                static = {
                    "/version": {"major": "1", "minor": "28", "gitVersion": "v1.28.0"},
                    "/api": {
                        "kind": "APIVersions",
                        "versions": ["v1"],
                        "serverAddressByClientCIDRs": [],
                    },
                    "/api/v1": {
                        "kind": "APIResourceList",
                        "groupVersion": "v1",
                        "resources": [
                            {
                                "name": "namespaces",
                                "singularName": "",
                                "namespaced": False,
                                "kind": "Namespace",
                                "verbs": ["get", "list"],
                            }
                        ],
                    },
                    "/api/v1/namespaces/kube-system": {
                        "apiVersion": "v1",
                        "kind": "Namespace",
                        "metadata": {
                            "name": "kube-system",
                            "uid": hub.cluster_uid,
                            "resourceVersion": "1",
                        },
                    },
                    "/apis": {
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
                            },
                            {
                                "name": "cluster.open-cluster-management.io",
                                "versions": [
                                    {
                                        "groupVersion": "cluster.open-cluster-management.io/v1",
                                        "version": "v1",
                                    }
                                ],
                                "preferredVersion": {
                                    "groupVersion": "cluster.open-cluster-management.io/v1",
                                    "version": "v1",
                                },
                            },
                        ],
                    },
                    "/apis/argoproj.io/v1alpha1": {
                        "kind": "APIResourceList",
                        "groupVersion": "argoproj.io/v1alpha1",
                        "resources": [
                            {
                                "name": "applications",
                                "singularName": "application",
                                "namespaced": True,
                                "kind": "Application",
                                "verbs": ["get", "list", "patch"],
                            }
                        ],
                    },
                    "/apis/cluster.open-cluster-management.io/v1": {
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
                    },
                    "/apis/cluster.open-cluster-management.io/v1/managedclusters": {
                        "apiVersion": "cluster.open-cluster-management.io/v1",
                        "kind": "ManagedClusterList",
                        "metadata": {"resourceVersion": "1"},
                        "items": [],
                    },
                }
                if path in static:
                    return static[path]

                parts = path.strip("/").split("/")
                if parts == ["apis", "argoproj.io", "v1alpha1", "applications"]:
                    with hub._lock:
                        items = [copy.deepcopy(app) for app in hub._applications.values()]
                    return self._application_list(items)
                if (
                    len(parts) >= 6
                    and parts[:4] == ["apis", "argoproj.io", "v1alpha1", "namespaces"]
                    and parts[5] == "applications"
                ):
                    namespace = parts[4]
                    if len(parts) == 6:
                        with hub._lock:
                            items = [
                                copy.deepcopy(app)
                                for (app_namespace, _), app in hub._applications.items()
                                if app_namespace == namespace
                            ]
                        return self._application_list(items)
                    if len(parts) == 7:
                        with hub._lock:
                            app = hub._applications.get((namespace, parts[6]))
                            return copy.deepcopy(app) if app is not None else None
                return None

            @staticmethod
            def _application_list(items: list[dict]) -> dict:
                return {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "ApplicationList",
                    "metadata": {"resourceVersion": "1"},
                    "items": items,
                }

            @staticmethod
            def _apply_application_patch(current: dict, patch: dict) -> None:
                metadata_patch = patch.get("metadata") or {}
                annotations_patch = metadata_patch.get("annotations") or {}
                annotations = current.setdefault("metadata", {}).setdefault("annotations", {})
                for key, value in annotations_patch.items():
                    if value is None:
                        annotations.pop(key, None)
                    else:
                        annotations[key] = value

                spec_patch = patch.get("spec") or {}
                if "syncPolicy" in spec_patch:
                    sync_policy_patch = spec_patch.get("syncPolicy") or {}
                    sync_policy = current.setdefault("spec", {}).setdefault("syncPolicy", {})
                    for key, value in sync_policy_patch.items():
                        if value is None:
                            sync_policy.pop(key, None)
                        else:
                            sync_policy[key] = copy.deepcopy(value)

            def _read_json(self) -> dict:
                length = int(self.headers.get("Content-Length", "0"))
                return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

            def _status(self, status: int, *, message: str, reason: str) -> None:
                self._write_json(
                    {
                        "apiVersion": "v1",
                        "kind": "Status",
                        "status": "Failure",
                        "message": message,
                        "reason": reason,
                        "code": status,
                    },
                    status=status,
                )

            def _write_json(self, payload: dict, status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler


def write_kubeconfig(
    path: Path,
    *,
    context: str,
    server: str,
    token: str | None = None,
    username: str = "fixture",
    password: str = "fixture",
) -> None:
    user = {"username": username, "password": password}
    if token is not None:
        user["token"] = token
    path.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "clusters": [
                    {
                        "name": f"{context}-cluster",
                        "cluster": {
                            "server": server,
                            "insecure-skip-tls-verify": True,
                        },
                    }
                ],
                "contexts": [
                    {
                        "name": context,
                        "context": {
                            "cluster": f"{context}-cluster",
                            "user": f"{context}-user",
                        },
                    }
                ],
                "current-context": context,
                "users": [
                    {
                        "name": f"{context}-user",
                        "user": user,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
