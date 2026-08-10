"""Static guardrails for CI and local test runner behavior."""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci-cd.yml"
COLLECTION_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ansible-collection-foundation.yml"
RUN_TESTS = REPO_ROOT / "run_tests.sh"
AGENT_INSTRUCTIONS = REPO_ROOT / "AGENTS.md"
SETUP_CFG = REPO_ROOT / "setup.cfg"

# Matches the AGENTS.md line that documents where agent worktrees are created, e.g.
# "Use one isolated `.claude/worktrees/thermos-*` worktree and one branch per PR."
DOCUMENTED_WORKTREE_DIR = re.compile(r"isolated `([^`]+/worktrees)/[^`]*` worktree")


def _documented_worktree_directory() -> Path:
    match = DOCUMENTED_WORKTREE_DIR.search(AGENT_INSTRUCTIONS.read_text())
    assert match, "AGENTS.md no longer documents where agent worktrees are created"

    documented = Path(match.group(1))
    assert not documented.is_absolute()
    assert ".." not in documented.parts
    return documented


def test_root_ci_excludes_e2e_tests_by_marker():
    text = CI_WORKFLOW.read_text()

    assert '-m "not e2e"' in text or "-m 'not e2e'" in text
    assert "--ignore=tests/release" in text
    assert "python -m pytest tests/release -q" in text


COLLECTION_PLAYBOOK_DIR = "ansible_collections/tomazb/acm_switchover/playbooks"


def _strip_shell_comments(script: str) -> str:
    """Drop shell comments, whole-line and inline.

    Removing only whole-line comments leaves `ansible-playbook "$p" # --syntax-check`
    looking like a syntax check, because a line-scanning pattern reaches the flag
    inside the comment. Stripping from the `#` token onwards fails closed: text
    that is not executed cannot satisfy any assertion below.
    """
    return "\n".join(re.sub(r"(?<!\S)#.*$", "", line) for line in script.splitlines())


def _foundation_run_scripts() -> list:
    """Return the foundation job's executable shell, with comments removed.

    Reading the workflow as raw text would accept a command that has been
    commented out or moved into an `echo`, so coverage is judged only from what
    the runner would actually execute.
    """
    workflow = yaml.safe_load(COLLECTION_WORKFLOW.read_text())
    return [_strip_shell_comments(step["run"]) for step in workflow["jobs"]["foundation"]["steps"] if step.get("run")]


# A command must be the thing being run, not a word inside an `echo`, a comment,
# or another command's arguments. Anchoring to the start of a line, after any
# environment assignments, is what separates "CI runs this" from "this text
# appears in the file".
_ENV_PREFIX = r"(?:[A-Za-z_][A-Za-z0-9_]*=\S*[ \t]+)*"
_PLAYBOOK_COMMAND = rf"(?m)^[ \t]*{_ENV_PREFIX}(?:sudo[ \t]+|time[ \t]+)?ansible-playbook[ \t]+"
_PYTEST_COMMAND = rf"(?m)^[ \t]*{_ENV_PREFIX}(?:python[0-9.]*[ \t]+-m[ \t]+)?pytest[ \t]+"

# The loop body is what runs per playbook; a command after `done` runs once, with
# the loop variable holding only its final value.
_PLAYBOOK_LOOP = re.compile(
    r"for[ \t]+(?P<var>\w+)[ \t]+in[ \t]+[^\n;]*" + re.escape(COLLECTION_PLAYBOOK_DIR) + r"/\*\.yml[ \t]*;?[ \t]*\n?"
    r"[ \t]*do\b(?P<body>.*?)\bdone\b",
    re.DOTALL,
)


def _syntax_checked_playbooks(scripts: list, playbook_names: set) -> set:
    """Names of playbooks an executed `ansible-playbook --syntax-check` would reach."""
    covered = set()
    for script in scripts:
        # Glob form: the check must run *inside* a loop over the collection's own
        # playbook directory, driven by that loop's variable.
        for match in _PLAYBOOK_LOOP.finditer(script):
            variable = match.group("var")
            fed_to_check = _PLAYBOOK_COMMAND + rf"\"?\$\{{?{variable}\}}?\"?[^\n]*--syntax-check"
            if re.search(fed_to_check, match.group("body")):
                covered |= playbook_names
        # Explicit form: an executed invocation naming the playbook by its full path.
        for name in playbook_names:
            named = rf"[^\n]*{re.escape(COLLECTION_PLAYBOOK_DIR)}/{re.escape(name)}[^\n]*--syntax-check"
            if re.search(_PLAYBOOK_COMMAND + named, script):
                covered.add(name)
    return covered


def _runs_pytest_for(scripts: list, path_fragment: str) -> bool:
    """Whether an executed pytest command targets the given path."""
    pattern = _PYTEST_COMMAND + rf"[^\n]*{re.escape(path_fragment)}"
    return any(re.search(pattern, script) for script in scripts)


def test_syntax_check_detection_ignores_non_executed_text():
    """Regression cases for the guardrail's own detection logic.

    Each script below mentions a syntax check without running one. If any is
    reported as covered, the guardrail would pass after CI stopped checking
    playbooks, which is the failure mode it exists to prevent.
    """
    names = {"restore_only.yml"}
    glob_path = COLLECTION_PLAYBOOK_DIR
    not_executed = {
        "echoed": f"echo ansible-playbook {glob_path}/restore_only.yml --syntax-check",
        "whole-line comment": f"# ansible-playbook {glob_path}/restore_only.yml --syntax-check",
        "trailing comment": f'echo "skipped"  # ansible-playbook {glob_path}/restore_only.yml --syntax-check',
        "flag only inside an inline comment": (
            f"for playbook in {glob_path}/*.yml; do\n" '  ansible-playbook "${playbook}" # --syntax-check\n' "done"
        ),
        "loop echoed only": (
            f"for playbook in {glob_path}/*.yml; do\n" '  echo ansible-playbook "${playbook}" --syntax-check\n' "done"
        ),
        "check runs after the loop, not inside it": (
            f"for playbook in {glob_path}/*.yml; do\n"
            '  echo "${playbook}"\n'
            "done\n"
            'ansible-playbook "${playbook}" --syntax-check'
        ),
        "loop over a different playbook directory": (
            "for playbook in some/other/playbooks/*.yml; do\n"
            '  ansible-playbook "${playbook}" --syntax-check\n'
            "done"
        ),
        "explicit invocation from a different directory": (
            f"ansible-playbook some/other/playbooks/restore_only.yml --syntax-check"
        ),
        "argument of another command": f"grep ansible-playbook {glob_path}/restore_only.yml --syntax-check",
    }

    for label, script in not_executed.items():
        assert (
            _syntax_checked_playbooks([_strip_shell_comments(script)], names) == set()
        ), f"{label} should not count as a syntax check"

    executed = {
        "explicit": f"ansible-playbook {glob_path}/restore_only.yml --syntax-check",
        "loop": (f"for playbook in {glob_path}/*.yml; do\n" '  ansible-playbook "${playbook}" --syntax-check\n' "done"),
        "loop with piped output": (
            f"for playbook in {glob_path}/*.yml; do\n"
            '  ansible-playbook "${playbook}" --syntax-check 2>&1 | tee -a "${log}" || status=1\n'
            "done"
        ),
    }

    for label, script in executed.items():
        assert (
            _syntax_checked_playbooks([_strip_shell_comments(script)], names) == names
        ), f"{label} should count as a syntax check"


def test_pytest_detection_requires_an_executed_command():
    """`tests/integration/` appearing in an echo or comment is not a test run."""
    path = "ansible_collections/tomazb/acm_switchover/tests/integration/"

    assert not _runs_pytest_for([_strip_shell_comments(f"echo pytest {path}")], path)
    assert not _runs_pytest_for([_strip_shell_comments(f"# pytest {path} -q")], path)
    assert not _runs_pytest_for([_strip_shell_comments(f'echo "would run {path}"')], path)

    assert _runs_pytest_for([f"pytest {path} -q"], path)
    assert _runs_pytest_for([f"PYTHONPATH=. pytest {path} -q"], path)
    assert _runs_pytest_for([f"PYTHONPATH=. python -m pytest {path} -q"], path)


def test_collection_ci_covers_every_shipped_playbook_and_runtime_tests():
    """Collection CI must actually execute a syntax check for every shipped playbook.

    The workflow may enumerate playbooks individually or iterate them with a
    glob, so this asserts the coverage property rather than any one literal
    command: a hand-maintained list can fall behind the shipped set, and a glob
    that has been commented out still leaves its text in the file.
    """
    playbook_dir = REPO_ROOT / "ansible_collections" / "tomazb" / "acm_switchover" / "playbooks"
    playbook_names = {playbook.name for playbook in playbook_dir.glob("*.yml")}

    assert playbook_names, "the collection should ship playbooks"
    # restore_only.yml regressed out of CI coverage once; name it so the
    # set-based assertion below cannot pass vacuously if the playbook is dropped.
    assert "restore_only.yml" in playbook_names

    scripts = _foundation_run_scripts()
    uncovered = playbook_names - _syntax_checked_playbooks(scripts, playbook_names)
    assert not uncovered, f"collection CI does not syntax-check: {', '.join(sorted(uncovered))}"

    for suite in ("tests/integration/", "tests/scenario/"):
        path = f"ansible_collections/tomazb/acm_switchover/{suite}"
        assert _runs_pytest_for(scripts, path), f"collection CI does not run pytest against {path}"


def test_collection_ci_installs_kubernetes_runtime_for_live_module_boundary():
    text = COLLECTION_WORKFLOW.read_text()

    assert '"kubernetes>=28.0.0"' in text


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
    _documented_worktree_directory()


def test_flake8_excludes_the_documented_worktree_directory(tmp_path):
    """Prove `flake8 .` skips the documented worktree location using the real setup.cfg.

    CI and run_tests.sh both invoke `flake8 .` from the repository root. flake8 resolves
    any exclude pattern containing a path separator against the current working directory,
    so this reproduces that layout in a throwaway tree instead of grepping setup.cfg for a
    string that may not actually match anything.
    """
    pytest.importorskip("flake8")
    documented = _documented_worktree_directory()

    shutil.copy(SETUP_CFG, tmp_path / "setup.cfg")
    tmp_root = tmp_path.resolve()
    worktree_probe = (tmp_root / documented / "probe-slice" / "probe.py").resolve()
    assert worktree_probe.is_relative_to(tmp_root)
    worktree_probe.parent.mkdir(parents=True)
    worktree_probe.write_text("undefined_name_inside_worktree\n")
    (tmp_root / "probe.py").write_text("undefined_name_at_repo_root\n")

    result = subprocess.run(
        [sys.executable, "-m", "flake8", ".", "--select=F821"],
        cwd=tmp_root,
        capture_output=True,
        text=True,
        check=False,
    )

    # Control: flake8 really scanned this tree, so an absent worktree finding means excluded.
    assert "undefined_name_at_repo_root" in result.stdout, result.stdout + result.stderr
    assert "undefined_name_inside_worktree" not in result.stdout, result.stdout + result.stderr
