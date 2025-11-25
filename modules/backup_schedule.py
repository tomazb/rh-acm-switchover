"""Shared helpers for BackupSchedule management."""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional

from lib.constants import BACKUP_NAMESPACE
from lib.kube_client import KubeClient
from lib.utils import StateManager, is_acm_version_ge

logger = logging.getLogger("acm_switchover")


class BackupScheduleManager:
    """Handles enabling or restoring BackupSchedules on hubs."""

    def __init__(
        self,
        kube_client: KubeClient,
        state_manager: StateManager,
        hub_label: str,
    ) -> None:
        self.client = kube_client
        self.state = state_manager
        self.hub_label = hub_label

    def ensure_enabled(self, acm_version: str) -> None:
        """Ensure a BackupSchedule exists and is not paused."""
        schedules = self._list_schedules()
        if not schedules:
            self._restore_saved_schedule()
            return

        schedule = schedules[0]
        schedule_name = schedule.get("metadata", {}).get("name", "schedule-rhacm")
        paused = schedule.get("spec", {}).get("paused")

        if paused is False or "paused" not in schedule.get("spec", {}):
            logger.info(
                "BackupSchedule %s already enabled on %s",
                schedule_name,
                self.hub_label,
            )
            return

        if is_acm_version_ge(acm_version, "2.12.0"):
            logger.info(
                "Unpausing BackupSchedule %s on %s via spec.paused",
                schedule_name,
                self.hub_label,
            )
            self.client.patch_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="backupschedules",
                name=schedule_name,
                patch={"spec": {"paused": False}},
                namespace=BACKUP_NAMESPACE,
            )
            logger.info("BackupSchedule %s enabled", schedule_name)
        else:
            logger.info(
                "BackupSchedule pause management for ACM %s handled via restore",
                acm_version,
            )

    def _list_schedules(self) -> List[Dict[str, Any]]:
        return self.client.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            namespace=BACKUP_NAMESPACE,
        )

    def _restore_saved_schedule(self) -> None:
        saved_bs = self.state.get_config("saved_backup_schedule")
        if not saved_bs:
            logger.warning(
                "No BackupSchedule found on %s and none saved in state",
                self.hub_label,
            )
            return

        logger.info("Restoring saved BackupSchedule on %s", self.hub_label)
        body = copy.deepcopy(saved_bs)
        self._clean_metadata(body)
        if "spec" not in body:
            body["spec"] = {}
        body["spec"]["paused"] = False
        body.pop("status", None)

        self.client.create_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            body=body,
            namespace=BACKUP_NAMESPACE,
        )
        logger.info("BackupSchedule restored on %s", self.hub_label)

    @staticmethod
    def _clean_metadata(schedule: Dict[str, Any]) -> None:
        metadata = schedule.get("metadata")
        if not metadata:
            return

        for key in (
            "uid",
            "resourceVersion",
            "creationTimestamp",
            "generation",
            "managedFields",
        ):
            metadata.pop(key, None)
