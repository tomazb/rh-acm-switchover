"""Unit tests for security-sensitive merged kubeconfig generation."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate-merged-kubeconfig.sh"


def test_merged_kubeconfig_output_is_written_with_restrictive_permissions():
    """The final merged kubeconfig must be explicitly protected on disk."""
    content = SCRIPT_PATH.read_text()

    secure_patterns = (
        "umask 077",
        "chmod 600 \"$OUTPUT_FILE\"",
        "chmod 0600 \"$OUTPUT_FILE\"",
        "install -m 600",
    )

    assert any(pattern in content for pattern in secure_patterns), (
        "generate-merged-kubeconfig.sh must explicitly write the merged kubeconfig "
        "with owner-only permissions"
    )
