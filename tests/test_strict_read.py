"""Strict read outcome algebra (R4-03 PR A)."""

import pytest

from lib.strict_read import StrictReadOutcome, StrictReadStatus


def test_items_outcome_carries_a_complete_inventory():
    outcome = StrictReadOutcome.from_items([{"metadata": {"name": "a"}}], resource_version="42")
    assert outcome.status is StrictReadStatus.ITEMS
    assert outcome.is_success is True
    assert outcome.proves_absence is False
    assert outcome.items == [{"metadata": {"name": "a"}}]
    assert outcome.resource_version == "42"


def test_error_outcome_is_never_absence_and_never_an_inventory():
    outcome = StrictReadOutcome.error("read_transport_failed")
    assert outcome.status is StrictReadStatus.ERROR
    assert outcome.is_success is False
    assert outcome.proves_absence is False
    assert outcome.items == []


@pytest.mark.parametrize(
    "factory, status",
    [
        (StrictReadOutcome.crd_absent, StrictReadStatus.CRD_ABSENT),
        (StrictReadOutcome.namespace_absent, StrictReadStatus.NAMESPACE_ABSENT),
        (StrictReadOutcome.object_absent, StrictReadStatus.OBJECT_ABSENT),
    ],
)
def test_positive_absence_outcomes_prove_absence(factory, status):
    outcome = factory("positively_absent")
    assert outcome.status is status
    assert outcome.proves_absence is True
    assert outcome.items == []


def test_non_items_outcome_cannot_carry_items():
    with pytest.raises(ValueError):
        StrictReadOutcome(status=StrictReadStatus.ERROR, items=[{"metadata": {}}])


@pytest.mark.parametrize(
    "factory",
    [
        StrictReadOutcome.crd_absent,
        StrictReadOutcome.namespace_absent,
        StrictReadOutcome.object_absent,
        StrictReadOutcome.error,
    ],
)
def test_absence_and_error_outcomes_never_carry_a_revision(factory):
    """§10.2.1b rule 1: a proof that returns no revision must not manufacture one."""
    assert factory("positively_absent").resource_version is None


@pytest.mark.parametrize(
    "status",
    [
        StrictReadStatus.CRD_ABSENT,
        StrictReadStatus.NAMESPACE_ABSENT,
        StrictReadStatus.OBJECT_ABSENT,
        StrictReadStatus.ERROR,
    ],
)
def test_a_non_items_outcome_cannot_be_given_a_revision(status):
    with pytest.raises(ValueError):
        StrictReadOutcome(status=status, resource_version="88190")


def test_an_empty_string_revision_is_rejected():
    with pytest.raises(ValueError):
        StrictReadOutcome.from_items([], resource_version="")
