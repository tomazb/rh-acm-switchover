"""R4-04 migration evidence: the pure Velero child-Restore model (plan Task 2).

This module owns the controller's generated child names, strict owner
validation and the five-field child entry, the one-shot Backup freeze and
role/membership completion, and the passive_patch completion cohorts.
Normative sources: the August amendment
(docs/plans/2026-08-27-r4-04-current-base-design-amendment.md sections 4 and 5)
and the controller child-evidence amendment
(docs/plans/2026-09-30-r4-04-controller-child-evidence-amendment.md sections 3
and 4).

Nothing here performs I/O. Every rule fails closed by raising
MigrationEvidenceError with a stable ``code``. The Ansible collection mirrors
this module in plugins/module_utils/migration_child_evidence.py; the two share
no code and tests/test_migration_evidence_parity.py holds them equal.
"""

from typing import Any, Dict, List, Optional, Tuple

from lib.migration_evidence import (
    ACM_MINOR_CONTRACTS,
    MigrationEvidenceError,
    predict_correlated_backup,
    select_correlated_evidence,
    select_latest_evidence,
)

# The four ACM Restore status fields that publish Velero Restore names.
STATUS_NAME_FIELDS = (
    "veleroManagedClustersRestoreName",
    "veleroCredentialsRestoreName",
    "veleroResourcesRestoreName",
    "veleroGenericResourcesRestoreName",
)
_MC, _CREDS, _RES, _GEN = STATUS_NAME_FIELDS
# August section 5: the journal's child lists, one per Backup category.
VELERO_RESTORE_LISTS = (
    "managed_clusters",
    "credentials",
    "resources",
    "resources_generic",
    "activation_credentials",
    "activation_resources",
    "activation_resources_generic",
)

_LEGACY = "legacy_2_12_2_16"
_ACTIVE = "active_2_17"
_ONE_SHOT_KINDS = ("passive_restore", "full_restore")
_MUTATION_KINDS = _ONE_SHOT_KINDS + ("passive_patch",)
_OWNER_GROUP = "cluster.open-cluster-management.io"
_MAX_CHILD_NAME = 252
_ACTIVE_SUFFIX = "-active"

# Amendment-2 sections 3.2, 3.3 and 4.1: the Backups frozen before a one-shot
# create, as (resource type, selection, correlation source, decision, frozen as).
# "equals" and "none_required" predictions freeze nothing; they gate the create.
_ONE_SHOT_PREDICTIONS = {
    ("passive_restore", _LEGACY): (
        ("ManagedClusters", "latest", None, "required", "managed_clusters"),
        ("Credentials", "latest", None, "required", "activation_credentials"),
        ("ResourcesGeneric", "latest", None, "optional", "activation_resources_generic"),
    ),
    ("passive_restore", _ACTIVE): (("ManagedClusters", "latest", None, "required", "managed_clusters"),),
}
_FULL_PREDICTIONS = (
    ("ManagedClusters", "concrete", None, "required", "managed_clusters"),
    ("Credentials", "concrete", None, "required", "credentials"),
    ("Resources", "concrete", None, "required", "resources"),
    ("ResourcesGeneric", "correlated", "resources", "required", "resources_generic"),
)
_ONE_SHOT_PREDICTIONS[("full_restore", _LEGACY)] = _FULL_PREDICTIONS + (
    ("CredentialsHive", "correlated", "credentials", "none_required", None),
    ("CredentialsCluster", "correlated", "credentials", "none_required", None),
)
_ONE_SHOT_PREDICTIONS[("full_restore", _ACTIVE)] = _FULL_PREDICTIONS + (
    ("CredentialsActive", "correlated", "credentials", "equals", "credentials"),
    ("ResourcesGenericActive", "correlated", "resources", "equals", "resources_generic"),
)

# Amendment-2 section 4.1 roles as (role, bound category, status field). A role
# without a status field is a 2.17 unpublished -active role located through the
# published role of the same category.
_ONE_SHOT_ROLES = {
    ("passive_restore", _LEGACY): (
        ("ManagedClusters", "managed_clusters", _MC),
        ("Credentials", "activation_credentials", _CREDS),
        ("ResourcesGeneric", "activation_resources_generic", _GEN),
    ),
    ("passive_restore", _ACTIVE): (("ManagedClusters", "managed_clusters", _MC),),
}
_FULL_ROLES = (
    ("ManagedClusters", "managed_clusters", _MC),
    ("Credentials", "credentials", _CREDS),
    ("Resources", "resources", _RES),
    ("ResourcesGeneric", "resources_generic", _GEN),
)
_ONE_SHOT_ROLES[("full_restore", _LEGACY)] = _FULL_ROLES
_ONE_SHOT_ROLES[("full_restore", _ACTIVE)] = _FULL_ROLES + (
    ("CredentialsActive", "credentials", None),
    ("ResourcesGenericActive", "resources_generic", None),
)
_ACTIVE_PUBLISHED_ROLE = {"CredentialsActive": "Credentials", "ResourcesGenericActive": "ResourcesGeneric"}
# Frozen Backup categories per mutation: (required, optional). Amendment-2
# section 5.4 for passive_restore; August sections 3 and 4 for the others.
FROZEN_CATEGORIES = {
    ("passive_restore", _LEGACY): (("managed_clusters", "activation_credentials"), ("activation_resources_generic",)),
    ("passive_restore", _ACTIVE): (("managed_clusters",), ()),
    ("full_restore", _LEGACY): (("managed_clusters", "credentials", "resources", "resources_generic"), ()),
    ("full_restore", _ACTIVE): (("managed_clusters", "credentials", "resources", "resources_generic"), ()),
}
PASSIVE_PATCH_CATEGORIES = (
    "managed_clusters",
    "activation_credentials",
    "activation_resources",
    "activation_resources_generic",
)
# passive_patch ordinary status associations and the category each binds to.
_PATCH_ASSOCIATIONS = (
    (_CREDS, "activation_credentials"),
    (_RES, "activation_resources"),
    (_GEN, "activation_resources_generic"),
)


def generated_child_name(acm_restore_name: Any, backup_name: Any, *, active_suffix: bool = False) -> str:
    """Mirror getValidKsRestoreName: "<restore>-<backup>" cut to 252 characters.

    The 2.17 -active suffix is appended after the truncation, as the controller
    renames the already generated child (amendment-2 section 4.1).
    """
    if not _non_empty_str(acm_restore_name) or not _non_empty_str(backup_name):
        raise MigrationEvidenceError("malformed_child_name_input", "restore and Backup names must be non-empty")
    name = f"{acm_restore_name}-{backup_name}"[:_MAX_CHILD_NAME]
    return name + _ACTIVE_SUFFIX if active_suffix else name


def is_owned_by(raw: Any, owner_name: str, owner_uid: str) -> bool:
    """Return whether a listed Velero Restore belongs in the owner-filtered list.

    The filter is deliberately wider than the exact owner identity: any
    controller reference carrying the ACM Restore UID, or naming a Restore of
    that name in the ACM group (the controller's own index key), selects the
    child, so validate_velero_child then blocks an impostor instead of the
    filter silently dropping it.
    """
    _, _, _, refs = _child_parts(raw)
    for ref in refs:
        if ref.get("controller") is not True:
            continue
        if ref.get("uid") == owner_uid:
            return True
        if ref.get("name") == owner_name and ref.get("kind") == "Restore" and _ref_group(ref) == _OWNER_GROUP:
            return True
    return False


def validate_velero_child(
    raw: Any, *, namespace: str, owner_name: str, owner_uid: str, expected_backup_name: str
) -> Dict[str, Any]:
    """Return the five-field entry of a Completed child bound to the expected Backup.

    August section 5 "Strict owner validation" and "Journaled child evidence".
    """
    if not _non_empty_str(expected_backup_name):
        raise MigrationEvidenceError("malformed_child_expectation", "the expected Backup name must be non-empty")
    return _child_entry(raw, namespace, owner_name, owner_uid, expected_backup_name)


def acm_phase_accepts(mutation_kind: Any, controller_contract: Any, phase: Any) -> bool:
    """Apply the August section 5 method-specific ACM phase gate.

    One-shot kinds need Finished; passive_patch also accepts Enabled, which is
    only a phase gate: the caller still requires the complete child proof.
    """
    _require_kind(mutation_kind, _MUTATION_KINDS)
    _require_contract(controller_contract)
    if mutation_kind == "passive_patch":
        return phase in ("Finished", "Enabled")
    return phase == "Finished"


def passive_patch_cohort(controller_contract: Any, owner_children: Any, status_names: Any) -> List[Dict[str, Any]]:
    """Return the owner children whose completion the controller aggregates.

    Legacy lanes aggregate the entire owner-filtered list. 2.17 mirrors
    getLatestVeleroRestores: with all four status names empty the entire list,
    otherwise the current names plus every child whose name, with one trailing
    -active removed, equals a current name with one trailing -active removed.
    Input order is preserved.
    """
    _require_contract(controller_contract)
    names = _status_names(status_names)
    children = _owner_children(owner_children)
    current = {name for name in names.values() if name}
    if controller_contract == _LEGACY or not current:
        return [raw for _, raw in children]
    bases = {_trim_active(name) for name in current}
    return [raw for name, raw in children if name in current or _trim_active(name) in bases]


def one_shot_required_predictions(mutation_kind: Any, controller_contract: Any) -> List[Dict[str, Any]]:
    """Return the Backup predictions a one-shot create freezes, in evaluation order."""
    _require_kind(mutation_kind, _ONE_SHOT_KINDS)
    _require_contract(controller_contract)
    return [
        {
            "resource_type": resource_type,
            "selection": selection,
            "source_category": source,
            "decision": decision,
            "freeze_as": category,
        }
        for resource_type, selection, source, decision, category in _ONE_SHOT_PREDICTIONS[
            (mutation_kind, controller_contract)
        ]
    ]


def freeze_one_shot_backups(
    mutation_kind: Any, controller_contract: Any, inventory: Any, namespace: str, concrete_backups: Any = None
) -> Dict[str, Dict[str, Any]]:
    """Return the frozen Backup categories of a one-shot create, or raise.

    passive_restore predicts every category from the strict Backup inventory;
    full_restore takes its three concrete ACM Restore Backups as seven-field
    projections and derives the rest. A legacy hive/cluster credential Backup
    correlated from the credentials Backup, or a 2.17 -active prediction that
    differs from its frozen category, blocks the create (amendment-2 section 4.1).
    """
    predictions = one_shot_required_predictions(mutation_kind, controller_contract)
    concrete_categories = [p["freeze_as"] for p in predictions if p["selection"] == "concrete"]
    frozen = _concrete_backups(concrete_backups, concrete_categories)
    for prediction in predictions:
        if prediction["selection"] == "concrete":
            continue
        resource_type = prediction["resource_type"]
        source = frozen[prediction["source_category"]]["name"] if prediction["source_category"] else None
        if prediction["selection"] == "latest":
            decision, evidence = select_latest_evidence(inventory, resource_type, namespace)
        elif prediction["decision"] in ("required", "optional"):
            decision, evidence = select_correlated_evidence(inventory, source, resource_type, namespace)
        else:
            # Gate-only predictions journal nothing, so the raw prediction suffices.
            decision, raw = predict_correlated_backup(inventory, source, resource_type)
            if prediction["decision"] == "none_required" and decision != "none":
                raise MigrationEvidenceError(
                    "legacy_credential_variant_selected", f"a {resource_type} Backup correlates with {source}"
                )
            if prediction["decision"] == "equals" and (
                raw is None or raw["metadata"]["name"] != frozen[prediction["freeze_as"]]["name"]
            ):
                raise MigrationEvidenceError(
                    "active_prediction_mismatch",
                    f"the {resource_type} prediction differs from backups.{prediction['freeze_as']}",
                )
            continue
        if decision == "none":
            if prediction["decision"] == "required":
                raise MigrationEvidenceError("required_prediction_missing", f"no {resource_type} Backup is selected")
            continue
        frozen[prediction["freeze_as"]] = evidence
    return frozen


def predict_one_shot_child_names(
    mutation_kind: Any, controller_contract: Any, acm_restore_name: Any, frozen_backups: Any
) -> Dict[str, str]:
    """Return every required role's generated child name; raise on a collision.

    Run before the create so a collision, including one produced by the
    252-character truncation, blocks with zero ACM Restore mutation
    (amendment-2 section 4.2).
    """
    roles = _one_shot_roles(mutation_kind, controller_contract, frozen_backups)
    names = {
        role: generated_child_name(acm_restore_name, frozen_backups[category]["name"], active_suffix=field is None)
        for role, category, field in roles
    }
    if len(set(names.values())) != len(names):
        raise MigrationEvidenceError("generated_name_collision", "two required roles share a generated child name")
    return names


def one_shot_completion(
    mutation_kind: Any,
    controller_contract: Any,
    *,
    acm_restore_name: str,
    acm_restore_uid: str,
    namespace: str,
    frozen_backups: Any,
    status_names: Any,
    owner_children: Any,
    acm_phase: Any,
) -> Dict[str, List[Dict[str, Any]]]:
    """Return the child lists of a completed one-shot Restore, or raise.

    Amendment-2 section 4.1 roles are satisfied first, then, independently,
    the section 4.2 owner membership: every owner child Completed and bound to
    a Backup frozen for this mutation kind and lane.
    """
    roles = _one_shot_roles(mutation_kind, controller_contract, frozen_backups)
    names = _status_names(status_names)
    children = dict(_owner_children(owner_children))
    if not acm_phase_accepts(mutation_kind, controller_contract, acm_phase):
        raise MigrationEvidenceError("acm_phase_not_accepted", f"ACM Restore phase {acm_phase!r} is not Finished")
    published = {field for _, _, field in roles if field}
    for field in STATUS_NAME_FIELDS:
        if field not in published and names[field] != "":
            raise MigrationEvidenceError("unexpected_status_name", f"status.{field} names no required role")
    owner = (namespace, acm_restore_name, acm_restore_uid)
    entries = {}
    for role, category, field in roles:
        backup_name = frozen_backups[category]["name"]
        if field:
            raw = _published_child(children, names, field)
        else:
            published_child = entries[_ACTIVE_PUBLISHED_ROLE[role]][1]["name"]
            raw = _unpublished_child(children, backup_name, published_child, role)
        entries[role] = (category, _child_entry(raw, *owner, backup_name))
    if len({entry["name"] for _, entry in entries.values()}) != len(entries):
        raise MigrationEvidenceError("role_child_collision", "two required roles resolve to one child")
    frozen_names = {backup["name"] for backup in frozen_backups.values()}
    for name, raw in children.items():
        backup_name = _child_backup_name(raw)
        if not _non_empty_str(backup_name):
            raise MigrationEvidenceError("malformed_velero_restore", f"owner child {name} has no spec.backupName")
        if backup_name not in frozen_names:
            raise MigrationEvidenceError(
                "owner_child_unfrozen_backup", f"owner child {name} restores an unfrozen Backup"
            )
        _child_entry(raw, *owner, backup_name)
    return _child_lists(entries.values())


def passive_patch_completion(
    controller_contract: Any,
    *,
    acm_restore_name: str,
    acm_restore_uid: str,
    namespace: str,
    frozen_backups: Any,
    precondition_status_names: Any,
    status_names: Any,
    owner_children: Any,
    acm_phase: Any,
) -> Dict[str, List[Dict[str, Any]]]:
    """Return the consumed child lists of a completed passive_patch, or raise.

    August section 5 legacy and 2.17 passive_patch contracts. Every cohort
    member must be Completed, but only children consumed by this transaction
    are returned; historical cohort children are checked, never journaled
    (amendment-2 section 5.2).
    """
    _require_contract(controller_contract)
    _require_categories(frozen_backups, PASSIVE_PATCH_CATEGORIES, ())
    before = _status_names(precondition_status_names)
    after = _status_names(status_names)
    if before[_MC] != "":
        raise MigrationEvidenceError("precondition_status_invalid", f"precondition status.{_MC} must be empty")
    children = _owner_children(owner_children)
    by_name = dict(children)
    if not acm_phase_accepts("passive_patch", controller_contract, acm_phase):
        raise MigrationEvidenceError("acm_phase_not_accepted", f"ACM Restore phase {acm_phase!r} is not accepted")
    owner = (namespace, acm_restore_name, acm_restore_uid)
    consumed = []

    def bind(raw: Any, category: str) -> None:
        consumed.append((category, _child_entry(raw, *owner, frozen_backups[category]["name"])))

    bind(_published_child(by_name, after, _MC), "managed_clusters")
    # Legacy restoreOnlyManagedClusters always publishes credentials and generic.
    required = (_CREDS, _GEN) if controller_contract == _LEGACY else ()
    for field, category in _PATCH_ASSOCIATIONS:
        if field in required:
            bind(_published_child(by_name, after, field), category)
    for field, category in _PATCH_ASSOCIATIONS:
        if field not in required and after[field] != before[field]:
            if after[field] == "":
                raise MigrationEvidenceError("status_name_cleared", f"status.{field} was cleared by the patch")
            bind(_published_child(by_name, after, field), category)
    if controller_contract == _ACTIVE:
        for category in ("activation_credentials", "activation_resources_generic"):
            bind(_active_variant(children, frozen_backups[category]["name"], category), category)
        for _, raw in children:
            _require_owner(raw, acm_restore_name, acm_restore_uid)
    for raw in passive_patch_cohort(controller_contract, [raw for _, raw in children], after):
        _child_entry(raw, *owner, None)
    return _child_lists(consumed)


def normalize_child_list(entries: Any) -> List[Dict[str, Any]]:
    """Return one child list sorted by name, collapsing identical duplicate entries.

    August section 5 "Journaled child evidence": a duplicate locator collapses
    only when every observed field agrees; conflicting evidence is malformed.
    """
    if not isinstance(entries, list) or not all(
        isinstance(entry, dict) and _non_empty_str(entry.get("name")) for entry in entries
    ):
        raise MigrationEvidenceError("malformed_child_entry", "a child list must hold entries with non-empty names")
    by_name: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        if by_name.setdefault(entry["name"], entry) != entry:
            raise MigrationEvidenceError(
                "conflicting_child_evidence", f"child {entry['name']} has conflicting evidence"
            )
    return [by_name[name] for name in sorted(by_name)]


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _require_kind(mutation_kind: Any, allowed: Tuple[str, ...]) -> None:
    if mutation_kind not in allowed:
        raise MigrationEvidenceError("unsupported_mutation_kind", f"mutation kind {mutation_kind!r} is not supported")


def _require_contract(controller_contract: Any) -> None:
    if controller_contract not in ACM_MINOR_CONTRACTS.values():
        raise MigrationEvidenceError(
            "unknown_controller_contract", f"controller contract {controller_contract!r} is not pinned"
        )


def _require_categories(frozen_backups: Any, required: Tuple[str, ...], optional: Tuple[str, ...]) -> None:
    if not isinstance(frozen_backups, dict) or not set(required) <= set(frozen_backups) <= set(required + optional):
        raise MigrationEvidenceError("frozen_backups_invalid", "the frozen Backup categories do not fit the mutation")
    for backup in frozen_backups.values():
        if not isinstance(backup, dict) or not _non_empty_str(backup.get("name")):
            raise MigrationEvidenceError("frozen_backups_invalid", "a frozen Backup has no name")


def _concrete_backups(concrete_backups: Any, categories: List[str]) -> Dict[str, Dict[str, Any]]:
    if not categories:
        if concrete_backups is not None:
            raise MigrationEvidenceError("concrete_backups_invalid", "this mutation takes no concrete Backups")
        return {}
    try:
        _require_categories(concrete_backups, tuple(categories), ())
    except MigrationEvidenceError as exc:
        raise MigrationEvidenceError("concrete_backups_invalid", str(exc)) from exc
    return dict(concrete_backups)


def _one_shot_roles(
    mutation_kind: Any, controller_contract: Any, frozen_backups: Any
) -> Tuple[Tuple[str, str, Optional[str]], ...]:
    _require_kind(mutation_kind, _ONE_SHOT_KINDS)
    _require_contract(controller_contract)
    _require_categories(frozen_backups, *FROZEN_CATEGORIES[(mutation_kind, controller_contract)])
    # An absent optional category is the immutable no-candidate decision: no role.
    return tuple(role for role in _ONE_SHOT_ROLES[(mutation_kind, controller_contract)] if role[1] in frozen_backups)


def _status_names(status_names: Any) -> Dict[str, str]:
    if (
        not isinstance(status_names, dict)
        or set(status_names) != set(STATUS_NAME_FIELDS)
        or not all(isinstance(value, str) for value in status_names.values())
    ):
        raise MigrationEvidenceError("malformed_status_names", "status names must be the four Restore-name strings")
    return status_names


def _owner_children(owner_children: Any) -> List[Tuple[str, Dict[str, Any]]]:
    """Return (name, raw) pairs of the owner-filtered child list."""
    if not isinstance(owner_children, list):
        raise MigrationEvidenceError("malformed_owner_children", "the owner-filtered children must be a list")
    pairs = []
    for raw in owner_children:
        metadata, _, _, _ = _child_parts(raw)
        pairs.append((metadata.get("name"), raw))
    names = [name for name, _ in pairs]
    if not all(_non_empty_str(name) for name in names) or len(set(names)) != len(names):
        raise MigrationEvidenceError("malformed_owner_children", "owner children need unique non-empty names")
    return pairs


def _published_child(children: Dict[str, Any], names: Dict[str, str], field: str) -> Any:
    name = names[field]
    if not name:
        raise MigrationEvidenceError("required_status_name_missing", f"status.{field} is empty")
    if name not in children:
        raise MigrationEvidenceError("required_child_missing", f"status.{field} names {name}, not an owner child")
    return children[name]


def _unpublished_child(children: Dict[str, Any], backup_name: str, published_name: str, role: str) -> Any:
    """Locate a 2.17 -active role: the one owner child bound to its Backup other than the published child."""
    matches = [
        raw for name, raw in children.items() if name != published_name and _child_backup_name(raw) == backup_name
    ]
    return _exactly_one(matches, role)


def _active_variant(children: List[Tuple[str, Any]], backup_name: str, category: str) -> Any:
    """Locate a 2.17 passive_patch -active child: the one -active owner child bound to the category."""
    matches = [
        raw for name, raw in children if name.endswith(_ACTIVE_SUFFIX) and _child_backup_name(raw) == backup_name
    ]
    return _exactly_one(matches, category)


def _exactly_one(matches: List[Any], label: str) -> Any:
    if not matches:
        raise MigrationEvidenceError("active_child_missing", f"no -active owner child satisfies {label}")
    if len(matches) > 1:
        raise MigrationEvidenceError("active_child_ambiguous", f"{len(matches)} owner children satisfy {label}")
    return matches[0]


def _child_parts(raw: Any) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    """Return (metadata, spec, status, ownerReferences) of a structurally sound Velero Restore."""
    if not isinstance(raw, dict) or not isinstance(raw.get("metadata"), dict):
        raise MigrationEvidenceError("malformed_velero_restore", "a Velero Restore must be an object with metadata")
    metadata = raw["metadata"]
    spec = raw.get("spec", {})
    status = raw.get("status", {})
    refs = metadata.get("ownerReferences", [])
    if not isinstance(spec, dict) or not isinstance(status, dict) or not isinstance(refs, list):
        raise MigrationEvidenceError("malformed_velero_restore", "Velero Restore spec/status/ownerReferences")
    for ref in refs:
        if not isinstance(ref, dict) or ("controller" in ref and not isinstance(ref["controller"], bool)):
            raise MigrationEvidenceError("malformed_velero_restore", "a Velero Restore ownerReference is malformed")
    return metadata, spec, status, refs


def _child_backup_name(raw: Any) -> Any:
    _, spec, _, _ = _child_parts(raw)
    return spec.get("backupName")


def _ref_group(ref: Dict[str, Any]) -> Optional[str]:
    api_version = ref.get("apiVersion")
    if not isinstance(api_version, str) or "/" not in api_version:
        return None
    group, version = api_version.split("/", 1)
    return group if version else None


def _require_owner(raw: Any, owner_name: str, owner_uid: str) -> None:
    _, _, _, refs = _child_parts(raw)
    controllers = [ref for ref in refs if ref.get("controller") is True]
    if not controllers:
        raise MigrationEvidenceError("velero_restore_owner_missing", "the Velero Restore has no controller owner")
    if len(controllers) > 1:
        raise MigrationEvidenceError("velero_restore_owner_ambiguous", "the Velero Restore has several controllers")
    ref = controllers[0]
    if (
        _ref_group(ref) != _OWNER_GROUP
        or ref.get("kind") != "Restore"
        or ref.get("name") != owner_name
        or ref.get("uid") != owner_uid
    ):
        raise MigrationEvidenceError(
            "velero_restore_owner_mismatch", "the Velero Restore controller is not the ACM Restore"
        )


def _child_entry(
    raw: Any, namespace: str, owner_name: str, owner_uid: str, expected_backup_name: Optional[str]
) -> Dict[str, Any]:
    """Validate an owned, Completed child; bind it when a Backup is expected.

    Checks run in a fixed order: structure, namespace, name/uid, owner,
    backupName, phase. None as the expected Backup checks a historical cohort
    member without binding it.
    """
    if not all(_non_empty_str(value) for value in (namespace, owner_name, owner_uid)):
        raise MigrationEvidenceError("malformed_child_expectation", "namespace and owner identity must be non-empty")
    metadata, spec, status, _ = _child_parts(raw)
    if metadata.get("namespace") != namespace:
        raise MigrationEvidenceError("velero_restore_namespace_mismatch", f"a child is not in namespace {namespace!r}")
    name = metadata.get("name")
    uid = metadata.get("uid")
    if not _non_empty_str(name) or not _non_empty_str(uid):
        raise MigrationEvidenceError("malformed_velero_restore", "Velero Restore name and uid must be non-empty")
    _require_owner(raw, owner_name, owner_uid)
    backup_name = spec.get("backupName")
    if not _non_empty_str(backup_name):
        raise MigrationEvidenceError("malformed_velero_restore", f"Velero Restore {name} has no spec.backupName")
    if expected_backup_name is not None and backup_name != expected_backup_name:
        raise MigrationEvidenceError(
            "velero_restore_backup_mismatch",
            f"Velero Restore {name} restores {backup_name}, not {expected_backup_name}",
        )
    phase = status.get("phase")
    if phase is None:
        raise MigrationEvidenceError("velero_restore_phase_missing", f"Velero Restore {name} has no status.phase")
    if not isinstance(phase, str):
        raise MigrationEvidenceError("velero_restore_phase_malformed", f"Velero Restore {name} phase is not a string")
    if phase != "Completed":
        raise MigrationEvidenceError("velero_restore_not_completed", f"Velero Restore {name} phase is {phase!r}")
    return {"namespace": namespace, "name": name, "uid": uid, "backup_name": backup_name, "phase": "Completed"}


def _child_lists(pairs: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Return the seven name-sorted child lists."""
    lists: Dict[str, List[Dict[str, Any]]] = {key: [] for key in VELERO_RESTORE_LISTS}
    for category, entry in pairs:
        lists[category].append(entry)
    return {key: normalize_child_list(entries) for key, entries in lists.items()}


def _trim_active(name: str) -> str:
    # Go strings.TrimSuffix: one trailing "-active" at most.
    return name[: -len(_ACTIVE_SUFFIX)] if name.endswith(_ACTIVE_SUFFIX) else name
