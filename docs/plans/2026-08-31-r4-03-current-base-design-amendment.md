# R4-03 Decommission Completion — Current-Base Design Amendment

**Date:** 2026-08-31
**Branch:** `docs/r4-03-current-base-amendment-2026-08-31` (base `origin/ansible` @ `74268192`)
**Status:** design **reopened** following exact-head validation of the implementation plan
(`DESIGN_REOPEN_REQUIRED`, IV-R403-12, PR [#280 comment 5494427270](https://github.com/tomazb/rh-acm-switchover/pull/280#issuecomment-5494427270)).
The operator has approved the design direction — **revision-only `resource_versions` plus a sibling typed
`absence_proofs` structure** — and §22 is the written reopened design candidate for that direction.
This candidate is **awaiting independent design validation**; it is not yet operator-approved in its
committed form. The implementation plan
([`2026-08-31-r4-03-decommission-completion-implementation-plan.md`](2026-08-31-r4-03-decommission-completion-implementation-plan.md))
is deliberately unchanged and is **non-authoritative wherever it conflicts with §22**; its repair is not
authorized until this reopened design is validated and re-approved. Runtime implementation remains
unauthorized.
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
intact; final live GET before success; `completed` carries `observed_at` plus the evidence
of the reads that proved it, and is necessary-but-never-sufficient for later destructive
decisions.

**Completion-evidence correction (design reopen).** The July wording "per-resource
`resourceVersion` values" remains binding for every final-proof read that actually returns a
revision, but it cannot express the positive-absence reads that carry none. §22 is the
authoritative completion-evidence schema for this amendment: `resource_versions` stays
revision-only, and positive absence is recorded in the sibling typed `absence_proofs`
structure. Where §22 and any earlier wording in this amendment, the July design, or the
implementation plan disagree about the shape of completion evidence, §22 governs.

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
- Identical completion-evidence schema: the §22 `resource_versions` / `absence_proofs`
  contract — the same closed key sets, the same `proof_type` vocabulary, the same
  `resource_key` grammar, the same per-family required key sets, and the same malformed
  classifications — is validated **independently** on each side (Python `RunRecord`,
  collection `checkpoint`), never through a shared implementation, and held equal by shared
  parity vectors including the malformed-record vectors.
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
| Teardown record, per resource | key `apiVersion/kind/namespace/name` → `{expected_uid, phase}` plus mandatory `{observed_at, resource_versions, absence_proofs}` when `phase == completed`, per the **§22 completion-evidence schema** (July §1 phase table) | teardown owner (§11) | reruns; integrated teardown's fresh live gate | forced-durable before DELETE and at every phase transition | exact CR identity via UID | intent + progress; `completed` additionally evidence | `completed` is proof at its final-read instant only; every later destructive decision re-proves live | fail closed before any mutation or clean-skip decision (July §1) | Python `--reset-state` or collection full `checkpoint.reset` destroys it; collection `reset_from` preserves `operational_data` and therefore the record |
| `operator_deployment` | July §1a schema (CSV + Deployment namespace/name/UID + capture metadata) | MCH teardown, before MCH DELETE | every drain/final-verification pass | one forced-durable write before DELETE | exact operator Deployment UID, bound to the enclosing MCH record key/UID | recorded identity expectation | immutable for the record's lifetime and never rebound; every pass strictly re-reads the located Deployment and requires the live UID to match, with positive namespace absence the only retained exception | fail closed; DELETE not issued if the write fails; later absence, replacement, or unverifiable read is `recovery_required` | Python `--reset-state` or collection full `checkpoint.reset` destroys it; collection `reset_from` preserves it |
| `operator_identity_unavailable` | July §1a schema (reason code + capture metadata) | same | same | same | enclosing MCH record | evidence (negative) | immutable; never silently upgraded by rediscovery | exactly one of the two outcomes must exist; both/neither/partial is malformed → fail closed | Python `--reset-state` or collection full `checkpoint.reset` destroys it; collection `reset_from` preserves it |

`absence_proofs` is a sibling field **inside** the teardown record, not a fourth durable
key. It therefore inherits that record's entire row above unchanged: same producer, same
consumers, same forced-durable write points, same binding to the record key and
`expected_uid`, same fail-closed malformed handling before any mutation or clean-skip
decision, and the same reset behaviour — Python `--reset-state` and the collection's full
`checkpoint.reset` destroy it with the record, while collection `reset_from` preserves it
and must revalidate it under §22. §22 owns its schema; this table owns its lifecycle.

No other durable state is added. Deliberately **not** persisted: the §9 gate result
(re-proven fresh on every run), refusal events (they end the run; the summary is
output, not state), dry-run observations (§14), and the target CR's pre-DELETE
`resourceVersion` (§22.6).

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
no operator-identity persistence, and no completion evidence — neither `observed_at`,
`resource_versions`, nor `absence_proofs`); claims no change. A later live run trusts nothing
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
12. **Completion-evidence schema matrix (§22)**, required in both form factors:
    1. *Revision-only enforcement*: a `completed` record whose `resource_versions`
       carries any key outside §22.3's closed label set is malformed → fail closed.
    2. *Namespace name rejected inside `resource_versions`*: the R2 shape — a
       `namespace_absent` key whose value is the namespace name — is rejected by key
       closure; paired with a producer-seam test asserting the only value source is the
       §22.3 provenance rule, so a namespace name can never be written there.
    3. *Arbitrary non-revision token rejected*: an unknown label, a non-string value, and
       an empty-string value are each malformed → fail closed.
    4. *Required absence proof missing*: a `completed` record whose proof path required a
       typed absence entry and omits it is malformed → fail closed; separately, an
       **absent** `resource_versions` or `absence_proofs` field at `completed` is
       malformed even when the record would otherwise be valid with an empty mapping.
    5. *Unknown absence proof type*: a `proof_type` outside §22.4's closed vocabulary,
       and a `proof_type` not permitted for its key, are each malformed → fail closed.
    6. *Malformed resource key*: wrong segment count, empty `apiVersion`/`kind`/`name`, a
       non-empty namespace on `drain_namespace`, and a `target_cr.resource_key` unequal to
       the record's own key are each malformed → fail closed.
    7. *MCO Pod-list proof path*: namespace-present completion records exactly
       `{drain_namespace, drain_pods}` revisions plus `absence_proofs.target_cr`; the
       recorded `drain_pods` value is the strict primitive's single snapshot revision for
       the whole paginated drain read, not a per-page value.
    8. *MCO namespace-absence proof path*: completion records an empty
       `resource_versions` mapping plus `absence_proofs.{target_cr, drain_namespace}`, and
       `drain_pods` is rejected in that mode.
    9. *ManagedCluster absence-only completion*: a valid `completed` record with an empty
       `resource_versions` mapping and `absence_proofs.target_cr` only; a record carrying
       any drain key is malformed; and no pre-DELETE revision is retained to make the
       mapping non-empty.
    10. *MCH both drain modes*: namespace-present with captured identity
        (`{drain_namespace, drain_pods, operator_deployment}`), namespace-present with
        `operator_identity_unavailable` (`operator_deployment` absent and rejected if
        present), and namespace-absent (empty `resource_versions`, `drain_namespace`
        absence proof discharging both the pod-empty and Deployment re-read predicates).
    11. *Python/Collection malformed-record parity*: every malformed vector above is a
        shared parity vector, and both independent validators classify it identically.
    12. *Evidence never substitutes for the fresh live gate*: a valid `completed` record
        carrying full evidence does not satisfy any later destructive precondition — the
        integrated teardown's live gate still runs its own reads, and a replacement
        created after the completion write is caught by that gate.

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

Added: no per-proof timestamps beyond the single completion `observed_at` (§22.5); no
generic or heterogeneous completion-evidence map (§22.7); no repair of the implementation
plan in this slice, and no design-level resolution of IV-R403-01 (§22.8); no tracker edits
(one stale sentence recorded in §19, not fixed here); no RBAC artifact edits; no `tests/release/` or lab-controller changes; no changes to
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
10. *Heterogeneous `resource_versions` ("proof key → strongest identifier the proving
    read can carry")* — **rejected** by operator decision on the design reopen. It
    silently changes the approved field's meaning, so no consumer can rely on any value
    being a revision, and it hides the revision/absence distinction that the strict-read
    algebra exists to keep explicit (§22.1, §22.7).
11. *A single generic `completion_evidence` map replacing both fields* — rejected. It has
    the same value-type erasure as alternative 10 with an additional cost: it discards the
    approved `resource_versions` field name that July criterion 8 and amendment §13 bind,
    forcing a rename with no safety gain.
12. *Substituting a LIST for an approved named GET to manufacture a revision* — rejected;
    it changes the recorded reads, the §15 verb rows, and the July §3 named-GET absence
    contract in order to satisfy a schema. The schema accommodates the approved reads;
    the reads are not bent to fit the schema (§22.1).
13. *Retaining the target CR's pre-DELETE `resourceVersion` as completion evidence* —
    rejected; that read precedes the mutation and never participates in the final proof,
    so recording it under a final-proof field would be exactly the masquerade §22.1
    forbids. Nothing else needs it: UID owns identity binding, and resourceVersion
    preconditions on decommission deletes remain a non-goal (§18, alternative 5), so it
    is persisted nowhere (§22.6).
14. *Recording per-ReplicaSet revisions in the completion evidence* — rejected with the
    justification in §22.6; their cardinality is variable, nothing consumes them, and
    excluding them removes no safety property because the owner chain is enforced at
    proof time rather than by the record.

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
    `reset_from` preserves and revalidates the recorded teardown obligations. A `completed`
    record satisfies the §22 completion-evidence schema exactly — `observed_at` present,
    `resource_versions` present and revision-only, `absence_proofs` present and typed, the
    per-family required key set matched, and every §22.2 malformed condition failing
    closed on both sides.

**July criterion 8 — clarification (design reopen).** July criterion 8 requires a
`completed` record to carry "that read's `observed_at` and per-resource `resourceVersion`
values". That requirement is **retained and sharpened, not relaxed**: every final-proof
read that returns a revision must have that revision recorded in `resource_versions`
(§22.1). It is clarified only where it was silent — a positive-absence read returns no
revision, and July criterion 8 never authorized inventing one. Such evidence is now carried
by the sibling typed `absence_proofs` structure (§22.4). Criterion 8's substantive
guarantee — that a `completed` record shows exactly what was proven and when, and that no
consumer treats it as proof of current state — is unchanged, and A8 above is the criterion
that closes it.
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

## 22. Design-reopen resolution — completion-evidence encoding

**Authority.** Independent exact-head validation of the R4-03 implementation plan returned
`DESIGN_REOPEN_REQUIRED` (IV-R403-12): the plan redefined the approved durable field
`resource_versions` as "proof key → strongest identifier the proving read can carry" and
stored a **namespace name** under it. That is a change to the normative meaning of an
approved durable field, not an implementation detail, so the validator correctly refused to
choose an encoding. The operator has since resolved the design question and approved this
direction:

> `resource_versions` remains revision-only, and positive absence evidence is recorded in a
> sibling typed `absence_proofs` structure.

This section is the written design contract for that decision. It is **authoritative for
completion evidence** across this amendment, the July design, and any implementation plan.
It changes no other R4-03 contract: the phase machine, UID-preconditioned deletion, the
strict-read algebra, the owner-chain classification, the destination gate, the outcome
table, RBAC, and the `changed` / `would_change` reporting rules are all unchanged.

A completed teardown record carries exactly three completion-evidence fields:
`observed_at`, `resource_versions`, and `absence_proofs`.

### 22.1 `resource_versions` — revision-only

`resource_versions` is a mapping. It remains semantically literal:

**Every value in this field MUST be an actual Kubernetes `resourceVersion` obtained from a
successful live read that participated in the final completion proof.**

It MUST NOT contain namespace names, resource names, UIDs, reason codes, absence tokens,
human-readable descriptions, synthetic revision values, or pre-DELETE revisions
masquerading as final-proof evidence.

Two rules bind the producer, and both are normative:

1. **Every revision-bearing final-proof read is recorded.** If a successful read that
   participates in the final completion proof returns a `resourceVersion`, that revision
   MUST appear in `resource_versions` under its §22.3 label. Evidence is not optional
   because a later consumer happens not to need it.
2. **No read is bent to fit the schema.** An approved named GET MUST NOT be replaced by a
   LIST in order to manufacture a revision. Doing so would change the recorded reads, the
   §15 verb rows, and the July §3 named-GET absence contract. Where the approved proof is
   an absence read, the evidence belongs in `absence_proofs`, not in a fabricated revision.

`resource_versions` is **mandatory at `phase == completed`** and MAY be an empty mapping.
An empty mapping is not missing evidence **if and only if** the complete final proof for
that record consisted only of positive absence reads, and every required absence predicate
is represented in `absence_proofs`. An *absent* field is never equivalent to an empty
mapping (§22.2).

Target identity remains bound by `expected_uid` and the phase machine (§8, July §1).
`resource_versions` carries no identity role and never rebinds one.

**Enforcement is honest about what a schema can check.** Kubernetes treats
`resourceVersion` as an opaque, server-defined string, so no validator can decide from the
value alone that a given string is a genuine revision, and a numeric-format check would
violate that API contract. Enforcement therefore rests on three checkable properties: the
closed key set (§22.3), the value type (non-empty string), and the **producer provenance
rule** — the only permitted value source is the `resourceVersion` surfaced by the strict
read that proved the predicate (§22.3). Tests bind the producer seam, not the string shape
(§16 item 12).

### 22.2 Required record invariants

**For `phase != completed`:** `observed_at`, `resource_versions`, and `absence_proofs`
carry no completion authority and MUST be absent. A record at `delete_started`,
`cr_absent`, `drain_pending`, `drained`, or `recovery_required` that carries any of the
three is **malformed → fail closed**. Completion evidence is never introduced early for
convenience, and no earlier pass's evidence is carried forward: the July §1 phase table
already requires the final absence and drain predicates to be re-proven at the `completed`
transition, so evidence written before that transition could only be stale.

**For `phase == completed`:**

- `observed_at` is mandatory (§22.5).
- `resource_versions` is mandatory, present as a mapping (possibly empty), and
  revision-only (§22.1).
- `absence_proofs` is mandatory, present as a mapping, whenever any required final
  predicate was proven through positive absence. For every R4-03 family this is always the
  case, because the target-CR predicate is always proven by a positive absence read that
  returns no revision (§22.6) — so in practice a valid `completed` record always carries a
  non-empty `absence_proofs`.
- Every final-proof predicate MUST be represented exactly once, by either a real
  revision-bearing successful read in `resource_versions` or a typed positive absence in
  `absence_proofs`, according to the proof path actually taken. The recorded key set MUST
  match the §22.6 required key set for that family and proof mode.

The following are each **malformed → fail closed**, on both sides, before any mutation or
clean-skip decision:

| Condition | Classification |
| --- | --- |
| Any of the three evidence fields present at `phase != completed` | malformed |
| `observed_at`, `resource_versions`, or `absence_proofs` missing at `completed` | malformed |
| An evidence field present but of the wrong type (not a string / not a mapping) | malformed |
| A key in `resource_versions` outside the §22.3 closed label set | malformed |
| A `resource_versions` value that is not a string, or is an empty string | malformed |
| A key in `absence_proofs` outside the §22.4 closed key set | malformed |
| An `absence_proofs` entry that is not a mapping, or whose field set is not exactly `{proof_type, resource_key}` | malformed |
| A `proof_type` outside the §22.4 vocabulary, or not permitted for its key | malformed |
| A `resource_key` violating the §22.4 grammar, or not equal to the value that key requires | malformed |
| Any empty required string anywhere in the evidence | malformed |
| A recorded key set that does not match the §22.6 required set for the family and mode | malformed |
| Evidence contradicting itself — both `resource_versions.drain_namespace` and `absence_proofs.drain_namespace`, or neither, for a family with a drain scope | malformed |
| `resource_versions` or `absence_proofs` mutated by a later write once `completed` | malformed, exactly like `expected_uid` |

Missing required evidence, unexpected evidence, contradictory evidence, unknown proof
types, malformed resource identity, and empty required strings all fail closed. No
persisted evidence, valid or otherwise, may substitute for the fresh live gate required
before a later destructive decision (§22.5).

### 22.3 `resource_versions` key grammar

The key namespace is a **closed set of explicitly defined proof labels**. Each label names
one predicate, each may occur at most once, and each maps one-to-one onto a single
revision-bearing final-proof read across all three families. Canonical Kubernetes resource
identities are deliberately **not** used as keys here, and the two grammars are never
mixed: a Pod LIST has no object identity to key on, so a canonical grammar would have to be
hybrid, which is exactly the ambiguity this decision removes.

| Label | Predicate whose proof this revision came from | Permitted value source |
| --- | --- | --- |
| `drain_namespace` | the fixed drain namespace was **present and readable** at final verification, selecting the pod-list proof mode | `metadata.resourceVersion` of the object returned by the fresh strict Namespace GET |
| `drain_pods` | the drain scope held zero Pods after §10 identity-based exclusion | the `metadata.resourceVersion` of the successful strict Pod LIST that proved the drain empty |
| `operator_deployment` | the durably recorded operator Deployment was re-read live and its UID still matched, satisfying the July §1a final identity predicate | `metadata.resourceVersion` of the object returned by that fresh strict Deployment GET |

Notes that bind the producer:

- **Provenance.** The value MUST be the `resource_version` the strict-read primitive
  (§6.1) surfaces for that exact read. No other source, and no derived or reformatted
  value, may be written.
- **Paginated drain reads.** `drain_pods` records the **single snapshot revision of the
  whole read**, which the strict primitive returns for the complete drained list, not a
  per-page value. The continuation contract pins every subsequent page to that same
  snapshot, so one revision correctly describes the entire proof.
- **`drain_namespace` is recorded because §22.1 rule 1 requires it.** The Namespace GET is
  not incidental: it is the read that decides which drain proof mode applies, so it
  participates in the final proof and returns a revision. Recording it also makes the two
  modes structurally exclusive — the same label appears in `resource_versions` when the
  namespace is present and in `absence_proofs` when it is absent, never in both and never
  in neither (§22.2).
- **`operator_deployment` shares its name with the record's sibling identity field
  deliberately**, and the two are different kinds of fact: the identity field records
  *which* Deployment, this label records *the revision of the read that re-verified it*.
  The binding rule removes any ambiguity:
  `resource_versions.operator_deployment` may be present **if and only if** the record
  carries an `operator_deployment` identity **and** the record completed in the
  namespace-present drain mode. Any other combination is malformed (§22.2).

Any key outside `{drain_namespace, drain_pods, operator_deployment}` — including a
canonical resource identity, an absence token, or the implementation plan's rejected
`cr` and `namespace_absent` keys — is malformed.

### 22.4 `absence_proofs` — typed positive-absence evidence

`absence_proofs` is a sibling mapping on the completed teardown record. It is **typed
historical completion evidence for positive absence predicates that provide no Kubernetes
`resourceVersion`**. Its vocabulary is aligned to the §6 strict-read algebra, so an absence
recorded here is exactly the outcome the strict primitive returned — never a re-derived or
generic "absent" string.

It **MUST NOT** be treated as reusable live-state truth. Integrated and later destructive
decisions rerun the approved fresh live gate regardless of what it contains (§22.5).

**Value schema.** Every entry is a mapping with **exactly** two fields, both required,
both non-empty strings:

```yaml
absence_proofs:
  <key>:
    proof_type: <object_absent | crd_absent | namespace_absent>
    resource_key: "<apiVersion>/<kind>/<namespace>/<name>"
```

Any additional field, any missing field, any non-string value, and any empty string is
malformed. There is no free-form polymorphism and no untyped variant.

**Key grammar — closed set.**

| Key | Predicate | Permitted `proof_type` | Required `resource_key` |
| --- | --- | --- | --- |
| `target_cr` | the object this record deleted is **positively absent** at final verification | `object_absent` — successful discovery plus a named GET returning 404; or `crd_absent` — a successful discovery document positively showing the target kind/APIResource is not served, which entails the object's absence | exactly the enclosing record's own key |
| `drain_namespace` | the fixed drain namespace is **positively absent**, proven by a fresh Namespace GET returning 404 | `namespace_absent` only | `v1/Namespace//<namespace>`, whose name segment is the fixed drain namespace for that family |

Any key outside `{target_cr, drain_namespace}` is malformed, as is a `proof_type` that is
outside the vocabulary or not permitted for its key.

**`resource_key` syntax.** `resource_key` is `<apiVersion>/<kind>/<namespace>/<name>` — the
same key grammar the teardown record itself uses (§13), reused rather than reinvented.
`apiVersion` is the literal Kubernetes `apiVersion` string, so it is `group/version` for
grouped resources and `version` for core resources; `namespace` is empty for cluster-scoped
objects; `name` is the exact object name. Parsing is deterministic: split from the right on
`/` exactly three times, yielding `[apiVersion, kind, namespace, name]`.

Validation, identical on both sides:

- the right-split MUST yield exactly four segments;
- `apiVersion` MUST be non-empty and contain at most one `/`;
- `kind` MUST be non-empty and MUST NOT contain `/`;
- `name` MUST be non-empty and MUST NOT contain `/`;
- `namespace` MUST NOT contain `/`, and MAY be empty only for a cluster-scoped object; for
  `drain_namespace` it MUST be empty, since `Namespace` is cluster-scoped;
- for `target_cr`, the whole `resource_key` MUST equal the enclosing record's key.

**Two examples, both obeying the grammar.** A ManagedCluster completed by a named-GET 404:

```yaml
absence_proofs:
  target_cr:
    proof_type: object_absent
    resource_key: "cluster.open-cluster-management.io/v1/ManagedCluster//cluster-a"
```

An MCO completed in the namespace-absent drain mode:

```yaml
absence_proofs:
  target_cr:
    proof_type: object_absent
    resource_key: "observability.open-cluster-management.io/v1beta2/MultiClusterObservability//observability"
  drain_namespace:
    proof_type: namespace_absent
    resource_key: "v1/Namespace//open-cluster-management-observability"
```

**Namespace absence never substitutes for the target-CR predicate.** July §3 requires the
final completion check to repeat the strict CR/CRD-absence predicate *and* the drain
predicate, and their joint success alone permits `completed`. `absence_proofs.target_cr` is
therefore always required at `completed`; `drain_namespace` absence discharges only the
drain-side predicates (§22.6).

### 22.5 `observed_at` and freshness

**`observed_at` is preserved unchanged as a single completion timestamp.** No per-proof
timestamps are added: the approved design does not require them, and adding one per read
would imply a per-read ordering guarantee the design deliberately declines to claim.

`observed_at` means: **the completion-proof observation boundary associated with the final
verification pass immediately preceding the `completed` durable write.** It does not assert
atomicity across the several API reads that make up that pass. July §1 already states the
guarantee at its real strength — no compare-and-swap spans a CR, a Pod list, a Deployment,
and a namespace, and this design does not invent one — so `observed_at` marks the boundary
of that pass, not an instant at which all four were simultaneously true.

Freshness semantics are unchanged and are not weakened by adding a second evidence field:

- `completed` is **historical evidence only**. It records that the teardown was proven
  complete at the instant of its final read; it never asserts current state.
- Every later destructive decision **reruns fresh live proof**. Integrated teardown
  re-runs the CR-absence and identity-aware Pod checks against live state before relying
  on this teardown being complete.
- Neither `resource_versions` nor `absence_proofs` may, alone or together, satisfy an
  execution-time predicate about a mutable resource. A replacement created after the
  completion write is caught by the fresh gate, never masked by the stored proof.
- The target UID is never rebound, and the recorded operator Deployment UID remains an
  **expected identity checked against live state** on every pass, never a substitute for
  reading it.
- The execution-time discovery invariant (`AGENTS.md`) is unchanged: no persisted evidence
  becomes an input to a live mutation predicate.

### 22.6 Per-family completion evidence

The required evidence is enumerated per teardown family and proof mode. Nothing here is
left generic, and nothing requires later invention.

#### MultiClusterObservability

The final proof always includes positive absence of the target MCO CR, recorded as
`absence_proofs.target_cr`. The drain scope is the selector-scoped Pod set in the fixed
observability namespace, and it has exactly **two mutually exclusive proof modes**,
selected by the fresh Namespace GET that opens the drain check (July §3):

| Drain proof mode | `resource_versions` | `absence_proofs` |
| --- | --- | --- |
| **Namespace present** — readable namespace, then a successful selector-scoped Pod LIST proving zero matching Pods | exactly `{drain_namespace, drain_pods}` | exactly `{target_cr}` |
| **Namespace positively absent** — a fresh Namespace GET returning 404, which entails the pod-empty predicate under the July §3 fixed-namespace scope rule | empty mapping | exactly `{target_cr, drain_namespace}` |

The modes are exclusive by construction: the namespace was either read present or proven
absent, so `drain_namespace` appears in exactly one of the two fields, and `drain_pods`
appears if and only if the namespace was present. The namespace **name** never appears as a
value in `resource_versions`; it appears only inside a typed `resource_key`.

#### ManagedCluster

Each ManagedCluster teardown record is per cluster name and has no drain scope: the July
flow is read → bind UID → preconditioned DELETE → confirm absent. The completion predicate
is positive final absence of that exact target, proven by a named GET returning 404 (or, if
the kind has ceased to be served, by a positive discovery-level absence).

| Proof mode | `resource_versions` | `absence_proofs` |
| --- | --- | --- |
| Named-GET 404, or positive kind absence | empty mapping | exactly `{target_cr}` |

`resource_versions` is validly **empty** here: no revision-bearing read participates in the
final proof. Any drain key is malformed for this family. No pre-delete revision is retained
to make the mapping non-empty — the empty mapping is the correct and complete record.

#### MultiClusterHub

The final proof includes positive absence of the target MCH CR plus the approved
identity-aware drain and final verification (July §1a). Three modes are reachable, and
which revision-bearing reads participate differs between them:

| Mode | `resource_versions` | `absence_proofs` |
| --- | --- | --- |
| **Namespace present, operator identity captured** — Namespace GET present, strict all-Pod LIST empty after owner-chain exclusion, and the recorded operator Deployment re-read live with a matching UID | exactly `{drain_namespace, drain_pods, operator_deployment}` | exactly `{target_cr}` |
| **Namespace present, `operator_identity_unavailable`** — no Deployment identity exists to re-read, so July criterion 11 requires a strictly verified **empty** Pod list with no exclusions | exactly `{drain_namespace, drain_pods}` | exactly `{target_cr}` |
| **Namespace positively absent** — the July §1a entailment exception | empty mapping | exactly `{target_cr, drain_namespace}` |

Two clarifications the schema depends on:

- **The namespace-absence entailment is recorded once and discharges both drain-side
  predicates.** July §1a states that a positively absent ACM namespace entails both the
  pod-empty predicate and the recorded Deployment re-read, because a namespaced object
  cannot exist under a namespace the API positively proves absent. A single
  `absence_proofs.drain_namespace` entry is therefore the complete durable representation
  of that mode; no separate operator-Deployment absence entry exists, and adding one would
  claim a read that was never performed. An unreadable or ambiguous namespace state never
  triggers this exception and records `recovery_required` instead (July §3).
- **The operator Deployment re-read is a genuine final-proof read**, performed on every
  pass even when no Pod is proposed for exclusion, so its revision is recorded whenever it
  happens — which is exactly the namespace-present, identity-captured mode.

#### Worked example — one completed MCH record

The namespace-present, identity-captured mode, showing both evidence fields together.
Every `resource_versions` value is a revision; the namespace name appears only inside a
typed `resource_key`.

```yaml
"operator.open-cluster-management.io/v1/MultiClusterHub/open-cluster-management/multiclusterhub":
  expected_uid: "2b71...-uid"
  phase: "completed"
  observed_at: "2026-09-04T10:11:12Z"
  resource_versions:
    drain_namespace: "88190"
    drain_pods: "88219"
    operator_deployment: "88203"
  absence_proofs:
    target_cr:
      proof_type: object_absent
      resource_key: "operator.open-cluster-management.io/v1/MultiClusterHub/open-cluster-management/multiclusterhub"
  operator_deployment:
    namespace: "open-cluster-management"
    name: "multiclusterhub-operator"
    uid: "aa10...-uid"
    # remaining July §1a capture fields omitted here; unchanged by this section
```

#### What is deliberately not recorded

Two exclusions are decisions, not omissions.

**1. The target CR's pre-DELETE `resourceVersion` is not persisted anywhere.** The
implementation plan introduced a `cr` key holding the revision observed by the strict named
GET that bound `expected_uid` — a read that happens *before* the mutation and does not
participate in the final completion proof. Recording it under a final-proof field is
precisely the masquerade §22.1 forbids, and the operator's resolution names it explicitly.
July required no such field: its `delete_started` row records the immutable resource key
and `expected_uid` and nothing more. Nothing else needs the value either — UID owns
identity binding, `resourceVersion` preconditions on decommission deletes remain an
explicit non-goal (§18; §19 alternative 5), and no consumer reads it. It is therefore
dropped rather than relocated. This overrides the earlier validation note that treated `cr`
as compatible because it is a genuine revision: it is a genuine revision of the *wrong
read*.

**2. ReplicaSet revisions are not part of the durable completion evidence.** Owner-chain
classification reads a ReplicaSet per Pod proposed for exclusion, so between zero and many
ReplicaSets may be read during a final MCH pass. Their revisions are excluded, with the
justification stated rather than assumed:

- They prove no completion predicate. The predicate is "the classified Pod set is empty
  after identity exclusion," which the `drain_pods` list revision and the recorded
  Deployment identity together pin. A ReplicaSet is an intermediate link in a per-Pod
  chain, not a predicate.
- Their cardinality is variable and topology-dependent — a rolling update legitimately
  produces several — so two equally valid `completed` records for the same cluster would
  carry different key sets. That is precisely the open-ended key namespace the parity
  vectors could not pin deterministically, and it would force the hybrid label/identity
  grammar §22.3 rejects.
- Nothing consumes them: the record is historical evidence, and every later destructive
  decision re-runs the full live chain against whatever ReplicaSets then exist.
- Excluding them removes no safety property. The owner chain is enforced **at proof time**
  and fails closed on every missing, malformed, ambiguous, replaced, or unreadable link
  (July §1a, criterion 10). Recording it would not enforce it.

### 22.7 Supersession

The design-reopen resolution, stated exactly:

1. The prior wording "per-resource `resourceVersion` values" (July §1, July criterion 8,
   amendment §8) **remains binding** for every final-proof read that actually provides a
   `resourceVersion`. It is not relaxed, and §22.1 rule 1 makes it mandatory rather than
   best-effort.
2. Positive absence reads inherently provide no `resourceVersion`. A named GET returning
   404, a discovery document showing a kind is not served, and a Namespace GET returning
   404 each prove a predicate and each return no revision. The earlier wording was silent
   about them; it never authorized inventing one.
3. Such evidence is now durably represented by the sibling typed `absence_proofs`
   structure (§22.4), with a closed key set, a closed `proof_type` vocabulary, and an
   exact `resource_key` grammar.
4. `resource_versions` is **not** generalized into a heterogeneous evidence map. Every
   value in it is a real revision, so a consumer may rely on that uniformly (§22.1).
5. The implementation plan's encoding of a namespace name under
   `resource_versions.namespace_absent` — and its accompanying "strongest identifier the
   proving read can carry" redefinition and `cr` key — is **rejected** (IV-R403-12).
6. The implementation plan is unchanged by this slice and is **non-authoritative wherever
   it conflicts with this section**. It must be repaired to conform *after* this reopened
   written design has been independently validated and re-approved by the operator. That
   repair is a separate, currently unauthorized step.

### 22.8 Boundaries

**IV-R403-01 is not resolved here, and is not affected by this section.** It remains an
open **implementation-plan** defect: on the same-substep path where a DELETE is accepted
and the subsequent final proof fails, the plan's aggregator cannot mechanically report the
actual change. This design's `changed` observable semantics are unchanged — `changed: true`
only after an accepted intended-UID mutation plus the full completion proof (§14, July
deletion boundary). No design-level result type, exception type, or reporting channel is
introduced, renamed, or widened by this slice. The later plan repair must make that path
report actual change mechanically, using the shapes the plan already declares, without
inventing a second undocumented channel.

**Reset and resume** are covered by §13 without a new rule: `absence_proofs` lives inside
the teardown record, so Python `--reset-state` and the collection's full `checkpoint.reset`
destroy it with the record, collection `reset_from` preserves it and must revalidate it
against §22.2, malformed reload fails closed, and parity validation covers it on both
sides (§12). The accepted R4-05 reset limitation (§13) is unchanged, and reset laundering
is not solved here.

**Cross-slice boundaries are unchanged.** This section absorbs no R4-05 reset/locking work,
no SSA-02 target-identity work, and no R4-04 evidence-transaction work. It adds no runtime
behavior, no RBAC verb, and no new persisted key beyond the sibling field on an
already-planned record.
