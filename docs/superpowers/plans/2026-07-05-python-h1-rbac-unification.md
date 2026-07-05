# Python H1 RBAC Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Derive `VALIDATOR_CLUSTER_PERMISSIONS` from `OPERATOR_CLUSTER_PERMISSIONS` and deduplicate the primary/secondary hub blocks in `validate_rbac_permissions()`, preserving behavior byte-for-byte (Thermos `H1`).

**Architecture:** Per approved design `docs/superpowers/specs/2026-07-05-python-h1-rbac-unification-design.md`: (1) module-level `_derive_read_only_permissions()` strips `MUTATING_VERBS` and verifies removals against explicit exception data `VALIDATOR_CLUSTER_VERB_EXCEPTIONS` (managedclusters `patch` only); (2) module-level `_validate_hub()` helper + loop over explicit per-hub spec tuples, asymmetries encoded as data. No Ansible files touched.

**Tech Stack:** Python 3, pytest, black/isort line-length 120.

## Global Constraints

- Base branch `ansible`; worktree `.worktrees/thermos-h1-python-rbac-unification`.
- Do NOT modify: `roles/preflight/tasks/validate_rbac.yml`, any Ansible RBAC validation logic, `docs/ACM_SWITCHOVER_RUNBOOK.md`, `.claude/skills/**/*.skill.md`.
- Do NOT remove/weaken/skip parity tests (`tests/test_rbac_collection_parity.py`, `tests/test_rbac_integration.py`, release RBAC certification tests).
- Exception message strings must stay byte-identical (primary without error count, secondary with `({N} error(s))`).
- Format touched files: `black --line-length 120`, `isort --profile black --line-length 120`.

---

### Task 1: Derive validator cluster table (Facet 1)

**Files:**
- Modify: `lib/rbac_validator.py` (lines 36-40 area for module constants; lines 79-100 replace literal `VALIDATOR_CLUSTER_PERMISSIONS`; line 314 `_is_write_verb`)
- Test: `tests/test_rbac_validator.py`

**Interfaces:**
- Produces: module-level `MUTATING_VERBS: FrozenSet[str]`, `VALIDATOR_CLUSTER_VERB_EXCEPTIONS: Dict[Tuple[str, str], FrozenSet[str]]`, `_derive_read_only_permissions(operator_permissions, expected_removals) -> List[Tuple[str, str, List[str]]]`. Class attr `RBACValidator.VALIDATOR_CLUSTER_PERMISSIONS` keeps identical value/shape.

- [ ] **Step 1: Write failing tests** — add to `tests/test_rbac_validator.py` (new class after `TestRBACValidator`); extend the `lib.constants` import block with `MANAGED_CLUSTER_API_GROUP, MANAGED_CLUSTER_PLURAL` and add `from lib import rbac_validator`:

```python
class TestValidatorClusterTableDerivation:
    """H1: VALIDATOR_CLUSTER_PERMISSIONS is derived from OPERATOR_CLUSTER_PERMISSIONS."""

    EXPECTED_VALIDATOR_TABLE = [
        ("", "namespaces", ["get", "list"]),
        ("", "nodes", ["get", "list"]),
        ("config.openshift.io", "clusteroperators", ["get", "list"]),
        ("config.openshift.io", "clusterversions", ["get", "list"]),
        ("cluster.open-cluster-management.io", "managedclusters", ["get", "list"]),
        ("hive.openshift.io", "clusterdeployments", ["get", "list"]),
        ("operator.open-cluster-management.io", "multiclusterhubs", ["get", "list"]),
        ("observability.open-cluster-management.io", "multiclusterobservabilities", ["get", "list"]),
    ]

    def test_validator_table_matches_pre_derivation_literal(self):
        """Regression pin: derived table equals the exact table shipped before H1."""
        assert RBACValidator.VALIDATOR_CLUSTER_PERMISSIONS == self.EXPECTED_VALIDATOR_TABLE

    def test_validator_table_is_derived_from_operator_table(self):
        derived = rbac_validator._derive_read_only_permissions(
            RBACValidator.OPERATOR_CLUSTER_PERMISSIONS,
            rbac_validator.VALIDATOR_CLUSTER_VERB_EXCEPTIONS,
        )
        assert derived == RBACValidator.VALIDATOR_CLUSTER_PERMISSIONS

    def test_managedclusters_patch_exception_is_explicit_data(self):
        assert rbac_validator.VALIDATOR_CLUSTER_VERB_EXCEPTIONS == {
            (MANAGED_CLUSTER_API_GROUP, MANAGED_CLUSTER_PLURAL): frozenset({"patch"})
        }

    def test_managedclusters_patch_stripped_for_validator(self):
        operator_rule = next(
            r for r in RBACValidator.OPERATOR_CLUSTER_PERMISSIONS
            if (r[0], r[1]) == (MANAGED_CLUSTER_API_GROUP, MANAGED_CLUSTER_PLURAL)
        )
        validator_rule = next(
            r for r in RBACValidator.VALIDATOR_CLUSTER_PERMISSIONS
            if (r[0], r[1]) == (MANAGED_CLUSTER_API_GROUP, MANAGED_CLUSTER_PLURAL)
        )
        assert "patch" in operator_rule[2]
        assert "patch" not in validator_rule[2]
        assert [v for v in operator_rule[2] if v != "patch"] == validator_rule[2]

    def test_unexpected_mutating_verb_fails_derivation(self):
        perms = [("group.example.io", "widgets", ["get", "delete"])]
        with pytest.raises(ValueError, match="drifted"):
            rbac_validator._derive_read_only_permissions(perms, {})

    def test_stale_exception_entry_fails_derivation(self):
        perms = [("group.example.io", "widgets", ["get", "list"])]
        expected = {("group.example.io", "widgets"): frozenset({"delete"})}
        with pytest.raises(ValueError, match="drifted"):
            rbac_validator._derive_read_only_permissions(perms, expected)

    def test_mutating_verbs_match_write_verb_helper(self, mock_client):
        mock_client.context = "test-context"
        validator = RBACValidator(mock_client)
        for verb in ("create", "update", "patch", "delete"):
            assert validator._is_write_verb(verb)
            assert verb in rbac_validator.MUTATING_VERBS
        for verb in ("get", "list", "watch"):
            assert not validator._is_write_verb(verb)
            assert verb not in rbac_validator.MUTATING_VERBS
```

- [ ] **Step 2: Run** `python -m pytest tests/test_rbac_validator.py::TestValidatorClusterTableDerivation -q` — expect FAIL (`AttributeError: ... _derive_read_only_permissions`).

- [ ] **Step 3: Implement** in `lib/rbac_validator.py`. Add after `VALID_ARGOCD_MODES` (module level):

```python
# Verbs that mutate cluster state. Single source for validator-table derivation
# and RBACValidator._is_write_verb().
MUTATING_VERBS = frozenset({"create", "update", "patch", "delete"})

# The one intentional operator-vs-validator cluster-table difference: the
# operator activates managed clusters during switchover via "patch"; the
# read-only validator role must never receive it. Any other mutating verb added
# to OPERATOR_CLUSTER_PERMISSIONS must be recorded here deliberately.
VALIDATOR_CLUSTER_VERB_EXCEPTIONS: Dict[Tuple[str, str], FrozenSet[str]] = {
    (MANAGED_CLUSTER_API_GROUP, MANAGED_CLUSTER_PLURAL): frozenset({"patch"}),
}


def _derive_read_only_permissions(
    operator_permissions: List[Tuple[str, str, List[str]]],
    expected_removals: Dict[Tuple[str, str], FrozenSet[str]],
) -> List[Tuple[str, str, List[str]]]:
    """Derive a read-only permission table by stripping mutating verbs.

    Fails fast (at import time for the class tables) when the stripped verbs do
    not exactly match the documented exception data, so validator-table changes
    are always a conscious decision rather than silent drift.
    """
    derived: List[Tuple[str, str, List[str]]] = []
    removed: Dict[Tuple[str, str], FrozenSet[str]] = {}
    for api_group, resource, verbs in operator_permissions:
        read_verbs = [verb for verb in verbs if verb not in MUTATING_VERBS]
        stripped = frozenset(verbs) - frozenset(read_verbs)
        if stripped:
            removed[(api_group, resource)] = stripped
        if read_verbs:
            derived.append((api_group, resource, read_verbs))
    if removed != expected_removals:
        raise ValueError(
            "Validator cluster permission derivation drifted from the documented exceptions: "
            f"removed {sorted(removed)!r}, expected {sorted(expected_removals)!r}. "
            "Update VALIDATOR_CLUSTER_VERB_EXCEPTIONS deliberately when changing "
            "mutating verbs in OPERATOR_CLUSTER_PERMISSIONS."
        )
    return derived
```

Extend imports: `from typing import ... FrozenSet ...`; add `MANAGED_CLUSTER_API_GROUP, MANAGED_CLUSTER_PLURAL` to the `lib.constants` import block. Replace the `VALIDATOR_CLUSTER_PERMISSIONS` literal (lines 79-97) with:

```python
    # Required cluster-scoped permissions for VALIDATOR role (read-only),
    # derived from the operator table by stripping mutating verbs. The only
    # verb stripped today is managedclusters "patch"
    # (see VALIDATOR_CLUSTER_VERB_EXCEPTIONS).
    VALIDATOR_CLUSTER_PERMISSIONS = _derive_read_only_permissions(
        OPERATOR_CLUSTER_PERMISSIONS, VALIDATOR_CLUSTER_VERB_EXCEPTIONS
    )
```

Change `_is_write_verb` body to `return verb in MUTATING_VERBS`.

- [ ] **Step 4: Run** `python -m pytest tests/test_rbac_validator.py tests/test_rbac_collection_parity.py tests/test_rbac_integration.py -q` — expect PASS.

- [ ] **Step 5: Format + commit**

```bash
black --line-length 120 lib/rbac_validator.py tests/test_rbac_validator.py
isort --profile black --line-length 120 lib/rbac_validator.py tests/test_rbac_validator.py
git add lib/rbac_validator.py tests/test_rbac_validator.py
git commit -m "refactor: derive validator RBAC cluster table from operator table (H1)"
```

### Task 2: `_validate_hub()` + hub-role loop (Facet 2)

**Files:**
- Modify: `lib/rbac_validator.py:946-1004` (`validate_rbac_permissions` body)
- Test: `tests/test_rbac_validator.py`

**Interfaces:**
- Consumes: `lib.constants.HUB_ROLE_PRIMARY` / `HUB_ROLE_SECONDARY` (existing, values `"primary"`/`"secondary"`).
- Produces: module-level `_validate_hub(hub_role, client, *, include_decommission, include_old_hub_finalization, skip_observability, argocd_mode, argocd_install_type, include_error_count) -> None`.

- [ ] **Step 1: Write failing tests** — add class after `TestValidateRBACPermissions`:

```python
class TestValidateHubLoop:
    """H1: primary/secondary validation routes through one hub-parameterized helper."""

    @pytest.fixture
    def mock_primary_client(self):
        client = MagicMock()
        client.context = "primary-hub"
        return client

    @pytest.fixture
    def mock_secondary_client(self):
        client = MagicMock()
        client.context = "secondary-hub"
        return client

    def test_both_hubs_route_through_validate_hub_with_explicit_asymmetries(
        self, mock_primary_client, mock_secondary_client, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            rbac_validator,
            "_validate_hub",
            lambda hub_role, client, **kwargs: calls.append((hub_role, client, kwargs)),
        )
        validate_rbac_permissions(
            mock_primary_client,
            mock_secondary_client,
            include_decommission=True,
            include_old_hub_finalization=True,
            skip_observability=True,
            argocd_mode="check",
            argocd_install_type="operator",
            secondary_argocd_install_type="vanilla",
        )
        assert [(hub_role, client) for hub_role, client, _ in calls] == [
            ("primary", mock_primary_client),
            ("secondary", mock_secondary_client),
        ]
        primary_kwargs = calls[0][2]
        secondary_kwargs = calls[1][2]
        # Primary-only asymmetries: decommission/old-hub-finalization pass through.
        assert primary_kwargs["include_decommission"] is True
        assert primary_kwargs["include_old_hub_finalization"] is True
        assert primary_kwargs["argocd_install_type"] == "operator"
        assert primary_kwargs["include_error_count"] is False
        # Secondary-only asymmetries: flags forced off, install-type override, error count.
        assert secondary_kwargs["include_decommission"] is False
        assert secondary_kwargs["include_old_hub_finalization"] is False
        assert secondary_kwargs["argocd_install_type"] == "vanilla"
        assert secondary_kwargs["include_error_count"] is True
        # Shared flags reach both hubs unchanged.
        for kwargs in (primary_kwargs, secondary_kwargs):
            assert kwargs["skip_observability"] is True
            assert kwargs["argocd_mode"] == "check"

    def test_secondary_install_type_falls_back_to_primary_install_type(
        self, mock_secondary_client, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            rbac_validator,
            "_validate_hub",
            lambda hub_role, client, **kwargs: calls.append((hub_role, kwargs)),
        )
        validate_rbac_permissions(None, mock_secondary_client, argocd_install_type="operator")
        assert calls[0][0] == "secondary"
        assert calls[0][1]["argocd_install_type"] == "operator"

    def test_primary_absent_logs_skip_and_still_validates_secondary(
        self, mock_secondary_client, monkeypatch, caplog
    ):
        calls = []
        monkeypatch.setattr(
            rbac_validator,
            "_validate_hub",
            lambda hub_role, client, **kwargs: calls.append(hub_role),
        )
        with caplog.at_level(logging.INFO, logger="acm_switchover"):
            validate_rbac_permissions(None, mock_secondary_client)
        assert calls == ["secondary"]
        assert "Primary hub not available; skipping primary RBAC validation" in caplog.text

    @patch("lib.rbac_validator.RBACValidator")
    def test_secondary_failure_message_includes_error_count(
        self, mock_validator_class, mock_secondary_client
    ):
        mock_validator = MagicMock()
        mock_validator.validate_all_permissions.return_value = (
            False,
            {"cluster": ["err one", "err two"], "namespaces": ["err three"]},
        )
        mock_validator.generate_permission_report.return_value = "Error report"
        mock_validator_class.return_value = mock_validator
        with pytest.raises(ValidationError) as exc_info:
            validate_rbac_permissions(None, mock_secondary_client)
        assert (
            str(exc_info.value)
            == "RBAC permission validation failed on secondary hub (3 error(s)). See report above for details."
        )

    @patch("lib.rbac_validator.RBACValidator")
    def test_primary_failure_message_has_no_error_count(self, mock_validator_class, mock_primary_client):
        mock_validator = MagicMock()
        mock_validator.validate_all_permissions.return_value = (False, {"cluster": ["err one"]})
        mock_validator.generate_permission_report.return_value = "Error report"
        mock_validator_class.return_value = mock_validator
        with pytest.raises(ValidationError) as exc_info:
            validate_rbac_permissions(mock_primary_client)
        assert (
            str(exc_info.value)
            == "RBAC permission validation failed on primary hub. See report above for details."
        )
```

Add `import logging` to the test-file imports if absent.

- [ ] **Step 2: Run** `python -m pytest tests/test_rbac_validator.py::TestValidateHubLoop -q` — expect FAIL (`AttributeError: ... _validate_hub`).

- [ ] **Step 3: Implement.** Extend `lib.constants` import with `HUB_ROLE_PRIMARY, HUB_ROLE_SECONDARY`. Add module-level helper directly above `validate_rbac_permissions`:

```python
def _validate_hub(
    hub_role: str,
    client: KubeClient,
    *,
    include_decommission: bool,
    include_old_hub_finalization: bool,
    skip_observability: bool,
    argocd_mode: str,
    argocd_install_type: str,
    include_error_count: bool,
) -> None:
    """Validate RBAC permissions on one hub, preserving per-hub message shapes.

    include_error_count encodes the secondary-only failure-message behavior;
    every other primary/secondary asymmetry is passed in explicitly by the
    caller's hub table.
    """
    logger.info("Validating RBAC permissions on %s hub...", hub_role)
    validator = RBACValidator(client)
    try:
        valid, errors = validator.validate_all_permissions(
            include_decommission=include_decommission,
            include_old_hub_finalization=include_old_hub_finalization,
            skip_observability=skip_observability,
            argocd_mode=argocd_mode,
            argocd_install_type=argocd_install_type,
        )
    except ValidationError as exc:
        raise ValidationError(f"RBAC permission validation could not be completed on {hub_role} hub: {exc}") from exc

    if valid:
        return

    report = validator.generate_permission_report(
        include_decommission=include_decommission,
        include_old_hub_finalization=include_old_hub_finalization,
        skip_observability=skip_observability,
        argocd_mode=argocd_mode,
        argocd_install_type=argocd_install_type,
    )
    logger.error("\n%s", report)
    if include_error_count:
        error_count = sum(len(errs) for errs in errors.values())
        raise ValidationError(
            f"RBAC permission validation failed on {hub_role} hub ({error_count} error(s)). "
            "See report above for details."
        )
    raise ValidationError(f"RBAC permission validation failed on {hub_role} hub. See report above for details.")
```

Replace `validate_rbac_permissions` body from `# Validate primary hub (when available)` through the secondary block (keep signature, docstring, both precondition `ValueError`s, `_validate_argocd_mode`, and the final success log):

```python
    # Per-hub validation table. Asymmetries are explicit data:
    # - decommission/old-hub-finalization checks apply to the primary hub only;
    # - the secondary hub honors secondary_argocd_install_type when provided;
    # - only the secondary failure message includes the error count.
    hub_validations = (
        (
            HUB_ROLE_PRIMARY,
            primary_client,
            include_decommission,
            include_old_hub_finalization,
            argocd_install_type,
            False,
        ),
        (
            HUB_ROLE_SECONDARY,
            secondary_client,
            False,
            False,
            secondary_argocd_install_type or argocd_install_type,
            True,
        ),
    )

    for (
        hub_role,
        client,
        hub_include_decommission,
        hub_include_old_hub_finalization,
        hub_argocd_install_type,
        include_error_count,
    ) in hub_validations:
        if hub_role == HUB_ROLE_PRIMARY and client is None:
            logger.info("Primary hub not available; skipping primary RBAC validation")
            continue
        if hub_role == HUB_ROLE_SECONDARY and not client:
            continue
        _validate_hub(
            hub_role,
            client,
            include_decommission=hub_include_decommission,
            include_old_hub_finalization=hub_include_old_hub_finalization,
            skip_observability=skip_observability,
            argocd_mode=argocd_mode,
            argocd_install_type=hub_argocd_install_type,
            include_error_count=include_error_count,
        )

    logger.info("✓ RBAC permission validation completed successfully")
```

- [ ] **Step 4: Run** `python -m pytest tests/test_rbac_validator.py tests/test_check_rbac.py tests/test_rbac_collection_parity.py -q` — expect PASS (existing `TestValidateRBACPermissions` message/kwarg assertions must pass unmodified).

- [ ] **Step 5: Format + commit** (same black/isort commands as Task 1)

```bash
git add lib/rbac_validator.py tests/test_rbac_validator.py
git commit -m "refactor: dedupe primary/secondary hub RBAC validation via _validate_hub loop (H1)"
```

### Task 3: Docs, tracker, full verification

**Files:**
- Modify: `thermos-resolution-plan.md` (H1 status + PR-row table + Last Updated), `CHANGELOG.md` (`[Unreleased]` → `### Changed`)

- [ ] **Step 1:** Add tracker row after row 38 (keeping PR 39 `planned`): status `ready_for_review`, branch/worktree, note that PR 39 remains the Ansible mirror follow-up and must mirror the hub-role loop shape; update `Last Updated` to 2026-07-05. Update the "Follow-up order after PR 32" H1 bullet with the in-flight reference.
- [ ] **Step 2:** CHANGELOG `[Unreleased]` `### Changed` entry: validator cluster RBAC table now derived from the operator table (managedclusters `patch` exception explicit); primary/secondary hub RBAC validation deduplicated behind `_validate_hub` (no behavior change).
- [ ] **Step 3: Full verification**

```bash
git diff --check
python -m pytest tests/test_rbac_validator.py tests/test_check_rbac.py tests/test_rbac_collection_parity.py tests/test_rbac_integration.py tests/release/checks/test_rbac_certification.py -q
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ tests/ -q
./run_tests.sh
```

- [ ] **Step 4: Commit docs**

```bash
git add thermos-resolution-plan.md CHANGELOG.md
git commit -m "docs: record Python H1 RBAC unification in tracker and changelog"
```

### Task 4: Review gate + PR

- [ ] **Step 1:** Run `code-review` skill on branch changes; fix critical/warning findings; re-run if changed.
- [ ] **Step 2:** Push branch; open PR base `ansible`, title `Thermos H1: unify Python RBAC validator tables and hub validation`, body per task requirements (tracker ref, PR39 relationship, files, spec/plan paths, behavior-preservation summary, parity impact, verification, protected-file confirmation, PR39 follow-up).
- [ ] **Step 3:** Update tracker row with PR URL; commit and push.
