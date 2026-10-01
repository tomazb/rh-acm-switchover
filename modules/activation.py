"""
Secondary hub activation module for ACM switchover.
"""

# Runbook: Step 4-5 (Method 1) / F4-F5 (Method 2)

import copy
import logging
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

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
    MANAGED_CLUSTER_EXPECTATION_WAIVER_FLAG,
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
from lib.migration_evidence import (
    MigrationEvidenceError,
    controller_contract_for_acm_minor,
    go_normalize,
    normalize_backup_evidence,
    require_same_backup_generation,
    select_latest_evidence,
)
from lib.migration_journal import restore_spec_fingerprint, validate_journal_transition, validate_waiver
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
        managed_cluster_expectation_waiver: Optional[Dict] = None,
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
        # A validated waiver request (lib.validation), journaled only when it covers activation.
        self.managed_cluster_expectation_waiver = managed_cluster_expectation_waiver
        # Cache for discovered passive sync restore name
        self._passive_sync_restore_name: Optional[str] = None
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
                else:
                    self._reconcile_restore_completion()

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
        if journal["restore"]["generation"] is None:
            raise FatalError(
                "The Restore mutation step is recorded as complete, but the migration journal holds no "
                "accepted mutation; the live Restore is not owned by this run."
            )
        self._require_status_subresource()
        self._reconcile_journaled_mutation(journal)

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
        if kind == "passive_patch":
            self._evidence(predict_passive_patch_child_names, contract, restore_name, backups)
        else:
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
            self._reconcile_journaled_mutation(journal)
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
            self._reconcile_journaled_mutation(journal)
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
        if passive is not None:
            self._delete_passive_sync_restore_guarded(passive)
        else:
            logger.info("No passive sync restore found; creating %s", name)
        # The deletion wait can take minutes: re-prove the frozen Backups at the create itself.
        self._revalidate_frozen_backups(journal)

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
            # The outcome may be unknown (a create can land and still time out), so nothing
            # is mutated again here: the next run re-proves the hub from the journal.
            deleted = f" The passive sync Restore {passive['metadata']['name']} was deleted." if passive else ""
            raise FatalError(
                f"The create of {name} was not accepted ({result['reason']}).{deleted} No automatic rollback is "
                f"attempted. A retry re-proves the destination hub: a {name} this attempt created is refused as "
                "unowned, otherwise the create is repeated from the migration journal."
            )
        if not result["generation_reported"] or result["generation"] < 1:
            raise FatalError(f"The accepted create of {name} reported no generation; no evidence is recorded")
        post = self._strict_get_restore(name)
        if post is None:
            raise FatalError(f"{name} is absent after an accepted create")
        generation = self._post_mutation_generation(post, result["uid"], result, name)
        self._require_one_shot_spec(post, journal, name)
        self._persist_journal(_with_restore(journal, uid=result["uid"], generation=generation))
        logger.info("Created %s (uid %s, generation %s)", name, result["uid"], generation)

    def _reconcile_journaled_mutation(self, journal: Dict) -> None:
        """The journaled mutation was accepted: the live Restore must still be exactly that object and spec."""
        restore = journal["restore"]
        self._require_owned_spec(self._require_journaled_restore(journal), journal)
        self._revalidate_frozen_backups(journal)
        logger.info("%s already mutated by this run (generation %s)", restore["name"], restore["generation"])

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

    def _delete_passive_sync_restore_guarded(self, discovered: Dict) -> None:
        """Delete the discovered passive sync Restore, and only that object.

        The final strict read must still be the discovered UID, still sync-enabled and
        still activation-ready; the guarded DELETE then binds that read's UID and
        resourceVersion. A replacement is never deleted.
        """
        name = discovered["metadata"]["name"]
        uid = discovered["metadata"].get("uid")
        if not isinstance(uid, str) or not uid:
            raise FatalError(f"Discovered passive sync restore {name} has no readable metadata.uid")
        live = self._strict_get_restore(name)
        if live is None:
            logger.info("Passive sync restore %s already deleted", name)
            return
        if live["metadata"].get("uid") != uid:
            raise FatalError(f"Passive sync restore {name} was replaced after discovery; the replacement is kept")
        if (live.get("spec") or {}).get(SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS) is not True:
            raise FatalError(f"{name} is no longer a {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS}=true passive Restore")
        self._assert_passive_restore_ready(live, name)
        revision = live["metadata"].get("resourceVersion")
        if not isinstance(revision, str) or not revision:
            raise FatalError(f"{name} has no readable metadata.resourceVersion")
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
    def _evidence(function, *args, **kwargs):
        """Call a pure evidence helper, turning its fail-closed refusal into a FatalError."""
        try:
            return function(*args, **kwargs)
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

    def _wait_for_restore_completion(self, timeout: int = RESTORE_WAIT_TIMEOUT) -> None:
        """Wait for the journaled Restore's lane-specific completion proof, then record it, completed_at last."""
        if self.secondary.dry_run:
            logger.info("[DRY-RUN] Skipping wait for restore completion")
            return
        journal = self._require_journal_for_completion()
        if journal["restore"]["completed_at"] is not None:
            self._revalidate_completed_evidence(journal)
            return
        self._collect_and_validate_velero_children(journal, timeout)
        waiver = self._activation_waiver(journal)
        if waiver is not None:
            logger.warning(
                "Expected ManagedCluster name check waived for activation by %s (%s); the count floor still applies",
                waiver["actor"],
                waiver["reason"],
            )
        if self.method == "passive":
            self._verify_managed_clusters_restored(enforce_names=waiver is None)
        else:
            self._wait_for_managed_clusters_restored(timeout, enforce_names=waiver is None)
        # The ManagedCluster wait can take minutes: commit only a proof taken after it.
        self._record_restore_completion_evidence(journal, self._prove_completion(journal), waiver)

    def _reconcile_restore_completion(self) -> None:
        """The wait step is marked complete: its completion evidence must be journaled and still hold."""
        if self.secondary.dry_run:
            return
        journal = self._require_journal_for_completion()
        if journal["restore"]["completed_at"] is None:
            raise FatalError(
                "The Restore wait step is recorded as complete, but the migration journal holds no completion "
                "evidence; resume cannot treat the Restore as completed."
            )
        self._revalidate_completed_evidence(journal)

    def _require_journal_for_completion(self) -> Dict:
        journal = self._load_journal()
        if journal is None or journal["restore"]["generation"] is None:
            raise FatalError("No journaled Restore mutation to wait for; completion evidence cannot be established")
        return journal

    def _collect_and_validate_velero_children(self, journal: Dict, timeout: int) -> None:
        """Poll the journaled Restore until its ACM phase and Velero children prove completion.

        Each poll strictly re-reads the ACM Restore (identity, generation, owned spec,
        cleanup policy) and the complete Velero Restore list of the namespace. A
        non-terminal ACM or child phase, or a child the controller has not created yet,
        keeps waiting; anything else, and a timeout, fails with no evidence written.
        The proof is taken again before it is committed (_prove_completion).
        """
        restore = journal["restore"]

        def _poll() -> WaitConditionResult:
            status, names, children = self._read_completion_inputs(journal)
            phase = status.get("phase")
            if not self._evidence(acm_phase_accepts, restore["mutation_kind"], restore["controller_contract"], phase):
                if phase in _ACM_PENDING_PHASES:
                    return WaitConditionResult.pending(f"phase={phase}")
                raise FatalError(f"Restore {restore['name']} reached phase {phase!r}: {status.get('lastMessage', '')}")
            if self._children_in_progress(journal, children, names):
                return WaitConditionResult.pending("Velero restores in progress")
            try:
                self._completion_lists(journal, names, children, phase)
            except MigrationEvidenceError as exc:
                if exc.code in _PENDING_CHILD_CODES:
                    return WaitConditionResult.pending(exc.code)
                raise FatalError(f"Restore {restore['name']} completion evidence refused ({exc})") from exc
            return WaitConditionResult.complete(f"phase={phase}")

        if not wait_for_condition(
            f"restore {restore['name']}",
            _poll,
            timeout=timeout,
            interval=RESTORE_POLL_INTERVAL,
            fast_interval=RESTORE_FAST_POLL_INTERVAL,
            fast_timeout=RESTORE_FAST_POLL_TIMEOUT,
            logger=logger,
        ):
            raise FatalError(f"Timeout waiting for restore to complete after {timeout}s")

    def _read_completion_inputs(self, journal: Dict) -> Tuple[Dict, Dict[str, str], List[Dict]]:
        """Strict reads a completion proof needs: the journaled Restore's status and the Velero Restores."""
        restore = journal["restore"]
        live = self._require_journaled_restore(journal)
        self._require_owned_spec(live, journal)
        status = live.get("status", {})
        if not isinstance(status, dict):
            raise FatalError(f"{restore['name']} status is malformed")
        names = {field: status.get(field, "") for field in STATUS_NAME_FIELDS}
        if not all(isinstance(value, str) for value in names.values()):
            raise FatalError(f"{restore['name']} status Restore names are malformed")
        return status, names, self._strict_velero_restores()

    def _prove_completion(self, journal: Dict) -> Dict[str, List[Dict]]:
        """One fresh, non-waiting completion proof: every rule of the method-specific validator, or FatalError.

        Identity, generation, owned spec and cleanup policy; the accepted ACM phase;
        roles, cohort, provenance, owner membership and the legacy hive/cluster
        backstop; then the frozen Backups' seven-field projections.
        """
        restore = journal["restore"]
        status, names, children = self._read_completion_inputs(journal)
        phase = status.get("phase")
        if not self._evidence(acm_phase_accepts, restore["mutation_kind"], restore["controller_contract"], phase):
            raise FatalError(f"Restore {restore['name']} reached phase {phase!r}: {status.get('lastMessage', '')}")
        lists = self._evidence(self._completion_lists, journal, names, children, phase)
        self._revalidate_frozen_backups(journal)
        return lists

    def _completion_lists(
        self, journal: Dict, names: Dict[str, str], children: List[Dict], phase: str
    ) -> Dict[str, List[Dict]]:
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

    def _children_in_progress(self, journal: Dict, children: List[Dict], names: Dict[str, str]) -> bool:
        """True while a child the completion proof aggregates is still running (or has no phase yet)."""
        restore = journal["restore"]
        owned = [child for child in children if self._evidence(is_owned_by, child, restore["name"], restore["uid"])]
        if restore["mutation_kind"] == "passive_patch":
            owned = self._evidence(passive_patch_cohort, restore["controller_contract"], owned, names)
        for child in owned:
            status = child.get("status", {})
            phase = status.get("phase") if isinstance(status, dict) else None
            if phase is None or phase in _VELERO_PENDING_PHASES:
                return True
        return False

    def _require_owned_spec(self, live: Dict, journal: Dict) -> None:
        restore = journal["restore"]
        if restore["mutation_kind"] == "passive_patch":
            if (live.get("spec") or {}).get(SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME) != VELERO_BACKUP_LATEST:
                raise FatalError(
                    f"{restore['name']} no longer holds the patched ManagedClusters '{VELERO_BACKUP_LATEST}'"
                )
        else:
            self._require_one_shot_spec(live, journal, restore["name"])

    def _strict_velero_restores(self) -> List[Dict]:
        outcome = self.secondary.list_custom_resources_strict(
            group=_VELERO_GROUP, version=_VELERO_VERSION, plural=RESTORE_PLURAL, namespace=BACKUP_NAMESPACE
        )
        if not outcome.is_success:
            raise FatalError(f"Velero Restore list read failed ({outcome.status.value}: {outcome.reason})")
        return outcome.items

    def _activation_waiver(self, journal: Dict) -> Optional[Dict]:
        """The waiver covering the activation name check: the journaled one, else a matching request."""
        journaled = journal["waiver"]
        requested = self.managed_cluster_expectation_waiver
        if journaled is not None:
            if requested is not None and any(requested[key] != journaled[key] for key in requested):
                raise FatalError(
                    "A different expected-ManagedCluster waiver is already journaled for this migration; "
                    "resume with the journaled waiver"
                )
            return journaled if journaled["scope"] in ("activation", "both") else None
        if requested is None or requested["scope"] not in ("activation", "both"):
            return None
        record = dict(
            requested, flag=MANAGED_CLUSTER_EXPECTATION_WAIVER_FLAG, journaled_at=_now_iso(), outcome="waived"
        )
        return self._evidence(validate_waiver, record, list(self.expected_managed_cluster_names), "activation")

    def _record_restore_completion_evidence(
        self, journal: Dict, children: Dict[str, List[Dict]], waiver: Optional[Dict]
    ) -> None:
        """Record the July section 1a bundle, then restore.completed_at as the strictly last write.

        A partial bundle from an interrupted attempt is reconciled, never rewritten:
        every persisted child entry and the persisted fingerprint must equal what the
        fresh validation produced, and set-once timestamps keep their values.
        """
        restore = journal["restore"]
        fingerprint = self._evidence(restore_spec_fingerprint, journal)
        if restore["spec_fingerprint"] not in (None, fingerprint):
            raise FatalError(f"{restore['name']} journaled fingerprint disagrees with the validated spec")
        for key, entries in restore["velero_restores"].items():
            fresh = {entry["name"]: entry for entry in children[key]}
            if any(fresh.get(entry["name"]) != entry for entry in entries):
                raise FatalError(f"Journaled {key} child evidence disagrees with the live Velero Restores")
        now = _now_iso()
        bundle = _with_restore(
            journal,
            velero_restores=children,
            spec_fingerprint=fingerprint,
            backup_names_verified_at=restore["backup_names_verified_at"] or now,
            names_verified_at=None if waiver is not None else (restore["names_verified_at"] or now),
        )
        if waiver is not None:
            bundle["waiver"] = waiver
        self._persist_journal(bundle)
        self._persist_journal(_with_restore(bundle, completed_at=_now_iso()))
        logger.info("Recorded completion evidence for %s", restore["name"])

    def revalidate_completion_evidence(self, journal: Dict) -> None:
        """Re-run the recorded completion proof live; finalization's teardown barrier (July section 4a)."""
        self._revalidate_completed_evidence(journal)

    def _revalidate_completed_evidence(self, journal: Dict) -> None:
        """Resume over completed evidence: re-run the completion contract live, never rewrite it."""
        restore = journal["restore"]
        fresh = self._prove_completion(journal)
        if self._evidence(restore_spec_fingerprint, journal) != restore["spec_fingerprint"]:
            raise FatalError(f"{restore['name']} journaled fingerprint disagrees with the journaled spec")
        for key, entries in restore["velero_restores"].items():
            observed = {entry["name"]: entry for entry in fresh[key]}
            for entry in entries:
                if observed.get(entry["name"]) != entry:
                    raise FatalError(f"Journaled {key} child {entry['name']} was replaced or changed")
        logger.info("Completion evidence for %s revalidated", restore["name"])

    def _list_restored_managed_cluster_names(
        self,
        name_cache: Optional[Dict[str, List[str]]] = None,
        force_refresh: bool = False,
    ) -> List[str]:
        """Return non-local ManagedCluster names currently visible on the secondary hub."""
        if name_cache is not None and not force_refresh and "non_local_names" in name_cache:
            return list(name_cache["non_local_names"])

        outcome = self.secondary.list_managed_clusters_strict()
        if not outcome.is_success:
            raise FatalError(f"ManagedCluster inventory read failed ({outcome.status.value}: {outcome.reason})")
        names = []
        for mc in outcome.items:
            name = mc.get("metadata", {}).get("name")
            if name and name != LOCAL_CLUSTER_NAME:
                names.append(name)
        if name_cache is not None:
            name_cache["non_local_names"] = list(names)
        return names

    def _wait_for_managed_clusters_restored(self, timeout: int, enforce_names: bool = True) -> None:
        """Wait for full-restore ManagedCluster resources before enforcing the minimum.

        enforce_names False is the journaled activation waiver: it lifts only the name check.
        """
        enforce = self.enforce_expected_managed_cluster_names and enforce_names
        # A zero floor has nothing to wait for only when no expected name is enforced (AC25).
        if self.min_managed_clusters == 0 and not (enforce and self.expected_managed_cluster_names):
            self._verify_managed_clusters_restored(enforce_names=enforce_names)
            return

        def _poll_managed_clusters():
            non_local_clusters = self._list_restored_managed_cluster_names()
            count = len(non_local_clusters)
            missing_expected = sorted(set(self.expected_managed_cluster_names) - set(non_local_clusters))
            if enforce and missing_expected:
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
            self._verify_managed_clusters_restored(enforce_names=enforce_names)

    def _verify_managed_clusters_restored(self, enforce_names: bool = True):
        """
        Verify that ManagedCluster resources were actually restored.

        This is a sanity check to ensure the restore actually brought over
        the managed clusters before we proceed with creating a BackupSchedule.
        enforce_names False is the journaled activation waiver: it lifts only the
        name check, never the count floor.
        """
        logger.info("Verifying ManagedCluster resources were restored...")
        non_local_clusters = self._list_restored_managed_cluster_names()
        count = len(non_local_clusters)
        enforce = self.enforce_expected_managed_cluster_names and enforce_names

        missing_expected = sorted(set(self.expected_managed_cluster_names) - set(non_local_clusters))
        if enforce and missing_expected:
            raise FatalError(
                "Missing expected ManagedCluster(s) after restore: "
                f"{', '.join(missing_expected)}. Found {count}: {non_local_clusters}"
            )

        if enforce and count < self.min_managed_clusters:
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
