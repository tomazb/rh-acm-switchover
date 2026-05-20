"""Shared restore discovery helpers for ACM switchover workflows."""

import re
from typing import Dict, Optional

from lib.constants import (
    BACKUP_NAMESPACE,
    BENIGN_ALREADY_AVAILABLE_MESSAGE_PATTERN,
    SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS,
)
from lib.kube_client import KubeClient

BENIGN_ALREADY_AVAILABLE_MESSAGE = re.compile(BENIGN_ALREADY_AVAILABLE_MESSAGE_PATTERN)


def find_passive_sync_restore(client: KubeClient, namespace: str = BACKUP_NAMESPACE) -> Optional[Dict]:
    """Return the newest sync-enabled passive-sync restore.

    When multiple restores carry the syncRestoreWithNewBackups spec flag,
    the one with the most recent creationTimestamp is selected.  This avoids
    non-deterministic results from unordered Kubernetes API list responses.
    """
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

    return None


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
