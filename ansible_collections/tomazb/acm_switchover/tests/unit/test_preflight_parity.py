"""Static parity tests for preflight role behavior."""

import pathlib

from jinja2 import Environment

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
PREFLIGHT_TASKS = ROLES_DIR / "preflight" / "tasks"


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
