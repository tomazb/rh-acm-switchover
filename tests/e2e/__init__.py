"""
E2E Testing Package for ACM Switchover.

This package provides Python-based E2E orchestration for running
complete switchover cycles with automated context swapping, metrics
collection, and pytest integration.
"""

from .orchestrator import E2EOrchestrator, RunConfig
from .phase_handlers import PhaseHandlers

__all__ = [
    "E2EOrchestrator",
    "RunConfig",
    "PhaseHandlers",
]
