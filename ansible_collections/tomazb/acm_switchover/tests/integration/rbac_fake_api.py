"""Fake Kubernetes API that stores what `kubernetes.core.k8s state=present` creates.

Serves discovery for the kinds the bundled RBAC manifests use (Namespace, ServiceAccount,
Role, RoleBinding, ClusterRole, ClusterRoleBinding), answers GET from what was created and
POST by storing the object. Every request is recorded with its query string; a write that
carries dryRun=All stores nothing, as on a real API server.
"""

from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, unquote, urlsplit

_CORE = [("namespaces", "Namespace", False), ("serviceaccounts", "ServiceAccount", True)]
_RBAC = [
    ("roles", "Role", True),
    ("rolebindings", "RoleBinding", True),
    ("clusterroles", "ClusterRole", False),
    ("clusterrolebindings", "ClusterRoleBinding", False),
]
_RBAC_GROUP = "rbac.authorization.k8s.io"


def _resource_list(group_version: str, resources) -> dict:
    return {
        "kind": "APIResourceList",
        "groupVersion": group_version,
        "resources": [
            {
                "name": name,
                "singularName": kind.lower(),
                "namespaced": namespaced,
                "kind": kind,
                "verbs": ["create", "get", "list", "patch"],
            }
            for name, kind, namespaced in resources
        ],
    }


def _status(code: int, reason: str) -> dict:
    return {"apiVersion": "v1", "kind": "Status", "status": "Failure", "reason": reason, "code": code}


class FakeRBACAPI:
    """Record every request; store created objects by their item path."""

    def __init__(self):
        self.objects: dict[str, dict] = {}
        self._requests: list[dict[str, str]] = []
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

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _handler(self):
        api = self
        static = {
            "/version": {"major": "1", "minor": "28", "gitVersion": "v1.28.0"},
            "/api": {"kind": "APIVersions", "versions": ["v1"], "serverAddressByClientCIDRs": []},
            "/api/v1": _resource_list("v1", _CORE),
            "/apis": {
                "kind": "APIGroupList",
                "groups": [
                    {
                        "name": _RBAC_GROUP,
                        "versions": [{"groupVersion": f"{_RBAC_GROUP}/v1", "version": "v1"}],
                        "preferredVersion": {"groupVersion": f"{_RBAC_GROUP}/v1", "version": "v1"},
                    }
                ],
            },
            f"/apis/{_RBAC_GROUP}/v1": _resource_list(f"{_RBAC_GROUP}/v1", _RBAC),
        }

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def _record(self, method: str) -> tuple[str, str]:
                parts = urlsplit(self.path)
                path = unquote(parts.path)
                with api._lock:
                    api._requests.append({"method": method, "path": path, "query": parts.query})
                return path, parts.query

            def do_GET(self):
                path, _query = self._record("GET")
                if path in static:
                    self._write(static[path])
                    return
                with api._lock:
                    stored = copy.deepcopy(api.objects.get(path))
                if stored is None:
                    self._write(_status(404, "NotFound"), 404)
                else:
                    self._write(stored)

            def do_POST(self):
                path, query = self._record("POST")
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                name = (body.get("metadata") or {}).get("name", "")
                item_path = f"{path}/{name}"
                created = copy.deepcopy(body)
                created.setdefault("metadata", {})["resourceVersion"] = "1"
                if "All" not in parse_qs(query).get("dryRun", []):
                    with api._lock:
                        api.objects[item_path] = copy.deepcopy(created)
                self._write(created, 201)

            def do_PUT(self):
                self._refuse("PUT")

            def do_PATCH(self):
                self._refuse("PATCH")

            def do_DELETE(self):
                self._refuse("DELETE")

            def _refuse(self, method: str) -> None:
                self._record(method)
                length = int(self.headers.get("Content-Length", "0"))
                if length:
                    self.rfile.read(length)
                self._write(_status(405, "MethodNotAllowed"), 405)

            def _write(self, payload: dict, status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler
