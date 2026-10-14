"""Parity contract: post-activation completion evidence decides identically in both form factors.

The Python CLI's PostActivationVerification and the collection's acm_migration_evidence
decisions (module_utils/migration_post_activation.py, driven in the order
roles/post_activation/tasks/main.yml runs them) start from the same journal and see the same
strict ManagedCluster inventory. The refusal code, the persisted journal and the order of the
journal writes must agree. Klusterlet, annotation cleanup and observability work is replaced
by a stub that succeeds or fails identically; timestamps are pinned. The YAML wiring itself is
covered by the collection's integration tests against a fake API.
"""

import copy
import json
import re
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import modules.post_activation as post_activation_module
from ansible_collections.tomazb.acm_switchover.plugins.module_utils import checkpoint as col_checkpoint
from ansible_collections.tomazb.acm_switchover.plugins.module_utils import migration_activation as col_activation
from ansible_collections.tomazb.acm_switchover.plugins.module_utils import migration_post_activation as col
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_evidence import (
    MigrationEvidenceError as ColMigrationEvidenceError,
)
from lib.exceptions import SwitchoverError
from lib.run_record import RunRecord
from lib.strict_read import StrictReadOutcome
from lib.utils import StateManager

NOW = "2026-10-01T00:00:00+00:00"
NAMES = ["cluster-a", "cluster-b"]
_CASES = {
    case["id"]: case
    for case in json.loads(
        (Path(__file__).parent / "fixtures" / "r4_04_migration_evidence_vectors.json").read_text(encoding="utf-8")
    )["cases"]
}
COMPLETED = _CASES["post-activation-journal-completed"]["input"]["journal"]
PRE_COMPLETION = _CASES["post-activation-journal-pre-completion-blocks"]["input"]["journal"]
BOTH_WAIVED = _CASES["post-activation-waiver-journaled-both-reused"]["input"]["journal"]
RECORDED = _CASES["completion-kept-on-resume"]["input"]["journal"]
REQUEST = {"scope": "post_activation", "actor": "operator@example.com", "reason": "stale list", "request_id": None}
_CODE = re.compile(r"\(([a-z]+_[a-z_]+)[:)]")


def _mc(name, available="True"):
    return {
        "metadata": {"name": name},
        "status": {
            "conditions": [
                {"type": "ManagedClusterConditionAvailable", "status": available},
                {"type": "ManagedClusterJoined", "status": "True"},
            ]
        },
    }


def _inventory(*names, unavailable=()):
    return [_mc("local-cluster")] + [_mc(name, "False" if name in unavailable else "True") for name in names]


class LaterWorkFailed(Exception):
    """The stubbed klusterlet, cleanup or observability work failed."""


class Scenario:
    def __init__(self, tmp_path, journal, outcome, expected=NAMES, minimum=2, requested=None, failing=None, polls=1):
        self.tmp_path = tmp_path
        self.journal = journal
        # One outcome, or the successive outcomes of the strict reads (the last one repeats).
        self.outcomes = list(outcome) if isinstance(outcome, list) else [outcome]
        self.polls = polls
        self.expected = list(expected)
        self.minimum = minimum
        self.requested = requested
        self.failing = failing  # None, "connectivity" or "later"

    # -- Python CLI ------------------------------------------------------------------------

    def python(self):
        state = StateManager(str(self.tmp_path / "state.json"))
        if self.journal is not None:
            state.state["config"]["migration_backups"] = copy.deepcopy(self.journal)
        state.flush_state()
        writes = []
        secondary = Mock(name="secondary")
        outcomes = list(self.outcomes)
        secondary.list_managed_clusters_strict.side_effect = lambda: (
            outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
        )
        verifier = post_activation_module.PostActivationVerification(
            secondary_client=secondary,
            state_manager=state,
            has_observability=False,
            min_managed_clusters=self.minimum,
            expected_managed_cluster_names=self.expected,
            enforce_expected_managed_cluster_names=bool(self.expected),
            managed_cluster_expectation_waiver=self.requested,
        )
        record = RunRecord.record_migration_backups

        def recording(run_record, candidate):
            writes.append(copy.deepcopy(candidate["post_activation"]))
            record(run_record, candidate)

        def klusterlet():
            if self.failing == "connectivity":
                raise SwitchoverError("klusterlet failed (later_work_failed)")

        def later():
            if self.failing == "later":
                raise SwitchoverError("cleanup failed (later_work_failed)")

        with patch.object(post_activation_module, "_now_iso", return_value=NOW), patch.object(
            RunRecord, "record_migration_backups", recording
        ), patch.object(verifier, "_verify_klusterlet_connections", side_effect=klusterlet), patch.object(
            verifier, "_verify_disable_auto_import_cleared", side_effect=later
        ), patch(
            "modules.post_activation.wait_for_condition",
            side_effect=lambda _d, condition, **_k: any(condition().done for _ in range(self.polls)),
        ):
            passed = verifier.verify()
        code = None
        if not passed:
            error = state.state["errors"][-1]["error"]
            match = _CODE.search(error)
            code = "inventory_read_failed" if "inventory read failed" in error else match.group(1)
        return code, RunRecord(state).migration_backups(), writes

    # -- collection, in roles/post_activation/tasks/main.yml order --------------------------

    def collection(self):
        store = {"operational_data": {}}
        if self.journal is not None:
            store["operational_data"]["migration_backups"] = copy.deepcopy(self.journal)
        writes = []

        def persist(decision):
            if decision["write"]:
                writes.append(copy.deepcopy(decision["candidate"]["post_activation"]))
                col_checkpoint.record_migration_backups(store, decision["candidate"])
            return decision["candidate"]

        outcomes = list(self.outcomes)

        def read():
            outcome = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
            if not outcome.is_success:
                raise col_activation.ActivationRefused("ManagedCluster inventory read failed (error)")
            return outcome.items

        try:
            journal = col.require_completed_restore(col_checkpoint.migration_backups(store))
            waiver = col.post_activation_waiver(
                journal=journal,
                requested=self.requested,
                expected_names=self.expected,
                flag=col_activation.WAIVER_FLAG,
                journaled_at=NOW,
            )["waiver"]
            for _ in range(self.polls):  # roles/post_activation/tasks/poll_managed_cluster_readiness.yml
                readiness = col.managed_cluster_readiness(
                    items=read(),
                    expected_names=self.expected,
                    min_managed_clusters=self.minimum,
                    names_waived=waiver is not None,
                )
                if readiness["ready"]:
                    break
            if self.failing == "connectivity":
                raise LaterWorkFailed("later_work_failed")
            if readiness["ready"]:
                journal = persist(
                    col.post_activation_names_evidence(journal=journal, readiness=readiness, waiver=waiver, now=NOW)
                )
            if self.failing == "later":
                raise LaterWorkFailed("later_work_failed")
            if not readiness["ready"]:
                return readiness["code"], col_checkpoint.migration_backups(store), writes
            persist(col.post_activation_completion(journal=journal, now=NOW))
        except ColMigrationEvidenceError as exc:
            return exc.code, col_checkpoint.migration_backups(store), writes
        except col_activation.ActivationRefused:
            return "inventory_read_failed", col_checkpoint.migration_backups(store), writes
        except LaterWorkFailed as exc:
            return str(exc), col_checkpoint.migration_backups(store), writes
        return None, col_checkpoint.migration_backups(store), writes


def _comparable(journal):
    """The Python CLI journals its --skip-managed-cluster-expectations flag, the collection its variable."""
    if journal is None or journal.get("waiver") is None:
        return journal
    flags = {"--skip-managed-cluster-expectations", col_activation.WAIVER_FLAG, BOTH_WAIVED["waiver"]["flag"]}
    assert journal["waiver"]["flag"] in flags
    return dict(journal, waiver=dict(journal["waiver"], flag="<flag>"))


def _decide(tmp_path, journal=COMPLETED, outcome=None, **kwargs):
    scenario = Scenario(tmp_path, journal, outcome or StrictReadOutcome.from_items(_inventory(*NAMES)), **kwargs)
    python_code, python_journal, python_writes = scenario.python()
    collection_code, collection_journal, collection_writes = scenario.collection()
    assert python_code == collection_code
    assert _comparable(python_journal) == _comparable(collection_journal)
    assert python_writes == collection_writes
    return python_code, python_journal, python_writes


def test_names_evidence_then_completion_identically(tmp_path):
    code, journal, writes = _decide(tmp_path)
    assert code is None
    assert writes == [
        {"names_verified_at": NOW, "completed_at": None},
        {"names_verified_at": NOW, "completed_at": NOW},
    ]


@pytest.mark.parametrize(
    "journal, code",
    [
        (None, "journal_required"),
        (PRE_COMPLETION, "restore_not_completed"),
    ],
    ids=["no-journal", "restore-not-completed"],
)
def test_an_executing_run_without_a_completed_journal_refuses_identically(tmp_path, journal, code):
    assert _decide(tmp_path, journal)[0] == code


@pytest.mark.parametrize(
    "items, code",
    [
        ([], "inventory_empty"),
        (_inventory("cluster-a", "cluster-c"), "expected_names_missing"),
        (_inventory("cluster-a", "cluster-b", unavailable=("cluster-b",)), "clusters_not_ready"),
    ],
    ids=["empty", "missing-name", "not-available"],
)
def test_an_unready_inventory_records_no_evidence_identically(tmp_path, items, code):
    result = _decide(tmp_path, outcome=StrictReadOutcome.from_items(items))
    assert result[0] == code and result[2] == []


def test_an_explicit_zero_floor_without_names_completes_on_an_empty_inventory_identically(tmp_path):
    """A hub with self-management disabled has no local-cluster: with an explicit minimum of 0
    and no expected name, an empty inventory completes post-activation in both form factors."""
    code, _journal, writes = _decide(tmp_path, outcome=StrictReadOutcome.from_items([]), expected=[], minimum=0)
    assert code is None
    assert writes == [
        {"names_verified_at": NOW, "completed_at": None},
        {"names_verified_at": NOW, "completed_at": NOW},
    ]


def test_an_empty_inventory_with_a_positive_floor_records_no_evidence_identically(tmp_path):
    result = _decide(tmp_path, outcome=StrictReadOutcome.from_items([]), expected=[], minimum=1)
    assert result[0] == "inventory_empty" and result[2] == []


def test_the_count_floor_is_enforced_alongside_names_identically(tmp_path):
    assert _decide(tmp_path, minimum=3)[0] == "count_below_minimum"


@pytest.mark.parametrize(
    "outcome",
    [StrictReadOutcome.error("Forbidden"), StrictReadOutcome.crd_absent("not served")],
    ids=["error", "crd-absent"],
)
def test_a_failed_strict_inventory_is_never_zero_clusters_identically(tmp_path, outcome):
    result = _decide(tmp_path, outcome=outcome, expected=[], minimum=0)
    assert result[0] == "inventory_read_failed" and result[2] == []


def test_a_post_activation_waiver_lifts_only_the_name_check_identically(tmp_path):
    code, journal, writes = _decide(
        tmp_path, outcome=StrictReadOutcome.from_items(_inventory("cluster-a", "cluster-c")), requested=REQUEST
    )
    assert code is None
    assert journal["waiver"]["outcome"] == "waived" and journal["post_activation"]["names_verified_at"] is None
    assert writes == [
        {"names_verified_at": None, "completed_at": None},
        {"names_verified_at": None, "completed_at": NOW},
    ]


def test_a_waiver_never_lifts_the_floor_identically(tmp_path):
    outcome = StrictReadOutcome.from_items(_inventory("cluster-a"))
    assert _decide(tmp_path, outcome=outcome, requested=REQUEST)[0] == "count_below_minimum"


@pytest.mark.parametrize(
    "requested, code",
    [
        (dict(REQUEST, scope="both"), "expected_names_missing"),
        ({"scope": "both", "actor": "other", "reason": "x", "request_id": None}, "waiver_request_mismatch"),
    ],
    ids=["both-scope-not-journaled-here", "differing-request"],
)
def test_waiver_scope_rules_decide_identically(tmp_path, requested, code):
    journal = BOTH_WAIVED if code == "waiver_request_mismatch" else COMPLETED
    outcome = StrictReadOutcome.from_items(_inventory("cluster-a", "cluster-c"))
    assert _decide(tmp_path, journal, outcome=outcome, requested=requested)[0] == code


def test_a_waiver_request_without_names_is_refused_identically(tmp_path):
    assert _decide(tmp_path, expected=[], minimum=1, requested=REQUEST)[0] == "waiver_expected_names_empty"


@pytest.mark.parametrize(
    "failing, names_recorded",
    [("connectivity", False), ("later", True)],
    ids=["klusterlet-failure", "cleanup-failure"],
)
def test_a_failure_leaves_completion_absent_identically(tmp_path, failing, names_recorded):
    code, journal, _ = _decide(tmp_path, failing=failing)
    assert code == "later_work_failed"
    assert journal["post_activation"]["completed_at"] is None
    assert (journal["post_activation"]["names_verified_at"] is not None) is names_recorded


def test_resume_keeps_recorded_evidence_identically(tmp_path):
    code, journal, writes = _decide(tmp_path, RECORDED)
    assert code is None and journal == RECORDED and writes == []


def test_a_failed_revalidation_keeps_recorded_evidence_identically(tmp_path):
    outcome = StrictReadOutcome.from_items(_inventory("cluster-a", "cluster-c"))
    code, journal, writes = _decide(tmp_path, RECORDED, outcome=outcome)
    assert code == "expected_names_missing" and journal == RECORDED and writes == []


def test_w2_a_transient_unreadiness_is_reconciled_by_the_same_bounded_wait(tmp_path):
    not_ready = StrictReadOutcome.from_items(_inventory(*NAMES, unavailable=("cluster-a",)))
    ready = StrictReadOutcome.from_items(_inventory(*NAMES))
    code, journal, _ = _decide(tmp_path, outcome=[not_ready, ready], polls=2)
    assert code is None and journal["post_activation"]["completed_at"] == NOW
