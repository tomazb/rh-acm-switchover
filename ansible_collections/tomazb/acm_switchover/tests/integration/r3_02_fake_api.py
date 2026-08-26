"""Reusable fake Kubernetes API for non-live R3-02 integration tests."""

from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import unquote, urlsplit

SENTINEL = "R302-SENTINEL-HTTP-BODY"


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
        configmap_status: int = 200,
        configmap_body: dict | None = None,
        core_resources: list[dict] | None = None,
    ):
        self.pod_list_status = pod_list_status
        self.pod_list_body = copy.deepcopy(pod_list_body)
        self.configmap_status = configmap_status
        self.configmap_body = copy.deepcopy(configmap_body)
        self.core_resources = copy.deepcopy(core_resources)
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
        return [
            request
            for request in self.requests
            if request["method"] in {"POST", "PUT", "PATCH", "DELETE"}
        ]

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _record(self, method: str, path: str) -> None:
        with self._lock:
            self._requests.append({"method": method, "path": path})

    def _handler(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def do_GET(self):
                path = unquote(urlsplit(self.path).path)
                api._record("GET", path)
                if path == "/version":
                    self._write_json(
                        {"major": "1", "minor": "28", "gitVersion": "v1.28.0"}
                    )
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
                    self._write_json({"kind": "APIGroupList", "groups": []})
                    return
                if path == "/api/v1/namespaces/test-ns/pods":
                    payload = api.pod_list_body
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
                if path == "/api/v1/namespaces/test-ns/configmaps/test-config":
                    payload = api.configmap_body
                    if payload is None:
                        payload = {
                            "apiVersion": "v1",
                            "kind": "ConfigMap",
                            "metadata": {
                                "name": "test-config",
                                "namespace": "test-ns",
                                "resourceVersion": "1",
                            },
                            "data": {},
                        }
                    self._write_json(
                        copy.deepcopy(payload),
                        status=api.configmap_status,
                    )
                    return
                self._write_json(status_payload(404), status=404)

            def do_POST(self):
                self._unsupported_write("POST")

            def do_PUT(self):
                self._unsupported_write("PUT")

            def do_PATCH(self):
                self._unsupported_write("PATCH")

            def do_DELETE(self):
                self._unsupported_write("DELETE")

            def _unsupported_write(self, method: str) -> None:
                path = unquote(urlsplit(self.path).path)
                api._record(method, path)
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

        return Handler
