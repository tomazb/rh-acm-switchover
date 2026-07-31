"""Contract tests for preflight backup selection fallbacks."""

from pathlib import Path

BACKUP_TASK_DIR = Path(__file__).resolve().parents[2] / "roles" / "preflight" / "tasks" / "validate_backups"


def test_primary_backup_selector_falls_back_without_creation_timestamp():
    text = (BACKUP_TASK_DIR / "artifacts.yml").read_text(encoding="utf-8")

    assert "primary_backups_with_timestamps" in text
    assert "primary_backups | selectattr('metadata.creationTimestamp', 'defined') | list" in text
    assert "else (primary_backups | last | default({}))" in text


def test_secondary_backup_selector_falls_back_without_creation_timestamp():
    text = (BACKUP_TASK_DIR / "artifacts.yml").read_text(encoding="utf-8")

    assert "secondary_backups_with_timestamps" in text
    assert "secondary_backups | selectattr('metadata.creationTimestamp', 'defined') | list" in text
    assert "else (secondary_backups | last | default({}))" in text


def test_managed_cluster_backup_selector_falls_back_without_creation_timestamp():
    text = (BACKUP_TASK_DIR / "managed_cluster_backups.yml").read_text(encoding="utf-8")

    assert "backups_with_timestamps" in text
    assert "backups | selectattr('metadata.creationTimestamp', 'defined') | list" in text
    assert "else (backups | last | default({}))" in text
