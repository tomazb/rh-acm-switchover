"""Input validation utilities for collection modules.

Ported and adapted from lib/validation.py for use in the Ansible Collection.
Operates on dictionary-structured collection params rather than argparse args.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ansible_collections.tomazb.acm_switchover.plugins.module_utils import path_safety
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    ARGOCD_RESUME_ON_FAILURE_REQUIRES_MANAGE_MESSAGE,
    ARGOCD_RESUME_ON_FAILURE_VALIDATE_MODE_MESSAGE,
    VALIDATION_ACTIVATION_METHOD_CHOICES,
    VALIDATION_EXECUTION_MODE_CHOICES,
    VALIDATION_METHOD_CHOICES,
    VALIDATION_OLD_HUB_ACTION_CHOICES,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_evidence import MigrationEvidenceError
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_journal import (
    validate_repair_request,
    validate_waiver_request,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.path_safety import ValidationError

CONTEXT_NAME_MAX_LENGTH = 128
CONTEXT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-/@]*[A-Za-z0-9]$|^[A-Za-z0-9]$")


def validate_context_name(context: str) -> None:
    """Validate a Kubernetes context name.

    Raises:
        ValidationError: If the context name is empty, too long, or contains invalid characters.
    """
    if not context:
        raise ValidationError("Context name cannot be empty")

    if len(context) > CONTEXT_NAME_MAX_LENGTH:
        raise ValidationError(
            f"Context name '{context}' exceeds maximum length of {CONTEXT_NAME_MAX_LENGTH} characters"
        )

    if not CONTEXT_NAME_PATTERN.match(context):
        raise ValidationError(
            f"Invalid context name '{context}'. "
            "Must consist of alphanumeric characters, '-', '_', '.', ':', '/', or '@', "
            "and must start and end with an alphanumeric character"
        )


def validate_safe_path(path: str) -> None:
    """Validate that a path is safe (no traversal, no shell metacharacters).

    Raises:
        ValidationError: If the path is empty or unsafe.
    """
    path_safety.validate_safe_path(path)


def validate_report_artifact_path(path: str) -> None:
    """Validate a report artifact path before controller-side writes."""
    path_safety.validate_report_artifact_path(path)


def validate_report_artifact_directory(path: str) -> None:
    """Validate a report artifact directory before the final filename is known."""
    path_safety.validate_report_artifact_directory(path)


def _validate_choice(value: str, valid_choices: Sequence[str], field_name: str) -> None:
    """Validate that a value is one of the allowed choices.

    Raises:
        ValidationError: If the value is not in the allowed choices.
    """
    if value not in valid_choices:
        choices_str = ", ".join(valid_choices)
        raise ValidationError(f"Invalid {field_name} '{value}'. Must be one of: {choices_str}")


def _validate_observability_acknowledgement(
    *,
    acknowledged: bool,
    execution_mode: str,
    restore_only: bool,
    old_hub_action: str,
    standalone_decommission: bool,
) -> None:
    """Mirror of the Python CLI's --acknowledge-observability-not-migrated rule.

    The destination-observability gate this acknowledges runs only where an
    integrated switchover tears the old hub down, so the flag is refused
    everywhere else rather than silently ignored.
    """
    if not acknowledged:
        return
    if standalone_decommission:
        raise ValidationError(
            "acknowledge_observability_not_migrated cannot be used with a standalone decommission: "
            "there is no destination hub to acknowledge"
        )
    if execution_mode == "validate":
        raise ValidationError(
            "acknowledge_observability_not_migrated cannot be used in validate mode: "
            "validation performs no teardown to acknowledge"
        )
    if restore_only:
        raise ValidationError(
            "acknowledge_observability_not_migrated cannot be used with restore_only: "
            "there is no old hub to tear down"
        )
    if old_hub_action != "decommission":
        raise ValidationError("acknowledge_observability_not_migrated requires old_hub_action=decommission")


# R4-04 plan Task 6: the operator inputs. With no enabling flag of its own, a
# repair block is requested once it is not a dict, carries an undocumented key, or
# names any field; the waiver block is enabled by skip_managed_cluster_expectations.
_WAIVER_KEYS = ("scope", "actor", "reason", "request_id")
_REPAIR_KEYS = ("actor", "reason", "inspected_evidence", "run_id", "operation_id")
_UNSET_INPUTS: tuple[object, ...] = (None, "", [])
_MALFORMED_WAIVER_MESSAGE = (
    "malformed_waiver: skip_managed_cluster_expectations requires managed_cluster_expectation_waiver.scope "
    "(activation, post_activation or both) and a non-empty managed_cluster_expectation_waiver.actor and "
    "managed_cluster_expectation_waiver.reason; managed_cluster_expectation_waiver.request_id is optional "
    "but cannot be empty"
)
_MALFORMED_REPAIR_MESSAGE = (
    "malformed_repair: migration_cleanup_repair requires a non-empty actor and reason, at least one "
    "inspected_evidence reference (none empty), and a UUID run_id and operation_id"
)


def _names_any_field(value: object, keys: Sequence[str]) -> bool:
    if value is None:
        return False
    if not isinstance(value, dict) or not set(value) <= set(keys):
        return True
    return any(item not in _UNSET_INPUTS for item in value.values())


def _waiver_request(operation: dict) -> dict | None:
    """Mirror of the Python CLI's --skip-managed-cluster-expectations waiver request."""
    waiver = operation.get("managed_cluster_expectation_waiver")
    enabled = operation.get("skip_managed_cluster_expectations", False)
    if not isinstance(enabled, bool):
        raise ValidationError("malformed_waiver: skip_managed_cluster_expectations must be a boolean")
    if not enabled:
        if _names_any_field(waiver, _WAIVER_KEYS):
            raise ValidationError(
                "malformed_waiver: managed_cluster_expectation_waiver requires skip_managed_cluster_expectations: true"
            )
        return None
    if isinstance(waiver, dict):
        # request_id is optional: an omitted key is null, as the CLI's unset option is.
        waiver = {"request_id": None, **waiver}
    try:
        return validate_waiver_request(waiver)
    except MigrationEvidenceError:
        raise ValidationError(_MALFORMED_WAIVER_MESSAGE) from None


def _repair_request(operation: dict) -> dict | None:
    """Mirror of the Python CLI's --repair-migration-cleanup acknowledgement request."""
    repair = operation.get("migration_cleanup_repair")
    if not _names_any_field(repair, _REPAIR_KEYS):
        return None
    try:
        return validate_repair_request(repair)
    except MigrationEvidenceError:
        raise ValidationError(_MALFORMED_REPAIR_MESSAGE) from None


def _validate_migration_evidence_inputs(
    operation: dict,
    *,
    execution_mode: str,
    restore_only: bool,
    standalone_decommission: bool,
) -> tuple[dict | None, dict | None]:
    """Mirror of the Python CLI's waiver and cleanup-repair rules; returns both requests.

    Each is refused where nothing evaluates it rather than silently ignored.
    """
    if standalone_decommission:
        excluded_mode = "a standalone decommission"
    elif execution_mode == "validate":
        excluded_mode = "validate mode"
    else:
        excluded_mode = None
    waiver = _waiver_request(operation)
    if waiver is not None:
        if excluded_mode:
            raise ValidationError(
                f"waiver_not_applicable: skip_managed_cluster_expectations cannot be used with {excluded_mode}: "
                "no ManagedCluster expectation is evaluated"
            )
        if restore_only:
            raise ValidationError(
                "waiver_expected_names_empty: restore_only records no expected ManagedCluster name, so a "
                "waiver of any scope has nothing to waive"
            )
    repair = _repair_request(operation)
    if repair is not None and excluded_mode:
        raise ValidationError(
            f"repair_not_applicable: migration_cleanup_repair cannot be used with {excluded_mode}: "
            "no migration Restore cleanup runs"
        )
    return waiver, repair


def validate_operation_inputs(
    operation: dict,
    features: dict,
    execution: dict | None = None,
    decommission: dict | None = None,
) -> dict:
    """Validate that operation and feature params form a supported combination.

    Returns:
        Normalized dict of validated values.

    Raises:
        ValidationError: If the combination is not supported.
    """
    if not isinstance(operation, dict):
        raise ValidationError("operation must be a dictionary")
    if not isinstance(features, dict):
        raise ValidationError("features must be a dictionary")
    if execution is None:
        execution = {}
    if not isinstance(execution, dict):
        raise ValidationError("execution must be a dictionary")
    if decommission is None:
        decommission = {}
    if not isinstance(decommission, dict):
        raise ValidationError("decommission must be a dictionary")

    min_mc = operation.get("min_managed_clusters")
    if min_mc is not None:
        try:
            min_mc = int(min_mc)
        except (TypeError, ValueError):
            raise ValidationError("min_managed_clusters must be an integer")
        if min_mc < 0:
            raise ValidationError("min_managed_clusters must be a non-negative integer")

    restore_only = operation.get("restore_only", False)
    activation_method = operation.get("activation_method", "patch")
    old_hub_action = operation.get("old_hub_action", "secondary")
    disable_observability_on_secondary = features.get("disable_observability_on_secondary", False)
    argocd = features.get("argocd", {})
    if argocd is None:
        argocd = {}
    if not isinstance(argocd, dict):
        raise ValidationError("features.argocd must be a dictionary")
    argocd_manage = argocd.get("manage", False)
    argocd_resume_on_failure = argocd.get("resume_on_failure", False)
    execution_mode = execution.get("mode", "execute")

    _validate_choice(activation_method, VALIDATION_ACTIVATION_METHOD_CHOICES, "activation_method")
    _validate_choice(execution_mode, VALIDATION_EXECUTION_MODE_CHOICES, "execution.mode")

    if argocd_resume_on_failure and not argocd_manage:
        raise ValidationError(ARGOCD_RESUME_ON_FAILURE_REQUIRES_MANAGE_MESSAGE)
    if argocd_resume_on_failure and execution_mode == "validate":
        raise ValidationError(ARGOCD_RESUME_ON_FAILURE_VALIDATE_MODE_MESSAGE)

    if disable_observability_on_secondary and old_hub_action != "secondary":
        raise ValidationError(
            "disable_observability_on_secondary requires old_hub_action=secondary so the old hub remains available"
        )

    # Checked before the restore_only branch below, which returns early. The
    # acknowledgement lives on `acm_switchover_decommission`, the variable the plan's
    # decision record names, and `operation.decommission` is the standalone
    # decommission entry point's discriminator -- the collection's equivalent of the
    # Python CLI's `--decommission`.
    _validate_observability_acknowledgement(
        acknowledged=decommission.get("acknowledge_observability_not_migrated", False),
        execution_mode=execution_mode,
        restore_only=restore_only,
        old_hub_action=old_hub_action,
        standalone_decommission=bool(operation.get("decommission", False)),
    )
    waiver, repair = _validate_migration_evidence_inputs(
        operation,
        execution_mode=execution_mode,
        restore_only=restore_only,
        standalone_decommission=bool(operation.get("decommission", False)),
    )

    if restore_only:
        method = operation.get("method", "full")
        old_hub_action = operation.get("old_hub_action", "none")

        if method != "full":
            raise ValidationError("restore_only requires method=full (passive sync needs a live primary hub)")
        if old_hub_action != "none":
            raise ValidationError("restore_only requires old_hub_action=none (no old hub to manage)")

        return {
            "restore_only": True,
            "method": "full",
            "old_hub_action": "none",
            "activation_method": activation_method,
            "argocd_manage": argocd_manage,
            "argocd_resume_on_failure": argocd_resume_on_failure,
            "managed_cluster_expectation_waiver": waiver,
            "migration_cleanup_repair": repair,
        }

    method = operation.get("method", "passive")

    _validate_choice(method, VALIDATION_METHOD_CHOICES, "method")
    _validate_choice(old_hub_action, VALIDATION_OLD_HUB_ACTION_CHOICES, "old_hub_action")

    if method != "passive" and activation_method == "restore":
        raise ValidationError(
            "activation_method=restore requires method=passive; full restore does not use a passive sync restore"
        )

    return {
        "restore_only": False,
        "method": method,
        "old_hub_action": old_hub_action,
        "activation_method": activation_method,
        "argocd_manage": argocd_manage,
        "argocd_resume_on_failure": argocd_resume_on_failure,
        "managed_cluster_expectation_waiver": waiver,
        "migration_cleanup_repair": repair,
    }
