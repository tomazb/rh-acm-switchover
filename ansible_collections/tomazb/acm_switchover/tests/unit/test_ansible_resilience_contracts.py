"""Tests for high-signal Ansible resilience contracts."""

import pathlib

import yaml

COLLECTION_DIR = pathlib.Path(__file__).resolve().parents[2]
ROLES_DIR = COLLECTION_DIR / "roles"
PLAYBOOKS_DIR = COLLECTION_DIR / "playbooks"
TESTS_DIR = COLLECTION_DIR / "tests"

ARGOCD_TASKS = ROLES_DIR / "argocd_manage" / "tasks"
ACTIVATION_TASKS = ROLES_DIR / "activation" / "tasks"
PREFLIGHT_TASKS = ROLES_DIR / "preflight" / "tasks"
POST_ACTIVATION_TASKS = ROLES_DIR / "post_activation" / "tasks"
FINALIZATION_TASKS = ROLES_DIR / "finalization" / "tasks"
DECOMMISSION_TASKS = ROLES_DIR / "decommission" / "tasks"


def _load_yaml(path: pathlib.Path) -> list[dict]:
    return yaml.safe_load(path.read_text())


def test_activate_restore_verifies_patch_application_after_patch():
    """activation/activate_restore.yml must verify Restore patch application with polling."""
    tasks = _load_yaml(ACTIVATION_TASKS / "activate_restore.yml")

    restore_queries = [
        task
        for task in tasks
        if task.get("kubernetes.core.k8s_info", {}).get("kind") == "Restore"
    ]
    assert (
        restore_queries
    ), "activate_restore.yml must re-read Restore resources after patching"

    verification_tasks = [
        task
        for task in restore_queries
        if "resourceVersion" in str(task.get("until", ""))
        or "veleroManagedClustersBackupName" in str(task.get("until", ""))
    ]
    assert (
        verification_tasks
    ), "activate_restore.yml must poll until the activation patch is observable"

    verify_task = verification_tasks[0]
    assert (
        "retries" in verify_task and "delay" in verify_task
    ), "activate_restore.yml must retry patch verification instead of trusting a single patch response"
    until = str(verify_task.get("until", ""))
    assert (
        "resourceVersion" in until
    ), "activate_restore.yml must verify a Restore resourceVersion change"
    assert (
        "veleroManagedClustersBackupName" in until
    ), "activate_restore.yml must verify the managed-clusters backup field after patching"


def test_preflight_validate_rbac_detects_argocd_install_type():
    """preflight RBAC validation must detect Argo CD install type instead of hardcoding unknown."""
    tasks = _load_yaml(PREFLIGHT_TASKS / "validate_rbac.yml")
    text = (PREFLIGHT_TASKS / "validate_rbac.yml").read_text()

    crd_queries = [
        task
        for task in tasks
        if task.get("kubernetes.core.k8s_info", {}).get("kind")
        == "CustomResourceDefinition"
    ]
    assert (
        crd_queries
    ), "validate_rbac.yml must query Argo CD CRDs to determine install type"
    assert (
        "argocds.argoproj.io" in text
    ), "validate_rbac.yml must detect operator installs via the argocds CRD"
    assert (
        "argocd_install_type: unknown" not in text
    ), "validate_rbac.yml must stop widening permissions with a hardcoded unknown install type"
    assert (
        "applications.argoproj.io" in text
    ), "validate_rbac.yml must probe the applications CRD to distinguish vanilla Argo CD from no install"
    assert (
        "'check'" in text
    ), "validate_rbac.yml must support the read-only Argo CD RBAC check mode"
    assert (
        "skip_gitops_check" in text
    ), "validate_rbac.yml must derive Argo CD RBAC mode from skip_gitops_check"


def test_decommission_validates_rbac_before_destructive_steps():
    """decommission must perform RBAC validation before deleting resources."""
    assert (
        DECOMMISSION_TASKS / "validate_rbac.yml"
    ).exists(), "decommission must define a dedicated RBAC validation task file"

    main_tasks = _load_yaml(DECOMMISSION_TASKS / "main.yml")
    includes = [task.get("ansible.builtin.include_tasks", "") for task in main_tasks]

    assert (
        "validate_rbac.yml" in includes
    ), "decommission/main.yml must include validate_rbac.yml"
    assert (
        "delete_managed_clusters.yml" in includes
    ), "decommission/main.yml must include delete_managed_clusters.yml"
    assert includes.index("validate_rbac.yml") < includes.index(
        "delete_managed_clusters.yml"
    ), "decommission RBAC validation must run before destructive delete tasks"

    validate_text = (DECOMMISSION_TASKS / "validate_rbac.yml").read_text()
    assert "tomazb.acm_switchover.acm_rbac_validate" in validate_text
    assert "include_decommission: true" in validate_text
    assert "decommission_only: true" in validate_text
    assert (
        "run_ssar" in validate_text
    ), "decommission validate_rbac.yml must execute SSAR checks before proceeding"


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
        assert (
            "default('dry_run')" in text
        ), f"{path.name} must treat missing execution.mode as dry_run"


def test_decommission_waits_for_non_local_managed_clusters_before_mch_delete():
    """ManagedCluster finalizers must drain before MultiClusterHub deletion starts."""
    tasks = _load_yaml(DECOMMISSION_TASKS / "delete_managed_clusters.yml")
    text = (DECOMMISSION_TASKS / "delete_managed_clusters.yml").read_text()

    wait_tasks = [
        task
        for task in tasks
        if task.get("kubernetes.core.k8s_info", {}).get("kind") == "ManagedCluster"
        and "until" in task
    ]

    assert (
        wait_tasks
    ), "delete_managed_clusters.yml must poll ManagedClusters after delete requests"
    wait_task = wait_tasks[-1]
    assert "retries" in wait_task and "delay" in wait_task
    until = str(wait_task.get("until", ""))
    assert "local-cluster" in until
    assert "| length" in until
    assert "== 0" in until
    assert text.index("Delete non-local ManagedClusters") < text.index(
        wait_task["name"]
    )
    assert isinstance(wait_task.get("when"), list)
    assert any(
        "_managed_cluster_delete_targets" in str(condition)
        for condition in wait_task["when"]
    )


def test_decommission_deletes_all_discovered_observability_and_mch_resources():
    """Decommission must enumerate CRs instead of assuming conventional resource names."""
    for filename, kind, fixed_name in (
        ("delete_observability.yml", "MultiClusterObservability", "observability"),
        ("delete_multiclusterhub.yml", "MultiClusterHub", "multiclusterhub"),
    ):
        tasks = _load_yaml(DECOMMISSION_TASKS / filename)
        text = (DECOMMISSION_TASKS / filename).read_text()

        discovery_tasks = [
            task
            for task in tasks
            if task.get("kubernetes.core.k8s_info", {}).get("kind") == kind
        ]
        delete_tasks = [
            task
            for task in tasks
            if task.get("kubernetes.core.k8s", {}).get("kind") == kind
        ]

        assert discovery_tasks, f"{filename} must list {kind} resources before deletion"
        assert delete_tasks, f"{filename} must delete discovered {kind} resources"
        assert "{{ item.metadata.name }}" in str(
            delete_tasks[0].get("kubernetes.core.k8s", {}).get("name")
        )
        assert "loop" in delete_tasks[0]
        assert f"name: {fixed_name}" not in text


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
        assert (
            "default('dry_run')" in text
        ), f"{path.name} must treat missing execution.mode as dry_run"


def test_run_ssar_records_failed_or_malformed_reviews_as_denied_permissions():
    """Failed SSAR calls and malformed API replies must fail closed in RBAC summaries."""
    text = (PREFLIGHT_TASKS / "run_ssar.yml").read_text()

    assert "result.failed | default(false)" in text
    assert "result.result is not defined" in text
    assert "result.result.status is not defined" in text
    assert "reason" in text


def test_fixture_playbook_runs_use_per_test_ansible_temp_dirs():
    """Integration fixtures must avoid shared /tmp ansible dirs across parallel test runs."""
    text = (TESTS_DIR / "integration" / "conftest.py").read_text()

    assert '"/tmp/ansible-local"' not in text
    assert '"/tmp/ansible-remote"' not in text
    assert 'tmp_path / "ansible-local"' in text
    assert 'tmp_path / "ansible-remote"' in text


def test_checkpoint_fixture_seed_matches_runtime_checkpoint_contract():
    """Pre-seeded checkpoint fixtures must use the runtime checkpoint fields."""
    text = (TESTS_DIR / "conftest.py").read_text()

    assert '"phase": pre_completed_phases[-1]' in text
    assert '"created_at": "2026-01-01T00:00:00+00:00"' in text
    assert '"phase_status": "pass"' not in text


def test_activation_rediscovers_restore_facts_before_passive_selection():
    """Checkpoint resume can skip preflight, so activation must discover Restore facts itself."""
    assert (ACTIVATION_TASKS / "discover_resources.yml").exists()
    main_tasks = _load_yaml(ACTIVATION_TASKS / "main.yml")
    block_tasks = next(task["block"] for task in main_tasks if "block" in task)
    includes = [task.get("ansible.builtin.include_tasks", "") for task in block_tasks]

    assert includes[0] == "discover_resources.yml"
    assert includes.index("discover_resources.yml") < includes.index(
        "verify_passive_sync.yml"
    )


def test_restore_wait_accepts_only_benign_finished_with_errors():
    """FinishedWithErrors is successful only for consecutive-switchover already-available messages."""
    text = (ACTIVATION_TASKS / "wait_for_restore.yml").read_text()
    assert "FinishedWithErrors" in text
    assert "already available" in text


def test_collection_mutation_tasks_default_missing_execution_mode_to_dry_run():
    """Missing execution.mode must not trigger live pause, import, or reset mutations."""
    files = [
        ARGOCD_TASKS / "pause.yml",
        ARGOCD_TASKS / "resume.yml",
        ACTIVATION_TASKS / "manage_auto_import.yml",
        ACTIVATION_TASKS / "apply_immediate_import.yml",
        FINALIZATION_TASKS / "reset_auto_import.yml",
    ]

    for path in files:
        text = path.read_text()
        assert "default('') != 'dry_run'" not in text
        assert "default('') == 'dry_run'" not in text
        assert (
            "acm_switchover_execution.mode | default('dry_run') != 'dry_run'" in text
        ), f"{path.name} must guard live mutations with dry_run-safe default"


def test_post_activation_main_skips_live_checks_in_dry_run():
    """Dry-run activation creates no Restore, so post_activation must not perform live waits/remediation."""
    text = (POST_ACTIVATION_TASKS / "main.yml").read_text()
    assert "reason: dry_run" in text
    assert "acm_switchover_execution.mode | default('dry_run') != 'dry_run'" in text


def test_argocd_resume_splits_checkpoint_path_facts():
    """Ansible evaluates set_fact values before assigning them, so dependent facts must be split."""
    playbook = _load_yaml(PLAYBOOKS_DIR / "argocd_resume.yml")
    pre_tasks = playbook[0].get("pre_tasks", [])
    path_fact_tasks = [
        task
        for task in pre_tasks
        if "_argocd_resume_checkpoint_path" in task.get("ansible.builtin.set_fact", {})
    ]
    abs_fact_tasks = [
        task
        for task in pre_tasks
        if "_argocd_resume_checkpoint_path_abs"
        in task.get("ansible.builtin.set_fact", {})
    ]

    assert path_fact_tasks
    assert abs_fact_tasks
    for task in pre_tasks:
        facts = task.get("ansible.builtin.set_fact", {})
        assert not (
            "_argocd_resume_checkpoint_path" in facts
            and "_argocd_resume_checkpoint_path_abs" in facts
        ), "checkpoint path and absolute path facts must be assigned in separate tasks"
