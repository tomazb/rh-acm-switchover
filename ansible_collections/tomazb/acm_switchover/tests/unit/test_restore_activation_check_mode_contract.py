"""Contract: activate_restore publishes a truthful changed under native check mode (Thermos R2-M1 part 2)."""

from pathlib import Path

ROLES_DIR = Path(__file__).resolve().parents[2] / "roles"
ACTIVATE_RESTORE = ROLES_DIR / "activation" / "tasks" / "activate_restore.yml"


def test_activate_restore_published_changed_covers_native_check_mode():
    text = ACTIVATE_RESTORE.read_text()
    assert (
        "ansible_check_mode" in text
    ), "activate_restore.yml published changed must treat native check mode like dry_run"
