from pathlib import Path

import pytest

from tests.release.adapters.ansible import SUPPORTED_SCENARIO_IDS as ANSIBLE_SUPPORTED_SCENARIOS
from tests.release.adapters.bash import SUPPORTED_SCENARIO_IDS as BASH_SUPPORTED_SCENARIOS
from tests.release.adapters.python_cli import SUPPORTED_SCENARIO_IDS as PYTHON_SUPPORTED_SCENARIOS
from tests.release.contracts.loader import load_profile
from tests.release.contracts.models import ScenarioProfile
from tests.release.scenarios.catalog import (
    SCENARIO_LIFECYCLE_BY_ID,
    SCENARIO_SUPPORT_BY_ID,
    SCENARIOS_BY_ID,
    V1_SCENARIOS,
    select_release_matrix,
    validate_release_matrix,
)

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


def test_explicitly_filtered_optional_scenario_becomes_required() -> None:
    selected = select_release_matrix(
        enabled_streams=("python", "ansible"),
        scenario_filters=("rbac-bootstrap-live",),
        stream_filters=(),
    )

    scenario = next(item for item in selected.scenarios if item.id == "rbac-bootstrap-live")
    assert scenario.required is True


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


def test_catalog_lifecycle_and_support_metadata_cover_every_scenario() -> None:
    scenario_ids = {scenario.id for scenario in V1_SCENARIOS}

    assert set(SCENARIO_LIFECYCLE_BY_ID) == scenario_ids
    assert set(SCENARIO_SUPPORT_BY_ID) == scenario_ids
    assert SCENARIO_LIFECYCLE_BY_ID["python-passive-switchover"].final_primary == "secondary"
    assert SCENARIO_LIFECYCLE_BY_ID["ansible-restore-only"].final_primary == "secondary"
    assert SCENARIO_LIFECYCLE_BY_ID["rbac-bootstrap"].mutates_lab is False
    assert SCENARIO_LIFECYCLE_BY_ID["rbac-bootstrap-live"].mutates_lab is False

    for scenario in V1_SCENARIOS:
        lifecycle = SCENARIO_LIFECYCLE_BY_ID[scenario.id]
        assert scenario.mutates_lab == lifecycle.mutates_lab
        if lifecycle.mutates_lab:
            assert lifecycle.recovery_strategy != "none"
            assert lifecycle.reset_required is True


def test_catalog_adapter_support_is_explicit_for_unsupported_pairs() -> None:
    supported_by_stream = {
        "bash": BASH_SUPPORTED_SCENARIOS,
        "python": PYTHON_SUPPORTED_SCENARIOS,
        "ansible": ANSIBLE_SUPPORTED_SCENARIOS,
    }

    for stream, supported_ids in supported_by_stream.items():
        for scenario_id in supported_ids:
            assert stream in SCENARIOS_BY_ID[scenario_id].streams

    for scenario in V1_SCENARIOS:
        support = SCENARIO_SUPPORT_BY_ID[scenario.id]
        for stream in scenario.streams:
            if stream == "local":
                continue
            if scenario.id not in supported_by_stream[stream]:
                assert stream in support.unsupported_stream_reasons

    assert "ansible" in SCENARIO_SUPPORT_BY_ID["full-restore"].unsupported_stream_reasons
    assert SCENARIO_SUPPORT_BY_ID["failure-injection"].unsupported_reason
    assert SCENARIO_SUPPORT_BY_ID["soak"].unsupported_reason


def test_matrix_validation_records_optional_unsupported_pairs_as_not_applicable() -> None:
    selected = select_release_matrix(
        enabled_streams=("python", "ansible"),
        scenario_filters=(),
        stream_filters=(),
        profile_scenarios=(
            ScenarioProfile(id="static-gates"),
            ScenarioProfile(id="lab-readiness"),
            ScenarioProfile(id="baseline-check"),
            ScenarioProfile(id="full-restore", required=False, streams=("ansible",)),
        ),
    )

    validation = validate_release_matrix(
        matrix=selected,
        release_mode="certification",
        scenario_filters=(),
        adapter_supported_scenarios={
            "bash": BASH_SUPPORTED_SCENARIOS,
            "python": PYTHON_SUPPORTED_SCENARIOS,
            "ansible": ANSIBLE_SUPPORTED_SCENARIOS,
        },
    )

    assert validation.status == "passed"
    assert validation.blocked is False
    assert validation.issues[0].status == "not_applicable"
    assert validation.issues[0].scenario_id == "full-restore"
    assert validation.issues[0].stream == "ansible"


def test_matrix_validation_fails_required_unsupported_pairs() -> None:
    selected = select_release_matrix(
        enabled_streams=("python", "ansible"),
        scenario_filters=("full-restore",),
        stream_filters=(),
        profile_scenarios=(ScenarioProfile(id="full-restore", required=True, streams=("ansible",)),),
    )

    validation = validate_release_matrix(
        matrix=selected,
        release_mode="certification",
        scenario_filters=("full-restore",),
        adapter_supported_scenarios={
            "bash": BASH_SUPPORTED_SCENARIOS,
            "python": PYTHON_SUPPORTED_SCENARIOS,
            "ansible": ANSIBLE_SUPPORTED_SCENARIOS,
        },
    )

    assert validation.status == "failed"
    assert validation.blocked is True
    assert validation.issues[0].status == "failed"
    assert "does not implement" in validation.issues[0].reason


def test_matrix_validation_blocks_unsafe_mutating_sequences_in_certification() -> None:
    selected = select_release_matrix(
        enabled_streams=("python", "ansible"),
        scenario_filters=(),
        stream_filters=(),
        profile_scenarios=(
            ScenarioProfile(id="static-gates"),
            ScenarioProfile(id="lab-readiness"),
            ScenarioProfile(id="baseline-check"),
            ScenarioProfile(id="python-passive-switchover", required=True),
            ScenarioProfile(id="ansible-passive-switchover", required=True),
            ScenarioProfile(id="runtime-parity"),
            ScenarioProfile(id="final-baseline-check"),
        ),
    )

    validation = validate_release_matrix(
        matrix=selected,
        release_mode="certification",
        scenario_filters=(),
        adapter_supported_scenarios={
            "bash": BASH_SUPPORTED_SCENARIOS,
            "python": PYTHON_SUPPORTED_SCENARIOS,
            "ansible": ANSIBLE_SUPPORTED_SCENARIOS,
        },
    )

    assert validation.status == "failed"
    assert validation.blocked is True
    assert any("requires reset/recovery" in issue.reason for issue in validation.issues)


def test_matrix_validation_allows_focused_single_mutating_rerun() -> None:
    selected = select_release_matrix(
        enabled_streams=("python", "ansible"),
        scenario_filters=("python-passive-switchover",),
        stream_filters=(),
        profile_scenarios=(ScenarioProfile(id="python-passive-switchover", required=True),),
    )

    validation = validate_release_matrix(
        matrix=selected,
        release_mode="focused-rerun",
        scenario_filters=("python-passive-switchover",),
        adapter_supported_scenarios={
            "bash": BASH_SUPPORTED_SCENARIOS,
            "python": PYTHON_SUPPORTED_SCENARIOS,
            "ansible": ANSIBLE_SUPPORTED_SCENARIOS,
        },
    )

    assert validation.status == "passed"
    assert validation.blocked is False
