"""Shared restore discovery helpers for ACM switchover workflows."""

from typing import Dict, Optional

from lib.constants import BACKUP_NAMESPACE, RESTORE_PASSIVE_SYNC_NAME, SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS
from lib.kube_client import KubeClient


def find_passive_sync_restore(client: KubeClient, namespace: str = BACKUP_NAMESPACE) -> Optional[Dict]:
    """Return the newest passive-sync restore, with fallback to the conventional name."""
    restores = client.list_custom_resources(
        group="cluster.open-cluster-management.io",
        version="v1beta1",
        plural="restores",
        namespace=namespace,
    )

    passive_candidates = [
        restore for restore in restores if restore.get("spec", {}).get(SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS) is True
    ]
    passive_candidates.sort(
        key=lambda item: item.get("metadata", {}).get("creationTimestamp", ""),
        reverse=True,
    )
    if passive_candidates:
        return passive_candidates[0]

    return client.get_custom_resource(
        group="cluster.open-cluster-management.io",
        version="v1beta1",
        plural="restores",
        name=RESTORE_PASSIVE_SYNC_NAME,
        namespace=namespace,
    )
