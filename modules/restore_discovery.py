"""Shared restore discovery helpers for ACM switchover workflows."""

import re
from typing import Any, Dict, Optional

from lib.constants import (
    BACKUP_NAMESPACE,
    BENIGN_ALREADY_AVAILABLE_MESSAGE_PATTERN,
    CLUSTER_BACKUP_API_GROUP,
    CLUSTER_BACKUP_API_VERSION,
    RESTORE_PLURAL,
    SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS,
)
from lib.exceptions import FatalError
from lib.kube_client import KubeClient
from lib.migration_evidence import rfc3339_ns

BENIGN_ALREADY_AVAILABLE_MESSAGE = re.compile(BENIGN_ALREADY_AVAILABLE_MESSAGE_PATTERN)


def find_passive_sync_restore(client: KubeClient, namespace: str = BACKUP_NAMESPACE) -> Optional[Dict]:
    """Return the newest sync-enabled passive-sync restore.

    When multiple restores carry the syncRestoreWithNewBackups spec flag,
    the one with the most recent creationTimestamp is selected.  This avoids
    non-deterministic results from unordered Kubernetes API list responses.
    """
    restores = client.list_custom_resources(
        group=CLUSTER_BACKUP_API_GROUP,
        version=CLUSTER_BACKUP_API_VERSION,
        plural=RESTORE_PLURAL,
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

    return None


def find_passive_sync_restore_strict(client: KubeClient, namespace: str = BACKUP_NAMESPACE) -> Optional[Dict]:
    """Return the newest sync-enabled passive Restore from a positively complete strict LIST.

    R4-04 (July design section 3): "no passive Restore" is concluded only from a
    complete read. A failed, partial or absent-API read, a malformed item, or a tie
    for the newest creationTimestamp raises FatalError before any create, patch or
    delete decision. None means the complete list holds no sync-enabled Restore.
    """
    outcome = client.list_custom_resources_strict(
        group=CLUSTER_BACKUP_API_GROUP,
        version=CLUSTER_BACKUP_API_VERSION,
        plural=RESTORE_PLURAL,
        namespace=namespace,
    )
    if not outcome.is_success:
        raise FatalError(
            f"Passive Restore discovery in {namespace} failed ({outcome.status.value}: {outcome.reason}); "
            "no Restore mutation was attempted"
        )
    candidates = []
    for item in outcome.items:
        metadata = item.get("metadata")
        spec = item.get("spec", {})
        if not isinstance(metadata, dict) or not _non_empty_str(metadata.get("name")) or not isinstance(spec, dict):
            raise FatalError(f"Passive Restore discovery in {namespace} returned a malformed Restore")
        if spec.get(SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS) is True:
            created = rfc3339_ns(metadata.get("creationTimestamp"))
            if created is None:
                raise FatalError(f"Passive sync Restore {metadata['name']} has no valid creationTimestamp")
            candidates.append((created, item))
    if not candidates:
        return None
    newest = max(created for created, _ in candidates)
    winners = [item for created, item in candidates if created == newest]
    if len(winners) > 1:
        raise FatalError(f"{len(winners)} sync-enabled passive Restores share the newest creationTimestamp")
    return winners[0]


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def is_benign_already_available_message(message: object) -> bool:
    """Return True only for exact benign ManagedCluster already-available messages."""
    return isinstance(message, str) and BENIGN_ALREADY_AVAILABLE_MESSAGE.fullmatch(message) is not None


def restore_messages_are_benign_already_available(messages: object) -> bool:
    """Return True when all restore messages are exact benign already-available messages."""
    return (
        isinstance(messages, list)
        and bool(messages)
        and all(is_benign_already_available_message(message) for message in messages)
    )
