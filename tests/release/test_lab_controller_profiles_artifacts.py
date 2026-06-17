from __future__ import annotations

import json

import pytest

from tests.release.lab_controller.artifacts import build_segment_artifact
from tests.release.lab_controller.models import (
    ControllerDecision,
    DesiredRoleState,
    HubIdentityEvidence,
    ObservedRoleState,
    PhysicalHubConfig,
    PhysicalHubLabel,
    SegmentDecision,
    SegmentPlan,
    StableLabConfig,
)
from tests.release.lab_controller.profiles import (
    build_role_aware_profile,
    redact_generated_profile_metadata,
    validate_profile_role_mapping,
)


def _role_state(primary: PhysicalHubLabel, secondary: PhysicalHubLabel) -> ObservedRoleState:
    return ObservedRoleState(primary_physical_hub=primary, secondary_physical_hub=secondary)


def _desired(primary: PhysicalHubLabel, secondary: PhysicalHubLabel) -> DesiredRoleState:
    return DesiredRoleState(primary_physical_hub=primary, secondary_physical_hub=secondary)


def _identity(label: PhysicalHubLabel) -> HubIdentityEvidence:
    return HubIdentityEvidence(
        physical_label=label,
        kube_system_uid=f"uid-{label.value}",
        api_server_fingerprint=f"api-{label.value}",
        context_name=f"{label.value}-context",
    )


def _lab_config() -> StableLabConfig:
    return StableLabConfig(
        physical_hubs={
            PhysicalHubLabel.HUB_A: PhysicalHubConfig(
                physical_label=PhysicalHubLabel.HUB_A,
                kubeconfig_reference="hub-a-kubeconfig-ref",
                context_name="hub-a-context",
                expected_identity=_identity(PhysicalHubLabel.HUB_A),
            ),
            PhysicalHubLabel.HUB_B: PhysicalHubConfig(
                physical_label=PhysicalHubLabel.HUB_B,
                kubeconfig_reference="hub-b-kubeconfig-ref",
                context_name="hub-b-context",
                expected_identity=_identity(PhysicalHubLabel.HUB_B),
            ),
        },
        expected_managed_cluster_names=("mc-1", "mc-2", "mc-3"),
        enabled_streams=("python", "ansible"),
        scenario_ids=("preflight",),
        profile_name="lab-controller-unit",
        artifact_root="artifacts/release-lab/unit",
    )


def _segment_plan() -> SegmentPlan:
    return SegmentPlan(
        segment_id="segment-python",
        scenario_id="python-passive-switchover",
        expected_initial_role_state=_desired(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        expected_final_role_state=_desired(PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
        mutates_lab=True,
    )


def test_profile_maps_primary_to_hub_a_when_hub_a_is_observed_primary() -> None:
    generated = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
    )

    assert generated.profile_data["hubs"]["primary"]["context"] == "hub-a-context"
    assert generated.profile_data["hubs"]["secondary"]["context"] == "hub-b-context"
    assert generated.logical_to_physical["primary"] == "hub-a"
    assert len(generated.sha256) == 64


def test_profile_maps_primary_to_hub_b_when_hub_b_is_observed_primary() -> None:
    generated = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
    )

    assert generated.profile_data["hubs"]["primary"]["context"] == "hub-b-context"
    assert generated.profile_data["hubs"]["secondary"]["context"] == "hub-a-context"
    assert generated.logical_to_physical["primary"] == "hub-b"


def test_profile_hash_changes_when_role_mapping_changes() -> None:
    hub_a_primary = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
    )
    hub_b_primary = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
    )

    assert hub_a_primary.sha256 != hub_b_primary.sha256


def test_profile_generation_fails_closed_on_impossible_initial_role_mapping() -> None:
    malformed_initial_state = ObservedRoleState(
        primary_physical_hub=PhysicalHubLabel.HUB_A,
        secondary_physical_hub=PhysicalHubLabel.HUB_A,
    )

    with pytest.raises(ValueError, match="primary and secondary physical hubs must differ"):
        build_role_aware_profile(
            _lab_config(),
            malformed_initial_state,
            final_role_state=_role_state(PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
        )


def test_redacted_profile_metadata_does_not_expose_raw_kubeconfig_references() -> None:
    generated = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
    )

    redacted = redact_generated_profile_metadata(generated)
    redacted_text = json.dumps(redacted, sort_keys=True)

    assert "hub-a-kubeconfig-ref" not in redacted_text
    assert "hub-b-kubeconfig-ref" not in redacted_text
    assert redacted["redaction_status"] == "redacted"


def test_profile_validation_rejects_stale_mapping_after_role_transition() -> None:
    stale = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
    )

    decision = validate_profile_role_mapping(
        stale.profile_data,
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
    )

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.safe_to_continue is False
    assert "stale role-aware profile" in decision.reason


def test_pass_artifact_includes_initial_and_final_role_mapping() -> None:
    generated = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
    )

    artifact = build_segment_artifact(
        plan=_segment_plan(),
        observed_initial_role_state=_role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        desired_initial_role_state=_desired(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        observed_final_role_state=_role_state(PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
        controller_decision=ControllerDecision(SegmentDecision.PASS, "segment passed", True),
        managed_cluster_summary={"hub-a": {"observed": ["mc-1", "mc-2", "mc-3"]}},
        generated_profile_ref=redact_generated_profile_metadata(generated),
        redaction_status="ok",
    )
    artifact_text = json.dumps(artifact, sort_keys=True)

    assert artifact["controller_decision"] == "PASS"
    assert artifact["observed_initial_role_state"]["primary_physical_hub"] == "hub-a"
    assert artifact["expected_final_role_state"]["primary_physical_hub"] == "hub-b"
    assert artifact["observed_final_role_state"]["primary_physical_hub"] == "hub-b"
    assert artifact["generated_profile"]["profile_sha256"] == generated.sha256
    assert "hub-a-kubeconfig-ref" not in artifact_text
    assert "hub-b-kubeconfig-ref" not in artifact_text


def test_artifact_rejects_unredacted_generated_profile_metadata() -> None:
    generated = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
    )

    with pytest.raises(ValueError, match="generated profile metadata must be redacted"):
        build_segment_artifact(
            plan=_segment_plan(),
            observed_initial_role_state=_role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
            desired_initial_role_state=_desired(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
            observed_final_role_state=_role_state(PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
            controller_decision=ControllerDecision(SegmentDecision.PASS, "segment passed", True),
            managed_cluster_summary={"hub-a": {"observed": ["mc-1", "mc-2", "mc-3"]}},
            generated_profile_ref=generated.metadata,
            redaction_status="ok",
        )


def test_artifact_rejects_live_certification_evidence_claims() -> None:
    with pytest.raises(ValueError, match="cannot claim live certification evidence"):
        build_segment_artifact(
            plan=_segment_plan(),
            observed_initial_role_state=_role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
            desired_initial_role_state=_desired(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
            observed_final_role_state=_role_state(PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
            controller_decision=ControllerDecision(SegmentDecision.PASS, "segment passed", True),
            managed_cluster_summary={"hub-a": {"observed": ["mc-1", "mc-2", "mc-3"]}},
            execution_request_summary={
                "execution_backend": "release_framework",
                "execution_mode": "release_framework_local",
                "dry_run": False,
                "real_execution_evidence": True,
                "live_certification_evidence": True,
            },
            redaction_status="redacted",
        )


def test_artifact_rejects_dry_run_real_execution_evidence_claims() -> None:
    with pytest.raises(ValueError, match="dry-run segment artifacts cannot claim real execution evidence"):
        build_segment_artifact(
            plan=_segment_plan(),
            observed_initial_role_state=_role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
            desired_initial_role_state=_desired(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
            observed_final_role_state=_role_state(PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
            controller_decision=ControllerDecision(SegmentDecision.PASS, "segment passed", True),
            managed_cluster_summary={"hub-a": {"observed": ["mc-1", "mc-2", "mc-3"]}},
            execution_request_summary={
                "execution_backend": "release_framework",
                "execution_mode": "release_framework_dry_run",
                "dry_run": True,
                "real_execution_evidence": True,
                "live_certification_evidence": False,
            },
            redaction_status="redacted",
        )


def test_recovery_required_artifact_includes_reason() -> None:
    artifact = build_segment_artifact(
        plan=_segment_plan(),
        observed_initial_role_state=ObservedRoleState(None, None, "neither hub is active"),
        desired_initial_role_state=_desired(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        observed_final_role_state=None,
        controller_decision=ControllerDecision(
            SegmentDecision.RECOVERY_REQUIRED,
            "neither hub is active",
            False,
            recovery_hint="operator must restore a known active hub",
        ),
        managed_cluster_summary={},
        redaction_status="not_published",
    )

    assert artifact["controller_decision"] == "RECOVERY_REQUIRED"
    assert artifact["reason"] == "neither hub is active"
    assert artifact["recovery_hint"] == "operator must restore a known active hub"


def test_no_go_artifact_includes_blocking_reason_and_redaction_status() -> None:
    artifact = build_segment_artifact(
        plan=_segment_plan(),
        observed_initial_role_state=ObservedRoleState(None, None, "both hubs active"),
        desired_initial_role_state=_desired(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        observed_final_role_state=None,
        controller_decision=ControllerDecision(SegmentDecision.NO_GO, "both hubs active", False),
        managed_cluster_summary={},
        redaction_status="redacted",
    )

    assert artifact["controller_decision"] == "NO_GO"
    assert artifact["reason"] == "both hubs active"
    assert artifact["redaction_status"] == "redacted"
