"""R4-04 plan Task 6: the collection's waiver and cleanup-repair operator inputs."""

import pathlib

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

try:  # ansible-core 2.19+ templates only strings marked as trusted.
    from ansible.template import trust_as_template
except ImportError:  # pragma: no cover - ansible-core < 2.19

    def trust_as_template(value):
        return value


COLLECTION_DIR = pathlib.Path(__file__).resolve().parents[2]
ROLES_DIR = COLLECTION_DIR / "roles"
POST_IDENTITY = ROLES_DIR / "preflight" / "tasks" / "post_identity.yml"
WAIVER_TASK = "Reject a ManagedCluster expectation waiver with no expected name to waive"
EMPTY_WAIVER = {"scope": None, "actor": "", "reason": "", "request_id": None}
EMPTY_REPAIR = {"actor": "", "reason": "", "inspected_evidence": [], "run_id": "", "operation_id": ""}


def _load(path: pathlib.Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _waiver_task() -> dict:
    return next(task for task in _load(POST_IDENTITY) if task.get("name") == WAIVER_TASK)


def _waiver_accepted(scope, restore_only, names, configured=None) -> bool:
    operation = {"restore_only": restore_only, "skip_managed_cluster_expectations": scope is not None}
    if scope is not None:
        operation["managed_cluster_expectation_waiver"] = dict(EMPTY_WAIVER, scope=scope, actor="a", reason="r")
    task = _waiver_task()
    variables = {
        "acm_switchover_operation": operation,
        "acm_switchover_expected_managed_cluster_names": names,
        "acm_switchover_managed_clusters": configured or {},
    }
    # Task vars are templated lazily on use, as Ansible does.
    variables.update({name: trust_as_template(value) for name, value in task.get("vars", {}).items()})
    templar = Templar(loader=DataLoader(), variables=variables)
    return all(
        templar.template(trust_as_template("{{ (%s) | bool }}" % condition)) is True
        for condition in task["ansible.builtin.assert"]["that"]
    )


@pytest.mark.parametrize(
    "scope, restore_only, names, configured, accepted",
    [
        (None, False, [], None, True),
        (None, True, [], None, True),
        ("activation", False, ["cluster-a"], None, True),
        ("post_activation", False, ["cluster-a"], None, True),
        ("both", False, ["cluster-a"], None, True),
        ("activation", False, [], None, False),
        ("post_activation", False, [], None, False),
        ("both", False, [], None, False),
        ("post_activation", True, [], None, False),
        # restore_only records no expected name; configured klusterlet clusters are not one (Python parity).
        ("post_activation", True, [], {"cluster-a": {"kubeconfig": "./a"}}, False),
        ("post_activation", True, ["cluster-a"], None, False),
        ("both", True, [], {"cluster-a": {"kubeconfig": "./a"}}, False),
    ],
)
def test_preflight_rejects_a_waiver_whose_scope_has_no_expected_name(scope, restore_only, names, configured, accepted):
    assert _waiver_accepted(scope, restore_only, names, configured) is accepted


def test_preflight_reads_the_enabling_field_as_a_real_boolean_only():
    """Review finding 3: the assert and input validation share one interpretation."""
    condition = " ".join(_waiver_task()["ansible.builtin.assert"]["that"])
    assert "skip_managed_cluster_expectations | default(false)) is sameas true" in condition
    assert "skip_managed_cluster_expectations | default(false) | bool" not in condition


def test_waiver_rejection_names_its_error_class_and_runs_before_the_checkpoint_pass():
    tasks = _load(POST_IDENTITY)
    names = [task.get("name") for task in tasks]
    task = _waiver_task()

    assert task["ansible.builtin.assert"]["fail_msg"].startswith("waiver_expected_names_empty:")
    assert names.index("Stop on critical preflight failures") < names.index(WAIVER_TASK)
    assert names.index(WAIVER_TASK) < names.index("Mark checkpoint phase completion")
    assert names.index("Restore operational facts from checkpoint when preflight is skipped") < names.index(WAIVER_TASK)


@pytest.mark.parametrize("role", ["preflight", "activation", "post_activation", "finalization"])
def test_role_defaults_document_the_disabled_operator_inputs(role):
    operation = _load(ROLES_DIR / role / "defaults" / "main.yml")["acm_switchover_operation"]

    assert operation["skip_managed_cluster_expectations"] is False
    assert operation["managed_cluster_expectation_waiver"] == EMPTY_WAIVER
    assert operation["migration_cleanup_repair"] == EMPTY_REPAIR


def test_example_group_vars_leave_the_managed_cluster_minimum_unset():
    """restore_only.yml reuses the example: an explicit 0 there is the empty-target opt-in and
    would switch off restore-only's default floor of one restored non-local ManagedCluster."""
    operation = _load(COLLECTION_DIR / "examples" / "group_vars" / "all.yml")["acm_switchover_operation"]

    assert operation.get("min_managed_clusters") is None


def test_example_group_vars_document_the_disabled_operator_inputs():
    operation = _load(COLLECTION_DIR / "examples" / "group_vars" / "all.yml")["acm_switchover_operation"]

    assert operation["skip_managed_cluster_expectations"] is False
    assert operation["managed_cluster_expectation_waiver"] == EMPTY_WAIVER
    assert operation["migration_cleanup_repair"] == EMPTY_REPAIR
