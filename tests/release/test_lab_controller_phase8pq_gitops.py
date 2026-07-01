from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from scripts.release import run_lab_role_controller as cli
from tests.release.lab_controller.gitops import (
    ArgoCDInterferenceMode,
    CoordinationStrategy,
    build_gitops_artifact_summary,
    classify_gitops_ownership,
    load_automated_enabled_capability_from_crd,
    load_gitops_ownership_from_fixture,
)
from tests.release.lab_controller.models import (
    ArgoCDApplicationEvidence,
    GitOpsCapabilityEvidence,
    GitOpsOwnershipEvidence,
    GitOpsTrackedResource,
    HubIdentityEvidence,
    LabArgoCDSettings,
    PhysicalHubConfig,
    PhysicalHubLabel,
    SegmentDecision,
    StableLabConfig,
)
from tests.release.lab_controller.planner import (
    CertificationDecision,
    PlannedSegment,
    run_certification_plan,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = Path(__file__).resolve().parent / "kustomize"
GITOPS_MODULE_PATH = Path(__file__).resolve().parent / "lab_controller" / "gitops.py"
CAPABILITY_FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "gitops"
EXPECTED_CLUSTERS = ("mc-1", "mc-2", "mc-3")


def _scenario_fixture(name: str) -> Path:
    return FIXTURE_ROOT / "overlays" / "scenarios" / name


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


def _expected_identities(config: StableLabConfig) -> dict[PhysicalHubLabel, HubIdentityEvidence]:
    return {
        label: hub.expected_identity for label, hub in config.physical_hubs.items() if hub.expected_identity is not None
    }


def _lab_config(
    gitops_evidence: GitOpsOwnershipEvidence | None,
    *,
    mandatory_argocd: bool = True,
) -> StableLabConfig:
    return StableLabConfig(
        physical_hubs={
            PhysicalHubLabel.HUB_A: _hub_config(PhysicalHubLabel.HUB_A),
            PhysicalHubLabel.HUB_B: _hub_config(PhysicalHubLabel.HUB_B),
        },
        expected_managed_cluster_names=EXPECTED_CLUSTERS,
        enabled_streams=("python", "ansible"),
        scenario_ids=("argocd-managed-switchover",),
        profile_name="phase8pq-gitops",
        artifact_root="artifacts/release-lab/unit",
        argocd=LabArgoCDSettings(mandatory=mandatory_argocd, namespaces=("openshift-gitops",)),
        gitops=gitops_evidence,
    )


def _argocd_segment() -> PlannedSegment:
    plan = cli.run_controller(
        plan_name="ping-pong",
        mode="fake",
        plan_id="phase8pq-template",
    ).plan
    base = plan.segments[1]
    return replace(
        base,
        segment_id="argocd-gitops-lane",
        scenario_id="argocd-managed-switchover",
        mutates_lab=True,
        execution_result=replace(
            base.execution_result,
            scenario_id="argocd-managed-switchover",
        ),
    )


def _write_gitops_fixture(
    scenario_dir: Path,
    *,
    application_name: str = "acm-owner-fixture",
    sync_policy_yaml: str | None = "  syncPolicy: {}\n",
    resource_namespace: str = "open-cluster-management-backup",
    resource_label: str | None = '"true"',
    tracking_id: str | None = None,
    include_status_resource: bool = False,
) -> Path:
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "kustomization.yaml").write_text("resources:\n  - app.yaml\n  - resource.yaml\n", encoding="utf-8")
    status_yaml = (
        """
status:
  resources:
    - group: ""
      kind: ConfigMap
      namespace: open-cluster-management-backup
      name: fixture-owned-resource
      status: Synced
"""
        if include_status_resource
        else ""
    )
    (scenario_dir / "app.yaml").write_text(
        f"""
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: {application_name}
  namespace: openshift-gitops
spec:
  project: default
  source:
    repoURL: https://git.example.invalid/acm-switchover-lab.git
    targetRevision: HEAD
    path: tests/release/kustomize/bases/acm-dr-objects
  destination:
    name: in-cluster
    namespace: open-cluster-management-backup
{sync_policy_yaml or ""}{status_yaml}
""",
        encoding="utf-8",
    )
    annotations_yaml = (
        f"""
  annotations:
    argocd.argoproj.io/tracking-id: {tracking_id}
"""
        if tracking_id is not None
        else ""
    )
    labels_yaml = (
        f"""
  labels:
    acm-switchover.redhat-lab/acm-object: {resource_label}
"""
        if resource_label is not None
        else ""
    )
    (scenario_dir / "resource.yaml").write_text(
        f"""
apiVersion: v1
kind: ConfigMap
metadata:
  name: fixture-owned-resource
  namespace: {resource_namespace}
{labels_yaml}{annotations_yaml}data:
  placeholder: static-fixture
""",
        encoding="utf-8",
    )
    return scenario_dir


def _manual_owned_application(
    *,
    automated: dict[str, Any] | None,
    capability: GitOpsCapabilityEvidence,
) -> GitOpsOwnershipEvidence:
    resource = GitOpsTrackedResource(
        group="cluster.open-cluster-management.io",
        kind="Restore",
        namespace="open-cluster-management-backup",
        name="restore-acm-passive-sync",
        tracking_id="acm-owner-enabled:/Restore:open-cluster-management-backup/restore-acm-passive-sync",
        owning_application="acm-owner-enabled",
        acm_object=True,
    )
    application = ArgoCDApplicationEvidence(
        name="acm-owner-enabled",
        namespace="openshift-gitops",
        owns_acm_resources=True,
        tracked_resources=(resource,),
        sync_policy={"automated": automated} if automated is not None else {},
    )
    return GitOpsOwnershipEvidence(
        evaluated=True,
        source="unit-test",
        applications=(application,),
        tracked_resources=(resource,),
        automated_enabled_capability=capability,
    )


def test_safe_autosync_off_fixture_classifies_as_pass_without_automated_enabled_capability() -> None:
    evidence = load_gitops_ownership_from_fixture(
        _scenario_fixture("gitops-owns-acm-autosync-off"),
        automated_enabled_capability=GitOpsCapabilityEvidence.unknown("not supplied by fixture"),
    )
    decision = classify_gitops_ownership(evidence)

    assert decision.decision is SegmentDecision.PASS
    assert decision.interference_mode is ArgoCDInterferenceMode.OWNED_AUTOSYNC_OFF
    assert decision.coordination_strategy is CoordinationStrategy.NOT_REQUIRED
    assert decision.safe_to_continue is True
    assert any(resource.name == "acm-dr-restore-desired-state" for resource in evidence.tracked_resources)


def test_observe_only_fixture_classifies_as_pass_without_acm_ownership() -> None:
    evidence = load_gitops_ownership_from_fixture(_scenario_fixture("gitops-observe-only"))
    decision = classify_gitops_ownership(evidence)

    assert decision.decision is SegmentDecision.PASS
    assert decision.interference_mode is ArgoCDInterferenceMode.OBSERVE_ONLY
    assert decision.coordination_strategy is CoordinationStrategy.OBSERVE_ONLY
    assert decision.blocking_reason is None


def test_annotation_only_hostile_application_ownership_blocks(tmp_path: Path) -> None:
    fixture = _write_gitops_fixture(
        tmp_path / "kustomize" / "overlays" / "scenarios" / "annotation-hostile",
        application_name="annotation-hostile-owner",
        sync_policy_yaml="  syncPolicy:\n    automated:\n      selfHeal: true\n",
        tracking_id="annotation-hostile-owner:/ConfigMap:open-cluster-management-backup/fixture-owned-resource",
    )

    evidence = load_gitops_ownership_from_fixture(fixture)
    decision = classify_gitops_ownership(evidence)

    assert evidence.applications[0].owns_acm_resources is True
    assert evidence.applications[0].tracked_resources[0].owning_application == "annotation-hostile-owner"
    assert decision.decision is SegmentDecision.NO_GO
    assert decision.interference_mode is ArgoCDInterferenceMode.AUTOMATED_SELF_HEAL


def test_annotation_only_autosync_off_application_ownership_is_safe(tmp_path: Path) -> None:
    fixture = _write_gitops_fixture(
        tmp_path / "kustomize" / "overlays" / "scenarios" / "annotation-safe",
        application_name="annotation-safe-owner",
        sync_policy_yaml="  syncPolicy: {}\n",
        tracking_id="annotation-safe-owner:/ConfigMap:open-cluster-management-backup/fixture-owned-resource",
    )

    evidence = load_gitops_ownership_from_fixture(fixture)
    decision = classify_gitops_ownership(evidence)

    assert evidence.applications[0].owns_acm_resources is True
    assert len(evidence.tracked_resources) == 1
    assert decision.decision is SegmentDecision.PASS
    assert decision.interference_mode is ArgoCDInterferenceMode.OWNED_AUTOSYNC_OFF


@pytest.mark.parametrize(
    "tracking_id",
    (
        "not-a-valid-tracking-id",
        "malformed-tracking-owner:bad",
        "malformed-tracking-owner:/ConfigMap",
    ),
)
def test_malformed_tracking_id_on_acm_resource_blocks(tmp_path: Path, tracking_id: str) -> None:
    fixture = _write_gitops_fixture(
        tmp_path / "kustomize" / "overlays" / "scenarios" / "malformed-tracking",
        application_name="malformed-tracking-owner",
        sync_policy_yaml="  syncPolicy: {}\n",
        tracking_id=tracking_id,
    )

    evidence = load_gitops_ownership_from_fixture(fixture)
    decision = classify_gitops_ownership(evidence)

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.interference_mode is ArgoCDInterferenceMode.UNKNOWN
    assert "malformed" in decision.blocking_reason


def test_fixture_without_tracked_acm_resource_remains_observe_only(tmp_path: Path) -> None:
    fixture = _write_gitops_fixture(
        tmp_path / "kustomize" / "overlays" / "scenarios" / "no-tracked-acm",
        application_name="observe-only-hostile",
        sync_policy_yaml="  syncPolicy:\n    automated:\n      selfHeal: true\n",
        resource_namespace="acm-lab-hub",
        resource_label=None,
        tracking_id=None,
    )

    evidence = load_gitops_ownership_from_fixture(fixture)
    decision = classify_gitops_ownership(evidence)

    assert evidence.applications[0].owns_acm_resources is False
    assert evidence.tracked_resources == ()
    assert decision.decision is SegmentDecision.PASS
    assert decision.interference_mode is ArgoCDInterferenceMode.OBSERVE_ONLY


def test_no_tracked_acm_resources_classifies_as_pass_not_owned() -> None:
    decision = classify_gitops_ownership(GitOpsOwnershipEvidence(evaluated=True, source="unit-test"))

    assert decision.decision is SegmentDecision.PASS
    assert decision.interference_mode is ArgoCDInterferenceMode.NOT_OWNED
    assert decision.coordination_strategy is CoordinationStrategy.NOT_REQUIRED


def test_fixture_loader_accepts_relative_fixture_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    evidence = load_gitops_ownership_from_fixture(
        _scenario_fixture("gitops-owns-acm-autosync-off").relative_to(REPO_ROOT)
    )

    assert evidence.source == "overlays/scenarios/gitops-owns-acm-autosync-off"
    assert evidence.tracked_resources


def test_fixture_loader_normalizes_malformed_namespace_values(tmp_path: Path) -> None:
    scenario_dir = tmp_path / "kustomize" / "overlays" / "scenarios" / "malformed-namespace"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "kustomization.yaml").write_text("resources:\n  - app.yaml\n  - resource.yaml\n", encoding="utf-8")
    (scenario_dir / "app.yaml").write_text(
        """
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: malformed-namespace-owner
  namespace: openshift-gitops
spec:
  syncPolicy: {}
status:
  resources:
    - group: ""
      kind: ConfigMap
      namespace: []
      name: malformed-namespace-resource
""",
        encoding="utf-8",
    )
    (scenario_dir / "resource.yaml").write_text(
        """
apiVersion: v1
kind: ConfigMap
metadata:
  name: malformed-namespace-resource
  namespace: []
  labels:
    acm-switchover.redhat-lab/acm-object: "true"
data:
  placeholder: static-fixture
""",
        encoding="utf-8",
    )

    evidence = load_gitops_ownership_from_fixture(scenario_dir)
    decision = classify_gitops_ownership(evidence)

    assert decision.decision is SegmentDecision.PASS
    assert evidence.tracked_resources[0].namespace is None


@pytest.mark.parametrize(
    ("fixture_name", "mode", "reason"),
    (
        ("gitops-owns-acm-selfheal-on", ArgoCDInterferenceMode.AUTOMATED_SELF_HEAL, "selfHeal"),
        ("gitops-owns-acm-prune-on", ArgoCDInterferenceMode.AUTOMATED_PRUNE, "prune"),
    ),
)
def test_hostile_automated_sync_fixtures_block_without_coordination(
    fixture_name: str,
    mode: ArgoCDInterferenceMode,
    reason: str,
) -> None:
    evidence = load_gitops_ownership_from_fixture(_scenario_fixture(fixture_name))
    decision = classify_gitops_ownership(evidence)

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.interference_mode is mode
    assert decision.coordination_strategy is CoordinationStrategy.APPLICATION_COORDINATION_REQUIRED
    assert decision.blocking_reason is not None
    assert reason in decision.blocking_reason


@pytest.mark.parametrize(
    "sync_policy_yaml",
    (
        "  syncPolicy: []\n",
        '  syncPolicy: "bad"\n',
        "  syncPolicy:\n    automated: []\n",
        '  syncPolicy:\n    automated: "bad"\n',
    ),
)
def test_malformed_sync_policy_on_acm_owning_application_blocks(tmp_path: Path, sync_policy_yaml: str) -> None:
    fixture = _write_gitops_fixture(
        tmp_path / "kustomize" / "overlays" / "scenarios" / "malformed-sync-policy",
        sync_policy_yaml=sync_policy_yaml,
        include_status_resource=True,
    )

    evidence = load_gitops_ownership_from_fixture(fixture)
    decision = classify_gitops_ownership(evidence)

    assert evidence.applications[0].owns_acm_resources is True
    assert decision.decision is SegmentDecision.NO_GO
    assert decision.interference_mode is ArgoCDInterferenceMode.UNKNOWN
    assert "syncPolicy" in decision.blocking_reason


@pytest.mark.parametrize("sync_policy_yaml", (None, "  syncPolicy: {}\n"))
def test_absent_or_empty_sync_policy_on_acm_owning_application_is_safe(
    tmp_path: Path,
    sync_policy_yaml: str | None,
) -> None:
    fixture = _write_gitops_fixture(
        tmp_path / "kustomize" / "overlays" / "scenarios" / "safe-sync-policy",
        sync_policy_yaml=sync_policy_yaml,
        include_status_resource=True,
    )

    evidence = load_gitops_ownership_from_fixture(fixture)
    decision = classify_gitops_ownership(evidence)

    assert evidence.applications[0].owns_acm_resources is True
    assert decision.decision is SegmentDecision.PASS
    assert decision.interference_mode is ArgoCDInterferenceMode.OWNED_AUTOSYNC_OFF


def test_applicationset_child_blocks_without_parent_coordination() -> None:
    evidence = load_gitops_ownership_from_fixture(_scenario_fixture("gitops-owns-acm-appset-child"))
    decision = classify_gitops_ownership(evidence)
    summary = build_gitops_artifact_summary(evidence, decision)

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.interference_mode is ArgoCDInterferenceMode.APPLICATIONSET_CHILD
    assert decision.coordination_strategy is CoordinationStrategy.APPLICATIONSET_PARENT_COORDINATION_REQUIRED
    assert "ApplicationSet" in decision.blocking_reason
    assert "static-appset-child-fixture-uid" not in json.dumps(summary, sort_keys=True)
    assert summary["application_set_evidence"][0]["owner_uid_identity_evidence"] == "not_used"


def test_applicationset_child_passes_with_explicit_parent_level_coordination() -> None:
    evidence = load_gitops_ownership_from_fixture(
        _scenario_fixture("gitops-owns-acm-appset-child"),
        coordinated_appsets=("acm-owner-appset",),
    )
    decision = classify_gitops_ownership(evidence)

    assert decision.decision is SegmentDecision.PASS
    assert decision.interference_mode is ArgoCDInterferenceMode.APPLICATIONSET_CHILD
    assert decision.coordination_strategy is CoordinationStrategy.PARENT_LEVEL_COORDINATION


def test_unknown_ownership_evidence_fails_closed() -> None:
    evidence = GitOpsOwnershipEvidence.unknown("malformed tracking annotation payload")
    decision = classify_gitops_ownership(evidence)

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.interference_mode is ArgoCDInterferenceMode.UNKNOWN
    assert decision.coordination_strategy is CoordinationStrategy.BLOCKED_UNKNOWN
    assert "malformed" in decision.blocking_reason


def test_unknown_automated_enabled_capability_fails_closed_when_required_for_decision() -> None:
    evidence = _manual_owned_application(
        automated={"enabled": False, "selfHeal": True},
        capability=GitOpsCapabilityEvidence.unknown("CRD schema not available"),
    )
    decision = classify_gitops_ownership(evidence)

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.interference_mode is ArgoCDInterferenceMode.UNKNOWN
    assert "automated.enabled" in decision.blocking_reason


def test_crd_schema_capability_evidence_allows_automated_enabled_false_to_disable_hostile_fields() -> None:
    capability = load_automated_enabled_capability_from_crd(
        CAPABILITY_FIXTURE_ROOT / "application-crd-with-automated-enabled.yaml"
    )
    evidence = _manual_owned_application(
        automated={"enabled": False, "selfHeal": True, "prune": True},
        capability=capability,
    )
    decision = classify_gitops_ownership(evidence)

    assert capability.automated_enabled_supported is True
    assert capability.source == "crd_schema"
    assert decision.decision is SegmentDecision.PASS
    assert decision.interference_mode is ArgoCDInterferenceMode.OWNED_AUTOSYNC_OFF


def test_crd_schema_without_automated_enabled_reports_unsupported_capability() -> None:
    capability = load_automated_enabled_capability_from_crd(
        CAPABILITY_FIXTURE_ROOT / "application-crd-without-automated-enabled.yaml"
    )

    assert capability.automated_enabled_supported is False
    assert capability.source == "crd_schema"


def test_gitops_artifact_summary_is_redacted_and_dry_run_only() -> None:
    evidence = load_gitops_ownership_from_fixture(_scenario_fixture("gitops-owns-acm-autosync-off"))
    decision = classify_gitops_ownership(evidence)
    summary = build_gitops_artifact_summary(evidence, decision)
    text = json.dumps(summary, sort_keys=True)

    assert summary["evaluated"] is True
    assert summary["live_certification_evidence"] is False
    assert summary["not_live_acm_certification_evidence"] is True
    assert summary["tracked_acm_resources"]
    assert summary["application_evidence"]
    assert summary["sync_policy_classification"]["interference_mode"] == "owned_autosync_off"
    assert summary["automated_enabled_capability"]["source"] == "unknown"
    assert "git.example.invalid" not in text
    assert "static-appset-child-fixture-uid" not in text


def test_mandatory_argocd_lane_with_unknown_gitops_evidence_blocks_before_execution() -> None:
    config = _lab_config(GitOpsOwnershipEvidence.unknown("missing non-live GitOps fixture evidence"))
    result = run_certification_plan(
        replace(
            cli.run_controller(plan_name="ping-pong", mode="fake", plan_id="phase8pq").plan,
            segments=(_argocd_segment(),),
        ),
        lab_config=config,
        expected_identities=_expected_identities(config),
    )

    assert result.decision is CertificationDecision.NO_GO
    assert result.segment_results[0].decision is CertificationDecision.NO_GO
    assert result.segment_results[0].controller_result is not None
    assert result.segment_results[0].controller_result.execution_result is None
    assert "GitOps" in result.first_blocking_reason


def test_mandatory_argocd_lane_with_unevaluated_gitops_evidence_blocks_before_execution() -> None:
    config = _lab_config(GitOpsOwnershipEvidence.not_evaluated("GitOps fixture evidence was not loaded"))
    result = run_certification_plan(
        replace(
            cli.run_controller(plan_name="ping-pong", mode="fake", plan_id="phase8pq").plan,
            segments=(_argocd_segment(),),
        ),
        lab_config=config,
        expected_identities=_expected_identities(config),
    )

    assert result.decision is CertificationDecision.NO_GO
    assert result.segment_results[0].decision is CertificationDecision.NO_GO
    assert result.segment_results[0].controller_result is not None
    assert result.segment_results[0].controller_result.execution_result is None
    assert "GitOps" in result.first_blocking_reason


def test_safe_gitops_evidence_allows_dry_run_materialized_plan_and_records_summary() -> None:
    evidence = load_gitops_ownership_from_fixture(_scenario_fixture("gitops-owns-acm-autosync-off"))
    config = _lab_config(evidence)
    result = run_certification_plan(
        replace(
            cli.run_controller(plan_name="ping-pong", mode="fake", plan_id="phase8pq").plan,
            segments=(_argocd_segment(),),
        ),
        lab_config=config,
        expected_identities=_expected_identities(config),
    )

    summary = result.artifact_bundle.payload["gitops_evidence"]

    assert result.decision is CertificationDecision.PASS
    assert summary["evaluated"] is True
    assert summary["final_decision"] == "PASS"
    assert summary["not_live_acm_certification_evidence"] is True
    assert result.artifact_bundle.payload["live_certification_evidence"] is False


def test_cli_dry_run_artifact_includes_gitops_evidence_without_live_certification_claim(tmp_path: Path) -> None:
    code = cli.main(
        ["--plan", "ping-pong", "--mode", "release-framework-dry-run", "--artifact-dir", str(tmp_path)],
        stdout=None,
        stderr=None,
    )
    artifact = json.loads((tmp_path / "lab-controller-run.json").read_text(encoding="utf-8"))

    assert code == 0
    assert artifact["gitops_evidence"]["evaluated"] is True
    assert artifact["gitops_evidence"]["final_decision"] == "PASS"
    assert artifact["gitops_evidence"]["live_certification_evidence"] is False
    assert artifact["live_certification_evidence"] is False
    assert "live ACM certification evidence" not in json.dumps(artifact)


def test_gitops_module_does_not_import_or_call_live_command_paths() -> None:
    tree = ast.parse(GITOPS_MODULE_PATH.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    imported_targets: set[str] = set()
    called_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".")[0])
                imported_targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
            imported_targets.add(f"tests.release.lab_controller.{node.module}" if node.level else node.module)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called_names.add(func.id)
            elif isinstance(func, ast.Attribute):
                called_names.add(func.attr)

    for forbidden_module in ("os", "subprocess", "socket", "requests", "urllib", "http", "kubernetes", "openshift"):
        assert forbidden_module not in imported_roots

    for forbidden_internal_module in (
        "tests.release.lab_controller.live_config",
        "tests.release.lab_controller.read_only_discovery",
        "tests.release.lab_controller.read_only_live_transport",
        "tests.release.lab_controller.read_only_preflight_pilot",
        "tests.release.lab_controller.read_only_transport",
    ):
        assert forbidden_internal_module not in imported_targets

    for forbidden_call in ("system", "run", "Popen", "check_output", "check_call", "call"):
        assert forbidden_call not in called_names
