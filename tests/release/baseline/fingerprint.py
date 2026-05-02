from __future__ import annotations

from datetime import datetime, timezone

from tests.release.baseline.discovery import HubFacts


def _hub_payload(facts: HubFacts) -> dict:
    return {
        "context": facts.context,
        "acm_namespace": facts.acm_namespace,
        "acm_version": facts.acm_version,
        "platform_version": "unknown",
        "kubernetes_version": "unknown",
        "hub_role": facts.hub_role,
        "backup_schedule": facts.backup_schedule,
        "backup_storage_location": {"present": False, "health": "unknown"},
        "oadp": {"present": False, "status": "unknown"},
        "restore": facts.restore,
        "observability": facts.observability,
        "argocd": facts.argocd,
    }


def _active_hub(primary: HubFacts, secondary: HubFacts) -> HubFacts | None:
    primary_is_active = primary.hub_role == "primary"
    secondary_is_active = secondary.hub_role == "primary"

    if primary_is_active == secondary_is_active:
        return None
    if primary_is_active:
        return primary
    return secondary


def build_environment_fingerprint(
    *,
    primary: HubFacts,
    secondary: HubFacts,
    expected_names: tuple[str, ...],
    expected_count: int | None,
    lab_readiness_status: str,
) -> dict:
    active = _active_hub(primary, secondary)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hubs": {
            "primary": _hub_payload(primary),
            "secondary": _hub_payload(secondary),
        },
        "managed_clusters": {
            "expectation_type": "names" if expected_names else "count",
            "expected_names": list(expected_names),
            "expected_count": expected_count,
            "observed_active_names": (list(active.managed_cluster_names) if expected_names and active else []),
            "observed_active_count": len(active.managed_cluster_names) if active else 0,
            "contexts_available": [],
        },
        "lab_readiness": {
            "status": lab_readiness_status,
            "required_crds_present": [],
            "evidence_paths": [],
        },
        "capabilities": {
            "observability": True,
            "argocd": True,
            "rbac_validation": True,
            "rbac_bootstrap": True,
            "decommission": True,
        },
    }
