from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.release.lab_controller.controller import ScenarioExecutionStatus, run_segment
from tests.release.lab_controller.discovery import fake_lab_observation
from tests.release.lab_controller.execution import (
    ExecutionMode,
    ReleaseFrameworkDryRunBackend,
    build_release_framework_request,
)
from tests.release.lab_controller.invocation import (
    build_release_framework_env_plan,
    evaluate_release_framework_execution_eligibility,
    materialize_release_framework_request,
    plan_release_framework_artifact_directory,
    summarize_materialized_invocation,
    validate_materialized_invocation,
    verify_release_profile_compatibility,
)
from tests.release.lab_controller.models import (
    DesiredRoleState,
    GeneratedProfile,
    HubIdentityEvidence,
    LabArgoCDSettings,
    LabReleaseMetadata,
    ObservedRoleState,
    PhysicalHubConfig,
    PhysicalHubLabel,
    SegmentPlan,
    StableLabConfig,
)
from tests.release.lab_controller.planner import (
    CertificationDecision,
    build_ping_pong_plan,
    run_certification_plan,
)
from tests.release.lab_controller.profiles import build_role_aware_profile, redact_generated_profile_metadata

EXPECTED_CLUSTERS = ("mc-1", "mc-2", "mc-3")
PLAN_ID = "phase6b-plan"
ARTIFACT_ROOT = "artifacts/release-lab/unit"


def _identity(label: PhysicalHubLabel) -> HubIdentityEvidence:
    return HubIdentityEvidence(
        physical_label=label,
        kube_system_uid=f"uid-{label.value}",
        api_server_fingerprint=f"api-{label.value}",
        context_name=f"{label.value}-context",
    )


def _hub_config(label: PhysicalHubLabel) -> PhysicalHubConfig:
    return PhysicalHubConfig(
        physical_label=label,
        kubeconfig_reference=f"kubeconfig-ref-{label.value}",
        context_name=f"{label.value}-context",
        expected_identity=_identity(label),
    )


def _lab_config(*, primary_name: str = "lab-controller-phase6b") -> StableLabConfig:
    return StableLabConfig(
        physical_hubs={
            PhysicalHubLabel.HUB_A: _hub_config(PhysicalHubLabel.HUB_A),
            PhysicalHubLabel.HUB_B: _hub_config(PhysicalHubLabel.HUB_B),
        },
        expected_managed_cluster_names=EXPECTED_CLUSTERS,
        enabled_streams=("bash", "python", "ansible"),
        scenario_ids=("preflight",),
        profile_name=primary_name,
        artifact_root=ARTIFACT_ROOT,
        release=LabReleaseMetadata(expected_version="1.7.10", metadata_files=("README.md",)),
        argocd=LabArgoCDSettings(mandatory=True, namespaces=("openshift-gitops",)),
    )


def _expected_identities(config: StableLabConfig | None = None) -> dict[PhysicalHubLabel, HubIdentityEvidence]:
    config = config or _lab_config()
    return {
        label: hub.expected_identity for label, hub in config.physical_hubs.items() if hub.expected_identity is not None
    }


def _desired(primary: PhysicalHubLabel, secondary: PhysicalHubLabel) -> DesiredRoleState:
    return DesiredRoleState(primary_physical_hub=primary, secondary_physical_hub=secondary)


def _plan(
    *,
    segment_id: str = "segment-preflight",
    scenario_id: str = "preflight",
    initial_primary: PhysicalHubLabel = PhysicalHubLabel.HUB_A,
    initial_secondary: PhysicalHubLabel = PhysicalHubLabel.HUB_B,
    final_primary: PhysicalHubLabel | None = None,
    final_secondary: PhysicalHubLabel | None = None,
    mutates_lab: bool = False,
) -> SegmentPlan:
    return SegmentPlan(
        segment_id=segment_id,
        scenario_id=scenario_id,
        expected_initial_role_state=_desired(initial_primary, initial_secondary),
        expected_final_role_state=_desired(final_primary or initial_primary, final_secondary or initial_secondary),
        mutates_lab=mutates_lab,
    )


def _profile_for_plan(plan: SegmentPlan, *, config: StableLabConfig | None = None) -> GeneratedProfile:
    config = config or _lab_config()
    return build_role_aware_profile(
        config,
        ObservedRoleState(
            primary_physical_hub=plan.expected_initial_role_state.primary_physical_hub,
            secondary_physical_hub=plan.expected_initial_role_state.secondary_physical_hub,
        ),
        segment_plan=plan,
    )


def _request_for_plan(
    plan: SegmentPlan,
    *,
    config: StableLabConfig | None = None,
    generated_profile: GeneratedProfile | None = None,
    artifact_root: str = ARTIFACT_ROOT,
):
    generated = generated_profile or _profile_for_plan(plan, config=config)
    return build_release_framework_request(
        plan=plan,
        generated_profile=generated,
        generated_profile_metadata=redact_generated_profile_metadata(generated),
        artifact_root=artifact_root,
        plan_id=PLAN_ID,
    )


def _materialized(
    plan: SegmentPlan,
    *,
    config: StableLabConfig | None = None,
    generated_profile: GeneratedProfile | None = None,
    artifact_root: str = ARTIFACT_ROOT,
    explicit_env: dict[str, str] | None = None,
):
    generated = generated_profile or _profile_for_plan(plan, config=config)
    request = _request_for_plan(plan, config=config, generated_profile=generated, artifact_root=artifact_root)
    return materialize_release_framework_request(
        request=request,
        generated_profile=generated,
        plan_id=PLAN_ID,
        artifact_root=artifact_root,
        explicit_env=explicit_env,
    )


def _summary_text(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True)


def test_materializes_non_mutating_release_framework_dry_run_without_execution() -> None:
    materialized = _materialized(_plan())

    assert materialized.argv.executed is False
    assert materialized.argv.pytest_target == "tests/release/test_release_certification.py"
    assert materialized.eligibility.dry_run_only is True
    assert materialized.eligibility.live_execution_supported is False
    assert materialized.eligibility.real_execution_evidence is False
    assert materialized.eligibility.live_certification_evidence is False


def test_materializes_mutating_release_framework_dry_run_without_execution() -> None:
    plan = _plan(
        segment_id="segment-python",
        scenario_id="python-passive-switchover",
        final_primary=PhysicalHubLabel.HUB_B,
        final_secondary=PhysicalHubLabel.HUB_A,
        mutates_lab=True,
    )

    materialized = _materialized(plan)

    assert materialized.argv.executed is False
    assert materialized.argv.scenario_selectors == ("python-passive-switchover",)
    assert materialized.argv.stream_selectors == ("python",)
    assert materialized.summary["real_execution_evidence"] is False
    assert materialized.summary["live_certification_evidence"] is False


def test_materialized_argv_includes_pytest_target_and_supported_release_options() -> None:
    materialized = _materialized(_plan())
    argv = materialized.argv.argv

    assert argv[:4] == ("python", "-m", "pytest", "tests/release/test_release_certification.py")
    assert "--release-profile" in argv
    assert "--release-mode" in argv
    assert "--release-scenario" in argv
    assert "--release-stream" in argv
    assert "--release-artifact-dir" in argv
    assert "focused-rerun" in argv
    assert "preflight" in argv
    assert "python" in argv
    assert "ansible" in argv


def test_materialized_argv_is_structured_not_shell_string() -> None:
    materialized = _materialized(_plan())

    assert isinstance(materialized.argv.argv, tuple)
    assert all(isinstance(item, str) for item in materialized.argv.argv)
    assert not isinstance(materialized.argv.argv, str)
    assert " ".join(materialized.argv.argv) == materialized.argv.to_display_command()


def test_materialized_display_command_is_sanitized_from_structured_argv(tmp_path: Path) -> None:
    raw_root = str(tmp_path / ".kube" / "release-output")
    materialized = _materialized(_plan(), artifact_root=raw_root)

    display_command = materialized.argv.to_display_command()

    assert raw_root not in display_command
    assert "[REDACTED]" in display_command
    assert isinstance(materialized.argv.argv, tuple)
    assert materialized.argv.artifact_directory == materialized.artifact_directory.runtime_path


def test_materialized_request_includes_scenario_and_stream_selectors_from_catalog_profile_data() -> None:
    materialized = _materialized(_plan())

    assert materialized.argv.scenario_selectors == ("preflight",)
    assert materialized.argv.stream_selectors == ("bash", "python", "ansible")
    assert materialized.profile_compatibility.selected_streams == ("bash", "python", "ansible")


@pytest.mark.parametrize(
    ("primary", "secondary"),
    (
        (PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        (PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
    ),
)
def test_profile_compatibility_passes_for_generated_role_aware_profiles(
    primary: PhysicalHubLabel,
    secondary: PhysicalHubLabel,
) -> None:
    plan = _plan(initial_primary=primary, initial_secondary=secondary)
    materialized = _materialized(plan)

    assert materialized.profile_compatibility.compatible is True
    assert materialized.profile_compatibility.contract_shape_compatible is True
    assert materialized.profile_compatibility.profile_hash_matches is True
    assert materialized.profile_compatibility.runtime_only_profile is True
    assert materialized.profile_compatibility.loader_compatible is False
    assert "runtime-only profile payload was not written to disk" in materialized.profile_compatibility.warnings


def test_profile_compatibility_fails_closed_for_missing_primary_hub() -> None:
    plan = _plan()
    generated = _profile_for_plan(plan)
    profile_data = json.loads(json.dumps(generated.profile_data, sort_keys=True))
    del profile_data["hubs"]["primary"]

    compatibility = verify_release_profile_compatibility(
        profile_data=profile_data,
        generated_profile_hash=generated.sha256,
        generated_profile_metadata=redact_generated_profile_metadata(generated),
        scenario_id="preflight",
        selected_streams=("python", "ansible"),
        request_profile_hash=generated.sha256,
    )

    assert compatibility.compatible is False
    assert any("hubs.primary" in error for error in compatibility.errors)


def test_profile_compatibility_fails_closed_for_missing_secondary_hub() -> None:
    plan = _plan()
    generated = _profile_for_plan(plan)
    profile_data = json.loads(json.dumps(generated.profile_data, sort_keys=True))
    del profile_data["hubs"]["secondary"]

    compatibility = verify_release_profile_compatibility(
        profile_data=profile_data,
        generated_profile_hash=generated.sha256,
        generated_profile_metadata=redact_generated_profile_metadata(generated),
        scenario_id="preflight",
        selected_streams=("python", "ansible"),
        request_profile_hash=generated.sha256,
    )

    assert compatibility.compatible is False
    assert any("hubs.secondary" in error for error in compatibility.errors)


def test_profile_compatibility_fails_closed_for_missing_managed_cluster_expectations() -> None:
    plan = _plan()
    generated = _profile_for_plan(plan)
    profile_data = json.loads(json.dumps(generated.profile_data, sort_keys=True))
    profile_data["managed_clusters"] = {}

    compatibility = verify_release_profile_compatibility(
        profile_data=profile_data,
        generated_profile_hash=generated.sha256,
        generated_profile_metadata=redact_generated_profile_metadata(generated),
        scenario_id="preflight",
        selected_streams=("python",),
        request_profile_hash=generated.sha256,
    )

    assert compatibility.compatible is False
    assert any("managed cluster" in error for error in compatibility.errors)


def test_profile_compatibility_fails_closed_for_unsupported_stream_settings() -> None:
    plan = _plan(
        segment_id="segment-python",
        scenario_id="python-passive-switchover",
        final_primary=PhysicalHubLabel.HUB_B,
        final_secondary=PhysicalHubLabel.HUB_A,
        mutates_lab=True,
    )
    generated = _profile_for_plan(plan)

    compatibility = verify_release_profile_compatibility(
        profile_data=generated.profile_data,
        generated_profile_hash=generated.sha256,
        generated_profile_metadata=redact_generated_profile_metadata(generated),
        scenario_id="python-passive-switchover",
        selected_streams=("ansible",),
        request_profile_hash=generated.sha256,
    )

    assert compatibility.compatible is False
    assert any("unsupported stream selector" in error for error in compatibility.errors)


def test_profile_compatibility_fails_closed_for_profile_hash_mismatch() -> None:
    plan = _plan()
    generated = _profile_for_plan(plan)

    compatibility = verify_release_profile_compatibility(
        profile_data=generated.profile_data,
        generated_profile_hash=generated.sha256,
        generated_profile_metadata={**redact_generated_profile_metadata(generated), "profile_sha256": "bad-hash"},
        scenario_id="preflight",
        selected_streams=("python",),
        request_profile_hash=generated.sha256,
    )

    assert compatibility.compatible is False
    assert compatibility.profile_hash_matches is False
    assert any("profile hash" in error for error in compatibility.errors)


def test_env_plan_rejects_and_redacts_kubeconfig_like_values() -> None:
    raw_path = "/home/operator/.kube/config"
    env_plan = build_release_framework_env_plan({"KUBECONFIG": raw_path, "SAFE_FLAG": "1"})
    summary = env_plan.to_summary()
    summary_text = _summary_text(summary)

    assert env_plan.safe is False
    assert "KUBECONFIG" in env_plan.rejected_keys
    assert "KUBECONFIG" not in summary_text
    assert raw_path not in summary_text
    assert summary["redacted_env"]["[REDACTED]"] == "[REDACTED]"
    assert summary["redacted_env"]["SAFE_FLAG"] == "1"


def test_env_plan_rejects_and_redacts_secret_markers_in_keys_and_values() -> None:
    env_plan = build_release_framework_env_plan(
        {
            "API_TOKEN": "value",
            "NORMAL": "password=super-secret",
            "ACM_RELEASE_PROFILE": "/tmp/private-profile.yaml",
        }
    )
    summary_text = _summary_text(env_plan.to_summary())

    assert env_plan.safe is False
    assert "API_TOKEN" in env_plan.rejected_keys
    assert "ACM_RELEASE_PROFILE" in env_plan.rejected_keys
    assert "API_TOKEN" not in summary_text
    assert "ACM_RELEASE_PROFILE" not in summary_text
    assert "super-secret" not in summary_text
    assert "/tmp/private-profile.yaml" not in summary_text


def test_artifact_directory_planning_is_deterministic() -> None:
    first = plan_release_framework_artifact_directory(
        plan_id=PLAN_ID,
        segment_id="segment-preflight",
        scenario_id="preflight",
        backend_mode="release_framework_dry_run",
        artifact_root=ARTIFACT_ROOT,
    )
    second = plan_release_framework_artifact_directory(
        plan_id=PLAN_ID,
        segment_id="segment-preflight",
        scenario_id="preflight",
        backend_mode="release_framework_dry_run",
        artifact_root=ARTIFACT_ROOT,
    )

    assert first == second
    assert first.runtime_path.endswith(
        "artifacts/release-lab/unit/phase6b-plan/segment-preflight/preflight/release_framework_dry_run"
    )
    assert first.to_summary() == second.to_summary()


def test_artifact_directory_planning_rejects_path_traversal() -> None:
    artifact_plan = plan_release_framework_artifact_directory(
        plan_id=PLAN_ID,
        segment_id="../segment",
        scenario_id="preflight",
        backend_mode="release_framework_dry_run",
        artifact_root=ARTIFACT_ROOT,
    )

    assert artifact_plan.safe is False
    assert "path traversal" in artifact_plan.reason


def test_artifact_directory_planning_rejects_unsafe_absolute_path_in_artifact_summary(tmp_path: Path) -> None:
    root = tmp_path / "release-output"
    artifact_plan = plan_release_framework_artifact_directory(
        plan_id=PLAN_ID,
        segment_id="segment-preflight",
        scenario_id="preflight",
        backend_mode="release_framework_dry_run",
        artifact_root=str(root),
    )
    summary = artifact_plan.to_summary()
    summary_text = _summary_text(summary)

    assert artifact_plan.safe is False
    assert summary["artifact_path_summary"] == "[REDACTED]"
    assert str(root) not in summary_text


def test_future_execution_eligibility_is_false_when_profile_compatibility_fails() -> None:
    materialized = _materialized(_plan())
    compatibility = replace(materialized.profile_compatibility, compatible=False, errors=("missing primary hub",))

    eligibility = evaluate_release_framework_execution_eligibility(
        scenario_id="preflight",
        execution_mode="release_framework_dry_run",
        selected_streams=("python",),
        profile_compatibility=compatibility,
        env_plan=materialized.env_plan,
        artifact_directory_plan=materialized.artifact_directory,
    )

    assert eligibility.eligible is False
    assert "profile_compatibility" in eligibility.blocking_fields


def test_future_execution_eligibility_is_false_for_unsupported_live_mode() -> None:
    materialized = _materialized(_plan())

    eligibility = evaluate_release_framework_execution_eligibility(
        scenario_id="preflight",
        execution_mode="release_framework_live",
        selected_streams=("python",),
        profile_compatibility=materialized.profile_compatibility,
        env_plan=materialized.env_plan,
        artifact_directory_plan=materialized.artifact_directory,
    )

    assert eligibility.eligible is False
    assert eligibility.live_execution_supported is False
    assert "execution_mode" in eligibility.blocking_fields


def test_release_framework_request_rejects_unsupported_release_mode() -> None:
    plan = _plan()
    generated = _profile_for_plan(plan)

    with pytest.raises(ValueError, match="unsupported release mode"):
        build_release_framework_request(
            plan=plan,
            generated_profile=generated,
            generated_profile_metadata=redact_generated_profile_metadata(generated),
            artifact_root=ARTIFACT_ROOT,
            release_mode="invented-mode",
        )


def test_future_execution_eligibility_is_false_for_unknown_scenario() -> None:
    materialized = _materialized(_plan())

    eligibility = evaluate_release_framework_execution_eligibility(
        scenario_id="future-scenario",
        execution_mode="release_framework_dry_run",
        selected_streams=("python",),
        profile_compatibility=materialized.profile_compatibility,
        env_plan=materialized.env_plan,
        artifact_directory_plan=materialized.artifact_directory,
    )

    assert eligibility.eligible is False
    assert "scenario_id" in eligibility.blocking_fields


def test_future_execution_eligibility_is_false_for_unsupported_stream() -> None:
    materialized = _materialized(_plan())

    eligibility = evaluate_release_framework_execution_eligibility(
        scenario_id="python-passive-switchover",
        execution_mode="release_framework_dry_run",
        selected_streams=("bash",),
        profile_compatibility=materialized.profile_compatibility,
        env_plan=materialized.env_plan,
        artifact_directory_plan=materialized.artifact_directory,
    )

    assert eligibility.eligible is False
    assert "selected_streams" in eligibility.blocking_fields


def test_future_execution_eligibility_is_false_for_destructive_scenario() -> None:
    materialized = _materialized(_plan())

    eligibility = evaluate_release_framework_execution_eligibility(
        scenario_id="decommission",
        execution_mode="release_framework_dry_run",
        selected_streams=("python",),
        profile_compatibility=materialized.profile_compatibility,
        env_plan=materialized.env_plan,
        artifact_directory_plan=materialized.artifact_directory,
    )

    assert eligibility.eligible is False
    assert "scenario_id" in eligibility.blocking_fields
    assert "destructive/disposable-lab-only" in eligibility.reason


def test_dry_run_materialization_never_sets_real_execution_or_live_certification_evidence() -> None:
    materialized = _materialized(_plan())
    summary = summarize_materialized_invocation(materialized)

    validate_materialized_invocation(materialized)
    assert materialized.eligibility.real_execution_evidence is False
    assert materialized.eligibility.live_certification_evidence is False
    assert summary["real_execution_evidence"] is False
    assert summary["live_certification_evidence"] is False


def test_dry_run_materialization_never_sets_live_certification_evidence() -> None:
    materialized = _materialized(_plan())

    assert materialized.eligibility.live_certification_evidence is False
    assert summarize_materialized_invocation(materialized)["live_certification_evidence"] is False


def test_planner_includes_materialized_dry_run_summaries_in_segment_and_run_artifacts() -> None:
    result = run_certification_plan(
        build_ping_pong_plan(plan_id=PLAN_ID),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
        execution_mode=ExecutionMode.RELEASE_FRAMEWORK_DRY_RUN,
    )
    first_segment = result.segment_results[0].artifact_payload
    run_summary = result.artifact_bundle.payload["materialized_release_framework"]

    assert first_segment["materialized_argv_summary"]["pytest_target"] == "tests/release/test_release_certification.py"
    assert first_segment["environment_plan_summary"]["safe"] is True
    assert first_segment["profile_compatibility_summary"]["compatible"] is True
    assert first_segment["artifact_directory_summary"]["safe"] is True
    assert first_segment["future_execution_eligibility"]["eligible"] is True
    assert first_segment["real_execution_evidence"] is False
    assert first_segment["live_certification_evidence"] is False
    assert run_summary["materialized_segments"] == 5
    assert run_summary["not_materialized_segments"] == []
    assert run_summary["real_execution_evidence_exists"] is False


def test_phase5_recovery_and_final_decision_behavior_is_unchanged_for_fake_execution_results() -> None:
    pass_result = run_certification_plan(
        build_ping_pong_plan(),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )
    no_go_plan = build_ping_pong_plan()
    no_go_result = run_certification_plan(
        replace(
            no_go_plan,
            segments=(
                replace(
                    no_go_plan.segments[0],
                    execution_result=replace(
                        no_go_plan.segments[0].execution_result,
                        status=ScenarioExecutionStatus.FAILED,
                        failure_reason="preflight failed",
                    ),
                ),
            )
            + no_go_plan.segments[1:],
        ),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )
    recovery_plan = build_ping_pong_plan()
    recovery_result = run_certification_plan(
        replace(
            recovery_plan,
            segments=(
                replace(
                    recovery_plan.segments[1],
                    execution_result=replace(
                        recovery_plan.segments[1].execution_result,
                        mutation_completed=False,
                        post_segment_observation=None,
                    ),
                    post_segment_observation=None,
                ),
            )
            + recovery_plan.segments[1:],
        ),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )
    retry_plan = build_ping_pong_plan()
    retry_result = run_certification_plan(
        replace(
            retry_plan,
            segments=(
                replace(
                    retry_plan.segments[0],
                    execution_result=replace(
                        retry_plan.segments[0].execution_result,
                        status=ScenarioExecutionStatus.FAILED,
                        retryable_infra_failure=True,
                        failure_reason="temporary artifact store outage",
                    ),
                ),
            )
            + retry_plan.segments[1:],
        ),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert pass_result.decision is CertificationDecision.PASS
    assert no_go_result.decision is CertificationDecision.NO_GO
    assert recovery_result.decision is CertificationDecision.RECOVERY_REQUIRED
    assert retry_result.decision is CertificationDecision.INFRA_RETRYABLE


def test_sensitive_values_in_materialized_summary_are_rejected_or_sanitized(tmp_path: Path) -> None:
    raw_url = "https://" + "api" + ".private" + ".cluster:6443"
    raw_path = str(tmp_path / ".kube" / "config")
    materialized = _materialized(
        _plan(),
        artifact_root=raw_path,
        explicit_env={
            "KUBECONFIG": raw_path,
            "API_TOKEN": "secret-token",
            "SAFE_DETAIL": raw_url,
        },
    )
    summary = summarize_materialized_invocation(materialized)
    summary_text = _summary_text(summary)

    assert materialized.env_plan.safe is False
    assert materialized.artifact_directory.safe is False
    assert materialized.eligibility.eligible is False
    assert raw_path not in summary_text
    assert raw_url not in summary_text
    assert "secret-token" not in summary_text
    assert "[REDACTED]" in summary_text


def test_dry_run_backend_materialization_blockers_fail_closed_before_mutation(tmp_path: Path) -> None:
    raw_root = str(tmp_path / ".kube" / "release-output")
    plan = _plan()

    result = run_segment(
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
        pre_segment_observation=fake_lab_observation(primary_label=PhysicalHubLabel.HUB_A),
        plan=plan,
        executor=ReleaseFrameworkDryRunBackend(explicit_env={"API_TOKEN": "secret-token"}),
        artifact_root=raw_root,
    )

    assert result.decision.decision.name == "NO_GO"
    assert result.execution_result is not None
    assert result.execution_result.mutation_attempted is False
    assert result.artifact_payload["future_execution_eligibility"]["eligible"] is False
    assert "materialization blocked" in result.decision.reason
    assert raw_root not in _summary_text(result.artifact_payload)
    assert "secret-token" not in _summary_text(result.artifact_payload)


def test_materialized_invocation_summary_is_stable_for_identical_inputs() -> None:
    first = summarize_materialized_invocation(_materialized(_plan()))
    second = summarize_materialized_invocation(_materialized(_plan()))

    assert first == second
