"""Property-based tests for RBAC permission-set safety and parity."""

from __future__ import annotations

import importlib
import sys
import types
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from typing import Any

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from lib.constants import (
    BACKUP_NAMESPACE,
    MANAGED_CLUSTER_AGENT_NAMESPACE,
    MCE_NAMESPACE,
    OBSERVABILITY_NAMESPACE,
)
from lib.rbac_validator import (
    MUTATING_VERBS,
    VALIDATOR_CLUSTER_VERB_EXCEPTIONS,
    RBACValidator,
    _derive_read_only_permissions,
    _format_verb_removals,
)
from tests.properties.strategies import (
    RBAC_MUTATING_VERBS,
    RBAC_PERMISSION_VERBS,
    InvalidRbacSelectorCase,
    PermissionTableCase,
    RbacSelectorCase,
    argocd_install_types,
    collection_only_invalid_rbac_selector_cases,
    drifted_expected_removals,
    invalid_argocd_modes,
    invalid_rbac_selector_cases,
    permission_table_cases,
    rbac_roles,
    rbac_selector_cases,
    valid_argocd_role_mode_pairs,
)

pytestmark = [pytest.mark.unit, pytest.mark.property]

Permission = tuple[str, str, str, str | None]
PermissionRow = tuple[str, str, list[str]]
COLLECTION_MODULE_NAME = "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_rbac_validate"
ARGOCD_RESOURCES = {"applications", "argocds", "customresourcedefinitions"}
ARGOCD_BASE_READS = {
    ("argoproj.io", "applications", "get", None),
    ("argoproj.io", "applications", "list", None),
    ("apiextensions.k8s.io", "customresourcedefinitions", "get", None),
}
ARGOCDS_DISCOVERY_READS = {
    ("argoproj.io", "argocds", "get", None),
    ("argoproj.io", "argocds", "list", None),
}

# This exact baseline is intentionally test-owned rather than derived from either
# production permission catalog.  It independently pins the documented validator
# read contract, including namespace scope, for every semantic selector domain.
VALIDATOR_HUB_REQUIRED_READS: frozenset[Permission] = frozenset(
    {
        ("", "namespaces", "get", None),
        ("", "namespaces", "list", None),
        ("", "nodes", "get", None),
        ("", "nodes", "list", None),
        ("config.openshift.io", "clusteroperators", "get", None),
        ("config.openshift.io", "clusteroperators", "list", None),
        ("config.openshift.io", "clusterversions", "get", None),
        ("config.openshift.io", "clusterversions", "list", None),
        ("cluster.open-cluster-management.io", "managedclusters", "get", None),
        ("cluster.open-cluster-management.io", "managedclusters", "list", None),
        ("hive.openshift.io", "clusterdeployments", "get", None),
        ("hive.openshift.io", "clusterdeployments", "list", None),
        ("operator.open-cluster-management.io", "multiclusterhubs", "get", None),
        ("operator.open-cluster-management.io", "multiclusterhubs", "list", None),
        ("", "configmaps", "get", "open-cluster-management-backup"),
        ("", "configmaps", "list", "open-cluster-management-backup"),
        ("", "secrets", "get", "open-cluster-management-backup"),
        ("", "pods", "get", "open-cluster-management-backup"),
        ("", "pods", "list", "open-cluster-management-backup"),
        (
            "cluster.open-cluster-management.io",
            "backupschedules",
            "get",
            "open-cluster-management-backup",
        ),
        (
            "cluster.open-cluster-management.io",
            "backupschedules",
            "list",
            "open-cluster-management-backup",
        ),
        (
            "cluster.open-cluster-management.io",
            "restores",
            "get",
            "open-cluster-management-backup",
        ),
        (
            "cluster.open-cluster-management.io",
            "restores",
            "list",
            "open-cluster-management-backup",
        ),
        ("velero.io", "backups", "get", "open-cluster-management-backup"),
        ("velero.io", "backups", "list", "open-cluster-management-backup"),
        ("velero.io", "restores", "get", "open-cluster-management-backup"),
        ("velero.io", "restores", "list", "open-cluster-management-backup"),
        ("velero.io", "backupstoragelocations", "get", "open-cluster-management-backup"),
        ("velero.io", "backupstoragelocations", "list", "open-cluster-management-backup"),
        ("oadp.openshift.io", "dataprotectionapplications", "get", "open-cluster-management-backup"),
        ("oadp.openshift.io", "dataprotectionapplications", "list", "open-cluster-management-backup"),
        ("", "pods", "get", "open-cluster-management"),
        ("", "pods", "list", "open-cluster-management"),
        ("", "configmaps", "get", "multicluster-engine"),
        ("", "configmaps", "list", "multicluster-engine"),
    }
)
VALIDATOR_OBSERVABILITY_REQUIRED_READS: frozenset[Permission] = frozenset(
    {
        (
            "observability.open-cluster-management.io",
            "multiclusterobservabilities",
            "get",
            None,
        ),
        (
            "observability.open-cluster-management.io",
            "multiclusterobservabilities",
            "list",
            None,
        ),
        ("", "pods", "get", "open-cluster-management-observability"),
        ("", "pods", "list", "open-cluster-management-observability"),
        ("", "secrets", "get", "open-cluster-management-observability"),
        ("apps", "deployments", "get", "open-cluster-management-observability"),
        ("apps", "deployments", "list", "open-cluster-management-observability"),
        ("apps", "statefulsets", "get", "open-cluster-management-observability"),
        ("apps", "statefulsets", "list", "open-cluster-management-observability"),
        ("route.openshift.io", "routes", "get", "open-cluster-management-observability"),
    }
)
VALIDATOR_MANAGED_CLUSTER_REQUIRED_READS: frozenset[Permission] = frozenset(
    {
        ("", "secrets", "get", "open-cluster-management-agent"),
        ("apps", "deployments", "get", "open-cluster-management-agent"),
    }
)


def _permission_sort_key(permission: Permission) -> tuple[str, str, str, int, str]:
    """Sort cluster and namespace scopes without comparing ``None`` to strings."""
    api_group, resource, verb, namespace = permission
    return api_group, resource, verb, 0 if namespace is None else 1, namespace or ""


def _sorted_permissions(permissions: Iterable[Permission]) -> list[Permission]:
    """Return deterministic diagnostics while preserving original namespace values."""
    return sorted(permissions, key=_permission_sort_key)


def _sorted_permission_counts(counts: Mapping[Permission, int]) -> list[tuple[Permission, int]]:
    """Render permission multiplicity with the shared mixed-scope ordering."""
    return [(permission, counts[permission]) for permission in _sorted_permissions(counts)]


def _load_collection_rbac_module() -> Any:
    """Transactionally import the pure collection expander without ansible-core."""
    parent_name, _, parent_attribute = COLLECTION_MODULE_NAME.rpartition(".")
    managed_names = (
        "ansible",
        "ansible.module_utils",
        "ansible.module_utils.basic",
        COLLECTION_MODULE_NAME,
    )
    missing = object()
    previous_modules = {name: sys.modules.get(name, missing) for name in managed_names}
    parent_module = sys.modules.get(parent_name)
    previous_parent_attribute = (
        getattr(parent_module, parent_attribute, missing) if parent_module is not None else missing
    )

    def restore() -> None:
        current_parent = sys.modules.get(parent_name)
        if current_parent is not None:
            if previous_parent_attribute is missing:
                if hasattr(current_parent, parent_attribute):
                    delattr(current_parent, parent_attribute)
            else:
                setattr(current_parent, parent_attribute, previous_parent_attribute)
        for name, previous in previous_modules.items():
            if previous is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous

    try:
        try:
            return importlib.import_module(COLLECTION_MODULE_NAME)
        except ModuleNotFoundError as exc:
            expected_missing = {"ansible", "ansible.module_utils", "ansible.module_utils.basic"}
            if exc.name not in expected_missing:
                raise

            ansible_module = types.ModuleType("ansible")
            module_utils_module = types.ModuleType("ansible.module_utils")
            basic_module = types.ModuleType("ansible.module_utils.basic")

            class _AnsibleModule:  # pragma: no cover - used only without ansible-core.
                pass

            basic_module.AnsibleModule = _AnsibleModule
            ansible_module.module_utils = module_utils_module
            module_utils_module.basic = basic_module
            sys.modules["ansible"] = ansible_module
            sys.modules["ansible.module_utils"] = module_utils_module
            sys.modules["ansible.module_utils.basic"] = basic_module
            sys.modules.pop(COLLECTION_MODULE_NAME, None)
            if parent_module is not None and hasattr(parent_module, parent_attribute):
                delattr(parent_module, parent_attribute)
            return importlib.import_module(COLLECTION_MODULE_NAME)
    finally:
        restore()


collection_rbac = _load_collection_rbac_module()


def _expand(entries: Iterable[PermissionRow], namespace: str | None = None) -> list[Permission]:
    return [(api_group, resource, verb, namespace) for api_group, resource, verbs in entries for verb in verbs]


def _deduplicate(permissions: Iterable[Permission]) -> list[Permission]:
    return list(dict.fromkeys(permissions))


def _validator_for(role: str) -> RBACValidator:
    validator = RBACValidator.__new__(RBACValidator)
    validator.role = role
    return validator


def _python_non_argocd_permissions(case: RbacSelectorCase) -> list[Permission]:
    """Expand Python base catalogs without consulting the Argo CD helper."""
    validator = _validator_for(case.role)
    permissions: list[Permission] = []
    for api_group, resource, verbs in validator._get_cluster_permissions():
        if case.skip_observability and api_group == "observability.open-cluster-management.io":
            continue
        permissions.extend(_expand([(api_group, resource, verbs)]))
    for namespace, entries in validator._get_hub_namespace_permissions().items():
        if case.skip_observability and namespace == OBSERVABILITY_NAMESPACE:
            continue
        permissions.extend(_expand(entries, namespace))
    return permissions


def _python_permissions_raw(case: RbacSelectorCase | InvalidRbacSelectorCase) -> list[Permission]:
    """Purely expand the Python catalogs while preserving raw duplicate entries."""
    validator = _validator_for(case.role)

    if case.scope == "managed_cluster":
        if case.include_decommission:
            raise ValueError("include_decommission is only valid for hub scope")
        if case.include_old_hub_finalization:
            raise ValueError("include_old_hub_finalization is only valid for hub scope")
        if case.decommission_only:
            raise ValueError("decommission_only is only valid for hub scope")
        if case.argocd_mode != "none":
            raise ValueError("argocd_mode is only valid for hub scope")
        permissions: list[Permission] = []
        for namespace, entries in validator._get_managed_cluster_namespace_permissions().items():
            permissions.extend(_expand(entries, namespace))
        return permissions

    if case.decommission_only:
        if case.role != "operator":
            raise ValueError("decommission_only is only valid for the operator role")
        permissions = []
        for api_group, resource, verbs in RBACValidator.DECOMMISSION_CLUSTER_PERMISSIONS:
            if case.skip_observability and api_group == "observability.open-cluster-management.io":
                continue
            permissions.extend(_expand([(api_group, resource, verbs)]))
        for namespace, entries in RBACValidator.DECOMMISSION_NAMESPACE_PERMISSIONS.items():
            if case.skip_observability and namespace == OBSERVABILITY_NAMESPACE:
                continue
            permissions.extend(_expand(entries, namespace))
        return permissions

    if case.include_decommission and case.role != "operator":
        raise ValueError("include_decommission is only valid for the operator role")
    if case.include_old_hub_finalization and case.role != "operator":
        raise ValueError("include_old_hub_finalization is only valid for the operator role")

    permissions = _python_non_argocd_permissions(case)
    permissions.extend(_expand(validator._get_argocd_cluster_permissions(case.argocd_mode, case.argocd_install_type)))

    if case.include_decommission:
        for api_group, resource, verbs in RBACValidator.DECOMMISSION_PERMISSIONS:
            if case.skip_observability and api_group == "observability.open-cluster-management.io":
                continue
            permissions.extend(_expand([(api_group, resource, verbs)]))
    if case.include_old_hub_finalization and not case.skip_observability:
        permissions.extend(_expand(RBACValidator.OLD_HUB_FINALIZATION_PERMISSIONS))
    return permissions


def _python_permissions(case: RbacSelectorCase | InvalidRbacSelectorCase) -> list[Permission]:
    """Expand and normalize the Python permission catalogs for set comparison."""
    return _deduplicate(_python_permissions_raw(case))


def _collection_permissions(case: RbacSelectorCase | InvalidRbacSelectorCase) -> list[Permission]:
    return collection_rbac.expand_rbac_requirements(
        role=case.role,
        include_decommission=case.include_decommission,
        include_old_hub_finalization=case.include_old_hub_finalization,
        skip_observability=case.skip_observability,
        argocd_mode=case.argocd_mode,
        argocd_install_type=case.argocd_install_type,
        decommission_only=case.decommission_only,
        scope=case.scope,
    )


def _collection_permissions_raw(case: RbacSelectorCase) -> list[Permission]:
    """Run the real Collection expander while preserving pre-normalization multiplicity."""
    original_deduplicator = collection_rbac._deduplicate_permissions

    def preserve_multiplicity(permissions: list[Permission]) -> list[Permission]:
        return list(permissions)

    collection_rbac._deduplicate_permissions = preserve_multiplicity
    try:
        return _collection_permissions(case)
    finally:
        collection_rbac._deduplicate_permissions = original_deduplicator


def _run_python_invalid_cluster_validation(case: InvalidRbacSelectorCase) -> None:
    """Invoke the Python validator's real selector guards without Kubernetes I/O."""
    validator = _validator_for(case.role)
    setattr(validator, "check_permission", lambda *args, **kwargs: (True, ""))
    validator.validate_cluster_permissions(
        include_decommission=case.include_decommission,
        include_old_hub_finalization=case.include_old_hub_finalization,
        skip_observability=case.skip_observability,
        argocd_mode=case.argocd_mode,
        argocd_install_type=case.argocd_install_type,
    )


def _assert_permission_sets_equal(
    case: RbacSelectorCase,
    python_permissions: Sequence[Permission],
    collection_permissions: Sequence[Permission],
) -> None:
    python_set = set(python_permissions)
    collection_set = set(collection_permissions)
    assert collection_set == python_set, (
        f"Unapproved Python/collection RBAC divergence for {case!r}.\n"
        f"  Missing from collection: {_sorted_permissions(python_set - collection_set)!r}\n"
        f"  Unexpected in collection: {_sorted_permissions(collection_set - python_set)!r}"
    )


def _permission_map(entries: Iterable[PermissionRow]) -> dict[tuple[str, str], set[str]]:
    return {(api_group, resource): set(verbs) for api_group, resource, verbs in entries}


def _read_permissions(permissions: Iterable[Permission]) -> set[Permission]:
    return {permission for permission in permissions if permission[2] not in MUTATING_VERBS}


def _expected_validator_reads(case: RbacSelectorCase) -> set[Permission]:
    """Build the exact expected read baseline from independent test-owned sets."""
    if case.scope == "managed_cluster":
        return set(VALIDATOR_MANAGED_CLUSTER_REQUIRED_READS)

    expected = set(VALIDATOR_HUB_REQUIRED_READS)
    if not case.skip_observability:
        expected.update(VALIDATOR_OBSERVABILITY_REQUIRED_READS)
    if case.argocd_mode == "check" and case.argocd_install_type != "none":
        expected.update(ARGOCD_BASE_READS)
        if case.argocd_install_type != "vanilla":
            expected.update(ARGOCDS_DISCOVERY_READS)
    return expected


def _assert_validator_read_contract(
    implementation: str,
    case: RbacSelectorCase,
    permissions: Iterable[Permission],
) -> None:
    """Assert the independent baseline and diagnose missing or mis-scoped reads."""
    expected = _expected_validator_reads(case)
    actual = _read_permissions(permissions)
    missing = expected - actual
    unexpected = actual - expected
    wrong_scope = []
    for required in _sorted_permissions(missing):
        observed = _sorted_permissions(
            permission for permission in actual if permission[:3] == required[:3] and permission[3] != required[3]
        )
        if observed:
            wrong_scope.append((required, observed))

    assert actual == expected, (
        f"{implementation} validator required-read contract mismatch for {case!r}.\n"
        f"  Missing required reads: {_sorted_permissions(missing)!r}\n"
        f"  Unexpected reads: {_sorted_permissions(unexpected)!r}\n"
        f"  Wrong-scope matches: {wrong_scope!r}"
    )


def _expected_raw_duplicate_overlap(case: RbacSelectorCase) -> dict[Permission, int]:
    """Return the one reviewed overlap produced before effective-set normalization."""
    if (
        case.role == "operator"
        and case.scope == "hub"
        and not case.decommission_only
        and case.include_decommission
        and case.include_old_hub_finalization
        and not case.skip_observability
    ):
        return {
            (
                "observability.open-cluster-management.io",
                "multiclusterobservabilities",
                "delete",
                None,
            ): 2
        }
    return {}


def _expected_operator_mutations(case: RbacSelectorCase) -> set[Permission]:
    if case.scope == "managed_cluster":
        return {
            ("", "secrets", "create", MANAGED_CLUSTER_AGENT_NAMESPACE),
            ("", "secrets", "patch", MANAGED_CLUSTER_AGENT_NAMESPACE),
            ("apps", "deployments", "patch", MANAGED_CLUSTER_AGENT_NAMESPACE),
        }

    if case.decommission_only:
        expected = {
            ("cluster.open-cluster-management.io", "managedclusters", "delete", None),
            ("operator.open-cluster-management.io", "multiclusterhubs", "delete", None),
        }
        if not case.skip_observability:
            expected.add(
                (
                    "observability.open-cluster-management.io",
                    "multiclusterobservabilities",
                    "delete",
                    None,
                )
            )
        return expected

    expected = {
        ("cluster.open-cluster-management.io", "managedclusters", "patch", None),
        ("", "configmaps", "create", BACKUP_NAMESPACE),
        ("", "configmaps", "patch", BACKUP_NAMESPACE),
        ("", "configmaps", "delete", BACKUP_NAMESPACE),
        ("cluster.open-cluster-management.io", "backupschedules", "create", BACKUP_NAMESPACE),
        ("cluster.open-cluster-management.io", "backupschedules", "patch", BACKUP_NAMESPACE),
        ("cluster.open-cluster-management.io", "backupschedules", "delete", BACKUP_NAMESPACE),
        ("cluster.open-cluster-management.io", "restores", "create", BACKUP_NAMESPACE),
        ("cluster.open-cluster-management.io", "restores", "patch", BACKUP_NAMESPACE),
        ("cluster.open-cluster-management.io", "restores", "delete", BACKUP_NAMESPACE),
        ("", "configmaps", "create", MCE_NAMESPACE),
        ("", "configmaps", "patch", MCE_NAMESPACE),
        ("", "configmaps", "delete", MCE_NAMESPACE),
    }
    if not case.skip_observability:
        expected.update(
            {
                ("apps", "deployments", "patch", OBSERVABILITY_NAMESPACE),
                ("apps", "statefulsets", "patch", OBSERVABILITY_NAMESPACE),
                ("apps", "statefulsets/scale", "patch", OBSERVABILITY_NAMESPACE),
            }
        )
    if case.include_decommission:
        expected.update(
            {
                ("cluster.open-cluster-management.io", "managedclusters", "delete", None),
                ("operator.open-cluster-management.io", "multiclusterhubs", "delete", None),
            }
        )
    if (case.include_decommission or case.include_old_hub_finalization) and not case.skip_observability:
        expected.add(
            (
                "observability.open-cluster-management.io",
                "multiclusterobservabilities",
                "delete",
                None,
            )
        )
    if case.argocd_mode == "manage" and case.argocd_install_type != "none":
        expected.add(("argoproj.io", "applications", "patch", None))
    return expected


def test_permission_diagnostics_stably_preserve_mixed_cluster_and_namespace_scopes() -> None:
    mixed_scopes: set[Permission] = {
        ("", "configmaps", "get", None),
        ("", "configmaps", "get", "open-cluster-management-backups"),
    }

    ordered = _sorted_permissions(mixed_scopes)
    assert ordered == [
        ("", "configmaps", "get", None),
        ("", "configmaps", "get", "open-cluster-management-backups"),
    ]
    assert ordered[0][3] is None

    case = RbacSelectorCase(
        role="operator",
        scope="hub",
        include_decommission=False,
        include_old_hub_finalization=False,
        skip_observability=False,
        argocd_mode="none",
        argocd_install_type="none",
        decommission_only=False,
    )
    with pytest.raises(AssertionError, match="Missing from collection") as error:
        _assert_permission_sets_equal(case, list(mixed_scopes), [])

    message = str(error.value)
    assert repr(("", "configmaps", "get", None)) in message
    assert repr(("", "configmaps", "get", "open-cluster-management-backups")) in message


@given(permission_table_cases())
def test_read_only_derivation_is_exact_contained_and_non_mutating(case: PermissionTableCase) -> None:
    original = deepcopy(case.operator_permissions)
    derived = _derive_read_only_permissions(case.operator_permissions, case.expected_removals)
    operator = _permission_map(case.operator_permissions)
    read_only = _permission_map(derived)
    expected_keys = {key for key, verbs in operator.items() if verbs - set(RBAC_MUTATING_VERBS)}

    assert case.operator_permissions == original
    assert set(read_only) == expected_keys, f"Derived resource-key mismatch for {case!r}"
    for key, verbs in read_only.items():
        expected = operator[key] - set(RBAC_MUTATING_VERBS)
        assert verbs == expected, (
            f"Read-only verb mismatch for resource {key!r} in {case!r}: "
            f"missing={sorted(expected - verbs)!r}, unexpected={sorted(verbs - expected)!r}"
        )
        assert verbs.isdisjoint(RBAC_MUTATING_VERBS), (
            f"Validator derivation retained mutation verbs for resource {key!r}: "
            f"{sorted(verbs & set(RBAC_MUTATING_VERBS))!r}"
        )
        source_verbs = next(row[2] for row in case.operator_permissions if row[:2] == key)
        derived_verbs = next(row[2] for row in derived if row[:2] == key)
        assert derived_verbs is not source_verbs


@given(permission_table_cases(), st.data())
def test_read_only_derivation_rejects_any_removal_drift(case: PermissionTableCase, data: st.DataObject) -> None:
    drifted = data.draw(drifted_expected_removals(case), label="drifted_removals")

    with pytest.raises(ValueError, match="derivation drifted"):
        _derive_read_only_permissions(case.operator_permissions, drifted)


@given(st.sampled_from(RBAC_PERMISSION_VERBS))
def test_write_verb_classification_matches_the_shared_mutation_vocabulary(verb: str) -> None:
    validator = _validator_for("operator")
    assert validator._is_write_verb(verb) is (verb in RBAC_MUTATING_VERBS)
    assert set(MUTATING_VERBS) == set(RBAC_MUTATING_VERBS)


@given(permission_table_cases())
def test_removal_format_is_deterministic_across_mapping_order(case: PermissionTableCase) -> None:
    forward = case.expected_removals
    reversed_mapping = dict(reversed(tuple(forward.items())))

    assert list(_format_verb_removals(forward).items()) == list(_format_verb_removals(reversed_mapping).items())


def test_real_cluster_validator_permissions_are_the_documented_read_only_subset() -> None:
    operator = _permission_map(RBACValidator.OPERATOR_CLUSTER_PERMISSIONS)
    validator = _permission_map(RBACValidator.VALIDATOR_CLUSTER_PERMISSIONS)
    stripped = {
        key: frozenset(verbs & set(MUTATING_VERBS)) for key, verbs in operator.items() if verbs & set(MUTATING_VERBS)
    }

    assert set(validator) <= set(operator)
    assert all(verbs <= operator[key] for key, verbs in validator.items())
    assert all(verbs.isdisjoint(MUTATING_VERBS) for verbs in validator.values())
    assert stripped == VALIDATOR_CLUSTER_VERB_EXCEPTIONS


def test_real_validator_namespace_tables_contain_no_mutation_verbs() -> None:
    tables: Mapping[str, Sequence[PermissionRow]] = {
        **RBACValidator.VALIDATOR_HUB_NAMESPACE_PERMISSIONS,
        **RBACValidator.VALIDATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS,
    }
    unexpected = {
        (namespace, api_group, resource, verb)
        for namespace, entries in tables.items()
        for api_group, resource, verbs in entries
        for verb in verbs
        if verb in MUTATING_VERBS
    }

    assert not unexpected, f"Validator namespace mutation permissions: {sorted(unexpected)!r}"


@given(rbac_selector_cases())
def test_python_and_collection_expanded_permission_sets_agree(case: RbacSelectorCase) -> None:
    python_permissions = _python_permissions(case)
    collection_permissions = _collection_permissions(case)
    _assert_permission_sets_equal(case, python_permissions, collection_permissions)


@given(rbac_selector_cases(role="validator"))
def test_validator_expansions_never_contain_mutation_verbs(case: RbacSelectorCase) -> None:
    python_permissions = _python_permissions(case)
    collection_permissions = _collection_permissions(case)

    for implementation, permissions in (
        ("Python", python_permissions),
        ("Collection", collection_permissions),
    ):
        unexpected = {permission for permission in permissions if permission[2] in MUTATING_VERBS}
        assert not unexpected, (
            f"{implementation} validator mutation permissions for {case!r}: " f"{_sorted_permissions(unexpected)!r}"
        )
    _assert_permission_sets_equal(case, python_permissions, collection_permissions)


@given(rbac_selector_cases(role="validator"))
def test_validator_expansions_match_independent_required_read_contract(case: RbacSelectorCase) -> None:
    python_permissions = _python_permissions(case)
    collection_permissions = _collection_permissions(case)

    _assert_validator_read_contract("Python", case, python_permissions)
    _assert_validator_read_contract("Collection", case, collection_permissions)
    _assert_permission_sets_equal(case, python_permissions, collection_permissions)


@given(rbac_selector_cases(role="operator"))
def test_operator_expansions_contain_exact_required_mutation_surfaces(case: RbacSelectorCase) -> None:
    python_permissions = _python_permissions(case)
    collection_permissions = _collection_permissions(case)
    _assert_permission_sets_equal(case, python_permissions, collection_permissions)
    expected = _expected_operator_mutations(case)

    for implementation, permissions in (
        ("Python", python_permissions),
        ("Collection", collection_permissions),
    ):
        actual = {permission for permission in permissions if permission[2] in MUTATING_VERBS}
        assert actual == expected, (
            f"{implementation} operator mutation surface mismatch for {case!r}.\n"
            f"  Missing required mutations: {_sorted_permissions(expected - actual)!r}\n"
            f"  Unexpected mutations: {_sorted_permissions(actual - expected)!r}"
        )


@given(
    skip_observability=st.booleans(),
    argocd_mode=st.sampled_from(("none", "check")),
    argocd_install_type=argocd_install_types(),
)
def test_operator_cluster_reads_superset_validator_cluster_reads(
    skip_observability: bool,
    argocd_mode: str,
    argocd_install_type: str,
) -> None:
    common = dict(
        scope="hub",
        include_decommission=False,
        include_old_hub_finalization=False,
        skip_observability=skip_observability,
        argocd_mode=argocd_mode,
        argocd_install_type=argocd_install_type,
        decommission_only=False,
    )
    operator = _read_permissions(_python_permissions(RbacSelectorCase(role="operator", **common)))
    validator = _read_permissions(_python_permissions(RbacSelectorCase(role="validator", **common)))
    operator_cluster = {permission for permission in operator if permission[3] is None}
    validator_cluster = {permission for permission in validator if permission[3] is None}

    assert operator_cluster >= validator_cluster, (
        "Operator cluster reads do not cover validator cluster reads for "
        f"mode={argocd_mode!r}, install_type={argocd_install_type!r}, "
        f"skip_observability={skip_observability!r}: "
        f"missing={_sorted_permissions(validator_cluster - operator_cluster)!r}"
    )


@example(
    InvalidRbacSelectorCase(
        role="validator",
        scope="hub",
        include_decommission=False,
        include_old_hub_finalization=False,
        skip_observability=False,
        argocd_mode="manage",
        argocd_install_type="none",
        decommission_only=False,
        invalid_kind="validator_manage",
        expected_error="validator role cannot use",
    )
)
@given(invalid_rbac_selector_cases())
def test_invalid_role_feature_combinations_are_rejected_by_both_forms(case: InvalidRbacSelectorCase) -> None:
    with pytest.raises(ValueError) as python_error:
        _run_python_invalid_cluster_validation(case)
    with pytest.raises(ValueError) as collection_error:
        _collection_permissions(case)

    assert case.expected_error in str(python_error.value), f"Unexpected Python rejection for {case!r}"
    assert case.expected_error in str(collection_error.value), f"Unexpected collection rejection for {case!r}"


@given(collection_only_invalid_rbac_selector_cases())
def test_collection_rejects_collection_only_invalid_selector_combinations(case: InvalidRbacSelectorCase) -> None:
    with pytest.raises(ValueError) as collection_error:
        _collection_permissions(case)

    assert case.expected_error in str(collection_error.value), f"Unexpected collection rejection for {case!r}"


@given(role=rbac_roles(), argocd_mode=invalid_argocd_modes(), argocd_install_type=argocd_install_types())
def test_python_argocd_helper_rejects_invalid_modes(
    role: str,
    argocd_mode: str,
    argocd_install_type: str,
) -> None:
    validator = _validator_for(role)

    with pytest.raises(ValueError, match="Invalid argocd_mode"):
        validator._get_argocd_cluster_permissions(argocd_mode, argocd_install_type)


@given(role_mode=valid_argocd_role_mode_pairs())
def test_vanilla_argocd_omits_argocds_but_keeps_base_reads(role_mode: tuple[str, str]) -> None:
    role, argocd_mode = role_mode
    case = RbacSelectorCase(
        role=role,
        scope="hub",
        include_decommission=False,
        include_old_hub_finalization=False,
        skip_observability=False,
        argocd_mode=argocd_mode,
        argocd_install_type="vanilla",
        decommission_only=False,
    )
    python_permissions = _python_permissions(case)
    collection_permissions = _collection_permissions(case)

    _assert_permission_sets_equal(case, python_permissions, collection_permissions)
    for implementation, permissions in (
        ("Python", python_permissions),
        ("Collection", collection_permissions),
    ):
        permission_set = set(permissions)
        argocds = {permission for permission in permission_set if permission[1] == "argocds"}
        assert not argocds, (
            f"{implementation} vanilla installation required argocds discovery for {case!r}: "
            f"{_sorted_permissions(argocds)!r}"
        )
        assert ARGOCD_BASE_READS <= permission_set, (
            f"{implementation} vanilla installation lost Argo CD base reads for {case!r}: "
            f"{_sorted_permissions(ARGOCD_BASE_READS - permission_set)!r}"
        )


def _assert_operator_or_unknown_install_discovery_reads(
    role: str,
    argocd_mode: str,
    argocd_install_type: str,
) -> None:
    case = RbacSelectorCase(
        role=role,
        scope="hub",
        include_decommission=False,
        include_old_hub_finalization=False,
        skip_observability=False,
        argocd_mode=argocd_mode,
        argocd_install_type=argocd_install_type,
        decommission_only=False,
    )
    python_permissions = _python_permissions(case)
    collection_permissions = _collection_permissions(case)

    _assert_permission_sets_equal(case, python_permissions, collection_permissions)
    for implementation, permissions in (
        ("Python", python_permissions),
        ("Collection", collection_permissions),
    ):
        argocds = {permission for permission in permissions if permission[1] == "argocds"}
        assert argocds == ARGOCDS_DISCOVERY_READS, (
            f"{implementation} Argo CD operator-install discovery mismatch for {case!r}: "
            f"missing={_sorted_permissions(ARGOCDS_DISCOVERY_READS - argocds)!r}, "
            f"unexpected={_sorted_permissions(argocds - ARGOCDS_DISCOVERY_READS)!r}"
        )


@pytest.mark.parametrize("role", ("operator", "validator"))
@given(argocd_install_type=st.sampled_from(("operator", "unknown")))
def test_check_mode_operator_or_unknown_install_requires_discovery_reads(
    role: str,
    argocd_install_type: str,
) -> None:
    _assert_operator_or_unknown_install_discovery_reads(role, "check", argocd_install_type)


@given(argocd_install_type=st.sampled_from(("operator", "unknown")))
def test_manage_mode_operator_or_unknown_install_requires_discovery_reads(argocd_install_type: str) -> None:
    _assert_operator_or_unknown_install_discovery_reads("operator", "manage", argocd_install_type)


@given(role=rbac_roles(), argocd_install_type=argocd_install_types())
def test_argocd_none_mode_preserves_base_and_adds_no_argocd_permissions(
    role: str,
    argocd_install_type: str,
) -> None:
    case = RbacSelectorCase(
        role=role,
        scope="hub",
        include_decommission=False,
        include_old_hub_finalization=False,
        skip_observability=False,
        argocd_mode="none",
        argocd_install_type=argocd_install_type,
        decommission_only=False,
    )
    expected_base = set(_python_non_argocd_permissions(case))
    python_permissions = _python_permissions(case)
    collection_permissions = _collection_permissions(case)

    _assert_permission_sets_equal(case, python_permissions, collection_permissions)
    for implementation, permissions in (
        ("Python", python_permissions),
        ("Collection", collection_permissions),
    ):
        permission_set = set(permissions)
        unexpected = {permission for permission in permission_set if permission[1] in ARGOCD_RESOURCES}
        assert not unexpected, (
            f"{implementation} Argo CD none mode added permissions for {case!r}: "
            f"{_sorted_permissions(unexpected)!r}"
        )
        assert permission_set == expected_base, (
            f"{implementation} Argo CD none mode changed unrelated base permissions for {case!r}: "
            f"missing={_sorted_permissions(expected_base - permission_set)!r}, "
            f"unexpected={_sorted_permissions(permission_set - expected_base)!r}"
        )


@given(rbac_selector_cases())
def test_normalized_permission_expansions_contain_no_duplicate_tuples(case: RbacSelectorCase) -> None:
    python_permissions = _python_permissions(case)
    collection_permissions = _collection_permissions(case)

    assert len(python_permissions) == len(set(python_permissions)), f"Duplicate Python permissions for {case!r}"
    assert len(collection_permissions) == len(
        set(collection_permissions)
    ), f"Duplicate collection permissions for {case!r}"


@given(rbac_selector_cases())
def test_python_raw_expansion_contains_only_documented_duplicate_overlap(case: RbacSelectorCase) -> None:
    counts = Counter(_python_permissions_raw(case))
    duplicates = {permission: count for permission, count in counts.items() if count > 1}
    expected = _expected_raw_duplicate_overlap(case)

    assert duplicates == expected, (
        f"Unexpected raw Python permission duplication for {case!r}: "
        f"expected={_sorted_permission_counts(expected)!r}, "
        f"actual={_sorted_permission_counts(duplicates)!r}"
    )


@given(rbac_selector_cases())
def test_collection_raw_expansion_contains_only_documented_duplicate_overlap(case: RbacSelectorCase) -> None:
    counts = Counter(_collection_permissions_raw(case))
    duplicates = {permission: count for permission, count in counts.items() if count > 1}
    expected = _expected_raw_duplicate_overlap(case)

    assert duplicates == expected, (
        f"Unexpected raw Collection permission duplication for {case!r}: "
        f"expected={_sorted_permission_counts(expected)!r}, "
        f"actual={_sorted_permission_counts(duplicates)!r}"
    )
