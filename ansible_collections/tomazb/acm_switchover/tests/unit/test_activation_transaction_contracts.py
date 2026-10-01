"""R4-04 PR C Task 8: the shape of the collection's activation migration-evidence transaction.

The decisions are held equal to the Python CLI by tests/test_activation_transaction_parity.py
and exercised against a fake hub by tests/integration/test_activation_transaction_runtime.py;
these structural checks pin the ordering and preview rules the YAML owns.
"""

import pathlib

import yaml

TASKS = pathlib.Path(__file__).resolve().parents[2] / "roles" / "activation" / "tasks"
MUTATION = "tomazb.acm_switchover.acm_restore_guarded_mutation"
WRITER = "tomazb.acm_switchover.checkpoint_phase"


def _flat(tasks):
    for task in tasks:
        yield task
        for section in ("block", "rescue", "always"):
            yield from _flat(task.get(section) or [])


def _tasks(name):
    return list(_flat(yaml.safe_load((TASKS / name).read_text(encoding="utf-8"))))


def test_the_activation_role_mutates_restores_only_through_the_guarded_module():
    for path in sorted(TASKS.glob("*.yml")):
        for task in _flat(yaml.safe_load(path.read_text(encoding="utf-8")) or []):
            args = task.get("kubernetes.core.k8s")
            if isinstance(args, dict):
                assert args.get("kind") != "Restore", f"{path.name}: {task.get('name')}"
                assert "Restore" not in str(args.get("definition", "")), f"{path.name}: {task.get('name')}"


def test_every_restore_mutation_follows_the_durable_freeze_and_previews_without_a_request():
    tasks = _tasks("activate_restore.yml")
    names = [task.get("name") for task in tasks]
    freeze = names.index("Persist the frozen migration journal before any Restore mutation")
    assert "not (_acm_preview | bool)" in str(tasks[freeze]["when"])
    mutations = [index for index, task in enumerate(tasks) if MUTATION in task]
    assert [tasks[index][MUTATION]["action"] for index in mutations] == ["patch", "delete", "create"]
    for index in mutations:
        task = tasks[index]
        assert freeze < index
        assert task["check_mode"] == "{{ _acm_preview | bool }}"
        assert task["failed_when"] is False and task["no_log"] is True
        assert "ignore_errors" not in task


def test_each_accepted_mutation_is_bound_by_a_post_read_before_its_journal_write():
    tasks = _tasks("activate_restore.yml")
    names = [task.get("name") for task in tasks]
    for mutation, read, bind, persist in (
        (
            "Patch the passive sync Restore under the UID, resourceVersion and value guard",
            "Read the patched Restore strictly",
            "Bind the accepted PATCH and its post-read",
            "Persist the accepted PATCH evidence",
        ),
        (
            "Create the one-shot Restore with one request",
            "Read the created Restore strictly",
            "Bind the accepted create and its post-read",
            "Persist the accepted create evidence",
        ),
    ):
        assert names.index(mutation) < names.index(read) < names.index(bind) < names.index(persist)


def test_the_create_is_preceded_by_a_second_frozen_backup_revalidation():
    names = [task.get("name") for task in _tasks("activate_restore.yml")]
    delete = names.index("Delete the passive sync Restore under its UID and resourceVersion")
    recheck = names.index("Re-read the frozen Backups at the create itself")
    assert delete < recheck < names.index("Create the one-shot Restore with one request")


def test_completed_at_is_the_strictly_last_activation_evidence_write():
    tasks = _tasks("wait_for_restore.yml")
    writes = [task for task in tasks if WRITER in task]
    assert [task[WRITER]["status"] for task in writes] == ["update", "update"]
    bundle, final = writes
    assert bundle[WRITER]["operational_data"]["migration_backups"] == "{{ _acm_completion_bundle.candidate }}"
    assert final[WRITER]["operational_data"]["migration_backups"] == "{{ _acm_completion.candidate }}"
    complete = next(task for task in tasks if task.get("register") == "_acm_completion")
    assert complete["tomazb.acm_switchover.acm_migration_evidence"]["inputs"]["journal"] == (
        "{{ _acm_completion_bundle.candidate }}"
    )
    names = [task.get("name") for task in tasks]
    assert names.index(bundle["name"]) < names.index(complete["name"]) < names.index(final["name"])


def test_a_preview_waits_for_nothing_and_writes_no_evidence():
    tasks = yaml.safe_load((TASKS / "wait_for_restore.yml").read_text(encoding="utf-8"))
    gated = [task for task in tasks if "block" in task]
    assert gated and all("not (_acm_preview | bool)" in str(task["when"]) for task in gated)
