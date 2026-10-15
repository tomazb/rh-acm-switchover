# SPDX-License-Identifier: MIT
"""Shared klusterlet probe and remediation helpers for collection modules.

R4-06 PR 2 (docs/plans/2026-10-15-r4-06-current-base-amendment.md §2.3-§2.8): a mapped
cluster is inspected only after this invocation has proven its identity - a fresh
ManagedCluster read, the expected hub from its import secret, the mapped kubeconfig
file validated, the mapped server equal to the ManagedCluster URL - and its spoke client
is bound to that endpoint before any read. Only a fresh ``wrong_hub`` is ever remediated,
and any unproven cluster blocks every mutation in the batch.
"""

from __future__ import annotations

import base64
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import Callable, Protocol

import yaml

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
    HUB_KUBECONFIG_SECRET_NAME,
    KLUSTERLET_DEFAULT_WORKERS,
    KLUSTERLET_MAX_WORKERS,
    KLUSTERLET_RECHECK_INTERVAL,
    KLUSTERLET_REQUEST_TIMEOUT,
    KLUSTERLET_WORKER_TIMEOUT,
    MANAGED_CLUSTER_AGENT_NAMESPACE,
    WORKER_TIMEOUT_REASON_TEMPLATE,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.endpoint_identity import (
    REASON_CLIENT_ENDPOINT_MISMATCH,
    REASON_ENDPOINT_UNAVAILABLE,
    REASON_EXPECTED_ENDPOINT_MALFORMED,
    REASON_EXPECTED_ENDPOINT_UNAVAILABLE,
    classify_hub,
    managed_cluster_url,
    normalized_equal,
    parse_endpoint,
    prove_target_identity,
    redact,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.kubeconfig_file import (
    KubeconfigFileError,
    load_kubeconfig_file,
    select_context,
)


class CoreV1Client(Protocol):
    def read_namespaced_secret(self, name: str, namespace: str, **kwargs) -> dict | object: ...

    def create_namespaced_secret(self, namespace: str, body: dict, **kwargs) -> object: ...

    def patch_namespaced_secret(self, name: str, namespace: str, body: dict, **kwargs) -> object: ...


class AppsV1Client(Protocol):
    def patch_namespaced_deployment(self, name: str, namespace: str, body: dict, **kwargs) -> object: ...


CoreClientFactory = Callable[[str, str | None], CoreV1Client]
AppsClientFactory = Callable[[str, str | None], AppsV1Client]
# A ManagedCluster object by name; None when the hub answers 404. Any other error raises.
ManagedClusterReader = Callable[[str], "dict | None"]
ManagedClusterReaderFactory = Callable[[str, "str | None", "int | float"], ManagedClusterReader]


def normalize_workers(workers: int | None) -> int:
    if workers is None:
        return KLUSTERLET_DEFAULT_WORKERS
    try:
        worker_count = int(workers)
    except (TypeError, ValueError) as exc:
        raise ValueError("workers must be a positive integer") from exc
    if worker_count < 1:
        raise ValueError("workers must be a positive integer")
    if worker_count > KLUSTERLET_MAX_WORKERS:
        raise ValueError(
            f"workers must be at most {KLUSTERLET_MAX_WORKERS}; lower "
            "acm_switchover_execution.concurrency.klusterlet_probe_workers / klusterlet_remediation_workers"
        )
    return worker_count


def normalize_timeout(timeout: int | float | None, field_name: str) -> int | float:
    if timeout is None:
        return KLUSTERLET_REQUEST_TIMEOUT if field_name == "request_timeout" else KLUSTERLET_WORKER_TIMEOUT
    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive number") from exc
    if timeout_value <= 0:
        raise ValueError(f"{field_name} must be a positive number")
    if timeout_value.is_integer():
        return int(timeout_value)
    return timeout_value


def build_core_v1_client(
    kubeconfig: str,
    context: str | None = None,
    request_timeout: int | float | None = None,
):
    if not kubeconfig:
        raise ValueError("kubeconfig is required")

    from kubernetes import client, config

    kwargs = {"persist_config": False, "config_file": kubeconfig}
    if context:
        kwargs["context"] = context
    api_client = config.new_client_from_config(**kwargs)
    api_client.configuration.timeout = normalize_timeout(request_timeout, "request_timeout")
    return client.CoreV1Api(api_client=api_client)


def build_apps_v1_client(
    kubeconfig: str,
    context: str | None = None,
    request_timeout: int | float | None = None,
):
    if not kubeconfig:
        raise ValueError("kubeconfig is required")

    from kubernetes import client, config

    kwargs = {"persist_config": False, "config_file": kubeconfig}
    if context:
        kwargs["context"] = context
    api_client = config.new_client_from_config(**kwargs)
    api_client.configuration.timeout = normalize_timeout(request_timeout, "request_timeout")
    return client.AppsV1Api(api_client=api_client)


def build_managed_cluster_reader(
    kubeconfig: str,
    context: str | None = None,
    request_timeout: int | float | None = None,
) -> ManagedClusterReader:
    """A named ManagedCluster read through the secondary hub (ManagedCluster ``get``).

    Every invocation reads the URL itself (amendment §2.6 rule 4); nothing published by an
    earlier task is trusted. A 404 is ``None``; any other outcome raises, so a read that
    cannot complete is never mistaken for an absent URL.
    """
    if not kubeconfig:
        raise ValueError("kubeconfig is required")

    from kubernetes import client, config

    kwargs = {"persist_config": False, "config_file": kubeconfig}
    if context:
        kwargs["context"] = context
    api_client = config.new_client_from_config(**kwargs)
    custom = client.CustomObjectsApi(api_client=api_client)
    timeout = normalize_timeout(request_timeout, "request_timeout")

    def read(name: str) -> dict | None:
        try:
            managed_cluster = custom.get_cluster_custom_object(
                "cluster.open-cluster-management.io", "v1", "managedclusters", name, _request_timeout=timeout
            )
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                return None
            raise
        if not isinstance(managed_cluster, dict):
            raise ValueError("malformed ManagedCluster response")
        return managed_cluster

    return read


def request_timeout_kwargs(request_timeout: int | float | None) -> dict:
    return {"_request_timeout": normalize_timeout(request_timeout, "request_timeout")}


def worker_timeout_reason(timeout: int | float) -> str:
    return WORKER_TIMEOUT_REASON_TEMPLATE.format(timeout=timeout)


def read_secret(
    core_client: CoreV1Client,
    namespace: str,
    name: str,
    request_timeout: int | float | None = None,
) -> dict | object | None:
    try:
        return core_client.read_namespaced_secret(
            name=name,
            namespace=namespace,
            **request_timeout_kwargs(request_timeout),
        )
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


def hub_server_from_b64(kubeconfig_b64: str) -> tuple[bool, object]:
    """``(present, server)`` from a base64 hub kubeconfig (amendment §2.3).

    Absent - no kubeconfig, an empty document, no clusters, or no ``server`` key - is
    ``(False, None)``. A present ``server`` is returned unvalidated, so a null, empty or
    non-string value is classified as malformed rather than skipped. Undecodable or
    structurally unreadable YAML raises, which the probe reports as ``failed`` (the
    collection's existing outcome for unreadable embedded YAML).
    """
    if not kubeconfig_b64:
        return False, None
    kubeconfig = yaml.safe_load(decode_b64_text(kubeconfig_b64))
    if kubeconfig is None:
        return False, None
    clusters = kubeconfig.get("clusters") or []
    if not clusters:
        return False, None
    cluster = clusters[0].get("cluster", {})
    if "server" not in cluster:
        return False, None
    return True, cluster["server"]


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


def ordered_bounded_map(
    items: list[str],
    workers: int,
    fn: Callable[[str], dict],
    future_timeout: int | float | None = None,
    timeout_result: Callable[[str], dict] | None = None,
) -> list[dict]:
    if not items:
        return []

    results: list[dict | None] = [None] * len(items)
    timeout = normalize_timeout(future_timeout, "future_timeout")
    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {executor.submit(fn, item): index for index, item in enumerate(items)}
        done, not_done = wait(futures, timeout=timeout)

        for future in done:
            index = futures[future]
            results[index] = future.result()

        for future in not_done:
            index = futures[future]
            item = items[index]
            future.cancel()
            if timeout_result:
                results[index] = timeout_result(item)
            else:
                results[index] = {
                    "cluster": item,
                    "status": "failed",
                    "reason": worker_timeout_reason(timeout),
                }
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return [item for item in results if item is not None]


def _client_host(api: object) -> object:
    return getattr(getattr(getattr(api, "api_client", None), "configuration", None), "host", None)


class _Refusal(Exception):
    """A per-cluster refusal decided before any spoke read or mutation."""

    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _prove_target(
    cluster_name: str,
    managed: dict,
    managed_cluster_reader: ManagedClusterReader,
    secondary_client: CoreV1Client,
    request_timeout: int | float | None,
) -> tuple[str, str, str, str]:
    """Amendment §2.4/§2.6: ``(context, api_url, expected_hub, import_yaml_b64)``, or raise.

    Everything here happens before any spoke client exists: a fresh ManagedCluster read,
    the URL rule, the expected hub from the import secret, the mapped file's validation,
    context selection and the identity proof. Each failure is a ``_Refusal``.
    """
    managed_cluster = managed_cluster_reader(cluster_name)
    if managed_cluster is None:
        raise _Refusal("failed", REASON_ENDPOINT_UNAVAILABLE)
    api_url, url_reason = managed_cluster_url(managed_cluster)
    if api_url is None:
        raise _Refusal("failed", url_reason or REASON_ENDPOINT_UNAVAILABLE)

    import_secret = read_secret(
        secondary_client,
        cluster_name,
        f"{cluster_name}-import",
        request_timeout=request_timeout,
    )
    import_yaml = secret_data(import_secret).get("import.yaml", "")
    try:
        present, expected_hub = hub_server_from_b64(bootstrap_kubeconfig_from_import(import_yaml))
    except Exception:  # noqa: BLE001 - parser messages can quote secret material; never report them
        present, expected_hub = False, None
    if not present:
        raise _Refusal("unverified", REASON_EXPECTED_ENDPOINT_UNAVAILABLE)
    if not isinstance(expected_hub, str) or parse_endpoint(expected_hub)[0] is None:
        raise _Refusal("unverified", REASON_EXPECTED_ENDPOINT_MALFORMED)

    try:
        document = load_kubeconfig_file(managed.get("kubeconfig") or "")
        context_name, cluster_entry = select_context(document, managed.get("context"))
    except KubeconfigFileError as exc:
        raise _Refusal("failed", exc.reason) from None
    mapped_server = (cluster_entry or {}).get("cluster", {}).get("server")
    identity_reason = prove_target_identity(api_url, mapped_server)
    if identity_reason:
        raise _Refusal("failed", identity_reason)
    return context_name, api_url, expected_hub, import_yaml


def _bound_spoke_client(factory: Callable, kubeconfig: str, context: str, api_url: str):
    """A spoke client whose host is the proven endpoint (amendment §2.7), or a refusal."""
    api = factory(kubeconfig, context)
    if not normalized_equal(_client_host(api), api_url):
        raise _Refusal("unverified", REASON_CLIENT_ENDPOINT_MISMATCH)
    return api


def _classify_spoke(
    cluster_name: str,
    managed_client: CoreV1Client,
    expected_hub: str,
    request_timeout: int | float | None,
) -> dict:
    current_secret = read_secret(
        managed_client,
        MANAGED_CLUSTER_AGENT_NAMESPACE,
        HUB_KUBECONFIG_SECRET_NAME,
        request_timeout=request_timeout,
    )
    if current_secret is None:
        current_secret = read_secret(
            managed_client,
            MANAGED_CLUSTER_AGENT_NAMESPACE,
            BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
            request_timeout=request_timeout,
        )
    current_kubeconfig = secret_data(current_secret).get("kubeconfig", "")
    if not current_kubeconfig:
        return {"cluster": cluster_name, "status": "skipped", "reason": "current_hub_secret_missing"}
    try:
        present, current_server = hub_server_from_b64(current_kubeconfig)
    except Exception:  # noqa: BLE001 - unreadable YAML stays `failed`; its parser text can quote secrets
        return {"cluster": cluster_name, "status": "failed", "reason": "hub_kubeconfig_unreadable"}
    if not present:
        return {
            "cluster": cluster_name,
            "status": "skipped",
            "reason": "hub_server_unavailable",
            "expected_hub_server": redact(expected_hub),
        }
    status, reason = classify_hub(expected_hub, current_server)
    result = {
        "cluster": cluster_name,
        "status": status,
        "current_hub_server": redact(current_server),
        "expected_hub_server": redact(expected_hub),
    }
    if reason:
        result["reason"] = reason
    return result


def probe_one_cluster(
    cluster_name: str,
    secondary_client: CoreV1Client | None,
    managed_clusters: dict,
    core_client_factory: CoreClientFactory,
    request_timeout: int | float | None = None,
    managed_cluster_reader: ManagedClusterReader | None = None,
) -> dict:
    """Probe one mapped cluster (amendment §2.3, §2.6, §2.7).

    The target is proven before any spoke client exists, and the spoke client is bound to
    the proven endpoint before anything is read through it.
    """
    managed = managed_clusters.get(cluster_name) or {}
    kubeconfig = managed.get("kubeconfig") or ""
    if not kubeconfig:
        return {
            "cluster": cluster_name,
            "status": "skipped",
            "reason": "no_managed_cluster_kubeconfig",
        }
    if secondary_client is None or managed_cluster_reader is None:
        return {
            "cluster": cluster_name,
            "status": "skipped",
            "reason": "secondary_hub_client_unavailable",
        }

    try:
        context_name, api_url, expected_hub, _import_yaml = _prove_target(
            cluster_name, managed, managed_cluster_reader, secondary_client, request_timeout
        )
        managed_client = _bound_spoke_client(core_client_factory, kubeconfig, context_name, api_url)
        return _classify_spoke(cluster_name, managed_client, expected_hub, request_timeout)
    except _Refusal as refusal:
        return {"cluster": cluster_name, "status": refusal.status, "reason": refusal.reason}
    except Exception as exc:
        return {
            "cluster": cluster_name,
            "status": "failed",
            "reason": error_summary(exc),
        }


def _hub_clients(
    secondary_hub: dict,
    core_client_factory: CoreClientFactory,
    managed_cluster_reader_factory: ManagedClusterReaderFactory,
    request_timeout: int | float,
) -> tuple[CoreV1Client, ManagedClusterReader]:
    kubeconfig = secondary_hub.get("kubeconfig", "")
    context = secondary_hub.get("context")
    return (
        core_client_factory(kubeconfig, context),
        managed_cluster_reader_factory(kubeconfig, context, request_timeout),
    )


def probe_klusterlet_connections(
    secondary_hub: dict,
    managed_clusters: dict,
    candidate_clusters: list[str] | None = None,
    workers: int | None = None,
    request_timeout: int | float | None = None,
    future_timeout: int | float | None = None,
    wait_timeout: int | float | None = None,
    wait_interval: int | float | None = None,
    check_mode: bool = False,
    core_client_factory: CoreClientFactory = build_core_v1_client,
    managed_cluster_reader_factory: ManagedClusterReaderFactory | None = None,
) -> dict:
    worker_count = normalize_workers(workers)
    request_timeout_value = normalize_timeout(request_timeout, "request_timeout")
    future_timeout_value = normalize_timeout(future_timeout, "future_timeout")
    candidates = list(candidate_clusters if candidate_clusters is not None else managed_clusters.keys())
    reader_factory = managed_cluster_reader_factory or build_managed_cluster_reader

    def build_probe_result(results: list[dict]) -> dict:
        # An unverified cluster fails the probe exactly as a failed one does (amendment §2.8).
        failed_clusters = [item["cluster"] for item in results if item["status"] in ("failed", "unverified")]
        return {
            "changed": False,
            "failed": bool(failed_clusters),
            "workers": worker_count,
            "results": results,
            "verified_clusters": [item["cluster"] for item in results if item["status"] == "verified"],
            "wrong_hub_clusters": [item["cluster"] for item in results if item["status"] == "wrong_hub"],
            "unverified_clusters": [item["cluster"] for item in results if item["status"] == "unverified"],
            "skipped_clusters": [item["cluster"] for item in results if item["status"] == "skipped"],
            "failed_clusters": failed_clusters,
        }

    if check_mode:
        results = [{"cluster": cluster, "status": "planned", "reason": "check_mode"} for cluster in candidates]
        result = build_probe_result(results)
        result["planned_clusters"] = candidates
        return result

    secondary_client: CoreV1Client | None = None
    managed_cluster_reader: ManagedClusterReader | None = None
    if any((managed_clusters.get(cluster) or {}).get("kubeconfig") for cluster in candidates):
        # Preserve the probe contract: secondary hub client setup failures are
        # per-cluster failed probe results, not generic module exceptions.
        try:
            secondary_client, managed_cluster_reader = _hub_clients(
                secondary_hub, core_client_factory, reader_factory, request_timeout_value
            )
        except Exception as exc:
            reason = error_summary(exc)
            results = [
                (
                    {
                        "cluster": cluster,
                        "status": "failed",
                        "reason": reason,
                    }
                    if (managed_clusters.get(cluster) or {}).get("kubeconfig")
                    else {
                        "cluster": cluster,
                        "status": "skipped",
                        "reason": "no_managed_cluster_kubeconfig",
                    }
                )
                for cluster in candidates
            ]
            return build_probe_result(results)

    def run_probe_once() -> dict:
        results = ordered_bounded_map(
            candidates,
            worker_count,
            lambda cluster: probe_one_cluster(
                cluster,
                secondary_client,
                managed_clusters,
                core_client_factory,
                request_timeout=request_timeout_value,
                managed_cluster_reader=managed_cluster_reader,
            ),
            future_timeout=future_timeout_value,
            timeout_result=lambda cluster: {
                "cluster": cluster,
                "status": "failed",
                "reason": worker_timeout_reason(future_timeout_value),
            },
        )
        return build_probe_result(results)

    if wait_timeout is None:
        return run_probe_once()

    wait_timeout_value = normalize_timeout(wait_timeout, "wait_timeout")
    wait_interval_value = normalize_timeout(
        KLUSTERLET_RECHECK_INTERVAL if wait_interval is None else wait_interval,
        "wait_interval",
    )
    deadline = time.monotonic() + wait_timeout_value
    while True:
        result = run_probe_once()
        if not result["wrong_hub_clusters"]:
            return result

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return result
        time.sleep(min(wait_interval_value, remaining))


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


def _new_steps() -> dict:
    return {
        "import_secret_read": "pending",
        "bootstrap_secret_deleted": "pending",
        "bootstrap_secret_applied": "pending",
        "klusterlet_restarted": "pending",
    }


def prepare_one_cluster(
    cluster_name: str,
    secondary_client: CoreV1Client,
    managed_clusters: dict,
    core_client_factory: CoreClientFactory,
    apps_client_factory: AppsClientFactory,
    request_timeout: int | float | None = None,
    managed_cluster_reader: ManagedClusterReader | None = None,
) -> dict:
    """Remediation phase 1 (read-only): re-prove the target and re-classify the hub.

    A cluster is eligible only when this invocation's own fresh classification is
    ``wrong_hub`` (amendment §2.6 rule 4); the caller's list of names authorizes nothing.
    An eligible result carries the clients and manifest that phase 2 mutates with.
    """
    steps = _new_steps()
    managed = managed_clusters.get(cluster_name) or {}
    kubeconfig = managed.get("kubeconfig") or ""
    if not kubeconfig:
        steps = {key: "skipped" for key in steps}
        return _result(cluster_name, "skipped", steps, reason="no_managed_cluster_kubeconfig")
    if managed_cluster_reader is None:
        return _result(cluster_name, "failed", _mark_pending_not_run(steps), reason="secondary_hub_client_unavailable")

    try:
        context_name, api_url, expected_hub, import_yaml = _prove_target(
            cluster_name, managed, managed_cluster_reader, secondary_client, request_timeout
        )
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
        managed_core = _bound_spoke_client(core_client_factory, kubeconfig, context_name, api_url)
        apps_client = _bound_spoke_client(apps_client_factory, kubeconfig, context_name, api_url)
        probe = _classify_spoke(cluster_name, managed_core, expected_hub, request_timeout)
    except _Refusal as refusal:
        return _result(cluster_name, refusal.status, _mark_pending_not_run(steps), reason=refusal.reason)
    except Exception as exc:
        return _result(cluster_name, "failed", _mark_pending_not_run(steps), reason=error_summary(exc))

    if probe["status"] != "wrong_hub":
        result = _result(
            cluster_name,
            probe["status"],
            {key: ("not_required" if value == "pending" else value) for key, value in steps.items()},
            reason=probe.get("reason", "not_wrong_hub"),
        )
        return result
    result = _result(cluster_name, "eligible", steps)
    result["_prepared"] = {
        "managed_core": managed_core,
        "apps_client": apps_client,
        "bootstrap_doc": bootstrap_doc,
        "namespace": bootstrap_doc.get("metadata", {}).get("namespace") or MANAGED_CLUSTER_AGENT_NAMESPACE,
    }
    return result


def mutate_one_cluster(prepared_result: dict, request_timeout: int | float | None = None) -> dict:
    """Remediation phase 2: apply the bootstrap secret and restart the klusterlet."""
    cluster_name = prepared_result["cluster"]
    steps = dict(prepared_result["steps"])
    prepared = prepared_result["_prepared"]
    managed_core = prepared["managed_core"]
    apps_client = prepared["apps_client"]
    bootstrap_doc = prepared["bootstrap_doc"]
    bootstrap_namespace = prepared["namespace"]
    changed = False
    try:
        steps["bootstrap_secret_deleted"] = "not_required"
        try:
            managed_core.patch_namespaced_secret(
                name=BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
                namespace=bootstrap_namespace,
                body=bootstrap_doc,
                **request_timeout_kwargs(request_timeout),
            )
            steps["bootstrap_secret_applied"] = "patched"
            changed = True
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                try:
                    managed_core.create_namespaced_secret(
                        namespace=bootstrap_namespace,
                        body=bootstrap_doc,
                        **request_timeout_kwargs(request_timeout),
                    )
                    steps["bootstrap_secret_applied"] = "created"
                    changed = True
                except Exception as create_exc:
                    if getattr(create_exc, "status", None) == 409:
                        try:
                            managed_core.patch_namespaced_secret(
                                name=BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
                                namespace=bootstrap_namespace,
                                body=bootstrap_doc,
                                **request_timeout_kwargs(request_timeout),
                            )
                            steps["bootstrap_secret_applied"] = "patched"
                            changed = True
                        except Exception as retry_exc:
                            steps["bootstrap_secret_applied"] = "failed"
                            return _result(
                                cluster_name,
                                "failed",
                                _mark_pending_not_run(steps),
                                reason=error_summary(retry_exc),
                                changed=changed,
                            )
                    else:
                        steps["bootstrap_secret_applied"] = "failed"
                        return _result(
                            cluster_name,
                            "failed",
                            _mark_pending_not_run(steps),
                            reason=error_summary(create_exc),
                            changed=changed,
                        )
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
                namespace=bootstrap_namespace,
                body=patch,
                **request_timeout_kwargs(request_timeout),
            )
            steps["klusterlet_restarted"] = "ok"
            changed = True
        except Exception as exc:
            steps["klusterlet_restarted"] = "failed"
            return _result(
                cluster_name,
                "failed",
                _mark_pending_not_run(steps),
                reason=error_summary(exc),
                changed=changed,
            )

        return _result(cluster_name, "remediated", steps, changed=changed)
    except Exception as exc:
        return _result(
            cluster_name,
            "failed",
            _mark_pending_not_run(steps),
            reason=error_summary(exc),
            changed=changed,
        )


def remediate_klusterlets(
    secondary_hub: dict,
    managed_clusters: dict,
    pending_clusters: list[str],
    workers: int | None = None,
    strict: bool = False,
    check_mode: bool = False,
    request_timeout: int | float | None = None,
    future_timeout: int | float | None = None,
    core_client_factory: CoreClientFactory = build_core_v1_client,
    apps_client_factory: AppsClientFactory = build_apps_v1_client,
    managed_cluster_reader_factory: ManagedClusterReaderFactory | None = None,
) -> dict:
    """Two-phase remediation (amendment §2.6 rule 4 and §2.8).

    Phase 1 re-proves every candidate and re-classifies its hub, read-only. Phase 2 runs
    only if no candidate was refused (``failed`` or ``unverified``): any refusal blocks
    every mutation in the batch, and a candidate that is not freshly ``wrong_hub`` is
    never mutated.
    """
    worker_count = normalize_workers(workers)
    request_timeout_value = normalize_timeout(request_timeout, "request_timeout")
    future_timeout_value = normalize_timeout(future_timeout, "future_timeout")
    reader_factory = managed_cluster_reader_factory or build_managed_cluster_reader
    candidates = list(pending_clusters or [])
    if check_mode:
        results = []
        for cluster_name in candidates:
            if (managed_clusters.get(cluster_name) or {}).get("kubeconfig"):
                steps = {
                    "import_secret_read": "planned",
                    "bootstrap_secret_deleted": "not_required",
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
                results.append(
                    _result(
                        cluster_name,
                        "skipped",
                        steps,
                        reason="no_managed_cluster_kubeconfig",
                    )
                )
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
    managed_cluster_reader = None
    if any((managed_clusters.get(cluster) or {}).get("kubeconfig") for cluster in candidates):
        secondary_client, managed_cluster_reader = _hub_clients(
            secondary_hub, core_client_factory, reader_factory, request_timeout_value
        )
    timeout_steps = {
        "import_secret_read": "timeout",
        "bootstrap_secret_deleted": "not_run",
        "bootstrap_secret_applied": "not_run",
        "klusterlet_restarted": "not_run",
    }

    prepared = ordered_bounded_map(
        candidates,
        worker_count,
        lambda cluster: prepare_one_cluster(
            cluster,
            secondary_client,
            managed_clusters,
            core_client_factory,
            apps_client_factory,
            request_timeout=request_timeout_value,
            managed_cluster_reader=managed_cluster_reader,
        ),
        future_timeout=future_timeout_value,
        timeout_result=lambda cluster: _result(
            cluster,
            "failed",
            dict(timeout_steps),
            reason=worker_timeout_reason(future_timeout_value),
        ),
    )
    refused = [item["cluster"] for item in prepared if item["status"] in ("failed", "unverified")]
    eligible = {item["cluster"]: item for item in prepared if item["status"] == "eligible"}
    mutated: dict[str, dict] = {}
    if eligible and not refused:
        mutated = {
            item["cluster"]: item
            for item in ordered_bounded_map(
                list(eligible),
                worker_count,
                lambda cluster: mutate_one_cluster(eligible[cluster], request_timeout=request_timeout_value),
                future_timeout=future_timeout_value,
                timeout_result=lambda cluster: _result(
                    cluster,
                    "failed",
                    {
                        **eligible[cluster]["steps"],
                        "bootstrap_secret_applied": "timeout",
                        "klusterlet_restarted": "not_run",
                    },
                    reason=worker_timeout_reason(future_timeout_value),
                ),
            )
        }

    results = []
    for item in prepared:
        cluster_name = item["cluster"]
        if cluster_name in mutated:
            results.append(mutated[cluster_name])
        elif item["status"] == "eligible":
            # A wrong-hub cluster whose batch held an unproven peer: never mutated.
            results.append(
                _result(
                    cluster_name,
                    "failed",
                    _mark_pending_not_run(item["steps"]),
                    reason="remediation_blocked_by_unproven_cluster",
                )
            )
        else:
            results.append(item)

    failed_clusters = [item["cluster"] for item in results if item["status"] in ("failed", "unverified")]
    skipped_clusters = [item["cluster"] for item in results if item["status"] == "skipped"]
    changed = any(item.get("changed") for item in results)
    worker_timeout_prefix = WORKER_TIMEOUT_REASON_TEMPLATE.partition("{")[0]
    worker_timeout_failures = any(item.get("reason", "").startswith(worker_timeout_prefix) for item in results)
    failed = worker_timeout_failures or (strict and bool(failed_clusters))
    result = {
        "changed": changed,
        "failed": failed,
        "workers": worker_count,
        "results": results,
        "failed_clusters": failed_clusters,
        "unverified_clusters": [item["cluster"] for item in results if item["status"] == "unverified"],
        "skipped_clusters": skipped_clusters,
        "remediated_clusters": [item["cluster"] for item in results if item["status"] == "remediated"],
    }
    if failed:
        result["msg"] = "Klusterlet remediation failed for cluster(s): " + ", ".join(failed_clusters)
    return result
