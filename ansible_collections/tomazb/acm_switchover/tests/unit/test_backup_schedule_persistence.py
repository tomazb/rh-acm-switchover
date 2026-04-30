"""Tests for BackupSchedule persistence across Ansible phase handoff."""

from pathlib import Path

ROLES_DIR = Path(__file__).resolve().parents[2] / "roles"
PRIMARY_PREP_TASKS = ROLES_DIR / "primary_prep" / "tasks"


def test_pause_backups_captures_saved_backup_schedule_body():
    """primary_prep must persist a reusable BackupSchedule body before pause/delete."""
    text = (PRIMARY_PREP_TASKS / "pause_backups.yml").read_text()
    assert (
        "acm_switchover_saved_backup_schedule" in text
    ), "pause_backups.yml must store the current BackupSchedule body for later recreation"


def test_backup_schedule_version_derivation_does_not_default_to_212():
    """Missing MCH versions must fail planning instead of assuming ACM 2.12 semantics."""
    primary_text = (PRIMARY_PREP_TASKS / "pause_backups.yml").read_text()
    finalization_text = (ROLES_DIR / "finalization" / "tasks" / "enable_backups.yml").read_text()

    assert "currentVersion', '2.12.0'" not in primary_text
    assert "currentVersion', '2.12.0'" not in finalization_text


def test_primary_prep_persists_saved_backup_schedule_in_checkpoint_operational_data():
    """checkpointed primary_prep runs must carry the saved BackupSchedule across resume."""
    text = (PRIMARY_PREP_TASKS / "main.yml").read_text()
    assert "operational_data:" in text
    assert (
        "saved_backup_schedule" in text
    ), "primary_prep/main.yml must write saved_backup_schedule into checkpoint operational_data"
