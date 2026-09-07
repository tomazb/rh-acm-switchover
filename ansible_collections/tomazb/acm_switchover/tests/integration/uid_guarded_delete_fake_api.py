# SPDX-License-Identifier: MIT
"""A minimal real HTTP Kubernetes API for the guarded-delete runtime tests.

Purpose-built rather than reusing ``r3_02_fake_api``: this one must serve DELETE with
precondition evaluation for an arbitrary custom resource, which is the whole behaviour
under test. It evaluates ``preconditions.uid`` server-side exactly as a real API server
does, so the runtime tests exercise the precondition rather than trusting the client to
have sent it.
"""

from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

GROUP = "observability.open-cluster-management.io"
VERSION = "v1beta2"
PLURAL = "multiclusterobservabilities"
KIND = "MultiClusterObservability"


class FakeGuardedDeleteAPI:
    """Serves discovery plus one cluster-scoped CR, with real UID preconditions.

    ``on_delete`` lets a test mutate the store at the moment the delete lands, which is
    how the "replaced between read and delete" race is reproduced deterministically
    instead of hoped for.
    """

    def __init__(self, obj: dict | None = None, *, delete_status: int = 200, on_delete=None) -> None:
        self.obj = copy.deepcopy(obj) if obj else None
        self.delete_status = delete_status
        self.on_delete = on_delete
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

    def _record(self, method: str, path: str) -> None:
        with self._lock:
            self.requests.append({"method": method, "path": path})

    @property
    def delete_calls(self) -> list[dict]:
        with self._lock:
            return [r for r in self.requests if r["method"] == "DELETE"]

    def _handler(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):  # keep test output clean
                return

            def _send(self, status: int, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                path = self.path.split("?")[0]
                api._record("GET", path)
                if path == "/version":
                    return self._send(200, {"major": "1", "minor": "29"})
                if path == "/api":
                    return self._send(200, {"kind": "APIVersions", "versions": ["v1"]})
                if path == "/api/v1":
                    return self._send(200, {"kind": "APIResourceList", "groupVersion": "v1", "resources": []})
                if path == "/apis":
                    return self._send(
                        200,
                        {
                            "kind": "APIGroupList",
                            "groups": [
                                {
                                    "name": GROUP,
                                    "versions": [{"groupVersion": f"{GROUP}/{VERSION}", "version": VERSION}],
                                    "preferredVersion": {"groupVersion": f"{GROUP}/{VERSION}", "version": VERSION},
                                }
                            ],
                        },
                    )
                if path == f"/apis/{GROUP}/{VERSION}":
                    return self._send(
                        200,
                        {
                            "kind": "APIResourceList",
                            "groupVersion": f"{GROUP}/{VERSION}",
                            "resources": [
                                {
                                    "name": PLURAL,
                                    "singularName": "multiclusterobservability",
                                    "namespaced": False,
                                    "kind": KIND,
                                    "verbs": ["get", "list", "delete"],
                                }
                            ],
                        },
                    )
                if path == f"/apis/{GROUP}/{VERSION}/{PLURAL}/observability":
                    if api.obj is None:
                        return self._send(404, {"kind": "Status", "code": 404, "reason": "NotFound"})
                    return self._send(200, api.obj)
                return self._send(404, {"kind": "Status", "code": 404, "reason": "NotFound"})

            def do_DELETE(self):  # noqa: N802
                path = self.path.split("?")[0]
                api._record("DELETE", path)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {}

                if api.on_delete is not None:
                    api.on_delete(api)

                if api.delete_status != 200:
                    return self._send(
                        api.delete_status,
                        {"kind": "Status", "code": api.delete_status, "reason": "Conflict"},
                    )
                if api.obj is None:
                    return self._send(404, {"kind": "Status", "code": 404, "reason": "NotFound"})

                # Evaluate the UID precondition exactly as the API server would.
                wanted = (body.get("preconditions") or {}).get("uid")
                live = (api.obj.get("metadata") or {}).get("uid")
                if wanted is not None and wanted != live:
                    return self._send(
                        409,
                        {"kind": "Status", "code": 409, "reason": "Conflict", "message": "uid precondition"},
                    )
                api.obj = None
                return self._send(200, {"kind": "Status", "code": 200, "status": "Success"})

        return Handler


def mco_object(uid: str, resource_version: str = "1") -> dict:
    return {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": KIND,
        "metadata": {"name": "observability", "uid": uid, "resourceVersion": resource_version},
    }
