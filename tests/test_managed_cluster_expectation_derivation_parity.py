"""Parity contract: the expected ManagedCluster set is derived only from a positively
complete strict primary inventory (#340, design 2026-10-15 E1).

lib.migration_post_activation.expected_managed_cluster_names and its collection mirror
share no code. Both take the members of a strict LIST whose caller already refused every
non-success read outcome as ``inventory_unreadable`` (Python
PreflightValidator._derive_expected_managed_cluster_names, collection
acm_migration_evidence ``expected_managed_clusters``; each caller is tested on its own
side). Here both must derive the same names or refuse with the same code: a nameless,
malformed or repeated member refuses the whole derivation and is never dropped.
"""

from unittest.mock import Mock

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_evidence import (
    MigrationEvidenceError as ColMigrationEvidenceError,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_post_activation import (
    expected_managed_cluster_names as col_derive,
)
from lib.migration_evidence import MigrationEvidenceError
from lib.migration_post_activation import expected_managed_cluster_names as py_derive
from lib.strict_read import StrictReadOutcome
from modules.preflight import ValidationReporter
from modules.preflight_coordinator import PreflightValidator


def _cluster(name):
    return {"metadata": {"name": name}}


DERIVED = [
    pytest.param([], [], id="positively-empty-inventory-is-a-legitimate-zero"),
    pytest.param([_cluster("local-cluster")], [], id="local-cluster-only-is-a-legitimate-zero"),
    pytest.param(
        [_cluster("cluster-b"), _cluster("local-cluster"), _cluster("cluster-a")],
        ["cluster-a", "cluster-b"],
        id="non-local-names-sorted",
    ),
]

REFUSED = [
    pytest.param(None, "malformed_inventory", id="no-member-list"),
    pytest.param({"items": []}, "malformed_inventory", id="member-list-not-a-list"),
    pytest.param([_cluster("cluster-a"), {"metadata": {}}], "malformed_inventory", id="nameless-member"),
    pytest.param([_cluster("cluster-a"), {"metadata": {"name": ""}}], "malformed_inventory", id="empty-name"),
    pytest.param([_cluster("cluster-a"), {"metadata": {"name": 7}}], "malformed_inventory", id="non-string-name"),
    pytest.param([_cluster("cluster-a"), {"metadata": None}], "malformed_inventory", id="metadata-not-an-object"),
    pytest.param([_cluster("cluster-a"), "cluster-b"], "malformed_inventory", id="non-object-member"),
    pytest.param([_cluster("cluster-a"), _cluster("cluster-a")], "duplicate_managed_cluster", id="duplicate-name"),
    pytest.param(
        [_cluster("local-cluster"), _cluster("local-cluster")],
        "duplicate_managed_cluster",
        id="duplicate-local-cluster",
    ),
]


@pytest.mark.parametrize(("items", "names"), DERIVED)
def test_a_positively_complete_inventory_derives_the_same_names(items, names):
    assert py_derive(items) == names
    assert col_derive(items) == names


@pytest.mark.parametrize(("items", "code"), REFUSED)
def test_a_malformed_inventory_refuses_the_whole_derivation_identically(items, code):
    with pytest.raises(MigrationEvidenceError) as py_exc:
        py_derive(items)
    with pytest.raises(ColMigrationEvidenceError) as col_exc:
        col_derive(items)
    assert py_exc.value.code == col_exc.value.code == code


@pytest.mark.parametrize(
    "outcome",
    [
        pytest.param(StrictReadOutcome.crd_absent("kind_not_served"), id="api-not-served"),
        pytest.param(StrictReadOutcome.error("read_failed"), id="read-failed"),
        pytest.param(StrictReadOutcome.error("inventory_incomplete"), id="incomplete-pagination"),
        pytest.param(StrictReadOutcome.error("malformed_response"), id="malformed-response"),
    ],
)
def test_python_refuses_every_non_success_read_as_inventory_unreadable(outcome):
    """The Python caller half of the contract; the collection half is in
    tests/unit/test_preflight_managed_cluster_expectation.py with the same code."""
    validator = PreflightValidator.__new__(PreflightValidator)
    validator.restore_only = False
    validator.primary = Mock()
    validator.primary.list_managed_clusters_strict.return_value = outcome
    validator.reporter = ValidationReporter()

    assert validator._derive_expected_managed_cluster_names() == []
    (failure,) = validator.reporter.critical_failures()
    assert "inventory_unreadable" in failure["message"]
    assert outcome.reason in failure["message"]


def test_python_refuses_a_missing_primary_outside_restore_only():
    """No primary read is never zero names; the collection refuses an absent read the same way."""
    validator = PreflightValidator.__new__(PreflightValidator)
    validator.restore_only = False
    validator.primary = None
    validator.reporter = ValidationReporter()

    assert validator._derive_expected_managed_cluster_names() == []
    (failure,) = validator.reporter.critical_failures()
    assert "inventory_unreadable" in failure["message"]
