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
_MERGE_TAG = "tag:yaml.org,2002:merge"
# A YAML constructor raises these for a malformed scalar (e.g. `!!int <token>` is a ValueError
# whose message quotes the scalar) or for pathological nesting; they are input errors.
_CONSTRUCTOR_ERRORS = (ValueError, TypeError, AttributeError, RecursionError, OverflowError)


def kubeconfig_paths(kubeconfig_env: Optional[str]) -> List[str]:
    """The files to merge, in merge order.

    KUBECONFIG entries are taken left to right exactly as the Kubernetes client loader
    takes them (no whitespace trimming); empty entries are skipped and a repeated entry is
    read once, at its first position. The default path is used only when KUBECONFIG is
    unset or names no file, and is never appended to a named list.
    """
    paths: List[str] = []
    for entry in (kubeconfig_env or "").split(":"):
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
    raw = _read_bounded(path, size_limit)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is None:
        raise _failure("yaml_invalid", path, " (not UTF-8)")
    # The failure is raised outside every `except` block so the original exception - whose
    # message or Mark buffer can hold key material - is never attached as __context__.
    failure: Optional[SwitchoverError] = None
    try:
        document = yaml.load(text, Loader=_UniqueKeyLoader)  # nosec B506 - SafeLoader subclass
    except _DuplicateKey as exc:
        failure = _failure("duplicate_yaml_key", path, _position(exc.mark))
    except yaml.YAMLError as exc:
        failure = _failure("yaml_invalid", path, _position(getattr(exc, "problem_mark", None)))
    except _CONSTRUCTOR_ERRORS:
        failure = _failure("yaml_invalid", path)
    if failure is not None:
        raise failure
    _validate(document, path)
    return document


def _read_bounded(path: str, size_limit: int) -> bytes:
    """Read at most size_limit bytes; one byte more is `oversized` (inclusive limit)."""
    code = None
    try:
        with open(path, "rb") as handle:
            # Bounded by the real file size, so a large limit never pre-allocates; a file
            # that grows between the stat and the read still fails the length check.
            want = min(size_limit, os.fstat(handle.fileno()).st_size) + 1
            raw = handle.read(want)
            if len(raw) == want and want <= size_limit:
                raw += handle.read(size_limit + 1 - want)
    except FileNotFoundError:
        code = "missing"
    except OSError:
        code = "unreadable"
    if code is not None:
        raise _failure(code, path)
    if len(raw) > size_limit:
        raise _failure("oversized", path, f" (limit {size_limit} bytes)")
    return raw


def _position(mark: Any) -> str:
    if mark is None:
        return ""
    return f" line {mark.line + 1} column {mark.column + 1}"


class _DuplicateKey(Exception):
    def __init__(self, mark: Any) -> None:
        super().__init__("duplicate mapping key")
        self.mark = mark


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that refuses a repeated mapping key instead of keeping the last value.

    `<<` merges keep YAML semantics (explicit keys override merged ones), but a repeated
    `<<` key and a repeated key inside an inline merge source are refused too.
    """

    def construct_mapping(self, node, deep=False):
        self._refuse_duplicate_keys(node, deep)
        for key_node, value_node in node.value:
            if key_node.tag != _MERGE_TAG:
                continue
            sources = value_node.value if isinstance(value_node, yaml.SequenceNode) else [value_node]
            for source in sources:
                if isinstance(source, yaml.MappingNode):
                    self._refuse_duplicate_keys(source, deep)
        return super().construct_mapping(node, deep=deep)

    def _refuse_duplicate_keys(self, node, deep):
        seen = set()
        merges = 0
        for key_node, _value_node in node.value:
            if key_node.tag == _MERGE_TAG:
                merges += 1
                if merges > 1:
                    raise _DuplicateKey(key_node.start_mark)
                continue
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in seen:
                    raise _DuplicateKey(key_node.start_mark)
                seen.add(key)
            except TypeError:
                pass  # an unhashable key is refused by the base constructor


def _validate(document: Any, path: str) -> None:
    """Design §1 structure plus the 2026-08-02 nested resolver-field obligation.

    Only the failing location is reported, never a value. A section that is absent or
    null is empty (kubectl writes `users: null`). An entry's member, when present, must be
    a mapping (a present-but-null member would break every consumer). A context's
    `cluster`/`user`, when present, must be strings; kubectl writes "" for an unset one,
    which can never match anything, so "" is accepted as unset.
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
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                raise _failure("structure_invalid", path, f" ({where}.name is not a non-empty string)")
            if member not in entry:
                continue
            body = entry[member]
            if not isinstance(body, dict):
                raise _failure("structure_invalid", path, f" ({where}.{member} is not a mapping)")
            if member == "context":
                for field in ("cluster", "user"):
                    if field in body and not isinstance(body[field], str):
                        raise _failure("structure_invalid", path, f" ({where}.context.{field} is not a string)")
