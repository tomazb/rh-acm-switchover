# Lab Role Controller Read-Only Live Preflight Pilot Design

## Status

This is a pilot design.

- It does not run a pilot.
- It does not contact live clusters.
- It does not read kubeconfigs.
- It does not load live config files.
- It does not execute the Phase 8J live transport.
- It does not run `oc`, `kubectl`, `ansible-playbook`, release adapters, or the pytest release framework against live
  clusters.
- It does not enable mutation.
- It does not produce live ACM certification evidence.
- It does not enable automatic recovery.
- It does not add Agent-driven live behavior.
- Current defaults remain non-live.

Phase 8K defines the first read-only live preflight pilot plan that a later phase may rehearse or execute under strict
human approval. This document intentionally stops at design and audit requirements.

## Final Recommendation

READY_FOR_PHASE_8L_READ_ONLY_LIVE_PREFLIGHT_PILOT_DRY_RUN

Phase 8L must still be tightly gated. It should perform either a non-contact dry-run of the pilot package or a
fake-backed rehearsal before any real live-contact pilot is separately approved. This recommendation is not a broad
live rollout.

## Scope

In scope:

- first read-only live preflight pilot design
- operator prerequisite checklist
- runtime-only input model
- pilot scenario allowlist
- pilot query allowlist
- gate sequence and abort criteria
- artifact/evidence acceptance criteria
- failure interpretation
- manual-recovery/no-automatic-recovery posture
- audit checklist for actual pilot readiness
- next-phase entry criteria

Out of scope:

- executing the pilot
- enabling live defaults
- live config file loading
- kubeconfig reading
- real live cluster contact
- mutation
- restore
- decommission
- automatic recovery
- live ACM certification
- Agent-driven live operation
- production schema finalization
- protected runbook changes

## Current Foundation

Phase 8K builds on completed non-live and opt-in read-only foundations:

- **Phase 8C external live config model**: `ExternalLiveLabConfig` models runtime-only field sensitivity, sanitized
  examples, artifact-safe summaries, L0-L10 gate names, and fail-closed validation. It does not load live config files,
  read kubeconfigs, read environment credentials, contact clusters, or enable live execution.
- **Phase 8E read-only discovery guardrails**: `read_only_discovery.py` classifies scenarios, query families, verbs,
  gate sets, query plans, and provisional artifact fields. It requires L0-L9 for read-only discovery, keeps L10 out of
  read-only authorization, rejects mutation, and keeps `live_certification_evidence=false`.
- **Phase 8G backend interface skeleton**: `read_only_backend.py` defines request/result/evidence contracts and an
  `UnimplementedReadOnlyDiscoveryBackend` that validates requests and fails closed with `BLOCKED`, no live contact, no
  mutation, and no certification evidence.
- **Phase 8H fake transport contracts**: `read_only_transport.py` defines structured read-only query objects,
  deterministic fake fixtures, transport decisions, response summaries, and fake-backed contract tests. It never
  contacts clusters or sets live contact flags.
- **Phase 8I live transport design review**: the design review selects a typed, controller-owned client abstraction for
  a future read-only live transport and rejects shell, `oc`, `kubectl`, `ansible-playbook`, release adapters, raw HTTP,
  and arbitrary Agent commands for the first implementation.
- **Phase 8J opt-in read-only live transport implementation**: `read_only_live_transport.py` adds an injected-client
  transport abstraction with explicit opt-in flags, runtime-only handles, L0-L9 and query guardrails, redaction checks,
  and fail-closed decisions. It is disabled by default, ships no real client, is not wired into CLI/planner defaults,
  and never sets mutation or certification evidence flags.

Phase 8K uses these pieces to design the first pilot. It does not execute the pilot.

## Pilot Objective

The first pilot should prove that a human-approved, runtime-supplied, opt-in read-only preflight can:

- pass L0-L9 gates
- construct allowlisted read-only queries
- perform minimal live read contact only when explicitly enabled in a later phase
- collect artifact-safe evidence summaries
- distinguish live contact evidence from live certification evidence
- abort safely on ambiguity, unsafe payloads, mismatches, missing gates, or transient infrastructure problems

It must not prove switchover readiness, full release certification, restore success, mutation safety, or automatic
recovery.

## Pilot Non-Goals

- no passive switchover
- no restore
- no decommission
- no Argo CD sync/refresh/pause/resume
- no backup mutation
- no managed cluster import/removal
- no RBAC creation
- no workload mutation
- no ACM certification
- no production support claim
- no Agent-led live execution
- no automatic retry beyond explicitly authorized read-only retry classification

## Pilot Environment Assumptions

The pilot design assumes:

- two ACM hub candidates
- three managed clusters represented by operator-provided expected names or fingerprints outside Git
- the operator has external knowledge of physical hub identity
- the operator has runtime-only kubeconfig/context handles
- the operator can provide the expected managed cluster set
- the operator can provide an approval reference
- the operator can provide an artifact directory
- the operator can run from the expected `ansible` branch and expected commit
- the operator can prove a clean worktree
- the operator can review artifacts before any next step

This document intentionally includes no real cluster names, API endpoints, kubeconfig paths, tokens, credentials, or
private IDs.

## Operator Prerequisites

Before any later pilot can run, the human operator must have:

- accepted the Phase 8K design and the Phase 8L dry-run/rehearsal plan
- selected the exact branch and commit expected for the pilot package
- verified the worktree is clean, or explicitly documented why a dirty diagnostic run is allowed
- verified Phase 8J implementation tests are green in CI and locally for the targeted commit
- prepared runtime-only hub access handles outside Git
- prepared expected physical hub labels and identity fingerprints outside Git
- prepared the expected managed cluster set outside Git
- prepared a redaction policy and artifact review process
- prepared a caller-provided artifact directory outside committed paths and not the default `.release` location
- provided an approval reference specific to the pilot attempt
- confirmed no mutation, recovery, restore, decommission, live adapter, Agent live behavior, or certification scope is
  part of the pilot

## Runtime-Only Inputs

| Input | Required | Validation rule | Artifact exposure rule | Failure decision if missing or unsafe |
| --- | --- | --- | --- | --- |
| operator approval reference | Required | Non-empty, tied to this pilot attempt, not a credential-like value | Redacted presence and optional redacted reference only | `BLOCKED` before contact; `NO_GO` if unsafe publication risk is detected |
| expected branch/commit | Required | Must match current checkout and expected commit policy | Branch label and commit hash may appear; dirty summary must be sanitized | `BLOCKED` or `NO_GO` depending on readiness versus certification interpretation |
| expected physical hub labels | Required | Exactly the labels expected for the two hub candidates; no private IDs | Labels may appear only if operator-approved and sanitized | `BLOCKED` before contact; `NO_GO` on mismatch |
| expected role labels, if known | Optional | Must be supporting evidence only, never sole proof of current role | Sanitized category summary only | `RECOVERY_REQUIRED` if live role evidence remains ambiguous |
| expected managed cluster names or fingerprints | Required | Exact match required; unexpected cluster policy is `block` | Names, counts, hashes, or fingerprints according to redaction policy | `BLOCKED` if absent; `NO_GO` or `RECOVERY_REQUIRED` on drift |
| runtime kubeconfig/context handles | Required for any later live contact | Opaque handles only; no raw paths, endpoints, or config contents | Presence booleans and redacted/fingerprinted handle summary only | `BLOCKED` before contact; `NO_GO` if unsafe values would publish |
| credential handles | Required when the read client needs separate auth | Opaque handles only; no values, token-like text, or environment dumps | Presence booleans only | `BLOCKED` before contact; `NO_GO` if unsafe values would publish |
| artifact directory | Required | Caller-provided, outside committed paths, not default `.release`, writable in later phase | Sanitized directory category and write status only, not raw sensitive path | `BLOCKED` before contact; `NO_GO` on artifact write/redaction failure after contact |
| opt-in flags | Required | Explicit read-only live mode and read-only query opt-in; no implicit defaults | Boolean flags and selected mode label may appear | `BLOCKED` before contact |
| allowed scenario IDs | Required | Subset of the Phase 8K allowlist only | Scenario IDs may appear | `BLOCKED` before contact |
| allowed query families | Required | Subset of the Phase 8K query allowlist only | Query family labels may appear | `BLOCKED` before contact |
| timeout/retry budget | Required | Positive, bounded, read-only only; no indefinite waits | Sanitized numeric category or exact safe value | `BLOCKED` before contact; `INFRA_RETRYABLE` only under strict criteria |
| redaction policy version | Required | Redaction required, rejects raw API endpoints, secrets, credentials, private IDs, and runtime handles | Policy label and pass/fail status only | `BLOCKED` before contact; `NO_GO` on redaction failure |
| dry-run/fake/live mode selector for a later phase | Required | One of `fake`, `non-contact-dry-run`, or `live-read-only`; Phase 8K does not implement it | Mode label only | `BLOCKED` if absent or unsupported |

## Pilot Scenario Allowlist

Allowed for pilot design:

- `lab-readiness`: read-only readiness evidence only; no mutation or adapter execution.
- `baseline-check`: read-only baseline evidence only; expected managed cluster and role evidence summaries.
- `preflight`: read-only preflight query plan only; no Python CLI, Ansible, Bash, or pytest live adapter run.
- `final-baseline-check`: read-only post-check shape only; useful after a fake-backed rehearsal or future read-only
  contact, not after mutation in Phase 8K.

Deferred or blocked:

- `passive switchover`: blocked because it changes hub roles and requires mutation gates.
- `restore-only`: blocked because restore state is mutating and can change active management state.
- `argocd-managed-switchover`: blocked because sync, refresh, pause, resume, and ownership handling need separate
  non-mutating proof before any status-only inclusion.
- `checkpoint-resume`: blocked because stale checkpoint or resume state can cause wrong-state continuation.
- `full-restore`: blocked because restore creation and role activation are mutating.
- `decommission`: blocked because it is destructive/disposable-lab-only.
- `failure-injection`: blocked because it is disruptive and requires disposable-lab proof.
- `soak`: blocked because repeated cycles require proven mutation/recovery boundaries.
- `rbac-bootstrap-live`: deferred unless separately designed as read-only SubjectAccessReview-style evidence.
- any scenario that can mutate: blocked.

Unknown scenario IDs are `BLOCKED`.

## Pilot Query Allowlist

Allowed:

- `cluster_identity`
- `namespace_uid`
- `cluster_version`
- `acm_mce_mch_status`
- `managed_cluster_status`
- `backup_restore_status` only if safe, bounded, status-only, and needed for the allowed scenario

Conditional/deferred:

- `argocd_status` only after separate non-mutating proof
- `subject_access_review` only after separate non-mutating proof
- `logs_events` deferred
- broad resource dumps deferred

Forbidden:

- secret-bearing resources
- arbitrary shell
- Agent-invented commands
- mutation-capable queries
- `create`, `update`, `patch`, `delete`, `apply`, `scale`, `rollout`, `annotate`, `label`, `pause`, `resume`, `sync`,
  `refresh`, `restore`, and `decommission`

Allowed verbs are `get`, `list`, and `describe` only. Unknown verbs are `BLOCKED`.

## Gate Sequence

### Pre-Run Gates

1. Verify branch/commit/worktree.
2. Verify Phase 8J implementation exists and tests are green.
3. Verify operator approval reference.
4. Verify external runtime inputs are supplied outside Git.
5. Verify artifact directory is caller-provided and not the `.release` default.
6. Verify opt-in flags are explicit.
7. Verify scenario allowlist.
8. Verify query family allowlist.
9. Verify runtime handles are present and artifact-safe.
10. Verify redaction policy.

### Pre-Contact Checks

- L0 through L9 all pass.
- Phase 8E query guardrails return `PASS`.
- Phase 8H transport query validation returns `PASS` for fake/rehearsal parity.
- Phase 8J opt-in guard returns `PASS` only in a later live-contact phase.
- No L10 mutation authorization is used.
- `live_certification_evidence=true` is absent and forbidden.
- `mutation_enabled=false`.
- `redaction_required=true`.
- No live client is constructed from implicit environment state.
- No shell, `oc`, `kubectl`, `ansible-playbook`, or release adapter invocation is present.

### Live-Contact Checks For A Later Phase

These checks are design requirements only in Phase 8K:

- live contact can start only after pre-run and pre-contact gates pass
- each query is a structured allowlisted query object, not a command string
- the client receives only the query family, verb, hub label, resource family, query ID, scenario ID, and bounded
  timeout
- the client performs only the requested read and returns a structured raw response to the redaction path
- no raw response is written before redaction
- timeout and transient failures are classified without retrying automatically
- mutation and certification flags remain false on every result

### Post-Contact Redaction And Artifact Checks For A Later Phase

- `live_contact_attempted` and `live_contact_succeeded` flags are accurate.
- Raw payloads are not stored.
- Response summaries are redacted.
- Unsafe payloads produce `NO_GO`.
- Transient failures produce `INFRA_RETRYABLE` only under the criteria in this design.
- Identity ambiguity produces `RECOVERY_REQUIRED` or `NO_GO` as appropriate.
- Artifacts are reviewed by a human.
- Artifact write failures produce `NO_GO` or `BLOCKED` based on whether contact already occurred.
- Any detected raw secret, credential, API endpoint, runtime path, private ID, or over-broad dump aborts the pilot.

## Invocation Shape

Phase 8K does not add or validate a final command. The safe shape for a later pilot package is a non-executable
field template:

```text
pilot package shape only; not a runnable command

mode: <fake | non-contact-dry-run | live-read-only>
approval_reference: <approval-reference>
expected_branch: <expected-branch>
expected_commit: <expected-commit>
artifact_directory: <artifact-dir-provided-by-operator>
scenario_ids: [<allowed-scenario-id>]
query_families: [<allowed-query-family>]
hub_runtime_handles:
  - physical_label: <expected-physical-hub-label>
    kubeconfig_handle: <runtime-kubeconfig-handle>
    context_handle: <runtime-context-ref>
    credential_handle: <runtime-credential-handle>
expected_managed_cluster_set: <expected-managed-cluster-set-file-outside-git>
redaction_policy: <redaction-policy-version>
timeout_retry_budget: <bounded-read-only-budget>
```

The final Phase 8L shape must not include real paths, real cluster names, real API endpoints, real kubeconfig
locations, tokens, or private IDs. It must not be a copy-paste live command that can run accidentally.

## Artifact Contract For Pilot

The pilot artifact contract is provisional, redacted, and not a production JSON schema. Required fields:

- `artifact_version`
- `phase=8K/8L as appropriate`
- `mode=fake/dry-run/live-read-only`
- `branch`
- `commit`
- `clean_worktree`
- `approval_reference_redacted`
- `scenario_ids`
- `query_family_allowlist`
- `gate_status`
- `opt_in_flags`
- `runtime_handle_summary`
- `live_contact_attempted`
- `live_contact_succeeded`
- `real_execution_evidence`
- `live_certification_evidence=false`
- `mutation_enabled=false`
- `mutation_attempted=false`
- `query_plan_summary`
- `query_result_summary`
- `physical_identity_evidence`
- `logical_role_evidence`
- `managed_cluster_set_evidence`
- `read_prerequisite_evidence`
- `redaction_status`
- `decision`
- `retry_allowed`
- `manual_recovery_required`
- `first_blocking_reason`

Raw runtime inputs must never appear. The artifact may record live read contact in a later phase, but that is not live
ACM certification evidence.

## Evidence Acceptance Criteria

Acceptable evidence:

- sanitized gate status
- sanitized runtime handle presence summary
- sanitized query plan summary
- sanitized query result summary
- fingerprinted identity signals
- expected-vs-observed managed cluster set result
- ACM/MCE/MCH status categories
- backup/restore status categories, if used
- redaction pass/fail
- accurate live contact flags

Explicitly unacceptable evidence:

- raw kubeconfig refs
- raw API endpoints
- raw UIDs if private
- raw tokens/secrets/credentials
- private cluster IDs
- raw payload dumps
- shell command history
- unreviewed Agent summaries
- `live_certification_evidence=true`
- mutation evidence

Live contact evidence means the controller attempted or completed an allowlisted read. It is not certification
evidence, not switchover readiness proof, and not permission to run another phase.

## Decision Interpretation

`PASS` means only that the read-only pilot preflight criteria passed:

- no mutation
- redaction passed
- evidence complete for the read-only pilot objective
- live contact flags are accurate
- not release certification

`BLOCKED` applies to:

- missing approval
- missing runtime inputs
- missing gates
- invalid scenario/query
- unsafe before contact
- missing explicit opt-in

`NO_GO` applies to:

- identity mismatch
- managed cluster set mismatch
- unsafe post-contact payload
- redaction failure
- both hubs active if role evidence reaches that level

`RECOVERY_REQUIRED` applies to:

- ambiguous live state
- neither hub active if role evidence reaches that level
- manual inspection needed

`INFRA_RETRYABLE` applies only when:

- a transient read-only timeout/failure occurred
- no mutation occurred
- retry criteria are satisfied
- the previous contact was read-only
- a human approves any retry

## Abort Criteria

Hard aborts:

- dirty worktree unless explicitly allowed by design
- wrong branch/commit
- missing approval
- missing runtime handle
- unsafe runtime handle
- scenario not allowlisted
- query not allowlisted
- mutating verb present
- L0-L9 not all satisfied
- L10 used as mutation authorization
- `live_certification_evidence=true`
- `mutation_enabled=true`
- `redaction_required=false`
- raw secret/API endpoint/path/private ID detected
- client response unsafe
- unexpected managed cluster set
- hub identity mismatch
- both hubs active
- neither hub active
- artifact write failure
- CodeRabbit/reviewer blocker

Any abort before live contact is `BLOCKED` unless the condition is a hard safety mismatch that must be recorded as
`NO_GO`. Any abort after contact that prevents safe artifact publication is `NO_GO`.

## Manual Recovery And Retry Policy

- no automatic recovery
- no automatic switchover
- no automatic restore
- no automatic retry by Agent
- retry only when decision is `INFRA_RETRYABLE`, mutation is false, previous contact was read-only, and human
  explicitly approves
- retry evidence must be appended or superseded safely
- all `NO_GO` and `RECOVERY_REQUIRED` decisions require human investigation

There is no rollback procedure for Phase 8K because Phase 8K does not mutate and does not run. For a later read-only
pilot, rollback posture is "stop, preserve redacted evidence, and require human review." Any recovery action would be a
separate mutating design.

## Operator Checklist

- [ ] Repository state: expected branch, expected commit, clean worktree, Phase 8J code present.
- [ ] Validation state: Phase 8J tests and release helper tests green for the target commit.
- [ ] Approval state: pilot-specific human approval reference recorded outside Git.
- [ ] Runtime input state: hub handles, credentials, expected identities, and expected managed cluster set supplied
      outside Git.
- [ ] Scenario/query allowlist: only Phase 8K allowed scenarios and query families selected.
- [ ] Redaction policy: required, versioned, and reviewed before any contact.
- [ ] Artifact directory: caller-provided, outside committed paths, not the `.release` default.
- [ ] Dry-run/fake rehearsal: completed before live contact is considered.
- [ ] Live contact opt-in: explicit and reviewed only in a later phase.
- [ ] Post-run artifact review: human-reviewed before any next action.
- [ ] Abort decision review: `BLOCKED`, `NO_GO`, `RECOVERY_REQUIRED`, and `INFRA_RETRYABLE` interpretations accepted.

## Pilot Runbook Boundary

A future pilot runbook may contain:

- safe template placeholders
- prerequisites
- checks
- abort criteria
- expected artifact fields
- review procedure

It must not contain:

- real credentials
- private cluster values
- real kubeconfig paths
- raw API endpoints
- copy-paste live mutation commands
- automatic recovery steps
- Agent-only approval

Any future runbook-like operator instructions must stay outside protected operational runbooks unless the operator
explicitly approves those protected-file changes.

## Human Approval And Rollback Posture

- Human approval must be specific to the exact pilot mode, branch, commit, scenario allowlist, query allowlist, and
  artifact directory.
- Agent summaries are not approval.
- Approval expires when runtime inputs, branch, commit, query plan, or artifact directory changes.
- Rollback is not applicable to Phase 8K because no live action occurs.
- A later read-only pilot rollback posture is a stop-only posture: stop contact, preserve sanitized evidence, and
  require human review.
- Any command that changes cluster state is recovery or mutation and is outside this pilot.

## Audit Requirements Before Any Actual Pilot May Run

Before Phase 8L or any later live-contact pilot:

- independently audit the Phase 8K design
- confirm Phase 8J code and tests are green on CI
- review changed documentation guardrails
- verify no CodeRabbit/reviewer blockers remain
- verify no CLI/planner default wires live transport
- verify no live config loading path exists
- verify no kubeconfig reading path exists in the pilot package
- verify no environment credential inference exists
- verify no `oc`, `kubectl`, `ansible-playbook`, release adapter, or pytest live invocation exists
- verify scenario/query allowlists are conservative and catalog-aligned
- verify artifact redaction rejects unsafe payloads
- verify fake-backed or non-contact rehearsal is defined
- verify human approval and abort criteria are accepted

## Risk Register

| Risk | Impact | Mitigation | Blocking for Phase 8L |
| --- | --- | --- | --- |
| Accidental live contact without approval | Unauthorized live observation and trust loss | Require explicit opt-in flags, approval reference, L0 gate, and no implicit defaults | Yes |
| Wrong hub contacted | Evidence attributed to wrong physical hub | Require physical labels, runtime handle match, identity fingerprints, and mismatch aborts | Yes |
| Runtime handle leakage | Sensitive operational details in artifacts | Presence-only summaries, forbidden key checks, redaction required | Yes |
| API endpoint leakage | Private endpoints exposed | Reject or fingerprint endpoint-like values before artifact publication | Yes |
| Credential leakage | Credential compromise | Runtime-only handles, no environment inheritance, token/secret rejection | Yes |
| Broad query output | Unsafe or high-volume payload reaches artifacts | Query family allowlist, size bounds, raw payload ban, redaction chokepoint | Yes |
| False role inference | Unsafe role decision or incorrect PASS | Require fresh role evidence, ambiguity fail-closed, previous artifacts supporting only | Yes |
| Both hubs active | Split-brain-like state treated as safe | Map to `NO_GO`, abort, and require human investigation | Yes |
| Neither hub active | Active management state cannot be proven | Map to `RECOVERY_REQUIRED`, abort, and require human investigation | Yes |
| Managed cluster set drift | Pilot evidence no longer matches approved lab inventory | Exact match required; unexpected cluster policy is `block` | Yes |
| Redaction false negative | Unsafe data published | Reuse existing redaction checks, reject suspicious keys/values, human artifact review | Yes |
| Transient API instability | Misclassified pilot failure or retry loop | Bounded timeout/retry budget; `INFRA_RETRYABLE` only with human-approved retry | No |
| Agent overreach | Agent initiates contact or recovery | Controller-owned decisions; Agent cannot supply credentials or retry automatically | Yes |
| Operator approval ambiguity | Stale or vague approval authorizes wrong action | Pilot-specific approval reference tied to branch, commit, plan, and mode | Yes |
| Artifact write/retention mistake | Evidence lost or written to unsafe location | Caller-provided artifact directory, write check, no committed artifacts | Yes |
| Pilot interpreted as certification | Read-only pilot mistaken for release approval | `live_certification_evidence=false`, explicit wording, human review, no support claim | Yes |

## Phase 8L Entry Criteria

Hard entry criteria for Phase 8L:

- Phase 8K design accepted with READY recommendation
- Phase 8J code and tests green on CI
- independent audit of Phase 8K complete
- no CodeRabbit/reviewer blockers
- pilot checklist complete
- fake-backed rehearsal defined
- operator approval model defined
- runtime inputs model defined
- abort criteria accepted
- artifact review procedure defined
- no mutation scope
- no certification claim
- no automatic recovery
- human approval required before any live contact

Phase 8L is not broad live rollout. It must start with a fake-backed rehearsal or non-contact dry-run unless a separate
operator approval explicitly authorizes a later live-contact pilot.

## Documentation Integration

This design should be referenced by:

- `docs/development/lab-role-controller-read-only-backend-design.md`
- `docs/development/lab-role-controller-read-only-live-transport-design-review.md`
- `tests/test_documentation_guardrails.py`

No protected operational runbooks are modified by this design.

## Recommendation

Recommendation: READY_FOR_PHASE_8L_READ_ONLY_LIVE_PREFLIGHT_PILOT_DRY_RUN
