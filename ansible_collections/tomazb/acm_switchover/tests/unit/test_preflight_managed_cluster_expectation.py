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
from yaml_contract_helpers import _when_text

from ansible_collections.tomazb.acm_switchover.plugins.modules import acm_migration_evidence as module

ROLE_TASKS = pathlib.Path(__file__).resolve().parents[2] / "roles" / "preflight" / "tasks"


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
    def setup_method(self):
        self.tasks = yaml.safe_load((ROLE_TASKS / "discover_resources.yml").read_text()) or []
        self.read = next(t for t in self.tasks if t.get("register") == "acm_primary_managed_clusters_info")

    def test_the_primary_inventory_is_one_strict_list(self):
        params = self.read["tomazb.acm_switchover.acm_k8s_read_outcome"]
        assert params["read_mode"] == "list"
        assert params["api_version"] == "cluster.open-cluster-management.io/v1"
        assert params["kind"] == "ManagedCluster" and params["resource_name"] == "managedclusters"
        assert "primary" in params["kubeconfig"] and "primary" in params["context"]
        assert not [t for t in self.tasks if (t.get("kubernetes.core.k8s_info") or {}).get("kind") == "ManagedCluster"]

    def test_an_executing_run_always_reads_the_live_primary(self):
        when = _when_text(self.read)
        assert "== 'execute'" in when and "acm_primary_managed_clusters_info is not defined" in when
        assert "restore_only" in when


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
        assert "acm_primary_managed_clusters_info" in inputs["mc_read"]
        assert "restore_only" in _when_text(derive)

    def test_no_fail_open_filter_derives_names_from_the_raw_resources(self):
        assert "acm_primary_managed_clusters_info.resources | default([])" not in self.text
        assert "selectattr('metadata.name', 'defined')" not in self.text

    def test_a_refused_derivation_is_a_critical_preflight_result(self):
        assert "preflight-managed-cluster-inventory" in self.text
