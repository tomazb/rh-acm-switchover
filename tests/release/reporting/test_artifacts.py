import json
from pathlib import Path

import pytest

from tests.release.reporting.artifacts import ReleaseArtifacts


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
    artifacts.write_failed_manifest(
        reason="dirty checkout", command=["pytest", "-m", "release"]
    )

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
