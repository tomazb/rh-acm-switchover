"""
E2E Testing Package for ACM Switchover.

This package provides Python-based E2E orchestration for running
complete switchover cycles with automated context swapping, metrics
collection, and pytest integration.
"""

from .orchestrator import E2EOrchestrator, RunConfig
from .phase_handlers import PhaseHandlers
from .monitoring import (
    Alert,
    AlertThresholds,
    MetricsLogger,
    MonitoringContext,
    ResourceMonitor,
    ResourceSnapshot,
)
from .failure_injection import (
    FailureInjector,
    FailureScenario,
    InjectionPhase,
    InjectionResult,
)

__all__ = [
    "E2EOrchestrator",
    "RunConfig",
    "PhaseHandlers",
    "Alert",
    "AlertThresholds",
    "MetricsLogger",
    "MonitoringContext",
    "ResourceMonitor",
    "ResourceSnapshot",
    "FailureInjector",
    "FailureScenario",
    "InjectionPhase",
    "InjectionResult",
]
