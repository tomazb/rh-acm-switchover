"""Static contract for positive-evidence hub connectivity decisions."""

from __future__ import annotations

from pathlib import Path

import yaml

TASK_FILE = Path(__file__).resolve().parents[2] / "roles" / "preflight" / "tasks" / "validate_kubeconfigs.yml"


def _tasks() -> list[dict]:
    return yaml.safe_load(TASK_FILE.read_text(encoding="utf-8"))


def test_connectivity_pass_requires_exact_default_namespace_evidence():
    tasks = _tasks()
    probes = [
        task
        for task in tasks
        if task.get("kubernetes.core.k8s_info", {}).get("kind") == "Namespace"
        and task.get("kubernetes.core.k8s_info", {}).get("name") == "default"
    ]
    assert len(probes) == 2
    assert all(task.get("failed_when") is False for task in probes)
    assert all(task.get("no_log") is True for task in probes)

    evidence_facts = {
        key: str(value)
        for task in tasks
        for key, value in task.get("ansible.builtin.set_fact", {}).items()
        if key
        in {
            "_acm_primary_connectivity_verified",
            "_acm_secondary_connectivity_verified",
        }
    }
    assert set(evidence_facts) == {
        "_acm_primary_connectivity_verified",
        "_acm_secondary_connectivity_verified",
    }

    for expression in evidence_facts.values():
        assert "is mapping" in expression
        assert "api_found is defined" in expression
        assert "api_found == true" in expression
        assert "resources is defined" in expression
        assert "resources | type_debug" in expression
        assert "'list'" in expression
        assert "resources | length) == 1" in expression
        assert "resources[0] is mapping" in expression
        assert ".kind" in expression
        assert "'Namespace'" in expression
        assert ".metadata is mapping" in expression
        assert ".metadata.name" in expression
        assert "'default'" in expression
        assert ".failed" not in expression
        assert "is failed" not in expression

    result_tasks = [task for task in tasks if "connectivity result from direct API probe" in task.get("name", "")]
    assert len(result_tasks) == 2
    result_text = str(result_tasks)
    assert "_acm_primary_connectivity_verified" in result_text
    assert "_acm_secondary_connectivity_verified" in result_text
    assert ".failed" not in result_text
    assert "is failed" not in result_text
