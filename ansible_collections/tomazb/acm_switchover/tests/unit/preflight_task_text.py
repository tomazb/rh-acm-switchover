"""Helpers for static preflight task text contract tests."""

import pathlib

PREFLIGHT_TASKS = pathlib.Path(__file__).resolve().parents[2] / "roles" / "preflight" / "tasks"


def validate_backups_text() -> str:
    wrapper = PREFLIGHT_TASKS / "validate_backups.yml"
    text_parts = [wrapper.read_text(encoding="utf-8")]
    for fragment_name in (
        "progress.yml",
        "artifacts.yml",
        "schedule_storage.yml",
        "infrastructure.yml",
        "managed_cluster_backups.yml",
    ):
        text_parts.append((PREFLIGHT_TASKS / "validate_backups" / fragment_name).read_text(encoding="utf-8"))
    return "\n".join(text_parts)
