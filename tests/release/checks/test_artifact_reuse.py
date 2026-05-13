from pathlib import Path

import pytest

from tests.release.checks.artifact_reuse import validate_artifact_reuse_manifest


def test_reuse_manifest_requires_schema_version(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="schema_version"):
        validate_artifact_reuse_manifest(manifest, expected_profile_hash="p", expected_matrix_hash="m")
