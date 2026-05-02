import pytest

from tests.release.scenarios.catalog import select_release_matrix


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
        select_release_matrix(enabled_streams=("python",), scenario_filters=("missing",), stream_filters=())
