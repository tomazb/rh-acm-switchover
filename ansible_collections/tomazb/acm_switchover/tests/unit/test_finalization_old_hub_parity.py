"""Static parity tests for finalization old-hub behavior."""

import pathlib

import yaml

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
FINALIZATION_TASKS = ROLES_DIR / "finalization" / "tasks"


def _main_block_tasks() -> list[dict]:
    main_tasks = yaml.safe_load((FINALIZATION_TASKS / "main.yml").read_text())
    for task in main_tasks:
        if "block" in task:
            return task["block"]
    raise AssertionError("finalization/main.yml must contain a block of phase tasks")


def test_finalization_main_includes_old_hub_support_tasks():
    """finalization/main.yml must wire the old-hub parity task files."""
    includes = [task.get("ansible.builtin.include_tasks", "") for task in _main_block_tasks()]

    assert "disable_old_hub_observability.yml" in includes
    assert "verify_old_hub_state.yml" in includes
    assert includes.index("disable_old_hub_observability.yml") < includes.index("enable_backups.yml")
    assert includes.index("verify_old_hub_state.yml") > includes.index("handle_old_hub.yml")


def test_finalization_main_disables_old_hub_observability_only_when_observability_enabled():
    """Old-hub observability disablement must honor observability skip state."""
    disable_task = next(
        task
        for task in _main_block_tasks()
        if task.get("ansible.builtin.include_tasks") == "disable_old_hub_observability.yml"
    )
    when_text = "\n".join(disable_task.get("when", []))

    assert "disable_observability_on_secondary" not in when_text
    assert "skip_observability_checks" in when_text
    assert "(acm_switchover_operation.old_hub_action | default('secondary')) == 'secondary'" in when_text


def test_disable_old_hub_observability_deletes_mco_and_waits_for_termination():
    """disable_old_hub_observability.yml must delete MCO, not scale workloads to zero."""
    text = (FINALIZATION_TASKS / "disable_old_hub_observability.yml").read_text()

    assert "kind: MultiClusterObservability" in text
    assert "state: absent" in text
    assert "deleted_mcos" in text
    assert "kind: Pod" in text
    assert "kubernetes.core.k8s_scale" not in text


def test_verify_old_hub_state_checks_clusters_and_backup_schedule():
    """verify_old_hub_state.yml must query ManagedClusters and BackupSchedule on the old hub."""
    text = (FINALIZATION_TASKS / "verify_old_hub_state.yml").read_text()

    assert "kind: ManagedCluster" in text
    assert "ManagedClusterConditionAvailable" in text
    assert "kind: BackupSchedule" in text
    assert "paused" in text


def test_verify_old_hub_state_treats_null_paused_as_enabled():
    """Old-hub regression checks must not treat spec.paused=null as paused."""
    text = (FINALIZATION_TASKS / "verify_old_hub_state.yml").read_text()

    assert ".get('paused', false) | bool" in text


def test_handle_old_hub_decommission_includes_decommission_role():
    """old_hub_action=decommission must execute the collection decommission role."""
    tasks = yaml.safe_load((FINALIZATION_TASKS / "handle_old_hub.yml").read_text())
    decommission_tasks = [
        task
        for task in tasks
        if task.get("ansible.builtin.include_role", {}).get("name") == "tomazb.acm_switchover.decommission"
    ]

    assert decommission_tasks, "handle_old_hub.yml must include the decommission role"
    when_text = "\n".join(decommission_tasks[0].get("when", []))
    assert "(acm_switchover_operation.old_hub_action | default('secondary')) == 'decommission'" in when_text
    assert "acm_switchover_execution.mode | default('dry_run') != 'dry_run'" in when_text


def test_embedded_finalization_decommission_passes_scoped_confirmation():
    """Embedded finalization decommission must confirm only the included role invocation."""
    tasks = yaml.safe_load((FINALIZATION_TASKS / "handle_old_hub.yml").read_text())
    defaults = yaml.safe_load((ROLES_DIR / "decommission" / "defaults" / "main.yml").read_text())

    assert defaults["acm_switchover_decommission"]["confirmed"] is False
    assert not any(
        "acm_switchover_decommission:" in str(task.get("ansible.builtin.set_fact", {})) for task in tasks
    ), "handle_old_hub.yml must not overwrite the global decommission settings"

    settings_task = next(task for task in tasks if task.get("name") == "Build embedded decommission settings")
    settings_fact = settings_task.get("ansible.builtin.set_fact", {})
    settings_value = settings_fact.get("_acm_switchover_embedded_decommission", "")
    assert "acm_switchover_decommission | default({}, true)" in settings_value
    assert "combine({'confirmed': true}, recursive=True)" in settings_value

    decommission_task = next(
        task
        for task in tasks
        if task.get("ansible.builtin.include_role", {}).get("name") == "tomazb.acm_switchover.decommission"
    )
    assert "acm_switchover_decommission" in decommission_task.get("vars", {})

    settings_when = "\n".join(settings_task.get("when", []))
    assert "acm_switchover_execution.mode | default('dry_run') != 'dry_run'" in settings_when


def test_old_hub_disposition_uses_safe_defaults_and_real_decommission_changed_state():
    """Disposition changed must be safe for skipped branches and preserve decommission idempotence."""
    text = (FINALIZATION_TASKS / "handle_old_hub.yml").read_text()

    assert "register: _old_hub_decommission_role_result" in text
    assert "((_old_hub_restore_applied | default({})).changed | default(false))" in text
    assert "((_old_hub_decommission_role_result | default({})).changed | default(false))" in text
    assert "acm_switchover_decommission_result.status | default('')) == 'pass'" not in text


def test_decommission_disposition_message_requires_completed_live_decommission():
    """Finalization must not report completed decommission for dry-run or skipped role paths."""
    text = (FINALIZATION_TASKS / "handle_old_hub.yml").read_text()

    assert "_old_hub_decommission_completed" in text
    assert "((acm_switchover_decommission_result | default({})).status | default('')) == 'pass'" in text
    assert "Old hub decommission completed" in text
    assert "Old hub scheduled for decommission" in text
