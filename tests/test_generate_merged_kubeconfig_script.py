"""Unit tests for security-sensitive merged kubeconfig generation."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate-merged-kubeconfig.sh"


def test_merged_kubeconfig_output_is_written_with_restrictive_permissions():
    """The final merged kubeconfig must be explicitly protected on disk."""
    content = SCRIPT_PATH.read_text()

    secure_patterns = (
        "umask 077",
        'chmod 600 "$OUTPUT_FILE"',
        'chmod 0600 "$OUTPUT_FILE"',
        "install -m 600",
    )

    assert any(pattern in content for pattern in secure_patterns), (
        "generate-merged-kubeconfig.sh must explicitly write the merged kubeconfig " "with owner-only permissions"
    )


def test_individual_kubeconfig_outputs_are_written_with_restrictive_permissions():
    """Temporary and per-context kubeconfigs must never be created world-readable."""
    content = SCRIPT_PATH.read_text()

    assert 'TOKEN_DURATION="24h"' in content
    assert "TEMP_DIR=$(umask 077 && mktemp -d)" in content
    assert 'chmod 700 "$TEMP_DIR"' in content
    assert 'install -d -m 700 "$OUTPUT_DIR"' in content
    assert "umask 077" in content
    assert '> "$output_path"' in content
