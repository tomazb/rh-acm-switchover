"""Static tests for restore-only recovery and Argo CD persistence contracts."""

import pathlib

import yaml

COLLECTION_ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFLIGHT_TASKS = COLLECTION_ROOT / "roles" / "preflight" / "tasks"
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

    assert "acm_secondary_backups_info.resources" in text, (
        "validate_backups.yml must inspect secondary backup artifacts in restore-only mode"
    )
    assert "restore-only" in text.lower(), (
        "validate_backups.yml should describe the restore-only backup validation path explicitly"
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


def test_activation_checkpoint_persists_argocd_run_id():
    """Activation checkpoint writes must preserve the generated Argo CD run_id."""
    text = (ACTIVATION_TASKS / "main.yml").read_text()
    assert "argocd_run_id:" in text, "activation/main.yml must persist argocd_run_id in checkpoint operational_data"


def test_switchover_report_persists_argocd_run_id():
    """switchover-report.json must include Argo CD pause metadata for later explicit resume."""
    text = (PLAYBOOKS / "switchover.yml").read_text()
    assert "argocd:" in text, "switchover.yml must publish Argo CD metadata into the report contract"
    assert "run_id" in text, "switchover.yml report must carry the generated Argo CD run_id"


def test_restore_only_report_persists_argocd_run_id():
    """restore-only-report.json must include Argo CD pause metadata for later explicit resume."""
    text = (PLAYBOOKS / "restore_only.yml").read_text()
    assert "argocd:" in text, "restore_only.yml must publish Argo CD metadata into the report contract"
    assert "run_id" in text, "restore_only.yml report must carry the generated Argo CD run_id"
