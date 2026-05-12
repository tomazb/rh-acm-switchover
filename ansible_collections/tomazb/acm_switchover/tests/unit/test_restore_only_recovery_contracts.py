"""Static tests for restore-only recovery and Argo CD persistence contracts."""

import pathlib

import yaml

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
    text = (PREFLIGHT_TASKS / "validate_backups.yml").read_text()

    assert (
        "acm_secondary_backups_info.resources" in text
    ), "validate_backups.yml must inspect secondary backup artifacts in restore-only mode"
    assert (
        "restore-only" in text.lower()
    ), "validate_backups.yml should describe the restore-only backup validation path explicitly"


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
    """Passive activation precheck must reject stale restore-acm-activate objects."""
    text = (ACTIVATION_TASKS / "verify_passive_sync.yml").read_text()
    assert "sync_enabled_count" in text, (
        "verify_passive_sync.yml must still reject stale activation restores "
        "when no passive restore candidate is present"
    )
    assert "conventional_name_fallback" in text, (
        "verify_passive_sync.yml must accept the conventional passive restore name "
        "when ACM omits spec.syncRestoreWithNewBackups"
    )


def test_activation_checkpoint_persists_argocd_run_id():
    """Activation checkpoint writes must preserve the generated Argo CD run_id."""
    text = (ACTIVATION_TASKS / "main.yml").read_text()
    assert "argocd_run_id:" in text, "activation/main.yml must persist argocd_run_id in checkpoint operational_data"


def test_primary_prep_checkpoint_persists_argocd_run_id():
    """primary_prep checkpoint writes must preserve the generated Argo CD run_id."""
    text = (PRIMARY_PREP_TASKS / "main.yml").read_text()
    assert "argocd_run_id:" in text, "primary_prep/main.yml must persist argocd_run_id in checkpoint operational_data"


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
