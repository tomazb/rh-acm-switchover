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
