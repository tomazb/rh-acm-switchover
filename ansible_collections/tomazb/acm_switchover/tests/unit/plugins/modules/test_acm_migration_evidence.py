# SPDX-License-Identifier: MIT
"""Unit tests for acm_migration_evidence, the thin adapter over module_utils/migration_activation.

The transaction decisions themselves are held equal to the Python CLI by
tests/test_activation_transaction_parity.py at the repository root.
"""

from __future__ import annotations

import json
import uuid

import pytest
import yaml
from ansible.module_utils import basic
from ansible.module_utils.basic import AnsibleModule as RealAnsibleModule

from ansible_collections.tomazb.acm_switchover.plugins.modules import acm_migration_evidence as module


class _Exit(SystemExit):
    def __init__(self, results):
        super().__init__(0)
        self.results = results


def _run(monkeypatch, operation, inputs, *, check_mode=False):
    class Capturing(RealAnsibleModule):
        def exit_json(self, **kwargs):
            raise _Exit(kwargs)

        def fail_json(self, **kwargs):
            raise _Exit({**kwargs, "failed": True})

    args = {"operation": operation, "inputs": inputs}
    if check_mode:
        args["_ansible_check_mode"] = True
    monkeypatch.setattr(module, "AnsibleModule", Capturing)
    monkeypatch.setattr(basic, "_ANSIBLE_ARGS", json.dumps({"ANSIBLE_MODULE_ARGS": args}).encode("utf-8"))
    if hasattr(basic, "_ANSIBLE_PROFILE"):
        monkeypatch.setattr(basic, "_ANSIBLE_PROFILE", "legacy")
    with pytest.raises(_Exit) as exited:
        module.main()
    return exited.value.results


def _mch(version):
    return {"read_status": "ok", "resources": [{"metadata": {"name": "hub"}, "status": {"currentVersion": version}}]}


def test_a_decision_is_returned_unchanged_and_never_reports_a_change(monkeypatch):
    result = _run(monkeypatch, "acm_minor", {"mch_read": _mch("2.14.3")})
    assert result == {"changed": False, "acm_minor": "2.14", "controller_contract": "legacy_2_12_2_16"}


@pytest.mark.parametrize("check_mode", [False, True])
def test_a_refusal_fails_with_the_transaction_message(monkeypatch, check_mode):
    result = _run(monkeypatch, "acm_minor", {"mch_read": _mch("2.18.0")}, check_mode=check_mode)
    assert result["failed"] is True and result["changed"] is False
    assert result["msg"].startswith("Migration evidence refused (unknown_acm_minor:")


def test_an_unverified_read_is_never_an_answer(monkeypatch):
    result = _run(monkeypatch, "acm_minor", {"mch_read": {"read_status": "error", "resources": []}})
    assert result == {"failed": True, "changed": False, "msg": "MultiClusterHub read failed (error)"}


def test_incomplete_inputs_fail_closed(monkeypatch):
    result = _run(monkeypatch, "pre_patch", {"journal": {}})
    assert result["failed"] is True
    assert result["msg"] == "The pre_patch step received incomplete inputs."


def test_the_freeze_mints_a_transaction_identity_unless_one_is_supplied(monkeypatch):
    calls: list = []

    def freeze(**inputs):
        calls.append(inputs)
        return {"candidate": {}}

    monkeypatch.setattr(module.transaction, "freeze", freeze)
    _run(monkeypatch, "freeze", {"kind": "passive_patch"})
    _run(monkeypatch, "freeze", {"kind": "passive_patch", "run_id": "fixed", "resolved_at": "then"})
    assert uuid.UUID(calls[0]["run_id"]).version == 4 and calls[0]["resolved_at"]
    assert (calls[1]["run_id"], calls[1]["resolved_at"]) == ("fixed", "then")


def test_documentation_matches_the_argument_spec_and_operations():
    documentation = yaml.safe_load(module.DOCUMENTATION)
    assert documentation["options"]["operation"]["choices"] == sorted(module._OPERATIONS)
    assert set(documentation["options"]) == {"operation", "inputs"}
    yaml.safe_load(module.EXAMPLES)
    yaml.safe_load(module.RETURN)


def _mc_read(*names):
    return {"read_status": "ok", "resources": [{"metadata": {"name": name}} for name in (*names, "local-cluster")]}


@pytest.mark.parametrize("minimum, enforce", [(2, True), ("2", "True")], ids=["native", "stringified"])
def test_managed_cluster_inputs_survive_the_non_native_templating_floor(monkeypatch, minimum, enforce):
    """ansible-core 2.16 renders scalars nested in module inputs as text; the decision is the same."""
    inputs = {"expected_names": ["a"], "min_managed_clusters": minimum, "enforce": enforce}
    assert _run(monkeypatch, "managed_clusters_poll", {"mc_read": _mc_read("a"), **inputs})["state"] == "pending"
    refused = _run(monkeypatch, "verify_managed_clusters", {"mc_read": _mc_read("a"), **inputs})
    assert refused["failed"] is True and "Expected 2 ManagedCluster(s)" in refused["msg"]


@pytest.mark.parametrize("minimum, enforce", [("-1", True), ("two", True), (1, "yes"), (True, True)])
def test_malformed_managed_cluster_inputs_are_refused(monkeypatch, minimum, enforce):
    inputs = {"expected_names": [], "min_managed_clusters": minimum, "enforce": enforce}
    assert _run(monkeypatch, "verify_managed_clusters", {"mc_read": _mc_read("a"), **inputs})["failed"] is True
