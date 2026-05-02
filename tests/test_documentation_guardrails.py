"""Regression checks for maintained support documentation."""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _assert_argocd_script_only_in_deprecated_context(path: str, content: str) -> None:
    lines = content.splitlines()

    for idx, line in enumerate(lines):
        if "argocd-manage.sh" not in line:
            continue

        window_start = max(0, idx - 2)
        window_end = min(len(lines), idx + 3)
        context = "\n".join(lines[window_start:window_end]).lower()
        assert "deprecated" in context, f"Non-deprecated argocd-manage.sh guidance remains in {path}: {line}"


def test_docs_index_surfaces_collection_and_tldr_docs():
    """Docs landing page should point readers to newer major documentation areas."""
    content = _read("docs/README.md")

    assert "ansible-collection" in content
    assert "ACM_SWITCHOVER_RUNBOOK_TLDR.md" in content


def test_tests_readmes_cover_current_test_surfaces():
    """Test docs should mention collection, E2E, and newer tool coverage."""
    tests_readme = _read("tests/README.md")
    scripts_readme = _read("tests/README-scripts-tests.md")

    assert "ansible_collections/tomazb/acm_switchover/tests/" in tests_readme
    assert "tests/e2e/README.md" in tests_readme
    assert "check_rbac.py" in scripts_readme
    assert "generate-merged-kubeconfig.sh" in scripts_readme
    assert "argocd-manage.sh" in scripts_readme


def test_contributing_matches_current_dev_workflow():
    """Contributor guide should match current environment and test guidance."""
    content = _read("CONTRIBUTING.md")

    for token in (".venv", "requirements-dev.txt", "./run_tests.sh", "CHANGELOG.md"):
        assert token in content, f"Missing {token} from CONTRIBUTING.md"


def test_kustomize_readme_mentions_optional_decommission_extension():
    """Kustomize deployment docs should mention the split decommission RBAC extension."""
    content = _read("deploy/kustomize/README.md")

    assert "decommission extension" in content.lower()
    assert "deploy/rbac/extensions/decommission/clusterrole.yaml" in content
    assert "deploy/rbac/extensions/decommission/clusterrolebinding.yaml" in content


def test_argocd_guardrail_matches_script_without_dot_slash_prefix():
    """The guardrail should match deprecated script guidance regardless of path prefix."""
    _assert_argocd_script_only_in_deprecated_context(
        "sample.md",
        "Bash alternative (deprecated): `scripts/argocd-manage.sh` is deprecated.\n",
    )


def test_argocd_guardrail_accepts_deprecation_marker_in_nearby_context():
    """A nearby deprecation marker should satisfy the guardrail for a code example."""
    _assert_argocd_script_only_in_deprecated_context(
        "sample.md",
        "Deprecated:\n`argocd-manage.sh --context hub --mode pause`\n",
    )


def test_argocd_guardrail_rejects_non_deprecated_script_guidance():
    """Any active argocd-manage.sh recommendation should still fail the guardrail."""
    with pytest.raises(AssertionError):
        _assert_argocd_script_only_in_deprecated_context(
            "sample.md",
            "Run `argocd-manage.sh --context hub --mode pause` before the switchover.\n",
        )


def test_install_quick_test_mentions_supported_virtualenv_names():
    """Quick-test guidance should clarify both supported virtualenv directory names."""
    content = _read("docs/getting-started/install.md")
    quick_test_section = content.split("### Quick Test", 1)[1].split("### Enable Bash Completions", 1)[0]

    assert "source .venv/bin/activate" in quick_test_section
    assert "source venv/bin/activate" in quick_test_section


def test_active_operator_docs_do_not_recommend_deprecated_argocd_script():
    """Active operator guidance may mention the script only as deprecated, never as the recommended path."""
    guarded_paths = (
        "docs/operations/usage.md",
        "docs/operations/quickref.md",
        "docs/ACM_SWITCHOVER_RUNBOOK.md",
        ".claude/skills/operations/preflight-validation.skill.md",
    )

    for path in guarded_paths:
        _assert_argocd_script_only_in_deprecated_context(path, _read(path))
