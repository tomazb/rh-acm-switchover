"""Shipped activation transaction tasks against a fake destination hub (R4-04 PR C Task 8).

The activation role's discovery and Restore-mutation task files run in execute mode, dry-run
and check mode with a real checkpoint file. The fake hub records the migration journal the
checkpoint held at each Restore mutation, so "durable before mutation" is checked at the
instant of each request.
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
from ansible_collections.tomazb.acm_switchover.tests.integration.migration_fake_api import (
    CRED_B,
    GEN_B,
    MC_B,
    NS,
    PASSIVE,
    RES_B,
    FakeMigrationHub,
    passive_restore,
    velero_child,
)

KIND_OPERATION = {
    "passive_patch": {"method": "passive", "activation_method": "patch"},
    "passive_restore": {"method": "passive", "activation_method": "restore"},
    "full_restore": {"method": "full", "activation_method": "patch"},
}
TASKS = ("discover_resources", "verify_passive_sync", "activate_restore")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _identity(operation: dict) -> dict:
    return {
        "primary_context": "primary-hub",
        "secondary_context": "secondary-hub",
        "primary_cluster_uid": "uid-primary",
        "secondary_cluster_uid": "uid-secondary",
        "method": operation["method"],
        "activation_method": operation["activation_method"],
        "restore_only": False,
        "old_hub_action": "secondary",
        "collection_version": "",
    }


def complete_on_mutation(hub: FakeMigrationHub, kind: str, name: str) -> None:
    """The controller's visible result of an accepted Restore mutation: a published, Completed child."""
    if kind == "delete" or name not in hub.acm:
        return
    restore = hub.acm[name]
    child = f"{name}-{MC_B}"
    restore["status"] = dict(
        restore.get("status") or {},
        phase="Enabled" if kind == "patch" else "Finished",
        veleroManagedClustersRestoreName=child,
    )
    hub.velero[child] = velero_child(child, MC_B, name, restore["metadata"]["uid"])


class Run:
    def __init__(self, completed, hub, checkpoint_before, checkpoint_after):
        self.completed = completed
        self.hub = hub
        self.checkpoint_before = checkpoint_before
        self.checkpoint_after = checkpoint_after

    @property
    def output(self) -> str:
        return self.completed.stdout + self.completed.stderr

    @property
    def journal(self):
        return (json.loads(self.checkpoint_after).get("operational_data") or {}).get("migration_backups")


@pytest.fixture
def run_activation(tmp_path):
    def _run(kind, hub, *, mode="execute", check=False, journal=None, tasks=TASKS, extra=None):
        operation = KIND_OPERATION[kind]
        checkpoint_path = tmp_path / "checkpoint.json"
        if not checkpoint_path.exists():
            data = {"migration_backups": journal} if journal is not None else {}
            checkpoint_path.write_text(
                json.dumps(
                    {
                        "schema_version": "2.0",
                        "phase": "primary_prep",
                        "phase_status": "pass",
                        "completed_phases": ["preflight", "primary_prep"],
                        "operational_data": data,
                        "operation_identity": _identity(operation),
                        "errors": [],
                        "report_refs": [],
                    }
                ),
                encoding="utf-8",
            )

        def probe():
            stored = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            return (stored.get("operational_data") or {}).get("migration_backups")

        hub.journal_probe = probe
        kubeconfig = tmp_path / "secondary.kubeconfig"
        write_kubeconfig(kubeconfig, context="secondary-hub", server=hub.url, token="fixture-token")
        variables = {
            "acm_switchover_hubs": {
                "primary": {"context": "primary-hub", "kubeconfig": ""},
                "secondary": {"context": "secondary-hub", "kubeconfig": str(kubeconfig)},
            },
            "acm_switchover_operation": dict(operation, restore_only=False, old_hub_action="secondary"),
            "acm_switchover_execution": {
                "mode": mode,
                "report_dir": str(tmp_path / "artifacts"),
                "checkpoint": {"enabled": True, "backend": "file", "path": str(checkpoint_path)},
            },
            "_acm_restore_poll_delays": [0, 0, 0],
            **(extra or {}),
        }
        include = [
            {
                "name": f"Run activation {task}",
                "ansible.builtin.include_role": {"name": "tomazb.acm_switchover.activation", "tasks_from": task},
                **(
                    {"when": ["acm_switchover_operation.method == 'passive'", "_acm_journal is none"]}
                    if task == "verify_passive_sync"
                    else {}
                ),
            }
            for task in tasks
        ]
        playbook = tmp_path / "activation.yml"
        playbook.write_text(
            yaml.safe_dump(
                [
                    {
                        "hosts": "localhost",
                        "connection": "local",
                        "gather_facts": False,
                        "tasks": [
                            {
                                "name": "Enter the activation checkpoint",
                                "tomazb.acm_switchover.checkpoint_phase": {
                                    "phase": "activation",
                                    "checkpoint": "{{ acm_switchover_execution.checkpoint }}",
                                    "status": "enter",
                                },
                                "register": "_checkpoint_enter",
                            },
                            *include,
                            {
                                "name": "Report the transaction",
                                "ansible.builtin.debug": {"msg": "ACTIVATION_TRANSACTION=ok"},
                            },
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
    fake = FakeMigrationHub(restores=[passive_restore()], controller=complete_on_mutation)
    yield fake
    fake.close()


def _mutations(run):
    return [(kind, name) for kind, name, _ in run.hub.mutations]


def test_passive_patch_journals_then_patches_once_under_the_guard(run_activation, hub):
    run = run_activation("passive_patch", hub)
    assert run.completed.returncode == 0, run.output
    assert "ACTIVATION_TRANSACTION=ok" in run.output
    [(kind, name, journal_at_patch)] = hub.mutations
    assert (kind, name) == ("patch", PASSIVE)
    # Durable before the mutation: the frozen journal with no accepted generation yet.
    assert journal_at_patch["restore"]["generation"] is None
    assert journal_at_patch["restore"]["uid"] == "uid-passive"
    assert set(journal_at_patch["backups"]) == {
        "managed_clusters",
        "activation_credentials",
        "activation_resources",
        "activation_resources_generic",
    }
    assert run.journal["restore"]["generation"] == 4
    assert run.journal["run_id"] == journal_at_patch["run_id"]
    assert hub.acm[PASSIVE]["spec"]["veleroManagedClustersBackupName"] == "latest"
    paths = [request["path"] for request in hub.requests]
    assert "/apis/cluster.open-cluster-management.io/v1beta1" in paths  # the status-subresource discovery
    assert [request["method"] for request in hub.writes] == ["PATCH"]


@pytest.mark.parametrize(
    "restore",
    [
        passive_restore(status_names={"veleroManagedClustersRestoreName": "old"}),
        passive_restore(mc="latest"),
        passive_restore(cleanupBeforeRestore="None"),
    ],
    ids=["mc-locator-set", "unowned-latest", "cleanup-mismatch"],
)
def test_a_fresh_refusal_writes_no_journal_and_no_restore(run_activation, restore):
    fake = FakeMigrationHub(restores=[restore])
    try:
        run = run_activation("passive_patch", fake)
    finally:
        fake.close()
    assert run.completed.returncode != 0
    assert "ACTIVATION_TRANSACTION=ok" not in run.output
    assert fake.writes == []
    assert run.journal is None


def test_passive_restore_journals_deletes_by_uid_then_creates_latest_skip_skip(run_activation, hub):
    run = run_activation("passive_restore", hub)
    assert run.completed.returncode == 0, run.output
    assert _mutations(run) == [("delete", PASSIVE), ("create", "restore-acm-activate")]
    assert all(journal is not None and journal["restore"]["uid"] is None for _, _, journal in hub.mutations)
    created = hub.acm["restore-acm-activate"]["spec"]
    assert created == {
        "veleroManagedClustersBackupName": "latest",
        "veleroCredentialsBackupName": "skip",
        "veleroResourcesBackupName": "skip",
        "cleanupBeforeRestore": "CleanupRestored",
    }
    assert (run.journal["restore"]["uid"], run.journal["restore"]["generation"]) == ("uid-restore-acm-activate", 1)


def test_full_restore_creates_the_concrete_journaled_backups(run_activation, hub):
    run = run_activation("full_restore", hub)
    assert run.completed.returncode == 0, run.output
    assert _mutations(run) == [("delete", PASSIVE), ("create", "restore-acm-full")]
    assert hub.acm["restore-acm-full"]["spec"] == {
        "veleroManagedClustersBackupName": MC_B,
        "veleroCredentialsBackupName": CRED_B,
        "veleroResourcesBackupName": RES_B,
        "cleanupBeforeRestore": "CleanupRestored",
    }
    assert run.journal["backups"]["resources_generic"]["name"] == GEN_B


@pytest.mark.parametrize("kind", ["passive_restore", "full_restore"])
def test_a_failed_create_is_never_rolled_back_and_the_retry_creates_from_the_journal(run_activation, hub, kind):
    hub.failures[("POST", f"/apis/cluster.open-cluster-management.io/v1beta1/namespaces/{NS}/restores")] = 500
    run = run_activation(kind, hub)
    assert run.completed.returncode != 0
    assert "No automatic rollback is attempted" in run.output
    assert f"The passive sync Restore {PASSIVE} was deleted" in run.output
    # Exactly the DELETE and the one CREATE: no recreate of the passive sync Restore.
    assert [request["method"] for request in hub.writes] == ["DELETE", "POST"]
    assert PASSIVE not in hub.acm
    frozen = run.journal
    assert frozen["restore"]["uid"] is None

    hub.failures.clear()
    retry = run_activation(kind, hub)
    assert retry.completed.returncode == 0, retry.output
    assert [request["method"] for request in hub.writes] == ["DELETE", "POST", "POST"]
    assert retry.journal["run_id"] == frozen["run_id"] and retry.journal["backups"] == frozen["backups"]


@pytest.mark.parametrize("kind", ["passive_patch", "passive_restore", "full_restore"])
@pytest.mark.parametrize("preview", ["dry_run", "check"])
def test_a_preview_reads_and_predicts_but_mutates_and_journals_nothing(run_activation, hub, kind, preview):
    run = run_activation(kind, hub, mode="dry_run" if preview == "dry_run" else "execute", check=preview == "check")
    assert run.completed.returncode == 0, run.output
    assert "Would journal the" in run.output
    assert hub.writes == [] and hub.mutations == []
    assert run.checkpoint_after == run.checkpoint_before


def test_a_resumed_accepted_patch_is_only_reconciled(run_activation, hub):
    first = run_activation("passive_patch", hub)
    assert first.completed.returncode == 0, first.output
    second = run_activation("passive_patch", hub)
    assert second.completed.returncode == 0, second.output
    assert [request["method"] for request in hub.writes] == ["PATCH"]
    assert second.journal == first.journal


def test_an_invalid_recorded_journal_blocks_before_any_read_of_restores(run_activation, hub):
    run = run_activation("passive_patch", hub, journal={"schema_version": 9})
    assert run.completed.returncode != 0
    assert "The recorded migration journal is invalid and blocks activation" in run.output
    assert hub.writes == []


def test_a_missing_status_subresource_blocks_every_mutation(run_activation, hub):
    hub.status_served = False
    run = run_activation("passive_patch", hub)
    assert run.completed.returncode != 0
    assert "does not serve the status subresource" in run.output
    assert hub.writes == []
    # The freeze is durable; the mutation is not attempted.
    assert run.journal is not None and run.journal["restore"]["generation"] is None


def test_a_patch_conflict_records_no_generation_and_the_retry_repatches(run_activation, hub):
    original = copy.deepcopy(hub.acm[PASSIVE])
    hub.acm[PASSIVE]["metadata"]["resourceVersion"] = "1001"
    hub.failures[("PATCH", f"/apis/cluster.open-cluster-management.io/v1beta1/namespaces/{NS}/restores/{PASSIVE}")] = (
        409
    )
    run = run_activation("passive_patch", hub)
    assert run.completed.returncode != 0
    assert "The guarded patch of restore-acm-passive-sync was not accepted (precondition_failed)" in run.output
    assert run.journal["restore"]["generation"] is None
    hub.failures.clear()
    assert hub.acm[PASSIVE] == original
    retry = run_activation("passive_patch", hub)
    assert retry.completed.returncode == 0, retry.output
    assert retry.journal["restore"]["generation"] == 4
