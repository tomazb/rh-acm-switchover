"""Deterministic lab role controller primitives for release validation."""

from .artifacts import build_segment_artifact
from .decisions import classify_scenario
from .identity import verify_physical_hub_identities
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
    "build_segment_artifact",
    "classify_scenario",
    "evaluate_segment_chain",
    "evaluate_segment_start",
    "generate_segment_profile",
    "infer_observed_role_state",
    "redact_generated_profile_metadata",
    "validate_generated_profile_freshness",
    "validate_profile_role_mapping",
    "verify_physical_hub_identities",
    "write_generated_profile_yaml",
]
