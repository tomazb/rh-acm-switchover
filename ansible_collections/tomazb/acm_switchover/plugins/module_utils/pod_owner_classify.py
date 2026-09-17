# SPDX-License-Identifier: MIT
"""MCH operator identity capture and Pod owner-chain classification (R4-03 PR E, task E5).

The collection's independent implementation of the identity semantics the Python CLI owns in
``modules/decommission_identity.py``. Nothing is imported from the Python CLI; the root parity
test ``tests/test_mch_identity_parity.py`` drives both implementations over the shared vectors.

This module owns decisions only. Every Kubernetes read arrives through an injected ``read``
callable with the signature ``read(read_mode, api_version, kind, resource_name, namespace=None,
name=None)`` returning the collection strict-read triple ``(read_status, resources,
resource_version)`` -- in the shipped module, ``module_utils/k8s_read.strict_read``. It performs
no persistence, no mutation, no waits and no retries: teardown phases, checkpoints, DELETE and
completion policy belong to the decommission role (task E6).

Objects are the camelCase mappings the dynamic client returns (``ownerReferences``,
``apiVersion``).
"""

from __future__ import annotations

import re
from typing import Any, Callable

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    CSV_API_GROUP,
    CSV_API_VERSION,
    CSV_INSTALL_STRATEGY_DEPLOYMENT,
    CSV_KIND,
    CSV_PHASE_SUCCEEDED,
    CSV_PLURAL,
    MCH_OWNED_CRD,
    NAMESPACE_API_VERSION,
    NAMESPACE_KIND,
    OPERATOR_IDENTITY_DISCOVERY_METHOD,
    POD_CLASSIFICATION_DRAIN_BLOCKING,
    POD_CLASSIFICATION_IDENTITY_INCONSISTENT,
    POD_CLASSIFICATION_IDENTITY_UNAVAILABLE,
    POD_CLASSIFICATION_OPERATOR_OWNED,
)

Reader = Callable[..., "tuple[str, list[dict], str | None]"]

# DNS-1123 subdomain rule, mirrored from lib/validation.py and held equal by the root parity test.
K8S_NAME_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$")
K8S_NAME_MAX_LENGTH = 253

# Section 10.2.3 evidence_summary text, one static sanitized sentence per closed reason. Mirrored
# from modules/decommission_identity.py and held equal by the root parity test.
EVIDENCE_SUMMARY_BY_REASON = {
    "csv_absent": "No ClusterServiceVersion owning the MultiClusterHub CRD was found.",
    "csv_ambiguous": "More than one ClusterServiceVersion candidate identifies the MultiClusterHub operator.",
    "csv_not_succeeded": "The MultiClusterHub operator ClusterServiceVersion has not reached the Succeeded phase.",
    "csv_owned_crd_mismatch": "The candidate ClusterServiceVersion does not own the MultiClusterHub CRD.",
    "install_deployment_absent": (
        "The ClusterServiceVersion install strategy does not name a usable operator Deployment."
    ),
    "install_deployment_ambiguous": "The ClusterServiceVersion install strategy names more than one Deployment.",
    "deployment_read_failed": "The MultiClusterHub operator Deployment could not be read.",
    "deployment_identity_incomplete": "The MultiClusterHub operator Deployment did not carry a usable identity.",
}

READ_STATUS_OK = "ok"
READ_STATUS_NAMESPACE_ABSENT = "namespace_absent"
READ_STATUS_ERROR = "error"

DEPLOYMENT_STATUS_MATCHED = "matched"
DEPLOYMENT_STATUS_INCONSISTENT = "inconsistent"
DEPLOYMENT_STATUS_NOT_APPLICABLE = "not_applicable"

_CSV_API_VERSION = f"{CSV_API_GROUP}/{CSV_API_VERSION}"
_AMBIGUOUS_DEPLOYMENT = object()


class IdentityCaptureError(Exception):
    """A fatal, unverifiable identity read. The message is a fixed, sanitized stage."""


def is_valid_name(name: Any) -> bool:
    return isinstance(name, str) and 0 < len(name) <= K8S_NAME_MAX_LENGTH and bool(K8S_NAME_PATTERN.match(name))


def _metadata(item: Any) -> dict:
    metadata = item.get("metadata") if isinstance(item, dict) else None
    return metadata if isinstance(metadata, dict) else {}


def _fatal(stage: str) -> IdentityCaptureError:
    return IdentityCaptureError(f"Cannot capture the MultiClusterHub operator identity: {stage}")


def _unavailable(reason: str, mch_teardown_key: str, mch_expected_uid: str, captured_at: str) -> dict:
    return {
        "operator_deployment": None,
        "operator_identity_unavailable": {
            "reason": reason,
            "discovery_method": OPERATOR_IDENTITY_DISCOVERY_METHOD,
            "captured_at": captured_at,
            "evidence_summary": EVIDENCE_SUMMARY_BY_REASON[reason],
            "mch_teardown_key": mch_teardown_key,
            "mch_expected_uid": mch_expected_uid,
        },
    }


def _owns_mch_crd(item: Any) -> bool:
    spec = item.get("spec") if isinstance(item, dict) else None
    crds = spec.get("customresourcedefinitions") if isinstance(spec, dict) else None
    owned = crds.get("owned") if isinstance(crds, dict) else None
    if not isinstance(owned, list):
        return False
    return any(isinstance(entry, dict) and entry.get("name") == MCH_OWNED_CRD for entry in owned)


def _phase(item: Any) -> Any:
    status = item.get("status") if isinstance(item, dict) else None
    return status.get("phase") if isinstance(status, dict) else None


def _install_deployment_name(body: Any) -> Any:
    """The one install-strategy Deployment name, None when absent/unusable, or the ambiguity sentinel."""
    spec = body.get("spec") if isinstance(body, dict) else None
    install = spec.get("install") if isinstance(spec, dict) else None
    if not isinstance(install, dict) or install.get("strategy") != CSV_INSTALL_STRATEGY_DEPLOYMENT:
        return None
    install_spec = install.get("spec")
    deployments = install_spec.get("deployments") if isinstance(install_spec, dict) else None
    if not isinstance(deployments, list) or not deployments:
        return None
    if len(deployments) > 1:
        return _AMBIGUOUS_DEPLOYMENT
    name = deployments[0].get("name") if isinstance(deployments[0], dict) else None
    return name if is_valid_name(name) else None


def capture_identity(
    read: Reader,
    *,
    namespace: str,
    mch_teardown_key: str,
    mch_expected_uid: str,
    captured_at: str,
) -> dict:
    """Capture the live operator identity, or a determinate unavailable reason.

    Returns ``{"operator_deployment": ..., "operator_identity_unavailable": ...}`` with exactly
    one value set, in the exact plan §10.2.2 / §10.2.3 shapes. Raises ``IdentityCaptureError``
    when a CSV read is unverifiable; an error is never reported as an absent CSV.
    """

    def unavailable(reason: str) -> dict:
        return _unavailable(reason, mch_teardown_key, mch_expected_uid, captured_at)

    status, items, _revision = read("list", _CSV_API_VERSION, CSV_KIND, CSV_PLURAL, namespace)
    if status == "kind_not_served":
        return unavailable("csv_absent")
    if status != READ_STATUS_OK:
        raise _fatal("ClusterServiceVersion inventory unreadable")
    if not items:
        return unavailable("csv_absent")

    owning = [item for item in items if _owns_mch_crd(item)]
    if not owning:
        return unavailable("csv_owned_crd_mismatch")
    candidates = [item for item in owning if _phase(item) == CSV_PHASE_SUCCEEDED]
    if not candidates:
        return unavailable("csv_not_succeeded")
    if len(candidates) > 1:
        return unavailable("csv_ambiguous")

    candidate_name = _metadata(candidates[0]).get("name")
    if not is_valid_name(candidate_name):
        raise _fatal("ClusterServiceVersion candidate name is invalid")
    candidate_uid = _metadata(candidates[0]).get("uid")
    if not isinstance(candidate_uid, str) or not candidate_uid:
        raise _fatal("ClusterServiceVersion candidate is missing identity")

    status, items, _revision = read("get", _CSV_API_VERSION, CSV_KIND, CSV_PLURAL, namespace, candidate_name)
    if status in ("not_found", "kind_not_served"):
        return unavailable("csv_absent")
    if status != READ_STATUS_OK or len(items) != 1:
        raise _fatal("ClusterServiceVersion re-read failed")
    body = items[0]
    body_uid = _metadata(body).get("uid")
    if not isinstance(body_uid, str) or not body_uid:
        raise _fatal("ClusterServiceVersion re-read is missing identity")
    if body_uid != candidate_uid:
        return unavailable("csv_ambiguous")
    if not _owns_mch_crd(body):
        return unavailable("csv_owned_crd_mismatch")
    if _phase(body) != CSV_PHASE_SUCCEEDED:
        return unavailable("csv_not_succeeded")

    deployment_name = _install_deployment_name(body)
    if deployment_name is None:
        return unavailable("install_deployment_absent")
    if deployment_name is _AMBIGUOUS_DEPLOYMENT:
        return unavailable("install_deployment_ambiguous")

    status, items, _revision = read("get", "apps/v1", "Deployment", "deployments", namespace, deployment_name)
    if status == "not_found":
        return unavailable("install_deployment_absent")
    if status != READ_STATUS_OK or len(items) != 1:
        # `kind_not_served` included: apps/v1 Deployment not being served is no absence proof.
        return unavailable("deployment_read_failed")
    deployment_uid = _metadata(items[0]).get("uid")
    if not isinstance(deployment_uid, str) or not deployment_uid:
        return unavailable("deployment_identity_incomplete")

    return {
        "operator_deployment": {
            "namespace": namespace,
            "name": deployment_name,
            "uid": deployment_uid,
            "discovery_method": OPERATOR_IDENTITY_DISCOVERY_METHOD,
            "captured_at": captured_at,
            "csv": {"namespace": namespace, "name": candidate_name, "uid": body_uid, "owned_crd": MCH_OWNED_CRD},
            "mch_teardown_key": mch_teardown_key,
            "mch_expected_uid": mch_expected_uid,
        },
        "operator_identity_unavailable": None,
    }


def _pod_name(pod: Any) -> str:
    name = _metadata(pod).get("name")
    return name if isinstance(name, str) else ""


def _sole_controller_reference(owner_references: Any) -> dict | None:
    """The exactly-one ``controller is True`` owner reference, or None (absent, invalid, ambiguous)."""
    if not isinstance(owner_references, list) or not all(isinstance(ref, dict) for ref in owner_references):
        return None
    controllers = [ref for ref in owner_references if ref.get("controller") is True]
    return controllers[0] if len(controllers) == 1 else None


def _classify_pod(read: Reader, pod: Any, recorded: dict, memo: dict) -> str:
    """Owned only when Pod -> controller ReplicaSet -> controller Deployment is the recorded one."""
    metadata = _metadata(pod)
    if not metadata or metadata.get("namespace") != recorded["namespace"]:
        return POD_CLASSIFICATION_DRAIN_BLOCKING

    reference = _sole_controller_reference(metadata.get("ownerReferences"))
    if reference is None or reference.get("apiVersion") != "apps/v1" or reference.get("kind") != "ReplicaSet":
        return POD_CLASSIFICATION_DRAIN_BLOCKING
    replicaset_name = reference.get("name")
    replicaset_uid = reference.get("uid")
    if not is_valid_name(replicaset_name) or not isinstance(replicaset_uid, str) or not replicaset_uid:
        return POD_CLASSIFICATION_DRAIN_BLOCKING

    # One read per exact ReplicaSet identity within this pass only; the same name with another
    # UID is a different object and is read on its own.
    key = (metadata["namespace"], "ReplicaSet", replicaset_name, replicaset_uid)
    if key not in memo:
        memo[key] = read("get", "apps/v1", "ReplicaSet", "replicasets", metadata["namespace"], replicaset_name)
    status, items, _revision = memo[key]
    if status != READ_STATUS_OK or len(items) != 1:
        return POD_CLASSIFICATION_DRAIN_BLOCKING
    replicaset_metadata = _metadata(items[0])
    if replicaset_metadata.get("uid") != replicaset_uid:
        return POD_CLASSIFICATION_DRAIN_BLOCKING

    owner = _sole_controller_reference(replicaset_metadata.get("ownerReferences"))
    if owner is None or owner.get("apiVersion") != "apps/v1" or owner.get("kind") != "Deployment":
        return POD_CLASSIFICATION_DRAIN_BLOCKING
    if owner.get("name") != recorded["name"] or owner.get("uid") != recorded["uid"]:
        return POD_CLASSIFICATION_DRAIN_BLOCKING
    return POD_CLASSIFICATION_OPERATOR_OWNED


def classify_pods(read: Reader, pods: list, identity: dict, *, namespace: str) -> dict:
    """Classify an already-listed Pod inventory against the recorded identity, as one pass.

    ``identity`` carries exactly one of ``operator_deployment`` and
    ``operator_identity_unavailable``. Under an unavailable identity nothing is read and every
    Pod blocks. Under a captured identity the recorded Deployment is re-read first, even for
    zero Pods; unless it is live in ``namespace`` with the recorded UID, the identity is
    inconsistent, no ReplicaSet is read, and every Pod blocks.

    Returns ``decisions`` (one ``name``/``decision`` per Pod, in order), ``identity_status``
    (None, or the unavailable/inconsistent code) and ``deployment_resource_version`` (the
    re-read's revision when it matched, else None).
    """
    recorded = identity.get("operator_deployment")
    blocking_all = [{"name": _pod_name(pod), "decision": POD_CLASSIFICATION_DRAIN_BLOCKING} for pod in pods]
    if recorded is None:
        return {
            "decisions": blocking_all,
            "identity_status": POD_CLASSIFICATION_IDENTITY_UNAVAILABLE,
            "deployment_resource_version": None,
        }

    verified_revision = None
    if is_valid_name(recorded["name"]) and recorded["namespace"] == namespace:
        status, items, revision = read("get", "apps/v1", "Deployment", "deployments", namespace, recorded["name"])
        live_uid = _metadata(items[0]).get("uid") if status == READ_STATUS_OK and len(items) == 1 else None
        if isinstance(live_uid, str) and live_uid == recorded["uid"]:
            verified_revision = revision
    if verified_revision is None:
        return {
            "decisions": blocking_all,
            "identity_status": POD_CLASSIFICATION_IDENTITY_INCONSISTENT,
            "deployment_resource_version": None,
        }

    memo: dict = {}
    return {
        "decisions": [{"name": _pod_name(pod), "decision": _classify_pod(read, pod, recorded, memo)} for pod in pods],
        "identity_status": None,
        "deployment_resource_version": verified_revision,
    }


def classify_error_result() -> dict:
    """The classify-pass result for a pass that proved nothing."""
    return {
        "read_status": READ_STATUS_ERROR,
        "namespace_resource_version": None,
        "pods_resource_version": None,
        "deployment_resource_version": None,
        "identity_status": None,
        "deployment_status": DEPLOYMENT_STATUS_NOT_APPLICABLE,
        "decisions": [],
        "blocking_count": None,
    }


def classify_pass(read: Reader, identity: dict, *, namespace: str) -> dict:
    """One complete classification pass: Namespace GET, strict all-Pod LIST, classification.

    ``read_status`` is ``namespace_absent`` only on a named Namespace GET 404, with no Pod or
    Deployment read after it. A Pod LIST that is not a complete inventory -- a 404 included --
    is ``error``, never an empty one. ``blocking_count`` is None unless the pass is ``ok``.
    """
    result = classify_error_result()
    status, _items, namespace_revision = read(
        "get", NAMESPACE_API_VERSION, NAMESPACE_KIND, "namespaces", None, namespace
    )
    if status == "not_found":
        result["read_status"] = READ_STATUS_NAMESPACE_ABSENT
        return result
    if status != READ_STATUS_OK:
        return result

    status, pods, pods_revision = read("list", "v1", "Pod", "pods", namespace)
    if status != READ_STATUS_OK:
        return result

    classified = classify_pods(read, pods, identity, namespace=namespace)
    if classified["identity_status"] == POD_CLASSIFICATION_IDENTITY_UNAVAILABLE:
        deployment_status = DEPLOYMENT_STATUS_NOT_APPLICABLE
    elif classified["identity_status"] == POD_CLASSIFICATION_IDENTITY_INCONSISTENT:
        deployment_status = DEPLOYMENT_STATUS_INCONSISTENT
    else:
        deployment_status = DEPLOYMENT_STATUS_MATCHED
    result.update(classified)
    result.update(
        {
            "read_status": READ_STATUS_OK,
            "namespace_resource_version": namespace_revision,
            "pods_resource_version": pods_revision,
            "deployment_status": deployment_status,
            "blocking_count": sum(
                1 for entry in classified["decisions"] if entry["decision"] == POD_CLASSIFICATION_DRAIN_BLOCKING
            ),
        }
    )
    return result
