from pathlib import Path

import pytest

from tests.release.contracts.loader import load_profile
from tests.release.contracts.models import ScenarioProfile
from tests.release.scenarios.catalog import select_release_matrix

PROFILE_DIR = Path(__file__).resolve().parents[1] / "profiles"


def test_full_matrix_contains_required_scenarios_in_order() -> None:
    selected = select_release_matrix(enabled_streams=("python", "ansible"), scenario_filters=(), stream_filters=())

    assert [item.id for item in selected.scenarios[:4]] == [
        "static-gates",
        "lab-readiness",
        "baseline-check",
        "preflight",
    ]
    assert "runtime-parity" in selected.scenario_ids
    assert "final-baseline-check" in selected.scenario_ids
    assert len(selected.matrix_hash) == 64


def test_catalog_contains_optional_resilience_artifact_scenarios() -> None:
    selected = select_release_matrix(
        enabled_streams=("python", "ansible"),
        scenario_filters=("full-restore", "checkpoint-resume", "decommission", "rbac-bootstrap"),
        stream_filters=(),
    )

    assert selected.scenario_ids == (
        "static-gates",
        "lab-readiness",
        "baseline-check",
        "full-restore",
        "checkpoint-resume",
        "decommission",
        "rbac-bootstrap",
        "runtime-parity",
        "final-baseline-check",
    )
    assert selected.scenarios[6].id == "rbac-bootstrap"
    assert selected.scenarios[6].streams == ("ansible",)


def test_mutating_filter_adds_prerequisites_and_final_checks() -> None:
    selected = select_release_matrix(
        enabled_streams=("python", "ansible"),
        scenario_filters=("python-passive-switchover",),
        stream_filters=(),
    )

    assert selected.scenario_ids == (
        "static-gates",
        "lab-readiness",
        "baseline-check",
        "python-passive-switchover",
        "runtime-parity",
        "final-baseline-check",
    )


def test_unknown_scenario_fails_before_mutation() -> None:
    with pytest.raises(ValueError, match="unknown release scenario"):
        select_release_matrix(
            enabled_streams=("python",),
            scenario_filters=("missing",),
            stream_filters=(),
        )


def test_profile_declared_scenarios_define_full_matrix() -> None:
    profile = load_profile(str(PROFILE_DIR / "dev-minimal.example.yaml")).profile

    selected = select_release_matrix(
        enabled_streams=("python", "ansible"),
        scenario_filters=(),
        stream_filters=(),
        profile_scenarios=profile.scenarios,
    )

    assert selected.scenario_ids == (
        "static-gates",
        "lab-readiness",
        "baseline-check",
        "preflight",
        "final-baseline-check",
    )


def test_mutating_profile_filter_adds_prerequisites_and_final_checks() -> None:
    profile = load_profile(str(PROFILE_DIR / "full-release.example.yaml")).profile

    selected = select_release_matrix(
        enabled_streams=("python", "ansible"),
        scenario_filters=("python-passive-switchover",),
        stream_filters=(),
        profile_scenarios=profile.scenarios,
    )

    assert selected.scenario_ids == (
        "static-gates",
        "lab-readiness",
        "baseline-check",
        "python-passive-switchover",
        "runtime-parity",
        "final-baseline-check",
    )


def test_unknown_profile_scenario_fails_with_clear_error() -> None:
    with pytest.raises(ValueError, match="unknown profile release scenario: missing"):
        select_release_matrix(
            enabled_streams=("python",),
            scenario_filters=(),
            stream_filters=(),
            profile_scenarios=(ScenarioProfile(id="missing"),),
        )


def test_matrix_hash_includes_effective_profile_overrides() -> None:
    base = select_release_matrix(
        enabled_streams=("python", "ansible"),
        scenario_filters=(),
        stream_filters=(),
        profile_scenarios=(ScenarioProfile(id="preflight", required=True, streams=("python", "ansible")),),
    )
    overridden = select_release_matrix(
        enabled_streams=("python", "ansible"),
        scenario_filters=(),
        stream_filters=(),
        profile_scenarios=(ScenarioProfile(id="preflight", required=False, streams=("python",)),),
    )

    assert base.scenario_ids == overridden.scenario_ids
    assert base.matrix_hash != overridden.matrix_hash
