from __future__ import annotations

import hashlib
import json
from pathlib import Path


def compute_release_metadata_hash(
    *, repo_root: Path, metadata_files: tuple[str, ...], profile_hash: str, matrix_hash: str
) -> str:
    values: dict = {"profile_hash": profile_hash, "matrix_hash": matrix_hash, "files": []}
    for relative_path in metadata_files:
        path = repo_root / relative_path
        if not path.exists():
            raise FileNotFoundError(f"Missing release metadata file: {relative_path}")
        values["files"].append({"path": relative_path, "content": path.read_text(encoding="utf-8")})
    payload = json.dumps(values, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_release_metadata(
    *,
    repo_root: Path,
    metadata_files: tuple[str, ...],
    expected_version: str | None,
    profile_hash: str,
    matrix_hash: str,
) -> dict:
    file_results: list[dict[str, str]] = []
    failure_reasons: list[str] = []
    for relative_path in metadata_files:
        path = repo_root / relative_path
        if not path.exists():
            file_results.append({"path": relative_path, "status": "failed"})
            failure_reasons.append(f"Missing release metadata file: {relative_path}")
            continue
        content = path.read_text(encoding="utf-8")
        if expected_version and expected_version not in content:
            file_results.append({"path": relative_path, "status": "failed"})
            failure_reasons.append(f"{relative_path} does not reference expected version {expected_version}")
            continue
        file_results.append({"path": relative_path, "status": "passed"})

    metadata_hash = None
    if not failure_reasons:
        metadata_hash = compute_release_metadata_hash(
            repo_root=repo_root,
            metadata_files=metadata_files,
            profile_hash=profile_hash,
            matrix_hash=matrix_hash,
        )
    return {
        "status": "failed" if failure_reasons else "passed",
        "hash": metadata_hash,
        "expected_version": expected_version,
        "files": file_results,
        "failure_reasons": failure_reasons,
    }
