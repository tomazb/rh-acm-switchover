"""Tests for post_activation klusterlet auto-remediation."""

import pathlib

import yaml

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
POST_ACTIVATION_TASKS = ROLES_DIR / "post_activation" / "tasks"
POST_ACTIVATION_DEFAULTS = ROLES_DIR / "post_activation" / "defaults"


def _when_text(task: dict) -> str:
    when = task.get("when", [])
    if isinstance(when, str):
        return when
    return " ".join(str(item) for item in when)


def test_fix_klusterlet_file_exists():
    """fix_klusterlet.yml must exist in post_activation tasks."""
    assert (POST_ACTIVATION_TASKS / "fix_klusterlet.yml").exists()


def test_fix_klusterlet_single_file_exists():
    """fix_klusterlet_single.yml must exist in post_activation tasks."""
    assert (POST_ACTIVATION_TASKS / "fix_klusterlet_single.yml").exists()


def test_verify_klusterlet_includes_remediation():
    """verify_klusterlet.yml must include fix_klusterlet.yml for auto-remediation."""
    content = (POST_ACTIVATION_TASKS / "verify_klusterlet.yml").read_text()
    assert "fix_klusterlet.yml" in content, "Must include fix_klusterlet.yml"
    assert "acm_switchover_managed_clusters" in content, "Must guard on managed_clusters"


def test_verify_klusterlet_probes_connections_even_when_cluster_status_is_green():
    """Green ManagedCluster conditions can be stale, so klusterlet secrets still need probing."""
    tasks = yaml.safe_load((POST_ACTIVATION_TASKS / "verify_klusterlet.yml").read_text())

    probe_tasks = [
        task for task in tasks if task.get("ansible.builtin.include_tasks") == "verify_klusterlet_connections.yml"
    ]
    assert probe_tasks, "verify_klusterlet.yml must include the green-path klusterlet probe"
    probe_when = _when_text(probe_tasks[0])
    assert "acm_switchover_managed_clusters" in probe_when
    assert "pending" not in probe_when, "green-path probe must not be gated by pending cluster status"


def test_verify_klusterlet_connection_probe_can_remediate_wrong_hub_secret():
    """The probe must inspect hub kubeconfig secrets through the bounded module."""
    path = POST_ACTIVATION_TASKS / "verify_klusterlet_connections.yml"
    assert path.exists(), "verify_klusterlet_connections.yml must exist"
    content = path.read_text()

    assert "tomazb.acm_switchover.acm_klusterlet_probe" in content
    assert "klusterlet_probe_workers" in content
    assert "register: _klusterlet_probe_result" in content
    assert (
        "include_tasks: verify_klusterlet_connection_single.yml" not in content
    ), "Probe must not loop through per-cluster task includes"


def test_fix_klusterlet_uses_bounded_remediation_module():
    """Remediation must be handled by the bounded module, not a sequential task loop."""
    content = (POST_ACTIVATION_TASKS / "fix_klusterlet.yml").read_text()

    assert "tomazb.acm_switchover.acm_klusterlet_remediate" in content
    assert "klusterlet_remediation_workers" in content
    assert "strict_remediation" in content
    assert (
        "include_tasks: fix_klusterlet_single.yml" not in content
    ), "Remediation must not loop through per-cluster task includes"


def test_verify_klusterlet_records_module_remediation_attempts():
    """Re-verification must be gated on module-reported remediation attempts."""
    content = (POST_ACTIVATION_TASKS / "verify_klusterlet.yml").read_text()

    assert "_klusterlet_remediation_result is defined" in content
    assert "_klusterlet_initial_probe_result.wrong_hub_clusters" in content


def test_verify_klusterlet_reprobes_after_remediation():
    """Wrong-hub remediation must be followed by a second klusterlet probe."""
    tasks = yaml.safe_load((POST_ACTIVATION_TASKS / "verify_klusterlet.yml").read_text())

    fix_index = next(
        index for index, task in enumerate(tasks) if task.get("ansible.builtin.include_tasks") == "fix_klusterlet.yml"
    )
    reprobe_indexes = [
        index
        for index, task in enumerate(tasks)
        if task.get("ansible.builtin.include_tasks") == "verify_klusterlet_connections.yml" and index > fix_index
    ]

    assert reprobe_indexes, "verify_klusterlet.yml must re-probe after remediation"


def test_verify_klusterlet_fails_failed_or_persistent_wrong_hub_after_recheck():
    """Failed fixes and persistent wrong-hub probes must become fatal after re-check."""
    tasks = yaml.safe_load((POST_ACTIVATION_TASKS / "verify_klusterlet.yml").read_text())

    fail_tasks = [task for task in tasks if "ansible.builtin.fail" in task]
    assert fail_tasks, "verify_klusterlet.yml must fail strict remediation errors"

    fail_when = _when_text(fail_tasks[0])
    assert "_klusterlet_remediation_result.failed_clusters" in fail_when
    assert "_klusterlet_post_remediation_probe_result.wrong_hub_clusters" in fail_when
    assert "_klusterlet_post_remediation_probe_result.skipped_clusters" not in fail_when


def test_fix_klusterlet_filters_candidates_to_clusters_with_kubeconfigs():
    """The skip branch must apply when candidates lack managed-cluster kubeconfigs."""
    content = (POST_ACTIVATION_TASKS / "fix_klusterlet.yml").read_text()

    assert "acm_switchover_managed_clusters[item].kubeconfig" in content


def test_post_activation_defaults_include_klusterlet_concurrency_and_strict_mode():
    """Defaults must expose worker controls and preserve non-strict behavior."""
    defaults = yaml.safe_load((POST_ACTIVATION_DEFAULTS / "main.yml").read_text())

    assert defaults["acm_switchover_execution"]["concurrency"]["klusterlet_probe_workers"] == 10
    assert defaults["acm_switchover_execution"]["concurrency"]["klusterlet_remediation_workers"] == 10
    assert defaults["acm_switchover_features"]["klusterlet"]["strict_remediation"] is False


def test_verify_klusterlet_connection_probe_omits_missing_secondary_context():
    """Hub import-secret reads must not require an explicit secondary context."""
    tasks = yaml.safe_load((POST_ACTIVATION_TASKS / "verify_klusterlet_connections.yml").read_text())
    probe_task = next(task for task in tasks if "tomazb.acm_switchover.acm_klusterlet_probe" in task)

    assert probe_task["tomazb.acm_switchover.acm_klusterlet_probe"]["secondary_hub"]["context"] == (
        "{{ acm_switchover_hubs.secondary.context | default(omit) }}"
    )


def test_defaults_include_managed_clusters():
    """post_activation defaults must define acm_switchover_managed_clusters."""
    defaults = yaml.safe_load((POST_ACTIVATION_DEFAULTS / "main.yml").read_text())
    assert "acm_switchover_managed_clusters" in defaults, "Defaults must define acm_switchover_managed_clusters"
    assert defaults["acm_switchover_managed_clusters"] == {}, "Default must be empty dict"


def test_klusterlet_module_has_required_operations():
    """The remediation module must carry the key remediation operations."""
    module_path = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "module_utils" / "klusterlet.py"
    content = module_path.read_text()

    assert "import.yaml" in content, "Must fetch import secret from hub"
    assert "BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME" in content, "Must handle bootstrap hub kubeconfig secret"
    assert "patch_namespaced_deployment" in content, "Must restart klusterlet deployment"
    assert "MANAGED_CLUSTER_AGENT_NAMESPACE" in content, "Must reference agent namespace"
