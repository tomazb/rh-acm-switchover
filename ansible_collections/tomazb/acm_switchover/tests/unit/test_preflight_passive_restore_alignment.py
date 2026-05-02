"""Tests that preflight passive-restore validation aligns with activation logic."""

import pathlib

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
PREFLIGHT_TASKS = ROLES_DIR / "preflight" / "tasks"


def test_validate_backups_uses_acm_restore_info_for_passive_check():
    """validate_backups.yml must call acm_restore_info before the passive restore check.

    Without this, preflight uses raw restore count (any restore passes) while
    activation filters to syncRestoreWithNewBackups=True restores only.  This
    mismatch lets preflight pass → primary_prep runs destructive changes →
    activation fails.
    """
    text = (PREFLIGHT_TASKS / "validate_backups.yml").read_text()
    assert (
        "tomazb.acm_switchover.acm_restore_info" in text
    ), "validate_backups.yml must call acm_restore_info to filter passive sync restores"


def test_validate_backups_passive_check_uses_sync_enabled_count():
    """The passive restore validation must check sync_enabled_count, not raw length.

    The acm_restore_info module returns sync_enabled_count (restores with
    syncRestoreWithNewBackups=True).  Using raw length would re-introduce the
    preflight/activation mismatch.
    """
    text = (PREFLIGHT_TASKS / "validate_backups.yml").read_text()
    assert (
        "sync_enabled_count" in text
    ), "validate_backups.yml passive restore check must use sync_enabled_count from acm_restore_info"


def test_validate_backups_accepts_conventional_passive_restore_fallback():
    """Preflight must accept the same conventional passive Restore fallback as activation."""
    text = (PREFLIGHT_TASKS / "validate_backups.yml").read_text()
    assert (
        "acm_preflight_passive_restore_analysis.restore is not none" in text
    ), "validate_backups.yml must treat acm_restore_info.restore as the passive restore authority"


def test_validate_backups_requires_activation_ready_passive_restore_phase():
    """A present passive Restore in phase Unknown must not pass preflight."""
    text = (PREFLIGHT_TASKS / "validate_backups.yml").read_text()
    assert "restore_ready" in text, "validate_backups.yml must require acm_restore_info.restore_ready"
    assert "Wait for the Restore to reach" in text
