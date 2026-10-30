"""R4-04 PR C Task 8a: the Argo CD failure rescue under a migration journal.

PR C decisions amendment section 2.2 and #332 (N5, N6) on the collection's YAML: the
play rescues, the failed roles' fail transitions, the retry re-pause and the direct role
entry guards. Runtime behavior is covered by tests/integration/test_journal_rescue_runtime.py.
"""

import pathlib

import pytest
import yaml
from yaml_contract_helpers import _workflow_entry_sections

COLLECTION = pathlib.Path(__file__).resolve().parents[2]
PLAYBOOKS = COLLECTION / "playbooks"
ROLES = COLLECTION / "roles"
CHECKPOINT = "tomazb.acm_switchover.checkpoint_phase"
INCLUDE_ROLE = "ansible.builtin.include_role"
PHASE_ROLES = ("activation", "post_activation", "finalization")


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _when(task) -> str:
    when = task.get("when", [])
    return " ".join(str(item) for item in when) if isinstance(when, list) else str(when)


def _switchover_rescue():
    tasks = _load(PLAYBOOKS / "switchover.yml")[0]["tasks"]
    outer = next(task for task in tasks if task.get("name") == "Run switchover phases with reporting")
    nested = next(task for task in outer["block"] if task.get("name") == "Run post-barrier switchover phases")
    return outer, nested["rescue"]


def _restore_only_outer():
    tasks = _load(PLAYBOOKS / "restore_only.yml")[0]["tasks"]
    return next(task for task in tasks if "block" in task)


def _restore_only_block():
    """The post-identity block that carries the resume-enabled rescue."""
    return next(task for task in _restore_only_outer()["block"] if "rescue" in task)


def _named(tasks, name):
    (task,) = [task for task in tasks if task.get("name") == name]
    return task


@pytest.mark.parametrize("playbook", ["switchover", "restore_only"])
def test_the_rescue_never_resets_a_phase_under_a_journal(playbook):
    rescue = _switchover_rescue()[1] if playbook == "switchover" else _restore_only_block()["rescue"]
    resets = [task for task in _walk(rescue) if task.get(CHECKPOINT, {}).get("status") == "reset"]
    for task in resets:
        assert "not (_acm_rescue_under_journal | bool)" in _when(task)
    decide = _named(rescue, "Decide whether a migration journal governs the failure rescue")
    fact = decide["ansible.builtin.set_fact"]["_acm_rescue_under_journal"]
    # Checkpoint slot presence decides (an invalid journal is present); unreadable is journal.
    assert "migration_journal_present', true" in fact
    assert "checkpoint.enabled" in fact


@pytest.mark.parametrize("playbook, hubs", [("switchover", ["secondary", "primary"]), ("restore_only", ["secondary"])])
def test_the_journal_resume_is_visible_and_gated(playbook, hubs):
    rescue = _switchover_rescue()[1] if playbook == "switchover" else _restore_only_block()["rescue"]
    (task,) = [t for t in rescue if t.get(INCLUDE_ROLE, {}).get("tasks_from") == "journal_rescue"]
    assert "ignore_errors" not in task
    assert task["vars"]["_argocd_rescue_hubs"] == hubs
    when = _when(task)
    for guard in ("_acm_rescue_under_journal | bool", "not (_argocd_repause_failed", "manage", "resume_on_failure"):
        assert guard in when
    for journal_free in (
        t for t in rescue if t.get(INCLUDE_ROLE, {}).get("name") and "tasks_from" not in t[INCLUDE_ROLE]
    ):
        assert "not (_acm_rescue_under_journal | bool)" in _when(journal_free)
    final = rescue[-1]["ansible.builtin.fail"]["msg"]
    assert "_failure }}" in final and "_argocd_rescue_outcome" in final


def test_the_journal_rescue_resumes_only_with_a_durable_marker():
    tasks = _load(ROLES / "argocd_manage" / "tasks" / "journal_rescue.yml")
    assert not [task for task in _walk(tasks) if "ignore_errors" in task]
    execute = _named(tasks, "Resume Argo CD under the migration journal")
    assert "!= 'execute' or ansible_check_mode" in _when(
        _named(tasks, "Report the Argo CD rescue a preview would perform")
    )
    resolve = _named(execute["block"], "Resolve the durable re-pause marker")["ansible.builtin.set_fact"]
    assert "if (_acm_repause_marker_durable | default(false) | bool)" in resolve["_argocd_rescue_phase"]
    resume = _named(execute["block"], "Resume the paused Applications")
    assert _when(resume) == "_argocd_rescue_phase | length > 0"
    assert _when(_named(execute["block"], "Refuse the resume without a durable re-pause marker")) == (
        "_argocd_rescue_phase | length == 0"
    )
    assert "rescue" in resume
    assert not [t for t in _walk(tasks) if CHECKPOINT in t]


def test_the_retry_repause_clears_the_marker_only_after_every_hub_paused():
    tasks = _load(ROLES / "argocd_manage" / "tasks" / "repause_recovery.yml")
    assert not [task for task in _walk(tasks) if "ignore_errors" in task]
    block = _named(tasks, "Re-pause Argo CD before the recorded phase continues")
    names = [task["name"] for task in block["block"]]
    assert names[-1] == "Clear the Argo CD re-pause marker after the re-pause"
    assert block["block"][-1][CHECKPOINT] == {
        "clear_argocd_repause": True,
        "checkpoint": "{{ acm_switchover_execution.checkpoint | default({}) }}",
    }
    pause = _named(block["block"], "Re-pause Argo CD auto-sync on each recorded hub")
    assert pause["vars"]["acm_switchover_argocd_mode_override"] == "pause"
    # Regardless of acm_switchover_features.argocd.manage (amendment 2.2 rule 3).
    assert "manage" not in _when(block) + _when(pause)
    hubs = _named(tasks, "Resolve the recorded Argo CD re-pause obligation")["ansible.builtin.set_fact"]
    assert "['secondary'] if (acm_switchover_operation.restore_only" in hubs["_argocd_repause_hubs"]
    assert "['primary', 'secondary']" in hubs["_argocd_repause_hubs"]
    assert [t["name"] for t in block["rescue"]] == [
        "Record the failed Argo CD re-pause",
        "Fail the retry while the re-pause obligation remains",
    ]
    # A clear whose marker write-back also failed reports the marker indeterminate, never kept.
    message = block["rescue"][-1]["ansible.builtin.fail"]["msg"]
    assert "Unless that failure reports the marker indeterminate, the re-pause marker is kept" in message
    # No rest of primary preparation is replayed: only the pause mode of argocd_manage.
    assert not [t for t in _walk(tasks) if "primary_prep" in str(t.get(INCLUDE_ROLE, ""))]


def test_both_playbooks_repause_after_the_identity_barrier_and_before_phase_work():
    outer, _ = _switchover_rescue()
    _, _, sw_recovery, _ = _workflow_entry_sections(outer)
    restore_outer = _restore_only_outer()
    _, _, restore_recovery, restore_phases = _workflow_entry_sections(restore_outer)
    assert next(t for t in restore_phases["block"] if t.get(INCLUDE_ROLE, {}).get("tasks_from") == "post_identity")[
        INCLUDE_ROLE
    ] == {
        "name": "tomazb.acm_switchover.preflight",
        "tasks_from": "post_identity",
    }
    for task in (sw_recovery, restore_recovery):
        assert task[INCLUDE_ROLE] == {"name": "tomazb.acm_switchover.argocd_manage", "tasks_from": "repause_recovery"}


def test_preflight_main_is_still_the_two_tasks_restore_only_now_names():
    tasks = _load(ROLES / "preflight" / "tasks" / "main.yml")
    assert [task["ansible.builtin.include_tasks"] for task in tasks] == ["identity_barrier.yml", "post_identity.yml"]


@pytest.mark.parametrize("role", PHASE_ROLES)
def test_each_phase_role_repauses_and_guards_its_entry_before_any_work(role):
    tasks = _load(ROLES / role / "tasks" / "main.yml")
    names = [task.get("name") for task in tasks]
    enter = names.index("Enter checkpointed phase")
    recovery = names.index("Re-pause Argo CD recorded by a failure rescue")
    phase = names.index(f"Run {role} phase")
    # The re-pause runs outside the phase rescue: a failed re-pause resumes nothing.
    assert enter < recovery < phase
    assert "rescue" not in tasks[recovery]
    if role == "finalization":
        # Finalization validates direct caller inputs read-only before physical
        # identity discovery or phase entry; ordinary work remains after re-pause.
        guard_index = names.index(f"Guard the {role} role entry")
        identity = next(i for i, task in enumerate(tasks) if task.get(CHECKPOINT, {}).get("identity_barrier"))
        assert guard_index < identity < enter
        assert tasks[identity]["check_mode"] is True
        guard = tasks[guard_index]
        assert guard["vars"]["_checkpoint_enter"] == "{{ acm_switchover_backup_checkpoint }}"
    else:
        # Other phase guards remain the first ordinary work within phase rescue.
        guard = tasks[phase]["block"][0]
        assert guard["vars"] == {"_acm_role_entry_phase": role}
    assert guard["name"] == f"Guard the {role} role entry"
    assert guard["vars"]["_acm_role_entry_phase"] == role
    assert guard["ansible.builtin.include_tasks"] == "{{ role_path }}/../common/tasks/guard_role_entry.yml"


@pytest.mark.parametrize("role", PHASE_ROLES)
def test_each_fail_transition_carries_the_marker_request_and_keeps_the_original_failure(role):
    tasks = _load(ROLES / role / "tasks" / "main.yml")
    rescue = next(task for task in tasks if task.get("name") == f"Run {role} phase")["rescue"]
    assert [task["name"] for task in rescue] == [
        f"Record the original {role} failure",
        "Mark checkpoint failure",
        "Re-raise failure",
    ]
    assert rescue[0]["ansible.builtin.set_fact"]["_acm_repause_marker_durable"] is False
    mark = rescue[1]
    transition, acknowledge = mark["block"]
    # Review finding 1: durability is acknowledged only after the fail transition succeeded.
    assert transition["register"] == "_acm_fail_transition"
    assert acknowledge["ansible.builtin.set_fact"]["_acm_repause_marker_durable"] == (
        "{{ _acm_fail_transition.argocd_repause_recorded | default(false) }}"
    )
    args = transition[CHECKPOINT]
    assert args["status"] == "fail" and args["phase"] == role
    assert args["error"] == "{{ _acm_phase_failure }}"
    assert "argocd.manage" in args["argocd_repause"] and "resume_on_failure" in args["argocd_repause"]
    (report,) = mark["rescue"]
    assert report["ansible.builtin.fail"]["msg"].startswith("{{ _acm_phase_failure }}")
    assert rescue[2]["ansible.builtin.fail"]["msg"] == "{{ _acm_phase_failure }}"
    assert not [task for task in _walk(rescue) if "ignore_errors" in task]


def test_the_entry_guard_validates_inputs_waiver_names_and_the_activation_checkpoint():
    tasks = _load(ROLES / "common" / "tasks" / "guard_role_entry.yml")
    assert not [task for task in _walk(tasks) if "ignore_errors" in task]
    validate = tasks[0]["tomazb.acm_switchover.acm_input_validate"]
    assert set(validate) == {"hubs", "operation", "execution", "features", "decommission"}
    waiver = _named(tasks, "Refuse a ManagedCluster expectation waiver with no expected name to waive")
    assert "waiver_expected_names_empty" in waiver["ansible.builtin.assert"]["fail_msg"]
    assert "expected_managed_cluster_names" in waiver["vars"]["_acm_recorded_names"]
    # Review finding 3: a direct entry restores the recorded expectation, never defaults it away.
    restore = _named(tasks, "Restore the recorded ManagedCluster expectation at a direct role entry")
    assert "acm_switchover_expected_managed_cluster_names is not defined" in _when(restore)
    refuse = _named(tasks, "Refuse a direct role entry without a recorded ManagedCluster expectation")
    assert "['activation', 'post_activation']" in _when(refuse)
    assert "min_managed_clusters" in _when(refuse)
    assert "not ansible_check_mode" in _when(refuse)  # a preview performs no ManagedCluster check
    names = [task.get("name") for task in tasks]
    assert names.index(restore["name"]) < names.index(refuse["name"]) < names.index(waiver["name"])
    checkpoint = _named(tasks, "Require the checkpoint that stores the migration journal")
    assert "_acm_role_entry_phase == 'activation'" in _when(checkpoint)
    assert "not ansible_check_mode" in _when(checkpoint)
