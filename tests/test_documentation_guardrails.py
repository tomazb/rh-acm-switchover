"""Regression checks for maintained support documentation."""

from pathlib import Path

import pytest

from scripts.release import run_lab_role_controller as lab_controller_cli
from tests.release.scenarios.catalog import SCENARIOS_BY_ID

REPO_ROOT = Path(__file__).resolve().parents[1]
LAB_CONTROLLER_SCHEMA_DOC = "docs/development/lab-role-controller-live-lab-config-schema.md"
LAB_CONTROLLER_READ_ONLY_DISCOVERY_DOC = "docs/development/lab-role-controller-read-only-discovery-design.md"
LAB_CONTROLLER_READ_ONLY_BACKEND_DOC = "docs/development/lab-role-controller-read-only-backend-design.md"
LAB_CONTROLLER_READ_ONLY_LIVE_TRANSPORT_REVIEW_DOC = (
    "docs/development/lab-role-controller-read-only-live-transport-design-review.md"
)
LAB_CONTROLLER_SAFETY_DOCS = (
    "docs/development/lab-role-controller-live-readiness-design.md",
    LAB_CONTROLLER_SCHEMA_DOC,
    LAB_CONTROLLER_READ_ONLY_DISCOVERY_DOC,
    LAB_CONTROLLER_READ_ONLY_BACKEND_DOC,
    LAB_CONTROLLER_READ_ONLY_LIVE_TRANSPORT_REVIEW_DOC,
    "docs/development/lab-role-controller-agent-instructions.md",
)


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _assert_argocd_script_only_in_deprecated_context(path: str, content: str) -> None:
    lines = content.splitlines()

    for idx, line in enumerate(lines):
        if "argocd-manage.sh" not in line:
            continue

        window_start = max(0, idx - 2)
        window_end = min(len(lines), idx + 3)
        context = "\n".join(lines[window_start:window_end]).lower()
        assert "deprecated" in context, f"Non-deprecated argocd-manage.sh guidance remains in {path}: {line}"


def _assert_no_real_live_config_literals(path: str, content: str) -> None:
    lowered = content.lower()
    forbidden_literals = (
        "https://",
        "http://",
        "bearer ",
        "token=",
        "password=",
        "secret=",
        "~/.kube",
        "/home/",
        "/tmp/",
    )
    for literal in forbidden_literals:
        assert literal not in lowered, f"{path} contains forbidden live-config-like literal {literal!r}"

    assert "cluster-id" not in lowered, f"{path} contains a private-cluster-ID-like marker"


def test_docs_index_surfaces_collection_and_tldr_docs():
    """Docs landing page should point readers to newer major documentation areas."""
    content = _read("docs/README.md")

    assert "ansible-collection" in content
    assert "ACM_SWITCHOVER_RUNBOOK_TLDR.md" in content


def test_tests_readmes_cover_current_test_surfaces():
    """Test docs should mention collection, E2E, and newer tool coverage."""
    tests_readme = _read("tests/README.md")
    scripts_readme = _read("tests/README-scripts-tests.md")

    assert "ansible_collections/tomazb/acm_switchover/tests/" in tests_readme
    assert "tests/e2e/README.md" in tests_readme
    assert "check_rbac.py" in scripts_readme
    assert "generate-merged-kubeconfig.sh" in scripts_readme
    assert "argocd-manage.sh" in scripts_readme


def test_contributing_matches_current_dev_workflow():
    """Contributor guide should match current environment and test guidance."""
    content = _read("CONTRIBUTING.md")

    for token in (".venv", "requirements-dev.txt", "./run_tests.sh", "CHANGELOG.md"):
        assert token in content, f"Missing {token} from CONTRIBUTING.md"


def test_kustomize_readme_mentions_optional_decommission_extension():
    """Kustomize deployment docs should mention the split decommission RBAC extension."""
    content = _read("deploy/kustomize/README.md")

    assert "decommission extension" in content.lower()
    assert "deploy/rbac/extensions/decommission/clusterrole.yaml" in content
    assert "deploy/rbac/extensions/decommission/clusterrolebinding.yaml" in content


def test_argocd_guardrail_matches_script_without_dot_slash_prefix():
    """The guardrail should match deprecated script guidance regardless of path prefix."""
    _assert_argocd_script_only_in_deprecated_context(
        "sample.md",
        "Bash alternative (deprecated): `scripts/argocd-manage.sh` is deprecated.\n",
    )


def test_argocd_guardrail_accepts_deprecation_marker_in_nearby_context():
    """A nearby deprecation marker should satisfy the guardrail for a code example."""
    _assert_argocd_script_only_in_deprecated_context(
        "sample.md",
        "Deprecated:\n`argocd-manage.sh --context hub --mode pause`\n",
    )


def test_argocd_guardrail_rejects_non_deprecated_script_guidance():
    """Any active argocd-manage.sh recommendation should still fail the guardrail."""
    with pytest.raises(AssertionError):
        _assert_argocd_script_only_in_deprecated_context(
            "sample.md",
            "Run `argocd-manage.sh --context hub --mode pause` before the switchover.\n",
        )


def test_install_quick_test_mentions_supported_virtualenv_names():
    """Quick-test guidance should clarify both supported virtualenv directory names."""
    content = _read("docs/getting-started/install.md")
    quick_test_section = content.split("### Quick Test", 1)[1].split("### Enable Bash Completions", 1)[0]

    assert "source .venv/bin/activate" in quick_test_section
    assert "source venv/bin/activate" in quick_test_section


def test_active_operator_docs_do_not_recommend_deprecated_argocd_script():
    """Active operator guidance may mention the script only as deprecated, never as the recommended path."""
    guarded_paths = (
        "docs/operations/usage.md",
        "docs/operations/quickref.md",
        "docs/ACM_SWITCHOVER_RUNBOOK.md",
        ".claude/skills/operations/preflight-validation.skill.md",
    )

    for path in guarded_paths:
        _assert_argocd_script_only_in_deprecated_context(path, _read(path))


def test_deprecated_bash_argocd_boundary_documents_supported_safety_gap():
    """Deprecated Bash Argo CD docs must point production safety checks to supported paths."""
    content = _read("scripts/README.md")

    assert "Deprecated boundary" in content
    assert "ApplicationSet child-Application blocker" in content
    assert "post-patch auto-sync verification" in content
    assert "Python CLI `--argocd-manage`" in content
    assert "Ansible collection `argocd_manage` role" in content


def test_collection_migration_map_tracks_current_cli_surface():
    """The collection migration map must not preserve stale Python option names."""
    content = _read("ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md")

    assert "--generate-kubeconfig" not in content
    assert "--skip-kubeconfig-generation" in content
    assert "--manage-auto-import-strategy" in content
    assert "--log-format" in content


def test_collection_variable_reference_documents_checkpoint_and_klusterlet_controls():
    """Variable docs must cover the current checkpoint and post-activation control surface."""
    content = _read("ansible_collections/tomazb/acm_switchover/docs/variable-reference.md")

    assert "acm_switchover_execution.checkpoint.reset_from" in content
    assert "acm_switchover_execution.concurrency.klusterlet_probe_workers" in content
    assert "acm_switchover_execution.concurrency.klusterlet_remediation_workers" in content
    assert "acm_switchover_execution.timeouts.klusterlet_recheck_seconds" in content
    assert "acm_switchover_execution.timeouts.klusterlet_recheck_interval_seconds" in content
    assert "acm_switchover_features.klusterlet.strict_remediation" in content
    assert "acm_switchover_discovery.bridge_script" in content
    assert "scripts/discover-hub.sh" in content


def test_collection_artifact_schema_documents_current_checkpoint_contract():
    """Checkpoint docs must describe schema 2.0 and non-mutating validate/dry-run behavior."""
    content = _read("ansible_collections/tomazb/acm_switchover/docs/artifact-schema.md")

    assert '"schema_version": "2.0"' in content
    assert "operation_identity" in content
    assert '"primary_cluster_uid": "d1f2b8a0-0000-4000-9000-111111111111"' in content
    assert '"secondary_cluster_uid": "e3a4c9b1-0000-4000-9000-222222222222"' in content
    assert '"primary_kubeconfig": "./kubeconfigs/primary.kubeconfig"' not in content
    assert '"secondary_kubeconfig": "./kubeconfigs/secondary.kubeconfig"' not in content
    assert "checkpoint.reset_from" in content
    assert "validate" in content
    assert "dry_run" in content
    assert "locked_by" not in content


def test_rbac_docs_match_observability_route_and_scale_permissions():
    """Operator-facing RBAC docs must not drift from the enforced observability matrix."""
    rbac_requirements = _read("docs/deployment/rbac-requirements.md")
    install_doc = _read("docs/getting-started/install.md")

    assert "- **Verbs**: `get`" in rbac_requirements
    assert "- **Verbs**: `get`, `list`\n- **Scope**: Namespace-scoped (various)" not in rbac_requirements
    assert "`statefulsets/scale` (get, patch)" in rbac_requirements
    assert 'resources: ["routes"]' in install_doc
    assert 'verbs: ["get"]' in install_doc
    assert 'resources: ["deployments", "statefulsets", "statefulsets/scale"]' in install_doc


def test_rbac_requirements_document_current_cluster_read_permissions():
    """RBAC requirements must include the cluster-wide read surface shipped in manifests."""
    content = _read("docs/deployment/rbac-requirements.md")

    assert "- **Resources**: `namespaces`\n- **Verbs**: `get`, `list`" in content
    assert "#### Nodes" in content
    assert "- **Resources**: `nodes`\n- **Verbs**: `get`, `list`" in content
    assert "#### ClusterOperators" in content
    assert "- **Resources**: `clusteroperators`\n- **Verbs**: `get`, `list`" in content
    assert "#### ClusterVersions" in content
    assert "- **Resources**: `clusterversions`\n- **Verbs**: `get`, `list`" in content


def test_rbac_requirements_document_current_namespace_permissions():
    """Namespace-scoped RBAC docs must include read resources and verbs from shipped Roles."""
    content = _read("docs/deployment/rbac-requirements.md")
    namespace_section = content.split("### Namespace-Scoped Resources", 1)[1].split("### Managed-Cluster", 1)[0]

    assert "- `pods` (get, list)" in namespace_section
    assert "- `backupstoragelocations` (get, list - velero.io)" in namespace_section
    assert "- `configmaps` (get, list, create, patch, delete)" in namespace_section


def test_rbac_deployment_recommends_collection_bootstrap_before_deprecated_script():
    """Operator bootstrap docs should prefer the collection playbook over the deprecated script."""
    content = _read("docs/deployment/rbac-deployment.md")
    quick_start = content.split("## Quick Start", 1)[1].split("### Option 1", 1)[0]

    assert "playbooks/rbac_bootstrap.yml" in quick_start
    assert "scripts/setup-rbac.sh" in quick_start
    assert quick_start.index("playbooks/rbac_bootstrap.yml") < quick_start.index("scripts/setup-rbac.sh")
    assert "deprecated" in quick_start.lower()


def test_rbac_token_duration_docs_use_24h_default():
    """Operator-facing RBAC docs must match the generated kubeconfig token default."""
    docs = {
        "docs/deployment/rbac-deployment.md": _read("docs/deployment/rbac-deployment.md"),
        "scripts/README.md": _read("scripts/README.md"),
        "ansible_collections/tomazb/acm_switchover/docs/variable-reference.md": _read(
            "ansible_collections/tomazb/acm_switchover/docs/variable-reference.md"
        ),
    }

    for path, content in docs.items():
        assert "`24h`" in content or "24 hours" in content, f"{path} must document the 24h token default"

    for stale in ("Default 48-hour token", "`48h` | Token validity duration", "| `token_duration` | str | `48h`"):
        assert stale not in "\n".join(docs.values())


def test_helm_validator_custom_rules_document_read_only_guardrail():
    """Helm docs must describe validator custom rules as read-only, not unrestricted RBAC extension points."""
    content = _read("deploy/helm/acm-switchover-rbac/README.md")

    assert "`rbac.customValidatorRules`" in content
    assert "read-only" in content.lower()
    assert "`get`, `list`, and `watch`" in content
    assert "mutating verbs" in content.lower()


def test_collection_rbac_bootstrap_example_uses_absolute_kubeconfig_path():
    """JSON extra-vars do not shell-expand '~', so the recommended copy/paste example must avoid it."""
    content = _read("docs/deployment/rbac-deployment.md")
    collection_example = content.split("ansible-playbook ansible_collections", 1)[1].split("```", 1)[0]

    assert "~/.kube/admin.yaml" not in collection_example
    assert '"/home/admin/.kube/admin.yaml"' in collection_example


def test_collection_readme_no_longer_calls_collection_foundation_scope():
    """Collection README should describe the current production collection, not the old foundation scope."""
    content = _read("ansible_collections/tomazb/acm_switchover/README.md")

    assert "Foundation Ansible Collection" not in content
    assert "Production-ready Ansible Collection" in content


def test_project_docs_do_not_link_missing_ansible_design_spec():
    """Docs must not link the removed 2026-04-10 Ansible rewrite spec."""
    stale_spec = "2026-04-10-ansible-collection-rewrite-design.md"

    docs_paths = sorted(path for path in (REPO_ROOT / "docs").rglob("*.md") if path.is_file())
    for doc_path in docs_paths:
        content = doc_path.read_text(encoding="utf-8")
        assert stale_spec not in content

    for path in ("docs/project/summary.md", "docs/project/prd.md"):
        content = _read(path)
        assert "../ansible-collection/" in content


def test_report_artifact_docs_do_not_claim_identical_top_level_fields():
    """Report docs must describe aligned contracts without promising field-for-field identity."""
    artifact_schema = _read("ansible_collections/tomazb/acm_switchover/docs/artifact-schema.md")
    validation_rules = _read("docs/reference/validation-rules.md")
    architecture = _read("docs/development/architecture.md")

    forbidden = "same top-level status fields"
    assert forbidden not in artifact_schema
    assert "not identical across all report types" in artifact_schema
    assert "field parity with collection JSON artifacts" not in validation_rules
    assert "same core fields" not in architecture


def test_changelog_unreleased_keeps_standard_groups():
    """The active changelog section should keep all standard project groups present."""
    content = _read("CHANGELOG.md")
    unreleased = content.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]

    for heading in ("### Added", "### Changed", "### Fixed", "### Removed"):
        assert heading in unreleased


def test_lab_role_controller_agent_instructions_document_non_live_authority_boundary():
    """Phase 7B Agent guidance must preserve the controller-owned non-live boundary."""
    content = _read("docs/development/lab-role-controller-agent-instructions.md")

    required = (
        "The Python lab role controller owns truth",
        "The Agent owns only orchestration convenience and explanation",
        "This is not live ACM certification",
        "does not execute `oc`, `kubectl`, `ansible-playbook`",
        "Use the Phase 7A CLI as the only supported command boundary",
        "must not invent ad hoc live cluster commands",
        "must not override controller final decisions",
        "live_certification_evidence=false",
        "Dry-run materialization is not execution evidence",
        "Local harness evidence is not live ACM certification evidence",
        "`safe_to_continue` is non-live controller metadata",
    )

    for token in required:
        assert token in content


def test_lab_role_controller_agent_instructions_cover_decisions_and_artifact_fields():
    """Agent guidance must explain controller decision handling and required artifact reads."""
    content = _read("docs/development/lab-role-controller-agent-instructions.md")

    for decision in ("PASS", "NO_GO", "RECOVERY_REQUIRED", "INFRA_RETRYABLE", "BLOCKED"):
        assert decision in content

    for field in (
        "final_decision",
        "safe_to_continue",
        "retry_allowed",
        "manual_recovery_required",
        "first_blocking_segment",
        "first_blocking_scenario",
        "first_blocking_reason",
        "recovery_category",
        "operator_action_hint",
        "final_state_proven",
        "segment_decisions",
        "role_transition_graph",
        "summary_counts",
        "runtime_parity",
        "redaction_status",
        "real_execution_evidence",
        "live_certification_evidence",
        "materialized_release_framework",
        "execution_harness_summary",
    ):
        assert field in content


def test_lab_role_controller_agent_instruction_examples_are_supported_and_safe():
    """Documented runnable examples should stay on the supported non-live CLI surface."""
    content = _read("docs/development/lab-role-controller-agent-instructions.md")
    command_lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip().startswith("python scripts/release/run_lab_role_controller.py")
    ]

    assert command_lines
    assert any("--mode fake" in line and "--artifact-dir" in line for line in command_lines)
    assert any("--mode release-framework-dry-run" in line and "--output-format json" in line for line in command_lines)
    assert any("--mode fake" in line and "--no-write" in line for line in command_lines)
    assert any("--mode fake" in line and "--strict" in line for line in command_lines)
    assert any("--mode release-framework-local" in line and "--allow-local-execution" in line for line in command_lines)

    runnable_examples = [
        line for line in command_lines if "--mode live" not in line and "--mode release-framework-live" not in line
    ]
    for line in runnable_examples:
        assert "--plan ping-pong" in line
        assert ".release" not in line
        assert "--kubeconfig" not in line
        assert "https://" not in line
        assert "token=" not in line.lower()
        assert "bearer " not in line.lower()
        if "--mode release-framework-local" in line:
            assert "--allow-local-execution" in line


def test_lab_role_controller_agent_instructions_require_human_retry_for_no_go():
    """NO_GO handling should not imply automatic reruns from Agent intuition."""
    content = _read("docs/development/lab-role-controller-agent-instructions.md")

    assert "NO_GO" in content
    assert "retry_allowed=true" in content
    assert "human explicitly requests a new non-live run" in content


def test_lab_role_controller_live_readiness_design_preserves_non_live_boundary():
    """Phase 8A design must not imply live execution is currently enabled."""
    content = _read("docs/development/lab-role-controller-live-readiness-design.md")

    required = (
        "This is a proposed live-readiness design",
        "It does not enable live execution",
        "It does not approve live ACM certification",
        "Phase 7C remains non-live",
        "Any future live execution must be implemented in a later phase and pass independent audit",
        "`live_certification_evidence=true`",
        "unsupported through the",
        "lab role controller",
        "Phase 8A does not implement any of these checks",
        "Recommendation: READY_FOR_PHASE_8B_GUARDRAILS",
    )

    for token in required:
        assert token in content


def test_lab_role_controller_live_readiness_design_documents_safety_boundaries():
    """The live-readiness design should lock down recovery, Agent, and first-live boundaries."""
    content = _read("docs/development/lab-role-controller-live-readiness-design.md")

    required = (
        "No automatic live recovery",
        "Agent cannot override controller decisions",
        "Agent cannot invent live commands",
        "Agent must stop on `NO_GO`, `RECOVERY_REQUIRED`, or `BLOCKED`",
        "read-only live discovery plus preflight-only evidence",
        "Passive switchover",
        "no mutation",
        "Human approval is required before any live action",
        "L10: final mutation confirmation",
        "Controller owns truth and safety",
        "Agent owns only orchestration convenience and explanation",
        "Runtime-only lab config reference supplied outside Git",
        "Real kubeconfig paths must not appear in artifact-facing summaries",
        "Raw API URLs must be fingerprinted or redacted",
        "Private cluster identifiers must be redacted",
        "Shell/arbitrary subprocess",
    )

    for token in required:
        assert token in content

    assert "live_certification_evidence=true" in content
    assert "unsupported through the\nlab role controller" in content


def test_lab_role_controller_live_readiness_design_has_required_matrices():
    """Command and scenario policy tables should stay present in the Phase 8A design."""
    content = _read("docs/development/lab-role-controller-live-readiness-design.md")

    assert "## Allowed / Forbidden Command Matrix" in content
    assert "## Scenario Live Eligibility Matrix" in content

    for command_family in (
        "Read-only `oc`/`kubectl` discovery",
        "Mutating `oc`/`kubectl` actions",
        "`ansible-playbook` execution",
        "Shell/arbitrary subprocess",
        "Agent-invented commands",
    ):
        assert command_family in content

    for scenario_id in (
        "`preflight`",
        "`static-gates`",
        "`lab-readiness`",
        "`baseline-check`",
        "`runtime-parity`",
        "`final-baseline-check`",
        "`bash-discovery`",
        "`bash-postflight`",
        "`rbac-bootstrap`",
        "`rbac-bootstrap-live`",
        "`python-passive-switchover`",
        "`ansible-passive-switchover`",
        "`python-restore-only`",
        "`ansible-restore-only`",
        "`argocd-managed-switchover`",
        "`checkpoint-resume`",
        "`failure-injection`",
        "`full-restore`",
        "`decommission`",
        "`soak`",
    ):
        assert scenario_id in content


def test_lab_role_controller_live_readiness_scenarios_match_catalog():
    """The Phase 8A live eligibility matrix should use current catalog scenario IDs."""
    content = _read("docs/development/lab-role-controller-live-readiness-design.md")
    marker = "| `static-gates` |"
    assert marker in content
    table = content.split(marker, 1)[1].split("\n\nAll requested scenario names exist", 1)[0]
    documented_ids = {"static-gates"}
    for line in table.splitlines():
        stripped = line.strip()
        if not stripped.startswith("| `"):
            continue
        documented_ids.add(stripped.split("`", 2)[1])

    assert documented_ids == set(SCENARIOS_BY_ID)


def test_lab_role_controller_live_readiness_design_avoids_sensitive_examples():
    """The Phase 8A design must not carry real credential, API, or private lab examples."""
    content = _read("docs/development/lab-role-controller-live-readiness-design.md")
    _assert_no_real_live_config_literals("docs/development/lab-role-controller-live-readiness-design.md", content)


def test_lab_role_controller_live_readiness_design_references_phase8b_schema_design():
    """Phase 8A/8B docs should point future implementers to the external config schema design."""
    content = _read("docs/development/lab-role-controller-live-readiness-design.md")

    assert LAB_CONTROLLER_SCHEMA_DOC in content
    assert "READY_FOR_PHASE_8C_EXTERNAL_LIVE_CONFIG_MODEL" in content


def test_lab_role_controller_live_lab_config_schema_design_is_non_live():
    """Phase 8B schema design must exist without enabling live config loading or execution."""
    content = _read(LAB_CONTROLLER_SCHEMA_DOC)

    required = (
        "design-only",
        "does not introduce live config loading",
        "does not provide a real config",
        "does not execute anything live",
        "live config files must remain outside Git",
        "examples are sanitized and fake",
        "runtime-only fields must not appear in artifacts",
        "future implementation must validate redaction before artifact creation",
        "production JSON schema finalization remains unsupported",
    )

    for token in required:
        assert token in content


def test_lab_role_controller_live_lab_config_schema_sections_and_sensitivity():
    """The conceptual schema should separate runtime-only inputs from artifact-safe metadata."""
    content = _read(LAB_CONTROLLER_SCHEMA_DOC)

    for section in (
        "schema_version",
        "lab_id",
        "plan_id",
        "physical_hubs",
        "managed_clusters",
        "approval",
        "credentials",
        "identity_expectations",
        "role_discovery",
        "rbac_prerequisites",
        "scenario_allowlist",
        "artifact_policy",
        "redaction_policy",
        "execution_policy",
    ):
        assert section in content

    for runtime_only_field in (
        "context_ref",
        "kubeconfig_ref",
        "credentials.runtime_only",
        "persist_to_artifacts: false",
        "inherit_environment: false",
        "allowed_env_vars",
        "forbidden_env_patterns",
    ):
        assert runtime_only_field in content

    for artifact_safe_field in (
        "expected_identity_fingerprint",
        "expected_api_fingerprint",
        "cluster_identity_fingerprints",
        "approval_timestamp",
        "approver_reference",
        "redaction_policy",
    ):
        assert artifact_safe_field in content

    for conceptual_field in (
        "mismatch_policy",
        "ambiguity_policy",
        "read_only_checks_required",
        "mutation_checks_required",
        "allowlist_version",
        "reject_raw_api_urls",
        "fingerprint_identity_values",
        "forbidden committed values",
    ):
        assert conceptual_field in content


def test_lab_role_controller_live_lab_config_schema_documents_safe_defaults():
    """Future live execution policy defaults must remain fail-closed in the schema design."""
    content = _read(LAB_CONTROLLER_SCHEMA_DOC)

    for token in (
        "live_execution_enabled: false",
        "read_only_discovery_enabled: false",
        "mutation_enabled: false",
        "automatic_recovery_enabled: false",
        "live_certification_evidence_enabled: false",
        "artifact_dir must be caller-provided",
        "no default `.release` output",
        "no committed live artifacts",
        "stdout/stderr sanitization required",
    ):
        assert token in content


def test_lab_role_controller_live_lab_config_schema_documents_l0_l10_gates():
    """The future gate model must represent L0-L10 as design-only concepts."""
    content = _read(LAB_CONTROLLER_SCHEMA_DOC)

    assert "Gate ID" in content
    assert "Purpose" in content
    assert "Input evidence" in content
    assert "Artifact evidence" in content
    assert "Failure decision" in content
    assert "Retry/recovery stance" in content
    assert "Current Phase 8B status" in content

    expected_gates = {
        "L0: explicit live mode selected",
        "L1: clean working tree and expected branch/commit verified",
        "L2: external live lab config provided from outside Git",
        "L3: runtime-only kubeconfig/credential references validated",
        "L4: physical hub identity proof passes",
        "L5: logical role discovery proof passes",
        "L6: managed cluster set exactly matches expectation",
        "L7: RBAC/live prerequisites pass",
        "L8: scenario live allowlist permits scenario",
        "L9: dry-run/materialized invocation reviewed",
        "L10: final human confirmation before mutation",
    }
    for gate in expected_gates:
        assert gate in content

    assert content.count("design-only / not executable") >= len(expected_gates)


def test_lab_role_controller_live_lab_config_schema_scenario_policy_matches_catalog():
    """Phase 8B scenario policy should use actual catalog IDs and keep first live scope read-only."""
    content = _read(LAB_CONTROLLER_SCHEMA_DOC)

    for scenario_id in SCENARIOS_BY_ID:
        assert f"`{scenario_id}`" in content

    assert "first future live scenario remains read-only/preflight-only" in content
    assert "passive switchover is not the first live scenario" in content
    assert "restore, decommission, failure injection, and mutating scenarios are later-phase only" in content
    assert "decommission is disposable-lab-only unless separately designed" in content
    assert "arbitrary shell commands are forbidden" in content
    assert "Agent-invented live commands are forbidden" in content


def test_lab_role_controller_live_lab_config_schema_uses_sanitized_placeholders_only():
    """The schema design example must avoid real kubeconfig, API, credential, or private ID values."""
    content = _read(LAB_CONTROLLER_SCHEMA_DOC)

    for placeholder in (
        "<runtime-only-kubeconfig-ref>",
        "<runtime-only-context-ref>",
        "<redacted-api-fingerprint>",
        "<operator-provided-approval-ref>",
        "<caller-provided-artifact-dir>",
    ):
        assert placeholder in content

    for example_section in (
        "identity_expectations:",
        "role_discovery:",
        "rbac_prerequisites:",
        "scenario_allowlist:",
        "redaction_policy:",
    ):
        assert example_section in content

    _assert_no_real_live_config_literals(LAB_CONTROLLER_SCHEMA_DOC, content)


def test_lab_role_controller_live_lab_config_schema_documents_phase8c_model_boundary():
    """Phase 8C model docs must not imply config loading or live execution exists."""
    content = _read(LAB_CONTROLLER_SCHEMA_DOC)

    required = (
        "Phase 8C adds a pure typed Python model only",
        "no live config loading exists",
        "no YAML or JSON file loading of real config exists",
        "no live execution exists",
        "examples remain sanitized and fake",
        "Recommendation: READY_FOR_PHASE_8D_READ_ONLY_DISCOVERY_DESIGN",
    )

    for token in required:
        assert token in content


def test_lab_role_controller_read_only_discovery_design_is_non_live():
    """Phase 8D discovery design must not imply discovery is implemented."""
    content = _read(LAB_CONTROLLER_READ_ONLY_DISCOVERY_DOC)

    required = (
        "This is a proposed design",
        "It does not implement read-only discovery",
        "It does not contact live clusters",
        "It does not read kubeconfigs",
        "It does not\nenable live ACM certification",
        "It does not enable mutation",
        "It does not enable automatic recovery",
        "The current lab role\ncontroller implementation remains non-live",
        "Phase 8D does not load real config files",
        "Recommendation: READY_FOR_PHASE_8G_READ_ONLY_BACKEND_INTERFACE_SKELETON",
    )

    for token in required:
        assert token in content


def test_lab_role_controller_read_only_discovery_design_documents_required_contracts():
    """The Phase 8D design should define gates, inputs, interfaces, artifacts, and failure decisions."""
    content = _read(LAB_CONTROLLER_READ_ONLY_DISCOVERY_DOC)

    for section in (
        "## Definition of Read-Only Discovery",
        "## Required Live Gates Before Discovery",
        "## Runtime-Only Input Contract",
        "## Discovery Backend Interface Design",
        "## Physical Hub Identity Evidence",
        "## Logical Role Discovery Evidence",
        "## Managed Cluster Set Verification",
        "## Read-Only Query / Command Policy",
        "## Discovery Artifact Design",
        "## Redaction Policy For Discovery",
        "## Failure Decision Model",
        "## Test / Guardrail Requirements For Future Implementation",
    ):
        assert section in content

    for conceptual_type in (
        "ReadOnlyDiscoveryBackend",
        "ReadOnlyDiscoveryRequest",
        "ReadOnlyDiscoveryResult",
        "HubDiscoveryEvidence",
        "PhysicalIdentityEvidence",
        "LogicalRoleEvidence",
        "ManagedClusterSetEvidence",
        "ReadOnlyResourceQueryPlan",
        "DiscoveryRedactionReport",
        "DiscoveryDecision",
    ):
        assert conceptual_type in content

    for field in (
        "discovery_mode: read_only",
        "runtime_inputs_redacted: true",
        "live_certification_evidence",
        "physical_identity_evidence",
        "logical_role_evidence",
        "managed_cluster_set_evidence",
        "command_query_summary",
    ):
        assert field in content


def test_lab_role_controller_read_only_discovery_design_documents_gates_and_forbidden_commands():
    """The read-only discovery design must document live gates and keep mutation forbidden."""
    content = _read(LAB_CONTROLLER_READ_ONLY_DISCOVERY_DOC)

    for gate in (
        "L0: explicit live mode selected",
        "L1: clean working tree and expected branch/commit verified",
        "L2: external live lab config provided from outside Git",
        "L3: runtime-only kubeconfig and credential references validated",
        "L4: physical hub identity proof gate initialized",
        "L5: logical role discovery gate initialized",
        "L6: managed cluster set expectation available",
        "L7: RBAC/read prerequisites available",
        "L8: scenario allowlist permits read-only discovery/preflight",
        "L9: materialized read-only invocation reviewed",
        "L10: final confirmation before mutation",
    ):
        assert gate in content

    for token in (
        "L10 is not required for\nread-only discovery",
        "Arbitrary shell commands",
        "Mutation-capable commands",
        "Agent-invented commands",
        "Forbidden in read-only discovery",
        "Any mutation moves out of Phase 8D scope",
    ):
        assert token in content


def test_lab_role_controller_read_only_discovery_scenarios_match_catalog():
    """The Phase 8D scenario policy should use current catalog IDs."""
    content = _read(LAB_CONTROLLER_READ_ONLY_DISCOVERY_DOC)

    for scenario_id in SCENARIOS_BY_ID:
        assert f"`{scenario_id}`" in content

    for initially_allowed in ("lab-readiness", "baseline-check", "preflight", "final-baseline-check"):
        assert f"`{initially_allowed}`" in content


def test_lab_role_controller_read_only_discovery_design_documents_phase8e_guardrail_status():
    """Phase 8E status must stay guardrail-only and non-live, with no backend implementation claim."""
    content = _read(LAB_CONTROLLER_READ_ONLY_DISCOVERY_DOC)

    required = (
        "## Phase 8E Status",
        "Phase 8E adds guardrail code and no backend implementation",
        "tests/release/lab_controller/read_only_discovery.py",
        "tests/release/test_lab_controller_phase8e_read_only_discovery_guardrails.py",
        "Phase 8E remains non-live",
        "does not implement read-only discovery",
        "even with L10 present, Phase 8E never authorizes mutation",
        "fail-closed read-only query plan validation",
        "fail-closed read-only discovery artifact field contracts",
        "live_certification_evidence=false",
        "## Phase 8F Status",
        "lab-role-controller-read-only-backend-design.md",
        "READY_FOR_PHASE_8G_READ_ONLY_BACKEND_INTERFACE_SKELETON",
        "Live read-only discovery is still not implemented",
    )

    for token in required:
        assert token in content


def test_lab_role_controller_read_only_backend_design_is_non_live():
    """Phase 8F/8G/8H backend docs must not imply real transport or live execution exists."""
    content = _read(LAB_CONTROLLER_READ_ONLY_BACKEND_DOC)

    required = (
        "This is a proposed backend design",
        "it does not implement\na transport backend",
        "It does not contact live clusters",
        "It does not read kubeconfigs",
        "It does not load real live config files",
        "It does not\nexecute `oc`, `kubectl`, or `ansible-playbook`",
        "It does not invoke live adapters",
        "It does not enable live ACM\ncertification",
        "It does not enable mutation",
        "It does not enable automatic recovery",
        "The current implementation remains\nnon-live",
        "Transport execution is not part of Phase 8F or Phase 8G",
        "## Phase 8G Status",
        "tests/release/lab_controller/read_only_backend.py",
        "UnimplementedReadOnlyDiscoveryBackend",
        "## Phase 8H Status",
        "It adds fake transport contracts only",
        "tests/release/lab_controller/read_only_transport.py",
        "FakeReadOnlyTransport",
        "Phase 8H remains non-live",
        "not live mutation",
        "Recommendation: READY_FOR_PHASE_8I_READ_ONLY_LIVE_TRANSPORT_DESIGN_REVIEW",
    )

    for token in required:
        assert token in content


def test_lab_role_controller_read_only_backend_design_documents_contracts():
    """Phase 8F should define backend contracts while preserving fail-closed guardrail use."""
    content = _read(LAB_CONTROLLER_READ_ONLY_BACKEND_DOC)

    for section in (
        "## Backend Architecture Overview",
        "## Request Contract",
        "## Result Contract",
        "## Transport Abstraction",
        "## Query Planner Design",
        "## Evidence Collection Design",
        "## Decision Classification",
        "## Artifact Contract",
        "## Redaction Model",
        "## Integration With Existing Controller",
        "## Test Requirements For Future Backend Implementation",
        "## Future Implementation Sequence",
    ):
        assert section in content

    for component in (
        "ReadOnlyDiscoveryOrchestrator",
        "ReadOnlyDiscoveryBackend",
        "ReadOnlyQueryPlanner",
        "ReadOnlyTransport",
        "HubEvidenceCollector",
        "IdentityEvidenceCollector",
        "RoleEvidenceCollector",
        "ManagedClusterEvidenceCollector",
        "RbacReadinessCollector",
        "DiscoveryArtifactBuilder",
        "DiscoveryRedactor",
        "DiscoveryDecisionClassifier",
    ):
        assert component in content

    for field in (
        "runtime_only_hub_refs",
        "mutation_enabled=false",
        "live_certification_evidence=false",
        "runtime_inputs_redacted=true",
        "query_plan_summary",
        "transport_summary",
    ):
        assert field in content

    for guardrail in (
        "Phase 8C `ExternalLiveLabConfig`",
        "Phase 8E guardrail validation",
        "L0-L9",
        "not use L10 to authorize mutation",
        "not accept arbitrary shell strings",
        "backend rejects mutating verbs",
        "backend rejects Agent-invented commands",
        "Do not implement mutation next",
    ):
        assert guardrail in content


def test_lab_role_controller_read_only_live_transport_review_is_design_only():
    """Phase 8I review must stay design-only and must not imply live transport exists."""
    content = _read(LAB_CONTROLLER_READ_ONLY_LIVE_TRANSPORT_REVIEW_DOC)

    required = (
        "This is a design review.",
        "It does not implement live transport.",
        "It does not contact live clusters.",
        "It does not read kubeconfigs.",
        "It does not load real live config files.",
        "It does not run `oc`, `kubectl`, or `ansible-playbook`.",
        "It does not invoke live release adapters.",
        "It does not enable live ACM certification.",
        "It does not enable mutation.",
        "It does not enable automatic recovery.",
        "The current implementation remains non-live.",
        "READY_FOR_PHASE_8I_READ_ONLY_LIVE_TRANSPORT_DESIGN_REVIEW",
        "Recommendation: READY_FOR_PHASE_8J_OPT_IN_READ_ONLY_LIVE_TRANSPORT_IMPLEMENTATION",
        "is **not** live ACM certification evidence",
    )
    for token in required:
        assert token in content


def test_lab_role_controller_read_only_live_transport_review_documents_contract():
    """Phase 8I review must define transport policy, gates, decisions, and the artifact contract."""
    content = _read(LAB_CONTROLLER_READ_ONLY_LIVE_TRANSPORT_REVIEW_DOC)

    for section in (
        "## Status",
        "## Final Recommendation",
        "## Scope",
        "## Current Foundation",
        "## Transport Mechanism Decision",
        "## Live Contact Boundary",
        "## Required Gates Before Future Live Contact",
        "## Runtime Credential and Kubeconfig Boundary",
        "## Read-Only Query Surface",
        "## Query Plan Enforcement",
        "## Response Handling and Redaction",
        "## Evidence Mapping",
        "## Decision Mapping",
        "## Artifact Contract",
        "## Test Matrix For Phase 8J",
        "## Operational Safeguards",
        "## Risk Register",
        "## Phase 8J Entry Criteria",
        "## Documentation Integration",
    ):
        assert section in content

    for token in (
        "### Recommended Transport Policy",
        "structured API-client abstraction",
        "Rejected for the first read-only live implementation",
        "FakeReadOnlyTransport",
        "L0-L9 are required before read-only contact",
        "authorize read-only contact or mutation in Phase 8J",
        "mutation_enabled=false",
        "live_certification_evidence=false",
        "live_contact_attempted",
        "discovery_mode=read_only",
        "runtime_inputs_redacted=true",
    ):
        assert token in content


def test_lab_role_controller_cli_and_docs_do_not_claim_live_mode_support():
    """The CLI and Agent docs should keep live and release-framework-live unsupported."""
    help_text = lab_controller_cli._parser().format_help().lower()
    agent_doc = _read("docs/development/lab-role-controller-agent-instructions.md").lower()
    schema_doc = _read(LAB_CONTROLLER_SCHEMA_DOC).lower()

    assert "live" not in lab_controller_cli.SUPPORTED_MODES
    assert "release-framework-live" not in lab_controller_cli.SUPPORTED_MODES
    assert "live" in lab_controller_cli.LIVE_MODES
    assert "release-framework-live" in lab_controller_cli.LIVE_MODES
    assert "live modes are unsupported" in help_text
    assert "supported: fake, release-framework-dry-run, release-framework-local" in help_text
    assert "live mode is supported" not in agent_doc
    assert "live execution is supported" not in agent_doc
    assert ".release is the default" not in agent_doc
    assert ".release is the default" not in schema_doc


def test_lab_role_controller_safety_docs_avoid_real_live_config_examples():
    """Lab-controller safety docs must not introduce real-looking live config or credential examples."""
    for path in LAB_CONTROLLER_SAFETY_DOCS:
        _assert_no_real_live_config_literals(path, _read(path))


def test_lab_role_controller_backend_design_records_phase8j_status():
    """The backend design must record Phase 8J as an opt-in, non-live, gated implementation."""
    content = _read(LAB_CONTROLLER_READ_ONLY_BACKEND_DOC)

    for token in (
        "## Phase 8J Status",
        "tests/release/lab_controller/read_only_live_transport.py",
        "ReadOnlyLiveTransport",
        "disabled by default",
        "an injected client",
        "L0-L9 gate evidence",
        "mutation_attempted=false",
        "live_certification_evidence=false",
        "Tokens, passwords, secrets, and credentials are rejected outright",
        "ACM_ENABLE_LAB_CONTROLLER_LIVE_TRANSPORT_PILOT",
        "READY_FOR_PHASE_8K_READ_ONLY_LIVE_PREFLIGHT_PILOT_DESIGN",
        "Phase 8J: first opt-in read-only live transport implementation behind explicit gates (complete)",
        "Phase 8K: read-only live preflight artifact pilot (next)",
    ):
        assert token in content


def test_lab_role_controller_live_transport_review_records_phase8j_implementation():
    """The Phase 8I review must point at the opt-in Phase 8J implementation while staying design-only."""
    content = _read(LAB_CONTROLLER_READ_ONLY_LIVE_TRANSPORT_REVIEW_DOC)

    assert "## Phase 8J Implementation Status" in content
    for token in (
        "This document remains a design review.",
        "read_only_live_transport.py",
        "disabled by default",
        "never sets `live_certification_evidence` true",
        "ACM_ENABLE_LAB_CONTROLLER_LIVE_TRANSPORT_PILOT",
        "READY_FOR_PHASE_8K_READ_ONLY_LIVE_PREFLIGHT_PILOT_DESIGN",
    ):
        assert token in content
    # The review's own design-only recommendation must remain unchanged.
    assert "Recommendation: READY_FOR_PHASE_8J_OPT_IN_READ_ONLY_LIVE_TRANSPORT_IMPLEMENTATION" in content
