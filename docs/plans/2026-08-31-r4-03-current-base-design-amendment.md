# R4-03 Decommission Completion — Current-Base Design Amendment

**Date:** 2026-08-31
**Branch:** `docs/r4-03-current-base-amendment-2026-08-31` (base `origin/ansible` @ `74268192`)
**Status:** operator-approved design amendment; implementation plan authored; runtime implementation not authorized
**Amends:** [`docs/plans/2026-07-29-decommission-completion-design.md`](2026-07-29-decommission-completion-design.md)
(the "July design"), which remains the approved historical design and is not modified.

## 1. Authority and scope

This amendment revalidates the approved July R4-03 decommission-completion design against
the current `origin/ansible` head `74268192` and incorporates every later repository
finding and architectural change that affects the R4-03 boundary. Where this amendment and
the July design disagree, **this amendment is authoritative**; everywhere else the July
design stands and is cross-referenced rather than restated.

Governing authorities, in the `AGENTS.md` hierarchy order:

1. `AGENTS.md` at `74268192`.
2. `thermos-resolution-plan.md` R4-03 row and Area C acceptance requirements
   (`thermos-resolution-plan.md:1992`, `:2113-2130`, `:2299`), the R4-03 findings table
   (`:1966-1971`), and the GLM-H6 row (`:2576`).
3. The July design; the R4-04 current-base amendment
   (`docs/plans/2026-08-27-r4-04-current-base-design-amendment.md`) and R4-04
   implementation plan
   (`docs/plans/2026-08-27-r4-04-migration-evidence-implementation-plan.md`) as the
   consumer contract for the shared strict-read primitive; the parity matrix, behavior
   map, and coexistence policy.
4. Current source and tests at `74268192`.

Scope of this slice: **this one document.** No production code, tests, RBAC, manifests,
Helm, release-validation, lab-controller, tracker, or protected files change.
`thermos-resolution-plan.md` is deliberately untouched; one stale tracker sentence found
during revalidation is recorded in §19 for a later tracker-reconciliation slice
rather than edited here.

## 2. Current-base revalidation record

- Base: `origin/ansible` @ `74268192` (merge of PR #279), fetched fresh; worktree
  `.claude/worktrees/r4-03-amendment-2026-08-31` created from that exact SHA; clean tree.
- Method: three independent full source traces (Python `modules/decommission.py` +
  `modules/finalization.py` + `lib/kube_client.py`; collection `roles/decommission/` +
  `plugins/modules/acm_k8s_read_outcome.py`; R4-04 planning documents + tracker + RBAC +
  guardrail surfaces), each cross-checked against first-hand reads of the July design,
  the tracker sections, `lib/run_record.py`, `lib/rbac_validator.py:236-277`, and the
  full `acm_k8s_read_outcome` module. Generated-graph analysis was not used; every
  relationship reported here was verified directly against source and tests, which the
  `AGENTS.md` evidence rules treat as the stronger form of the same obligation.
- Code changes on the R4-03 surfaces since the July design are limited to four commits
  (RunRecord facade #215, SSA-01 identity binding, hub-client error sanitization,
  advisory ConfigMap reads). Everything merged after the R3-02 implementation (PR #273)
  is documentation-only, including the R4-04 planning documents (PR #275). **No R4-04
  implementation exists on `origin/ansible`.**

## 3. July assumptions superseded or retained

| July assumption | Current state | Disposition |
| --- | --- | --- |
| "Untracked in `thermos-resolution-plan.md`" (July header) | R4-03 is tracked at `thermos-resolution-plan.md:1992` with findings R4-C1..R4-C6 | Superseded; tracker row is the authorization record |
| `lib/kube_client.py:724-748` maps list 404 → `[]` | Behavior current, location moved: the map is at `lib/kube_client.py:776-778` inside the extracted `_list_custom_resources_raw` (`:729-798`) behind the public `list_custom_resources` (`:691-727`) | Retained with corrected reference |
| Python list reads may be incomplete | Python `_list_custom_resources_raw` already drains `continue` tokens completely (`lib/kube_client.py:741-796`); the strict primitive's pagination clause is therefore a *completeness-proof and failure-classification* requirement, not new paging machinery on the Python side | Narrowed |
| No shared read-outcome seam exists in the collection | `acm_k8s_read_outcome` exists since 2026-08-26 (R3-02, commit `7258e9f3`) with two merged consumers | Superseded; §6.2 extends this seam |
| Decommission has no durable state (implicit) | Confirmed and sharper: `modules/decommission.py` contains zero `StateManager`/checkpoint references, and `run_decommission` (`acm_switchover.py:933-976`) receives `state` and never uses it; the collection role has zero checkpoint/`operational_data` usage | Retained; §13 makes the wiring explicit |
| Observability facts are preflight booleans | Now typed: `RunRecord`/`HubFacts` persist `primary_has_observability` **and** `secondary_has_observability` (`lib/run_record.py`), but `Finalization` receives only the primary fact (`acm_switchover.py:911`) | Narrowed; §9 classifies both facts as informational-only for the gate |
| Design predates the RunRecord facade | `lib/run_record.py` now owns typed named operations over `StateManager` config keys, with guardrail tests locking the seam | New constraint; §13 routes new durable fields through the same facade pattern |
| Collection MCH wait is a bounded wait that warns | Sharper defect than recorded: `delete_multiclusterhub.yml:53` sets `failed_when: false` and the `until` (`:46-51`) applies Jinja's `default([])` filter to `resources`, so a persistent read/auth failure yields **no** `resources` key → empty → the wait "succeeds" on the first attempt with no warning | Retained and strengthened; §7/§16 |
| `acm_uid_guarded_delete` is the only guarded-delete module planned | The accepted R4-04 plan adds a second, Restore-scoped `acm_restore_guarded_mutation` (UID+resourceVersion, patch+delete; plan Task 4) | Retained with a §8 coordination rule |
| R4-C6 needed a new design (task-brief premise) | **False at this head**: the July design already contains the complete §1a owner-chain contract, goal 5, RBAC table, and the 20-case operator-identity matrix — R4-C6 was folded in during the PR #204 review rounds | Premise corrected; §10 revalidates rather than re-derives |

Everything else in the July design — the §1 phase table, §1a provenance and owner-chain
contract, the collection guarded-delete boundary, §2 refusal aborts, §3 strict-list
contract, §4 destination gate, §5/§6 behavior sections, §7 RBAC table, the testing
matrix, and the 12 acceptance criteria — is **retained** and revalidated below.

## 4. Current finding disposition table

Classification per finding against `origin/ansible` @ `74268192`. "Open as described"
means the tracker text is accurate for current source.

| Finding | Disposition | Current evidence |
| --- | --- | --- |
| R4-C1 (MCH completion fails open) | **Open as described** in Python; **open and worse than recorded** in the collection | Python: lingering non-operator pods warn only (`modules/decommission.py:444-448`), MCH CR never re-read after DELETE, `decommission()` returns `True` (`:97-98`). Collection: `delete_multiclusterhub.yml:53` `failed_when: false` + `default([])` in the `until` (`:46-51`) lets an unverifiable read satisfy the wait silently; the warn task (`:58-79`) uses the same default and also stays silent. Two collection unit tests pin the fail-open (`ansible_collections/tomazb/acm_switchover/tests/unit/test_decommission_role_contracts.py:336-350`; `.../tests/unit/test_ansible_resilience_contracts.py:485`). |
| R4-C2 (refusal → success) | **Open as described** (Python-only, matching the tracker's surface column) | Every per-step refusal logs `"Skipped: ..."` and falls through to `return True` (`modules/decommission.py:70-92`, `:97-98`); only the top-level prompt returns `False` (`:60-66`). The suite *positively pins* the defect: `tests/test_decommission.py:143-159` declines every destructive step and asserts `result is True`; its name describes an "extra MCH confirmation" that does not exist in source. Collection is non-interactive by design (`defaults/main.yml:7-10`, confirmed-gate at `roles/decommission/tasks/main.yml:2-9`) — refusal semantics do not apply, but outcome parity does (§7). |
| R4-C3 (no UID-preconditioned DELETE / absence proof) | **Open as described**, both form factors | Zero hits for `preconditions`/`expected_uid`/`V1DeleteOptions` in the Python tree; `delete_custom_resource` (`lib/kube_client.py:1024-1075`) passes no body. All three collection deletes are name-only `kubernetes.core.k8s state: absent` (`delete_observability.yml:16-29`, `delete_multiclusterhub.yml:17-31`, `delete_managed_clusters.yml:149-161`); `acm_uid_guarded_delete` exists only in design prose. MCO pod waits are namespace-wide with no selector (`modules/decommission.py:149-161`; `get_pods` supports `label_selector`, `lib/kube_client.py:1292`, unused here). The Python MCO CR is never re-read after DELETE (`:163-166` re-checks pods only), and the caller-side 404 arm (`:139-143`) is dead code because the `@api_call(not_found_value=True)` decorator (`:1024`, `:188-190`) already swallows 404. |
| R4-C4 (404 → `[]` inventory blindness) | **Open, reference corrected** | The map is now `lib/kube_client.py:776-778` (see §3). Same conflation on named reads: `get_custom_resource` maps 404 → `None` (`:594-638`). `_delete_managed_clusters` empty-return at `modules/decommission.py:174-176`; the base test fixture (`tests/test_decommission.py:25-31`) returns `[]` from the list mocks — exactly the error-as-absence shape. One nuance: the Hive `preserveOnDelete` gate is genuinely fail-closed for non-404 errors (`:258-266`) but a missing Hive CRD reads as "no ClusterDeployments" through the same 404 map. |
| R4-C5 (no destination-observability gate) | **Open as described**, both form factors | Python: the only gate is source-side (`modules/finalization.py:211`); `Finalization.__init__` has no `secondary_has_observability` parameter and `acm_switchover.py:911` passes only the primary fact, although `RunRecord` records both. Collection: the decommission role never touches `acm_switchover_hubs.secondary`, has no acknowledgement variable, and never calls `acm_k8s_read_outcome`. |
| R4-C6 (prefix-only operator-Pod identity) | **Open as described**; design already complete (July §1a) | Python exclusion is `startswith(ACM_OPERATOR_POD_PREFIX)` (`modules/decommission.py:427`; constant `lib/constants.py:97`). Collection: `rejectattr('metadata.name', 'match', '^multiclusterhub-operator')` three times (`delete_multiclusterhub.yml:48,65,76`). Tests lock names-only fixtures (`tests/test_decommission.py:677-680`) and a bare substring assertion (`test_ansible_resilience_contracts.py:479`). New current-base facts §10 adds: the prefix constant has **no collection mirror** in `module_utils/constants.py` and is **absent from `tests/test_constants_parity.py` `CONSTANT_PAIRS`** — two independent literals free to drift. |
| GLM-H6 (duplicated Python MCO teardown) | **Open as described** | `modules/finalization.py:1003-1088` vs `modules/decommission.py:107-166` share the list/guard/delete/dead-404/dry-run/wait/re-check skeleton line-for-line with the same hardcoded API identifiers. Three semantic differences — finalization's extra preconditions (`:1005-1013`), GitOps marker recording only in finalization (`:1030-1043`), and different timeout-failure message text (`:1084-1088`) — plus trivial mechanical deltas (exception-status access via `getattr` vs direct attribute, differing 404/wait log wording) that carry no behavioral weight but must not be silently lost by consolidation. Finalization imports `Decommission` (`:65`) and instantiates/invokes it at `:1138-1141`. |

No finding is resolved, superseded, or in conflict; none is narrowed in *scope* (R4-C4's
narrowing is a citation correction plus the discovery that Python pagination is already
complete). No manufactured problems: each disposition above is source-verified at this
head.

## 5. Current architecture and ownership

Facts the implementation plan must build against, established since July:

1. **RunRecord facade.** Durable cross-phase facts flow through typed named operations on
   `lib/run_record.py::RunRecord` over `StateManager` config keys; guardrail tests lock
   that seam. New R4-03 durable fields (teardown records, operator identity) follow the
   same pattern: typed accessors, no ad-hoc string keys at call sites (§13).
2. **Decommission is stateless today.** Neither `modules/decommission.py` nor the
   collection decommission role persists anything; the collection's summary artifact
   hard-codes `status: pass` (`roles/decommission/tasks/main.yml:55-76`). The July
   design's durable phase machine is net-new wiring in both form factors, not a
   retrofit of existing records.
3. **The collection strict-read seam exists and is owned code.**
   `plugins/modules/acm_k8s_read_outcome.py` is R3-02-delivered, single-read,
   sanitized, three-outcome (`ok` / named-GET `not_found` / `error`), with two merged
   fail-closed consumers (`roles/primary_prep/tasks/scale_observability.yml`,
   `roles/activation/tasks/apply_immediate_import.yml`) and unit + integration lanes.
   Extending it is a behavior-touching change to merged code, not greenfield (§6.2).
4. **R4-04 is a blocked consumer.** Its implementation plan's Task 0 Step 2 hard-gates on
   a merged R4-03 strict primitive in both form factors and instructs "STOP R4-04
   implementation" otherwise (plan `:69-79`); its amendment fixes the order (R4-03
   first, amendment `:732-737`) and forbids a competing read algebra (criterion 27,
   `:928-932`).
5. **Layering invariant.** Phase eligibility and durable transition verification belong
   to the workflow/runner layers (`AGENTS.md`); `Decommission` remains a module invoked
   by the CLI path (`run_decommission`) and by `Finalization`. The §7 outcome contract
   is therefore expressed in `Decommission`'s return/raise surface, and callers map it —
   handlers do not self-gate on phases.

## 6. Normative shared strict-read contract

The July §3 contract is retained in full — outcome algebra
(`items` / `crd_absent` / `namespace_absent` / `object_absent` / `error`), the two-404s
rule, error-is-never-absence, no silent partial aggregation, bounded calls, sanitized
errors, read-only, independent implementations held equal by parity vectors. The
tracker's shared 404 algebra rows (`thermos-resolution-plan.md:2119-2120`, `:2299`) bind
unchanged. This section states only the current-base deltas and decisions.

### 6.1 Python surface

- Add a strict read/list surface in `lib/kube_client.py` returning a typed outcome
  (the July name `list_custom_resources_strict` or equivalent; exact naming is an
  implementation-plan decision) plus the matching strict named-object GET.
- The existing `list_custom_resources` / `get_custom_resource` behavior is **unchanged
  for legacy callers**; consumers migrate per-slice (decommission here, R4-04's
  consumers in R4-04). No repo-wide migration (July non-goal retained).
- Pagination: the raw helper already drains `continue` tokens
  (`lib/kube_client.py:741-796`). The strict variant's obligations are
  **classification**, not new paging: a page failure fails the whole read as `error`
  (never returns the partial prefix), an expired `continue` token restarts the whole
  read within the caller's bounded budget, and success asserts that the final response
  carried no outstanding continuation. `max_items` truncation is incompatible with a
  strict inventory read and is not offered on the strict surface.
- Malformed-response rule: `items` missing or non-list, non-mapping members, or an
  undecodable body → `error`, per July.

### 6.2 Collection surface — extend `acm_k8s_read_outcome` (decision, not option)

R4-03 **extends the existing module**; no second collection read abstraction is created
(consistent with R4-04 amendment `:726-730` and criterion 27). The extension is:

1. **List completeness.** List mode follows `continue` tokens to exhaustion; any page
   failure or an outstanding continuation at exit → `error`. `ok` for a list therefore
   asserts a positively complete inventory. The current module makes one unpaginated
   request and supplies no `limit`, so truncation is latent rather than reachable today.
   The extension makes completeness load-bearing when it adds paging and prevents a
   future partial response from being reported as `ok`.
2. **Positive kind-absence.** Add `read_status: kind_not_served`,
   returned only on a *positive* discovery determination that the API
   group/version/kind is not served (the dynamic-client resource-lookup miss after a
   successful discovery fetch). Discovery calls that time out, are unauthorized, or
   return unparseable data remain `error` — the implementation must prove the
   positive-determination property, not infer it from exception type alone. This is a
   deliberate reclassification of the current `error` result for that exact positive
   miss, not a behavior-additive status: the existing unit and runtime integration
   expectations for the positive miss must be inverted to `kind_not_served`. The
   module's `RETURN.read_status` choices and descriptions must change with the code and
   pass the collection sanity lane.
3. **Namespace absence is composed, not added.** A caller needing `namespace_absent`
   issues a named GET of the `v1` `Namespace`; `not_found` there is the positive proof.
   The module gains no namespace-probing mode (KISS; the algebra outcome exists at the
   call-site level).

Existing-caller impact, verified at this head: both merged consumers read always-served
core kinds (`Pod`, `ConfigMap`), so `kind_not_served` is unreachable for them;
`scale_observability.yml` already fails closed on any non-`ok` status (`:59-73`), and
`apply_immediate_import.yml` handles `not_found` distinctly (`:84-94`). The list
strengthening can only convert a previously silent partial inventory into `error`,
which both consumers treat fail-closed. The implementation plan must nonetheless rerun
the runtime consumer lanes that can falsify this claim
(`tests/integration/test_r3_02_compactor_runtime.py` and
`tests/integration/test_r3_02_activation_runtime.py`), plus the read-outcome unit and
runtime integration lanes. The static role-contract tests remain useful structural
coverage but are not consumer-regression evidence by themselves. Extend the read-outcome
lanes with pagination and `kind_not_served` vectors, including inversion of the current
positive discovery-miss expectations.

### 6.3 Cross-form-factor mapping and parity

| Normative outcome | Python strict surface | Collection surface |
| --- | --- | --- |
| success, complete inventory | `items` (complete) | `ok` + complete `resources` |
| named-object absence after successful discovery | `object_absent` | `not_found` (named GET) |
| positive kind-not-served | `crd_absent` | `kind_not_served` |
| positive namespace absence | `namespace_absent` | composed: Namespace GET → `not_found` |
| authorization / transport / server / API failure | `error` | `error` |
| malformed or unexpected response | `error` | `error` |
| truncation / incomplete pagination | `error` | `error` |
| timeout / retry exhaustion | `error` (caller-bounded) | `error` (caller-bounded) |

For destructive and migration-safety consumers: `error != absence`, and partial
inventory never qualifies as complete inventory. One shared parity-vector set exercises
this table in both form factors (July §3 "tests are shared across consumers"), sized to
satisfy the R4-04 Task 0 Step 2 checklist verbatim: true empty, 404/discovery failure,
malformed `items`, transport/auth failure, complete pagination. The two implementations
share no runtime code (independence contract).

## 7. Decommission outcome and refusal contract

July §2 is retained. This amendment makes the outcome vocabulary exact, because current
source conflates four distinct states into `return True`:

| Substep outcome | Meaning | Effect on overall result |
| --- | --- | --- |
| `not_requested` | Step explicitly disabled/unrequested by configuration (e.g. `has_observability` false → no MCO substep) | Neutral; recorded in the summary |
| `precondition_noop` | Requested, and the July §1/§3 rules prove no mutation is needed (no teardown record + positive absence) | Neutral; counts as satisfied |
| `completed` | Requested, mutated, full completion proof obtained | Satisfied |
| `refused` | Interactive operator declined a requested destructive substep | **Overall failure**: stop remaining substeps, print completed/refused/not-attempted summary, non-zero exit |
| `failed` | Execution or verification failure | **Overall failure**, existing error semantics |

Rules:

- A refusal never falls through to overall success; `tests/test_decommission.py:143-159`
  (which pins the opposite and is named for a confirmation that does not exist) is
  replaced by tests asserting this table.
- Non-interactive (`interactive=False`, integrated decommission, `--non-interactive`)
  never prompts; every requested substep runs. Dry-run previews the requested
  substeps and records **no outcome at all** — neither `refused`, `completed`, nor
  any other value — consistent with §14: a later live run trusts nothing from a dry
  run.
- Collection parity: the role stays non-interactive with its confirmed-gate; parity is
  at the *outcome* level — the summary artifact must report the real aggregated
  outcome instead of the current hard-coded `status: pass`
  (`roles/decommission/tasks/main.yml:55-76`), and a failed or unverifiable substep
  fails the play rather than degrading to a warning (§4 R4-C1 collection evidence).

## 8. Guarded deletion and final-state proof

July §1 (per-resource phase machine), the July "Collection-owned UID-preconditioned
deletion boundary" section — hereafter the **July deletion boundary**, which defines
the collection `acm_uid_guarded_delete` contract — and the "what `completed` actually asserts" freshness rule are retained
unchanged, including: identity captured and forced-durable **before** DELETE; server-side
`V1DeleteOptions(preconditions=V1Preconditions(uid=expected_uid))`; 409/412 fatal, never
retried name-only; bounded CR-absence poll; different-UID replacement fatal and left
intact; final live GET before success; `completed` carries `observed_at` +
per-resource `resourceVersion` and is necessary-but-never-sufficient for later
destructive decisions.

Current-base decisions:

1. **UID-only preconditions stand for R4-03.** The July non-goal (no resourceVersion
   preconditions on decommission deletes) is retained: UID binds identity, which is the
   safety property here; resourceVersion adds a liveness constraint these teardown
   deletes do not need. This deliberately differs from R4-04's Restore-scoped
   `acm_restore_guarded_mutation` (UID **and** resourceVersion, patch+delete), whose
   evidence-transaction semantics need spec-freshness. The two modules have different
   ownership boundaries and remain separate; within the collection form factor, shared
   client-construction/sanitization mechanics may live in `module_utils` (intra-form-
   factor DRY is permitted and encouraged; cross-form-factor imports remain forbidden).
2. **One Python preconditioned-delete primitive.** R4-04's plan Task 4 sketches
   `delete_custom_resource_preconditioned(..., uid, resource_version, ...)` in
   `lib/kube_client.py`. Whichever slice merges first implements the single Python
   primitive with `uid` required and `resource_version` optional; the other slice
   consumes it. R4-03 callers pass UID only. Two parallel preconditioned-delete
   primitives in one form factor would violate the DRY ownership rule.
3. **Kubernetes API basis.** The July citations stand, with the July "citation
   provenance limitation" retained verbatim: before implementation, the Python-client
   and `kubernetes.core` references must be pinned to the exact depended-upon versions;
   until then no fail-closed rule may be relaxed on their basis. Repository usage today
   contains no preconditioned delete in either form factor (§4 R4-C3), so there is no
   current-usage counter-evidence to reconcile.
4. **Dead-code cleanup is in scope for the implementation.** The caller-side 404 arms
   made unreachable by `@api_call(not_found_value=...)` (e.g.
   `modules/decommission.py:139-143`) are removed or made real when the strict/guarded
   paths replace these call sites — behavior currently asserted only via decorator-
   bypassing mocks (`tests/test_decommission.py:190-204`) must be re-asserted against
   the real seam.

## 9. Destination-observability prerequisite

July §4 is retained in full: fresh strict source-hub read immediately before the source
MCO deletion substep (never the preflight boolean); fresh destination-hub check via the
secondary client; two distinguished failure reasons (positively-absent vs unverifiable);
`--acknowledge-observability-not-migrated` accepted **only** against a positively
verified absent destination, rejected when the gate would pass anyway; standalone
decommission unaffected (no destination client); collection role mirrors the gate when a
destination kubeconfig/context is provided, with a boolean ack variable.

Current-base clarifications:

- `RunRecord`/`HubFacts` now persist `secondary_has_observability`, but that fact is
  **informational only** and never a gate input — the gate consumes fresh strict reads
  on both hubs at mutation time, per the tracker Area C requirement ("source
  observability is re-read immediately before deletion (never trusting the preflight
  boolean)", `thermos-resolution-plan.md:2121-2123`). Plumbing the recorded fact into
  `Finalization` is not a substitute and is not required by this design.
- No waiver or compatibility mode beyond the single acknowledgement flag exists or is
  invented: current authorities define no "observability intentionally unsupported"
  contract, so the only supported states are gate-pass (destination positively
  present), acknowledged continuity end (destination positively absent + flag), and
  blocked (everything else, including every `error` and ambiguous mixed state).
- Resume: a prior run's gate result is progress, not evidence — on resume the gate
  re-runs its fresh reads before the deletion substep, unconditionally (§13).

## 10. Identity-bound MCH Pod classification (R4-C6)

The July §1a contract — OLM CSV-derived operator Deployment provenance, forced-durable
identity or `operator_identity_unavailable` outcome before MCH DELETE, the
`Pod → controller ReplicaSet → exact recorded Deployment UID` chain with every
missing/malformed/ambiguous/multiple/replaced link blocking, rolling-update handling,
the namespace-absence entailment exception, memoization bounds, and sanitization —
is **retained unchanged**, together with the 20-case operator-identity matrix from
the July Testing section. Both were revalidated here against the current object
topology: the ownerReference/controller model
and UID lifetime semantics it cites are unchanged Kubernetes API contracts, and the
CSV audit covering ACM 2.11–2.17 remains the widest ACM range any current repository
authority claims (the repo's version-dependent behavior references stop at "2.14+";
no authority asserts support beyond the audited range). Outside the audited range the
runtime contract fails closed, per July.

Current-base additions:

1. **Constants/parity gap.** `ACM_OPERATOR_POD_PREFIX` (`lib/constants.py:97`) has no
   mirror in the collection's `module_utils/constants.py`; the collection hardcodes the
   regex literal three times (`delete_multiclusterhub.yml:48,65,76`); and the pair is
   absent from `tests/test_constants_parity.py::CONSTANT_PAIRS`. Under this design the
   prefix is demoted to a supplementary diagnostic (July §5) — but *whatever diagnostic
   use survives* must be a mirrored constant held by the parity test, and the triple
   Jinja duplication must collapse into the collection-owned classification boundary
   (July §6) rather than remaining three drift-prone literals.
2. **Test displacement.** The current pins that preserve the defect —
   `tests/test_decommission.py:645-702` (names-only fixtures) and
   the collection's `tests/unit/test_ansible_resilience_contracts.py:479` (bare
   substring) — are
   replaced by the July matrix; the `failed_when: false` pins
   (`test_ansible_resilience_contracts.py:485`,
   `test_decommission_role_contracts.py:336-350`) are inverted, since the fail-open
   wait they protect is removed by §7/§8.
3. **Classification boundary placement.** Python: the narrowly scoped owner-chain
   helper (July §5) lives with the teardown owner (§11), not in `lib/kube_client.py`,
   which stays a transport/read layer. Collection: the July §6 module/module_utils
   boundary stands so Jinja filtering can never be the safety decision.

## 11. MCO teardown ownership (GLM-H6)

The duplication is confirmed current (§4). The consolidation rule:

1. **`Decommission` owns the one Python MCO teardown algorithm.** The July §1 phase machine
   (strict lookup → record → UID-preconditioned DELETE → CR-absence proof →
   selector-scoped bounded drain → final verification) is implemented exactly once, in
   `modules/decommission.py`, replacing both current copies.
2. **Caller-specific semantics are explicit parameters/wrappers, not a second copy.**
   Preserved differences, modeled as follows:
   - *Preconditions* (`self.primary` present, `old_hub_action == "secondary"`,
     `primary_has_observability` step gating): stay in `Finalization` before invoking
     the shared teardown — caller policy, not algorithm.
   - *GitOps marker recording* (`modules/finalization.py:1030-1043`): a caller-supplied
     option on the shared teardown API (flag or callback; exact shape is an
     implementation-plan decision). Finalization enables it; direct decommission does
     not — preserving today's observable difference.
   - *Phase/checkpoint behavior*: finalization's `state.step("disable_observability_on_secondary")`
     wrapper (`modules/finalization.py:211`) remains caller-side; the teardown's own
     durable per-resource records (§13) are algorithm-owned and shared.
   - *Outcome mapping*: finalization keeps raising `SwitchoverError` with its
     finalization-specific message context; direct decommission maps outcomes per §7.
     Caller-distinct failure text is caller-supplied context, not a forked algorithm.
   - *Interactive vs integrated*: the §7 contract already separates them
     (`interactive` reaches only the CLI path; `Finalization` invokes with
     `interactive=False` as today, `modules/finalization.py:1141`).
   - *Dry-run*: single shared behavior (§14) — both callers get identical preview
     semantics, as they already do.
   - *Evidence prerequisites*: the §9 destination gate binds to the integrated path
     (which has a destination client); standalone decommission is unaffected. R4-04's
     future evidence/cleanup gate (R4-D4) composes in front of the integrated call and
     is out of R4-03 scope.
3. **DRY boundary limit.** Consolidation is Python-internal. The collection role keeps
   its independent implementation held equal by parity vectors; GLM-H6 authorizes no
   cross-form-factor sharing.

## 12. Python/Collection parity contract

- Identical outcome algebras: §6 read outcomes, §7 decommission outcomes, §8 deletion
  phase machine and `changed`/`would_change` reporting, §10 pod-classification reason
  codes. Shared parity vectors, independent implementations, no cross-imports.
- Mirrored constants: any surviving operator-prefix diagnostic, the MCO drain label
  selector (July §1 promotes the observability selector to a shared constant; the
  collection's mirrored observability constants already exist in
  `module_utils/constants.py` and are parity-tested), and new stable reason-code
  vocabularies — all added to `tests/test_constants_parity.py`.
- Documentation surfaces: the parity matrix decommission row must replace its current
  "warns if ACM workload pods remain" text with the fail-closed completion contract.
  In the behavior map, update the existing `modules/decommission.py` →
  `roles/decommission/` row to identify the guarded-delete, durable-phase, and
  classification boundaries, and replace the generic `lib/kube_client.py` target with
  the Python strict-read → collection `acm_k8s_read_outcome` mapping. The strict-read
  seam also gains a documented parity row. Documentation-only here; no edits in this
  slice.
- Intentional divergences (each already contract-backed, restated for clarity): refusal
  prompts exist only in Python (collection is non-interactive with a confirmed-gate);
  `namespace_absent` is a composed call-site outcome in the collection rather than a
  module status; Python dry-run vs Ansible check-mode mechanics differ per §14 while
  observable guarantees match.

## 13. State, checkpoint, and resume contract

Durable fields introduced by R4-03, all net-new (§5 fact 2), all written through the
typed facade pattern (§5 fact 1) on the Python side and checkpoint `operational_data`
on the collection side (July deletion boundary):

| Field | Schema | Producer | Consumer | Written | Binds | Class | Freshness | Missing/malformed | Reset |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Teardown record, per resource | key `apiVersion/kind/namespace/name` → `{expected_uid, phase}` plus mandatory `{observed_at, resource_versions}` when `phase == completed` (July §1 phase table) | teardown owner (§11) | reruns; integrated teardown's fresh live gate | forced-durable before DELETE and at every phase transition | exact CR identity via UID | intent + progress; `completed` additionally evidence | `completed` is proof at its final-read instant only; every later destructive decision re-proves live | fail closed before any mutation or clean-skip decision (July §1) | Python `--reset-state` or collection full `checkpoint.reset` destroys it; collection `reset_from` preserves `operational_data` and therefore the record |
| `operator_deployment` | July §1a schema (CSV + Deployment namespace/name/UID + capture metadata) | MCH teardown, before MCH DELETE | every drain/final-verification pass | one forced-durable write before DELETE | exact operator Deployment UID, bound to the enclosing MCH record key/UID | recorded identity expectation | immutable for the record's lifetime and never rebound; every pass strictly re-reads the located Deployment and requires the live UID to match, with positive namespace absence the only retained exception | fail closed; DELETE not issued if the write fails; later absence, replacement, or unverifiable read is `recovery_required` | Python `--reset-state` or collection full `checkpoint.reset` destroys it; collection `reset_from` preserves it |
| `operator_identity_unavailable` | July §1a schema (reason code + capture metadata) | same | same | same | enclosing MCH record | evidence (negative) | immutable; never silently upgraded by rediscovery | exactly one of the two outcomes must exist; both/neither/partial is malformed → fail closed | Python `--reset-state` or collection full `checkpoint.reset` destroys it; collection `reset_from` preserves it |

No other durable state is added. Deliberately **not** persisted: the §9 gate result
(re-proven fresh on every run), refusal events (they end the run; the summary is
output, not state), and dry-run observations (§14).

**Reset limitation (explicit).** Python `--reset-state` removes the state file and the
collection's full `checkpoint.reset` rebuilds the checkpoint with empty
`operational_data`; either destroys teardown records. A post-reset rerun that finds a CR
absent is indistinguishable from never-attempted and takes the clean-skip path; the
anti-laundering guarantee of the July §1 phase table holds only while the record survives.
Collection `reset_from` is different: it prunes completed phases while retaining
`operational_data`, so it must retain and revalidate these records rather than laundering
them. General reset hardening (`--reset-state` under lock, narrowed `--force`) is owned by
R4-05 (R4-E5), and the cross-slice coordination is recorded in §17. The operator-facing
consequence of a **full** reset must be stated in the implementation's operator
documentation: resetting state between a failed drain and its rerun forfeits the drain
obligation's memory.

Resume paths re-prove every live destructive precondition: recorded phases resume
*obligations*, never conclusions (July §1 phase table); the §9 gate and the §10 final
classification always run against live state.

Standalone-decommission wiring: `run_decommission` already receives `state`
(`acm_switchover.py:936`) and must pass it into `Decommission`; collection execute-mode
decommission requires checkpointing so the identity map is durable before the first
DELETE (July deletion boundary) — both are net-new wiring the implementation plan must task
explicitly.

## 14. Dry-run and check-mode contract

**Python `--dry-run`** (July §5, retained): performs the strict provenance, inventory,
and owner-chain reads read-only; reports the predicted blocker set; issues no DELETE;
persists **no** authoritative teardown transition (no record creation, no phase write,
no operator-identity persistence); claims no change. A later live run trusts nothing
from a dry run.

**Ansible check mode and execution mode** — two layers, both specified:

1. *Mode gating (existing pattern).* The role's `acm_switchover_execution.mode`
   dry-run gate remains the primary operator-facing preview for destructive tasks:
   they announce rather than mutate. The current role still performs live reads before
   those gates. Default `has_observability: auto` reads the observability Namespace, and
   RBAC validation performs live SelfSubjectAccessReviews unless explicitly skipped.
   Implementation must preserve their read-only character and review them with the new
   strict-read/check-mode paths. Dry-run performs no mutation and writes no checkpoint.
2. *Native check mode (currently unhandled in the role).* Every new module supports
   native check mode: `acm_uid_guarded_delete` stops after the live read + UID
   validation and returns `changed: false` with explicit `would_change` (July deletion boundary,
   check-mode step); `acm_k8s_read_outcome` continues to perform its read in check mode (it is
   read-only by contract — existing tested behavior); the classification boundary
   module is read-only. Role wiring must be native-check-mode-safe end to end: no
   checkpoint `operational_data` transition is written in check mode, no task reports
   `changed: true`, and prediction is published only as `would_change`. The
   implementation plan adds the missing `ansible_check_mode` handling wherever a task
   would otherwise mutate or persist.

In neither preview form may simulated identity, progress, or completion ever be
readable by a later live run. Accurate `changed` reporting: `changed: true` only after
an accepted intended-UID mutation plus the full completion proof (the July deletion boundary's
execute-mode reporting rules).

## 15. RBAC impact

**RBAC contract change is required at implementation; no RBAC artifact changes in this
slice.** Verified current state: Python decommission RBAC
(`lib/rbac_validator.py:236-277`) grants CR list/delete, `namespaces get`, Hive
`clusterdeployments list`, and ACM/observability-namespace `pods get/list` — no
Deployments, ReplicaSets, or CSVs. The July §7 least-privilege table therefore stands
as the future surface: ACM-namespace `apps deployments get`, `apps replicasets get`,
`operators.coreos.com clusterserviceversions get/list`, retained `pods list` and
`namespaces get`; no speculative `list` on Deployments/ReplicaSets, no `watch`.

The future standalone-decommission surface also adds `get` on all three deleted CR
resources: `cluster.open-cluster-management.io/managedclusters`,
`operator.open-cluster-management.io/multiclusterhubs`, and
`observability.open-cluster-management.io/multiclusterobservabilities`. The current
standalone tables and optional decommission ClusterRole grant those CRs `list/delete` or
`delete` only. The named-object reads and final live absence proof retained by §6/§8
cannot succeed under that grant, and scoped LISTs are not substituted for the named-GET
contract.

Amendment addition: the §9 destination gate reads the **destination** hub (MCO CR
list/GET + observability namespace GET through the secondary client). The
implementation plan must verify whether the existing secondary-hub
preflight/validator grants already cover those reads and, if not, extend every RBAC
surface named by `AGENTS.md` together. The coordinated implementation task must cover the
Python `DECOMMISSION_CLUSTER_PERMISSIONS` table, the collection
`DECOMMISSION_CLUSTER_PERMISSIONS` decommission-only table and task wiring, root
`deploy/rbac/extensions/decommission/clusterrole.yaml`, its collection-bundled copy,
the Helm decommission ClusterRole, all affected RBAC documentation, Python and collection
RBAC tests, and parity/static-contract/manifest-consistency/negative-authorization tests.
It must include both the three CR `get` additions above and any §9 destination-read gap.
UID-preconditioned DELETE itself changes no verbs (`delete` is already
granted; preconditions are request-body, not authorization). The read-outcome
pagination extension changes no verbs.

## 16. Test and verification design

The July Testing section and the 20-case operator-identity matrix are retained as the
core future matrix. Current-base additions (each an implementation-plan task):

1. **Parity vectors for strict read outcomes** exercising the §6.3 table in both form
   factors, satisfying the R4-04 Task 0 Step 2 checklist; pagination completeness and
   later-page failure vectors on both the Python strict surface and the extended
   read-outcome module.
2. **Read-outcome extension regression**: rerun and extend
   the collection's `tests/unit/test_k8s_read_outcome.py` and
   `tests/integration/test_k8s_read_outcome_runtime.py`; invert their existing positive
   discovery-miss expectations from `error` to `kind_not_served`; add pagination and
   incomplete-list → `error` vectors; update the module's `RETURN.read_status` choices;
   run `ansible-test sanity`; and run the two behavior-falsifying consumer lanes
   `tests/integration/test_r3_02_compactor_runtime.py` and
   `tests/integration/test_r3_02_activation_runtime.py`. Static role-contract tests may
   supplement but do not replace those runtime lanes.
3. **Fail-open inversions**: the collection MCH wait loses `failed_when: false` — flip
   `test_decommission_role_contracts.py:336-350` and
   `test_ansible_resilience_contracts.py:485`; an unverifiable pod read fails the play.
   Python: lingering-pod warning becomes fatal; MCO/MCH CR absence re-checks asserted
   against the real client seam, not decorator-bypassing mocks.
4. **Refusal matrix** per §7: each prompt refused → abort + accurate summary +
   non-zero; rerun completes idempotently; `tests/test_decommission.py:143-159`
   replaced; non-interactive and integrated paths never prompt.
5. **Guarded-delete matrix** (July deletion-boundary tests retained): UID success, 409/412 fatal,
   pre-DELETE disappearance, mid-poll replacement, bounded timeout, check-mode
   `would_change`, redaction injection.
6. **Identity/TOCTOU matrix** (July operator-identity matrix retained), including
   same-name/new-UID replacement between discovery and DELETE, unrelated prefixed Pod,
   invalid/missing/multiple owner chains, Deployment/ReplicaSet replacement mid-drain.
7. **Destination gate matrix**: destination positively absent / present / `error` /
   ambiguous mixed state; ack flag accepted only against positive absence; flag
   rejected when gate passes; resume re-runs the gate.
8. **State/resume**: phase-table resume matrix (July), Python/full-collection-reset
   clean-skip limitation documented and asserted as *current* behavior (a test proving
   the record's absence changes the decision, so the R4-05 coordination is visible),
   collection `reset_from` preserving and revalidating teardown `operational_data`, and
   malformed-record fail-closed cases.
9. **Consolidation regression (GLM-H6)**: finalization and direct-decommission callers
   both exercise the single teardown path; GitOps-marker recording preserved for the
   finalization caller only; caller-specific preconditions preserved; artifact status
   honesty in the collection (`status` reflects the real outcome).
10. **Wrong-target boundary**: negative tests assert R4-03 checks bind *resource*
    identity (UID) — wrong-context/wrong-hub *target* protection is asserted absent
    here and owned by SSA-02, so the boundary itself is tested, not silently assumed.
11. **Constants parity**: new mirrored constants (surviving prefix diagnostic, drain
    selector, reason codes) added to `tests/test_constants_parity.py`.

Behavioral assertions are preferred over implementation-detail mocks throughout; the
decorator-bypassing mock pattern flagged in §8 item 4 is not carried forward.

## 17. Dependencies and ordering

- **R4-04 (consumer).** R4-03 must merge first: R4-04's Task 0 Step 2 hard gate names
  the exact evidence (strict primitive in both form factors, complete
  pagination/outcome tests, merged on `origin/ansible`). §6 of this amendment is
  written to satisfy that checklist without R4-04 needing another helper layer,
  weakened semantics, different pagination behavior, or drift. R4-04 is not
  implemented here.
- **SSA-02 (complementary, not absorbed).** Still `planned`, no design doc
  (`thermos-resolution-plan.md:703`, `:794-816`). The July carve-out stands:
  wrong-target/UID-expectation and embedded RBAC recheck gates are SSA-02's. Recorded
  dependency: standalone and non-interactive decommission remain without wrong-target
  protection until SSA-02 ships — the tracker holds SSA-P1 at "P1 (conditional: before
  next standalone/non-interactive decommission)" (`:2334`), so live standalone/
  non-interactive decommission use stays gated on SSA-02 regardless of R4-03's
  completion proofs. R4-03 binds *resource* identity; SSA-02 binds *target hub*
  identity. Neither replaces the other.
- **R4-05 (reset/locking).** The §13 reset limitation is mitigated by R4-05's
  reset-under-lock and narrowed `--force` (R4-E5); until then the limitation is
  documented operator guidance. No R4-05 work is pulled into R4-03.
- **R3-02 (seam owner).** Extending `acm_k8s_read_outcome` touches R3-02-delivered
  merged code and its two consumers; §6.2 and §16 item 2 carry the regression
  obligation.
- **R4-02.** The tracker's decommission gate for unrestored auto-import transactions
  (R4-B4) composes in front of integrated decommission and stays R4-02-owned.

## 18. Explicit non-goals

Retained from July: SSA-02 scope; repo-wide strict-read migration; resourceVersion
preconditions on decommission deletes; Hive `preserveOnDelete` check changes; treating
names/labels/images as stable contracts; implementing anything in this docs-only slice.

Added: no tracker edits (one stale sentence recorded in §19, not fixed here); no RBAC
artifact edits; no `tests/release/` or lab-controller changes; no changes to
`acm_restore_guarded_mutation`'s R4-04-owned contract; no parity-matrix/behavior-map
edits (implementation-slice obligation, §12); no new waiver/compatibility modes for
observability; no protected-file changes.

## 19. Risks and rejected alternatives

Rejected, with reasons:

1. *Second collection read helper* — rejected; extends `acm_k8s_read_outcome` (§6.2),
   per R4-04 amendment criterion 27 and the DRY ownership rule.
2. *Decommission-private strict list* — rejected in July and reaffirmed; R4-04's
   consumers depend on the shared contract.
3. *Gate on recorded `secondary_has_observability`* — rejected; stale-evidence
   substitution for a fresh mutation-time predicate violates the execution-time
   discovery invariant.
4. *Name-based delete plus before/after reads*: rejected; does not close the deleted
   object's identity race (July deletion boundary, binding). This does not close the
   separate Hive `preserveOnDelete` authorization TOCTOU, whose behavior is an explicit
   non-goal retained in §18.
5. *UID+resourceVersion preconditions for decommission deletes* — rejected; identity
   (UID) is the safety property; RV adds conflict-churn without a safety gain here,
   and would blur the deliberate contrast with R4-04's transaction semantics.
6. *Absorbing SSA-02* — rejected; no current authority does so, and target-identity
   hardening has its own design space (optional expected-UID, confirmation UX).
7. *Cross-form-factor teardown sharing for GLM-H6* — rejected; independence contract.
8. *Adding `namespace_absent` as a module status* — rejected; composable via a
   Namespace GET, keeping the R3-02 module narrow (KISS).
9. *Persisting the destination-gate result* — rejected; would invite stale-evidence
   reuse for a destructive decision.

Risks:

- *Read-outcome extension regressions* on merged R3-02 callers: mitigated by explicit
  inversion of the positive discovery-miss expectations, strengthening-only list
  semantics, and mandatory runtime consumer-lane reruns (§6.2, §16 item 2).
- *Reset laundering* (§13) — accepted, documented, R4-05-mitigated.
- *OLM/CSV contract drift beyond ACM 2.17* — fail-closed by design (July §1a);
  the audit range remains the widest any repository authority claims (§10).
- *Stale tracker sentence*: `thermos-resolution-plan.md:2148` still routes R4-04's
  Restore-cleanup delete through `acm_uid_guarded_delete`, while the accepted R4-04
  plan gives Restore cleanup its own UID+RV module. This does not affect R4-03
  correctness; it needs a tracker-reconciliation edit in a slice authorized to touch
  the tracker.
- *Two guarded-mutation modules in the collection* — accepted as different ownership
  boundaries (§8 item 1) with intra-collection `module_utils` reuse where natural;
  revisit only if implementation shows the boundaries collapsing.

## 20. Acceptance criteria

The July design's 12 acceptance criteria stand. This amendment adds:

A1. The shared strict-read contract is implemented per §6 in both form factors —
    Python strict surface plus the extended `acm_k8s_read_outcome` — with the shared
    parity vectors passing and the R4-04 Task 0 Step 2 evidence list satisfiable
    verbatim from merged `origin/ansible`.
A2. The collection MCH pod wait contains no `failed_when: false` on
    provenance/ownership/wait/final-verification paths; an unverifiable read fails the
    play; the previously pinning tests are inverted.
A3. The collection decommission summary artifact reports the real aggregated outcome;
    no hard-coded `pass`.
A4. Both merged read-outcome consumers pass their runtime regression lanes unchanged in
    behavior (`test_r3_02_compactor_runtime.py` and
    `test_r3_02_activation_runtime.py`); fail-closed paths remain fail-closed against the
    extended module.
A5. The §7 outcome table is observable in both form factors. Python distinguishes
    `refused` and `failed` from `not_requested` and `precondition_noop`, reports an
    accurate summary, and exits non-zero for refusal or failure. The non-interactive
    collection distinguishes `failed` from `not_requested`, `precondition_noop`, and
    `completed`, fails the play on `failed`, and publishes an accurate artifact status.
A6. The operator-prefix drift is closed either by removing that diagnostic from both form
    factors or by mirroring the surviving constant; every shared constant retained or
    introduced by §12 is enforced by `tests/test_constants_parity.py`.
A7. Python teardown exists exactly once (§11); finalization and direct decommission
    drive it with their caller-specific semantics preserved and tested.
A8. The §13 durable-field table is exhaustive for R4-03: no other durable keys are
    written, full-reset data loss is documented operator-facing, and collection
    `reset_from` preserves and revalidates the recorded teardown obligations.
A9. Native check mode is safe end-to-end in the collection decommission path
    (no mutation, no checkpoint transition, every module and the role-level
    `acm_switchover_decommission_result.changed` remain false, and prediction is reported
    separately and accurately as `would_change`).

## 21. Implementation-plan gate

This amendment authorizes no implementation-plan authoring by itself. After final
exact-head validation, the next authorized step is operator acceptance of this amended
written design. Only that acceptance authorizes writing an R4-03 implementation plan.
The later implementation plan must:

- decompose into PR-sized tasks with the §16 matrix mapped to executable test tasks;
- sequence the strict-read primitive (with parity vectors) before its decommission
  consumers, so the R4-04 Task 0 gate can be satisfied by the same merge order;
- carry the §15 RBAC realignment as an explicit coordinated task across every surface
  named by `AGENTS.md`;
- pin the two mutable API citations (July "citation provenance limitation") before any
  precondition-dependent code lands;
- follow the builder → independent validator → resolver workflow with terminal
  validation per `AGENTS.md`.

Runtime implementation remains unauthorized until that plan is separately reviewed and
approved.
