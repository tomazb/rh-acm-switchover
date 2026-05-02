from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class HubDiscoveryClient(Protocol):
    def list_resources(self, resource: str, namespace: str | None = None) -> list[dict]: ...


@dataclass(frozen=True)
class HubFacts:
    context: str
    acm_namespace: str
    acm_version: str
    hub_role: str
    backup_schedule: dict
    restore: dict
    managed_cluster_names: tuple[str, ...]
    observability: dict
    argocd: dict


def _first(items: list[dict]) -> dict | None:
    return items[0] if items else None


def _managed_cluster_names(items: list[dict]) -> tuple[str, ...]:
    names = []
    for item in items:
        metadata = item.get("metadata", {})
        name = metadata.get("name")
        if name:
            names.append(name)
    return tuple(sorted(names))


def discover_hub_facts(
    *,
    client: HubDiscoveryClient,
    context: str,
    acm_namespace: str,
    argocd_namespaces: tuple[str, ...],
) -> HubFacts:
    mch = _first(client.list_resources("multiclusterhubs", acm_namespace)) or {}
    backup = _first(client.list_resources("backupschedules", acm_namespace))
    restore = _first(client.list_resources("restores", acm_namespace))
    managed_clusters = client.list_resources("managedclusters")

    applications: list[dict] = []
    for namespace in argocd_namespaces:
        applications.extend(client.list_resources("applications.argoproj.io", namespace))

    backup_present = backup is not None
    restore_present = restore is not None
    if backup_present:
        hub_role = "primary"
    elif restore_present:
        hub_role = "secondary"
    else:
        hub_role = "standby"

    return HubFacts(
        context=context,
        acm_namespace=acm_namespace,
        acm_version=str(mch.get("status", {}).get("currentVersion", "unknown")),
        hub_role=hub_role,
        backup_schedule={
            "present": backup_present,
            "name": backup.get("metadata", {}).get("name") if backup else None,
            "paused": backup.get("spec", {}).get("paused") if backup else None,
        },
        restore={
            "present": restore_present,
            "name": restore.get("metadata", {}).get("name") if restore else None,
            "phase": restore.get("status", {}).get("phase") if restore else None,
            "sync_restore_enabled": (restore.get("spec", {}).get("syncRestoreWithNewBackups") if restore else None),
        },
        managed_cluster_names=_managed_cluster_names(managed_clusters),
        observability={
            "present": bool(client.list_resources("multiclusterobservabilities", acm_namespace)),
            "status": "unknown",
        },
        argocd={
            "present": bool(applications),
            "namespaces": tuple(argocd_namespaces),
            "application_count": len(applications),
            # Baseline snapshot for future parity checks against live counts.
            "fixture_application_count": len(applications),
        },
    )
