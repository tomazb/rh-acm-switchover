# TST-00 — Advanced Safety Testing Foundation

**Status:** Approved design; publication slice only
**Type:** Design/specification only
**Repository:** `tomazb/rh-acm-switchover`
**Primary development branch:** `ansible`
**Audited revision:** `c89de7ecd0bfbd31dc88971929f32fb00ea7e20b`
**Date:** 2026-08-08

## 1. Purpose

TST-00 defines the common safety-testing foundation for the proposed TST-01 through TST-10 program:

| ID | Method |
| --- | --- |
| TST-01 | Stateful model-based testing |
| TST-02 | Crash-consistency and deterministic fault injection |
| TST-03 | Generative Python ↔ Ansible differential parity |
| TST-04 | Deterministic interleaving/race testing |
| TST-05 | Coverage-guided fuzzing |
| TST-06 | Combinatorial / t-way scenario testing |
| TST-07 | Metamorphic testing |
| TST-08 | Formal controller model checking |
| TST-09 | Controller-owned live resilience/chaos testing |
| TST-10 | Controller-owned endurance/soak testing |

TST-00 adds **no production behavior, test implementation, dependency, CI workflow, release profile, live command, or lab mutation**.

Its purpose is to prevent the ten later methods from independently inventing incompatible safety invariants, scenario representations, fault names, observable behavior models, Python/Ansible parity definitions, evidence classifications, or certification claims.

The foundation is test architecture only. Python CLI and Ansible Collection production code remain independent and must not import from a common TST implementation.

## 2. Audit baseline

The audited `ansible` revision is:

```text
c89de7ecd0bfbd31dc88971929f32fb00ea7e20b
```

The PR's original approved publication base was `1e0486eb9edde99b10d37d13a051523c57901ba7`. The previously re-audited baseline was `98a228212d56418b50d09a4089dab18f53c26cf0`. The current re-audited integration base is `c89de7ecd0bfbd31dc88971929f32fb00ea7e20b`; only this last SHA is the current audited revision.

The current branch already has a substantial testing architecture.

### 2.1 Existing conventional testing

The repository has Python and Collection unit/integration coverage, E2E testing, parity tests, and a separate release-validation framework. The Collection remains a complete independent implementation, and operator-facing dual-supported behavior is required to remain aligned.

`./run_tests.sh` executes the ordinary non-E2E tests, the non-live release-framework helper suite, and strict quality/security gates. E2E remains opt-in.

### 2.2 Existing property-based testing

PBT is mature rather than experimental. It already covers generated semantic domains for validation/parity, path safety, checkpoint/resume, report artifacts, BackupSchedule behavior, Argo CD, and RBAC.

The design deliberately uses semantic generators instead of random blobs and requires pure/local fixtures rather than live clusters.

Current Hypothesis profiles provide 50-example development, 100-example CI, and 1000-example deep modes.

Checkpoint PBT already contains a useful form of generated-history testing: bounded sequences of `StateManager` operations are executed against an independent model and then checked after reload.

This is a strong foundation for TST-01, but it is **not yet a reusable state-machine model of the switchover safety protocol**.

The PBT design also deliberately excluded multi-process crash simulation.

### 2.3 Existing mutation testing

Mutation testing is already operational and should **not** be reinvented inside the TST program.

The mutation-testing design treats mutation analysis as an on-demand diagnostic for assertion quality rather than a normal required PR gate. It specifically targets wrong-hub/resource mutation, RBAC, checkpoint/resume, dry-run, Argo CD, waits, and parity.

Existing mutation baselines have already exposed meaningful categories such as missing assertions, Python/Collection parity gaps, wrong argument/payload assertions, and timeout/runtime noise.

The current `98a2282..c89de7e` re-audit delta adds a documented RBAC mutation baseline and focused survivor-killing tests only. It changes no production behavior or TST implementation boundary.

TST-01…10 therefore consume mutation findings as inputs; they do not replace `mutmut` or establish a second mutation-testing framework.

### 2.4 Existing reliability testing

Current reliability tests verify retry classification and bounded retry behavior for HTTP/network failures, including 429/5xx, connection errors, and immediate failure for non-retryable errors.

This is valuable but fundamentally example-based and does not exhaustively explore failure timing relative to externally committed mutations and local checkpoint persistence.

### 2.5 Existing state durability

`StateManager` already has substantial durability defenses:

- process-lifetime state locks;
- atomic temporary-file + `os.replace()` writes;
- `fsync()` of the temporary file;
- corruption fail-closed behavior;
- SIGTERM/SIGINT flushing;
- atexit flushing;
- hub-UID binding;
- fail-closed resume when identity cannot be proven.

Existing tests cover numerous durability and idempotence properties.

The important remaining problem is **transactional ambiguity across two systems**:

```text
Kubernetes mutation
        +
local durable checkpoint
```

Those cannot be made one atomic transaction.

### 2.6 Existing E2E/soak

The repository already has an `e2e_soak` marker and repeated-cycle live E2E logic.

The current full-validation E2E suite performs real-cluster switchover/soak work and includes explicit Argo CD cleanup using direct `kubectl` mutations.

That historical harness remains useful, but it must not become the authority for TST-10. The newer lab-controller safety architecture deliberately moves live truth and mutation authority into the controller.

### 2.7 Existing release-validation/controller architecture

Issue #121 remains the active RC-hardening umbrella and already owns cycles and cooldowns, soak, failure budgets, recovery/hard stops, scenario-aware runtime parity, live discovery, and artifact/redaction evidence.

Phase 9A established the controller authority model and known-state segment architecture. Phase 9B implemented controller-owned live **read-only** physical-identity proof; its corrected two-hub read-only exit evidence and implementation are merged, and its tracker is closed. Its evidence remains explicitly non-certification and non-mutation.

At the audited date:

```text
Phase 9A   CLOSED
Phase 9B   CLOSED

Phase 9C   OPEN   known-state / logical-role proof
Phase 9D   OPEN   controller-owned bootstrap/reset mutation
Phase 9E   OPEN   one Python passive-switchover segment
Phase 9F   OPEN   one Ansible reverse-switchover + parity segment
```

Phase 9C explicitly remains non-executable authorization only. Phase 9D introduces bounded controller-owned mutation authority only after 9C. Phase 9F eventually produces the reverse Ansible segment and narrow live parity proof.

This establishes an important TST-00 rule:

> **TST-09 and TST-10 must extend the existing lab controller and Issue #121 sequencing. They must never create an independent live mutation authority.**

## 3. Audit findings

### 3.1 What is already strong

The project already has excellent coverage of the **input space** through examples, PBT, and mutation analysis. It also has meaningful unit, integration, parity, E2E, release validation, and live read-only identity proof.

The next testing investment should therefore target dimensions poorly covered by those layers rather than simply increasing test count.

### 3.2 Remaining testing dimensions

#### GAP-01 — History-space coverage

Existing PBT generates bounded histories but does not maintain a reusable state-machine with transitions, preconditions, and invariants over arbitrarily generated long-lived operation histories.

TST owner: **TST-01**

#### GAP-02 — Mutation/checkpoint crash ambiguity

A mutation can reach Kubernetes while the client loses the response or crashes before durable local state records the outcome.

The current head itself documents an accepted Collection Argo CD case where a crash after the first pause patch but before checkpoint persistence leaves the `run_id` only on cluster annotations.

TST owner: **TST-02**

#### GAP-03 — General semantic differential execution

Existing parity tests compare important constants, fixtures, helper behavior, and release-normalized fields. Architectural differences are explicitly allowed when operator-visible behavior is equivalent.

There is not yet one generic scenario → Python/Ansible semantic-trace comparator.

TST owner: **TST-03**

#### GAP-04 — Controlled external-writer interleavings

Argo CD, ApplicationSet, and Kubernetes clients can mutate state between our GET and PATCH operations. Existing PBT intentionally does not run a real Argo controller.

TST owner: **TST-04**

#### GAP-05 — Coverage-directed parser/state input discovery

PBT deliberately prefers semantic domains. A separate coverage-directed technique could explore malformed structural combinations efficiently.

TST owner: **TST-05**

#### GAP-06 — Interaction coverage

The release configuration has many discrete factors. PBT samples combinations, and example scenarios cover selected combinations, but the project does not currently express a measurable `t`-way interaction-coverage goal.

TST owner: **TST-06**

#### GAP-07 — Systematic transformation invariants

Individual idempotence properties exist, but there is no shared metamorphic framework expressing transformations such as list reordering, pagination reshaping, irrelevant-resource insertion, or safe re-execution.

TST owner: **TST-07**

#### GAP-08 — Exhaustive safety-protocol verification

The Phase-9 controller protocol is safety-critical and already has a well-defined state model, but remains validated using prose, implementation tests, and reviews rather than exhaustive bounded model checking.

TST owner: **TST-08**

#### GAP-09 — Controller-authorized live fault testing

Phase 9B explicitly excluded failure injection and mutation. Later mutation authority is not complete yet.

TST owner: **TST-09**, blocked by Phase-9 prerequisites.

#### GAP-10 — Controller-authorized endurance testing

Legacy E2E soak exists, while Issue #121 still tracks proper repeated known-state cycles, cooldowns, and failure budgets.

TST owner: **TST-10**, integrated with #121 rather than competing with it.

## 4. Architectural principles

TST-01…10 must obey the following design rules.

### 4.1 Testing code is not production architecture

The shared scenario model and trace model live exclusively in testing infrastructure. Neither production form factor may import them.

### 4.2 Semantic parity, not structural parity

Python and Ansible deliberately use different architecture. For example, Python state files and Collection checkpoints/cluster markers are not byte-compatible mechanisms.

TST-03 compares observable safety semantics, not internal variables, number of tasks, exact reads, state-file shape, or implementation call graph.

### 4.3 Fail closed on comparator uncertainty

A differential normalizer must never discard a difference simply because it is inconvenient. Unknown events classify as `UNCLASSIFIED_EVENT`.

The comparator maps either an `UNCLASSIFIED_EVENT` or any otherwise-unclassified comparison result to blocking `UNCLASSIFIED_DIFFERENCE` until explicitly classified.

### 4.4 Reproducibility over randomness

Every generated/fuzz/fault/interleaving failure must be reproducible from one or more of:

- Hypothesis example/seed;
- scenario ID;
- fault schedule;
- interleaving schedule;
- fuzz corpus input;
- combinatorial row ID;
- formal counterexample trace.

### 4.5 Simulated evidence is never live evidence

TST-01 through TST-08 are non-live evidence.

A fake Kubernetes server, fault adapter, model checker, mock Argo actor, or generated scenario cannot become certification evidence.

TST-09/TST-10 live execution is still non-certification by default unless explicitly invoked through an approved controller-owned certification entrypoint whose eligibility gates independently succeed.

## 5. Safety invariant catalogue

Invariants are separated by authority domain so that production semantics are not accidentally conflated with stronger lab-certification controller rules.

### 5.1 CORE — production workflow invariants

| ID | Invariant |
| --- | --- |
| CORE-001 | A mutation targets the explicitly selected hub/context/resource; target ambiguity blocks mutation. |
| CORE-002 | Resume must not continue against a different live cluster identity from the identity bound to persisted progress. |
| CORE-003 | Dry-run/check-mode paths must not issue durable workload mutations where that mode promises non-mutation. |
| CORE-004 | Successfully completed idempotent operations may be replayed without producing unsafe duplicate effects. |
| CORE-005 | Persisted progress must never cause a required safety validation to be skipped after the validation evidence becomes invalid. |
| CORE-006 | Corrupt or structurally invalid persisted state fails closed rather than being silently replaced and replayed. |
| CORE-007 | Bounded operations have finite timeout/retry budgets; timeout exhaustion produces explicit failure. |
| CORE-008 | A successful operation is not recorded as completed before its required observable success condition is established. |
| CORE-009 | Kubernetes optimistic-concurrency conflicts are not silently treated as successful mutations. |
| CORE-010 | Operator-visible or machine-readable results must accurately distinguish changed, unchanged, failed, and skipped states. |

### 5.2 OBL — durable obligation invariants

These apply especially to Argo CD pause/resume and any future action that creates an obligation requiring later discharge.

| ID | Invariant |
| --- | --- |
| OBL-001 | Creating an externally durable side effect that requires cleanup creates a corresponding observable obligation. |
| OBL-002 | An unresolved obligation cannot be silently dropped because discovery later fails or the controlled API becomes invisible. |
| OBL-003 | An obligation is discharged only after the required restoration state is positively verified. |
| OBL-004 | Ownership ambiguity prevents destructive cleanup or restoration mutation. |
| OBL-005 | Repeating the same obligation-producing action with the same identity is idempotent. |

The internal representation is allowed to differ between Python and Ansible.

### 5.3 PAR — dual-form-factor parity invariants

| ID | Invariant |
| --- | --- |
| PAR-001 | A dual-supported scenario reaches semantically equivalent terminal outcome classes unless an approved divergence is documented. |
| PAR-002 | Safety-relevant mutations target equivalent logical roles, resource kinds, namespaces, and objects. |
| PAR-003 | Python and Ansible agree on fail-closed versus continue decisions at parity-sensitive safety boundaries. |
| PAR-004 | Dry-run/check-mode behavior remains semantically aligned where the parity contract says the behavior is shared. |
| PAR-005 | Report/checkpoint semantics used as shared operator contracts remain compatible even when internal persistence differs. |
| PAR-006 | A normalizer may ignore implementation-only events only through an explicit comparator policy; safety-relevant events are never silently ignored. |

### 5.4 LAB — Phase-9 controller invariants

The Phase-9 lab controller's decision vocabulary, failure dispositions, retry eligibility, and state machine are
defined normatively elsewhere in this repository:

- `docs/plans/2026-07-17-phase-9a-rc-hardening-rebaseline-and-live-controller-design.md` — the authoritative
  disposition table and the bounded pre-mutation retry state machine;
- `docs/development/lab-role-controller-spec.md` — the controller decision contract.

**TST-00 does not restate those semantics.** Where a LAB invariant below refers to a controller decision, the cited
documents are authoritative and this document defers to them without paraphrase. Any divergence between this document
and the cited authority is a defect in this document, never a competing contract.

| ID | Invariant |
| --- | --- |
| LAB-001 | Physical identity is proven before any controller-authorized live mutation. |
| LAB-002 | Logical primary/secondary role mapping is freshly proven before a role-dependent mutation. |
| LAB-003 | Unknown, stale, incomplete, or conflicting identity/role evidence blocks mutation. Which failure disposition the controller emits, and its precedence relative to other dispositions, is decided by the cited Phase-9 authority — never by a test method, harness, or this document. |
| LAB-004 | Each known-state segment contains at most one lab-mutating scenario. |
| LAB-005 | A mutation authorization is bound to the current source revision, identities, roles, profile/scenario, and freshness boundary. |
| LAB-006 | Authorization is invalidated by intervening mutations, stale evidence, or role transition. |
| LAB-007 | A mutating segment does not hand off to the next segment until final physical/role/state proof succeeds. |
| LAB-008 | Agent/Codex/assistant orchestration cannot override, reinterpret, or independently retry **any** controller failure disposition. Retry eligibility, its bounds, and its authorization requirements are defined solely by the cited Phase-9 authority; no orchestration layer derives retry authority from a disposition name. |
| LAB-009 | Recovery is a separately authorized known-state segment rather than an improvised automatic continuation. |
| LAB-010 | Live fault injection cannot independently select its mutation target; the lab controller remains the sole mutation authority. |

### 5.5 EVD — evidence invariants

| ID | Invariant |
| --- | --- |
| EVD-001 | Fake, injected, dry-run, static-fixture, and local-harness evidence cannot be labelled live certification evidence. |
| EVD-002 | Sensitive paths, tokens, kubeconfigs, credentials, and private identity material are excluded or safely redacted from publishable artifacts. |
| EVD-003 | A redaction-audit failure prevents publication of evidence that claims success. |
| EVD-004 | Every advanced test failure carries sufficient provenance to reproduce the counterexample. |
| EVD-005 | Dirty/diagnostic execution cannot be promoted to certification evidence merely because functional checks passed. |
| EVD-006 | TST evidence records the exact source revision and method/configuration used to generate it. |

### 5.6 Proposed invariant requiring characterization

One additional invariant is proposed rather than claimed as already universally implemented:

**CORE-011 — Ambiguous-mutation outcome reconciliation**

> If a mutating request may have committed externally but the client cannot determine its outcome, retry/resume logic must establish the actual live state before treating the mutation as either successful or absent.

TST-02 must characterize current behavior before CORE-011 is made a repository-wide enforced contract.

## 6. Canonical test scenario model

TST-00 defines a **test-only logical schema**, not a production Python class yet.

```text
SafetyScenario
├── scenario_id
├── initial_state
│   ├── hubs
│   ├── identities
│   ├── logical_roles
│   ├── managed_clusters
│   ├── ACM_state
│   ├── backup_restore_state
│   ├── argocd_state
│   ├── RBAC_state
│   └── persisted_progress
├── requested_operation
│   ├── workflow
│   ├── method
│   ├── execution_mode
│   ├── old_hub_action
│   ├── feature_flags
│   └── target_selector
│       ├── selected_hub
│       ├── safe_context_id
│       ├── logical_role
│       └── safe_resource_identity
├── external_actor_schedule
├── fault_schedule
└── expected_invariants
```

Rules:

1. The scenario contains semantic state, not Python or Ansible implementation objects.
2. Credentials, raw kubeconfigs, and private live identifiers are prohibited.
3. Test adapters translate the same scenario into each implementation's native inputs.
4. A scenario may be executable by only one test method; unsupported projections are explicit.
5. Random/generative builders construct this model from bounded semantic strategies.
6. External-actor and fault schedules share one monotonic boundary/ordinal namespace. Every entry records whether it
   occurs before or after the named boundary; equal ordinals are invalid rather than resolved by adapter-specific
   ordering.
7. Adapters and expected invariants consume the same `target_selector`; adapters may translate it but may not infer
   independent targets. A missing or ambiguous selector for an operation that may mutate is a blocking scenario and
   no mutation may be attempted.

TST-01, TST-03, TST-04, TST-06, and TST-07 should eventually share this model.

## 7. Canonical observable trace

TST-03 and TST-04 require a common representation of externally meaningful behavior.

Proposed logical event:

```text
SemanticEvent
├── ordinal
├── actor
│   ├── python
│   ├── ansible
│   ├── controller
│   └── external
├── phase
├── operation
├── target_role
├── safe_target_id
├── api_group
├── api_version
├── kind
├── namespace
├── name
├── verb
├── mutation
├── result_class
├── changed
├── obligation_delta
├── checkpoint_delta
│   ├── phase
│   ├── contract_status
│   ├── attempted
│   ├── completed
│   └── durably_reloaded
└── evidence_tags
```

`checkpoint_delta.contract_status` is a trace field of this foundation; its value vocabulary and the obligations
attached to each value are specified in tracker #228 (§11, TST-02).

The trace must not include kubeconfig contents, bearer tokens, raw certificates, private controller enrollment IDs, or arbitrary exception object representations.

Internal diagnostic traces and publishable evidence are distinct projections. A publishable trace uses controlled
operation/result/evidence-tag vocabularies and recursively validates every identifier and free-form field against the
redaction policy. Unknown fields, unsafe identifiers, or incomplete recursive validation fail closed and block
publication rather than being silently dropped.

### 7.1 Comparator policy

Different scenario classes require different comparison strength.

**STRICT_MUTATION** compares mutation count, logical target role, resource identity, mutation intent, required mutation order, and terminal result.

**SAFETY_OUTCOME** compares allow/block decision, failure classification, obligation state, and changed/no-change semantics. Implementation-specific reads may differ.

**ARTIFACT_CONTRACT** compares normalized operator-facing report fields rather than internal execution events.

**EXPLICIT_DIVERGENCE** is permitted only when existing parity documentation and operator approval authorize the difference.

Anything not covered by the selected policy becomes `UNCLASSIFIED_DIFFERENCE` rather than being automatically ignored.

Every scenario that permits or attempts a safety-relevant mutation, or creates or clears a safety obligation, uses
`STRICT_MUTATION`; this mechanically enforces PAR-002 target equivalence. `SAFETY_OUTCOME` is limited to mutation-free
allow/block comparisons. A policy cannot be weakened merely because one adapter omits target evidence.

## 8. Fault taxonomy

TST-02, TST-04, and later TST-09 use one shared vocabulary.

### 8.1 API faults

```text
API-403
API-404
API-409
API-429
API-500
API-503
API-TIMEOUT
API-MALFORMED-RESPONSE
```

### 8.2 Transport ambiguity

```text
NET-BEFORE-SEND
NET-AFTER-SEND-BEFORE-COMMIT
NET-AFTER-COMMIT-BEFORE-RESPONSE
NET-PARTIAL-RESPONSE
```

The test adapter must distinguish these because only some create an ambiguous committed outcome.

### 8.3 Process failures

```text
PROC-BEFORE-MUTATION
PROC-AFTER-MUTATION
PROC-BEFORE-CHECKPOINT
PROC-DURING-CHECKPOINT
PROC-AFTER-CHECKPOINT
PROC-SIGTERM
PROC-SIGKILL
```

### 8.4 Persistent-state failures

```text
STATE-PERMISSION
STATE-NOSPACE
STATE-CORRUPT
STATE-TRUNCATED
STATE-STALE
STATE-IDENTITY-MISMATCH
```

### 8.5 Concurrent/external actor changes

```text
EXT-RESOURCE-DELETE
EXT-RESOURCE-RECREATE-NEW-UID
EXT-RESOURCEVERSION-ADVANCE
ARGO-SELFHEAL
ARGO-APPLICATIONSET-RECREATE
ARGO-ANNOTATION-STRIP
ARGO-SYNC-POLICY-CHANGE
```

### 8.6 Discovery faults

```text
DISCOVERY-PARTIAL
DISCOVERY-PAGINATION-LOOP
DISCOVERY-TRUNCATED
DISCOVERY-MIXED-SNAPSHOT
DISCOVERY-STALE
DISCOVERY-MIXED-ORIGIN
```

### 8.7 Fault-injection rules

Initial non-live fault tests should inject one named fault at a deterministic boundary, avoid random sleeps, expose whether an external mutation was attempted and whether the fake API committed it, maintain a complete event trace, produce a reproducible scenario + fault ID, and never require a live cluster.

Combination faults can be introduced only after single-fault semantics are stable.

## 9. Evidence classification

Every TST result is assigned one immutable `method_class` describing how the result was produced.

```text
UNIT
PROPERTY
STATEFUL
FAULT_INJECTION
DIFFERENTIAL
INTERLEAVING
FUZZ
COMBINATORIAL
METAMORPHIC
FORMAL_MODEL
LIVE_RESILIENCE
SOAK
```

`method_class` is independent of the controller-owned artifact `evidence_class`. The latter uses the current Phase-9
allowlist:

```text
non_live_fake
non_live_dry_run
non_live_local_harness
static_fixture
live_read_only
live_mutating_segment
LAB_PREPARATION_ONLY
diagnostic_live
```

Certification is an eligibility decision, not a method or evidence class. `LIVE_CERTIFICATION` is therefore not a
harness-assignable class. Only the approved controller/release path may assign a `live_*` artifact class, and every
writer must reject unknown classes, false relabeling, or a class inconsistent with the recorded execution path.

Evidence-class authority is fail closed:

- `non_live_fake`, `non_live_dry_run`, `non_live_local_harness`, and `static_fixture` require `live=false`;
- `live_read_only`, `live_mutating_segment`, and `diagnostic_live` require `live=true` and all controller provenance
  fields below;
- `LAB_PREPARATION_ONLY` remains governed by the separate Phase-9 preparation contract, requires controller
  provenance, and can never be certification eligible or relabeled as scenario evidence; and
- `certification_eligible=true` requires the approved eligibility decision, `eligibility_gate_result`, and every
  controller reference below.

Writers reject a missing authority field, a class/`live` mismatch, an eligibility claim without complete authority,
or any attempt to infer live or certification status from `method_class`.

Minimum provenance for generated advanced-test evidence:

```text
source_revision
method
method_class
evidence_class
scenario_id
invariant_ids
implementation
reproduction
    kind (seed | scenario | corpus | schedule | combinatorial_row | model_trace | none)
    value_or_safe_reference
    schema_version
    tool_version
    relevant_configuration_hash
live
mutation_attempted
certification_eligible
redaction_status
controller_run_id (required for controller-owned live evidence)
controller_segment_id (required for controller-owned live evidence)
authorization_or_approval_reference (required for controller-owned live evidence)
eligibility_gate_result (required for controller-owned live evidence; also required when certification_eligible=true)
```

For `reproduction.kind=scenario`, the top-level `scenario_id` is the counterexample replay token and
`value_or_safe_reference` identifies the immutable scenario definition or input. For
`reproduction.kind=combinatorial_row`, `value_or_safe_reference` identifies the executed row or an immutable safe
reference to it, and `relevant_configuration_hash` binds the factor model, constraints/configuration, or equivalent
reproducibility configuration.

`reproduction.kind=none` is permitted only for a non-failure result with no counterexample-specific replay token. A
failure must use a non-`none` kind and carry enough reproduction information, together with the top-level provenance,
to reproduce or retrieve its counterexample. Evidence validation fails closed when a failure reproduction record is
missing or invalid.

Defaults:

```text
All TST-generated results:
    live = false
    certification_eligible = false

TST-01 ... TST-08:
    live = false
    certification_eligible = false

TST-09 ... TST-10:
    live = false
    certification_eligible = false
```

`live=true` is permitted only when evidence is produced through an approved controller-owned live entrypoint.

`certification_eligible=true` is never chosen by a test method or harness; it must come from a traceable approved controller/release eligibility decision and the recorded `eligibility_gate_result`.

TST-09/TST-10 may become certification-relevant only through a future approved release-controller contract. The testing method itself never decides certification eligibility.

## 10. TST program dependency model

```text
                         TST-00
                    Safety foundation
                          │
       ┌──────────────────┼───────────────────┐
       │                  │                   │
       ▼                  ▼                   ▼
    TST-01             TST-02              TST-07
    stateful            crash/fault         metamorphic
       │                  │                   │
       │                  └────────┐          │
       │                           ▼          │
       │                         TST-04 ◄─────┘
       │                      interleavings
       │
       └──────────┐
                  ▼
                TST-03
             differential
                  │
                  ▼
                TST-06
             combinations

    TST-05 fuzzing
       ▲
       └──── TST-00 only

    TST-08 formal model
       ▲
       └──── TST-00 + current Phase-9 design

Phase 9C → 9D → 9E → 9F
                  │
                  ▼
                TST-09
          controlled live faults
                  │
                  ▼
                TST-10
           controller-owned soak
```

TST-06 does not technically require TST-03 to generate a covering array, but parity-oriented combinatorial execution should reuse the TST-03 adapters once they exist.

## 11. TST slice specifications

### TST-01 — Stateful model-based testing

Start smaller than the complete switchover.

Initial target:

```text
StateManager
RunRecord
checkpoint semantics
hub identity binding
ArgocdPauseRegister / obligation state
```

Use Hypothesis `RuleBasedStateMachine` or an equivalent explicit state-machine mechanism.

Existing generated `StateManager` operation strategies should be reused where useful rather than discarded.

Key improvement over current PBT:

```text
Hypothesis chooses legal action
        ↓
model transition
        ↓
SUT transition
        ↓
invariants checked after every step
        ↓
next action depends on current state
```

Initial invariants: `CORE-002`, `CORE-004`, `CORE-005`, `CORE-006`, `CORE-008`, and `OBL-001..005`.

A shrunk state-machine history must identify failures as readable domain operations rather than internal method calls wherever possible.

### TST-02 — Crash consistency and fault injection

Initial scope should characterize mutation/checkpoint boundaries for Argo CD pause/resume, BackupSchedule pause/enable, phase/step checkpoint persistence, and one activation mutation.

For every selected mutation:

```text
state before
   ↓
precondition read
   ↓
pre-mutation obligation / mutation-start checkpoint, when required by the source contract
   ↓
durability barrier and reload through a newly constructed state reader
   ↓
mutation handoff / attempt
   ↓
authoritative verification or reconciliation
   ↓
post-verification completion checkpoint
   ↓
durability barrier and reload through a newly constructed state reader
```

Inject failure at each meaningful boundary. A stage may be marked not applicable only when the audited source contract
does not define it; TST-02 records that absence instead of inventing a common Python/Collection storage format or
silently treating in-memory state as durable evidence.

TST-00 fixes the following properties for checkpoint observation. The concrete observation schema — the
`contract_status` vocabulary and per-status obligations — is specified in tracker #228.

- A checkpoint whose absence in the audited source contract is itself the finding is recorded as a characterization
  result, never silently treated as satisfied or as not applicable.
- A missing checkpoint that the audited contract does require fails the checkpoint assertion closed.
- After a simulated process failure the original process and in-memory objects are discarded; a recovery claim
  requires a freshly constructed SUT/state reader to reconstruct the recorded state.
- Checkpoint-contract status and the authoritative external mutation outcome are separate trace dimensions. A
  positively verified external post-state is not rewritten as ambiguous because a durability contract is absent, and
  no checkpoint status can promote an ambiguous external outcome to `MUTATION_CONFIRMED`.
- TST-02 characterizes current checkpoint guarantees and, independently, ambiguous-outcome reconciliation before
  CORE-011 can become a shared enforced contract.

Required outcome classes:

```text
NO_MUTATION                  — no mutating request was attempted.
MUTATION_NOT_APPLIED         — a mutating request was attempted, and external non-commit was positively established.
MUTATION_CONFIRMED           — the required mutation/post-state was positively verified.
MUTATION_OUTCOME_AMBIGUOUS   — a mutating request may have committed, but available evidence cannot determine the external result.
RECOVERED_BY_RECONCILIATION  — an initially ambiguous or incomplete outcome was resolved by explicit reconciliation of authoritative external state.
FAIL_CLOSED                  — execution stopped without claiming success because the required safety state or mutation outcome could not be established.
```

`MUTATION_OUTCOME_AMBIGUOUS` is not success and must never be normalized directly to `MUTATION_CONFIRMED`.

Outcome normalization rules:

- proven non-commit maps to `MUTATION_NOT_APPLIED` and remains non-success;
- proven required post-state maps to `MUTATION_CONFIRMED`;
- neither proven maps to `MUTATION_OUTCOME_AMBIGUOUS` until reconciliation.

Initial normalization produces exactly one of `NO_MUTATION`, `MUTATION_NOT_APPLIED`, `MUTATION_CONFIRMED`, or `MUTATION_OUTCOME_AMBIGUOUS`. If explicit reconciliation is required, the trace retains that initial class and the authoritative state it later establishes, while the final outcome becomes exactly one of:

- `RECOVERED_BY_RECONCILIATION` only when authoritative external-state reconciliation positively establishes a safe resolved result; or
- `FAIL_CLOSED` when reconciliation is not performed, fails, remains ambiguous, or cannot prove every required safety condition.

`RECOVERED_BY_RECONCILIATION` and `FAIL_CLOSED` are terminal and mutually exclusive. `FAIL_CLOSED` takes precedence whenever the proof required for recovery is incomplete; an attempt cannot emit both classes or retain `MUTATION_OUTCOME_AMBIGUOUS` as a successful terminal result.

For NET-BEFORE-SEND and NET-AFTER-SEND-BEFORE-COMMIT fault classes, apply the normalization rules above. For NET-AFTER-COMMIT-BEFORE-RESPONSE and NET-PARTIAL-RESPONSE classes, ambiguity is expected until reconciliation establishes a safe authoritative outcome.

The current Collection Argo CD crash-before-checkpoint residual divergence should become one of the first characterized cases.

### TST-03 — Generative differential parity

Execute one canonical scenario through semantically equivalent Python and Collection test adapters.

```text
SafetyScenario
    │
    ├──── Python adapter ──── trace A
    │
    └──── Ansible adapter ─── trace B

                  compare
```

First candidates:

1. validation/preflight decision;
2. BackupSchedule pause/enable;
3. Argo CD pause safety;
4. checkpoint resume decision.

Activation/finalization can follow once trace semantics are proven.

Do not build the comparison around mocks that merely return the expected answer. The adapters must drive the existing implementation boundary far enough that the behavior under comparison is real.

### TST-04 — Deterministic interleaving

Initial scenarios include stale `resourceVersion`, delete/recreate with a new UID between GET and PATCH, ApplicationSet child recreation, and Argo self-heal between pause and verification.

All schedules are explicit and deterministic. A failing schedule must render as a concise chronological trace.

### TST-05 — Coverage-guided fuzzing

Initial target restrictions:

```text
checkpoint decode/normalize
report decode/write normalization
redaction
path validation
version parsing
legacy-state migration
Kubernetes object normalization
Argo Application metadata parsing
```

Do not fuzz live clusters, full CLI switchover subprocesses, real Ansible execution, or the release certification entrypoint during the initial slice.

Tool selection is intentionally deferred to TST-05 design/implementation. A short spike should compare available Python coverage-guided options against Python-version support, pytest integration, corpus persistence, sanitizer/crash reporting, and CI/runtime overhead.

Every confirmed defect produces both a minimized fuzz corpus input and an ordinary deterministic regression test.

### TST-06 — Combinatorial / t-way testing

Build a constrained factor model rather than a Cartesian-product test matrix.

Candidate factors:

```text
implementation
method
execution mode
restore-only
old-hub action
Argo mode
Argo install type
checkpoint state
observability state
managed-cluster count
ACM version band
API result class
RBAC mode
```

Initial target: 2-way as a tooling proof, then 3-way for safety-relevant combinations. Selected high-risk factor groups may receive 4-way coverage.

The generated suite must publish factor definitions, constraints, rows executed, interaction strength, and uncovered interactions if any. A claim such as "3-way coverage" must be mechanically measurable.

### TST-07 — Metamorphic testing

Initial transformations include API-list reordering, pagination-boundary changes, irrelevant-label/resource insertion, repeat execution of completed idempotent operations, equivalent JSON ordering, non-authoritative display-alias changes, and safe timestamp variation within an allowed window.

A metamorphic relation must document which fields are intentionally invariant and which are allowed to change.

### TST-08 — Formal controller model

TST-08 models the **controller safety protocol**, not Python/Ansible source code.

The model's candidate state space, its mapping onto controller decisions, and the disposition semantics it must
preserve are specified in tracker #234 and derive from the Phase-9 authority cited in §5.4. TST-00 does not duplicate
them; the model must take the cited authority as normative and enumerate no disposition the authority does not define.

Candidate actions include discovery, identity/role proof, authorization, mutation, verification, evidence invalidation, transport failure, recovery requirement, and handoff.

Core properties include:

```text
MutationStarted
    => IdentityProven
    /\ RoleProven
    /\ KnownState
    /\ AuthorizationCurrent

MutationsPerSegment <= 1

Handoff
    => PostStateProven

EvidenceStale
    => ~AuthorizationCurrent

UnknownState
    => ~MayMutate
```

These predicates are shorthand for current-segment, fresh, identity-bound evidence under the stable invariant
catalogue. `RoleProven` includes the LAB-002 physical/logical-role binding, and a final `Handoff` claim includes the
LAB-005 through LAB-007 final physical-identity, logical-role, and known-state proof. A pre-mutation adapter handoff is
modeled separately and never satisfies the final `Handoff` predicate.

The formal model must remain traceable to Phase-9 source contracts and issues. A model-checker PASS is design evidence, not proof that Python implementation conforms. Separate refinement/implementation tests provide that link.

### TST-09 — Controller-owned live resilience

Hard prerequisite: do not implement the live mutation portion until Phase 9C and 9D are complete and independently validated, and at least the Phase 9E/9F single-segment live mutation/parity path is proven.

The fault mechanism receives its exact authorized target from the controller. It does not decide which hub, role, or resource to target, whether retry is safe, or whether recovery is allowed.

Candidate eventual faults include bounded API unavailability, selected pod termination, bounded network delay/loss, Argo reconciliation interference, and controlled controller/operator restart. Each requires separate safety classification.

Live resilience artifacts remain distinct from normal certification evidence unless a later explicitly approved release contract integrates them.

### TST-10 — Controller-owned endurance/soak

TST-10 should supersede the *authority model* of the legacy direct-kubectl E2E soak, not necessarily delete that test immediately.

Target lifecycle:

```text
fresh physical-identity proof
  ↓
fresh logical-role and known-state proof
  ↓
new one-use profile binding
  ↓
new controller authorization
  ↓
one switchover
  ↓
fresh final physical-identity, logical-role, and known-state proof
  ↓
cooldown
  ↓
fresh rediscovery
  ↓
fresh physical-identity proof
  ↓
fresh logical-role and known-state proof
  ↓
new reverse-leg one-use profile binding
  ↓
new controller authorization
  ↓
reverse switchover
  ↓
fresh final physical-identity, logical-role, and known-state proof
```

repeat.

Each forward or reverse mutation is a separate known-state segment. A role transition invalidates the prior profile
and authorization; neither can be reused after rediscovery or for the reverse leg.

TST-00 fixes the following safety properties for the soak evidence. It does not fix the record format.

- Per-cycle evidence aggregates one immutable record for every known-state segment **actually instantiated**. A
  completed cycle contains exactly one forward and one reverse record; a lawfully stopped cycle contains fewer.
- The absence of a record is never, by itself, evidence that a leg did not start. A missing record is lawful only
  when separate evidence positively proves the segment was never instantiated. No record may be fabricated.
- Each instantiated segment is independently authorized and independently proven. Forward and reverse legs share no
  profile binding, authorization, or freshness proof, and no proof produced in one segment satisfies another
  segment's freshness requirement. Rediscovery alone satisfies no proof obligation.
- A profile binding is one-use. Reuse after consumption or role transition invalidates authorization, blocks
  mutation, and prevents continuation; a missing or failed consumption result has the same effect.
- Obligation evidence distinguishes positively resolved, unresolved, and unknown obligation state. Absence from the
  unresolved list is not proof of resolution, and unknown or contradictory obligation evidence blocks continuation.

Which controller disposition applies at any stop boundary, its precedence, and what continuation it permits are
decided by the Phase-9 authority cited in §5.4.

The concrete per-segment and cycle-termination record schemas — field lists, identifier shapes, issuance and
consumption results, and freshness-evaluation records — are specified in tracker #236 and are written when TST-10's
prerequisites (#192–#195, #121) are met.

All identifiers and proof references must be safe evidence references. Evidence must not expose raw kubeconfigs,
bearer tokens, credentials, certificates, private controller enrollment identifiers, or sensitive filesystem paths.

TST-10 must use the #121 release framework's eventual bounded failure-budget semantics rather than establishing an independent budget engine.

## 12. CI and execution tiers

### Tier A — required PR gate

Eventually suitable after runtime measurement:

```text
existing normal tests
bounded TST-01
selected deterministic TST-02
bounded TST-03
selected TST-04
TST-07
```

Nothing should become required merely because TST-00 lists it. Each slice first lands report-only/on-demand where necessary and demonstrates acceptable runtime/flakiness.

### Tier B — scheduled/manual deep verification

```text
TST-01 deep state machines
larger TST-02 failure matrix
larger TST-04 schedule exploration
TST-05 fuzzing
full TST-06 t-way matrix
mutation testing
TST-08 model checker
```

Existing mutation testing remains outside `./run_tests.sh` unless a separate future decision changes that policy.

### Tier C — controller-gated lab

```text
TST-09
TST-10
live release certification
```

Tier C cannot be triggered implicitly by root tests.

## 13. Interaction with existing testing programs

### PBT

TST-01 and TST-07 extend Hypothesis infrastructure rather than fork it. Reuse `tests/properties/strategies.py`, Hypothesis profiles, semantic-generator conventions, and counterexample shrinking. Do not rewrite completed PBT suites.

### Mutation testing

Mutation results become a risk-selection source. Mutation remains diagnostic.

### Release validation / #121

TST-09/TST-10 are downstream hardening techniques for the controller/release architecture. They do not create another live profile format, GO/NO-GO authority, recovery authority, or artifact-eligibility engine.

### Existing E2E

Existing E2E remains useful for regression and historical coverage. Controller-governed TST-09/TST-10 gradually become the preferred path for safety-sensitive repeated live mutation experiments.

## 14. Proposed repository layout

No files other than this design are created by TST-00, but the recommended future test-only shape is:

```text
tests/
  safety/
    __init__.py
    models.py
    invariants.py
    trace.py
    faults.py
    evidence.py
    adapters/
      python.py
      ansible.py

    stateful/
    fault_injection/
    differential/
    interleaving/
    metamorphic/
    combinatorial/
```

Do **not** move existing `tests/properties/` merely for naming consistency. TST code may import reusable strategies from it where the dependency remains test-only and clean.

Fuzz and formal-model artifacts may need separate locations after their tooling is chosen.

## 15. Hard boundaries

TST work must not:

1. weaken existing unit/PBT/parity assertions to make generated tests pass;
2. normalize away safety-relevant Python/Ansible differences;
3. introduce production imports between Python and Ansible implementations;
4. add implicit kubeconfig/context discovery;
5. turn fake or fault-injection evidence into live certification evidence;
6. introduce unbounded randomized live chaos;
7. allow a chaos tool to choose mutation targets independently of the lab controller;
8. execute more than one lab-mutating scenario inside a Phase-9 known-state segment;
9. use automatic recovery after mutation without fresh evidence and explicit controller authorization;
10. modify `docs/ACM_SWITCHOVER_RUNBOOK.md` or `.claude/skills/**/*.skill.md` without explicit operator approval.

## 16. TST-00 publication scope

TST-00 itself is documentation-only.

This publication must make no changes to:

```text
requirements*.txt
setup.cfg
run_tests.sh
.github/
tests/
lib/
modules/
ansible_collections/
tests/release/
```

No production parity impact is created by TST-00.

## 17. TST-00 acceptance criteria

TST-00 is complete when independent review confirms:

- the exact `origin/ansible` baseline is recorded;
- existing PBT, mutation, reliability, E2E, parity, and release-controller capabilities are accurately represented;
- TST-01…10 do not duplicate existing testing programs;
- production, parity, and lab-controller invariants are separated;
- invariant IDs are stable and machine-testable;
- the canonical scenario model is implementation-neutral;
- the trace model cannot silently hide safety-relevant differences;
- the fault taxonomy distinguishes committed-but-unacknowledged mutations from ordinary request failure;
- all advanced evidence classes remain non-certification by default;
- TST-09/TST-10 remain subordinate to controller authority and Issue #121;
- Phase-9 prerequisites reflect current 9C–9F sequencing;
- protected files remain untouched;
- no implementation authorization is implied by approving the design document.

**Explicitly out of scope for TST-00 review.** TST-00 is a foundation document: it fixes invariant families, the
scenario and trace models, the fault and evidence taxonomies, the dependency order, and the authority boundaries.
It is *not* the implementation specification for any slice.

Specification completeness for a slice whose prerequisites are still open — TST-08 (#234), TST-09 (#235), and
TST-10 (#236), all gated behind #192–#195 and #121 — is therefore not a TST-00 acceptance criterion. Record-level
schemas, field lists, and state-machine detail for those slices belong to their own trackers and are written when
their prerequisites are met. A review finding of that kind is valid, but it is a finding against the slice tracker,
not a defect blocking this document's publication.

Likewise, TST-00 defers to the Phase-9 authority documents cited in §5.4 rather than restating them. A finding that
TST-00 omits a controller decision, disposition, or transition defined there is resolved by citation, not by
copying the authority into this document.

## 18. Implementation sequence after TST-00 approval

Recommended order:

```text
Wave 1
  TST-01  Stateful model
  TST-02  Crash/fault framework
  TST-07  Metamorphic relations

Wave 2
  TST-03  Differential parity
  TST-04  Deterministic interleavings

Wave 3
  TST-05  Coverage-guided fuzzing
  TST-06  t-way combinations

Parallel design assurance
  TST-08  Phase-9 formal model

After Phase 9C–9F prerequisites
  TST-09  Controller-owned live resilience
  TST-10  Controller-owned soak/endurance
```

This sequence does not authorize TST-01 implementation. TST-01 may be prepared only under its own separately approved issue after TST-00 publication, unless a later dependency review shows that a smaller TST-07 slice should precede it.

## 19. Governance for TST-01…TST-10

Each TST slice requires its own issue and independent scope.

Every issue must state:

```text
failure class addressed
invariants covered
source/test targets
Python impact
Collection impact
Phase-9 impact
live/non-live classification
evidence class
CI placement
runtime/resource bounds
non-goals
acceptance criteria
dependencies
```

Each slice follows the repository's three-stage workflow:

```text
Builder
   ↓
Independent validator
   ↓
PR-comment resolver / final validator
```

The validator must work from a clean independent checkout/worktree and verify the actual implementation rather than accepting builder-generated evidence at face value.

## 20. Approval boundary

This design was approved by the operator for **publication and tracker creation only**.

Approval of TST-00 does **not** authorize implementation of TST-01…TST-10.

After TST-00 publication and independent validation, the next governed step is a focused TST-01 implementation plan. TST-01 code changes require separate operator approval of that exact plan.
