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
    if parsed <= 0:
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
    return DynamicClient(api_client)


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


def delete_object(resource, name: str, namespace: str | None, expected_uid: str) -> None:
    """The preconditioned DELETE. The API server evaluates the UID atomically."""
    body = {
        "apiVersion": "v1",
        "kind": "DeleteOptions",
        "preconditions": {"uid": expected_uid},
    }
    try:
        resource.delete(name=name, namespace=namespace, body=body)
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
            raise GuardedDeleteError(
                REASON_NOT_FOUND,
                f"{name} was already absent when the delete was issued, so this run changed nothing.",
                STAGE_DELETING,
            ) from None
        raise GuardedDeleteError(
            REASON_UNVERIFIABLE,
            f"The delete of {name} did not complete verifiably.",
            STAGE_DELETING,
        ) from None


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
        if monotonic() >= deadline:
            raise GuardedDeleteError(
                REASON_TIMEOUT,
                f"{name} was still present when the absence budget expired.",
                STAGE_POLLING,
            )
        sleep(wait_sleep)


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
        return {
            "changed": False,
            "would_change": False,
            "stage": STAGE_ABSENT,
            "reason": REASON_NOT_FOUND,
            "resource_version": None,
        }

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

    delete_object(resource, name, namespace, expected_uid)
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
