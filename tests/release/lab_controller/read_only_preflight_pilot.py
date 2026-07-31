"""Phase 8L non-contact read-only preflight pilot rehearsal.

This module assembles and rehearses the Phase 8K pilot package with dry-run or fake-backed
inputs only. It deliberately does not implement live contact, live config loading, kubeconfig
reading, real Kubernetes/OpenShift client creation, release-adapter execution, mutation, automatic
recovery, or live ACM certification evidence.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any

from tests.release.lab_controller.artifacts import (
    sanitize_artifact_payload,
    sanitize_artifact_text,
    validate_artifact_payload_redacted,
)
from tests.release.lab_controller.read_only_discovery import (
    ReadOnlyDiscoveryGuardDecision,
    ReadOnlyQueryFamily,
    ReadOnlyQueryFamilyStatus,
    ReadOnlyQueryPlan,
    classify_read_only_verb,
    read_only_query_family_status,
    required_read_only_discovery_gate_ids,
    validate_read_only_discovery_gates,
    validate_read_only_query_plan,
)
from tests.release.lab_controller.read_only_transport import (
    FakeReadOnlyTransport,
    FakeTransportFixture,
    ReadOnlyTransportDecision,
    ReadOnlyTransportKind,
    ReadOnlyTransportQuery,
    ReadOnlyTransportResponse,
    ReadOnlyTransportStatus,
    build_example_fake_transport_fixture,
    collect_fake_transport_evidence,
    summarize_fake_transport_run,
    summarize_transport_response,
    validate_transport_query,
)
from tests.release.scenarios.catalog import SCENARIOS_BY_ID

_URL_PATTERN = re.compile(r"https?://", re.IGNORECASE)
_KUBECONFIG_PATH_PATTERN = re.compile(r"(^|[\s'\"])(/home/|/tmp/|~/\.kube/|[^ \t\n'\";]+[/\\]\.kube[/\\])")
_CREDENTIAL_VALUE_PATTERN = re.compile(
    r"(bearer\s+|token\s*[:=]|password\s*[:=]|secret\s*[:=]|credential\s*[:=]|\bcredential[-_:][^\s,;]+)",
    re.IGNORECASE,
)
_PRIVATE_ID_PATTERN = re.compile(r"\bcluster[-_]id(?:[-_:][A-Za-z0-9][\w.-]*)?\b", re.IGNORECASE)
_COMMAND_LIKE_PATTERN = re.compile(
    r"\b(?:oc|kubectl|ansible-playbook|bash|sh|python3?|rm|curl|wget)\b(?=\s|$|[|;&])",
    re.IGNORECASE,
)
_RELEASE_PATH_MARKER = "." + "release"

_ALLOWED_SCENARIOS = frozenset({"lab-readiness", "baseline-check", "preflight", "final-baseline-check"})
_ALLOWED_EXECUTABLE_FAMILIES = frozenset(
    {
        ReadOnlyQueryFamily.CLUSTER_IDENTITY,
        ReadOnlyQueryFamily.NAMESPACE_UID,
        ReadOnlyQueryFamily.CLUSTER_VERSION,
        ReadOnlyQueryFamily.ACM_MCE_MCH_STATUS,
        ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS,
        ReadOnlyQueryFamily.BACKUP_RESTORE_STATUS,
    }
)
_DEFER_ONLY_STATUSES = frozenset({ReadOnlyQueryFamilyStatus.CONDITIONAL, ReadOnlyQueryFamilyStatus.DEFERRED})
_VALID_REDACTION_STATUSES = frozenset({"redacted", "safe", "pass"})
_SATISFIED_GATE_STATUS = "satisfied"
_ARTIFACT_FILENAME = "phase8l-read-only-preflight-pilot-rehearsal.json"
_PASS_RECOMMENDATION = "READY_FOR_PHASE_8M_READ_ONLY_LIVE_PREFLIGHT_PILOT_APPROVAL_PACKAGE"  # nosec B105
_STOP_RECOMMENDATION = "STOP_FOR_HUMAN_REVIEW_BEFORE_ANY_LIVE_CONTACT"
_RETRY_RECOMMENDATION = "STOP_FOR_HUMAN_REVIEW_BEFORE_RETRY"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_REQUIRED_OPT_IN_FLAGS = frozenset(
    {
        "operator_approved_phase8l_rehearsal",
        "read_only_scope",
        "fake_or_dry_run_only",
    }
)

_FORBIDDEN_ARTIFACT_KEY_SUBSTRINGS: tuple[str, ...] = (
    "kubeconfig",
    "context_ref",
    "credential_ref",
    "client_ref",
    "transport_handle_ref",
    "runtime_ref",
    "raw_api",
    "api_url",
    "api_server",
    "token",
    "password",
    "secret",
    "raw_command",
    "command_string",
    "argv",
)
_FORBIDDEN_TRUE_ARTIFACT_FLAGS = frozenset(
    {
        "live_contact_attempted",
        "live_contact_succeeded",
        "real_execution_evidence",
        "live_certification_evidence",
        "mutation_enabled",
        "mutation_attempted",
    }
)


class Phase8LPilotDecision(str, Enum):
    """Conservative Phase 8L pilot rehearsal decisions."""

    PASS = "PASS"  # nosec B105
    BLOCKED = "BLOCKED"
    NO_GO = "NO_GO"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    INFRA_RETRYABLE = "INFRA_RETRYABLE"


class Phase8LPilotMode(str, Enum):
    """Phase 8L supported mode vocabulary."""

    DRY_RUN_NO_CONTACT = "dry_run_no_contact"
    FAKE_BACKED_REHEARSAL = "fake_backed_rehearsal"
    LIVE_READ_ONLY_UNSUPPORTED_IN_PHASE_8L = "live_read_only_unsupported_in_phase_8l"


@dataclass(frozen=True)
class PilotInput:
    """Artifact-facing Phase 8L pilot input model using placeholder/runtime-only summaries."""

    approval_reference: str
    expected_branch: str
    expected_commit: str
    clean_worktree: bool
    expected_physical_hub_labels: tuple[str, ...]
    expected_managed_cluster_set: tuple[str, ...]
    runtime_handle_summary: Mapping[str, Any]
    artifact_directory_ref: str
    scenario_allowlist: tuple[str, ...]
    query_family_allowlist: tuple[ReadOnlyQueryFamily | str, ...]
    opt_in_flags: Mapping[str, bool]
    timeout_seconds: int | float
    retry_budget: int
    redaction_policy_version: str
    pilot_mode: Phase8LPilotMode | str
    expected_role_labels: tuple[str, ...] = ()
    allow_dirty_worktree_for_dry_run: bool = False
    deferred_query_families: tuple[ReadOnlyQueryFamily | str, ...] = ()
    query_verbs: Mapping[str, str] = field(default_factory=dict)
    precontact_gate_status: Mapping[str, str] = field(default_factory=dict)
    l10_authorizes_mutation: bool = False
    live_certification_evidence: bool = False
    mutation_enabled: bool = False
    redaction_required: bool = True


@dataclass(frozen=True)
class PilotQueryPackage:
    """Validated structured query objects for a Phase 8L rehearsal."""

    scenario_ids: tuple[str, ...]
    query_family_allowlist: tuple[str, ...]
    deferred_query_families: tuple[str, ...]
    queries: tuple[ReadOnlyTransportQuery, ...]

    def to_artifact_safe_summary(self) -> dict[str, Any]:
        payload = {
            "scenario_ids": list(self.scenario_ids),
            "query_family_allowlist": list(self.query_family_allowlist),
            "deferred_query_families": list(self.deferred_query_families),
            "query_count": len(self.queries),
            "structured_query_objects_only": True,
            "redaction_required": True,
            "live_certification_evidence": False,
            "mutation_enabled": False,
            "queries": [
                {
                    "query_id": query.query_id,
                    "scenario_id": query.scenario_id,
                    "query_family": _family_value(query.query_family),
                    "verb": query.verb,
                    "verb_class": classify_read_only_verb(query.verb).value,
                    "hub_label": query.hub_label,
                    "resource_family": query.resource_family,
                    "redaction_required": query.redaction_required,
                    "live_certification_evidence": False,
                    "mutation_enabled": False,
                }
                for query in self.queries
            ],
        }
        return _safe_summary(payload)


@dataclass(frozen=True)
class PilotQueryPackageResult:
    decision: Phase8LPilotDecision
    query_package: PilotQueryPackage | None = None
    reasons: tuple[str, ...] = ()
    blocking_fields: tuple[str, ...] = ()
    first_blocking_reason: str | None = None


@dataclass(frozen=True)
class Phase8LPilotResult:
    decision: Phase8LPilotDecision
    artifact_summary: Mapping[str, Any]
    reasons: tuple[str, ...] = ()
    blocking_fields: tuple[str, ...] = ()
    retry_allowed: bool = False
    manual_recovery_required: bool = False
    first_blocking_reason: str | None = None


@dataclass(frozen=True)
class PilotArtifactValidation:
    decision: Phase8LPilotDecision
    reasons: tuple[str, ...] = ()
    blocking_fields: tuple[str, ...] = ()
    first_blocking_reason: str | None = None


@dataclass(frozen=True)
class FakeResponseContractValidation:
    decision: Phase8LPilotDecision
    reasons: tuple[str, ...] = ()
    blocking_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class PilotArtifactWriteResult:
    decision: Phase8LPilotDecision
    path: Path | None = None
    reasons: tuple[str, ...] = ()
    first_blocking_reason: str | None = None


def build_pilot_query_package(inputs: PilotInput) -> PilotQueryPackageResult:
    """Build a Phase 8E/8H-validated structured query package without live contact."""
    precheck = _validate_pilot_input(inputs, include_mode=False)
    if precheck:
        return _query_package_blocked(precheck[0], precheck[1])

    scenarios = tuple(dict.fromkeys(inputs.scenario_allowlist))
    family_values = tuple(_family_value(item) for item in inputs.query_family_allowlist)
    deferred_values = tuple(dict.fromkeys(_family_value(item) for item in inputs.deferred_query_families))
    executable_families = tuple(
        item for item in inputs.query_family_allowlist if _family_value(item) not in deferred_values
    )
    if not executable_families:
        return _query_package_blocked(
            ("no executable query families remain after deferral",), ("query_family_allowlist",)
        )

    queries: list[ReadOnlyTransportQuery] = []
    query_index = 0
    for scenario_id in scenarios:
        for hub_index, hub_label in enumerate(inputs.expected_physical_hub_labels, start=1):
            for query_family in executable_families:
                family = _coerce_family(query_family)
                if family is None:
                    return _query_package_blocked(("unknown query family is not allowed",), ("query_family_allowlist",))
                verb = _query_verb_for(inputs, family)
                plan = ReadOnlyQueryPlan(
                    scenario_id=scenario_id,
                    query_family=family,
                    verb=verb,
                    required_gate_ids=tuple(required_read_only_discovery_gate_ids()),
                    artifact_fields=(
                        "physical_identity_evidence",
                        "logical_role_evidence",
                        "managed_cluster_set_evidence",
                        "read_prerequisite_evidence",
                        "gate_status",
                        "decision",
                    ),
                    redaction_required=True,
                    live_certification_evidence=False,
                )
                guardrail_result = validate_read_only_query_plan(plan)
                if guardrail_result.decision is not ReadOnlyDiscoveryGuardDecision.PASS:
                    return _query_package_blocked(guardrail_result.reasons, guardrail_result.blocking_fields)

                query_index += 1
                query = ReadOnlyTransportQuery(
                    query_id=f"phase8l-{scenario_id}-hub{hub_index}-{family.value}-{query_index}",
                    scenario_id=scenario_id,
                    query_family=family,
                    verb=verb,
                    hub_label=str(hub_label),
                    resource_family=family.value,
                    guardrail_result=guardrail_result,
                    required_gate_ids=tuple(required_read_only_discovery_gate_ids()),
                    artifact_fields=plan.artifact_fields,
                    redaction_required=True,
                    live_certification_evidence=False,
                    mutation_enabled=False,
                )
                transport_validation = validate_transport_query(query)
                if transport_validation.decision is not ReadOnlyTransportDecision.PASS:
                    return _query_package_blocked(transport_validation.reasons, transport_validation.blocking_fields)
                queries.append(query)

    package = PilotQueryPackage(
        scenario_ids=scenarios,
        query_family_allowlist=family_values,
        deferred_query_families=deferred_values,
        queries=tuple(queries),
    )
    return PilotQueryPackageResult(decision=Phase8LPilotDecision.PASS, query_package=package)


def run_preflight_pilot_rehearsal(
    inputs: PilotInput,
    *,
    fake_transport: FakeReadOnlyTransport | None = None,
) -> Phase8LPilotResult:
    """Run the Phase 8L non-contact pilot rehearsal with dry-run or fake-backed inputs only."""
    if not isinstance(inputs, PilotInput):
        return _invalid_pilot_input_result("input must be a PilotInput", "input")

    mode = _coerce_mode(inputs.pilot_mode)
    input_failure = _validate_pilot_input(inputs, include_mode=True)
    if input_failure:
        reasons, fields = input_failure
        return _pilot_result(
            inputs=inputs,
            decision=Phase8LPilotDecision.BLOCKED,
            reasons=reasons,
            blocking_fields=fields,
            mode=mode,
            query_package=None,
        )

    if mode is Phase8LPilotMode.LIVE_READ_ONLY_UNSUPPORTED_IN_PHASE_8L:
        return _pilot_result(
            inputs=inputs,
            decision=Phase8LPilotDecision.BLOCKED,
            reasons=("live read-only contact is unsupported in Phase 8L",),
            blocking_fields=("pilot_mode",),
            mode=mode,
            query_package=None,
        )
    if mode is None:
        return _pilot_result(
            inputs=inputs,
            decision=Phase8LPilotDecision.BLOCKED,
            reasons=("unknown pilot_mode is unsupported",),
            blocking_fields=("pilot_mode",),
            mode=None,
            query_package=None,
        )

    package_result = build_pilot_query_package(inputs)
    if package_result.decision is not Phase8LPilotDecision.PASS or package_result.query_package is None:
        return _pilot_result(
            inputs=inputs,
            decision=package_result.decision,
            reasons=package_result.reasons,
            blocking_fields=package_result.blocking_fields,
            mode=mode,
            query_package=None,
        )

    if mode is Phase8LPilotMode.DRY_RUN_NO_CONTACT:
        return _pilot_result(
            inputs=inputs,
            decision=Phase8LPilotDecision.PASS,
            reasons=(),
            blocking_fields=(),
            mode=mode,
            query_package=package_result.query_package,
            responses=(),
            simulated_contact_attempted=False,
            simulated_contact_succeeded=False,
        )

    if fake_transport is not None and not isinstance(fake_transport, FakeReadOnlyTransport):
        return _pilot_result(
            inputs=inputs,
            decision=Phase8LPilotDecision.BLOCKED,
            reasons=("fake_transport must be a FakeReadOnlyTransport",),
            blocking_fields=("fake_transport",),
            mode=mode,
            query_package=package_result.query_package,
        )

    selected_transport = (
        fake_transport
        if fake_transport is not None
        else _default_success_fake_transport(package_result.query_package.queries)
    )
    responses = collect_fake_transport_evidence(selected_transport, package_result.query_package.queries)
    response_contract = _validate_fake_response_contract(package_result.query_package.queries, responses)
    if response_contract.decision is not Phase8LPilotDecision.PASS:
        return _pilot_result(
            inputs=inputs,
            decision=response_contract.decision,
            reasons=response_contract.reasons,
            blocking_fields=response_contract.blocking_fields,
            mode=mode,
            query_package=package_result.query_package,
            responses=responses,
            simulated_contact_attempted=bool(responses),
            simulated_contact_succeeded=False,
        )
    decision, reasons = _decision_from_fake_responses(responses)
    return _pilot_result(
        inputs=inputs,
        decision=decision,
        reasons=reasons,
        blocking_fields=(),
        mode=mode,
        query_package=package_result.query_package,
        responses=responses,
        simulated_contact_attempted=bool(responses),
        simulated_contact_succeeded=bool(responses)
        and all(response.decision is ReadOnlyTransportDecision.PASS for response in responses),
    )


def validate_pilot_artifact_payload(payload: Mapping[str, Any]) -> PilotArtifactValidation:  # noqa: C901
    """Validate that a Phase 8L artifact payload is provisional, redacted, and non-live."""
    if not isinstance(payload, Mapping):
        return PilotArtifactValidation(
            decision=Phase8LPilotDecision.BLOCKED,
            reasons=("artifact payload must be a mapping",),
            blocking_fields=("artifact",),
            first_blocking_reason="artifact payload must be a mapping",
        )

    required_fields = (
        "artifact_version",
        "phase",
        "mode",
        "branch",
        "commit",
        "clean_worktree",
        "approval_reference_redacted",
        "scenario_ids",
        "query_family_allowlist",
        "gate_status",
        "opt_in_flags",
        "runtime_handle_summary",
        "artifact_directory_ref_redacted",
        "live_contact_attempted",
        "live_contact_succeeded",
        "real_execution_evidence",
        "simulated_contact_attempted",
        "simulated_contact_succeeded",
        "live_certification_evidence",
        "mutation_enabled",
        "mutation_attempted",
        "query_plan_summary",
        "query_result_summary",
        "physical_identity_evidence",
        "logical_role_evidence",
        "managed_cluster_set_evidence",
        "read_prerequisite_evidence",
        "redaction_status",
        "decision",
        "retry_allowed",
        "manual_recovery_required",
        "first_blocking_reason",
        "next_phase_recommendation",
    )
    reasons: list[str] = []
    fields: list[str] = []

    for field_name in required_fields:
        if field_name not in payload:
            _block(reasons, fields, field_name, f"required Phase 8L artifact field is missing: {field_name}")

    mode: Phase8LPilotMode | None = None
    mode_value = payload.get("mode")
    if not isinstance(mode_value, str):
        _block(reasons, fields, "mode", "Phase 8L artifact mode must be a supported string")
    else:
        try:
            mode = Phase8LPilotMode(mode_value)
        except ValueError:
            _block(reasons, fields, "mode", "Phase 8L artifact mode is unsupported")

    decision: Phase8LPilotDecision | None = None
    decision_value = payload.get("decision")
    if not isinstance(decision_value, str):
        _block(reasons, fields, "decision", "Phase 8L artifact decision must be a supported string")
    else:
        try:
            decision = Phase8LPilotDecision(decision_value)
        except ValueError:
            _block(reasons, fields, "decision", "Phase 8L artifact decision is unsupported")

    if decision is Phase8LPilotDecision.PASS and mode not in {
        Phase8LPilotMode.DRY_RUN_NO_CONTACT,
        Phase8LPilotMode.FAKE_BACKED_REHEARSAL,
    }:
        reason = "Phase 8L PASS artifact decision requires dry-run or fake-backed mode"
        _block(reasons, fields, "mode", reason)
        _block(reasons, fields, "decision", reason)
    if (
        mode is Phase8LPilotMode.LIVE_READ_ONLY_UNSUPPORTED_IN_PHASE_8L
        and decision is not None
        and decision is not Phase8LPilotDecision.BLOCKED
    ):
        _block(
            reasons,
            fields,
            "decision",
            "Phase 8L live read-only unsupported mode must validate only as BLOCKED",
        )

    if payload.get("phase") != "8L":
        _block(reasons, fields, "phase", "Phase 8L artifact phase must be 8L")
    if not str(payload.get("artifact_version", "")).startswith("provisional.phase8l"):
        _block(reasons, fields, "artifact_version", "Phase 8L artifact schema must remain provisional")
    if payload.get("live_contact_attempted") is not False:
        _block(reasons, fields, "live_contact_attempted", "Phase 8L must not claim live contact attempted")
    if payload.get("live_contact_succeeded") is not False:
        _block(reasons, fields, "live_contact_succeeded", "Phase 8L must not claim live contact succeeded")
    if payload.get("real_execution_evidence") is not False:
        _block(reasons, fields, "real_execution_evidence", "Phase 8L must not claim real execution evidence")
    if payload.get("live_certification_evidence") is not False:
        _block(
            reasons,
            fields,
            "live_certification_evidence",
            "Phase 8L must not claim live certification evidence",
        )
    if payload.get("mutation_enabled") is not False:
        _block(reasons, fields, "mutation_enabled", "Phase 8L must not enable mutation")
    if payload.get("mutation_attempted") is not False:
        _block(reasons, fields, "mutation_attempted", "Phase 8L must not attempt mutation")
    if (
        payload.get("redaction_status") not in _VALID_REDACTION_STATUSES
        and payload.get("redaction_status") != "rejected"
    ):
        _block(reasons, fields, "redaction_status", "redaction_status is unsupported")

    unsafe_reasons: list[str] = []
    unsafe_fields: list[str] = []
    if _payload_has_forbidden_artifact_key(payload):
        _block(unsafe_reasons, unsafe_fields, "artifact", "artifact contains forbidden runtime-only keys")
    if _payload_has_forbidden_evidence_claim(payload):
        _block(unsafe_reasons, unsafe_fields, "artifact", "artifact contains forbidden Phase 8L evidence claims")
    if _payload_has_unsafe_value(payload):
        _block(unsafe_reasons, unsafe_fields, "artifact", "artifact contains unsafe artifact-facing values")

    if unsafe_reasons:
        return PilotArtifactValidation(
            decision=Phase8LPilotDecision.NO_GO,
            reasons=tuple(dict.fromkeys(unsafe_reasons)),
            blocking_fields=tuple(dict.fromkeys(unsafe_fields)),
            first_blocking_reason=unsafe_reasons[0],
        )
    if reasons:
        return PilotArtifactValidation(
            decision=Phase8LPilotDecision.BLOCKED,
            reasons=tuple(dict.fromkeys(reasons)),
            blocking_fields=tuple(dict.fromkeys(fields)),
            first_blocking_reason=reasons[0],
        )
    try:
        validate_artifact_payload_redacted(dict(payload))
    except ValueError as exc:
        return PilotArtifactValidation(
            decision=Phase8LPilotDecision.NO_GO,
            reasons=(f"artifact redaction failed: {_safe_text(str(exc))}",),
            blocking_fields=("artifact",),
            first_blocking_reason="artifact redaction failed",
        )
    return PilotArtifactValidation(decision=Phase8LPilotDecision.PASS)


def write_pilot_artifact(
    result: Phase8LPilotResult,
    artifact_dir: Path,
    *,
    filename: str = _ARTIFACT_FILENAME,
) -> PilotArtifactWriteResult:
    """Write redacted Phase 8L JSON only to an explicit caller-provided directory."""
    if not isinstance(result, Phase8LPilotResult):
        return PilotArtifactWriteResult(
            decision=Phase8LPilotDecision.BLOCKED,
            reasons=("result must be a Phase8LPilotResult",),
            first_blocking_reason="result must be a Phase8LPilotResult",
        )
    if not _artifact_write_dir_is_safe(artifact_dir):
        reason = "artifact directory is unsafe or not explicitly caller-provided"
        return PilotArtifactWriteResult(
            decision=Phase8LPilotDecision.BLOCKED,
            reasons=(reason,),
            first_blocking_reason=reason,
        )
    if not _artifact_filename_is_safe(filename):
        reason = "artifact filename must be a safe basename"
        return PilotArtifactWriteResult(
            decision=Phase8LPilotDecision.BLOCKED,
            reasons=(reason,),
            first_blocking_reason=reason,
        )
    validation = validate_pilot_artifact_payload(result.artifact_summary)
    if validation.decision is not Phase8LPilotDecision.PASS:
        return PilotArtifactWriteResult(
            decision=validation.decision,
            reasons=validation.reasons,
            first_blocking_reason=validation.first_blocking_reason,
        )
    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        safe_dir = artifact_dir.resolve(strict=True)
        if not _artifact_write_dir_is_safe(safe_dir):
            reason = "resolved artifact directory is unsafe"
            return PilotArtifactWriteResult(
                decision=Phase8LPilotDecision.BLOCKED,
                reasons=(reason,),
                first_blocking_reason=reason,
            )
        artifact_path = safe_dir / filename
        staging_dir = safe_dir / f".phase8l-write-{id(result)}"
        staging_file = staging_dir / filename
        try:
            payload_json = json.dumps(dict(result.artifact_summary), indent=2, sort_keys=True) + "\n"
            staging_dir.mkdir(mode=0o700, exist_ok=False)
            staging_file.write_text(payload_json, encoding="utf-8")
            artifact_path.hardlink_to(staging_file)
        except FileExistsError as exc:
            raise OSError("artifact path already exists or is a symlink") from exc
        finally:
            try:
                staging_file.unlink()
            except OSError:
                pass
            try:
                staging_dir.rmdir()
            except OSError:
                pass
    except (OSError, TypeError, ValueError) as exc:
        decision = (
            Phase8LPilotDecision.NO_GO
            if bool(result.artifact_summary.get("simulated_contact_attempted"))
            else Phase8LPilotDecision.BLOCKED
        )
        reason = f"artifact write failed: {_safe_text(str(exc))}"
        return PilotArtifactWriteResult(decision=decision, reasons=(reason,), first_blocking_reason=reason)
    return PilotArtifactWriteResult(decision=Phase8LPilotDecision.PASS, path=artifact_path)


def _validate_pilot_input(  # noqa: C901
    inputs: PilotInput, *, include_mode: bool
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    reasons: list[str] = []
    fields: list[str] = []

    if not isinstance(inputs, PilotInput):
        return ("input must be a PilotInput",), ("input",)
    if include_mode:
        mode = _coerce_mode(inputs.pilot_mode)
        if mode is None:
            _block(reasons, fields, "pilot_mode", "unknown pilot_mode is unsupported")
        elif mode is Phase8LPilotMode.LIVE_READ_ONLY_UNSUPPORTED_IN_PHASE_8L:
            _block(reasons, fields, "pilot_mode", "live read-only mode is unsupported in Phase 8L")

    _require_safe_text(inputs.approval_reference, "approval_reference", reasons, fields)
    _require_safe_text(inputs.expected_branch, "expected_branch", reasons, fields)
    _require_safe_text(inputs.expected_commit, "expected_commit", reasons, fields)
    if inputs.clean_worktree is not True and not (
        _coerce_mode(inputs.pilot_mode) is Phase8LPilotMode.DRY_RUN_NO_CONTACT
        and inputs.allow_dirty_worktree_for_dry_run is True
    ):
        _block(reasons, fields, "clean_worktree", "clean_worktree must be true unless explicitly allowed for dry-run")
    if not _is_non_string_sequence(inputs.expected_physical_hub_labels) or not inputs.expected_physical_hub_labels:
        _block(reasons, fields, "expected_physical_hub_labels", "expected physical hub labels are required")
    else:
        for label in inputs.expected_physical_hub_labels:
            _require_safe_text(str(label), "expected_physical_hub_labels", reasons, fields)
    if not _is_non_string_sequence(inputs.expected_managed_cluster_set) or not inputs.expected_managed_cluster_set:
        _block(reasons, fields, "expected_managed_cluster_set", "expected managed cluster set is required")
    else:
        for name in inputs.expected_managed_cluster_set:
            _require_safe_text(str(name), "expected_managed_cluster_set", reasons, fields)
    _validate_runtime_handle_summary(inputs.runtime_handle_summary, reasons, fields)
    _require_safe_text(inputs.artifact_directory_ref, "artifact_directory_ref", reasons, fields)
    if _release_marker_in_text(inputs.artifact_directory_ref):
        _block(reasons, fields, "artifact_directory_ref", "artifact directory ref must not use default release output")
    _validate_opt_in_flags(inputs.opt_in_flags, reasons, fields)
    _validate_scenario_allowlist(inputs.scenario_allowlist, reasons, fields)
    _validate_query_family_allowlist(inputs, reasons, fields)
    _validate_query_verbs(inputs, reasons, fields)
    _validate_gate_status(inputs.precontact_gate_status, reasons, fields)
    _require_safe_text(inputs.redaction_policy_version, "redaction_policy_version", reasons, fields)
    if not _positive_number(inputs.timeout_seconds):
        _block(reasons, fields, "timeout_seconds", "timeout_seconds must be positive and bounded")
    if (
        not isinstance(inputs.retry_budget, int)
        or isinstance(inputs.retry_budget, bool)
        or not (0 <= inputs.retry_budget <= 3)
    ):
        _block(reasons, fields, "retry_budget", "retry_budget must be an integer between 0 and 3")
    if inputs.l10_authorizes_mutation is not False:
        _block(reasons, fields, "l10_authorizes_mutation", "L10 cannot authorize mutation in Phase 8L")
    if inputs.live_certification_evidence is not False:
        _block(
            reasons,
            fields,
            "live_certification_evidence",
            "live_certification_evidence must remain false in Phase 8L",
        )
    if inputs.mutation_enabled is not False:
        _block(reasons, fields, "mutation_enabled", "mutation_enabled must remain false in Phase 8L")
    if inputs.redaction_required is not True:
        _block(reasons, fields, "redaction_required", "redaction_required must remain true in Phase 8L")

    if reasons:
        return tuple(dict.fromkeys(reasons)), tuple(dict.fromkeys(fields))
    return None


def _validate_runtime_handle_summary(
    summary: Mapping[str, Any],
    reasons: list[str],
    fields: list[str],
) -> None:
    if not isinstance(summary, Mapping) or not summary:
        _block(reasons, fields, "runtime_handle_summary", "runtime handle summary is required")
        return
    if summary.get("runtime_values_redacted") is not True:
        _block(reasons, fields, "runtime_handle_summary", "runtime handle values must be redacted")
    if _payload_has_forbidden_artifact_key(summary) or _payload_has_unsafe_value(summary):
        _block(reasons, fields, "runtime_handle_summary", "runtime handle summary contains unsafe runtime values")
    try:
        _safe_summary(dict(summary))
    except ValueError:
        _block(reasons, fields, "runtime_handle_summary", "runtime handle summary failed redaction validation")


def _validate_opt_in_flags(flags: Mapping[str, bool], reasons: list[str], fields: list[str]) -> None:
    if not isinstance(flags, Mapping) or not flags:
        _block(reasons, fields, "opt_in_flags", "opt-in flags are required")
        return
    missing = _REQUIRED_OPT_IN_FLAGS - set(flags)
    disabled = {key for key in _REQUIRED_OPT_IN_FLAGS if flags.get(key) is not True}
    if missing or disabled:
        _block(reasons, fields, "opt_in_flags", "required opt-in flags must be present and true")
        return
    for key, value in flags.items():
        if _value_is_unsafe(str(key)) or not isinstance(value, bool):
            _block(reasons, fields, "opt_in_flags", "opt-in flags must be explicit safe booleans")
            return


def _validate_scenario_allowlist(scenarios: Sequence[str], reasons: list[str], fields: list[str]) -> None:
    if not _is_non_string_sequence(scenarios) or not scenarios:
        _block(reasons, fields, "scenario_allowlist", "scenario allowlist is required")
        return
    for scenario_id in scenarios:
        if not isinstance(scenario_id, str):
            _block(reasons, fields, "scenario_allowlist", "scenario allowlist entries must be strings")
            return
        if _value_is_unsafe(scenario_id):
            _block(reasons, fields, "scenario_allowlist", "scenario allowlist contains unsafe values")
            return
        scenario = SCENARIOS_BY_ID.get(scenario_id)
        if scenario_id not in _ALLOWED_SCENARIOS or scenario is None or scenario.mutates_lab:
            _block(reasons, fields, "scenario_allowlist", f"scenario is not allowed in Phase 8L: {scenario_id}")
            return


def _validate_query_family_allowlist(inputs: PilotInput, reasons: list[str], fields: list[str]) -> None:
    if not _is_non_string_sequence(inputs.query_family_allowlist) or not inputs.query_family_allowlist:
        _block(reasons, fields, "query_family_allowlist", "query family allowlist is required")
        return
    if not _is_non_string_sequence(inputs.deferred_query_families):
        _block(reasons, fields, "deferred_query_families", "deferred query families must be a sequence")
        return
    deferred = {_family_value(item) for item in inputs.deferred_query_families}
    allowlist_values: set[str] = set()
    for item in inputs.deferred_query_families:
        family = _coerce_family(item)
        family_value = _family_value(item)
        status = read_only_query_family_status(family_value)
        if family is None or status is None:
            _block(reasons, fields, "deferred_query_families", "unknown deferred query family is not allowed")
            return
        if status not in _DEFER_ONLY_STATUSES:
            _block(reasons, fields, "deferred_query_families", "deferred query family is not deferrable")
            return
    for item in inputs.query_family_allowlist:
        family = _coerce_family(item)
        family_value = _family_value(item)
        status = read_only_query_family_status(family_value)
        if family is None or status is None:
            _block(reasons, fields, "query_family_allowlist", "unknown query family is not allowed")
            return
        allowlist_values.add(family_value)
        if family in _ALLOWED_EXECUTABLE_FAMILIES:
            continue
        if family_value in deferred and status in _DEFER_ONLY_STATUSES:
            continue
        _block(reasons, fields, "query_family_allowlist", f"query family is not executable in Phase 8L: {family_value}")
        return
    if not deferred.issubset(allowlist_values):
        _block(reasons, fields, "deferred_query_families", "deferred query families must be included in the allowlist")


def _validate_query_verbs(inputs: PilotInput, reasons: list[str], fields: list[str]) -> None:
    if not isinstance(inputs.query_verbs, Mapping):
        _block(reasons, fields, "query_verbs", "query_verbs must be a mapping of query family to read-only verb")
        return
    allowlist_values = (
        {_family_value(item) for item in inputs.query_family_allowlist}
        if _is_non_string_sequence(inputs.query_family_allowlist)
        else set()
    )
    for key, verb in inputs.query_verbs.items():
        key_text = str(key)
        if _value_is_unsafe(key_text) or _value_is_unsafe(str(verb)):
            _block(reasons, fields, "query_verbs", "query verb override contains unsafe values")
            return
        if key_text != "default":
            family = _coerce_family(key_text)
            if family is None or _family_value(family) not in allowlist_values:
                _block(reasons, fields, "query_verbs", "query verb override must target an allowlisted query family")
                return
        if classify_read_only_verb(str(verb)) is not classify_read_only_verb("get"):
            _block(reasons, fields, "query_verbs", "query verb override is not read-only")
            return


def _validate_gate_status(gate_status: Mapping[str, str], reasons: list[str], fields: list[str]) -> None:
    if not isinstance(gate_status, Mapping) or not gate_status:
        _block(reasons, fields, "precontact_gate_status", "L0-L9 gate status is required")
        return
    for gate, status in gate_status.items():
        if _value_is_unsafe(str(gate)) or _value_is_unsafe(str(status)):
            _block(reasons, fields, "precontact_gate_status", "L0-L9 gate status contains unsafe values")
            return
    required_values = tuple(gate.value for gate in required_read_only_discovery_gate_ids())
    allowed_values = set(required_values)
    missing = [gate for gate in required_values if gate not in gate_status]
    unknown = sorted(str(gate) for gate in gate_status if str(gate) not in allowed_values)
    failed = [
        gate
        for gate in required_values
        if gate in gate_status and str(gate_status.get(gate)).lower() != _SATISFIED_GATE_STATUS
    ]
    if missing:
        _block(reasons, fields, "precontact_gate_status", "L0-L9 gate status is missing required gates")
    if failed:
        _block(reasons, fields, "precontact_gate_status", "L0-L9 gate status contains non-satisfied gates")
    if unknown:
        _block(reasons, fields, "precontact_gate_status", "L0-L9 gate status contains unknown gates")


def _decision_from_fake_responses(
    responses: Sequence[ReadOnlyTransportResponse],
) -> tuple[Phase8LPilotDecision, tuple[str, ...]]:
    if not responses:
        return Phase8LPilotDecision.BLOCKED, ("fake-backed rehearsal did not produce query responses",)
    if any(response.live_contact_attempted or response.live_contact_succeeded for response in responses):
        return Phase8LPilotDecision.NO_GO, ("fake-backed rehearsal unexpectedly produced live-contact evidence",)
    if any(response.live_certification_evidence or response.mutation_attempted for response in responses):
        return Phase8LPilotDecision.NO_GO, ("fake-backed rehearsal produced forbidden evidence flags",)
    if any(response.decision is ReadOnlyTransportDecision.NO_GO for response in responses):
        reason = _first_response_reason(responses, ReadOnlyTransportDecision.NO_GO)
        return Phase8LPilotDecision.NO_GO, (reason,)

    payload_decision, payload_reason, recovery_reason = _fake_payload_decision_context(responses)
    if payload_decision is not None:
        return payload_decision, (payload_reason or "fake result payload failed validation",)
    if recovery_reason:
        return Phase8LPilotDecision.RECOVERY_REQUIRED, (recovery_reason,)
    if any(response.decision is ReadOnlyTransportDecision.BLOCKED for response in responses):
        reason = _first_response_reason(responses, ReadOnlyTransportDecision.BLOCKED)
        return Phase8LPilotDecision.BLOCKED, (reason,)
    if any(response.decision is ReadOnlyTransportDecision.INFRA_RETRYABLE for response in responses):
        reason = _first_response_reason(responses, ReadOnlyTransportDecision.INFRA_RETRYABLE)
        return Phase8LPilotDecision.INFRA_RETRYABLE, (reason,)
    if _missing_required_positive_evidence(responses):
        return Phase8LPilotDecision.BLOCKED, ("fake-backed success is missing required evidence",)
    return Phase8LPilotDecision.PASS, ()


def _validate_fake_response_contract(
    queries: Sequence[ReadOnlyTransportQuery],
    responses: Sequence[ReadOnlyTransportResponse],
) -> FakeResponseContractValidation:
    reasons: list[str] = []
    fields: list[str] = []
    expected_query_ids = tuple(query.query_id for query in queries)
    response_query_ids = tuple(
        response.query_id if isinstance(response, ReadOnlyTransportResponse) else None for response in responses
    )

    if len(responses) != len(queries):
        _block(
            reasons,
            fields,
            "fake_response_count",
            "fake-backed rehearsal response count does not match the query package",
        )

    if any(not isinstance(response, ReadOnlyTransportResponse) for response in responses):
        _block(
            reasons,
            fields,
            "fake_response_type",
            "fake-backed rehearsal produced a non-transport response",
        )

    duplicate_query_ids = {
        query_id for query_id in response_query_ids if query_id is not None and response_query_ids.count(query_id) > 1
    }
    if duplicate_query_ids:
        _block(
            reasons,
            fields,
            "fake_response_query_id",
            "fake-backed rehearsal produced duplicate response query_id values",
        )

    ordered_query_mismatch = False
    if len(responses) == len(queries) and response_query_ids != expected_query_ids:
        response_ids_are_complete = all(query_id is not None for query_id in response_query_ids)
        if not duplicate_query_ids and response_ids_are_complete and set(response_query_ids) == set(expected_query_ids):
            ordered_query_mismatch = True
            _block(
                reasons,
                fields,
                "fake_response_order",
                "fake-backed rehearsal responses are not in query package order",
            )
        else:
            _block(
                reasons,
                fields,
                "fake_response_query_id",
                "fake-backed rehearsal response query_id does not match the query package",
            )

    for query, response in zip(queries, responses):
        if not isinstance(response, ReadOnlyTransportResponse):
            continue
        if not ordered_query_mismatch and response.query_id != query.query_id:
            _block(
                reasons,
                fields,
                "fake_response_query_id",
                "fake-backed rehearsal response query_id does not match the query it answers",
            )
        if response.scenario_id != query.scenario_id:
            _block(
                reasons,
                fields,
                "fake_response_scenario_id",
                "fake-backed rehearsal response scenario_id does not match the query it answers",
            )
        if _transport_kind_value(response.transport_kind) != ReadOnlyTransportKind.FAKE.value:
            _block(
                reasons,
                fields,
                "fake_response_transport_kind",
                "fake-backed rehearsal response transport_kind must be fake",
            )
        if response.no_live_contact is not True:
            _block(
                reasons,
                fields,
                "fake_response_no_live_contact",
                "fake-backed rehearsal response must assert no_live_contact",
            )
        if any(
            getattr(response, flag_name, False) is not False
            for flag_name in (
                "live_contact_attempted",
                "live_contact_succeeded",
                "real_execution_evidence",
                "live_certification_evidence",
                "mutation_enabled",
                "mutation_attempted",
            )
        ):
            _block(
                reasons,
                fields,
                "fake_response_evidence_flags",
                "fake-backed rehearsal response contains forbidden evidence flags",
            )

    if reasons:
        return FakeResponseContractValidation(
            decision=Phase8LPilotDecision.BLOCKED,
            reasons=tuple(dict.fromkeys(reasons)),
            blocking_fields=tuple(dict.fromkeys(fields)),
        )
    return FakeResponseContractValidation(decision=Phase8LPilotDecision.PASS)


def _fake_payload_decision_context(
    responses: Sequence[ReadOnlyTransportResponse],
) -> tuple[Phase8LPilotDecision | None, str | None, str | None]:
    recovery_reason: str | None = None
    for response in responses:
        payload, payload_safe = _safe_fake_response_payload(response.artifact_safe_payload)
        if not payload_safe:
            return Phase8LPilotDecision.NO_GO, "fake result payload failed redaction validation", None
        redaction_status = payload.get("redaction_status")
        identity_status = payload.get("identity_status")
        managed_state = payload.get("managed_cluster_state")
        logical_state = payload.get("logical_role_state")
        if redaction_status is not None and redaction_status not in _VALID_REDACTION_STATUSES:
            return Phase8LPilotDecision.NO_GO, "fake result redaction failed", None
        if identity_status == "mismatch" or payload.get("identity_match") is False:
            return Phase8LPilotDecision.NO_GO, "fake result indicates identity mismatch", None
        if managed_state == "drift" or payload.get("managed_cluster_set_exact") is False:
            return Phase8LPilotDecision.NO_GO, "fake result indicates managed cluster drift", None
        if logical_state == "both_hubs_active":
            return Phase8LPilotDecision.NO_GO, "fake result indicates both hubs active", None
        if logical_state in {"neither_hub_active", "ambiguous"}:
            recovery_reason = "fake result indicates ambiguous live state"
    return None, None, recovery_reason


def _pilot_result(
    *,
    inputs: PilotInput,
    decision: Phase8LPilotDecision,
    reasons: Sequence[str],
    blocking_fields: Sequence[str],
    mode: Phase8LPilotMode | None,
    query_package: PilotQueryPackage | None,
    responses: Sequence[ReadOnlyTransportResponse] = (),
    simulated_contact_attempted: bool = False,
    simulated_contact_succeeded: bool = False,
) -> Phase8LPilotResult:
    retry_allowed = decision is Phase8LPilotDecision.INFRA_RETRYABLE and inputs.retry_budget > 0
    manual_recovery_required = decision is Phase8LPilotDecision.RECOVERY_REQUIRED
    first_reason = _safe_text(next(iter(reasons), None))
    artifact = _build_artifact_summary(
        inputs=inputs,
        decision=decision,
        mode=mode,
        query_package=query_package,
        responses=responses,
        reasons=tuple(_safe_text(reason) for reason in reasons),
        blocking_fields=blocking_fields,
        retry_allowed=retry_allowed,
        manual_recovery_required=manual_recovery_required,
        first_blocking_reason=first_reason,
        simulated_contact_attempted=simulated_contact_attempted,
        simulated_contact_succeeded=simulated_contact_succeeded,
    )
    validation = validate_pilot_artifact_payload(artifact)
    if validation.decision is not Phase8LPilotDecision.PASS:
        artifact = _scrub_forbidden_evidence_claims(dict(artifact))
        if decision in {
            Phase8LPilotDecision.PASS,
            Phase8LPilotDecision.INFRA_RETRYABLE,
            Phase8LPilotDecision.RECOVERY_REQUIRED,
        }:
            decision = Phase8LPilotDecision.NO_GO
        retry_allowed = decision is Phase8LPilotDecision.INFRA_RETRYABLE and inputs.retry_budget > 0
        manual_recovery_required = decision is Phase8LPilotDecision.RECOVERY_REQUIRED
        artifact["decision"] = decision.value
        artifact["safe_to_continue"] = False
        artifact["retry_allowed"] = retry_allowed
        artifact["manual_recovery_required"] = manual_recovery_required
        artifact["redaction_status"] = "rejected"
        artifact["first_blocking_reason"] = validation.first_blocking_reason or "artifact validation failed"
        artifact["next_phase_recommendation"] = _STOP_RECOMMENDATION
        first_reason = str(artifact["first_blocking_reason"])
        reasons = tuple(dict.fromkeys(tuple(reasons) + (first_reason,)))
    return Phase8LPilotResult(
        decision=decision,
        artifact_summary=artifact,
        reasons=tuple(dict.fromkeys(_safe_text(reason) for reason in reasons)),
        blocking_fields=tuple(dict.fromkeys(blocking_fields)),
        retry_allowed=retry_allowed,
        manual_recovery_required=manual_recovery_required,
        first_blocking_reason=first_reason,
    )


def _invalid_pilot_input_result(reason: str, field_name: str) -> Phase8LPilotResult:
    placeholder_inputs = PilotInput(
        approval_reference="missing",
        expected_branch="missing",
        expected_commit="missing",
        clean_worktree=False,
        expected_physical_hub_labels=("unavailable",),
        expected_managed_cluster_set=("unavailable",),
        runtime_handle_summary={"runtime_values_redacted": True, "summary_present": False},
        artifact_directory_ref="missing",
        scenario_allowlist=(),
        query_family_allowlist=(),
        opt_in_flags={},
        timeout_seconds=1,
        retry_budget=0,
        redaction_policy_version="phase8l-redaction-policy",
        pilot_mode="invalid",
        precontact_gate_status={},
    )
    return _pilot_result(
        inputs=placeholder_inputs,
        decision=Phase8LPilotDecision.BLOCKED,
        reasons=(_safe_text(reason),),
        blocking_fields=(field_name,),
        mode=None,
        query_package=None,
    )


def _build_artifact_summary(
    *,
    inputs: PilotInput,
    decision: Phase8LPilotDecision,
    mode: Phase8LPilotMode | None,
    query_package: PilotQueryPackage | None,
    responses: Sequence[ReadOnlyTransportResponse],
    reasons: Sequence[str],
    blocking_fields: Sequence[str],
    retry_allowed: bool,
    manual_recovery_required: bool,
    first_blocking_reason: str | None,
    simulated_contact_attempted: bool,
    simulated_contact_succeeded: bool,
) -> dict[str, Any]:
    hub_by_query_id = {query.query_id: query.hub_label for query in query_package.queries} if query_package else {}
    response_payloads = []
    for response in responses:
        payload, _ = _safe_fake_response_payload(response.artifact_safe_payload)
        if response.query_id in hub_by_query_id:
            payload["hub_label"] = hub_by_query_id[response.query_id]
        response_payloads.append(payload)
    redaction_status = "rejected" if _fake_redaction_rejected(responses, response_payloads) else "redacted"
    scenario_ids = list(inputs.scenario_allowlist) if _is_non_string_sequence(inputs.scenario_allowlist) else []
    query_family_allowlist = (
        [_family_value(item) for item in inputs.query_family_allowlist]
        if _is_non_string_sequence(inputs.query_family_allowlist)
        else []
    )
    opt_in_flags = dict(inputs.opt_in_flags) if isinstance(inputs.opt_in_flags, Mapping) else {}
    payload = {
        "artifact_version": "provisional.phase8l.v1",
        "phase": "8L",
        "mode": mode.value if mode is not None else str(inputs.pilot_mode),
        "branch": _safe_text(inputs.expected_branch),
        "commit": _safe_text(inputs.expected_commit),
        "clean_worktree": bool(inputs.clean_worktree),
        "approval_reference_redacted": _redacted_ref(inputs.approval_reference),
        "scenario_ids": scenario_ids,
        "query_family_allowlist": query_family_allowlist,
        "gate_status": _gate_artifact_status(inputs, decision, responses),
        "opt_in_flags": opt_in_flags,
        "runtime_handle_summary": _safe_runtime_summary(inputs.runtime_handle_summary),
        "artifact_directory_ref_redacted": _redacted_ref(inputs.artifact_directory_ref),
        "live_contact_attempted": False,
        "live_contact_succeeded": False,
        "real_execution_evidence": False,
        "simulated_contact_attempted": simulated_contact_attempted,
        "simulated_contact_succeeded": simulated_contact_succeeded,
        "rehearsal_contact_simulated": simulated_contact_attempted,
        "live_certification_evidence": False,
        "mutation_enabled": False,
        "mutation_attempted": False,
        "query_plan_summary": (
            query_package.to_artifact_safe_summary()
            if query_package is not None
            else {
                "query_count": 0,
                "structured_query_objects_only": True,
                "redaction_required": True,
                "live_certification_evidence": False,
                "mutation_enabled": False,
            }
        ),
        "query_result_summary": _query_result_summary(query_package, responses),
        "physical_identity_evidence": _physical_identity_evidence(response_payloads, decision),
        "logical_role_evidence": _logical_role_evidence(response_payloads, decision),
        "managed_cluster_set_evidence": _managed_cluster_set_evidence(response_payloads, decision),
        "read_prerequisite_evidence": _read_prerequisite_evidence(response_payloads, decision),
        "redaction_status": redaction_status,
        "decision": decision.value,
        "safe_to_continue": decision is Phase8LPilotDecision.PASS,
        "retry_allowed": retry_allowed,
        "manual_recovery_required": manual_recovery_required,
        "first_blocking_reason": _safe_text(first_blocking_reason),
        "reasons": [_safe_text(reason) for reason in reasons],
        "blocking_fields": [str(field) for field in blocking_fields],
        "next_phase_recommendation": _next_phase_recommendation(decision),
        "production_json_schema_finalized": False,
    }
    return _safe_summary(payload)


def _gate_artifact_status(
    inputs: PilotInput,
    decision: Phase8LPilotDecision,
    responses: Sequence[ReadOnlyTransportResponse],
) -> dict[str, Any]:
    gate_status = inputs.precontact_gate_status if isinstance(inputs.precontact_gate_status, Mapping) else {}
    gate_result = validate_read_only_discovery_gates(tuple(str(gate_id) for gate_id in gate_status))
    precontact_gate_values = {
        gate.value: _safe_text(str(gate_status.get(gate.value, "missing"))) or "missing"
        for gate in required_read_only_discovery_gate_ids()
    }
    gates_passed = all(str(value).lower() == _SATISFIED_GATE_STATUS for value in precontact_gate_values.values())
    return _safe_summary(
        {
            "pre_run": "PASS" if decision is not Phase8LPilotDecision.BLOCKED else "BLOCKED",
            "pre_contact": (
                "PASS" if gates_passed and gate_result.decision is ReadOnlyDiscoveryGuardDecision.PASS else "BLOCKED"
            ),
            "post_rehearsal": (
                decision.value if responses else ("PASS" if decision is Phase8LPilotDecision.PASS else decision.value)
            ),
            "l0_l9": precontact_gate_values,
            "l10_required_for_read_only": False,
            "l10_authorizes_mutation": False,
            "phase8e_guardrails": gate_result.decision.value,
            "phase8h_transport_validation": "represented",
            "phase8j_opt_in_semantics_represented": True,
            "redaction_required": True,
            "live_certification_evidence": False,
            "mutation_enabled": False,
        }
    )


def _query_result_summary(
    query_package: PilotQueryPackage | None,
    responses: Sequence[ReadOnlyTransportResponse],
) -> dict[str, Any]:
    if query_package is None or not responses:
        return _safe_summary(
            {
                "transport_kind": "none",
                "executed_query_count": 0,
                "live_contact_attempted": False,
                "live_contact_succeeded": False,
                "real_execution_evidence": False,
                "simulated_contact_attempted": False,
                "simulated_contact_succeeded": False,
                "live_certification_evidence": False,
                "mutation_attempted": False,
                "redaction_status": "redacted",
                "responses": [],
            }
        )
    fake_summary = summarize_fake_transport_run(query_package.queries, responses).to_payload()
    response_summaries = [_phase8l_response_summary(response) for response in responses]
    fake_summary["no_live_contact"] = True
    fake_summary["executed_query_count"] = len(responses)
    fake_summary["simulated_contact_attempted"] = True
    fake_summary["simulated_contact_succeeded"] = all(
        response.decision is ReadOnlyTransportDecision.PASS for response in responses
    )
    fake_summary["real_execution_evidence"] = False
    fake_summary["live_contact_attempted"] = False
    fake_summary["live_contact_succeeded"] = False
    fake_summary["live_certification_evidence"] = False
    fake_summary["mutation_attempted"] = False
    fake_summary["response_summaries"] = response_summaries
    fake_summary["responses"] = response_summaries
    return _safe_summary(fake_summary)


def _phase8l_response_summary(response: ReadOnlyTransportResponse) -> dict[str, Any]:
    payload, payload_safe = _safe_fake_response_payload(response.artifact_safe_payload)
    safe_response = replace(
        response,
        artifact_safe_payload=payload,
        redaction_status="rejected" if not payload_safe else response.redaction_status,
    )
    summary = summarize_transport_response(safe_response)
    summary["no_live_contact"] = True
    summary["live_contact_attempted"] = False
    summary["live_contact_succeeded"] = False
    summary["mutation_attempted"] = False
    summary["live_certification_evidence"] = False
    return _safe_summary(summary)


def _physical_identity_evidence(
    payloads: Sequence[Mapping[str, Any]],
    decision: Phase8LPilotDecision,
) -> dict[str, Any]:
    status = _aggregate_identity_status(payloads)
    represented_hubs = {
        str(payload.get("hub_label"))
        for payload in payloads
        if (payload.get("identity_status") == "matched" or payload.get("identity_match") is True)
        and payload.get("hub_label")
    }
    proven = status == "matched" and decision is Phase8LPilotDecision.PASS and bool(represented_hubs)
    return _safe_summary(
        {
            "status": status,
            "proven": proven,
            "signal_count": len(represented_hubs) if proven else 0,
            "live_certification_evidence": False,
        }
    )


def _logical_role_evidence(
    payloads: Sequence[Mapping[str, Any]],
    decision: Phase8LPilotDecision,
) -> dict[str, Any]:
    status = _aggregate_logical_role_state(payloads)
    proven = status == "proven" and decision is Phase8LPilotDecision.PASS and bool(payloads)
    return _safe_summary(
        {
            "status": status,
            "proven": proven,
            "previous_artifact_supporting_only": True,
            "live_certification_evidence": False,
        }
    )


def _managed_cluster_set_evidence(
    payloads: Sequence[Mapping[str, Any]],
    decision: Phase8LPilotDecision,
) -> dict[str, Any]:
    status = _aggregate_managed_cluster_state(payloads)
    exact_match = status == "exact" and decision is Phase8LPilotDecision.PASS and bool(payloads)
    return _safe_summary(
        {
            "status": status,
            "exact_match": exact_match,
            "unexpected_cluster_policy": "block",
            "live_certification_evidence": False,
        }
    )


def _read_prerequisite_evidence(
    payloads: Sequence[Mapping[str, Any]],
    decision: Phase8LPilotDecision,
) -> dict[str, Any]:
    status = _aggregate_read_prerequisite_state(payloads)
    proven = status == "represented" and decision is Phase8LPilotDecision.PASS and bool(payloads)
    return _safe_summary(
        {
            "status": status,
            "proven": proven,
            "mutation_checks_required": False,
            "live_certification_evidence": False,
        }
    )


def _default_success_fake_transport(queries: Sequence[ReadOnlyTransportQuery]) -> FakeReadOnlyTransport:
    fixtures = []
    for query in queries:
        base = build_example_fake_transport_fixture(query_id=query.query_id)
        fixtures.append(
            FakeTransportFixture(
                query_id=base.query_id,
                status=ReadOnlyTransportStatus.SUCCESS,
                payload={
                    "identity_status": "matched",
                    "managed_cluster_state": "exact",
                    "logical_role_state": "proven",
                    "read_prerequisite_state": "represented",
                    "redaction_status": "redacted",
                },
            )
        )
    return FakeReadOnlyTransport(tuple(fixtures))


def _query_package_blocked(
    reasons: Sequence[str],
    fields: Sequence[str],
) -> PilotQueryPackageResult:
    safe_reasons = tuple(dict.fromkeys(_safe_text(reason) for reason in reasons if reason))
    return PilotQueryPackageResult(
        decision=Phase8LPilotDecision.BLOCKED,
        reasons=safe_reasons,
        blocking_fields=tuple(dict.fromkeys(str(field) for field in fields)),
        first_blocking_reason=next(iter(safe_reasons), None),
    )


def _first_response_reason(
    responses: Sequence[ReadOnlyTransportResponse],
    decision: ReadOnlyTransportDecision,
) -> str:
    for response in responses:
        if response.decision is decision:
            return _safe_text(response.first_blocking_reason or response.response_summary)
    return "fake transport returned a blocking decision"


def _fake_redaction_rejected(
    responses: Sequence[ReadOnlyTransportResponse],
    payloads: Sequence[Mapping[str, Any]],
) -> bool:
    return any(response.status is ReadOnlyTransportStatus.UNSAFE_PAYLOAD for response in responses) or any(
        payload.get("redaction_status") not in (None, *_VALID_REDACTION_STATUSES) for payload in payloads
    )


def _missing_required_positive_evidence(responses: Sequence[ReadOnlyTransportResponse]) -> bool:
    for response in responses:
        if response.decision is not ReadOnlyTransportDecision.PASS:
            continue
        payload, payload_safe = _safe_fake_response_payload(response.artifact_safe_payload)
        if not payload_safe:
            return True
        if not (
            (payload.get("identity_status") == "matched" or payload.get("identity_match") is True)
            and (payload.get("managed_cluster_state") == "exact" or payload.get("managed_cluster_set_exact") is True)
            and payload.get("logical_role_state") == "proven"
            and payload.get("read_prerequisite_state") == "represented"
            and payload.get("redaction_status") in _VALID_REDACTION_STATUSES
        ):
            return True
    return False


def _safe_fake_response_payload(payload: Any) -> tuple[dict[str, Any], bool]:
    if not isinstance(payload, Mapping):
        return {"redaction_status": "rejected", "unsafe_payload_removed": True}, False
    try:
        safe_payload = _safe_summary(dict(payload))
    except ValueError:
        return {"redaction_status": "rejected", "unsafe_payload_removed": True}, False
    if _payload_has_forbidden_evidence_claim(safe_payload):
        return {"redaction_status": "rejected", "unsafe_payload_removed": True}, False
    return safe_payload, True


def _coerce_mode(value: Phase8LPilotMode | str) -> Phase8LPilotMode | None:
    if isinstance(value, Phase8LPilotMode):
        return value
    try:
        return Phase8LPilotMode(str(value))
    except ValueError:
        return None


def _coerce_family(value: ReadOnlyQueryFamily | str) -> ReadOnlyQueryFamily | None:
    if isinstance(value, ReadOnlyQueryFamily):
        return value
    try:
        return ReadOnlyQueryFamily(str(value))
    except ValueError:
        return None


def _family_value(value: ReadOnlyQueryFamily | str) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _transport_kind_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _query_verb_for(inputs: PilotInput, family: ReadOnlyQueryFamily) -> str:
    if family.value in inputs.query_verbs:
        return str(inputs.query_verbs[family.value])
    if "default" in inputs.query_verbs:
        return str(inputs.query_verbs["default"])
    return "get"


def _require_safe_text(value: Any, field_name: str, reasons: list[str], fields: list[str]) -> None:
    if not isinstance(value, str):
        _block(reasons, fields, field_name, f"{field_name} must be a string")
    elif not value:
        _block(reasons, fields, field_name, f"{field_name} is required")
    elif _value_is_unsafe(value):
        _block(reasons, fields, field_name, f"{field_name} contains an unsafe artifact-facing value")


def _positive_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return 0 < value <= 300


def _is_non_string_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _safe_runtime_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(summary, Mapping):
        return {"runtime_values_redacted": True, "summary_present": False}
    payload = dict(summary)
    payload["runtime_values_redacted"] = True
    try:
        return _safe_summary(payload)
    except ValueError:
        return {
            "runtime_values_redacted": True,
            "summary_present": True,
            "redaction_status": "rejected",
            "unsafe_runtime_values_removed": True,
        }


def _redacted_ref(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return "missing"
    if _value_is_unsafe(value):
        return "[REDACTED]"
    return "present-redacted"


def _safe_text(value: Any) -> str:
    if value is not None and not isinstance(value, str):
        return "[REDACTED]"
    sanitized = sanitize_artifact_text(value)
    if sanitized is None:
        return ""
    if _value_is_unsafe(sanitized):
        return "[REDACTED]"
    return sanitized


def _next_phase_recommendation(decision: Phase8LPilotDecision) -> str:
    if decision is Phase8LPilotDecision.PASS:
        return _PASS_RECOMMENDATION
    if decision is Phase8LPilotDecision.INFRA_RETRYABLE:
        return _RETRY_RECOMMENDATION
    return _STOP_RECOMMENDATION


def _payload_has_forbidden_artifact_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key).lower()
            if any(marker in key_text for marker in _FORBIDDEN_ARTIFACT_KEY_SUBSTRINGS):
                return True
            if _payload_has_forbidden_artifact_key(child):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_payload_has_forbidden_artifact_key(item) for item in value)
    return False


def _payload_has_forbidden_evidence_claim(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in _FORBIDDEN_TRUE_ARTIFACT_FLAGS and child is not False:
                return True
            if _payload_has_forbidden_evidence_claim(child):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_payload_has_forbidden_evidence_claim(item) for item in value)
    return False


def _scrub_forbidden_evidence_claims(value: Any) -> Any:
    if isinstance(value, Mapping):
        scrubbed: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key)
            scrubbed[key_text] = (
                False if key_text.lower() in _FORBIDDEN_TRUE_ARTIFACT_FLAGS else _scrub_forbidden_evidence_claims(child)
            )
        return scrubbed
    if isinstance(value, list):
        return [_scrub_forbidden_evidence_claims(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub_forbidden_evidence_claims(item) for item in value)
    return value


def _payload_has_unsafe_value(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_payload_has_unsafe_value(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_payload_has_unsafe_value(item) for item in value)
    if isinstance(value, str):
        return _value_is_unsafe(value)
    return False


def _value_is_unsafe(value: str) -> bool:
    lowered = value.lower()
    return bool(
        _URL_PATTERN.search(value)
        or _KUBECONFIG_PATH_PATTERN.search(value)
        or _CREDENTIAL_VALUE_PATTERN.search(value)
        or _PRIVATE_ID_PATTERN.search(value)
        or _COMMAND_LIKE_PATTERN.search(value)
        or _RELEASE_PATH_MARKER in lowered
    )


def _release_marker_in_text(value: Any) -> bool:
    return isinstance(value, str) and _RELEASE_PATH_MARKER in value.lower()


def _artifact_write_dir_is_safe(path: Path) -> bool:
    if not isinstance(path, Path):
        return False
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    try:
        resolved.relative_to(_REPO_ROOT)
        return False
    except ValueError:
        pass
    parts = tuple(part.lower() for part in path.parts if part not in {"", "."})
    resolved_parts = tuple(part.lower() for part in resolved.parts if part not in {"", "."})
    parts_to_check = (parts, resolved_parts)
    if not parts or any(part == ".." for part in parts):
        return False
    if any(
        _RELEASE_PATH_MARKER in part or part == ".kube" for checked_parts in parts_to_check for part in checked_parts
    ):
        return False
    if any(
        any(marker in part for marker in ("kubeconfig", "token", "secret", "credential", "password"))
        for checked_parts in parts_to_check
        for part in checked_parts
    ):
        return False
    if not path.is_absolute():
        return False
    return True


def _artifact_filename_is_safe(filename: str) -> bool:
    if not isinstance(filename, str) or not filename:
        return False
    path = Path(filename)
    if path.is_absolute() or path.name != filename:
        return False
    if any(part in {"", ".", ".."} for part in path.parts):
        return False
    if _value_is_unsafe(filename):
        return False
    return True


def _aggregate_identity_status(payloads: Sequence[Mapping[str, Any]]) -> str:
    if any(
        payload.get("identity_status") == "mismatch" or payload.get("identity_match") is False for payload in payloads
    ):
        return "mismatch"
    if payloads and all(
        payload.get("identity_status") == "matched" or payload.get("identity_match") is True for payload in payloads
    ):
        return "matched"
    return "not_proven"


def _aggregate_logical_role_state(payloads: Sequence[Mapping[str, Any]]) -> str:
    states = tuple(payload.get("logical_role_state") for payload in payloads)
    if "both_hubs_active" in states:
        return "both_hubs_active"
    if "ambiguous" in states:
        return "ambiguous"
    if "neither_hub_active" in states:
        return "neither_hub_active"
    if payloads and all(state == "proven" for state in states):
        return "proven"
    return "not_proven"


def _aggregate_managed_cluster_state(payloads: Sequence[Mapping[str, Any]]) -> str:
    if any(
        payload.get("managed_cluster_state") == "drift" or payload.get("managed_cluster_set_exact") is False
        for payload in payloads
    ):
        return "drift"
    if payloads and all(
        payload.get("managed_cluster_state") == "exact" or payload.get("managed_cluster_set_exact") is True
        for payload in payloads
    ):
        return "exact"
    return "not_proven"


def _aggregate_read_prerequisite_state(payloads: Sequence[Mapping[str, Any]]) -> str:
    if payloads and all(payload.get("read_prerequisite_state") == "represented" for payload in payloads):
        return "represented"
    return "not_proven"


def _safe_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_artifact_payload(dict(payload))
    validate_artifact_payload_redacted(sanitized)
    if _payload_has_forbidden_artifact_key(sanitized) or _payload_has_unsafe_value(sanitized):
        raise ValueError("artifact payload is not safe for Phase 8L publication")
    return sanitized


def _block(reasons: list[str], fields: list[str], field_name: str, reason: str) -> None:
    reasons.append(_safe_text(reason))
    fields.append(field_name)


__all__ = [
    "Phase8LPilotDecision",
    "Phase8LPilotMode",
    "Phase8LPilotResult",
    "PilotArtifactValidation",
    "PilotArtifactWriteResult",
    "PilotInput",
    "PilotQueryPackage",
    "PilotQueryPackageResult",
    "build_pilot_query_package",
    "run_preflight_pilot_rehearsal",
    "validate_pilot_artifact_payload",
    "write_pilot_artifact",
]
