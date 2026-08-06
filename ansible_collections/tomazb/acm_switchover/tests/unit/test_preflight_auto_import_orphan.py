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


def test_orphan_warning_uses_marker_and_import_and_sync():
    text = ORPHAN_FILE.read_text()
    assert "acm-switchover.open-cluster-management.io/import-strategy-set-by" in text
    assert "ImportAndSync" in text
    assert "preflight-auto-import-orphan" in text
    assert "acm_switchover_validation_results" in text


def test_preflight_main_includes_orphan_check_before_discovery():
    main = yaml.safe_load((PREFLIGHT_TASKS / "main.yml").read_text())
    includes = [t.get("ansible.builtin.include_tasks", "") for t in _flatten(main)]
    assert "check_auto_import_orphan.yml" in includes
    assert includes.index("check_auto_import_orphan.yml") < includes.index("discover_resources.yml")
