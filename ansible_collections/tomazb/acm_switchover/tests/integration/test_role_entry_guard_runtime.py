"""Direct role entry guards (docs/variable-reference.md) at runtime.

Including activation, post_activation or finalization without the playbook's preflight
still proves the operator inputs, the waiver's expected names and, for an executing
activation, the checkpoint that carries the migration journal, before any cluster
request is made.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.tests.conftest import _ansible_env
from ansible_collections.tomazb.acm_switchover.tests.integration.argocd_fake_api import write_kubeconfig
from ansible_collections.tomazb.acm_switchover.tests.integration.r3_02_fake_api import FakeR302API

WAIVER = {"actor": "operator@example.com", "reason": "stale inventory", "request_id": None}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _identity(restore_only: bool) -> dict:
    return {
        "primary_context": "" if restore_only else "primary-hub",
        "secondary_context": "secondary-hub",
        "primary_cluster_uid": "" if restore_only else "uid-primary",
        "secondary_cluster_uid": "uid-secondary",
        "method": "full" if restore_only else "passive",
        "activation_method": "patch",
        "restore_only": restore_only,
        "old_hub_action": "none" if restore_only else "secondary",
        "collection_version": "",
    }


def _run_role(tmp_path, role, *, restore_only=False, waiver_scope=None, recorded_names=(), checkpoint=True):
    api = FakeR302API()
    try:
        kubeconfig = tmp_path / "secondary.kubeconfig"
        write_kubeconfig(kubeconfig, context="secondary-hub", server=api.url, token="fixture-token")
        primary_kubeconfig = tmp_path / "primary.kubeconfig"
        write_kubeconfig(primary_kubeconfig, context="primary-hub", server=api.url, token="fixture-token")
        checkpoint_path = tmp_path / "checkpoint.json"
        phases = ["preflight"] if restore_only else ["preflight", "primary_prep"]
        phases += {
            "activation": [],
            "post_activation": ["activation"],
            "finalization": ["activation", "post_activation"],
        }[role]
        checkpoint_path.write_text(
            json.dumps(
                {
                    "schema_version": "2.0",
                    "phase": phases[-1],
                    "phase_status": "pass",
                    "completed_phases": phases,
                    "operational_data": (
                        {}
                        if recorded_names is None
                        else {
                            "expected_managed_cluster_names": list(recorded_names),
                            "expected_managed_cluster_count": len(recorded_names),
                        }
                    ),
                    "operation_identity": _identity(restore_only),
                    "errors": [],
                    "report_refs": [],
                }
            ),
            encoding="utf-8",
        )
        operation = {
            "restore_only": restore_only,
            "method": "full" if restore_only else "passive",
            "old_hub_action": "none" if restore_only else "secondary",
            "activation_method": "patch",
        }
        if waiver_scope is not None:
            operation["skip_managed_cluster_expectations"] = True
            operation["managed_cluster_expectation_waiver"] = dict(WAIVER, scope=waiver_scope)
        variables = {
            "acm_switchover_hubs": {
                "primary": {
                    "context": "" if restore_only else "primary-hub",
                    "kubeconfig": "" if restore_only else str(primary_kubeconfig),
                },
                "secondary": {"context": "secondary-hub", "kubeconfig": str(kubeconfig)},
            },
            "acm_switchover_operation": operation,
            "acm_switchover_execution": {
                "mode": "execute",
                "report_dir": str(tmp_path / "artifacts"),
                "checkpoint": {"enabled": checkpoint, "backend": "file", "path": str(checkpoint_path)},
            },
        }
        playbook = tmp_path / "direct-role.yml"
        playbook.write_text(
            yaml.safe_dump(
                [
                    {
                        "hosts": "localhost",
                        "connection": "local",
                        "gather_facts": False,
                        "vars": variables,
                        "tasks": [
                            {
                                "name": "Enter the role directly",
                                "ansible.builtin.include_role": {"name": f"tomazb.acm_switchover.{role}"},
                            }
                        ],
                    }
                ],
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        completed = subprocess.run(
            ["ansible-playbook", str(playbook), "-i", "localhost,"],
            cwd=_repo_root(),
            capture_output=True,
            text=True,
            check=False,
            env=_ansible_env(_repo_root(), tmp_path),
            timeout=300,
        )
        return completed, api.requests
    finally:
        api.close()


def _output(completed) -> str:
    return completed.stdout + completed.stderr


@pytest.mark.parametrize(
    "role, kwargs, message",
    [
        (
            "activation",
            {"restore_only": True, "waiver_scope": "activation"},
            "waiver_expected_names_empty: restore_only records no expected ManagedCluster name",
        ),
        ("activation", {"waiver_scope": "both"}, "waiver_expected_names_empty: skip_managed_cluster_expectations"),
        ("activation", {"checkpoint": False}, "Activation records its migration evidence in the checkpoint"),
        (
            "post_activation",
            {"waiver_scope": "post_activation"},
            "waiver_expected_names_empty: skip_managed_cluster_expectations",
        ),
        (
            "finalization",
            {"waiver_scope": "activation", "recorded_names": []},
            "waiver_expected_names_empty: skip_managed_cluster_expectations",
        ),
    ],
    ids=[
        "restore-only-activation-waiver",
        "waiver-without-names",
        "checkpoint-disabled",
        "post-activation",
        "finalization",
    ],
)
def test_a_direct_role_entry_refuses_before_any_cluster_request(tmp_path, role, kwargs, message):
    completed, requests = _run_role(tmp_path, role, **kwargs)
    output = _output(completed)
    assert completed.returncode != 0, output
    assert message in output
    assert requests == []


@pytest.mark.parametrize(
    "kwargs",
    [{"restore_only": True}, {"restore_only": True, "waiver_scope": "post_activation", "recorded_names": ["a"]}],
    ids=["restore-only-without-waiver", "names-to-waive"],
)
def test_a_valid_direct_activation_entry_passes_its_guard(tmp_path, kwargs):
    completed, requests = _run_role(tmp_path, "activation", **kwargs)
    output = _output(completed)
    assert "Refuse invalid operator inputs before any role work] ***\nskipping" in output
    assert "waiver_expected_names_empty" not in output
    assert "Activation records its migration evidence" not in output
    # Past the guard, activation reaches its first read of the destination hub.
    assert "Discover secondary hub resources" in output
    assert requests


@pytest.mark.parametrize("role", ["activation", "post_activation"])
def test_a_direct_entry_without_a_recorded_expectation_is_refused(tmp_path, role):
    """Review finding 3: nothing recorded and no explicit minimum, so the check is never skipped."""
    completed, requests = _run_role(tmp_path, role, recorded_names=None)
    output = _output(completed)
    assert completed.returncode != 0, output
    assert "No managed-cluster expectation is recorded in the checkpoint" in output
    assert requests == []
