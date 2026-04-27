from pathlib import Path

import pytest

from tests.release.reporting.artifacts import ReleaseArtifacts
from tests.release.reporting.redaction import RedactionError, sanitize_text


def test_sanitize_text_replaces_bearer_token() -> None:
    sanitized = sanitize_text("Authorization: Bearer abcdef1234567890")

    assert "abcdef1234567890" not in sanitized.text
    assert sanitized.redacted_counts_by_class["authorization-header"] == 1


def test_sanitized_write_rejects_secret_data(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")

    with pytest.raises(RedactionError, match="kubernetes-secret-data"):
        artifacts.write_sanitized_text("logs/bad.txt", "data:\n  password: c2VjcmV0")
