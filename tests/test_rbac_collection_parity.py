"""Parity tests for collection RBAC expansion against Python RBAC definitions."""

import importlib
import sys
import types

import pytest

from lib.rbac_validator import RBACValidator


def _load_expand_rbac_requirements():
    """Import the collection RBAC helper without requiring ansible-core in root test jobs."""
    module_name = "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_rbac_validate"
    try:
        return importlib.import_module(module_name).expand_rbac_requirements
    except ModuleNotFoundError as exc:
        if exc.name not in {"ansible", "ansible.module_utils", "ansible.module_utils.basic"}:
            raise

    ansible_module = types.ModuleType("ansible")
    module_utils = types.ModuleType("ansible.module_utils")
    basic = types.ModuleType("ansible.module_utils.basic")

    class _AnsibleModule:  # pragma: no cover - the stub only exists in CI environments without ansible-core.
        pass

    basic.AnsibleModule = _AnsibleModule
    ansible_module.module_utils = module_utils
    module_utils.basic = basic
    sys.modules.setdefault("ansible", ansible_module)
    sys.modules.setdefault("ansible.module_utils", module_utils)
    sys.modules.setdefault("ansible.module_utils.basic", basic)
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name).expand_rbac_requirements


expand_rbac_requirements = _load_expand_rbac_requirements()


def _expand(entries, namespace=None):
    flattened = []
    for api_group, resource, verbs in entries:
        for verb in verbs:
            flattened.append((api_group, resource, verb, namespace))
    return flattened


def _python_hub_permissions(
    role,
    *,
    include_decommission,
    include_old_hub_finalization,
    skip_observability,
    argocd_mode,
    argocd_install_type,
):
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
        for api_group, resource, verbs in RBACValidator.DECOMMISSION_PERMISSIONS:
            if skip_observability and api_group == "observability.open-cluster-management.io":
                continue
            permissions.extend(_expand([(api_group, resource, verbs)]))

    if include_old_hub_finalization and not skip_observability:
        if role != "operator":
            raise ValueError("include_old_hub_finalization=True is not valid for the validator role.")
        permissions.extend(_expand(RBACValidator.OLD_HUB_FINALIZATION_PERMISSIONS))

    return sorted(set(permissions))


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


def _python_managed_cluster_permissions(role):
    managed_namespaces = (
        RBACValidator.OPERATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS
        if role == "operator"
        else RBACValidator.VALIDATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS
    )
    permissions = []
    for namespace, entries in managed_namespaces.items():
        permissions.extend(_expand(entries, namespace=namespace))
    return sorted(permissions)


@pytest.mark.parametrize(
    (
        "role",
        "include_decommission",
        "include_old_hub_finalization",
        "skip_observability",
        "argocd_mode",
        "argocd_install_type",
    ),
    [
        ("operator", False, False, False, "none", "unknown"),
        ("operator", False, False, False, "check", "operator"),
        ("operator", False, False, False, "check", "none"),
        ("operator", False, False, True, "manage", "vanilla"),
        ("operator", True, False, False, "none", "unknown"),
        ("operator", True, False, True, "none", "unknown"),
        ("operator", False, True, False, "none", "unknown"),
        ("operator", False, True, True, "none", "unknown"),
        ("validator", False, False, False, "check", "operator"),
        ("validator", False, False, True, "check", "none"),
    ],
)
def test_collection_hub_rbac_expansion_matches_python(
    role,
    include_decommission,
    include_old_hub_finalization,
    skip_observability,
    argocd_mode,
    argocd_install_type,
):
    collection_permissions = sorted(
        expand_rbac_requirements(
            role=role,
            include_decommission=include_decommission,
            include_old_hub_finalization=include_old_hub_finalization,
            skip_observability=skip_observability,
            argocd_mode=argocd_mode,
            argocd_install_type=argocd_install_type,
        )
    )
    python_permissions = _python_hub_permissions(
        role,
        include_decommission=include_decommission,
        include_old_hub_finalization=include_old_hub_finalization,
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


def test_collection_rejects_validator_old_hub_finalization_like_python():
    with pytest.raises(ValueError, match="include_old_hub_finalization"):
        expand_rbac_requirements(
            role="validator",
            include_decommission=False,
            include_old_hub_finalization=True,
            skip_observability=False,
            argocd_mode="none",
            argocd_install_type="unknown",
        )


@pytest.mark.parametrize("argocd_install_type", ["none", "vanilla", "operator", "unknown"])
def test_collection_rejects_validator_argocd_manage_like_python(argocd_install_type):
    validator = RBACValidator.__new__(RBACValidator)
    validator.role = "validator"

    with pytest.raises(ValueError, match="validator.*manage"):
        RBACValidator._get_argocd_cluster_permissions(  # type: ignore[misc]
            validator,
            argocd_mode="manage",
            argocd_install_type=argocd_install_type,
        )

    with pytest.raises(ValueError, match="validator.*manage"):
        expand_rbac_requirements(
            role="validator",
            include_decommission=False,
            skip_observability=False,
            argocd_mode="manage",
            argocd_install_type=argocd_install_type,
        )


# Operator-installed Argo CD ("operator") and undetermined installs ("unknown") both
# require the argocds discovery permission; vanilla installs omit it because the CRD
# is absent. Expressed as normalized (api_group, resource, verb, namespace) tuples.
_ARGOCDS_DISCOVERY_PERMISSIONS = {
    ("argoproj.io", "argocds", "get", None),
    ("argoproj.io", "argocds", "list", None),
}


@pytest.mark.parametrize("role", ["operator", "validator"])
@pytest.mark.parametrize(
    ("argocd_install_type", "expect_argocds"),
    [
        ("unknown", True),
        ("vanilla", False),
    ],
)
def test_collection_argocd_check_install_type_permissions_match_python(role, argocd_install_type, expect_argocds):
    """Argo CD check mode derives identical argocds discovery permissions on both sides.

    For ``argocd_install_type="unknown"`` both the Python validator and the collection
    expansion must include ``argoproj.io/argocds`` (get, list); for ``"vanilla"`` both
    must omit it. This closes the ``check/unknown`` parity gap: without it the collection
    condition ``!= "vanilla"`` could be mutated to ``== "operator"`` and silently drop the
    argocds discovery permission for ``unknown`` installs while ``operator`` stayed intact.
    """
    python_permissions = set(
        _python_hub_permissions(
            role,
            include_decommission=False,
            include_old_hub_finalization=False,
            skip_observability=False,
            argocd_mode="check",
            argocd_install_type=argocd_install_type,
        )
    )
    collection_permissions = set(
        expand_rbac_requirements(
            role=role,
            include_decommission=False,
            include_old_hub_finalization=False,
            skip_observability=False,
            argocd_mode="check",
            argocd_install_type=argocd_install_type,
        )
    )

    assert collection_permissions == python_permissions, (
        f"Python/Collection permission drift for role={role}, argocd_mode=check, "
        f"install_type={argocd_install_type}: "
        f"only-in-Python={sorted(python_permissions - collection_permissions)}, "
        f"only-in-Collection={sorted(collection_permissions - python_permissions)}"
    )

    if expect_argocds:
        assert _ARGOCDS_DISCOVERY_PERMISSIONS <= python_permissions, (
            f"Python missing argocds discovery permissions for install_type={argocd_install_type}: "
            f"{sorted(_ARGOCDS_DISCOVERY_PERMISSIONS - python_permissions)}"
        )
        assert _ARGOCDS_DISCOVERY_PERMISSIONS <= collection_permissions, (
            f"Collection missing argocds discovery permissions for install_type={argocd_install_type}: "
            f"{sorted(_ARGOCDS_DISCOVERY_PERMISSIONS - collection_permissions)}"
        )
    else:
        assert not (_ARGOCDS_DISCOVERY_PERMISSIONS & python_permissions), (
            f"Python unexpectedly grants argocds permissions for install_type={argocd_install_type}: "
            f"{sorted(_ARGOCDS_DISCOVERY_PERMISSIONS & python_permissions)}"
        )
        assert not (_ARGOCDS_DISCOVERY_PERMISSIONS & collection_permissions), (
            f"Collection unexpectedly grants argocds permissions for install_type={argocd_install_type}: "
            f"{sorted(_ARGOCDS_DISCOVERY_PERMISSIONS & collection_permissions)}"
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


@pytest.mark.parametrize("role", ["operator", "validator"])
def test_collection_managed_cluster_rbac_expansion_matches_python(role):
    collection_permissions = sorted(
        expand_rbac_requirements(
            role=role,
            include_decommission=False,
            skip_observability=False,
            argocd_mode="none",
            argocd_install_type="unknown",
            scope="managed_cluster",
        )
    )

    assert collection_permissions == _python_managed_cluster_permissions(role)
