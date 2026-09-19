# SPDX-License-Identifier: MIT
"""A minimal real HTTP Kubernetes API for the acm_pod_owner_classify runtime tests.

Serves discovery for the core, apps and OLM operators groups plus a caller-supplied table of
object routes in the ACM namespace. Any unlisted path is a 404, and every request is recorded,
so a test asserts both what the shipped module concluded and that it only ever read.
"""

from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

ACM_NS = "open-cluster-management"
CSV_GROUP_VERSION = "operators.coreos.com/v1alpha1"

_DISCOVERY = {
    "/api/v1": [
        {"name": "namespaces", "singularName": "namespace", "namespaced": False, "kind": "Namespace", "verbs": ["get"]},
        {"name": "pods", "singularName": "pod", "namespaced": True, "kind": "Pod", "verbs": ["get", "list"]},
    ],
    "/apis/apps/v1": [
        {
            "name": "deployments",
            "singularName": "deployment",
            "namespaced": True,
            "kind": "Deployment",
            "verbs": ["get"],
        },
        {
            "name": "replicasets",
            "singularName": "replicaset",
            "namespaced": True,
            "kind": "ReplicaSet",
            "verbs": ["get"],
        },
    ],
    f"/apis/{CSV_GROUP_VERSION}": [
        {
            "name": "clusterserviceversions",
            "singularName": "clusterserviceversion",
            "namespaced": True,
            "kind": "ClusterServiceVersion",
            "verbs": ["get", "list"],
        }
    ],
}


class FakePodOwnerAPI:
    """Discovery plus ``routes``: an exact request path mapped to ``(status, body)``."""

    def __init__(self, routes: dict[str, tuple[int, dict]]) -> None:
        self.routes = copy.deepcopy(routes)
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _handler(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def _send(self, status: int, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _record(self, method: str) -> str:
                path = self.path.split("?")[0]
                with api._lock:
                    api.requests.append({"method": method, "path": path})
                return path

            def do_GET(self):  # noqa: N802
                path = self._record("GET")
                if path == "/version":
                    return self._send(200, {"major": "1", "minor": "29"})
                if path == "/api":
                    return self._send(200, {"kind": "APIVersions", "versions": ["v1"]})
                if path == "/apis":
                    groups = []
                    for group_version in ("apps/v1", CSV_GROUP_VERSION):
                        group, version = group_version.split("/")
                        entry = {"groupVersion": group_version, "version": version}
                        groups.append({"name": group, "versions": [entry], "preferredVersion": entry})
                    return self._send(200, {"kind": "APIGroupList", "groups": groups})
                if path in _DISCOVERY:
                    group_version = path.split("/", 2)[2] if path.startswith("/apis/") else "v1"
                    return self._send(
                        200,
                        {"kind": "APIResourceList", "groupVersion": group_version, "resources": _DISCOVERY[path]},
                    )
                if path in api.routes:
                    status, body = api.routes[path]
                    return self._send(status, body)
                return self._send(404, {"kind": "Status", "code": 404, "reason": "NotFound"})

            def _refuse(self, method: str):
                self._record(method)
                return self._send(405, {"kind": "Status", "code": 405, "reason": "MethodNotAllowed"})

            def do_POST(self):  # noqa: N802
                return self._refuse("POST")

            def do_PUT(self):  # noqa: N802
                return self._refuse("PUT")

            def do_PATCH(self):  # noqa: N802
                return self._refuse("PATCH")

            def do_DELETE(self):  # noqa: N802
                return self._refuse("DELETE")

        return Handler
