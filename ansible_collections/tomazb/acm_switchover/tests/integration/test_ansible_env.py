import sys
from pathlib import Path

from ansible_collections.tomazb.acm_switchover.tests.integration.conftest import (
    _ansible_env,
    _find_repo_root,
)


def test_integration_ansible_env_includes_python314_compat_path(tmp_path):
    repo_root = _find_repo_root()

    env = _ansible_env(repo_root, tmp_path)

    compat_path = repo_root / "ansible_collections/tomazb/acm_switchover/tests/support/python314_ast_compat"
    pythonpaths = env["PYTHONPATH"].split(":")

    assert str(compat_path) in pythonpaths
    assert Path(env["ANSIBLE_LOCAL_TEMP"]).is_relative_to(tmp_path)
    assert Path(env["ANSIBLE_REMOTE_TMP"]).is_relative_to(tmp_path)


def test_integration_ansible_env_disables_callback_color(monkeypatch, tmp_path):
    """Sensitive-output tests must not mistake Ansible's own color controls for leaked data."""
    monkeypatch.setenv("ANSIBLE_FORCE_COLOR", "1")

    env = _ansible_env(_find_repo_root(), tmp_path)

    assert "ANSIBLE_FORCE_COLOR" not in env
    assert env["ANSIBLE_NOCOLOR"] == "1"


def test_integration_ansible_env_pins_controller_python_interpreter(tmp_path):
    """Shipped playbook subprocesses must not discover a system Python lacking kubernetes.

    ansible-core 2.16 auto-discovery commonly selects /usr/bin/python3 for local
    connection. Nested kubernetes.core.k8s_info then fails before any API request,
    which breaks the SSA-01 live-identity barrier under the foundation-min lane.
    """
    env = _ansible_env(_find_repo_root(), tmp_path)

    assert env["ANSIBLE_PYTHON_INTERPRETER"] == sys.executable
