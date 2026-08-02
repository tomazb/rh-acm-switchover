"""Tests for the RunRecord facade — the named, typed cross-phase interface.

All tests go through the public interface only: no raw key literals, no
reaching into StateManager internals beyond constructing it.
"""

import pytest

from lib.run_record import HubFacts, ManagedClusterExpectation, RunRecord
from lib.utils import StateManager


@pytest.fixture
def state(tmp_path):
    return StateManager(str(tmp_path / "switchover-test.json"))


@pytest.fixture
def record(state):
    return RunRecord(state)


class TestHubFacts:
    def test_defaults_before_recording(self, record):
        facts = record.hub_facts()
        assert facts == HubFacts()
        assert facts.primary_version == "unknown"
        assert facts.has_observability is False

    def test_round_trip(self, record):
        written = HubFacts(
            primary_version="2.13.2",
            primary_observability_detected=True,
            primary_has_observability=True,
            secondary_version="2.14.0",
            secondary_observability_detected=False,
            secondary_has_observability=False,
            has_observability=True,
        )
        record.record_hub_facts(written)
        assert record.hub_facts() == written

    def test_survives_reload(self, state, record):
        record.record_hub_facts(HubFacts(secondary_version="2.14.0"))
        reloaded = RunRecord(StateManager(state.state_file))
        assert reloaded.hub_facts().secondary_version == "2.14.0"


class TestManagedClusterExpectation:
    def test_default_before_recording(self, record):
        assert record.managed_cluster_expectation() == ManagedClusterExpectation()

    def test_round_trip_normalizes_to_tuple(self, record):
        record.record_managed_cluster_expectation(
            names=["cluster-a", "cluster-b"], count=2, mode="derived_from_preflight"
        )
        exp = record.managed_cluster_expectation()
        assert exp.names == ("cluster-a", "cluster-b")
        assert exp.count == 2
        assert exp.mode == "derived_from_preflight"


class TestPreflightResults:
    def test_record_writes_results_and_summary(self, state, record):
        results = [{"check": "versions", "status": "pass", "message": "ok"}]
        record.record_preflight_results(results, passed=True, critical_failures=0)
        # Interface-only persistence: the raw file keeps today's key names.
        snapshot = state.capture_state_snapshot()
        assert snapshot["config"]["preflight_results"] == results
        assert snapshot["config"]["preflight_summary"] == {
            "passed": True,
            "critical_failures": 0,
            "total": 1,
        }
