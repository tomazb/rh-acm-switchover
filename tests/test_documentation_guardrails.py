"""Regression checks for maintained support documentation."""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


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
