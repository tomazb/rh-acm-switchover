import os
import subprocess
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


def test_integration_ansible_env_gives_each_call_its_own_short_existing_tmpdir(monkeypatch, tmp_path):
    """Each call gets its own existing TMPDIR, and it is not placed under tmp_path (#314).

    A shared TMPDIR shares the kubernetes.core and kubernetes.dynamic discovery caches
    between runs, and tempfile.gettempdir() silently falls back to /tmp when TMPDIR does not
    exist. An inherited TMPDIR (common on CI runners) must not become the per-call directory.
    The directory must not sit under tmp_path either: ansible-core 2.21 binds a Unix socket
    beneath TMPDIR, and pytest-xdist tmp_paths are too deep for one.
    """
    shared = tmp_path / "inherited"
    shared.mkdir()
    monkeypatch.setenv("TMPDIR", str(shared))

    first = Path(_ansible_env(_find_repo_root(), tmp_path)["TMPDIR"])
    second = Path(_ansible_env(_find_repo_root(), tmp_path)["TMPDIR"])

    assert first.is_dir() and second.is_dir()
    assert len({first, second, shared}) == 3
    assert not first.is_relative_to(tmp_path) and not second.is_relative_to(tmp_path)


def test_integration_ansible_env_tmpdirs_are_removed_when_the_process_exits(tmp_path):
    """TMPDIRs live outside tmp_path, so pytest's tmp_path retention cannot remove them (#314)."""
    repo_root = _find_repo_root()
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from ansible_collections.tomazb.acm_switchover.tests.conftest import _ansible_env\n"
        "print(_ansible_env(Path(sys.argv[1]), Path(sys.argv[2]))['TMPDIR'])\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script, str(repo_root), str(tmp_path)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(repo_root)},
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    tmpdir = Path(completed.stdout.strip())
    assert tmpdir.is_absolute(), completed.stdout
    assert not tmpdir.exists()
    assert not tmpdir.parent.exists()


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
