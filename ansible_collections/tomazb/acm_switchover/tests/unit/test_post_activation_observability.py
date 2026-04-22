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


def test_verify_observability_performs_real_health_checks():
    """verify_observability.yml must query Kubernetes health, not publish a manual stub."""
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
    assert any(
        "ansible.builtin.assert" in task or "ansible.builtin.fail" in task for task in tasks
    ), "verify_observability.yml must fail when critical observability health checks do not pass"


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
