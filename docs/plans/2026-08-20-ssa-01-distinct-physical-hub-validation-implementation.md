# SSA-01 Distinct Physical-Hub Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce fail-closed distinct physical-hub validation in the Python CLI and Ansible Collection while preserving existing per-role stored-versus-current resume identity binding.

**Architecture:** The Python CLI keeps orchestration in `_bind_runtime_hub_identities`: normal-flow input validation rejects equal context names, role-scoped client construction and identity reads suppress raw diagnostics, a pure validator compares the two live `kube-system` Namespace UIDs, and `StateManager.ensure_hub_identities` retains per-role resume binding. The Collection adds a literal identity-barrier path to the existing `checkpoint_phase` action so live module results and UIDs remain action-local through distinctness and checkpoint validation, then splits the switchover playbook structurally so mutation-capable recovery exists only after that trusted barrier.

**Tech Stack:** Python, Kubernetes Python client, Tenacity, Ansible Collection action plugins, `kubernetes.core.k8s_info`, YAML, pytest, and the shipped fake Kubernetes integration server.

**Spec:** `docs/plans/2026-08-20-ssa-01-distinct-physical-hub-validation-design.md`

## Global Constraints

- A normal two-hub flow must establish that primary and secondary resolve to distinct physical Kubernetes clusters before any mutation-capable phase.
- Physical identity is the trimmed, non-empty string UID of the live `kube-system` Namespace.
- Equal primary and secondary context names are rejected early as a defense in depth; different names that resolve to one UID are also rejected.
- Execute-mode identity evidence is fresh. Collection execute plus native `ansible-playbook --check` still performs fresh UID GETs.
- Stale, public, caller-supplied, registered, `set_fact`, cached, or underscore-prefixed Collection variables never satisfy physical-identity safety decisions.
- `acm_switchover_test_overrides.non_live_hub_identities` is eligible only in Collection `validate` and `dry_run`; execute ignores it, including execute plus native check mode.
- The Python CLI has no production pre-seeded identity contract and reads identity freshly in validate-only, dry-run, and execute.
- Collection UID evidence remains action-local until per-role evidence validation, cross-role distinctness, and current checkpoint operation-identity handling complete.
- Caller-injected `acm_switchover_hubs.<role>.cluster_uid` never enters the initial barrier's expected checkpoint identity. The barrier builds allowlisted context-only hubs and supplies the same trusted local UIDs through `hub_identities`.
- Existing per-role stored-versus-current resume validation remains additive and is not replaced by the cross-role predicate.
- Existing `reset` and `reset_from` behavior is unchanged. `R3-06`, not SSA-01, owns correction of `reset_from`; SSA-01 must not claim that later `reset_from` preserves the old identity.
- `_checkpoint_enter.skipped_phase` and `_checkpoint_enter.facts` remain compatibility and operational control data. They are never authoritative physical-identity evidence.
- A Collection pre-barrier failure cannot enter primary preparation, Argo CD recovery, checkpoint reset recovery, or another mutation-capable path. A post-barrier failure retains existing recovery behavior.
- Restore-only remains secondary-only and does not run the two-hub predicate or contact primary. Standalone/single-hub decommission behavior remains unchanged; SSA-02 owns decommission target hardening.
- No checkpoint schema change, new Kubernetes API group/resource/verb/namespace, impersonation, token mechanism, credential type, or RBAC permission is permitted.
- Python and Collection remain independent production implementations and do not cross-import runtime code. Production code must not import from `tests/release/lab_controller/`.
- `docs/ACM_SWITCHOVER_RUNBOOK.md` and `.claude/skills/**` remain unchanged. No live cluster access or release certification is part of this slice.

## Bound Base and Preconditions

- Repository: `tomazb/rh-acm-switchover`
- Governing issue: GitHub #267, `SSA-01 / SSA-A2 + SSA-P2`
- Base: `origin/ansible@7a29974c2e914af30b1d9a02ee194295bdfe0722`
- Design authority: `docs/plans/2026-08-20-ssa-01-distinct-physical-hub-validation-design.md`
- Supported Collection endpoints: `ansible-core 2.16.* / Python 3.11` and `ansible-core 2.21.* / Python 3.12`.
- A later builder must re-run the repository start gate before the first implementation edit. If `origin/ansible`, issue authorization, or the approved spec relationship has changed, stop and revalidate rather than applying this plan mechanically.

## Definitive File Map

### Python production

| File | Current responsibility | SSA-01 responsibility after implementation |
| --- | --- | --- |
| `lib/validation.py` | CLI input validation and reusable input predicates | Add normal-flow equal-context validation and pure `validate_distinct_hub_identities` shape/type/non-empty/equality validation. |
| `lib/kube_client.py` | Kubernetes configuration, API clients, generic API retries, and `get_cluster_identity` | Add default-preserving constructor error-log control and an identity-specific, non-logging `kube-system` Namespace read using `retry_api_call_advisory`. |
| `lib/runtime_bootstrap.py` | State/client construction and hub-identity collection | Add `HubIdentityVerificationError`, role-specific safe messages, role-scoped client-construction translation, and role-scoped identity-read translation. |
| `acm_switchover.py` | CLI runtime wiring and operation dispatch | Enable sanitized construction on identity-bound paths, convert safe identity failures to existing outcomes, and order fresh collection then cross-role validation then `StateManager.ensure_hub_identities`. |

`lib/utils.py`, `lib/cli_outcomes.py`, `lib/constants.py`, state schema, and outcome schema remain unchanged production owners. Their tests are regression gates only.

### Collection production

| File | Current responsibility | SSA-01 responsibility after implementation |
| --- | --- | --- |
| `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_input_validate.py` | Structured Collection input validation | Add normal two-hub equal-context UX failure with the approved message and restore-only exclusion. |
| `ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py` | Checkpoint load/normalize/validate/transition/persist | Add literal `identity_barrier` ownership, action-local discovery and validation, distinctness, trusted expected-identity construction, check-mode authority, checkpoint-disabled handling, and ordinary later-transition carry-forward of established identity. Preserve explicit reset/reset-from behavior. |
| `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/main.yml` | Entire preflight task sequence | Become a two-line composition owner: `identity_barrier.yml` followed by `post_identity.yml`, preserving standalone preflight. |
| `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/identity_barrier.yml` | New | Initialize validation state, run input validation, invoke the unconditional literal trusted action, register `_checkpoint_enter`, and optionally publish a non-authoritative identity summary. |
| `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/post_identity.yml` | New | Own skipped-preflight operational fact restoration, remaining checks, preflight reporting, critical failure, and pass transition after the trusted barrier. |
| `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/discover_hub_identities.yml` | Caller-shadowable register/`set_fact` identity discovery | Delete after its reporting-compatible output is produced only from the completed action barrier. Do not retain a second safety path. |
| `ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml` | Full phase block, recovery rescue, and report always | Put the identity barrier outside the inner recovery block; keep post-identity preflight, mutation phases, and current recovery inside the inner block; retain report publication in the outer `always`. |

The following remain unchanged production files: `plugins/module_utils/checkpoint.py`, `roles/preflight/tasks/write_report.yml`, `playbooks/preflight.yml`, `playbooks/restore_only.yml`, decommission playbooks/roles, RBAC validators, manifests, Helm, and protected files.

### Test and harness files

Planned Python test edits:

- `tests/test_validation.py`
- `tests/test_kube_client.py`
- `tests/test_runtime_bootstrap.py`
- `tests/test_main.py`
- `tests/test_cli_outcomes.py`
- `tests/test_utils.py`
- `tests/test_resume_safety_guards.py`
- `tests/test_main_argocd_resume.py`
- `tests/test_decommission.py`
- `tests/fixtures/validation_parity_cases.yml`
- `tests/test_validation_parity.py`
- `tests/test_documentation_guardrails.py`

Planned Collection unit/static test edits:

- `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_input_validate.py`
- `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py`
- `ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_checkpoint_validation_order.py`
- `ansible_collections/tomazb/acm_switchover/tests/unit/test_checkpoint_vocabulary_guardrail.py`
- `ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_parity.py`
- `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py`
- `ansible_collections/tomazb/acm_switchover/tests/unit/test_restore_only_recovery_contracts.py`
- `ansible_collections/tomazb/acm_switchover/tests/unit/test_release_source_schema.py`
- `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_report_aggregation.py`
- `ansible_collections/tomazb/acm_switchover/tests/unit/test_validation_parity_fixture.py`

Planned Collection shipped-flow/harness edits:

- `ansible_collections/tomazb/acm_switchover/tests/conftest.py`
- `ansible_collections/tomazb/acm_switchover/tests/integration/conftest.py`
- `ansible_collections/tomazb/acm_switchover/tests/integration/argocd_fake_api.py`
- `ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py`
- `ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py`
- `ansible_collections/tomazb/acm_switchover/tests/integration/test_restore_only_role.py`
- `ansible_collections/tomazb/acm_switchover/tests/integration/test_distinct_hub_identity_barrier.py` (new)
- `ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py`
- `ansible_collections/tomazb/acm_switchover/tests/scenario/fixtures/checkpoint/interrupted_after_activation.yml`
- `ansible_collections/tomazb/acm_switchover/tests/scenario/fixtures/checkpoint/preflight_completed_without_preflight_facts.yml`

Fixture edits must migrate dry-run/validate safety evidence from ordinary `acm_switchover_hub_identities` seeding to the explicit `acm_switchover_test_overrides.non_live_hub_identities` contract. Execute fixtures that reach the barrier must use the fake API; no execute test may be made to pass with an override.

All sanitization tests use the same deliberately recognizable values so a negative assertion cannot accidentally pass against an unrelated message:

| Evidence class | Test sentinel |
| --- | --- |
| kubeconfig path | `/tmp/ssa01-secret-kubeconfig-KP71` |
| token | `ssa01-secret-token-TK72` |
| Kubernetes API body | `ssa01-secret-api-body-BD73` |
| raw exception | `ssa01-secret-raw-exception-EX74` |
| Namespace UID | `ssa01-secret-uid-UID75` |
| context | `ssa01-secret-context-CTX76` |
| credential | `ssa01-secret-credential-CR77` |

Tests assert that none of these strings appears in logs, stdout, stderr, final refusal text, Ansible callback-visible failure output, or generated reports on the new refusal paths.

Planned new or extended pytest identifiers are fixed here so later tasks do not invent a second naming scheme:

| Task | Test identifiers |
| --- | --- |
| 1 | `test_normal_two_hub_rejects_same_context`, `test_same_context_guard_excludes_single_hub_modes`, `test_validate_distinct_hub_identities_rejects_unverifiable_role`, `test_validate_distinct_hub_identities_rejects_same_uid`, `test_validate_distinct_hub_identities_accepts_distinct_uids` |
| 2 | `test_kube_client_can_suppress_raw_config_exception_log`, `test_kube_client_default_config_exception_logging_is_unchanged`, `test_initialize_clients_translates_role_config_failure`, `test_prepare_runtime_identity_config_failure_does_not_leak_sentinels` |
| 3 | `test_cluster_identity_retry_failure_does_not_log_sentinels`, `test_collect_hub_identities_translates_role_read_failure`, `test_bind_runtime_hub_identities_rejects_same_uid_before_state`, `test_identity_refusal_never_dispatches_operation`, `test_identity_refusal_does_not_persist_or_complete_phase` |
| 4 | `test_normal_two_hub_same_context_fails`, `test_restore_only_does_not_apply_distinct_context_rule`, `test_different_hub_contexts_pass_distinct_context_rule` |
| 5 | `test_identity_barrier_rejects_invalid_phase_or_status`, `test_identity_barrier_uses_play_context_check_mode`, `test_identity_barrier_reads_live_uids_in_execute`, `test_identity_barrier_rejects_same_live_uid`, `test_identity_barrier_rejects_malformed_role_evidence`, `test_identity_barrier_ignores_execute_override_and_public_preseed`, `test_identity_barrier_uses_override_only_in_non_live_modes`, `test_identity_barrier_ignores_caller_hub_cluster_uid`, `test_identity_barrier_rejects_checkpoint_drift_from_trusted_uid`, `test_identity_barrier_runs_with_checkpoint_disabled`, `test_identity_barrier_preserves_checkpoint_enter_contract`, `test_explicit_reset_from_replacement_semantics_are_unchanged` |
| 6 | `test_preflight_composes_identity_then_post_identity`, `test_identity_barrier_call_is_literal_unconditional_and_registered`, `test_checkpoint_operational_facts_are_not_identity_evidence`, `test_obsolete_registered_identity_discovery_is_removed` |
| 7 | `test_identity_barrier_is_outside_mutation_recovery`, `test_post_barrier_phases_are_inside_mutation_recovery`, plus the existing `test_switchover_rescue_*` tests updated for the nested block |
| 8 | `test_restore_only_identity_barrier_reads_secondary_only`, `test_restore_only_keeps_checkpoint_enter_operational_facts`, `test_skipped_preflight_revalidates_identity_before_rehydration`, `test_reset_from_regression_remains_owned_by_r3_06` |
| 9 | `test_same_live_cluster_rejects_spoofed_distinct_extra_vars`, `test_unavailable_live_uid_rejects_spoofed_identity`, `test_checkpoint_drift_rejects_spoofed_stored_identity`, `test_pre_barrier_failure_ignores_spoofed_recovery_values`, `test_post_barrier_failure_retains_recovery`, `test_execute_check_mode_uses_fresh_uids_without_mutation` |
| 10 | Existing parameterized `test_python_validation_matches_shared_parity_fixture` and `test_collection_validation_matches_shared_parity_fixture`, plus `test_distinct_hub_runtime_owners_do_not_cross_import` |
| 11 | Existing documentation/CI guardrails plus `test_ssa_01_documentation_contract_is_complete` |

### Later documentation

Task 11 updates only behavior-bearing, non-protected authorities:

- `thermos-resolution-plan.md`
- `CHANGELOG.md` under `Unreleased`
- `README.md`
- `ansible_collections/tomazb/acm_switchover/README.md`
- `docs/development/architecture.md` and its affected Mermaid interaction flow
- `ansible_collections/tomazb/acm_switchover/docs/architecture.md`
- `docs/operations/usage.md`
- `docs/reference/validation-rules.md`
- `docs/ansible-collection/parity-matrix.md` with capability status unchanged
- `docs/ansible-collection/behavior-map.md`
- `ansible_collections/tomazb/acm_switchover/docs/coexistence.md`
- `ansible_collections/tomazb/acm_switchover/docs/variable-reference.md`
- `ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md`
- `docs/ansible-collection/scenario-catalog.md`
- `docs/ansible-collection/test-migration-catalog.md`

`docs/development/testing.md` remains unchanged unless implementation introduces a genuinely new named gate; this plan uses existing surfaces. Protected documentation is verification-only and receives no edit.

---

### Task 1: Shared/static decision contract and Python same-context validation

**Files:**

- Modify: `lib/validation.py`
- Modify: `tests/test_validation.py`
- Modify: `tests/fixtures/validation_parity_cases.yml`
- Modify: `tests/test_validation_parity.py`
- Modify later in Task 4/10: `ansible_collections/tomazb/acm_switchover/tests/unit/test_validation_parity_fixture.py`

**Interfaces:**

- Add `validate_distinct_hub_identities(hub_identities: Mapping[str, object]) -> None` to `lib.validation`.
- Keep failures as `ValidationError`; callers translate them to their existing outcome layer.
- Exact messages:

```text
Primary and secondary Kubernetes context names must differ for a normal two-hub switchover.
Primary and secondary hubs resolve to the same physical Kubernetes cluster. Refusing the normal two-hub switchover.
Unable to verify the primary hub physical identity from the live kube-system Namespace UID. Refusing the normal two-hub switchover.
Unable to verify the secondary hub physical identity from the live kube-system Namespace UID. Refusing the normal two-hub switchover.
```

- The pure validator requires `primary` and `secondary` mappings, a string `cluster_uid` for each role, and non-empty values after trimming. It compares the two trimmed strings exactly and returns `None` only when they differ.

- [ ] **Step 1: Add focused failing Python tests.** In `tests/test_validation.py`, add named cases for a normal flow with equal contexts; restore-only, setup, decommission, and standalone Argo CD resume exclusions; malformed outer/role mappings; missing, non-string, empty, and whitespace-only UIDs by role; equal trimmed UID refusal; distinct UID success; and proof that `force`/reset-oriented arguments do not bypass input or physical distinctness.

- [ ] **Step 2: Extend the existing parity fixture, not a new framework.** Add `hub_contexts` and `hub_identity` cases to `tests/fixtures/validation_parity_cases.yml`; update `tests/test_validation_parity.py::_python_args` and its dispatcher so the Python side consumes the fixture's actual hub contexts/identity evidence and pins every approved message.

- [ ] **Step 3: Run the focused tests and record the expected red result.** The equal-context and identity cases must fail because neither the static guard nor helper exists yet.

```bash
python -m pytest tests/test_validation.py tests/test_validation_parity.py -q
```

- [ ] **Step 4: Implement the minimal validation changes.** Add the static comparison inside `InputValidator.validate_all_cli_args` only when both roles are part of a normal two-hub invocation. Add the pure helper at module scope using `collections.abc.Mapping`; do not add Kubernetes or state ownership to this module.

- [ ] **Step 5: Re-run the focused tests, then adjacent validation regressions.** Expect all selected tests to pass.

```bash
python -m pytest tests/test_validation.py tests/test_validation_parity.py -q
python -m pytest tests/test_decommission.py tests/test_main_argocd_resume.py -q
```

- [ ] **Step 6: Commit during later implementation.** Commit only the coherent Python input/pure-decision contract after green tests.

```bash
git add lib/validation.py tests/test_validation.py tests/fixtures/validation_parity_cases.yml tests/test_validation_parity.py
git commit -m "feat: validate distinct switchover hub identities"
```

### Task 2: Python sanitized client establishment

**Files:**

- Modify: `lib/kube_client.py`
- Modify: `lib/runtime_bootstrap.py`
- Modify: `acm_switchover.py`
- Modify: `tests/test_kube_client.py`
- Modify: `tests/test_runtime_bootstrap.py`
- Modify: `tests/test_main.py`

**Interfaces:**

- Extend `KubeClient.__init__` with keyword `log_config_errors: bool = True`. The default preserves every unrelated caller's current logging. When false, the `ConfigException` catch re-raises without logging raw context or exception text.
- Add `runtime_bootstrap.HubIdentityVerificationError`, constructed only from logical role and emitting the stable role-specific message. It must not retain the raw exception text.
- Extend `runtime_bootstrap.initialize_clients(..., *, sanitize_identity_errors: bool = False)`. When true, construct each present role separately with `log_config_errors=False`, catch construction/configuration failure at that role boundary, and raise `HubIdentityVerificationError(role) from None`.
- `_prepare_runtime` passes `sanitize_identity_errors=True` for identity-bound switchover/restore-only preparation, catches `HubIdentityVerificationError` before the generic `Exception` arm, logs only its stable text without `exc_info`, restores the dry-run snapshot when applicable, and exits through the existing failure path.

- [ ] **Step 1: Add constructor-default and suppression tests.** In `tests/test_kube_client.py`, prove default `KubeClient` construction still logs the existing diagnostic for unrelated callers and `log_config_errors=False` emits none of the sentinel `ConfigException`, context, path, token-like, or credential values.

- [ ] **Step 2: Add role-scoped bootstrap tests.** In `tests/test_runtime_bootstrap.py`, add primary and secondary constructor failures using distinct sentinels and assert only the appropriate stable role message is available. Assert the other role is not constructed after the relevant failure and restore-only constructs/translates only secondary.

- [ ] **Step 3: Add CLI output/log leak tests.** In `tests/test_main.py`, drive `_prepare_runtime` through both role failures. Capture logger output, stdout, and stderr and assert the stable refusal is present while the kubeconfig path, token-like value, raw exception string, context value, and credential sentinel are absent. Assert the dry-run snapshot restoration call remains intact.

- [ ] **Step 4: Run the focused tests and record the expected red result.** Failures should identify the missing constructor option and translation path.

```bash
python -m pytest tests/test_kube_client.py tests/test_runtime_bootstrap.py tests/test_main.py -q \
  -k "config or initialize_clients or prepare_runtime or identity"
```

- [ ] **Step 5: Implement the bounded constructor/logging changes.** Do not modify the generic Kubernetes API decorator, retry configuration, global logger settings, or unrelated constructors. Ensure every translation uses `raise ... from None`.

- [ ] **Step 6: Re-run focused and adjacent bootstrap tests.** Expect no sentinel leakage and no change to default caller behavior.

```bash
python -m pytest tests/test_kube_client.py tests/test_runtime_bootstrap.py tests/test_main.py -q \
  -k "config or initialize_clients or prepare_runtime or identity"
python -m pytest tests/test_runtime_bootstrap.py tests/test_resume_safety_guards.py -q
```

- [ ] **Step 7: Commit during later implementation.** Keep this reviewable separately from UID API-read changes.

```bash
git add lib/kube_client.py lib/runtime_bootstrap.py acm_switchover.py tests/test_kube_client.py tests/test_runtime_bootstrap.py tests/test_main.py
git commit -m "fix: sanitize hub client establishment failures"
```

### Task 3: Python silent live identity read and cross-role binding

**Files:**

- Modify: `lib/kube_client.py`
- Modify: `lib/runtime_bootstrap.py`
- Modify: `acm_switchover.py`
- Modify: `tests/test_kube_client.py`
- Modify: `tests/test_runtime_bootstrap.py`
- Modify: `tests/test_main.py`
- Modify: `tests/test_cli_outcomes.py`
- Regression-only: `tests/test_utils.py`, `tests/test_resume_safety_guards.py`, `tests/test_main_argocd_resume.py`

**Interfaces and flow:**

```text
KubeClient.get_cluster_identity
→ private identity-only read of CoreV1Api.read_namespace("kube-system")
→ retry_api_call_advisory (existing retry predicate, five attempts, exponential wait)
→ no low-level exception-string emission
→ runtime_bootstrap.collect_hub_identities role-aware translation
→ _bind_runtime_hub_identities
→ validate_distinct_hub_identities
→ StateManager.ensure_hub_identities
→ existing StateIdentityMismatch outcome
→ _execute_operation only after success
```

- Add the private KubeClient method `_read_cluster_identity_namespace` with the exact `@retry_api_call_advisory` owner. It calls `self.core_v1.read_namespace("kube-system", **self._request_timeout_kwargs())` directly and converts the result to the current dictionary shape. It does not call generic `get_namespace`, `api_call`, or `retry_api_call` because those paths log raw exception strings.
- `collect_hub_identities` catches each required role read separately and raises `HubIdentityVerificationError(role) from None` without returning partial evidence.
- `_bind_runtime_hub_identities` translates safe collection or pure-validation failure into `StateIdentityMismatch` without an exception cause, then invokes existing state binding only after distinctness passes.

- [ ] **Step 1: Add direct identity-read tests.** In `tests/test_kube_client.py`, retain the current successful `kube-system` UID assertion and add non-retryable and retryable `ApiException` failures. Patch Tenacity waits so tests remain fast, assert exactly five calls for a retryable failure, and assert no sentinel API body, server message, URL/path, credential, token, or UID appears in `caplog`.

- [ ] **Step 2: Add role-aware collection tests.** In `tests/test_runtime_bootstrap.py`, add primary and secondary `get_cluster_identity` failures and assert stable role messages and no partial identity return. Add successful two-role and secondary-only cases.

- [ ] **Step 3: Add binder ordering and refusal tests.** In `tests/test_main.py`, assert equal UIDs prevent `StateManager.ensure_hub_identities`; distinct identities are passed unchanged after validation; validate-only/dry-run use `persist=False`; execute uses `persist=True`; malformed/empty evidence fails before state; and `_execute_operation` is not called through `run_operation_mode` after refusal.

- [ ] **Step 4: Add no-state/no-phase assertions.** Snapshot the state file before refusal and verify no equal identity is persisted, no mutation helper is called, and no mutation-capable completed step/phase appears. Reuse `tests/test_cli_outcomes.py::test_run_operation_mode_identity_mismatch_never_touches_state` and extend it only if an assertion is missing.

- [ ] **Step 5: Run focused tests and record the expected red result.** Expect the generic logged path and missing binder comparison to fail the new assertions.

```bash
python -m pytest tests/test_kube_client.py tests/test_runtime_bootstrap.py tests/test_main.py tests/test_cli_outcomes.py -q \
  -k "cluster_identity or hub_identities or identity_mismatch or bind_runtime"
```

- [ ] **Step 6: Implement the minimal silent-read and binder ordering.** Preserve request timeout, retry predicate, stop bound, wait policy, dry-run state guard, and existing `StateManager.ensure_hub_identities` code.

- [ ] **Step 7: Re-run focused and regression tests.** Expect the new behavior and existing per-role drift/Argo CD resume tests to pass.

```bash
python -m pytest tests/test_kube_client.py tests/test_runtime_bootstrap.py tests/test_main.py tests/test_cli_outcomes.py -q \
  -k "cluster_identity or hub_identities or identity_mismatch or bind_runtime"
python -m pytest tests/test_utils.py tests/test_resume_safety_guards.py tests/test_main_argocd_resume.py tests/test_decommission.py -q
```

- [ ] **Step 8: Commit during later implementation.** This commit completes the Python safety barrier.

```bash
git add lib/kube_client.py lib/runtime_bootstrap.py acm_switchover.py tests/test_kube_client.py tests/test_runtime_bootstrap.py tests/test_main.py tests/test_cli_outcomes.py
git commit -m "feat: bind distinct live hub identities before dispatch"
```

### Task 4: Collection static input guard

**Files:**

- Modify: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_input_validate.py`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_input_validate.py`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/test_validation_parity_fixture.py`
- Modify shared fixture from Task 1: `tests/fixtures/validation_parity_cases.yml`

**Contract:** Add a critical structured result for equal non-empty primary/secondary context names only when `operation.restore_only` is false. Use the exact approved message and a corrective action that tells the operator to select two different hub contexts without echoing their values. This module remains UX defense; the action in Task 5 repeats the check as the trusted runtime owner.

- [ ] **Step 1: Add failing module tests.** Add `test_normal_two_hub_same_context_fails`, `test_restore_only_does_not_apply_distinct_context_rule`, and a test confirming different names pass this rule. Verify `summarize_input_validation` counts the failure as critical.

- [ ] **Step 2: Extend the existing Collection parity runner.** Teach `test_validation_parity_fixture.py` to pass fixture hub data through `build_input_validation_results`, and consume the same `hub_contexts` cases added in Task 1.

- [ ] **Step 3: Run the focused tests and record the expected red result.**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_input_validate.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_validation_parity_fixture.py -q
```

- [ ] **Step 4: Implement the one validation result.** Do not reject extra `cluster_uid` keys here as a security mechanism; Task 5's allowlist makes them irrelevant to trusted evidence.

- [ ] **Step 5: Re-run focused tests.** Expect exact message parity with Python.

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_input_validate.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_validation_parity_fixture.py -q
```

- [ ] **Step 6: Commit during later implementation.** This may be combined with Task 5 if the builder needs a single Collection safety commit, but the test diff must remain reviewable by owner.

```bash
git add ansible_collections/tomazb/acm_switchover/plugins/modules/acm_input_validate.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_input_validate.py ansible_collections/tomazb/acm_switchover/tests/unit/test_validation_parity_fixture.py
git commit -m "feat: reject identical collection hub contexts"
```

### Task 5: Collection checkpoint action identity barrier

**Files:**

- Modify: `ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py`
- Explicitly unchanged: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/checkpoint.py`

**Literal action interface:**

```yaml
tomazb.acm_switchover.checkpoint_phase:
  identity_barrier: true
  phase: preflight
  status: enter
  checkpoint: "{{ acm_switchover_execution.checkpoint | default({}) }}"
  hubs: "{{ acm_switchover_hubs | default({}) }}"
  operation: "{{ acm_switchover_operation | default({}) }}"
  execution: "{{ acm_switchover_execution | default({}) }}"
  test_overrides: "{{ acm_switchover_test_overrides | default({}) }}"
  collection_version: "{{ acm_switchover_collection_version | default('') }}"
register: _checkpoint_enter
```

`identity_barrier: true`, `phase: preflight`, and `status: enter` are literal. The action rejects any identity-barrier call with another phase/status. Operator configuration is allowed through action arguments; physical evidence is not.

**Action-local implementation contract:**

- Add narrowly scoped helpers with fixed responsibilities:
  - `_run_identity_barrier(...)` orchestrates applicability, evidence, distinctness, and checkpoint handling.
  - `_read_live_namespace_uid(role, hub, task_vars, tmp) -> str` invokes `kubernetes.core.k8s_info` through `_execute_module` and returns only a validated trimmed UID.
  - `_validated_namespace_uid(role, result) -> str` enforces one usable Namespace resource, mapping metadata, string UID, and trimmed non-empty value and returns a stable role failure without raw module output.
  - `_build_trusted_operation_identity(hubs, operation, collection_version, trusted_uids) -> dict` builds fresh allowlisted locals and calls unchanged `build_operation_identity`.
- Derive native check mode only from `self._play_context.check_mode`; remove `task_vars.get("ansible_check_mode")` as an authority.
- Execute mode always calls `_execute_module(module_name="kubernetes.core.k8s_info", ...)` separately for required roles with `api_version: v1`, `kind: Namespace`, `name: kube-system`, and that role's configured `kubeconfig` and `context`. Module results never enter `task_vars`, register, `set_fact`, or a returned fact before safety decisions finish.
- Validate/dry-run use fresh reads by default. Only their explicit `test_overrides.non_live_hub_identities` mapping may replace reads. Execute ignores the override even under native check.
- A normal flow validates both contexts, rejects equality before API reads, validates both UIDs, and rejects equal UIDs. Restore-only validates and reads secondary only.
- Construct local data from scratch:

```python
sanitized_local_hubs = {
    "primary": {"context": validated_primary_context},
    "secondary": {"context": validated_secondary_context},
}
trusted_local_hub_identities = {
    "primary": {"cluster_uid": established_primary_uid},
    "secondary": {"cluster_uid": established_secondary_uid},
}
```

Restore-only constructs only the secondary entries. No local hubs mapping contains `cluster_uid`, kubeconfig, or arbitrary nested caller fields. The same local UID strings feed equality and expected identity.
- When checkpoint is enabled, build the initial expected identity from these locals, then run the existing `_normalize_checkpoint_data` algorithm. Without explicit reset, this retains stored-versus-current validation. With `reset`/`reset_from`, preserve the existing branch and `_build_reset_from_checkpoint` replacement behavior exactly.
- When checkpoint is disabled, perform the identity barrier and return compatible `changed: false`, `skipped_phase: false`, empty operational `facts`, and a sanitized non-authoritative identity summary without checkpoint load/write.
- Native check performs live reads and read-only checkpoint validation but no initialization, backfill, transition, quarantine, or write.
- Ordinary later transitions outside explicit reset/reset-from stop rebuilding physical UID fields from `task_vars`. They load and carry the established checkpoint `operation_identity`; an enabled execute transition with no established identity fails closed. Explicit reset/reset-from retain the current construction/replacement behavior for `R3-06`.
- Preserve `skipped_phase`, `facts`, checkpoint data, resume sentinel, and existing transition return shapes. A sanitized returned identity summary is compatibility/report output only.

- [ ] **Step 1: Add argument/authority tests.** Verify literal valid combination, invalid phase/status combinations, checkpoint disabled, and native check authority from `play_context.check_mode` even when a same-named task variable claims false/true.

- [ ] **Step 2: Add action-local live-read tests.** Mock `_execute_module` with distinct primary/secondary Namespace results and assert exact `k8s_info` calls. Add same-context no-read refusal, equal-live-UID refusal, distinct success, and restore-only secondary-only calls.

- [ ] **Step 3: Add strict shape and sanitization tests.** Parameterize failed/missing module result, zero/multiple resources, missing/non-mapping metadata, missing/non-string/empty/whitespace UID for both roles. Supply recognizable API body, path, context, token, credential, raw exception, and UID sentinels; assert no sentinel occurs in `result["msg"]`, captured display/log output, or returned report identity, and assert the stable role message occurs.

- [ ] **Step 4: Add freshness matrix tests.** Cover execute/check false with stale preseed, execute/check true with stale preseed, validate override, dry-run override, ordinary public preseed, and execute test override. Assert execute calls `_execute_module`; validate/dry override does not; execute ignores every injected value.

- [ ] **Step 5: Add provenance/checkpoint tests.** Inject `acm_switchover_hubs.primary.cluster_uid` and `.secondary.cluster_uid`, public/underscore identity variables, `_checkpoint_enter`, `acm_input_validation`, `_acm_identity_barrier_result`, returned-result names, and candidate verified booleans. Assert expected identity contains only fresh local UIDs, equal-live UID still fails, unavailable live evidence still fails, and primary/secondary stored drift still reaches `CheckpointIdentityMismatch` behavior.

- [ ] **Step 6: Add checkpoint mode tests.** Prove enabled fresh checkpoint initialization, disabled enforcement without file access, completed preflight still reruns reads before `skipped_phase`, check/validate/dry no persistence, and ordinary later transition carry-forward/fail-closed behavior.

- [ ] **Step 7: Pin the reset boundary.** Extend existing reset/reset-from unit tests to assert `_build_reset_from_checkpoint` still replaces operation identity with its supplied expected identity and SSA-01 does not alter explicit reset handling. Add a test that the initial barrier's expected identity is trusted when it happens to enter the current reset-from branch, without claiming preservation on later reset-from.

- [ ] **Step 8: Run the action tests and record the expected red result.**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py -q
```

- [ ] **Step 9: Implement the action-local barrier with the unchanged helper.** Keep all discovered evidence in local Python variables. Never read a UID back through `task_vars`, the registered result, or a public fact.

- [ ] **Step 10: Re-run the full action test file and adjacent checkpoint contracts.**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_checkpoint_identity_validate.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_checkpoint_facade.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_checkpoint_vocabulary_guardrail.py -q
```

- [ ] **Step 11: Commit during later implementation.** Include the action and its direct tests only.

```bash
git add ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py
git commit -m "feat: establish trusted collection hub identity barrier"
```

### Task 6: Collection preflight role split

**Files:**

- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/main.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/identity_barrier.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/post_identity.yml`
- Delete: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/discover_hub_identities.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_checkpoint_validation_order.py`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/test_checkpoint_vocabulary_guardrail.py`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_parity.py`

**Exact task ownership:**

- `identity_barrier.yml` contains, in order: current accumulator initialization; `validate_inputs.yml`; the literal Task 5 action call registered as `_checkpoint_enter`; and publication of `_checkpoint_enter.hub_identities` to `acm_switchover_hub_identities` only as non-authoritative compatibility/report output. The action has no `when` based on `acm_input_validation`, preflight summary, checkpoint enablement, `_checkpoint_enter`, or another caller-shadowable pass flag.
- `post_identity.yml` receives the current tasks after checkpoint enter: required skipped-checkpoint fact validation, operational fact rehydration, remaining preflight block, report write, critical failure, and preflight pass transition.
- `main.yml` includes `identity_barrier.yml`, then `post_identity.yml`, so `playbooks/preflight.yml` continues to execute complete preflight.
- `discover_hub_identities.yml` is deleted. There is one production discovery owner.

- [ ] **Step 1: Rewrite static tests first.** Assert the two-file composition order, literal action arguments, unconditional barrier call, register name `_checkpoint_enter`, absence of a Boolean trust fact, and that all skipped/operational fact consumers occur only in `post_identity.yml`.

- [ ] **Step 2: Add compatibility assertions.** Pin `_checkpoint_enter.skipped_phase` and `.facts` as allowed post-barrier control inputs while rejecting any UID/distinctness/freshness/checkpoint-identity use of `_checkpoint_enter`, `acm_switchover_hub_identities`, or the deleted discovery result names.

- [ ] **Step 3: Run static tests and record the expected red result.**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_checkpoint_validation_order.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_checkpoint_vocabulary_guardrail.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_parity.py -q
```

- [ ] **Step 4: Move tasks without duplicating behavior.** Preserve all current task names where practical because scenario assertions use them. Do not alter the preflight validation algorithm, report module, operational keys, or phase completion semantics.

- [ ] **Step 5: Re-run static and standalone preflight integration tests.**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_checkpoint_validation_order.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_checkpoint_vocabulary_guardrail.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_parity.py -q
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py -q
```

- [ ] **Step 6: Commit during later implementation.** The delete and split belong in one atomic commit.

```bash
git add ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/main.yml ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/identity_barrier.yml ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/post_identity.yml ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/discover_hub_identities.yml ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_checkpoint_validation_order.py ansible_collections/tomazb/acm_switchover/tests/unit/test_checkpoint_vocabulary_guardrail.py ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_parity.py
git commit -m "refactor: split collection preflight at identity barrier"
```

### Task 7: Collection structural recovery barrier

**Files:**

- Modify: `ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/test_restore_only_recovery_contracts.py`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/test_release_source_schema.py`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_report_aggregation.py`

**Required YAML shape:**

```yaml
tasks:
  - name: Run switchover phases with reporting
    block:
      - name: Establish trusted identity and checkpoint barrier
        ansible.builtin.include_role:
          name: tomazb.acm_switchover.preflight
          tasks_from: identity_barrier

      - name: Run post-barrier switchover phases
        block:
          - name: Run remaining preflight validation
            ansible.builtin.include_role:
              name: tomazb.acm_switchover.preflight
              tasks_from: post_identity

          - name: Stop after preflight when mode is validate
            ansible.builtin.meta: end_play
            when: acm_switchover_execution.mode | default('') == 'validate'

          - name: Run primary prep
            ansible.builtin.include_role:
              name: tomazb.acm_switchover.primary_prep

          - name: Run activation
            ansible.builtin.include_role:
              name: tomazb.acm_switchover.activation

          - name: Run post activation verification
            ansible.builtin.include_role:
              name: tomazb.acm_switchover.post_activation

          - name: Run finalization
            ansible.builtin.include_role:
              name: tomazb.acm_switchover.finalization
        rescue:
          - name: Attempt Argo CD resume on secondary hub after failure
            ansible.builtin.include_role:
              name: tomazb.acm_switchover.argocd_manage
            vars:
              acm_switchover_argocd_mode_override: resume
              _argocd_discover_hub: secondary
            when:
              - acm_switchover_features.argocd.manage | default(false)
              - acm_switchover_features.argocd.resume_on_failure | default(false)
            ignore_errors: true  # noqa: ignore-errors

          - name: Attempt Argo CD resume on primary hub after failure
            ansible.builtin.include_role:
              name: tomazb.acm_switchover.argocd_manage
            vars:
              acm_switchover_argocd_mode_override: resume
              _argocd_discover_hub: primary
            when:
              - acm_switchover_features.argocd.manage | default(false)
              - acm_switchover_features.argocd.resume_on_failure | default(false)
              - acm_switchover_hubs.primary is defined
              - (acm_switchover_hubs.primary.kubeconfig | default('')) | length > 0
              - (acm_switchover_hubs.primary.context | default('')) | length > 0
            ignore_errors: true  # noqa: ignore-errors

          - name: Reset primary prep checkpoint after Argo CD resume on failure
            tomazb.acm_switchover.checkpoint_phase:
              phase: primary_prep
              checkpoint: "{{ acm_switchover_execution.checkpoint | combine({'reset_from': 'primary_prep'}) }}"
              status: reset
              operational_data:
                argocd_run_id: "{{ acm_switchover_argocd.run_id | default('') }}"
                argocd_discovery_namespaces: "{{ acm_switchover_argocd_discovery_namespaces | default({}) }}"
            when:
              - acm_switchover_features.argocd.manage | default(false)
              - acm_switchover_features.argocd.resume_on_failure | default(false)
              - acm_switchover_execution.checkpoint.enabled | default(false)
            ignore_errors: true  # noqa: ignore-errors

          - name: Re-raise original switchover failure
            ansible.builtin.fail:
              msg: "{{ ansible_failed_result.msg | default('Switchover failed') }}"
    always:
      - name: Build switchover report contract
        ansible.builtin.set_fact:
          acm_switchover_report:
            schema_version: "1.0"
            source: tomazb.acm_switchover
            argocd:
              run_id: "{{ acm_switchover_argocd.run_id | default(acm_switchover_execution.run_id | default('')) }}"
              summary: >-
                {%- set hubs = acm_switchover_argocd_summary_by_hub | default({}) -%}
                {%- if hubs | length > 0 -%}
                {%- set ns = namespace(paused=0, restored=0) -%}
                {%- for _hub_name, hub_summary in hubs.items() -%}
                {%- set ns.paused = ns.paused + (hub_summary.get('paused', 0) | int) -%}
                {%- set ns.restored = ns.restored + (hub_summary.get('restored', 0) | int) -%}
                {%- endfor -%}
                {{ {'paused': ns.paused, 'restored': ns.restored} }}
                {%- else -%}
                {{ acm_switchover_argocd_summary | default({}) }}
                {%- endif -%}
            phases: >-
              {{
                {}
                | combine({'primary_prep': acm_switchover_primary_prep_result} if acm_switchover_primary_prep_result is defined else {})
                | combine({'activation': acm_switchover_activation_result} if acm_switchover_activation_result is defined else {})
                | combine({'post_activation': acm_switchover_post_activation_result} if acm_switchover_post_activation_result is defined else {})
                | combine({'finalization': acm_switchover_finalization_result} if acm_switchover_finalization_result is defined else {})
              }}

      - name: Write switchover report artifact
        tomazb.acm_switchover.acm_report_artifact:
          path: "{{ (acm_switchover_execution.report_dir | default('./artifacts')) ~ '/switchover-report.json' }}"
          report: "{{ acm_switchover_report }}"
```

No Boolean or action result selects recovery eligibility; YAML block membership does.

- [ ] **Step 1: Update static recovery tests before YAML.** Replace helpers that assume the outermost block owns rescue with helpers that locate the nested post-barrier block. Assert the identity include is an outer sibling before it, the nested block alone owns Argo CD resume/reset/re-raise, and outer `always` still owns the report.

- [ ] **Step 2: Add explicit pre/post boundary assertions.** Pin that no `rescue` encloses the identity include; input/same-context/identity/checkpoint mismatch therefore cannot reach recovery. Pin that `post_identity`, validate stop, `primary_prep`, activation, post-activation, and finalization are inside the nested recovery block.

- [ ] **Step 3: Run static tests and record the expected red result.**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_restore_only_recovery_contracts.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_release_source_schema.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_report_aggregation.py -q
```

- [ ] **Step 4: Apply only the structural move.** Preserve current recovery feature guards, target roles, variables, `ignore_errors`, reset-from configuration, operational data, re-raise text, and report schema.

- [ ] **Step 5: Re-run static tests and the focused switchover integration file.**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_restore_only_recovery_contracts.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_release_source_schema.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_report_aggregation.py -q
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py -q
```

- [ ] **Step 6: Commit during later implementation.**

```bash
git add ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py ansible_collections/tomazb/acm_switchover/tests/unit/test_restore_only_recovery_contracts.py ansible_collections/tomazb/acm_switchover/tests/unit/test_release_source_schema.py ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_report_aggregation.py
git commit -m "refactor: fence switchover recovery behind identity proof"
```

### Task 8: Restore-only and checkpoint compatibility regressions

**Files:**

- Production unchanged: `ansible_collections/tomazb/acm_switchover/playbooks/restore_only.yml`
- Modify tests/fixtures only as required:
  - `ansible_collections/tomazb/acm_switchover/tests/unit/test_restore_only_recovery_contracts.py`
  - `ansible_collections/tomazb/acm_switchover/tests/integration/test_restore_only_role.py`
  - `ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py`
  - `ansible_collections/tomazb/acm_switchover/tests/scenario/fixtures/checkpoint/interrupted_after_activation.yml`
  - `ansible_collections/tomazb/acm_switchover/tests/scenario/fixtures/checkpoint/preflight_completed_without_preflight_facts.yml`

**Regression contract:** Complete preflight composition must register the new barrier result as `_checkpoint_enter`; restore-only continues to read `_checkpoint_enter.facts.argocd_run_id` and `.argocd_discovery_namespaces`; identity establishment contacts secondary only; no two-role comparison or primary GET occurs; validate/dry fixtures use the explicit non-live identity override; existing reset/reset-from results remain unchanged.

- [ ] **Step 1: Add restore-only contract assertions.** Pin the unchanged consumer expressions in `restore_only.yml`, the preflight `main.yml` composition, and the fact that `_checkpoint_enter` is not read for a physical UID.

- [ ] **Step 2: Add secondary-only action/integration coverage.** Use a primary hub configuration containing a failure sentinel and assert no primary `_execute_module` call or fake API request occurs. Assert secondary stored-versus-live mismatch still fails and secondary match succeeds.

- [ ] **Step 3: Migrate scenario fixture identity evidence.** Put distinct non-live identities under `acm_switchover_test_overrides.non_live_hub_identities` for dry-run/validate cases. Leave ordinary public identities present only where a test deliberately proves they are non-authoritative.

- [ ] **Step 4: Pin `_checkpoint_enter` operational compatibility and reset ownership.** Prove skipped preflight restores expected managed-cluster/Observability facts, restore-only rehydrates Argo CD fields, and current explicit reset/reset-from behavior is unchanged. Do not assert corrected `R3-06` semantics.

- [ ] **Step 5: Run the focused tests and record any expected red fixture failures before migration.**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_restore_only_recovery_contracts.py -q
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_restore_only_role.py \
  ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py -q
```

- [ ] **Step 6: Apply only test/fixture compatibility changes and re-run.** `playbooks/restore_only.yml` must remain byte-for-byte unchanged unless a newly discovered incompatibility forces spec revalidation.

- [ ] **Step 7: Commit during later implementation.**

```bash
git add ansible_collections/tomazb/acm_switchover/tests/unit/test_restore_only_recovery_contracts.py ansible_collections/tomazb/acm_switchover/tests/integration/test_restore_only_role.py ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py ansible_collections/tomazb/acm_switchover/tests/scenario/fixtures/checkpoint/interrupted_after_activation.yml ansible_collections/tomazb/acm_switchover/tests/scenario/fixtures/checkpoint/preflight_completed_without_preflight_facts.yml
git commit -m "test: preserve restore-only checkpoint compatibility"
```

### Task 9: Full adversarial Collection integration matrix

**Files:**

- Modify: `ansible_collections/tomazb/acm_switchover/tests/conftest.py`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/integration/conftest.py`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/integration/argocd_fake_api.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/test_distinct_hub_identity_barrier.py`
- Modify adjacent shipped-flow tests only for explicit evidence setup:
  - `ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py`
  - `ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py`
  - `ansible_collections/tomazb/acm_switchover/tests/integration/test_restore_only_role.py`

**Harness changes:**

- Stop `_seed_fixture_defaults` from seeding `acm_switchover_hub_identities` as implicit safety evidence. For validate/dry-run fixtures, seed distinct identities only under `acm_switchover_test_overrides.non_live_hub_identities`; an explicitly injected public identity remains a malicious test value.
- Extend `FakeArgoCDHub` with thread-safe request records containing method and path for every GET/PATCH/POST/PUT/DELETE, configurable `kube-system` identity failure/status/body, and no change to existing Argo CD fixture behavior.
- Add a fixture that writes separate primary/secondary kubeconfigs pointing at two fake hubs, invokes the shipped playbook with an extra-vars file, optionally appends native `--check`, returns process output/checkpoint/report, and exposes both request logs.
- Execute-mode fixtures that reach preflight use this fake API. No live endpoint is used.

**Malicious variable set:**

```text
_acm_primary_identity_namespace
_acm_secondary_identity_namespace
acm_switchover_hub_identities
_acm_switchover_verified_hub_identities
acm_switchover_distinct_hubs_verified
_checkpoint_enter
acm_input_validation
_acm_identity_barrier_result
every implemented action-result/public identity/report fact name
acm_switchover_hubs.primary.cluster_uid
acm_switchover_hubs.secondary.cluster_uid
acm_switchover_test_overrides.non_live_hub_identities
```

Ansible variables resembling action-local Python names may be injected solely to prove the action never resolves them; do not describe them as real provenance channels.

- [ ] **Step 1: Add Case A — same physical cluster, spoofed distinct identities.** Both fake hubs return `LIVE-SAME`; extra vars claim `FAKE-A` and `FAKE-B`. Assert two fresh Namespace GETs, exact equal-cluster refusal, no checkpoint phase completion, no `primary_prep`, no Argo CD PATCH, no checkpoint reset task, and no POST/PATCH/PUT/DELETE.

- [ ] **Step 2: Add Case B — unavailable UID, spoofed usable identity.** Parameterize primary/secondary failures and include sentinel API body, path, exception-like text, token, credential, and UID. Assert stable role refusal, no sentinel in stdout/stderr/report/Ansible failure output, and no mutation request.

- [ ] **Step 3: Add Case C — stored-versus-live checkpoint drift.** Seed schema 2.0 `STORED-A`/`STORED-B`, return a different live UID for one role, and inject matching stored UIDs through every public/private-looking channel including `hubs.<role>.cluster_uid`. Assert the existing checkpoint mismatch and inspect the action/checkpoint result or saved unchanged file to prove the fresh local UID was the expected current value. Cover both roles.

- [ ] **Step 4: Add Case D — pre-barrier failure with spoofed recovery values.** Inject every likely recovery/verified/result variable as true/pass, cause trusted identity failure, and assert structural absence of Argo CD resume, checkpoint reset, and all Kubernetes mutation methods.

- [ ] **Step 5: Add Case E — post-barrier controlled failure.** Return distinct UIDs, let the trusted barrier pass, fail a controlled post-identity task, and assert configured Argo CD recovery plus current checkpoint-reset recovery remains reachable. This test must distinguish recovery PATCHes after the barrier from forbidden pre-barrier mutations.

- [ ] **Step 6: Add Case F — execute plus native check.** Inject stale public identities and explicit test override, run shipped `switchover.yml --check`, assert primary and secondary live Namespace GETs, assert the live value determines pass/refusal, and assert zero POST/PATCH/PUT/DELETE and byte-identical/missing checkpoint file.

- [ ] **Step 7: Add checkpoint-disabled and dry-run request proofs.** Disable persistence and prove the guard still rejects equality; run dry-run with explicit override and prove zero fake API mutation. Add checkpoint-complete resume proof that fresh reads still precede use of `skipped_phase`.

- [ ] **Step 8: Run the new file and record the expected red result.**

```bash
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_distinct_hub_identity_barrier.py -q
```

- [ ] **Step 9: Implement harness/fixture changes and re-run the new plus adjacent integration files.**

```bash
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_distinct_hub_identity_barrier.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_restore_only_role.py -q
```

- [ ] **Step 10: Run the same adversarial file in both endpoint environments.** Use the exact lane setup in Task 12; both must pass with identical safety decisions.

- [ ] **Step 11: Commit during later implementation.**

```bash
git add ansible_collections/tomazb/acm_switchover/tests/conftest.py ansible_collections/tomazb/acm_switchover/tests/integration/conftest.py ansible_collections/tomazb/acm_switchover/tests/integration/argocd_fake_api.py ansible_collections/tomazb/acm_switchover/tests/integration/test_distinct_hub_identity_barrier.py ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py ansible_collections/tomazb/acm_switchover/tests/integration/test_restore_only_role.py
git commit -m "test: prove collection identity barrier resists extra vars"
```

### Task 10: Cross-form-factor parity and static contracts

**Files:**

- Modify: `tests/fixtures/validation_parity_cases.yml`
- Modify: `tests/test_validation_parity.py`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/test_validation_parity_fixture.py`
- Modify or extend current static contracts in `ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_parity.py`
- Add no new runtime dependency between form factors.

**Pinned contract:** applicability to normal two-hub flows; exclusions; source `kube-system` Namespace UID; role names `primary`/`secondary`; exact context/equal-cluster/unavailable messages; distinct success; stale/preseed behavior; dry/check zero mutation; additive resume binding; no imports between Python/Collection or from `tests/release`.

- [ ] **Step 1: Complete shared fixture cases.** Ensure the existing fixture covers same context, equal UID, primary/secondary missing/malformed/empty evidence, and distinct UIDs with exact expected messages.

- [ ] **Step 2: Add static ownership assertions.** Pin Python call order `_collect_hub_identities` → `validate_distinct_hub_identities` → `ensure_hub_identities`; Collection literal action ownership, action-local `k8s_info`, sanitized local-hub allowlist without `cluster_uid`, deleted register discovery, structural rescue placement, and restore/decommission exclusions.

- [ ] **Step 3: Add import-boundary assertions.** Scan production Python and Collection files for cross-imports and `tests.release`/`tests/release` imports. Report modules may share fixture text only through tests.

- [ ] **Step 4: Run focused parity tests and record the expected red result before completing both runners.**

```bash
python -m pytest tests/test_validation_parity.py -q
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_validation_parity_fixture.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_parity.py -q
```

- [ ] **Step 5: Implement only fixture/static test plumbing and re-run.** No parity-status change is permitted.

- [ ] **Step 6: Run the required parity-sensitive combined gate.** Root tests must remain import-safe even where `ansible-core` is not installed.

```bash
unset ACM_RELEASE_PROFILE PYTEST_ADDOPTS
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ tests/ -q
```

- [ ] **Step 7: Commit during later implementation.**

```bash
git add tests/fixtures/validation_parity_cases.yml tests/test_validation_parity.py ansible_collections/tomazb/acm_switchover/tests/unit/test_validation_parity_fixture.py ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_parity.py
git commit -m "test: pin distinct hub validation parity"
```

### Task 11: Operator/developer documentation and tracker updates

**Files:** Use the exact later-documentation list in the definitive file map. Do not touch protected files.

**Concrete content:**

- `thermos-resolution-plan.md`: update only SSA-01/SSA-A2/SSA-P2 evidence/status according to issue #267's implementation workflow; do not modify another slice.
- `CHANGELOG.md`: record the fail-closed normal two-hub guard under `Unreleased`; do not bump a released version.
- READMEs, usage, validation rules, Collection variable reference, and CLI migration map: document same-context and same-physical-UID refusals, fresh execute behavior, explicit validate/dry test override, native check freshness, and restore/decommission exclusions.
- Architecture and Mermaid flows: show Python binder ordering and Collection action-local identity/checkpoint barrier plus structural recovery boundary.
- Parity/behavior/coexistence documents: record equivalent decisions and independent implementations while leaving capability status unchanged.
- Scenario/test-migration catalogs: map the six adversarial cases, checkpoint/resume, restore-only, and both endpoint lanes to their concrete tests.

- [ ] **Step 1: Add documentation guardrail expectations before prose where the repository already pins required text.** Extend `tests/test_documentation_guardrails.py` only for safety-critical contract presence, not exact formatting.

- [ ] **Step 2: Run the documentation guardrails and record the expected red result.**

```bash
python -m pytest tests/test_documentation_guardrails.py tests/test_ci_guardrails.py -q
```

- [ ] **Step 3: Update only the listed behavior-bearing sections.** Do not edit `docs/ACM_SWITCHOVER_RUNBOOK.md`, `.claude/skills/**`, version identifiers, release profiles, or unrelated status tables.

- [ ] **Step 4: Re-run documentation/static guards and check changed links.** Use the repository markdown-link configuration for the changed Markdown files; do not run live link checks against protected documents.

```bash
python -m pytest tests/test_documentation_guardrails.py tests/test_ci_guardrails.py -q
npx --yes markdown-link-check --config .github/markdown-link-check.json \
  thermos-resolution-plan.md CHANGELOG.md README.md \
  ansible_collections/tomazb/acm_switchover/README.md \
  docs/development/architecture.md ansible_collections/tomazb/acm_switchover/docs/architecture.md \
  docs/operations/usage.md docs/reference/validation-rules.md \
  docs/ansible-collection/parity-matrix.md docs/ansible-collection/behavior-map.md \
  ansible_collections/tomazb/acm_switchover/docs/coexistence.md \
  ansible_collections/tomazb/acm_switchover/docs/variable-reference.md \
  ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md \
  docs/ansible-collection/scenario-catalog.md docs/ansible-collection/test-migration-catalog.md
```

- [ ] **Step 5: Commit during later implementation.** Keep tracker/changelog/docs together after behavior is final.

```bash
git add thermos-resolution-plan.md CHANGELOG.md README.md ansible_collections/tomazb/acm_switchover/README.md docs/development/architecture.md ansible_collections/tomazb/acm_switchover/docs/architecture.md docs/operations/usage.md docs/reference/validation-rules.md docs/ansible-collection/parity-matrix.md docs/ansible-collection/behavior-map.md ansible_collections/tomazb/acm_switchover/docs/coexistence.md ansible_collections/tomazb/acm_switchover/docs/variable-reference.md ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md docs/ansible-collection/scenario-catalog.md docs/ansible-collection/test-migration-catalog.md tests/test_documentation_guardrails.py
git commit -m "docs: document distinct physical hub validation"
```

### Task 12: Final verification and exact-head preparation

This task executes only after Tasks 1–11 are implemented. Run targeted gates first, then every invalidated full surface. Do not run live certification. Do not create a release tag or change a released version.

- [ ] **Step 1: Re-run all SSA-01 focused Python tests.**

```bash
python -m pytest \
  tests/test_validation.py \
  tests/test_kube_client.py \
  tests/test_runtime_bootstrap.py \
  tests/test_main.py \
  tests/test_cli_outcomes.py \
  tests/test_utils.py \
  tests/test_resume_safety_guards.py \
  tests/test_main_argocd_resume.py \
  tests/test_decommission.py \
  tests/test_validation_parity.py -q
```

- [ ] **Step 2: Re-run all SSA-01 focused Collection tests.**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_input_validate.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_checkpoint_validation_order.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_checkpoint_vocabulary_guardrail.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_parity.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_restore_only_recovery_contracts.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_validation_parity_fixture.py -q

export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_distinct_hub_identity_barrier.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_restore_only_role.py \
  ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py -q
```

- [ ] **Step 3: Run the root and parity-sensitive suites.** The authoritative root selection excludes release and e2e. Also run the issue-required combined parity gate.

```bash
python -m pytest tests/ --ignore=tests/release -v -m "not e2e"
unset ACM_RELEASE_PROFILE PYTEST_ADDOPTS
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ tests/ -q
```

- [ ] **Step 4: Reproduce the `min` Collection endpoint.** Use a dedicated environment; do not alter the shared environment.

```bash
python3.11 -m venv .venv-lane-min
source .venv-lane-min/bin/activate
python -m pip install --upgrade pip
pip install "ansible-core==2.16.*" pytest PyYAML "kubernetes>=28.0.0"
ansible-galaxy collection install -r ansible_collections/tomazb/acm_switchover/requirements.yml
export ANSIBLE_COLLECTIONS_PATH="$PWD:$HOME/.ansible/collections"
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_compatibility_contract.py -q
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/ -q
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/scenario/ -q
```

- [ ] **Step 5: In the same `min` environment, syntax-check every playbook with the warning backstop, then build.**

```bash
set -o pipefail
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
log="$(mktemp)"
status=0
for playbook in ansible_collections/tomazb/acm_switchover/playbooks/*.yml; do
  echo "== ${playbook}"
  ansible-playbook "${playbook}" --syntax-check 2>&1 | tee -a "${log}" || status=1
done
if [ "${status}" -ne 0 ]; then
  echo "playbook syntax check failed"
  exit 1
fi
if grep -qE "does not support Ansible version" "${log}"; then
  echo "a collection reported an unsupported ansible-core version for this lane"
  grep -nE "does not support Ansible version" "${log}"
  exit 1
fi
ansible-galaxy collection build --output-path /tmp/dist \
  ansible_collections/tomazb/acm_switchover
deactivate
```

- [ ] **Step 6: Reproduce the `current` Collection endpoint.** Repeat all Collection surfaces under Python 3.12 and `ansible-core 2.21.*`.

```bash
python3.12 -m venv .venv-lane-current
source .venv-lane-current/bin/activate
python -m pip install --upgrade pip
pip install "ansible-core==2.21.*" pytest PyYAML "kubernetes>=28.0.0"
ansible-galaxy collection install -r ansible_collections/tomazb/acm_switchover/requirements.yml
export ANSIBLE_COLLECTIONS_PATH="$PWD:$HOME/.ansible/collections"
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_compatibility_contract.py -q
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/ -q
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/scenario/ -q
```

- [ ] **Step 7: In the same `current` environment, run the identical syntax warning backstop and collection build.** Use the exact Step 5 loop and build command, then `deactivate`.

- [ ] **Step 8: Run formatter, import, type, lint, and security gates with the repository-authorized scopes.** Do not point black/isort at the repository root.

```bash
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
flake8 . --count --exit-zero --max-complexity=15 --max-line-length=120 --statistics
pylint acm_switchover.py lib/ modules/ --exit-zero --max-line-length=120 \
  --disable=C0103,C0114,C0115,C0116
black --check --line-length 120 --diff acm_switchover.py lib modules \
  ansible_collections/tomazb/acm_switchover/plugins \
  ansible_collections/tomazb/acm_switchover/tests tests
isort --check-only --profile black --line-length 120 acm_switchover.py lib modules \
  ansible_collections/tomazb/acm_switchover/plugins \
  ansible_collections/tomazb/acm_switchover/tests tests
mypy --explicit-package-bases acm_switchover.py lib/ modules/ \
  ansible_collections/tomazb/acm_switchover/plugins \
  ansible_collections/tomazb/acm_switchover/tests \
  --ignore-missing-imports --no-strict-optional
bandit --ini .bandit -f json -o bandit-report.json || true
bandit --ini .bandit -f txt
pip-audit
```

- [ ] **Step 9: Re-run docs/static checks and inspect reports for sentinel leakage.** Search generated test/report artifacts for every deliberate sentinel. Any occurrence in callback-visible output, reports, stdout/stderr, or retry logs is a failure.

```bash
python -m pytest tests/test_documentation_guardrails.py tests/test_ci_guardrails.py -q
rg -n "ssa01-secret-(kubeconfig|token|api-body|raw-exception|uid|context|credential)|FAKE-A|FAKE-B" \
  artifacts .state /tmp/dist 2>/dev/null
```

- [ ] **Step 10: Verify no-mutation evidence.** Archive the adversarial fake-server request logs. For every refusal and execute-plus-check case, assert GET-only traffic and no checkpoint write; for post-barrier recovery, identify the recovery request as occurring only after the barrier-success marker.

- [ ] **Step 11: Verify RBAC no-impact.** Inspect the final diff and assert it adds only the existing core/v1 Namespace GET. Confirm no diff in Python/Collection RBAC validators, root/bundled manifests, or Helm.

```bash
git diff -- lib/rbac_validator.py \
  ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_validate.py \
  deploy/rbac/ \
  ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/files/deploy/rbac/ \
  deploy/helm/acm-switchover-rbac/
```

If this command is non-empty or implementation adds another resource/verb/namespace/credential mechanism, stop for scope-expansion approval.

Expected RBAC result: `NO CHANGE`.

- [ ] **Step 12: Verify protected and excluded surfaces.** Expect no output.

```bash
git diff -- docs/ACM_SWITCHOVER_RUNBOOK.md .claude/skills/
git diff -- tests/release/lab_controller/ tests/release/
git diff -- ansible_collections/tomazb/acm_switchover/playbooks/restore_only.yml
git diff -- ansible_collections/tomazb/acm_switchover/plugins/module_utils/checkpoint.py
```

Expected protected-file result: `NO DIFF`.

The `tests/release/` check is scope verification only; no release test or live certification command is authorized.

- [ ] **Step 13: Inspect exact changed-file scope and working-tree state.** Confirm only planned production, tests, fixtures, and later documentation changed, no generated lane environments/artifacts are staged, and the approved design remains unchanged.

```bash
git status --porcelain=v1
git diff --check
git diff --name-status origin/ansible...HEAD
git diff -- docs/plans/2026-08-20-ssa-01-distinct-physical-hub-validation-design.md
```

- [ ] **Step 14: Prepare the exact head for governed validation only after operator-authorized implementation is complete.** Record base, head, merge base, changed files, protected diff, all targeted/full gate results, both endpoint lanes, RBAC result, and no-live evidence. Do not push, create a PR, or invoke an independent validator without the separate authorization required by issue #267.

## Future Commit Sequence

The task-level commits above may be consolidated only when necessary to keep a buildable intermediate state. The preferred review sequence is:

1. Python context and pure identity validation.
2. Python sanitized discovery and pre-dispatch binding.
3. Collection input UX and trusted checkpoint identity barrier.
4. Collection preflight/recovery structural split and compatibility regressions.
5. Adversarial integration and parity coverage.
6. Documentation/tracker/changelog updates.

The merge-ready endpoint must contain both form factors and parity evidence; no intermediate commit authorizes intentional divergence.

## Acceptance-Criterion Traceability

| Issue #267 requirement | Implementation tasks | Focused verification |
| --- | --- | --- |
| 1. Same-context refusal | Tasks 1, 4, 5 | Python validation tests; Collection input/action tests; parity fixture. |
| 2. Same-live-UID refusal | Tasks 1, 3, 5, 9 | Python binder tests; action equal-UID test; adversarial Case A. |
| 3. Unavailable/malformed evidence with sanitized errors | Tasks 2, 3, 5, 9 | Python constructor/read sentinel tests; Collection shape tests; Case B primary/secondary. |
| 4. Distinct UID success | Tasks 1, 3, 5, 9 | Python distinct binder test; action distinct result; shipped-flow success. |
| 5. Execute freshness | Tasks 3, 5, 9 | Python fresh mode tests; Collection execute stale-preseed tests; fake API GET accounting. |
| 6. Existing stored-versus-current resume preservation | Tasks 3, 5, 8, 9 | `tests/test_utils.py`; action drift tests; Case C; reset boundary regression. |
| 7. No mutation after refusal | Tasks 3, 7, 9 | No `_execute_operation`; unchanged state/phase; Cases A/B/D request logs. |
| 8. Restore-only/decommission exclusions | Tasks 1, 4, 5, 8 | Python restore/decommission regressions; secondary-only Collection tests; decommission suite. |
| 9. Python tests | Tasks 1–3, 12 | Focused Python command, then authoritative root suite. |
| 10. Collection shipped-flow tests | Tasks 6–9, 12 | Preflight/switchover/restore integration, scenario, syntax, and build. |
| 11. Parity/static contracts | Tasks 1, 4, 10 | Shared fixture, preflight static contracts, required combined parity gate. |
| 12. Stale-preseed negative coverage | Tasks 5, 9 | Action freshness matrix; Cases A/B/C/F with all spoof names. |
| 13. Dry-run/check zero mutation | Tasks 3, 5, 9 | Python dry-run state tests; fake request methods; execute-plus-check no checkpoint write. |
| 14. Checkpoint and Argo CD regressions | Tasks 5, 7, 8, 9 | `_checkpoint_enter` compatibility; explicit reset boundary; pre/post recovery Cases D/E. |
| 15. Compatibility lanes | Tasks 9, 12 | Complete min 2.16/Python 3.11 and current 2.21/Python 3.12 surfaces. |
| 16. Targeted-before-full gate ordering | Every task, Task 12 | Each task runs focused red/green first; Task 12 widens to full invalidated gates. |
| 17. Protected-file exclusion | Global constraints, Task 12 | Base-relative protected diff and `.claude/skills/**` diff must be empty. |

No criterion is satisfied only by a broad full-suite result; every row has a focused behavioral assertion.

## Implementation Stop Conditions

Stop and request new operator direction if implementation discovers any need for:

- a Kubernetes permission beyond the existing core/v1 Namespace `get`;
- checkpoint schema change or correction of current `reset_from` replacement behavior;
- general Argo CD transaction/recovery redesign;
- standalone decommission target hardening;
- production reuse/import from release-controller code;
- a protected-file change;
- live cluster evidence or certification;
- an intentional Python/Collection parity divergence;
- a change to the approved action-local evidence or structural barrier architecture.
