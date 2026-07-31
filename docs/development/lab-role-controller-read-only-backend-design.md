# Lab Role Controller Read-Only Backend Design

## Status

This is a proposed backend design. Phase 8G adds a pure read-only backend interface skeleton, but it does not implement
a transport backend.

It does not contact live clusters. It does not read kubeconfigs. It does not load real live config files. It does not
execute `oc`, `kubectl`, or `ansible-playbook`. It does not invoke live adapters. It does not enable live ACM
certification. It does not enable mutation. It does not enable automatic recovery. The current implementation remains
non-live.

Phase 8F defines how a later read-only discovery backend should consume the Phase 8C `ExternalLiveLabConfig` model and
the Phase 8E read-only discovery guardrails before transport implementation exists. Phase 8G turns the design into
typed request/result/evidence contracts, deterministic validators, artifact-safe summaries, a backend protocol, and an
`UnimplementedReadOnlyDiscoveryBackend` that fails closed with `BLOCKED`. Transport execution, live cluster contact,
runtime config loading, and production artifact schema finalization remain future work.

## Scope

In scope:

- future read-only backend architecture
- component responsibilities
- request and result contracts
- transport abstraction design
- query planning design
- evidence collection design
- Phase 8C config model consumption
- Phase 8E guardrail integration
- artifact and redaction contract
- failure decision mapping
- future implementation phases and tests

Out of scope:

- implementing a live backend
- loading live config from disk
- reading kubeconfigs
- reading environment credentials
- contacting API servers
- running `oc` or `kubectl`
- running `ansible-playbook`
- invoking live adapters
- mutation
- restore
- decommission
- automatic recovery
- Agent-driven live operation
- production JSON schema finalization

## Current Foundation

Phase 8C added the external live lab config model in `tests/release/lab_controller/live_config.py`. The model is an
in-memory dataclass and validation layer only. It provides `ExternalLiveLabConfig`, runtime-only field sensitivity,
sanitized summaries, L0-L10 gate names, fail-closed validation, and disabled execution-policy defaults. It does not load
real config files, read kubeconfigs, read environment credentials, contact clusters, run commands, write live artifacts,
or enable live execution.

Phase 8D documented the read-only discovery contract in
`docs/development/lab-role-controller-read-only-discovery-design.md`. That design defines live gate requirements,
runtime-only input boundaries, future backend interfaces, identity evidence, logical role evidence, managed cluster set
evidence, artifact requirements, redaction policy, and failure decisions. It does not implement discovery.

Phase 8E added pure read-only discovery guardrails in `tests/release/lab_controller/read_only_discovery.py`. Those
guardrails classify query families, verbs, scenarios, gate sets, query plans, and provisional artifact fields. They fail
closed on unknown or unsafe inputs, require L0-L9 before read-only contact, exclude L10 from read-only authorization,
reject mutation even when L10 is present, and keep `live_certification_evidence=false`.

The current controller and CLI remain non-live. `scripts/release/run_lab_role_controller.py` supports only `fake`,
`release-framework-dry-run`, and explicitly gated local fake-harness execution. Live modes fail closed. Phase 8F depends
on the Phase 8E guardrails but does not instantiate a backend.

## Backend Architecture Overview

A future read-only backend should be a controller-owned pipeline with narrow, typed boundaries. The backend must not
accept arbitrary commands or raw runtime config. Every query must be planned, classified, guardrail-validated, executed
through a constrained transport, converted into evidence, redacted, and then classified into a controller decision.

Conceptual components:

- `ReadOnlyDiscoveryOrchestrator`: owns the end-to-end request lifecycle, gate ordering, and decision handoff to the
  existing lab role controller.
- `ReadOnlyDiscoveryBackend`: receives a validated request, coordinates planning and evidence collection, and returns a
  `ReadOnlyDiscoveryResult`.
- `ReadOnlyQueryPlanner`: produces deterministic structured query plans from validated config summaries and scenario
  policy.
- `ReadOnlyTransport`: future transport interface for structured read-only query objects only.
- `HubEvidenceCollector`: coordinates per-hub evidence collection and merges collector outputs.
- `IdentityEvidenceCollector`: produces physical hub identity evidence.
- `RoleEvidenceCollector`: produces active/passive logical role evidence.
- `ManagedClusterEvidenceCollector`: produces exact managed cluster set evidence.
- `RbacReadinessCollector`: produces read prerequisite evidence without RBAC mutation.
- `DiscoveryArtifactBuilder`: builds provisional publishable artifact payloads.
- `DiscoveryRedactor`: rejects, redacts, or fingerprints unsafe runtime and response data before publication.
- `DiscoveryDecisionClassifier`: maps config, guardrail, transport, evidence, and redaction outcomes to decisions.

Text diagram:

```text
ExternalLiveLabConfig
  -> live gate evaluation
  -> read-only query planner
  -> Phase 8E guardrail validation
  -> transport execution (future only)
  -> evidence collectors
  -> redaction
  -> decision classifier
  -> discovery artifact
```

Transport execution is not part of Phase 8F or Phase 8G. This document only defines the future shape and constraints.

## Request Contract

A future `ReadOnlyDiscoveryRequest` should be an immutable, structured request assembled by the controller after the
caller has already supplied a validated `ExternalLiveLabConfig` object. The request must not load or parse real files.

Fields:

- `request_id`
- `scenario_id`
- `plan_id`
- `validated_config_summary`
- `runtime_only_hub_refs`
- `expected_physical_labels`
- `expected_managed_cluster_names`
- `required_gate_status`
- `query_plan`
- `redaction_policy`
- `artifact_policy`
- `retry_policy`
- `live_execution_enabled`
- `mutation_enabled=false`
- `live_certification_evidence=false`

Rules:

- `runtime_only_hub_refs` are not artifact-facing and must never be copied into publishable summaries.
- `validated_config_summary` comes from Phase 8C validation and redaction helpers, not from live file loading.
- `expected_physical_labels` and `expected_managed_cluster_names` come from the validated config model.
- `query_plan` must pass Phase 8E guardrails before contact.
- `required_gate_status` must satisfy L0-L9 before contact.
- `mutation_enabled` must be false.
- `live_certification_evidence` must remain false for the first read-only phases.
- The request must be rejected if Phase 8C validation fails, Phase 8E guardrails fail, or redaction policy is missing.

## Result Contract

A future `ReadOnlyDiscoveryResult` should be a structured controller result that is safe to summarize without exposing
runtime-only inputs.

Fields:

- `decision`
- `request_id`
- `scenario_id`
- `physical_identity_evidence`
- `logical_role_evidence`
- `managed_cluster_set_evidence`
- `read_prerequisite_evidence`
- `gate_status`
- `query_results_summary`
- `redaction_status`
- `retry_allowed`
- `manual_recovery_required`
- `first_blocking_reason`
- `live_certification_evidence=false`
- `runtime_inputs_redacted=true`
- `artifact_safe_summary`

Rules:

- `decision` uses the existing controller vocabulary: `PASS`, `BLOCKED`, `NO_GO`, `RECOVERY_REQUIRED`, and
  `INFRA_RETRYABLE`.
- `runtime_inputs_redacted` must be true for any artifact-facing result.
- `live_certification_evidence` remains false unless a later audited phase changes that contract.
- A result produced after failed redaction is not publishable certification evidence and must map to `NO_GO` or
  `BLOCKED` according to where the failure happened.

## Transport Abstraction

The future `ReadOnlyTransport` must be constrained enough that the query planner, not an Agent or shell string, owns all
live contact semantics.

The transport should:

- accept structured read-only query objects
- not accept arbitrary shell strings
- use explicit runtime credential handles only
- not inherit `os.environ` wholesale
- never expose raw kubeconfig paths or API URLs in artifacts
- return structured response summaries
- provide timeout and error categories
- mark whether any live contact occurred
- never perform mutation in read-only mode

The transport must reject:

- unknown query families
- mutating verbs
- arbitrary commands
- Agent-invented commands
- secret-bearing queries
- unsafe artifact-facing payloads

Transport inputs are runtime-only. Transport outputs are not artifact-safe until `DiscoveryRedactor` accepts or
summarizes them.

## Query Planner Design

The future `ReadOnlyQueryPlanner` converts a validated request into deterministic query objects. It must be smaller and
stricter than a command builder: it emits only known query families, known read-only verbs, known hub targets, and known
artifact summary fields.

It must:

- generate only Phase 8E-valid read-only query plans
- validate every planned query with `read_only_discovery.py` guardrails
- require L0-L9 gate satisfaction
- not use L10 to authorize mutation
- keep query plans deterministic
- produce artifact-safe query summaries
- fail closed on unknown scenario or query family
- require exact managed cluster expectations

The planner should produce query plan objects with:

- `query_id`
- `scenario_id`
- `hub_label`
- `query_family`
- `verb`
- `resource_family`
- `required_gate_ids`
- `expected_artifact_fields`
- `redaction_requirements`
- `may_expose_secrets=false`
- `mutates_state=false`
- `uses_arbitrary_command=false`
- `agent_invented=false`

Planner output becomes executable only after every query plan passes Phase 8E validation. Conditional Phase 8E query
families, such as Argo CD status or SubjectAccessReview-style checks, remain blocked until a separate audited design
adds the scenario-specific proof field required to allow them.

## Evidence Collection Design

Evidence collectors should consume structured transport responses and produce redacted evidence models. They must not
execute transport calls themselves unless the orchestrator explicitly passes a guarded query result into them.

### Physical Identity Evidence

Physical identity evidence should include:

- `kube-system` namespace UID fingerprint
- API identity fingerprint
- OpenShift version summary
- ACM, MCE, and MCH evidence summary
- expected identity fingerprint comparison
- signal count and match or mismatch summary

Rules:

- Context names and kubeconfig references are not identity proof.
- At least two independent identity signals are preferred where practical.
- Identity mismatch and swapped identity map to `NO_GO`.
- Missing identity proof before contact maps to `BLOCKED`; missing or contradictory live evidence after read contact
  maps to `NO_GO` or `RECOVERY_REQUIRED` depending on the evidence.
- UID values are fingerprinted when policy treats them as private.

### Logical Role Evidence

Logical role evidence should include:

- managed cluster ownership or presence
- active and passive evidence categories
- backup, restore, and passive indicators
- ambiguity status
- previous artifact reference status as supporting only, never sole proof

Rules:

- Both hubs active maps to `NO_GO`.
- Neither hub active maps to `RECOVERY_REQUIRED`.
- Ambiguous role evidence fails closed.
- A previous artifact may explain expected state but must never prove current live state alone.

### Managed Cluster Evidence

Managed cluster set evidence should include:

- expected names
- observed names
- missing names
- extra names
- exact match result
- unexpected cluster policy

Rules:

- Expected names come from the validated Phase 8C config model.
- `exact_match_required` must be true.
- `unexpected_cluster_policy` must be `block`.
- Missing or extra clusters block certification.
- Artifact policy may choose names, counts, hashes, or fingerprints depending on lab privacy requirements.

### RBAC And Read Prerequisite Evidence

RBAC/read prerequisite evidence should include:

- read capability list
- allow or deny status
- missing capability summary
- no RBAC mutation

Rules:

- The initial backend must not bootstrap RBAC.
- SubjectAccessReview-style checks remain conditional until separately designed as a non-mutating query family.
- Missing read prerequisites map to `NO_GO` after read contact or `BLOCKED` when the plan cannot safely collect them.

## Decision Classification

`DiscoveryDecisionClassifier` maps outcomes to the controller decision vocabulary.

`PASS` requires:

- all gates satisfied
- all queries guardrail-valid
- identity proven
- role state proven
- managed cluster set exact
- redaction passed
- no mutation
- `live_certification_evidence=false` unless a later audited phase changes this

`BLOCKED` applies to:

- invalid config
- missing runtime handles
- missing gates
- invalid query plan
- unsupported scenario
- forbidden command or query
- guardrail failure before live contact

`NO_GO` applies to:

- identity mismatch
- swapped identity
- both hubs active
- managed cluster drift judged unsafe
- redaction failure after evidence collection
- unsafe artifact payload

`RECOVERY_REQUIRED` applies to:

- neither hub active
- ambiguous role evidence requiring manual inspection
- live state cannot be proven after read contact

`INFRA_RETRYABLE` applies only when:

- a read-only query timeout or transient error occurred
- no mutation was attempted
- initial gates and config remain valid
- retry criteria are satisfied

Retry does not mean automatic recovery. A retry still requires an explicit operator action in a later implementation.

## Artifact Contract

Future read-only backend artifacts are runtime outputs and must not be committed. The artifact contract remains
provisional and is not a production JSON schema.

Top-level fields:

- `artifact_version`
- `controller_phase`
- `backend_phase`
- `discovery_mode=read_only`
- `request_id`
- `scenario_id`
- `live_execution_enabled`
- `mutation_enabled=false`
- `live_certification_evidence=false`
- `runtime_inputs_redacted=true`
- `query_plan_summary`
- `query_result_summary`
- `physical_identity_evidence`
- `logical_role_evidence`
- `managed_cluster_set_evidence`
- `read_prerequisite_evidence`
- `transport_summary`
- `gate_status`
- `decision`
- `retry_allowed`
- `manual_recovery_required`
- `first_blocking_reason`
- `redaction_status`

Artifact rules:

- Raw runtime inputs must not appear.
- Query summaries use query IDs, families, verbs, labels, counts, booleans, hashes, and fingerprints.
- Transport summaries record contact status, timeout category, and error category without raw endpoints or credentials.
- Artifacts record whether a plan was blocked before live contact.
- `mutation_enabled` must be false.
- `live_certification_evidence` must be false for the first backend phases.

## Redaction Model

`DiscoveryRedactor` must treat runtime and response values as unsafe until proven otherwise.

Redact, fingerprint, omit, or reject:

- runtime hub refs
- kubeconfig refs
- context refs
- raw API URLs
- UID values, if private
- cluster IDs
- token, password, secret, and credential values
- stdout, stderr, and log-like values
- query response snippets
- approval references
- artifact paths

Artifact-safe forms:

- labels
- counts
- gate IDs
- scenario IDs
- fingerprints
- hashes
- booleans
- decision strings
- redacted summaries

Redaction failure before live contact maps to `BLOCKED` when the request shape is unsafe. Redaction failure after
evidence collection maps to `NO_GO` because the backend cannot safely publish or use the evidence.

## Integration With Existing Controller

The future backend should integrate as an explicit controller backend, not as a new default CLI path.

Rules:

- default CLI remains non-live
- future backend requires explicit live/read-only mode
- future backend consumes the Phase 8C config model
- future backend validates Phase 8E guardrails before contact
- current fake, dry-run, and local harness modes remain unchanged
- Agent instructions remain non-live until a later audited update
- `live_certification_evidence` remains false in initial read-only backend phases

Integration shape:

- A later controller entrypoint creates `ExternalLiveLabConfig` from a caller-supplied object that has already been
  obtained outside this repository's committed files.
- The controller validates the config with `validate_external_live_lab_config`.
- The controller builds `ReadOnlyDiscoveryRequest` from the redacted config summary and runtime-only handles.
- The backend validates the request and every query through Phase 8E guardrails.
- Only a later phase may add transport execution behind explicit live/read-only gates.
- The resulting evidence can feed the existing identity, role, managed-cluster, recovery, and artifact vocabulary after
  type-specific adapters are designed.

## Test Requirements For Future Backend Implementation

Before implementation can be considered ready, tests must prove:

- no live backend default
- backend cannot run without explicit live/read-only mode
- backend cannot run without validated config
- backend cannot run without L0-L9 gates
- backend rejects L10 mutation authorization
- backend rejects mutating verbs
- backend rejects arbitrary commands
- backend rejects Agent-invented commands
- backend rejects secret-bearing queries
- backend validates every query through Phase 8E guardrails
- backend redacts runtime refs
- backend redacts API URLs and private IDs
- backend records `live_certification_evidence=false`
- identity mismatch maps to `NO_GO`
- both hubs active maps to `NO_GO`
- neither hub active maps to `RECOVERY_REQUIRED`
- managed cluster drift blocks
- transport timeout maps to `INFRA_RETRYABLE` only under strict criteria

Tests must remain non-live by default. Any future live test path must be opt-in, clearly named, and impossible to trigger
from implicit local environment state.

## Future Implementation Sequence

Recommended staged sequence after Phase 8G:

- Phase 8G: read-only backend interface skeleton, no transport implementation (complete)
- Phase 8H: fake transport contracts and contract tests, no live contact (complete)
- Phase 8I: read-only live transport design review (complete)
- Phase 8J: first opt-in read-only live transport implementation behind explicit gates (complete)
- Phase 8K: read-only live preflight pilot design, no pilot execution (complete)
- Phase 8L: read-only live preflight pilot dry-run or fake-backed rehearsal (next)
- Later audited phase: read-only live pilot audit and closeout after separately approved live contact

Do not implement mutation next. Mutating live implementation requires a later audited design after the read-only backend
contract, fake transport, live transport review, pilot design, dry-run/rehearsal, any separately approved live-contact
pilot, and audit have proven identity, role, managed-cluster, guardrail, artifact, and redaction behavior.

## Documentation Integration

This design should be linked from:

- `docs/development/lab-role-controller-read-only-discovery-design.md`
- `docs/development/lab-role-controller-live-readiness-design.md`

The Phase 8I read-only live transport design review that this design recommends now exists at
[`lab-role-controller-read-only-live-transport-design-review.md`](lab-role-controller-read-only-live-transport-design-review.md).
It remains design-only and recommends `READY_FOR_PHASE_8J_OPT_IN_READ_ONLY_LIVE_TRANSPORT_IMPLEMENTATION`
without adding any live transport.

The Phase 8K read-only live preflight pilot design now exists at
[`lab-role-controller-read-only-live-preflight-pilot-design.md`](lab-role-controller-read-only-live-preflight-pilot-design.md).
It remains design-only, does not run a pilot, does not contact clusters, does not read kubeconfigs, and recommends
`READY_FOR_PHASE_8L_READ_ONLY_LIVE_PREFLIGHT_PILOT_DRY_RUN`.

Documentation guardrails should pin that Phase 8F remains design-only, Phase 8G remains interface-only, Phase 8H adds
fake transport contracts only, the code defines request/result contracts, constrains future transport, consumes Phase
8C and Phase 8E, keeps current controller defaults non-live, and recommends Phase 8I read-only live transport design
review rather than live implementation.

Protected operational runbooks remain read-only.

## Phase 8G Status

Phase 8G adds the read-only backend interface skeleton in
`tests/release/lab_controller/read_only_backend.py`, with focused tests in
`tests/release/test_lab_controller_phase8g_read_only_backend_interface.py`.

The Phase 8G code defines pure typed contracts for future read-only discovery requests, results, evidence, query-plan
bundles, runtime-only hub references, transport summaries, artifact-safe summaries, and backend interface decisions.
It consumes Phase 8C redacted config summaries and Phase 8E guardrail results, validates that L0-L9 guardrails passed
before any future contact, and keeps `mutation_enabled=false` and `live_certification_evidence=false`.

`UnimplementedReadOnlyDiscoveryBackend` is intentionally fail-closed. It validates requests, then returns `BLOCKED`
with no live contact, no query execution, runtime inputs redacted, mutation disabled, and live certification evidence
disabled. It exists so later phases can type against the backend protocol without accidentally adding transport.

Phase 8G remains non-live. It does not implement transport, fake transport execution, live transport execution, live
config loading, kubeconfig reading, environment credential reading, live cluster commands, release adapter execution,
live discovery, mutation, automatic recovery, committed generated profiles, `.release` runtime output, production JSON
schema finalization, or Agent live behavior.

## Phase 8H Status

Phase 8H adds deterministic fake read-only transport contracts in
`tests/release/lab_controller/read_only_transport.py`, with focused tests in
`tests/release/test_lab_controller_phase8h_fake_transport_contracts.py`. It adds fake transport contracts only, not a
real transport backend.

The Phase 8H code defines the structured query a future transport would receive (`ReadOnlyTransportQuery`), the
deterministic fake response it returns (`ReadOnlyTransportResponse`), in-memory response fixtures
(`FakeTransportFixture`), a deterministic `FakeReadOnlyTransport`, and artifact-safe transport summaries. Transport
decisions are `PASS`, `BLOCKED`, `NO_GO`, and `INFRA_RETRYABLE`; response statuses are success, blocked, failed,
timeout, and unsafe_payload. Every query is validated through the Phase 8E guardrails before any fixture lookup, so an
invalid query is `BLOCKED` before lookup, a missing fixture is `BLOCKED`, an unsafe payload is `NO_GO`, a non-retryable
timeout is `NO_GO`, and a retryable timeout is `INFRA_RETRYABLE` only when no contact occurred.

A fake transport never sets `live_contact_attempted`, `live_contact_succeeded`, `mutation_attempted`, or
`live_certification_evidence` to true, never mutates state outside its in-memory call log, and records every received
query deterministically. Fixtures must carry artifact-safe payloads, reject duplicate query IDs at construction, and
fail closed. The Phase 8H integration helpers let the Phase 8G request/result skeleton consume fake transport responses
without ever flipping the unimplemented backend to `PASS`.

Phase 8H remains non-live. It does not implement a real transport, contact clusters, load live config files, read
kubeconfigs, read environment credentials, run subprocesses, execute `oc`, `kubectl`, or `ansible-playbook`, call
release adapters, implement live discovery, enable mutation, enable automatic recovery, commit generated profiles, emit
`.release` runtime output, finalize a production JSON schema, or add Agent live behavior. The next phase is a read-only
live transport design review, not live mutation.

## Phase 8J Status

Phase 8J adds the first opt-in, read-only live transport implementation in
`tests/release/lab_controller/read_only_live_transport.py`, with focused tests in
`tests/release/test_lab_controller_phase8j_read_only_live_transport.py` and opt-in pilot scaffolding in
`tests/release/test_lab_controller_phase8j_live_opt_in.py`. It adds an opt-in transport abstraction only; it does not
wire a real client and does not implement live read-only discovery, so `UnimplementedReadOnlyDiscoveryBackend` stays
fail-closed.

The Phase 8J code defines runtime-only handle/context/option types (`RuntimeOnlyLiveHubHandle`,
`RuntimeOnlyLiveTransportContext`, `ReadOnlyLiveTransportOptions`), a controller-owned client protocol
(`ReadOnlyLiveClientProtocol`) that receives structured query objects and returns raw runtime data, typed
transient/timeout/permanent/safety errors, an opt-in guard (`evaluate_read_only_live_contact_guard`), and the
`ReadOnlyLiveTransport` itself. The transport is disabled by default: it returns `BLOCKED` before any client call
unless `allow_live_contact` and `allow_read_only_queries` are exactly true, a runtime handle and an injected client are
present, the L0-L9 gate evidence passes `validate_read_only_discovery_gates`, and the structured query passes the
Phase 8E/8H `validate_transport_query` guardrails. L10 cannot authorize contact or mutation.

The transport classifies responses as `PASS`, `BLOCKED`, `NO_GO`, or `INFRA_RETRYABLE`, records
`live_contact_attempted` and `live_contact_succeeded` accurately, summarizes responses through the existing redaction
helpers (reused, not widened), and rejects unsafe payloads (raw API URLs, kubeconfig-like values, tokens, passwords,
secrets, credentials, private cluster identifiers, command-like strings, `.release` paths, forbidden runtime-only keys,
and over-broad dumps) as `NO_GO`. Tokens, passwords, secrets, and credentials are rejected outright. Every result forces
`mutation_attempted=false` and `live_certification_evidence=false`, and `real_execution_evidence` (set only when the
injected client is actually called) stays distinct from live certification evidence.

Phase 8J remains non-live by default. It ships no real client, contacts no cluster on its own, loads no live config
files, reads no kubeconfigs, reads no environment credentials, runs no subprocesses, executes no `oc`, `kubectl`, or
`ansible-playbook`, calls no release adapters, implements no live read-only discovery, enables no mutation, enables no
automatic recovery, commits no generated profiles, emits no `.release` runtime output, finalizes no production JSON
schema, and adds no Agent live behavior. Live transport is not wired into the CLI or planner. All live pilot tests are
opt-in behind `ACM_ENABLE_LAB_CONTROLLER_LIVE_TRANSPORT_PILOT` and excluded from normal CI; even when enabled they
exercise a fake injected client and never contact a cluster. The next phase is a read-only live preflight pilot design
(`READY_FOR_PHASE_8K_READ_ONLY_LIVE_PREFLIGHT_PILOT_DESIGN`), not live mutation.

## Phase 8K Status

Phase 8K adds the read-only live preflight pilot design in
[`lab-role-controller-read-only-live-preflight-pilot-design.md`](lab-role-controller-read-only-live-preflight-pilot-design.md).
It is design/documentation only. It does not run a pilot, contact live clusters, read kubeconfigs, load live config
files, execute the Phase 8J live transport, run `oc`, `kubectl`, `ansible-playbook`, release adapters, or the pytest
release framework against live clusters, enable mutation, produce live ACM certification evidence, enable automatic
recovery, add Agent-driven live behavior, or change the current non-live defaults.

The Phase 8K design defines the first pilot objective, boundaries, operator prerequisites, runtime-only inputs,
scenario/query allowlists, gate sequence, abort criteria, artifact contract, evidence acceptance criteria, decision
interpretation, manual recovery/retry policy, risk register, and Phase 8L entry criteria. Its recommendation is
`READY_FOR_PHASE_8L_READ_ONLY_LIVE_PREFLIGHT_PILOT_DRY_RUN`; Phase 8L is not a broad live rollout and must start with
a fake-backed or non-contact rehearsal unless separately approved.

## Recommendation

Recommendation: READY_FOR_PHASE_8L_READ_ONLY_LIVE_PREFLIGHT_PILOT_DRY_RUN

Phases 8G-8K are complete (see the phase status sections above); the next phase is a read-only live preflight pilot
dry-run or fake-backed rehearsal, not broad live rollout and not live mutation. The original Phase 8F/8G backend design
recommendation was `Recommendation: READY_FOR_PHASE_8I_READ_ONLY_LIVE_TRANSPORT_DESIGN_REVIEW`, which has since been
satisfied by the Phase 8I review, the opt-in Phase 8J implementation, and the Phase 8K pilot design.
