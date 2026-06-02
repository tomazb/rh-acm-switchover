# SPDX-License-Identifier: MIT
"""Path-safety helpers for ACM switchover collection modules."""

from __future__ import annotations

import os
from pathlib import Path

UNSAFE_PATH_CHARS = ["~", "$", "{", "}", "|", "&", ";", "<", ">", "`"]


class ValidationError(Exception):
    """Raised when input validation fails."""


def validate_path_syntax(path: str) -> None:
    """Apply common path syntax checks without requiring parents to exist."""
    if not path:
        raise ValidationError("Path cannot be empty")

    if ".." in path.split("/"):
        raise ValidationError(f"Path traversal attempt detected in '{path}'. The '..' sequence is not allowed.")

    if any(char in path for char in UNSAFE_PATH_CHARS):
        raise ValidationError(f"Path '{path}' contains unsafe characters. Disallowed: {', '.join(UNSAFE_PATH_CHARS)}")


def _commonpath_is_parent(path: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath([str(path), str(parent)]) == str(parent)
    except ValueError:
        return False


def _nearest_existing_ancestor(path: Path) -> tuple[Path, list[str]]:
    current = path
    missing_parts: list[str] = []
    while not current.exists() and current.parent != current:
        missing_parts.insert(0, current.name)
        current = current.parent
    return current, missing_parts


def _safe_roots() -> list[Path]:
    roots = [Path("/tmp"), Path("/var")]
    home_dir = Path(os.path.expanduser("~")).resolve()
    if str(home_dir):
        roots.append(home_dir)
    cwd = Path.cwd().resolve()
    if str(cwd):
        roots.append(cwd)
    return [root.resolve() for root in roots]


def _allowed_root(path: Path, *, description: str, path_label: str) -> Path:
    matching_roots = [root for root in _safe_roots() if _commonpath_is_parent(path, root)]
    if not matching_roots:
        home_dir = os.path.expanduser("~")
        raise ValidationError(
            f"{description} '{path_label}' is outside allowed directories. "
            f"Allowed prefixes: /tmp/, /var/, {home_dir}/"
        )
    return max(matching_roots, key=lambda root: len(str(root)))


def validate_safe_path(path: str) -> None:
    """Validate that a path is safe for collection filesystem operations."""
    validate_path_syntax(path)

    if not path.startswith("/"):
        return

    absolute_path = Path(path)
    if absolute_path.exists():
        resolved_path = absolute_path.resolve()
    else:
        ancestor, missing_parts = _nearest_existing_ancestor(absolute_path)
        if not ancestor.exists():
            raise ValidationError(f"Absolute path '{path}' cannot be resolved against an existing directory.")
        if not ancestor.is_dir():
            raise ValidationError(f"Absolute path '{path}' resolves through a non-directory ancestor.")

        resolved_path = ancestor.resolve()
        for part in missing_parts:
            resolved_path = resolved_path / part

    _allowed_root(resolved_path, description="Absolute path", path_label=path)


def _artifact_root(path: Path) -> Path:
    return _allowed_root(path, description="Artifact path", path_label=str(path))


def _reject_symlink_escape(path: Path, root: Path) -> None:
    root = root.resolve()
    absolute_path = Path(os.path.abspath(path))
    try:
        relative_path = absolute_path.relative_to(root)
    except ValueError:
        raise ValidationError(f"Artifact path '{path}' resolves outside the allowed root '{root}'.")

    current = root
    for part in relative_path.parts[:-1]:
        current = current / part
        if current.is_symlink() and not _commonpath_is_parent(current.resolve(), root):
            raise ValidationError(f"Artifact path '{path}' contains a symlink that escapes '{root}'.")


def validate_report_artifact_path(path: str) -> None:
    """Validate a report artifact path before controller-side reads or writes."""
    validate_safe_path(path)

    path_obj = Path(path)
    if path_obj.is_absolute():
        root = _artifact_root(path_obj)
        absolute_path = path_obj
        ancestor, _missing_parts = _nearest_existing_ancestor(absolute_path.parent)
        if ancestor.exists() and not ancestor.is_dir():
            raise ValidationError(f"Artifact path '{path}' resolves through a non-directory ancestor.")
    else:
        root = Path.cwd().resolve()
        absolute_path = Path(os.path.abspath(path))

    if absolute_path.is_symlink():
        raise ValidationError(f"Artifact path '{path}' must not be a symlink.")

    _reject_symlink_escape(absolute_path, root)
    parent = absolute_path.parent
    if parent.exists() and not _commonpath_is_parent(parent.resolve(), root):
        raise ValidationError(f"Artifact directory '{parent}' resolves outside '{root}'.")


def validate_report_artifact_directory(path: str) -> None:
    """Validate a report artifact directory before the final filename is known."""
    validate_report_artifact_path(str(Path(path) / ".artifact-path-check"))
