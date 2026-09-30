"""
Secondary hub activation module for ACM switchover.
"""

# Runbook: Step 4-5 (Method 1) / F4-F5 (Method 2)

import copy
import logging
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Dict, List, Optional

from kubernetes.client.rest import ApiException

from lib.constants import (
    ACM_NAMESPACE,
    AUTO_IMPORT_STRATEGY_DEFAULT,
    AUTO_IMPORT_STRATEGY_KEY,
    AUTO_IMPORT_STRATEGY_SYNC,
    BACKUP_NAMESPACE,
    CLEANUP_BEFORE_RESTORE_VALUE,
    CLUSTER_BACKUP_API_GROUP,
    CLUSTER_BACKUP_API_VERSION,
    CLUSTER_BACKUP_API_VERSION_FULL,
    DELETE_REQUEST_TIMEOUT,
    IMMEDIATE_IMPORT_ANNOTATION,
    IMPORT_CONTROLLER_CONFIG_CM,
    LOCAL_CLUSTER_NAME,
    MANAGED_CLUSTER_API_GROUP,
    MANAGED_CLUSTER_API_VERSION,
    MANAGED_CLUSTER_PLURAL,
    MANAGED_CLUSTER_RESTORE_NAME,
    MCE_NAMESPACE,
    RESTORE_FAST_POLL_INTERVAL,
    RESTORE_FAST_POLL_TIMEOUT,
    RESTORE_FULL_NAME,
    RESTORE_PLURAL,
    RESTORE_POLL_INTERVAL,
    RESTORE_WAIT_TIMEOUT,
    SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS,
    SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME,
    VELERO_BACKUP_LATEST,
    VELERO_BACKUP_SKIP,
)
from lib.exceptions import FatalError, SwitchoverError
from lib.gitops_detector import safe_record_gitops_markers
from lib.kube_client import KubeClient
from lib.migration_child_evidence import (
    STATUS_NAME_FIELDS,
    VELERO_RESTORE_LISTS,
    freeze_one_shot_backups,
    freeze_passive_patch_backups,
    predict_one_shot_child_names,
    predict_passive_patch_child_names,
    predict_passive_patch_credential_variants,
)
from lib.migration_evidence import (
    MigrationEvidenceError,
    controller_contract_for_acm_minor,
    go_normalize,
    normalize_backup_evidence,
    require_same_backup_generation,
    select_latest_evidence,
)
from lib.migration_journal import validate_journal_transition
from lib.run_record import RunRecord, StateStructureError
from lib.strict_read import StrictReadStatus
from lib.utils import Phase, StateManager, is_acm_version_ge, parse_acm_version
from lib.waiter import WaitConditionResult, wait_for_condition

from .restore_discovery import find_passive_sync_restore_strict, restore_messages_are_benign_already_available

logger = logging.getLogger("acm_switchover")

# Minimum number of ManagedClusters expected (excluding local-cluster)
# Set to 0 to allow switchover with only local-cluster
MIN_MANAGED_CLUSTERS = 0

AUTO_IMPORT_VERIFY_ERROR = "Unable to verify autoImportStrategy on the destination hub; verify API access and retry."

_VELERO_GROUP = "velero.io"
_VELERO_VERSION = "v1"
_VELERO_BACKUPS = "backups"
_MCH_GROUP = "operator.open-cluster-management.io"
_MCH_VERSION = "v1"
_MCH_PLURAL = "multiclusterhubs"
_RESTORE_STATUS_SUBRESOURCE = f"{RESTORE_PLURAL}/status"
_CREDENTIALS_FIELD = "veleroCredentialsBackupName"
_RESOURCES_FIELD = "veleroResourcesBackupName"
# August section 4: the three sync options a fresh passive_patch reads and the
# upstream-normalized values it requires.
_PATCH_FIELDS = (SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME, _CREDENTIALS_FIELD, _RESOURCES_FIELD)
_PATCH_NORMALIZED = dict(zip(_PATCH_FIELDS, (VELERO_BACKUP_SKIP, VELERO_BACKUP_LATEST, VELERO_BACKUP_LATEST)))
# July section 1: a full_restore names the controller's direct `latest` choice for
# each concrete field, as (resource type, frozen category, Restore spec field).
_FULL_RESTORE_SELECTIONS = (
    ("ManagedClusters", "managed_clusters", SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME),
    ("Credentials", "credentials", _CREDENTIALS_FIELD),
    ("Resources", "resources", _RESOURCES_FIELD),
)
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _with_restore(journal: Dict, **fields) -> Dict:
    """A detached copy of the journal with `fields` set on its restore record."""
    candidate = copy.deepcopy(journal)
    candidate["restore"].update(fields)
    return candidate


class SecondaryActivation:
    """Handles activation steps on secondary hub."""

    def __init__(
        self,
        secondary_client: KubeClient,
        state_manager: StateManager,
        method: str = "passive",
        activation_method: str = "patch",
        manage_auto_import_strategy: bool = False,
        old_hub_action: str = "secondary",
        min_managed_clusters: int = MIN_MANAGED_CLUSTERS,
        expected_managed_cluster_names: Optional[List[str]] = None,
        enforce_expected_managed_cluster_names: bool = False,
    ):
        self.secondary = secondary_client
        self.state = state_manager
        self.method = method
        self.activation_method = activation_method
        self.manage_auto_import_strategy = manage_auto_import_strategy
        self.old_hub_action = old_hub_action
        self.min_managed_clusters = min_managed_clusters
        self.expected_managed_cluster_names = sorted(expected_managed_cluster_names or [])
        self.enforce_expected_managed_cluster_names = enforce_expected_managed_cluster_names
        # Cache for discovered passive sync restore name
        self._passive_sync_restore_name: Optional[str] = None
        self._activation_restore_name: Optional[str] = None
        self._require_new_velero_restore_signal = False
        self._status_subresource_verified = False
        self.run_record = RunRecord(self.state)

    def _get_passive_sync_restore_name(self) -> str:
        """
        Get the name of the passive sync restore, discovering it if needed.

        This uses cached value if available, otherwise discovers the restore
        by looking for one with syncRestoreWithNewBackups = true in a strictly
        complete Restore list.

        Returns:
            The name of the passive sync restore

        Raises:
            FatalError: If discovery fails or no passive sync restore is found
        """
        if self._passive_sync_restore_name:
            return self._passive_sync_restore_name

        restore = find_passive_sync_restore_strict(self.secondary, BACKUP_NAMESPACE)
        if not restore:
            raise FatalError(
                "No sync-enabled passive Restore found on secondary hub. "
                f"Expected a Restore with spec.{SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS}=true."
            )

        self._passive_sync_restore_name = restore["metadata"]["name"]
        logger.info("Discovered passive sync restore: %s", self._passive_sync_restore_name)
        return self._passive_sync_restore_name

    def activate(self) -> bool:
        """
        Execute activation on secondary hub.

        Returns:
            True if activation completed successfully
        """
        logger.info("Starting secondary hub activation (method: %s)...", self.method)

        try:
            if self.method == "passive":
                # Method 1: Continuous Passive Restore
                with self.state.step("verify_passive_sync", logger) as should_run:
                    if should_run:
                        self._verify_passive_sync()

                # Optional: set ImportAndSync before activation when applicable
                self._maybe_set_auto_import_strategy()
                mutation_step = "activate_managed_clusters"
            else:
                # Method 2: One-Time Full Restore
                # Optional: set ImportAndSync pre-restore when applicable
                self._maybe_set_auto_import_strategy()
                mutation_step = "create_full_restore"

            # The migration journal, not the step marker, decides what the Restore
            # mutation still owes: a completed marker only skips to reconciliation.
            with self.state.step(mutation_step, logger) as should_run:
                if should_run:
                    self._apply_migration_mutation()
                else:
                    self._reconcile_migration_mutation()

            # Wait for restore to complete
            with self.state.step("wait_restore_completion", logger) as should_run:
                if should_run:
                    self._wait_for_restore_completion()

            # Apply immediate-import annotations when ImportOnly is in effect (ACM 2.14+)
            with self.state.step("apply_immediate_import_annotations", logger) as should_run:
                if should_run:
                    self._apply_immediate_import_annotations()

            logger.info("Secondary hub activation completed successfully")
            return True

        except SwitchoverError as e:
            logger.error("Secondary hub activation failed: %s", e)
            self.state.add_error(str(e), Phase.ACTIVATION.value)
            return False
        except (RuntimeError, ValueError) as e:
            logger.error("Unexpected error during activation: %s", e)
            self.state.add_error(f"Unexpected: {str(e)}", Phase.ACTIVATION.value)
            return False
        except Exception as e:
            # Log programming errors but re-raise so they're not hidden
            logger.error("Programming error during activation: %s: %s", type(e).__name__, e)
            raise

    def _verify_passive_sync(self):
        """Verify passive sync restore is up-to-date."""
        logger.info("Verifying passive sync restore status...")

        restore_name = self._get_passive_sync_restore_name()
        restore = self._strict_get_restore(restore_name)
        if restore is None:
            raise FatalError(f"{restore_name} not found on secondary hub")

        # Record GitOps markers if present
        metadata = restore.get("metadata", {})
        safe_record_gitops_markers(
            logger=logger,
            context="secondary",
            namespace=BACKUP_NAMESPACE,
            kind="Restore",
            name=restore_name,
            metadata=metadata,
        )

        self._assert_passive_restore_ready(restore, restore_name)

    # -- R4-04 migration-evidence transaction (plan Task 7) -----------------------------
    #
    # The journal is frozen and durably acknowledged through RunRecord before any Restore
    # mutation, including the preparatory passive-sync deletion; resume reuses it and
    # never refreezes. Dry-run performs the same reads and predictions and writes nothing.

    def _mutation_kind(self) -> str:
        if self.method == "passive":
            return "passive_restore" if self.activation_method == "restore" else "passive_patch"
        return "full_restore"

    def _apply_migration_mutation(self) -> None:
        """Freeze or reload the journal, then perform the kind's guarded Restore mutation."""
        kind = self._mutation_kind()
        journal = self._load_or_freeze_migration_journal(kind)
        self._activation_restore_name = journal["restore"]["name"]
        self._require_status_subresource()
        if kind == "passive_patch":
            self._apply_passive_patch_guarded(journal)
        elif kind == "passive_restore":
            self._create_passive_restore_from_journal(journal)
        else:
            self._create_full_restore_from_journal(journal)

    def _reconcile_migration_mutation(self) -> None:
        """The mutation step is marked complete: require its journaled evidence and a matching live Restore."""
        kind = self._mutation_kind()
        journal = self._load_journal()
        if journal is None:
            raise FatalError(
                "The Restore mutation step is recorded as complete, but no migration journal records its "
                "evidence. Resume cannot prove which Restore this run mutated; review the destination hub "
                "and start again with --reset-state."
            )
        self._require_journal_matches_run(journal, kind, self._live_acm_minor())
        self._activation_restore_name = journal["restore"]["name"]
        if journal["restore"]["generation"] is None:
            raise FatalError(
                "The Restore mutation step is recorded as complete, but the migration journal holds no "
                "accepted mutation; the live Restore is not owned by this run."
            )
        self._require_status_subresource()
        if kind == "passive_patch":
            self._reconcile_patched_restore(journal)
        else:
            self._reconcile_created_restore(journal)

    def _load_or_freeze_migration_journal(self, kind: str) -> Dict:
        """Return the recorded journal, or freeze a new one and persist it before any mutation."""
        minor = self._live_acm_minor()
        journal = self._load_journal()
        if journal is not None:
            self._require_journal_matches_run(journal, kind, minor)
            logger.info("Reusing migration journal %s (%s)", journal["run_id"], kind)
            return journal

        contract = self._evidence(controller_contract_for_acm_minor, minor)
        precondition: Optional[Dict] = None
        uid: Optional[str] = None
        if kind == "passive_patch":
            restore_name = self._get_passive_sync_restore_name()
            live = self._strict_get_restore(restore_name)
            if live is None:
                raise FatalError(f"{restore_name} not found before the migration freeze")
            self._assert_passive_restore_ready(live, restore_name)
            # Fresh intent (August section 4): refused before any Backup is frozen.
            precondition = self._passive_patch_precondition(live, restore_name)
            uid = live["metadata"]["uid"]
        else:
            restore_name = MANAGED_CLUSTER_RESTORE_NAME if kind == "passive_restore" else RESTORE_FULL_NAME
            self._require_unowned_restore_absent(restore_name)

        inventory = self._strict_backup_inventory()
        if kind == "passive_patch":
            backups = self._evidence(freeze_passive_patch_backups, contract, inventory, BACKUP_NAMESPACE)
            backup_fields = {SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME: VELERO_BACKUP_LATEST}
        elif kind == "passive_restore":
            backups = self._evidence(freeze_one_shot_backups, kind, contract, inventory, BACKUP_NAMESPACE)
            backup_fields = {SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME: VELERO_BACKUP_LATEST}
        else:
            concrete = self._select_full_restore_backups(inventory)
            backups = self._evidence(freeze_one_shot_backups, kind, contract, inventory, BACKUP_NAMESPACE, concrete)
            backup_fields = {field: concrete[category]["name"] for _, category, field in _FULL_RESTORE_SELECTIONS}
        if kind != "passive_patch":
            self._evidence(predict_one_shot_child_names, kind, contract, restore_name, backups)

        restore: Dict = {
            "namespace": BACKUP_NAMESPACE,
            "name": restore_name,
            "uid": uid,
            "generation": None,
            "activation_method": "full" if kind == "full_restore" else "passive",
            "mutation_kind": kind,
            "acm_minor": minor,
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
            "run_id": str(uuid.uuid4()),
            "resolved_at": _now_iso(),
            "backups": backups,
            "restore": restore,
            "cleanup": dict(_CLEANUP_NOT_STARTED, backup_fields={}),
            "post_activation": {"names_verified_at": None, "completed_at": None},
            "waiver": None,
        }
        self._evidence(validate_journal_transition, None, journal)
        frozen = ", ".join(f"{category}={backup['name']}" for category, backup in sorted(backups.items()))
        if self.secondary.dry_run:
            logger.info("[DRY-RUN] Would journal the %s migration evidence before mutation: %s", kind, frozen)
            return journal
        self._persist_journal(journal)
        logger.info("Journaled migration evidence %s (%s): %s", journal["run_id"], kind, frozen)
        return journal

    def _apply_passive_patch_guarded(self, journal: Dict) -> None:
        """Patch the passive sync Restore's ManagedClusters option to `latest` under the UID/resourceVersion guard."""
        restore = journal["restore"]
        name = restore["name"]
        if restore["generation"] is not None:
            self._reconcile_patched_restore(journal)
            return
        live = self._strict_get_restore(name)
        if live is None:
            raise FatalError(f"{name} not found before the guarded patch")
        self._require_uid(live, restore["uid"], name)
        precondition = restore["passive_patch_precondition"]
        raw_mc = (live.get("spec") or {}).get(SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME)
        if isinstance(raw_mc, str) and go_normalize(raw_mc) == VELERO_BACKUP_LATEST:
            raise FatalError(
                f"{name} already requests ManagedClusters '{raw_mc}', but the migration journal holds no accepted "
                "patch: the live `latest` is stale or unowned. Review the destination hub before any retry."
            )
        # Final pre-PATCH boundary: fresh strict reads, frozen evidence never changes.
        self._assert_passive_restore_ready(live, name)
        observed = self._passive_patch_precondition(live, name)
        if {k: v for k, v in observed.items() if k != "resource_version"} != {
            k: v for k, v in precondition.items() if k != "resource_version"
        }:
            raise FatalError(f"{name} changed since the migration freeze; the journaled precondition no longer holds")
        self._revalidate_frozen_backups(journal)
        contract = restore["controller_contract"]
        credentials = journal["backups"]["activation_credentials"]["name"]
        variants = self._evidence(
            predict_passive_patch_credential_variants, contract, self._strict_backup_inventory(), credentials
        )
        logger.info("Hive/cluster credential requests before the patch: %s", variants)
        self._evidence(predict_passive_patch_child_names, contract, name, journal["backups"])

        raw_value = precondition["backup_fields_raw"][SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME]
        result = self.secondary.json_patch_custom_resource_guarded(
            name,
            namespace=BACKUP_NAMESPACE,
            uid=restore["uid"],
            resource_version=observed["resource_version"],
            expected_managed_clusters_backup_name=raw_value,
            replacement_managed_clusters_backup_name=VELERO_BACKUP_LATEST,
        )
        if self.secondary.dry_run:
            logger.info("[DRY-RUN] Would patch %s to ManagedClusters '%s'", name, VELERO_BACKUP_LATEST)
            return
        if not result["accepted"] or result["reason"] != "ok":
            raise FatalError(
                f"The guarded patch of {name} was not accepted ({result['reason']}); no applied evidence was "
                "recorded. Retry re-reads the Restore and re-proves every precondition."
            )
        post = self._strict_get_restore(name)
        if post is None:
            raise FatalError(f"{name} is absent after an accepted guarded patch")
        generation = self._post_mutation_generation(post, restore["uid"], result, name)
        if generation <= precondition["generation"]:
            raise FatalError(f"{name} generation {generation} did not advance past the patched precondition")
        spec = post.get("spec") or {}
        if (
            spec.get(SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME) != VELERO_BACKUP_LATEST
            or spec.get(_CREDENTIALS_FIELD) != precondition["backup_fields_raw"][_CREDENTIALS_FIELD]
            or spec.get(_RESOURCES_FIELD) != precondition["backup_fields_raw"][_RESOURCES_FIELD]
            or spec.get(SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS) is not True
        ):
            raise FatalError(f"{name} does not hold the patched owned spec after the accepted patch")
        self._require_cleanup_policy(post, restore, name)
        self._persist_journal(_with_restore(journal, generation=generation))
        logger.info("Patched %s to activate managed clusters (generation %s)", name, generation)

    def _create_passive_restore_from_journal(self, journal: Dict) -> None:
        """Create the one-shot activation Restore: `latest` ManagedClusters, skipped credentials and resources."""
        self._create_one_shot_restore(journal)

    def _create_full_restore_from_journal(self, journal: Dict) -> None:
        """Create the full Restore with the journaled concrete Backup names."""
        self._create_one_shot_restore(journal)

    def _create_one_shot_restore(self, journal: Dict) -> None:
        restore = journal["restore"]
        name = restore["name"]
        if restore["uid"] is not None:
            self._reconcile_created_restore(journal)
            return
        self._require_unowned_restore_absent(name)
        self._revalidate_frozen_backups(journal)
        self._evidence(
            predict_one_shot_child_names,
            restore["mutation_kind"],
            restore["controller_contract"],
            name,
            journal["backups"],
        )

        # Only one ACM Restore may be active: remove the passive sync Restore first,
        # under its UID and resourceVersion, after the journal is durable.
        passive = find_passive_sync_restore_strict(self.secondary, BACKUP_NAMESPACE)
        snapshot: Optional[Dict] = None
        if passive is not None:
            passive_name = passive["metadata"]["name"]
            self._assert_passive_restore_ready(passive, passive_name)
            snapshot = self._build_restore_snapshot(passive)
            self._delete_passive_sync_restore_guarded(passive_name)
        else:
            logger.info("No passive sync restore found; creating %s", name)

        body = {
            "apiVersion": CLUSTER_BACKUP_API_VERSION_FULL,
            "kind": "Restore",
            "metadata": {"name": name, "namespace": BACKUP_NAMESPACE},
            "spec": self._one_shot_spec(journal),
        }
        result = self.secondary.create_restore_guarded(body, namespace=BACKUP_NAMESPACE)
        if self.secondary.dry_run:
            logger.info("[DRY-RUN] Would create %s", name)
            return
        if not result["accepted"] or result["reason"] != "ok":
            message = f"Failed to create {name} ({result['reason']}); the create is not accepted"
            if snapshot is not None:
                try:
                    self._recreate_restore_from_snapshot(snapshot)
                except Exception as rollback_error:
                    raise FatalError(
                        f"{message}. Rollback failed to recreate passive sync restore "
                        f"{snapshot['metadata']['name']}: {rollback_error}"
                    ) from rollback_error
                raise FatalError(f"{message}. Recreated passive sync restore {snapshot['metadata']['name']}.")
            raise FatalError(message)
        post = self._strict_get_restore(name)
        if post is None:
            raise FatalError(f"{name} is absent after an accepted create")
        generation = self._post_mutation_generation(post, result["uid"], result, name)
        self._require_one_shot_spec(post, journal, name)
        self._persist_journal(_with_restore(journal, uid=result["uid"], generation=generation))
        logger.info("Created %s (uid %s, generation %s)", name, result["uid"], generation)

    def _reconcile_patched_restore(self, journal: Dict) -> None:
        restore = journal["restore"]
        live = self._require_journaled_restore(journal)
        spec = live.get("spec") or {}
        if spec.get(SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME) != VELERO_BACKUP_LATEST:
            raise FatalError(f"{restore['name']} no longer holds the patched ManagedClusters '{VELERO_BACKUP_LATEST}'")
        logger.info("Guarded patch of %s already accepted (generation %s)", restore["name"], restore["generation"])

    def _reconcile_created_restore(self, journal: Dict) -> None:
        restore = journal["restore"]
        live = self._require_journaled_restore(journal)
        self._require_one_shot_spec(live, journal, restore["name"])
        logger.info("%s already created by this run (generation %s)", restore["name"], restore["generation"])

    def _require_journaled_restore(self, journal: Dict) -> Dict:
        """Strictly read the journaled Restore and require its UID, generation and cleanup policy."""
        restore = journal["restore"]
        live = self._strict_get_restore(restore["name"])
        if live is None:
            raise FatalError(f"{restore['name']} recorded by the migration journal is absent")
        self._require_uid(live, restore["uid"], restore["name"])
        if live["metadata"].get("generation") != restore["generation"]:
            raise FatalError(f"{restore['name']} spec changed since this run mutated it (generation drift)")
        self._require_cleanup_policy(live, restore, restore["name"])
        return live

    def _one_shot_spec(self, journal: Dict) -> Dict[str, str]:
        restore = journal["restore"]
        if restore["mutation_kind"] == "passive_restore":
            spec = {
                SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME: VELERO_BACKUP_LATEST,
                _CREDENTIALS_FIELD: VELERO_BACKUP_SKIP,
                _RESOURCES_FIELD: VELERO_BACKUP_SKIP,
            }
        else:
            spec = dict(restore["backup_fields"])
        spec["cleanupBeforeRestore"] = restore["cleanup_before_restore"]
        return spec

    def _require_one_shot_spec(self, live: Dict, journal: Dict, name: str) -> None:
        spec = live.get("spec") or {}
        expected = self._one_shot_spec(journal)
        if any(spec.get(field) != value for field, value in expected.items()):
            raise FatalError(f"{name} does not hold the journaled Backup names and cleanup policy")

    def _post_mutation_generation(self, post: Dict, uid: str, result: Dict, name: str) -> int:
        """The generation evidence of an accepted mutation, bound by the strict post-read (C section 5.1)."""
        self._require_uid(post, uid, name)
        generation = post["metadata"].get("generation")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
            raise FatalError(f"{name} has no valid metadata.generation after the mutation")
        if result["generation_reported"] and result["generation"] != generation:
            raise FatalError(f"{name} generation {generation} differs from the accepted response")
        return generation

    def _passive_patch_precondition(self, live: Dict, name: str) -> Dict:
        """Build the August section 4 fresh-intent precondition from a strict read, or refuse."""
        metadata = live.get("metadata") or {}
        spec = live.get("spec")
        status = live.get("status", {})
        uid, revision, generation = metadata.get("uid"), metadata.get("resourceVersion"), metadata.get("generation")
        if not isinstance(uid, str) or not uid or not isinstance(revision, str) or not revision:
            raise FatalError(f"{name} has no readable metadata.uid/resourceVersion")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
            raise FatalError(f"{name} has no valid metadata.generation")
        if not isinstance(spec, dict) or spec.get(SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS) is not True:
            raise FatalError(f"{name} is not a {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS}=true passive Restore")
        raw = {field: spec.get(field) for field in _PATCH_FIELDS}
        if not all(isinstance(value, str) and value for value in raw.values()):
            raise FatalError(f"{name} does not carry all three Backup name options")
        normalized = {field: go_normalize(value) for field, value in raw.items()}
        if normalized != _PATCH_NORMALIZED:
            raise FatalError(
                f"{name} Backup options are not the skip/latest/latest sync contract required before activation"
            )
        if not isinstance(status, dict):
            raise FatalError(f"{name} status is malformed")
        names = {field: status.get(field, "") for field in STATUS_NAME_FIELDS}
        if not all(isinstance(value, str) for value in names.values()):
            raise FatalError(f"{name} status Restore names are malformed")
        if names[STATUS_NAME_FIELDS[0]] != "":
            raise FatalError(
                f"{name} already publishes {STATUS_NAME_FIELDS[0]}={names[STATUS_NAME_FIELDS[0]]}; fresh "
                "passive activation requires it to be empty"
            )
        if spec.get("cleanupBeforeRestore") != CLEANUP_BEFORE_RESTORE_VALUE:
            raise FatalError(f"{name} cleanupBeforeRestore is not {CLEANUP_BEFORE_RESTORE_VALUE}")
        return {
            "generation": generation,
            "resource_version": revision,
            "backup_fields_raw": raw,
            "backup_fields_normalized": normalized,
            "status_restore_names": names,
        }

    def _select_full_restore_backups(self, inventory: List[Dict]) -> Dict[str, Dict]:
        """The controller's direct `latest` choice for the three concrete full_restore fields, one generation."""
        concrete = {}
        for resource_type, category, _ in _FULL_RESTORE_SELECTIONS:
            decision, evidence = self._evidence(select_latest_evidence, inventory, resource_type, BACKUP_NAMESPACE)
            if decision != "selected" or evidence is None:
                raise FatalError(f"No {resource_type} Backup can be selected for the full restore")
            concrete[category] = evidence
        self._evidence(require_same_backup_generation, [backup["name"] for backup in concrete.values()])
        return concrete

    def _revalidate_frozen_backups(self, journal: Dict) -> None:
        """Strictly re-read every frozen Backup and require its complete seven-field projection."""
        for category, frozen in sorted(journal["backups"].items()):
            outcome = self.secondary.get_custom_resource_strict(
                group=_VELERO_GROUP,
                version=_VELERO_VERSION,
                plural=_VELERO_BACKUPS,
                name=frozen["name"],
                namespace=BACKUP_NAMESPACE,
            )
            if not outcome.is_success:
                raise FatalError(
                    f"Frozen {category} Backup {frozen['name']} could not be re-read "
                    f"({outcome.status.value}: {outcome.reason})"
                )
            observed = self._evidence(normalize_backup_evidence, outcome.resource, BACKUP_NAMESPACE)
            if observed != frozen:
                raise FatalError(
                    f"Frozen {category} Backup {frozen['name']} was replaced or its status drifted since the freeze"
                )

    def _strict_backup_inventory(self) -> List[Dict]:
        outcome = self.secondary.list_custom_resources_strict(
            group=_VELERO_GROUP, version=_VELERO_VERSION, plural=_VELERO_BACKUPS, namespace=BACKUP_NAMESPACE
        )
        if not outcome.is_success:
            raise FatalError(f"Velero Backup inventory read failed ({outcome.status.value}: {outcome.reason})")
        return outcome.items

    def _strict_get_restore(self, name: str) -> Optional[Dict]:
        """Strictly GET an ACM Restore: the object, None for a proven 404, or FatalError."""
        outcome = self.secondary.get_custom_resource_strict(
            group=CLUSTER_BACKUP_API_GROUP,
            version=CLUSTER_BACKUP_API_VERSION,
            plural=RESTORE_PLURAL,
            name=name,
            namespace=BACKUP_NAMESPACE,
        )
        if outcome.status is StrictReadStatus.OBJECT_ABSENT:
            return None
        if not outcome.is_success:
            raise FatalError(f"Restore {name} could not be read ({outcome.status.value}: {outcome.reason})")
        return outcome.resource

    def _require_unowned_restore_absent(self, name: str) -> None:
        if self._strict_get_restore(name) is not None:
            raise FatalError(
                f"Restore {name} already exists, but this run's migration journal never recorded creating it; "
                "it is never adopted. Review and delete it, then retry."
            )

    def _delete_passive_sync_restore_guarded(self, name: str) -> None:
        live = self._strict_get_restore(name)
        if live is None:
            logger.info("Passive sync restore %s already deleted", name)
            return
        metadata = live["metadata"]
        uid, revision = metadata.get("uid"), metadata.get("resourceVersion")
        if not isinstance(uid, str) or not uid or not isinstance(revision, str) or not revision:
            raise FatalError(f"{name} has no readable metadata.uid/resourceVersion")
        logger.info("Deleting passive sync restore %s before creating the activation restore", name)
        result = self.secondary.delete_restore_guarded(
            name, namespace=BACKUP_NAMESPACE, uid=uid, resource_version=revision, timeout_seconds=DELETE_REQUEST_TIMEOUT
        )
        if self.secondary.dry_run:
            return
        if result["reason"] not in ("ok", "not_found"):
            raise FatalError(f"Guarded delete of passive sync restore {name} was not accepted ({result['reason']})")
        self._wait_for_restore_absent(name, uid)

    def _wait_for_restore_absent(self, name: str, uid: str, timeout: int = RESTORE_WAIT_TIMEOUT) -> None:
        """Poll strict reads until the deleted Restore is gone; a replacement or read failure stops the wait."""

        def _poll() -> WaitConditionResult:
            live = self._strict_get_restore(name)
            if live is None:
                return WaitConditionResult.complete("deleted")
            if live["metadata"].get("uid") != uid:
                raise FatalError(f"{name} was replaced while its deletion was pending")
            return WaitConditionResult.pending("still present")

        if not wait_for_condition(
            f"deletion of restore {name}",
            _poll,
            timeout=timeout,
            interval=RESTORE_POLL_INTERVAL,
            fast_interval=RESTORE_FAST_POLL_INTERVAL,
            fast_timeout=RESTORE_FAST_POLL_TIMEOUT,
            logger=logger,
        ):
            raise FatalError(f"Timeout waiting for deletion of restore {name} after {timeout}s")

    def _require_status_subresource(self) -> None:
        """First use: generation guards spec changes only while the Restore CRD serves the status subresource."""
        if self._status_subresource_verified:
            return
        outcome = self.secondary.api_resource_served(
            CLUSTER_BACKUP_API_GROUP, CLUSTER_BACKUP_API_VERSION, _RESTORE_STATUS_SUBRESOURCE
        )
        if outcome.status is StrictReadStatus.CRD_ABSENT:
            raise FatalError(
                "The ACM Restore CRD does not serve the status subresource; metadata.generation cannot guard "
                "its spec, so migration evidence cannot be established"
            )
        if not outcome.is_success:
            raise FatalError(f"The ACM Restore status subresource could not be verified ({outcome.reason})")
        self._status_subresource_verified = True

    def _live_acm_minor(self) -> str:
        """The destination hub's live ACM minor, read strictly from its MultiClusterHub."""
        outcome = self.secondary.list_custom_resources_strict(
            group=_MCH_GROUP, version=_MCH_VERSION, plural=_MCH_PLURAL, namespace=ACM_NAMESPACE
        )
        if not outcome.is_success:
            raise FatalError(f"MultiClusterHub read failed ({outcome.status.value}: {outcome.reason})")
        if len(outcome.items) != 1:
            raise FatalError(f"Expected exactly one MultiClusterHub in {ACM_NAMESPACE}, found {len(outcome.items)}")
        status = outcome.items[0].get("status")
        parsed = parse_acm_version(status.get("currentVersion") if isinstance(status, dict) else None)
        if parsed is None:
            raise FatalError("The MultiClusterHub reports no parseable status.currentVersion")
        minor = f"{parsed[0]}.{parsed[1]}"
        self._evidence(controller_contract_for_acm_minor, minor)
        return minor

    def _require_journal_matches_run(self, journal: Dict, kind: str, minor: str) -> None:
        restore = journal["restore"]
        if restore["mutation_kind"] != kind:
            raise FatalError(
                f"The migration journal records a {restore['mutation_kind']} transaction, but this run requests "
                f"{kind}; resume with the original activation method"
            )
        if restore["acm_minor"] != minor:
            raise FatalError(
                f"The migration journal was frozen on ACM {restore['acm_minor']}, but the destination hub now runs "
                f"ACM {minor}; the controller contract cannot change mid-transaction"
            )

    def _require_uid(self, live: Dict, uid: Optional[str], name: str) -> None:
        if not uid or live.get("metadata", {}).get("uid") != uid:
            raise FatalError(f"{name} is not the Restore this run journaled (UID mismatch); it is never adopted")

    def _require_cleanup_policy(self, live: Dict, restore: Dict, name: str) -> None:
        if (live.get("spec") or {}).get("cleanupBeforeRestore") != restore["cleanup_before_restore"]:
            raise FatalError(
                f"{name} cleanupBeforeRestore differs from the journaled {restore['cleanup_before_restore']}"
            )

    def _load_journal(self) -> Optional[Dict]:
        try:
            return self.run_record.migration_backups()
        except (MigrationEvidenceError, StateStructureError) as exc:
            raise FatalError(f"The recorded migration journal is invalid and blocks activation: {exc}") from exc

    def _persist_journal(self, candidate: Dict) -> None:
        """Durably record a journal transition; a failure stops the run before the next mutation."""
        if self.secondary.dry_run:
            raise FatalError("Refusing to write migration evidence in dry-run mode")
        try:
            self.run_record.record_migration_backups(candidate)
        except MigrationEvidenceError as exc:
            raise FatalError(f"The migration journal transition was refused: {exc}") from exc
        except OSError as exc:
            raise FatalError(f"The migration journal could not be recorded durably: {exc}") from exc

    @staticmethod
    def _evidence(function, *args):
        """Call a pure evidence helper, turning its fail-closed refusal into a FatalError."""
        try:
            return function(*args)
        except MigrationEvidenceError as exc:
            raise FatalError(f"Migration evidence refused ({exc})") from exc

    def _assert_passive_restore_ready(self, restore: Dict, restore_name: str) -> None:
        """Raise FatalError unless the passive-sync restore is in an activation-ready phase.

        Shared by the verify_passive_sync step and the activation paths' resume
        re-validation (Thermos R2-M2): a crash between the verify step completing
        and activation completing must not let a resumed run activate against a
        degraded restore.
        """
        status = restore.get("status", {})
        phase = status.get("phase", "unknown")
        message = status.get("lastMessage", "")

        # "Enabled" = continuous sync running
        # "Finished"/"Completed" = initial sync completed successfully (also valid for activation)
        # "Running" = actively syncing a new backup (transient, restore still functional)
        if phase in ("Enabled", "Finished", "Completed", "Running"):
            logger.info("Passive sync verified (%s): %s", phase, message)
            return
        if phase == "FinishedWithErrors":
            messages = status.get("messages", [])
            if restore_messages_are_benign_already_available(messages):
                logger.warning(
                    "Passive sync restore %s in %s state but all errors are"
                    " 'already available' clusters (expected for consecutive"
                    " switchovers). Proceeding.",
                    restore_name,
                    phase,
                )
                return
        raise FatalError(f"Passive sync restore not ready: {phase} - {message}")

    @staticmethod
    def _build_restore_snapshot(restore: Dict) -> Dict:
        """Build a minimal restore body that can be safely recreated."""
        metadata = restore.get("metadata", {})
        restore_name = metadata.get("name")
        if not restore_name:
            raise FatalError("Passive sync restore missing metadata.name")

        snapshot_metadata = {
            "name": restore_name,
            "namespace": metadata.get("namespace", BACKUP_NAMESPACE),
        }
        labels = metadata.get("labels")
        annotations = metadata.get("annotations")
        if labels:
            snapshot_metadata["labels"] = labels
        if annotations:
            snapshot_metadata["annotations"] = annotations

        return {
            "apiVersion": CLUSTER_BACKUP_API_VERSION_FULL,
            "kind": "Restore",
            "metadata": snapshot_metadata,
            "spec": restore.get("spec", {}),
        }

    def _recreate_restore_from_snapshot(self, restore_snapshot: Dict) -> None:
        """Best-effort rollback helper for recreate-on-failure semantics."""
        restore_name = restore_snapshot.get("metadata", {}).get("name", "unknown")
        try:
            self.secondary.create_custom_resource(
                group=CLUSTER_BACKUP_API_GROUP,
                version=CLUSTER_BACKUP_API_VERSION,
                plural=RESTORE_PLURAL,
                body=restore_snapshot,
                namespace=BACKUP_NAMESPACE,
            )
            logger.warning(
                "Recreated passive sync restore %s after activation restore creation failure",
                restore_name,
            )
        except ApiException as e:
            if getattr(e, "status", None) == 409:
                logger.warning(
                    "Passive sync restore %s already exists during rollback recreation; surfacing conflict",
                    restore_name,
                )
            raise

    def _maybe_set_auto_import_strategy(self) -> None:
        """If requested, set ImportAndSync on secondary for ACM 2.14+ with existing clusters."""
        try:
            version = self.run_record.hub_facts().secondary_version
            if not is_acm_version_ge(version, "2.14.0"):
                return
            # Count non-local clusters
            mcs = self.secondary.list_custom_resources(
                group=MANAGED_CLUSTER_API_GROUP,
                version=MANAGED_CLUSTER_API_VERSION,
                plural=MANAGED_CLUSTER_PLURAL,
            )
            has_non_local = any(mc.get("metadata", {}).get("name") != LOCAL_CLUSTER_NAME for mc in mcs)
            if not has_non_local:
                return
            if self.old_hub_action != "secondary":
                logger.info(
                    "Skipping autoImportStrategy change (old hub action is '%s', not intended for switchback)",
                    self.old_hub_action,
                )
                return
            # Determine current strategy
            cm = self.secondary.get_configmap(MCE_NAMESPACE, IMPORT_CONTROLLER_CONFIG_CM)
            current = (cm or {}).get("data", {}).get(AUTO_IMPORT_STRATEGY_KEY, "default")
            if current == AUTO_IMPORT_STRATEGY_SYNC:
                return
            if not self.manage_auto_import_strategy:
                logger.info(
                    "Detect-only: destination hub has existing clusters with default autoImportStrategy; "
                    "use --manage-auto-import-strategy to set %s temporarily.",
                    AUTO_IMPORT_STRATEGY_SYNC,
                )
                return
            logger.info(
                "Setting autoImportStrategy=%s on destination hub (temporary)",
                AUTO_IMPORT_STRATEGY_SYNC,
            )
            self.secondary.create_or_patch_configmap(
                namespace=MCE_NAMESPACE,
                name=IMPORT_CONTROLLER_CONFIG_CM,
                data={AUTO_IMPORT_STRATEGY_KEY: AUTO_IMPORT_STRATEGY_SYNC},
            )
            self.run_record.record_auto_import_override()
        except Exception as e:
            if self.manage_auto_import_strategy:
                raise SwitchoverError(f"Failed to manage autoImportStrategy on destination hub: {e}") from e
            logger.warning("Unable to manage auto-import strategy: %s", e)

    def _get_auto_import_strategy(self) -> str:
        """Return the autoImportStrategy value or 'default' when not configured."""
        try:
            configmap = self.secondary.get_configmap_advisory(MCE_NAMESPACE, IMPORT_CONTROLLER_CONFIG_CM)
        except Exception as exc:
            raise FatalError(AUTO_IMPORT_VERIFY_ERROR) from exc

        if configmap is None:
            return "default"

        if not isinstance(configmap, Mapping):
            raise FatalError(AUTO_IMPORT_VERIFY_ERROR)

        metadata = configmap.get("metadata")
        data = configmap.get("data")
        if (
            not isinstance(metadata, Mapping)
            or metadata.get("name") != IMPORT_CONTROLLER_CONFIG_CM
            or metadata.get("namespace") != MCE_NAMESPACE
            or (data is not None and not isinstance(data, Mapping))
        ):
            raise FatalError(AUTO_IMPORT_VERIFY_ERROR)

        data = data or {}
        strategy = data.get(AUTO_IMPORT_STRATEGY_KEY, "")
        return strategy or "default"

    def _apply_immediate_import_annotations(self) -> None:
        """Apply immediate-import annotations when ImportOnly is in effect (ACM 2.14+)."""
        version = self.run_record.hub_facts().secondary_version
        if not is_acm_version_ge(version, "2.14.0"):
            logger.info("Skipping immediate-import annotations (ACM %s < 2.14)", version)
            return

        strategy = self._get_auto_import_strategy()
        if strategy not in ("default", AUTO_IMPORT_STRATEGY_DEFAULT):
            logger.info(
                "Skipping immediate-import annotations (autoImportStrategy=%s)",
                strategy,
            )
            return

        managed_clusters = self.secondary.list_custom_resources(
            group=MANAGED_CLUSTER_API_GROUP,
            version=MANAGED_CLUSTER_API_VERSION,
            plural=MANAGED_CLUSTER_PLURAL,
        )

        non_local_clusters = [mc for mc in managed_clusters if mc.get("metadata", {}).get("name") != LOCAL_CLUSTER_NAME]
        if not non_local_clusters:
            logger.info("No non-local ManagedClusters found; skipping immediate-import annotations")
            return

        updated = 0
        failures = []
        for mc in non_local_clusters:
            name = mc.get("metadata", {}).get("name")
            if not name:
                continue
            annotations = mc.get("metadata", {}).get("annotations", {}) or {}
            annotation_value = annotations.get(IMMEDIATE_IMPORT_ANNOTATION)
            if annotation_value == "":
                continue
            if self._reset_immediate_import_annotation(name, annotation_value):
                updated += 1
            else:
                failures.append(name)

        if updated:
            logger.info("Applied immediate-import annotations to %s ManagedCluster(s)", updated)
        elif not failures:
            logger.info("All ManagedClusters already had immediate-import annotations")

        if failures:
            message = (
                "Failed to update immediate-import annotation on "
                f"{len(failures)} ManagedCluster(s): {', '.join(sorted(failures))}"
            )
            logger.warning(message)
            raise FatalError(message)

    def _reset_immediate_import_annotation(self, cluster_name: str, current_value: Optional[str]) -> bool:
        """Ensure the immediate-import annotation is set to empty string for the given cluster."""
        try:
            if current_value not in (None, ""):
                logger.debug(
                    "Clearing existing immediate-import annotation value '%s' on %s",
                    current_value,
                    cluster_name,
                )
                # Remove the annotation first so the controller can re-process it
                self.secondary.patch_managed_cluster(
                    name=cluster_name,
                    patch={"metadata": {"annotations": {IMMEDIATE_IMPORT_ANNOTATION: None}}},
                )

            self.secondary.patch_managed_cluster(
                name=cluster_name,
                patch={"metadata": {"annotations": {IMMEDIATE_IMPORT_ANNOTATION: ""}}},
            )
            return True
        except ApiException as exc:
            logger.warning(
                "Failed to annotate %s with immediate-import (status=%s): %s",
                cluster_name,
                getattr(exc, "status", "unknown"),
                exc,
            )
            return False
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to annotate %s with immediate-import: %s", cluster_name, exc)
            return False

    def _wait_for_restore_completion(self, timeout: int = RESTORE_WAIT_TIMEOUT):
        """Wait for restore to complete and verify managed clusters are restored."""

        # Skip waiting in dry-run mode since no actual activation occurred
        if self.secondary.dry_run:
            logger.info("[DRY-RUN] Skipping wait for restore completion")
            return

        restore_name = self._activation_restore_name
        if not restore_name:
            if self.method == "passive":
                if self.activation_method == "restore":
                    restore_name = MANAGED_CLUSTER_RESTORE_NAME
                else:
                    restore_name = self._get_passive_sync_restore_name()
            else:
                restore_name = RESTORE_FULL_NAME
            self._activation_restore_name = restore_name

        def _poll_restore():
            restore = self.secondary.get_custom_resource(
                group=CLUSTER_BACKUP_API_GROUP,
                version=CLUSTER_BACKUP_API_VERSION,
                plural=RESTORE_PLURAL,
                name=restore_name,
                namespace=BACKUP_NAMESPACE,
            )

            if not restore:
                raise FatalError(f"Restore {restore_name} disappeared during wait")

            status = restore.get("status", {})
            phase = status.get("phase", "unknown")
            message = status.get("lastMessage", "")

            # For passive sync, "Enabled" means the restore is actively syncing - this is the success state
            # For full restore, "Finished"/"Completed" mean the restore completed
            if self.method == "passive" and phase == "Enabled":
                return WaitConditionResult.complete("phase=Enabled")
            if phase in ("Finished", "Completed"):
                return WaitConditionResult.complete(f"phase={phase}")
            if phase == "FinishedWithErrors":
                messages = status.get("messages", [])
                if restore_messages_are_benign_already_available(messages):
                    logger.warning(
                        "Restore %s reported FinishedWithErrors but all errors are"
                        " 'already available' clusters (expected for consecutive"
                        " switchovers): %s",
                        restore_name,
                        "; ".join(messages),
                    )
                    return WaitConditionResult.complete("phase=FinishedWithErrors (clusters already available)")
                raise FatalError(f"Restore failed: {phase} - {message}")
            if phase in ("Error", "Failed", "PartiallyFailed"):
                raise FatalError(f"Restore failed: {phase} - {message}")

            return WaitConditionResult.pending(f"phase={phase}")

        completed = wait_for_condition(
            f"restore {restore_name}",
            _poll_restore,
            timeout=timeout,
            interval=RESTORE_POLL_INTERVAL,
            fast_interval=RESTORE_FAST_POLL_INTERVAL,
            fast_timeout=RESTORE_FAST_POLL_TIMEOUT,
            logger=logger,
        )

        if not completed:
            raise FatalError(f"Timeout waiting for restore to complete after {timeout}s")

        # For passive sync, wait for the managed clusters Velero restore to actually complete.
        # For full restore, the ACM Restore completion is the terminal signal; then enforce
        # the ManagedCluster count contract exposed by --min-managed-clusters.
        if self.method == "passive":
            # Skip waiting for Velero restore in dry-run mode since the patch wasn't applied
            if self.secondary.dry_run:
                logger.info("[DRY-RUN] Skipping wait for Velero managed clusters restore")
            else:
                self._wait_for_managed_clusters_velero_restore(restore_name, timeout)
        else:
            self._wait_for_managed_clusters_restored(timeout)

    def _wait_for_managed_clusters_velero_restore(self, restore_name: str, timeout: int = 300):
        """
        Wait for the Velero managed clusters restore to complete.

        This is critical because:
        1. The ACM restore controller patches the passive sync restore
        2. This triggers creation of a Velero restore for managed clusters
        3. The Velero restore needs to complete before ManagedCluster resources exist
        4. Only after this can we safely create a BackupSchedule

        If we create a BackupSchedule before the managed clusters restore completes,
        the new backups won't contain the managed clusters!
        """
        logger.info("Waiting for managed clusters Velero restore to complete...")
        previous_velero_restore_name = (
            self.run_record.pre_activation_velero_restore() if self._require_new_velero_restore_signal else None
        )

        def _poll_velero_restore():
            # Get the ACM restore to find the Velero restore name
            restore = self.secondary.get_custom_resource(
                group=CLUSTER_BACKUP_API_GROUP,
                version=CLUSTER_BACKUP_API_VERSION,
                plural=RESTORE_PLURAL,
                name=restore_name,
                namespace=BACKUP_NAMESPACE,
            )

            if not restore:
                return WaitConditionResult.pending("ACM restore not found")

            status = restore.get("status", {})
            velero_mc_restore_name = status.get("veleroManagedClustersRestoreName")

            if not velero_mc_restore_name:
                return WaitConditionResult.pending("Velero managed clusters restore not yet created")

            if previous_velero_restore_name and velero_mc_restore_name == previous_velero_restore_name:
                return WaitConditionResult.pending("waiting for new Velero managed clusters restore")

            # Check the Velero restore status
            velero_restore = self.secondary.get_custom_resource(
                group="velero.io",
                version="v1",
                plural="restores",
                name=velero_mc_restore_name,
                namespace=BACKUP_NAMESPACE,
            )

            if not velero_restore:
                return WaitConditionResult.pending(f"waiting for Velero restore {velero_mc_restore_name}")

            velero_phase = velero_restore.get("status", {}).get("phase", "unknown")

            if velero_phase == "Completed":
                items_restored = velero_restore.get("status", {}).get("progress", {}).get("itemsRestored", 0)
                logger.info(
                    "Velero managed clusters restore completed: %s items restored",
                    items_restored,
                )
                return WaitConditionResult.complete(f"phase=Completed ({items_restored} items)")
            if velero_phase in ("Failed", "PartiallyFailed"):
                raise FatalError(f"Velero managed clusters restore failed: {velero_phase}")

            return WaitConditionResult.pending(f"phase={velero_phase}")

        completed = wait_for_condition(
            "Velero managed clusters restore",
            _poll_velero_restore,
            timeout=timeout,
            interval=RESTORE_POLL_INTERVAL,
            fast_interval=RESTORE_FAST_POLL_INTERVAL,
            fast_timeout=RESTORE_FAST_POLL_TIMEOUT,
            logger=logger,
        )

        if not completed:
            raise FatalError(f"Timeout waiting for Velero managed clusters restore after {timeout}s")

        # Verify ManagedCluster resources actually exist
        self._verify_managed_clusters_restored()

    def _list_restored_managed_cluster_names(
        self,
        name_cache: Optional[Dict[str, List[str]]] = None,
        force_refresh: bool = False,
    ) -> List[str]:
        """Return non-local ManagedCluster names currently visible on the secondary hub."""
        if name_cache is not None and not force_refresh and "non_local_names" in name_cache:
            return list(name_cache["non_local_names"])

        managed_clusters = self.secondary.list_custom_resources(
            group=MANAGED_CLUSTER_API_GROUP,
            version=MANAGED_CLUSTER_API_VERSION,
            plural=MANAGED_CLUSTER_PLURAL,
        )
        names = []
        for mc in managed_clusters:
            name = mc.get("metadata", {}).get("name")
            if name and name != LOCAL_CLUSTER_NAME:
                names.append(name)
        if name_cache is not None:
            name_cache["non_local_names"] = list(names)
        return names

    def _wait_for_managed_clusters_restored(self, timeout: int) -> None:
        """Wait for full-restore ManagedCluster resources before enforcing the minimum."""
        # A zero floor has nothing to wait for only when no expected name is enforced (AC25).
        if self.min_managed_clusters == 0 and not (
            self.enforce_expected_managed_cluster_names and self.expected_managed_cluster_names
        ):
            self._verify_managed_clusters_restored()
            return

        def _poll_managed_clusters():
            non_local_clusters = self._list_restored_managed_cluster_names()
            count = len(non_local_clusters)
            missing_expected = sorted(set(self.expected_managed_cluster_names) - set(non_local_clusters))
            if self.enforce_expected_managed_cluster_names and missing_expected:
                return WaitConditionResult.pending(
                    f"found {count}/{self.min_managed_clusters} ManagedCluster(s); "
                    f"missing expected: {missing_expected}"
                )
            if count >= self.min_managed_clusters:
                return WaitConditionResult.complete(f"found {count} ManagedCluster(s): {', '.join(non_local_clusters)}")
            return WaitConditionResult.pending(
                f"found {count}/{self.min_managed_clusters} ManagedCluster(s): {non_local_clusters}"
            )

        completed = wait_for_condition(
            "full-restore ManagedCluster resources",
            _poll_managed_clusters,
            timeout=timeout,
            interval=RESTORE_POLL_INTERVAL,
            fast_interval=RESTORE_FAST_POLL_INTERVAL,
            fast_timeout=RESTORE_FAST_POLL_TIMEOUT,
            logger=logger,
        )

        if not completed:
            self._verify_managed_clusters_restored()

    def _verify_managed_clusters_restored(self):
        """
        Verify that ManagedCluster resources were actually restored.

        This is a sanity check to ensure the restore actually brought over
        the managed clusters before we proceed with creating a BackupSchedule.
        """
        logger.info("Verifying ManagedCluster resources were restored...")
        non_local_clusters = self._list_restored_managed_cluster_names()
        count = len(non_local_clusters)

        missing_expected = sorted(set(self.expected_managed_cluster_names) - set(non_local_clusters))
        if self.enforce_expected_managed_cluster_names and missing_expected:
            raise FatalError(
                "Missing expected ManagedCluster(s) after restore: "
                f"{', '.join(missing_expected)}. Found {count}: {non_local_clusters}"
            )

        if self.enforce_expected_managed_cluster_names and count < self.min_managed_clusters:
            raise FatalError(
                f"Expected {self.min_managed_clusters} ManagedCluster(s) from primary preflight after restore, "
                f"but found only {count}: {non_local_clusters}"
            )

        if self.min_managed_clusters == 0:
            if count == 0:
                logger.warning(
                    "No non-local ManagedClusters found after restore "
                    "(managed cluster count check is informational only; use --min-managed-clusters N "
                    "to enforce a minimum). This may be expected if no clusters were imported on the "
                    "primary hub."
                )
            else:
                logger.info(
                    "Found %s ManagedCluster(s) on secondary hub: %s",
                    count,
                    ", ".join(non_local_clusters),
                )
        elif count >= self.min_managed_clusters:
            logger.info(
                "Found %s ManagedCluster(s) on secondary hub (minimum required: %s): %s",
                count,
                self.min_managed_clusters,
                ", ".join(non_local_clusters),
            )
        else:
            raise FatalError(
                f"Expected at least {self.min_managed_clusters} ManagedCluster(s) after restore, "
                f"but found only {count}: {non_local_clusters}"
            )
