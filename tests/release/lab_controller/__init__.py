"""Deterministic lab role controller primitives for release validation."""

from .artifacts import build_segment_artifact
from .controller import (
    FakeScenarioExecutor,
    ScenarioExecutionResult,
    ScenarioExecutionStatus,
    plan_segment,
    run_segment,
    verify_segment_result,
)
from .decisions import classify_scenario
from .execution import (
    ExecutionBackend,
    ExecutionBackendKind,
    ExecutionMode,
    ExecutionRequest,
    ExecutionResult,
    FakeExecutionBackend,
    ReleaseFrameworkDryRunBackend,
    ReleaseFrameworkLocalBackend,
    build_release_framework_request,
    summarize_execution_request,
    validate_execution_request,
)
from .harness import (
    CommandRunRequest,
    CommandRunResult,
    FakeCommandRunner,
    ReleaseFrameworkExecutionHarness,
    evaluate_execution_gates,
    execute_materialized_invocation,
    summarize_execution_evidence,
)
from .identity import verify_physical_hub_identities
from .models import CertificationDecision
from .planner import (
    CertificationArtifactBundle,
    CertificationPlan,
    CertificationRunResult,
    PlannedSegment,
    RoleTransition,
    SegmentRunResult,
    build_ping_pong_plan,
    evaluate_certification_decision,
    merge_segment_artifacts,
    run_certification_plan,
    run_segment_plan,
)
from .profiles import (
    build_role_aware_profile,
    redact_generated_profile_metadata,
    validate_generated_profile_freshness,
    validate_profile_role_mapping,
    write_generated_profile_yaml,
)
from .recovery import (
    RecoveryCategory,
    RunRecoveryDecision,
    build_recovery_summary,
    classify_segment_stop,
    determine_manual_recovery_requirement,
    determine_retry_eligibility,
    evaluate_run_decision,
)
from .roles import infer_observed_role_state
from .segments import evaluate_segment_chain, evaluate_segment_start, generate_segment_profile

__all__ = [
    # Deterministic model and decision primitives.
    "CertificationDecision",
    "classify_scenario",
    "infer_observed_role_state",
    "verify_physical_hub_identities",
    "RecoveryCategory",
    "RunRecoveryDecision",
    "classify_segment_stop",
    "determine_manual_recovery_requirement",
    "determine_retry_eligibility",
    "evaluate_run_decision",
    "build_recovery_summary",
    # Profile generation and artifact helpers.
    "build_role_aware_profile",
    "build_segment_artifact",
    "redact_generated_profile_metadata",
    "validate_generated_profile_freshness",
    "validate_profile_role_mapping",
    "write_generated_profile_yaml",
    # One-segment controller.
    "FakeScenarioExecutor",
    "generate_segment_profile",
    "plan_segment",
    "run_segment",
    "ScenarioExecutionResult",
    "ScenarioExecutionStatus",
    "verify_segment_result",
    "evaluate_segment_chain",
    "evaluate_segment_start",
    # Multi-segment planner and recovery/final-decision layer.
    "CertificationArtifactBundle",
    "CertificationPlan",
    "CertificationRunResult",
    "evaluate_certification_decision",
    "merge_segment_artifacts",
    "PlannedSegment",
    "RoleTransition",
    "SegmentRunResult",
    "build_ping_pong_plan",
    "run_certification_plan",
    "run_segment_plan",
    # Dry-run, materialization, and explicitly gated local harness abstractions.
    "ExecutionBackend",
    "ExecutionBackendKind",
    "ExecutionMode",
    "ExecutionRequest",
    "ExecutionResult",
    "FakeExecutionBackend",
    "FakeCommandRunner",
    "ReleaseFrameworkDryRunBackend",
    "ReleaseFrameworkLocalBackend",
    "ReleaseFrameworkExecutionHarness",
    "build_release_framework_request",
    "CommandRunRequest",
    "CommandRunResult",
    "evaluate_execution_gates",
    "execute_materialized_invocation",
    "summarize_execution_evidence",
    "summarize_execution_request",
    "validate_execution_request",
]
