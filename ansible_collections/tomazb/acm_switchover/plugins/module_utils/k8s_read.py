# SPDX-License-Identifier: MIT
"""The collection's one bounded strict Kubernetes read (R4-03).

Moved unchanged out of ``plugins/modules/acm_k8s_read_outcome.py`` so every collection
module that needs a strict read consumes this single owner instead of a copy: complete
pagination under fixed page and restart bounds, a bounded request timeout on every
request, a live discovery-served proof before any kind is reported as not served and before
any named 404 is reported as an absent object, rejection
of malformed responses, and an error that is never reported as an empty inventory.

``strict_read`` returns the same ``(read_status, resources, resource_version)`` triple
``acm_k8s_read_outcome`` publishes. It raises nothing for an API outcome; argument
validation and client construction stay with the calling module.
"""

from __future__ import annotations

import json
from typing import Any

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    STRICT_READ_MAX_PAGES,
    STRICT_READ_MAX_RESTARTS,
    STRICT_READ_PAGE_LIMIT,
    STRICT_READ_REQUEST_TIMEOUT,
)

try:
    from kubernetes.dynamic.exceptions import NotFoundError
except ImportError:  # pragma: no cover - dependency declared by kubernetes.core
    NotFoundError = type("NotFoundError", (Exception,), {})  # type: ignore[misc,assignment]


def _to_mapping(value: Any) -> dict | None:
    if isinstance(value, dict):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        if isinstance(converted, dict):
            return dict(converted)
    return None


def _normalize_resources(read_mode: str, raw: Any) -> list[dict] | None:
    mapping = _to_mapping(raw)
    if mapping is None:
        return None

    kind = mapping.get("kind")
    if isinstance(kind, str) and kind.endswith("List"):
        items = mapping.get("items", [])
        if items is None:
            items = []
        if not isinstance(items, list):
            return None
        normalized: list[dict] = []
        for item in items:
            item_mapping = _to_mapping(item)
            if item_mapping is None:
                return None
            normalized.append(item_mapping)
        return normalized

    if "items" in mapping:
        items = mapping.get("items")
        if not isinstance(items, list):
            return None
        normalized = []
        for item in items:
            item_mapping = _to_mapping(item)
            if item_mapping is None:
                return None
            normalized.append(item_mapping)
        return normalized

    if read_mode == "get":
        return [mapping]

    # Bare non-list object is not a valid list inventory proof.
    return None


def _strict_list_page(raw) -> tuple[list[dict] | None, str | None, str | None]:
    """Return (members, continue_token, revision) for one page, or (None, None, None) if malformed.

    The revision is the page's own `metadata.resourceVersion`. `_drain_list_once` keeps only
    page 1's, which is the snapshot the whole read belongs to (A3.0 rule 8).
    """
    mapping = _to_mapping(raw)
    if mapping is None:
        return None, None, None
    if "items" not in mapping:
        return None, None, None
    items = mapping.get("items")
    if not isinstance(items, list):
        return None, None, None
    members: list[dict] = []
    for item in items:
        item_mapping = _to_mapping(item)
        if item_mapping is None:
            return None, None, None
        members.append(item_mapping)
    metadata = mapping.get("metadata")
    if not isinstance(metadata, dict):
        return None, None, None
    revision = metadata.get("resourceVersion")
    if not isinstance(revision, str) or not revision:
        # A complete read must be describable by a revision; anything else is malformed.
        return None, None, None
    token = metadata.get("continue") or None
    return members, token, revision


def _discovery_serves(api_client, api_version: str, resource_name: str) -> bool | None:
    """True if served, False if positively absent, None if unverifiable.

    The dynamic client's discovery cache substitutes an empty resource list
    for some discovery-fetch failures, and the substituted set differs across
    the supported client range, so a lookup miss alone never proves absence.
    """
    path = f"/apis/{api_version}" if "/" in api_version else f"/api/{api_version}"
    try:
        response = api_client.client.request("GET", path, serialize=False, _request_timeout=STRICT_READ_REQUEST_TIMEOUT)
        body = json.loads(response.data.decode("utf8"))
    except Exception:
        return None
    if not isinstance(body, dict) or body.get("kind") != "APIResourceList":
        return None
    resources = body.get("resources")
    if not isinstance(resources, list):
        return None
    # The whole document is validated before any verdict. Deciding on the first matching entry
    # would let one response mean `served` or `unverifiable` purely by server entry order.
    # Absence was never order-sensitive: it already required the full list to validate.
    for entry in resources:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or not entry["name"]
            or not isinstance(entry.get("kind"), str)
            or not entry["kind"]
        ):
            return None
    if any(entry["name"] == resource_name for entry in resources):
        return True
    return False


def _drain_list(api_client, resource, params) -> tuple[list[dict] | None, str, str | None]:
    """Drain every page of one list, or fail closed. Never returns a partial prefix."""
    for _ in range(STRICT_READ_MAX_RESTARTS + 1):
        collected, status, revision = _drain_list_once(api_client, resource, params)
        if status != "restart":
            return collected, status, revision
    return None, "error", None


def _drain_list_once(api_client, resource, params) -> tuple[list[dict] | None, str, str | None]:
    collected: list[dict] = []
    continue_token = None
    # Page 1 owns the snapshot revision, every later page must be served at that same value, and
    # a 410 restart re-enters this function and re-establishes it.
    snapshot_revision = None
    for _ in range(STRICT_READ_MAX_PAGES):
        page_params = dict(params)
        page_params["limit"] = STRICT_READ_PAGE_LIMIT
        page_params["_request_timeout"] = STRICT_READ_REQUEST_TIMEOUT
        if continue_token:
            page_params["_continue"] = continue_token
        else:
            page_params["_continue"] = None
        try:
            raw = api_client.get(resource, **page_params)
        except Exception as exc:
            if getattr(exc, "status", None) == 410 and continue_token:
                # Expired continuation: discard everything, including this read's revision.
                return None, "restart", None
            return None, "error", None
        members, token, revision = _strict_list_page(raw)
        if members is None:
            return None, "error", None
        # A3.0 rule 8: every normal continuation page belongs to page 1's snapshot.
        if snapshot_revision is None:
            snapshot_revision = revision
        elif revision != snapshot_revision:
            return None, "error", None
        collected.extend(members)
        continue_token = token
        if not continue_token:
            return collected, "ok", snapshot_revision
    return None, "error", None


def _object_revision(resources: list[dict]) -> str | None:
    """The named object's own revision, or None when the response cannot supply one.

    `None` is not an `ok` value on the `get` path: its only caller classifies it as
    `error` before any success is published (A3.0 rule 9).
    """
    if len(resources) != 1:
        return None
    metadata = resources[0].get("metadata")
    if not isinstance(metadata, dict):
        return None
    revision = metadata.get("resourceVersion")
    return revision if isinstance(revision, str) and revision else None


def _is_named_not_found(exc: BaseException) -> bool:
    if isinstance(exc, NotFoundError):
        return True
    status = getattr(exc, "status", None)
    return status == 404


def strict_read(
    api_client,
    *,
    read_mode: str,
    api_version: str,
    kind: str,
    resource_name: str,
    namespace: str | None = None,
    name: str | None = None,
    label_selectors: list[str] | None = None,
) -> tuple[str, list[dict], str | None]:
    """One strict GET or complete LIST through an already-constructed client.

    Returns ``(read_status, resources, resource_version)`` with ``read_status`` one of
    ``ok``, ``not_found`` (named GET 404 while live discovery serves the kind),
    ``kind_not_served`` (discovery positively shows the kind is not served) or ``error``.
    ``resources`` is empty and ``resource_version`` is ``None`` on every outcome other
    than ``ok``.
    """
    try:
        resource = api_client.resource(kind, api_version)
    except Exception:
        served = _discovery_serves(api_client, api_version, resource_name)
        return ("kind_not_served" if served is False else "error"), [], None

    params: dict[str, Any] = {}
    if namespace:
        params["namespace"] = namespace
    if read_mode == "get":
        params["name"] = name
    else:
        if label_selectors:
            params["label_selector"] = ",".join(label_selectors)

    if read_mode == "list":
        resources, status, revision = _drain_list(api_client, resource, params)
        if status != "ok":
            return "error", [], None
        return "ok", list(resources or []), revision

    # Every strict request is bounded, not just the paginated ones: the Python surface bounds
    # each call with its per-instance request timeout, and the collection has no client instance
    # to carry one (plan section 9.1, per-call timeout).
    params["_request_timeout"] = STRICT_READ_REQUEST_TIMEOUT

    try:
        raw = api_client.get(resource, **params)
    except Exception as exc:
        if read_mode == "get" and _is_named_not_found(exc):
            # kubernetes.core may resolve the kind from its shared on-disk discovery cache, and
            # the object route of a kind that is no longer served also answers 404. Absence is
            # proved only when live discovery still serves the kind (#317).
            served = _discovery_serves(api_client, api_version, resource_name)
            if served is True:
                return "not_found", [], None
            return ("kind_not_served" if served is False else "error"), [], None
        return "error", [], None

    normalized = _normalize_resources(read_mode, raw)
    if normalized is None:
        return "error", [], None
    revision = _object_revision(normalized)
    if revision is None:
        # A3.0 rule 9: a successful named GET must expose the object's own revision.
        # A response that cannot supply one is a malformed response, not an `ok` with null.
        return "error", [], None
    return "ok", normalized, revision
