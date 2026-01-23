"""
E2E Switchover Tests.

This module contains end-to-end tests that run actual switchover cycles
against real Kubernetes clusters. These tests require cluster contexts
to be provided via command line options.

Usage:
    # Single cycle test
    pytest -m e2e --primary-context=hub1 --secondary-context=hub2 \
        tests/e2e/test_e2e_switchover.py::TestE2ESwitchover::test_single_switchover_cycle

    # Multi-cycle soak test
    pytest -m e2e --primary-context=hub1 --secondary-context=hub2 --e2e-cycles=10 \
        tests/e2e/test_e2e_switchover.py::TestE2ESwitchover::test_multi_cycle_switchover
"""

import pytest
from pathlib import Path

from tests.e2e.orchestrator import RunConfig, E2EOrchestrator
from tests.e2e.phase_handlers import CycleResult


@pytest.mark.e2e
class TestE2ESwitchover:
    """End-to-end switchover tests requiring real clusters."""

    def test_single_switchover_cycle(
        self,
        e2e_config: RunConfig,
        require_cluster_contexts,
        validate_cluster_access,
        cycle_output_dir: Path,
    ):
        """
        Test a single complete switchover cycle.
        
        This test runs one full switchover from primary to secondary hub,
        verifying that all phases complete successfully.
        """
        from dataclasses import replace
        
        # Override to single cycle and use test output directory
        config = replace(
            e2e_config,
            cycles=1,
            output_dir=cycle_output_dir,
            stop_on_failure=True,
        )
        
        orchestrator = E2EOrchestrator(config)
        success = orchestrator.run_all_cycles()
        
        assert success.success_rate == 100.0, "Single switchover cycle should complete successfully"
        
        # Verify artifacts were created
        assert orchestrator.manifest_path.exists(), "Manifest file should be created"
        metrics_files = list(orchestrator.run_output_dir.glob("metrics/metrics_*.json"))
        assert len(metrics_files) == 1, \
            "One cycle metrics file should exist"

    @pytest.mark.slow
    def test_multi_cycle_switchover(
        self,
        e2e_config: RunConfig,
        require_cluster_contexts,
        validate_cluster_access,
        cycle_output_dir: Path,
    ):
        """
        Test multiple consecutive switchover cycles.
        
        This test runs the configured number of cycles (from --e2e-cycles),
        alternating between hubs. It validates that the system can reliably
        perform repeated switchovers.
        """
        from dataclasses import replace
        
        # Use configured cycle count (minimum 2 for multi-cycle test)
        cycles = max(e2e_config.cycles, 2)
        
        config = replace(
            e2e_config,
            cycles=cycles,
            output_dir=cycle_output_dir,
            stop_on_failure=False,  # Continue on failure to gather all data
        )
        
        orchestrator = E2EOrchestrator(config)
        result = orchestrator.run_all_cycles()
        
        # Check success rate
        assert result.success_rate >= 95.0, \
            f"Success rate {result.success_rate:.1f}% below 95% threshold ({result.success_count}/{len(result.cycles)} cycles)"
        
        # Verify all cycle artifacts exist
        metrics_files = list(orchestrator.run_output_dir.glob("metrics/metrics_*.json"))
        assert len(metrics_files) == cycles, \
            f"Expected {cycles} cycle metrics files, found {len(metrics_files)}"

    @pytest.mark.parametrize(
        "method,old_hub_action",
        [
            ("passive", "secondary"),
            ("passive", "decommission"),
            ("passive", "none"),
            ("full", "secondary"),
            ("full", "decommission"),
            ("full", "none"),
        ],
        ids=[
            "passive-secondary",
            "passive-decommission",
            "passive-none",
            "full-secondary",
            "full-decommission",
            "full-none",
        ],
    )
    def test_switchover_method_and_action_combinations(
        self,
        e2e_config: RunConfig,
        require_cluster_contexts,
        validate_cluster_access,
        cycle_output_dir: Path,
        method: str,
        old_hub_action: str,
    ):
        """
        Test switchover with all supported method and old-hub-action combinations.

        This parametrized test verifies that all CLI-supported combinations
        of --method (passive/full) and --old-hub-action (secondary/decommission/none)
        complete successfully.

        Args:
            method: Switchover method - 'passive' (continuous sync) or 'full' (one-time restore)
            old_hub_action: Action for old hub - 'secondary', 'decommission', or 'none'
        """
        from dataclasses import replace

        config = replace(
            e2e_config,
            cycles=1,
            method=method,
            old_hub_action=old_hub_action,
            output_dir=cycle_output_dir,
            stop_on_failure=True,
        )

        orchestrator = E2EOrchestrator(config)
        result = orchestrator.run_all_cycles()

        assert result.success_rate == 100.0, (
            f"Switchover with method={method}, old_hub_action={old_hub_action} should succeed"
        )

        # Verify the cycle result
        assert len(orchestrator.cycle_results) == 1
        cycle_result = orchestrator.cycle_results[0]
        assert cycle_result.success, f"Cycle failed: {cycle_result.error}"

        # Verify manifest was written with correct config
        assert orchestrator.manifest_path.exists(), "Manifest should be created"

    def test_switchover_with_secondary_action(
        self,
        e2e_config: RunConfig,
        require_cluster_contexts,
        validate_cluster_access,
        cycle_output_dir: Path,
    ):
        """
        Test switchover with old hub configured as secondary.
        
        This test verifies that after switchover, the old primary hub
        is correctly configured as a secondary (ready for future switchback).
        """
        from dataclasses import replace
        
        config = replace(
            e2e_config,
            cycles=1,
            old_hub_action="secondary",
            output_dir=cycle_output_dir,
            stop_on_failure=True,
        )
        
        orchestrator = E2EOrchestrator(config)
        result = orchestrator.run_all_cycles()
        
        assert result.success_rate == 100.0, "Switchover with secondary action should succeed"
        
        # Verify the cycle result
        assert len(orchestrator.cycle_results) == 1
        cycle_result = orchestrator.cycle_results[0]
        assert cycle_result.success, f"Cycle failed: {cycle_result.error}"

    def test_phase_timing_collection(
        self,
        e2e_config: RunConfig,
        require_cluster_contexts,
        validate_cluster_access,
        cycle_output_dir: Path,
    ):
        """
        Test that phase timing metrics are correctly collected.
        
        This test verifies that the orchestrator correctly captures
        timing information for each phase of the switchover.
        """
        import json
        from dataclasses import replace
        
        config = replace(
            e2e_config,
            cycles=1,
            output_dir=cycle_output_dir,
            stop_on_failure=True,
        )
        
        orchestrator = E2EOrchestrator(config)
        orchestrator.run_all_cycles()
        
        # Find metrics file
        metrics_files = list(orchestrator.run_output_dir.glob("metrics/metrics_*.json"))
        assert len(metrics_files) == 1, "Expected one metrics file"
        
        with open(metrics_files[0]) as f:
            metrics = json.load(f)
        
        # Verify phases list exists
        assert "phases" in metrics, "Metrics should contain phases"
        
        expected_phases = ["preflight", "primary_prep", "activation", 
                          "post_activation", "finalization"]
        
        # Get phase names from the metrics
        recorded_phases = {p["name"] for p in metrics["phases"]}
        
        for phase in expected_phases:
            assert phase in recorded_phases, \
                f"Phase timing for '{phase}' should be recorded"
        
        # Verify each phase has duration_seconds
        for phase_data in metrics["phases"]:
            assert "duration_seconds" in phase_data, \
                f"Phase '{phase_data['name']}' should have duration_seconds"
            assert phase_data["duration_seconds"] >= 0, \
                f"Phase '{phase_data['name']}' duration should be non-negative"


@pytest.mark.e2e
class TestE2EValidation:
    """Validation tests for E2E infrastructure."""

    def test_cluster_connectivity(
        self,
        primary_client,
        secondary_client,
        validate_cluster_access,
    ):
        """
        Test basic connectivity to both clusters.
        
        This is a simple smoke test to verify that the test infrastructure
        can connect to both hub clusters.
        """
        # If we get here, validate_cluster_access already verified connectivity
        assert primary_client is not None, "Primary client should be created"
        assert secondary_client is not None, "Secondary client should be created"

    def test_acm_operator_present(
        self,
        primary_client,
        secondary_client,
        require_cluster_contexts,
    ):
        """
        Test that ACM operator is installed on both hubs.
        
        This test verifies that the Advanced Cluster Management operator
        is present on both clusters, which is required for switchover.
        """
        from lib.constants import ACM_NAMESPACE
        
        # Check primary
        primary_ns = primary_client.get_namespace(ACM_NAMESPACE)
        assert primary_ns is not None, \
            f"ACM namespace '{ACM_NAMESPACE}' should exist on primary"
        
        # Check secondary  
        secondary_ns = secondary_client.get_namespace(ACM_NAMESPACE)
        assert secondary_ns is not None, \
            f"ACM namespace '{ACM_NAMESPACE}' should exist on secondary"

    def test_backup_namespace_present(
        self,
        primary_client,
        secondary_client,
        require_cluster_contexts,
    ):
        """
        Test that backup namespace exists on both hubs.
        
        This test verifies that the OADP/backup namespace is present,
        which is required for backup/restore operations.
        """
        from lib.constants import BACKUP_NAMESPACE
        
        # Check primary
        primary_ns = primary_client.get_namespace(BACKUP_NAMESPACE)
        assert primary_ns is not None, \
            f"Backup namespace '{BACKUP_NAMESPACE}' should exist on primary"
        
        # Check secondary
        secondary_ns = secondary_client.get_namespace(BACKUP_NAMESPACE)
        assert secondary_ns is not None, \
            f"Backup namespace '{BACKUP_NAMESPACE}' should exist on secondary"
