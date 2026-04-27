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
        values["files"].append({"path": relative_path, "content": path.read_text(encoding="utf-8") if path.exists() else ""})
    payload = json.dumps(values, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
