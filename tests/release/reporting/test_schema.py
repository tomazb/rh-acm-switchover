import pytest

from tests.release.reporting.schema import validate_required_artifact


def test_manifest_requires_schema_version_one() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        validate_required_artifact("manifest.json", {"schema_version": 2})


def test_scenario_results_requires_lists() -> None:
    with pytest.raises(ValueError, match="results"):
        validate_required_artifact("scenario-results.json", {"schema_version": 1, "results": {}, "scenario_statuses": []})
