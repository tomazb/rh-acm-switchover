"""Fail-closed KUBECONFIG merge for the post-activation klusterlet path (R4-06 PR 1, #349).

Implements docs/plans/2026-07-29-kubeconfig-ambiguity-guard-design.md §1 for Python:
deterministic input precedence; every named file read once, bounded by one inclusive size
limit; YAML parsed with duplicate mapping keys refused; every document structurally
validated (including the resolver-consumed nested fields) before anything is merged. Any
failure aborts the whole merge with a sanitized reason code, the file path and, for a YAML
error, the line and column - never the parser message, the offending line, or key
material, because a kubeconfig line can be a token or a private key.

Duplicate entry names, endpoint matching and snapshot-built clients are R4-06 PR 2 and 3.
"""

import os
from typing import Any, Dict, List, Optional

import yaml

from lib.exceptions import SwitchoverError

DEFAULT_KUBECONFIG_PATH = "~/.kube/config"
_SECTIONS = (("clusters", "cluster"), ("contexts", "context"), ("users", "user"))


def kubeconfig_paths(kubeconfig_env: Optional[str]) -> List[str]:
    """The files to merge, in merge order.

    KUBECONFIG entries are taken left to right; empty entries are skipped and a repeated
    entry is read once, at its first position. The default path is used only when
    KUBECONFIG is unset or names no file, and is never appended to a named list.
    """
    paths: List[str] = []
    for entry in (kubeconfig_env or "").split(":"):
        entry = entry.strip()
        if not entry:
            continue
        path = os.path.expanduser(entry)
        if path not in paths:
            paths.append(path)
    return paths or [os.path.expanduser(DEFAULT_KUBECONFIG_PATH)]


def load_merged_kubeconfig(kubeconfig_env: Optional[str], size_limit: int) -> Dict[str, List[Dict[str, Any]]]:
    """Load, validate and merge every kubeconfig file, or raise SwitchoverError.

    Entries keep their file and in-file order. Nothing is merged unless every file loads
    and validates: a mutating path never runs on a partial view.
    """
    merged: Dict[str, List[Dict[str, Any]]] = {"contexts": [], "clusters": [], "users": []}
    for path in kubeconfig_paths(kubeconfig_env):
        document = _load_document(path, size_limit)
        for section, _member in _SECTIONS:
            merged[section].extend(document.get(section) or [])
    return merged


def _failure(code: str, path: str, detail: str = "") -> SwitchoverError:
    return SwitchoverError(f"kubeconfig_load_failed ({code}): {path}{detail}")


def _load_document(path: str, size_limit: int) -> Dict[str, Any]:
    try:
        with open(path, "rb") as handle:
            raw = handle.read(size_limit + 1)
    except FileNotFoundError:
        raise _failure("missing", path) from None
    except OSError:
        raise _failure("unreadable", path) from None
    if len(raw) > size_limit:
        raise _failure("oversized", path, f" (limit {size_limit} bytes)")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _failure("yaml_invalid", path, " (not UTF-8)") from None
    try:
        document = yaml.load(text, Loader=_UniqueKeyLoader)  # nosec B506 - SafeLoader subclass
    except _DuplicateKey as exc:
        raise _failure("duplicate_yaml_key", path, _position(exc.mark)) from None
    except yaml.YAMLError as exc:
        raise _failure("yaml_invalid", path, _position(getattr(exc, "problem_mark", None))) from None
    _validate(document, path)
    return document


def _position(mark: Any) -> str:
    if mark is None:
        return ""
    return f" line {mark.line + 1} column {mark.column + 1}"


class _DuplicateKey(Exception):
    def __init__(self, mark: Any) -> None:
        super().__init__("duplicate mapping key")
        self.mark = mark


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that refuses a repeated mapping key instead of keeping the last value."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in seen:
                    raise _DuplicateKey(key_node.start_mark)
                seen.add(key)
            except TypeError:
                pass  # an unhashable key is refused by the base constructor below
        return super().construct_mapping(node, deep=deep)


def _validate(document: Any, path: str) -> None:
    """Design §1 structure plus the 2026-08-02 nested resolver-field obligation.

    Only the failing location is reported, never a value.
    """
    if not isinstance(document, dict):
        raise _failure("structure_invalid", path, " (document root is not a mapping)")
    for section, member in _SECTIONS:
        entries = document.get(section)
        if entries is None:
            continue
        if not isinstance(entries, list):
            raise _failure("structure_invalid", path, f" ({section} is not a list)")
        for index, entry in enumerate(entries):
            where = f"{section}[{index}]"
            if not isinstance(entry, dict):
                raise _failure("structure_invalid", path, f" ({where} is not a mapping)")
            if not _non_empty_str(entry.get("name")):
                raise _failure("structure_invalid", path, f" ({where}.name is not a non-empty string)")
            body = entry.get(member)
            if body is None:
                continue
            if not isinstance(body, dict):
                raise _failure("structure_invalid", path, f" ({where}.{member} is not a mapping)")
            if member == "context":
                if not _non_empty_str(body.get("cluster")):
                    raise _failure("structure_invalid", path, f" ({where}.context.cluster is not a non-empty string)")
                if "user" in body and not _non_empty_str(body.get("user")):
                    raise _failure("structure_invalid", path, f" ({where}.context.user is not a non-empty string)")


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value)
