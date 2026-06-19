# Lab Role Controller Read-Only Discovery Design

## Status

This is a proposed design.

It does not implement read-only discovery. It does not contact live clusters. It does not read kubeconfigs. It does not
enable live ACM certification. It does not enable mutation. It does not enable automatic recovery. The current lab role
controller implementation remains non-live.

Read-only discovery is still live execution because it observes real cluster state. This design defines the future
contract a read-only discovery backend must satisfy before implementation exists. It does not add an executable backend,
does not add live config loading, does not add release adapter execution, and does not change Agent behavior.

## Scope

In scope:

- future read-only discovery backend contract
- required live gates before discovery
- runtime-only input model
- physical hub identity evidence design
- logical role discovery evidence design
- managed cluster set verification design
- read-only command/resource policy
- artifact and redaction requirements
- failure decision model
- test and guardrail requirements for a future implementation

Out of scope:

- implementation of discovery
- live config loading
- kubeconfig reading
- `oc` or `kubectl` execution
- `ansible-playbook` execution
- release adapter execution
- mutation
- restore
- decommission
- Argo CD mutation
- automatic recovery
- production JSON schema finalization
- Agent-driven live operation

## Current Foundation

The Phase 7C closeout recommendation was `READY_FOR_LIVE_READINESS_DESIGN`. Phase 7C confirmed that the controller,
CLI wrapper, local fake harness, artifacts, and Agent instructions are deterministic and non-live.

Phase 8A added live-readiness design. It defined the L0-L10 live gate vocabulary, the "read-only is still live"
boundary, physical identity proof concepts, role discovery concepts, command policy, redaction expectations, and the
first safe live family as read-only discovery plus preflight-only evidence.

Phase 8B added live-readiness guardrails and the external live lab config schema design. It kept all live config files
outside Git, separated runtime-only values from artifact-safe summaries, and documented that production JSON schema
finalization remains unsupported.

Phase 8C added a pure external live config model in
`tests/release/lab_controller/live_config.py`. That model provides in-memory dataclasses, sanitized example builders,
field-sensitivity helpers, redacted summaries, L0-L10 gate names, and fail-closed validation. It does not load live
config files, read kubeconfigs, read environment credentials, run discovery, or enable live execution.

Phase 8D depends on the Phase 8C model as the future input contract. Phase 8D does not load real config files, does not
read runtime credentials, and does not materialize discovery requests.

## Definition of Read-Only Discovery

Read-only discovery is a future controller-owned live backend that collects cluster evidence without changing durable
cluster state. It may perform only explicitly allowlisted read operations after the required live gates pass.

Read-only discovery may include future queries such as:

- querying cluster identity metadata
- querying namespace and resource UIDs
- querying ACM, MCE, and MCH status
- querying `ManagedCluster` resources
- querying Backup, Restore, and BackupSchedule status
- querying Argo CD application or namespace status when relevant
- querying cluster version and operator health
- querying SubjectAccessReview-style read checks if a future design permits them

Read-only discovery must not include:

- create, update, patch, or delete
- apply
- scale
- rollout operations
- pause or resume mutations
- restore creation
- BackupSchedule mutation
- Argo CD mutation
- decommission
- namespace or resource deletion
- arbitrary shell commands
- Agent-invented live commands

Any mutation moves out of Phase 8D scope. A future implementation must classify every planned operation before it runs;
unknown operations are blocked.

## Required Live Gates Before Discovery

Future read-only discovery must pass the Phase 8A and Phase 8B gate model before any live query. L10 is not required for
read-only discovery unless a later audited design says otherwise, because L10 is final confirmation before mutation.

| Gate | Required before read-only discovery | Evidence required | Failure decision | Artifact evidence | Retry/recovery stance |
| --- | --- | --- | --- | --- | --- |
| L0: explicit live mode selected | Yes | Explicit live mode input and operator approval metadata for live read-only contact. | `BLOCKED` before contact. | Selected mode, approval reference summary, timestamp summary. | Retry only after corrected invocation and fresh approval. |
| L1: clean working tree and expected branch/commit verified | Yes | Expected branch, expected commit, clean working tree, release metadata status. | `NO_GO` for certification; `BLOCKED` for diagnostic readiness. | Commit hash, branch label, redacted status summary. | Retry after checkout correction; no automatic recovery. |
| L2: external live lab config provided from outside Git | Yes | Validated Phase 8C `ExternalLiveLabConfig` supplied by the caller from outside Git. | `BLOCKED`. | Config schema version, config hash, source category; no raw path. | Retry after the operator supplies valid external config. |
| L3: runtime-only kubeconfig and credential references validated | Yes | Runtime handles are present, policy-compliant, and not artifact-facing. | `BLOCKED` before contact; `NO_GO` if unsafe values would be published. | Credential presence booleans and redacted handle fingerprints. | Retry after credential correction and fresh redaction check. |
| L4: physical hub identity proof gate initialized | Yes | Query plan includes identity signals for both physical hubs and expected fingerprints from config. | `BLOCKED` if the plan cannot collect evidence; `NO_GO` on mismatch. | Redacted identity signal plan and comparison status. | Retry only before mutation and with fresh approval. |
| L5: logical role discovery gate initialized | Yes | Query plan includes active/passive role evidence for both hubs. | `NO_GO` or `RECOVERY_REQUIRED` depending on observed state. | Role signal categories, ambiguity status, and confidence summary. | Retry only after fresh discovery and operator approval. |
| L6: managed cluster set expectation available | Yes | Exact expected managed cluster names from the validated external config. | `BLOCKED` if missing; `NO_GO` or `RECOVERY_REQUIRED` on drift. | Expected count, observed count, name comparison summary, optional hashes. | Retry after operator resolves drift or supplies corrected config. |
| L7: RBAC/read prerequisites available | Yes | Read permission plan, ACM/MCE/MCH health prerequisites, backup/restore prerequisites. | `NO_GO` for unsafe or denied prerequisites; `BLOCKED` for missing plan. | Failing capability list and redacted prerequisite summary. | Retry after remediation and fresh checks. |
| L8: scenario allowlist permits read-only discovery/preflight | Yes | Scenario ID is in the audited read-only allowlist. | `BLOCKED`. | Scenario classification, allowlist version, and reason. | Retry only after reviewed code or config change. |
| L9: materialized read-only invocation reviewed | Yes | Sanitized query plan, redacted environment plan, artifact plan, and operator review reference. | `BLOCKED`. | Reviewed query plan hash, artifact plan summary, approval reference. | Retry after re-materialization and fresh review. |
| L10: final confirmation before mutation | No by default | Not applicable to read-only discovery. Required only if a later design adds mutation. | `BLOCKED` for any attempted mutation without L10. | Mutation confirmation must be absent for read-only artifacts. | No automatic retry; mutation requires a separate audited phase. |

## Runtime-Only Input Contract

Future discovery receives runtime data from the caller. It must not discover or infer credentials from ambient process
state.

Inputs:

- validated `ExternalLiveLabConfig` object from Phase 8C
- runtime-only kubeconfig references
- runtime-only context references
- explicit allowed environment map, if any
- caller-provided artifact directory
- read-only scenario ID, for example `preflight` or `final-baseline-check`
- expected physical hub labels
- expected managed cluster names
- read-only command/API allowlist

Rules:

- no runtime credential input is artifact-facing
- `os.environ` must not be inherited wholesale
- runtime-only values must not be committed
- runtime-only values must not appear in artifact summaries
- artifact-safe summaries use labels, hashes, fingerprints, counts, booleans, and redacted values only
- missing runtime-only input produces `BLOCKED`
- unsafe artifact-facing runtime data produces `NO_GO`

## Discovery Backend Interface Design

The following are conceptual future types. Phase 8D does not add code for them.

`ReadOnlyDiscoveryBackend`:

- receives a `ReadOnlyDiscoveryRequest`
- validates that the request is read-only
- validates that all required gates through L9 are satisfied
- executes only allowlisted read queries in a later implementation
- returns a `ReadOnlyDiscoveryResult`

`ReadOnlyDiscoveryRequest` fields:

- validated external live config summary
- runtime-only hub access handles
- artifact directory handle
- scenario ID
- required gate status
- query allowlist version
- expected physical hub labels
- expected managed cluster names
- redaction policy
- retry policy

`ReadOnlyDiscoveryResult` fields:

- `decision`: `PASS`, `NO_GO`, `RECOVERY_REQUIRED`, `BLOCKED`, or `INFRA_RETRYABLE`
- physical hub evidence
- logical role evidence
- managed cluster set evidence
- RBAC/read prerequisite evidence
- redaction status
- first blocking reason
- `retry_allowed`
- `manual_recovery_required`
- `live_certification_evidence=false` for the first read-only phases unless a later audited phase changes this

`HubDiscoveryEvidence` fields:

- physical label
- physical identity evidence
- logical role evidence
- managed cluster evidence
- prerequisite evidence
- query summary
- redaction status

`PhysicalIdentityEvidence` fields:

- physical label
- expected identity fingerprint summary
- observed identity fingerprint summary
- signal count
- matched signals
- missing signals
- mismatch reason

`LogicalRoleEvidence` fields:

- inferred logical role
- active evidence categories
- passive evidence categories
- ambiguous evidence categories
- confidence summary
- previous artifact reference status

`ManagedClusterSetEvidence` fields:

- expected names
- observed names
- missing names
- extra names
- exact match result
- unexpected cluster policy

`ReadOnlyResourceQueryPlan` fields:

- query family
- resource family
- verb
- hub label
- required gate
- allowed status
- redaction requirements
- artifact fields emitted

`DiscoveryRedactionReport` fields:

- redaction status
- rejected field count
- fingerprinted field count
- sanitized summary count
- first unsafe field
- safe-to-publish boolean

`DiscoveryDecision` fields:

- decision
- reason
- first blocking reason
- retry allowed
- manual recovery required
- safe to continue
- live certification evidence flag

## Physical Hub Identity Evidence

Future read-only discovery must prove that each operator-provided physical label maps to the intended physical hub.

Candidate signals:

- `kube-system` namespace UID
- OpenShift cluster version
- API server fingerprint or redacted API identity
- ACM, MCE, or MCH resource evidence
- hub namespace and resource fingerprints
- operator-provided physical label from external config
- expected identity fingerprint from Phase 8C config

Rules:

- context name alone is insufficient
- kubeconfig path alone is not evidence
- at least two independent signals are preferred where practical
- identity mismatch is `NO_GO`
- swapped identity is `NO_GO`
- missing identity evidence is `BLOCKED` or `NO_GO` depending on where the failure occurs
- all identity artifact fields must be redacted or fingerprinted

If only one signal is available, a future implementation must document why that signal is temporarily acceptable and
must not use the result as live certification evidence unless a later audited phase approves it.

## Logical Role Discovery Evidence

Future read-only discovery must infer logical primary and secondary roles from live read evidence, not from static
profile role names.

Candidate evidence:

- `ManagedCluster` resources present on a hub
- expected managed cluster names exactly match the active hub set
- managed cluster availability and ownership indicators
- restore or passive evidence on the secondary hub
- Backup, Restore, and BackupSchedule status
- previous controller artifact only as supporting evidence, not sole proof
- Argo CD pause/resume status only where relevant

Rules:

- both hubs active is `NO_GO`
- neither hub active is `RECOVERY_REQUIRED`
- ambiguous role state is fail-closed
- unexpected managed cluster set blocks certification
- exact expected managed cluster names are required for certification
- operator override is recovery or non-certification evidence only, not discovery proof

Role discovery evidence must be fresh for each read-only discovery run. A previous artifact may explain expected state,
but it cannot prove current live state by itself.

## Managed Cluster Set Verification

Future read-only discovery must verify the expected managed cluster inventory before any certification claim.

Requirements:

- expected names come from validated external config
- `exact_match_required` must be true
- `unexpected_cluster_policy` must be `block`
- active hub must show exactly the expected managed cluster names
- missing expected cluster blocks
- extra cluster blocks
- evidence is artifact-safe: names and counts only, with any private IDs redacted or fingerprinted

The initial policy is exact match only. Partial match, warning-only drift, or allowlisted extra clusters require a later
audited design.

## Read-Only Query / Command Policy

| Resource/query family | Future query examples | Read-only status | Required gate | Artifact evidence | Notes |
| --- | --- | --- | --- | --- | --- |
| Cluster identity queries | API identity fingerprint, platform identity summary | Allowed in future implementation | L0-L4, L9 | Fingerprinted identity signal summary | Raw API identity values must not be published. |
| Namespace UID queries | `kube-system` UID, ACM namespace UID | Allowed in future implementation | L0-L4, L9 | Fingerprinted UID summary and match result | UID values may be fingerprinted if treated as private. |
| ClusterVersion queries | OpenShift version and available status | Allowed in future implementation | L0-L4, L7, L9 | Version summary when operator-approved; health booleans | Avoid publishing private platform details unless approved. |
| ACM/MCE/MCH status queries | Hub operator resource status and conditions | Allowed in future implementation | L0-L5, L7, L9 | Condition counts, health booleans, redacted resource fingerprints | Status only; no patch or repair. |
| ManagedCluster list/status queries | Managed cluster names and availability state | Allowed in future implementation | L0-L6, L9 | Expected/observed names, missing names, extra names, counts | Exact expected set is required. |
| Backup/Restore/BackupSchedule status queries | Passive sync and backup readiness evidence | Allowed in future implementation | L0-L7, L9 | Status categories and age summaries | No restore creation or BackupSchedule mutation. |
| Argo CD status queries | Application and namespace status where relevant | Allowed only when scenario requires it | L0-L8, L9 | Health/sync booleans and ownership summary | No pause, resume, sync, refresh, or annotation mutation. |
| SubjectAccessReview-style read checks | Read permission checks for query families | Allowed only if separately designed as non-mutating | L0-L7, L9 | Capability names and allow/deny status | Must not bootstrap or mutate RBAC. |
| Logs and events | Selected events or log-like snippets | Later-phase only unless strong redaction is proven | L0-L9 plus redaction gate | Redacted counts or sanitized excerpts only | High leakage risk; default is defer. |
| Secret-bearing or credential-bearing resources | Secret data, kubeconfig content, credential values | Blocked | None | Blocked reason only | Any query that may expose secrets is blocked or later-phase only. |
| Arbitrary shell commands | Unclassified shell commands | Forbidden | None | Blocked reason only | Controller must expose deterministic operations only. |
| Mutation-capable commands | Apply, patch, delete, scale, rollout, restore, pause/resume | Forbidden in read-only discovery | L10 would be required in later mutating phase | Blocked reason only | Any mutation attempt exits Phase 8D scope. |
| Agent-invented commands | Commands not emitted by controller query planner | Forbidden | None | Blocked reason only | Agent cannot improvise live operations. |

The future implementation must classify by operation semantics, not by command spelling alone. A command family that can
mutate resources remains forbidden in read-only discovery unless the controller has a deterministic, allowlisted,
read-only suboperation and a redaction policy for its output.

## Scenario Eligibility For Read-Only Discovery

The initial read-only discovery allowlist must use actual catalog IDs from `tests/release/scenarios/catalog.py`.

Initially eligible read-only candidates:

- `lab-readiness`
- `baseline-check`
- `preflight`
- `final-baseline-check`

Supporting non-live or artifact-analysis candidates, not live discovery contact by themselves:

- `static-gates`
- `runtime-parity`

Deferred shell-backed read-only candidates until controller-owned query planning replaces shell drift risk:

- `bash-discovery`
- `bash-postflight`

Explicitly not eligible in Phase 8D:

- `python-passive-switchover`
- `ansible-passive-switchover`
- `python-restore-only`
- `ansible-restore-only`
- `argocd-managed-switchover` when it mutates
- `checkpoint-resume` when it mutates or requires state writes
- `failure-injection`
- `full-restore`
- `decommission`
- `soak` unless separately proven read-only later
- `rbac-bootstrap` if it mutates
- `rbac-bootstrap-live` unless it is strictly read-only SubjectAccessReview style and separately designed

Unknown scenario IDs are `BLOCKED`. A future implementation must fail closed if the catalog changes and a scenario has
not been reclassified for read-only discovery.

## Discovery Artifact Design

Future read-only discovery artifacts are runtime outputs, not committed files. They must be safe to publish after
redaction and must not contain raw runtime inputs.

Required top-level fields:

- `artifact_version`
- `controller_phase`
- `discovery_mode: read_only`
- `live_execution_enabled`
- `mutation_enabled`
- `live_certification_evidence`
- `physical_identity_evidence`
- `logical_role_evidence`
- `managed_cluster_set_evidence`
- `read_prerequisite_evidence`
- `scenario_id`
- `gate_status`
- `decision`
- `safe_to_continue`
- `retry_allowed`
- `manual_recovery_required`
- `first_blocking_reason`
- `redaction_status`
- `command_query_summary`
- `runtime_inputs_redacted: true`

Rules:

- `live_certification_evidence` remains false unless a later audited phase explicitly supports it
- raw runtime inputs must not appear
- redaction failure is `NO_GO`
- artifacts must not be committed
- command and query summaries must use allowlist IDs, labels, hashes, fingerprints, counts, and booleans
- output must record whether any query was blocked before live contact

## Redaction Policy For Discovery

Discovery redaction must sanitize:

- kubeconfig refs
- context refs if runtime-only
- raw API URLs
- private cluster IDs
- tokens, passwords, secrets, and credentials
- namespace and resource UIDs when policy treats them as private, or represent them as fingerprints
- stdout, stderr, and log-like text
- command and query summaries
- operator approval references if sensitive

Artifact-safe values:

- physical labels
- expected managed cluster names if not private by policy
- counts
- booleans
- gate IDs
- scenario IDs
- redacted fingerprints
- validation decisions

If redaction cannot prove an artifact safe, the result is `NO_GO`. If the failure occurs before any live contact because
the request shape is unsafe, the result is `BLOCKED`.

## Failure Decision Model

| Condition | Decision | Notes |
| --- | --- | --- |
| Invalid external config | `BLOCKED` | Fail before discovery. |
| Missing runtime-only kubeconfig ref | `BLOCKED` | Runtime handle is required but not artifact-facing. |
| Credential validation failure | `BLOCKED` or `NO_GO` | `BLOCKED` before contact; `NO_GO` if unsafe publication risk is detected. |
| Identity mismatch | `NO_GO` | Certification cannot continue. |
| Swapped identity | `NO_GO` | Treat as wrong physical hub binding. |
| Both hubs active | `NO_GO` | Split-brain-like role state blocks certification. |
| Neither hub active | `RECOVERY_REQUIRED` | Manual recovery or inspection is required. |
| Ambiguous role evidence | `RECOVERY_REQUIRED` or `NO_GO` | Choose the stricter decision when evidence suggests unsafe state. |
| Managed cluster set drift | `NO_GO` or `RECOVERY_REQUIRED` | Missing or extra clusters block; recovery depends on evidence. |
| Redaction failure | `NO_GO` | Unsafe artifacts cannot support certification. |
| Read-only query timeout before mutation | `INFRA_RETRYABLE` | Only if no state changed and retry criteria are satisfied. |
| Unsupported scenario | `BLOCKED` | Scenario is not in the read-only allowlist. |
| Forbidden command or query | `BLOCKED` | No live contact for forbidden plans. |
| Any attempted mutation in read-only discovery | `BLOCKED` | Later mutating phase and L10 are required. |

`safe_to_continue` is not permission to mutate. In read-only discovery, it means only that the read-only result did not
find a blocking condition for the requested read-only scenario.

## Test / Guardrail Requirements For Future Implementation

Before any Phase 8E or Phase 8F implementation, tests and guardrails must exist for:

- no live backend default
- live mode requires explicit approval flags
- no mutation verbs in query plans
- query allowlist enforced
- forbidden command families blocked
- kubeconfig refs runtime-only
- `os.environ` not inherited
- identity mismatch fails
- swapped identity fails
- both hubs active fails
- neither hub active returns recovery-required
- managed cluster exact match required
- missing managed cluster blocks
- extra managed cluster blocks
- artifact redaction of API URLs, kubeconfig refs, and private IDs
- read-only discovery artifact contains required fields
- `live_certification_evidence` remains false
- Agent cannot override discovery decisions
- L10 is absent for read-only discovery and required for any mutation attempt
- no committed generated profiles or runtime output artifacts

Future tests must remain non-live by default. Any live test path must be opt-in, explicitly named, and impossible to
trigger from implicit local environment state.

## Documentation Integration

This design is linked from the Phase 8A live-readiness design and the Phase 8B external live lab config schema design.
Those links are documentation-only pointers.

No protected operational runbooks are modified by this design. No Agent live behavior is added.

## Recommendation

Recommendation: READY_FOR_PHASE_8E_READ_ONLY_DISCOVERY_GUARDRAILS

Phase 8E should add read-only discovery guardrails before any backend implementation. It should not implement live
discovery immediately unless the guardrail phase is explicitly completed and independently reviewed.
