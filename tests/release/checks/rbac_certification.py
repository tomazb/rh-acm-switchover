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
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

from tests.release.contracts.models import HubProfile

# Valid roles for RBAC certification
VALID_ROLES = ("operator", "validator")

# Service account format for impersonation
RBAC_SERVICE_ACCOUNT_FORMAT = "system:serviceaccount:{namespace}:{name}"


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


def _get_required_permissions(
    *,
    role: str,
    include_decommission: bool,
    include_old_hub_finalization: bool,
) -> list[PermissionCheck]:
    """Build the permission matrix for certification based on role and flags.

    This mirrors the permission matrix from lib/rbac_validator.py and
    ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_validate.py.
    """
    permissions = []

    # Cluster-scoped permissions
    cluster_perms = [
        ("", "namespaces", "get"),
        ("", "namespaces", "list"),
        ("", "nodes", "get"),
        ("", "nodes", "list"),
        ("config.openshift.io", "clusteroperators", "get"),
        ("config.openshift.io", "clusteroperators", "list"),
        ("config.openshift.io", "clusterversions", "get"),
        ("config.openshift.io", "clusterversions", "list"),
        ("cluster.open-cluster-management.io", "managedclusters", "get"),
        ("cluster.open-cluster-management.io", "managedclusters", "list"),
        ("hive.openshift.io", "clusterdeployments", "get"),
        ("hive.openshift.io", "clusterdeployments", "list"),
        ("operator.open-cluster-management.io", "multiclusterhubs", "get"),
        ("operator.open-cluster-management.io", "multiclusterhubs", "list"),
        (
            "observability.open-cluster-management.io",
            "multiclusterobservabilities",
            "get",
        ),
        (
            "observability.open-cluster-management.io",
            "multiclusterobservabilities",
            "list",
        ),
    ]

    # Add write permissions for operator role
    if role == "operator":
        cluster_perms.extend(
            [
                (
                    "cluster.open-cluster-management.io",
                    "managedclusters",
                    "patch",
                ),
            ]
        )

    # Add decommission permissions (delete verbs)
    if include_decommission or include_old_hub_finalization:
        if role == "operator":
            cluster_perms.extend(
                [
                    (
                        "observability.open-cluster-management.io",
                        "multiclusterobservabilities",
                        "delete",
                    ),
                ]
            )
        if include_decommission:
            # Full decommission requires additional delete permissions
            cluster_perms.extend(
                [
                    (
                        "cluster.open-cluster-management.io",
                        "managedclusters",
                        "delete",
                    ),
                    (
                        "operator.open-cluster-management.io",
                        "multiclusterhubs",
                        "delete",
                    ),
                ]
            )

    for api_group, resource, verb in cluster_perms:
        permissions.append(
            PermissionCheck(api_group=api_group, resource=resource, verb=verb)
        )

    # Namespace-scoped permissions
    namespace_perms = {
        "open-cluster-management-backup": [
            ("", "configmaps", ["get", "list"]),
            ("", "secrets", ["get"]),
            ("", "pods", ["get", "list"]),
            (
                "cluster.open-cluster-management.io",
                "backupschedules",
                ["get", "list"],
            ),
            ("cluster.open-cluster-management.io", "restores", ["get", "list"]),
            ("velero.io", "backups", ["get", "list"]),
            (
                "velero.io",
                "backupstoragelocations",
                ["get", "list"],
            ),
        ],
        "open-cluster-management": [
            ("", "pods", ["get", "list"]),
        ],
        "open-cluster-management-observability": [
            ("apps", "statefulsets", ["get", "list"]),
            ("apps", "deployments", ["get", "list"]),
            ("", "pods", ["get", "list"]),
            ("route.openshift.io", "routes", ["get", "list"]),
        ],
        "multicluster-engine": [
            ("", "configmaps", ["get", "list"]),
        ],
    }

    # Add write verbs for operator role
    if role == "operator":
        namespace_perms["open-cluster-management-backup"].extend(
            [
                ("", "configmaps", ["create", "patch", "delete"]),
                (
                    "cluster.open-cluster-management.io",
                    "backupschedules",
                    ["patch"],
                ),
                (
                    "cluster.open-cluster-management.io",
                    "restores",
                    ["create", "patch"],
                ),
            ]
        )
        namespace_perms["open-cluster-management-observability"].extend(
            [
                ("apps", "statefulsets", ["patch"]),
            ]
        )

    for namespace, perms in namespace_perms.items():
        for api_group, resource, verbs in perms:
            for verb in verbs:
                permissions.append(
                    PermissionCheck(
                        api_group=api_group,
                        resource=resource,
                        verb=verb,
                        namespace=namespace,
                    )
                )

    return permissions


def _check_permission_via_sar(
    *,
    kubeconfig: str,
    context: str,
    permission: PermissionCheck,
    service_account: str,
    artifact_dir: Path,
) -> tuple[bool, str]:
    """Check a single permission via SubjectAccessReview with impersonation.

    Returns (allowed, evidence_path).
    """
    sar_manifest = {
        "apiVersion": "authorization.k8s.io/v1",
        "kind": "SubjectAccessReview",
        "spec": {
            **permission.as_sar_spec(),
            "user": service_account,
        },
    }

    sar_file = artifact_dir / f"sar-{permission.resource}-{permission.verb}.json"
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
        return False, str(sar_file)

    if completed.returncode != 0:
        return False, str(sar_file)

    try:
        result = json.loads(completed.stdout or "{}")
        allowed = result.get("status", {}).get("allowed", False)
        return bool(allowed), str(sar_file)
    except json.JSONDecodeError:
        return False, str(sar_file)


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
    namespace: str = "acm-switchover",
    service_account: str = "acm-switchover-operator",
    include_decommission: bool = True,
    include_old_hub_finalization: bool = True,
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

    permissions = _get_required_permissions(
        role=role,
        include_decommission=include_decommission,
        include_old_hub_finalization=include_old_hub_finalization,
    )

    sa_full_name = RBAC_SERVICE_ACCOUNT_FORMAT.format(
        namespace=namespace, name=service_account
    )

    assertions: list[CertificationAssertion] = []
    denied_count = 0

    for permission in permissions:
        allowed, evidence = _check_permission_via_sar(
            kubeconfig=hub.kubeconfig,
            context=hub.context,
            permission=permission,
            service_account=sa_full_name,
            artifact_dir=artifact_dir,
        )

        scope = f"namespace={permission.namespace}" if permission.namespace else "cluster"
        perm_name = f"{permission.api_group or 'core'}/{permission.resource}:{permission.verb}@{scope}"

        if not allowed:
            denied_count += 1
            assertions.append(
                CertificationAssertion(
                    capability="rbac-certification",
                    name=perm_name,
                    status="failed",
                    expected="allowed",
                    actual="denied",
                    evidence_path=evidence,
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
                    evidence_path=evidence,
                    message=f"Permission allowed for {sa_full_name}",
                )
            )

    status = "passed" if denied_count == 0 else "failed"
    reason = None if denied_count == 0 else f"{denied_count} permissions denied"

    # Write summary artifact
    summary = {
        "schema_version": 1,
        "status": status,
        "hub": hub_name,
        "role": role,
        "service_account": sa_full_name,
        "include_decommission": include_decommission,
        "include_old_hub_finalization": include_old_hub_finalization,
        "total_permissions": len(permissions),
        "denied_count": denied_count,
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
