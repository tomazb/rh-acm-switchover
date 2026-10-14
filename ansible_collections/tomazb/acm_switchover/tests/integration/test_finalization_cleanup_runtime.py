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
from ansible_collections.tomazb.acm_switchover.tests.integration.test_post_activation_evidence_runtime import (  # noqa: F401
    run_post_activation,
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
        tz=None,
        entry=None,
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
        primary_kubeconfig = ""
        if entry == "main":
            # The role entry guard validates the hub inputs as preflight would.
            primary_kubeconfig = str(tmp_path / "primary.kubeconfig")
            write_kubeconfig(Path(primary_kubeconfig), context="primary-hub", server=hub.url, token="fixture-token")
        variables = {
            "acm_switchover_hubs": {
                "primary": {"context": "primary-hub", "kubeconfig": primary_kubeconfig},
                "secondary": {"context": "secondary-hub", "kubeconfig": str(kubeconfig)},
            },
            "acm_switchover_operation": dict(
                operation,
                restore_only=False,
                old_hub_action="secondary",
                **({"migration_cleanup_repair": repair} if entry == "main" and repair else {}),
            ),
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
        # entry="main" runs the role's main.yml: its own checkpoint enter, role entry guard
        # and fact publication, with the repair request as the operator supplies it.
        if entry == "main":
            include = [
                {
                    "name": "Run the finalization role",
                    "ansible.builtin.include_role": {"name": "tomazb.acm_switchover.finalization"},
                }
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
                            *(
                                []
                                if entry == "main"
                                else [
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
                                ]
                            ),
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
            env=dict(_ansible_env(_repo_root(), tmp_path), **({"TZ": tz} if tz else {})),
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


def test_the_role_entry_derives_the_repair_request_from_the_operator_input(
    run_activation, run_finalization, hub  # noqa: F811
):
    """Task 10 carry-forward: main.yml takes the request from the role entry guard's validated inputs."""
    journal = completed_journal(run_activation, "full_restore", hub)
    run_finalization("full_restore", hub, journal, extra={"_acm_cleanup_poll_delays": []})
    recovered = run_finalization("full_restore", hub, None)
    assert recovered.journal["cleanup"]["state"] == "recovery_required"

    blocked = run_finalization("full_restore", hub, None, entry="main")
    assert blocked.completed.returncode != 0
    assert "cleanup_recovery_required" in blocked.output
    assert blocked.journal == recovered.journal

    request = dict(REPAIR, run_id=journal["run_id"], operation_id=recovered.journal["cleanup"]["operation_id"])
    # The repair is durable before the old-hub tail, which this fake hub does not serve.
    repaired = run_finalization("full_restore", hub, None, repair=request, entry="main")

    cleanup = repaired.journal["cleanup"]
    assert cleanup["state"] == "repaired", repaired.output
    assert {key: cleanup["repair"][key] for key in request} == request
    assert cleanup["recovery"] == recovered.journal["cleanup"]["recovery"]


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
    # The no_log DELETE never censors the refusal: the phase failure names its reason, and only that.
    failure = "Migration evidence refused (delete_not_accepted: the guarded DELETE was not accepted (unverifiable))"
    assert failure in run.output
    assert "fixture-token" not in run.output
    assert "secondary.kubeconfig" not in run.output


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
    assert "(controller_lane_changed)" in run.output
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


_SCHEDULES_PATH = (
    "/apis/cluster.open-cluster-management.io/v1beta1/namespaces/open-cluster-management-backup/backupschedules"
)


def _collision_schedule(hub):
    schedule = {
        "apiVersion": "cluster.open-cluster-management.io/v1beta1",
        "kind": "BackupSchedule",
        "metadata": {"name": "schedule-rhacm", "namespace": "open-cluster-management-backup", "uid": "uid-bs"},
        "spec": {"veleroSchedule": "0 */1 * * *"},
        "status": {"phase": "Enabled"},
    }
    hub.schedules["schedule-rhacm"] = schedule
    return schedule


def _schedule_writes(hub):
    return [
        r["method"] for r in hub.requests if r["method"] in {"DELETE", "POST", "PUT"} and _SCHEDULES_PATH in r["path"]
    ]


def test_collision_repair_never_reads_the_locator_between_the_delete_and_the_recreate(
    run_activation, run_finalization, hub  # noqa: F811
):
    """CodeRabbit 4274709915: the last locator proof precedes the BackupSchedule DELETE, as in
    Python, so a read failure there can never strike between the DELETE and the re-create."""
    journal = completed_journal(run_activation, "full_restore", hub)
    assert run_finalization("full_restore", hub, journal).journal["cleanup"]["state"] == "completed"
    locator = _RESTORES + journal["restore"]["name"]
    schedule = _collision_schedule(hub)
    plain_failure = hub._failure

    def fail_locator_reads_after_the_schedule_delete(method, path):
        if method == "GET" and path == locator and "DELETE" in _schedule_writes(hub):
            return 500
        return plain_failure(method, path)

    hub._failure = fail_locator_reads_after_the_schedule_delete

    run = run_finalization(
        "full_restore",
        hub,
        None,
        tasks=("cleanup_journaled_restore", "repair_backup_schedule_collision"),
        published={"acm_finalization_backup_schedules_info": {"resources": [schedule]}},
    )

    assert run.completed.returncode == 0, run.output
    assert _schedule_writes(hub) == ["DELETE", "POST"]
    assert "schedule-rhacm" in hub.schedules


def test_a_locator_read_error_before_collision_repair_leaves_the_schedule_and_is_retryable(
    run_activation, run_finalization, hub  # noqa: F811
):
    journal = completed_journal(run_activation, "full_restore", hub)
    assert run_finalization("full_restore", hub, journal).journal["cleanup"]["state"] == "completed"
    locator = _RESTORES + journal["restore"]["name"]
    schedule = _collision_schedule(hub)
    hub.failures[("GET", locator)] = 500
    args = {
        "tasks": ("cleanup_journaled_restore", "repair_backup_schedule_collision"),
        "published": {"acm_finalization_backup_schedules_info": {"resources": [schedule]}},
    }

    refused = run_finalization("full_restore", hub, None, **args)

    assert refused.completed.returncode != 0
    assert _schedule_writes(hub) == []
    assert hub.schedules["schedule-rhacm"]["metadata"]["uid"] == "uid-bs"

    del hub.failures[("GET", locator)]
    retried = run_finalization("full_restore", hub, None, **args)

    assert retried.completed.returncode == 0, retried.output
    assert _schedule_writes(hub) == ["DELETE", "POST"]


def test_the_poll_deadline_writes_no_completion(run_activation, run_finalization, hub):  # noqa: F811
    """Finding 4: the shared poll timeout is elapsed time; once spent, nothing completes."""
    journal = completed_journal(run_activation, "full_restore", hub)

    run = run_finalization("full_restore", hub, journal, extra={"_acm_cleanup_poll_timeout": 0})

    assert run.completed.returncode != 0
    assert "Timeout waiting for the journaled Restore" in run.output
    assert run.journal["cleanup"]["state"] == "delete_accepted"


# -- Plan Task 11: the evidence gate before every integrated old-hub action ----------------------

_GATED = ("cleanup_journaled_restore", "require_old_hub_evidence")


@pytest.mark.parametrize("kind", ["passive_patch", "passive_restore", "full_restore"])
def test_the_old_hub_gate_opens_after_this_pass_completes_the_cleanup(
    run_activation, run_finalization, hub, kind  # noqa: F811
):
    journal = completed_journal(run_activation, kind, hub)
    name = journal["restore"]["name"]

    run = run_finalization(kind, hub, journal, tasks=_GATED)

    assert run.completed.returncode == 0, run.output
    assert run.journal["cleanup"]["state"] == "completed"
    # The gate's own fresh strict read of the locator is the last request.
    assert hub.requests[-1] == {"method": "GET", "path": _RESTORES + name}


def test_the_old_hub_gate_refuses_a_replacement_after_completion(run_activation, run_finalization, hub):  # noqa: F811
    journal = completed_journal(run_activation, "full_restore", hub)
    first = run_finalization("full_restore", hub, journal)
    assert first.journal["cleanup"]["state"] == "completed"
    name = journal["restore"]["name"]
    occupant = passive_restore(name=name)
    occupant["metadata"]["uid"] = "uid-replacement"
    hub.acm[name] = occupant
    writes = len(hub.writes)

    run = run_finalization("full_restore", hub, None, tasks=_GATED)

    assert run.completed.returncode != 0
    assert "locator_occupied" in run.output
    assert run.journal == first.journal
    assert len(hub.writes) == writes
    assert hub.acm[name]["metadata"]["uid"] == "uid-replacement"


def test_the_old_hub_gate_refuses_unverified_activation_names(run_activation, run_finalization, hub):  # noqa: F811
    journal = completed_journal(run_activation, "full_restore", hub)
    journal["restore"]["names_verified_at"] = None

    run = run_finalization("full_restore", hub, journal, tasks=_GATED)

    assert run.completed.returncode != 0
    assert "activation_names_unverified" in run.output
    # The cleanup itself completed; only the old-hub actions stay blocked.
    assert run.journal["cleanup"]["state"] == "completed"


def test_a_sub_second_poll_budget_issues_no_read_and_completes_nothing(
    run_activation, run_finalization, hub  # noqa: F811
):
    """Edge 2 (Codex): under one second of budget left, no read (rounded up to 1 s) is allowed."""
    journal = completed_journal(run_activation, "full_restore", hub)
    name = journal["restore"]["name"]
    path = _RESTORES + name

    run = run_finalization("full_restore", hub, journal, extra={"_acm_cleanup_poll_timeout": 0.9})

    assert run.completed.returncode != 0
    assert "Timeout waiting for the journaled Restore" in run.output
    assert run.journal["cleanup"]["state"] == "delete_accepted"
    methods = [request["method"] for request in hub.requests if request["path"] == path]
    deleted_at = len(methods) - 1 - methods[::-1].index("DELETE")
    assert methods[deleted_at + 1 :] == []


@pytest.fixture
def old_hub(tmp_path):
    """A primary (old) hub the secondary disposition creates its passive Restore on."""
    fake = FakeMigrationHub(restores=[])
    kubeconfig = tmp_path / "primary.kubeconfig"
    write_kubeconfig(kubeconfig, context="primary-hub", server=fake.url, token="fixture-token")
    yield fake, {"context": "primary-hub", "kubeconfig": str(kubeconfig)}
    fake.close()


def _hubs(hub, tmp_path, primary):
    secondary = tmp_path / "secondary.kubeconfig"
    write_kubeconfig(secondary, context="secondary-hub", server=hub.url, token="fixture-token")
    return {
        "acm_switchover_hubs": {
            "primary": primary,
            "secondary": {"context": "secondary-hub", "kubeconfig": str(secondary)},
        }
    }


def test_handle_old_hub_entered_directly_without_a_journal_creates_no_passive_restore(
    run_finalization, hub, old_hub, tmp_path
):
    """Codex warning on cb0302ff: the direct `tasks_from: handle_old_hub` entry gates itself."""
    primary, primary_hub = old_hub

    run = run_finalization(
        "full_restore", hub, None, tasks=("handle_old_hub",), extra=_hubs(hub, tmp_path, primary_hub)
    )

    assert run.completed.returncode != 0
    assert "no validated migration journal" in run.output
    assert primary.requests == []
    assert "restore-acm-passive-sync" not in primary.acm


def test_handle_old_hub_creates_the_passive_restore_after_the_evidence_gate(
    run_activation, run_finalization, hub, old_hub, tmp_path  # noqa: F811
):
    primary, primary_hub = old_hub
    journal = completed_journal(run_activation, "full_restore", hub)
    name = journal["restore"]["name"]

    run = run_finalization(
        "full_restore",
        hub,
        journal,
        tasks=("cleanup_journaled_restore", "handle_old_hub"),
        extra=_hubs(hub, tmp_path, primary_hub),
    )

    assert run.completed.returncode == 0, run.output
    assert hub.requests[-1] == {"method": "GET", "path": _RESTORES + name}
    assert primary.acm["restore-acm-passive-sync"]["spec"]["veleroManagedClustersBackupName"] == "skip"


def test_handle_old_hub_refuses_a_replacement_at_the_journaled_locator(
    run_activation, run_finalization, hub, old_hub, tmp_path  # noqa: F811
):
    primary, primary_hub = old_hub
    journal = completed_journal(run_activation, "full_restore", hub)
    first = run_finalization("full_restore", hub, journal)
    assert first.journal["cleanup"]["state"] == "completed"
    name = journal["restore"]["name"]
    occupant = passive_restore(name=name)
    occupant["metadata"]["uid"] = "uid-replacement"
    hub.acm[name] = occupant

    run = run_finalization(
        "full_restore",
        hub,
        None,
        tasks=("cleanup_journaled_restore", "handle_old_hub"),
        extra=_hubs(hub, tmp_path, primary_hub),
    )

    assert run.completed.returncode != 0
    assert "locator_occupied" in run.output
    assert primary.requests == []


@pytest.mark.parametrize("tz", ["Etc/GMT-14", "Etc/GMT+12"], ids=["utc-plus-14", "utc-minus-12"])
def test_the_poll_deadline_is_one_wall_clock_on_a_non_utc_controller(
    run_activation, run_finalization, hub, tz  # noqa: F811
):
    """Clock sweep: the YAML deadline and the module's time.time() share one epoch whatever the
    controller's zone. A UTC wall time read as local (now(utc=true).timestamp()) puts the deadline
    14 hours in the past on a UTC+14 controller, so the cleanup would time out without a poll."""
    journal = completed_journal(run_activation, "full_restore", hub)

    run = run_finalization("full_restore", hub, journal, tz=tz)

    assert run.completed.returncode == 0, run.output
    assert run.journal["cleanup"]["state"] == "completed"


def test_the_composed_lifecycle_reaches_the_old_hub_gate_without_inserted_evidence(
    run_activation, run_post_activation, run_finalization  # noqa: F811
):
    """Activation, post-activation and the journaled cleanup each write their own evidence.

    Each stage's journal is the previous stage's output, carried between the per-stage
    checkpoint files the harnesses own; nothing is synthesized. The fake hub serves no
    old-hub observability or passive Restore setup, so the lifecycle stops at the old-hub
    gate's decision.
    """
    hub = FakeMigrationHub(
        restores=[passive_restore()], controller=complete_on_mutation, managed_clusters=["cluster-a"]
    )
    try:
        activated = run_activation("passive_patch", hub)
        assert activated.completed.returncode == 0, activated.output
        assert activated.journal["restore"]["completed_at"]
        assert activated.journal["post_activation"]["completed_at"] is None

        verified = run_post_activation(hub, journal=activated.journal, names=("cluster-a",))
        assert verified.completed.returncode == 0, verified.output
        post = verified.journal["post_activation"]
        assert post["names_verified_at"] and post["completed_at"]
        assert verified.journal["restore"] == activated.journal["restore"]

        finalized = run_finalization("passive_patch", hub, verified.journal, tasks=_GATED)
        assert finalized.completed.returncode == 0, finalized.output
        assert finalized.journal["cleanup"]["state"] == "completed"
        assert finalized.journal["post_activation"] == post
        name = activated.journal["restore"]["name"]
        assert name not in hub.acm
        # The gate's own fresh strict read of the locator is the last request.
        assert hub.requests[-1] == {"method": "GET", "path": _RESTORES + name}
    finally:
        hub.close()
