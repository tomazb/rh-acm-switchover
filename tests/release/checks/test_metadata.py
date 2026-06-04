from pathlib import Path

import pytest

from tests.release.checks.metadata import compute_release_metadata_hash, validate_release_metadata


def test_metadata_hash_changes_when_authoritative_file_changes(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("version 1.0.0\n", encoding="utf-8")
    first = compute_release_metadata_hash(
        repo_root=tmp_path, metadata_files=("README.md",), profile_hash="a", matrix_hash="b"
    )
    readme.write_text("version 1.0.1\n", encoding="utf-8")
    second = compute_release_metadata_hash(
        repo_root=tmp_path, metadata_files=("README.md",), profile_hash="a", matrix_hash="b"
    )
    assert first != second


def test_metadata_hash_fails_when_authoritative_file_is_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="MISSING.md"):
        compute_release_metadata_hash(
            repo_root=tmp_path, metadata_files=("MISSING.md",), profile_hash="a", matrix_hash="b"
        )


def test_release_metadata_validation_passes_when_files_match_expected_version(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("Version 1.2.3\n", encoding="utf-8")

    result = validate_release_metadata(
        repo_root=tmp_path,
        metadata_files=("README.md",),
        expected_version="1.2.3",
        profile_hash="a",
        matrix_hash="b",
    )

    assert result["status"] == "passed"
    assert result["hash"]
    assert result["files"] == [{"path": "README.md", "status": "passed"}]


def test_release_metadata_validation_fails_when_file_version_mismatches(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("Version 1.2.2\n", encoding="utf-8")

    result = validate_release_metadata(
        repo_root=tmp_path,
        metadata_files=("README.md",),
        expected_version="1.2.3",
        profile_hash="a",
        matrix_hash="b",
    )

    assert result["status"] == "failed"
    assert result["hash"] is None
    assert result["files"] == [{"path": "README.md", "status": "failed"}]
    assert result["failure_reasons"] == ["README.md does not reference expected version 1.2.3"]
