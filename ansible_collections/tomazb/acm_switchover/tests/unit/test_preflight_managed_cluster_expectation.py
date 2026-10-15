# SPDX-License-Identifier: MIT
"""#340 E1: the collection derives the expected ManagedCluster set only from a strict primary read.

The primary ManagedCluster discovery is an acm_k8s_read_outcome LIST (an executing run always
reads the live hub; a pre-seeded read serves only dry_run/validate fixtures), and the
derivation is the acm_migration_evidence `expected_managed_clusters` decision, never a
`resources | default([])` filter that turns an unreadable or malformed inventory into zero.
"""

from __future__ import annotations

import json
import pathlib

import pytest
import yaml
from ansible.module_utils import basic
from ansible.module_utils.basic import AnsibleModule as RealAnsibleModule
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar
from yaml_contract_helpers import _when_text

from ansible_collections.tomazb.acm_switchover.plugins.modules import acm_migration_evidence as module

try:  # ansible-core 2.19+ templates only strings marked as trusted.
    from ansible.template import trust_as_template
except ImportError:  # pragma: no cover - ansible-core < 2.19

    def trust_as_template(value):
        return value


ROLE_TASKS = pathlib.Path(__file__).resolve().parents[2] / "roles" / "preflight" / "tasks"
STRICT_READ = pathlib.Path(__file__).resolve().parents[2] / "roles" / "activation" / "tasks" / "strict_read.yml"
WAIT_FOR_RESTORE = (
    pathlib.Path(__file__).resolve().parents[2] / "roles" / "activation" / "tasks" / "wait_for_restore.yml"
)


def _holds(conditions, variables) -> bool:
    """Evaluate a task's `when` list (or an assert's `that`) the way Ansible does: all must hold."""
    templar = Templar(loader=DataLoader(), variables=variables)
    conditions = conditions if isinstance(conditions, list) else [conditions]
    return all(templar.template(trust_as_template("{{ (%s) | bool }}" % c)) is True for c in conditions)


def _find(tasks, predicate):
    stack = list(tasks)
    while stack:
        task = stack.pop(0)
        if predicate(task):
            return task
        for key in ("block", "rescue", "always"):
            stack.extend(task.get(key) or [])
    raise AssertionError("task not found")


class _Exit(SystemExit):
    def __init__(self, results):
        super().__init__(0)
        self.results = results


def _run(monkeypatch, inputs):
    class Capturing(RealAnsibleModule):
        def exit_json(self, **kwargs):
            raise _Exit(kwargs)

        def fail_json(self, **kwargs):
            raise _Exit({**kwargs, "failed": True})

    monkeypatch.setattr(module, "AnsibleModule", Capturing)
    monkeypatch.setattr(
        basic,
        "_ANSIBLE_ARGS",
        json.dumps({"ANSIBLE_MODULE_ARGS": {"operation": "expected_managed_clusters", "inputs": inputs}}).encode(),
    )
    if hasattr(basic, "_ANSIBLE_PROFILE"):
        monkeypatch.setattr(basic, "_ANSIBLE_PROFILE", "legacy")
    with pytest.raises(_Exit) as exc:
        module.main()
    return exc.value.results


def _ok(*names):
    return {"read_status": "ok", "resources": [{"metadata": {"name": n}} for n in names], "resource_version": "1"}


def test_a_complete_inventory_publishes_the_derived_expectation(monkeypatch):
    result = _run(monkeypatch, {"mc_read": _ok("cluster-b", "local-cluster", "cluster-a")})
    assert result.get("failed") is not True and result["changed"] is False
    assert result["derivable"] is True and result["code"] is None
    assert result["names"] == ["cluster-a", "cluster-b"] and result["count"] == 2


@pytest.mark.parametrize(
    ("mc_read", "code"),
    [
        ({"read_status": "error", "resources": [], "resource_version": None}, "inventory_unreadable"),
        ({"read_status": "kind_not_served", "resources": [], "resource_version": None}, "inventory_unreadable"),
        ({"read_status": "ok", "resources": [{"metadata": {}}], "resource_version": "1"}, "malformed_inventory"),
        (_ok("cluster-a", "cluster-a"), "duplicate_managed_cluster"),
    ],
)
def test_an_unprovable_inventory_is_a_refused_decision_not_zero_names(monkeypatch, mc_read, code):
    """The refusal is a decision the role records as a critical preflight result; it never derives zero."""
    result = _run(monkeypatch, {"mc_read": mc_read})
    assert result.get("failed") is not True
    assert result["derivable"] is False and result["code"] == code
    assert result["names"] is None and result["count"] is None
    assert code in result["message"]


class TestPrimaryManagedClusterDiscovery:
    """The primary read goes through the shared strict-read seam with the primary hub."""

    def setup_method(self):
        self.tasks = yaml.safe_load((ROLE_TASKS / "discover_resources.yml").read_text()) or []
        self.read = next(
            t
            for t in self.tasks
            if ((t.get("vars") or {}).get("_acm_read") or {}).get("key") == "primary_managedclusters"
        )
        self.seam = yaml.safe_load(STRICT_READ.read_text()) or []

    def test_the_primary_inventory_is_one_strict_list_through_the_shared_seam(self):
        assert self.read["ansible.builtin.include_tasks"].endswith("/activation/tasks/strict_read.yml")
        spec = self.read["vars"]["_acm_read"]
        assert spec == {
            "key": "primary_managedclusters",
            "hub": "primary",
            "read_mode": "list",
            "api_version": "cluster.open-cluster-management.io/v1",
            "kind": "ManagedCluster",
            "resource_name": "managedclusters",
        }
        assert self.read["vars"]["_acm_read_fact"] == "_acm_primary_managed_clusters_derivation_read"
        assert "restore_only" in _when_text(self.read)
        assert not [t for t in self.tasks if (t.get("kubernetes.core.k8s_info") or {}).get("kind") == "ManagedCluster"]

    @pytest.mark.parametrize(("hub", "expected"), [("primary", "/kc/primary"), (None, "/kc/secondary")])
    def test_the_seam_reads_the_named_hub_and_defaults_to_the_destination(self, hub, expected):
        (read,) = [t for t in self.seam if "tomazb.acm_switchover.acm_k8s_read_outcome" in t]
        assert read.get("no_log") is True
        spec = {"key": "k"} if hub is None else {"key": "k", "hub": hub}
        variables = {
            "_acm_read": spec,
            "acm_switchover_hubs": {
                "primary": {"kubeconfig": "/kc/primary", "context": "ctx-primary"},
                "secondary": {"kubeconfig": "/kc/secondary", "context": "ctx-secondary"},
            },
        }
        templar = Templar(loader=DataLoader(), variables=variables)
        params = read["tomazb.acm_switchover.acm_k8s_read_outcome"]
        assert templar.template(trust_as_template(params["kubeconfig"])) == expected
        assert templar.template(trust_as_template(params["context"])) == expected.replace("/kc/", "ctx-")

    @pytest.mark.parametrize(
        ("mode", "seeded", "overridden"),
        [
            ("execute", True, False),
            ("execute", False, False),
            ("dry_run", True, True),
            ("validate", True, True),
            ("dry_run", False, False),
        ],
    )
    def test_only_a_non_live_run_honours_a_pre_seeded_primary_read(self, mode, seeded, overridden):
        """An executing run always reads the live primary; a fixture stands in only in dry_run/validate."""
        resolve = self.seam[0]["ansible.builtin.set_fact"]["_acm_read_overridden"]
        overrides = {"migration_reads": {"primary_managedclusters": {"read_status": "ok"}}} if seeded else {}
        variables = {
            "_acm_read": {"key": "primary_managedclusters"},
            "acm_switchover_execution": {"mode": mode},
            "acm_switchover_test_overrides": overrides,
        }
        templar = Templar(loader=DataLoader(), variables=variables)
        assert (str(templar.template(trust_as_template(resolve))).strip() == "True") is overridden

    def test_the_other_preflight_checks_see_the_strict_read(self):
        publish = next(
            t for t in self.tasks if "acm_primary_managed_clusters_info" in (t.get("ansible.builtin.set_fact") or {})
        )
        assert self.tasks.index(publish) > self.tasks.index(self.read)
        value = publish["ansible.builtin.set_fact"]["acm_primary_managed_clusters_info"]
        assert value == "{{ _acm_primary_managed_clusters_derivation_read }}"


class TestDerivation:
    def setup_method(self):
        self.text = (ROLE_TASKS / "post_identity.yml").read_text()
        self.tasks = yaml.safe_load(self.text) or []

    def _all_tasks(self):
        stack = list(self.tasks)
        while stack:
            task = stack.pop()
            yield task
            for key in ("block", "rescue", "always"):
                stack.extend(task.get(key) or [])

    def test_the_derivation_is_the_strict_decision(self):
        (derive,) = [
            t
            for t in self._all_tasks()
            if (t.get("tomazb.acm_switchover.acm_migration_evidence") or {}).get("operation")
            == "expected_managed_clusters"
        ]
        inputs = derive["tomazb.acm_switchover.acm_migration_evidence"]["inputs"]
        assert "_acm_primary_managed_clusters_derivation_read" in inputs["mc_read"]
        assert "restore_only" in _when_text(derive)

    def test_no_fail_open_filter_derives_names_from_the_raw_resources(self):
        assert "acm_primary_managed_clusters_info.resources | default([])" not in self.text
        assert "selectattr('metadata.name', 'defined')" not in self.text

    def test_a_refused_derivation_is_a_critical_preflight_result(self):
        assert "preflight-managed-cluster-inventory" in self.text


@pytest.mark.parametrize(
    ("minimum", "names", "enforce", "polls"),
    [
        (0, [], False, False),
        (0, ["cluster-a"], True, True),
        # #340 E2: a waived name check (enforce false) still waits for a non-empty inventory.
        (0, ["cluster-a"], False, True),
        (1, [], False, True),
        (2, ["cluster-a"], False, True),
    ],
)
def test_the_full_restore_wait_gate_truth_table(minimum, names, enforce, polls):
    tasks = yaml.safe_load(WAIT_FOR_RESTORE.read_text()) or []
    gate = _find(tasks, lambda t: t.get("ansible.builtin.include_tasks") == "poll_managed_clusters.yml")
    variables = {
        "acm_switchover_operation": {"method": "full"},
        "_acm_mc_check": {"min_managed_clusters": minimum, "expected_names": names, "enforce": enforce},
        "_acm_mc_state": "pending",
    }
    assert _holds(gate["when"], variables) is polls
