"""Endpoint identity for the klusterlet wrong-hub check (R4-06 PR 2).

Implements the endpoint rules of docs/plans/2026-07-29-kubeconfig-ambiguity-guard-design.md
§3 as amended by docs/plans/2026-10-15-r4-06-current-base-amendment.md §2:

- ``parse_endpoint`` validates and canonicalizes one ``server`` URL, or names why it is
  rejected (July §3 rejection table; a trailing slash is never significant, amendment §2.1);
- ``classify_hub`` compares an expected hub endpoint with an observed one and returns
  ``verified``, ``wrong_hub`` or ``unverified`` (amendment §2.3). A pair that either legacy
  hostname-only comparator called equal is never ``wrong_hub``, so the stricter comparison
  never authorizes a new remediation;
- ``select_spoke_context`` is Python's spoke-context discovery over the validated
  KUBECONFIG merge (amendment §2.5). There is no name-only fallback.

The collection mirrors the endpoint functions in
``plugins/module_utils/endpoint_identity.py``; the two share no code and are held equal by
``tests/fixtures/r4_06_endpoint_vectors.json``.
"""

import ipaddress
import re
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from lib.constants import KLUSTERLET_REASON_CLIENT_ENDPOINT_MISMATCH as REASON_CLIENT_ENDPOINT_MISMATCH
from lib.constants import KLUSTERLET_REASON_CONTEXT_AMBIGUOUS as REASON_CONTEXT_AMBIGUOUS
from lib.constants import KLUSTERLET_REASON_ENDPOINT_MALFORMED as REASON_ENDPOINT_MALFORMED
from lib.constants import KLUSTERLET_REASON_ENDPOINT_MISMATCH_UNVERIFIED as REASON_ENDPOINT_MISMATCH_UNVERIFIED
from lib.constants import KLUSTERLET_REASON_ENDPOINT_UNAVAILABLE as REASON_ENDPOINT_UNAVAILABLE
from lib.constants import KLUSTERLET_REASON_EXPECTED_ENDPOINT_MALFORMED as REASON_EXPECTED_ENDPOINT_MALFORMED
from lib.constants import KLUSTERLET_REASON_EXPECTED_ENDPOINT_UNAVAILABLE as REASON_EXPECTED_ENDPOINT_UNAVAILABLE
from lib.constants import KLUSTERLET_RESULT_NO_CONTEXT as REASON_NO_CONTEXT
from lib.constants import KLUSTERLET_RESULT_UNVERIFIED as RESULT_UNVERIFIED
from lib.constants import KLUSTERLET_RESULT_VERIFIED as RESULT_VERIFIED
from lib.constants import KLUSTERLET_RESULT_WRONG_HUB as RESULT_WRONG_HUB

_DEFAULT_PORTS = {"https": 443, "http": 80}
_HOSTNAME_RE = re.compile(r"^[a-z0-9_.-]+$")
_IPV4_LIKE_RE = re.compile(r"^[0-9.]+$")
_PORT_RE = re.compile(r"^[0-9]+$")
_PERCENT_RE = re.compile(r"%([0-9A-Fa-f]{2})")
_FORBIDDEN_CHARS_RE = re.compile(r"[\s\x00-\x1f\x7f]")


class Endpoint(NamedTuple):
    """A validated, canonical endpoint. Equality of two values is endpoint equality."""

    scheme: str
    host: str
    port: int
    path: str

    def __str__(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}{self.path}"


def parse_endpoint(value: Any) -> Tuple[Optional[Endpoint], Optional[str]]:
    """``(endpoint, None)`` for a valid server URL, else ``(None, rejection_code)``.

    The rejection code names the July §3 row; the input itself is never part of it.
    """
    if value is None:
        return None, "null"
    if not isinstance(value, str):
        return None, "not_string"
    if not value.strip():
        return None, "empty"
    if _FORBIDDEN_CHARS_RE.search(value):
        return None, "whitespace"
    scheme_part, separator, rest = value.partition("://")
    if not separator or not scheme_part or "/" in scheme_part:
        return None, "relative"
    scheme = scheme_part.lower()
    if scheme not in _DEFAULT_PORTS:
        return None, "scheme"
    if "?" in value or "#" in value:
        return None, "query_fragment"
    slash = rest.find("/")
    authority, path = (rest, "") if slash < 0 else (rest[:slash], rest[slash:])
    if "@" in authority:
        return None, "userinfo"
    host, port_text, code = _split_authority(authority)
    if code:
        return None, code
    if port_text is None:
        port = _DEFAULT_PORTS[scheme]
    else:
        # Bound the significant digits before int(): an arbitrarily long digit string would
        # otherwise raise instead of being classified.
        significant = port_text.lstrip("0")
        if not _PORT_RE.match(port_text) or len(significant) > 5 or not 1 <= int(significant or "0") <= 65535:
            return None, "port"
        port = int(significant)
    path, code = _normalize_path(path)
    if code:
        return None, code
    return Endpoint(scheme, host, port, path), None


def _split_authority(authority: str) -> Tuple[str, Optional[str], Optional[str]]:
    """``(canonical_host, port_text_or_None, rejection_code)``."""
    if authority.startswith("["):
        close = authority.find("]")
        if close < 0:
            return "", None, "brackets"
        inner, after = authority[1:close], authority[close + 1 :]
        if after and not after.startswith(":"):
            return "", None, "brackets"
        if ":" not in inner:
            return "", None, "brackets"  # brackets around a host that is not IPv6
        if "%" in inner:
            return "", None, "ipv6"  # zone identifiers are refused, never compared
        try:
            address = ipaddress.IPv6Address(inner)
        except ValueError:
            return "", None, "ipv6"
        return f"[{address.compressed}]", (after[1:] if after else None), None
    if "[" in authority or "]" in authority:
        return "", None, "brackets"
    if authority.count(":") > 1:
        return "", None, "brackets"  # an unbracketed IPv6 literal
    host, colon, port_text = authority.partition(":")
    host = host.lower()
    if not host:
        return "", None, "invalid"
    if _IPV4_LIKE_RE.match(host):
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            return "", None, "ipv4"
    elif not _HOSTNAME_RE.match(host):
        return "", None, "invalid"
    return host, (port_text if colon else None), None


def _normalize_path(path: str) -> Tuple[str, Optional[str]]:
    if "%" in path:
        if len(_PERCENT_RE.findall(path)) != path.count("%"):
            return "", "percent"
        path = _PERCENT_RE.sub(lambda match: "%" + match.group(1).upper(), path)
    # The Kubernetes client strips every trailing "/" from a server URL, so the endpoint
    # a client really uses never carries one (amendment §2.1).
    return path.rstrip("/"), None


def canonical_host(endpoint: Endpoint) -> str:
    return endpoint.host


def normalized_equal(left: Any, right: Any) -> bool:
    left_endpoint, _ = parse_endpoint(left)
    right_endpoint, _ = parse_endpoint(right)
    return left_endpoint is not None and left_endpoint == right_endpoint


def py_legacy_host(value: str) -> str:
    """Python's pre-PR 2 host extraction (unanchored, https only)."""
    return re.sub(r"https://([^:/]+).*", r"\1", value)


def col_legacy_host(value: str) -> str:
    """The collection's pre-PR 2 ``server_host`` extraction."""
    return re.sub(r"^https?://([^:/]+).*$", r"\1", value or "")


def legacy_equal(left: str, right: str) -> bool:
    """Whether either pre-PR 2 hostname-only comparator called the pair equal."""
    return py_legacy_host(left) == py_legacy_host(right) or col_legacy_host(left) == col_legacy_host(right)


def classify_hub(expected: Any, observed: Any) -> Tuple[str, Optional[str]]:
    """Amendment §2.3: ``(result, reason)`` for an expected and a present observed server.

    An absent observation is the caller's documented skip and never reaches this function.
    """
    expected_endpoint, _ = parse_endpoint(expected)
    if expected_endpoint is None:
        return RESULT_UNVERIFIED, REASON_EXPECTED_ENDPOINT_MALFORMED
    observed_endpoint, _ = parse_endpoint(observed)
    if observed_endpoint is None:
        return RESULT_UNVERIFIED, REASON_ENDPOINT_MALFORMED
    if expected_endpoint == observed_endpoint:
        return RESULT_VERIFIED, None
    if canonical_host(expected_endpoint) != canonical_host(observed_endpoint) and not legacy_equal(expected, observed):
        return RESULT_WRONG_HUB, None
    return RESULT_UNVERIFIED, REASON_ENDPOINT_MISMATCH_UNVERIFIED


def redact(value: Any) -> str:
    """A loggable form of a server value: canonical when valid, never the raw input."""
    endpoint, code = parse_endpoint(value)
    if endpoint is None:
        return f"<rejected:{code}>"
    return str(endpoint)


def managed_cluster_url(managed_cluster: Any) -> Tuple[Optional[str], Optional[str]]:
    """Amendment §2.5 rule 1: ``(url, None)`` or ``(None, reason)``.

    The URL is the first ``spec.managedClusterClientConfigs[]`` entry whose ``url`` is a
    non-empty string. Any malformed shape is ``endpoint_unavailable``, never an exception.
    """
    spec = managed_cluster.get("spec") if isinstance(managed_cluster, dict) else None
    configs = spec.get("managedClusterClientConfigs") if isinstance(spec, dict) else None
    if isinstance(configs, list):
        for entry in configs:
            url = entry.get("url") if isinstance(entry, dict) else None
            if isinstance(url, str) and url:
                if parse_endpoint(url)[0] is None:
                    return None, REASON_ENDPOINT_MALFORMED
                return url, None
    return None, REASON_ENDPOINT_UNAVAILABLE


def select_spoke_context(merged: Dict[str, List[Dict[str, Any]]], url: str) -> Tuple[Optional[str], Optional[str]]:
    """Amendment §2.5 rules 2-4: ``(context_name, None)`` or ``(None, reason)``.

    ``merged`` is the validated KUBECONFIG merge with duplicate names already resolved, so
    a cluster entry is identified by its name. The reason is ``context_ambiguous``,
    ``endpoint_mismatch_unverified`` or ``no_context`` (the documented non-fatal skip).
    """
    target, _ = parse_endpoint(url)
    if target is None:
        return None, REASON_ENDPOINT_MALFORMED
    servers: Dict[str, Endpoint] = {}
    for cluster in merged.get("clusters", []):
        server = (cluster.get("cluster") or {}).get("server")
        endpoint, _ = parse_endpoint(server)
        if endpoint is not None:
            servers[cluster["name"]] = endpoint
    candidates: Dict[str, str] = {}
    near_match = False
    for context in merged.get("contexts", []):
        cluster_name = (context.get("context") or {}).get("cluster")
        endpoint = servers.get(cluster_name) if cluster_name else None
        if endpoint is None:
            continue
        if endpoint == target:
            candidates.setdefault(cluster_name, context["name"])
        elif canonical_host(endpoint) == canonical_host(target):
            near_match = True
    if len(candidates) > 1:
        return None, REASON_CONTEXT_AMBIGUOUS
    if candidates:
        return next(iter(candidates.values())), None
    if near_match:
        return None, REASON_ENDPOINT_MISMATCH_UNVERIFIED
    return None, REASON_NO_CONTEXT
