# Lab Role Controller Read-Only Live Transport Design Review

## Status

This is a design review. It is the final implementation-readiness review before any future phase
may add actual read-only live cluster contact through the lab role controller.

- This is a design review.
- It does not implement live transport.
- It does not contact live clusters.
- It does not read kubeconfigs.
- It does not load real live config files.
- It does not run `oc`, `kubectl`, or `ansible-playbook`.
- It does not invoke live release adapters.
- It does not enable live ACM certification.
- It does not enable mutation.
- It does not enable automatic recovery.
- The current implementation remains non-live.

Phase 8H added deterministic fake read-only transport contracts in
`tests/release/lab_controller/read_only_transport.py` and recommended
`READY_FOR_PHASE_8I_READ_ONLY_LIVE_TRANSPORT_DESIGN_REVIEW`. Phase 8I reviews readiness for a
future opt-in read-only live transport implementation. It does not add that transport, and it does
not change the controller, the CLI, or the Agent away from non-live behavior. The lab role
controller CLI `scripts/release/run_lab_role_controller.py` still supports only `fake`,
`release-framework-dry-run`, and explicitly gated `release-framework-local`; `live` and
`release-framework-live` remain unsupported live modes.

## Final Recommendation

Recommendation: READY_FOR_PHASE_8J_OPT_IN_READ_ONLY_LIVE_TRANSPORT_IMPLEMENTATION

Phase 8J is read-only, opt-in, and gated. Phase 8J is **not** live ACM certification evidence
unless a later audited phase explicitly changes that contract. Phase 8J must not add mutation, must
not add automatic recovery, and must keep every live test path opt-in and impossible to trigger
from implicit local environment state. This review does not recommend live mutation. The hard
entry criteria for Phase 8J are listed in [Phase 8J Entry Criteria](#phase-8j-entry-criteria).

## Scope

In scope:

- implementation-readiness review for a future read-only live transport
- transport mechanism selection
- live-contact gate requirements
- runtime-only credential and kubeconfig boundary
- read-only query allowlist
- response sanitization and redaction design
- live-contact evidence versus certification evidence distinction
- failure classification
- implementation test matrix
- rollback and no-automatic-recovery policy for read-only contact

Out of scope:

- implementation
- live discovery backend
- live config loading
- kubeconfig reading
- `oc` or `kubectl` calls
- `ansible-playbook` calls
- release adapter execution
- mutation
- restore
- decommission
- automatic recovery
- Agent-driven live operation
- production JSON schema finalization

## Current Foundation

The read-only live transport rests on four completed non-live phases plus the unchanged
non-live controller and CLI.

- **Phase 8C external live config model**
  ([`live_config.py`](../../tests/release/lab_controller/live_config.py)) provides
  `ExternalLiveLabConfig`, runtime-only field sensitivity, sanitized example builders, redacted
  summaries, the L0-L10 gate names, and fail-closed validation. It does not load real config files,
  read kubeconfigs, read environment credentials, contact clusters, or enable live execution.
- **Phase 8E read-only discovery guardrails**
  ([`read_only_discovery.py`](../../tests/release/lab_controller/read_only_discovery.py)) classify
  query families, verbs, scenarios, gate sets, query plans, and provisional artifact fields. They
  fail closed on unknown inputs, require L0-L9 before read-only contact, exclude L10 from read-only
  authorization, reject mutation even when L10 is present, and keep
  `live_certification_evidence=false`.
- **Phase 8G backend interface skeleton**
  ([`read_only_backend.py`](../../tests/release/lab_controller/read_only_backend.py)) defines typed
  request/result/evidence contracts and the backend protocol. `UnimplementedReadOnlyDiscoveryBackend`
  validates a request, then fails closed with `BLOCKED`, no live contact, runtime inputs redacted,
  `mutation_enabled=false`, and `live_certification_evidence=false`.
- **Phase 8H fake transport contracts**
  ([`read_only_transport.py`](../../tests/release/lab_controller/read_only_transport.py)) define the
  structured `ReadOnlyTransportQuery`, the deterministic `ReadOnlyTransportResponse`, in-memory
  `FakeTransportFixture` data, the `FakeReadOnlyTransport`, and artifact-safe transport summaries.
  Every query is validated through the Phase 8E guardrails before any fixture lookup. A fake
  transport never sets `live_contact_attempted`, `live_contact_succeeded`, `mutation_attempted`, or
  `live_certification_evidence` to true.
- **Current CLI and controller remain non-live.** The default controller path, dry-run
  materialization, and the gated local fake harness are not live cluster evidence.

Phase 8I reviews readiness for a future transport implementation but **does not add one**. The
review converts the fake transport behavior and the existing guardrails into an explicit, gated
safety contract that a later implementation phase must satisfy before any live read query.

## Transport Mechanism Decision

A future read-only live transport must own all live-contact semantics behind a narrow,
controller-owned, typed interface. The query planner — not an Agent, not a shell string — decides
what is contacted. The following mechanisms were evaluated for the **first** read-only live
implementation.

### 1. Kubernetes / OpenShift Python client

- **Suitability**: High. A typed client returns structured objects, supports explicit per-request
  timeouts, and maps cleanly onto the existing read-only query families and evidence collectors.
- **Security implications**: Credentials are supplied as runtime-only client configuration handles,
  never as committed files. No shell surface is exposed. Endpoint configuration must be runtime-only
  and must never reach artifacts.
- **Redaction implications**: Responses are structured Python objects, so the redactor can allowlist
  specific fields instead of scraping free text. Raw API URLs and identity values must still be
  fingerprinted or rejected before any artifact.
- **Testability**: High. The client can be wrapped behind a controller-owned interface and faked in
  unit tests, preserving behavioral symmetry with the Phase 8H fake transport.
- **Decision**: **Accepted as the preferred backing mechanism**, but only behind a controller-owned
  typed query interface that exposes read-only verbs and known resource families. The raw client is
  never handed to the Agent or to scenario code.

### 2. `oc` / `kubectl` subprocess

- **Suitability**: Low for a first implementation. Command spelling, plugins, and shell quoting
  introduce drift and ambiguity that are hard to classify deterministically.
- **Security implications**: A subprocess surface invites argument injection, environment leakage,
  and arbitrary-command risk. The guardrails already treat `oc`, `kubectl`, and shell tokens as
  command-like and unsafe.
- **Redaction implications**: Output is free text on stdout and stderr, which is the hardest surface
  to prove artifact-safe. Raw API URLs, kubeconfig paths, and identity values can appear anywhere.
- **Testability**: Lower. Faithful faking of a CLI subprocess requires emulating output formats and
  exit codes.
- **Decision**: **Rejected for the first read-only live implementation.** A later design may revisit
  a tightly constrained, allowlisted, read-only suboperation only if it can prove the same safety
  and redaction guarantees as the typed interface.

### 3. Ansible modules / playbooks

- **Suitability**: Out of scope. Playbook execution is a live release-adapter path, not a
  controller-owned read query interface.
- **Security implications**: Extra vars and inventory can leak credential references and paths into
  logs and artifacts.
- **Redaction implications**: Task output and callbacks are verbose and free-form.
- **Testability**: Requires a live Ansible runtime, which contradicts the non-live test default.
- **Decision**: **Rejected / out of scope.** Live adapters remain out of scope for read-only
  discovery.

### 4. Direct HTTPS / raw request library

- **Suitability**: Out of scope for a first implementation. It duplicates client capabilities while
  re-implementing authentication, TLS, and resource modeling by hand.
- **Security implications**: Hand-built requests increase the risk of leaking raw API URLs and
  credential material, and weaken TLS verification guarantees.
- **Redaction implications**: Raw response bodies are unstructured and high-leakage.
- **Testability**: Moderate, but with more error-prone surface than a typed client.
- **Decision**: **Rejected / out of scope** unless a separate design proves it is safer than the
  typed client.

### 5. Existing release adapters

- **Suitability**: Out of scope. Release adapters execute pytest streams, the Python CLI, or the
  Ansible collection against live state, which is live mutation-capable execution.
- **Security and redaction implications**: Broad and mutation-capable; not a read-only surface.
- **Testability**: Not applicable for read-only transport.
- **Decision**: **Rejected / out of scope.**

### 6. Fake transport

- **Suitability**: Test-only. The Phase 8H `FakeReadOnlyTransport` is the contract reference for
  behavioral symmetry, not a live mechanism.
- **Decision**: **Remains test-only.** It must keep proving that a real transport behaves
  identically for blocked, unsafe-payload, timeout, and success classification, without ever
  flipping live-contact or certification flags.

### Recommended Transport Policy

- The first real read-only implementation should prefer a **structured API-client abstraction
  behind a narrowly scoped, controller-owned query interface**.
- Arbitrary shell, `oc`, and `kubectl` subprocess transports remain **rejected** for the initial
  implementation unless a later design explicitly proves equivalent safety.
- Ansible and live release adapters remain **out of scope**.
- Direct raw HTTP remains **out of scope** unless separately designed and audited.
- The fake transport remains **test-only** and is the behavioral-symmetry reference.

## Live Contact Boundary

"Live contact" is any action that observes real cluster state. For this repository, read-only access
is still live execution. The following count as live contact:

- creating an API client from runtime-only kubeconfig or context references
- contacting a hub API server
- listing or getting live resources
- running live SubjectAccessReview-style read checks
- retrieving live status and conditions
- reading live logs or events, if a later phase ever allows them

Live contact is **not** the same as live certification evidence. Observing a cluster does not
certify a switchover.

Rules:

- `live_contact_attempted` may become true only in a future audited implementation phase, never in
  Phase 8I.
- `live_certification_evidence` remains false for the first read-only transport unless a later
  audited phase explicitly changes that contract.
- `mutation_enabled` must remain false.
- Live contact must never occur before all required gates and Phase 8E/8G guardrails pass.

## Required Gates Before Future Live Contact

A future implementation must pass the gate set below before the first live read query. These mirror
the Phase 8A/8C/8E gate vocabulary. L0-L9 are required before read-only contact. L10 is **not**
required for read-only contact and **cannot** authorize read-only contact or mutation in Phase 8J;
it exists only for a separately audited future mutating phase.

| Gate | Implementation check | Failure decision | Artifact evidence | Retry stance |
| --- | --- | --- | --- | --- |
| L0: explicit live mode selected | Caller explicitly selected a live read-only mode with recorded operator approval metadata. | `BLOCKED` before contact. | Selected mode, redacted approval reference, timestamp summary. | Retry only after corrected invocation and fresh approval. |
| L1: expected branch/commit and clean working tree verified | Expected branch, expected commit, and a clean working tree are confirmed. | `NO_GO` for certification; `BLOCKED` for diagnostic readiness. | Commit hash, branch label, redacted status summary. | Retry after checkout correction; no automatic recovery. |
| L2: validated external live config supplied from outside Git | A Phase 8C `ExternalLiveLabConfig` is supplied by the caller from outside Git and passes validation. | `BLOCKED`. | Config schema version, config hash, source category; no raw path. | Retry after the operator supplies valid external config. |
| L3: runtime-only kubeconfig/context/credential references validated | Runtime handles are present, policy-compliant, and not artifact-facing. | `BLOCKED` before contact; `NO_GO` if unsafe values would be published. | Credential-presence booleans and redacted handle fingerprints. | Retry after credential correction and a fresh redaction check. |
| L4: physical identity query plan prepared | The plan can collect identity signals for both physical hubs and carries expected fingerprints from config. | `BLOCKED` if the plan cannot collect evidence; `NO_GO` on mismatch after contact. | Redacted identity signal plan and comparison status. | Retry only before mutation and with fresh approval. |
| L5: logical role query plan prepared | The plan can collect active/passive role evidence for both hubs. | `NO_GO` or `RECOVERY_REQUIRED` depending on observed state. | Role signal categories, ambiguity status, confidence summary. | Retry only after fresh discovery and operator approval. |
| L6: managed cluster expectations available, exact-match active | Exact expected managed cluster names come from validated config; `exact_match_required` is true and `unexpected_cluster_policy` is `block`. | `BLOCKED` if missing; `NO_GO` or `RECOVERY_REQUIRED` on drift. | Expected count, observed count, name comparison summary, optional hashes. | Retry after the operator resolves drift or supplies corrected config. |
| L7: read/RBAC prerequisites available | Read permission plan and ACM/MCE/MCH and backup/restore read prerequisites are available; no RBAC mutation. | `NO_GO` for unsafe or denied prerequisites; `BLOCKED` for a missing plan. | Failing capability list and redacted prerequisite summary. | Retry after remediation and fresh checks. |
| L8: scenario allowlist permits read-only discovery/preflight | The scenario ID is in the audited read-only allowlist. | `BLOCKED`. | Scenario classification, allowlist version, reason. | Retry only after reviewed code or config change. |
| L9: materialized read-only invocation reviewed | The sanitized query plan, redacted environment plan, and artifact plan are reviewed with an operator approval reference. | `BLOCKED`. | Reviewed query plan hash, artifact plan summary, approval reference. | Retry after re-materialization and fresh review. |
| L10: final mutation confirmation | Not required for read-only contact. Must be absent for read-only artifacts. | `BLOCKED` for any attempted mutation; cannot authorize read-only contact or mutation in Phase 8J. | Mutation confirmation must be absent for read-only results. | No automatic retry; mutation requires a separate audited phase. |

L10 cannot be used as a shortcut: even when present, Phase 8E and the read-only transport never
authorize mutation. Read-only contact is authorized only by L0-L9.

## Runtime Credential and Kubeconfig Boundary

The future implementation must keep two strictly separated representations: a runtime execution
context available only to the executing process, and a publishable artifact context that is safe for
operator review. Hard requirements:

- Real kubeconfig contents are never loaded from committed files.
- Kubeconfig and context references are runtime-only handles supplied by the caller.
- Runtime handles are not artifact-facing and must never be copied into summaries.
- The implementation must not inherit `os.environ` wholesale.
- Allowed environment values must be explicit and minimal, declared by the validated config.
- No artifact may contain raw kubeconfig paths, raw API URLs, tokens, secrets, credentials, or
  private cluster IDs.
- Logs, stdout, stderr, and exception messages must be sanitized before publication.
- Credential validation must prove presence and shape without disclosing values.
- Missing or unsafe runtime handles produce `BLOCKED` before any contact.
- Unsafe publication risk after collection produces `NO_GO`.

The redaction and key-substring rejection already present in the Phase 8E/8G/8H modules — rejecting
keys such as `kubeconfig_ref`, `context_ref`, `credential_ref`, `api_url`, `token`, `password`,
`secret`, `raw_command`, and `argv` — must be reused unchanged. The transport must not widen them.

## Read-Only Query Surface

The first implementation may contact clusters only with the conservative families below, and only
after the required gates pass. Families map to the Phase 8E `ReadOnlyQueryFamily` vocabulary.

Allowed for the first implementation, only after gates:

- cluster identity query (`cluster_identity`)
- namespace UID query (`namespace_uid`)
- ClusterVersion / status query (`cluster_version`)
- ACM / MCE / MCH status query (`acm_mce_mch_status`)
- ManagedCluster list / status query (`managed_cluster_status`)
- Backup / Restore / BackupSchedule **status** query (`backup_restore_status`), only if needed and
  proven safe, with no restore creation and no schedule mutation
- Argo CD **status** query (`argocd_status`), only if a scenario requires it and it is proven
  non-mutating
- SubjectAccessReview-style read checks (`subject_access_review`), only if separately classified as a
  non-mutating query family and never used to bootstrap or mutate RBAC

Deferred to later phases:

- logs and events (`logs_events`)
- broad resource dumps
- arbitrary custom resource dumps
- high-volume outputs
- secret-bearing resources
- direct API discovery across all resource types

Forbidden in read-only discovery:

- create, update, patch, delete, apply, scale, rollout, annotate, label, pause, resume, sync,
  refresh, restore, and decommission
- arbitrary shell commands
- Agent-invented commands
- live adapter invocation
- `ansible-playbook` execution

Classification is by operation semantics, not command spelling. Conditional families (Argo CD,
SubjectAccessReview-style checks) stay blocked until a separate audited design adds the
scenario-specific proof field that allows them.

## Query Plan Enforcement

The future implementation must:

- construct structured query objects only, never command strings
- validate every query through the Phase 8E guardrails (`validate_read_only_query_plan`,
  `classify_read_only_verb`, `required_read_only_discovery_gate_ids`)
- validate query bundles through the Phase 8G backend request validation
- reuse the Phase 8H fake transport contract tests for behavioral symmetry between fake and live
  transports
- fail closed on unknown scenario, query family, or verb
- fail closed on any query that may expose secrets
- reject runtime-only fields in artifacts
- reject any query plan that sets `live_certification_evidence=true`
- record query IDs and structured summaries without raw command strings

A plan that fails any guardrail is `BLOCKED` before contact. The planner stays smaller and stricter
than a command builder: it emits only known families, known read-only verbs, known hub targets, and
known artifact summary fields.

## Response Handling and Redaction

Raw transport responses are unsafe until the redactor proves otherwise. Requirements:

- Raw response payloads must not be stored directly in artifacts.
- Response summaries must be structured and allowlisted.
- Raw API URLs are redacted or fingerprinted.
- UIDs are fingerprinted when policy treats them as private.
- Private cluster IDs are redacted or fingerprinted.
- Tokens, passwords, secrets, and credentials are rejected outright.
- Exception messages are sanitized.
- Transport error messages are sanitized.
- High-volume output is blocked or summarized.
- Redaction failure after collection is `NO_GO`.
- A response summary must be useful for human review without leaking secrets.

The transport returns structured response summaries plus timeout and error categories, and marks
whether any live contact occurred. The redactor is the single chokepoint between collected data and
any publishable artifact.

## Evidence Mapping

Response summaries map to evidence types as follows. All evidence fields are redacted or
fingerprinted before they reach an artifact.

**Physical identity evidence:**

- `kube-system` namespace UID fingerprint
- API identity fingerprint
- OpenShift version summary
- ACM / MCE / MCH evidence summary
- expected-versus-observed fingerprint match

**Logical role evidence:**

- ManagedCluster presence and ownership indicators
- active and passive evidence categories
- ambiguity status
- previous-artifact reference as supporting evidence only, never sole proof

**Managed cluster set evidence:**

- expected names
- observed names
- missing names
- extra names
- exact-match result

**Read prerequisite evidence:**

- read capability checks
- prerequisite status categories
- missing or denied capability summary
- no RBAC mutation

Context names and kubeconfig references are never identity proof. At least two independent identity
signals are preferred where practical. Role evidence must be fresh for each run; a previous artifact
may explain expected state but cannot prove current live state by itself.

## Decision Mapping

The classifier maps outcomes to the existing controller decision vocabulary. The mapping is
fail-closed.

**PASS** requires all of:

- gates L0-L9 passed
- every query guardrail-valid
- live contact completed for the allowed reads
- physical identity proven
- logical role proven
- managed cluster set exact
- redaction passed
- no mutation attempted
- `live_certification_evidence=false` unless a later audited phase changes it

**BLOCKED** applies to:

- invalid config
- missing runtime handles
- missing gates
- invalid query plan
- unsupported scenario
- forbidden query before contact
- transport construction blocked before contact

**NO_GO** applies to:

- identity mismatch
- swapped hub identity
- both hubs active
- unsafe managed cluster drift
- redaction failure after collection
- unsafe artifact payload

**RECOVERY_REQUIRED** applies to:

- neither hub active
- ambiguous or unprovable live state after read contact
- live state that indicates manual inspection is required

**INFRA_RETRYABLE** applies only when all of:

- a read-only transient or timeout error occurred
- no mutation was attempted
- no partial unsafe state exists
- gates and config remain valid
- explicit retry criteria are satisfied

`INFRA_RETRYABLE` is not automatic recovery. A retry still requires an explicit human action.

## Artifact Contract

Future Phase 8J artifacts are runtime outputs. They must not be committed, and the contract is
provisional and redacted, not a production JSON schema. Required fields:

- `artifact_version`
- `controller_phase`
- `backend_phase`
- `discovery_mode=read_only`
- `transport_kind`
- `live_contact_attempted`
- `live_contact_succeeded`
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

Artifacts are provisional, redacted, and not a production schema. Raw runtime inputs must never
appear. Query and transport summaries use IDs, families, verbs, labels, counts, booleans, hashes,
and fingerprints, and record whether a plan was blocked before live contact.

## Test Matrix For Phase 8J

The following tests must exist before Phase 8J can pass. All live test paths are opt-in and excluded
from normal CI unless explicitly enabled.

- no live backend default
- explicit opt-in required for any live transport
- no contact before L0-L9 gates pass
- no contact when guardrail validation fails
- no contact when runtime handles are missing
- no contact for a forbidden scenario
- no contact for a mutating verb
- no contact for a secret-bearing query
- query planner emits only structured query objects
- no shell strings in any query plan
- no `oc` or `kubectl` subprocess
- no environment inheritance
- sanitized exception messages
- redaction failure returns `NO_GO`
- timeout returns `INFRA_RETRYABLE` only under the strict criteria
- identity mismatch returns `NO_GO`
- both hubs active returns `NO_GO`
- neither hub active returns `RECOVERY_REQUIRED`
- exact managed cluster match required
- `live_contact_attempted` recorded accurately
- `live_certification_evidence` remains false
- fake and live transport contracts stay behaviorally aligned
- all live tests are opt-in and excluded from normal CI unless explicitly enabled

## Operational Safeguards

- The live mode flag must be explicit and unambiguous, and it must read as deliberately risky.
- The artifact directory must be caller-provided, never defaulted into version control.
- The branch and commit must be recorded for every run.
- The dirty-checkout policy must be enforced before any live contact.
- The operator approval reference is runtime-only and redacted in artifacts.
- There is no automatic recovery.
- The Agent cannot initiate live contact without an explicit human request.
- The Agent cannot supply credentials.
- The Agent cannot retry without an explicit human instruction and `retry_allowed=true`.

## Risk Register

| Risk | Impact | Mitigation | Blocking for Phase 8J |
| --- | --- | --- | --- |
| Credential leakage | Secrets exposed in artifacts or logs | Runtime-only handles, key-substring rejection, presence-only validation, sanitized logs | Yes |
| API URL leakage | Private endpoints exposed | Reject or fingerprint raw API URLs; never store raw endpoints | Yes |
| Accidental mutation | Wrong-state cluster change | Read-only verb allowlist, mutation rejected even with L10, `mutation_enabled=false` | Yes |
| Shell injection | Arbitrary command execution | Typed query interface only; reject shell, `oc`, `kubectl`, and Agent-invented commands | Yes |
| Scenario allowlist drift | Unintended scenario contacts clusters | Fail closed on unknown scenario IDs; catalog-aligned allowlist with an allowlist version | Yes |
| Query output too broad | High-volume or secret-bearing data leaks | Defer logs/events/dumps; block secret-bearing families; bound and summarize output | Yes |
| False role inference | Wrong active/passive decision | Fresh per-run discovery; previous artifact is supporting only; ambiguity fails closed | Yes |
| Both-hubs-active misclassification | Split-brain treated as safe | Map both-hubs-active to `NO_GO`; require exact managed cluster set | Yes |
| Redaction false negative | Unsafe value published as safe | Redaction is the single chokepoint; redaction failure is `NO_GO`; reused, not widened, patterns | Yes |
| Agent overreach | Agent initiates or retries live contact | Controller owns decisions; Agent cannot start contact, supply credentials, or auto-retry | Yes |
| Operator approval ambiguity | Unclear or stale approval authorizes contact | Explicit, redacted, runtime-only approval reference recorded per run; fresh approval on retry | Yes |

## Phase 8J Entry Criteria

Phase 8J may begin only when all of the following hold:

- Phase 8I is accepted with a READY recommendation.
- Phase 8E, 8G, and 8H tests are green.
- A dedicated Phase 8J design audit is complete.
- The read-only query allowlist is explicitly coded.
- The transport implementation plan is reviewed and approved.
- All live tests are opt-in only and excluded from normal CI unless explicitly enabled.
- No mutation verbs are possible through the read-only transport.
- No path can set `live_certification_evidence=true`.
- Rollback and recovery remain manual; there is no automatic recovery.
- An independent review of the implementation is required before merge.

## Documentation Integration

- [`lab-role-controller-read-only-backend-design.md`](lab-role-controller-read-only-backend-design.md)
  carries a pointer to this Phase 8I review.
- [`lab-role-controller-read-only-discovery-design.md`](lab-role-controller-read-only-discovery-design.md)
  already records the future read-only discovery contract; its roadmap is unchanged by this review.
- `tests/test_documentation_guardrails.py` pins this design as design-only and checks it for
  real-looking live-config or credential literals.

This review modifies no protected operational runbooks and adds no Agent live behavior.

## Recommendation

Recommendation: READY_FOR_PHASE_8J_OPT_IN_READ_ONLY_LIVE_TRANSPORT_IMPLEMENTATION

Phase 8J is still read-only, opt-in, and gated. It must not add mutation, and it is not live ACM
certification evidence unless a later audited phase explicitly changes that contract.

## Validation

Run before relying on this design:

- `python -m pytest tests/test_documentation_guardrails.py -q`
- `python -m pytest tests/release/test_lab_controller*.py -q`
- `python -m pytest tests/release -q`
