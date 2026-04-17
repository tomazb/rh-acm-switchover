"""Tests for high-signal Ansible resilience contracts."""

import pathlib

import yaml

COLLECTION_DIR = pathlib.Path(__file__).resolve().parents[2]
ROLES_DIR = COLLECTION_DIR / "roles"
PLAYBOOKS_DIR = COLLECTION_DIR / "playbooks"

ACTIVATION_TASKS = ROLES_DIR / "activation" / "tasks"
PREFLIGHT_TASKS = ROLES_DIR / "preflight" / "tasks"
DECOMMISSION_TASKS = ROLES_DIR / "decommission" / "tasks"


def _load_yaml(path: pathlib.Path) -> list[dict]:
    return yaml.safe_load(path.read_text())


def test_activate_restore_verifies_patch_application_after_patch():
    """activation/activate_restore.yml must verify Restore patch application with polling."""
    tasks = _load_yaml(ACTIVATION_TASKS / "activate_restore.yml")

    restore_queries = [task for task in tasks if task.get("kubernetes.core.k8s_info", {}).get("kind") == "Restore"]
    assert restore_queries, "activate_restore.yml must re-read Restore resources after patching"

    verification_tasks = [
        task
        for task in restore_queries
        if "resourceVersion" in str(task.get("until", ""))
        or "veleroManagedClustersBackupName" in str(task.get("until", ""))
    ]
    assert verification_tasks, "activate_restore.yml must poll until the activation patch is observable"

    verify_task = verification_tasks[0]
    assert "retries" in verify_task and "delay" in verify_task, (
        "activate_restore.yml must retry patch verification instead of trusting a single patch response"
    )
    until = str(verify_task.get("until", ""))
    assert "resourceVersion" in until, "activate_restore.yml must verify a Restore resourceVersion change"
    assert "veleroManagedClustersBackupName" in until, (
        "activate_restore.yml must verify the managed-clusters backup field after patching"
    )


def test_preflight_validate_rbac_detects_argocd_install_type():
    """preflight RBAC validation must detect Argo CD install type instead of hardcoding unknown."""
    tasks = _load_yaml(PREFLIGHT_TASKS / "validate_rbac.yml")
    text = (PREFLIGHT_TASKS / "validate_rbac.yml").read_text()

    crd_queries = [
        task
        for task in tasks
        if task.get("kubernetes.core.k8s_info", {}).get("kind") == "CustomResourceDefinition"
    ]
    assert crd_queries, "validate_rbac.yml must query Argo CD CRDs to determine install type"
    assert "argocds.argoproj.io" in text, "validate_rbac.yml must detect operator installs via argocds.argoproj.io"
    assert "argocd_install_type: unknown" not in text, (
        "validate_rbac.yml must stop widening permissions with a hardcoded unknown install type"
    )


def test_decommission_validates_rbac_before_destructive_steps():
    """decommission must perform RBAC validation before deleting resources."""
    assert (DECOMMISSION_TASKS / "validate_rbac.yml").exists(), (
        "decommission must define a dedicated RBAC validation task file"
    )

    main_tasks = _load_yaml(DECOMMISSION_TASKS / "main.yml")
    includes = [task.get("ansible.builtin.include_tasks", "") for task in main_tasks]

    assert "validate_rbac.yml" in includes, "decommission/main.yml must include validate_rbac.yml"
    assert "delete_managed_clusters.yml" in includes, "decommission/main.yml must include delete_managed_clusters.yml"
    assert includes.index("validate_rbac.yml") < includes.index("delete_managed_clusters.yml"), (
        "decommission RBAC validation must run before destructive delete tasks"
    )

    validate_text = (DECOMMISSION_TASKS / "validate_rbac.yml").read_text()
    assert "tomazb.acm_switchover.acm_rbac_validate" in validate_text
    assert "include_decommission: true" in validate_text
    assert "run_ssar" in validate_text, "decommission validate_rbac.yml must execute SSAR checks before proceeding"


def test_decommission_playbook_exposes_precheck_role_path():
    """decommission playbook must still run through the decommission role entrypoint."""
    playbook = (PLAYBOOKS_DIR / "decommission.yml").read_text()
    assert "role: tomazb.acm_switchover.decommission" in playbook


def test_decommission_defaults_missing_execution_mode_to_dry_run_for_destructive_tasks():
    """Missing execution.mode must not fall through to live deletes."""
    files = [
        DECOMMISSION_TASKS / "main.yml",
        DECOMMISSION_TASKS / "delete_managed_clusters.yml",
        DECOMMISSION_TASKS / "delete_multiclusterhub.yml",
        DECOMMISSION_TASKS / "delete_observability.yml",
    ]

    for path in files:
        text = path.read_text()
        assert "default('') != 'dry_run'" not in text
        assert "default('') == 'dry_run'" not in text
        assert "default('dry_run')" in text, f"{path.name} must treat missing execution.mode as dry_run"


def test_rbac_bootstrap_defaults_missing_execution_mode_to_dry_run_for_mutations():
    """Missing execution.mode must not trigger bootstrap mutations implicitly."""
    files = [
        ROLES_DIR / "rbac_bootstrap" / "tasks" / "main.yml",
        ROLES_DIR / "rbac_bootstrap" / "tasks" / "deploy_manifests.yml",
        ROLES_DIR / "rbac_bootstrap" / "tasks" / "generate_kubeconfigs.yml",
    ]

    for path in files:
        text = path.read_text()
        assert "default('') != 'dry_run'" not in text
        assert "default('') == 'dry_run'" not in text
        assert "default('dry_run')" in text, f"{path.name} must treat missing execution.mode as dry_run"
