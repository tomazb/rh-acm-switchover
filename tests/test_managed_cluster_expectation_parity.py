"""Parity contract: additive ManagedCluster count/name expectations (R4-04 plan Task 6, AC25).

The Python resolver (acm_switchover._resolve_managed_cluster_expectation) and the
collection resolver (roles/common/tasks/resolve_managed_cluster_expectation.yml) share
no code. This test runs the Python function and renders the collection task file with
Ansible's own templar over the same inputs, and requires the same effective floor and
expected names. Names are the recorded preflight expectation in both form factors; an
explicit minimum is only an additional count floor and never clears them.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

from acm_switchover import _resolve_managed_cluster_expectation
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_post_activation import (
    managed_cluster_readiness as col_readiness,
)
from lib.constants import (
    MANAGED_CLUSTER_EXPECTATION_DERIVED_FROM_PREFLIGHT,
    MANAGED_CLUSTER_EXPECTATION_EXPLICIT_EMPTY_ALLOWED,
    MANAGED_CLUSTER_EXPECTATION_EXPLICIT_MINIMUM,
    MANAGED_CLUSTER_EXPECTATION_RESTORE_ONLY,
)
from lib.migration_post_activation import managed_cluster_readiness as py_readiness
from lib.run_record import RunRecord
from lib.utils import StateManager

try:  # ansible-core 2.19+ templates only strings marked as trusted.
    from ansible.template import trust_as_template
except ImportError:  # pragma: no cover - ansible-core < 2.19

    def trust_as_template(value):
        return value


RESOLVER = (
    Path(__file__).resolve().parents[1]
    / "ansible_collections/tomazb/acm_switchover/roles/common/tasks/resolve_managed_cluster_expectation.yml"
)
COLLECTION_MODES = {"activation": "activation_restore_wait", "post_activation": "post_activation_verify"}
NAMES = ["cluster-a", "cluster-b"]


def _render_collection(mode, operation, names, count, configured=None):
    """Run the resolver's assert/set_fact tasks in order and return the published facts."""
    variables = {
        "acm_switchover_managed_cluster_expectation_mode": mode,
        "acm_switchover_operation": operation,
        "acm_switchover_expected_managed_cluster_names": names,
        "acm_switchover_expected_managed_cluster_count": count,
        "acm_switchover_managed_clusters": configured or {},
    }
    for task in yaml.safe_load(RESOLVER.read_text(encoding="utf-8")):
        templar = Templar(loader=DataLoader(), variables=variables)

        def truth(expression):
            return templar.template(trust_as_template("{{ (%s) | bool }}" % expression)) is True

        when = task.get("when", [])
        if not all(truth(condition) for condition in (when if isinstance(when, list) else [when])):
            continue
        if "ansible.builtin.assert" in task:
            for condition in task["ansible.builtin.assert"]["that"]:
                assert truth(condition), task["ansible.builtin.assert"]["fail_msg"]
        else:
            for name, expression in task["ansible.builtin.set_fact"].items():
                rendered = (
                    templar.template(trust_as_template(expression)) if isinstance(expression, str) else expression
                )
                variables[name] = rendered
    return variables


def _python_resolve(tmp_path, restore_only, raw_min, names):
    state = StateManager(str(tmp_path / "state.json"))
    if restore_only:
        mode = MANAGED_CLUSTER_EXPECTATION_RESTORE_ONLY
    elif raw_min is None:
        mode = MANAGED_CLUSTER_EXPECTATION_DERIVED_FROM_PREFLIGHT
    elif raw_min == 0:
        mode = MANAGED_CLUSTER_EXPECTATION_EXPLICIT_EMPTY_ALLOWED
    else:
        mode = MANAGED_CLUSTER_EXPECTATION_EXPLICIT_MINIMUM
    RunRecord(state).record_managed_cluster_expectation(names=names, count=len(names), mode=mode)
    return _resolve_managed_cluster_expectation(SimpleNamespace(min_managed_clusters=raw_min), state)


CASES = [
    (restore_only, raw_min, names)
    for restore_only, names in [(False, NAMES), (False, []), (True, [])]
    for raw_min in [None, 0, 1, 5]
]


@pytest.mark.parametrize("scope", sorted(COLLECTION_MODES))
@pytest.mark.parametrize("restore_only, raw_min, names", CASES)
def test_both_resolvers_publish_the_same_additive_expectation(tmp_path, scope, restore_only, raw_min, names):
    python_min, python_names, python_enforce = _python_resolve(tmp_path, restore_only, raw_min, names)
    operation = {"restore_only": restore_only, "min_managed_clusters": raw_min}
    facts = _render_collection(COLLECTION_MODES[scope], operation, names, len(names))

    assert int(facts["acm_switchover_resolved_min_managed_clusters"]) == python_min
    assert list(facts["acm_switchover_resolved_expected_managed_cluster_names"]) == python_names
    assert python_enforce is bool(python_names)
    # AC25: recorded names survive every count input, and zero is never permitted beside them.
    assert python_names == names
    if names:
        assert facts["acm_switchover_resolved_allow_zero_managed_clusters"] is False


@pytest.mark.parametrize("scope", sorted(COLLECTION_MODES))
def test_collection_allow_zero_request_never_clears_expected_names(scope):
    operation = {"restore_only": False, "min_managed_clusters": 0, "allow_zero_managed_clusters": True}
    facts = _render_collection(COLLECTION_MODES[scope], operation, NAMES, len(NAMES))

    assert facts["acm_switchover_resolved_expected_managed_cluster_names"] == NAMES
    assert facts["acm_switchover_resolved_allow_zero_managed_clusters"] is False


def test_collection_allow_zero_request_still_applies_without_expected_names():
    operation = {"restore_only": True, "min_managed_clusters": None, "allow_zero_managed_clusters": True}
    facts = _render_collection(COLLECTION_MODES["post_activation"], operation, [], 0)

    assert facts["acm_switchover_resolved_allow_zero_managed_clusters"] is True
    assert int(facts["acm_switchover_resolved_min_managed_clusters"]) == 0


def _cluster(name):
    conditions = [{"type": t, "status": "True"} for t in ("ManagedClusterConditionAvailable", "ManagedClusterJoined")]
    return {"metadata": {"name": name}, "status": {"conditions": conditions}}


INVENTORIES = {
    "local-cluster-only": [_cluster("local-cluster")],
    "one-cluster": [_cluster("local-cluster"), _cluster("cluster-x")],
}


@pytest.mark.parametrize("inventory", sorted(INVENTORIES))
@pytest.mark.parametrize("raw_min", [None, 0, 1, 2], ids=["omitted", "zero", "one", "two"])
@pytest.mark.parametrize("scope", sorted(COLLECTION_MODES))
def test_restore_only_configured_clusters_never_become_expected_names(tmp_path, scope, raw_min, inventory):
    """Python records no restore-only names; the klusterlet map is never an expectation (#332 parity)."""
    python_min, python_names, _ = _python_resolve(tmp_path, True, raw_min, [])
    operation = {"restore_only": True, "min_managed_clusters": raw_min}
    configured = {"cluster-a": {"kubeconfig": "./a"}}
    facts = _render_collection(COLLECTION_MODES[scope], operation, [], 0, configured)
    collection_min = int(facts["acm_switchover_resolved_min_managed_clusters"])
    collection_names = list(facts["acm_switchover_resolved_expected_managed_cluster_names"])

    assert (collection_min, collection_names) == (python_min, python_names)
    items = INVENTORIES[inventory]
    python = py_readiness(items=items, expected_names=python_names, min_managed_clusters=python_min, names_waived=False)
    collection = col_readiness(
        items=items, expected_names=collection_names, min_managed_clusters=collection_min, names_waived=False
    )
    assert (collection["ready"], collection["code"]) == (python["ready"], python["code"])


# --- #340 E2: activation never accepts what post-activation structurally refuses -------------

PHASE_INVENTORIES = {
    "empty": [],
    "local-cluster-only": [_cluster("local-cluster")],
    "expected-present": [_cluster("local-cluster"), _cluster("cluster-a")],
    "other-cluster": [_cluster("local-cluster"), _cluster("cluster-z")],
}
EXPECTATIONS = [
    pytest.param([], False, id="nothing-recorded"),
    pytest.param(["cluster-a"], False, id="names-enforced"),
    pytest.param(["cluster-a"], True, id="names-waived"),
]


def _python_activation_accepts(tmp_path, items, names, waived, minimum):
    from unittest.mock import Mock

    from lib.exceptions import FatalError
    from lib.strict_read import StrictReadOutcome
    from modules.activation import SecondaryActivation

    secondary = Mock()
    secondary.list_managed_clusters_strict.return_value = StrictReadOutcome.from_items(items)
    state = StateManager(str(tmp_path / "phase-state.json"))
    activation = SecondaryActivation(
        secondary,
        state,
        method="passive",
        min_managed_clusters=minimum,
        expected_managed_cluster_names=names,
        enforce_expected_managed_cluster_names=bool(names),
    )
    try:
        activation._verify_managed_clusters_restored(enforce_names=not waived)
    except FatalError:
        return False
    return True


def _collection_activation_accepts(items, names, waived, minimum):
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils import migration_activation as col

    read = {"read_status": "ok", "resources": items, "resource_version": "1"}
    try:
        col.verify_managed_clusters(
            mc_read=read, expected_names=names, min_managed_clusters=minimum, enforce=bool(names) and not waived
        )
    except col.ActivationRefused:
        return False
    return True


@pytest.mark.parametrize("minimum", [0, 1])
@pytest.mark.parametrize(("names", "waived"), EXPECTATIONS)
@pytest.mark.parametrize("inventory", sorted(PHASE_INVENTORIES))
def test_activation_never_accepts_an_inventory_post_activation_structurally_refuses(
    tmp_path, inventory, names, waived, minimum
):
    """Every cluster here is Available and Joined, so post-activation can refuse only on the
    inventory shape (names, floor, emptiness). For the same effective expectation, an inventory
    activation accepts must also be ready at post-activation, in both form factors."""
    items = PHASE_INVENTORIES[inventory]
    python_accepts = _python_activation_accepts(tmp_path, items, names, waived, minimum)
    collection_accepts = _collection_activation_accepts(items, names, waived, minimum)
    assert python_accepts == collection_accepts

    python = py_readiness(items=items, expected_names=names, min_managed_clusters=minimum, names_waived=waived)
    collection = col_readiness(items=items, expected_names=names, min_managed_clusters=minimum, names_waived=waived)
    assert (python["ready"], python["code"]) == (collection["ready"], collection["code"])
    if python_accepts:
        assert python["ready"], (inventory, names, waived, minimum, python["code"])
