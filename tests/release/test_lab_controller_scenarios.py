from __future__ import annotations

from dataclasses import replace

import pytest

from tests.release.lab_controller import decisions
from tests.release.lab_controller.decisions import classify_scenario
from tests.release.lab_controller.models import ScenarioClassification
from tests.release.scenarios.catalog import SCENARIOS_BY_ID


@pytest.mark.parametrize(
    ("scenario_id", "expected"),
    [
        ("preflight", ScenarioClassification.LIVE_NON_MUTATING),
        ("rbac-bootstrap", ScenarioClassification.RECOVERY),
        ("rbac-bootstrap-live", ScenarioClassification.LIVE_NON_MUTATING),
        ("python-passive-switchover", ScenarioClassification.LAB_MUTATING),
        ("ansible-passive-switchover", ScenarioClassification.LAB_MUTATING),
        ("python-restore-only", ScenarioClassification.LAB_MUTATING),
        ("ansible-restore-only", ScenarioClassification.LAB_MUTATING),
        ("argocd-managed-switchover", ScenarioClassification.LAB_MUTATING),
        ("decommission", ScenarioClassification.DESTRUCTIVE_DISPOSABLE_LAB_ONLY),
        ("full-restore", ScenarioClassification.LAB_MUTATING),
        ("checkpoint-resume", ScenarioClassification.LAB_MUTATING),
        ("failure-injection", ScenarioClassification.DESTRUCTIVE_DISPOSABLE_LAB_ONLY),
        ("soak", ScenarioClassification.LAB_MUTATING),
    ],
)
def test_known_scenarios_have_controller_classification(
    scenario_id: str,
    expected: ScenarioClassification,
) -> None:
    assert scenario_id in SCENARIOS_BY_ID

    assert classify_scenario(scenario_id) is expected


def test_static_and_live_non_mutating_catalog_helpers_are_classified() -> None:
    assert classify_scenario("static-gates") is ScenarioClassification.STATIC_ONLY
    assert classify_scenario("runtime-parity") is ScenarioClassification.STATIC_ONLY
    assert classify_scenario("lab-readiness") is ScenarioClassification.LIVE_NON_MUTATING
    assert classify_scenario("baseline-check") is ScenarioClassification.LIVE_NON_MUTATING
    assert classify_scenario("final-baseline-check") is ScenarioClassification.LIVE_NON_MUTATING
    assert classify_scenario("bash-discovery") is ScenarioClassification.LIVE_NON_MUTATING
    assert classify_scenario("bash-postflight") is ScenarioClassification.LIVE_NON_MUTATING


def test_unknown_scenario_classification_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown release scenario"):
        classify_scenario("unknown-future-scenario")


def test_catalog_scenario_without_controller_classification_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    scenario_id = "future-catalog-scenario"
    monkeypatch.setitem(
        decisions.SCENARIOS_BY_ID,
        scenario_id,
        replace(SCENARIOS_BY_ID["preflight"], id=scenario_id),
    )

    with pytest.raises(ValueError, match="unclassified release scenario"):
        classify_scenario(scenario_id)
