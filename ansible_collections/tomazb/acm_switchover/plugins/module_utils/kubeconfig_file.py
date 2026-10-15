# SPDX-License-Identifier: MIT
"""Validated loading of one mapped managed-cluster kubeconfig (R4-06 PR 2).

Implements docs/plans/2026-10-15-r4-06-current-base-amendment.md §3-§4 for the collection,
whose ``acm_switchover_managed_clusters`` mapping names one file (and optionally a context)
per cluster. The file contract mirrors Python's ``lib/kubeconfig_merge.py`` (the two share
no code; shared vectors hold them equal):

- an inclusive size limit with a chunked bounded read;
- YAML parsed with duplicate mapping keys, ``<<`` merge keys and ``!!set`` refused, and any
  constructor error reported without the parser's text (it can quote a token or key);
- recursive aliases and non-tree values refused;
- the structural and nested-field rules recorded on #349;
- within the file, identical duplicate entries resolve first-wins and a (kind, name) group
  with more than one distinct variant fails the cluster (``kubeconfig_duplicate_conflict``).

Every failure is a stable reason for that cluster only; the file is never partially used.
"""

from __future__ import annotations

import datetime
from typing import Any

import yaml

KUBECONFIG_SIZE_LIMIT = 10 * 1024 * 1024
REASON_KUBECONFIG_PATH_UNSUPPORTED = "kubeconfig_path_unsupported"
REASON_KUBECONFIG_DUPLICATE_CONFLICT = "kubeconfig_duplicate_conflict"
REASON_CONTEXT_MISSING = "context_missing"
REASON_CONTEXT_AMBIGUOUS = "context_ambiguous"

_SECTIONS = (("clusters", "cluster"), ("contexts", "context"), ("users", "user"))
_MERGE_TAG = "tag:yaml.org,2002:merge"
_SET_TAG = "tag:yaml.org,2002:set"
_READ_CHUNK = 64 * 1024
_TREE_SCALARS = (str, int, float, bool, bytes, datetime.date, type(None))


class KubeconfigFileError(Exception):
    """A sanitized refusal: ``reason`` is stable, ``detail`` carries location only."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}{': ' + detail if detail else ''}")
        self.reason = reason
        self.detail = detail


def _load_failure(code: str, detail: str = "") -> KubeconfigFileError:
    return KubeconfigFileError(f"kubeconfig_load_failed ({code})", detail)


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
    """SafeLoader refusing repeated mapping keys, ``<<`` merge keys and ``!!set``."""

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


def _position(mark: Any) -> str:
    if mark is None:
        return ""
    return f"line {mark.line + 1} column {mark.column + 1}"


def _read_bounded(path: str, size_limit: int) -> bytes:
    code = None
    chunks: list[bytes] = []
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
        raise _load_failure(code)
    if total > size_limit:
        raise _load_failure("oversized", f"limit {size_limit} bytes")
    return b"".join(chunks)


def parse_kubeconfig_text(raw: bytes) -> dict:
    """Parse and validate one kubeconfig document, or raise ``KubeconfigFileError``."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is None:
        raise _load_failure("yaml_invalid", "not UTF-8")
    # Raised outside every `except` block so the original exception - whose message or
    # Mark buffer can hold key material - is never attached as __context__.
    failure: KubeconfigFileError | None = None
    try:
        document = yaml.load(text, Loader=_UniqueKeyLoader)  # nosec B506 - SafeLoader subclass
    except _DuplicateKey as exc:
        failure = _load_failure("duplicate_yaml_key", _position(exc.mark))
    except _MergeKey as exc:
        failure = _load_failure("yaml_invalid", _position(exc.mark))
    except _SetValue as exc:
        failure = _load_failure("structure_invalid", _position(exc.mark))
    except yaml.YAMLError as exc:
        failure = _load_failure("yaml_invalid", _position(getattr(exc, "problem_mark", None)))
    except Exception:  # noqa: BLE001 - any constructor error quotes its input
        failure = _load_failure("yaml_invalid")
    if failure is not None:
        raise failure
    _require_tree(document)
    _validate(document)
    return document


def _require_tree(value: Any, ancestors: set | None = None, done: set | None = None) -> None:
    if isinstance(value, _TREE_SCALARS):
        return
    if not isinstance(value, (dict, list)):
        raise _load_failure("structure_invalid", "unsupported YAML value type")
    ancestors = set() if ancestors is None else ancestors
    done = set() if done is None else done
    if id(value) in ancestors:
        raise _load_failure("structure_invalid", "recursive YAML alias")
    if id(value) in done:
        return  # a shared alias already checked: each node is walked once
    ancestors.add(id(value))
    children = [part for pair in value.items() for part in pair] if isinstance(value, dict) else value
    for child in children:
        _require_tree(child, ancestors, done)
    ancestors.discard(id(value))
    done.add(id(value))


def _validate(document: Any) -> None:
    if not isinstance(document, dict):
        raise _load_failure("structure_invalid", "document root is not a mapping")
    for section, member in _SECTIONS:
        entries = document.get(section)
        if entries is None:
            continue
        if not isinstance(entries, list):
            raise _load_failure("structure_invalid", f"{section} is not a list")
        for index, entry in enumerate(entries):
            where = f"{section}[{index}]"
            if not isinstance(entry, dict):
                raise _load_failure("structure_invalid", f"{where} is not a mapping")
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                raise _load_failure("structure_invalid", f"{where}.name is not a non-empty string")
            if member not in entry:
                continue
            body = entry[member]
            if not isinstance(body, dict):
                raise _load_failure("structure_invalid", f"{where}.{member} is not a mapping")
            if member == "context":
                for field in ("cluster", "user"):
                    if field in body and not isinstance(body[field], str):
                        raise _load_failure("structure_invalid", f"{where}.context.{field} is not a string")


class CanonicalForms:
    """Type-tagged structural identity for parsed YAML values (amendment §3), by hash-consing.

    ``key(value)`` returns a small integer; two values get the same integer exactly when they
    are equal parsed values compared with their types: formatting never matters, ``1``,
    ``true``, ``"1"`` and ``1.0`` are distinct, equal floats (``0.0``/``-0.0``) and the same
    instant at different UTC offsets are equal, and every NaN is one value. Each node is
    interned from its children's integers, so nested shared aliases cost linear work, and
    comparing two forms is one integer comparison however deep they are.
    """

    def __init__(self) -> None:
        self._ids: dict = {}
        self._by_object: dict = {}

    def _intern(self, form) -> int:
        return self._ids.setdefault(form, len(self._ids))

    def key(self, value) -> int:
        if isinstance(value, bool):
            return self._intern(("bool", value))
        if isinstance(value, int):
            return self._intern(("int", value))
        if isinstance(value, float):
            return self._intern(("float", 1, 0.0) if value != value else ("float", 0, value))
        if isinstance(value, str):
            return self._intern(("str", value))
        if isinstance(value, bytes):
            return self._intern(("bytes", value))
        if value is None:
            return self._intern(("null",))
        if isinstance(value, datetime.datetime):
            aware = value.tzinfo is not None
            instant = value.astimezone(datetime.timezone.utc) if aware else value
            return self._intern(("datetime", aware, instant.isoformat()))
        if isinstance(value, datetime.date):
            return self._intern(("date", value.isoformat()))
        if id(value) in self._by_object:
            return self._by_object[id(value)][0]
        if isinstance(value, list):
            form: tuple = ("list", tuple(self.key(item) for item in value))
        elif isinstance(value, dict):
            form = ("map", tuple(sorted((self.key(key), self.key(item)) for key, item in value.items())))
        else:
            raise TypeError(f"no canonical form for {type(value).__name__}")
        identifier = self._intern(form)
        # Keep the object alive with its id so the id cannot be reused within this run.
        self._by_object[id(value)] = (identifier, value)
        return identifier


def resolve_duplicates(document: dict) -> dict:
    """Within-file duplicate rule: one entry per (kind, name), or ``kubeconfig_duplicate_conflict``."""
    resolved: dict = {}
    conflicts = []
    forms = CanonicalForms()
    for section, member in _SECTIONS:
        groups: dict[str, list[tuple[int, Any]]] = {}
        for index, entry in enumerate(document.get(section) or []):
            groups.setdefault(entry["name"], []).append((index, forms.key(entry)))
        for name, group in groups.items():
            variants: list[Any] = []
            labels = []
            for index, form in group:
                if form not in variants:
                    variants.append(form)
                labels.append(f"[{index}] (variant {variants.index(form) + 1})")
            if len(variants) > 1:
                conflicts.append(f"{member} {name!r} at " + ", ".join(labels))
        first: dict[str, dict] = {}
        for entry in document.get(section) or []:
            first.setdefault(entry["name"], entry)
        resolved[section] = list(first.values())
    if conflicts:
        raise KubeconfigFileError(REASON_KUBECONFIG_DUPLICATE_CONFLICT, "; ".join(conflicts))
    resolved["current-context"] = document.get("current-context")
    return resolved


def load_kubeconfig_file(path: str, size_limit: int = KUBECONFIG_SIZE_LIMIT) -> dict:
    """Load one mapped file under the full contract, duplicates resolved, or raise."""
    if ":" in path:
        # The Kubernetes client loader splits a config_file path on ":" and would read files
        # nobody validated (amendment §2.6 rule 3).
        raise KubeconfigFileError(REASON_KUBECONFIG_PATH_UNSUPPORTED)
    return resolve_duplicates(parse_kubeconfig_text(_read_bounded(path, size_limit)))


def select_context(document: dict, context: str | None) -> tuple[str, dict | None]:
    """Amendment §4: ``(context_name, cluster_entry_or_None)`` for the mapped context.

    A named context must exist (``context_missing``). An omitted one requires a
    ``current-context`` naming exactly one context (``context_ambiguous``). The cluster
    entry is ``None`` when the context names no existing cluster; the caller's identity
    proof then refuses it.
    """
    contexts = {entry["name"]: entry for entry in document.get("contexts") or []}
    if context:
        if context not in contexts:
            raise KubeconfigFileError(REASON_CONTEXT_MISSING)
        name = context
    else:
        current = document.get("current-context")
        if not isinstance(current, str) or current not in contexts:
            raise KubeconfigFileError(REASON_CONTEXT_AMBIGUOUS)
        name = current
    cluster_name = (contexts[name].get("context") or {}).get("cluster")
    clusters = {entry["name"]: entry for entry in document.get("clusters") or []}
    return name, clusters.get(cluster_name) if cluster_name else None
