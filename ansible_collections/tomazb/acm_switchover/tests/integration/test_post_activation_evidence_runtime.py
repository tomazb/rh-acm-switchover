"""Shipped post-activation evidence tasks against a fake destination hub (R4-04 PR C Task 9).

The post_activation role runs in execute mode and check mode with a real checkpoint file.
The ManagedCluster inventory is read strictly; the name evidence and post_activation.completed_at
are journaled through checkpoint_phase status: update, completed_at strictly last.
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.tests.conftest import _ansible_env
from ansible_collections.tomazb.acm_switchover.tests.integration.argocd_fake_api import write_kubeconfig
from ansible_collections.tomazb.acm_switchover.tests.integration.migration_fake_api import FakeMigrationHub

_MC_LIST = "/apis/cluster.open-cluster-management.io/v1/managedclusters"
_REQUEST = {"scope": "post_activation", "actor": "operator@example.com", "reason": "stale list", "request_id": None}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _vector(case_id: str) -> dict:
    path = _repo_root() / "tests" / "fixtures" / "r4_04_migration_evidence_vectors.json"
    return next(case for case in json.loads(path.read_text(encoding="utf-8"))["cases"] if case["id"] == case_id)


COMPLETED = _vector("post-activation-journal-completed")["input"]["journal"]
RECORDED = _vector("completion-kept-on-resume")["input"]["journal"]


class Run:
    def __init__(self, completed, hub, before, after):
        self.completed = completed
        self.hub = hub
        self.before = before
        self.after = after

    @property
    def output(self) -> str:
        return self.completed.stdout + self.completed.stderr

    @property
    def checkpoint(self) -> dict:
        return json.loads(self.after)

    @property
    def journal(self):
        return (self.checkpoint.get("operational_data") or {}).get("migration_backups")

    @property
    def mc_reads(self) -> int:
        return sum(1 for request in self.hub.requests if request["path"] == _MC_LIST)


@pytest.fixture
def run_post_activation(tmp_path):
    def _run(hub, *, journal=COMPLETED, names=("cluster-a",), check=False, waiver=None, extra=None):
        checkpoint_path = tmp_path / "checkpoint.json"
        data = {"expected_managed_cluster_names": list(names), "expected_managed_cluster_count": len(names)}
        if journal is not None:
            data["migration_backups"] = copy.deepcopy(journal)
        checkpoint_path.write_text(
            json.dumps(
                {
                    "schema_version": "2.0",
                    "phase": "activation",
                    "phase_status": "pass",
                    "completed_phases": ["preflight", "primary_prep", "activation"],
                    "operational_data": data,
                    "operation_identity": {
                        "primary_context": "primary-hub",
                        "secondary_context": "secondary-hub",
                        "primary_cluster_uid": "uid-primary",
                        "secondary_cluster_uid": "uid-secondary",
                        "method": "passive",
                        "activation_method": "patch",
                        "restore_only": False,
                        "old_hub_action": "secondary",
                        "collection_version": "",
                    },
                    "errors": [],
                    "report_refs": [],
                }
            ),
            encoding="utf-8",
        )
        kubeconfig = tmp_path / "secondary.kubeconfig"
        write_kubeconfig(kubeconfig, context="secondary-hub", server=hub.url, token="fixture-token")
        primary_kubeconfig = tmp_path / "primary.kubeconfig"
        write_kubeconfig(primary_kubeconfig, context="primary-hub", server=hub.url, token="fixture-token")
        operation = {"restore_only": False, "method": "passive", "activation_method": "patch"}
        operation["old_hub_action"] = "secondary"
        if waiver is not None:
            operation.update(skip_managed_cluster_expectations=True, managed_cluster_expectation_waiver=waiver)
        variables = {
            "acm_switchover_hubs": {
                "primary": {"context": "primary-hub", "kubeconfig": str(primary_kubeconfig)},
                "secondary": {"context": "secondary-hub", "kubeconfig": str(kubeconfig)},
            },
            "acm_switchover_operation": operation,
            "acm_switchover_execution": {
                "mode": "execute",
                "report_dir": str(tmp_path / "artifacts"),
                "checkpoint": {"enabled": True, "backend": "file", "path": str(checkpoint_path)},
            },
            "_acm_mc_reconcile_pause_seconds": 0,
            "_acm_mc_readiness_poll_delays": [0],
            **(extra or {}),
        }
        playbook = tmp_path / "post_activation.yml"
        playbook.write_text(
            yaml.safe_dump(
                [
                    {
                        "hosts": "localhost",
                        "connection": "local",
                        "gather_facts": False,
                        "tasks": [
                            {
                                "name": "Run post_activation",
                                "ansible.builtin.include_role": {"name": "tomazb.acm_switchover.post_activation"},
                            },
                            {"name": "Report the phase", "ansible.builtin.debug": {"msg": "POST_ACTIVATION=ok"}},
                        ],
                    }
                ],
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        vars_file = tmp_path / "vars.yml"
        vars_file.write_text(yaml.safe_dump(variables, sort_keys=False), encoding="utf-8")
        before = checkpoint_path.read_bytes()
        command = ["ansible-playbook", str(playbook), "-i", "localhost,", "-e", f"@{vars_file}"]
        if check:
            command.append("--check")
        completed = subprocess.run(
            command,
            cwd=_repo_root(),
            capture_output=True,
            text=True,
            check=False,
            env=_ansible_env(_repo_root(), tmp_path),
            timeout=300,
        )
        return Run(completed, hub, before, checkpoint_path.read_bytes())

    return _run


@pytest.fixture
def hub():
    fake = FakeMigrationHub(managed_clusters=["cluster-a"])
    yield fake
    fake.close()


def test_names_evidence_then_completion_are_journaled(run_post_activation, hub):
    run = run_post_activation(hub)
    assert run.completed.returncode == 0, run.output
    assert "POST_ACTIVATION=ok" in run.output
    post = run.journal["post_activation"]
    assert post["names_verified_at"] and post["completed_at"]
    assert run.journal["waiver"] is None
    assert {key: run.journal[key] for key in ("restore", "backups", "run_id")} == {
        key: COMPLETED[key] for key in ("restore", "backups", "run_id")
    }
    assert run.checkpoint["completed_phases"][-1] == "post_activation"
    assert hub.writes == []


def test_a_failed_strict_inventory_fails_the_phase_and_records_nothing(run_post_activation, hub):
    hub.failures[("GET", _MC_LIST)] = 403
    run = run_post_activation(hub)
    assert run.completed.returncode != 0
    assert "ManagedCluster inventory read failed" in run.output
    assert run.journal == COMPLETED
    # The phase rescue recorded the failure: the retry stays at post_activation.
    assert (run.checkpoint["phase"], run.checkpoint["phase_status"]) == ("post_activation", "fail")


@pytest.mark.parametrize(
    "names, unavailable",
    [(("cluster-a", "cluster-b"), ()), (("cluster-a",), ("cluster-a",))],
    ids=["missing-expected-name", "not-available"],
)
def test_an_unready_inventory_records_no_evidence(run_post_activation, hub, names, unavailable):
    hub.unavailable_clusters.update(unavailable)
    run = run_post_activation(hub, names=names)
    assert run.completed.returncode != 0
    assert "Post activation verification failed" in run.output
    assert run.journal == COMPLETED


def test_a_post_activation_waiver_substitutes_only_for_the_name_check(run_post_activation, hub):
    hub.managed_clusters.append("cluster-c")  # the count floor of two still holds
    run = run_post_activation(hub, names=("cluster-a", "cluster-b"), waiver=_REQUEST)
    assert run.completed.returncode == 0, run.output
    journal = run.journal
    assert {key: journal["waiver"][key] for key in _REQUEST} == _REQUEST
    assert journal["waiver"]["flag"] == "acm_switchover_operation.skip_managed_cluster_expectations"
    assert journal["waiver"]["outcome"] == "waived"
    assert journal["post_activation"]["names_verified_at"] is None
    assert journal["post_activation"]["completed_at"]


def test_a_waiver_never_lifts_readiness(run_post_activation, hub):
    hub.managed_clusters.append("cluster-c")
    hub.unavailable_clusters.add("cluster-a")
    run = run_post_activation(hub, names=("cluster-a", "cluster-b"), waiver=_REQUEST)
    assert run.completed.returncode != 0
    assert "Post activation verification failed: cluster-a, managed-cluster-status-timeout" in run.output
    assert run.journal["post_activation"] == COMPLETED["post_activation"]


def test_an_executing_run_without_a_journal_refuses_before_any_inventory_read(run_post_activation, hub):
    run = run_post_activation(hub, journal=None)
    assert run.completed.returncode != 0
    assert "Migration evidence refused (journal_required:" in run.output
    assert run.mc_reads == 0
    assert run.journal is None


def test_resume_revalidates_and_keeps_the_recorded_evidence(run_post_activation, hub):
    run = run_post_activation(hub, journal=RECORDED)
    assert run.completed.returncode == 0, run.output
    assert run.journal == RECORDED
    assert run.mc_reads >= 1


def test_a_failed_revalidation_keeps_the_recorded_evidence(run_post_activation, hub):
    run = run_post_activation(hub, journal=RECORDED, names=("cluster-a", "cluster-b"))
    assert run.completed.returncode != 0
    assert run.journal == RECORDED


def test_check_mode_reads_but_journals_nothing(run_post_activation, hub):
    run = run_post_activation(hub, check=True)
    assert run.completed.returncode == 0, run.output
    assert run.after == run.before
    assert hub.writes == []


def test_a_later_cleanup_failure_leaves_completion_absent(run_post_activation, hub):
    # The fake hub refuses the annotation removal, so the stale annotation stays and cleanup fails.
    hub.annotated_clusters.add("cluster-a")
    run = run_post_activation(hub)
    assert run.completed.returncode != 0
    post = run.journal["post_activation"]
    assert post["names_verified_at"] and post["completed_at"] is None
    assert (run.checkpoint["phase"], run.checkpoint["phase_status"]) == ("post_activation", "fail")
