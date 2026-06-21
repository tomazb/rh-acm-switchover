from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import pytest

from tests.release.lab_controller.live_config import (
    CredentialReferenceConfig,
    ExternalLiveLabConfig,
    LiveConfigFieldSensitivity,
    LiveConfigValidationDecision,
    LiveExecutionPolicy,
    LiveGateId,
    LiveGateStatus,
    ScenarioAllowlistConfig,
    build_sanitized_example_live_lab_config,
    classify_live_config_field,
    is_artifact_safe_field,
    redact_live_config_summary,
    required_live_gate_ids,
    summarize_live_gates,
    validate_external_live_lab_config,
    validate_live_gate_set,
)
from tests.release.scenarios.catalog import SCENARIOS_BY_ID

_INITIAL_ALLOWED_SCENARIOS = {"baseline-check", "final-baseline-check", "lab-readiness", "preflight"}


def _flatten_strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        strings: list[str] = []
        for key, child in value.items():
            strings.extend(_flatten_strings(str(key)))
            strings.extend(_flatten_strings(child))
        return tuple(strings)
    if isinstance(value, (list, tuple, set)):
        strings = []
        for item in value:
            strings.extend(_flatten_strings(item))
        return tuple(strings)
    if isinstance(value, str):
        return (value,)
    return ()


def _validate(config: ExternalLiveLabConfig):
    return validate_external_live_lab_config(config)


def _summary_text(summary: Mapping[str, Any]) -> str:
    return "\n".join(_flatten_strings(summary)).lower()


def test_sanitized_example_config_validates_with_safe_pass_decision() -> None:
    result = _validate(build_sanitized_example_live_lab_config())

    assert result.decision is LiveConfigValidationDecision.PASS
    assert result.live_execution_enabled is False
    assert result.read_only_discovery_enabled is False
    assert result.mutation_enabled is False
    assert result.automatic_recovery_enabled is False
    assert result.live_certification_evidence_enabled is False
    assert result.redaction_status == "redacted"


def test_all_l0_l10_gates_are_present_and_design_only() -> None:
    gate_ids = required_live_gate_ids()

    assert gate_ids == (
        LiveGateId.L0,
        LiveGateId.L1,
        LiveGateId.L2,
        LiveGateId.L3,
        LiveGateId.L4,
        LiveGateId.L5,
        LiveGateId.L6,
        LiveGateId.L7,
        LiveGateId.L8,
        LiveGateId.L9,
        LiveGateId.L10,
    )
    assert validate_live_gate_set(gate_ids).decision is LiveConfigValidationDecision.PASS
    assert {item["status"] for item in summarize_live_gates()} == {LiveGateStatus.DESIGN_ONLY.value}


def test_execution_policy_defaults_fail_closed() -> None:
    policy = LiveExecutionPolicy()

    assert policy.live_execution_enabled is False
    assert policy.read_only_discovery_enabled is False
    assert policy.mutation_enabled is False
    assert policy.automatic_recovery_enabled is False
    assert policy.live_certification_evidence_enabled is False


def test_execution_policy_enabled_flags_are_blocked() -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(
        example,
        execution_policy=LiveExecutionPolicy(
            live_execution_enabled=True,
            read_only_discovery_enabled=True,
            mutation_enabled=True,
            automatic_recovery_enabled=True,
            live_certification_evidence_enabled=True,
        ),
    )

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "execution_policy.live_execution_enabled" in result.blocking_fields
    assert "execution_policy.read_only_discovery_enabled" in result.blocking_fields
    assert "execution_policy.mutation_enabled" in result.blocking_fields
    assert "execution_policy.automatic_recovery_enabled" in result.blocking_fields
    assert "execution_policy.live_certification_evidence_enabled" in result.blocking_fields


def test_credentials_are_runtime_only_and_not_persisted_or_environment_inherited() -> None:
    credentials = build_sanitized_example_live_lab_config().credentials

    assert credentials.runtime_only is True
    assert credentials.persist_to_artifacts is False
    assert credentials.inherit_environment is False


def test_credential_policy_violations_are_blocked() -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(
        example,
        credentials=replace(
            example.credentials,
            runtime_only=False,
            persist_to_artifacts=True,
            inherit_environment=True,
        ),
    )

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "credentials.runtime_only" in result.blocking_fields
    assert "credentials.persist_to_artifacts" in result.blocking_fields
    assert "credentials.inherit_environment" in result.blocking_fields


def test_runtime_only_fields_are_not_artifact_safe() -> None:
    assert classify_live_config_field("physical_hubs.context_ref") is LiveConfigFieldSensitivity.RUNTIME_ONLY
    assert classify_live_config_field("physical_hubs.kubeconfig_ref") is LiveConfigFieldSensitivity.RUNTIME_ONLY
    assert classify_live_config_field("credentials.references") is LiveConfigFieldSensitivity.RUNTIME_ONLY
    assert is_artifact_safe_field("physical_hubs.context_ref") is False
    assert is_artifact_safe_field("physical_hubs.kubeconfig_ref") is False


def test_missing_runtime_only_hub_references_are_blocked() -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(
        example,
        physical_hubs=(
            replace(example.physical_hubs[0], context_ref="", kubeconfig_ref=""),
            example.physical_hubs[1],
        ),
    )

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "physical_hubs[0].context_ref" in result.blocking_fields
    assert "physical_hubs[0].kubeconfig_ref" in result.blocking_fields


def test_artifact_safe_summary_excludes_runtime_only_hub_references() -> None:
    summary = redact_live_config_summary(build_sanitized_example_live_lab_config())
    summary_text = _summary_text(summary)

    assert "kubeconfig_ref" not in summary_text
    assert "context_ref" not in summary_text
    assert "<runtime-only-kubeconfig-ref>" not in summary_text
    assert "<runtime-only-context-ref>" not in summary_text
    assert summary["live_execution_enabled"] is False
    assert summary["read_only_discovery_enabled"] is False
    assert summary["mutation_enabled"] is False
    assert summary["automatic_recovery_enabled"] is False
    assert summary["live_certification_evidence_enabled"] is False


def test_to_artifact_safe_dict_excludes_runtime_only_hub_references() -> None:
    artifact_safe = build_sanitized_example_live_lab_config().to_artifact_safe_dict()
    summary_text = _summary_text(artifact_safe)

    assert "kubeconfig_ref" not in summary_text
    assert "context_ref" not in summary_text
    assert "<runtime-only-kubeconfig-ref>" not in summary_text
    assert "<runtime-only-context-ref>" not in summary_text
    assert artifact_safe["validation"]["decision"] == "PASS"


def test_to_artifact_safe_dict_sanitizes_nested_validation_payload_for_invalid_config() -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(
        example,
        physical_hubs=(replace(example.physical_hubs[0], kubeconfig_ref=""), example.physical_hubs[1]),
    )

    artifact_safe = config.to_artifact_safe_dict()
    summary_text = _summary_text(artifact_safe)

    assert artifact_safe["validation"]["decision"] == "BLOCKED"
    assert "kubeconfig_ref" not in summary_text
    assert "<runtime-only-kubeconfig-ref>" not in summary_text


def test_artifact_safe_summary_excludes_runtime_only_credential_and_env_references() -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(
        example,
        credentials=replace(
            example.credentials,
            references=(CredentialReferenceConfig(name="runtime-handle", runtime_ref="<runtime-only-kubeconfig-ref>"),),
            allowed_env_vars=("EXPLICIT_RUNTIME_ENV",),
        ),
    )

    summary = redact_live_config_summary(config)
    summary_text = _summary_text(summary)

    assert "runtime_ref" not in summary_text
    assert "runtime-handle" not in summary_text
    assert "EXPLICIT_RUNTIME_ENV" not in summary_text
    assert summary["runtime_access_policy"]["reference_count"] == 1


def test_raw_kubeconfig_like_path_is_rejected_without_raising() -> None:
    raw_path = "/" + "home/operator/.kube/config"
    config = replace(
        build_sanitized_example_live_lab_config(),
        physical_hubs=(
            replace(build_sanitized_example_live_lab_config().physical_hubs[0], kubeconfig_ref=raw_path),
            build_sanitized_example_live_lab_config().physical_hubs[1],
        ),
    )

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "physical_hubs[0].kubeconfig_ref" in result.blocking_fields


def test_missing_schema_version_is_blocked() -> None:
    config = replace(build_sanitized_example_live_lab_config(), schema_version="")

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "schema_version" in result.blocking_fields


def test_raw_api_url_is_rejected_without_committing_real_endpoint_literal() -> None:
    raw_api = "https://" + "api.example.invalid:6443"
    config = replace(
        build_sanitized_example_live_lab_config(),
        physical_hubs=(
            replace(build_sanitized_example_live_lab_config().physical_hubs[0], expected_api_fingerprint=raw_api),
            build_sanitized_example_live_lab_config().physical_hubs[1],
        ),
    )

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "physical_hubs[0].expected_api_fingerprint" in result.blocking_fields


def test_token_password_secret_or_credential_like_value_is_rejected() -> None:
    config = replace(
        build_sanitized_example_live_lab_config(),
        approval=replace(
            build_sanitized_example_live_lab_config().approval,
            approver_reference="token" + "=unsafe",
        ),
    )

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "approval.approver_reference" in result.blocking_fields


def test_release_artifact_default_is_rejected() -> None:
    config = replace(
        build_sanitized_example_live_lab_config(),
        artifact_policy=replace(build_sanitized_example_live_lab_config().artifact_policy, artifact_dir=".release"),
    )

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "artifact_policy.artifact_dir" in result.blocking_fields


def test_release_artifact_default_flag_is_rejected() -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(example, artifact_policy=replace(example.artifact_policy, default_release_output=True))

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "artifact_policy.artifact_dir" in result.blocking_fields


def test_redaction_required_false_is_rejected() -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(
        example,
        artifact_policy=replace(example.artifact_policy, redaction_required=False),
        redaction_policy=replace(example.redaction_policy, required=False),
    )

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "artifact_policy.redaction_required" in result.blocking_fields
    assert "redaction_policy.required" in result.blocking_fields


def test_physical_hub_duplicate_labels_are_rejected() -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(
        example,
        physical_hubs=(
            example.physical_hubs[0],
            replace(example.physical_hubs[1], physical_label=example.physical_hubs[0].physical_label),
        ),
    )

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "physical_hubs" in result.blocking_fields


def test_missing_second_physical_hub_is_rejected() -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(example, physical_hubs=(example.physical_hubs[0],))

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "physical_hubs" in result.blocking_fields


def test_managed_cluster_empty_set_is_rejected() -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(example, managed_clusters=replace(example.managed_clusters, expected_names=()))

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "managed_clusters.expected_names" in result.blocking_fields


def test_managed_cluster_exact_match_must_be_required() -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(example, managed_clusters=replace(example.managed_clusters, exact_match_required=False))

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "managed_clusters.exact_match_required" in result.blocking_fields


def test_unexpected_cluster_policy_must_block() -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(example, managed_clusters=replace(example.managed_clusters, unexpected_cluster_policy="warn"))

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "managed_clusters.unexpected_cluster_policy" in result.blocking_fields


def test_committed_examples_cannot_default_operator_live_confirmation_true() -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(example, approval=replace(example.approval, operator_confirmed_live_mode=True))

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "approval.operator_confirmed_live_mode" in result.blocking_fields


def test_committed_examples_cannot_default_mutation_allowed_true() -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(example, approval=replace(example.approval, mutation_allowed=True))

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "approval.mutation_allowed" in result.blocking_fields


def test_unknown_scenario_id_in_allowlist_is_rejected() -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(
        example,
        scenario_allowlist=ScenarioAllowlistConfig(approved_scenarios=("future-live-scenario",)),
    )

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "scenario_allowlist.approved_scenarios" in result.blocking_fields


def test_passive_switchover_is_not_initially_live_allowed() -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(
        example,
        scenario_allowlist=ScenarioAllowlistConfig(approved_scenarios=("python-passive-switchover",)),
    )

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "scenario_allowlist.approved_scenarios" in result.blocking_fields


def test_decommission_is_not_initially_live_allowed() -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(example, scenario_allowlist=ScenarioAllowlistConfig(approved_scenarios=("decommission",)))

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "scenario_allowlist.approved_scenarios" in result.blocking_fields


def test_failure_injection_is_not_initially_live_allowed() -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(example, scenario_allowlist=ScenarioAllowlistConfig(approved_scenarios=("failure-injection",)))

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "scenario_allowlist.approved_scenarios" in result.blocking_fields


def test_initial_live_allowlist_contains_only_non_mutating_catalog_scenarios() -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(
        example,
        scenario_allowlist=ScenarioAllowlistConfig(approved_scenarios=tuple(sorted(_INITIAL_ALLOWED_SCENARIOS))),
    )

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.PASS
    assert all(not SCENARIOS_BY_ID[scenario_id].mutates_lab for scenario_id in _INITIAL_ALLOWED_SCENARIOS)


@pytest.mark.parametrize("scenario_id", sorted(set(SCENARIOS_BY_ID) - _INITIAL_ALLOWED_SCENARIOS))
def test_catalog_scenarios_outside_initial_read_only_allowlist_are_rejected(scenario_id: str) -> None:
    example = build_sanitized_example_live_lab_config()
    config = replace(example, scenario_allowlist=ScenarioAllowlistConfig(approved_scenarios=(scenario_id,)))

    result = _validate(config)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "scenario_allowlist.approved_scenarios" in result.blocking_fields


def test_mapping_boolean_string_false_fails_closed_for_required_true_fields() -> None:
    payload = build_sanitized_example_live_lab_config().to_dict()
    payload["managed_clusters"]["exact_match_required"] = "false"
    payload["credentials"]["runtime_only"] = "false"
    payload["rbac_prerequisites"]["read_only_checks_required"] = "false"
    payload["artifact_policy"]["redaction_required"] = "false"
    payload["redaction_policy"]["required"] = "false"

    result = validate_external_live_lab_config(payload)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "managed_clusters.exact_match_required" in result.blocking_fields
    assert "credentials.runtime_only" in result.blocking_fields
    assert "rbac_prerequisites.read_only_checks_required" in result.blocking_fields
    assert "artifact_policy.redaction_required" in result.blocking_fields
    assert "redaction_policy.required" in result.blocking_fields


def test_forbidden_values_in_unknown_mapping_fields_are_rejected_before_coercion_drops_them() -> None:
    payload = build_sanitized_example_live_lab_config().to_dict()
    payload["unexpected_runtime_data"] = {"nested": "token" + "=unsafe"}

    result = validate_external_live_lab_config(payload)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "unexpected_runtime_data.nested" in result.blocking_fields
    assert result.redaction_status == "blocked"


def test_arbitrary_shell_command_like_value_is_rejected() -> None:
    payload = build_sanitized_example_live_lab_config().to_dict()
    payload["plan_id"] = "bash ./run-live-command"

    result = validate_external_live_lab_config(payload)

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert "plan_id" in result.blocking_fields


def test_sanitized_example_contains_only_fake_placeholders_for_runtime_and_redacted_values() -> None:
    example = build_sanitized_example_live_lab_config()
    strings = _flatten_strings(example.to_dict())
    joined = "\n".join(strings)

    for placeholder in (
        "<runtime-only-kubeconfig-ref>",
        "<runtime-only-context-ref>",
        "<redacted-api-fingerprint>",
        "<redacted-hub-identity-fingerprint>",
        "<operator-provided-approval-ref>",
        "<caller-provided-artifact-dir>",
    ):
        assert placeholder in joined

    for forbidden in ("/home/", "/tmp/", "~/.kube/", "token=", "password=", "secret=", ".release"):
        assert forbidden not in joined.lower()


def test_artifact_safe_summary_contains_no_real_looking_sensitive_values() -> None:
    summary = redact_live_config_summary(build_sanitized_example_live_lab_config())
    summary_text = _summary_text(summary)

    for forbidden in (
        "/home/",
        "/tmp/",
        "~/.kube/",
        "http://",
        "https://",
        "token=",
        "password=",
        "secret=",
        ".release",
        "cluster-id",
    ):
        assert forbidden not in summary_text


def test_validation_result_is_structured_for_invalid_input_without_exceptions() -> None:
    result = validate_external_live_lab_config(
        {
            "schema_version": "unsupported",
            "physical_hubs": [],
            "managed_clusters": {"expected_names": []},
        }
    )

    assert result.decision is LiveConfigValidationDecision.BLOCKED
    assert isinstance(result.reasons, tuple)
    assert isinstance(result.blocking_fields, tuple)
    assert isinstance(result.artifact_safe_summary, dict)
    assert result.live_execution_enabled is False
    assert result.read_only_discovery_enabled is False
    assert result.mutation_enabled is False
