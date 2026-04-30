"""Tests for primary_prep disable-auto-import behavior."""

import pathlib

import yaml

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
PRIMARY_PREP_TASKS = ROLES_DIR / "primary_prep" / "tasks"


def _load_yaml(name: str) -> list[dict]:
    return yaml.safe_load((PRIMARY_PREP_TASKS / name).read_text())


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
