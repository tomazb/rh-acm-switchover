from __future__ import annotations

import json
from dataclasses import replace

import pytest

from tests.release.lab_controller.controller import ScenarioExecutionStatus, run_segment
from tests.release.lab_controller.discovery import fake_lab_observation
from tests.release.lab_controller.execution import (
    ExecutionBackendKind,
    ExecutionMode,
    FakeExecutionBackend,
    ReleaseFrameworkDryRunBackend,
    build_release_framework_request,
    summarize_execution_request,
    validate_execution_request,
)
from tests.release.lab_controller.models import (
    DesiredRoleState,
    HubIdentityEvidence,
    ObservedRoleState,
    PhysicalHubConfig,
    PhysicalHubLabel,
    SegmentDecision,
    SegmentPlan,
    StableLabConfig,
)
from tests.release.lab_controller.planner import (
    CertificationDecision,
    build_ping_pong_plan,
    run_certification_plan,
)
from tests.release.lab_controller.profiles import build_role_aware_profile, redact_generated_profile_metadata
from tests.release.scenarios.catalog import SCENARIOS_BY_ID

EXPECTED_CLUSTERS = ("mc-1", "mc-2", "mc-3")


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


def _lab_config() -> StableLabConfig:
    return StableLabConfig(
        physical_hubs={
            PhysicalHubLabel.HUB_A: _hub_config(PhysicalHubLabel.HUB_A),
            PhysicalHubLabel.HUB_B: _hub_config(PhysicalHubLabel.HUB_B),
        },
        expected_managed_cluster_names=EXPECTED_CLUSTERS,
        enabled_streams=("python", "ansible"),
        scenario_ids=("preflight",),
        profile_name="lab-controller-phase6",
        artifact_root="artifacts/release-lab/unit",
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


def _profile_for_plan(plan: SegmentPlan):
    return build_role_aware_profile(
        _lab_config(),
        ObservedRoleState(
            primary_physical_hub=plan.expected_initial_role_state.primary_physical_hub,
            secondary_physical_hub=plan.expected_initial_role_state.secondary_physical_hub,
        ),
        segment_plan=plan,
    )


def _request_for_plan(plan: SegmentPlan):
    generated = _profile_for_plan(plan)
    return build_release_framework_request(
        plan=plan,
        generated_profile=generated,
        generated_profile_metadata=redact_generated_profile_metadata(generated),
        artifact_root="artifacts/release-lab/unit",
    )


def test_fake_backend_preserves_existing_fake_execution_behavior() -> None:
    plan = build_ping_pong_plan()

    result = run_certification_plan(
        plan,
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert result.decision is CertificationDecision.PASS
    assert [segment.decision for segment in result.segment_results] == [CertificationDecision.PASS] * 5
    assert result.artifact_bundle.payload["execution_backends"]["real_execution_evidence_exists"] is False


def test_release_framework_dry_run_builds_deterministic_request_for_non_mutating_segment() -> None:
    request = _request_for_plan(_plan())
    same_request = _request_for_plan(_plan())

    assert request == same_request
    assert request.backend_kind is ExecutionBackendKind.RELEASE_FRAMEWORK
    assert request.execution_mode is ExecutionMode.RELEASE_FRAMEWORK_DRY_RUN
    assert request.scenario_id == "preflight"
    assert request.selected_streams == ("python", "ansible")
    assert request.dry_run is True
    assert request.request_hash == same_request.request_hash


def test_release_framework_dry_run_builds_mutating_request_without_real_execution() -> None:
    plan = _plan(
        segment_id="segment-python",
        scenario_id="python-passive-switchover",
        final_primary=PhysicalHubLabel.HUB_B,
        final_secondary=PhysicalHubLabel.HUB_A,
        mutates_lab=True,
    )
    backend = ReleaseFrameworkDryRunBackend(
        simulated_status=ScenarioExecutionStatus.SUCCEEDED.value,
        simulated_mutation_attempted=True,
        simulated_mutation_completed=True,
        simulated_post_segment_observation=fake_lab_observation(primary_label=PhysicalHubLabel.HUB_B),
    )

    execution = backend.execute(
        plan,
        _profile_for_plan(plan),
        generated_profile_metadata=redact_generated_profile_metadata(_profile_for_plan(plan)),
        artifact_root="artifacts/release-lab/unit",
    )

    assert execution.request.scenario_id == "python-passive-switchover"
    assert execution.request.selected_streams == ("python",)
    assert execution.dry_run is True
    assert execution.real_execution_evidence is False
    assert execution.live_certification_evidence is False
    assert execution.mutation_attempted is True


def test_dry_run_request_summary_contains_required_release_fields() -> None:
    request = _request_for_plan(_plan())
    summary = summarize_execution_request(request)

    assert summary["intended_pytest_target"] == "tests/release/test_release_certification.py"
    assert summary["intended_release_mode"] == "focused-rerun"
    assert summary["intended_scenario"] == "preflight"
    assert summary["intended_stream"] == ["python", "ansible"]
    assert summary["generated_profile_hash"] == request.generated_profile_hash
    assert summary["intended_artifact_dir"] == "artifacts/release-lab/unit"
    assert summary["dry_run"] is True
    assert summary["execution_request_redaction_status"] == "redacted"


def test_dry_run_request_uses_real_catalog_scenario_ids() -> None:
    request = _request_for_plan(_plan())

    assert request.scenario_id in SCENARIOS_BY_ID
    assert SCENARIOS_BY_ID[request.scenario_id].id == request.scenario_id


def test_unknown_scenario_fails_closed_before_request_is_built() -> None:
    with pytest.raises(ValueError, match="unknown release scenario"):
        _request_for_plan(_plan(scenario_id="future-release-scenario"))


def test_destructive_scenario_fails_closed_in_certification_dry_run_mode() -> None:
    with pytest.raises(ValueError, match="destructive/disposable-lab-only"):
        _request_for_plan(_plan(scenario_id="decommission", mutates_lab=True))


def test_unredacted_generated_profile_metadata_is_rejected() -> None:
    plan = _plan()
    generated = _profile_for_plan(plan)

    with pytest.raises(ValueError, match="generated profile metadata must be redacted"):
        build_release_framework_request(
            plan=plan,
            generated_profile=generated,
            generated_profile_metadata=generated.metadata,
            artifact_root="artifacts/release-lab/unit",
        )


def test_raw_kubeconfig_like_path_in_execution_summary_is_sanitized() -> None:
    request = replace(
        _request_for_plan(_plan()),
        execution_summary={"debug": "would inspect /home/operator/.kube/config"},
    )
    summary = summarize_execution_request(request)
    summary_text = json.dumps(summary, sort_keys=True)

    validate_execution_request(request)
    assert "/home/operator/.kube/config" not in summary_text
    assert summary["execution_summary"]["debug"] == "would inspect [REDACTED]"


def test_raw_api_server_url_in_execution_summary_is_sanitized() -> None:
    raw_url = "https://" + "api" + ".private" + ".cluster:6443"
    request = replace(_request_for_plan(_plan()), execution_summary={"api_server": raw_url})
    summary = summarize_execution_request(request)
    summary_text = json.dumps(summary, sort_keys=True)

    validate_execution_request(request)
    assert raw_url not in summary_text
    assert summary["execution_summary"]["api_server"] == "[REDACTED]"


def test_raw_sensitive_keys_in_execution_summary_are_sanitized() -> None:
    raw_path_key = "/home/operator/.kube/config"
    raw_url_key = "https://" + "api" + ".private" + ".cluster:6443"
    request = replace(
        _request_for_plan(_plan()),
        execution_summary={
            raw_path_key: "path-key",
            raw_url_key: "url-key",
            "nested": {raw_path_key: "nested-path-key"},
        },
    )
    summary = summarize_execution_request(request)
    summary_text = json.dumps(summary, sort_keys=True)

    validate_execution_request(request)
    assert raw_path_key not in summary_text
    assert raw_url_key not in summary_text
    assert "[REDACTED]" in summary["execution_summary"]
    assert "[REDACTED]" in summary["execution_summary"]["nested"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("segment_id", "", "missing required identity fields"),
        ("selected_streams", (), "must select at least one stream"),
        ("intended_release_mode", "", "missing release mode"),
        ("intended_artifact_dir", "", "missing artifact directory"),
        ("redaction_status", "not_redacted", "redaction status"),
    ),
)
def test_missing_required_request_fields_fail_closed(field: str, value: object, message: str) -> None:
    request = replace(_request_for_plan(_plan()), **{field: value})

    with pytest.raises(ValueError, match=message):
        validate_execution_request(request)


def test_dry_run_execution_does_not_mark_real_execution_evidence() -> None:
    backend = ReleaseFrameworkDryRunBackend()
    plan = _plan()
    execution = backend.execute(
        plan,
        _profile_for_plan(plan),
        generated_profile_metadata=redact_generated_profile_metadata(_profile_for_plan(plan)),
    )

    assert execution.real_execution_evidence is False


def test_dry_run_execution_cannot_be_mistaken_for_live_certification_evidence() -> None:
    backend = ReleaseFrameworkDryRunBackend()
    plan = _plan()
    execution = backend.execute(
        plan,
        _profile_for_plan(plan),
        generated_profile_metadata=redact_generated_profile_metadata(_profile_for_plan(plan)),
    )
    summary = execution.request_summary

    assert execution.status == ScenarioExecutionStatus.SUCCEEDED.value
    assert summary["dry_run"] is True
    assert summary["real_execution_evidence"] is False
    assert summary["live_certification_evidence"] is False
    assert summary["evidence_status"] == "dry_run_only"


def test_planner_can_run_full_fake_ping_pong_plan_with_existing_fake_backend() -> None:
    result = run_certification_plan(
        build_ping_pong_plan(),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert result.decision is CertificationDecision.PASS
    assert result.artifact_bundle.payload["execution_backends"]["backend_counts"] == {"fake": 5}


def test_planner_can_build_dry_run_requests_for_all_planned_segments_without_live_execution() -> None:
    result = run_certification_plan(
        build_ping_pong_plan(),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
        execution_mode=ExecutionMode.RELEASE_FRAMEWORK_DRY_RUN,
    )

    assert result.decision is CertificationDecision.PASS
    assert len(result.segment_results) == 5
    assert result.artifact_bundle.payload["execution_backends"]["backend_counts"] == {"release_framework": 5}
    assert all(
        segment.artifact_payload["execution_mode"] == "release_framework_dry_run" for segment in result.segment_results
    )
    assert all(segment.artifact_payload["dry_run"] is True for segment in result.segment_results)


def test_run_level_artifact_records_backend_kind_and_mode_per_segment() -> None:
    result = run_certification_plan(
        build_ping_pong_plan(),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
        execution_mode=ExecutionMode.RELEASE_FRAMEWORK_DRY_RUN,
    )

    per_segment = result.artifact_bundle.payload["execution_backends"]["per_segment"]

    assert [item["backend_kind"] for item in per_segment] == ["release_framework"] * 5
    assert [item["execution_mode"] for item in per_segment] == ["release_framework_dry_run"] * 5
    assert result.artifact_bundle.payload["execution_backends"]["real_execution_evidence_exists"] is False


def test_dry_run_mode_cannot_bypass_known_state_gates() -> None:
    plan = build_ping_pong_plan()
    stale_initial_observation = replace(
        plan.segments[0],
        pre_segment_observation=fake_lab_observation(primary_label=PhysicalHubLabel.HUB_B),
    )

    result = run_certification_plan(
        replace(plan, segments=(stale_initial_observation,) + plan.segments[1:]),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
        execution_mode=ExecutionMode.RELEASE_FRAMEWORK_DRY_RUN,
    )

    assert result.decision is CertificationDecision.NO_GO
    assert result.segment_results[0].controller_result is not None
    assert result.segment_results[0].controller_result.execution_result is None
    assert "does not match expected initial role state" in result.first_blocking_reason


def test_dry_run_mode_cannot_bypass_profile_freshness() -> None:
    plan = _plan()
    stale_profile = build_role_aware_profile(
        _lab_config(),
        ObservedRoleState(
            primary_physical_hub=PhysicalHubLabel.HUB_B,
            secondary_physical_hub=PhysicalHubLabel.HUB_A,
        ),
        segment_plan=plan,
    )

    result = run_segment(
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
        pre_segment_observation=fake_lab_observation(primary_label=PhysicalHubLabel.HUB_A),
        plan=plan,
        executor=ReleaseFrameworkDryRunBackend(),
        generated_profile=stale_profile,
    )

    assert result.decision.decision is SegmentDecision.NO_GO
    assert result.execution_result is None
    assert "stale role-aware profile" in result.decision.reason


def test_dry_run_mode_cannot_bypass_redaction_checks() -> None:
    plan = _plan()
    generated = _profile_for_plan(plan)
    unredactable_profile = replace(
        generated,
        metadata={
            **generated.metadata,
            "hubs": {"primary": {"physical_label": PhysicalHubLabel.HUB_A.value}},
        },
    )

    result = run_segment(
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
        pre_segment_observation=fake_lab_observation(primary_label=PhysicalHubLabel.HUB_A),
        plan=plan,
        executor=ReleaseFrameworkDryRunBackend(),
        generated_profile=unredactable_profile,
    )

    assert result.decision.decision is SegmentDecision.NO_GO
    assert result.execution_result is None
    assert "redaction failure" in result.decision.reason


def test_dry_run_backend_failure_is_pre_mutation_no_go_not_recovery() -> None:
    plan = _plan()

    result = run_segment(
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
        pre_segment_observation=fake_lab_observation(primary_label=PhysicalHubLabel.HUB_A),
        plan=plan,
        executor=ReleaseFrameworkDryRunBackend(
            simulated_status=ScenarioExecutionStatus.FAILED.value,
            simulated_failure_reason="request construction failed",
            simulated_mutation_attempted=False,
            simulated_mutation_completed=False,
        ),
    )

    assert result.decision.decision is SegmentDecision.NO_GO
    assert result.execution_result is not None
    assert result.execution_result.mutation_attempted is False
    assert "failed before mutation" in result.decision.reason


def test_recovery_and_final_decision_behavior_is_unchanged_for_fake_results() -> None:
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
                    recovery_plan.segments[0],
                    pre_segment_observation=fake_lab_observation(primary_label=None),
                ),
            )
            + recovery_plan.segments[1:],
        ),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert pass_result.decision is CertificationDecision.PASS
    assert no_go_result.decision is CertificationDecision.NO_GO
    assert recovery_result.decision is CertificationDecision.RECOVERY_REQUIRED


def test_unsupported_future_live_backend_fails_closed() -> None:
    plan = _plan()

    with pytest.raises(ValueError, match="live release-framework execution is not supported"):
        build_release_framework_request(
            plan=plan,
            generated_profile=_profile_for_plan(plan),
            generated_profile_metadata=redact_generated_profile_metadata(_profile_for_plan(plan)),
            execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LIVE,
        )


def test_execution_request_hash_and_summary_are_stable_for_identical_input() -> None:
    first = _request_for_plan(_plan())
    second = _request_for_plan(_plan())

    assert first.request_hash == second.request_hash
    assert summarize_execution_request(first) == summarize_execution_request(second)


def test_fake_execution_backend_returns_supplied_simulated_result() -> None:
    backend = FakeExecutionBackend(
        scenario_id="preflight",
        status=ScenarioExecutionStatus.FAILED.value,
        failure_reason="simulated failure",
    )
    plan = _plan()
    execution = backend.execute(
        plan,
        _profile_for_plan(plan),
        generated_profile_metadata=redact_generated_profile_metadata(_profile_for_plan(plan)),
    )

    assert execution.backend_kind is ExecutionBackendKind.FAKE
    assert execution.execution_mode is ExecutionMode.FAKE
    assert execution.status == ScenarioExecutionStatus.FAILED.value
    assert execution.failure_reason == "simulated failure"
