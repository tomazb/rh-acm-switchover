"""Static parity tests for preflight role behavior."""

import pathlib

import yaml
from jinja2 import Environment
from preflight_task_text import validate_backups_text

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
PREFLIGHT_TASKS = ROLES_DIR / "preflight" / "tasks"


def _load_yaml(name: str) -> list[dict]:
    return yaml.safe_load((PREFLIGHT_TASKS / name).read_text())


def _include_task_names(tasks: list[dict]) -> list[str]:
    includes = []
    for task in tasks:
        include = task.get("ansible.builtin.include_tasks")
        if include:
            includes.append(include)
        if "block" in task:
            includes.extend(_include_task_names(task["block"]))
        if "rescue" in task:
            includes.extend(_include_task_names(task["rescue"]))
    return includes


def test_validate_kubeconfigs_uses_direct_api_probe():
    """Kubeconfig validation must not depend on MultiClusterHub discovery."""
    text = (PREFLIGHT_TASKS / "validate_kubeconfigs.yml").read_text()

    assert "kind: Namespace" in text, "validate_kubeconfigs.yml must probe a core resource directly"
    assert "name: default" in text, "validate_kubeconfigs.yml must query the default namespace for reachability"
    assert "acm_primary_mch_info" not in text, "validate_kubeconfigs.yml must not depend on MCH discovery"
    assert "acm_secondary_mch_info" not in text, "validate_kubeconfigs.yml must not depend on MCH discovery"


def test_preflight_discovers_dpa_velero_and_managed_clusters():
    """discover_resources.yml must fetch the resources needed for parity backup checks."""
    text = (PREFLIGHT_TASKS / "discover_resources.yml").read_text()

    assert "kind: DataProtectionApplication" in text, "discover_resources.yml must query DataProtectionApplications"
    assert "app.kubernetes.io/name=velero" in text, "discover_resources.yml must query Velero pods"
    assert "kind: ManagedCluster" in text, "discover_resources.yml must query ManagedClusters"


def test_preflight_mch_discovery_is_live_not_preseeded():
    """Production preflight must not let stale MCH variables satisfy version validation."""
    primary_task = next(
        task for task in _load_yaml("discover_resources.yml") if "primary MultiClusterHub" in task["name"]
    )
    secondary_task = next(
        task for task in _load_yaml("discover_resources.yml") if "secondary MultiClusterHub" in task["name"]
    )
    primary_when = " ".join(primary_task.get("when", []))
    secondary_when = str(secondary_task.get("when", ""))

    assert primary_task["register"] == "acm_primary_mch_info"
    assert secondary_task["register"] == "acm_secondary_mch_info"
    assert "mode | default('dry_run') == 'execute'" in primary_when
    assert "mode | default('dry_run') == 'execute'" in secondary_when
    assert "or (acm_primary_mch_info is not defined)" in primary_when
    assert "or (acm_secondary_mch_info is not defined)" in secondary_when


def test_preflight_persists_observability_detection_for_later_phases():
    """Collection preflight must carry Python-equivalent Observability detection through checkpoints."""
    text = (PREFLIGHT_TASKS / "main.yml").read_text()

    assert "acm_switchover_primary_has_observability" in text
    assert "acm_switchover_secondary_has_observability" in text
    assert "primary_has_observability" in text
    assert "secondary_has_observability" in text


def test_preflight_rbac_skips_observability_permissions_when_observability_absent():
    """Collection RBAC checks must mirror Python's automatic Observability absence handling."""
    text = (PREFLIGHT_TASKS / "validate_rbac.yml").read_text()
    hub_text = (PREFLIGHT_TASKS / "validate_rbac_hub.yml").read_text()

    assert "_rbac_skip_observability" in text
    assert "not (acm_switchover_primary_has_observability | default(false) | bool)" in text
    assert "not (acm_switchover_secondary_has_observability | default(false) | bool)" in text
    assert 'skip_observability: "{{ _rbac_skip_observability }}"' in hub_text


def test_preflight_rbac_validates_hubs_through_shared_hub_loop():
    """Both hubs must flow through one parameterized include, mirroring Python H1's hub table + loop."""
    tasks = _load_yaml("validate_rbac.yml")

    hub_includes = [t for t in tasks if t.get("ansible.builtin.include_tasks") == "validate_rbac_hub.yml"]
    assert len(hub_includes) == 1, "validate_rbac.yml must drive hub validation through exactly one shared include"
    loop_task = hub_includes[0]
    assert loop_task["loop"] == "{{ _rbac_hub_validations }}"
    assert loop_task["loop_control"]["loop_var"] == "rbac_hub"
    assert loop_task["when"] == "rbac_hub.enabled | bool"
    assert (PREFLIGHT_TASKS / "validate_rbac_hub.yml").is_file()

    table_task = next(t for t in tasks if "_rbac_hub_validations" in t.get("ansible.builtin.set_fact", {}))
    entries = table_task["ansible.builtin.set_fact"]["_rbac_hub_validations"]
    assert [entry["hub"] for entry in entries] == ["primary", "secondary"]
    primary, secondary = entries
    assert "restore_only" in str(primary["enabled"]), "primary hub entry must be disabled in restore-only mode"
    assert secondary["enabled"] is True, "secondary hub validation must be unconditional"


def test_preflight_rbac_primary_restore_only_skip_expression_is_consistent():
    """The disabled primary row must not evaluate primary hub fields through a different restore-only rule."""
    tasks = _load_yaml("validate_rbac.yml")
    table_task = next(t for t in tasks if "_rbac_hub_validations" in t.get("ansible.builtin.set_fact", {}))
    primary = table_task["ansible.builtin.set_fact"]["_rbac_hub_validations"][0]
    restore_only_expression = "acm_switchover_operation.restore_only | default(false)"

    assert primary["enabled"] == "{{ not (%s) }}" % restore_only_expression
    for field in ("kubeconfig", "context"):
        assert "if (%s)" % restore_only_expression in primary[field]
        assert "if (%s | bool)" % restore_only_expression not in primary[field]


def test_preflight_rbac_hub_loop_preserves_registered_fact_names():
    """The shared hub file must re-publish every per-hub fact the pre-loop file registered."""
    hub_text = (PREFLIGHT_TASKS / "validate_rbac_hub.yml").read_text()
    main_text = (PREFLIGHT_TASKS / "validate_rbac.yml").read_text()

    for published in [
        '"_rbac_argocd_app_crd_{{ rbac_hub.hub }}"',
        '"_rbac_argocd_instance_crd_{{ rbac_hub.hub }}"',
        '"_rbac_argocd_install_type_{{ rbac_hub.hub }}"',
        '"_rbac_expanded_{{ rbac_hub.hub }}"',
        '"_rbac_denied_permissions_{{ rbac_hub.hub }}"',
        '"acm_{{ rbac_hub.hub }}_rbac_validation"',
    ]:
        assert published in hub_text, f"validate_rbac_hub.yml must re-publish {published}"

    assert "acm_primary_rbac_validation.results" in main_text
    assert "acm_secondary_rbac_validation.results" in main_text
    assert "acm_managed_cluster_rbac_validation.results" in main_text


def test_preflight_rbac_hub_loop_keeps_primary_only_and_secondary_only_behavior():
    """Asymmetries must live in the hub table as data, exactly like Python H1's hub_validations."""
    tasks = _load_yaml("validate_rbac.yml")
    table_task = next(t for t in tasks if "_rbac_hub_validations" in t.get("ansible.builtin.set_fact", {}))
    primary, secondary = table_task["ansible.builtin.set_fact"]["_rbac_hub_validations"]

    assert "'decommission'" in primary["include_decommission"]
    assert "_rbac_include_old_hub_finalization_primary" in primary["include_old_hub_finalization"]
    assert secondary["include_decommission"] is False
    assert secondary["include_old_hub_finalization"] is False

    old_hub_task = next(t for t in tasks if "old-hub finalization" in t["name"])
    assert "restore_only" in str(old_hub_task.get("when", "")), "old-hub finalization must stay primary-only input"


def test_preflight_rbac_managed_cluster_validation_stays_separate_from_hub_loop():
    """Managed-cluster RBAC validation keeps its own scope/loop, matching Python's separate scope."""
    tasks = _load_yaml("validate_rbac.yml")
    includes = [t.get("ansible.builtin.include_tasks") for t in tasks if t.get("ansible.builtin.include_tasks")]
    assert "validate_managed_cluster_rbac.yml" in includes

    hub_text = (PREFLIGHT_TASKS / "validate_rbac_hub.yml").read_text()
    assert "validate_managed_cluster_rbac" not in hub_text
    assert "managed_cluster" not in hub_text


def test_preflight_skip_requires_observability_checkpoint_data():
    """Skipped preflight checkpoints must not lose post-activation Observability gating inputs."""
    text = (PREFLIGHT_TASKS / "main.yml").read_text()

    assert "Skipped preflight checkpoint is missing required operational metadata" in text
    assert "expected_managed_cluster_names/expected_managed_cluster_count" in text
    assert "primary_has_observability/secondary_has_observability" in text


def test_preflight_runs_auto_import_strategy_validator_after_version_checks():
    """Collection preflight must keep Python's ACM 2.14+ auto-import advisory."""
    main = _load_yaml("main.yml")
    include_names = _include_task_names(main)

    assert "validate_versions.yml" in include_names
    assert "validate_auto_import.yml" in include_names
    assert include_names.index("validate_versions.yml") < include_names.index("validate_auto_import.yml")

    tasks = _load_yaml("validate_auto_import.yml")
    text = (PREFLIGHT_TASKS / "validate_auto_import.yml").read_text()
    assert tasks, "validate_auto_import.yml must be parseable YAML with tasks"
    assert "autoImportStrategy" in text
    assert "ImportAndSync" in text
    assert "ImportOnly" in text
    assert "local-cluster" in text
    assert "2.14.0" in text
    assert '"severity": "warning"' in text


def test_preflight_runs_controller_tooling_advisory():
    """Collection preflight should surface Python-equivalent tooling guidance without failing."""
    main = _load_yaml("main.yml")
    include_names = _include_task_names(main)

    assert "validate_tooling.yml" in include_names

    tasks = _load_yaml("validate_tooling.yml")
    text = (PREFLIGHT_TASKS / "validate_tooling.yml").read_text()
    assert tasks, "validate_tooling.yml must be parseable YAML with tasks"
    assert "command -v oc" in text
    assert "command -v kubectl" in text
    assert "command -v jq" in text
    assert '"severity": "warning"' in text


def test_validate_versions_requires_exact_acm_version_match():
    """Collection preflight must match Python's exact ACM version equality check."""
    text = (PREFLIGHT_TASKS / "validate_versions.yml").read_text()

    assert "acm_primary_version == acm_secondary_version" in text
    assert ".split('.')[0:2]" not in text
    assert "same ACM version" in text


def test_validate_backups_enforces_backup_and_cluster_parity_checks():
    """validate_backups.yml must include the missing critical parity checks."""
    text = validate_backups_text()

    assert "InProgress" in text, "validate_backups.yml must wait for in-progress Velero backups"
    assert "until:" in text, "validate_backups.yml must poll backup state before judging latest backup phase"
    assert (
        "Read primary hub backups after in-progress wait" in text
    ), "validate_backups.yml must refresh backup facts after waiting"
    assert (
        "latest backup" in text and "unexpected state" in text
    ), "validate_backups.yml must fail when the latest Velero backup is not Completed"
    assert (
        "no managed-clusters backups found" in text
    ), "validate_backups.yml must fail when joined clusters exist without a managed-clusters backup artifact"
    assert (
        "latest managed-clusters backup" in text and "not completed" in text
    ), "validate_backups.yml must fail when the latest managed-clusters backup is not Completed"
    assert (
        "useManagedServiceAccount" in text
    ), "validate_backups.yml must enforce BackupSchedule useManagedServiceAccount"
    assert "preserveOnDelete" in text, "validate_backups.yml must enforce ClusterDeployment preserveOnDelete"
    assert "Reconciled" in text, "validate_backups.yml must verify DataProtectionApplication reconciliation"
    assert "velero_pod_count" in text, "validate_backups.yml must verify OADP/Velero presence"
    assert (
        "clusters imported after latest backup will be lost" in text
    ), "validate_backups.yml must detect clusters imported after the latest managed-clusters backup"
    msa_block = text[
        text.index('"id": "preflight-backup-schedule-use-managed-service-account"') : text.index(
            "- name: Record primary hub BackupStorageLocation health"
        )
    ]
    assert 'status": "skip"' not in msa_block
    assert "not required for full restore" not in msa_block
    assert (
        "acm_switchover_operation.method == 'full'" not in msa_block
    ), "useManagedServiceAccount validation must be critical for full and passive non-restore-only switchovers"


def test_validate_backups_is_split_into_focused_task_files():
    """The backup preflight wrapper should remain small and delegate focused validation sections."""
    tasks = yaml.safe_load((PREFLIGHT_TASKS / "validate_backups.yml").read_text())
    includes = [task.get("ansible.builtin.include_tasks") for task in tasks]

    assert includes == [
        "validate_backups/progress.yml",
        "validate_backups/artifacts.yml",
        "validate_backups/schedule_storage.yml",
        "validate_backups/infrastructure.yml",
        "validate_backups/managed_cluster_backups.yml",
    ]
    for include in includes:
        assert (PREFLIGHT_TASKS / include).is_file()


def test_validate_backups_records_remaining_in_progress_backups_after_wait():
    """Collection preflight must fail if in-progress Velero backups remain after polling."""
    text = validate_backups_text()

    assert "preflight-backup-in-progress-after-wait" in text
    assert "backup(s) still InProgress after waiting" in text
    assert "_acm_primary_backup_wait_info.attempts" in text
    assert "preflight-backup-in-progress-after-wait-restore-only" in text
    assert "restore-only target backup(s) still InProgress after waiting" in text
    assert "_acm_secondary_backup_wait_info.attempts" in text
    assert "from_json" in text


def test_validate_backups_use_managed_service_account_recommended_action_is_valid_jinja():
    """validate_backups.yml must keep the useManagedServiceAccount advisory expression parseable."""
    text = validate_backups_text()
    anchor = (
        '"recommended_action": "Set spec.useManagedServiceAccount=true in the primary BackupSchedule before '
        'switchover"'
    )
    start = text.index(anchor)
    end = text.index("else None", start) + len("else None")
    expression = text[start:end].split('"recommended_action": ', 1)[1].strip()

    Environment().compile_expression(expression)
