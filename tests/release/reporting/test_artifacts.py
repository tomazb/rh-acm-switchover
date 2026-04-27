import json
from pathlib import Path

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
