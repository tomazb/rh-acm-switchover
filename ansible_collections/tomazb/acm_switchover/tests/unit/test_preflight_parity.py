"""Static parity tests for preflight role behavior."""

import pathlib

import yaml
from jinja2 import Environment

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


def test_validate_backups_enforces_backup_and_cluster_parity_checks():
    """validate_backups.yml must include the missing critical parity checks."""
    text = (PREFLIGHT_TASKS / "validate_backups.yml").read_text()

    assert (
        "useManagedServiceAccount" in text
    ), "validate_backups.yml must enforce BackupSchedule useManagedServiceAccount"
    assert "preserveOnDelete" in text, "validate_backups.yml must enforce ClusterDeployment preserveOnDelete"
    assert "Reconciled" in text, "validate_backups.yml must verify DataProtectionApplication reconciliation"
    assert "velero_pod_count" in text, "validate_backups.yml must verify OADP/Velero presence"
    assert (
        "clusters imported after latest backup will be lost" in text
    ), "validate_backups.yml must detect clusters imported after the latest managed-clusters backup"


def test_validate_backups_use_managed_service_account_recommended_action_is_valid_jinja():
    """validate_backups.yml must keep the useManagedServiceAccount advisory expression parseable."""
    text = (PREFLIGHT_TASKS / "validate_backups.yml").read_text()
    anchor = (
        '"recommended_action": "Set spec.useManagedServiceAccount=true in the primary BackupSchedule before '
        'passive switchover"'
    )
    start = text.index(anchor)
    end = text.index("else None", start) + len("else None")
    expression = text[start:end].split('"recommended_action": ', 1)[1].strip()

    Environment().compile_expression(expression)
