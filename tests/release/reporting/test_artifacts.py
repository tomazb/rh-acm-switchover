import json
from pathlib import Path

import pytest

from tests.release.reporting.artifacts import ReleaseArtifacts
from tests.release.reporting.redaction import RedactionError


def test_release_artifacts_create_required_files(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")

    assert artifacts.run_dir == tmp_path / "run-1"
    for filename in [
        "manifest.json",
        "scenario-results.json",
        "runtime-parity.json",
        "recovery.json",
        "redaction.json",
        "summary.json",
    ]:
        data = json.loads((artifacts.run_dir / filename).read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
    assert (artifacts.run_dir / "release-report.md").exists()


def test_create_raises_if_run_id_exists(tmp_path: Path) -> None:
    ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    with pytest.raises(FileExistsError):
        ReleaseArtifacts.create(root=tmp_path, run_id="run-1")


def test_write_failed_manifest_records_reason(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    manifest_path, summary_path = artifacts.write_failed_manifest(
        reason="dirty checkout", command=["pytest", "-m", "release"]
    )

    assert manifest_path == artifacts.run_dir / "manifest.json"
    assert summary_path == artifacts.run_dir / "summary.json"
    manifest = json.loads(
        (artifacts.run_dir / "manifest.json").read_text(encoding="utf-8")
    )
    summary = json.loads(
        (artifacts.run_dir / "summary.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "failed"
    assert "dirty checkout" in manifest["failure_reasons"]
    assert manifest["run_id"] == "run-1"
    assert manifest["command"] == ["pytest", "-m", "release"]
    assert manifest["certification_eligible"] is False
    assert summary["status"] == "failed"
    assert "dirty checkout" in summary["failure_reasons"]
    assert summary["certification_eligible"] is False


def test_sanitized_write_updates_redaction_record(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    artifacts.write_sanitized_text(
        "logs/output.txt", "Authorization: Bearer secret-token-xyz"
    )

    redaction = json.loads(
        (artifacts.run_dir / "redaction.json").read_text(encoding="utf-8")
    )
    assert "logs/output.txt" in redaction["scanned_artifacts"]
    assert redaction["redacted_counts_by_class"].get("authorization-header", 0) >= 1
    assert redaction["status"] == "redacted"


def test_sanitized_write_records_rejection_in_redaction_json(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    with pytest.raises(RedactionError):
        artifacts.write_sanitized_text("logs/secrets.txt", "token: abc123")

    redaction = json.loads(
        (artifacts.run_dir / "redaction.json").read_text(encoding="utf-8")
    )
    assert "logs/secrets.txt" in redaction["rejected_artifacts"]


def test_write_json_rejects_path_traversal(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    with pytest.raises(ValueError, match="escapes"):
        artifacts.write_json("../escaped.json", {"schema_version": 1})


def test_write_json_rejects_absolute_path(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    outside = str(tmp_path / "other" / "file.json")
    with pytest.raises(ValueError, match="escapes"):
        artifacts.write_json(outside, {"schema_version": 1})


def test_write_sanitized_text_rejects_path_traversal(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    with pytest.raises(ValueError, match="escapes"):
        artifacts.write_sanitized_text("../escaped.txt", "safe content")
