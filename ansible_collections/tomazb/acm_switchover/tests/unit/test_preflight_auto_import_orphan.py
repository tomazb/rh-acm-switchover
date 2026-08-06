"""Contract tests for the preflight auto-import orphan warning (issue #214, audit C3)."""

import pathlib

import yaml

PREFLIGHT_TASKS = pathlib.Path(__file__).resolve().parents[2] / "roles" / "preflight" / "tasks"
ORPHAN_FILE = PREFLIGHT_TASKS / "check_auto_import_orphan.yml"


def _flatten(tasks):
    result = []
    for task in tasks or []:
        result.append(task)
        for key in ("block", "rescue", "always"):
            if key in task:
                result.extend(_flatten(task[key]))
    return result


def test_orphan_check_file_exists():
    assert ORPHAN_FILE.exists()


def test_orphan_check_reads_both_hubs_non_fatally():
    tasks = yaml.safe_load(ORPHAN_FILE.read_text())
    read_task = next(t for t in _flatten(tasks) if "kubernetes.core.k8s_info" in t)
    assert read_task.get("ignore_errors") is True, "read failure must degrade to a warning, not block preflight"
    assert "failed_when" not in read_task, (
        "failed_when: false would force the registered result's failed key to False, "
        "making the read-failure warning unreachable"
    )
    loop_text = str(read_task.get("loop", ""))
    assert "primary" in loop_text and "secondary" in loop_text


def test_orphan_check_omits_empty_context():
    tasks = yaml.safe_load(ORPHAN_FILE.read_text())
    read_task = next(t for t in _flatten(tasks) if "kubernetes.core.k8s_info" in t)
    context_expr = read_task["kubernetes.core.k8s_info"].get("context", "")
    assert "else omit" in context_expr, (
        "context must genuinely omit for both undefined and empty-string values, "
        "not just pass through an empty string to k8s_info"
    )


def test_orphan_check_skips_primary_in_restore_only_mode():
    """restore_only is the documented secondary-only flow (AGENTS.md): the
    primary hub may be entirely undefined, so the loop must not template
    acm_switchover_hubs.primary at all in that mode."""
    tasks = yaml.safe_load(ORPHAN_FILE.read_text())
    read_task = next(t for t in _flatten(tasks) if "kubernetes.core.k8s_info" in t)
    loop_text = str(read_task.get("loop", ""))
    assert "restore_only" in loop_text, "primary probe must be gated on restore_only"
    assert loop_text.index("restore_only") < loop_text.index("primary"), (
        "the restore_only branch must decide whether the primary item is built "
        "before any acm_switchover_hubs.primary reference is evaluated"
    )


def test_orphan_recommendation_matches_reset_target_per_hub():
    """reset_auto_import.yml only targets the secondary (destination) hub, so
    only secondary findings may promise finalization discharge; primary
    findings must recommend manual cleanup."""
    text = ORPHAN_FILE.read_text()
    assert "if item.item.hub == 'secondary'" in text
    assert "manually" in text
    assert "finalization only resets the secondary" in text


def test_orphan_warning_uses_marker_and_import_and_sync():
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
        AUTO_IMPORT_MARKER_ANNOTATION,
    )

    text = ORPHAN_FILE.read_text()
    assert AUTO_IMPORT_MARKER_ANNOTATION in text
    assert "ImportAndSync" in text
    assert "preflight-auto-import-orphan" in text
    assert "acm_switchover_validation_results" in text


def test_preflight_main_includes_orphan_check_before_discovery():
    main = yaml.safe_load((PREFLIGHT_TASKS / "main.yml").read_text())
    includes = [t.get("ansible.builtin.include_tasks", "") for t in _flatten(main)]
    assert "check_auto_import_orphan.yml" in includes
    assert includes.index("check_auto_import_orphan.yml") < includes.index("discover_resources.yml")
