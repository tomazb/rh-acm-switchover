"""Regression checks for maintained support documentation."""

import ast
import re
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
LAB_CONTROLLER_READ_ONLY_LIVE_PREFLIGHT_PILOT_DOC = (
    "docs/development/lab-role-controller-read-only-live-preflight-pilot-design.md"
)
LAB_CONTROLLER_SAFETY_DOCS = (
    "docs/development/lab-role-controller-live-readiness-design.md",
    LAB_CONTROLLER_SCHEMA_DOC,
    LAB_CONTROLLER_READ_ONLY_DISCOVERY_DOC,
    LAB_CONTROLLER_READ_ONLY_BACKEND_DOC,
    LAB_CONTROLLER_READ_ONLY_LIVE_TRANSPORT_REVIEW_DOC,
    LAB_CONTROLLER_READ_ONLY_LIVE_PREFLIGHT_PILOT_DOC,
    "docs/development/lab-role-controller-agent-instructions.md",
)

CONTRIBUTING_DOC = "CONTRIBUTING.md"
TESTING_DOC = "docs/development/testing.md"
ARCHITECTURE_DOC = "docs/development/architecture.md"
LAB_CONTROLLER_SPEC_DOC = "docs/development/lab-role-controller-spec.md"
CONTRIBUTOR_DOCS = (CONTRIBUTING_DOC, TESTING_DOC, ARCHITECTURE_DOC)


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
        assert (
            "deprecated" in context or "removed" in context
        ), f"Active argocd-manage.sh guidance remains in {path}: {line}"


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


def test_contributing_matches_current_dev_workflow():
    """Contributor guide should match current environment and test guidance."""
    content = _read("CONTRIBUTING.md")

    for token in (".venv", "requirements-dev.txt", "./run_tests.sh", "CHANGELOG.md"):
        assert token in content, f"Missing {token} from CONTRIBUTING.md"


CI_WORKFLOW = ".github/workflows/ci-cd.yml"

# Matches the governing formatter invocations only: a line whose command is black or isort and
# which passes --line-length explicitly. pylint/flake8 `--max-line-length` is deliberately not
# matched (see _ci_governing_line_length).
_FORMATTER_LINE_LENGTH = re.compile(r"^\s*(?:black|isort)\b[^\n]*?--line-length[ =](\d+)", re.MULTILINE)


def _ci_governing_line_length() -> str:
    """Read the line length CI actually enforces, from its black/isort invocations.

    `setup.cfg` is deliberately NOT the source of truth. CI never consults it: it passes
    `--max-line-length` to flake8 explicitly, and runs that flake8 pass with `--exit-zero`, so
    flake8 cannot fail the build at all. The value that can break a build is the one handed to
    `black --check` and `isort --check-only`.

    A parse failure raises instead of falling back to a default. A silent fallback would
    reproduce precisely the defect class this guardrail exists to catch: a documented number
    that no longer tracks the value CI enforces.
    """
    workflow = _read(CI_WORKFLOW)
    values = set(_FORMATTER_LINE_LENGTH.findall(workflow))

    assert values, (
        f"could not parse a --line-length from any black/isort invocation in {CI_WORKFLOW}; "
        "this guardrail must fail loudly rather than assume a default"
    )
    assert len(values) == 1, (
        f"{CI_WORKFLOW} passes disagreeing --line-length values to black/isort: {sorted(values)}. "
        "CONTRIBUTING.md cannot document one maximum until CI agrees with itself."
    )
    return values.pop()


def test_contributing_line_length_matches_ci():
    """Contributor line-length guidance must match the line length CI enforces.

    The maximum is parsed from the workflow's black/isort invocations rather than hard-coded or
    read from `setup.cfg`, so this test actually breaks if CI's enforced line length moves and
    CONTRIBUTING.md is not updated to match.
    """
    content = _read(CONTRIBUTING_DOC)
    max_line_length = _ci_governing_line_length()

    assert re.search(rf"[Mm]aximum line length:\s*{re.escape(max_line_length)}\b", content), (
        f"CONTRIBUTING.md must state the {max_line_length}-character maximum line length that "
        f"CI enforces via the black/isort --line-length flags in {CI_WORKFLOW}"
    )

    # "100 characters" is the specific obsolete phrasing this guardrail was written to catch.
    # Skip it only if the configured value is itself 100: the dynamic assertion above already
    # covers correctness then, and this literal would otherwise reject the true value.
    if max_line_length != "100":
        assert "100 characters" not in content, "CONTRIBUTING.md still states the obsolete 100-character limit"


def test_contributing_routes_validation_to_modular_owners():
    """Contributor guide must route changes to current owners, not the retired validator class."""
    content = _read(CONTRIBUTING_DOC)

    for token in (
        "lib/validation.py",
        "modules/preflight/",
        "preflight_coordinator",
        "lib/workflow.py",
        "lib/operation_runners.py",
        "tests/release/checks/",
        "tests/release/lab_controller/",
    ):
        assert token in content, f"CONTRIBUTING.md must route work to {token}"

    assert not re.search(
        r"[Aa]dd (?:a )?method to\s+`?PreflightValidator", content
    ), "CONTRIBUTING.md still teaches the obsolete 'add a method to PreflightValidator' recipe"


def test_contributing_names_primary_branch_and_start_gate():
    """Contributor guide must name the development branch and the mandatory reading gate."""
    content = _read(CONTRIBUTING_DOC)

    assert "AGENTS.md" in content, "CONTRIBUTING.md must direct contributors to AGENTS.md"
    assert re.search(
        r"`ansible`[^\n]*(primary|development) branch", content
    ), "CONTRIBUTING.md must identify `ansible` as the primary development branch"


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


def test_removed_bash_argocd_script_routes_to_supported_paths():
    """The argocd-manage.sh removal note must route operators to the supported form factors."""
    content = _read("scripts/README.md")

    assert "argocd-manage.sh" in content
    assert "removed" in content
    assert "--argocd-resume-only" in content
    assert "argocd_manage" in content


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


def _agents_headings() -> list:
    return re.findall(r"^## (.+)$", _read("AGENTS.md"), re.MULTILINE)


def _agents_section(heading_pattern: str) -> str:
    """Return the body of the AGENTS.md `##` section whose heading matches.

    Sections are located by regex rather than by an exact heading literal so that policy
    semantics, not one historical phrasing, are what these guardrails pin. Exactly one
    section may match: a decoy section that satisfies a guardrail while a later duplicate
    contradicts it is the failure mode this rejects.
    """
    content = _read("AGENTS.md")
    sections = re.split(r"^## ", content, flags=re.MULTILINE)[1:]
    matches = [s for s in sections if re.search(heading_pattern, s.splitlines()[0], re.IGNORECASE)]

    assert matches, f"AGENTS.md has no `##` section matching {heading_pattern!r}"
    assert len(matches) == 1, f"AGENTS.md has {len(matches)} sections matching {heading_pattern!r}; expected one"
    return matches[0]


_LIST_ITEM = re.compile(r"^\s*(?:[-*]\s+|\d+\.\s+|\|\s*)")


def _statements(section: str) -> list:
    """Flattened list items and table rows — the units policy rules are written in.

    Anchoring an assertion to the start of a statement is what distinguishes a stated rule
    from the same words appearing anywhere in the section, including inside a sentence that
    negates them.
    """
    items: list = []
    current = None

    for line in section.splitlines():
        if _LIST_ITEM.match(line):
            if current is not None:
                items.append(_flatten(current).strip())
            current = _LIST_ITEM.sub("", line, count=1)
        elif current is not None and line.startswith((" ", "\t")) and line.strip():
            current += " " + line.strip()
        elif current is not None:
            items.append(_flatten(current).strip())
            current = None

    if current is not None:
        items.append(_flatten(current).strip())
    return items


def _assert_rules(section: str, label: str, *patterns: str) -> None:
    """Each pattern must match the start of a statement, i.e. be stated as its own rule."""
    statements = _statements(section)
    for pattern in patterns:
        assert any(
            re.search(pattern, statement, re.IGNORECASE) for statement in statements
        ), f"{label} should state, as its own rule: {pattern}"


def _flatten(text: str) -> str:
    """Collapse whitespace so policy guardrails do not depend on where lines wrap."""
    return re.sub(r"\s+", " ", text)


def _assert_states(section: str, label: str, *patterns: str) -> None:
    flattened = _flatten(section)
    for pattern in patterns:
        assert re.search(pattern, flattened, re.IGNORECASE), f"{label} should state: {pattern}"


def _enforced_version_surfaces() -> tuple:
    """Version surfaces the collection metadata guardrail actually enforces.

    Read from that test's source rather than restated here, so this guardrail cannot drift
    away from the check it is describing. The file is read as text, not imported: root
    `tests/` jobs do not install `ansible-core`.
    """
    source = _read("ansible_collections/tomazb/acm_switchover/tests/unit/test_collection_metadata.py")
    enforcing = "test_all_release_version_surfaces_match_repo_release_version"

    functions = [
        node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef) and node.name == enforcing
    ]
    assert len(functions) == 1, f"Expected exactly one {enforcing}"

    mappings = [node for node in ast.walk(functions[0]) if isinstance(node, ast.Dict)]
    assert len(mappings) == 1, f"Expected exactly one version-surface mapping in {enforcing}"

    surfaces = []
    for key in mappings[0].keys:
        # Fail loudly rather than silently dropping a surface declared via a constant or an
        # f-string: a missed surface is exactly the drift this guardrail exists to catch.
        assert isinstance(key, ast.Constant) and isinstance(
            key.value, str
        ), f"{enforcing} declares a version surface this guardrail cannot read: {ast.dump(key)}"
        surfaces.append(key.value)

    assert len(surfaces) >= 8, "Expected the enforcing test to declare the full version-surface set"
    return tuple(surfaces)


def test_agents_release_governance_separates_development_from_release_work():
    """Version policy must not turn every development PR into a release."""
    policy = _agents_section(r"(version|release).*(governance|management)")

    _assert_states(
        policy,
        "Ordinary development policy",
        r"ordinary development",
        r"may modify code, tests, documentation, and tooling",
        r"\[Unreleased\]",
        r"not change released version identifiers or create release tags",
        r"next explicit release version from accumulated changes",
        r"not require every individual development PR to bump a version",
    )
    _assert_states(
        policy,
        "Explicit release policy",
        r"explicit release",
        r"Python and Bash released versions must match",
        r"changelog release heading",
        r"changelog comparison links",
        r"tag for the exact release commit",
        r"partial metadata bump",
        r"incomplete release",
    )

    # A governance or process correction is not a release. The durable rule is what matters;
    # naming the issue that once prompted it is not a policy requirement.
    _assert_states(
        policy,
        "Non-release correction policy",
        r"(governance|process|documentation)[^.]{0,80}correction is not a release",
    )
    assert "#165" not in policy, "Release policy must state the durable rule, not cite a historical issue"

    contradictory = "When making changes to either Python or Bash code, update BOTH version files"
    assert contradictory not in policy


def test_agents_release_governance_names_every_enforced_version_surface():
    """A release bump that misses an enforced surface leaves the suite red; policy must list them all."""
    policy = _agents_section(r"(version|release).*(governance|management)")

    for surface in _enforced_version_surfaces():
        assert surface in policy, f"Release governance omits enforced version surface {surface}"

    _assert_states(
        policy,
        "Collection version lifecycle",
        r"galaxy\.yml",
        r"no independent\s+release lifecycle|follows the repository release version",
    )


def test_agents_defines_a_mandatory_start_gate():
    """Agents must have a deterministic, fail-closed way to start work."""
    gate = _agents_section(r"start gate")

    # Each step must be stated as an instruction, not merely mentioned. Vocabulary that
    # appears only inside a sentence relaxing the gate must not satisfy these.
    _assert_rules(
        gate,
        "Start gate",
        r"^fetch current .{0,4}origin/ansible",
        r"^confirm repository identity",
        r"^read the current .{0,4}AGENTS\.md",
        r"^read the governing issue",
        r"^when the work will mutate",
        r"^record, before the first edit",
    )
    _assert_states(gate, "Start gate", r"isolated .{0,40}/worktrees/", r"base SHA", r"head SHA", r"merge base")
    _assert_states(gate, "Start gate hard-fail rule", r"hard-fail", r"stop and return to the operator")
    _assert_rules(
        gate,
        "Start gate hard-fail conditions",
        r"^authorization .{0,30}missing",
        r"^the scope is ambiguous",
        r"^the base is stale",
        r"^an independent-validation checkout is dirty",
        r"^mandatory evidence is unavailable",
    )


def test_agents_defines_an_ordered_authority_hierarchy():
    """Conflicts between sources must resolve deterministically, not by preference."""
    hierarchy = _flatten(_agents_section(r"authority hierarchy"))

    tiers = (
        r"`AGENTS\.md`",
        r"governing issue",
        r"domain authority",
        r"current source, tests",
        r"hypothes[ei]s",
    )
    positions = []
    for tier in tiers:
        match = re.search(tier, hierarchy, re.IGNORECASE)
        assert match, f"Authority hierarchy is missing tier: {tier}"
        positions.append(match.start())

    assert positions == sorted(positions), "Authority tiers must appear in precedence order"

    _assert_states(
        hierarchy,
        "Conflict handling",
        r"stop",
        r"surface",
        r"not silently",
    )


def test_agents_protected_file_policy_scopes_hook_enforcement_honestly():
    """The hook is defense-in-depth; the policy is the control."""
    content = _read("AGENTS.md")
    assert "\n## Protected Critical Files\n" in content, "`.claude/settings.json` cites this heading by name"

    policy = _agents_section(r"protected critical files")

    _assert_states(
        policy,
        "Protected-file policy",
        r"ACM_SWITCHOVER_RUNBOOK\.md",
        r"\*\.skill\.md",
        r"explicit operator approval",
        r"diff",
        r"sync",
        r"no speculative or cosmetic",
    )
    # Case-sensitive and literal: `.claude/skills/**/*.skill.md` does not match a file named
    # `SKILL.md`, which left the release, refactor-simplify, and mutation-testing skills
    # outside the protected set. Pin both the recursive scope and the uppercase form so that
    # gap cannot reopen.
    assert ".claude/skills/**" in policy, "Protected-file policy must cover the whole .claude/skills tree"
    assert "SKILL.md" in policy, "Protected-file policy must name the uppercase SKILL.md definitions"

    _assert_states(
        policy,
        "Hook enforcement scope",
        r"defense-in-depth",
        r"not universal enforcement",
        r"regardless of tool",
    )
    _assert_states(
        policy,
        "Independent protected-file verification",
        r"builder.{0,80}validator.{0,80}resolver",
    )


def test_agents_verification_matrix_covers_every_changed_surface():
    """Generic full-suite wording is replaced by a matrix keyed to the change."""
    matrix = _flatten(_agents_section(r"verification matrix"))

    for surface in (
        r"documentation",
        r"Python CLI",
        r"Ansible Collection",
        r"parity",
        r"RBAC",
        r"release-validation",
        r"lab-controller",
        r"release / version|release and version|version work",
    ):
        assert re.search(surface, matrix, re.IGNORECASE), f"Verification matrix omits surface: {surface}"

    _assert_states(
        matrix,
        "Verification rules",
        r"targeted tests first",
        r"every gate the actual edit invalidates",
        r"before terminal validation",
        r"not rerun unrelated full suites",
        r"exact-head CI",
    )
    _assert_states(
        matrix,
        "Collection gate surfaces",
        r"integration",
        r"scenario",
        r"syntax",
        r"build",
    )


def test_agents_defines_governed_finding_disposition():
    """Findings are dispositioned against the governing gate, and deferrals are tracked."""
    review = _agents_section(r"review priorities")

    # Each disposition must head its own row of the classification table.
    _assert_rules(
        review,
        "Finding disposition model",
        r"^\*\*blocking, in scope\*\*",
        r"^\*\*valid, deferred\*\*",
        r"^\*\*non-blocking observation\*\*",
        r"^\*\*invalid",
    )
    _assert_states(
        review,
        "Deferral tracking rule",
        r"deferral is complete only when it is filed",
        r"reply alone is not durable tracking",
    )


def test_agents_does_not_restate_status_owned_by_another_authority():
    """Volatile status and stale implementation snapshots must not live in policy."""
    content = _read("AGENTS.md")

    forbidden = {
        "Phase 9B remains blocked": "phase status is owned by the GitHub issues",
        "monolithic orchestrator": "the Python CLI is a layered entrypoint",
        "Each phase handler checks": "phase eligibility is owned by the workflow layer",
        "lab-phase9-readiness-checklist": "that document does not exist",
    }
    for literal, reason in forbidden.items():
        assert literal not in content, f"AGENTS.md still states {literal!r}; {reason}"

    for absent_gate in ("molecule", "ansible-lint", "ansible-test"):
        assert absent_gate not in content, f"AGENTS.md claims a {absent_gate} gate that this repository does not run"


def test_agents_sections_are_unique_and_ordered():
    """Policy is navigable only when each concern has exactly one home, in a stable order."""
    headings = _agents_headings()

    duplicates = [h for h in headings if headings.count(h) > 1]
    assert not duplicates, f"AGENTS.md has duplicate `##` headings: {sorted(set(duplicates))}"

    required_order = (
        r"repository identity",
        r"start gate",
        r"authority hierarchy",
        r"invariants",
        r"protected critical files",
        r"parity contract",
        r"rbac",
        r"builder",
        r"terminal validation and review convergence",
        r"verification matrix",
        r"review priorities",
        r"version governance",
        r"lab-controller",
        r"evidence rules",
        r"authoritative document index",
    )

    positions = []
    for pattern in required_order:
        matched = [i for i, h in enumerate(headings) if re.search(pattern, h, re.IGNORECASE)]
        assert len(matched) == 1, f"Expected exactly one AGENTS.md section matching {pattern!r}, found {len(matched)}"
        positions.append(matched[0])

    assert positions == sorted(positions), "AGENTS.md sections must follow the documented policy order"


def test_agents_policy_is_not_relaxed_by_escape_hatches():
    """A rule plus a sentence retracting it is not policy. Reject the known retraction shapes.

    This is a blocklist and cannot be exhaustive: no text guardrail can prove that arbitrary
    prose does not negate a rule somewhere else in the document. It raises the cost of an
    accidental relaxation; review and the authority hierarchy remain the real control.
    """
    content = _flatten(_read("AGENTS.md")).lower()

    for retraction in (
        "may proceed without authorization",
        "do not classify findings",
        "historical vocabulary only",
        "is obsolete",
        "may be skipped",
        "no longer applies",
        "for reference only",
        "is discretionary",
        "at your discretion",
        "continue after any failure",
        "continue working anyway",
    ):
        assert retraction not in content, f"AGENTS.md contains a policy retraction: {retraction!r}"


def test_agents_hard_fail_conditions_are_not_softened():
    """A hard-fail condition that also permits continuing is not a hard fail."""
    gate = _agents_section(r"start gate")

    conditions = (
        r"^authorization .{0,30}missing",
        r"^the scope is ambiguous",
        r"^the base is stale",
        r"^an independent-validation checkout is dirty",
        r"^mandatory evidence is unavailable",
    )
    softeners = ("continue", "proceed", "anyway", "assume", "ignore", "skip", "discretion", "optional")

    matched = 0
    for statement in _statements(gate):
        if not any(re.search(condition, statement, re.IGNORECASE) for condition in conditions):
            continue
        matched += 1
        for softener in softeners:
            assert softener not in statement.lower(), f"Hard-fail condition is softened by {softener!r}: {statement}"

    assert matched == len(conditions), f"Expected {len(conditions)} hard-fail conditions, matched {matched}"


def test_agents_document_links_resolve():
    """Delegation only works when every named authority actually exists."""
    content = _read("AGENTS.md")

    for target in re.findall(r"\]\(([^)]+)\)", content):
        if target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        path = target.split("#", 1)[0]
        assert (REPO_ROOT / path).exists(), f"AGENTS.md links to missing path: {path}"


def test_agents_internal_anchors_resolve():
    """Cross-references inside the policy must not rot when sections move."""
    content = _read("AGENTS.md")

    slugs = set()
    for heading in re.findall(r"^#{2,3} (.+)$", content, re.MULTILINE):
        slug = re.sub(r"[^a-z0-9\s-]", "", heading.lower())
        slugs.add(re.sub(r"\s+", "-", slug.strip()))

    for target in re.findall(r"\]\(#([^)]+)\)", content):
        assert target in slugs, f"AGENTS.md has a dangling internal anchor: #{target}"

    assert "terminal-validation-and-review-convergence" in slugs, "Other documents anchor to this section"


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


def test_lab_role_controller_live_readiness_design_records_phase8k_phase8l_sequence():
    """The live-readiness roadmap should keep Phase 8K design-only and Phase 8L rehearsal-only."""
    content = _read("docs/development/lab-role-controller-live-readiness-design.md")

    for token in (
        "Phase 8K: read-only live preflight pilot design, no pilot execution",
        "Phase 8L: read-only live preflight pilot dry-run or fake-backed rehearsal",
        "Later audited phase: read-only live pilot audit and closeout after separately approved live contact",
        "before any live mutation path exists",
    ):
        assert token in content

    for stale in (
        "Phase 8K: read-only live preflight artifact pilot",
        "Phase 8L: read-only live pilot audit and closeout",
    ):
        assert stale not in content


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
        "Phase 8K: read-only live preflight pilot design, no pilot execution (complete)",
        "Phase 8L: read-only live preflight pilot dry-run or fake-backed rehearsal (next)",
    ):
        assert token in content


def test_lab_role_controller_backend_design_records_phase8k_status():
    """The backend design should point to the Phase 8K pilot design and keep Phase 8L narrow."""
    content = _read(LAB_CONTROLLER_READ_ONLY_BACKEND_DOC)

    for token in (
        "## Phase 8K Status",
        "lab-role-controller-read-only-live-preflight-pilot-design.md",
        "It is design/documentation only.",
        "does not run a pilot",
        "run `oc`, `kubectl`, `ansible-playbook`, release adapters, or the pytest\nrelease framework",
        "scenario/query allowlists",
        "manual recovery/retry policy",
        "READY_FOR_PHASE_8L_READ_ONLY_LIVE_PREFLIGHT_PILOT_DRY_RUN",
        "Phase 8L is not a broad live rollout",
        "Recommendation: READY_FOR_PHASE_8L_READ_ONLY_LIVE_PREFLIGHT_PILOT_DRY_RUN",
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


def test_lab_role_controller_live_transport_review_records_phase8k_design():
    """The Phase 8I review should point to Phase 8K without changing its own recommendation."""
    content = _read(LAB_CONTROLLER_READ_ONLY_LIVE_TRANSPORT_REVIEW_DOC)

    for token in (
        "## Phase 8K Pilot Design Status",
        "lab-role-controller-read-only-live-preflight-pilot-design.md",
        "It is design/documentation only.",
        "does not run a pilot",
        "does not run a pilot, contact live clusters, read kubeconfigs",
        "READY_FOR_PHASE_8L_READ_ONLY_LIVE_PREFLIGHT_PILOT_DRY_RUN",
        "fake-backed or\nnon-contact rehearsal",
    ):
        assert token in content
    assert "Recommendation: READY_FOR_PHASE_8J_OPT_IN_READ_ONLY_LIVE_TRANSPORT_IMPLEMENTATION" in content


def test_lab_role_controller_phase8k_preflight_pilot_design_is_design_only():
    """Phase 8K must remain design-only and must not imply live pilot execution exists."""
    content = _read(LAB_CONTROLLER_READ_ONLY_LIVE_PREFLIGHT_PILOT_DOC)

    for token in (
        "# Lab Role Controller Read-Only Live Preflight Pilot Design",
        "This is a pilot design.",
        "It does not run a pilot.",
        "It does not contact live clusters.",
        "It does not read kubeconfigs.",
        "It does not load live config files.",
        "It does not execute the Phase 8J live transport.",
        "It does not run `oc`, `kubectl`, `ansible-playbook`, release adapters, or the pytest release framework",
        "It does not enable mutation.",
        "It does not produce live ACM certification evidence.",
        "It does not enable automatic recovery.",
        "It does not add Agent-driven live behavior.",
        "Current defaults remain non-live.",
        "READY_FOR_PHASE_8L_READ_ONLY_LIVE_PREFLIGHT_PILOT_DRY_RUN",
        "not a broad\nlive rollout",
    ):
        assert token in content


def test_lab_role_controller_phase8k_preflight_pilot_design_documents_required_contract():
    """Phase 8K should define the pilot objective, gates, evidence, decisions, and next entry criteria."""
    content = _read(LAB_CONTROLLER_READ_ONLY_LIVE_PREFLIGHT_PILOT_DOC)

    for section in (
        "## Final Recommendation",
        "## Scope",
        "## Current Foundation",
        "## Pilot Objective",
        "## Pilot Non-Goals",
        "## Pilot Environment Assumptions",
        "## Operator Prerequisites",
        "## Runtime-Only Inputs",
        "## Pilot Scenario Allowlist",
        "## Pilot Query Allowlist",
        "## Gate Sequence",
        "## Invocation Shape",
        "## Artifact Contract For Pilot",
        "## Evidence Acceptance Criteria",
        "## Decision Interpretation",
        "## Abort Criteria",
        "## Manual Recovery And Retry Policy",
        "## Operator Checklist",
        "## Pilot Runbook Boundary",
        "## Human Approval And Rollback Posture",
        "## Audit Requirements Before Any Actual Pilot May Run",
        "## Risk Register",
        "## Phase 8L Entry Criteria",
        "## Recommendation",
    ):
        assert section in content

    for token in (
        "pass L0-L9 gates",
        "construct allowlisted read-only queries",
        "distinguish live contact evidence from live certification evidence",
        "`lab-readiness`",
        "`baseline-check`",
        "`preflight`",
        "`final-baseline-check`",
        "`cluster_identity`",
        "`namespace_uid`",
        "`cluster_version`",
        "`acm_mce_mch_status`",
        "`managed_cluster_status`",
        "`backup_restore_status`",
        "`live_certification_evidence=false`",
        "`mutation_enabled=false`",
        "`mutation_attempted=false`",
        "`INFRA_RETRYABLE`",
        "retry only when decision is `INFRA_RETRYABLE`",
        "human approval required before any live contact",
        "Recommendation: READY_FOR_PHASE_8L_READ_ONLY_LIVE_PREFLIGHT_PILOT_DRY_RUN",
    ):
        assert token in content

    for forbidden_claim in (
        "Recommendation: READY_WITH_FOLLOW_UPS",
        "Recommendation: FAIL_BLOCKED",
        "live ACM certification evidence=true",
        "mutation_enabled=true is allowed",
        "automatic recovery is enabled",
    ):
        assert forbidden_claim not in content


def test_lab_role_controller_phase8l_preflight_pilot_rehearsal_status_is_non_contact():
    """Phase 8L documentation should pin fake-only rehearsal boundaries and the next safe recommendation."""
    content = _read(LAB_CONTROLLER_READ_ONLY_LIVE_PREFLIGHT_PILOT_DOC)

    for token in (
        "## Phase 8L Rehearsal Status",
        "read_only_preflight_pilot.py",
        "test_lab_controller_phase8l_read_only_preflight_pilot_dry_run.py",
        "`dry_run_no_contact`",
        "`fake_backed_rehearsal`",
        "`live_read_only_unsupported_in_phase_8l`",
        "`simulated_contact_attempted`",
        "`live_contact_attempted=false`",
        "`live_contact_succeeded=false`",
        "`real_execution_evidence=false`",
        "`live_certification_evidence=false`",
        "`mutation_enabled=false`",
        "`mutation_attempted=false`",
        "does not read live config files, read kubeconfigs, read environment credentials",
        "does not read live config files, read kubeconfigs, read environment credentials,\ncreate real Kubernetes/OpenShift clients",
        "CLI/planner defaults remain non-live",
        "READY_FOR_PHASE_8M_READ_ONLY_LIVE_PREFLIGHT_PILOT_APPROVAL_PACKAGE",
        "This is not broad live rollout and is not authorization for live contact.",
    ):
        assert token in content


COLLECTION_VERIFICATION_TOKENS = (
    "ansible_collections/tomazb/acm_switchover/tests/unit/",
    "ansible_collections/tomazb/acm_switchover/tests/integration/",
    "ansible_collections/tomazb/acm_switchover/tests/scenario/",
    "--syntax-check",
    "ansible-galaxy collection build",
    "tests/e2e",
    "tests/release",
    "certification",
)


def test_testing_guide_covers_every_collection_verification_surface():
    """The gate inventory must name every maintained verification surface separately."""
    content = _read(TESTING_DOC)

    for token in COLLECTION_VERIFICATION_TOKENS:
        assert token in content, f"testing.md must document the verification surface using {token}"


def test_testing_guide_states_run_tests_is_not_complete():
    """The runner must not be presented as the complete verification surface."""
    content = _read(TESTING_DOC)

    assert "./run_tests.sh" in content
    assert (
        "is not a complete verification surface" in content
    ), "testing.md must state that ./run_tests.sh is not a complete verification surface"


def test_testing_guide_links_compatibility_authority():
    """Compatibility facts must be linked to their authority, never restated."""
    content = _read(TESTING_DOC)

    assert (
        "ansible_collections/tomazb/acm_switchover/docs/compatibility.md" in content
    ), "testing.md must link the compatibility authority"
    assert not re.search(
        r"ansible-core\s*==", content
    ), "testing.md must not pin ansible-core versions; link the compatibility authority instead"


def test_architecture_names_workflow_and_runner_extraction():
    """Architecture prose must describe the extracted flow, runner, and run-record layers."""
    content = _read(ARCHITECTURE_DOC)

    for token in (
        "run_phase_flow",
        "handle_completed_state",
        "execute_operation",
        "OperationDispatchHooks",
    ):
        assert token in content, f"architecture.md must describe {token} in prose"

    for path in ("lib/workflow.py", "lib/operation_runners.py", "lib/run_record.py"):
        assert content.count(path) >= 2, (
            f"architecture.md must reference {path} at least twice — a section heading plus a "
            "prose cross-reference — not merely list it in the file tree."
        )


def test_architecture_uses_run_record_vocabulary():
    """Architecture must use RunRecord vocabulary, not the config wording CONTEXT.md forbids."""
    content = _read(ARCHITECTURE_DOC)

    assert "RunRecord" in content, "architecture.md must name the RunRecord facade"
    assert (
        "config discovered during execution" not in content
    ), "architecture.md uses state-config wording that CONTEXT.md lists under Avoid"


def test_architecture_links_authorities_without_restating_status():
    """Architecture must link authority documents and must not carry volatile status or a version."""
    content = _read(ARCHITECTURE_DOC)

    for token in ("release-validation-framework.md", "lab-role-controller-spec.md"):
        assert token in content, f"architecture.md must link {token}"

    assert not re.search(
        r"^\*\*Version\*\*:", content, re.MULTILINE
    ), "architecture.md must not carry a document version that reads as a product release"
    assert not re.search(
        r"Phase 9[A-Z]?\s+(is|remains|has|was)\b", content
    ), "architecture.md must not restate Phase 9 status; the issue tracker owns it"


def _run_record_avoid_terms():
    """Extract the CONTEXT.md `_Avoid_` terms for the Run record concept (CONTEXT.md:21).

    Reads the terms from CONTEXT.md itself, under the `**Run record**:` entry, so this test
    tracks the authority instead of duplicating its vocabulary. If CONTEXT.md's Run record
    entry or its `_Avoid_` line goes missing, the extraction fails loudly rather than silently
    checking nothing.
    """
    context = _read("CONTEXT.md")
    match = re.search(r"\*\*Run record\*\*:.*?^_Avoid_:\s*(.+)$", context, re.DOTALL | re.MULTILINE)
    assert match, "CONTEXT.md must define an _Avoid_ line under the Run record entry (see CONTEXT.md:21)"

    avoid_line = re.sub(r"\([^)]*\)\s*$", "", match.group(1)).strip()

    terms = []
    for chunk in avoid_line.split(","):
        chunk = chunk.strip()
        if "/" in chunk:
            terms.extend(part.strip() for part in chunk.split("/") if part.strip())
        elif chunk:
            terms.append(chunk)
    return terms


def test_contributor_docs_avoid_run_record_config_vocabulary():
    """Contributor docs must not use the config-key wording CONTEXT.md bans for Run record."""
    terms = _run_record_avoid_terms()
    assert terms, "CONTEXT.md Run record _Avoid_ line must yield at least one banned term"

    for doc in CONTRIBUTOR_DOCS:
        content_lower = _read(doc).lower()
        for term in terms:
            assert term.lower() not in content_lower, (
                f"{doc} uses the term '{term}', which CONTEXT.md's Run record entry "
                "(CONTEXT.md:21, under _Avoid_) forbids outside the RunRecord facade; "
                "reword to RunRecord/persisted-key vocabulary instead"
            )


def test_lab_role_controller_spec_attributes_uid_binding_to_owning_authority():
    """Cluster-UID binding must be attributed to its owning authorities, not to AGENTS.md."""
    content = _read(LAB_CONTROLLER_SPEC_DOC)

    assert (
        "records hub identities by" in content
    ), "lab-role-controller-spec.md must still describe cluster-UID identity recording"
    assert not re.search(
        r"records hub identities by[^.]*`AGENTS\.md`", content
    ), "cluster-UID binding must cite docs/operations/usage.md and architecture.md, not AGENTS.md"
    assert "docs/operations/usage.md" in content

    sentence_match = re.search(
        r"The Python CLI already records hub identities by.*?(?=The release framework\s+already builds)",
        content,
        re.DOTALL,
    )
    assert sentence_match, (
        "lab-role-controller-spec.md must contain the cluster-UID attribution sentence "
        "immediately preceding the sentence about the release framework's environment "
        "fingerprints; if that neighbouring sentence moved, update this anchor"
    )
    sentence = sentence_match.group(0)

    assert "AGENTS.md" not in sentence, "the cluster-UID attribution sentence must not cite AGENTS.md"
    assert "docs/operations/usage.md" in sentence, (
        "the cluster-UID attribution sentence must cite docs/operations/usage.md, not merely "
        "mention it elsewhere in the file"
    )
    assert "docs/development/architecture.md" in sentence, (
        "the cluster-UID attribution sentence must cite docs/development/architecture.md, not "
        "merely mention it elsewhere in the file"
    )


OBSOLETE_CLI_PATTERNS = (
    (re.compile(r"acm_switchover\.py\s+switchover"), "the obsolete `switchover` subcommand"),
    (re.compile(r"(?<![-\w])passive-sync"), "the obsolete `passive-sync` method value"),
)

BARE_DOT_FORMATTER = re.compile(r"^\s*(?:\$\s*)?(?:black|isort)\b[^\n]*\s\.\s*$", re.MULTILINE)


def test_active_docs_avoid_obsolete_cli_shapes():
    """Contributor-facing docs must not show CLI shapes the parser rejects."""
    for doc in CONTRIBUTOR_DOCS:
        content = _read(doc)
        for pattern, label in OBSOLETE_CLI_PATTERNS:
            match = pattern.search(content)
            assert match is None, f"{doc} still documents {label}: {match.group(0)!r}"


def test_formatter_guidance_avoids_repo_wide_traversal():
    """Documented formatter commands must not target the repository root."""
    for doc in (CONTRIBUTING_DOC, TESTING_DOC):
        content = _read(doc)
        match = BARE_DOT_FORMATTER.search(content)
        assert match is None, f"{doc} documents repo-wide formatting that can walk .venv/: {match.group(0).strip()!r}"


_BASH_FENCE = re.compile(r"^```bash\n(.*?)^```", re.MULTILINE | re.DOTALL)
_SHELL_LOOP = re.compile(r"^[ \t]*for\s+\w+\s+in\s+.+?;\s*do\s*$(.*?)^[ \t]*done\s*$", re.MULTILINE | re.DOTALL)
# A real pipe, not the `||` operator.
_REAL_PIPE = re.compile(r"(?<!\|)\|(?!\|)")


def test_documented_verification_loops_aggregate_failures():
    """A documented loop over verification commands must not swallow an early failure.

    A bare ``for f in ...; do cmd "$f"; done`` exits with the status of the LAST iteration only.
    A failing playbook followed by a passing one therefore returns 0, and the documented gate
    reports success while a real defect sits in the log. CI does not make that mistake; the
    documentation must not either.

    This asserts the *meaning* rather than any particular prose or variable name. Each loop must
    either:

    * fail immediately inside the loop (``|| exit 1`` / ``|| return 1``), or
    * accumulate a status flag that is initialised before the loop and consulted after it in a
      branch that exits non-zero.

    Additionally, a loop whose command is piped (for example through ``tee`` into a log) must be
    preceded by ``set -o pipefail`` in the same block: without it, ``|| flag=1`` observes the
    exit status of the last stage of the pipeline instead of the command being verified, and the
    aggregation is decorative.

    The non-empty guard is deliberate. If no documented loop can be found at all, this test
    FAILS rather than passing vacuously — a guardrail that quietly inspects nothing is how the
    defect it guards against comes back.
    """
    content = _read(TESTING_DOC)

    inspected = 0
    for block in _BASH_FENCE.findall(content):
        for match in _SHELL_LOOP.finditer(block):
            inspected += 1
            head, body, tail = block[: match.start()], match.group(1), block[match.end() :]

            fails_inside = "|| exit 1" in body or "|| return 1" in body

            aggregated = False
            accumulator = re.search(r"\|\|\s*(\w+)=1\b", body)
            if accumulator:
                name = re.escape(accumulator.group(1))
                initialised = re.search(rf"^[ \t]*{name}=0\b", head, re.MULTILINE)
                acted_on = re.search(rf"\$\{{?{name}\b.*?\bexit\s+1\b", tail, re.DOTALL)
                aggregated = bool(initialised) and bool(acted_on)

            assert fails_inside or aggregated, (
                f"{TESTING_DOC} documents a loop that exits with only the last iteration's "
                f"status, so an early failure is silently swallowed:\n{match.group(0).strip()}\n"
                "Fail inside the loop, or accumulate a flag initialised before the loop and "
                "exit non-zero on it afterwards, as CI does."
            )

            if _REAL_PIPE.search(body):
                # Anchored to the start of a line so a prose comment *mentioning* pipefail
                # cannot satisfy the check — only an actual command can.
                assert re.search(r"^[ \t]*set -o pipefail\b", head, re.MULTILINE), (
                    f"{TESTING_DOC} documents a loop whose command is piped, without "
                    "`set -o pipefail` earlier in the same block. The failure check then "
                    "observes the last stage of the pipe (for example `tee`), which almost "
                    f"always succeeds, so the aggregation cannot fire:\n{match.group(0).strip()}"
                )

    assert inspected, (
        f"no shell loop was found in any ```bash block of {TESTING_DOC}. This guardrail exists "
        "to keep documented multi-command verification surfaces from swallowing early failures; "
        "if the loop was replaced, re-point this test at whatever replaced it rather than "
        "leaving it inspecting nothing."
    )
