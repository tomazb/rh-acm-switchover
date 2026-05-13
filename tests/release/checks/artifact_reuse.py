from __future__ import annotations

import json
from pathlib import Path


def validate_artifact_reuse_manifest(
    manifest_path: Path, *, expected_profile_hash: str, expected_matrix_hash: str
) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError(f"{manifest_path}: schema_version must be 1")
    if manifest.get("profile", {}).get("sha256") != expected_profile_hash:
        raise ValueError(f"{manifest_path}: profile.sha256 does not match active profile")
    if manifest.get("selected_matrix_hash") != expected_matrix_hash:
        raise ValueError(f"{manifest_path}: selected_matrix_hash does not match active matrix")
    return manifest
