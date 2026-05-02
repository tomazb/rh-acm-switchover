import pytest

from tests.release.reporting.schema import validate_required_artifact


def test_manifest_requires_schema_version_one() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        validate_required_artifact("manifest.json", {"schema_version": 2})


def test_scenario_results_requires_lists() -> None:
    with pytest.raises(ValueError, match="results"):
        validate_required_artifact(
            "scenario-results.json",
            {"schema_version": 1, "results": {}, "scenario_statuses": []},
        )


def test_recovery_post_failure_must_be_list() -> None:
    with pytest.raises(ValueError, match="post_failure"):
        validate_required_artifact(
            "recovery.json",
            {
                "schema_version": 1,
                "budget_minutes": 0,
                "budget_consumed_seconds": 0,
                "pre_run": [],
                "post_failure": "not_a_list",
                "hard_stops": [],
                "status": "ok",
            },
        )


def test_unknown_filename_raises_value_error() -> None:
    with pytest.raises(ValueError, match="not a recognised required artifact"):
        validate_required_artifact("unknown.json", {"schema_version": 1})
