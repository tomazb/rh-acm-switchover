"""Shipped Restore-cleanup transaction tasks against a fake destination hub (R4-04 PR C Task 10).

A real activation run against the fake hub produces the completed migration journal; the
post-activation completion is then recorded and the finalization role's
cleanup_journaled_restore.yml, cleanup_restores.yml and enable_backups.yml run in execute
mode, dry-run and check mode with a real checkpoint file. The fake hub records the journal
the checkpoint held at each Restore mutation, so "intent durable before DELETE" is checked at
the instant of the request.
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
    FakeMigrationHub,
    passive_restore,
)
from ansible_collections.tomazb.acm_switchover.tests.integration.test_activation_transaction_runtime import (  # noqa: F401
    _identity,
    complete_on_mutation,
    run_activation,
)

KIND_OPERATION = {
    "passive_patch": {"method": "passive", "activation_method": "patch"},
    "passive_restore": {"method": "passive", "activation_method": "restore"},
    "full_restore": {"method": "full", "activation_method": "patch"},
}
POST_DONE = {"names_verified_at": "2024-01-02T14:00:00+00:00", "completed_at": "2024-01-02T14:05:00+00:00"}
REPAIR = {
    "actor": "operator@example.com",
    "reason": "the Restore was removed out of band; confirmed from the audit log",
    "inspected_evidence": ["audit-log:restore-delete"],
}
FINALIZATION_TASKS = ("cleanup_journaled_restore",)
_NO_SCHEDULES: dict = {"acm_finalization_backup_schedules_info": {"resources": []}}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


class Run:
    def __init__(self, completed, hub, path):
        self.completed = completed
        self.hub = hub
        self.path = path

    @property
    def output(self) -> str:
        return self.completed.stdout + self.completed.stderr

    @property
    def journal(self):
        return (json.loads(self.path.read_text(encoding="utf-8")).get("operational_data") or {}).get(
            "migration_backups"
        )


def completed_journal(run_activation, kind, hub):  # noqa: F811
    """A real activation run, then the post-activation completion the cleanup needs."""
    run = run_activation(kind, hub)
    assert run.completed.returncode == 0, run.output
    journal = copy.deepcopy(run.journal)
    journal["post_activation"] = dict(POST_DONE)
    return journal


@pytest.fixture
def run_finalization(tmp_path):
    def _run(
        kind,
        hub,
        journal,
        *,
        mode="execute",
        check=False,
        tasks=FINALIZATION_TASKS,
        repair=None,
        extra=None,
        published=None,
    ):
        operation = KIND_OPERATION[kind]
        path = tmp_path / "finalization-checkpoint.json"
        if journal is not None or not path.exists():
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "2.0",
                        "phase": "post_activation",
                        "phase_status": "pass",
                        "completed_phases": ["preflight", "primary_prep", "activation", "post_activation"],
                        "operational_data": {} if journal is None else {"migration_backups": journal},
                        "operation_identity": _identity(operation),
                        "errors": [],
                        "report_refs": [],
                    }
                ),
                encoding="utf-8",
            )

        def probe():
            stored = json.loads(path.read_text(encoding="utf-8"))
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
                "checkpoint": {"enabled": True, "backend": "file", "path": str(path)},
            },
            "_acm_cleanup_poll_delays": [0, 0, 0],
            "acm_finalization_mch_info": {"resources": [{"status": {"currentVersion": hub.version}}]},
            "acm_finalization_restores_info": {"resources": []},
            "acm_switchover_saved_backup_schedule": {},
            **(extra or {}),
        }
        # main.yml publishes these facts before the transaction; the role entry guard
        # supplies the validated repair request.
        prelude = {
            "name": "Publish the finalization migration journal facts",
            "ansible.builtin.set_fact": {
                "_acm_preview": "{{ (acm_switchover_execution.mode | default('dry_run')) != 'execute' "
                "or ansible_check_mode }}",
                "_acm_journal": "{{ (((_checkpoint_enter | default({})).get('facts') or {})"
                ".get('migration_journal')) or {} }}",
                "_acm_has_journal": "{{ (((_checkpoint_enter | default({})).get('facts') or {})"
                ".get('migration_journal')) is not none }}",
                "_acm_cleanup_repair_request": repair or {},
            },
        }
        include = [
            {
                "name": f"Run finalization {task}",
                "ansible.builtin.include_role": {"name": "tomazb.acm_switchover.finalization", "tasks_from": task},
            }
            for task in tasks
        ]
        playbook = tmp_path / "finalization.yml"
        playbook.write_text(
            yaml.safe_dump(
                [
                    {
                        "hosts": "localhost",
                        "connection": "local",
                        "gather_facts": False,
                        "tasks": [
                            {
                                "name": "Enter the finalization checkpoint",
                                "tomazb.acm_switchover.checkpoint_phase": {
                                    "phase": "finalization",
                                    "checkpoint": "{{ acm_switchover_execution.checkpoint }}",
                                    "status": "enter",
                                },
                                "register": "_checkpoint_enter",
                            },
                            prelude,
                            *(
                                [
                                    {
                                        "name": "Publish facts an earlier phase left behind",
                                        "ansible.builtin.set_fact": published,
                                    }
                                ]
                                if published
                                else []
                            ),
                            *include,
                            {
                                "name": "Report the published BackupSchedules",
                                "ansible.builtin.debug": {
                                    "msg": "SCHEDULES={{ acm_finalization_backup_schedules_info.resources"
                                    " | map(attribute='metadata.name') | join(',') }}"
                                },
                                "when": "_acm_report_schedules | default(false)",
                            },
                            {"name": "Report the cleanup", "ansible.builtin.debug": {"msg": "FINALIZATION_CLEANUP=ok"}},
                        ],
                    }
                ],
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        vars_file = tmp_path / "finalization-vars.yml"
        vars_file.write_text(yaml.safe_dump(variables, sort_keys=False), encoding="utf-8")
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
        return Run(completed, hub, path)

    return _run


@pytest.fixture
def hub():
    fake = FakeMigrationHub(restores=[passive_restore()], controller=complete_on_mutation)
    yield fake
    fake.close()


def _deletes(hub):
    return [(name, journal) for kind, name, journal in hub.mutations if kind == "delete"]


@pytest.mark.parametrize("kind", ["passive_patch", "passive_restore", "full_restore"])
def test_the_cleanup_revalidates_records_intent_then_deletes_and_completes(
    run_activation, run_finalization, hub, kind  # noqa: F811
):
    journal = completed_journal(run_activation, kind, hub)
    name = journal["restore"]["name"]
    revision = hub.acm[name]["metadata"]["resourceVersion"]
    before = len(_deletes(hub))

    run = run_finalization(kind, hub, journal)

    assert run.completed.returncode == 0, run.output
    assert "FINALIZATION_CLEANUP=ok" in run.output
    deletes = _deletes(hub)[before:]
    assert [deleted for deleted, _ in deletes] == [name]
    # The intent was durable at the instant of the DELETE.
    assert deletes[0][1]["cleanup"]["state"] == "intent_persisted"
    assert deletes[0][1]["restore"]["teardown_revalidated_at"]
    # The fake hub refuses a DELETE whose UID or resourceVersion precondition is missing or stale.
    cleanup = run.journal["cleanup"]
    assert cleanup["state"] == "completed"
    assert cleanup["absence_verified_at"] == cleanup["completed_at"]
    assert cleanup["final_get_resource_version"] == revision
    for field in ("uid", "generation", "spec_fingerprint", "backup_fields", "mutation_kind"):
        assert cleanup[field] == journal["restore"][field], field
    assert name not in hub.acm


def test_a_resumed_completed_cleanup_touches_nothing(run_activation, run_finalization, hub):  # noqa: F811
    journal = completed_journal(run_activation, "full_restore", hub)
    first = run_finalization("full_restore", hub, journal)
    assert first.completed.returncode == 0, first.output
    writes = len(hub.writes)

    second = run_finalization("full_restore", hub, None)

    assert second.completed.returncode == 0, second.output
    assert second.journal == first.journal
    assert len(hub.writes) == writes


def test_a_failed_barrier_records_nothing_and_deletes_nothing(run_activation, run_finalization, hub):  # noqa: F811
    journal = completed_journal(run_activation, "full_restore", hub)
    child = journal["restore"]["velero_restores"]["managed_clusters"][0]["name"]
    hub.velero[child]["metadata"]["uid"] = "uid-replacement"
    before = len(_deletes(hub))

    run = run_finalization("full_restore", hub, journal)

    assert run.completed.returncode != 0
    assert run.journal == journal
    assert _deletes(hub)[before:] == []


def test_a_replacement_on_resume_recovers_and_is_kept(run_activation, run_finalization, hub):  # noqa: F811
    journal = completed_journal(run_activation, "full_restore", hub)
    name = journal["restore"]["name"]
    first = run_finalization("full_restore", hub, journal, extra={"_acm_cleanup_poll_delays": []})
    # The bounded poll was empty: the accepted DELETE stays resumable with no completion.
    assert first.completed.returncode != 0
    assert first.journal["cleanup"]["state"] == "delete_accepted"
    intent = first.journal
    replacement = passive_restore(name=name)
    replacement["metadata"].update(uid="uid-replacement", resourceVersion="5005")
    hub.acm[name] = replacement
    before = len(_deletes(hub))

    run = run_finalization("full_restore", hub, None)

    assert run.completed.returncode != 0
    assert "replacement_uid" in run.output
    cleanup = run.journal["cleanup"]
    assert cleanup["state"] == "recovery_required"
    assert (cleanup["recovery"]["observed_uid"], cleanup["recovery"]["observed_resource_version"]) == (
        "uid-replacement",
        "5005",
    )
    assert cleanup["delete_accepted_at"] == intent["cleanup"]["delete_accepted_at"]
    assert hub.acm[name]["metadata"]["uid"] == "uid-replacement"
    assert _deletes(hub)[before:] == []


def test_absence_on_resume_without_completion_recovers_never_succeeds(
    run_activation, run_finalization, hub  # noqa: F811
):
    journal = completed_journal(run_activation, "full_restore", hub)
    first = run_finalization("full_restore", hub, journal, extra={"_acm_cleanup_poll_delays": []})
    assert first.journal["cleanup"]["state"] == "delete_accepted"

    run = run_finalization("full_restore", hub, None)

    assert run.completed.returncode != 0
    assert "absent_without_completion" in run.output
    cleanup = run.journal["cleanup"]
    assert (cleanup["state"], cleanup["completed_at"]) == ("recovery_required", None)
    assert cleanup["recovery"]["observed_uid"] is None


def test_recovery_blocks_until_an_audited_repair_with_a_proven_absence(
    run_activation, run_finalization, hub  # noqa: F811
):
    journal = completed_journal(run_activation, "full_restore", hub)
    run_finalization("full_restore", hub, journal, extra={"_acm_cleanup_poll_delays": []})
    recovered = run_finalization("full_restore", hub, None)
    assert recovered.journal["cleanup"]["state"] == "recovery_required"

    blocked = run_finalization("full_restore", hub, None)
    assert blocked.completed.returncode != 0
    assert "cleanup_recovery_required" in blocked.output
    assert blocked.journal == recovered.journal

    request = dict(REPAIR, run_id=journal["run_id"], operation_id=recovered.journal["cleanup"]["operation_id"])
    writes = len(hub.writes)
    repaired = run_finalization(
        "full_restore",
        hub,
        None,
        repair=request,
        tasks=("cleanup_journaled_restore", "enable_backups"),
        published=_NO_SCHEDULES,
    )

    assert repaired.completed.returncode == 0, repaired.output
    cleanup = repaired.journal["cleanup"]
    assert cleanup["state"] == "repaired"
    assert {key: cleanup["repair"][key] for key in request} == request
    assert cleanup["repair"]["acknowledged_at"]
    assert cleanup["recovery"] == recovered.journal["cleanup"]["recovery"]
    assert (cleanup["absence_verified_at"], cleanup["completed_at"]) == (None, None)
    assert len(hub.writes) == writes


def test_a_repair_with_an_occupied_locator_is_refused(run_activation, run_finalization, hub):  # noqa: F811
    journal = completed_journal(run_activation, "full_restore", hub)
    run_finalization("full_restore", hub, journal, extra={"_acm_cleanup_poll_delays": []})
    recovered = run_finalization("full_restore", hub, None)
    name = journal["restore"]["name"]
    occupant = passive_restore(name=name)
    occupant["metadata"]["uid"] = "uid-replacement"
    hub.acm[name] = occupant
    request = dict(REPAIR, run_id=journal["run_id"], operation_id=recovered.journal["cleanup"]["operation_id"])

    run = run_finalization("full_restore", hub, None, repair=request)

    assert run.completed.returncode != 0
    assert "repair_locator_occupied" in run.output
    assert run.journal == recovered.journal
    assert hub.acm[name]["metadata"]["uid"] == "uid-replacement"


def test_enablement_refuses_a_replacement_at_the_completed_locator(run_activation, run_finalization, hub):  # noqa: F811
    journal = completed_journal(run_activation, "full_restore", hub)
    first = run_finalization("full_restore", hub, journal)
    assert first.journal["cleanup"]["state"] == "completed"
    name = journal["restore"]["name"]
    occupant = passive_restore(name=name)
    occupant["metadata"]["uid"] = "uid-replacement"
    hub.acm[name] = occupant

    run = run_finalization(
        "full_restore", hub, None, tasks=("cleanup_journaled_restore", "enable_backups"), published=_NO_SCHEDULES
    )

    assert run.completed.returncode != 0
    assert "locator_occupied" in run.output
    assert hub.acm[name]["metadata"]["uid"] == "uid-replacement"


def test_generic_cleanup_never_routes_the_journaled_locator(run_activation, run_finalization, hub):  # noqa: F811
    journal = completed_journal(run_activation, "full_restore", hub)
    first = run_finalization("full_restore", hub, journal)
    assert first.journal["cleanup"]["state"] == "completed"
    name = journal["restore"]["name"]
    occupant = passive_restore(name=name)
    occupant["metadata"]["uid"] = "uid-replacement"
    hub.acm[name] = occupant
    before = len(_deletes(hub))

    run = run_finalization("full_restore", hub, None, tasks=("cleanup_journaled_restore", "cleanup_restores"))

    assert run.completed.returncode != 0
    assert "locator_occupied" in run.output
    assert _deletes(hub)[before:] == []


def test_a_missing_journal_blocks_the_executing_cleanup(run_finalization, hub):
    run = run_finalization("full_restore", hub, None)

    assert run.completed.returncode != 0
    assert "journal_required" in run.output
    assert hub.writes == []


@pytest.mark.parametrize("preview", [{"mode": "dry_run"}, {"mode": "validate"}, {"check": True}])
def test_a_preview_reads_and_writes_nothing(run_activation, run_finalization, hub, preview):  # noqa: F811
    journal = completed_journal(run_activation, "full_restore", hub)
    reads = len(hub.requests)

    run = run_finalization("full_restore", hub, journal, **preview)

    assert run.completed.returncode == 0, run.output
    assert "cleanup step: revalidate" in run.output
    assert run.journal == journal
    assert len(hub.requests) == reads


_RESTORES = "/apis/cluster.open-cluster-management.io/v1beta1/namespaces/open-cluster-management-backup/restores/"


def test_a_persistent_conflict_exhausts_the_bounded_retry(run_activation, run_finalization, hub):  # noqa: F811
    journal = completed_journal(run_activation, "full_restore", hub)
    name = journal["restore"]["name"]
    hub.failures[("DELETE", _RESTORES + name)] = 409
    before = len([r for r in hub.requests if r["method"] == "DELETE"])

    run = run_finalization("full_restore", hub, journal)

    assert run.completed.returncode != 0
    assert "cleanup_conflict_retries_exhausted" in run.output
    assert len([r for r in hub.requests if r["method"] == "DELETE"]) - before == 4
    assert run.journal["cleanup"]["state"] == "intent_persisted"
    assert name in hub.acm


def test_an_unverifiable_delete_fails_at_once_and_stays_resumable(run_activation, run_finalization, hub):  # noqa: F811
    journal = completed_journal(run_activation, "full_restore", hub)
    name = journal["restore"]["name"]
    hub.failures[("DELETE", _RESTORES + name)] = 500
    before = len([r for r in hub.requests if r["method"] == "DELETE"])

    run = run_finalization("full_restore", hub, journal)

    assert run.completed.returncode != 0
    assert len([r for r in hub.requests if r["method"] == "DELETE"]) - before == 1
    assert run.journal["cleanup"]["state"] == "intent_persisted"


_SCHEDULES = (
    "/apis/cluster.open-cluster-management.io/v1beta1/namespaces/open-cluster-management-backup/backupschedules"
)
_DISCOVERY_EXTRA: dict = {"_old_hub_existing_restore_info": {"resources": []}}
# An inventory an earlier phase published that an executing run must never trust.
_STALE = {"acm_finalization_backup_schedules_info": {"resources": [{"metadata": {"name": "stale-preseeded"}}]}}


def _published_schedules(run):
    line = next(line for line in run.output.splitlines() if "SCHEDULES=" in line)
    return line.split("SCHEDULES=", 1)[1].strip().strip('"').strip()


def test_an_executing_discovery_reads_backup_schedules_strictly_and_fresh(run_finalization, hub):
    hub.schedules["schedule-rhacm"] = {
        "apiVersion": "cluster.open-cluster-management.io/v1beta1",
        "kind": "BackupSchedule",
        "metadata": {"name": "schedule-rhacm", "namespace": "open-cluster-management-backup", "uid": "uid-bs"},
        "spec": {"paused": True},
    }

    run = run_finalization(
        "full_restore",
        hub,
        None,
        tasks=("discover_resources",),
        extra={**_DISCOVERY_EXTRA, "_acm_report_schedules": True},
        published=_STALE,
    )

    assert run.completed.returncode == 0, run.output
    assert any(request["path"] == _SCHEDULES for request in hub.requests)
    assert _published_schedules(run) == "schedule-rhacm"


def test_an_unproven_backup_schedule_inventory_blocks_the_executing_discovery(run_finalization, hub):
    hub.failures[("GET", _SCHEDULES)] = 403

    run = run_finalization(
        "full_restore", hub, None, tasks=("discover_resources",), extra=_DISCOVERY_EXTRA, published=_STALE
    )

    assert run.completed.returncode != 0
    assert "BackupSchedule inventory" in run.output


# -- Task 10 review findings (Codex, b0a5fae0..a0e41fac) -------------------------------------------


@pytest.mark.parametrize("resumed", [False, True], ids=["before-the-barrier", "before-a-resumed-delete"])
def test_a_changed_controller_lane_refuses(run_activation, run_finalization, hub, resumed):  # noqa: F811
    """Finding 3: a 2.14 journal on a destination upgraded to 2.17 never changes contract (A section 10)."""
    journal = completed_journal(run_activation, "full_restore", hub)
    if resumed:
        first = run_finalization("full_restore", hub, journal, extra={"_acm_cleanup_poll_delays": []})
        assert first.journal["cleanup"]["state"] == "delete_accepted"
        journal = first.journal
    hub.version = "2.17.1"
    before = len(_deletes(hub))

    run = run_finalization("full_restore", hub, None if resumed else journal)

    assert run.completed.returncode != 0
    assert "frozen on ACM 2.14, but the destination hub now runs ACM 2.17" in run.output
    assert run.journal == journal
    assert _deletes(hub)[before:] == []


def test_collision_recreation_requires_a_fresh_absent_locator(run_activation, run_finalization, hub):  # noqa: F811
    """Finding 1: collision repair recreates an enabled BackupSchedule only over a proven-empty locator."""
    journal = completed_journal(run_activation, "full_restore", hub)
    first = run_finalization("full_restore", hub, journal)
    assert first.journal["cleanup"]["state"] == "completed"
    name = journal["restore"]["name"]
    occupant = passive_restore(name=name)
    occupant["metadata"]["uid"] = "uid-replacement"
    hub.acm[name] = occupant
    schedule = {
        "apiVersion": "cluster.open-cluster-management.io/v1beta1",
        "kind": "BackupSchedule",
        "metadata": {"name": "schedule-rhacm", "namespace": "open-cluster-management-backup", "uid": "uid-bs"},
        "spec": {"veleroSchedule": "0 */1 * * *"},
        "status": {"phase": "Enabled"},
    }
    # Served live too, so a delete of the schedule would reach the API and be recorded.
    hub.schedules["schedule-rhacm"] = schedule

    run = run_finalization(
        "full_restore",
        hub,
        None,
        tasks=("cleanup_journaled_restore", "repair_backup_schedule_collision"),
        published={"acm_finalization_backup_schedules_info": {"resources": [schedule]}},
    )

    assert run.completed.returncode != 0
    assert "locator_occupied" in run.output
    assert not [r for r in hub.requests if r["method"] in {"DELETE", "POST", "PUT"} and "backupschedules" in r["path"]]
    assert hub.acm[name]["metadata"]["uid"] == "uid-replacement"


def test_the_poll_deadline_writes_no_completion(run_activation, run_finalization, hub):  # noqa: F811
    """Finding 4: the shared poll timeout is elapsed time; once spent, nothing completes."""
    journal = completed_journal(run_activation, "full_restore", hub)

    run = run_finalization("full_restore", hub, journal, extra={"_acm_cleanup_poll_timeout": 0})

    assert run.completed.returncode != 0
    assert "Timeout waiting for the journaled Restore" in run.output
    assert run.journal["cleanup"]["state"] == "delete_accepted"
