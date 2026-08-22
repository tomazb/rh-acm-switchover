"""Static contract tests for the preflight identity barrier split."""

import pathlib

import yaml

PREFLIGHT_TASKS = pathlib.Path(__file__).resolve().parents[2] / "roles" / "preflight" / "tasks"
PREFLIGHT_MAIN = PREFLIGHT_TASKS / "main.yml"
IDENTITY_BARRIER = PREFLIGHT_TASKS / "identity_barrier.yml"
POST_IDENTITY = PREFLIGHT_TASKS / "post_identity.yml"


def _load_tasks(path: pathlib.Path) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_preflight_composes_identity_barrier_before_post_identity_work():
    """A caller cannot reach checkpoint-dependent work before the barrier."""
    tasks = _load_tasks(PREFLIGHT_MAIN)

    assert [task.get("ansible.builtin.include_tasks") for task in tasks] == [
        "identity_barrier.yml",
        "post_identity.yml",
    ]


def test_identity_barrier_validates_inputs_before_unconditional_literal_action():
    """Invalid input reports and stops; every continuing run reaches the action."""
    tasks = _load_tasks(IDENTITY_BARRIER)
    action_index = next(index for index, task in enumerate(tasks) if "tomazb.acm_switchover.checkpoint_phase" in task)
    action = tasks[action_index]

    assert [task["name"] for task in tasks[:action_index]] == [
        "Initialize preflight result accumulator",
        "Validate controller-side inputs",
        "Persist report and summary facts for invalid controller-side input",
        "Stop on invalid controller-side input",
    ]
    report_task, failure_task = tasks[action_index - 2 : action_index]
    assert report_task["ansible.builtin.include_tasks"] == "write_report.yml"
    assert report_task["when"] == "not acm_switchover_preflight_summary.passed"
    assert failure_task["ansible.builtin.fail"]["msg"] == (
        "Preflight failed with {{ acm_switchover_preflight_summary.critical_failures }} critical finding(s). "
        "See the structured preflight report artifact for details."
    )
    assert failure_task["when"] == "not acm_switchover_preflight_summary.passed"
    assert action["tomazb.acm_switchover.checkpoint_phase"] == {
        "identity_barrier": True,
        "phase": "preflight",
        "status": "enter",
        "checkpoint": "{{ acm_switchover_execution.checkpoint | default({}) }}",
        "hubs": "{{ acm_switchover_hubs | default({}) }}",
        "operation": "{{ acm_switchover_operation | default({}) }}",
        "execution": "{{ acm_switchover_execution | default({}) }}",
        "test_overrides": "{{ acm_switchover_test_overrides | default({}) }}",
        "collection_version": "{{ acm_switchover_collection_version | default('') }}",
    }
    assert action["register"] == "_checkpoint_enter"
    assert "when" not in action


def test_only_post_identity_consumes_checkpoint_control_results():
    """Only post-barrier work may use resume facts or skipped-phase control flow."""
    main_text = PREFLIGHT_MAIN.read_text(encoding="utf-8")
    barrier_text = IDENTITY_BARRIER.read_text(encoding="utf-8")
    post_text = POST_IDENTITY.read_text(encoding="utf-8")

    for text in (main_text, barrier_text):
        assert "_checkpoint_enter.skipped_phase" not in text
        assert "_checkpoint_enter | default({})).get('facts'" not in text
        assert "cluster_uid" not in text
        assert "operation_identity" not in text
        assert "_acm_primary_identity_namespace" not in text
        assert "_acm_secondary_identity_namespace" not in text

    assert "_checkpoint_enter | default({})).skipped_phase" in post_text
    assert "_checkpoint_enter | default({})).get('facts', {})" in post_text
    assert "cluster_uid" not in post_text
    assert "operation_identity" not in post_text
    assert "_acm_primary_identity_namespace" not in post_text
    assert "_acm_secondary_identity_namespace" not in post_text
