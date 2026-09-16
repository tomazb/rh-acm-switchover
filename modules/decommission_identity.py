"""MCH operator identity capture through OLM CSV provenance (R4-03 PR E, task E2).

This module owns exactly one thing: producing the read-side operator-identity
value described by plan §10.2.2 (`operator_deployment`) and §10.2.3
(`operator_identity_unavailable`), shaped for `lib.teardown_record.TeardownRecord`.
It performs no persistence, no mutation, and no orchestration -- those stay in
`modules/decommission.py`. In particular this module does not own teardown
phases, RunRecord/StateManager access, checkpoints, DELETE, waits, namespace
reads, Pod-list orchestration, dry-run policy, or completion transitions.

The identity is discovered by finding the one `Succeeded` ClusterServiceVersion
that owns the MultiClusterHub CRD (`lib.teardown_record.MCH_OWNED_CRD`), then by
resolving its install-strategy Deployment. A Pod classifier lands separately
(task E3) in this same module; nothing here is pre-built for it.
"""

from dataclasses import dataclass
from typing import Any, Dict, NoReturn, Optional

from lib.constants import (
    ACM_NAMESPACE,
    CSV_API_GROUP,
    CSV_API_VERSION,
    CSV_INSTALL_STRATEGY_DEPLOYMENT,
    CSV_PHASE_SUCCEEDED,
    CSV_PLURAL,
    OPERATOR_IDENTITY_DISCOVERY_METHOD,
    OPERATOR_IDENTITY_UNAVAILABLE_REASONS,
)
from lib.exceptions import SwitchoverError, ValidationError
from lib.strict_read import StrictReadStatus
from lib.teardown_record import MCH_OWNED_CRD
from lib.validation import InputValidator

# Section 10.2.3 evidence_summary text: one static, sanitized sentence per closed
# reason. Never built from API-supplied strings or server-side counts.
EVIDENCE_SUMMARY_BY_REASON: Dict[str, str] = {
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
assert set(EVIDENCE_SUMMARY_BY_REASON) == set(
    OPERATOR_IDENTITY_UNAVAILABLE_REASONS
), "EVIDENCE_SUMMARY_BY_REASON must cover exactly OPERATOR_IDENTITY_UNAVAILABLE_REASONS"

# Sentinel distinguishing ">1 install-strategy Deployment" from "no usable name".
_AMBIGUOUS_DEPLOYMENT = object()


@dataclass(frozen=True)
class OperatorIdentity:
    """Exactly one of the two §10.2.2/§10.2.3 record values, shaped for TeardownRecord."""

    operator_deployment: Optional[Dict[str, Any]] = None
    operator_identity_unavailable: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if (self.operator_deployment is None) == (self.operator_identity_unavailable is None):
            raise ValueError(
                "OperatorIdentity must carry exactly one of operator_deployment / operator_identity_unavailable"
            )

    @property
    def available(self) -> bool:
        return self.operator_deployment is not None


def capture_operator_identity(
    client: Any,
    *,
    mch_teardown_key: str,
    mch_expected_uid: str,
    captured_at: str,
) -> OperatorIdentity:
    """Capture the live MCH operator identity, or a sanitized unavailable reason.

    `client` is a KubeClient-like object exposing `list_custom_resources_strict`,
    `get_custom_resource_strict`, and `get_deployment_strict`. The three keyword
    values are caller-supplied and copied verbatim into the returned value; they
    are never generated here.

    Raises `lib.exceptions.SwitchoverError` for fatal outcomes (an unreadable or
    malformed CSV inventory), with a sanitized, stable stage message. Raises
    `ValueError` if any of the three keyword values is not a non-empty string.
    """
    _require_non_empty_str("mch_teardown_key", mch_teardown_key)
    _require_non_empty_str("mch_expected_uid", mch_expected_uid)
    _require_non_empty_str("captured_at", captured_at)

    csv_list = client.list_custom_resources_strict(
        group=CSV_API_GROUP, version=CSV_API_VERSION, plural=CSV_PLURAL, namespace=ACM_NAMESPACE
    )
    if csv_list.status is StrictReadStatus.CRD_ABSENT:
        return _unavailable("csv_absent", mch_teardown_key, mch_expected_uid, captured_at)
    if csv_list.status is not StrictReadStatus.ITEMS:
        _raise_fatal("ClusterServiceVersion inventory unreadable")

    if not csv_list.items:
        return _unavailable("csv_absent", mch_teardown_key, mch_expected_uid, captured_at)

    owning = [item for item in csv_list.items if _owns_mch_crd(item)]
    if not owning:
        return _unavailable("csv_owned_crd_mismatch", mch_teardown_key, mch_expected_uid, captured_at)

    candidates = [item for item in owning if _phase(item) == CSV_PHASE_SUCCEEDED]
    if not candidates:
        return _unavailable("csv_not_succeeded", mch_teardown_key, mch_expected_uid, captured_at)
    if len(candidates) > 1:
        return _unavailable("csv_ambiguous", mch_teardown_key, mch_expected_uid, captured_at)

    candidate = candidates[0]
    candidate_name = _metadata(candidate).get("name")
    if not isinstance(candidate_name, str) or not _is_valid_name(candidate_name):
        _raise_fatal("ClusterServiceVersion candidate name is invalid")
    candidate_uid = _metadata(candidate).get("uid")
    if not isinstance(candidate_uid, str) or not candidate_uid:
        _raise_fatal("ClusterServiceVersion candidate is missing identity")

    csv_get = client.get_custom_resource_strict(
        group=CSV_API_GROUP,
        version=CSV_API_VERSION,
        plural=CSV_PLURAL,
        name=candidate_name,
        namespace=ACM_NAMESPACE,
    )
    if csv_get.status is StrictReadStatus.ERROR:
        _raise_fatal("ClusterServiceVersion re-read failed")
    if csv_get.status in (StrictReadStatus.OBJECT_ABSENT, StrictReadStatus.CRD_ABSENT):
        return _unavailable("csv_absent", mch_teardown_key, mch_expected_uid, captured_at)
    if csv_get.status is not StrictReadStatus.ITEMS:
        _raise_fatal("ClusterServiceVersion re-read failed")

    body = csv_get.resource
    body_uid = _metadata(body).get("uid")
    if not isinstance(body_uid, str) or not body_uid:
        _raise_fatal("ClusterServiceVersion re-read is missing identity")
    if body_uid != candidate_uid:
        return _unavailable("csv_ambiguous", mch_teardown_key, mch_expected_uid, captured_at)
    if not _owns_mch_crd(body):
        return _unavailable("csv_owned_crd_mismatch", mch_teardown_key, mch_expected_uid, captured_at)
    if _phase(body) != CSV_PHASE_SUCCEEDED:
        return _unavailable("csv_not_succeeded", mch_teardown_key, mch_expected_uid, captured_at)

    deployment_name = _install_deployment_name(body)
    if deployment_name is None:
        return _unavailable("install_deployment_absent", mch_teardown_key, mch_expected_uid, captured_at)
    if deployment_name is _AMBIGUOUS_DEPLOYMENT:
        return _unavailable("install_deployment_ambiguous", mch_teardown_key, mch_expected_uid, captured_at)

    deployment_get = client.get_deployment_strict(name=deployment_name, namespace=ACM_NAMESPACE)
    if deployment_get.status is StrictReadStatus.OBJECT_ABSENT:
        return _unavailable("install_deployment_absent", mch_teardown_key, mch_expected_uid, captured_at)
    if deployment_get.status is not StrictReadStatus.ITEMS:
        return _unavailable("deployment_read_failed", mch_teardown_key, mch_expected_uid, captured_at)

    deployment_uid = _metadata(deployment_get.resource).get("uid")
    if not isinstance(deployment_uid, str) or not deployment_uid:
        return _unavailable("deployment_identity_incomplete", mch_teardown_key, mch_expected_uid, captured_at)

    operator_deployment = {
        "namespace": ACM_NAMESPACE,
        "name": deployment_name,
        "uid": deployment_uid,
        "discovery_method": OPERATOR_IDENTITY_DISCOVERY_METHOD,
        "captured_at": captured_at,
        "csv": {
            "namespace": ACM_NAMESPACE,
            "name": candidate_name,
            "uid": body_uid,
            "owned_crd": MCH_OWNED_CRD,
        },
        "mch_teardown_key": mch_teardown_key,
        "mch_expected_uid": mch_expected_uid,
    }
    return OperatorIdentity(operator_deployment=operator_deployment)


def _unavailable(reason: str, mch_teardown_key: str, mch_expected_uid: str, captured_at: str) -> OperatorIdentity:
    return OperatorIdentity(
        operator_identity_unavailable={
            "reason": reason,
            "discovery_method": OPERATOR_IDENTITY_DISCOVERY_METHOD,
            "captured_at": captured_at,
            "evidence_summary": EVIDENCE_SUMMARY_BY_REASON[reason],
            "mch_teardown_key": mch_teardown_key,
            "mch_expected_uid": mch_expected_uid,
        }
    )


def _raise_fatal(stage_message: str) -> NoReturn:
    # A stable, sanitized stage message -- never the StrictReadOutcome.reason code
    # or any API-supplied string.
    raise SwitchoverError(f"Cannot capture the MultiClusterHub operator identity: {stage_message}")


def _require_non_empty_str(label: str, value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")


def _metadata(item: Any) -> Dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item, dict) else None
    return metadata if isinstance(metadata, dict) else {}


def _owns_mch_crd(item: Dict[str, Any]) -> bool:
    """Tolerate non-list/non-mapping shapes as "not owning" (read flow step 3)."""
    spec = item.get("spec")
    if not isinstance(spec, dict):
        return False
    crds = spec.get("customresourcedefinitions")
    if not isinstance(crds, dict):
        return False
    owned = crds.get("owned")
    if not isinstance(owned, list):
        return False
    return any(isinstance(entry, dict) and entry.get("name") == MCH_OWNED_CRD for entry in owned)


def _phase(item: Dict[str, Any]) -> Any:
    status = item.get("status")
    return status.get("phase") if isinstance(status, dict) else None


def _is_valid_name(name: str) -> bool:
    try:
        InputValidator.validate_kubernetes_name(name)
    except ValidationError:
        return False
    return True


def _install_deployment_name(body: Dict[str, Any]) -> Any:
    """Read flow step 7. Returns a name, None (absent), or _AMBIGUOUS_DEPLOYMENT."""
    spec = body.get("spec")
    if not isinstance(spec, dict):
        return None
    install = spec.get("install")
    if not isinstance(install, dict):
        return None
    if install.get("strategy") != CSV_INSTALL_STRATEGY_DEPLOYMENT:
        return None
    install_spec = install.get("spec")
    if not isinstance(install_spec, dict):
        return None
    deployments = install_spec.get("deployments")
    if not isinstance(deployments, list) or not deployments:
        return None
    if len(deployments) > 1:
        return _AMBIGUOUS_DEPLOYMENT
    entry = deployments[0]
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    if not isinstance(name, str) or not _is_valid_name(name):
        return None
    return name
