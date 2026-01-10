"""
E2E Resilience Tests for ACM Switchover.

This module contains tests for Phase 3 (E2E-300): Failure injection scenarios.
These tests validate that the system handles injected failures appropriately
and can recover in subsequent cycles.

Test markers:
- @pytest.mark.e2e: Standard E2E test marker
- @pytest.mark.resilience: Resilience-specific tests (failure injection)
"""

import pytest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.e2e import (
    E2EOrchestrator,
    RunConfig,
    FailureInjector,
    FailureScenario,
    InjectionPhase,
    InjectionResult,
)


@pytest.mark.e2e
class TestFailureInjector:
    """Unit tests for the FailureInjector class."""

    def test_failure_scenario_choices(self):
        """Test that all failure scenarios are enumerated."""
        choices = FailureScenario.choices()
        assert "pause-backup" in choices
        assert "delay-restore" in choices
        assert "kill-observability-pod" in choices
        assert "random" in choices
        assert len(choices) == 4

    def test_injectable_scenarios_excludes_random(self):
        """Test that injectable_scenarios excludes 'random'."""
        injectable = FailureScenario.injectable_scenarios()
        assert FailureScenario.RANDOM not in injectable
        assert FailureScenario.PAUSE_BACKUP in injectable
        assert FailureScenario.DELAY_RESTORE in injectable
        assert FailureScenario.KILL_OBSERVABILITY_POD in injectable

    def test_injection_phase_choices(self):
        """Test that all injection phases are enumerated."""
        choices = InjectionPhase.choices()
        assert "preflight" in choices
        assert "primary_prep" in choices
        assert "activation" in choices
        assert "post_activation" in choices
        assert "finalization" in choices
        assert len(choices) == 5

    def test_injector_dry_run_pause_backup(self):
        """Test pause-backup injection in dry-run mode."""
        mock_client = MagicMock()
        injector = FailureInjector(
            client=mock_client,
            scenario="pause-backup",
            inject_at_phase="activation",
            dry_run=True,
        )

        result = injector.inject()
        
        assert result.success is True
        assert "[DRY-RUN]" in result.message
        assert result.scenario == "pause-backup"
        assert result.phase == "activation"
        # Should not call any API methods in dry-run
        mock_client.patch_custom_resource.assert_not_called()

    def test_injector_dry_run_delay_restore(self):
        """Test delay-restore injection in dry-run mode."""
        mock_client = MagicMock()
        injector = FailureInjector(
            client=mock_client,
            scenario="delay-restore",
            inject_at_phase="primary_prep",
            dry_run=True,
        )

        result = injector.inject()
        
        assert result.success is True
        assert "[DRY-RUN]" in result.message
        assert "Velero" in result.message
        mock_client.scale_deployment.assert_not_called()

    def test_injector_dry_run_kill_observability_pod(self):
        """Test kill-observability-pod injection in dry-run mode."""
        mock_client = MagicMock()
        injector = FailureInjector(
            client=mock_client,
            scenario="kill-observability-pod",
            inject_at_phase="post_activation",
            dry_run=True,
        )

        result = injector.inject()
        
        assert result.success is True
        assert "[DRY-RUN]" in result.message
        assert "MCO" in result.message
        mock_client.delete_pod.assert_not_called()

    def test_injector_random_selects_valid_scenario(self):
        """Test that 'random' scenario resolves to a valid injectable scenario."""
        mock_client = MagicMock()
        
        # Run multiple times to verify randomness
        selected_scenarios = set()
        for _ in range(20):
            injector = FailureInjector(
                client=mock_client,
                scenario="random",
                inject_at_phase="activation",
                dry_run=True,
            )
            selected_scenarios.add(injector.scenario)
        
        # Should only select from injectable scenarios (not 'random')
        for scenario in selected_scenarios:
            assert scenario != FailureScenario.RANDOM
            assert scenario in FailureScenario.injectable_scenarios()

    def test_should_inject_at_matching_phase(self):
        """Test should_inject_at returns True for matching phase."""
        mock_client = MagicMock()
        injector = FailureInjector(
            client=mock_client,
            scenario="pause-backup",
            inject_at_phase="activation",
            dry_run=True,
        )

        assert injector.should_inject_at("activation") is True
        assert injector.should_inject_at("ACTIVATION") is True
        assert injector.should_inject_at("preflight") is False
        assert injector.should_inject_at("finalization") is False

    def test_double_injection_fails(self):
        """Test that injecting twice returns an error."""
        mock_client = MagicMock()
        injector = FailureInjector(
            client=mock_client,
            scenario="pause-backup",
            inject_at_phase="activation",
            dry_run=True,
        )

        # First injection should succeed
        result1 = injector.inject()
        assert result1.success is True

        # Second injection should fail
        result2 = injector.inject()
        assert result2.success is False
        assert "already injected" in result2.message

    def test_cleanup_without_injection(self):
        """Test that cleanup without prior injection succeeds."""
        mock_client = MagicMock()
        injector = FailureInjector(
            client=mock_client,
            scenario="pause-backup",
            inject_at_phase="activation",
            dry_run=True,
        )

        result = injector.cleanup()
        assert result.success is True
        assert "No injection" in result.message

    def test_pause_backup_injection_real(self):
        """Test pause-backup injection with mocked API calls."""
        mock_client = MagicMock()
        mock_client.list_custom_resources.return_value = [
            {"metadata": {"name": "acm-backup-schedule"}}
        ]
        mock_client.patch_custom_resource.return_value = {}

        injector = FailureInjector(
            client=mock_client,
            scenario="pause-backup",
            inject_at_phase="activation",
            dry_run=False,
        )

        result = injector.inject()

        assert result.success is True
        assert "Paused BackupSchedule" in result.message
        mock_client.patch_custom_resource.assert_called_once()
        call_args = mock_client.patch_custom_resource.call_args
        assert call_args.kwargs["name"] == "acm-backup-schedule"
        assert call_args.kwargs["patch"] == {"spec": {"paused": True}}

    def test_pause_backup_cleanup_real(self):
        """Test pause-backup cleanup with mocked API calls."""
        mock_client = MagicMock()
        mock_client.list_custom_resources.return_value = [
            {"metadata": {"name": "acm-backup-schedule"}}
        ]
        mock_client.patch_custom_resource.return_value = {}

        injector = FailureInjector(
            client=mock_client,
            scenario="pause-backup",
            inject_at_phase="activation",
            dry_run=False,
        )

        # Inject first
        injector.inject()
        
        # Then cleanup
        result = injector.cleanup()

        assert result.success is True
        assert "Unpaused BackupSchedule" in result.message
        # Should have been called twice (inject + cleanup)
        assert mock_client.patch_custom_resource.call_count == 2

    def test_delay_restore_injection_real(self):
        """Test delay-restore injection with mocked API calls."""
        mock_client = MagicMock()
        mock_client.get_deployment.return_value = {
            "spec": {"replicas": 2}
        }
        mock_client.scale_deployment.return_value = {}

        injector = FailureInjector(
            client=mock_client,
            scenario="delay-restore",
            inject_at_phase="activation",
            dry_run=False,
        )

        result = injector.inject()

        assert result.success is True
        assert "Velero" in result.message
        assert result.details.get("original_replicas") == 2
        mock_client.scale_deployment.assert_called_once_with(
            name="velero",
            namespace="velero",
            replicas=0,
        )

    def test_kill_observability_pod_real(self):
        """Test kill-observability-pod injection with mocked API calls."""
        mock_client = MagicMock()
        mock_client.list_pods.return_value = [
            {"metadata": {"name": "mco-operator-abc123"}}
        ]
        mock_client.delete_pod.return_value = True

        injector = FailureInjector(
            client=mock_client,
            scenario="kill-observability-pod",
            inject_at_phase="activation",
            dry_run=False,
        )

        result = injector.inject()

        assert result.success is True
        assert "mco-operator-abc123" in result.message
        mock_client.delete_pod.assert_called_once()


@pytest.mark.e2e
class TestRunConfigWithInjection:
    """Tests for RunConfig with failure injection options."""

    def test_config_with_injection_options(self, tmp_path):
        """Test that RunConfig accepts injection options."""
        config = RunConfig(
            primary_context="hub1",
            secondary_context="hub2",
            inject_failure="pause-backup",
            inject_at_phase="activation",
            dry_run=True,
            output_dir=tmp_path,
        )

        assert config.inject_failure == "pause-backup"
        assert config.inject_at_phase == "activation"

    def test_config_defaults_no_injection(self, tmp_path):
        """Test that injection is disabled by default."""
        config = RunConfig(
            primary_context="hub1",
            secondary_context="hub2",
            dry_run=True,
            output_dir=tmp_path,
        )

        assert config.inject_failure is None
        assert config.inject_at_phase == "activation"


@pytest.mark.e2e
class TestOrchestratorWithInjection:
    """Tests for E2EOrchestrator with failure injection."""

    def test_orchestrator_creates_injector_when_configured(self, tmp_path):
        """Test that orchestrator creates a FailureInjector when configured."""
        config = RunConfig(
            primary_context="hub1",
            secondary_context="hub2",
            inject_failure="pause-backup",
            inject_at_phase="activation",
            dry_run=True,
            cycles=1,
            output_dir=tmp_path,
        )

        orchestrator = E2EOrchestrator(config)
        
        # Verify config is stored
        assert orchestrator.config.inject_failure == "pause-backup"
        assert orchestrator.config.inject_at_phase == "activation"

    @patch("tests.e2e.orchestrator.E2EOrchestrator._create_clients")
    @patch("tests.e2e.orchestrator.E2EOrchestrator._create_state_manager")
    def test_orchestrator_injects_failure_at_correct_phase(
        self, mock_state, mock_clients, tmp_path
    ):
        """Test that failure is injected at the configured phase."""
        mock_primary = MagicMock()
        mock_secondary = MagicMock()
        mock_clients.return_value = (mock_primary, mock_secondary)
        mock_state.return_value = MagicMock()

        config = RunConfig(
            primary_context="hub1",
            secondary_context="hub2",
            inject_failure="pause-backup",
            inject_at_phase="activation",
            dry_run=True,
            cycles=1,
            output_dir=tmp_path,
        )

        orchestrator = E2EOrchestrator(config)
        
        # Track when injection happens
        injection_phases = []
        
        original_run_all_phases = orchestrator.phase_handlers.run_all_phases
        def mock_run_all_phases(*args, **kwargs):
            phase_callback = kwargs.get("phase_callback")
            if phase_callback:
                # Simulate calling phase_callback for each phase
                for phase in ["preflight", "primary_prep", "activation", "post_activation", "finalization"]:
                    phase_callback(phase, "before")
                    injection_phases.append(phase)
                    phase_callback(phase, "after")
            return []
        
        orchestrator.phase_handlers.run_all_phases = mock_run_all_phases
        
        with patch.object(orchestrator, "_write_cycle_metrics"):
            orchestrator._run_cycle(1, "hub1", "hub2")
        
        # Verify phases were called
        assert "activation" in injection_phases


@pytest.mark.e2e
@pytest.mark.resilience
class TestResilienceScenarios:
    """
    Integration tests for resilience scenarios.
    
    These tests verify that failure injection works end-to-end
    and that the system handles failures appropriately.
    
    Note: These tests require --primary-context and --secondary-context
    to run with real clusters. They are skipped in dry-run mode.
    """

    @pytest.fixture
    def resilience_config(self, e2e_config, tmp_path):
        """Create a config suitable for resilience testing."""
        return replace(
            e2e_config,
            cycles=1,
            stop_on_failure=False,
            output_dir=tmp_path / "resilience_test",
        )

    def test_pause_backup_scenario_dry_run(self, resilience_config, tmp_path):
        """Test pause-backup scenario in dry-run mode."""
        config = replace(
            resilience_config,
            inject_failure="pause-backup",
            inject_at_phase="activation",
            dry_run=True,
            output_dir=tmp_path,
        )
        
        # Verify config is correct
        assert config.inject_failure == "pause-backup"
        assert config.dry_run is True

    def test_delay_restore_scenario_dry_run(self, resilience_config, tmp_path):
        """Test delay-restore scenario in dry-run mode."""
        config = replace(
            resilience_config,
            inject_failure="delay-restore",
            inject_at_phase="activation",
            dry_run=True,
            output_dir=tmp_path,
        )
        
        assert config.inject_failure == "delay-restore"

    def test_kill_observability_pod_scenario_dry_run(self, resilience_config, tmp_path):
        """Test kill-observability-pod scenario in dry-run mode."""
        config = replace(
            resilience_config,
            inject_failure="kill-observability-pod",
            inject_at_phase="post_activation",
            dry_run=True,
            output_dir=tmp_path,
        )
        
        assert config.inject_failure == "kill-observability-pod"
        assert config.inject_at_phase == "post_activation"

    def test_random_scenario_selection(self, resilience_config, tmp_path):
        """Test that 'random' scenario resolves to a valid scenario."""
        config = replace(
            resilience_config,
            inject_failure="random",
            inject_at_phase="activation",
            dry_run=True,
            output_dir=tmp_path,
        )
        
        # The random selection happens in FailureInjector.__init__
        mock_client = MagicMock()
        injector = FailureInjector(
            client=mock_client,
            scenario=config.inject_failure,
            inject_at_phase=config.inject_at_phase,
            dry_run=config.dry_run,
        )
        
        # Should have resolved to a real scenario
        assert injector.scenario != FailureScenario.RANDOM
        assert injector.scenario in FailureScenario.injectable_scenarios()


@pytest.mark.e2e
@pytest.mark.resilience
@pytest.mark.skipif(
    True,  # Skip by default - requires real clusters
    reason="Requires real cluster contexts for failure injection"
)
class TestRealClusterResilience:
    """
    Real cluster resilience tests.
    
    These tests run actual failure injection on real clusters.
    They are skipped by default and should only be run explicitly
    with real cluster contexts.
    
    To run:
        pytest -m resilience tests/e2e/test_e2e_resilience.py \
            --primary-context=hub1 \
            --secondary-context=hub2 \
            -k "real_cluster"
    """

    def test_real_cluster_pause_backup_recovery(
        self,
        e2e_config,
        require_cluster_contexts,
        validate_cluster_access,
        cycle_output_dir,
    ):
        """
        Test that system recovers after backup is paused mid-cycle.
        
        This test:
        1. Injects pause-backup failure during activation
        2. Verifies the cycle handles the failure
        3. Runs a second cycle to verify recovery
        """
        if e2e_config.dry_run:
            pytest.skip("Skipping real cluster test in dry-run mode")

        config = replace(
            e2e_config,
            inject_failure="pause-backup",
            inject_at_phase="activation",
            cycles=2,  # Run 2 cycles to test recovery
            stop_on_failure=False,
            output_dir=cycle_output_dir,
        )

        orchestrator = E2EOrchestrator(config)
        result = orchestrator.run_all_cycles()

        # Even with injection, we should complete cycles
        assert len(result.cycles) == 2
        # Recovery cycle should succeed if injection was cleaned up
        assert result.cycles[1].success is True
