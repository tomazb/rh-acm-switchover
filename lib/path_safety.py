"""Shared path-safety helpers for Python CLI filesystem operations."""

from __future__ import annotations

import os
from pathlib import Path

from lib.exceptions import SecurityValidationError, ValidationError

UNSAFE_PATH_CHARS = ["~", "$", "{", "}", "|", "&", ";", "<", ">", "`"]


def validate_path_syntax(path_value: str, field_name: str) -> None:
    """Apply common path syntax checks without requiring parents to exist."""
    if not path_value:
        raise ValidationError(f"{field_name} path cannot be empty")

    if ".." in path_value.split("/"):
        raise SecurityValidationError(
            f"SECURITY: Path traversal attempt detected in {field_name} path '{path_value}'. "
            "The '..' sequence is not allowed as a path component."
        )

    if any(char in path_value for char in UNSAFE_PATH_CHARS):
        raise SecurityValidationError(
            f"SECURITY: Invalid characters in {field_name} path '{path_value}'. "
            "Path contains unsafe characters that could be used for command injection. "
            f"Disallowed patterns: {', '.join(UNSAFE_PATH_CHARS)}."
        )


def _commonpath_is_parent(path: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath([str(path), str(parent)]) == str(parent)
    except ValueError:
        return False


def _nearest_existing_ancestor(path: Path) -> tuple[Path, list[str]]:
    """Return the nearest existing path and missing suffix for an absolute path."""
    current = path
    missing_parts: list[str] = []
    while not current.exists() and current.parent != current:
        missing_parts.insert(0, current.name)
        current = current.parent
    return current, missing_parts


def _safe_roots() -> list[Path]:
    roots = [Path("/tmp"), Path("/var")]
    cwd = Path.cwd().resolve()
    if str(cwd):
        roots.append(cwd)
    home = Path.home().resolve()
    if str(home):
        roots.append(home)
    return [root.resolve() for root in roots]


def _allowed_root(path: Path, *, description: str, path_label: str) -> Path:
    matching_roots = [root for root in _safe_roots() if _commonpath_is_parent(path, root)]
    if not matching_roots:
        raise SecurityValidationError(
            f"SECURITY: {description} '{path_label}' is outside allowed directories. "
            "Use relative paths or paths within /tmp, /var, workspace root, or home directory."
        )
    return max(matching_roots, key=lambda root: len(str(root)))


def validate_safe_filesystem_path(path: str, field_name: str) -> None:
    """Validate a path for general filesystem operations."""
    validate_path_syntax(path, field_name)

    if not path.startswith("/"):
        return

    absolute_path = Path(path)
    if absolute_path.exists():
        resolved_path = absolute_path.resolve()
    else:
        ancestor, missing_parts = _nearest_existing_ancestor(absolute_path)
        if not ancestor.exists():
            raise SecurityValidationError(
                f"SECURITY: Absolute path '{path}' for {field_name} cannot be resolved against an existing directory."
            )
        if not ancestor.is_dir():
            raise SecurityValidationError(
                f"SECURITY: Absolute path '{path}' for {field_name} resolves through a non-directory ancestor."
            )
        resolved_path = ancestor.resolve()
        for part in missing_parts:
            resolved_path = resolved_path / part

    _allowed_root(resolved_path, description="Absolute path", path_label=path)


def _artifact_root(path: Path) -> Path:
    return _allowed_root(path, description="Artifact path", path_label=str(path))


def _reject_symlink_escape(path: Path, root: Path, field_name: str) -> None:
    """Reject existing symlinks in path parents that resolve outside root."""
    root = root.resolve()
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError as exc:
        raise SecurityValidationError(
            f"SECURITY: {field_name} path '{path}' resolves outside the allowed root '{root}'."
        ) from exc

    current = root
    for part in relative_parts[:-1]:
        current = current / part
        if current.is_symlink() and not _commonpath_is_parent(current.resolve(), root):
            raise SecurityValidationError(
                f"SECURITY: {field_name} path '{path}' contains a symlink that escapes '{root}'."
            )


def validate_report_artifact_path(destination: str, field_name: str = "report artifact") -> Path:
    """Validate an artifact path before controller-side reads or writes."""
    validate_path_syntax(destination, field_name)

    path = Path(destination)
    if path.is_absolute():
        absolute_path = path
        root = _artifact_root(absolute_path)
        ancestor, _missing_parts = _nearest_existing_ancestor(absolute_path.parent)
        if ancestor.exists() and not ancestor.is_dir():
            raise SecurityValidationError(
                f"SECURITY: {field_name} path '{destination}' resolves through a non-directory ancestor."
            )
    else:
        root = Path.cwd().resolve()
        absolute_path = (root / path).absolute()

    if absolute_path.is_symlink():
        raise SecurityValidationError(f"SECURITY: {field_name} path '{destination}' must not be a symlink.")

    _reject_symlink_escape(absolute_path.parent / absolute_path.name, root, field_name)
    if absolute_path.parent.exists() and not _commonpath_is_parent(absolute_path.parent.resolve(), root):
        raise SecurityValidationError(
            f"SECURITY: {field_name} directory '{absolute_path.parent}' resolves outside '{root}'."
        )

    return path


def validate_report_artifact_directory(path_value: str, field_name: str = "report artifact directory") -> None:
    """Validate a report artifact directory supplied before the final filename is known."""
    validate_report_artifact_path(str(Path(path_value) / ".artifact-path-check"), field_name)
