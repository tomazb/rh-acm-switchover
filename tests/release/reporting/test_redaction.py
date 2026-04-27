from pathlib import Path

import pytest

from tests.release.reporting.artifacts import ReleaseArtifacts
from tests.release.reporting.redaction import RedactionError, sanitize_text


def test_sanitize_text_replaces_bearer_token() -> None:
    sanitized = sanitize_text("Authorization: Bearer abcdef1234567890")

    assert "abcdef1234567890" not in sanitized.text
    assert sanitized.redacted_counts_by_class["authorization-header"] == 1


def test_sanitize_text_replaces_api_token() -> None:
    sanitized = sanitize_text("api_token=supersecret123")
    assert "supersecret123" not in sanitized.text
    assert sanitized.redacted_counts_by_class["api-token"] == 1


def test_sanitize_text_redacts_pem_block() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nABCDEFGH\n-----END RSA PRIVATE KEY-----"
    sanitized = sanitize_text(pem)
    assert "ABCDEFGH" not in sanitized.text
    assert sanitized.redacted_counts_by_class["pem-block"] == 1


def test_sanitize_text_rejects_kubeconfig_client_key() -> None:
    with pytest.raises(RedactionError, match="kubeconfig-client-key"):
        sanitize_text("client-key-data: LS0tLS1CRUdJTi...")


def test_sanitize_text_rejects_kubeconfig_token() -> None:
    with pytest.raises(RedactionError, match="kubeconfig-token"):
        sanitize_text("token: eyJhbGciOiJSUzI1NiJ9...")


def test_sanitize_text_passes_clean_text_unchanged() -> None:
    sanitized = sanitize_text("No secrets here, just a plain log line.")
    assert sanitized.text == "No secrets here, just a plain log line."
    assert sanitized.redacted_counts_by_class == {}


def test_sanitized_write_rejects_secret_data(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")

    with pytest.raises(RedactionError, match="kubernetes-secret-data"):
        artifacts.write_sanitized_text("logs/bad.txt", "data:\n  password: c2VjcmV0")


def test_sanitized_write_does_not_create_file_on_rejection(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    target = artifacts.run_dir / "logs" / "secret.txt"

    with pytest.raises(RedactionError):
        artifacts.write_sanitized_text("logs/secret.txt", "client-key-data: abc123")

    assert not target.exists(), "file must not be created when RedactionError is raised"
