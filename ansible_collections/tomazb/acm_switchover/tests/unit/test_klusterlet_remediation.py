"""Tests for post_activation klusterlet auto-remediation."""

import pathlib
import yaml

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
POST_ACTIVATION_TASKS = ROLES_DIR / "post_activation" / "tasks"
POST_ACTIVATION_DEFAULTS = ROLES_DIR / "post_activation" / "defaults"


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


def test_defaults_include_managed_clusters():
    """post_activation defaults must define acm_switchover_managed_clusters."""
    defaults = yaml.safe_load((POST_ACTIVATION_DEFAULTS / "main.yml").read_text())
    assert "acm_switchover_managed_clusters" in defaults, \
        "Defaults must define acm_switchover_managed_clusters"
    assert defaults["acm_switchover_managed_clusters"] == {}, \
        "Default must be empty dict"


def test_fix_klusterlet_single_has_required_steps():
    """fix_klusterlet_single.yml must have the 4 remediation steps."""
    content = (POST_ACTIVATION_TASKS / "fix_klusterlet_single.yml").read_text()
    # Check for the key operations
    assert "import" in content.lower() and "secret" in content.lower(), \
        "Must fetch import secret from hub"
    assert "bootstrap-hub-kubeconfig" in content, \
        "Must handle bootstrap-hub-kubeconfig secret"
    assert "klusterlet" in content.lower(), \
        "Must restart klusterlet deployment"
    assert "open-cluster-management-agent" in content, \
        "Must reference agent namespace"
