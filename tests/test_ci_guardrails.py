"""Static guardrails for CI and local test runner behavior."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci-cd.yml"
COLLECTION_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ansible-collection-foundation.yml"
RUN_TESTS = REPO_ROOT / "run_tests.sh"


def test_root_ci_excludes_e2e_tests_by_marker():
    text = CI_WORKFLOW.read_text()

    assert '-m "not e2e"' in text or "-m 'not e2e'" in text


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

    assert "STRICT_QUALITY" in text
    assert "QUALITY_PATHS=" in text
    assert "black --check --line-length 120 ." not in text
    assert "isort --check-only --profile black --line-length 120 ." not in text


def test_run_tests_excludes_release_framework_by_default():
    text = RUN_TESTS.read_text()

    assert "--ignore=tests/release" in text
