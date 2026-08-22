"""Tests for high-signal Ansible resilience contracts."""

import json
import pathlib

import yaml
from jinja2 import Environment
from yaml_contract_helpers import _flatten_tasks, _when_text

COLLECTION_DIR = pathlib.Path(__file__).resolve().parents[2]
ROLES_DIR = COLLECTION_DIR / "roles"
PLAYBOOKS_DIR = COLLECTION_DIR / "playbooks"
TESTS_DIR = COLLECTION_DIR / "tests"

ARGOCD_TASKS = ROLES_DIR / "argocd_manage" / "tasks"
ACTIVATION_TASKS = ROLES_DIR / "activation" / "tasks"
COMMON_TASKS = ROLES_DIR / "common" / "tasks"
PREFLIGHT_TASKS = ROLES_DIR / "preflight" / "tasks"
PRIMARY_PREP_TASKS = ROLES_DIR / "primary_prep" / "tasks"
POST_ACTIVATION_TASKS = ROLES_DIR / "post_activation" / "tasks"
FINALIZATION_TASKS = ROLES_DIR / "finalization" / "tasks"
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
    assert (
        "retries" in verify_task and "delay" in verify_task
    ), "activate_restore.yml must retry patch verification instead of trusting a single patch response"
    until = str(verify_task.get("until", ""))
    assert "resourceVersion" in until, "activate_restore.yml must verify a Restore resourceVersion change"
    assert (
        "veleroManagedClustersBackupName" in until
    ), "activate_restore.yml must verify the managed-clusters backup field after patching"


def test_full_restore_waits_for_managed_cluster_presence_after_acm_restore():
    """Full restore must wait for ManagedCluster resources after the ACM Restore is terminal."""
    tasks = _load_yaml(ACTIVATION_TASKS / "wait_for_restore.yml")
    text = (ACTIVATION_TASKS / "wait_for_restore.yml").read_text()
    expectation_text = (COMMON_TASKS / "resolve_managed_cluster_expectation.yml").read_text()

    managed_cluster_waits = [
        task
        for task in tasks
        if task.get("kubernetes.core.k8s_info", {}).get("kind") == "ManagedCluster" and "until" in task
    ]

    assert managed_cluster_waits, "wait_for_restore.yml must poll ManagedClusters for full Restore activation"
    wait_task = managed_cluster_waits[0]
    until = str(wait_task["until"])
    when = str(wait_task.get("when", ""))

    assert "managed_cluster_presence_required" in when
    assert "metadata.name', 'ne', 'local-cluster'" in until
    assert "_acm_activation_min_managed_clusters" in until
    assert ">= (_acm_activation_min_managed_clusters | int)" in until
    assert "_acm_activation_expected_managed_cluster_names" in until
    assert "difference(" in until
    assert "resolve_managed_cluster_expectation.yml" in text
    assert "acm_switchover_operation.min_managed_clusters is defined" in expectation_text
    assert "acm_switchover_expected_managed_cluster_count" in expectation_text
    assert "acm_switchover_operation.restore_only | default(false) | bool" in expectation_text
    assert "1\n          if (acm_switchover_operation.restore_only | default(false) | bool)" in expectation_text


def test_preflight_validate_rbac_detects_argocd_install_type():
    """preflight RBAC validation must detect Argo CD install type instead of hardcoding unknown."""
    hub_tasks = _load_yaml(PREFLIGHT_TASKS / "validate_rbac_hub.yml")
    hub_text = (PREFLIGHT_TASKS / "validate_rbac_hub.yml").read_text()
    main_text = (PREFLIGHT_TASKS / "validate_rbac.yml").read_text()
    argocds_crd = ".".join(("argocds", "argoproj", "io"))
    applications_crd = ".".join(("applications", "argoproj", "io"))

    crd_queries = [
        task for task in hub_tasks if task.get("kubernetes.core.k8s_info", {}).get("kind") == "CustomResourceDefinition"
    ]
    assert crd_queries, "validate_rbac_hub.yml must query Argo CD CRDs to determine install type"
    assert argocds_crd in hub_text, "validate_rbac_hub.yml must detect operator installs via the argocds CRD"
    for text in (main_text, hub_text):
        assert (
            "argocd_install_type: unknown" not in text
        ), "RBAC validation must stop widening permissions with a hardcoded unknown install type"
    assert (
        applications_crd in hub_text
    ), "validate_rbac_hub.yml must probe the applications CRD to distinguish vanilla Argo CD from no install"
    assert "'check'" in main_text, "validate_rbac.yml must support the read-only Argo CD RBAC check mode"
    assert "skip_gitops_check" in main_text, "validate_rbac.yml must derive Argo CD RBAC mode from skip_gitops_check"


def test_decommission_validates_rbac_before_destructive_steps():
    """decommission must perform RBAC validation before deleting resources."""
    assert (
        DECOMMISSION_TASKS / "validate_rbac.yml"
    ).exists(), "decommission must define a dedicated RBAC validation task file"

    main_tasks = _load_yaml(DECOMMISSION_TASKS / "main.yml")
    includes = [task.get("ansible.builtin.include_tasks", "") for task in main_tasks]

    assert "validate_rbac.yml" in includes, "decommission/main.yml must include validate_rbac.yml"
    assert "delete_managed_clusters.yml" in includes, "decommission/main.yml must include delete_managed_clusters.yml"
    assert includes.index("validate_rbac.yml") < includes.index(
        "delete_managed_clusters.yml"
    ), "decommission RBAC validation must run before destructive delete tasks"
    validate_include = next(
        task for task in main_tasks if task.get("ansible.builtin.include_tasks") == "validate_rbac.yml"
    )
    assert validate_include["when"] == "not (acm_switchover_features.skip_rbac_validation | default(false))"

    validate_text = (DECOMMISSION_TASKS / "validate_rbac.yml").read_text()
    assert "tomazb.acm_switchover.acm_rbac_validate" in validate_text
    assert "include_decommission: true" in validate_text
    assert "decommission_only: true" in validate_text
    assert "run_ssar" in validate_text, "decommission validate_rbac.yml must execute SSAR checks before proceeding"


def test_decommission_asserts_explicit_primary_hub_before_kubernetes_operations():
    """Decommission must not fall through to the implicit kube context when primary input is empty."""
    main_tasks = _load_yaml(DECOMMISSION_TASKS / "main.yml")
    flattened_tasks = _flatten_tasks(main_tasks)
    first_k8s_index = next(
        index
        for index, task in enumerate(flattened_tasks)
        if "kubernetes.core.k8s_info" in task or "kubernetes.core.k8s" in task
    )
    assert_tasks = [
        (index, task)
        for index, task in enumerate(flattened_tasks)
        if "ansible.builtin.assert" in task and index < first_k8s_index
    ]

    assert assert_tasks, "decommission/main.yml must assert primary hub inputs before Kubernetes calls"
    assert_text = "\n".join(str(task["ansible.builtin.assert"]) for _, task in assert_tasks)
    assert "acm_switchover_hubs.primary.kubeconfig" in assert_text
    assert "acm_switchover_hubs.primary.context" in assert_text
    assert "| length) > 0" in assert_text
    assert "default kube context" in assert_text


def test_decommission_autodetects_observability_by_default_before_rbac():
    """Decommission should derive observability from the namespace unless explicitly overridden."""
    defaults = yaml.safe_load((ROLES_DIR / "decommission" / "defaults" / "main.yml").read_text())
    main_text = (DECOMMISSION_TASKS / "main.yml").read_text()
    main_tasks = _load_yaml(DECOMMISSION_TASKS / "main.yml")
    includes = [task.get("ansible.builtin.include_tasks", "") for task in main_tasks]

    assert defaults["acm_switchover_decommission"]["has_observability"] == "auto"
    assert "Namespace" in main_text
    assert "open-cluster-management-observability" in main_text
    assert "acm_switchover_decommission_effective_has_observability" in main_text

    namespace_lookup_index = next(
        index
        for index, task in enumerate(main_tasks)
        if task.get("kubernetes.core.k8s_info", {}).get("kind") == "Namespace"
    )
    observability_fact_index = next(
        index
        for index, task in enumerate(main_tasks)
        if "acm_switchover_decommission_effective_has_observability" in str(task.get("ansible.builtin.set_fact", {}))
    )
    validate_index = includes.index("validate_rbac.yml")

    assert namespace_lookup_index < observability_fact_index < validate_index
    assert validate_index < includes.index("delete_observability.yml")


def test_decommission_observability_autodetection_fails_closed():
    """Auto detection must not convert 403/timeouts/API errors into no-observability."""
    main_tasks = _load_yaml(DECOMMISSION_TASKS / "main.yml")
    namespace_task = next(
        task for task in main_tasks if task.get("kubernetes.core.k8s_info", {}).get("kind") == "Namespace"
    )

    assert "failed_when" not in namespace_task
    assert "ignore_errors" not in namespace_task


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


def test_decommission_summary_uses_report_artifact_safe_path_policy():
    """Optional decommission summaries must use the shared report artifact writer."""
    main_tasks = _load_yaml(DECOMMISSION_TASKS / "main.yml")
    summary_tasks = [task for task in main_tasks if task.get("name") == "Write decommission summary when requested"]

    assert summary_tasks, "decommission/main.yml must write the optional summary"
    summary_task = summary_tasks[0]
    artifact_args = summary_task.get("tomazb.acm_switchover.acm_report_artifact")
    assert artifact_args, "decommission summary writes must use acm_report_artifact"
    assert artifact_args["path"] == "{{ _acm_summary_path_abs }}"
    assert artifact_args["report"] == "{{ acm_switchover_decommission_result }}"
    assert artifact_args["mode"] == "0644"
    assert summary_task["when"] == "_acm_decommission_summary_path | default('') | length > 0"
    assert not any(
        task.get("ansible.builtin.copy", {}).get("dest") == "{{ _acm_summary_path_abs }}" for task in main_tasks
    ), "decommission summary writes must not bypass artifact path validation"


def test_discovery_summary_uses_report_artifact_safe_path_policy():
    """Optional discovery summaries must use the shared report artifact writer."""
    main_tasks = _load_yaml(ROLES_DIR / "discovery" / "tasks" / "main.yml")
    summary_tasks = [task for task in main_tasks if task.get("name") == "Write summary when requested"]

    assert summary_tasks, "discovery/main.yml must write the optional summary"
    summary_task = summary_tasks[0]
    artifact_args = summary_task.get("tomazb.acm_switchover.acm_report_artifact")
    assert artifact_args, "discovery summary writes must use acm_report_artifact"
    assert artifact_args["path"] == "{{ _acm_summary_path_abs }}"
    assert artifact_args["report"] == "{{ acm_switchover_discovery_result }}"
    assert artifact_args["mode"] == "0644"
    assert not any(
        task.get("ansible.builtin.copy", {}).get("dest") == "{{ _acm_summary_path_abs }}" for task in main_tasks
    ), "discovery summary writes must not bypass artifact path validation"


def test_rbac_bootstrap_summary_uses_report_artifact_safe_path_policy():
    """Optional RBAC bootstrap summaries must use the shared report artifact writer."""
    main_tasks = _load_yaml(ROLES_DIR / "rbac_bootstrap" / "tasks" / "main.yml")
    summary_tasks = [task for task in main_tasks if task.get("name") == "Write summary when requested"]

    assert summary_tasks, "rbac_bootstrap/main.yml must write the optional summary"
    summary_task = summary_tasks[0]
    artifact_args = summary_task.get("tomazb.acm_switchover.acm_report_artifact")
    assert artifact_args, "rbac_bootstrap summary writes must use acm_report_artifact"
    assert artifact_args["path"] == "{{ _acm_summary_path_abs }}"
    assert artifact_args["report"] == "{{ acm_switchover_rbac_bootstrap_result }}"
    assert artifact_args["mode"] == "0644"
    assert not any(
        task.get("ansible.builtin.copy", {}).get("dest") == "{{ _acm_summary_path_abs }}" for task in main_tasks
    ), "rbac_bootstrap summary writes must not bypass artifact path validation"


def test_argocd_manage_test_summary_uses_report_artifact_safe_path_policy():
    """The optional Argo CD test summary must use the shared report artifact writer."""
    play_tasks = _flatten_tasks(_load_yaml(PLAYBOOKS_DIR / "argocd_manage_test.yml")[0]["tasks"])
    summary_tasks = [task for task in play_tasks if task.get("name") == "Write summary file"]

    assert summary_tasks, "argocd_manage_test.yml must write the optional summary"
    summary_task = summary_tasks[0]
    artifact_args = summary_task.get("tomazb.acm_switchover.acm_report_artifact")
    assert artifact_args, "argocd_manage_test summary writes must use acm_report_artifact"
    assert artifact_args["path"] == "{{ _acm_summary_path_abs }}"
    assert artifact_args["mode"] == "0644"
    assert not any(
        task.get("ansible.builtin.copy", {}).get("dest") == "{{ _acm_summary_path_abs }}" for task in play_tasks
    ), "argocd_manage_test summary writes must not bypass artifact path validation"


def test_decommission_waits_for_non_local_managed_clusters_before_mch_delete():
    """ManagedCluster finalizers must drain before MultiClusterHub deletion starts."""
    tasks = _load_yaml(DECOMMISSION_TASKS / "delete_managed_clusters.yml")
    text = (DECOMMISSION_TASKS / "delete_managed_clusters.yml").read_text()

    wait_tasks = [
        task
        for task in tasks
        if task.get("kubernetes.core.k8s_info", {}).get("kind") == "ManagedCluster" and "until" in task
    ]

    assert wait_tasks, "delete_managed_clusters.yml must poll ManagedClusters after delete requests"
    wait_task = wait_tasks[-1]
    assert "retries" in wait_task and "delay" in wait_task
    until = str(wait_task.get("until", ""))
    assert "local-cluster" in until
    assert "| length" in until
    assert "== 0" in until
    assert text.index("Delete non-local ManagedClusters") < text.index(wait_task["name"])
    assert isinstance(wait_task.get("when"), list)
    assert any("_managed_cluster_delete_targets" in str(condition) for condition in wait_task["when"])


def test_decommission_checks_clusterdeployments_before_managedcluster_delete():
    """ManagedCluster deletion must have a just-in-time Hive preserveOnDelete safety gate."""
    tasks = _load_yaml(DECOMMISSION_TASKS / "delete_managed_clusters.yml")
    flattened_tasks = _flatten_tasks(tasks)
    text = (DECOMMISSION_TASKS / "delete_managed_clusters.yml").read_text()

    clusterdeployment_reads = [
        task for task in flattened_tasks if task.get("kubernetes.core.k8s_info", {}).get("kind") == "ClusterDeployment"
    ]
    fail_tasks = [task for task in flattened_tasks if task.get("ansible.builtin.fail")]

    assert clusterdeployment_reads, "delete_managed_clusters.yml must re-read Hive ClusterDeployments"
    assert fail_tasks, "delete_managed_clusters.yml must fail before unsafe deletes"
    assert text.index("List Hive ClusterDeployments before ManagedCluster deletion") < text.index(
        "Block unsafe ManagedCluster deletion"
    )
    assert text.index("Block unsafe ManagedCluster deletion") < text.index("Delete non-local ManagedClusters")
    assert "_managed_cluster_delete_targets" in text
    assert "preserveOnDelete" in text
    assert "clusterMetadata" in text
    assert "clusterInstallRef" in text
    assert "Cannot verify ManagedCluster relationship" in text
    assert "local-cluster" in text
    assert "from_json" in text
    assert "to_json" in text


def _render_clusterdeployment_delete_safety(clusterdeployments: list[dict], target_names: list[str]) -> dict:
    tasks = _load_yaml(DECOMMISSION_TASKS / "delete_managed_clusters.yml")
    classify_task = next(
        task
        for task in tasks
        if task.get("name") == "Classify matching ClusterDeployments before ManagedCluster deletion"
    )
    expression = classify_task["vars"]["_clusterdeployment_delete_safety_json"]
    environment = Environment()
    environment.filters["bool"] = bool
    environment.filters["to_json"] = json.dumps
    environment.filters["unique"] = lambda values: list(dict.fromkeys(values))
    rendered = environment.from_string(expression).render(
        _decommission_clusterdeployments={"resources": clusterdeployments},
        _managed_cluster_delete_targets=[{"metadata": {"name": name}} for name in target_names],
    )

    return json.loads(rendered)


def test_decommission_clusterdeployment_safety_classifier_behavior():
    """The actual Jinja classifier must preserve safe matches and fail closed on ambiguous relationships."""
    by_metadata_name = {
        "metadata": {"namespace": "hive-ns", "name": "cluster-a"},
        "spec": {"preserveOnDelete": False},
    }
    by_spec_cluster_name = {
        "metadata": {"namespace": "hive-ns", "name": "install-b"},
        "spec": {"clusterName": "cluster-b", "preserveOnDelete": False},
    }
    by_cluster_metadata = {
        "metadata": {"namespace": "hive-ns", "name": "install-c"},
        "spec": {
            "clusterMetadata": {"clusterName": "cluster-c"},
            "preserveOnDelete": False,
        },
    }
    by_install_ref_convention = {
        "metadata": {"namespace": "cluster-d", "name": "install-d"},
        "spec": {"clusterInstallRef": {"name": "cluster-d"}, "preserveOnDelete": False},
    }
    preserved = {
        "metadata": {"namespace": "hive-ns", "name": "cluster-e"},
        "spec": {"preserveOnDelete": True},
    }
    ambiguous = {
        "metadata": {"namespace": "cluster-g", "name": "cluster-f"},
        "spec": {"preserveOnDelete": True},
    }
    plausible = {
        "metadata": {"namespace": "cluster-h", "name": "install-h"},
        "spec": {"preserveOnDelete": True},
    }

    safety = _render_clusterdeployment_delete_safety(
        [
            by_metadata_name,
            by_spec_cluster_name,
            by_cluster_metadata,
            by_install_ref_convention,
            preserved,
            ambiguous,
            plausible,
        ],
        [
            "cluster-a",
            "cluster-b",
            "cluster-c",
            "cluster-d",
            "cluster-e",
            "cluster-f",
            "cluster-g",
            "cluster-h",
        ],
    )

    assert "cluster-a (hive-ns/cluster-a)" in safety["unsafe"]
    assert "cluster-b (hive-ns/install-b)" in safety["unsafe"]
    assert "cluster-c (hive-ns/install-c)" in safety["unsafe"]
    assert "cluster-d (cluster-d/install-d)" in safety["unsafe"]
    assert not any("cluster-e" in item for item in safety["unsafe"])
    assert any(
        "cluster-g/cluster-f: conflicting ManagedCluster identifiers" in item
        and "metadata.name=cluster-f" in item
        and "metadata.namespace=cluster-g" in item
        for item in safety["unverified"]
    )
    assert any(
        "cluster-h/install-h: plausible but unverified ManagedCluster relationship" in item
        and "metadata.namespace=cluster-h" in item
        for item in safety["unverified"]
    )


def test_decommission_missing_clusterdeployment_api_fails_before_delete():
    """Missing Hive ClusterDeployment API must fail before ManagedCluster deletion."""
    text = (DECOMMISSION_TASKS / "delete_managed_clusters.yml").read_text()

    assert "block:" in text and "rescue:" in text
    assert "Record verified absence of Hive ClusterDeployment API" not in text
    assert "_clusterdeployments_verified_absent" not in text
    assert "Unable to verify ClusterDeployment preserveOnDelete safety" in text
    assert text.index("Unable to verify ClusterDeployment preserveOnDelete safety") < text.index(
        "Delete non-local ManagedClusters"
    )


def test_decommission_deletes_all_discovered_observability_and_mch_resources():
    """Decommission must enumerate CRs instead of assuming conventional resource names."""
    for filename, kind, fixed_name in (
        ("delete_observability.yml", "MultiClusterObservability", "observability"),
        ("delete_multiclusterhub.yml", "MultiClusterHub", "multiclusterhub"),
    ):
        tasks = _load_yaml(DECOMMISSION_TASKS / filename)
        text = (DECOMMISSION_TASKS / filename).read_text()

        discovery_tasks = [task for task in tasks if task.get("kubernetes.core.k8s_info", {}).get("kind") == kind]
        delete_tasks = [task for task in tasks if task.get("kubernetes.core.k8s", {}).get("kind") == kind]

        assert discovery_tasks, f"{filename} must list {kind} resources before deletion"
        assert delete_tasks, f"{filename} must delete discovered {kind} resources"
        assert "{{ item.metadata.name }}" in str(delete_tasks[0].get("kubernetes.core.k8s", {}).get("name"))
        assert "loop" in delete_tasks[0]
        assert f"name: {fixed_name}" not in text


def test_decommission_waits_for_observability_and_acm_workload_pods():
    """Collection decommission must wait for workload pods like Python does."""
    obs_text = (DECOMMISSION_TASKS / "delete_observability.yml").read_text()
    mch_text = (DECOMMISSION_TASKS / "delete_multiclusterhub.yml").read_text()
    mch_tasks = _load_yaml(DECOMMISSION_TASKS / "delete_multiclusterhub.yml")

    assert "kind: Pod" in obs_text
    assert "until" in obs_text
    assert "failed_when" in obs_text
    assert "NotFound" in obs_text
    assert "not found" in obs_text
    assert "open-cluster-management-observability" in obs_text
    assert "kind: Pod" in mch_text
    assert "until" in mch_text
    assert "failed_when" in mch_text
    assert "Some ACM pods still running" in mch_text
    assert "multiclusterhub-operator" in mch_text
    pod_wait = next(
        task
        for task in mch_tasks
        if task.get("name") == "Wait for ACM workload pods to terminate after MultiClusterHub deletion"
    )
    assert pod_wait.get("failed_when") is False


def test_decommission_result_reports_actual_delete_changes():
    """Decommission result changed state must come from delete tasks, not status alone."""
    main_text = (DECOMMISSION_TASKS / "main.yml").read_text()
    managed_text = (DECOMMISSION_TASKS / "delete_managed_clusters.yml").read_text()
    obs_text = (DECOMMISSION_TASKS / "delete_observability.yml").read_text()
    mch_text = (DECOMMISSION_TASKS / "delete_multiclusterhub.yml").read_text()

    assert "changed:" in main_text
    for result_name in (
        "_managed_cluster_delete_results",
        "_multiclusterobservability_delete_results",
        "_multiclusterhub_delete_results",
    ):
        assert result_name in main_text
        assert f"({result_name} | default({{}})).results | default([])" in main_text
        assert "| selectattr('changed')" in main_text

    assert "register: _managed_cluster_delete_results" in managed_text
    assert "register: _multiclusterobservability_delete_results" in obs_text
    assert "register: _multiclusterhub_delete_results" in mch_text


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


def test_run_ssar_records_failed_or_malformed_reviews_as_denied_permissions():
    """Failed SSAR calls and malformed API replies must fail closed in RBAC summaries."""
    text = (PREFLIGHT_TASKS / "run_ssar.yml").read_text()

    assert "result.failed | default(false)" in text
    assert "result.result is not defined" in text
    assert "result.result.status is not defined" in text
    assert "reason" in text


def test_collection_ssar_tasks_split_resource_subresources():
    """Kubernetes access reviews require resource and subresource fields to be separate."""
    for path in [
        PREFLIGHT_TASKS / "run_ssar.yml",
        ROLES_DIR / "rbac_bootstrap" / "tasks" / "validate_permission_target.yml",
    ]:
        text = path.read_text()
        assert "resource: \"{{ item.1.split('/')[0] }}\"" in text
        assert "subresource: \"{{ item.1.split('/')[1] | default(omit, true) }}\"" in text


def test_collection_rbac_validation_runs_ssars_in_dry_run():
    """Dry-run must still validate RBAC with non-mutating SSAR/SAR requests."""
    for path in [
        PREFLIGHT_TASKS / "validate_rbac.yml",
        PREFLIGHT_TASKS / "validate_rbac_hub.yml",
        DECOMMISSION_TASKS / "validate_rbac.yml",
    ]:
        text = path.read_text()
        assert "mode | default('dry_run') == 'dry_run'" not in text
        assert "mode | default('dry_run') != 'dry_run'" not in text

    for path in [
        PREFLIGHT_TASKS / "validate_rbac_hub.yml",
        DECOMMISSION_TASKS / "validate_rbac.yml",
    ]:
        assert "run_ssar" in path.read_text()


def test_fixture_playbook_runs_use_per_test_ansible_temp_dirs():
    """Integration fixtures must avoid shared /tmp ansible dirs across parallel test runs."""
    text = (TESTS_DIR / "conftest.py").read_text()

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
    discover_text = (ACTIVATION_TASKS / "discover_resources.yml").read_text()
    main_tasks = _load_yaml(ACTIVATION_TASKS / "main.yml")
    block_tasks = next(task["block"] for task in main_tasks if "block" in task)
    includes = [task.get("ansible.builtin.include_tasks", "") for task in block_tasks]

    assert "register: _acm_activation_restores_live_info" in discover_text
    assert "register: _acm_activation_mch_live_info" in discover_text
    assert "acm_activation_mch_info:" in discover_text
    assert "activation_restores_info" in discover_text
    assert "acm_activation_restores_info:" in discover_text
    assert "acm_activation_mch_info" in discover_text
    assert "acm_switchover_test_overrides | default({})" in discover_text
    assert "register: acm_secondary_restores_info" not in discover_text
    assert includes[0] == "discover_resources.yml"
    assert includes.index("discover_resources.yml") < includes.index("verify_passive_sync.yml")


def test_activation_uses_live_restore_facts_instead_of_preflight_restore_facts():
    """Activation must not select or mutate Restores from stale preflight variables."""
    for path in [
        ACTIVATION_TASKS / "verify_passive_sync.yml",
        ACTIVATION_TASKS / "activate_restore.yml",
    ]:
        text = path.read_text()
        assert "acm_activation_restores_info.resources" in text
        assert "acm_secondary_restore_info" not in text
        assert "acm_secondary_restores_info" not in text


def test_resumable_roles_use_phase_local_discovery_facts():
    """Checkpoint resumes must not depend on facts owned by skipped preflight."""
    expected_phase_facts = {
        PRIMARY_PREP_TASKS
        / "discover_resources.yml": [
            "acm_primary_prep_mch_info",
            "acm_primary_prep_backup_schedules_info",
        ],
        PRIMARY_PREP_TASKS
        / "pause_backups.yml": [
            "acm_primary_prep_mch_info",
            "acm_primary_prep_backup_schedules_info",
        ],
        ACTIVATION_TASKS / "discover_resources.yml": ["acm_activation_mch_info"],
        ACTIVATION_TASKS / "resolve_auto_import_support.yml": ["acm_activation_mch_info"],
        FINALIZATION_TASKS
        / "discover_resources.yml": [
            "acm_finalization_backup_schedules_info",
            "acm_finalization_mch_info",
            "acm_finalization_restores_info",
        ],
        FINALIZATION_TASKS / "cleanup_restores.yml": ["acm_finalization_restores_info"],
        FINALIZATION_TASKS
        / "enable_backups.yml": [
            "acm_finalization_backup_schedules_info",
            "acm_finalization_mch_info",
        ],
        FINALIZATION_TASKS
        / "repair_backup_schedule_collision.yml": [
            "acm_finalization_backup_schedules_info",
        ],
        FINALIZATION_TASKS / "verify_backups.yml": ["acm_finalization_backup_schedules_info"],
    }
    stale_preflight_facts = {
        "acm_primary_mch_info",
        "acm_primary_backup_schedules_info",
        "acm_secondary_mch_info",
        "acm_secondary_backup_schedules_info",
        "acm_secondary_restore_info",
        "acm_secondary_restores_info",
    }

    for path, expected_facts in expected_phase_facts.items():
        text = path.read_text()
        for expected_fact in expected_facts:
            assert expected_fact in text, f"{path.name} must use {expected_fact}"
        for stale_fact in stale_preflight_facts:
            assert stale_fact not in text, f"{path.name} must not read {stale_fact}"


def test_phase_local_facts_are_not_direct_register_targets():
    """Skipped discovery tasks must not overwrite pre-seeded phase-local facts."""
    phase_local_facts = [
        "acm_primary_prep_mch_info",
        "acm_primary_prep_backup_schedules_info",
        "acm_activation_mch_info",
        "acm_finalization_backup_schedules_info",
        "acm_finalization_mch_info",
        "acm_finalization_restores_info",
    ]
    files = [
        PRIMARY_PREP_TASKS / "discover_resources.yml",
        ACTIVATION_TASKS / "discover_resources.yml",
        FINALIZATION_TASKS / "discover_resources.yml",
        FINALIZATION_TASKS / "enable_backups.yml",
        FINALIZATION_TASKS / "repair_backup_schedule_collision.yml",
    ]

    for path in files:
        text = path.read_text()
        for fact in phase_local_facts:
            assert f"register: {fact}" not in text, (
                f"{path.name} must register to a temporary variable and publish " f"{fact} only with guarded set_fact"
            )


def test_activation_requires_passive_restore_ready_before_mutation_tasks():
    """Passive readiness must be asserted before auto-import or Restore mutations run."""
    verify_text = (ACTIVATION_TASKS / "verify_passive_sync.yml").read_text()
    assert "restore_ready" in verify_text
    assert "restore_ready_reason" in verify_text

    main_tasks = _load_yaml(ACTIVATION_TASKS / "main.yml")
    block_tasks = next(task["block"] for task in main_tasks if "block" in task)
    includes = [task.get("ansible.builtin.include_tasks", "") for task in block_tasks]

    assert includes.index("verify_passive_sync.yml") < includes.index("manage_auto_import.yml")
    assert includes.index("verify_passive_sync.yml") < includes.index("activate_restore.yml")


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
        POST_ACTIVATION_TASKS / "verify_observability.yml",
        POST_ACTIVATION_TASKS / "cleanup_auto_import_annotations.yml",
        FINALIZATION_TASKS / "disable_old_hub_observability.yml",
        PRIMARY_PREP_TASKS / "manage_auto_import.yml",
    ]

    for path in files:
        text = path.read_text()
        assert "default('') != 'dry_run'" not in text
        assert "default('') == 'dry_run'" not in text
        assert "default('execute')" not in text, (
            f"{path.name} uses default('execute') which allows live mutations "
            f"when execution.mode is missing; must use default('dry_run')"
        )
        assert (
            "acm_switchover_execution.mode | default('dry_run')" in text
        ), f"{path.name} must guard live mutations with dry_run-safe default"


def test_post_identity_checkpoint_validation_defaults_to_execute():
    """Checkpoint data validation is read-only; defaulting to execute over-validates safely."""
    text = (PREFLIGHT_TASKS / "post_identity.yml").read_text()
    assert "default('execute') == 'execute'" in text
    assert "read-only validation assert" in text


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
        task for task in pre_tasks if "_argocd_resume_checkpoint_path" in task.get("ansible.builtin.set_fact", {})
    ]
    abs_fact_tasks = [
        task for task in pre_tasks if "_argocd_resume_checkpoint_path_abs" in task.get("ansible.builtin.set_fact", {})
    ]

    assert path_fact_tasks
    assert abs_fact_tasks
    for task in pre_tasks:
        facts = task.get("ansible.builtin.set_fact", {})
        assert not (
            "_argocd_resume_checkpoint_path" in facts and "_argocd_resume_checkpoint_path_abs" in facts
        ), "checkpoint path and absolute path facts must be assigned in separate tasks"


def test_preflight_validate_rbac_fails_closed_on_argocd_401():
    """The shared hub RBAC task file must fail closed on HTTP 401 during Argo CD CRD discovery.

    401 indicates broken/expired credentials, not merely missing CRD read permission
    (which is 403). Silently deferring 401 to RBAC validation would hide auth problems
    and potentially produce misleading "missing permissions" results. This mirrors the
    Python CLI behavior in preflight_coordinator.py. Since the hub-loop refactor
    (Thermos PR 39 / R2-H3), both hubs run the same shared fail-closed tasks, driven
    by the _rbac_hub_validations table in validate_rbac.yml.
    """
    hub_tasks = _load_yaml(PREFLIGHT_TASKS / "validate_rbac_hub.yml")
    main_tasks = _load_yaml(PREFLIGHT_TASKS / "validate_rbac.yml")

    loop_task = next(t for t in main_tasks if t.get("ansible.builtin.include_tasks") == "validate_rbac_hub.yml")
    assert loop_task["loop"] == "{{ _rbac_hub_validations }}"
    table_task = next(t for t in main_tasks if "_rbac_hub_validations" in t.get("ansible.builtin.set_fact", {}))
    hubs = [entry["hub"] for entry in table_task["ansible.builtin.set_fact"]["_rbac_hub_validations"]]
    assert hubs == ["primary", "secondary"], "the 401 fail-closed path must cover both hubs via the hub table"

    fail_tasks = [t for t in hub_tasks if "ansible.builtin.fail" in t]
    auth_fail_tasks = [
        t
        for t in fail_tasks
        if "authorization denied" in t.get("ansible.builtin.fail", {}).get("msg", "").lower()
        or "401" in t.get("ansible.builtin.fail", {}).get("msg", "")
    ]
    assert auth_fail_tasks, "validate_rbac_hub.yml must have a fail-closed task for 401/unauthorized"
    for task in auth_fail_tasks:
        assert (
            "{{ rbac_hub.hub }}" in task["ansible.builtin.fail"]["msg"]
        ), "401 fail-closed message must name the hub being validated"
        when = _when_text(task).lower()
        assert ".msg" in when, "401 fail-closed task must inspect the CRD discovery error message"
        assert (
            "'401'" in when and "unauthorized" in when
        ), "401 fail-closed task must be gated on 401/unauthorized discovery errors"

    unexpected_fail_tasks = [
        t for t in fail_tasks if "unable to inspect" in t.get("ansible.builtin.fail", {}).get("msg", "").lower()
    ]
    assert unexpected_fail_tasks, "validate_rbac_hub.yml must fail on unexpected CRD discovery errors"
    for task in unexpected_fail_tasks:
        assert "{{ rbac_hub.hub }}" in task["ansible.builtin.fail"]["msg"]
        when = _when_text(task)
        assert (
            "'401' not in" not in when
        ), "Unexpected-error fail task must not exclude 401 — the dedicated 401 task handles it"
        assert (
            "'unauthorized' not in" not in when
        ), "Unexpected-error fail task must not exclude unauthorized — the dedicated 401 task handles it"
        assert "'403' not in" in when, "Unexpected-error fail task must still exclude 403 (deferred to RBAC validation)"

    for task in auth_fail_tasks + unexpected_fail_tasks:
        when = _when_text(task)
        assert ".failed" not in when, (
            "Error detection must use .msg presence, not .failed — "
            "failed_when: false on k8s_info overrides .failed to False"
        )

    for task in unexpected_fail_tasks:
        when = _when_text(task)
        assert ".msg" in when, (
            "Unexpected-error fail task must gate on .msg presence " "since failed_when: false overrides .failed"
        )

    auth_indices = [i for i, t in enumerate(hub_tasks) if t in auth_fail_tasks]
    unexpected_indices = [i for i, t in enumerate(hub_tasks) if t in unexpected_fail_tasks]
    assert auth_indices and unexpected_indices
    assert auth_indices[0] < unexpected_indices[0], "401 fail task must precede the unexpected-error fail task"
