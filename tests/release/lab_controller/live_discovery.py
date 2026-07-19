"""Controller-owned Phase 9B read-only live discovery and physical identity proof.

This module is deliberately not a credential loader or Kubernetes client factory. A caller must
provide two explicit runtime-only handles containing typed read API objects. The controller owns
all gates, fixed query selection, collection bounds, pagination completeness, provenance,
freshness, repeated physical identity proof, and final artifact publication.

The only cluster-facing operation is ``TypedReadApi.read_page``. Its request contract has no
command, argv, shell, release-adapter, endpoint, credential, or mutation fields, and its verb is
restricted to the fixed ``list`` query definitions below.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Protocol

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from tests.release.lab_controller.artifacts import strict_recursive_artifact_audit
from tests.release.lab_controller.read_only_backend import (
    Phase9BReadOnlyBackendResult,
    ReadOnlyBackendDecision,
)
from tests.release.lab_controller.read_only_discovery import required_read_only_discovery_gate_ids
from tests.release.lab_controller.read_only_live_transport import (
    READ_ONLY_LIVE_MAX_ITEMS_PER_QUERY,
    READ_ONLY_LIVE_MAX_PAGE_SIZE,
    READ_ONLY_LIVE_MAX_PAGES_PER_QUERY,
    READ_ONLY_LIVE_MAX_REQUEST_TIMEOUT_SECONDS,
    READ_ONLY_LIVE_MAX_TOTAL_DEADLINE_SECONDS,
    RawReadOnlyLiveResponse,
    ReadOnlyLiveClientProtocol,
    ReadOnlyLiveClientRequest,
    ReadOnlyLivePermanentError,
    ReadOnlyLiveSafetyError,
    ReadOnlyLiveTimeoutError,
    ReadOnlyLiveTransientError,
    ReadOnlyLiveTransport,
    ReadOnlyLiveTransportOptions,
    RuntimeOnlyLiveHubHandle,
    RuntimeOnlyLiveTransportContext,
)
from tests.release.lab_controller.read_only_transport import (
    ReadOnlyTransportDecision,
    build_example_transport_query,
)

Phase9BDecision = ReadOnlyBackendDecision

PHASE9B_SCHEMA_REVISION = "phase9b.live_read_only.v1"
PHASE9B_WRITER_REVISION = "phase9b.strict_recursive_writer.v1"
PHASE9B_CONTROLLER_REVISION = "phase9b.controller_owned_discovery.v1"
IDENTITY_BUNDLE_QUERY_ID = "phase9b.identity_bundle"

_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_PUBLIC_ID = re.compile(r"^[a-z][a-z0-9-]{2,62}$")
_SAFE_ORIGIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_SAFE_APPROVAL_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_SAFE_IDENTITY_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,252}$")
_MAX_EVIDENCE_AGE_SECONDS = 900.0
_MAX_CLOCK_SKEW_SECONDS = 120.0
_TYPED_TIMEOUT_CONTRACT = "typed_request_timeout_v1"
_TRUST_ANCHOR_CANONICALIZATION_REVISION = "phase9b.api_trust_anchor.v1"
_MAX_TRUST_ANCHOR_BYTES = 1024 * 1024
_MAX_TRUST_ANCHOR_CERTIFICATES = 64
_ALLOWED_ADDITIONAL_ARTIFACT_KEYS = frozenset({"diagnostics"})
_FORBIDDEN_CLAIM_MARKERS = (
    "certification",
    "certified",
    "logical",
    "known_state",
    "knownstate",
    "readiness",
    "mutation",
    "recovery",
    "executable",
    "authorization",
    "primary",
    "secondary",
)


class IdentityQueryId(str, Enum):
    KUBE_SYSTEM_NAMESPACE = "identity.kube_system_namespace"
    OPENSHIFT_INFRASTRUCTURE = "identity.openshift_infrastructure"
    OPENSHIFT_CLUSTER_VERSION = "identity.openshift_cluster_version"


IDENTITY_QUERY_IDS: tuple[str, ...] = tuple(query.value for query in IdentityQueryId)


@dataclass(frozen=True)
class _IdentityQueryDefinition:
    query_id: IdentityQueryId
    api_group: str
    api_version: str
    resource_plural: str
    expected_api_version: str
    expected_kind: str
    expected_name: str

    @property
    def field_selector(self) -> str:
        return f"metadata.name={self.expected_name}"


_IDENTITY_QUERIES: tuple[_IdentityQueryDefinition, ...] = (
    _IdentityQueryDefinition(
        query_id=IdentityQueryId.KUBE_SYSTEM_NAMESPACE,
        api_group="",
        api_version="v1",
        resource_plural="namespaces",
        expected_api_version="v1",
        expected_kind="Namespace",
        expected_name="kube-system",
    ),
    _IdentityQueryDefinition(
        query_id=IdentityQueryId.OPENSHIFT_INFRASTRUCTURE,
        api_group="config.openshift.io",
        api_version="v1",
        resource_plural="infrastructures",
        expected_api_version="config.openshift.io/v1",
        expected_kind="Infrastructure",
        expected_name="cluster",
    ),
    _IdentityQueryDefinition(
        query_id=IdentityQueryId.OPENSHIFT_CLUSTER_VERSION,
        api_group="config.openshift.io",
        api_version="v1",
        resource_plural="clusterversions",
        expected_api_version="config.openshift.io/v1",
        expected_kind="ClusterVersion",
        expected_name="version",
    ),
)


@dataclass(frozen=True)
class TypedReadRequest:
    """Single fixed, bounded Kubernetes/OpenShift list request."""

    query_id: str
    verb: str
    api_group: str
    api_version: str
    resource_plural: str
    field_selector: str
    continuation_token: str | None
    resource_version: str | None
    page_size: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        if self.verb != "list":
            raise ValueError("typed Phase 9B requests permit only the list verb")
        definition = next(
            (candidate for candidate in _IDENTITY_QUERIES if candidate.query_id.value == self.query_id),
            None,
        )
        if definition is None:
            raise ValueError("typed Phase 9B request is not allowlisted")
        if (
            self.api_group,
            self.api_version,
            self.resource_plural,
            self.field_selector,
        ) != (
            definition.api_group,
            definition.api_version,
            definition.resource_plural,
            definition.field_selector,
        ):
            raise ValueError("typed Phase 9B request does not match its allowlisted query")


@dataclass(frozen=True)
class TypedReadPage:
    """Normalized page returned by an injected typed API implementation."""

    query_id: str
    items: tuple[Mapping[str, Any], ...]
    requested_continuation_token: str | None
    continuation_token: str | None
    resource_version: str
    remaining_item_count: int | None
    truncated: bool
    collected_at: datetime
    evidence_origin: str
    source_revision: str


class TypedReadPageReader(Protocol):
    """Caller-supplied page reader invoked only after controller and transport gates pass.

    Implementations admitted through ``TypedReadApi`` must enforce ``request.timeout_seconds``
    in the underlying Kubernetes/OpenShift request. The controller rejects bindings that do not
    explicitly select that contract before contact.
    """

    def read_page(
        self,
        *,
        public_hub_id: str,
        access_handle: object,
        context_handle: object,
        api_trust_anchor_pem: bytes,
        request: TypedReadRequest,
    ) -> TypedReadPage:
        """Perform one bounded read-only page request."""
        ...


@dataclass(frozen=True)
class TypedReadApi:
    """Controller-owned passive binding between runtime handles and an injected page reader."""

    public_hub_id: str
    access_handle: object
    context_handle: object
    api_trust_anchor_pem: bytes
    reader: TypedReadPageReader
    timeout_contract: str

    def read_page(self, request: TypedReadRequest) -> TypedReadPage:
        return self.reader.read_page(
            public_hub_id=self.public_hub_id,
            access_handle=self.access_handle,
            context_handle=self.context_handle,
            api_trust_anchor_pem=self.api_trust_anchor_pem,
            request=request,
        )


class Phase9BClock(Protocol):
    def utcnow(self) -> datetime:
        """Return a timezone-aware controller timestamp."""
        ...

    def monotonic(self) -> float:
        """Return a monotonic controller time value."""
        ...


class _SystemClock:
    def utcnow(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()


class _ControllerClockFailure(Exception):
    pass


class _TrustedControllerClock:
    def __init__(self, clock: Phase9BClock) -> None:
        self._clock = clock
        self._last_utc: datetime | None = None
        self._last_monotonic: float | None = None

    def utcnow(self) -> datetime:
        try:
            value = self._clock.utcnow()
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise ValueError
            normalized = value.astimezone(timezone.utc)
        except Exception:
            raise _ControllerClockFailure from None
        if self._last_utc is not None and normalized < self._last_utc:
            raise _ControllerClockFailure
        self._last_utc = normalized
        return normalized

    def monotonic(self) -> float:
        try:
            value = self._clock.monotonic()
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError
            normalized = float(value)
            if not math.isfinite(normalized):
                raise ValueError
        except Exception:
            raise _ControllerClockFailure from None
        if self._last_monotonic is not None and normalized < self._last_monotonic:
            raise _ControllerClockFailure
        self._last_monotonic = normalized
        return normalized


@dataclass(frozen=True)
class LiveDiscoveryBounds:
    request_timeout_seconds: float = 15.0
    page_size: int = 100
    max_pages_per_query: int = 20
    max_items_per_query: int = 1000
    total_deadline_seconds: float = 120.0
    max_evidence_age_seconds: float = 300.0
    max_clock_skew_seconds: float = 60.0


@dataclass(frozen=True)
class Phase9BRuntimeHandle:
    """Explicit runtime-only handle. Object fields are never serialized or stringified."""

    public_hub_id: str
    access_handle: object
    context_handle: object
    typed_api: TypedReadApi
    expected_evidence_origin: str


@dataclass(frozen=True)
class Phase9BIdentityEnrollment:
    """Immutable physical-ID enrollment bound to controller source/config/profile inputs."""

    hub_fingerprints: tuple[tuple[str, str], ...]
    hub_api_trust_anchor_fingerprints: tuple[tuple[str, str], ...]
    source_revision: str
    config_sha256: str
    profile_sha256: str
    enrollment_sha256: str


@dataclass(frozen=True)
class Phase9BLiveDiscoveryRequest:
    """Complete controller request. Live contact remains disabled by default."""

    allow_live_contact: bool = False
    allow_read_only_queries: bool = False
    approval_reference: str | None = None
    source_revision: str = ""
    expected_source_revision: str = ""
    source_tree_clean: bool = False
    config_sha256: str = ""
    profile_sha256: str = ""
    identity_enrollment: Phase9BIdentityEnrollment | None = None
    required_gate_ids: tuple[Any, ...] = ()
    runtime_handles: tuple[Phase9BRuntimeHandle, ...] = ()
    bounds: LiveDiscoveryBounds = field(default_factory=LiveDiscoveryBounds)
    requested_query_ids: tuple[str, ...] = (IDENTITY_BUNDLE_QUERY_ID,)
    requested_verb: str = "get"
    inherit_ambient_credentials: bool = False
    requested_claims: Mapping[str, Any] = field(default_factory=dict)
    additional_artifact_fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _QueryCollection:
    query_id: str
    items: tuple[Mapping[str, Any], ...]
    resource_version: str
    page_count: int
    item_count: int
    collected_at: tuple[datetime, ...]
    origin_sha256: str
    source_revision: str


@dataclass(frozen=True)
class _IdentityPass:
    fingerprint: str
    signal_names: tuple[str, ...]
    cluster_version_corroboration_sha256: str
    pagination: tuple[Mapping[str, Any], ...]
    timestamps: tuple[datetime, ...]
    origin_sha256: str
    source_revision: str


@dataclass(frozen=True)
class _ClientTraceEntry:
    public_hub_id: str
    query_id: str
    verb: str
    page_ordinal: int
    pagination_complete: bool
    mutation_attempted: bool = False

    def to_artifact(self) -> dict[str, Any]:
        return {
            "public_hub_id": self.public_hub_id,
            "query_id": self.query_id,
            "verb": self.verb,
            "page_ordinal": self.page_ordinal,
            "pagination_complete": self.pagination_complete,
            "mutation_attempted": False,
        }


class _CollectionBlocked(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__("Phase 9B collection blocked")
        self.reason_code = reason_code


def fingerprint_identity_inputs(
    *,
    kube_system_uid: str,
    infrastructure_uid: str,
    infrastructure_name: str,
    cluster_version_uid: str,
    api_trust_anchor_fingerprint: str,
) -> str:
    """Canonicalize the allowlisted physical identity fields and derive a redacted fingerprint."""

    document = _canonical_identity_document(
        kube_system_uid=kube_system_uid,
        infrastructure_uid=infrastructure_uid,
        infrastructure_name=infrastructure_name,
        cluster_version_uid=cluster_version_uid,
        api_trust_anchor_fingerprint=api_trust_anchor_fingerprint,
    )
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def fingerprint_api_trust_anchor(api_trust_anchor_pem: bytes) -> str:
    """Return a versioned fingerprint of a validated PEM trust-anchor bundle."""

    if (
        type(api_trust_anchor_pem) is not bytes
        or not api_trust_anchor_pem
        or len(api_trust_anchor_pem) > _MAX_TRUST_ANCHOR_BYTES
    ):
        raise ValueError("API trust anchor is missing or invalid")
    try:
        certificates = x509.load_pem_x509_certificates(api_trust_anchor_pem)
        certificate_digests = sorted(
            hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()
            for certificate in certificates
        )
    except Exception:
        raise ValueError("API trust anchor is missing or invalid") from None
    if (
        not certificate_digests
        or len(certificate_digests) > _MAX_TRUST_ANCHOR_CERTIFICATES
        or len(set(certificate_digests)) != len(certificate_digests)
    ):
        raise ValueError("API trust anchor is missing or invalid")
    document = {
        "schema": _TRUST_ANCHOR_CANONICALIZATION_REVISION,
        "certificate_sha256": certificate_digests,
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def build_phase9b_identity_enrollment(
    *,
    hub_fingerprints: Mapping[str, str],
    hub_api_trust_anchor_fingerprints: Mapping[str, str],
    source_revision: str,
    config_sha256: str,
    profile_sha256: str,
) -> Phase9BIdentityEnrollment:
    """Build the immutable enrollment record consumed by the controller entrypoint."""

    entries = tuple(sorted((str(hub_id), str(fingerprint)) for hub_id, fingerprint in hub_fingerprints.items()))
    trust_anchor_entries = tuple(
        sorted((str(hub_id), str(fingerprint)) for hub_id, fingerprint in hub_api_trust_anchor_fingerprints.items())
    )
    enrollment_sha256 = _identity_enrollment_digest(
        hub_fingerprints=entries,
        hub_api_trust_anchor_fingerprints=trust_anchor_entries,
        source_revision=source_revision,
        config_sha256=config_sha256,
        profile_sha256=profile_sha256,
    )
    return Phase9BIdentityEnrollment(
        hub_fingerprints=entries,
        hub_api_trust_anchor_fingerprints=trust_anchor_entries,
        source_revision=source_revision,
        config_sha256=config_sha256,
        profile_sha256=profile_sha256,
        enrollment_sha256=enrollment_sha256,
    )


class ControllerOwnedLiveDiscoveryClient(ReadOnlyLiveClientProtocol):
    """Real controller client over an injected typed read API.

    It performs every page read itself, returns only fingerprinted/allowlisted summaries to the
    transport, and retains raw API observations only in local call frames.
    """

    def __init__(
        self,
        *,
        public_hub_id: str,
        api: TypedReadApi,
        expected_origin: str,
        source_revision: str,
        bounds: LiveDiscoveryBounds,
        clock: Phase9BClock,
        collection_start_utc: datetime,
        controller_deadline: float,
    ) -> None:
        self._public_hub_id = public_hub_id
        self._api = api
        self._expected_origin = expected_origin
        self._source_revision = source_revision
        self._bounds = bounds
        self._clock = clock
        self._collection_start_utc = collection_start_utc
        self._controller_deadline = controller_deadline
        self.failure_code: str | None = None
        self.last_identity_pass: _IdentityPass | None = None
        self.trace: list[_ClientTraceEntry] = []
        self._controller_armed = False

    def _arm_for_controller_transport(self) -> None:
        self._controller_armed = True

    def execute_read_query(self, request: ReadOnlyLiveClientRequest) -> RawReadOnlyLiveResponse:
        self.failure_code = None
        self.last_identity_pass = None
        try:
            if self._controller_armed is not True:
                raise _CollectionBlocked("contact_before_controller_gates")
            self._controller_armed = False
            self._validate_bundle_request(request)
            identity_pass = self._collect_identity_pass(request)
        except _CollectionBlocked as exc:
            self.failure_code = exc.reason_code
            raise ReadOnlyLiveSafetyError("Phase 9B collection safety gate blocked") from None
        except _ControllerClockFailure:
            self.failure_code = "controller_clock_failure"
            raise ReadOnlyLiveSafetyError("Phase 9B controller clock failed") from None
        except TimeoutError:
            self.failure_code = "api_timeout"
            raise ReadOnlyLiveTimeoutError("Phase 9B typed read timed out") from None
        except ReadOnlyLivePermanentError:
            self.failure_code = "api_permanent_failure"
            raise
        except (ReadOnlyLiveSafetyError, ReadOnlyLiveTimeoutError, ReadOnlyLiveTransientError):
            raise
        except Exception:
            self.failure_code = "api_failure"
            raise ReadOnlyLiveTransientError("Phase 9B typed read failed") from None

        self.last_identity_pass = identity_pass
        return RawReadOnlyLiveResponse(
            query_id=request.query_id,
            payload={
                "identity_fingerprint": identity_pass.fingerprint,
                "signal_names": list(identity_pass.signal_names),
                "signal_count": len(identity_pass.signal_names),
                "pagination": [dict(item) for item in identity_pass.pagination],
                "pagination_complete": True,
                "evidence_origin_sha256": identity_pass.origin_sha256,
                "source_revision": identity_pass.source_revision,
            },
        )

    def _validate_bundle_request(self, request: ReadOnlyLiveClientRequest) -> None:
        if (
            request.query_id != IDENTITY_BUNDLE_QUERY_ID
            or request.query_family != "cluster_identity"
            or request.verb != "get"
            or request.resource_family != "cluster_identity"
            or request.hub_label != self._public_hub_id
        ):
            raise _CollectionBlocked("query_not_allowlisted")
        if (
            request.page_size != self._bounds.page_size
            or request.max_pages_per_query != self._bounds.max_pages_per_query
            or request.max_items_per_query != self._bounds.max_items_per_query
            or request.total_deadline_seconds != self._bounds.total_deadline_seconds
        ):
            raise _CollectionBlocked("collection_bound_mismatch")

    def _collect_identity_pass(self, request: ReadOnlyLiveClientRequest) -> _IdentityPass:
        collections = tuple(self._collect_query(definition, request) for definition in _IDENTITY_QUERIES)
        by_id = {collection.query_id: collection for collection in collections}
        namespace = _single_identity_item(
            by_id[IdentityQueryId.KUBE_SYSTEM_NAMESPACE.value],
            _IDENTITY_QUERIES[0],
        )
        infrastructure = _single_identity_item(
            by_id[IdentityQueryId.OPENSHIFT_INFRASTRUCTURE.value],
            _IDENTITY_QUERIES[1],
        )
        cluster_version = _single_identity_item(
            by_id[IdentityQueryId.OPENSHIFT_CLUSTER_VERSION.value],
            _IDENTITY_QUERIES[2],
        )

        fingerprint = fingerprint_identity_inputs(
            kube_system_uid=_metadata_scalar(namespace, "uid"),
            infrastructure_uid=_metadata_scalar(infrastructure, "uid"),
            infrastructure_name=_nested_scalar(infrastructure, ("status", "infrastructureName")),
            cluster_version_uid=_metadata_scalar(cluster_version, "uid"),
            api_trust_anchor_fingerprint=fingerprint_api_trust_anchor(self._api.api_trust_anchor_pem),
        )
        cluster_version_corroboration_sha256 = _digest_text(
            _nested_scalar(cluster_version, ("status", "desired", "version"))
        )
        timestamps = tuple(timestamp for collection in collections for timestamp in collection.collected_at)
        origins = {collection.origin_sha256 for collection in collections}
        revisions = {collection.source_revision for collection in collections}
        if len(origins) != 1:
            raise _CollectionBlocked("mixed_evidence_origin")
        if revisions != {self._source_revision}:
            raise _CollectionBlocked("wrong_evidence_source_revision")
        return _IdentityPass(
            fingerprint=fingerprint,
            signal_names=(
                "kube_system_namespace_uid",
                "openshift_infrastructure_identity",
                "api_trust_anchor_sha256",
                "openshift_cluster_version_uid",
            ),
            cluster_version_corroboration_sha256=cluster_version_corroboration_sha256,
            pagination=tuple(
                {
                    "query_id": collection.query_id,
                    "page_count": collection.page_count,
                    "item_count": collection.item_count,
                    "pagination_complete": True,
                    "resource_version_sha256": _digest_text(collection.resource_version),
                }
                for collection in collections
            ),
            timestamps=timestamps,
            origin_sha256=next(iter(origins)),
            source_revision=next(iter(revisions)),
        )

    def _collect_query(
        self,
        definition: _IdentityQueryDefinition,
        request: ReadOnlyLiveClientRequest,
    ) -> _QueryCollection:
        items: list[Mapping[str, Any]] = []
        timestamps: list[datetime] = []
        requested_token: str | None = None
        seen_tokens: set[str] = set()
        bound_resource_version: str | None = None
        page_count = 0
        origin_sha256: str | None = None

        while True:
            request_started = self._clock.monotonic()
            remaining_seconds = self._controller_deadline - request_started
            if remaining_seconds <= 0:
                raise _CollectionBlocked("collection_deadline_exceeded")
            typed_request = TypedReadRequest(
                query_id=definition.query_id.value,
                verb="list",
                api_group=definition.api_group,
                api_version=definition.api_version,
                resource_plural=definition.resource_plural,
                field_selector=definition.field_selector,
                continuation_token=requested_token,
                resource_version=bound_resource_version,
                page_size=request.page_size,
                timeout_seconds=min(
                    request.timeout_seconds,
                    self._bounds.request_timeout_seconds,
                    remaining_seconds,
                ),
            )
            request_deadline = request_started + typed_request.timeout_seconds
            page = self._api.read_page(typed_request)
            page_count += 1
            request_completed = self._clock.monotonic()
            if request_completed >= request_deadline:
                raise _CollectionBlocked("request_deadline_exceeded")
            if request_completed >= self._controller_deadline:
                raise _CollectionBlocked("collection_deadline_exceeded")
            _validate_page_shape(page, typed_request)
            if page.evidence_origin != self._expected_origin:
                raise _CollectionBlocked("wrong_evidence_origin")
            page_origin_sha256 = _digest_text(page.evidence_origin)
            if origin_sha256 is not None and page_origin_sha256 != origin_sha256:
                raise _CollectionBlocked("mixed_evidence_origin")
            origin_sha256 = page_origin_sha256
            _validate_page_provenance(
                page,
                expected_source_revision=self._source_revision,
                controller_now=self._clock.utcnow(),
                collection_start_utc=self._collection_start_utc,
                bounds=self._bounds,
            )
            timestamps.append(page.collected_at)

            bound_resource_version = _bind_resource_version(bound_resource_version, page.resource_version)
            if len(items) + len(page.items) > request.max_items_per_query:
                raise _CollectionBlocked("item_limit_before_completeness")
            items.extend(page.items)

            next_token = _normalize_continuation_token(page.continuation_token)
            complete = _page_is_complete(page, next_token)
            self.trace.append(
                _ClientTraceEntry(
                    public_hub_id=self._public_hub_id,
                    query_id=definition.query_id.value,
                    verb="list",
                    page_ordinal=page_count,
                    pagination_complete=complete,
                )
            )
            if complete:
                if bound_resource_version is None or origin_sha256 is None:
                    raise _CollectionBlocked("partial_discovery")
                return _QueryCollection(
                    query_id=definition.query_id.value,
                    items=tuple(items),
                    resource_version=bound_resource_version,
                    page_count=page_count,
                    item_count=len(items),
                    collected_at=tuple(timestamps),
                    origin_sha256=origin_sha256,
                    source_revision=self._source_revision,
                )
            if page_count >= request.max_pages_per_query:
                raise _CollectionBlocked("page_limit_before_completeness")
            _validate_next_token(next_token, requested_token, seen_tokens)
            if next_token is None:  # pragma: no cover - helper blocks; narrows the type
                raise _CollectionBlocked("missing_continuation_state")
            seen_tokens.add(next_token)
            requested_token = next_token


def run_phase9b_live_discovery(
    request: Phase9BLiveDiscoveryRequest,
    *,
    clock: Phase9BClock | None = None,
) -> Phase9BReadOnlyBackendResult:
    """Run the controller-owned Phase 9B entrypoint.

    No typed API method is touched until every controller gate passes.
    """

    controller_clock = _TrustedControllerClock(clock or _SystemClock())
    validated_request, gate_failure = _validate_precontact_request(request)
    if gate_failure is not None:
        return _blocked(gate_failure)
    if validated_request is None:  # pragma: no cover - failure branch above guarantees this
        return _blocked("invalid_controller_request")
    request = validated_request

    try:
        start_utc = controller_clock.utcnow()
        start_monotonic = controller_clock.monotonic()
    except _ControllerClockFailure:
        return _blocked("controller_clock_failure")
    controller_deadline = start_monotonic + request.bounds.total_deadline_seconds
    clients: dict[str, ControllerOwnedLiveDiscoveryClient] = {}
    first_passes: dict[str, _IdentityPass] = {}
    second_passes: dict[str, _IdentityPass] = {}

    for pass_number, target in ((1, first_passes), (2, second_passes)):
        for handle in request.runtime_handles:
            client = clients.get(handle.public_hub_id)
            if client is None:
                client = ControllerOwnedLiveDiscoveryClient(
                    public_hub_id=handle.public_hub_id,
                    api=handle.typed_api,
                    expected_origin=handle.expected_evidence_origin,
                    source_revision=request.source_revision,
                    bounds=request.bounds,
                    clock=controller_clock,
                    collection_start_utc=start_utc,
                    controller_deadline=controller_deadline,
                )
                clients[handle.public_hub_id] = client
            result = _execute_identity_bundle(request, handle, client)
            if result.decision is not ReadOnlyTransportDecision.PASS or client.last_identity_pass is None:
                reason_code = client.failure_code or _transport_failure_code(result.decision.value)
                decision = (
                    Phase9BDecision.INFRA_RETRYABLE
                    if reason_code in {"api_timeout", "api_failure", "transport_failure"}
                    else Phase9BDecision.BLOCKED
                )
                return _blocked(reason_code, decision=decision)
            target[handle.public_hub_id] = client.last_identity_pass

        if pass_number == 1:
            try:
                if controller_clock.monotonic() >= controller_deadline:
                    return _blocked("collection_deadline_exceeded")
            except _ControllerClockFailure:
                return _blocked("controller_clock_failure")

    for handle in request.runtime_handles:
        first = first_passes[handle.public_hub_id]
        second = second_passes[handle.public_hub_id]
        if first.fingerprint != second.fingerprint:
            return _blocked("identity_changed_during_collection")
        if first.origin_sha256 != second.origin_sha256:
            return _blocked("mixed_evidence_origin")

    identity_fingerprints = {
        handle.public_hub_id: first_passes[handle.public_hub_id].fingerprint for handle in request.runtime_handles
    }
    if len(set(identity_fingerprints.values())) != len(identity_fingerprints):
        return _blocked("duplicate_identity_fingerprint")
    enrollment = request.identity_enrollment
    if not isinstance(enrollment, Phase9BIdentityEnrollment):  # pragma: no cover - pre-contact gate guarantees this
        return _blocked("invalid_identity_enrollment")
    expected_fingerprints = dict(enrollment.hub_fingerprints)
    if any(identity_fingerprints[hub_id] != expected_fingerprints[hub_id] for hub_id in identity_fingerprints):
        return _blocked("identity_fingerprint_mismatch")

    all_timestamps = [
        timestamp
        for pass_map in (first_passes, second_passes)
        for identity_pass in pass_map.values()
        for timestamp in identity_pass.timestamps
    ]
    if not all_timestamps:
        return _blocked("partial_discovery")
    spread = max(all_timestamps) - min(all_timestamps)
    if spread > timedelta(seconds=request.bounds.max_clock_skew_seconds):
        return _blocked("excessive_clock_skew")
    try:
        end_monotonic = controller_clock.monotonic()
        end_utc = controller_clock.utcnow()
        if end_monotonic >= controller_deadline:
            return _blocked("collection_deadline_exceeded")
        for timestamp in all_timestamps:
            _validate_freshness(timestamp, end_utc, request.bounds)
    except _ControllerClockFailure:
        return _blocked("controller_clock_failure")
    except _CollectionBlocked as exc:
        return _blocked(exc.reason_code)

    artifact = _build_artifact(
        request=request,
        start_utc=start_utc,
        end_utc=end_utc,
        identity_fingerprints=identity_fingerprints,
        first_passes=first_passes,
        second_passes=second_passes,
        clients=clients,
        spread_seconds=spread.total_seconds(),
    )
    if artifact is None:
        return _blocked("redaction_failure")
    return Phase9BReadOnlyBackendResult(
        decision=Phase9BDecision.PASS,
        identity_fingerprints=identity_fingerprints,
        artifact=artifact,
    )


def _execute_identity_bundle(
    request: Phase9BLiveDiscoveryRequest,
    handle: Phase9BRuntimeHandle,
    client: ControllerOwnedLiveDiscoveryClient,
) -> Any:
    client._arm_for_controller_transport()
    context = RuntimeOnlyLiveTransportContext(
        handle=RuntimeOnlyLiveHubHandle(
            physical_label=handle.public_hub_id,
            kubeconfig_ref="phase9b-runtime-access-handle",
            context_ref="phase9b-runtime-context-handle",
        ),
        options=ReadOnlyLiveTransportOptions(
            allow_live_contact=request.allow_live_contact,
            allow_read_only_queries=request.allow_read_only_queries,
            timeout_seconds=request.bounds.request_timeout_seconds,
            approval_reference=request.approval_reference,
            page_size=request.bounds.page_size,
            max_pages_per_query=request.bounds.max_pages_per_query,
            max_items_per_query=request.bounds.max_items_per_query,
            total_deadline_seconds=request.bounds.total_deadline_seconds,
        ),
        gate_ids=request.required_gate_ids,
    )
    query = replace(
        build_example_transport_query(),
        query_id=IDENTITY_BUNDLE_QUERY_ID,
        hub_label=handle.public_hub_id,
    )
    return ReadOnlyLiveTransport(context, client).execute(query)


def _validate_precontact_request(
    request: Any,
) -> tuple[Phase9BLiveDiscoveryRequest | None, str | None]:
    if not isinstance(request, Phase9BLiveDiscoveryRequest):
        return None, "invalid_controller_request"
    if type(request.additional_artifact_fields) is not dict:
        return None, "redaction_failure"
    controller_policy_failure = _validate_controller_policy(request)
    if controller_policy_failure is not None:
        return None, controller_policy_failure
    try:
        audited_additional_fields, _ = strict_recursive_artifact_audit(request.additional_artifact_fields)
    except Exception:
        return None, "redaction_failure"
    request_snapshot = replace(request, additional_artifact_fields=audited_additional_fields)
    for validator in (
        _validate_query_and_claim_policy,
        _validate_runtime_handle_set,
        _validate_identity_enrollment,
    ):
        failure = validator(request_snapshot)
        if failure is not None:
            return None, failure
    return request_snapshot, None


def _validate_controller_policy(request: Phase9BLiveDiscoveryRequest) -> str | None:
    if request.allow_live_contact is not True:
        return "live_contact_disabled"
    if request.allow_read_only_queries is not True:
        return "read_only_queries_disabled"
    if not isinstance(request.approval_reference, str) or not _SAFE_APPROVAL_REFERENCE.fullmatch(
        request.approval_reference
    ):
        return "missing_operator_authorization"
    if request.inherit_ambient_credentials is not False:
        return "ambient_credentials_forbidden"
    if request.source_tree_clean is not True:
        return "dirty_source_revision"
    if (
        not isinstance(request.source_revision, str)
        or not isinstance(request.expected_source_revision, str)
        or not _HEX_40.fullmatch(request.source_revision)
        or request.source_revision != request.expected_source_revision
    ):
        return "wrong_source_revision"
    if not isinstance(request.config_sha256, str) or not _SHA256.fullmatch(request.config_sha256):
        return "invalid_config_hash"
    if not isinstance(request.profile_sha256, str) or not _SHA256.fullmatch(request.profile_sha256):
        return "invalid_profile_hash"
    if type(request.required_gate_ids) is not tuple or any(
        not isinstance(gate, (str, Enum)) for gate in request.required_gate_ids
    ):
        return "missing_controller_gates"
    required_gates = {gate.value for gate in required_read_only_discovery_gate_ids()}
    provided_gates = {_enum_value(gate) for gate in request.required_gate_ids}
    if not required_gates.issubset(provided_gates):
        return "missing_controller_gates"
    if not _valid_bounds(request.bounds):
        return "invalid_collection_bounds"
    return None


def _validate_query_and_claim_policy(request: Phase9BLiveDiscoveryRequest) -> str | None:
    if (
        type(request.requested_query_ids) is not tuple
        or request.requested_query_ids != (IDENTITY_BUNDLE_QUERY_ID,)
        or request.requested_verb != "get"
    ):
        return "query_not_allowlisted"
    if type(request.requested_claims) is not dict or request.requested_claims:
        return "forbidden_claim"
    if not set(request.additional_artifact_fields).issubset(_ALLOWED_ADDITIONAL_ARTIFACT_KEYS):
        return "forbidden_claim"
    if _contains_forbidden_claim_key(request.additional_artifact_fields):
        return "forbidden_claim"
    return None


def _validate_runtime_handle_set(request: Phase9BLiveDiscoveryRequest) -> str | None:
    if type(request.runtime_handles) is not tuple:
        return "invalid_runtime_handle"
    if len(request.runtime_handles) != 2:
        return "invalid_runtime_handle"
    public_ids: set[str] = set()
    origins: set[str] = set()
    access_ids: set[int] = set()
    context_ids: set[int] = set()
    api_ids: set[int] = set()
    for handle in request.runtime_handles:
        if not isinstance(handle, Phase9BRuntimeHandle):
            return "invalid_runtime_handle"
        if (
            handle.access_handle is None
            or handle.context_handle is None
            or handle.typed_api is None
            or type(handle.typed_api) is not TypedReadApi
            or not _reader_has_callable_page_method(handle.typed_api.reader)
            or handle.typed_api.timeout_contract != _TYPED_TIMEOUT_CONTRACT
        ):
            return "invalid_runtime_handle"
        if type(handle.typed_api.api_trust_anchor_pem) is not bytes or not handle.typed_api.api_trust_anchor_pem:
            return "missing_api_trust_anchor"
        try:
            fingerprint_api_trust_anchor(handle.typed_api.api_trust_anchor_pem)
        except ValueError:
            return "invalid_api_trust_anchor"
        if (
            handle.typed_api.public_hub_id != handle.public_hub_id
            or handle.typed_api.access_handle is not handle.access_handle
            or handle.typed_api.context_handle is not handle.context_handle
        ):
            return "runtime_handle_binding_mismatch"
        if not isinstance(handle.public_hub_id, str) or not _SAFE_PUBLIC_ID.fullmatch(handle.public_hub_id):
            return "invalid_runtime_handle"
        if not isinstance(handle.expected_evidence_origin, str) or not _SAFE_ORIGIN.fullmatch(
            handle.expected_evidence_origin
        ):
            return "invalid_runtime_handle"
        if (
            handle.public_hub_id in public_ids
            or id(handle.access_handle) in access_ids
            or id(handle.context_handle) in context_ids
            or id(handle.typed_api.reader) in api_ids
        ):
            return "duplicate_runtime_handle"
        public_ids.add(handle.public_hub_id)
        origins.add(handle.expected_evidence_origin)
        access_ids.add(id(handle.access_handle))
        context_ids.add(id(handle.context_handle))
        api_ids.add(id(handle.typed_api.reader))
    if len(origins) != 2:
        return "mixed_evidence_origin"
    return None


def _reader_has_callable_page_method(reader: Any) -> bool:
    try:
        read_page = inspect.getattr_static(reader, "read_page")
    except (AttributeError, TypeError):
        return False
    return callable(read_page)


def _validate_identity_enrollment(request: Phase9BLiveDiscoveryRequest) -> str | None:
    enrollment = request.identity_enrollment
    if type(enrollment) is not Phase9BIdentityEnrollment:
        return "invalid_identity_enrollment"
    if type(enrollment.hub_fingerprints) is not tuple or any(
        type(pair) is not tuple or len(pair) != 2 or not isinstance(pair[0], str) or not isinstance(pair[1], str)
        for pair in enrollment.hub_fingerprints
    ):
        return "invalid_identity_enrollment"
    if type(enrollment.hub_api_trust_anchor_fingerprints) is not tuple or any(
        type(pair) is not tuple or len(pair) != 2 or not isinstance(pair[0], str) or not isinstance(pair[1], str)
        for pair in enrollment.hub_api_trust_anchor_fingerprints
    ):
        return "invalid_identity_enrollment"
    if (
        not isinstance(enrollment.source_revision, str)
        or not isinstance(enrollment.config_sha256, str)
        or not isinstance(enrollment.profile_sha256, str)
        or not isinstance(enrollment.enrollment_sha256, str)
        or enrollment.source_revision != request.source_revision
        or enrollment.config_sha256 != request.config_sha256
        or enrollment.profile_sha256 != request.profile_sha256
    ):
        return "invalid_identity_enrollment"
    expected_digest = _identity_enrollment_digest(
        hub_fingerprints=enrollment.hub_fingerprints,
        hub_api_trust_anchor_fingerprints=enrollment.hub_api_trust_anchor_fingerprints,
        source_revision=enrollment.source_revision,
        config_sha256=enrollment.config_sha256,
        profile_sha256=enrollment.profile_sha256,
    )
    if enrollment.enrollment_sha256 != expected_digest:
        return "invalid_identity_enrollment"
    if len(enrollment.hub_fingerprints) != 2:
        return "invalid_identity_enrollment"
    fingerprints = dict(enrollment.hub_fingerprints)
    if len(fingerprints) != 2 or set(fingerprints) != {handle.public_hub_id for handle in request.runtime_handles}:
        return "invalid_identity_enrollment"
    if any(
        not _SAFE_PUBLIC_ID.fullmatch(hub_id) or not _FINGERPRINT.fullmatch(fingerprint)
        for hub_id, fingerprint in enrollment.hub_fingerprints
    ):
        return "invalid_identity_enrollment"
    if len(set(fingerprints.values())) != 2:
        return "duplicate_identity_fingerprint"
    trust_anchor_fingerprints = dict(enrollment.hub_api_trust_anchor_fingerprints)
    if (
        len(enrollment.hub_api_trust_anchor_fingerprints) != 2
        or len(trust_anchor_fingerprints) != 2
        or set(trust_anchor_fingerprints) != {handle.public_hub_id for handle in request.runtime_handles}
        or any(
            not _SAFE_PUBLIC_ID.fullmatch(hub_id) or not _FINGERPRINT.fullmatch(fingerprint)
            for hub_id, fingerprint in enrollment.hub_api_trust_anchor_fingerprints
        )
    ):
        return "invalid_identity_enrollment"
    observed_trust_anchor_fingerprints = {
        handle.public_hub_id: fingerprint_api_trust_anchor(handle.typed_api.api_trust_anchor_pem)
        for handle in request.runtime_handles
    }
    if observed_trust_anchor_fingerprints != trust_anchor_fingerprints:
        return "api_trust_anchor_mismatch"
    return None


def _valid_bounds(bounds: Any) -> bool:
    if type(bounds) is not LiveDiscoveryBounds:
        return False
    if not all(
        _finite_positive_at_most(value, maximum)
        for value, maximum in (
            (bounds.request_timeout_seconds, READ_ONLY_LIVE_MAX_REQUEST_TIMEOUT_SECONDS),
            (bounds.total_deadline_seconds, READ_ONLY_LIVE_MAX_TOTAL_DEADLINE_SECONDS),
            (bounds.max_evidence_age_seconds, _MAX_EVIDENCE_AGE_SECONDS),
            (bounds.max_clock_skew_seconds, _MAX_CLOCK_SKEW_SECONDS),
        )
    ):
        return False
    if (
        not isinstance(bounds.page_size, int)
        or isinstance(bounds.page_size, bool)
        or bounds.page_size <= 0
        or not isinstance(bounds.max_pages_per_query, int)
        or isinstance(bounds.max_pages_per_query, bool)
        or bounds.max_pages_per_query <= 0
        or not isinstance(bounds.max_items_per_query, int)
        or isinstance(bounds.max_items_per_query, bool)
        or bounds.max_items_per_query <= 0
    ):
        return False
    if (
        bounds.page_size > READ_ONLY_LIVE_MAX_PAGE_SIZE
        or bounds.max_pages_per_query > READ_ONLY_LIVE_MAX_PAGES_PER_QUERY
        or bounds.max_items_per_query > READ_ONLY_LIVE_MAX_ITEMS_PER_QUERY
    ):
        return False
    return True


def _finite_positive_at_most(value: Any, maximum: float) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, int):
        return 0 < value <= maximum
    return math.isfinite(value) and 0 < value <= maximum


def _validate_page_shape(page: Any, request: TypedReadRequest) -> None:
    if not isinstance(page, TypedReadPage) or page.query_id != request.query_id:
        raise _CollectionBlocked("partial_discovery")
    if page.requested_continuation_token != request.continuation_token:
        raise _CollectionBlocked("invalid_token_transition")
    if not isinstance(page.items, tuple) or any(not isinstance(item, Mapping) for item in page.items):
        raise _CollectionBlocked("partial_discovery")
    if not isinstance(page.resource_version, str) or not page.resource_version.strip():
        raise _CollectionBlocked("inconsistent_resource_version")
    if page.remaining_item_count is not None and (
        not isinstance(page.remaining_item_count, int)
        or isinstance(page.remaining_item_count, bool)
        or page.remaining_item_count < 0
    ):
        raise _CollectionBlocked("invalid_continuation_state")
    if not isinstance(page.truncated, bool):
        raise _CollectionBlocked("invalid_continuation_state")


def _validate_freshness(collected_at: Any, controller_now: datetime, bounds: LiveDiscoveryBounds) -> None:
    if not isinstance(collected_at, datetime) or collected_at.tzinfo is None or collected_at.utcoffset() is None:
        raise _CollectionBlocked("unreadable_evidence_timestamp")
    observed = collected_at.astimezone(timezone.utc)
    now = controller_now.astimezone(timezone.utc)
    if now - observed > timedelta(seconds=bounds.max_evidence_age_seconds):
        raise _CollectionBlocked("stale_evidence")
    if observed - now > timedelta(seconds=bounds.max_clock_skew_seconds):
        raise _CollectionBlocked("excessive_clock_skew")


def _validate_page_provenance(
    page: TypedReadPage,
    *,
    expected_source_revision: str,
    controller_now: datetime,
    collection_start_utc: datetime,
    bounds: LiveDiscoveryBounds,
) -> None:
    if page.source_revision != expected_source_revision:
        raise _CollectionBlocked("wrong_evidence_source_revision")
    _validate_freshness(page.collected_at, controller_now, bounds)
    if page.collected_at.astimezone(timezone.utc) < collection_start_utc.astimezone(timezone.utc) - timedelta(
        seconds=bounds.max_clock_skew_seconds
    ):
        raise _CollectionBlocked("excessive_clock_skew")


def _bind_resource_version(current: str | None, observed: str) -> str:
    if current is None:
        return observed
    if observed != current:
        raise _CollectionBlocked("inconsistent_resource_version")
    return current


def _page_is_complete(page: TypedReadPage, next_token: str | None) -> bool:
    if page.truncated and next_token is None:
        raise _CollectionBlocked("truncated_collection")
    if page.remaining_item_count is not None and page.remaining_item_count > 0 and next_token is None:
        raise _CollectionBlocked("missing_continuation_state")
    return next_token is None and page.remaining_item_count in (None, 0) and page.truncated is False


def _validate_next_token(
    next_token: str | None,
    requested_token: str | None,
    seen_tokens: set[str],
) -> None:
    if next_token is None:
        raise _CollectionBlocked("missing_continuation_state")
    if next_token == requested_token or next_token in seen_tokens:
        raise _CollectionBlocked("repeated_continuation_token")


def _normalize_continuation_token(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise _CollectionBlocked("invalid_continuation_state")
    return value


def _single_identity_item(
    collection: _QueryCollection,
    definition: _IdentityQueryDefinition,
) -> Mapping[str, Any]:
    if not collection.items:
        raise _CollectionBlocked("missing_identity_signal")
    if len(collection.items) != 1:
        raise _CollectionBlocked("ambiguous_identity_signal")
    item = collection.items[0]
    if item.get("apiVersion") != definition.expected_api_version or item.get("kind") != definition.expected_kind:
        raise _CollectionBlocked("conflicting_identity_signal")
    metadata = item.get("metadata")
    if not isinstance(metadata, Mapping) or metadata.get("name") != definition.expected_name:
        raise _CollectionBlocked("conflicting_identity_signal")
    return item


def _metadata_scalar(item: Mapping[str, Any], name: str) -> str:
    metadata = item.get("metadata")
    if not isinstance(metadata, Mapping):
        raise _CollectionBlocked("unreadable_identity_signal")
    return _identity_scalar(metadata.get(name))


def _nested_scalar(item: Mapping[str, Any], path: Sequence[str]) -> str:
    current: Any = item
    for part in path:
        if not isinstance(current, Mapping):
            raise _CollectionBlocked("unreadable_identity_signal")
        current = current.get(part)
    return _identity_scalar(current)


def _identity_scalar(value: Any) -> str:
    if not isinstance(value, str) or not _SAFE_IDENTITY_VALUE.fullmatch(value):
        raise _CollectionBlocked("unreadable_identity_signal")
    return value


def _canonical_identity_document(
    *,
    kube_system_uid: str,
    infrastructure_uid: str,
    infrastructure_name: str,
    cluster_version_uid: str,
    api_trust_anchor_fingerprint: str,
) -> dict[str, Any]:
    if not _FINGERPRINT.fullmatch(api_trust_anchor_fingerprint):
        raise ValueError("API trust anchor fingerprint is invalid")
    return {
        "schema": "phase9b.physical_identity.v1",
        "signals": {
            "api_trust_anchor": {"fingerprint": api_trust_anchor_fingerprint},
            "kube_system_namespace": {"uid": _identity_scalar(kube_system_uid)},
            "openshift_infrastructure": {
                "uid": _identity_scalar(infrastructure_uid),
                "infrastructure_name": _identity_scalar(infrastructure_name),
            },
            "openshift_cluster_version": {"uid": _identity_scalar(cluster_version_uid)},
        },
    }


def _build_artifact(
    *,
    request: Phase9BLiveDiscoveryRequest,
    start_utc: datetime,
    end_utc: datetime,
    identity_fingerprints: Mapping[str, str],
    first_passes: Mapping[str, _IdentityPass],
    second_passes: Mapping[str, _IdentityPass],
    clients: Mapping[str, ControllerOwnedLiveDiscoveryClient],
    spread_seconds: float,
) -> Mapping[str, Any] | None:
    physical_proofs: dict[str, Any] = {}
    for public_hub_id, fingerprint in identity_fingerprints.items():
        first = first_passes[public_hub_id]
        second = second_passes[public_hub_id]
        physical_proofs[public_hub_id] = {
            "identity_fingerprint": fingerprint,
            "evidence_origin_sha256": first.origin_sha256,
            "signal_names": list(first.signal_names),
            "signal_count": len(first.signal_names),
            "cluster_version_corroboration": {
                "authoritative": False,
                "first_sha256": first.cluster_version_corroboration_sha256,
                "second_sha256": second.cluster_version_corroboration_sha256,
                "stable": (first.cluster_version_corroboration_sha256 == second.cluster_version_corroboration_sha256),
            },
            "stable": first.fingerprint == second.fingerprint,
            "distinct": True,
            "pagination_complete": True,
            "collection_pass_count": 2,
            "pagination": [dict(summary) for summary in first.pagination + second.pagination],
            "source_revision": request.source_revision,
        }
    trace = [entry.to_artifact() for handle in request.runtime_handles for entry in clients[handle.public_hub_id].trace]
    artifact: dict[str, Any] = dict(request.additional_artifact_fields)
    artifact.update(
        {
            "schema_revision": PHASE9B_SCHEMA_REVISION,
            "writer_revision": PHASE9B_WRITER_REVISION,
            "controller_revision": PHASE9B_CONTROLLER_REVISION,
            "purpose": "live_read_only",
            "evidence_class": "live_read_only",
            "certification_eligible": False,
            "live_certification_evidence": False,
            "mutation_attempted": False,
            "source_revision": request.source_revision,
            "source_revision_clean": True,
            "config_sha256": request.config_sha256,
            "profile_sha256": request.profile_sha256,
            "identity_enrollment_sha256": (
                request.identity_enrollment.enrollment_sha256
                if isinstance(request.identity_enrollment, Phase9BIdentityEnrollment)
                else ""
            ),
            "collection_started_at": start_utc.astimezone(timezone.utc).isoformat(),
            "collection_ended_at": end_utc.astimezone(timezone.utc).isoformat(),
            "freshness": {
                "result": "passed",
                "max_evidence_age_seconds": request.bounds.max_evidence_age_seconds,
            },
            "clock_skew": {
                "result": "passed",
                "observed_spread_seconds": spread_seconds,
                "maximum_seconds": request.bounds.max_clock_skew_seconds,
            },
            "pagination_completeness": {
                "result": "complete",
                "all_queries_complete": True,
                "page_limit": request.bounds.max_pages_per_query,
                "item_limit": request.bounds.max_items_per_query,
                "total_deadline_seconds": request.bounds.total_deadline_seconds,
            },
            "physical_identity_proofs": physical_proofs,
            "call_trace": trace,
            "raw_observations": {
                "authoritative": False,
                "logical_role_inference_performed": False,
                "known_state_inference_performed": False,
                "readiness_inference_performed": False,
            },
        }
    )
    try:
        audited, first_audit = strict_recursive_artifact_audit(artifact)
        audited["recursive_redaction_audit"] = first_audit
        audited, final_audit = strict_recursive_artifact_audit(audited)
        audited["recursive_redaction_audit"] = final_audit
        audited, _ = strict_recursive_artifact_audit(audited)
    except Exception:
        return None
    return audited


def _digest_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _identity_enrollment_digest(
    *,
    hub_fingerprints: Sequence[tuple[str, str]],
    hub_api_trust_anchor_fingerprints: Sequence[tuple[str, str]],
    source_revision: str,
    config_sha256: str,
    profile_sha256: str,
) -> str:
    document = {
        "schema": "phase9b.identity_enrollment.v1",
        "source_revision": source_revision,
        "config_sha256": config_sha256,
        "profile_sha256": profile_sha256,
        "hub_fingerprints": [
            {"public_hub_id": hub_id, "identity_fingerprint": fingerprint}
            for hub_id, fingerprint in sorted(hub_fingerprints)
        ],
        "hub_api_trust_anchor_fingerprints": [
            {"public_hub_id": hub_id, "api_trust_anchor_fingerprint": fingerprint}
            for hub_id, fingerprint in sorted(hub_api_trust_anchor_fingerprints)
        ],
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _enum_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _contains_forbidden_claim_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            normalized = re.sub(r"[^a-z0-9]", "", lowered)
            if any(
                marker in lowered or re.sub(r"[^a-z0-9]", "", marker) in normalized
                for marker in _FORBIDDEN_CLAIM_MARKERS
            ):
                return True
            if _contains_forbidden_claim_key(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_forbidden_claim_key(item) for item in value)
    elif isinstance(value, str):
        lowered = value.lower()
        normalized = re.sub(r"[^a-z0-9]", "", lowered)
        return any(
            marker in lowered or re.sub(r"[^a-z0-9]", "", marker) in normalized for marker in _FORBIDDEN_CLAIM_MARKERS
        )
    return False


def _transport_failure_code(decision: str) -> str:
    if decision == ReadOnlyTransportDecision.INFRA_RETRYABLE.value:
        return "transport_failure"
    return "transport_blocked"


def _blocked(
    reason_code: str,
    *,
    decision: Phase9BDecision = Phase9BDecision.BLOCKED,
) -> Phase9BReadOnlyBackendResult:
    safe_reasons = {
        "ambient_credentials_forbidden": "ambient credential inheritance is forbidden",
        "api_trust_anchor_mismatch": "API trust anchor does not match enrolled evidence",
        "api_failure": "typed read API failed before complete evidence was collected",
        "api_permanent_failure": "typed read API reported a permanent failure",
        "api_timeout": "typed read API timed out before complete evidence was collected",
        "collection_deadline_exceeded": "total collection deadline was reached before completeness",
        "dirty_source_revision": "source revision is not clean",
        "duplicate_identity_fingerprint": "physical hub fingerprints are not distinct",
        "duplicate_runtime_handle": "runtime handles are not distinct",
        "forbidden_claim": "Phase 9B request contains an out-of-scope authority claim",
        "identity_changed_during_collection": "physical identity changed during the evidence window",
        "identity_fingerprint_mismatch": "physical identity does not match enrolled evidence",
        "invalid_config_hash": "configuration hash is missing or invalid",
        "invalid_api_trust_anchor": "API trust anchor is malformed or unverifiable",
        "invalid_identity_enrollment": "physical identity enrollment is missing or invalid",
        "invalid_profile_hash": "profile hash is missing or invalid",
        "invalid_runtime_handle": "two complete explicit runtime-only handles are required",
        "live_contact_disabled": "live contact was not explicitly enabled",
        "missing_controller_gates": "required controller gates are incomplete",
        "missing_api_trust_anchor": "API trust anchor is required for each controller-owned connection",
        "missing_operator_authorization": "operator authorization reference is missing",
        "query_not_allowlisted": "requested query or verb is not allowlisted",
        "request_deadline_exceeded": "typed read exceeded its controller-measured request deadline",
        "read_only_queries_disabled": "read-only queries were not explicitly enabled",
        "redaction_failure": "recursive artifact publication audit failed",
        "wrong_evidence_origin": "evidence origin does not match its runtime handle",
        "wrong_source_revision": "source revision does not match the clean binding",
    }
    return Phase9BReadOnlyBackendResult(
        decision=decision,
        reason_codes=(reason_code,),
        reasons=(safe_reasons.get(reason_code, "Phase 9B evidence failed a controller safety gate"),),
        artifact=None,
    )


__all__ = [
    "ControllerOwnedLiveDiscoveryClient",
    "IDENTITY_BUNDLE_QUERY_ID",
    "IDENTITY_QUERY_IDS",
    "IdentityQueryId",
    "LiveDiscoveryBounds",
    "PHASE9B_CONTROLLER_REVISION",
    "PHASE9B_SCHEMA_REVISION",
    "PHASE9B_WRITER_REVISION",
    "Phase9BClock",
    "Phase9BDecision",
    "Phase9BIdentityEnrollment",
    "Phase9BLiveDiscoveryRequest",
    "Phase9BReadOnlyBackendResult",
    "Phase9BRuntimeHandle",
    "TypedReadApi",
    "TypedReadPageReader",
    "TypedReadPage",
    "TypedReadRequest",
    "build_phase9b_identity_enrollment",
    "fingerprint_api_trust_anchor",
    "fingerprint_identity_inputs",
    "run_phase9b_live_discovery",
]
