"""Static guardrails for CI and local test runner behavior."""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci-cd.yml"
COLLECTION_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ansible-collection-foundation.yml"
RUN_TESTS = REPO_ROOT / "run_tests.sh"
AGENT_INSTRUCTIONS = REPO_ROOT / "AGENTS.md"
SETUP_CFG = REPO_ROOT / "setup.cfg"

# Matches the AGENTS.md line that documents where agent worktrees are created, e.g.
# "Use one isolated `.claude/worktrees/thermos-*` worktree and one branch per PR."
DOCUMENTED_WORKTREE_DIR = re.compile(r"isolated `([^`]+/worktrees)/[^`]*` worktree")


def test_root_ci_excludes_e2e_tests_by_marker():
    text = CI_WORKFLOW.read_text()

    assert '-m "not e2e"' in text or "-m 'not e2e'" in text
    assert "--ignore=tests/release" in text
    assert "python -m pytest tests/release -q" in text


def test_collection_ci_covers_restore_only_syntax_and_runtime_tests():
    text = COLLECTION_WORKFLOW.read_text()

    assert "playbooks/restore_only.yml --syntax-check" in text
    assert "tests/integration/" in text
    assert "tests/scenario/" in text


def test_ci_version_check_uses_runtime_version_metadata():
    text = CI_WORKFLOW.read_text()

    assert 'grep -q "version.*1.0.0"' not in text
    assert "from lib import __version__, __version_date__" in text


def test_github_actions_use_node24_action_versions():
    workflow_text = "\n".join(path.read_text() for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")))

    assert "actions/checkout@v4" not in workflow_text
    assert "actions/setup-python@v5" not in workflow_text
    assert "actions/checkout@v6" in workflow_text
    assert "actions/setup-python@v6" in workflow_text


def test_run_tests_quality_gates_are_explicit_and_scoped():
    text = RUN_TESTS.read_text()

    assert 'STRICT_QUALITY="${STRICT_QUALITY:-1}"' in text
    assert "QUALITY_PATHS=" in text
    assert "ansible_collections/tomazb/acm_switchover/plugins" in text
    assert "ansible_collections/tomazb/acm_switchover/tests" in text
    assert "tests" in text
    assert "black --check --line-length 120 ." not in text
    assert "isort --check-only --profile black --line-length 120 ." not in text


def test_release_framework_ci_job_is_explicit_and_not_overstated():
    text = CI_WORKFLOW.read_text()

    assert "Release Readiness" not in text
    assert "Release Framework Tests" in text
    assert "permissions:\n      contents: read" in text
    assert "persist-credentials: false" in text


def test_run_tests_executes_release_framework_explicitly():
    text = RUN_TESTS.read_text()

    assert "--ignore=tests/release" in text
    assert "python -m pytest tests/release -q" in text


def test_agent_instructions_document_a_worktree_directory():
    match = DOCUMENTED_WORKTREE_DIR.search(AGENT_INSTRUCTIONS.read_text())

    assert match, "AGENTS.md no longer documents where agent worktrees are created"
    assert not Path(match.group(1)).is_absolute()


def test_flake8_excludes_the_documented_worktree_directory(tmp_path):
    """Prove `flake8 .` skips the documented worktree location using the real setup.cfg.

    CI and run_tests.sh both invoke `flake8 .` from the repository root. flake8 resolves
    any exclude pattern containing a path separator against the current working directory,
    so this reproduces that layout in a throwaway tree instead of grepping setup.cfg for a
    string that may not actually match anything.
    """
    pytest.importorskip("flake8")
    documented = DOCUMENTED_WORKTREE_DIR.search(AGENT_INSTRUCTIONS.read_text())
    assert documented, "AGENTS.md no longer documents where agent worktrees are created"

    shutil.copy(SETUP_CFG, tmp_path / "setup.cfg")
    worktree_probe = tmp_path / documented.group(1) / "probe-slice" / "probe.py"
    worktree_probe.parent.mkdir(parents=True)
    worktree_probe.write_text("undefined_name_inside_worktree\n")
    (tmp_path / "probe.py").write_text("undefined_name_at_repo_root\n")

    result = subprocess.run(
        [sys.executable, "-m", "flake8", ".", "--select=F821"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    # Control: flake8 really scanned this tree, so an absent worktree finding means excluded.
    assert "undefined_name_at_repo_root" in result.stdout, result.stdout + result.stderr
    assert "undefined_name_inside_worktree" not in result.stdout, result.stdout + result.stderr
