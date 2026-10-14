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
    complete,
    passive_restore,
)

KIND_OPERATION = {
    "passive_patch": {"method": "passive", "activation_method": "patch"},
    "passive_restore": {"method": "passive", "activation_method": "restore"},
    "full_restore": {"method": "full", "activation_method": "patch"},
}
TASKS = ("discover_resources", "verify_passive_sync", "activate_restore", "wait_for_restore")


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
    """The controller's visible result of an accepted Restore mutation: the lane's Completed cohort."""
    if kind == "delete" or name not in hub.acm:
        return
    mutation_kind = {"restore-acm-activate": "passive_restore", "restore-acm-full": "full_restore"}
    complete(hub, name, kind="passive_patch" if kind == "patch" else mutation_kind[name])


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
                    {"when": ["acm_switchover_operation.method == 'passive'", "not (_acm_has_journal | bool)"]}
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


@pytest.mark.parametrize("selectors", [[{"matchLabels": {"a": "b"}}], []], ids=["shortcut", "no-candidate"])
def test_a_credentials_backup_replaced_before_the_final_list_issues_zero_patch(run_activation, hub, selectors):
    """Codex pre-PR finding 1: the strict GET still sees the frozen UID, the final LIST a replacement."""
    patch_path = f"/apis/cluster.open-cluster-management.io/v1beta1/namespaces/{NS}/restores/{PASSIVE}"
    hub.failures[("PATCH", patch_path)] = 409
    frozen = run_activation("passive_patch", hub)
    assert frozen.completed.returncode != 0
    assert frozen.journal["restore"]["generation"] is None
    hub.failures.clear()
    replacement = dict(copy.deepcopy(hub.backups[CRED_B]), spec={"orLabelSelectors": selectors})
    replacement["metadata"]["uid"] = "uid-replacement"
    hub.backup_list_items = [raw for name, raw in hub.backups.items() if name != CRED_B] + [replacement]
    retry = run_activation("passive_patch", hub)
    assert retry.completed.returncode != 0
    assert "credential_variant_source_mismatch" in retry.output
    assert [request["method"] for request in hub.writes] == ["PATCH"]  # only the first, rejected attempt
    assert hub.mutations == []
    assert retry.journal == frozen.journal


# --- completion evidence (8c) -------------------------------------------------------------------


@pytest.mark.parametrize("version", ["2.14.3", "2.17.1"])
@pytest.mark.parametrize("kind", ["passive_patch", "passive_restore", "full_restore"])
def test_completion_evidence_is_recorded_with_completed_at(run_activation, kind, version):
    fake = FakeMigrationHub(version=version, restores=[passive_restore()], controller=complete_on_mutation)
    try:
        run = run_activation(kind, fake)
    finally:
        fake.close()
    assert run.completed.returncode == 0, run.output
    restore = run.journal["restore"]
    assert restore["completed_at"] and restore["spec_fingerprint"]
    assert restore["backup_names_verified_at"] and restore["names_verified_at"]
    assert restore["velero_restores"]["managed_clusters"][0]["backup_name"] == MC_B


def _failing_controller(**overrides):
    def controller(hub, kind, name):
        if kind != "delete" and name in hub.acm:
            mutation_kind = {"restore-acm-activate": "passive_restore", "restore-acm-full": "full_restore"}
            complete(hub, name, kind="passive_patch" if kind == "patch" else mutation_kind[name], **overrides)

    return controller


@pytest.mark.parametrize(
    "kind, overrides, message",
    [
        ("passive_patch", {"child_phase": "Failed"}, "completion evidence refused"),
        ("passive_restore", {"phase": "FinishedWithErrors"}, "reached phase 'FinishedWithErrors'"),
        ("full_restore", {"child_phase": "PartiallyFailed"}, "completion evidence refused"),
    ],
)
def test_a_failed_completion_records_no_evidence(run_activation, kind, overrides, message):
    fake = FakeMigrationHub(restores=[passive_restore()], controller=_failing_controller(**overrides))
    try:
        run = run_activation(kind, fake)
    finally:
        fake.close()
    assert run.completed.returncode != 0
    assert message in run.output
    restore = run.journal["restore"]
    assert restore["generation"] is not None
    assert (restore["completed_at"], restore["spec_fingerprint"], restore["names_verified_at"]) == (None, None, None)


def test_a_controller_that_never_publishes_times_out_with_no_evidence(run_activation):
    fake = FakeMigrationHub(restores=[passive_restore()])
    try:
        run = run_activation("passive_patch", fake)
    finally:
        fake.close()
    assert run.completed.returncode != 0
    assert "Timeout waiting for restore to complete after 0s" in run.output
    assert run.journal["restore"]["completed_at"] is None


def test_an_interrupted_bundle_is_reconciled_and_completed_last(run_activation, hub, tmp_path):
    first = run_activation("passive_patch", hub)
    assert first.completed.returncode == 0, first.output
    # The process stopped between the bundle write and the completed_at write.
    checkpoint_path = tmp_path / "checkpoint.json"
    stored = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    stored["operational_data"]["migration_backups"]["restore"]["completed_at"] = None
    checkpoint_path.write_text(json.dumps(stored), encoding="utf-8")

    second = run_activation("passive_patch", hub)
    assert second.completed.returncode == 0, second.output
    before, after = first.journal["restore"], second.journal["restore"]
    assert after["completed_at"]
    for field in ("velero_restores", "spec_fingerprint", "backup_names_verified_at", "names_verified_at"):
        assert after[field] == before[field], field
    assert [request["method"] for request in hub.writes] == ["PATCH"]


def test_completed_evidence_is_revalidated_on_resume_and_drift_blocks(run_activation, hub):
    first = run_activation("passive_patch", hub)
    assert first.completed.returncode == 0, first.output
    second = run_activation("passive_patch", hub)
    assert second.completed.returncode == 0, second.output
    assert second.journal == first.journal
    child = first.journal["restore"]["velero_restores"]["managed_clusters"][0]["name"]
    hub.velero[child]["metadata"]["uid"] = "uid-replacement"
    third = run_activation("passive_patch", hub)
    assert third.completed.returncode != 0
    assert third.journal == first.journal


def test_a_missing_expected_name_blocks_before_any_evidence(run_activation, hub):
    run = run_activation("passive_patch", hub, extra={"acm_switchover_expected_managed_cluster_names": ["cluster-x"]})
    assert run.completed.returncode != 0
    assert "Missing expected ManagedCluster(s) after restore: cluster-x" in run.output
    assert run.journal["restore"]["completed_at"] is None


def test_an_activation_waiver_is_journaled_and_never_verifies_names(run_activation, hub):
    waiver = {"scope": "activation", "actor": "operator@example.com", "reason": "stale inventory", "request_id": None}
    run = run_activation(
        "passive_patch",
        hub,
        extra={
            "acm_switchover_expected_managed_cluster_names": ["cluster-x"],
            "_acm_role_entry_inputs": {
                "results": [
                    {"id": "preflight-input-operation", "details": {"managed_cluster_expectation_waiver": waiver}}
                ]
            },
        },
    )
    assert run.completed.returncode == 0, run.output
    journaled = run.journal["waiver"]
    assert {key: journaled[key] for key in waiver} == waiver
    assert (journaled["outcome"], journaled["flag"]) == (
        "waived",
        "acm_switchover_operation.skip_managed_cluster_expectations",
    )
    assert run.journal["restore"]["names_verified_at"] is None
    assert run.journal["restore"]["completed_at"]


@pytest.mark.parametrize("kind", ["passive_patch", "full_restore"])
def test_the_whole_activation_role_runs_the_transaction_and_completes_the_phase(tmp_path, kind):
    """Direct role entry (docs/variable-reference.md): guard, transaction, completion and pass."""
    fake = FakeMigrationHub(version="2.13.4", restores=[passive_restore()], controller=complete_on_mutation)
    try:
        operation = KIND_OPERATION[kind]
        kubeconfig = tmp_path / "secondary.kubeconfig"
        write_kubeconfig(kubeconfig, context="secondary-hub", server=fake.url, token="fixture-token")
        primary = tmp_path / "primary.kubeconfig"
        write_kubeconfig(primary, context="primary-hub", server=fake.url, token="fixture-token")
        checkpoint_path = tmp_path / "checkpoint.json"
        checkpoint_path.write_text(
            json.dumps(
                {
                    "schema_version": "2.0",
                    "phase": "primary_prep",
                    "phase_status": "pass",
                    "completed_phases": ["preflight", "primary_prep"],
                    "operational_data": {
                        "expected_managed_cluster_names": ["cluster-a"],
                        "expected_managed_cluster_count": 1,
                    },
                    "operation_identity": _identity(operation),
                    "errors": [],
                    "report_refs": [],
                }
            ),
            encoding="utf-8",
        )
        variables = {
            "acm_switchover_hubs": {
                "primary": {"context": "primary-hub", "kubeconfig": str(primary)},
                "secondary": {"context": "secondary-hub", "kubeconfig": str(kubeconfig)},
            },
            "acm_switchover_operation": dict(operation, restore_only=False, old_hub_action="secondary"),
            "acm_switchover_execution": {
                "mode": "execute",
                "report_dir": str(tmp_path / "artifacts"),
                "checkpoint": {"enabled": True, "backend": "file", "path": str(checkpoint_path)},
            },
            "acm_switchover_expected_managed_cluster_names": ["cluster-a"],
            "acm_switchover_expected_managed_cluster_count": 1,
            "_acm_restore_poll_delays": [0, 0, 0],
        }
        playbook = tmp_path / "role.yml"
        playbook.write_text(
            yaml.safe_dump(
                [
                    {
                        "hosts": "localhost",
                        "connection": "local",
                        "gather_facts": False,
                        "tasks": [
                            {
                                "name": "Enter the activation role directly",
                                "ansible.builtin.include_role": {"name": "tomazb.acm_switchover.activation"},
                            }
                        ],
                    }
                ],
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        vars_file = tmp_path / "vars.yml"
        vars_file.write_text(yaml.safe_dump(variables, sort_keys=False), encoding="utf-8")
        completed = subprocess.run(
            ["ansible-playbook", str(playbook), "-i", "localhost,", "-e", f"@{vars_file}"],
            cwd=_repo_root(),
            capture_output=True,
            text=True,
            check=False,
            env=_ansible_env(_repo_root(), tmp_path),
            timeout=300,
        )
    finally:
        fake.close()
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    stored = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert stored["completed_phases"] == ["preflight", "primary_prep", "activation"]
    journal = stored["operational_data"]["migration_backups"]
    assert journal["restore"]["completed_at"] and journal["restore"]["names_verified_at"]
    assert journal["restore"]["mutation_kind"] == kind and journal["restore"]["acm_minor"] == "2.13"


def _run_whole_role(
    tmp_path,
    kind,
    *,
    operational_data,
    managed_clusters,
    restore_only=False,
    operation_extra=None,
    mode="execute",
    version="2.13.4",
    features=None,
):
    """The activation role entered directly, with only the checkpoint as recorded state."""
    fake = FakeMigrationHub(
        version=version,
        restores=[passive_restore()],
        controller=complete_on_mutation,
        managed_clusters=managed_clusters,
    )
    try:
        operation = dict(KIND_OPERATION[kind])
        identity = _identity(operation)
        if restore_only:
            operation.update(method="full")
            identity.update(
                primary_context="", primary_cluster_uid="", method="full", restore_only=True, old_hub_action="none"
            )
        kubeconfig = tmp_path / "secondary.kubeconfig"
        write_kubeconfig(kubeconfig, context="secondary-hub", server=fake.url, token="fixture-token")
        primary = tmp_path / "primary.kubeconfig"
        write_kubeconfig(primary, context="primary-hub", server=fake.url, token="fixture-token")
        checkpoint_path = tmp_path / "checkpoint.json"
        checkpoint_path.write_text(
            json.dumps(
                {
                    "schema_version": "2.0",
                    "phase": "preflight" if restore_only else "primary_prep",
                    "phase_status": "pass",
                    "completed_phases": ["preflight"] if restore_only else ["preflight", "primary_prep"],
                    "operational_data": operational_data,
                    "operation_identity": identity,
                    "errors": [],
                    "report_refs": [],
                }
            ),
            encoding="utf-8",
        )
        variables = {
            "acm_switchover_hubs": {
                "primary": (
                    {"context": "", "kubeconfig": ""}
                    if restore_only
                    else {"context": "primary-hub", "kubeconfig": str(primary)}
                ),
                "secondary": {"context": "secondary-hub", "kubeconfig": str(kubeconfig)},
            },
            "acm_switchover_operation": dict(
                operation,
                restore_only=restore_only,
                old_hub_action="none" if restore_only else "secondary",
                **(operation_extra or {}),
            ),
            "acm_switchover_execution": {
                "mode": mode,
                "report_dir": str(tmp_path / "artifacts"),
                "checkpoint": {"enabled": True, "backend": "file", "path": str(checkpoint_path)},
            },
            "_acm_restore_poll_delays": [0, 0, 0],
        }
        if features is not None:
            variables["acm_switchover_features"] = features
        playbook = tmp_path / "role.yml"
        playbook.write_text(
            yaml.safe_dump(
                [
                    {
                        "hosts": "localhost",
                        "connection": "local",
                        "gather_facts": False,
                        "tasks": [
                            {
                                "name": "Enter the activation role directly",
                                "ansible.builtin.include_role": {"name": "tomazb.acm_switchover.activation"},
                            }
                        ],
                    }
                ],
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        vars_file = tmp_path / "vars.yml"
        vars_file.write_text(yaml.safe_dump(variables, sort_keys=False), encoding="utf-8")
        completed = subprocess.run(
            ["ansible-playbook", str(playbook), "-i", "localhost,", "-e", f"@{vars_file}"],
            cwd=_repo_root(),
            capture_output=True,
            text=True,
            check=False,
            env=_ansible_env(_repo_root(), tmp_path),
            timeout=300,
        )
    finally:
        fake.close()
    return completed, json.loads(checkpoint_path.read_text(encoding="utf-8")), fake


def test_direct_entry_enforces_the_recorded_expectation(tmp_path):
    """Review finding 3: preflight's public facts are absent; the checkpoint records cluster-a."""
    completed, stored, _ = _run_whole_role(
        tmp_path,
        "passive_patch",
        operational_data={"expected_managed_cluster_names": ["cluster-a"], "expected_managed_cluster_count": 1},
        managed_clusters=[],
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0, output
    assert "Missing expected ManagedCluster(s) after restore: cluster-a" in output
    assert stored["operational_data"]["migration_backups"]["restore"]["completed_at"] is None


def test_direct_entry_without_a_recorded_expectation_is_refused_before_any_mutation(tmp_path):
    completed, stored, fake = _run_whole_role(tmp_path, "passive_patch", operational_data={}, managed_clusters=[])
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0, output
    assert "No managed-cluster expectation is recorded" in output
    assert fake.writes == [] and "migration_backups" not in stored["operational_data"]


def test_direct_entry_with_an_explicit_minimum_needs_no_recorded_expectation(tmp_path):
    completed, stored, _ = _run_whole_role(
        tmp_path,
        "passive_patch",
        operational_data={},
        managed_clusters=["cluster-a"],
        operation_extra={"min_managed_clusters": 1},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert stored["operational_data"]["migration_backups"]["restore"]["completed_at"]


def test_direct_restore_only_entry_keeps_its_recorded_empty_expectation(tmp_path):
    completed, stored, _ = _run_whole_role(
        tmp_path,
        "full_restore",
        operational_data={"expected_managed_cluster_names": [], "expected_managed_cluster_count": 0},
        managed_clusters=["cluster-a"],
        restore_only=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert stored["operational_data"]["migration_backups"]["restore"]["completed_at"]


def test_a_direct_validate_entry_mutates_nothing(tmp_path):
    """Round-2 finding 1: validate is non-mutating like a dry run, even with auto-import management
    enabled on ACM 2.14+ with a non-local cluster, the default import strategy and no recorded
    ManagedCluster expectation or minimum (Codex's input)."""
    completed, stored, fake = _run_whole_role(
        tmp_path,
        "passive_patch",
        operational_data={},
        managed_clusters=["cluster-a"],
        mode="validate",
        version="2.14.3",
        features={"manage_auto_import_strategy": True},
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "Set autoImportStrategy to ImportAndSync] ***\nskipping" in output
    assert fake.writes == [], output
    assert fake.mutations == []
    assert "migration_backups" not in stored["operational_data"]


# --- Task 8 round 3, item 6: an accepted poll decision ends the loop's work ----------------------


def _poll_reads(hub: FakeMigrationHub) -> dict:
    paths = [request["path"].split("?")[0] for request in hub.requests if request["method"] == "GET"]
    return {
        "deleted_restore": sum(path.endswith(f"/restores/{PASSIVE}") for path in paths),
        "journaled_restore": sum(path.endswith("/restores/restore-acm-full") for path in paths),
        "velero_restores": sum(path.endswith(f"/apis/velero.io/v1/namespaces/{NS}/restores") for path in paths),
        "managed_clusters": sum(path.endswith("/managedclusters") for path in paths),
    }


@pytest.mark.parametrize("delays", [[0], [0, 0, 0, 0]], ids=["one-poll", "four-polls"])
def test_polls_stop_reading_once_their_decision_is_accepted(tmp_path, delays):
    """Every poll is satisfied at once: a longer poll budget adds no request to any wait."""
    fake = FakeMigrationHub(restores=[passive_restore()], controller=complete_on_mutation)
    operation = dict(KIND_OPERATION["full_restore"], restore_only=False, old_hub_action="secondary")
    try:
        run = _run_full_restore(tmp_path, fake, delays, operation)
    finally:
        fake.close()
    assert run.completed.returncode == 0, run.output
    assert run.journal["restore"]["completed_at"]
    reads = _poll_reads(fake)
    assert reads == _BASELINE_POLL_READS, reads


def _run_full_restore(tmp_path, fake, delays, operation):
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "primary_prep",
                "phase_status": "pass",
                "completed_phases": ["preflight", "primary_prep"],
                "operational_data": {},
                "operation_identity": _identity(operation),
                "errors": [],
                "report_refs": [],
            }
        ),
        encoding="utf-8",
    )
    kubeconfig = tmp_path / "secondary.kubeconfig"
    write_kubeconfig(kubeconfig, context="secondary-hub", server=fake.url, token="fixture-token")
    variables = {
        "acm_switchover_hubs": {
            "primary": {"context": "primary-hub", "kubeconfig": ""},
            "secondary": {"context": "secondary-hub", "kubeconfig": str(kubeconfig)},
        },
        "acm_switchover_operation": dict(operation, min_managed_clusters=1),
        "acm_switchover_execution": {
            "mode": "execute",
            "report_dir": str(tmp_path / "artifacts"),
            "checkpoint": {"enabled": True, "backend": "file", "path": str(checkpoint_path)},
        },
        "_acm_restore_poll_delays": delays,
    }
    tasks = [
        {
            "name": "Enter the activation checkpoint",
            "tomazb.acm_switchover.checkpoint_phase": {
                "phase": "activation",
                "checkpoint": "{{ acm_switchover_execution.checkpoint }}",
                "status": "enter",
            },
            "register": "_checkpoint_enter",
        },
        *(
            {
                "name": f"Run activation {task}",
                "ansible.builtin.include_role": {"name": "tomazb.acm_switchover.activation", "tasks_from": task},
            }
            for task in ("discover_resources", "activate_restore", "wait_for_restore")
        ),
    ]
    playbook = tmp_path / "activation.yml"
    playbook.write_text(
        yaml.safe_dump(
            [{"hosts": "localhost", "connection": "local", "gather_facts": False, "tasks": tasks}], sort_keys=False
        ),
        encoding="utf-8",
    )
    vars_file = tmp_path / "vars.yml"
    vars_file.write_text(yaml.safe_dump(variables, sort_keys=False), encoding="utf-8")
    completed = subprocess.run(
        ["ansible-playbook", str(playbook), "-i", "localhost,", "-e", f"@{vars_file}"],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
        env=_ansible_env(_repo_root(), tmp_path),
        timeout=300,
    )
    return Run(completed, fake, b"", checkpoint_path.read_bytes())


# The reads of a run with a one-poll budget, whose every poll is satisfied by its first read
# (the accepted ManagedCluster decision also skips the post-loop re-read).
_BASELINE_POLL_READS = {"deleted_restore": 2, "journaled_restore": 5, "velero_restores": 2, "managed_clusters": 1}
