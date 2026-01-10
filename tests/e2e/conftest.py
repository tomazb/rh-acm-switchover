"""
Pytest fixtures for E2E testing.

This module provides fixtures for running end-to-end switchover tests,
including CLI option handling, KubeClient creation, and E2EOrchestrator setup.
"""

import os
import pytest
from pathlib import Path
from typing import Optional

from tests.e2e.orchestrator import RunConfig, E2EOrchestrator


def pytest_addoption(parser):
    """Add E2E-specific command line options."""
    group = parser.getgroup("e2e", "E2E Testing Options")
    
    group.addoption(
        "--primary-context",
        action="store",
        default=os.environ.get("E2E_PRIMARY_CONTEXT", ""),
        help="Kubernetes context for the primary hub (env: E2E_PRIMARY_CONTEXT)"
    )
    
    group.addoption(
        "--secondary-context",
        action="store",
        default=os.environ.get("E2E_SECONDARY_CONTEXT", ""),
        help="Kubernetes context for the secondary hub (env: E2E_SECONDARY_CONTEXT)"
    )
    
    group.addoption(
        "--e2e-cycles",
        action="store",
        default=os.environ.get("E2E_CYCLES", "1"),
        type=int,
        help="Number of switchover cycles to run (env: E2E_CYCLES, default: 1)"
    )
    
    group.addoption(
        "--e2e-dry-run",
        action="store_true",
        default=os.environ.get("E2E_DRY_RUN", "").lower() in ("1", "true", "yes"),
        help="Run in dry-run mode without making changes (env: E2E_DRY_RUN)"
    )
    
    group.addoption(
        "--e2e-method",
        action="store",
        default=os.environ.get("E2E_METHOD", "passive"),
        choices=["passive"],
        help="Switchover method (env: E2E_METHOD, default: passive)"
    )
    
    group.addoption(
        "--e2e-old-hub-action",
        action="store",
        default=os.environ.get("E2E_OLD_HUB_ACTION", "secondary"),
        choices=["secondary", "decommission"],
        help="Action for old hub after switchover (env: E2E_OLD_HUB_ACTION, default: secondary)"
    )
    
    group.addoption(
        "--e2e-output-dir",
        action="store",
        default=os.environ.get("E2E_OUTPUT_DIR", ""),
        help="Output directory for E2E artifacts (env: E2E_OUTPUT_DIR)"
    )
    
    group.addoption(
        "--e2e-stop-on-failure",
        action="store_true",
        default=os.environ.get("E2E_STOP_ON_FAILURE", "").lower() in ("1", "true", "yes"),
        help="Stop on first cycle failure (env: E2E_STOP_ON_FAILURE)"
    )
    
    group.addoption(
        "--e2e-cooldown",
        action="store",
        default=os.environ.get("E2E_COOLDOWN", "30"),
        type=int,
        help="Cooldown seconds between cycles (env: E2E_COOLDOWN, default: 30)"
    )

    group.addoption(
        "--e2e-run-hours",
        action="store",
        default=os.environ.get("E2E_RUN_HOURS", None),
        type=float,
        help="Time limit in hours for soak testing (env: E2E_RUN_HOURS)"
    )

    group.addoption(
        "--e2e-max-failures",
        action="store",
        default=os.environ.get("E2E_MAX_FAILURES", None),
        type=int,
        help="Stop after N failures (env: E2E_MAX_FAILURES)"
    )

    group.addoption(
        "--e2e-resume",
        action="store_true",
        default=os.environ.get("E2E_RESUME", "").lower() in ("1", "true", "yes"),
        help="Resume from last completed cycle (env: E2E_RESUME)"
    )

    group.addoption(
        "--e2e-inject-failure",
        action="store",
        default=os.environ.get("E2E_INJECT_FAILURE", None),
        choices=["pause-backup", "delay-restore", "kill-observability-pod", "random"],
        help="Inject failure scenario during cycle (env: E2E_INJECT_FAILURE)"
    )

    group.addoption(
        "--e2e-inject-at-phase",
        action="store",
        default=os.environ.get("E2E_INJECT_AT_PHASE", "activation"),
        choices=["preflight", "primary_prep", "activation", "post_activation", "finalization"],
        help="Phase at which to inject failure (env: E2E_INJECT_AT_PHASE, default: activation)"
    )


def pytest_configure(config):
    """Register E2E and resilience markers."""
    config.addinivalue_line(
        "markers",
        "e2e: End-to-end tests requiring real clusters"
    )
    config.addinivalue_line(
        "markers",
        "resilience: Resilience tests with failure injection"
    )


@pytest.fixture(scope="session")
def e2e_config(request, tmp_path_factory) -> RunConfig:
    """
    Create E2E configuration from command line options.
    
    This fixture creates a RunConfig object from pytest command line options,
    suitable for use with the E2EOrchestrator.
    
    Returns:
        RunConfig: Configuration for E2E test runs
    """
    primary = request.config.getoption("--primary-context")
    secondary = request.config.getoption("--secondary-context")
    dry_run = request.config.getoption("--e2e-dry-run")
    
    # Determine output directory
    output_dir_opt = request.config.getoption("--e2e-output-dir")
    if output_dir_opt:
        output_dir = Path(output_dir_opt)
    else:
        # Use pytest's tmp_path_factory for session-scoped temp directory
        output_dir = tmp_path_factory.mktemp("e2e_results")
    
    return RunConfig(
        primary_context=primary,
        secondary_context=secondary,
        method=request.config.getoption("--e2e-method"),
        old_hub_action=request.config.getoption("--e2e-old-hub-action"),
        cycles=request.config.getoption("--e2e-cycles"),
        dry_run=dry_run,
        output_dir=output_dir,
        stop_on_failure=request.config.getoption("--e2e-stop-on-failure"),
        cooldown_seconds=request.config.getoption("--e2e-cooldown"),
        run_hours=request.config.getoption("--e2e-run-hours"),
        max_failures=request.config.getoption("--e2e-max-failures"),
        resume=request.config.getoption("--e2e-resume"),
        inject_failure=request.config.getoption("--e2e-inject-failure"),
        inject_at_phase=request.config.getoption("--e2e-inject-at-phase"),
    )


@pytest.fixture(scope="session")
def require_cluster_contexts(e2e_config: RunConfig):
    """
    Ensure cluster contexts are provided for real cluster tests.
    
    This fixture should be used by tests that require actual cluster access.
    It will skip the test if contexts are not provided and dry-run is not enabled.
    """
    if e2e_config.dry_run:
        # Dry-run mode doesn't need real contexts
        return
    
    if not e2e_config.primary_context:
        pytest.skip("--primary-context not provided (required for real cluster tests)")
    
    if not e2e_config.secondary_context:
        pytest.skip("--secondary-context not provided (required for real cluster tests)")


@pytest.fixture(scope="session")
def primary_client(e2e_config: RunConfig, require_cluster_contexts):
    """
    Create a KubeClient for the primary hub.
    
    This fixture creates a session-scoped KubeClient connected to the primary hub.
    Tests using this fixture will be skipped if contexts are not provided.
    
    Returns:
        KubeClient: Client connected to the primary hub
    """
    if e2e_config.dry_run:
        pytest.skip("Skipping real client creation in dry-run mode")
    
    from lib.kube_client import KubeClient
    return KubeClient(context=e2e_config.primary_context)


@pytest.fixture(scope="session")
def secondary_client(e2e_config: RunConfig, require_cluster_contexts):
    """
    Create a KubeClient for the secondary hub.
    
    This fixture creates a session-scoped KubeClient connected to the secondary hub.
    Tests using this fixture will be skipped if contexts are not provided.
    
    Returns:
        KubeClient: Client connected to the secondary hub
    """
    if e2e_config.dry_run:
        pytest.skip("Skipping real client creation in dry-run mode")
    
    from lib.kube_client import KubeClient
    return KubeClient(context=e2e_config.secondary_context)


@pytest.fixture(scope="session")
def validate_cluster_access(primary_client, secondary_client):
    """
    Validate that both clusters are accessible.
    
    This fixture performs a basic connectivity check to both clusters
    to ensure they are reachable before running E2E tests.
    
    Raises:
        pytest.fail: If either cluster is not accessible
    """
    try:
        # Simple connectivity check - list namespaces
        primary_client.core_v1.list_namespace(limit=1)
    except Exception as e:
        pytest.fail(f"Cannot connect to primary cluster: {e}")
    
    try:
        secondary_client.core_v1.list_namespace(limit=1)
    except Exception as e:
        pytest.fail(f"Cannot connect to secondary cluster: {e}")


@pytest.fixture
def e2e_orchestrator(e2e_config: RunConfig) -> E2EOrchestrator:
    """
    Create an E2EOrchestrator instance.
    
    This fixture creates an orchestrator configured with the E2E options
    from the command line.
    
    Returns:
        E2EOrchestrator: Configured orchestrator instance
    """
    return E2EOrchestrator(e2e_config)


@pytest.fixture
def e2e_orchestrator_factory(e2e_config: RunConfig):
    """
    Factory fixture for creating E2EOrchestrator instances with custom config.
    
    This allows tests to override specific config values while keeping
    the base configuration from command line options.
    
    Returns:
        Callable that creates E2EOrchestrator instances
    """
    from dataclasses import replace
    
    def create_orchestrator(**overrides) -> E2EOrchestrator:
        config = replace(e2e_config, **overrides)
        return E2EOrchestrator(config)
    
    return create_orchestrator


@pytest.fixture
def cycle_output_dir(tmp_path) -> Path:
    """
    Create a temporary output directory for a single test's cycle results.
    
    Returns:
        Path: Temporary directory for test artifacts
    """
    output_dir = tmp_path / "e2e_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir
