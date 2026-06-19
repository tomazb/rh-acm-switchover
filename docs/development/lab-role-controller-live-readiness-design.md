# Lab Role Controller Live-Readiness Design

## Status

This is a proposed live-readiness design.

It does not enable live execution. It does not approve live ACM certification. The current implementation through
Phase 7C remains non-live: fake controller execution, dry-run release-framework materialization, local fake-harness
execution, the Phase 7A CLI wrapper, Phase 7B Agent instructions, and the Phase 7C closeout review are not live ACM
certification evidence.

Any future live execution must be implemented in a later phase and pass independent audit before it can produce live
certification evidence. Until that audited phase exists, `live_certification_evidence=true` is unsupported through the
lab role controller.

Phase 8B turns the highest-risk boundaries from this document into non-live guardrails and records the external live
lab config schema design in [`docs/development/lab-role-controller-live-lab-config-schema.md`](lab-role-controller-live-lab-config-schema.md).
That schema design is not consumed by current code and does not enable live config loading or live execution.

Phase 8D records the future read-only discovery backend contract in
[`docs/development/lab-role-controller-read-only-discovery-design.md`](lab-role-controller-read-only-discovery-design.md).
That design is documentation-only and does not implement live discovery, kubeconfig reading, live adapter execution, or
mutation.

## Scope

In scope:

- safety requirements for future live execution
- human approval gates
- kubeconfig and credential handling design
- live identity proof design
- live role discovery design
- allowed and forbidden command matrix
- live artifact and redaction requirements
- first safe live scenario definition
- manual recovery and no-automatic-recovery boundaries
- audit and evidence requirements

Out of scope:

- implementing live execution
- adding `oc` or `kubectl` calls
- adding `ansible-playbook` calls
- invoking live release adapters
- creating real lab config files
- committing generated live profiles
- automatic live recovery
- changing protected operational runbooks
- production JSON schema finalization
- Agent-driven live operation

## Current Non-Live Foundation

The current lab role controller stack is a deterministic, non-live foundation for reasoning about two physical hubs,
logical roles, segment handoff, profile freshness, and artifact safety.

| Phase | Completed non-live capability | Live certification evidence |
| --- | --- | --- |
| Phase 1 | Deterministic physical identity, logical role, and segment decisions using fake observations. | No |
| Phase 2 | Role-aware profile generation, redacted metadata, and stale profile rejection. | No |
| Phase 3 | One-segment controller wrapper around planning, fake execution, verification, and artifacts. | No |
| Phase 4 | Multi-segment ping-pong planner with proven-state handoff between segments. | No |
| Phase 5 | Recovery decision tree and provisional run artifact contract. | No |
| Phase 6A | Execution backend abstraction with dry-run release-framework request construction only. | No |
| Phase 6B | Materialized release-framework invocation model that does not execute pytest or adapters. | No |
| Phase 6C | Explicitly gated local execution harness using injected fake command runners only. | No |
| Phase 6D | Consolidation and architecture hardening across controller, planner, recovery, redaction, and execution modules. | No |
| Phase 7A | Non-live CLI wrapper in `scripts/release/run_lab_role_controller.py`. | No |
| Phase 7B | Agent operating instructions that keep the Agent subordinate to controller decisions. | No |
| Phase 7C | Closeout review with `READY_FOR_LIVE_READINESS_DESIGN`. | No |

None of these phases provides live ACM certification evidence. `safe_to_continue` is controller metadata, not
permission to run live commands. Dry-run materialization, fake execution, and the local fake harness are not live
cluster evidence.

## Definition of Live Execution

Live execution means that the lab role controller, release framework, an adapter, or an Agent-controlled command does
any one of the following against real lab resources:

- reads real kubeconfigs
- contacts real ACM hub API servers
- runs `oc` or `kubectl` against live clusters
- runs `ansible-playbook` against live clusters
- invokes Python or Ansible release adapters that mutate or observe live state
- produces artifacts from real cluster data
- executes release-framework scenarios with real hub or managed-cluster contexts

Any one of these actions crosses from non-live into live scope. A future implementation must not treat read-only
cluster access as "still non-live"; observing real cluster state is live execution for this repository.

## Explicitly Unsupported Today

The following behavior remains unsupported through Phase 8A:

- live execution through the lab role controller
- live ACM certification through the lab role controller
- live discovery through the lab role controller
- `oc`, `kubectl`, or `ansible-playbook` execution from the controller or Agent path
- live Python CLI or Ansible collection adapter execution from the controller
- automatic live recovery
- Agent-selected live commands
- committed real lab configuration
- committed generated live profiles
- `.release` runtime output committed to Git
- production JSON schema finalization for live artifacts
- `live_certification_evidence=true`

## Hard Safety Principles

1. Fail closed on ambiguity.
2. Human approval is required before any live action.
3. Controller owns truth and safety.
4. Agent owns only orchestration convenience and explanation.
5. No automatic live recovery.
6. No live mutation without proven initial state.
7. No second mutation without proven final state from the previous mutation.
8. No `live_certification_evidence=true` unless a future audited live phase explicitly supports it.
9. Redaction failure is a certification blocker.
10. Live artifacts must be useful but must not expose credentials, raw API URLs, kubeconfig paths, private IDs, or
    secret-like data.

## Human Approval Gates

Every future live path must pass explicit gates before any live command runs. Gates are controller-enforced; Agent
agreement is not a substitute for operator approval.

| Gate | Purpose | Required evidence | Failure decision | Artifact evidence | Retry allowed |
| --- | --- | --- | --- | --- | --- |
| L0: live mode selected | Prove the operator intentionally entered live scope. | Explicit live-mode input plus recorded operator approval metadata. | `BLOCKED` before execution. | Approval reference, selected mode, and timestamp summary. | Yes, after corrected invocation. |
| L1: checkout verified | Prevent certification from unreviewed source state. | Expected branch, commit, clean working tree, release metadata state. | `NO_GO` for certification; `BLOCKED` for diagnostic live-readiness. | Redacted git status summary and commit hash. | Yes, after checkout is corrected. |
| L2: external lab config provided | Ensure no committed or implicit live lab configuration is used. | Runtime-only lab config reference supplied outside Git. | `BLOCKED`. | Config hash, schema version, source category, no raw path. | Yes, after config is supplied. |
| L3: credentials validated | Prove credential references exist and are usable without publishing them. | Runtime-only credential handles pass presence and permissions prechecks. | `BLOCKED` before live contact; `NO_GO` if unsafe data would be published. | Credential-presence status and redacted handle fingerprints. | Yes, after credential correction. |
| L4: physical identity proof | Bind `hub-a` and `hub-b` to live physical clusters. | Multiple identity signals, including `kube-system` UID and API identity fingerprint. | `NO_GO` before mutation; `BLOCKED` if evidence cannot be collected before any live contact. | Redacted/fingerprinted identity evidence and comparison result. | Yes only before mutation and after operator approval. |
| L5: logical role discovery | Prove which physical hub is primary and which is secondary. | Active/passive ACM role evidence from both hubs. | `NO_GO` or `RECOVERY_REQUIRED`, depending on whether unsafe live state is discovered. | Role evidence summary with signal confidence and ambiguity status. | Yes only after fresh discovery and approval. |
| L6: managed cluster set match | Ensure the certification lab matches the expected managed cluster inventory. | Exact observed names match the external expected set. | `NO_GO` for certification; `RECOVERY_REQUIRED` if state may need human repair. | Redacted expected-count and hashed-name comparison summary. | Yes after operator resolves drift. |
| L7: RBAC and prerequisites | Confirm required read or mutation permissions and service health. | RBAC checks, ACM/MCE/MCH health, backup/restore health, and tool versions. | `NO_GO`. | Prerequisite summary and failing capability list. | Yes after remediation and fresh checks. |
| L8: scenario allowlist | Prevent accidental execution of unsupported live scenarios. | Scenario ID is present in a future audited live allowlist. | `BLOCKED`. | Scenario classification, allowlist version, and reason. | Yes only after code/config update and review. |
| L9: dry-run invocation review | Let the operator review the exact materialized command and environment plan before execution. | Sanitized argv summary, redacted env plan, profile hash, artifact plan. | `BLOCKED`. | Reviewed materialization hash and operator approval reference. | Yes after re-materialization. |
| L10: final mutation confirmation | Require immediate human confirmation before the first mutating command. | Fresh approval after L0-L9, no stale identity/role evidence, mutation target summary. | `BLOCKED`. | Confirmation timestamp, scenario ID, role state, and profile hash. | No automatic retry; each mutation needs a new confirmation. |

Retry never means automatic recovery. A retry is allowed only when the controller marks `retry_allowed=true`, no mutation
has occurred or the post-mutation state has been independently proven, and a human explicitly starts a new attempt.

## Kubeconfig and Credential Handling Model

Future live phases must treat credentials as runtime-only inputs separated from artifact-facing metadata.

Requirements:

- Real kubeconfigs must never be committed.
- Real kubeconfig paths must not appear in artifact-facing summaries.
- Runtime-only credential references must be separated from artifact metadata.
- Environment variables must not be blindly inherited.
- `KUBECONFIG`-like values must be explicit and runtime-only.
- Token, password, secret, and credential markers must be rejected from artifacts unless fully redacted.
- Raw API URLs must be fingerprinted or redacted.
- Private cluster identifiers must be redacted.
- Credential presence must be validated without disclosing values.
- Logs, stdout, and stderr must be sanitized before artifact publication.

Forbidden:

- storing kubeconfig contents
- printing kubeconfig paths
- embedding credentials in generated profiles committed to Git
- copying `os.environ` wholesale into execution environments
- writing live credential paths to run artifacts

The future controller should keep two representations:

- **Runtime execution context**: credential handles, live process environment, and temporary profile paths available only
  to the executing process.
- **Publishable artifact context**: hashes, booleans, redacted labels, and sanitized summaries safe for operator review.

If a runtime value cannot be summarized safely, the controller must omit or fingerprint it. If redaction cannot prove
the artifact safe, the segment decision is `NO_GO`.

## Live Physical Hub Identity Proof

The controller must prove physical hub identity before any live mutation. Stable labels such as `hub-a` and `hub-b`
are operator-facing names; they are not identity proof.

Candidate evidence:

- `kube-system` namespace UID
- API server fingerprint or redacted API identity
- OpenShift cluster version
- ACM, MCE, or MCH evidence where applicable
- hub namespace/resource fingerprints
- expected hub labels from external lab config
- stable operator-provided physical labels such as `hub-a` and `hub-b`

Rules:

- Context name alone is insufficient.
- One signal alone should not be trusted when multiple signals are available.
- Identity mismatch is `NO_GO`.
- Missing identity evidence is `NO_GO` before mutation, or `BLOCKED` when live execution cannot safely start.
- Swapped identity evidence is `NO_GO`.
- Identity evidence in artifacts must be redacted or fingerprinted.

The future implementation may reuse the existing Python CLI concept of binding to `cluster_uid`, but the lab role
controller needs its own audited live collection path and artifact redaction rules before it can rely on live evidence.

## Live Logical Role Discovery

The controller must prove current logical roles from live evidence before each segment. Previous controller artifacts
are supporting evidence only; they must never be the sole proof for a new live segment.

Evidence:

- which hub actively manages the expected `ManagedCluster` resources
- managed cluster availability and ownership indicators
- restore/passive state evidence
- backup and restore status
- Argo CD pause/resume state when relevant
- previous controller state artifact only as supporting evidence
- explicit operator override only as non-certification or recovery evidence

Rules:

- Both hubs active is `NO_GO`.
- Neither hub active is `RECOVERY_REQUIRED`.
- Ambiguous role evidence is `RECOVERY_REQUIRED` or `NO_GO`.
- Role discovery must fail closed.
- The active managed cluster set must exactly match expected names for certification.
- Unexpected managed clusters block certification unless explicitly documented as allowed in a future audited design.

Role discovery must run before every known-state segment. A successful passive switchover changes the logical mapping;
the next segment must rediscover the lab instead of reusing stale static profile roles.

## Live RBAC and Prerequisite Certification

Future live execution requires prerequisite checks before any live scenario is allowed.

Required prerequisite categories:

- required RBAC for discovery
- required RBAC for mutation scenarios
- deny checks for over-permission where current framework supports live RBAC certification
- backup and restore operator health prerequisites
- ACM, MCE, and MCH health prerequisites
- Argo CD health prerequisites for Argo CD lanes
- managed cluster readiness prerequisites
- release metadata and version checks
- clean checkout and dirty checkout policy
- artifact redaction readiness
- external tool version checks

The existing `rbac-bootstrap-live` release scenario is an opt-in live RBAC certification surface in the release
framework, not a lab role controller live-execution approval. A future controller phase must explicitly gate and audit
any use of that scenario through the lab role controller, including environment handling and artifact redaction.

Phase 8A does not implement any of these checks.

## Allowed / Forbidden Command Matrix

| Command family | Examples | Phase 8A status | Future live status | Required gate | Notes |
| --- | --- | --- | --- | --- | --- |
| Read-only `oc`/`kubectl` discovery | Cluster version, namespace UID, resource get/list | Design-only / not implemented | Allowed only after human approval and identity gate | L0-L5 | Read-only cluster contact is still live execution. |
| Mutating `oc`/`kubectl` actions | Patch, apply, delete, scale | Design-only / not implemented | Allowed only in later audited phase with scenario allowlist | L0-L10 | No mutation without proven initial state. |
| `ansible-playbook` execution | Collection playbooks | Design-only / not implemented | Allowed only in later audited phase with scenario allowlist | L0-L10 | Must use role-aware runtime profile and redacted extra vars. |
| Pytest release-framework invocation | Release certification entrypoint | Design-only / dry-run materialization only | Conditionally allowed after live gates and audited backend | L0-L9 for read-only, L10 for mutation | Current controller must not execute live adapters. |
| Python CLI switchover execution | Python stream scenarios | Design-only / not implemented | Mutating allowlist only in later audited phase | L0-L10 | Passive/full/restore-only scenarios require role proof and post-checks. |
| Ansible collection switchover execution | Ansible stream scenarios | Design-only / not implemented | Mutating allowlist only in later audited phase | L0-L10 | Extra vars must not leak credential paths into artifacts. |
| Argo CD pause/resume handling | Argo CD managed switchover lane | Design-only / not implemented | Mutating allowlist only after Argo CD health and fixture proof | L0-L10 | Application/ApplicationSet ownership must be proven. |
| Backup/restore checks | BackupSchedule, Restore, passive sync evidence | Design-only / not implemented | Read-only first; mutation only in later audited scenario | L0-L7 | Evidence must distinguish active and passive roles. |
| Decommission | Remove old hub resources | Design-only / not implemented | Disposable-lab-only unless future design says otherwise | L0-L10 plus disposable-lab proof | Never first live scenario. |
| Shell/arbitrary subprocess | Any unclassified shell command | Forbidden | Forbidden | None | Controller must expose deterministic operations only. |
| Agent-invented commands | Any live command not emitted by controller | Forbidden | Forbidden | None | Agent cannot improvise live operations. |

## Scenario Live Eligibility Matrix

The table uses actual scenario IDs from `tests/release/scenarios/catalog.py`.

| Scenario ID | Current non-live status | Proposed first live-readiness status | Live mutation risk | Required prerequisites | Required post-checks | Initial recommendation |
| --- | --- | --- | --- | --- | --- | --- |
| `static-gates` | Static-only catalog scenario. | Eligible as non-live prerequisite. | None. | Clean checkout and metadata policy. | Static gate artifact. | Keep non-live. |
| `lab-readiness` | Local/static lifecycle; controller classifies as live-non-mutating. | Candidate for read-only live-readiness after gates. | Low read-only risk. | L0-L7, redaction readiness. | Redacted readiness artifact. | Include in first read-only phase. |
| `baseline-check` | Local/static lifecycle; controller classifies as live-non-mutating. | Candidate for read-only live-readiness after gates. | Low read-only risk. | Identity, role, expected managed cluster set. | Baseline evidence artifact. | Include in first read-only phase. |
| `preflight` | Required across bash, Python, and Ansible streams; controller non-live only today. | Candidate for first read-only live preflight segment. | Low if validate-only/read-only is enforced. | L0-L9, read RBAC, no mutation path. | Redacted preflight result and no-mutation proof. | First safe live scenario. |
| `python-passive-switchover` | Lab-mutating in catalog; non-live through controller. | Not eligible in first live-readiness phase. | High role-changing mutation. | Successful read-only phase, RBAC certification, L10. | Proven role flip and final baseline. | Later audited phase only. |
| `ansible-passive-switchover` | Lab-mutating in catalog; non-live through controller. | Not eligible in first live-readiness phase. | High role-changing mutation. | Successful read-only phase, RBAC certification, L10. | Proven role flip and final baseline. | Later audited phase only. |
| `python-restore-only` | Lab-mutating in catalog; non-live through controller. | Not eligible in first live-readiness phase. | High restore-state mutation. | Restore evidence, split-brain proof, L10. | Restore result, active role proof, backup boundary check. | Later audited phase only. |
| `ansible-restore-only` | Lab-mutating in catalog; non-live through controller. | Not eligible in first live-readiness phase. | High restore-state mutation. | Restore evidence, split-brain proof, L10. | Restore result, active role proof, backup boundary check. | Later audited phase only. |
| `argocd-managed-switchover` | Lab-mutating in catalog; non-live through controller. | Not eligible in first live-readiness phase. | High role-changing plus Argo CD state mutation. | Argo CD health, fixture proof, RBAC, L10. | Pause/resume proof and role proof. | Later audited phase only. |
| `runtime-parity` | Static-only catalog scenario. | Eligible only after source artifacts exist. | None by itself. | Prior artifact set and parity inputs. | Runtime parity artifact. | Keep supporting, not first live contact. |
| `final-baseline-check` | Local/static lifecycle; controller classifies as live-non-mutating. | Candidate as read-only post-check. | Low read-only risk. | Proven prior segment state. | Final baseline evidence. | Use after read-only preflight; required after future mutation. |
| `bash-discovery` | Optional bash stream discovery. | Not first through controller unless reworked as deterministic read-only backend. | Medium because shell path can drift. | L0-L9 and command allowlist. | Redacted discovery artifact. | Defer behind controller-owned discovery. |
| `bash-postflight` | Optional bash stream postflight. | Not first through controller unless reworked as deterministic read-only backend. | Medium because shell path can drift. | L0-L9 and command allowlist. | Redacted postflight artifact. | Defer behind controller-owned post-checks. |
| `full-restore` | Optional lab-mutating scenario; partial stream support. | Not eligible in first live-readiness phase. | High restore mutation. | Disposable or resettable lab proof, restore prerequisites, L10. | Proven final role and restore state. | Later audited phase only. |
| `checkpoint-resume` | Optional lab-mutating scenario; partial stream support. | Not eligible in first live-readiness phase. | High if stale checkpoint resumes wrong state. | Hub identity-bound checkpoint proof, L10. | Resume path and final role proof. | Later audited phase only. |
| `decommission` | Destructive/disposable-lab-only in controller. | Not eligible in first live-readiness phase. | Destructive. | Disposable-lab proof and explicit destructive approval. | Resource deletion proof and recovery boundary. | Do not enable except disposable-lab design. |
| `rbac-bootstrap` | Recovery scenario in controller; Ansible adapter currently dry-run. | Not eligible as live mutation in first live-readiness phase. | Medium to high RBAC mutation. | Bootstrap design, RBAC diff review, L10. | Applied permission and deny-check evidence. | Defer; keep dry-run until audited. |
| `rbac-bootstrap-live` | Local/static lifecycle; existing opt-in release-framework live RBAC check outside controller. | Candidate only after live RBAC gate design is implemented. | Low mutation risk if SAR-only, but live API contact. | Explicit opt-in, admin RBAC, L0-L7. | SAR allow/deny artifact. | Phase 8F after read-only discovery. |
| `failure-injection` | Not certification-supported; destructive/disposable-lab-only in controller. | Not eligible in first live-readiness phase. | High and potentially disruptive. | Disposable-lab proof, separate design, L10. | Injected failure and recovery evidence. | Do not enable in reusable lab. |
| `soak` | Not certification-supported; lab-mutating in controller. | Not eligible in first live-readiness phase. | High due repeated cycles. | Proven cycle boundaries, reset/recovery design, L10. | Per-cycle evidence and final baseline. | Defer until after first mutation phases. |

All requested scenario names exist in the current catalog. The matrix also includes the catalog-only supporting
scenarios `static-gates`, `lab-readiness`, `baseline-check`, `runtime-parity`, `bash-discovery`, and `bash-postflight`.

## First Safe Live Scenario

The first live scenario for a future phase should be read-only live discovery plus preflight-only evidence.

Required properties:

- no mutation
- no restore
- no decommission
- no Argo CD mutation
- no automatic recovery
- artifact redaction required
- human approval required
- no `live_certification_evidence=true` unless the future audited phase explicitly supports it

This is safer than starting with passive switchover because it proves identity, role discovery, managed-cluster
expectations, RBAC read permissions, and artifact redaction without changing durable lab state. Passive switchover
should wait until the controller has already demonstrated that it can safely contact the lab, prove roles, and publish
safe artifacts.

Success criteria:

- physical identity proof for both hubs
- logical role discovery for primary and secondary
- exact expected managed cluster set
- read RBAC checks pass
- redacted artifact is accepted
- no live certification flag unless a future audited phase explicitly allows it
- no mutation attempted

## Live Mutation Enablement Requirements

A future mutating scenario must not be enabled until all of the following are true:

- successful read-only live discovery phase
- successful live RBAC certification
- explicit scenario allowlist
- human approval immediately before mutation
- dry-run/materialized invocation reviewed
- pre-mutation artifact snapshot
- proven initial primary/secondary role state
- expected final role state defined
- post-mutation verification plan
- recovery stop rules
- manual recovery plan
- audit artifact requirements
- independent review

The first mutating implementation must be a separate audited phase. Phase 8A does not authorize it.

## No-Automatic-Recovery Policy

The controller may classify `RECOVERY_REQUIRED`. It may produce operator action hints. It must not automatically recover
live lab state in Phase 8A or the first live phases.

Rules:

- Controller may classify `RECOVERY_REQUIRED`.
- Controller may produce operator action hints.
- Controller must not automatically recover live lab state in Phase 8A or first live phases.
- Agent must not attempt recovery.
- Human operator must decide recovery actions.
- Any future automated recovery must be separately designed, audited, and gated.

Recovery commands are live mutation commands. They require the same identity, role, RBAC, artifact, and human approval
controls as certification scenarios.

## Live Artifact and Redaction Policy

Live artifacts must preserve enough evidence for operator audit without exposing sensitive lab data.

Required top-level fields for a future live artifact:

- schema/artifact version
- controller phase and live support phase
- selected scenario and stream
- final decision
- `safe_to_continue`
- `retry_allowed`
- `manual_recovery_required`
- first blocker fields
- human approval gate results
- physical identity proof summary
- logical role discovery summary
- managed cluster evidence summary
- RBAC/prerequisite summary
- command/evidence summary
- redaction status
- `real_execution_evidence`
- `live_certification_evidence`

Segment evidence must include:

- identity evidence redaction status
- role discovery evidence redaction status
- command family and allowlist result
- sanitized stdout/stderr summaries
- pre-segment and post-segment evidence snapshots
- mutation attempted/completed booleans
- final-state proof status

Redaction policy:

- Identity evidence is redacted or fingerprinted.
- Role discovery evidence is redacted or fingerprinted.
- Raw command summaries must not include credential paths or API URLs.
- stdout and stderr are sanitized before artifact publication.
- kubeconfig paths, API URLs, tokens, passwords, secrets, credentials, and private IDs are redacted or rejected.
- Redaction failure is `NO_GO`.
- Live artifacts require human review before they can support certification.
- Retention must be explicit and consistent with the external lab config.
- Live artifacts must not be committed.

## Agent Live Boundary

Future Agent behavior must preserve the controller-owned safety boundary.

Rules:

- Agent cannot initiate live mode unless a human explicitly asks.
- Agent cannot supply or infer credentials.
- Agent cannot invent live commands.
- Agent must invoke deterministic controller CLI only.
- Agent cannot override controller decisions or approval gates.
- Agent must stop on `NO_GO`, `RECOVERY_REQUIRED`, or `BLOCKED`.
- Agent must not retry live operations without explicit human instruction and `retry_allowed=true`.
- Agent must not perform recovery.
- Agent must not claim live evidence unless an artifact explicitly supports it in a future audited phase.

The Agent may explain controller output and summarize artifacts. It is not a source of truth for identity, role state,
mutation safety, recovery, or certification eligibility.

## Future Implementation Phases

Recommended next phases after Phase 8A:

- Phase 8B: live-readiness guardrail tests and config schema design
- Phase 8C: external live lab config model, no execution
- Phase 8D: read-only live discovery backend contract design, no implementation
- Phase 8E: read-only discovery guardrails before backend implementation
- Phase 8F: read-only live discovery backend only after guardrails and review
- Phase 9A: first gated mutating live scenario design
- Phase 9B: first gated mutating implementation, only after independent audit

Do not implement a live mutating scenario immediately after Phase 8A. The next phase should strengthen guardrails and
external config shape before any live command path exists.

## Validation / Acceptance Requirements for Future Live Work

Future live implementation pull requests must satisfy these hard requirements:

- independent design review before implementation
- tests for every live gate
- no default live mode
- explicit human approval parameter
- no environment inheritance
- live config excluded from Git
- redaction tests with realistic unsafe examples
- blocked live mode tests
- no Agent override
- audit evidence in artifacts
- CI/non-live tests remain green
- live tests clearly separated from normal CI

Live tests must not run in normal CI. Any live test path must be opt-in, clearly named, and impossible to trigger from
implicit local environment state.

## Findings / Follow-Ups

| ID | Description | Blocking | Suggested phase |
| --- | --- | --- | --- |
| FU-8A-01 | Add tests that enforce the live-readiness design document exists and preserves non-live Phase 8A boundaries. | no | Phase 8A |
| FU-8A-02 | Define an external live lab config schema that keeps credential handles runtime-only and artifact metadata redacted. | yes | Phase 8C |
| FU-8A-03 | Design and test a live gate state machine for L0-L10 approval evidence. | yes | Phase 8B |
| FU-8A-04 | Design a read-only live discovery backend that collects identity and role evidence without mutation. | yes | Phase 8D |
| FU-8A-05 | Audit whether `rbac-bootstrap-live` remains SAR-only and how it should be sequenced through the controller. | yes | Phase 8F |
| FU-8A-06 | Define disposable-lab requirements before any destructive or failure-injection scenario can be considered. | yes | Phase 9 or later |

No blocker was found that should stop live-readiness design.

## Final Recommendation

Recommendation: READY_FOR_PHASE_8B_GUARDRAILS

Phase 8B target recommendation after guardrails and schema design: READY_FOR_PHASE_8C_EXTERNAL_LIVE_CONFIG_MODEL
