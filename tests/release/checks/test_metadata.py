from pathlib import Path

from tests.release.checks.metadata import compute_release_metadata_hash


def test_metadata_hash_changes_when_authoritative_file_changes(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("version 1.0.0\n", encoding="utf-8")
    first = compute_release_metadata_hash(repo_root=tmp_path, metadata_files=("README.md",), profile_hash="a", matrix_hash="b")
    readme.write_text("version 1.0.1\n", encoding="utf-8")
    second = compute_release_metadata_hash(repo_root=tmp_path, metadata_files=("README.md",), profile_hash="a", matrix_hash="b")
    assert first != second
