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
