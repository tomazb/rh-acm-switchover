"""Fail-closed KUBECONFIG merge for the post-activation klusterlet path (R4-06 PR 1, #349).

Implements docs/plans/2026-07-29-kubeconfig-ambiguity-guard-design.md §1 for Python:
deterministic input precedence; every named file read once, bounded by one inclusive size
limit; YAML parsed with duplicate mapping keys refused; every document structurally
validated (including the resolver-consumed nested fields) before anything is merged. Any
failure aborts the whole merge with a sanitized reason code, the file path and, for a YAML
error, the line and column - never the parser message, the offending line, or key
material, because a kubeconfig line can be a token or a private key.

R4-06 PR 2 (docs/plans/2026-10-15-r4-06-current-base-amendment.md §3) adds the duplicate-name
rule across the whole KUBECONFIG chain: identical entries (equal parsed values, compared
with their types) resolve first-wins; a (kind, name) group with more than one distinct
variant refuses the whole merge with every occurrence located and labelled, never shown.
`!!set` values, recursive aliases and any other non-tree value are refused, so accepted
input is always a finite tree the comparison is defined on. Snapshot-built clients are
R4-06 PR 3.
"""

import datetime
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import yaml

from lib.exceptions import SwitchoverError

logger = logging.getLogger("acm_switchover")

DEFAULT_KUBECONFIG_PATH = "~/.kube/config"
_SECTIONS = (("clusters", "cluster"), ("contexts", "context"), ("users", "user"))
_MERGE_TAG = "tag:yaml.org,2002:merge"
_SET_TAG = "tag:yaml.org,2002:set"
_READ_CHUNK = 64 * 1024


def kubeconfig_paths(kubeconfig_env: Optional[str]) -> List[str]:
    """The files to merge, in merge order.

    KUBECONFIG entries are taken left to right without whitespace trimming, as the
    Kubernetes client loader splits them; empty entries are skipped and a repeated entry
    is read once, at its first position. The default path is used only when KUBECONFIG is
    unset or names no file (design section 1), and is never appended to a named list.
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
    and validates: a mutating path never runs on a partial view. Duplicate names are
    resolved by the amendment §3 rule before anything is returned, so every returned
    (kind, name) is unique.
    """
    occurrences: Dict[str, List[Tuple[Dict[str, Any], str, int]]] = {"contexts": [], "clusters": [], "users": []}
    for path in kubeconfig_paths(kubeconfig_env):
        document = _load_document(path, size_limit)
        for section, _member in _SECTIONS:
            for index, entry in enumerate(document.get(section) or []):
                occurrences[section].append((entry, path, index))
    return {section: _resolve_duplicates(section, entries) for section, entries in occurrences.items()}


def canonical_value(value: Any) -> Any:
    """A type-tagged, order-independent form of a parsed YAML value (amendment §3).

    Two values are identical exactly when their canonical forms are equal: formatting
    never creates a conflict, and `1`, `true`, `"1"` and `1.0` are all distinct.
    """
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return ("float", repr(value))
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, bytes):
        return ("bytes", value)
    if value is None:
        return ("null",)
    if isinstance(value, datetime.datetime):
        return ("datetime", value.isoformat())
    if isinstance(value, datetime.date):
        return ("date", value.isoformat())
    if isinstance(value, list):
        return ("list", tuple(canonical_value(item) for item in value))
    if isinstance(value, dict):
        return ("map", tuple(sorted((canonical_value(key), canonical_value(item)) for key, item in value.items())))
    raise TypeError(f"no canonical form for {type(value).__name__}")


_KINDS = {"clusters": "cluster", "contexts": "context", "users": "user"}


def _resolve_duplicates(section: str, entries: List[Tuple[Dict[str, Any], str, int]]) -> List[Dict[str, Any]]:
    """First-wins for identical duplicates; refuse a group with more than one variant."""
    groups: Dict[str, List[Tuple[Dict[str, Any], str, int]]] = {}
    for occurrence in entries:
        groups.setdefault(occurrence[0]["name"], []).append(occurrence)
    conflicts = []
    for name, group in groups.items():
        variants: List[Any] = []
        labels = []
        for entry, path, index in group:
            form = canonical_value(entry)
            if form not in variants:
                variants.append(form)
            labels.append(f"{path}[{index}] (variant {variants.index(form) + 1})")
        if len(variants) > 1:
            conflicts.append(f"{_KINDS[section]} {name!r} at " + ", ".join(labels))
        elif len(group) > 1:
            logger.debug(
                "Identical kubeconfig %s %r defined %d times; the first definition is used",
                _KINDS[section],
                name,
                len(group),
            )
    if conflicts:
        raise SwitchoverError("kubeconfig_duplicate_conflict: " + "; ".join(conflicts))
    return [group[0][0] for group in groups.values()]


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
    except _MergeKey as exc:
        failure = _failure("yaml_invalid", path, _position(exc.mark) + " (YAML merge keys are not supported)")
    except _SetValue as exc:
        failure = _failure("structure_invalid", path, _position(exc.mark) + " (YAML sets are not supported)")
    except yaml.YAMLError as exc:
        failure = _failure("yaml_invalid", path, _position(getattr(exc, "problem_mark", None)))
    except Exception:  # noqa: BLE001 - any constructor error (`!!int <v>`, `!!bool <v>`, `!!int ''`) quotes input
        failure = _failure("yaml_invalid", path)
    if failure is not None:
        raise failure
    _require_tree(document, path)
    _validate(document, path)
    return document


_TREE_SCALARS = (str, int, float, bool, bytes, datetime.date, type(None))


def _require_tree(value: Any, path: str, ancestors: Optional[set] = None) -> None:
    """Refuse a recursive alias or any value outside mappings, lists and plain scalars.

    PyYAML builds a recursive alias (`&a [*a]`) as a self-containing object; `!!omap` and
    `!!pairs` build tuples. Neither has a defined duplicate comparison (amendment §3).
    """
    if isinstance(value, _TREE_SCALARS):
        return
    if not isinstance(value, (dict, list)):
        raise _failure("structure_invalid", path, " (unsupported YAML value type)")
    ancestors = set() if ancestors is None else ancestors
    if id(value) in ancestors:
        raise _failure("structure_invalid", path, " (recursive YAML alias)")
    ancestors.add(id(value))
    for item in value.items() if isinstance(value, dict) else value:
        if isinstance(value, dict):
            _require_tree(item[0], path, ancestors)
            _require_tree(item[1], path, ancestors)
        else:
            _require_tree(item, path, ancestors)
    ancestors.discard(id(value))


def _read_bounded(path: str, size_limit: int) -> bytes:
    """Read at most size_limit bytes; one byte more is `oversized` (inclusive limit).

    Chunked, so no read ever asks for more than one chunk (a large limit never
    pre-allocates, and a pipe or FIFO is bounded the same way as a regular file).
    """
    code = None
    chunks: List[bytes] = []
    total = 0
    try:
        with open(path, "rb") as handle:
            while total <= size_limit:
                chunk = handle.read(min(_READ_CHUNK, size_limit + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
    except FileNotFoundError:
        code = "missing"
    except OSError:
        code = "unreadable"
    if code is not None:
        raise _failure(code, path)
    if total > size_limit:
        raise _failure("oversized", path, f" (limit {size_limit} bytes)")
    return b"".join(chunks)


def _position(mark: Any) -> str:
    if mark is None:
        return ""
    return f" line {mark.line + 1} column {mark.column + 1}"


class _DuplicateKey(Exception):
    def __init__(self, mark: Any) -> None:
        super().__init__("duplicate mapping key")
        self.mark = mark


class _MergeKey(Exception):
    def __init__(self, mark: Any) -> None:
        super().__init__("merge key")
        self.mark = mark


class _SetValue(Exception):
    def __init__(self, mark: Any) -> None:
        super().__init__("set value")
        self.mark = mark


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that refuses a repeated mapping key instead of keeping the last value,
    and refuses `<<` merge keys.

    Merge keys are refused because their override order is parser-specific: a kubeconfig
    whose endpoint depends on it could resolve differently here than in client-go, which
    is the ambiguity this loader exists to close. Every mapping that holds a merge key is
    constructed here before PyYAML flattens it, so a nested or anchored merge is caught.
    """

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _value_node in node.value:
            if key_node.tag == _MERGE_TAG:
                raise _MergeKey(key_node.start_mark)
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in seen:
                    raise _DuplicateKey(key_node.start_mark)
                seen.add(key)
            except TypeError:
                pass  # an unhashable key is refused by the base constructor
        return super().construct_mapping(node, deep=deep)

    def construct_yaml_set(self, node):
        raise _SetValue(node.start_mark)


_UniqueKeyLoader.add_constructor(_SET_TAG, _UniqueKeyLoader.construct_yaml_set)


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
