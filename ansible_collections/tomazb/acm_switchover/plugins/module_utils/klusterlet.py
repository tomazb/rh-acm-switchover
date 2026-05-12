# SPDX-License-Identifier: MIT
"""Shared klusterlet probe and remediation helpers for collection modules."""

from __future__ import annotations

import base64
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Protocol

import yaml

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
    HUB_KUBECONFIG_SECRET_NAME,
    KLUSTERLET_DEFAULT_WORKERS,
    MANAGED_CLUSTER_AGENT_NAMESPACE,
)

class CoreV1Client(Protocol):
    def read_namespaced_secret(self, name: str, namespace: str) -> dict | object:
        ...

    def delete_namespaced_secret(self, name: str, namespace: str) -> object:
        ...

    def create_namespaced_secret(self, namespace: str, body: dict) -> object:
        ...


class AppsV1Client(Protocol):
    def patch_namespaced_deployment(self, name: str, namespace: str, body: dict) -> object:
        ...


CoreClientFactory = Callable[[str, str | None], CoreV1Client]
AppsClientFactory = Callable[[str, str | None], AppsV1Client]


def normalize_workers(workers: int | None) -> int:
    if workers is None:
        return KLUSTERLET_DEFAULT_WORKERS
    try:
        worker_count = int(workers)
    except (TypeError, ValueError) as exc:
        raise ValueError("workers must be a positive integer") from exc
    if worker_count < 1:
        raise ValueError("workers must be a positive integer")
    return worker_count


def build_core_v1_client(kubeconfig: str, context: str | None = None):
    if not kubeconfig:
        raise ValueError("kubeconfig is required")

    from kubernetes import client, config

    kwargs = {"persist_config": False, "config_file": kubeconfig}
    if context:
        kwargs["context"] = context
    api_client = config.new_client_from_config(**kwargs)
    return client.CoreV1Api(api_client=api_client)


def build_apps_v1_client(kubeconfig: str, context: str | None = None):
    if not kubeconfig:
        raise ValueError("kubeconfig is required")

    from kubernetes import client, config

    kwargs = {"persist_config": False, "config_file": kubeconfig}
    if context:
        kwargs["context"] = context
    api_client = config.new_client_from_config(**kwargs)
    return client.AppsV1Api(api_client=api_client)


def read_secret(core_client: CoreV1Client, namespace: str, name: str) -> dict | object | None:
    try:
        return core_client.read_namespaced_secret(name=name, namespace=namespace)
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            return None
        raise


def error_summary(exc: Exception) -> str:
    status = getattr(exc, "status", None)
    reason = getattr(exc, "reason", None)
    if status is not None:
        return f"status={status} reason={reason or exc.__class__.__name__}"
    return f"{exc.__class__.__name__}: {exc}"


def secret_data(secret: dict | object | None) -> dict:
    if not secret:
        return {}
    if isinstance(secret, dict):
        return secret.get("data") or {}
    return getattr(secret, "data", None) or {}


def decode_b64_text(value: str) -> str:
    return base64.b64decode(value).decode("utf-8")


def kubeconfig_server_from_b64(kubeconfig_b64: str) -> str:
    if not kubeconfig_b64:
        return ""
    kubeconfig = yaml.safe_load(decode_b64_text(kubeconfig_b64)) or {}
    clusters = kubeconfig.get("clusters") or []
    if not clusters:
        return ""
    return clusters[0].get("cluster", {}).get("server", "") or ""


def server_host(server: str) -> str:
    return re.sub(r"^https?://([^:/]+).*$", r"\1", server or "")


def import_manifest_docs(import_yaml_b64: str) -> list[dict]:
    if not import_yaml_b64:
        return []
    return [doc for doc in yaml.safe_load_all(decode_b64_text(import_yaml_b64)) if isinstance(doc, dict)]


def bootstrap_secret_doc(import_yaml_b64: str) -> dict | None:
    for doc in import_manifest_docs(import_yaml_b64):
        if doc.get("kind") == "Secret" and doc.get("metadata", {}).get("name") == BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME:
            return doc
    return None


def bootstrap_kubeconfig_from_import(import_yaml_b64: str) -> str:
    doc = bootstrap_secret_doc(import_yaml_b64)
    if not doc:
        return ""
    return doc.get("data", {}).get("kubeconfig", "") or ""


def ordered_bounded_map(items: list[str], workers: int, fn: Callable[[str], dict]) -> list[dict]:
    if not items:
        return []
    if workers == 1:
        return [fn(item) for item in items]

    results: list[dict | None] = [None] * len(items)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fn, item): index for index, item in enumerate(items)}
        for future, index in futures.items():
            results[index] = future.result()
    return [item for item in results if item is not None]


def probe_one_cluster(
    cluster_name: str,
    secondary_client: CoreV1Client | None,
    managed_clusters: dict,
    core_client_factory: CoreClientFactory,
) -> dict:
    managed = managed_clusters.get(cluster_name) or {}
    kubeconfig = managed.get("kubeconfig") or ""
    context = managed.get("context")
    if not kubeconfig:
        return {"cluster": cluster_name, "status": "skipped", "reason": "no_managed_cluster_kubeconfig"}

    try:
        managed_client = core_client_factory(kubeconfig, context)
        current_secret = read_secret(
            managed_client,
            MANAGED_CLUSTER_AGENT_NAMESPACE,
            HUB_KUBECONFIG_SECRET_NAME,
        )
        if current_secret is None:
            current_secret = read_secret(
                managed_client,
                MANAGED_CLUSTER_AGENT_NAMESPACE,
                BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
            )
        current_kubeconfig = secret_data(current_secret).get("kubeconfig", "")
        if not current_kubeconfig:
            return {"cluster": cluster_name, "status": "skipped", "reason": "current_hub_secret_missing"}

        if secondary_client is None:
            return {"cluster": cluster_name, "status": "skipped", "reason": "secondary_hub_client_unavailable"}

        import_secret = read_secret(secondary_client, cluster_name, f"{cluster_name}-import")
        import_yaml = secret_data(import_secret).get("import.yaml", "")
        expected_kubeconfig = bootstrap_kubeconfig_from_import(import_yaml)
        if not expected_kubeconfig:
            return {"cluster": cluster_name, "status": "skipped", "reason": "expected_import_secret_missing"}

        current_server = kubeconfig_server_from_b64(current_kubeconfig)
        expected_server = kubeconfig_server_from_b64(expected_kubeconfig)
        current_host = server_host(current_server)
        expected_host = server_host(expected_server)
        if not current_host or not expected_host:
            return {
                "cluster": cluster_name,
                "status": "skipped",
                "reason": "hub_server_unavailable",
                "current_hub_server": current_server,
                "expected_hub_server": expected_server,
            }
        status = "verified" if current_host == expected_host else "wrong_hub"
        return {
            "cluster": cluster_name,
            "status": status,
            "current_hub_server": current_server,
            "expected_hub_server": expected_server,
        }
    except Exception as exc:
        return {"cluster": cluster_name, "status": "skipped", "reason": error_summary(exc)}


def probe_klusterlet_connections(
    secondary_hub: dict,
    managed_clusters: dict,
    candidate_clusters: list[str] | None = None,
    workers: int | None = None,
    core_client_factory: CoreClientFactory = build_core_v1_client,
) -> dict:
    worker_count = normalize_workers(workers)
    candidates = list(candidate_clusters if candidate_clusters is not None else managed_clusters.keys())
    secondary_client: CoreV1Client | None = None
    if any((managed_clusters.get(cluster) or {}).get("kubeconfig") for cluster in candidates):
        secondary_client = core_client_factory(secondary_hub.get("kubeconfig", ""), secondary_hub.get("context"))
    results = ordered_bounded_map(
        candidates,
        worker_count,
        lambda cluster: probe_one_cluster(cluster, secondary_client, managed_clusters, core_client_factory),
    )
    return {
        "changed": False,
        "failed": False,
        "workers": worker_count,
        "results": results,
        "verified_clusters": [item["cluster"] for item in results if item["status"] == "verified"],
        "wrong_hub_clusters": [item["cluster"] for item in results if item["status"] == "wrong_hub"],
        "skipped_clusters": [item["cluster"] for item in results if item["status"] == "skipped"],
    }


def _result(cluster_name: str, status: str, steps: dict, reason: str = "", changed: bool = False) -> dict:
    result = {
        "cluster": cluster_name,
        "status": status,
        "steps": steps,
        "changed": changed,
    }
    if reason:
        result["reason"] = reason
    return result


def _mark_pending_not_run(steps: dict) -> dict:
    return {key: ("not_run" if value == "pending" else value) for key, value in steps.items()}


def remediate_one_cluster(
    cluster_name: str,
    secondary_client: CoreV1Client,
    managed_clusters: dict,
    core_client_factory: CoreClientFactory,
    apps_client_factory: AppsClientFactory,
) -> dict:
    steps = {
        "import_secret_read": "pending",
        "bootstrap_secret_deleted": "pending",
        "bootstrap_secret_applied": "pending",
        "klusterlet_restarted": "pending",
    }
    managed = managed_clusters.get(cluster_name) or {}
    kubeconfig = managed.get("kubeconfig") or ""
    context = managed.get("context")
    if not kubeconfig:
        steps = {key: "skipped" for key in steps}
        return _result(cluster_name, "skipped", steps, reason="no_managed_cluster_kubeconfig")

    try:
        import_secret = read_secret(secondary_client, cluster_name, f"{cluster_name}-import")
        import_yaml = secret_data(import_secret).get("import.yaml", "")
        if not import_yaml:
            steps["import_secret_read"] = "missing"
            return _result(cluster_name, "failed", _mark_pending_not_run(steps), reason="import_secret_missing")
        steps["import_secret_read"] = "ok"

        bootstrap_doc = bootstrap_secret_doc(import_yaml)
        if not bootstrap_doc:
            steps["bootstrap_secret_applied"] = "missing"
            return _result(
                cluster_name,
                "failed",
                _mark_pending_not_run(steps),
                reason="bootstrap_secret_missing_from_import",
            )
        bootstrap_namespace = bootstrap_doc.get("metadata", {}).get("namespace") or MANAGED_CLUSTER_AGENT_NAMESPACE

        managed_core = core_client_factory(kubeconfig, context)
        apps_client = apps_client_factory(kubeconfig, context)
        changed = False
        try:
            managed_core.delete_namespaced_secret(
                name=BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
                namespace=bootstrap_namespace,
            )
            steps["bootstrap_secret_deleted"] = "ok"
            changed = True
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                steps["bootstrap_secret_deleted"] = "absent"
            else:
                steps["bootstrap_secret_deleted"] = "failed"
                return _result(
                    cluster_name,
                    "failed",
                    _mark_pending_not_run(steps),
                    reason=error_summary(exc),
                    changed=changed,
                )

        try:
            managed_core.create_namespaced_secret(namespace=bootstrap_namespace, body=bootstrap_doc)
            steps["bootstrap_secret_applied"] = "ok"
            changed = True
        except Exception as exc:
            if getattr(exc, "status", None) == 409:
                steps["bootstrap_secret_applied"] = "exists"
            else:
                steps["bootstrap_secret_applied"] = "failed"
                return _result(
                    cluster_name,
                    "failed",
                    _mark_pending_not_run(steps),
                    reason=error_summary(exc),
                    changed=changed,
                )

        restarted_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "acm-switchover/restartedAt": restarted_at,
                        }
                    }
                }
            }
        }
        try:
            apps_client.patch_namespaced_deployment(
                name="klusterlet",
                namespace=MANAGED_CLUSTER_AGENT_NAMESPACE,
                body=patch,
            )
            steps["klusterlet_restarted"] = "ok"
            changed = True
        except Exception as exc:
            steps["klusterlet_restarted"] = "failed"
            return _result(
                cluster_name, "failed", _mark_pending_not_run(steps), reason=error_summary(exc), changed=changed
            )

        return _result(cluster_name, "remediated", steps, changed=changed)
    except Exception as exc:
        return _result(cluster_name, "failed", _mark_pending_not_run(steps), reason=error_summary(exc))


def remediate_klusterlets(
    secondary_hub: dict,
    managed_clusters: dict,
    pending_clusters: list[str],
    workers: int | None = None,
    strict: bool = False,
    check_mode: bool = False,
    core_client_factory: CoreClientFactory = build_core_v1_client,
    apps_client_factory: AppsClientFactory = build_apps_v1_client,
) -> dict:
    worker_count = normalize_workers(workers)
    candidates = list(pending_clusters or [])
    if check_mode:
        results = []
        for cluster_name in candidates:
            if (managed_clusters.get(cluster_name) or {}).get("kubeconfig"):
                steps = {
                    "import_secret_read": "planned",
                    "bootstrap_secret_deleted": "planned",
                    "bootstrap_secret_applied": "planned",
                    "klusterlet_restarted": "planned",
                }
                results.append(_result(cluster_name, "planned", steps))
            else:
                steps = {
                    "import_secret_read": "skipped",
                    "bootstrap_secret_deleted": "skipped",
                    "bootstrap_secret_applied": "skipped",
                    "klusterlet_restarted": "skipped",
                }
                results.append(_result(cluster_name, "skipped", steps, reason="no_managed_cluster_kubeconfig"))
        planned_clusters = [item["cluster"] for item in results if item["status"] == "planned"]
        return {
            "changed": bool(planned_clusters),
            "failed": False,
            "workers": worker_count,
            "results": results,
            "failed_clusters": [],
            "skipped_clusters": [item["cluster"] for item in results if item["status"] == "skipped"],
            "remediated_clusters": [],
            "planned_clusters": planned_clusters,
        }
    secondary_client = None
    if any((managed_clusters.get(cluster) or {}).get("kubeconfig") for cluster in candidates):
        secondary_client = core_client_factory(secondary_hub.get("kubeconfig", ""), secondary_hub.get("context"))
    results = ordered_bounded_map(
        candidates,
        worker_count,
        lambda cluster: remediate_one_cluster(
            cluster,
            secondary_client,
            managed_clusters,
            core_client_factory,
            apps_client_factory,
        ),
    )
    failed_clusters = [item["cluster"] for item in results if item["status"] == "failed"]
    skipped_clusters = [item["cluster"] for item in results if item["status"] == "skipped"]
    changed = any(item.get("changed") for item in results)
    failed = strict and bool(failed_clusters)
    result = {
        "changed": changed,
        "failed": failed,
        "workers": worker_count,
        "results": results,
        "failed_clusters": failed_clusters,
        "skipped_clusters": skipped_clusters,
        "remediated_clusters": [item["cluster"] for item in results if item["status"] == "remediated"],
    }
    if failed:
        result["msg"] = "Klusterlet remediation failed for cluster(s): " + ", ".join(failed_clusters)
    return result
