"""Live RBAC bootstrap certification for release validation.

This module validates applied RBAC permissions end-to-end using SubjectAccessReview
against a live or disposable cluster. It confirms that bootstrapped service accounts
have the required permissions for switchover, restore-only, decommission, and
old-hub finalization flows.

The certification scenario is opt-in via ACM_ENABLE_LIVE_RBAC_CERTIFICATION=1 and
requires explicit cluster context configuration in the release profile.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal

from lib.rbac_validator import RBACValidator
from tests.release.contracts.models import HubProfile
from tests.release.reporting.redaction import RedactionError, sanitize_text

VALID_ROLES = ("operator", "validator")
RBAC_SERVICE_ACCOUNT_FORMAT = "system:serviceaccount:{namespace}:{name}"
RBAC_DEFAULT_NAMESPACE = "acm-switchover"
RBAC_DEFAULT_SERVICE_ACCOUNT = "acm-switchover-operator"

FORBIDDEN_PERMISSIONS = (
    ("rbac.authorization.k8s.io", "clusterrolebindings", "create", None),
    ("rbac.authorization.k8s.io", "clusterrolebindings", "update", None),
    ("rbac.authorization.k8s.io", "clusterroles", "update", None),
    ("", "secrets", "list", "kube-system"),
    ("", "pods/exec", "create", "open-cluster-management"),
)


@dataclass(frozen=True)
class PermissionCheck:
    """A single RBAC permission to verify via SubjectAccessReview."""

    api_group: str
    resource: str
    verb: str
    namespace: str | None = None

    def as_sar_spec(self) -> dict:
        """Build SubjectAccessReview spec for this permission."""
        spec = {
            "resourceAttributes": {
                "verb": self.verb,
                "resource": self.resource,
            }
        }
        if self.api_group:
            spec["resourceAttributes"]["group"] = self.api_group
        if self.namespace:
            spec["resourceAttributes"]["namespace"] = self.namespace
        return spec


@dataclass(frozen=True)
class CertificationAssertion:
    """A single certification assertion result."""

    capability: str
    name: str
    status: Literal["passed", "failed"]
    expected: str
    actual: str
    evidence_path: str
    message: str


@dataclass(frozen=True)
class CertificationResult:
    """Result of RBAC live certification."""

    status: Literal["passed", "failed", "skipped"]
    assertions: list[CertificationAssertion]
    reason: str | None = None


@dataclass(frozen=True)
class SARCheckResult:
    """Result of a single SubjectAccessReview API call."""

    allowed: bool
    evidence_path: str
    error: str | None = None


def _get_required_permissions(
    *,
    role: str,
    include_decommission: bool,
    include_old_hub_finalization: bool,
) -> list[PermissionCheck]:
    """Build the permission matrix for certification based on role and flags.

    The Python RBAC validator is the source of truth; collection parity tests keep
    the Ansible expansion aligned with the same matrix.
    """
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role}")
    if role != "operator" and include_decommission:
        raise ValueError("include_decommission is only valid for the operator role")
    if role != "operator" and include_old_hub_finalization:
        raise ValueError("include_old_hub_finalization is only valid for the operator role")

    cluster_perms = (
        RBACValidator.OPERATOR_CLUSTER_PERMISSIONS
        if role == "operator"
        else RBACValidator.VALIDATOR_CLUSTER_PERMISSIONS
    )
    namespace_perms = (
        RBACValidator.OPERATOR_HUB_NAMESPACE_PERMISSIONS
        if role == "operator"
        else RBACValidator.VALIDATOR_HUB_NAMESPACE_PERMISSIONS
    )

    permissions: list[PermissionCheck] = []
    permissions.extend(_expand_permission_entries(cluster_perms))
    for namespace, entries in namespace_perms.items():
        permissions.extend(_expand_permission_entries(entries, namespace=namespace))
    if include_decommission:
        permissions.extend(_expand_permission_entries(RBACValidator.DECOMMISSION_PERMISSIONS))
    if include_old_hub_finalization:
        permissions.extend(_expand_permission_entries(RBACValidator.OLD_HUB_FINALIZATION_PERMISSIONS))
    return list(dict.fromkeys(permissions))


def _expand_permission_entries(
    entries: list[tuple[str, str, list[str]]],
    namespace: str | None = None,
) -> list[PermissionCheck]:
    return [
        PermissionCheck(
            api_group=api_group,
            resource=resource,
            verb=verb,
            namespace=namespace,
        )
        for api_group, resource, verbs in entries
        for verb in verbs
    ]


def _get_forbidden_permissions() -> list[PermissionCheck]:
    """Return dangerous permissions that release service accounts must not hold."""
    return [
        PermissionCheck(
            api_group=api_group,
            resource=resource,
            verb=verb,
            namespace=namespace,
        )
        for api_group, resource, verb, namespace in FORBIDDEN_PERMISSIONS
    ]


def _permission_name(permission: PermissionCheck) -> str:
    scope = f"namespace={permission.namespace}" if permission.namespace else "cluster"
    return f"{permission.api_group or 'core'}/{permission.resource}:{permission.verb}@{scope}"


def _safe_filename_component(value: str | None) -> str:
    normalized = value or "cluster"
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", normalized).strip("-") or "core"


def _evidence_stem(permission: PermissionCheck) -> str:
    digest = sha256(
        json.dumps(
            {
                "api_group": permission.api_group,
                "resource": permission.resource,
                "verb": permission.verb,
                "namespace": permission.namespace,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]
    parts = [
        "sar",
        _safe_filename_component(permission.namespace),
        _safe_filename_component(permission.api_group or "core"),
        _safe_filename_component(permission.resource),
        _safe_filename_component(permission.verb),
        digest,
    ]
    return "-".join(parts)


def _sanitize_artifact_text(text: str) -> str:
    try:
        return sanitize_text(text).text
    except RedactionError as exc:
        return f"[REDACTED: {exc.rejected_class}]"


def _write_sar_evidence(
    *,
    evidence_file: Path,
    request: dict,
    returncode: int | None,
    stdout: str,
    stderr: str,
    exception: str | None = None,
) -> None:
    response = None
    if stdout:
        try:
            response = json.loads(stdout)
        except json.JSONDecodeError:
            response = None
    payload = {
        "schema_version": 1,
        "request": request,
        "result": {
            "returncode": returncode,
            "response": response,
            "stdout": _sanitize_artifact_text(stdout),
            "stderr": _sanitize_artifact_text(stderr),
            "exception": exception,
        },
    }
    evidence_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _check_permission_via_sar(
    *,
    kubeconfig: str,
    context: str,
    permission: PermissionCheck,
    service_account: str,
    artifact_dir: Path,
) -> SARCheckResult:
    """Check a single permission via SubjectAccessReview with impersonation.

    Returns a structured result with allow/deny state, evidence path, and any
    operational error encountered before SAR authorization could be evaluated.
    """
    sar_manifest = {
        "apiVersion": "authorization.k8s.io/v1",
        "kind": "SubjectAccessReview",
        "spec": {
            **permission.as_sar_spec(),
            "user": service_account,
        },
    }

    artifact_dir.mkdir(parents=True, exist_ok=True)
    stem = _evidence_stem(permission)
    sar_file = artifact_dir / f"{stem}-request.json"
    evidence_file = artifact_dir / f"{stem}-evidence.json"
    sar_file.write_text(json.dumps(sar_manifest, indent=2), encoding="utf-8")

    command = [
        "oc",
        "--kubeconfig",
        kubeconfig,
        "--context",
        context,
        "create",
        "-f",
        str(sar_file),
        "-o",
        "json",
    ]

    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        error = f"{type(exc).__name__}: {exc}"
        _write_sar_evidence(
            evidence_file=evidence_file,
            request=sar_manifest,
            returncode=None,
            stdout="",
            stderr="",
            exception=error,
        )
        return SARCheckResult(False, str(evidence_file), error)

    if completed.returncode != 0:
        error = f"oc create SubjectAccessReview failed with return code {completed.returncode}"
        _write_sar_evidence(
            evidence_file=evidence_file,
            request=sar_manifest,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
        return SARCheckResult(False, str(evidence_file), error)

    try:
        result = json.loads(completed.stdout or "{}")
        allowed = result.get("status", {}).get("allowed", False)
        _write_sar_evidence(
            evidence_file=evidence_file,
            request=sar_manifest,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
        return SARCheckResult(bool(allowed), str(evidence_file))
    except json.JSONDecodeError:
        error = "SubjectAccessReview response was not valid JSON"
        _write_sar_evidence(
            evidence_file=evidence_file,
            request=sar_manifest,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
        return SARCheckResult(False, str(evidence_file), error)


def _certification_enabled() -> bool:
    """Check if live RBAC certification is enabled via environment variable."""
    import os

    return os.environ.get("ACM_ENABLE_LIVE_RBAC_CERTIFICATION", "").lower() in {
        "1",
        "true",
        "yes",
    }


def certify_rbac_permissions(
    *,
    hub: HubProfile,
    hub_name: str,
    artifact_dir: Path,
    role: str = "operator",
    namespace: str = RBAC_DEFAULT_NAMESPACE,
    service_account: str = RBAC_DEFAULT_SERVICE_ACCOUNT,
    include_decommission: bool = False,
    include_old_hub_finalization: bool = False,
    include_forbidden_permissions: bool = True,
) -> CertificationResult:
    """Certify RBAC permissions on a live cluster using SubjectAccessReview.

    Args:
        hub: Hub profile with kubeconfig and context
        hub_name: Human-readable hub identifier (e.g., "primary")
        artifact_dir: Directory to write certification artifacts
        role: Role to certify (operator or validator)
        namespace: Namespace where service account exists
        service_account: Service account name to impersonate
        include_decommission: Include decommission delete permissions
        include_old_hub_finalization: Include old-hub MCO delete permission

    Returns:
        CertificationResult with status and detailed assertions
    """
    if not _certification_enabled():
        return CertificationResult(
            status="skipped",
            assertions=[],
            reason="ACM_ENABLE_LIVE_RBAC_CERTIFICATION is not set",
        )

    if role not in VALID_ROLES:
        return CertificationResult(
            status="failed",
            assertions=[
                CertificationAssertion(
                    capability="rbac-certification",
                    name="role-validation",
                    status="failed",
                    expected=f"one of {VALID_ROLES}",
                    actual=role,
                    evidence_path="",
                    message=f"Invalid role: {role}",
                )
            ],
            reason=f"Invalid role: {role}",
        )

    artifact_dir.mkdir(parents=True, exist_ok=True)

    try:
        permissions = _get_required_permissions(
            role=role,
            include_decommission=include_decommission,
            include_old_hub_finalization=include_old_hub_finalization,
        )
    except ValueError as exc:
        return CertificationResult(
            status="failed",
            assertions=[
                CertificationAssertion(
                    capability="rbac-certification",
                    name="scope-validation",
                    status="failed",
                    expected="valid RBAC certification scope",
                    actual=str(exc),
                    evidence_path="",
                    message=str(exc),
                )
            ],
            reason=str(exc),
        )

    sa_full_name = RBAC_SERVICE_ACCOUNT_FORMAT.format(
        namespace=namespace, name=service_account
    )

    assertions: list[CertificationAssertion] = []
    denied_count = 0
    forbidden_allowed_count = 0
    error_count = 0

    for permission in permissions:
        sar_result = _check_permission_via_sar(
            kubeconfig=hub.kubeconfig,
            context=hub.context,
            permission=permission,
            service_account=sa_full_name,
            artifact_dir=artifact_dir,
        )

        perm_name = _permission_name(permission)

        if sar_result.error:
            error_count += 1
            assertions.append(
                CertificationAssertion(
                    capability="rbac-certification",
                    name=perm_name,
                    status="failed",
                    expected="allowed",
                    actual="error",
                    evidence_path=sar_result.evidence_path,
                    message=f"SAR check failed for {sa_full_name}: {sar_result.error}",
                )
            )
        elif not sar_result.allowed:
            denied_count += 1
            assertions.append(
                CertificationAssertion(
                    capability="rbac-certification",
                    name=perm_name,
                    status="failed",
                    expected="allowed",
                    actual="denied",
                    evidence_path=sar_result.evidence_path,
                    message=f"Permission denied for {sa_full_name}",
                )
            )
        else:
            assertions.append(
                CertificationAssertion(
                    capability="rbac-certification",
                    name=perm_name,
                    status="passed",
                    expected="allowed",
                    actual="allowed",
                    evidence_path=sar_result.evidence_path,
                    message=f"Permission allowed for {sa_full_name}",
                )
            )

    if include_forbidden_permissions:
        for permission in _get_forbidden_permissions():
            sar_result = _check_permission_via_sar(
                kubeconfig=hub.kubeconfig,
                context=hub.context,
                permission=permission,
                service_account=sa_full_name,
                artifact_dir=artifact_dir,
            )

            perm_name = _permission_name(permission)
            if sar_result.error:
                error_count += 1
                assertions.append(
                    CertificationAssertion(
                        capability="rbac-certification",
                        name=perm_name,
                        status="failed",
                        expected="denied",
                        actual="error",
                        evidence_path=sar_result.evidence_path,
                        message=f"SAR check failed for {sa_full_name}: {sar_result.error}",
                    )
                )
            elif sar_result.allowed:
                forbidden_allowed_count += 1
                assertions.append(
                    CertificationAssertion(
                        capability="rbac-certification",
                        name=perm_name,
                        status="failed",
                        expected="denied",
                        actual="allowed",
                        evidence_path=sar_result.evidence_path,
                        message=f"Forbidden permission allowed for {sa_full_name}",
                    )
                )
            else:
                assertions.append(
                    CertificationAssertion(
                        capability="rbac-certification",
                        name=perm_name,
                        status="passed",
                        expected="denied",
                        actual="denied",
                        evidence_path=sar_result.evidence_path,
                        message=f"Forbidden permission denied for {sa_full_name}",
                    )
                )

    failed_count = denied_count + forbidden_allowed_count + error_count
    status = "passed" if failed_count == 0 else "failed"
    reason = None if failed_count == 0 else f"{failed_count} RBAC assertions failed"

    # Write summary artifact
    summary = {
        "schema_version": 1,
        "status": status,
        "hub": hub_name,
        "role": role,
        "service_account": sa_full_name,
        "include_decommission": include_decommission,
        "include_old_hub_finalization": include_old_hub_finalization,
        "include_forbidden_permissions": include_forbidden_permissions,
        "total_permissions": len(permissions),
        "denied_count": denied_count,
        "forbidden_allowed_count": forbidden_allowed_count,
        "error_count": error_count,
        "failed_count": failed_count,
        "assertions": [
            {
                "capability": a.capability,
                "name": a.name,
                "status": a.status,
                "expected": a.expected,
                "actual": a.actual,
                "evidence_path": a.evidence_path,
                "message": a.message,
            }
            for a in assertions
        ],
    }
    (artifact_dir / f"rbac-certification-{hub_name}.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    return CertificationResult(status=status, assertions=assertions, reason=reason)
