# SPDX-License-Identifier: MIT
"""R4-04 activation migration-evidence transaction decisions (plan Task 8).

Collection mirror of the transaction in modules/activation.py (SecondaryActivation).
Every function is pure: role YAML performs each strict read through
acm_k8s_read_outcome and each guarded mutation through acm_restore_guarded_mutation,
passes their sanitized results here, and persists a returned ``candidate`` journal
only through checkpoint_phase ``status: update``. Nothing here performs I/O or imports
Ansible, so root parity tests drive the same decision sequence as the Python CLI.

A read result is the acm_k8s_read_outcome mapping (``read_status``, ``resources``,
``resource_version``). Every refusal raises ActivationRefused with an operator
message mirroring the Python FatalError; MigrationEvidenceError is wrapped exactly as
SecondaryActivation._evidence wraps it.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    ACTIVATION_RESTORE_NAME,
    BACKUP_NAMESPACE,
    BENIGN_ALREADY_AVAILABLE_MESSAGE_PATTERN,
    CLEANUP_BEFORE_RESTORE_VALUE,
    FULL_RESTORE_NAME,
    LOCAL_CLUSTER_NAME,
    VELERO_BACKUP_LATEST,
    VELERO_BACKUP_SKIP,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_child_evidence import (
    STATUS_NAME_FIELDS,
    VELERO_RESTORE_LISTS,
    acm_phase_accepts,
    freeze_one_shot_backups,
    freeze_passive_patch_backups,
    is_owned_by,
    one_shot_completion,
    passive_patch_cohort,
    passive_patch_completion,
    predict_one_shot_child_names,
    predict_passive_patch_child_names,
    predict_passive_patch_credential_variants,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_evidence import (
    MigrationEvidenceError,
    controller_contract_for_acm_minor,
    go_normalize,
    normalize_backup_evidence,
    require_same_backup_generation,
    rfc3339_ns,
    select_latest_evidence,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_journal import (
    restore_spec_fingerprint,
    validate_journal_transition,
    validate_waiver,
)

RESTORE_API_VERSION = "cluster.open-cluster-management.io/v1beta1"
_SYNC_FIELD = "syncRestoreWithNewBackups"
_MC_FIELD = "veleroManagedClustersBackupName"
_CREDENTIALS_FIELD = "veleroCredentialsBackupName"
_RESOURCES_FIELD = "veleroResourcesBackupName"
# August section 4: the three sync options a fresh passive_patch reads and the
# upstream-normalized values it requires.
_PATCH_FIELDS = (_MC_FIELD, _CREDENTIALS_FIELD, _RESOURCES_FIELD)
_PATCH_NORMALIZED = dict(zip(_PATCH_FIELDS, (VELERO_BACKUP_SKIP, VELERO_BACKUP_LATEST, VELERO_BACKUP_LATEST)))
# July section 1: a full_restore names the controller's direct `latest` choice for
# each concrete field, as (resource type, frozen category, Restore spec field).
_FULL_RESTORE_SELECTIONS = (
    ("ManagedClusters", "managed_clusters", _MC_FIELD),
    ("Credentials", "credentials", _CREDENTIALS_FIELD),
    ("Resources", "resources", _RESOURCES_FIELD),
)
_READY_PHASES = ("Enabled", "Finished", "Completed", "Running")
_BENIGN_MESSAGE = re.compile(BENIGN_ALREADY_AVAILABLE_MESSAGE_PATTERN)
# Python parse_acm_version (lib/utils.py _ACM_VERSION_RE).
_ACM_VERSION = re.compile(r"^\s*(\d+)\.(\d+)(?:\.(\d+))?(?:[-+][A-Za-z0-9][A-Za-z0-9.+-]*)?\s*$")
MUTATION_KINDS = ("passive_patch", "passive_restore", "full_restore")
# ACM Restore phases that precede completion, and Velero Restore phases that are not
# terminal yet: both keep the completion wait polling (August section 5).
_ACM_PENDING_PHASES = (None, "", "New", "Started", "Running")
_VELERO_PENDING_PHASES = (
    "",
    "New",
    "InProgress",
    "WaitingForPluginOperations",
    "WaitingForPluginOperationsPartiallyFailed",
    "Finalizing",
    "FinalizingPartiallyFailed",
)
# Completion refusals that mean "the controller has not produced this yet".
_PENDING_CHILD_CODES = frozenset(
    {"required_status_name_missing", "required_child_missing", "active_child_missing", "velero_restore_phase_missing"}
)
# The collection variable that requests the waiver; the Python CLI journals its own flag.
WAIVER_FLAG = "acm_switchover_operation.skip_managed_cluster_expectations"
_CLEANUP_NOT_STARTED = {
    "operation_id": None,
    "state": "not_started",
    "namespace": None,
    "name": None,
    "uid": None,
    "generation": None,
    "activation_method": None,
    "mutation_kind": None,
    "cleanup_before_restore": None,
    "spec_fingerprint": None,
    "intent_at": None,
    "final_get_resource_version": None,
    "delete_accepted_at": None,
    "absence_verified_at": None,
    "completed_at": None,
    "recovery": None,
    "repair": None,
}


class ActivationRefused(Exception):
    """The activation transaction refuses to continue. Fail with this message."""


def _evidence(function: Callable, *args, **kwargs):
    """Call a pure evidence helper, turning its fail-closed refusal into ActivationRefused."""
    try:
        return function(*args, **kwargs)
    except MigrationEvidenceError as exc:
        raise ActivationRefused(f"Migration evidence refused ({exc})") from exc


def _read_ok(read: Any) -> bool:
    return isinstance(read, dict) and read.get("read_status") == "ok" and isinstance(read.get("resources"), list)


def _status(read: Any) -> str:
    return read.get("read_status", "error") if isinstance(read, dict) else "error"


def strict_get(read: Any, label: str) -> Optional[Dict[str, Any]]:
    """The object of a strict named GET, None for a proven absence, or ActivationRefused."""
    if _status(read) == "not_found":
        return None
    if not _read_ok(read) or len(read["resources"]) != 1 or not isinstance(read["resources"][0], dict):
        raise ActivationRefused(f"{label} could not be read ({_status(read)})")
    return read["resources"][0]


def strict_list(read: Any, label: str) -> List[Dict[str, Any]]:
    """The items of a positively complete strict LIST, or ActivationRefused."""
    if not _read_ok(read):
        raise ActivationRefused(f"{label} read failed ({_status(read)})")
    return read["resources"]


def acm_minor(mch_read: Any) -> Dict[str, str]:
    """The destination hub's live ACM minor and controller contract, read strictly from its MultiClusterHub."""
    items = strict_list(mch_read, "MultiClusterHub")
    if len(items) != 1:
        raise ActivationRefused(f"Expected exactly one MultiClusterHub in open-cluster-management, found {len(items)}")
    status = items[0].get("status")
    version = status.get("currentVersion") if isinstance(status, dict) else None
    match = _ACM_VERSION.match(version) if isinstance(version, str) else None
    if match is None:
        raise ActivationRefused("The MultiClusterHub reports no parseable status.currentVersion")
    minor = f"{int(match.group(1))}.{int(match.group(2))}"
    return {"acm_minor": minor, "controller_contract": _evidence(controller_contract_for_acm_minor, minor)}


def require_status_subresource(discovery_read: Any) -> None:
    """Generation guards spec changes only while the Restore CRD serves the status subresource."""
    status = _status(discovery_read)
    if status == "kind_not_served":
        raise ActivationRefused(
            "The ACM Restore CRD does not serve the status subresource; metadata.generation cannot guard "
            "its spec, so migration evidence cannot be established"
        )
    if status != "ok":
        raise ActivationRefused(f"The ACM Restore status subresource could not be verified ({status})")


def find_passive_sync_restore(restores_read: Any, namespace: str = BACKUP_NAMESPACE) -> Optional[Dict[str, Any]]:
    """The newest sync-enabled passive Restore of a positively complete strict LIST, or None.

    Mirror of modules/restore_discovery.find_passive_sync_restore_strict: a failed or
    partial read, a malformed item, or a tie for the newest creationTimestamp refuses
    before any create, patch or delete decision.
    """
    if not _read_ok(restores_read):
        raise ActivationRefused(
            f"Passive Restore discovery in {namespace} failed ({_status(restores_read)}); "
            "no Restore mutation was attempted"
        )
    candidates = []
    for item in restores_read["resources"]:
        metadata = item.get("metadata") if isinstance(item, dict) else None
        spec = item.get("spec", {}) if isinstance(item, dict) else None
        if not isinstance(metadata, dict) or not _non_empty_str(metadata.get("name")) or not isinstance(spec, dict):
            raise ActivationRefused(f"Passive Restore discovery in {namespace} returned a malformed Restore")
        if spec.get(_SYNC_FIELD) is True:
            created = rfc3339_ns(metadata.get("creationTimestamp"))
            if created is None:
                raise ActivationRefused(f"Passive sync Restore {metadata['name']} has no valid creationTimestamp")
            candidates.append((created, item))
    if not candidates:
        return None
    newest = max(created for created, _ in candidates)
    winners = [item for created, item in candidates if created == newest]
    if len(winners) > 1:
        raise ActivationRefused(f"{len(winners)} sync-enabled passive Restores share the newest creationTimestamp")
    return winners[0]


def require_passive_sync_restore(restores_read: Any) -> Dict[str, Any]:
    """The discovered passive sync Restore, which the passive method requires."""
    restore = find_passive_sync_restore(restores_read)
    if restore is None:
        raise ActivationRefused(
            "No sync-enabled passive Restore found on secondary hub. "
            f"Expected a Restore with spec.{_SYNC_FIELD}=true."
        )
    return restore


def assert_passive_restore_ready(restore: Dict[str, Any], restore_name: str) -> None:
    """Refuse unless the passive sync Restore is in an activation-ready phase (Thermos R2-M2)."""
    status = restore.get("status", {})
    phase = status.get("phase", "unknown")
    message = status.get("lastMessage", "")
    if phase in _READY_PHASES:
        return
    if phase == "FinishedWithErrors":
        messages = status.get("messages", [])
        if (
            isinstance(messages, list)
            and messages
            and all(isinstance(item, str) and _BENIGN_MESSAGE.fullmatch(item) for item in messages)
        ):
            return
    raise ActivationRefused(f"Passive sync restore not ready: {phase} - {message}")


def verify_passive_sync(restores_read: Any, live_read: Any) -> Dict[str, Any]:
    """The verify_passive_sync step: a discovered, strictly re-read, activation-ready passive Restore."""
    name = require_passive_sync_restore(restores_read)["metadata"]["name"]
    restore = strict_get(live_read, f"Restore {name}")
    if restore is None:
        raise ActivationRefused(f"{name} not found on secondary hub")
    assert_passive_restore_ready(restore, name)
    return {"name": name}


def passive_patch_precondition(live: Dict[str, Any], name: str) -> Dict[str, Any]:
    """Build the August section 4 fresh-intent precondition from a strict read, or refuse."""
    metadata = live.get("metadata") or {}
    spec = live.get("spec")
    status = live.get("status", {})
    uid, revision, generation = metadata.get("uid"), metadata.get("resourceVersion"), metadata.get("generation")
    if not _non_empty_str(uid) or not _non_empty_str(revision):
        raise ActivationRefused(f"{name} has no readable metadata.uid/resourceVersion")
    if not _positive_int(generation):
        raise ActivationRefused(f"{name} has no valid metadata.generation")
    if not isinstance(spec, dict) or spec.get(_SYNC_FIELD) is not True:
        raise ActivationRefused(f"{name} is not a {_SYNC_FIELD}=true passive Restore")
    raw = {field: spec.get(field) for field in _PATCH_FIELDS}
    if not all(_non_empty_str(value) for value in raw.values()):
        raise ActivationRefused(f"{name} does not carry all three Backup name options")
    normalized = {field: go_normalize(value) for field, value in raw.items()}
    if normalized != _PATCH_NORMALIZED:
        raise ActivationRefused(
            f"{name} Backup options are not the skip/latest/latest sync contract required before activation"
        )
    if not isinstance(status, dict):
        raise ActivationRefused(f"{name} status is malformed")
    names = {field: status.get(field, "") for field in STATUS_NAME_FIELDS}
    if not all(isinstance(value, str) for value in names.values()):
        raise ActivationRefused(f"{name} status Restore names are malformed")
    if names[STATUS_NAME_FIELDS[0]] != "":
        raise ActivationRefused(
            f"{name} already publishes {STATUS_NAME_FIELDS[0]}={names[STATUS_NAME_FIELDS[0]]}; fresh "
            "passive activation requires it to be empty"
        )
    if spec.get("cleanupBeforeRestore") != CLEANUP_BEFORE_RESTORE_VALUE:
        raise ActivationRefused(f"{name} cleanupBeforeRestore is not {CLEANUP_BEFORE_RESTORE_VALUE}")
    return {
        "generation": generation,
        "resource_version": revision,
        "backup_fields_raw": raw,
        "backup_fields_normalized": normalized,
        "status_restore_names": names,
    }


def require_journal_matches_run(journal: Dict[str, Any], kind: str, minor: str) -> None:
    restore = journal["restore"]
    if restore["mutation_kind"] != kind:
        raise ActivationRefused(
            f"The migration journal records a {restore['mutation_kind']} transaction, but this run requests "
            f"{kind}; resume with the original activation method"
        )
    if restore["acm_minor"] != minor:
        raise ActivationRefused(
            f"The migration journal was frozen on ACM {restore['acm_minor']}, but the destination hub now runs "
            f"ACM {minor}; the controller contract cannot change mid-transaction (controller_lane_changed)"
        )


def target_restore_name(kind: str) -> Optional[str]:
    """The one-shot Restore name a mutation kind creates; passive_patch patches a discovered one."""
    return {"passive_restore": ACTIVATION_RESTORE_NAME, "full_restore": FULL_RESTORE_NAME}.get(kind)


def require_unowned_restore_absent(target_read: Any, name: str) -> None:
    if strict_get(target_read, f"Restore {name}") is not None:
        raise ActivationRefused(
            f"Restore {name} already exists, but this run's migration journal never recorded creating it; "
            "it is never adopted. Review and delete it, then retry."
        )


def freeze(
    *,
    kind: str,
    acm_minor: str,
    inventory_read: Any,
    run_id: str,
    resolved_at: str,
    restores_read: Any = None,
    live_read: Any = None,
    target_read: Any = None,
) -> Dict[str, Any]:
    """Freeze a new journal before any Restore mutation (SecondaryActivation._load_or_freeze_migration_journal).

    passive_patch takes the discovery LIST and the strict GET of the discovered
    Restore; a one-shot kind takes the strict GET of its target name, which must be
    absent. The returned candidate is validated as the freeze write.
    """
    contract = _evidence(controller_contract_for_acm_minor, acm_minor)
    precondition: Optional[Dict[str, Any]] = None
    uid: Optional[str] = None
    if kind == "passive_patch":
        restore_name = require_passive_sync_restore(restores_read)["metadata"]["name"]
        live = strict_get(live_read, f"Restore {restore_name}")
        if live is None:
            raise ActivationRefused(f"{restore_name} not found before the migration freeze")
        assert_passive_restore_ready(live, restore_name)
        # Fresh intent (August section 4): refused before any Backup is frozen.
        precondition = passive_patch_precondition(live, restore_name)
        uid = live["metadata"]["uid"]
    elif kind in MUTATION_KINDS:
        restore_name = target_restore_name(kind)
        require_unowned_restore_absent(target_read, restore_name)
    else:
        raise ActivationRefused(f"Unknown mutation kind {kind!r}")

    inventory = strict_list(inventory_read, "Velero Backup inventory")
    if kind == "passive_patch":
        backups = _evidence(freeze_passive_patch_backups, contract, inventory, BACKUP_NAMESPACE)
        backup_fields = {_MC_FIELD: VELERO_BACKUP_LATEST}
    elif kind == "passive_restore":
        backups = _evidence(freeze_one_shot_backups, kind, contract, inventory, BACKUP_NAMESPACE)
        backup_fields = {_MC_FIELD: VELERO_BACKUP_LATEST}
    else:
        concrete = _select_full_restore_backups(inventory)
        backups = _evidence(freeze_one_shot_backups, kind, contract, inventory, BACKUP_NAMESPACE, concrete)
        backup_fields = {field: concrete[category]["name"] for _, category, field in _FULL_RESTORE_SELECTIONS}
    if kind == "passive_patch":
        _evidence(predict_passive_patch_child_names, contract, restore_name, backups)
    else:
        _evidence(predict_one_shot_child_names, kind, contract, restore_name, backups)

    restore: Dict[str, Any] = {
        "namespace": BACKUP_NAMESPACE,
        "name": restore_name,
        "uid": uid,
        "generation": None,
        "activation_method": "full" if kind == "full_restore" else "passive",
        "mutation_kind": kind,
        "acm_minor": acm_minor,
        "controller_contract": contract,
        "backup_fields": backup_fields,
        "cleanup_before_restore": CLEANUP_BEFORE_RESTORE_VALUE,
        "spec_fingerprint": None,
        "backup_names_verified_at": None,
        "completed_at": None,
        "names_verified_at": None,
        "teardown_revalidated_at": None,
        "velero_restores": {key: [] for key in VELERO_RESTORE_LISTS},
    }
    if precondition is not None:
        restore["passive_patch_precondition"] = precondition
    journal = {
        "schema_version": 2,
        "run_id": run_id,
        "resolved_at": resolved_at,
        "backups": backups,
        "restore": restore,
        "cleanup": dict(_CLEANUP_NOT_STARTED, backup_fields={}),
        "post_activation": {"names_verified_at": None, "completed_at": None},
        "waiver": None,
    }
    _evidence(validate_journal_transition, None, journal)
    frozen = ", ".join(f"{category}={backup['name']}" for category, backup in sorted(backups.items()))
    return {"candidate": journal, "frozen": frozen}


def _select_full_restore_backups(inventory: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """The controller's direct `latest` choice for the three concrete full_restore fields, one generation."""
    concrete = {}
    for resource_type, category, _ in _FULL_RESTORE_SELECTIONS:
        decision, evidence = _evidence(select_latest_evidence, inventory, resource_type, BACKUP_NAMESPACE)
        if decision != "selected" or evidence is None:
            raise ActivationRefused(f"No {resource_type} Backup can be selected for the full restore")
        concrete[category] = evidence
    _evidence(require_same_backup_generation, [backup["name"] for backup in concrete.values()])
    return concrete


def frozen_backup_names(journal: Dict[str, Any]) -> List[Dict[str, str]]:
    """The frozen Backups in revalidation order, for the strict GET of each."""
    return [{"category": category, "name": frozen["name"]} for category, frozen in sorted(journal["backups"].items())]


def revalidate_frozen_backups(journal: Dict[str, Any], backup_reads: Any) -> None:
    """Every frozen Backup's fresh strict GET must hold its complete seven-field projection.

    `backup_reads` are the strict GETs of frozen_backup_names(journal), in that order.
    """
    expected = frozen_backup_names(journal)
    if not isinstance(backup_reads, list) or len(backup_reads) != len(expected):
        raise ActivationRefused("The frozen Backups were not all re-read before the mutation boundary")
    for entry, read in zip(expected, backup_reads):
        category, frozen = entry["category"], journal["backups"][entry["category"]]
        if _status(read) != "ok":
            raise ActivationRefused(f"Frozen {category} Backup {frozen['name']} could not be re-read ({_status(read)})")
        resource = strict_get(read, f"Frozen {category} Backup {frozen['name']}")
        observed = _evidence(normalize_backup_evidence, resource, BACKUP_NAMESPACE)
        if observed != frozen:
            raise ActivationRefused(
                f"Frozen {category} Backup {frozen['name']} was replaced or its status drifted since the freeze"
            )


def pre_patch(*, journal: Dict[str, Any], live_read: Any, backup_reads: Any, inventory_read: Any) -> Dict[str, Any]:
    """The final pre-PATCH boundary of a passive_patch (fresh strict reads, frozen evidence unchanged).

    Returns the guarded PATCH inputs: the journaled UID, the observed resourceVersion
    and the raw ManagedClusters value the precondition recorded.
    """
    restore = journal["restore"]
    name = restore["name"]
    live = strict_get(live_read, f"Restore {name}")
    if live is None:
        raise ActivationRefused(f"{name} not found before the guarded patch")
    require_uid(live, restore["uid"], name)
    precondition = restore["passive_patch_precondition"]
    raw_mc = (live.get("spec") or {}).get(_MC_FIELD)
    if isinstance(raw_mc, str) and go_normalize(raw_mc) == VELERO_BACKUP_LATEST:
        raise ActivationRefused(
            f"{name} already requests ManagedClusters '{raw_mc}', but the migration journal holds no accepted "
            "patch: the live `latest` is stale or unowned. Review the destination hub before any retry."
        )
    assert_passive_restore_ready(live, name)
    observed = passive_patch_precondition(live, name)
    if {k: v for k, v in observed.items() if k != "resource_version"} != {
        k: v for k, v in precondition.items() if k != "resource_version"
    }:
        raise ActivationRefused(
            f"{name} changed since the migration freeze; the journaled precondition no longer holds"
        )
    revalidate_frozen_backups(journal, backup_reads)
    contract = restore["controller_contract"]
    credentials = journal["backups"]["activation_credentials"]["name"]
    inventory = strict_list(inventory_read, "Velero Backup inventory")
    variants = _evidence(predict_passive_patch_credential_variants, contract, inventory, credentials)
    _evidence(predict_passive_patch_child_names, contract, name, journal["backups"])
    return {
        "name": name,
        "namespace": restore["namespace"],
        "uid": restore["uid"],
        "resource_version": observed["resource_version"],
        "expected_managed_clusters_backup_name": precondition["backup_fields_raw"][_MC_FIELD],
        "credential_variants": variants,
    }


def post_patch(*, journal: Dict[str, Any], result: Any, post_read: Any) -> Dict[str, Any]:
    """Bind an accepted guarded PATCH and its mandatory strict post-read; returns the generation candidate."""
    restore = journal["restore"]
    name = restore["name"]
    precondition = restore["passive_patch_precondition"]
    if not _accepted(result):
        raise ActivationRefused(
            f"The guarded patch of {name} was not accepted ({_reason(result)}); no applied evidence was "
            "recorded. Retry re-reads the Restore and re-proves every precondition."
        )
    post = strict_get(post_read, f"Restore {name}")
    if post is None:
        raise ActivationRefused(f"{name} is absent after an accepted guarded patch")
    generation = _post_mutation_generation(post, restore["uid"], result, name)
    if generation <= precondition["generation"]:
        raise ActivationRefused(f"{name} generation {generation} did not advance past the patched precondition")
    spec = post.get("spec") or {}
    if (
        spec.get(_MC_FIELD) != VELERO_BACKUP_LATEST
        or spec.get(_CREDENTIALS_FIELD) != precondition["backup_fields_raw"][_CREDENTIALS_FIELD]
        or spec.get(_RESOURCES_FIELD) != precondition["backup_fields_raw"][_RESOURCES_FIELD]
        or spec.get(_SYNC_FIELD) is not True
    ):
        raise ActivationRefused(f"{name} does not hold the patched owned spec after the accepted patch")
    require_cleanup_policy(post, restore, name)
    return {"candidate": _with_restore(journal, generation=generation)}


def pre_create(*, journal: Dict[str, Any], target_read: Any, backup_reads: Any, restores_read: Any) -> Dict[str, Any]:
    """The pre-create boundary of a one-shot Restore: the target is unowned-absent, the frozen
    Backups hold, the generated names do not collide, and the passive sync Restore (if any) is
    the one discovered for the guarded deletion.
    """
    restore = journal["restore"]
    name = restore["name"]
    require_unowned_restore_absent(target_read, name)
    revalidate_frozen_backups(journal, backup_reads)
    _evidence(
        predict_one_shot_child_names, restore["mutation_kind"], restore["controller_contract"], name, journal["backups"]
    )
    passive = find_passive_sync_restore(restores_read)
    return {"passive": passive}


def pre_delete(*, discovered: Dict[str, Any], live_read: Any) -> Dict[str, Any]:
    """The final strict read before deleting the discovered passive sync Restore, and only that object.

    Returns ``{"action": "absent"}`` when it is already gone, else the guarded DELETE
    inputs bound to that read. A replacement is never deleted.
    """
    name = discovered["metadata"]["name"]
    uid = discovered["metadata"].get("uid")
    if not _non_empty_str(uid):
        raise ActivationRefused(f"Discovered passive sync restore {name} has no readable metadata.uid")
    live = strict_get(live_read, f"Restore {name}")
    if live is None:
        return {"action": "absent", "name": name}
    if live["metadata"].get("uid") != uid:
        raise ActivationRefused(f"Passive sync restore {name} was replaced after discovery; the replacement is kept")
    if (live.get("spec") or {}).get(_SYNC_FIELD) is not True:
        raise ActivationRefused(f"{name} is no longer a {_SYNC_FIELD}=true passive Restore")
    assert_passive_restore_ready(live, name)
    revision = live["metadata"].get("resourceVersion")
    if not _non_empty_str(revision):
        raise ActivationRefused(f"{name} has no readable metadata.resourceVersion")
    return {"action": "delete", "name": name, "uid": uid, "resource_version": revision}


def post_delete(*, name: str, result: Any) -> None:
    """A guarded DELETE of the passive sync Restore is accepted, or it is already gone."""
    if _reason(result) not in ("ok", "not_found"):
        raise ActivationRefused(f"Guarded delete of passive sync restore {name} was not accepted ({_reason(result)})")


def deletion_poll(*, name: str, uid: str, live_read: Any) -> Dict[str, bool]:
    """One poll of the deleted Restore: absent, still present, or refused when replaced."""
    live = strict_get(live_read, f"Restore {name}")
    if live is None:
        return {"absent": True}
    if live["metadata"].get("uid") != uid:
        raise ActivationRefused(f"{name} was replaced while its deletion was pending")
    return {"absent": False}


def one_shot_spec(journal: Dict[str, Any]) -> Dict[str, str]:
    restore = journal["restore"]
    if restore["mutation_kind"] == "passive_restore":
        spec = {
            _MC_FIELD: VELERO_BACKUP_LATEST,
            _CREDENTIALS_FIELD: VELERO_BACKUP_SKIP,
            _RESOURCES_FIELD: VELERO_BACKUP_SKIP,
        }
    else:
        spec = dict(restore["backup_fields"])
    spec["cleanupBeforeRestore"] = restore["cleanup_before_restore"]
    return spec


def create_body(*, journal: Dict[str, Any], backup_reads: Any) -> Dict[str, Any]:
    """Re-prove the frozen Backups at the create itself and build the journaled create body."""
    revalidate_frozen_backups(journal, backup_reads)
    restore = journal["restore"]
    return {
        "body": {
            "apiVersion": RESTORE_API_VERSION,
            "kind": "Restore",
            "metadata": {"name": restore["name"], "namespace": restore["namespace"]},
            "spec": one_shot_spec(journal),
        }
    }


def post_create(
    *, journal: Dict[str, Any], result: Any, post_read: Any, deleted_passive: Optional[str] = None
) -> Dict[str, Any]:
    """Bind an accepted single-request create and its mandatory strict post-read; returns the candidate."""
    restore = journal["restore"]
    name = restore["name"]
    if not _accepted(result):
        # The outcome may be unknown (a create can land and still time out), so nothing
        # is mutated again here: the next run re-proves the hub from the journal.
        deleted = f" The passive sync Restore {deleted_passive} was deleted." if deleted_passive else ""
        raise ActivationRefused(
            f"The create of {name} was not accepted ({_reason(result)}).{deleted} No automatic rollback is "
            f"attempted. A retry re-proves the destination hub: a {name} this attempt created is refused as "
            "unowned, otherwise the create is repeated from the migration journal."
        )
    if result.get("generation_reported") is not True or not _positive_int(result.get("generation")):
        raise ActivationRefused(f"The accepted create of {name} reported no generation; no evidence is recorded")
    post = strict_get(post_read, f"Restore {name}")
    if post is None:
        raise ActivationRefused(f"{name} is absent after an accepted create")
    generation = _post_mutation_generation(post, result.get("uid"), result, name)
    require_one_shot_spec(post, journal, name)
    return {"candidate": _with_restore(journal, uid=result["uid"], generation=generation)}


def require_journaled_restore(journal: Dict[str, Any], live_read: Any) -> Dict[str, Any]:
    """The journaled Restore by strict read: its UID, generation and cleanup policy must hold."""
    restore = journal["restore"]
    live = strict_get(live_read, f"Restore {restore['name']}")
    if live is None:
        raise ActivationRefused(f"{restore['name']} recorded by the migration journal is absent")
    require_uid(live, restore["uid"], restore["name"])
    if live["metadata"].get("generation") != restore["generation"]:
        raise ActivationRefused(f"{restore['name']} spec changed since this run mutated it (generation drift)")
    require_cleanup_policy(live, restore, restore["name"])
    return live


def require_owned_spec(live: Dict[str, Any], journal: Dict[str, Any]) -> None:
    restore = journal["restore"]
    if restore["mutation_kind"] == "passive_patch":
        if (live.get("spec") or {}).get(_MC_FIELD) != VELERO_BACKUP_LATEST:
            raise ActivationRefused(
                f"{restore['name']} no longer holds the patched ManagedClusters '{VELERO_BACKUP_LATEST}'"
            )
    else:
        require_one_shot_spec(live, journal, restore["name"])


def reconcile(*, journal: Dict[str, Any], live_read: Any, backup_reads: Any) -> Dict[str, Any]:
    """The journaled mutation was accepted: the live Restore must still be exactly that object and spec."""
    restore = journal["restore"]
    require_owned_spec(require_journaled_restore(journal, live_read), journal)
    revalidate_frozen_backups(journal, backup_reads)
    return {"name": restore["name"], "generation": restore["generation"]}


def require_one_shot_spec(live: Dict[str, Any], journal: Dict[str, Any], name: str) -> None:
    spec = live.get("spec") or {}
    if any(spec.get(field) != value for field, value in one_shot_spec(journal).items()):
        raise ActivationRefused(f"{name} does not hold the journaled Backup names and cleanup policy")


def require_uid(live: Dict[str, Any], uid: Optional[str], name: str) -> None:
    if not uid or (live.get("metadata") or {}).get("uid") != uid:
        raise ActivationRefused(f"{name} is not the Restore this run journaled (UID mismatch); it is never adopted")


def require_cleanup_policy(live: Dict[str, Any], restore: Dict[str, Any], name: str) -> None:
    if (live.get("spec") or {}).get("cleanupBeforeRestore") != restore["cleanup_before_restore"]:
        raise ActivationRefused(
            f"{name} cleanupBeforeRestore differs from the journaled {restore['cleanup_before_restore']}"
        )


def _post_mutation_generation(post: Dict[str, Any], uid: Optional[str], result: Dict[str, Any], name: str) -> int:
    """The generation evidence of an accepted mutation, bound by the strict post-read (C section 5.1)."""
    require_uid(post, uid, name)
    generation = (post.get("metadata") or {}).get("generation")
    if not _positive_int(generation):
        raise ActivationRefused(f"{name} has no valid metadata.generation after the mutation")
    if result.get("generation_reported") is True and result.get("generation") != generation:
        raise ActivationRefused(f"{name} generation {generation} differs from the accepted response")
    return generation


def _accepted(result: Any) -> bool:
    """Acceptance is the module's verified classification, never http_accepted."""
    return isinstance(result, dict) and result.get("accepted") is True and result.get("reason") == "ok"


def _reason(result: Any) -> str:
    reason = result.get("reason") if isinstance(result, dict) else None
    return reason if isinstance(reason, str) and reason else "unverifiable"


def _with_restore(journal: Dict[str, Any], **fields) -> Dict[str, Any]:
    """A detached copy of the journal with `fields` set on its restore record."""
    candidate = copy.deepcopy(journal)
    candidate["restore"].update(fields)
    return candidate


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


# -- completion evidence (SecondaryActivation._wait_for_restore_completion) --------------------


def require_completion_journal(journal: Any) -> Dict[str, Any]:
    if not isinstance(journal, dict) or journal["restore"]["generation"] is None:
        raise ActivationRefused("No journaled Restore mutation to wait for; completion evidence cannot be established")
    return journal


def _completion_inputs(journal: Dict[str, Any], restore_read: Any, velero_read: Any):
    """Strict reads a completion proof needs: the journaled Restore's status and the Velero Restores."""
    restore = journal["restore"]
    live = require_journaled_restore(journal, restore_read)
    require_owned_spec(live, journal)
    status = live.get("status", {})
    if not isinstance(status, dict):
        raise ActivationRefused(f"{restore['name']} status is malformed")
    names = {field: status.get(field, "") for field in STATUS_NAME_FIELDS}
    if not all(isinstance(value, str) for value in names.values()):
        raise ActivationRefused(f"{restore['name']} status Restore names are malformed")
    return status, names, strict_list(velero_read, "Velero Restore list")


def _completion_lists(journal: Dict[str, Any], names: Dict[str, str], children: List[Dict[str, Any]], phase: Any):
    restore = journal["restore"]
    common = {
        "acm_restore_name": restore["name"],
        "acm_restore_uid": restore["uid"],
        "namespace": restore["namespace"],
        "frozen_backups": journal["backups"],
        "status_names": names,
        "namespace_restores": children,
        "acm_phase": phase,
    }
    if restore["mutation_kind"] == "passive_patch":
        return passive_patch_completion(
            restore["controller_contract"],
            precondition_status_names=restore["passive_patch_precondition"]["status_restore_names"],
            **common,
        )
    return one_shot_completion(restore["mutation_kind"], restore["controller_contract"], **common)


def _children_in_progress(journal: Dict[str, Any], children: List[Dict[str, Any]], names: Dict[str, str]) -> bool:
    """True while a child the completion proof aggregates is still running (or has no phase yet)."""
    restore = journal["restore"]
    owned = [child for child in children if _evidence(is_owned_by, child, restore["name"], restore["uid"])]
    if restore["mutation_kind"] == "passive_patch":
        owned = _evidence(passive_patch_cohort, restore["controller_contract"], owned, names)
    for child in owned:
        status = child.get("status", {})
        phase = status.get("phase") if isinstance(status, dict) else None
        if phase is None or phase in _VELERO_PENDING_PHASES:
            return True
    return False


def completion_poll(*, journal: Dict[str, Any], restore_read: Any, velero_read: Any) -> Dict[str, str]:
    """One poll of the completion wait: pending while the controller works, complete, or refused.

    A non-terminal ACM or child phase, or a child the controller has not created yet, keeps
    the wait pending; anything else refuses with no evidence written.
    """
    restore = require_completion_journal(journal)["restore"]
    status, names, children = _completion_inputs(journal, restore_read, velero_read)
    phase = status.get("phase")
    if not _evidence(acm_phase_accepts, restore["mutation_kind"], restore["controller_contract"], phase):
        if phase in _ACM_PENDING_PHASES:
            return {"state": "pending", "detail": f"phase={phase}"}
        raise ActivationRefused(f"Restore {restore['name']} reached phase {phase!r}: {status.get('lastMessage', '')}")
    if _children_in_progress(journal, children, names):
        return {"state": "pending", "detail": "Velero restores in progress"}
    try:
        _completion_lists(journal, names, children, phase)
    except MigrationEvidenceError as exc:
        if exc.code in _PENDING_CHILD_CODES:
            return {"state": "pending", "detail": exc.code}
        raise ActivationRefused(f"Restore {restore['name']} completion evidence refused ({exc})") from exc
    return {"state": "complete", "detail": f"phase={phase}"}


def activation_waiver(
    *, journal: Dict[str, Any], expected_names: List[str], journaled_at: str, requested: Any = None
) -> Dict[str, Any]:
    """The waiver covering the activation name check: the journaled one, else a matching request."""
    journaled = journal["waiver"]
    if journaled is not None:
        if requested is not None and any(requested[key] != journaled[key] for key in requested):
            raise ActivationRefused(
                "A different expected-ManagedCluster waiver is already journaled for this migration; "
                "resume with the journaled waiver"
            )
        return {"waiver": journaled if journaled["scope"] in ("activation", "both") else None}
    if requested is None or requested["scope"] not in ("activation", "both"):
        return {"waiver": None}
    record = dict(requested, flag=WAIVER_FLAG, journaled_at=journaled_at, outcome="waived")
    return {"waiver": _evidence(validate_waiver, record, sorted(expected_names), "activation")}


def _check_inputs(min_managed_clusters: Any, enforce: Any) -> Tuple[int, bool]:
    """The count floor and enforcement flag. The non-native templating of the ansible-core
    floor stringifies scalars nested in module inputs, so their Ansible spellings are read too."""
    if isinstance(min_managed_clusters, str) and min_managed_clusters.isdigit():
        min_managed_clusters = int(min_managed_clusters)
    if isinstance(enforce, str) and enforce in ("True", "False"):
        enforce = enforce == "True"
    if not isinstance(min_managed_clusters, int) or isinstance(min_managed_clusters, bool) or min_managed_clusters < 0:
        raise ActivationRefused("The ManagedCluster count floor must be a non-negative integer")
    if not isinstance(enforce, bool):
        raise ActivationRefused("The ManagedCluster name enforcement flag must be a boolean")
    return min_managed_clusters, enforce


def _managed_cluster_names(mc_read: Any) -> List[str]:
    items = strict_list(mc_read, "ManagedCluster inventory")
    names = []
    for item in items:
        name = (item.get("metadata") or {}).get("name") if isinstance(item, dict) else None
        if name and name != LOCAL_CLUSTER_NAME:
            names.append(name)
    return names


def managed_clusters_poll(
    *, mc_read: Any, expected_names: List[str], min_managed_clusters: int, enforce: bool
) -> Dict[str, str]:
    """One poll of the full-restore ManagedCluster wait (enforce False is the journaled waiver)."""
    min_managed_clusters, enforce = _check_inputs(min_managed_clusters, enforce)
    names = _managed_cluster_names(mc_read)
    count = len(names)
    missing = sorted(set(expected_names) - set(names))
    if enforce and missing:
        return {
            "state": "pending",
            "detail": f"found {count}/{min_managed_clusters} ManagedCluster(s); missing expected: {missing}",
        }
    if count >= min_managed_clusters:
        return {"state": "complete", "detail": f"found {count} ManagedCluster(s): {', '.join(names)}"}
    return {"state": "pending", "detail": f"found {count}/{min_managed_clusters} ManagedCluster(s): {names}"}


def verify_managed_clusters(
    *, mc_read: Any, expected_names: List[str], min_managed_clusters: int, enforce: bool
) -> Dict[str, str]:
    """ManagedCluster resources were actually restored (SecondaryActivation._verify_managed_clusters_restored).

    enforce False is the journaled activation waiver: it lifts only the name check, never
    the count floor.
    """
    min_managed_clusters, enforce = _check_inputs(min_managed_clusters, enforce)
    names = _managed_cluster_names(mc_read)
    count = len(names)
    missing = sorted(set(expected_names) - set(names))
    if enforce and missing:
        raise ActivationRefused(
            f"Missing expected ManagedCluster(s) after restore: {', '.join(missing)}. Found {count}: {names}"
        )
    if min_managed_clusters == 0 or count >= min_managed_clusters:
        return {"detail": f"Found {count} ManagedCluster(s) on secondary hub: {', '.join(names)}"}
    raise ActivationRefused(
        f"Expected at least {min_managed_clusters} ManagedCluster(s) after restore, but found only {count}: {names}"
    )


def _prove_completion(journal: Dict[str, Any], restore_read: Any, velero_read: Any, backup_reads: Any):
    """One fresh, non-waiting completion proof: every rule of the method-specific validator, or refused."""
    restore = journal["restore"]
    status, names, children = _completion_inputs(journal, restore_read, velero_read)
    phase = status.get("phase")
    if not _evidence(acm_phase_accepts, restore["mutation_kind"], restore["controller_contract"], phase):
        raise ActivationRefused(f"Restore {restore['name']} reached phase {phase!r}: {status.get('lastMessage', '')}")
    lists = _evidence(_completion_lists, journal, names, children, phase)
    revalidate_frozen_backups(journal, backup_reads)
    return lists


def completion_bundle(
    *,
    journal: Dict[str, Any],
    restore_read: Any,
    velero_read: Any,
    backup_reads: Any,
    now: str,
    waiver: Any = None,
) -> Dict[str, Any]:
    """The July section 1a bundle from a fresh proof, the first of two writes (completed_at follows alone).

    A partial bundle from an interrupted attempt is reconciled, never rewritten: every
    persisted child entry and the persisted fingerprint must equal what the fresh validation
    produced, and set-once timestamps keep their values.
    """
    restore = require_completion_journal(journal)["restore"]
    children = _prove_completion(journal, restore_read, velero_read, backup_reads)
    fingerprint = _evidence(restore_spec_fingerprint, journal)
    if restore["spec_fingerprint"] not in (None, fingerprint):
        raise ActivationRefused(f"{restore['name']} journaled fingerprint disagrees with the validated spec")
    for key, entries in restore["velero_restores"].items():
        fresh = {entry["name"]: entry for entry in children[key]}
        if any(fresh.get(entry["name"]) != entry for entry in entries):
            raise ActivationRefused(f"Journaled {key} child evidence disagrees with the live Velero Restores")
    bundle = _with_restore(
        journal,
        velero_restores=children,
        spec_fingerprint=fingerprint,
        backup_names_verified_at=restore["backup_names_verified_at"] or now,
        names_verified_at=None if waiver is not None else (restore["names_verified_at"] or now),
    )
    if waiver is not None:
        bundle["waiver"] = waiver
    return {"candidate": bundle}


def complete(*, journal: Dict[str, Any], now: str) -> Dict[str, Any]:
    """restore.completed_at, the strictly last activation evidence write."""
    return {"candidate": _with_restore(journal, completed_at=now)}


def revalidate_completed(
    *, journal: Dict[str, Any], restore_read: Any, velero_read: Any, backup_reads: Any
) -> Dict[str, Any]:
    """Resume over completed evidence: re-run the completion contract live, never rewrite it."""
    restore = journal["restore"]
    fresh = _prove_completion(journal, restore_read, velero_read, backup_reads)
    if _evidence(restore_spec_fingerprint, journal) != restore["spec_fingerprint"]:
        raise ActivationRefused(f"{restore['name']} journaled fingerprint disagrees with the journaled spec")
    for key, entries in restore["velero_restores"].items():
        observed = {entry["name"]: entry for entry in fresh[key]}
        for entry in entries:
            if observed.get(entry["name"]) != entry:
                raise ActivationRefused(f"Journaled {key} child {entry['name']} was replaced or changed")
    return {"name": restore["name"]}
