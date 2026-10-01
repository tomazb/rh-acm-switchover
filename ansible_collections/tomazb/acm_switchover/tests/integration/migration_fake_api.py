"""Stateful fake destination hub for non-live R4-04 activation integration tests.

Serves the strict reads the activation transaction makes (ACM Restores, Velero Backups and
Restores, the MultiClusterHub, ManagedClusters and API discovery including the Restore
status subresource) and the guarded Restore PATCH, DELETE and CREATE with their server-side
guards. Every request is recorded; every mutation also records the migration journal the
checkpoint held at that instant. A controller callback may update status and Velero
children after each mutation.
"""

from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

NS = "open-cluster-management-backup"
ACM_GV = "cluster.open-cluster-management.io/v1beta1"
STAMP = "20240102120000"
START = "2024-01-02T12:00:00Z"
MC_B = f"acm-managed-clusters-schedule-{STAMP}"
CRED_B = f"acm-credentials-schedule-{STAMP}"
RES_B = f"acm-resources-schedule-{STAMP}"
GEN_B = f"acm-resources-generic-schedule-{STAMP}"
PASSIVE = "restore-acm-passive-sync"
_MC_FIELD = "veleroManagedClustersBackupName"


def backup(name: str, start: str = START, uid: str | None = None, phase: str = "Completed", **status) -> dict:
    return {
        "apiVersion": "velero.io/v1",
        "kind": "Backup",
        "metadata": {"name": name, "namespace": NS, "uid": uid or f"uid-{name}", "resourceVersion": "7"},
        "status": {"phase": phase, "startTimestamp": start, "completionTimestamp": "2024-01-02T12:05:00Z", **status},
    }


LEGACY_INVENTORY = [backup(MC_B), backup(CRED_B), backup(RES_B), backup(GEN_B)]


def passive_restore(name: str = PASSIVE, *, mc: str = "skip", status_names: dict | None = None, **spec) -> dict:
    status = {"phase": "Enabled", "veleroCredentialsRestoreName": f"{name}-{CRED_B}", "veleroResourcesRestoreName": ""}
    status.update(status_names or {})
    return {
        "apiVersion": ACM_GV,
        "kind": "Restore",
        "metadata": {
            "name": name,
            "namespace": NS,
            "uid": "uid-passive",
            "resourceVersion": "1001",
            "generation": 3,
            "creationTimestamp": "2024-01-01T00:00:00Z",
        },
        "spec": {
            _MC_FIELD: mc,
            "veleroCredentialsBackupName": "latest",
            "veleroResourcesBackupName": "latest",
            "cleanupBeforeRestore": "CleanupRestored",
            "syncRestoreWithNewBackups": True,
            **spec,
        },
        "status": status,
    }


def velero_child(name: str, backup_name: str, owner: str, owner_uid: str, phase: str = "Completed") -> dict:
    return {
        "apiVersion": "velero.io/v1",
        "kind": "Restore",
        "metadata": {
            "name": name,
            "namespace": NS,
            "uid": f"uid-{name}",
            "resourceVersion": "9",
            "ownerReferences": [
                {
                    "apiVersion": ACM_GV,
                    "kind": "Restore",
                    "name": owner,
                    "uid": owner_uid,
                    "controller": True,
                }
            ],
        },
        "spec": {"backupName": backup_name},
        "status": {"phase": phase},
    }


def child_name(owner: str, backup_name: str, active: bool = False) -> str:
    return f"{owner}-{backup_name}"[:252] + ("-active" if active else "")


def complete(hub: "FakeMigrationHub", name: str, *, kind: str, phase=None, child_phase="Completed") -> dict:
    """Play the controller: publish the lane's children and status for the journaled Restore.

    The same cohort tests/test_activation_evidence.complete publishes for the Python CLI.
    """
    restore = hub.acm[name]
    owner_uid = restore["metadata"]["uid"]
    legacy = not hub.version.startswith("2.17")
    children = {}
    status = {}
    if kind == "passive_patch":
        children["mc"] = (child_name(name, MC_B), MC_B)
        children["cred_active"] = (child_name(name, CRED_B, True), CRED_B)
        children["gen_active"] = (child_name(name, GEN_B, True), GEN_B)
        children["cred_shared"] = (child_name(name, CRED_B), CRED_B)
        status["veleroManagedClustersRestoreName"] = children["mc"][0]
        if legacy:
            status["veleroCredentialsRestoreName"] = children["cred_active"][0]
            status["veleroGenericResourcesRestoreName"] = children["gen_active"][0]
        phase = phase or "Enabled"
    else:
        children["mc"] = (child_name(name, MC_B), MC_B)
        status["veleroManagedClustersRestoreName"] = children["mc"][0]
        if kind == "full_restore" or legacy:
            children["cred"] = (child_name(name, CRED_B), CRED_B)
            children["gen"] = (child_name(name, GEN_B), GEN_B)
            status["veleroCredentialsRestoreName"] = children["cred"][0]
            status["veleroGenericResourcesRestoreName"] = children["gen"][0]
        if kind == "full_restore":
            children["res"] = (child_name(name, RES_B), RES_B)
            status["veleroResourcesRestoreName"] = children["res"][0]
            if not legacy:
                children["cred_active"] = (child_name(name, CRED_B, True), CRED_B)
                children["gen_active"] = (child_name(name, GEN_B, True), GEN_B)
        phase = phase or "Finished"
    for child, backup_name in children.values():
        hub.velero[child] = velero_child(child, backup_name, name, owner_uid, phase=child_phase)
    restore.setdefault("status", {}).update(status, phase=phase)
    return children


def _resource(name: str, kind: str, namespaced: bool = True, verbs=("get", "list")) -> dict:
    return {"name": name, "singularName": "", "namespaced": namespaced, "kind": kind, "verbs": list(verbs)}


class FakeMigrationHub:
    """In-memory destination hub behind a local Kubernetes-compatible API."""

    def __init__(
        self,
        *,
        cluster_uid: str = "LIVE-SECONDARY",
        version: str = "2.14.3",
        restores: list[dict] | None = None,
        backups: list[dict] | None = None,
        velero_restores: list[dict] | None = None,
        managed_clusters: list[str] | None = None,
        status_served: bool = True,
        journal_probe: Callable[[], Any] | None = None,
        controller: Callable[["FakeMigrationHub", str, str], None] | None = None,
    ):
        self.cluster_uid = cluster_uid
        self.version = version
        self.acm = {item["metadata"]["name"]: copy.deepcopy(item) for item in (restores or [])}
        self.backups = {item["metadata"]["name"]: copy.deepcopy(item) for item in (backups or LEGACY_INVENTORY)}
        self.velero = {item["metadata"]["name"]: copy.deepcopy(item) for item in (velero_restores or [])}
        self.managed_clusters = list(managed_clusters if managed_clusters is not None else ["cluster-a"])
        # ManagedClusters served without the Available condition (all are Available and Joined otherwise).
        self.unavailable_clusters: set[str] = set()
        # ManagedClusters served with a stale disable-auto-import annotation (the fake never patches them).
        self.annotated_clusters: set[str] = set()
        # Successive ManagedCluster LISTs serve these name lists first, then managed_clusters.
        self.managed_cluster_reads: list[list[str]] = []
        self.status_served = status_served
        self.journal_probe = journal_probe or (lambda: None)
        self.controller = controller
        # (method, path prefix) -> HTTP status served instead of the object.
        self.failures: dict[tuple[str, str], int] = {}
        self.mutations: list[tuple[str, str, Any]] = []
        self._requests: list[dict] = []
        self._rv = 2000
        self._lock = threading.RLock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    @property
    def requests(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._requests)

    @property
    def writes(self) -> list[dict]:
        return [request for request in self.requests if request["method"] in {"POST", "PATCH", "PUT", "DELETE"}]

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def next_rv(self) -> str:
        self._rv += 1
        return str(self._rv)

    def _discovery(self) -> dict:
        restores = [_resource("restores", "Restore", verbs=("get", "list", "create", "patch", "delete"))]
        if self.status_served:
            restores.append(_resource("restores/status", "Restore", verbs=("get", "patch")))
        group = lambda name, versions: {  # noqa: E731
            "name": name,
            "versions": [{"groupVersion": f"{name}/{version}", "version": version} for version in versions],
            "preferredVersion": {"groupVersion": f"{name}/{versions[0]}", "version": versions[0]},
        }
        return {
            "/version": {"major": "1", "minor": "28", "gitVersion": "v1.28.0"},
            "/api": {"kind": "APIVersions", "versions": ["v1"], "serverAddressByClientCIDRs": []},
            "/api/v1": {
                "kind": "APIResourceList",
                "groupVersion": "v1",
                # ConfigMaps are listed so a mutation attempt reaches the API and is recorded;
                # every ConfigMap read is a 404 (absent import-controller-config: default strategy).
                "resources": [
                    _resource("namespaces", "Namespace", namespaced=False),
                    _resource("configmaps", "ConfigMap", verbs=("get", "list", "create", "patch")),
                ],
            },
            "/apis": {
                "kind": "APIGroupList",
                "groups": [
                    group("cluster.open-cluster-management.io", ["v1beta1", "v1"]),
                    group("velero.io", ["v1"]),
                    group("operator.open-cluster-management.io", ["v1"]),
                ],
            },
            f"/apis/{ACM_GV}": {"kind": "APIResourceList", "groupVersion": ACM_GV, "resources": restores},
            "/apis/cluster.open-cluster-management.io/v1": {
                "kind": "APIResourceList",
                "groupVersion": "cluster.open-cluster-management.io/v1",
                "resources": [_resource("managedclusters", "ManagedCluster", namespaced=False)],
            },
            "/apis/velero.io/v1": {
                "kind": "APIResourceList",
                "groupVersion": "velero.io/v1",
                "resources": [_resource("backups", "Backup"), _resource("restores", "Restore")],
            },
            "/apis/operator.open-cluster-management.io/v1": {
                "kind": "APIResourceList",
                "groupVersion": "operator.open-cluster-management.io/v1",
                "resources": [_resource("multiclusterhubs", "MultiClusterHub")],
            },
            "/api/v1/namespaces/kube-system": {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": "kube-system", "uid": self.cluster_uid, "resourceVersion": "1"},
            },
        }

    def _collection(self, path: str):
        """(store, list kind, api version, item name or None) for an object path, else None."""
        parts = path.strip("/").split("/")
        tables = {
            ("cluster.open-cluster-management.io", "v1beta1", "restores"): (self.acm, "RestoreList", ACM_GV),
            ("velero.io", "v1", "backups"): (self.backups, "BackupList", "velero.io/v1"),
            ("velero.io", "v1", "restores"): (self.velero, "RestoreList", "velero.io/v1"),
        }
        if len(parts) in (6, 7) and parts[0] == "apis" and parts[3] == "namespaces" and parts[4] == NS:
            table = tables.get((parts[1], parts[2], parts[5]))
            if table is not None:
                return (*table, parts[6] if len(parts) == 7 else None)
        return None

    def _list_payload(self, path: str):
        if path == "/apis/operator.open-cluster-management.io/v1/namespaces/open-cluster-management/multiclusterhubs":
            hub = {
                "apiVersion": "operator.open-cluster-management.io/v1",
                "kind": "MultiClusterHub",
                "metadata": {"name": "multiclusterhub", "namespace": "open-cluster-management", "resourceVersion": "5"},
                "status": {"currentVersion": self.version},
            }
            return {
                "kind": "MultiClusterHubList",
                "apiVersion": "operator.open-cluster-management.io/v1",
                "metadata": {"resourceVersion": "5"},
                "items": [hub],
            }
        if path == "/apis/cluster.open-cluster-management.io/v1/managedclusters":
            names = self.managed_cluster_reads.pop(0) if self.managed_cluster_reads else self.managed_clusters
            items = [
                {
                    "apiVersion": "cluster.open-cluster-management.io/v1",
                    "kind": "ManagedCluster",
                    "metadata": {
                        "name": name,
                        "resourceVersion": "3",
                        **(
                            {"annotations": {"import.open-cluster-management.io/disable-auto-import": ""}}
                            if name in self.annotated_clusters
                            else {}
                        ),
                    },
                    "status": {
                        "conditions": [
                            {
                                "type": "ManagedClusterConditionAvailable",
                                "status": "False" if name in self.unavailable_clusters else "True",
                            },
                            {"type": "ManagedClusterJoined", "status": "True"},
                        ]
                    },
                }
                for name in names + ["local-cluster"]
            ]
            return {
                "kind": "ManagedClusterList",
                "apiVersion": "cluster.open-cluster-management.io/v1",
                "metadata": {"resourceVersion": "3"},
                "items": items,
            }
        return None

    def _record(self, method: str, path: str) -> None:
        with self._lock:
            self._requests.append({"method": method, "path": path})

    def _failure(self, method: str, path: str) -> int | None:
        for (failed_method, prefix), status in self.failures.items():
            if failed_method == method and path.startswith(prefix):
                return status
        return None

    def _mutated(self, kind: str, name: str) -> None:
        self.mutations.append((kind, name, copy.deepcopy(self.journal_probe())))
        if self.controller is not None:
            self.controller(self, kind, name)

    def _handler(self):  # noqa: C901 - one local handler keeps the fake API stateful and auditable
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def _path(self) -> str:
                return unquote(urlsplit(self.path).path)

            def _body(self):
                length = int(self.headers.get("Content-Length", "0"))
                return json.loads(self.rfile.read(length).decode("utf-8")) if length else None

            def _json(self, payload, status=200):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _status(self, code, reason, message="fixture request failed"):
                self._json(
                    {
                        "apiVersion": "v1",
                        "kind": "Status",
                        "status": "Failure",
                        "message": message,
                        "reason": reason,
                        "code": code,
                    },
                    status=code,
                )

            def _start(self, method):
                path = self._path()
                hub._record(method, path)
                failure = hub._failure(method, path)
                if failure is not None:
                    self._status(failure, "InternalError")
                    return None
                return path

            def do_GET(self):
                path = self._start("GET")
                if path is None:
                    return
                with hub._lock:
                    static = hub._discovery().get(path)
                    if static is not None:
                        self._json(static)
                        return
                    listed = hub._list_payload(path)
                    if listed is not None:
                        self._json(listed)
                        return
                    target = hub._collection(path)
                    if target is None:
                        self._status(404, "NotFound")
                        return
                    store, list_kind, api_version, name = target
                    if name is None:
                        self._json(
                            {
                                "apiVersion": api_version,
                                "kind": list_kind,
                                "metadata": {"resourceVersion": hub.next_rv()},
                                "items": [copy.deepcopy(item) for item in store.values()],
                            }
                        )
                        return
                    if name not in store:
                        self._status(404, "NotFound")
                        return
                    self._json(copy.deepcopy(store[name]))

            def do_PATCH(self):
                path = self._start("PATCH")
                if path is None:
                    return
                body = self._body()
                with hub._lock:
                    target = hub._collection(path)
                    if target is None or target[0] is not hub.acm or target[3] is None:
                        self._status(404, "NotFound")
                        return
                    name = target[3]
                    if name not in hub.acm:
                        self._status(404, "NotFound")
                        return
                    current = hub.acm[name]
                    values = {
                        "/metadata/uid": current["metadata"].get("uid"),
                        "/metadata/resourceVersion": current["metadata"].get("resourceVersion"),
                        f"/spec/{_MC_FIELD}": current["spec"].get(_MC_FIELD),
                    }
                    for op in body or []:
                        if op.get("op") == "test" and values.get(op.get("path")) != op.get("value"):
                            self._status(422, "Invalid", "the json patch test failed")
                            return
                    for op in body or []:
                        if op.get("op") == "replace" and op.get("path") == f"/spec/{_MC_FIELD}":
                            current["spec"][_MC_FIELD] = op["value"]
                    current["metadata"]["generation"] += 1
                    current["metadata"]["resourceVersion"] = hub.next_rv()
                    hub._mutated("patch", name)
                    self._json(copy.deepcopy(hub.acm.get(name, current)))

            def do_DELETE(self):
                path = self._start("DELETE")
                if path is None:
                    return
                body = self._body() or {}
                with hub._lock:
                    target = hub._collection(path)
                    if target is None or target[0] is not hub.acm or target[3] is None or target[3] not in hub.acm:
                        self._status(404, "NotFound")
                        return
                    name = target[3]
                    current = hub.acm[name]["metadata"]
                    preconditions = body.get("preconditions") or {}
                    if preconditions.get("uid") != current.get("uid") or preconditions.get(
                        "resourceVersion"
                    ) != current.get("resourceVersion"):
                        self._status(409, "Conflict", "the delete preconditions failed")
                        return
                    del hub.acm[name]
                    hub._mutated("delete", name)
                    self._json({"apiVersion": "v1", "kind": "Status", "status": "Success"})

            def do_POST(self):
                path = self._start("POST")
                if path is None:
                    return
                body = self._body() or {}
                with hub._lock:
                    target = hub._collection(path)
                    if target is None or target[0] is not hub.acm or target[3] is not None:
                        self._status(404, "NotFound")
                        return
                    name = (body.get("metadata") or {}).get("name")
                    if name in hub.acm:
                        self._status(409, "AlreadyExists", "the name is already taken")
                        return
                    created = copy.deepcopy(body)
                    created["metadata"].update(
                        uid=f"uid-{name}",
                        resourceVersion=hub.next_rv(),
                        generation=1,
                        creationTimestamp="2024-01-03T00:00:00Z",
                    )
                    hub.acm[name] = created
                    hub._mutated("create", name)
                    self._json(copy.deepcopy(hub.acm.get(name, created)), status=201)

            def do_PUT(self):
                self._start("PUT")
                self._status(405, "MethodNotAllowed")

        return Handler
