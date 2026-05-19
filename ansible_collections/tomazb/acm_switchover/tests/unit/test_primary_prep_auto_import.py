"""Tests for primary_prep disable-auto-import behavior."""

import pathlib

import yaml

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
PRIMARY_PREP_TASKS = ROLES_DIR / "primary_prep" / "tasks"


def _load_yaml(name: str) -> list[dict]:
    return yaml.safe_load((PRIMARY_PREP_TASKS / name).read_text())


def _walk_tasks(tasks: list[dict]):
    for task in tasks:
        yield task
        for key in ("block", "rescue", "always"):
            if key in task:
                yield from _walk_tasks(task[key])


def test_primary_prep_manage_auto_import_patches_managed_clusters():
    """primary_prep must add disable-auto-import annotations before activation."""
    tasks = _load_yaml("manage_auto_import.yml")
    text = (PRIMARY_PREP_TASKS / "manage_auto_import.yml").read_text()

    managed_cluster_queries = [
        task for task in tasks if task.get("kubernetes.core.k8s_info", {}).get("kind") == "ManagedCluster"
    ]
    patch_tasks = [task for task in tasks if "kubernetes.core.k8s" in task]

    assert managed_cluster_queries, "manage_auto_import.yml must query ManagedCluster resources"
    assert patch_tasks, "manage_auto_import.yml must patch ManagedClusters"
    assert "disable-auto-import" in text, "manage_auto_import.yml must add the disable-auto-import annotation"
    assert "local-cluster" in text, "manage_auto_import.yml must exclude local-cluster"


def test_primary_prep_uses_python_thanos_compactor_selector():
    text = (PRIMARY_PREP_TASKS / "discover_resources.yml").read_text()

    assert "app.kubernetes.io/name=thanos-compact" in text
    assert "app.kubernetes.io/name=thanos-compactor" not in text


def test_primary_prep_scales_observability_only_when_primary_observability_was_detected():
    tasks = list(_walk_tasks(_load_yaml("main.yml")))
    scale_tasks = [task for task in tasks if task.get("ansible.builtin.include_tasks") == "scale_observability.yml"]

    assert scale_tasks, "primary_prep must include scale_observability.yml"
    assert "acm_switchover_primary_has_observability" in str(scale_tasks[0].get("when", ""))


def test_scale_observability_fails_when_detected_but_compactor_is_missing():
    tasks = _load_yaml("scale_observability.yml")
    fail_tasks = [
        task
        for task in tasks
        if "Thanos compactor StatefulSet was not found" in str(task.get("ansible.builtin.fail", {}))
    ]

    assert fail_tasks, "detected Observability with no Thanos compactor must fail primary_prep"
    fail_when = str(fail_tasks[0].get("when", ""))
    assert "(acm_primary_compactor_info.resources | default([]) | length) == 0" in fail_when
    assert "acm_switchover_execution.mode" in fail_when


def test_scale_observability_blocks_when_thanos_pods_remain():
    """Collection primary_prep must block when Thanos pods remain after scale-down."""
    text = (PRIMARY_PREP_TASKS / "scale_observability.yml").read_text()
    tasks = _load_yaml("scale_observability.yml")

    pod_queries = [task for task in tasks if task.get("kubernetes.core.k8s_info", {}).get("kind") == "Pod"]
    fail_tasks = [task for task in tasks if "Thanos compactor still has" in str(task.get("ansible.builtin.fail", {}))]

    assert "ansible.builtin.pause" not in text
    assert pod_queries, "scale_observability.yml must query Thanos compactor pods after scaling"
    assert "app.kubernetes.io/name=thanos-compact" in str(pod_queries[0])
    assert pod_queries[0].get("retries") == 30
    assert pod_queries[0].get("delay") == 10
    assert pod_queries[0].get("failed_when") is False
    assert "(acm_primary_compactor_pods_after_scale.resources | default([]) | length) == 0" in str(
        pod_queries[0].get("until", "")
    )
    assert fail_tasks, "remaining Thanos compactor pods must fail primary_prep"
    fail_when = str(fail_tasks[0].get("when", ""))
    assert "acm_primary_compactor_pods_after_scale is defined" in fail_when
    assert "acm_primary_compactor_scale is defined" in fail_when
    assert "acm_primary_compactor_pods_after_scale is failed" in fail_when
    assert "acm_switchover_execution.mode" in fail_when
