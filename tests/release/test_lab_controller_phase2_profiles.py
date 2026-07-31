from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.release.contracts.loader import load_profile
from tests.release.lab_controller.artifacts import build_segment_artifact
from tests.release.lab_controller.models import (
    ControllerDecision,
    DesiredRoleState,
    HubIdentityEvidence,
    LabArgoCDSettings,
    LabArtifactSettings,
    LabReleaseMetadata,
    ManagedClusterInventory,
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
    validate_generated_profile_freshness,
    write_generated_profile_yaml,
)
from tests.release.lab_controller.segments import generate_segment_profile

EXPECTED_CLUSTERS = ("mc-1", "mc-2", "mc-3")


def _identity(label: PhysicalHubLabel, *, uid_suffix: str | None = None) -> HubIdentityEvidence:
    suffix = uid_suffix or label.value
    return HubIdentityEvidence(
        physical_label=label,
        kube_system_uid=f"uid-{suffix}",
        api_server_fingerprint=f"api-{suffix}",
        context_name=f"{label.value}-context",
        cluster_version="4.16",
        acm_evidence=f"mch-{label.value}",
    )


def _hub_config(label: PhysicalHubLabel) -> PhysicalHubConfig:
    return PhysicalHubConfig(
        physical_label=label,
        kubeconfig_reference=f"kubeconfig-ref-{label.value}",
        context_name=f"{label.value}-context",
        expected_identity=_identity(label),
    )


def _lab_config(*, artifact_root: str = "artifacts/release-lab/unit") -> StableLabConfig:
    return StableLabConfig(
        physical_hubs={
            PhysicalHubLabel.HUB_A: _hub_config(PhysicalHubLabel.HUB_A),
            PhysicalHubLabel.HUB_B: _hub_config(PhysicalHubLabel.HUB_B),
        },
        managed_clusters=ManagedClusterInventory(
            expected_names=EXPECTED_CLUSTERS,
            contexts={"mc-1": "mc-1-context", "mc-2": "mc-2-context"},
        ),
        enabled_streams=("python", "ansible"),
        scenario_ids=("preflight",),
        profile_name="lab-controller-generated",
        artifacts=LabArtifactSettings(root=artifact_root, redaction_required=True),
        release=LabReleaseMetadata(expected_version="1.7.10", metadata_files=("README.md",)),
        argocd=LabArgoCDSettings(mandatory=True, namespaces=("openshift-gitops",)),
    )


def _role_state(primary: PhysicalHubLabel, secondary: PhysicalHubLabel) -> ObservedRoleState:
    return ObservedRoleState(primary_physical_hub=primary, secondary_physical_hub=secondary)


def _desired(primary: PhysicalHubLabel, secondary: PhysicalHubLabel) -> DesiredRoleState:
    return DesiredRoleState(primary_physical_hub=primary, secondary_physical_hub=secondary)


def _pass_decision(reason: str = "proven") -> ControllerDecision:
    return ControllerDecision(SegmentDecision.PASS, reason, True)


def _switchover_plan(
    *,
    segment_id: str = "segment-python",
    scenario_id: str = "python-passive-switchover",
    initial_primary: PhysicalHubLabel = PhysicalHubLabel.HUB_A,
    initial_secondary: PhysicalHubLabel = PhysicalHubLabel.HUB_B,
) -> SegmentPlan:
    return SegmentPlan(
        segment_id=segment_id,
        scenario_id=scenario_id,
        expected_initial_role_state=_desired(initial_primary, initial_secondary),
        expected_final_role_state=_desired(initial_secondary, initial_primary),
        mutates_lab=True,
    )


def test_generated_profile_uses_existing_release_profile_contract(tmp_path: Path) -> None:
    generated = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        segment_plan=_switchover_plan(),
    )

    profile_path = write_generated_profile_yaml(generated, tmp_path / "generated-profile.yaml")
    loaded = load_profile(profile_path)

    assert loaded.profile.hubs["primary"].context == "hub-a-context"
    assert loaded.profile.hubs["secondary"].context == "hub-b-context"
    assert loaded.profile.managed_clusters.expected_names == EXPECTED_CLUSTERS
    assert loaded.profile.managed_clusters.contexts["mc-1"] == "mc-1-context"
    assert tuple(stream.id for stream in loaded.profile.streams) == ("python", "ansible")
    assert tuple(scenario.id for scenario in loaded.profile.scenarios) == ("python-passive-switchover",)
    assert loaded.profile.release is not None
    assert loaded.profile.release.expected_version == "1.7.10"
    assert loaded.profile.argocd.mandatory is True
    assert loaded.profile.argocd.namespaces == ("openshift-gitops",)
    assert loaded.profile.artifacts.root == "artifacts/release-lab/unit"


def test_generated_profile_hash_is_stable_for_identical_input() -> None:
    first = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        segment_plan=_switchover_plan(),
    )
    second = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        segment_plan=_switchover_plan(),
    )

    assert first.sha256 == second.sha256
    assert len(first.sha256) == 64


def test_generated_profile_hash_changes_when_logical_role_mapping_changes() -> None:
    hub_a_primary = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        segment_plan=_switchover_plan(),
    )
    hub_b_primary = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
        segment_plan=_switchover_plan(
            segment_id="segment-ansible",
            scenario_id="ansible-passive-switchover",
            initial_primary=PhysicalHubLabel.HUB_B,
            initial_secondary=PhysicalHubLabel.HUB_A,
        ),
    )

    assert hub_a_primary.sha256 != hub_b_primary.sha256


def test_generated_profile_hash_changes_when_identity_fingerprint_changes() -> None:
    config = _lab_config()
    generated = build_role_aware_profile(
        config,
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        segment_plan=_switchover_plan(),
    )
    drifted_hub_a = replace(
        config.physical_hubs[PhysicalHubLabel.HUB_A],
        expected_identity=_identity(PhysicalHubLabel.HUB_A, uid_suffix="hub-a-recreated"),
    )
    drifted_config = replace(
        config,
        physical_hubs={
            **config.physical_hubs,
            PhysicalHubLabel.HUB_A: drifted_hub_a,
        },
    )
    drifted_generated = build_role_aware_profile(
        drifted_config,
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        segment_plan=_switchover_plan(),
    )

    assert generated.sha256 != drifted_generated.sha256


def test_generated_profile_hash_changes_when_managed_cluster_expectations_change() -> None:
    generated = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        segment_plan=_switchover_plan(),
    )
    drifted_config = replace(
        _lab_config(),
        managed_clusters=ManagedClusterInventory(expected_names=("mc-1", "mc-2", "mc-3", "mc-4")),
    )
    drifted_generated = build_role_aware_profile(
        drifted_config,
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        segment_plan=_switchover_plan(),
    )

    assert generated.sha256 != drifted_generated.sha256


def test_profile_generation_rejects_impossible_observed_role_mapping() -> None:
    with pytest.raises(ValueError, match="primary and secondary physical hubs must differ"):
        build_role_aware_profile(
            _lab_config(),
            ObservedRoleState(
                primary_physical_hub=PhysicalHubLabel.HUB_A,
                secondary_physical_hub=PhysicalHubLabel.HUB_A,
            ),
            segment_plan=_switchover_plan(),
        )


def test_raw_kubeconfig_references_are_limited_to_runtime_profile_data() -> None:
    generated = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        segment_plan=_switchover_plan(),
    )

    runtime_profile_text = json.dumps(generated.profile_data, sort_keys=True)
    metadata_text = json.dumps(generated.metadata, sort_keys=True)

    assert "kubeconfig-ref-hub-a" in runtime_profile_text
    assert "kubeconfig-ref-hub-b" in runtime_profile_text
    assert "kubeconfig-ref-hub-a" not in metadata_text
    assert "kubeconfig-ref-hub-b" not in metadata_text
    assert generated.metadata["generated"] is True
    assert generated.metadata["runtime_only"] is True


def test_artifact_metadata_redacts_kubeconfig_references() -> None:
    generated = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        segment_plan=_switchover_plan(),
    )

    artifact_metadata = redact_generated_profile_metadata(generated)
    metadata_text = json.dumps(artifact_metadata, sort_keys=True)

    assert artifact_metadata["redaction_status"] == "redacted"
    assert artifact_metadata["hubs"]["primary"]["kubeconfig_reference"] == "[REDACTED]"
    assert artifact_metadata["hubs"]["secondary"]["kubeconfig_reference"] == "[REDACTED]"
    assert "kubeconfig-ref-hub-a" not in metadata_text
    assert "kubeconfig-ref-hub-b" not in metadata_text


def test_artifact_metadata_rejects_unredacted_kubeconfig_path() -> None:
    generated = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        segment_plan=_switchover_plan(),
    )
    unsafe_metadata = redact_generated_profile_metadata(generated)
    unsafe_metadata["hubs"]["primary"]["kubeconfig_reference"] = "UNREDACTED-KUBECONFIG-PATH"

    with pytest.raises(ValueError, match="generated profile metadata must be redacted"):
        build_segment_artifact(
            plan=_switchover_plan(),
            observed_initial_role_state=_role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
            desired_initial_role_state=_desired(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
            observed_final_role_state=None,
            controller_decision=_pass_decision(),
            managed_cluster_summary={},
            generated_profile_ref=unsafe_metadata,
            redaction_status="redacted",
        )


def test_artifact_metadata_rejects_unexpected_raw_kubeconfig_like_path() -> None:
    generated = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        segment_plan=_switchover_plan(),
    )
    unsafe_metadata = redact_generated_profile_metadata(generated)
    unsafe_metadata["debug_path"] = "/home/operator/.kube/config"

    with pytest.raises(ValueError, match="generated profile metadata must be redacted"):
        build_segment_artifact(
            plan=_switchover_plan(),
            observed_initial_role_state=_role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
            desired_initial_role_state=_desired(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
            observed_final_role_state=None,
            controller_decision=_pass_decision(),
            managed_cluster_summary={},
            generated_profile_ref=unsafe_metadata,
            redaction_status="redacted",
        )


def test_stale_profile_detection_rejects_role_mapping_drift() -> None:
    generated = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        segment_plan=_switchover_plan(),
    )

    decision = validate_generated_profile_freshness(
        generated,
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
    )

    assert decision.decision is SegmentDecision.NO_GO
    assert "does not map to observed primary hub-b" in decision.reason


def test_stale_profile_detection_rejects_managed_cluster_set_drift() -> None:
    generated = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        segment_plan=_switchover_plan(),
    )
    drifted_config = replace(
        _lab_config(),
        managed_clusters=ManagedClusterInventory(expected_names=("mc-1", "mc-2")),
    )

    decision = validate_generated_profile_freshness(
        generated,
        drifted_config,
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
    )

    assert decision.decision is SegmentDecision.NO_GO
    assert "managed cluster set" in decision.reason


def test_stale_profile_detection_rejects_physical_hub_identity_drift() -> None:
    config = _lab_config()
    generated = build_role_aware_profile(
        config,
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        segment_plan=_switchover_plan(),
    )
    drifted_hub_a = replace(
        config.physical_hubs[PhysicalHubLabel.HUB_A],
        expected_identity=_identity(PhysicalHubLabel.HUB_A, uid_suffix="hub-a-recreated"),
    )
    drifted_config = replace(
        config,
        physical_hubs={
            **config.physical_hubs,
            PhysicalHubLabel.HUB_A: drifted_hub_a,
        },
    )

    decision = validate_generated_profile_freshness(
        generated,
        drifted_config,
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
    )

    assert decision.decision is SegmentDecision.NO_GO
    assert "identity fingerprint" in decision.reason


def test_stale_profile_detection_rejects_recorded_hash_mismatch() -> None:
    generated = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        segment_plan=_switchover_plan(),
    )
    tampered_metadata = {**generated.metadata, "profile_sha256": "0" * 64}
    tampered = replace(generated, metadata=tampered_metadata)

    decision = validate_generated_profile_freshness(
        tampered,
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
    )

    assert decision.decision is SegmentDecision.NO_GO
    assert "profile hash" in decision.reason


def test_segment_with_hub_a_primary_generates_hub_a_primary_profile() -> None:
    result = generate_segment_profile(
        _switchover_plan(),
        lab_config=_lab_config(),
        observed_role_state=_role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        identity_decision=_pass_decision("identity proven"),
        role_decision=_pass_decision("role state proven"),
        artifact_root="artifacts/release-lab/run-a",
    )

    assert result.decision.decision is SegmentDecision.PASS
    assert result.generated_profile is not None
    assert result.generated_profile.profile_data["hubs"]["primary"]["context"] == "hub-a-context"
    assert result.generated_profile.profile_data["artifacts"]["root"] == "artifacts/release-lab/run-a"


def test_segment_with_hub_b_primary_generates_hub_b_primary_profile() -> None:
    result = generate_segment_profile(
        _switchover_plan(
            segment_id="segment-ansible",
            scenario_id="ansible-passive-switchover",
            initial_primary=PhysicalHubLabel.HUB_B,
            initial_secondary=PhysicalHubLabel.HUB_A,
        ),
        lab_config=_lab_config(),
        observed_role_state=_role_state(PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
        identity_decision=_pass_decision("identity proven"),
        role_decision=_pass_decision("role state proven"),
    )

    assert result.decision.decision is SegmentDecision.PASS
    assert result.generated_profile is not None
    assert result.generated_profile.profile_data["hubs"]["primary"]["context"] == "hub-b-context"


def test_mutating_segment_cannot_get_generated_profile_from_ambiguous_state() -> None:
    result = generate_segment_profile(
        _switchover_plan(),
        lab_config=_lab_config(),
        observed_role_state=ObservedRoleState(None, None, "both hubs active"),
        identity_decision=_pass_decision("identity proven"),
        role_decision=ControllerDecision(SegmentDecision.NO_GO, "both hubs active", False),
    )

    assert result.decision.decision is SegmentDecision.NO_GO
    assert result.generated_profile is None


def test_segment_profile_generation_fails_closed_for_unknown_scenario() -> None:
    plan = SegmentPlan(
        segment_id="segment-future",
        scenario_id="future-scenario",
        expected_initial_role_state=_desired(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        expected_final_role_state=_desired(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        mutates_lab=False,
    )

    result = generate_segment_profile(
        plan,
        lab_config=_lab_config(),
        observed_role_state=_role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        identity_decision=_pass_decision("identity proven"),
        role_decision=_pass_decision("role state proven"),
    )

    assert result.decision.decision is SegmentDecision.NO_GO
    assert result.generated_profile is None
    assert "unknown release scenario" in result.decision.reason


def test_non_mutating_segment_can_get_generated_profile_from_proven_state() -> None:
    plan = SegmentPlan(
        segment_id="segment-preflight",
        scenario_id="preflight",
        expected_initial_role_state=_desired(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        expected_final_role_state=_desired(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        mutates_lab=False,
    )

    result = generate_segment_profile(
        plan,
        lab_config=_lab_config(),
        observed_role_state=_role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        identity_decision=_pass_decision("identity proven"),
        role_decision=_pass_decision("role state proven"),
    )

    assert result.decision.decision is SegmentDecision.PASS
    assert result.generated_profile is not None
    assert result.generated_profile.profile_data["scenarios"] == [{"id": "preflight"}]


def test_stale_generated_profile_is_rejected_before_scenario_execution() -> None:
    result = generate_segment_profile(
        _switchover_plan(),
        lab_config=_lab_config(),
        observed_role_state=_role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        identity_decision=_pass_decision("identity proven"),
        role_decision=_pass_decision("role state proven"),
    )
    assert result.generated_profile is not None

    decision = validate_generated_profile_freshness(
        result.generated_profile,
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
    )

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.safe_to_continue is False
