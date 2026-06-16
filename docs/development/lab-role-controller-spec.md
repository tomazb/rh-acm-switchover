# Lab Role Controller Specification

## Status

This is a proposed future design for release validation. It is not implemented behavior, it does not change
the current release profile schema, and it does not authorize live lab mutation outside the existing release
validation entrypoints.

The controller described here is a design target for safely certifying a live two-hub ACM lab when scenarios
may change which physical cluster is the logical primary.

The intended hierarchy is:

- Authoritative implementation: Python lab role controller.
- Convenience and orchestration layer: Agent skill or Agent instructions.

The controller owns truth and safety. The Agent owns orchestration convenience and explanation.

## Problem Statement

The current release framework can validate individual scenarios and profile-driven runs through
`tests/release/test_release_certification.py`, `tests/release/orchestrator.py`, and the scenario catalog in
`tests/release/scenarios/catalog.py`. Profiles under `tests/release/profiles/` describe `hubs.primary` and
`hubs.secondary` statically, and the Python and Ansible adapters build commands from those static roles.

That is enough for single-scenario validation and useful focused reruns, but it is unsafe as one linear live
certification run across multiple mutating scenarios because:

- static primary/secondary profiles become stale after switchover
- a successful passive switchover changes logical roles
- a failed or partial switchover may leave the lab in an ambiguous state
- focused reruns are useful diagnostics, but they are not full multi-mutation certification
- any matrix validator or controller validation is correct to block or produce NO-GO for multi-mutation certification
  until known-state sequencing exists

> A release certification run is not a single linear script over a static primary/secondary profile. It is a
> sequence of known-state segments. Each segment starts with live discovery, proves the
> physical-hub-to-logical-role mapping, executes at most one lab-mutating scenario, verifies the expected final
> state, and either hands a proven state to the next segment or stops with a recovery-required NO-GO.

## Goals

- Safely support full release certification across two ACM hub clusters and three managed clusters.
- Support Python CLI and Ansible collection parity across mutating scenarios.
- Discover current hub roles before each mutation.
- Generate or select role-aware profiles per segment.
- Execute at most one lab-mutating scenario per known-state segment.
- Verify final role state before continuing.
- Provide deterministic GO/NO-GO evidence.
- Keep the Agent as an orchestrator and supervisor, not an improviser of live cluster mutations.
- Keep the Python controller as the authoritative implementation for truth and safety.

## Non-Goals

- Replace the existing pytest release framework.
- Change production switchover semantics.
- Make arbitrary recovery decisions without explicit evidence.
- Hide failed or ambiguous lab states.
- Edit protected operational runbooks.
- Embed real kubeconfig paths, cluster IDs, tokens, or credentials in versioned files.
- Make the Agent the authority for live-cluster safety decisions.

## Terminology

- **Physical hub**: A stable cluster identity such as `hub-a` or `hub-b`. These labels are operator-facing
  names, not proof of identity.
- **Logical role**: The role currently assigned to a physical hub, usually `primary` or `secondary`.
- **Active primary**: The physical hub that currently owns the active ACM management role for the expected
  managed cluster set.
- **Passive/secondary hub**: The physical hub that is expected to hold restore/sync evidence and not actively
  manage the cluster set.
- **Desired state**: The physical-hub-to-logical-role assignment required before a scenario starts.
- **Observed state**: The role assignment discovered from live cluster evidence.
- **Known-state segment**: One release-validation segment that starts from a proven lab state.
- **Lab-mutating scenario**: A scenario that may change hub roles, backup/restore state, Argo CD pause state,
  RBAC state, managed-cluster ownership, or destructive lab resources.
- **Non-mutating scenario**: A scenario that only inspects source, artifacts, or live state and does not change
  the lab's durable role or resource state.
- **Recovery-required state**: A state where the harness cannot safely prove the next starting state.
- **Role-aware profile**: A generated or selected release profile whose `primary` and `secondary` entries map
  to the physical hubs that currently hold those logical roles for one segment.
- **Role transition**: A verified change in logical role assignment, for example `hub-a primary` to
  `hub-b primary`.
- **Segment artifact bundle**: The artifacts for one known-state segment, including role evidence, command
  summaries, scenario results, redaction state, and the segment decision.
- **Certification run artifact bundle**: The top-level artifact bundle that orders all segments, merges
  scenario results, records role transitions, and renders the final GO/NO-GO decision.
- **Agent**: The generic orchestration and explanation layer that invokes deterministic release tooling,
  summarizes artifacts, and follows controller decisions. The Agent is not the authority for safety decisions.
- **Agent skill / Agent instructions**: Optional convenience layers that guide Agent behavior after the Python
  controller has a deterministic command and artifact contract.

## Current Framework Constraints

The controller must wrap the current framework instead of assuming behavior that does not exist:

- Release profiles in `tests/release/contracts/models.py` define static `hubs.primary` and `hubs.secondary`
  entries with kubeconfig and context fields.
- `tests/release/adapters/python_cli.py` builds Python CLI commands from static primary and secondary
  contexts and kubeconfigs.
- `tests/release/adapters/ansible.py` builds `acm_switchover_hubs.primary` and
  `acm_switchover_hubs.secondary` extra vars from the same static role mapping.
- `tests/release/scenarios/catalog.py` identifies catalog scenarios, supported streams, `mutates_lab`, and
  runtime parity requirements. Mutating focused filters add `static-gates`, `lab-readiness`,
  `baseline-check`, `runtime-parity`, and `final-baseline-check`.
- Recovery expectations are modeled in `tests/release/contracts/models.py` and validated in
  `tests/release/contracts/schema.py`; they are not yet a known-state segment sequencer.
- `tests/release/contracts/schema.py` validates profile shape, known scenario names, static
  `baseline.initial_primary` and `baseline.final_primary`, allowed recovery actions, and credential-like
  content.
- `tests/release/orchestrator.py` performs initial discovery, lab readiness, baseline assertions, stream
  execution, runtime parity, final discovery, and summary rendering for one selected matrix.
- Focused reruns filter scenarios and streams through `tests/release/conftest.py`; they do not resume from or
  prove a previous artifact directory.
- Artifact redaction is fail-closed through `tests/release/reporting/artifacts.py` and
  `tests/release/reporting/redaction.py`.
- Certification must not rely on committed real kubeconfig paths. Example profiles intentionally use
  placeholder paths under `tests/release/profiles/`.

The current catalog and tests are correct to be conservative: without per-segment known-state sequencing, a
plan that tries to run multiple lab-mutating scenarios over one static role mapping must be blocked before
mutation or must produce a NO-GO decision.

## Proposed Architecture

The lab role controller is a deterministic orchestration layer around the existing pytest release framework.
It owns known-state sequencing and delegates scenario execution to the current release entrypoint.

Required controller flow:

1. Discover physical hub identities.
2. Discover current logical roles.
3. Compare observed state with the desired segment start state.
4. Refuse, recover, or continue based on an explicit decision tree.
5. Generate or select a role-aware profile for the current segment.
6. Run static gates or non-mutating prerequisites as needed.
7. Run at most one lab-mutating scenario.
8. Verify the expected final role state.
9. Verify managed-cluster, Argo CD, RBAC, backup/restore, and artifact-redaction evidence.
10. Record role transitions and recovery decisions.
11. Hand the proven state to the next segment or stop with NO-GO.

Text architecture diagram:

```text
Agent
  -> release-control script
     -> lab role controller
        -> discovery
        -> profile generation
        -> pytest release framework
        -> segment verification
        -> artifact merger
```

The Python lab role controller owns truth and safety. The Agent owns orchestration convenience and
explanation. The Agent invokes the controller, not the other way around, and must not improvise live-cluster
mutations. Controller and pytest artifacts must make the mutation and GO/NO-GO decisions from live evidence.

## Physical Hub Identity Model

The controller must identify physical hubs safely before any mutation.

Requirements:

- Stable labels such as `hub-a` and `hub-b` are operator-facing names only.
- The controller must bind those labels to live cluster identity evidence before every segment.
- Identity evidence may include the `kube-system` namespace UID, API server URL, cluster version, ACM hub
  evidence, and kubeconfig context name.
- Kubeconfig context names alone are insufficient.
- Identity mismatches are hard failures.
- Identity evidence must be redacted before artifacts are published.

This should reuse existing identity ideas where possible. The Python CLI already records hub identities by
cluster UID in state, as described in `AGENTS.md` and `docs/operations/usage.md`. The release framework
already builds environment fingerprints in `tests/release/baseline/fingerprint.py`. The controller should
define required properties first, then choose the smallest implementation that satisfies those properties.

## Logical Role Discovery

The controller determines the active primary from multiple pieces of live evidence where possible and fails
closed on ambiguity.

Candidate evidence includes:

- which hub actively owns or manages the expected managed cluster set
- ACM `ManagedCluster` resources on each hub
- restore and passive sync evidence on the passive hub
- backup schedule and backup/restore status
- Argo CD pause/resume state where relevant
- switchover state files or reports when available
- live RBAC evidence when a segment depends on bootstrapped permissions

Explicit operator override is allowed only as recovery or diagnostic evidence. A certification run that needs
manual role override remains non-certification or NO-GO unless the controller can independently prove the
resulting state before the next mutation.

The current discovery helper in `tests/release/baseline/discovery.py` infers `hub_role` from BackupSchedule
and Restore evidence. That is useful input, not enough by itself for multi-mutation certification.

## Known-State Segment Lifecycle

Each segment follows this lifecycle:

1. Segment plan selected.
2. Physical hub identity verified.
3. Current logical roles discovered.
4. Desired initial state checked.
5. Pre-segment baseline captured.
6. Role-aware profile generated.
7. Scenario executed.
8. Scenario artifacts collected.
9. Expected final state verified.
10. Post-segment baseline captured.
11. Segment decision emitted.
12. Proven final state passed to next segment.

Allowed segment decisions:

- **PASS**: The scenario and all required post-checks passed, and the final state is proven.
- **NO-GO**: The run is failed for release certification and must not continue as certification evidence.
- **RECOVERY_REQUIRED**: The lab may be recoverable, but the controller cannot prove the next safe starting
  state.
- **INFRA_RETRYABLE**: A pre-mutation infrastructure failure occurred and a bounded retry or focused rerun may
  be allowed.

Each segment may run many non-mutating checks, but it must execute at most one lab-mutating scenario.

## Scenario Classification

The controller needs a stricter classification than the catalog's current `mutates_lab` flag. The catalog in
`tests/release/scenarios/catalog.py` remains the source for scenario names and stream coverage; the controller
adds release-lab safety classification.

| Scenario | Controller classification | Notes |
| --- | --- | --- |
| `static-gates` | static-only | Source and metadata gate; no live mutation. |
| `lab-readiness` | live non-mutating | Live discovery/readiness assertion. |
| `baseline-check` | live non-mutating | Verifies desired role and managed-cluster baseline. |
| `preflight` | live non-mutating | Must remain validate-only/preflight behavior for all streams. |
| `runtime-parity` | static-only | Compares normalized artifacts and live evidence where already collected. |
| `final-baseline-check` | live non-mutating | Verifies final role and managed-cluster baseline. |
| `bash-discovery` | live non-mutating | Discovery helper only. |
| `bash-postflight` | live non-mutating | Postflight verification only. |
| `python-passive-switchover` | lab-mutating | Role-changing segment. |
| `ansible-passive-switchover` | lab-mutating | Role-changing segment. |
| `python-restore-only` | lab-mutating | Requires split-brain proof and restore evidence. |
| `ansible-restore-only` | lab-mutating | Requires split-brain proof and restore evidence. |
| `argocd-managed-switchover` | lab-mutating | Role-changing and Argo CD state-changing. |
| `full-restore` | lab-mutating | High-risk restore lane; requires disposable or proven reset state. |
| `checkpoint-resume` | lab-mutating | Resume state must be bound to current physical hubs. |
| `rbac-bootstrap` | recovery or lab-mutating | Current Ansible adapter runs dry-run; live bootstrap would change RBAC state. |
| `rbac-bootstrap-live` | live non-mutating certification check | SAR-based evidence; catalog currently marks it mutating, so keep a segment boundary until clarified. |
| `decommission` | destructive/disposable-lab-only | Live decommission must not run on a reusable two-hub certification lab. Dry-run may be a non-mutating artifact lane. |
| `failure-injection` | destructive/disposable-lab-only | Must be isolated from reusable certification state unless the injection is proven non-persistent. |
| `soak` | lab-mutating | Must be decomposed into known-state cycles; no open-ended mutation loop. |

If future catalog names change, the controller must fail closed for unknown scenarios until they are
classified.

## 2-Hub / 3-Managed-Cluster Certification Flow

The intended lab has:

- `hub-a`
- `hub-b`
- `mc-1`
- `mc-2`
- `mc-3`

The exact scenario order is subject to implementation, but the known-state segment rule is mandatory. A safe
ping-pong flow could be:

1. Static gates.
2. Initial discovery and baseline.
3. Segment A: non-mutating checks while `hub-a` is primary.
4. Segment B: Python CLI passive switchover from `hub-a` to `hub-b`.
5. Segment C: verify `hub-b` primary state.
6. Segment D: Ansible passive switchover from `hub-b` to `hub-a`.
7. Segment E: restore-only lane if the lab can prove no split-brain risk.
8. Segment F: Argo CD lane using one managed cluster as fixture target.
9. Segment G: optional failure-injection, checkpoint, and decommission dry-run lanes.
10. Final baseline and merged GO/NO-GO decision.

The controller must never assume that the profile's original `primary` entry still names the active hub after
Segment B. Every later segment must rediscover and rebind roles.

## Role-Aware Profile Strategy

Profiles should be generated or selected per segment from a stable lab configuration.

Strategy requirements:

- Maintain a stable lab config with physical hub labels and expected managed cluster names.
- Never commit real kubeconfig paths, cluster IDs, or credentials.
- Generate sanitized per-segment release profiles from the lab config.
- Store generated profiles under an ignored runtime directory such as `.release/`.
- Map current logical primary/secondary roles to physical hub identities at segment start.
- Record generated profile path and hash in segment artifacts.
- Redact generated profiles before publishing or merging artifacts.

An illustrative, non-contractual lab config may contain physical hub labels, references to externally provided
kubeconfig paths, expected managed cluster names, required Argo CD fixtures, and the planned segment list. The
final schema is intentionally out of scope for this spec.

## Recovery Decision Tree

The controller must choose from explicit outcomes when observed state does not match desired state.

| Observed condition | Controller outcome |
| --- | --- |
| Physical identity cannot be proven | Hard NO-GO. |
| Kubeconfig context resolves to a different physical hub than recorded | Hard NO-GO. |
| Both hubs appear active primary | Hard NO-GO and manual recovery required. |
| Neither hub appears active primary | RECOVERY_REQUIRED. |
| Active hub is opposite of desired, but otherwise healthy | Generate a swapped profile or run an approved reset segment, depending on the scenario plan. |
| Managed cluster set differs from expected `mc-1`, `mc-2`, `mc-3` | RECOVERY_REQUIRED or NO-GO based on whether drift is explainable before mutation. |
| Argo CD state cannot be proven | Block the Argo CD lane; NO-GO if mandatory. |
| RBAC evidence is missing or over-permissive | NO-GO. |
| Restore evidence is missing for restore-only scenarios | NO-GO. |
| Artifact redaction fails | NO-GO. |
| Failure appears infrastructure/transient before mutation | INFRA_RETRYABLE; focused retry may be allowed and recorded. |
| Failure occurs during or after mutation | No automatic retry unless state is rediscovered and proven. |

Recovery automation must be conservative. A recovery action that changes live state is itself a lab-mutating
segment and must produce its own artifacts and final-state proof.

## Artifact Requirements

Exact JSON schema is future work. These fields are conceptual requirements.

Segment artifact bundle:

- segment plan
- physical hub fingerprints
- observed initial role mapping
- desired initial role mapping
- generated profile hash and path after redaction
- scenario command summary
- scenario result
- expected final role mapping
- observed final role mapping
- managed cluster evidence
- Argo CD evidence, when applicable
- RBAC evidence, when applicable
- recovery decision
- redaction report

Certification run artifact bundle:

- ordered segment list
- role transition graph
- merged scenario results
- runtime parity result
- final baseline
- GO/NO-GO decision
- human-readable release report

The artifact merger must preserve enough detail for a human operator to reconstruct why each segment was
allowed to start, what changed, and why the next segment was allowed or blocked.

## Agent Execution Contract

The Agent is an orchestration and explanation layer around deterministic release tooling. It must not be the
authority for live-cluster safety decisions.

Contract:

- The Agent invokes deterministic scripts and pytest entrypoints.
- The Agent uses the lab role controller for all known-state, role-discovery, profile-generation, mutation,
  recovery, and GO/NO-GO decisions.
- The Agent must not invent ad hoc live mutation commands.
- The Agent may summarize artifacts, classify failures, and suggest focused reruns only when the controller says
  state is safe.
- The Agent must not edit source files during certification.
- The Agent should operate in a controlled runner with access to the required kubeconfigs, `oc`, Python,
  Ansible, and other release tools.
- The Agent output must include a GO/NO-GO summary derived from artifacts, not intuition.
- Agent skills or Agent instructions are optional convenience layers.
- The Python controller remains the authority.

The Agent must stop when the controller reports NO-GO, RECOVERY_REQUIRED, ambiguous hub roles, failed identity
verification, failed redaction, or any other blocking release-certification condition.

## GO/NO-GO Rules

Hard GO requirements:

- Static gates pass.
- Release metadata and version checks pass.
- Physical hub identity is proven for both hubs.
- Managed cluster set exactly matches expected names.
- Each mutating segment starts from and ends in a proven state.
- Python and Ansible mutating coverage pass according to the release plan.
- Argo CD lane passes if mandatory in the profile.
- Live RBAC certification passes, including deny checks.
- Runtime parity passes where required.
- Final baseline passes.
- Artifact redaction passes.
- No recovery-required state remains unresolved.

Hard NO-GO examples:

- ambiguous primary
- both hubs active
- unexpected managed cluster set
- stale static profile used after role transition
- failed restore evidence
- RBAC over-permission
- artifact redaction failure
- dirty checkout in certification mode, matching current framework behavior

## Implementation Plan

Phase 0: documentation and terminology

- Deliver this spec and keep `docs/development/release-validation-framework.md` aligned.
- Validation: documentation guardrail tests and `git diff --check`.

Phase 1: lab config and discovery prototype

- Add a non-secret lab config contract for physical hubs and expected managed clusters.
- Prototype physical identity and logical role discovery with fake discovery clients.
- Tests: identity mismatch, ambiguous active hub, expected `mc-1`/`mc-2`/`mc-3` match, redacted discovery output.

Phase 2: role-aware profile generation

- Generate per-segment profiles under an ignored runtime directory.
- Preserve compatibility with `tests/release/contracts/loader.py`.
- Tests: `hub-a` primary profile, `hub-b` primary profile, profile hash recording, credential rejection.

Phase 3: one-segment controller wrapper

- Wrap one mutating scenario with pre-discovery, profile generation, pytest invocation, post-discovery, and
  segment decision.
- Tests: one Python passive switchover segment with fake role transition, no mutation when initial state fails.

Phase 4: multi-segment ping-pong certification

- Chain known-state segments for Python then Ansible passive switchover.
- Tests: `hub-a` to `hub-b` to `hub-a` role transition graph, blocked second mutation when state is unproven.

Phase 5: recovery decision tree and artifact merger

- Implement explicit recovery outcomes and merge segment artifacts into a certification bundle.
- Tests: both-active NO-GO, neither-active recovery-required, pre-mutation infra retry, post-mutation no auto-retry,
  merged report includes transition and recovery evidence.

Phase 6: Agent release-runner integration

- Provide a deterministic release-control command suitable for an Agent to invoke.
- Tests: command does not accept ad hoc mutation snippets, summarizes only artifact-derived GO/NO-GO state.

Phase 7: CI/lab hardening and docs

- Add lab-only validation guidance and update release docs without touching protected operational runbooks unless
  explicitly approved.
- Tests: non-live unit/helper suite remains CI-safe, live certification remains opt-in.

## Open Questions

- Which exact signals should be required for primary role discovery?
- Should the role transition graph live inside `tests/release/` or in an outer controller wrapper?
- How much recovery should be automatic versus operator-approved?
- Do restore-only lanes need separate disposable profiles?
- Should live decommission ever run outside disposable labs?
- What exact JSON schemas should segment and merged artifacts use?
- What exact lab config and generated profile schema changes are needed?
- Should `AGENTS.md` get a short release-controller instruction after implementation starts?
- Should `rbac-bootstrap-live` remain cataloged as lab-mutating if it stays SAR-only and non-persistent?
- Should an Agent skill be added only after the controller CLI contract is implemented?

## Acceptance Criteria for the Future Implementation

The controller is ready for full live certification only when it can:

- detect `hub-a` primary and `hub-b` secondary
- detect `hub-b` primary and `hub-a` secondary after switchover
- refuse ambiguous states
- generate correct role-aware profiles
- run one mutating scenario and verify the role transition
- block a second mutation unless state is proven
- record artifacts sufficient for human review
- support Python and Ansible coverage
- produce a deterministic GO/NO-GO decision
- Agent summaries are derived from controller artifacts and never override controller decisions
