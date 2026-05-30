"""Tests for post_activation observability verification and auto-import cleanup."""

import pathlib

import yaml

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
POST_ACTIVATION_TASKS = ROLES_DIR / "post_activation" / "tasks"
CONSTANTS_FILE = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "module_utils" / "constants.py"


def _load_yaml(name: str) -> list[dict]:
    return yaml.safe_load((POST_ACTIVATION_TASKS / name).read_text())


def _main_block_tasks() -> list[dict]:
    main_tasks = _load_yaml("main.yml")
    for task in main_tasks:
        if "block" in task:
            return task["block"]
    raise AssertionError("post_activation/main.yml must contain a block of phase tasks")


def test_cleanup_auto_import_annotations_file_exists():
    """post_activation must define a dedicated cleanup task file."""
    assert (POST_ACTIVATION_TASKS / "cleanup_auto_import_annotations.yml").exists()


def test_main_cleans_auto_import_annotations_before_observability():
    """post_activation/main.yml must clean stale auto-import markers before observability checks."""
    includes = [task.get("ansible.builtin.include_tasks", "") for task in _main_block_tasks()]

    assert (
        "cleanup_auto_import_annotations.yml" in includes
    ), "main.yml must include cleanup_auto_import_annotations.yml"
    assert "verify_observability.yml" in includes, "main.yml must include verify_observability.yml"
    assert includes.index("cleanup_auto_import_annotations.yml") < includes.index(
        "verify_observability.yml"
    ), "cleanup_auto_import_annotations.yml must run before verify_observability.yml"


def test_main_runs_observability_only_when_secondary_observability_was_detected():
    """post_activation must mirror Python and skip Observability when secondary lacks it."""
    observability_task = next(
        task for task in _main_block_tasks() if task.get("ansible.builtin.include_tasks") == "verify_observability.yml"
    )
    when_text = "\n".join(observability_task.get("when", []))

    assert "acm_switchover_secondary_has_observability" in when_text
    assert "skip_observability_checks" in when_text


def test_verify_observability_performs_real_health_checks():
    """verify_observability.yml must query Kubernetes health and block unhealthy Observability."""
    tasks = _load_yaml("verify_observability.yml")
    text = (POST_ACTIVATION_TASKS / "verify_observability.yml").read_text()

    deployment_checks = [task for task in tasks if task.get("kubernetes.core.k8s_info", {}).get("kind") == "Deployment"]
    pod_checks = [task for task in tasks if task.get("kubernetes.core.k8s_info", {}).get("kind") == "Pod"]

    assert deployment_checks, "verify_observability.yml must query observability Deployments"
    assert pod_checks, "verify_observability.yml must query observability Pods"
    assert "observatorium-api" in text, "verify_observability.yml must verify observatorium-api readiness"
    assert "thanos-compact" in text, "verify_observability.yml must verify thanos-compact readiness"
    assert "manual verification" not in text.lower(), "verify_observability.yml must not remain a placeholder"
    assert any(
        "retries" in task and "delay" in task for task in deployment_checks + pod_checks
    ), "verify_observability.yml must poll until workloads recover"
    fail_tasks = [task for task in tasks if "ansible.builtin.fail" in task]
    assert fail_tasks, "verify_observability.yml must fail when Observability health checks do not pass"
    assert (
        "status:" in text and "'failed'" in text
    ), "verify_observability.yml must publish failed status for unhealthy observability"
    wait_tasks = [task for task in deployment_checks + pod_checks if "retries" in task and "delay" in task]
    assert wait_tasks, "verify_observability.yml must retain bounded waits before explicit failure"
    assert all(
        task.get("failed_when") is False for task in wait_tasks
    ), "observability wait timeouts must continue to publish deterministic failure facts"
    fail_when = str(fail_tasks[-1].get("when", ""))
    assert "_acm_observatorium_rollout_ready" in fail_when
    assert "_acm_observability_pods_ready" in fail_when


def test_observatorium_rollout_gate_requires_updated_replicas():
    """Deployment rollout gate must reject stale ready replicas from the old ReplicaSet."""
    tasks = _load_yaml("verify_observability.yml")
    rollout_tasks = [
        task for task in tasks if task.get("name") == "Wait for observatorium-api Deployment rollout to stabilize"
    ]
    assert rollout_tasks, "verify_observability.yml must wait for observatorium-api rollout"

    until = str(rollout_tasks[0].get("until", ""))
    assert "observedGeneration" in until
    assert "updatedReplicas" in until
    assert "availableReplicas" in until
    assert "readyReplicas" in until
    assert "unavailableReplicas" in until
    assert ".get('status', {}).get('replicas'" in until
    assert ".get('spec', {}).get('replicas'" in until


def test_observability_publish_and_failure_gates_default_readiness_to_failed():
    """Unset observability readiness facts must fail closed instead of passing by default."""
    text = (POST_ACTIVATION_TASKS / "verify_observability.yml").read_text()

    assert "_acm_observatorium_rollout_ready | default(true)" not in text
    assert "_acm_observability_pods_ready | default(true)" not in text
    assert "_acm_observatorium_rollout_ready | default(false)" in text
    assert "_acm_observability_pods_ready | default(false)" in text


def test_observability_result_uses_prefixed_fact_with_compatibility_alias():
    """The public observability result must be namespaced while preserving the legacy alias."""
    tasks = _load_yaml("verify_observability.yml")
    publish_tasks = [
        task
        for task in tasks
        if "ansible.builtin.set_fact" in task
        and "acm_switchover_observability_check_result" in task["ansible.builtin.set_fact"]
    ]
    alias_tasks = [
        task
        for task in tasks
        if "ansible.builtin.set_fact" in task
        and task["ansible.builtin.set_fact"].get("acm_observability_check_result")
        == "{{ acm_switchover_observability_check_result }}"
    ]

    assert publish_tasks, "verify_observability.yml must publish acm_switchover_observability_check_result"
    assert alias_tasks, "verify_observability.yml must preserve the acm_observability_check_result alias"


def test_cleanup_auto_import_annotations_patches_managed_clusters():
    """cleanup task must remove disable-auto-import from non-local ManagedClusters."""
    tasks = _load_yaml("cleanup_auto_import_annotations.yml")
    text = (POST_ACTIVATION_TASKS / "cleanup_auto_import_annotations.yml").read_text()

    managed_cluster_queries = [
        task for task in tasks if task.get("kubernetes.core.k8s_info", {}).get("kind") == "ManagedCluster"
    ]
    patch_tasks = [task for task in tasks if "kubernetes.core.k8s" in task]

    assert managed_cluster_queries, "cleanup_auto_import_annotations.yml must query ManagedCluster resources"
    assert patch_tasks, "cleanup_auto_import_annotations.yml must patch stale ManagedCluster annotations"
    assert (
        "disable-auto-import" in text
    ), "cleanup_auto_import_annotations.yml must remove the disable-auto-import annotation"
    assert (
        "local-cluster" in text or "LOCAL_CLUSTER_NAME" in text
    ), "cleanup_auto_import_annotations.yml must exclude the local-cluster"
    assert "null" in text, "cleanup_auto_import_annotations.yml must remove the annotation with a null patch"


def test_collection_constants_include_post_activation_parity_constants():
    """collection constants must define observability and annotation names used by post_activation."""
    text = CONSTANTS_FILE.read_text()

    assert "DISABLE_AUTO_IMPORT_ANNOTATION" in text
    assert "OBSERVATORIUM_API_DEPLOYMENT" in text
    assert "THANOS_COMPACTOR_STATEFULSET" in text
