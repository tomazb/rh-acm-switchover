"""Static tests for restore-only recovery and Argo CD persistence contracts."""

import pathlib

import yaml
from preflight_task_text import validate_backups_text

COLLECTION_ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFLIGHT_TASKS = COLLECTION_ROOT / "roles" / "preflight" / "tasks"
PRIMARY_PREP_TASKS = COLLECTION_ROOT / "roles" / "primary_prep" / "tasks"
ACTIVATION_TASKS = COLLECTION_ROOT / "roles" / "activation" / "tasks"
PLAYBOOKS = COLLECTION_ROOT / "playbooks"


def _load_playbook(name: str) -> list[dict]:
    return yaml.safe_load((PLAYBOOKS / name).read_text())


def test_restore_only_discovers_secondary_backups():
    """Restore-only preflight must gather backup artifacts from the target hub."""
    text = (PREFLIGHT_TASKS / "discover_resources.yml").read_text()

    assert "register: acm_secondary_backups_info" in text, (
        "discover_resources.yml must query secondary Backup resources so "
        "restore-only preflight can validate backup presence"
    )


def test_restore_only_validates_secondary_backup_artifacts():
    """Restore-only preflight must fail when the target bucket has no synced backups."""
    text = validate_backups_text()

    assert (
        "acm_secondary_backups_info.resources" in text
    ), "validate_backups.yml must inspect secondary backup artifacts in restore-only mode"
    assert (
        "restore-only" in text.lower()
    ), "validate_backups.yml should describe the restore-only backup validation path explicitly"


def test_switchover_playbook_rejects_restore_only_mode_before_roles():
    """The full switchover playbook must not run primary_prep for restore-only requests."""
    play = _load_playbook("switchover.yml")[0]
    pre_tasks = play.get("pre_tasks", [])

    fail_tasks = [task for task in pre_tasks if task.get("name") == "Reject restore-only mode in switchover playbook"]

    assert fail_tasks, "switchover.yml must fail before roles when restore_only=true"
    fail_task = fail_tasks[0]
    assert "ansible.builtin.fail" in fail_task
    assert "restore_only" in str(fail_task["ansible.builtin.fail"].get("msg", ""))
    assert "restore_only.yml" in str(fail_task["ansible.builtin.fail"].get("msg", ""))
    assert "(acm_switchover_operation | default({})).restore_only | default(false) | bool" in str(
        fail_task.get("when", "")
    )


def test_verify_passive_sync_passes_activation_method_to_restore_selector():
    """Passive activation resume must let acm_restore_info see activation_method=restore."""
    tasks = yaml.safe_load((ACTIVATION_TASKS / "verify_passive_sync.yml").read_text())
    selector_tasks = [t for t in tasks if "tomazb.acm_switchover.acm_restore_info" in t]
    assert selector_tasks, "verify_passive_sync.yml must call acm_restore_info"

    selector = selector_tasks[0]["tomazb.acm_switchover.acm_restore_info"]
    assert "activation_method" in selector, (
        "verify_passive_sync.yml must pass activation_method through to acm_restore_info "
        "so reruns can recognize restore-acm-activate"
    )


def test_verify_passive_sync_requires_passive_restore_candidate():
    """Passive activation precheck requires sync-enabled restore for patch mode and initial option-B runs."""
    text = (ACTIVATION_TASKS / "verify_passive_sync.yml").read_text()
    assert "sync_enabled_count" in text, (
        "verify_passive_sync.yml must check sync_enabled_count so that patch-mode runs "
        "and initial option-B runs (before activation restore is created) require a passive sync restore"
    )
    assert "allow_conventional_passive_restore_fallback" in text, (
        "verify_passive_sync.yml must put conventional-name passive restore compatibility "
        "behind an explicit opt-in variable"
    )


def test_verify_passive_sync_skips_assert_for_option_b_resume_state():
    """verify_passive_sync.yml must not fail when activation_method=restore and only restore-acm-activate exists."""
    tasks = yaml.safe_load((ACTIVATION_TASKS / "verify_passive_sync.yml").read_text())

    assert_tasks = [t for t in tasks if "ansible.builtin.assert" in t]
    assert assert_tasks, "verify_passive_sync.yml must contain a passive restore assert"

    assert_task = assert_tasks[0]
    when_clause = str(assert_task.get("when", ""))
    assert "_acm_activation_restore_resume" in when_clause, (
        "The passive restore assert must be guarded by the _acm_activation_restore_resume flag "
        "so that option-B resume (only restore-acm-activate present) is not rejected"
    )
    assert (
        "not" in when_clause
    ), "The passive restore assert must be skipped (not asserted) when in the option-B resume state"
    assert "not (_acm_activation_restore_resume | bool)" in when_clause, (
        "The passive restore assert must boolean-cast _acm_activation_restore_resume; "
        "without | bool, a string-valued Ansible fact can take the wrong truthiness path"
    )


def test_verify_passive_sync_publishes_activation_restore_in_resume_state():
    """verify_passive_sync.yml must publish restore-acm-activate as selection for option-B resume."""
    text = (ACTIVATION_TASKS / "verify_passive_sync.yml").read_text()
    assert "restore-acm-activate" in text, (
        "verify_passive_sync.yml must reference restore-acm-activate to handle the resume state "
        "where only the activation Restore is present"
    )
    assert "_acm_activation_restore_resume" in text, (
        "verify_passive_sync.yml must set a resume-state flag to guard the passive-sync assert "
        "and choose the correct publish task"
    )


def test_activation_checkpoint_persists_argocd_run_id():
    """Activation checkpoint writes must preserve the generated Argo CD run_id."""
    text = (ACTIVATION_TASKS / "main.yml").read_text()
    assert "argocd_run_id:" in text, "activation/main.yml must persist argocd_run_id in checkpoint operational_data"


def test_activation_checkpoint_defaults_checkpoint_enter_before_discovery_namespace_reads():
    """Activation checkpoint writes must not dereference an undefined _checkpoint_enter."""
    text = (ACTIVATION_TASKS / "main.yml").read_text()
    assert "(((_checkpoint_enter | default({})) or {}).get('checkpoint', {}) or {})" in text


def test_activation_wait_rejects_stale_velero_restore_signal():
    """Activation wait must require a new managed-clusters Velero restore name when one existed before activation."""
    tasks = yaml.safe_load((ACTIVATION_TASKS / "wait_for_restore.yml").read_text())
    wait_task = next(t for t in tasks if t.get("name") == "Wait for managed-clusters Velero restore to be created")
    stale_asserts = [t for t in tasks if t.get("name") == "Fail when managed-clusters Velero restore signal is stale"]

    assert "previous_velero_restore_name" in wait_task["until"], (
        "wait_for_restore.yml must keep polling while the Restore still reports the "
        "pre-activation managed-clusters Velero restore signal"
    )
    assert "!=" in wait_task["until"]
    assert stale_asserts == []


def test_restore_wait_uses_exact_benign_finished_with_errors_matching():
    """FinishedWithErrors compatibility must not use loose substring matching."""
    text = (ACTIVATION_TASKS / "wait_for_restore.yml").read_text()

    assert "match', '^ManagedCluster [^ ]+ already available$'" in text
    assert "reject('search', 'already available')" not in text


def test_preflight_skipped_checkpoint_requires_expected_managedcluster_metadata():
    """Skipped preflight must not silently downgrade expected ManagedCluster enforcement to 0."""
    tasks = yaml.safe_load((PREFLIGHT_TASKS / "main.yml").read_text())
    restore_index = next(
        idx
        for idx, task in enumerate(tasks)
        if task.get("name") == "Restore operational facts from checkpoint when preflight is skipped"
    )
    assert restore_index > 0

    validation_task = tasks[restore_index - 1]
    assert validation_task.get("name") == "Validate required checkpoint data when preflight is skipped"
    validation = validation_task["ansible.builtin.assert"]
    validation_text = "\n".join(validation["that"])
    assert "expected_managed_cluster_names" in validation_text
    assert "expected_managed_cluster_count" in validation_text

    restored_values = yaml.dump(tasks[restore_index]["ansible.builtin.set_fact"])
    assert ".get('expected_managed_cluster_names', [])" not in restored_values
    assert ".get('expected_managed_cluster_count', 0)" not in restored_values


def test_primary_prep_checkpoint_persists_argocd_run_id():
    """primary_prep checkpoint writes must preserve the generated Argo CD run_id."""
    text = (PRIMARY_PREP_TASKS / "main.yml").read_text()
    assert "argocd_run_id:" in text, "primary_prep/main.yml must persist argocd_run_id in checkpoint operational_data"


def test_primary_prep_checkpoint_persists_argocd_discovery_namespaces():
    """primary_prep checkpoint writes must preserve trusted per-hub Application namespace hints."""
    text = (PRIMARY_PREP_TASKS / "main.yml").read_text()
    assert (
        "argocd_discovery_namespaces:" in text
    ), "primary_prep/main.yml must persist argocd_discovery_namespaces in checkpoint operational_data"


def test_primary_prep_defaults_checkpoint_enter_before_discovery_namespace_rehydrate():
    """primary_prep must guard _checkpoint_enter before rehydrating discovery namespaces."""
    text = (PRIMARY_PREP_TASKS / "main.yml").read_text()
    assert "(((_checkpoint_enter | default({})) or {}).get('checkpoint', {}) or {})" in text


def test_switchover_report_persists_argocd_run_id():
    """switchover-report.json must include Argo CD pause metadata for later explicit resume."""
    text = (PLAYBOOKS / "switchover.yml").read_text()
    assert "argocd:" in text, "switchover.yml must publish Argo CD metadata into the report contract"
    assert "run_id" in text, "switchover.yml report must carry the generated Argo CD run_id"


def test_switchover_report_uses_validated_report_writer():
    """switchover.yml must route the final report through the validated writer module."""
    text = (PLAYBOOKS / "switchover.yml").read_text()
    assert (
        "tomazb.acm_switchover.acm_report_artifact" in text
    ), "switchover.yml must use acm_report_artifact for the final report write"
    assert "ansible.builtin.copy" not in text, "switchover.yml should not use raw copy for final report artifacts"
    assert "ansible.builtin.file" not in text, "switchover.yml should not mkdir final report artifacts directly"


def test_restore_only_report_persists_argocd_run_id():
    """restore-only-report.json must include Argo CD pause metadata for later explicit resume."""
    text = (PLAYBOOKS / "restore_only.yml").read_text()
    assert "argocd:" in text, "restore_only.yml must publish Argo CD metadata into the report contract"
    assert "run_id" in text, "restore_only.yml report must carry the generated Argo CD run_id"


def test_restore_only_report_uses_validated_report_writer():
    """restore_only.yml must route the final report through the validated writer module."""
    text = (PLAYBOOKS / "restore_only.yml").read_text()
    assert (
        "tomazb.acm_switchover.acm_report_artifact" in text
    ), "restore_only.yml must use acm_report_artifact for the final report write"
    assert "ansible.builtin.copy" not in text, "restore_only.yml should not use raw copy for final report artifacts"
    assert "ansible.builtin.file" not in text, "restore_only.yml should not mkdir final report artifacts directly"


def test_restore_only_persists_argocd_run_id_in_checkpoint_after_pause():
    """restore_only.yml must persist the Argo CD pause run_id before activation starts."""
    text = (PLAYBOOKS / "restore_only.yml").read_text()
    assert "checkpoint_phase" in text, "restore_only.yml must update the checkpoint after Argo CD pause"
    assert (
        "operational_data" in text and "argocd_run_id" in text
    ), "restore_only.yml must persist operational_data.argocd_run_id for standalone argocd_resume.yml"


def test_restore_only_persists_argocd_discovery_namespaces_in_checkpoint_after_pause():
    """restore_only.yml must persist trusted namespace hints alongside the Argo CD run_id."""
    text = (PLAYBOOKS / "restore_only.yml").read_text()
    assert (
        "argocd_discovery_namespaces:" in text
    ), "restore_only.yml must persist operational_data.argocd_discovery_namespaces for resume/retry discovery"


def test_restore_only_rehydrates_argocd_run_id_from_checkpoint_before_pause():
    """Retrying restore-only must not generate a new run_id while old markers remain."""
    text = (PLAYBOOKS / "restore_only.yml").read_text()
    pause_index = text.index("Pause Argo CD auto-sync on secondary hub before restore")
    rehydrate_index = text.find("Rehydrate Argo CD run_id from checkpoint before pause")

    assert rehydrate_index != -1
    assert rehydrate_index < pause_index
    assert "operational_data" in text
    assert "argocd_run_id" in text


def test_restore_only_rehydrates_discovery_namespaces_from_checkpoint_before_pause():
    """Retrying restore-only must reuse persisted Application namespace hints before pause."""
    text = (PLAYBOOKS / "restore_only.yml").read_text()
    pause_index = text.index("Pause Argo CD auto-sync on secondary hub before restore")
    rehydrate_index = text.find("Rehydrate Argo CD discovery namespaces from checkpoint before pause")

    assert rehydrate_index != -1
    assert rehydrate_index < pause_index
    assert "argocd_discovery_namespaces" in text


def test_restore_only_validates_rehydrated_discovery_namespace_lists():
    """Malformed checkpoint namespace hints must fail before restore-only Argo CD pause."""
    text = (PLAYBOOKS / "restore_only.yml").read_text()
    assert "Validate rehydrated Argo CD discovery namespaces from checkpoint" in text
    validate_index = text.find("Validate rehydrated Argo CD discovery namespaces from checkpoint")
    rehydrate_index = text.find("Rehydrate Argo CD discovery namespaces from checkpoint before pause")
    assert validate_index != -1
    assert validate_index < rehydrate_index
    assert "(item.value | type_debug) == 'list'" in text


def test_restore_only_rehydrate_is_guarded_by_checkpoint_enablement():
    """Restore-only rehydrate must skip cleanly when checkpointing is disabled."""
    tasks = _load_playbook("restore_only.yml")[0]["tasks"][0]["block"]
    rehydrate_task = next(
        task for task in tasks if task.get("name") == "Rehydrate Argo CD run_id from checkpoint before pause"
    )
    when_text = " ".join(str(item) for item in rehydrate_task.get("when", []))
    fact_text = str(rehydrate_task["ansible.builtin.set_fact"]["acm_switchover_argocd"])

    assert "acm_switchover_execution.checkpoint.enabled | default(false)" in when_text
    assert "((_checkpoint_enter | default({})) or {})" in fact_text


def test_restore_only_does_not_default_to_zero_managed_clusters():
    """Restore-only must not silently pass with local-cluster or zero restored ManagedClusters."""
    text = (PLAYBOOKS / "restore_only.yml").read_text()

    assert "'min_managed_clusters': 0" not in text
    assert "allow_zero_managed_clusters" in text
    assert "default(false)" in text


def test_argocd_manage_test_only_writes_summary_when_requested():
    """The Argo CD integration-test playbook should tolerate omitted summary_path."""
    text = (PLAYBOOKS / "argocd_manage_test.yml").read_text()
    assert text.count("when: summary_path is defined") >= 2, (
        "argocd_manage_test.yml should guard summary-path resolution and file write "
        "so the playbook still runs when summary_path is omitted"
    )


def test_argocd_manage_test_validates_summary_path_before_write():
    """The optional Argo CD test summary path must use the collection safe-path validator."""
    tasks = _load_playbook("argocd_manage_test.yml")[0]["tasks"]
    validate_indices = [idx for idx, task in enumerate(tasks) if "tomazb.acm_switchover.acm_safe_path_validate" in task]
    write_indices = [idx for idx, task in enumerate(tasks) if task.get("name") == "Write summary file"]

    assert validate_indices, "argocd_manage_test.yml must validate summary_path before writing it"
    assert write_indices, "argocd_manage_test.yml must still write the requested summary file"
    assert validate_indices[0] < write_indices[0]
