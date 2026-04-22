"""Parity tests for collection RBAC expansion against Python RBAC definitions."""

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_rbac_validate import expand_rbac_requirements
from lib.rbac_validator import RBACValidator


def _expand(entries, namespace=None):
    flattened = []
    for api_group, resource, verbs in entries:
        for verb in verbs:
            flattened.append((api_group, resource, verb, namespace))
    return flattened


def _python_hub_permissions(role, *, include_decommission, skip_observability, argocd_mode, argocd_install_type):
    cluster = (
        RBACValidator.OPERATOR_CLUSTER_PERMISSIONS
        if role == "operator"
        else RBACValidator.VALIDATOR_CLUSTER_PERMISSIONS
    )
    hub_namespaces = (
        RBACValidator.OPERATOR_HUB_NAMESPACE_PERMISSIONS
        if role == "operator"
        else RBACValidator.VALIDATOR_HUB_NAMESPACE_PERMISSIONS
    )

    permissions = []
    for api_group, resource, verbs in cluster:
        if skip_observability and api_group == "observability.open-cluster-management.io":
            continue
        permissions.extend(_expand([(api_group, resource, verbs)]))

    for namespace, entries in hub_namespaces.items():
        if skip_observability and namespace == "open-cluster-management-observability":
            continue
        permissions.extend(_expand(entries, namespace=namespace))

    validator = RBACValidator.__new__(RBACValidator)
    validator.role = role
    permissions.extend(
        _expand(
            RBACValidator._get_argocd_cluster_permissions(  # type: ignore[misc]
                validator,
                argocd_mode=argocd_mode,
                argocd_install_type=argocd_install_type,
            )
        )
    )

    if include_decommission:
        if role != "operator":
            raise ValueError("include_decommission=True is not valid for the validator role.")
        permissions.extend(_expand(RBACValidator.DECOMMISSION_PERMISSIONS))

    return sorted(permissions)


def _python_decommission_permissions(*, skip_observability):
    permissions = []
    for api_group, resource, verbs in RBACValidator.DECOMMISSION_CLUSTER_PERMISSIONS:
        if skip_observability and api_group == "observability.open-cluster-management.io":
            continue
        permissions.extend(_expand([(api_group, resource, verbs)]))

    for namespace, entries in RBACValidator.DECOMMISSION_NAMESPACE_PERMISSIONS.items():
        if skip_observability and namespace == "open-cluster-management-observability":
            continue
        permissions.extend(_expand(entries, namespace=namespace))

    return sorted(permissions)


@pytest.mark.parametrize(
    ("role", "include_decommission", "skip_observability", "argocd_mode", "argocd_install_type"),
    [
        ("operator", False, False, "none", "unknown"),
        ("operator", False, False, "check", "operator"),
        ("operator", False, False, "check", "none"),
        ("operator", False, True, "manage", "vanilla"),
        ("operator", True, False, "none", "unknown"),
        ("validator", False, False, "check", "operator"),
        ("validator", False, True, "check", "none"),
    ],
)
def test_collection_hub_rbac_expansion_matches_python(
    role, include_decommission, skip_observability, argocd_mode, argocd_install_type
):
    collection_permissions = sorted(
        expand_rbac_requirements(
            role=role,
            include_decommission=include_decommission,
            skip_observability=skip_observability,
            argocd_mode=argocd_mode,
            argocd_install_type=argocd_install_type,
        )
    )
    python_permissions = _python_hub_permissions(
        role,
        include_decommission=include_decommission,
        skip_observability=skip_observability,
        argocd_mode=argocd_mode,
        argocd_install_type=argocd_install_type,
    )

    assert collection_permissions == python_permissions


def test_collection_rejects_validator_decommission_like_python():
    with pytest.raises(ValueError, match="include_decommission"):
        expand_rbac_requirements(
            role="validator",
            include_decommission=True,
            skip_observability=False,
            argocd_mode="none",
            argocd_install_type="unknown",
        )


def test_collection_decommission_only_expansion_matches_python():
    collection_permissions = sorted(
        expand_rbac_requirements(
            role="operator",
            include_decommission=True,
            skip_observability=True,
            argocd_mode="none",
            argocd_install_type="unknown",
            decommission_only=True,
        )
    )

    assert collection_permissions == _python_decommission_permissions(skip_observability=True)
