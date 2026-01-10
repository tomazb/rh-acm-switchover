"""
E2E Phase Handlers with Timing Instrumentation.

This module provides wrappers around existing switchover phase modules
with timing, logging, and metrics collection for E2E testing.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional

from lib.constants import ACM_NAMESPACE, OBSERVABILITY_NAMESPACE
from lib.kube_client import KubeClient
from lib.utils import StateManager
from modules import (
    Finalization,
    PostActivationVerification,
    PrimaryPreparation,
    SecondaryActivation,
)
from modules.preflight_coordinator import PreflightValidator


@dataclass
class PhaseResult:
    """Result of a single phase execution."""

    phase_name: str
    success: bool
    start_time: datetime
    end_time: datetime
    error: Optional[str] = None

    @property
    def duration_seconds(self) -> float:
        """Calculate phase duration in seconds."""
        return (self.end_time - self.start_time).total_seconds()

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON output."""
        return {
            "phase_name": self.phase_name,
            "success": self.success,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "duration_seconds": self.duration_seconds,
            "error": self.error,
        }


@dataclass
class CycleResult:
    """Result of a complete switchover cycle."""

    cycle_id: str
    cycle_num: int
    success: bool
    start_time: datetime
    end_time: datetime
    primary_context: str
    secondary_context: str
    phase_results: List[PhaseResult] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def total_duration_seconds(self) -> float:
        """Calculate total cycle duration in seconds."""
        return (self.end_time - self.start_time).total_seconds()


class PhaseHandlers:
    """
    Wrapper class for executing switchover phases with timing instrumentation.

    Each phase handler wraps the corresponding module class, captures timing
    information, and returns structured results for metrics collection.
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        """
        Initialize the phase handlers.

        Args:
            logger: Optional logger instance
        """
        self.logger = logger or logging.getLogger("e2e_phase_handlers")

    def _run_timed_phase(
        self,
        phase_name: str,
        phase_func,
    ) -> PhaseResult:
        """
        Execute a phase function with timing instrumentation.

        Args:
            phase_name: Name of the phase for logging and metrics
            phase_func: Callable that executes the phase and returns bool

        Returns:
            PhaseResult with timing and status information
        """
        self.logger.info("Starting phase: %s", phase_name)
        start_time = datetime.now(timezone.utc)
        error_msg = None
        success = False

        try:
            success = phase_func()
            if not success:
                error_msg = f"Phase {phase_name} returned False"
        except Exception as e:
            self.logger.error("Phase %s failed with exception: %s", phase_name, e)
            error_msg = str(e)
            success = False

        end_time = datetime.now(timezone.utc)
        duration = (end_time - start_time).total_seconds()

        if success:
            self.logger.info("✅ Phase %s completed in %.1f seconds", phase_name, duration)
        else:
            self.logger.error("❌ Phase %s failed after %.1f seconds: %s", phase_name, duration, error_msg)

        return PhaseResult(
            phase_name=phase_name,
            success=success,
            start_time=start_time,
            end_time=end_time,
            error=error_msg,
        )

    def run_preflight(
        self,
        primary_client: KubeClient,
        secondary_client: KubeClient,
        method: str = "passive",
        skip_rbac_validation: bool = False,
    ) -> PhaseResult:
        """
        Run preflight validation phase.

        Args:
            primary_client: KubeClient for primary hub
            secondary_client: KubeClient for secondary hub
            method: Switchover method (passive/active)
            skip_rbac_validation: Whether to skip RBAC validation

        Returns:
            PhaseResult with timing and validation results
        """
        validator = PreflightValidator(
            primary_client,
            secondary_client,
            method,
            skip_rbac_validation=skip_rbac_validation,
        )

        def phase_func() -> bool:
            success, config = validator.validate_all()
            # Store config for later phases (return it somehow?)
            # For now, just return success
            return success

        return self._run_timed_phase("preflight", phase_func)

    def run_primary_prep(
        self,
        primary_client: KubeClient,
        state_manager: StateManager,
        acm_version: str,
        has_observability: bool,
        dry_run: bool = False,
    ) -> PhaseResult:
        """
        Run primary hub preparation phase.

        Args:
            primary_client: KubeClient for primary hub
            state_manager: StateManager for idempotent execution
            acm_version: ACM version string
            has_observability: Whether observability is installed
            dry_run: Whether to run in dry-run mode

        Returns:
            PhaseResult with timing and status
        """
        prep = PrimaryPreparation(
            primary_client,
            state_manager,
            acm_version,
            has_observability,
            dry_run=dry_run,
        )

        return self._run_timed_phase("primary_prep", prep.prepare)

    def run_activation(
        self,
        secondary_client: KubeClient,
        state_manager: StateManager,
        method: str = "passive",
        manage_auto_import_strategy: bool = False,
    ) -> PhaseResult:
        """
        Run secondary hub activation phase.

        Args:
            secondary_client: KubeClient for secondary hub
            state_manager: StateManager for idempotent execution
            method: Switchover method (passive/active)
            manage_auto_import_strategy: Whether to manage auto-import strategy

        Returns:
            PhaseResult with timing and status
        """
        activation = SecondaryActivation(
            secondary_client=secondary_client,
            state_manager=state_manager,
            method=method,
            manage_auto_import_strategy=manage_auto_import_strategy,
        )

        return self._run_timed_phase("activation", activation.activate)

    def run_post_activation(
        self,
        secondary_client: KubeClient,
        state_manager: StateManager,
        has_observability: bool,
        dry_run: bool = False,
    ) -> PhaseResult:
        """
        Run post-activation verification phase.

        Args:
            secondary_client: KubeClient for secondary hub
            state_manager: StateManager for idempotent execution
            has_observability: Whether observability is installed
            dry_run: Whether to run in dry-run mode

        Returns:
            PhaseResult with timing and status
        """
        verification = PostActivationVerification(
            secondary_client,
            state_manager,
            has_observability,
            dry_run=dry_run,
        )

        return self._run_timed_phase("post_activation", verification.verify)

    def run_finalization(
        self,
        secondary_client: KubeClient,
        state_manager: StateManager,
        acm_version: str,
        primary_client: Optional[KubeClient] = None,
        primary_has_observability: bool = False,
        dry_run: bool = False,
        old_hub_action: str = "secondary",
        manage_auto_import_strategy: bool = False,
    ) -> PhaseResult:
        """
        Run finalization phase.

        Args:
            secondary_client: KubeClient for secondary hub
            state_manager: StateManager for idempotent execution
            acm_version: ACM version string
            primary_client: Optional KubeClient for primary hub
            primary_has_observability: Whether primary has observability
            dry_run: Whether to run in dry-run mode
            old_hub_action: Action for old hub (secondary/decommission/none)
            manage_auto_import_strategy: Whether to manage auto-import strategy

        Returns:
            PhaseResult with timing and status
        """
        finalization = Finalization(
            secondary_client,
            state_manager,
            acm_version,
            primary_client=primary_client,
            primary_has_observability=primary_has_observability,
            dry_run=dry_run,
            old_hub_action=old_hub_action,
            manage_auto_import_strategy=manage_auto_import_strategy,
        )

        return self._run_timed_phase("finalization", finalization.finalize)

    def run_all_phases(
        self,
        primary_client: KubeClient,
        secondary_client: KubeClient,
        state_manager: StateManager,
        method: str = "passive",
        old_hub_action: str = "secondary",
        dry_run: bool = False,
        skip_observability_checks: bool = False,
        skip_rbac_validation: bool = False,
        manage_auto_import_strategy: bool = False,
        phase_callback: Optional[Callable[[str, str], None]] = None,
    ) -> List[PhaseResult]:
        """
        Run all switchover phases in sequence.

        This is the main entry point for running a complete switchover cycle.
        It executes all phases in order and collects results.

        Args:
            primary_client: KubeClient for primary hub
            secondary_client: KubeClient for secondary hub
            state_manager: StateManager for idempotent execution
            method: Switchover method (passive/active)
            old_hub_action: Action for old hub (secondary/decommission/none)
            dry_run: Whether to run in dry-run mode
            skip_observability_checks: Whether to skip observability checks
            skip_rbac_validation: Whether to skip RBAC validation
            manage_auto_import_strategy: Whether to manage auto-import strategy
            phase_callback: Optional callback called before each phase with (phase_name, "before")
                           and after each phase with (phase_name, "after")

        Returns:
            List of PhaseResult for each phase executed
        """
        results: List[PhaseResult] = []

        # Phase 1: Preflight validation
        self.logger.info("")
        self.logger.info("-" * 40)
        self.logger.info("PHASE 1: PREFLIGHT VALIDATION")
        self.logger.info("-" * 40)

        if phase_callback:
            phase_callback("preflight", "before")

        preflight_result = self.run_preflight(
            primary_client,
            secondary_client,
            method,
            skip_rbac_validation=skip_rbac_validation,
        )
        results.append(preflight_result)

        if phase_callback:
            phase_callback("preflight", "after")

        if not preflight_result.success:
            self.logger.error("Preflight validation failed, aborting cycle")
            return results

        # Get configuration from preflight (simplified - in real scenario, we'd get this from preflight)
        # For E2E, we detect observability ourselves
        primary_has_obs = not skip_observability_checks and primary_client.namespace_exists(OBSERVABILITY_NAMESPACE)
        secondary_has_obs = not skip_observability_checks and secondary_client.namespace_exists(OBSERVABILITY_NAMESPACE)

        # Get ACM version from primary hub
        try:
            mch = primary_client.get_custom_resource(
                group="operator.open-cluster-management.io",
                version="v1",
                plural="multiclusterhubs",
                name="multiclusterhub",
                namespace=ACM_NAMESPACE,
            )
            acm_version = mch.get("status", {}).get("currentVersion", "unknown") if mch else "unknown"
        except Exception:
            acm_version = "unknown"

        # Store config in state manager
        state_manager.set_config("primary_version", acm_version)
        state_manager.set_config("primary_has_observability", primary_has_obs)
        state_manager.set_config("secondary_has_observability", secondary_has_obs)

        # Phase 2: Primary hub preparation
        self.logger.info("")
        self.logger.info("-" * 40)
        self.logger.info("PHASE 2: PRIMARY HUB PREPARATION")
        self.logger.info("-" * 40)

        if phase_callback:
            phase_callback("primary_prep", "before")

        primary_prep_result = self.run_primary_prep(
            primary_client,
            state_manager,
            acm_version,
            primary_has_obs,
            dry_run=dry_run,
        )
        results.append(primary_prep_result)

        if phase_callback:
            phase_callback("primary_prep", "after")

        if not primary_prep_result.success:
            self.logger.error("Primary prep failed, aborting cycle")
            return results

        # Phase 3: Secondary hub activation
        self.logger.info("")
        self.logger.info("-" * 40)
        self.logger.info("PHASE 3: SECONDARY HUB ACTIVATION")
        self.logger.info("-" * 40)

        if phase_callback:
            phase_callback("activation", "before")

        activation_result = self.run_activation(
            secondary_client,
            state_manager,
            method,
            manage_auto_import_strategy=manage_auto_import_strategy,
        )
        results.append(activation_result)

        if phase_callback:
            phase_callback("activation", "after")

        if not activation_result.success:
            self.logger.error("Activation failed, aborting cycle")
            return results

        # Phase 4: Post-activation verification
        self.logger.info("")
        self.logger.info("-" * 40)
        self.logger.info("PHASE 4: POST-ACTIVATION VERIFICATION")
        self.logger.info("-" * 40)

        if phase_callback:
            phase_callback("post_activation", "before")

        post_activation_result = self.run_post_activation(
            secondary_client,
            state_manager,
            secondary_has_obs,
            dry_run=dry_run,
        )
        results.append(post_activation_result)

        if phase_callback:
            phase_callback("post_activation", "after")

        if not post_activation_result.success:
            self.logger.error("Post-activation verification failed, aborting cycle")
            return results

        # Phase 5: Finalization
        self.logger.info("")
        self.logger.info("-" * 40)
        self.logger.info("PHASE 5: FINALIZATION")
        self.logger.info("-" * 40)

        if phase_callback:
            phase_callback("finalization", "before")

        finalization_result = self.run_finalization(
            secondary_client,
            state_manager,
            acm_version,
            primary_client=primary_client,
            primary_has_observability=primary_has_obs,
            dry_run=dry_run,
            old_hub_action=old_hub_action,
            manage_auto_import_strategy=manage_auto_import_strategy,
        )
        results.append(finalization_result)

        if phase_callback:
            phase_callback("finalization", "after")

        return results
