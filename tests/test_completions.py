"""Regression checks for bash completion coverage and install verification."""

from pathlib import Path
import re
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPLETIONS_DIR = REPO_ROOT / "completions"


def _completion_text(name: str) -> str:
    return (COMPLETIONS_DIR / name).read_text(encoding="utf-8")


def _is_repo_owned_completion_name(name: str) -> bool:
    junk_suffixes = ("~", ".swp", ".swo", ".tmp", ".bak", ".orig")
    return not name.startswith(".") and not name.endswith(junk_suffixes)


def test_acm_switchover_completion_tracks_current_cli_surface():
    """Main CLI completion should include newer post-December flags."""
    content = _completion_text("acm_switchover.py")

    required_tokens = (
        "--setup",
        "--restore-only",
        "--activation-method",
        "--min-managed-clusters",
        "--admin-kubeconfig",
        "--role",
        "--token-duration",
        "--output-dir",
        "--skip-kubeconfig-generation",
        "--include-decommission",
        "--disable-observability-on-secondary",
        "--skip-gitops-check",
        "--argocd-manage",
        "--argocd-resume-only",
        "--argocd-resume-on-failure",
        "--force",
    )

    for token in required_tokens:
        assert token in content, f"Missing {token} from acm_switchover.py completion"

    assert "patch restore" in content
    assert "operator validator both" in content


def test_acm_switchover_boolean_flags_do_not_offer_argument_values():
    """Boolean flags should appear in option lists without value completion branches."""
    content = _completion_text("acm_switchover.py")

    assert "--manage-auto-import-strategy" in content
    assert "--manage-auto-import-strategy)" not in content
    assert "--manage-auto-import-strategy=*" not in content


def test_check_rbac_completion_tracks_role_and_managed_cluster_flags():
    """RBAC completion should cover newer mode and role flags."""
    content = _completion_text("check_rbac.py")

    for token in ("--role", "--managed-cluster"):
        assert token in content, f"Missing {token} from check_rbac.py completion"

    assert "operator validator" in content


def test_generate_sa_kubeconfig_completion_tracks_current_options():
    """Service-account kubeconfig completion should expose all supported options."""
    content = _completion_text("generate-sa-kubeconfig.sh")

    for token in ("--kubeconfig", "--user", "--token-duration"):
        assert token in content, f"Missing {token} from generate-sa-kubeconfig.sh completion"


def test_pre_and_postflight_completions_include_gitops_skip_flag():
    """Shell check completions should expose the current GitOps skip flag."""
    for name in ("preflight-check.sh", "postflight-check.sh"):
        content = _completion_text(name)
        assert "--skip-gitops-check" in content, f"Missing --skip-gitops-check from {name} completion"


def test_generate_merged_kubeconfig_completion_exists_and_lists_supported_flags():
    """Merged kubeconfig generator should ship a matching completion file."""
    path = COMPLETIONS_DIR / "generate-merged-kubeconfig.sh"
    assert path.exists(), "Missing completion for generate-merged-kubeconfig.sh"

    content = path.read_text(encoding="utf-8")
    for token in ("--admin-kubeconfig", "--token-duration", "--output", "--namespace", "--managed-cluster"):
        assert token in content, f"Missing {token} from generate-merged-kubeconfig.sh completion"


@pytest.mark.parametrize(
    ("name", "expected"),
    (
        ("acm_switchover.py", True),
        (".DS_Store", False),
        ("check_rbac.py~", False),
        ("generate-merged-kubeconfig.sh.swp", False),
        ("notes.txt", True),
    ),
)
def test_completion_file_filter_ignores_local_junk_names(name: str, expected: bool):
    """Completion parity checks should ignore common local junk files."""
    assert _is_repo_owned_completion_name(name) is expected


def test_install_completion_verification_tracks_all_completion_files():
    """Install verification should fail if any shipped completion file is missing."""
    script_text = (REPO_ROOT / "scripts" / "install-completions.sh").read_text(encoding="utf-8")
    match = re.search(r"EXPECTED_COMPLETION_FILES=\((.*?)\)", script_text, re.DOTALL)
    assert match, "install-completions.sh must declare EXPECTED_COMPLETION_FILES"

    expected = {
        entry.strip('"').strip("'")
        for entry in match.group(1).split()
        if entry.strip()
    }
    actual = {
        path.name
        for path in COMPLETIONS_DIR.iterdir()
        if path.is_file() and _is_repo_owned_completion_name(path.name)
    }

    assert expected == actual
