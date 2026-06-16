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
from .identity import verify_physical_hub_identities
from .planner import (
    CertificationArtifactBundle,
    CertificationDecision,
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
from .roles import infer_observed_role_state
from .segments import evaluate_segment_chain, evaluate_segment_start, generate_segment_profile

__all__ = [
    "build_role_aware_profile",
    "build_ping_pong_plan",
    "build_segment_artifact",
    "CertificationArtifactBundle",
    "CertificationDecision",
    "CertificationPlan",
    "CertificationRunResult",
    "classify_scenario",
    "evaluate_certification_decision",
    "evaluate_segment_chain",
    "evaluate_segment_start",
    "FakeScenarioExecutor",
    "generate_segment_profile",
    "infer_observed_role_state",
    "merge_segment_artifacts",
    "PlannedSegment",
    "plan_segment",
    "redact_generated_profile_metadata",
    "RoleTransition",
    "run_certification_plan",
    "run_segment",
    "run_segment_plan",
    "ScenarioExecutionResult",
    "ScenarioExecutionStatus",
    "SegmentRunResult",
    "validate_generated_profile_freshness",
    "validate_profile_role_mapping",
    "verify_segment_result",
    "verify_physical_hub_identities",
    "write_generated_profile_yaml",
]
