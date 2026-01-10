"""
Failure Injection Module for E2E Resilience Testing.

This module provides a FailureInjector class that can inject various failure
scenarios during E2E switchover tests to validate system resilience and
recovery behavior.

Supported failure scenarios:
- pause-backup: Pause the BackupSchedule mid-cycle
- delay-restore: Scale down Velero to delay restore completion
- kill-observability-pod: Delete the observability MCO pod

Usage:
    injector = FailureInjector(
        client=kube_client,
        scenario="pause-backup",
        dry_run=False,
    )
    
    # Inject failure at specified phase
    injector.inject()
    
    # Later: cleanup/restore (if applicable)
    injector.cleanup()
"""

import logging
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, List, Optional

from lib.constants import (
    BACKUP_NAMESPACE,
    OBSERVABILITY_NAMESPACE,
)

if TYPE_CHECKING:
    from lib.kube_client import KubeClient

logger = logging.getLogger("e2e_orchestrator")


class FailureScenario(str, Enum):
    """Available failure injection scenarios."""

    PAUSE_BACKUP = "pause-backup"
    DELAY_RESTORE = "delay-restore"
    KILL_OBSERVABILITY_POD = "kill-observability-pod"
    RANDOM = "random"

    @classmethod
    def choices(cls) -> List[str]:
        """Return list of valid scenario choices for CLI."""
        return [s.value for s in cls]

    @classmethod
    def injectable_scenarios(cls) -> List["FailureScenario"]:
        """Return scenarios that can actually be injected (excludes 'random')."""
        return [cls.PAUSE_BACKUP, cls.DELAY_RESTORE, cls.KILL_OBSERVABILITY_POD]


class InjectionPhase(str, Enum):
    """Phases at which failure can be injected."""

    PREFLIGHT = "preflight"
    PRIMARY_PREP = "primary_prep"
    ACTIVATION = "activation"
    POST_ACTIVATION = "post_activation"
    FINALIZATION = "finalization"

    @classmethod
    def choices(cls) -> List[str]:
        """Return list of valid phase choices for CLI."""
        return [p.value for p in cls]


@dataclass
class InjectionResult:
    """Result of a failure injection attempt."""

    scenario: str
    phase: str
    success: bool
    message: str
    details: Optional[dict] = field(default_factory=dict)


class FailureInjector:
    """
    Injects failures during E2E cycles for resilience testing.

    This class provides methods to inject various failure scenarios at
    specified phases during switchover cycles. Each injection can be
    cleaned up/reversed after the test.
    """

    # Velero namespace for delay-restore scenario
    VELERO_NAMESPACE = "velero"
    VELERO_DEPLOYMENT = "velero"

    # MCO pod label for observability scenario
    MCO_POD_LABEL = "name=multicluster-observability-operator"

    def __init__(
        self,
        client: "KubeClient",
        scenario: str,
        inject_at_phase: str = "activation",
        dry_run: bool = False,
    ):
        """
        Initialize the failure injector.

        Args:
            client: KubeClient instance for API operations
            scenario: Failure scenario to inject (from FailureScenario)
            inject_at_phase: Phase at which to inject (from InjectionPhase)
            dry_run: If True, only log what would happen
        """
        self.client = client
        self.dry_run = dry_run
        self._original_velero_replicas: Optional[int] = None
        self._injected = False

        # Resolve 'random' scenario to an actual injectable scenario
        if scenario == FailureScenario.RANDOM.value:
            self.scenario = random.choice(FailureScenario.injectable_scenarios())
            logger.info("Random scenario selected: %s", self.scenario.value)
        else:
            self.scenario = FailureScenario(scenario)

        self.inject_at_phase = InjectionPhase(inject_at_phase)

    def should_inject_at(self, current_phase: str) -> bool:
        """
        Check if failure should be injected at the current phase.

        Args:
            current_phase: Name of the current phase (e.g., "activation")

        Returns:
            True if injection should occur at this phase
        """
        return current_phase.lower() == self.inject_at_phase.value

    def inject(self) -> InjectionResult:
        """
        Inject the configured failure scenario.

        Returns:
            InjectionResult with success status and details
        """
        if self._injected:
            return InjectionResult(
                scenario=self.scenario.value,
                phase=self.inject_at_phase.value,
                success=False,
                message="Failure already injected for this cycle",
            )

        logger.warning(
            "INJECTING FAILURE: %s at phase %s",
            self.scenario.value,
            self.inject_at_phase.value,
        )

        try:
            if self.scenario == FailureScenario.PAUSE_BACKUP:
                return self._inject_pause_backup()
            elif self.scenario == FailureScenario.DELAY_RESTORE:
                return self._inject_delay_restore()
            elif self.scenario == FailureScenario.KILL_OBSERVABILITY_POD:
                return self._inject_kill_observability_pod()
            else:
                return InjectionResult(
                    scenario=self.scenario.value,
                    phase=self.inject_at_phase.value,
                    success=False,
                    message=f"Unknown scenario: {self.scenario}",
                )
        except Exception as e:
            logger.error("Failure injection failed: %s", e)
            return InjectionResult(
                scenario=self.scenario.value,
                phase=self.inject_at_phase.value,
                success=False,
                message=f"Injection error: {e}",
            )

    def cleanup(self) -> InjectionResult:
        """
        Clean up / reverse the injected failure.

        Returns:
            InjectionResult with cleanup status
        """
        if not self._injected:
            return InjectionResult(
                scenario=self.scenario.value,
                phase=self.inject_at_phase.value,
                success=True,
                message="No injection to clean up",
            )

        logger.info("Cleaning up injected failure: %s", self.scenario.value)

        try:
            if self.scenario == FailureScenario.PAUSE_BACKUP:
                return self._cleanup_pause_backup()
            elif self.scenario == FailureScenario.DELAY_RESTORE:
                return self._cleanup_delay_restore()
            elif self.scenario == FailureScenario.KILL_OBSERVABILITY_POD:
                # Pod will be recreated by controller; no explicit cleanup needed
                self._injected = False
                return InjectionResult(
                    scenario=self.scenario.value,
                    phase=self.inject_at_phase.value,
                    success=True,
                    message="Observability pod will be recreated by controller",
                )
            else:
                return InjectionResult(
                    scenario=self.scenario.value,
                    phase=self.inject_at_phase.value,
                    success=True,
                    message="No cleanup needed for this scenario",
                )
        except Exception as e:
            logger.error("Cleanup failed: %s", e)
            return InjectionResult(
                scenario=self.scenario.value,
                phase=self.inject_at_phase.value,
                success=False,
                message=f"Cleanup error: {e}",
            )

    def _inject_pause_backup(self) -> InjectionResult:
        """Pause the BackupSchedule to simulate backup interruption."""
        if self.dry_run:
            logger.info("[DRY-RUN] Would pause BackupSchedule in %s", BACKUP_NAMESPACE)
            self._injected = True
            return InjectionResult(
                scenario=self.scenario.value,
                phase=self.inject_at_phase.value,
                success=True,
                message="[DRY-RUN] Would pause BackupSchedule",
            )

        # Find active BackupSchedule
        schedules = self.client.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            namespace=BACKUP_NAMESPACE,
        )

        if not schedules:
            return InjectionResult(
                scenario=self.scenario.value,
                phase=self.inject_at_phase.value,
                success=False,
                message="No BackupSchedule found to pause",
            )

        # Pause the first schedule found
        schedule_name = schedules[0].get("metadata", {}).get("name")
        patch = {"spec": {"paused": True}}

        self.client.patch_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            name=schedule_name,
            patch=patch,
            namespace=BACKUP_NAMESPACE,
        )

        self._injected = True
        logger.warning("BackupSchedule %s paused", schedule_name)

        return InjectionResult(
            scenario=self.scenario.value,
            phase=self.inject_at_phase.value,
            success=True,
            message=f"Paused BackupSchedule: {schedule_name}",
            details={"schedule_name": schedule_name},
        )

    def _cleanup_pause_backup(self) -> InjectionResult:
        """Unpause the BackupSchedule."""
        if self.dry_run:
            self._injected = False
            return InjectionResult(
                scenario=self.scenario.value,
                phase=self.inject_at_phase.value,
                success=True,
                message="[DRY-RUN] Would unpause BackupSchedule",
            )

        schedules = self.client.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            namespace=BACKUP_NAMESPACE,
        )

        if not schedules:
            self._injected = False
            return InjectionResult(
                scenario=self.scenario.value,
                phase=self.inject_at_phase.value,
                success=True,
                message="No BackupSchedule to unpause",
            )

        schedule_name = schedules[0].get("metadata", {}).get("name")
        patch = {"spec": {"paused": False}}

        self.client.patch_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            name=schedule_name,
            patch=patch,
            namespace=BACKUP_NAMESPACE,
        )

        self._injected = False
        logger.info("BackupSchedule %s unpaused", schedule_name)

        return InjectionResult(
            scenario=self.scenario.value,
            phase=self.inject_at_phase.value,
            success=True,
            message=f"Unpaused BackupSchedule: {schedule_name}",
        )

    def _inject_delay_restore(self) -> InjectionResult:
        """Scale down Velero to delay restore completion."""
        if self.dry_run:
            logger.info("[DRY-RUN] Would scale down Velero deployment")
            self._injected = True
            return InjectionResult(
                scenario=self.scenario.value,
                phase=self.inject_at_phase.value,
                success=True,
                message="[DRY-RUN] Would scale Velero to 0 replicas",
            )

        # Get current replica count
        deployment = self.client.get_deployment(
            name=self.VELERO_DEPLOYMENT,
            namespace=self.VELERO_NAMESPACE,
        )

        if not deployment:
            return InjectionResult(
                scenario=self.scenario.value,
                phase=self.inject_at_phase.value,
                success=False,
                message="Velero deployment not found",
            )

        self._original_velero_replicas = deployment.get("spec", {}).get("replicas", 1)

        # Scale to 0
        self.client.scale_deployment(
            name=self.VELERO_DEPLOYMENT,
            namespace=self.VELERO_NAMESPACE,
            replicas=0,
        )

        self._injected = True
        logger.warning(
            "Velero scaled to 0 (was %d replicas)", self._original_velero_replicas
        )

        return InjectionResult(
            scenario=self.scenario.value,
            phase=self.inject_at_phase.value,
            success=True,
            message=f"Scaled Velero to 0 (was {self._original_velero_replicas})",
            details={"original_replicas": self._original_velero_replicas},
        )

    def _cleanup_delay_restore(self) -> InjectionResult:
        """Scale Velero back to original replica count."""
        if self.dry_run:
            self._injected = False
            return InjectionResult(
                scenario=self.scenario.value,
                phase=self.inject_at_phase.value,
                success=True,
                message="[DRY-RUN] Would scale Velero back up",
            )

        # Use explicit None check to preserve original replicas=0 if that was the state
        replicas = self._original_velero_replicas if self._original_velero_replicas is not None else 1

        self.client.scale_deployment(
            name=self.VELERO_DEPLOYMENT,
            namespace=self.VELERO_NAMESPACE,
            replicas=replicas,
        )

        self._injected = False
        logger.info("Velero scaled back to %d replicas", replicas)

        return InjectionResult(
            scenario=self.scenario.value,
            phase=self.inject_at_phase.value,
            success=True,
            message=f"Scaled Velero back to {replicas} replicas",
        )

    def _inject_kill_observability_pod(self) -> InjectionResult:
        """Kill the observability MCO pod to test recovery."""
        if self.dry_run:
            logger.info("[DRY-RUN] Would delete observability MCO pod")
            self._injected = True
            return InjectionResult(
                scenario=self.scenario.value,
                phase=self.inject_at_phase.value,
                success=True,
                message="[DRY-RUN] Would delete MCO pod",
            )

        # Find MCO pod
        pods = self.client.list_pods(
            namespace=OBSERVABILITY_NAMESPACE,
            label_selector=self.MCO_POD_LABEL,
        )

        if not pods:
            return InjectionResult(
                scenario=self.scenario.value,
                phase=self.inject_at_phase.value,
                success=False,
                message="No MCO pod found in observability namespace",
            )

        pod_name = pods[0].get("metadata", {}).get("name")

        # Delete the pod
        self.client.delete_pod(
            namespace=OBSERVABILITY_NAMESPACE,
            name=pod_name,
        )

        self._injected = True
        logger.warning("Deleted observability MCO pod: %s", pod_name)

        return InjectionResult(
            scenario=self.scenario.value,
            phase=self.inject_at_phase.value,
            success=True,
            message=f"Deleted MCO pod: {pod_name}",
            details={"pod_name": pod_name},
        )
