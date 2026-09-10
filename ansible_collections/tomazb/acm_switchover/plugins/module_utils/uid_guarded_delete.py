# SPDX-License-Identifier: MIT
"""UID-guarded delete: the July deletion-boundary state machine.

A name-only delete cannot bind identity. Between the read that establishes which
object is being removed and the delete itself, the name may come to refer to a
different object -- a recreated CR, a controller's replacement, someone else's
resource. This deletes only the object whose ``metadata.uid`` matches what the
caller proved, and it proves absence afterwards rather than inferring it.

Two rules run through every stage:

**Only an API 404 means absent.** Discovery, authorization, TLS, timeout, transport
and decode failures are *unverifiable*, not empty, and every one fails closed. This
mirrors the strict-read contract PR A established: absence is a positive proof
obligation, never a default.

**A same-name different-UID object is fatal wherever it appears**, and is left
intact. The safe response to "this is not the object you proved" is to stop --
never to delete whatever is there now.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable

#: Stages in the order the machine passes through them, reported verbatim as
#: ``stage`` so an operator can say exactly how far a delete got.
STAGE_READ = "read"
STAGE_UID_MISMATCH = "uid_mismatch"
STAGE_ABSENT = "absent"
STAGE_WOULD_DELETE = "would_delete"
STAGE_DELETING = "delete_issued"
STAGE_POLLING = "awaiting_absence"
STAGE_COMPLETED = "completed"

#: Why a stage ended. A closed set, so callers branch on a value rather than parsing
#: a message -- and so no server text ever needs to reach the result.
REASON_OK = "ok"
REASON_NOT_FOUND = "not_found"
REASON_UID_MISMATCH = "uid_mismatch"
REASON_PRECONDITION_FAILED = "precondition_failed"
REASON_UNVERIFIABLE = "unverifiable"
REASON_TIMEOUT = "timeout"

DEFAULT_REQUEST_TIMEOUT = 30
DEFAULT_WAIT_TIMEOUT = 300
DEFAULT_WAIT_SLEEP = 5


class GuardedDeleteError(Exception):
    """Fatal guarded-delete failure, carrying a closed reason and a safe message.

    Nothing from the originating exception is interpolated -- see ``safe_api_reason``.
    """

    def __init__(self, reason: str, message: str, stage: str, *, changed: bool = False) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.stage = stage
        self.changed = changed


def normalize_timeout(value: Any, field_name: str, default: int | float) -> int | float:
    """A positive number, or the default when unset."""
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive number") from exc
    # NaN and infinity are floats that survive ``> 0`` and then defeat every deadline:
    # a NaN comparison is always false, so the bounded absence poll would never end.
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{field_name} must be a positive number")
    return int(parsed) if parsed.is_integer() else parsed


def build_dynamic_client(kubeconfig: str, context: str, request_timeout: int | float):
    """An explicitly routed dynamic client. No ambient or default context, ever.

    Both arguments are required and neither may default. An ambient fallback would
    route a *delete* at whatever cluster the environment happens to point at, which
    is the precise failure this boundary exists to prevent.
    """
    if not isinstance(kubeconfig, str) or not kubeconfig.strip():
        raise ValueError("kubeconfig is required: refusing an implicit Kubernetes context")
    if not isinstance(context, str) or not context.strip():
        raise ValueError("context is required: refusing an implicit Kubernetes context")

    from kubernetes import config
    from kubernetes.dynamic import DynamicClient

    api_client = config.new_client_from_config(
        persist_config=False,
        config_file=kubeconfig,
        context=context,
    )
    api_client.configuration.timeout = request_timeout
    original_call_api = api_client.call_api

    def bounded_call_api(*args, **kwargs):
        # kubernetes-client does not propagate Configuration.timeout through the
        # dynamic client. Apply the default at this ApiClient instance boundary so
        # discovery, GET, DELETE, and the absence polls all bound connect and read.
        # Keep a caller's explicit override intact.
        if kwargs.get("_request_timeout") is None:
            kwargs["_request_timeout"] = (request_timeout, request_timeout)
        return original_call_api(*args, **kwargs)

    api_client.call_api = bounded_call_api
    client = DynamicClient(api_client)
    # The SDK's discoverer restores a cache file keyed only by API host, shared with
    # every process that has ever talked to that cluster. Resolving a *deletion* target
    # from it is a claim about the past: a kind that has since stopped being served, or
    # a credential that may no longer read it, would still resolve, and the object-route
    # 404 that follows would be reported as an absent object. Force one live discovery
    # per invocation instead. This rewrites the shared cache file, which is the SDK's
    # own behaviour for a refresh and is acceptable here.
    client.resources.invalidate_cache()
    return client


def api_status(exc: BaseException) -> int | None:
    """The HTTP status of an API exception, or None when it is not one."""
    status = getattr(exc, "status", None)
    return status if isinstance(status, int) else None


def safe_api_reason(exc: BaseException) -> str:
    """Classify an exception without returning anything from it.

    Deliberately never interpolates the exception. API error bodies routinely echo
    request content, headers carry bearer tokens, and a kubeconfig path is itself an
    infrastructure detail. Callers get a status class and nothing more. This is the
    collection-side counterpart of the hazard tracked in issue #283.
    """
    status = api_status(exc)
    if status == 404:
        return REASON_NOT_FOUND
    if status in (409, 412):
        return REASON_PRECONDITION_FAILED
    return REASON_UNVERIFIABLE


def _metadata_of(obj: Any) -> Any:
    metadata = getattr(obj, "metadata", None)
    if metadata is None and isinstance(obj, dict):
        metadata = obj.get("metadata")
    return metadata


def _field(obj: Any, field: str) -> str | None:
    metadata = _metadata_of(obj)
    if metadata is None:
        return None
    value = getattr(metadata, field, None)
    if value is None and isinstance(metadata, dict):
        value = metadata.get(field)
    return value if isinstance(value, str) and value.strip() else None


def _absent_result() -> dict:
    """The single result shape a proved 404 produces, wherever it was proved."""
    return {
        "changed": False,
        "would_change": False,
        "stage": STAGE_ABSENT,
        "reason": REASON_NOT_FOUND,
        "resource_version": None,
    }


def validate_namespace_scope(resource, namespace: str | None) -> None:
    """Check the supplied namespace against the *discovered* scope, before any request.

    The SDK routes by what discovery says, not by what the caller supplied: a namespace
    handed to a cluster-scoped kind is silently dropped and the cluster-scoped object is
    deleted anyway, and a namespaced kind with no namespace is read through a
    cluster-style route whose 404 would be reported as an absent object. Both are
    mis-routed mutations, so both are refused before the first object request.

    The messages are fixed text: like everything else at this boundary they interpolate
    nothing, not even the namespace the caller passed.
    """
    supplied = isinstance(namespace, str) and bool(namespace.strip())
    if resource.namespaced and not supplied:
        raise GuardedDeleteError(
            REASON_UNVERIFIABLE,
            "namespace is required for the namespaced kind: without it the read is "
            "routed cluster-style and its 404 would not mean the object is absent.",
            STAGE_READ,
        )
    if not resource.namespaced and supplied:
        raise GuardedDeleteError(
            REASON_UNVERIFIABLE,
            "namespace must not be set for the cluster-scoped kind: it is ignored during "
            "routing, so the delete would land on the cluster-scoped object instead.",
            STAGE_READ,
        )


def read_object(resource, name: str, namespace: str | None, stage: str = STAGE_READ):
    """A live GET. Returns the object, or None **only** on a real 404.

    Every other failure raises: an unverifiable read must never be mistaken for an
    absent object.
    """
    try:
        return resource.get(name=name, namespace=namespace)
    except Exception as exc:  # noqa: BLE001 -- classified immediately, never propagated raw
        if safe_api_reason(exc) == REASON_NOT_FOUND:
            return None
        raise GuardedDeleteError(
            REASON_UNVERIFIABLE,
            f"Cannot verify {name}: the read did not complete, so its absence is unproven.",
            stage,
        ) from None


def delete_object(resource, name: str, namespace: str | None, expected_uid: str) -> bool:
    """The preconditioned DELETE. The API server evaluates the UID atomically.

    Returns True when the server accepted the delete for the proved UID, and False when
    the server answered 404. A 404 is *not* a fatal outcome and not a proof of absence
    either: it says only that the name resolved to nothing at that instant, which the
    caller resolves with a final live read.
    """
    body = {
        "apiVersion": "v1",
        "kind": "DeleteOptions",
        "preconditions": {"uid": expected_uid},
    }
    try:
        resource.delete(name=name, namespace=namespace, body=body)
        return True
    except Exception as exc:  # noqa: BLE001 -- classified immediately, never propagated raw
        reason = safe_api_reason(exc)
        if reason == REASON_PRECONDITION_FAILED:
            raise GuardedDeleteError(
                REASON_UID_MISMATCH,
                f"Refusing to delete {name}: the live object is no longer the one that was "
                "proved, and it was left intact. Re-prove identity from a fresh read; a retry "
                "without the precondition would delete whatever is there now.",
                STAGE_DELETING,
            ) from None
        if reason == REASON_NOT_FOUND:
            return False
        raise GuardedDeleteError(
            REASON_UNVERIFIABLE,
            f"The delete of {name} did not complete verifiably.",
            STAGE_DELETING,
        ) from None


def prove_absence_after_missing_delete(resource, name: str, namespace: str | None, expected_uid: str) -> dict:
    """Resolve a DELETE 404 with one final live read (July step 5).

    The 404 alone is a statement about one instant, not a proof: a genuine
    disappearance and a name already recreated under a new identity produce exactly the
    same answer. Confirmed absence is an idempotent unchanged success; anything else
    fails, and nothing here reports ``changed`` -- this invocation deleted nothing.
    """
    current = read_object(resource, name, namespace, stage=STAGE_COMPLETED)
    if current is None:
        return _absent_result()
    if _field(current, "uid") != expected_uid:
        raise GuardedDeleteError(
            REASON_UID_MISMATCH,
            f"{name} exists under a different identity although the delete reported it "
            "absent; the replacement was left intact.",
            STAGE_COMPLETED,
        )
    raise GuardedDeleteError(
        REASON_UNVERIFIABLE,
        f"The delete reported {name} absent and the proof read found it present; its state is unverifiable.",
        STAGE_COMPLETED,
    )


def await_absence(
    resource,
    name: str,
    namespace: str | None,
    expected_uid: str,
    wait_timeout: int | float,
    wait_sleep: int | float,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Poll until the proved object is gone, on a bounded monotonic budget.

    Monotonic deliberately: a wall clock can be stepped by NTP, and this loop bounds
    a destructive operation.

    A same-name object bearing a **different** UID appearing mid-poll is fatal, not
    success -- something recreated the name, and calling that completion would report
    a teardown that did not happen.
    """
    deadline = monotonic() + wait_timeout
    while True:
        current = read_object(resource, name, namespace, stage=STAGE_POLLING)
        if current is None:
            return
        if _field(current, "uid") != expected_uid:
            raise GuardedDeleteError(
                REASON_UID_MISMATCH,
                f"{name} exists again under a different identity while awaiting its absence; "
                "the replacement was left intact.",
                STAGE_POLLING,
            )
        now = monotonic()
        if now >= deadline:
            raise GuardedDeleteError(
                REASON_TIMEOUT,
                f"{name} was still present when the absence budget expired.",
                STAGE_POLLING,
            )
        # Never sleep past the deadline: an interval longer than the remaining budget
        # would run this destructive operation arbitrarily far beyond the bound the
        # operator configured. One clock reading serves both the check and the cap.
        sleep(min(wait_sleep, deadline - now))


def run_guarded_delete(
    resource,
    *,
    name: str,
    namespace: str | None,
    expected_uid: str,
    check_mode: bool,
    wait_timeout: int | float,
    wait_sleep: int | float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """The whole state machine: one stage per step, no stage re-deriving another's work.

    A successful result reports ``changed`` only after this invocation's DELETE was
    accepted for the intended UID, the bounded absence poll completed, and an
    independent final live proof succeeded. If a later proof fails after the API
    accepted the DELETE, the raised error carries ``changed=True`` so the caller can
    report the mutation that already happened.

    There are two unchanged successes, and both rest on a proved 404: an object already
    absent at the guarded read, and one the API server reports absent when the DELETE
    lands, whose absence is then confirmed by a final live read.
    """
    if not isinstance(expected_uid, str) or not expected_uid.strip():
        raise GuardedDeleteError(
            REASON_UID_MISMATCH,
            "expected_uid is required: an unconditional delete is not an acceptable fallback.",
            STAGE_READ,
        )
    expected_uid = expected_uid.strip()

    existing = read_object(resource, name, namespace)
    if existing is None:
        return _absent_result()

    if _field(existing, "uid") != expected_uid:
        raise GuardedDeleteError(
            REASON_UID_MISMATCH,
            f"{name} exists but is not the object that was proved; it was left intact.",
            STAGE_UID_MISMATCH,
        )

    resource_version = _field(existing, "resourceVersion")

    if check_mode:
        # Stop after the read and the UID validation: state what a real run would do,
        # issue no DELETE, and prove no absence.
        return {
            "changed": False,
            "would_change": True,
            "stage": STAGE_WOULD_DELETE,
            "reason": REASON_OK,
            "resource_version": resource_version,
        }

    if not delete_object(resource, name, namespace, expected_uid):
        # The object was gone before this invocation's delete landed. Nothing was
        # changed here, and the final proof runs all the same.
        return prove_absence_after_missing_delete(resource, name, namespace, expected_uid)

    try:
        await_absence(
            resource,
            name,
            namespace,
            expected_uid,
            wait_timeout,
            wait_sleep,
            monotonic=monotonic,
            sleep=sleep,
        )

        # One independent live proof. The poll already observed absence, but that is the
        # poll's own evidence; the deletion boundary requires a separate confirmation
        # before any completion is recorded.
        if read_object(resource, name, namespace, stage=STAGE_COMPLETED) is not None:
            raise GuardedDeleteError(
                REASON_UNVERIFIABLE,
                f"{name} reappeared after its absence was observed; completion is unproven.",
                STAGE_COMPLETED,
            )
    except GuardedDeleteError as exc:
        # The API server already accepted this invocation's UID-preconditioned DELETE.
        # A later proof failure still fails the module, but it cannot erase that change.
        exc.changed = True
        raise

    return {
        "changed": True,
        "would_change": False,
        "stage": STAGE_COMPLETED,
        "reason": REASON_OK,
        "resource_version": resource_version,
    }
