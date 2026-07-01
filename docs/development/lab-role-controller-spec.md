# Lab Role Controller Specification

## Status

This document is both the design target for future live release validation and the implementation reference for
the deterministic, non-live lab role controller now present under `tests/release/lab_controller/`.

Phases 1 through 8P/8Q are implemented as non-live controller primitives, profile generation, provisional artifacts,
dry-run request construction, non-executed invocation materialization, an explicitly gated local harness, a thin CLI
wrapper for deterministic planning and redacted artifact emission, and static GitOps ownership/interference evidence
from checked-in release-lab fixtures. These phases do not change the current release profile schema, do not finalize a
production JSON schema, and do not authorize live lab mutation outside the existing release validation entrypoints.

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
- Store generated profiles only under a caller-provided external runtime or artifact directory; `.release/` must not be
  the default output.
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

Phase 5 implements the recovery decision tree for the non-live deterministic planner only. It centralizes
run-level `PASS`, `NO_GO`, `RECOVERY_REQUIRED`, `INFRA_RETRYABLE`, and `BLOCKED` decisions, plus retry and
manual-recovery metadata, but it does not perform live discovery or automatic live recovery.

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

Phase 5 adds a deterministic provisional run-level artifact contract with stable top-level decision,
recovery, mutation, final-state, segment-decision, runtime-parity placeholder, and redaction-status fields.
This is testable controller output, not a finalized production JSON schema.

Phase 6A adds a deterministic execution backend abstraction for the controller. The default backend remains the
existing fake executor. The release-framework backend is dry-run only: it builds and validates the structured pytest
release request, role-aware profile hash, scenario/stream selection, artifact directory summary, and redacted request
summary without invoking pytest, release adapters, subprocesses, `oc`, `kubectl`, or `ansible-playbook`. Phase 6A dry-run
artifacts are explicitly not live certification evidence; live release-framework execution remains unsupported and fails
closed until a later explicitly gated phase.

Phase 6B materializes validated release-framework dry-run requests into deterministic, structured invocation plans.
The materialized plan records the future pytest target, supported release options, runtime-only profile reference,
environment plan, profile compatibility result, deterministic artifact directory plan, and dry-run-only execution
eligibility. These materialized requests are not executed, are not live certification evidence, do not write generated
profiles under `.release/`, and do not enable live recovery or Agent integration. Live release-framework execution
continues to fail closed until a later explicitly gated phase.

Phase 6C adds an explicitly gated execution harness for materialized release-framework requests. Dry-run remains the
default non-executing path, and `release_framework_local` can run only through an injected command-runner interface when
local gates pass and `allow_local_execution` is set. Phase 6C local harness evidence is local release-framework
execution evidence only; it is never live ACM certification evidence. Live release-framework execution, live adapters,
live discovery, automatic recovery, and Agent integration remain unsupported and fail closed.

Phase 7A adds `scripts/release/run_lab_role_controller.py` as a non-live command boundary around the deterministic
controller. The wrapper uses a built-in sanitized `hub-a`/`hub-b` plus `mc-1`/`mc-2`/`mc-3` fake lab fixture, supports
fake and release-framework dry-run/materialization modes, and writes a redacted run artifact only to an explicitly
provided `--artifact-dir` unless `--no-write` is selected. `release-framework-local` remains explicitly gated and uses
only the fake command-runner harness path. Live modes are rejected, artifacts must not claim live ACM certification
evidence, and this is not Agent integration.

Phase 8P/8Q adds deterministic GitOps evidence for the non-live Argo CD lane. The controller can parse checked-in
release-lab Kustomize fixture YAML, summarize Argo CD Application/ApplicationSet ACM ownership, classify automated sync
interference (`selfHeal`, `prune`, ApplicationSet child ownership, malformed or unknown evidence), model whether
`spec.syncPolicy.automated.enabled` is supported from explicit non-live capability evidence, and record the resulting
coordination strategy in provisional dry-run/materialized artifacts. Unknown ownership and unknown capability evidence
fail closed when required for a decision. ApplicationSet child ownership remains blocked unless explicit parent-level
coordination evidence is present. These artifacts are dry-run/materialized evidence only, are not live ACM
certification evidence, and must not be used as proof that a live Argo CD installation or live CRD schema was inspected.
Live CRD/schema detection and server-side validation remain Phase 9 work.

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

## Implementation Roadmap

### Placement Recommendation

The controller should live with the release framework, not with production switchover code.

| Candidate | Fit | Tradeoffs |
| --- | --- | --- |
| `tests/release/lab_controller/` | Recommended for the controller package. It is release-only, can reuse `tests/release` contracts directly, runs in the existing non-live release helper suite, and keeps lab certification safety logic close to the matrix validator, profile loader, adapters, discovery, and artifact helpers it wraps. | The `tests/` prefix can look less authoritative than `lib/`, so the package should have clear module boundaries and a thin script entrypoint for operator use. |
| `scripts/release/` | Good for a thin executable wrapper such as `scripts/release/run_lab_role_controller.py`. | Poor fit for core logic because scripts are harder to unit test cleanly, encourage CLI parsing mixed with safety decisions, and risk duplicating pytest framework behavior. |
| `lib/release_controller/` | Attractive if the controller were product runtime code. | Not recommended for Phase 1 because this is release-lab certification machinery, not production CLI behavior. Putting it under `lib/` would blur support boundaries and invite imports from production paths that should remain independent of test-owned release tooling. |

Recommended structure:

- `tests/release/lab_controller/__init__.py` exports the stable Phase 1 API.
- `tests/release/lab_controller/models.py` defines controller-only dataclasses and enums.
- `tests/release/lab_controller/identity.py` validates physical hub bindings.
- `tests/release/lab_controller/roles.py` makes logical role decisions from discovered evidence.
- `tests/release/lab_controller/planner.py` builds known-state segments and enforces mutation boundaries.
- `tests/release/lab_controller/profiles.py` generates static-schema release profiles for one segment.
- `tests/release/lab_controller/artifacts.py` defines the preliminary segment artifact payload and delegates writes to existing release artifact helpers.
- `scripts/release/run_lab_role_controller.py` is a thin command-line wrapper around the package.

Generated runtime profiles should not be written under `tests/release/profiles/` except for operator-local inputs
under the already ignored `tests/release/profiles/local/`. Per-segment generated profiles should default under the
operator-provided `--artifact-dir`, for example `artifacts/release-lab/<run-id>/generated-profiles/`, so they stay out
of version control and can be redacted or withheld from publishable artifacts.

### Phase 1 Scope

Phase 1 should build deterministic safety primitives only. It should not run live switchover commands by default.

1. **Data models**
   - Add immutable models for `PhysicalHub`, `HubIdentity`, `HubObservation`, `LogicalRole`, `RoleMapping`,
     `ManagedClusterExpectation`, `SegmentPlan`, `SegmentDecision`, `GeneratedProfileRef`, and a minimal
     `SegmentArtifact`.
   - Keep model fields free of kubeconfig contents and tokens. Store physical labels, context names, redacted identity
     summaries, hashes, and artifact-relative paths.

2. **Fake discovery inputs**
   - Add test fixtures or builders that produce two physical hub observations without invoking `oc`.
   - Cover `hub-a` active, `hub-b` active, both active, neither active, mismatched identity, and managed-cluster drift.
   - Reuse the shape of `tests.release.baseline.discovery.HubFacts` where possible so later live discovery can adapt
     the existing `discover_hub_facts()` output instead of inventing a second discovery vocabulary.

3. **Physical hub identity checks**
   - Bind operator labels such as `hub-a` and `hub-b` to live evidence before planning a segment.
   - Phase 1 may use fake identity values, but the model should leave room for `kube-system` namespace UID, API server
     URL hash, context name, and ACM evidence.
   - A missing, changed, duplicated, or unreadable identity is a hard NO-GO before any mutation is considered.

4. **Logical role decision engine**
   - Decide exactly one active primary from observed evidence.
   - Return explicit decisions for `PASS`, `NO_GO`, and `RECOVERY_REQUIRED`.
   - Treat ambiguous primary evidence, both hubs active, neither hub active, and unexpected managed cluster sets as
     blocking states.

5. **Known-state segment planner**
   - Accept a requested scenario plan and the current proven role mapping.
   - Permit any number of non-mutating checks in a segment.
   - Permit at most one lab-mutating scenario in a segment.
   - Block a second mutation unless a previous segment produced a proven final state and the next segment starts with
     fresh identity and role discovery.

6. **Role-aware profile generation skeleton**
   - Generate an existing-schema release profile where `hubs.primary` and `hubs.secondary` map to the currently proven
     physical hubs for one segment.
   - Feed the generated file through `tests.release.contracts.loader.load_profile()` so Phase 1 remains compatible with
     the current profile contract.
   - Record the generated profile hash and artifact-relative path, not raw credential material, in the segment artifact.

7. **Artifact model skeleton**
   - Define a schema-versioned but explicitly provisional segment artifact with initial role mapping, expected final
     mapping, generated profile hash, decision, failure reasons, and redaction status.
   - Use `tests.release.reporting.artifacts.ReleaseArtifacts` and `tests.release.reporting.redaction` for persistence
     and sanitization.

### Existing Framework Reuse

The controller should be a sequencing layer around the current release framework. It should reuse these pieces instead
of duplicating them:

- `tests/release/scenarios/catalog.py`
  - `SCENARIOS_BY_ID`, `V1_SCENARIOS`, and `ScenarioLifecycle` for scenario identity and current mutation metadata.
  - `select_release_matrix()` to derive each segment's effective matrix.
  - `validate_release_matrix()` to preserve existing support checks and the focused single-mutation rule per segment.
  - `matrix_validation_results()` for consistent blocked-matrix result payloads.
- `tests/release/contracts/models.py`
  - `HubProfile`, `ManagedClustersProfile`, `StreamProfile`, `ScenarioProfile`, `BaselineProfile`,
    `ReleaseProfile`, and `LoadProfileResult` for generated profile compatibility.
- `tests/release/contracts/loader.py`
  - `load_profile()` to validate generated profiles and compute their SHA-256 hash.
- `tests/release/contracts/schema.py`
  - Existing credential rejection and static profile validation through the loader path; do not bypass
    `validate_profile_contents()`.
- `tests/release/baseline/discovery.py`
  - `HubDiscoveryClient`, `HubFacts`, and `discover_hub_facts()` as the initial discovery vocabulary.
- `tests/release/baseline/fingerprint.py`
  - `build_environment_fingerprint()` for baseline evidence snapshots, while adding stricter controller identity
    checks outside the fingerprint.
- `tests/release/baseline/assertions.py`
  - `assert_baseline()` for existing primary/secondary and managed-cluster assertions.
- `tests/release/checks/lab_readiness.py`
  - `assert_lab_readiness()` for current non-mutating readiness checks.
- `tests/release/baseline/recovery.py`
  - `RecoveryPolicy`, `RecoveryBudget`, and `plan_recovery_actions()` later for recorded recovery decisions, not
    automatic live recovery in Phase 1.
- `tests/release/reporting/artifacts.py` and `tests/release/reporting/redaction.py`
  - `ReleaseArtifacts`, `write_capture_artifact()`, `sanitize_text()`, and `RedactionError` for safe artifact writes.
- `tests/release/orchestrator.py`
  - `run_release_certification()` as the delegated pytest certification entrypoint once execution is wired.
  - `build_default_adapters()` and `build_default_discovery_clients()` as later integration points, after Phase 1
    proves the planner and profile generator with fakes.
- `tests/release/adapters/python_cli.py` and `tests/release/adapters/ansible.py`
  - Existing command and extra-var construction. The controller should supply a role-aware profile and let these
    adapters continue building stream commands.
- `tests/release/conftest.py`
  - Existing CLI concepts such as profile, mode, scenario filter, stream filter, artifact dir, and dirty-check policy
    should shape the controller CLI, even if the script does not import pytest fixtures directly.

### Phase 1 Tests

Add focused non-live unit tests under `tests/release/lab_controller/`.

- `test_roles_detect_hub_a_primary_hub_b_secondary`: fake observations show `hub-a` active with exactly
  `mc-1`, `mc-2`, `mc-3`; the decision maps `hub-a` to logical primary and `hub-b` to logical secondary.
- `test_roles_detect_hub_b_primary_hub_a_secondary`: same evidence after a role transition; generated mapping is
  swapped and does not reuse stale static profile roles.
- `test_roles_fail_ambiguous_primary`: conflicting role signals on one or both hubs produce NO-GO with no segment
  planned.
- `test_roles_fail_both_hubs_active`: both hubs appear primary for the expected managed cluster set; the controller
  returns hard NO-GO.
- `test_roles_mark_neither_hub_active_recovery_required`: neither hub has active-primary evidence; the controller
  returns RECOVERY_REQUIRED.
- `test_roles_fail_unexpected_managed_cluster_set`: active hub has anything other than `mc-1`, `mc-2`, `mc-3`; planning
  stops before mutation.
- `test_profiles_reject_stale_profile_after_role_transition`: after `hub-b` becomes primary, a generated or selected
  profile that still maps `hubs.primary` to `hub-a` is rejected before execution.
- `test_planner_allows_one_mutating_segment`: a segment with prerequisites, one mutating scenario, runtime parity, and
  final baseline is accepted when the starting state is proven.
- `test_planner_blocks_second_mutation_without_proven_state`: two mutating scenarios in the same known-state segment,
  or a second segment without a proven prior final state, is rejected.

Useful companion tests:

- identity mismatch blocks planning before role evaluation.
- generated profile loads with `load_profile()` and has a stable SHA-256.
- segment artifact persistence rejects unsafe content through the existing redaction layer.
- unknown catalog scenario fails closed until classified.

### First CLI Contract

The first operator-facing command is a thin wrapper, not the implementation authority:

```bash
python scripts/release/run_lab_role_controller.py \
  --plan ping-pong \
  --mode release-framework-dry-run \
  --artifact-dir artifacts/release-lab/20260616T000000Z
```

Phase 7A contract:

- `--plan ping-pong` selects the deterministic fake ping-pong plan.
- `--mode fake` runs the fake controller path; `--mode release-framework-dry-run` materializes release-framework
  requests without invoking pytest or adapters.
- `--mode release-framework-local` requires `--allow-local-execution` and uses only the fake command-runner harness
  path; it is local harness evidence, not live ACM certification evidence.
- `--allow-local-execution` is optional by default and is required only when selecting
  `--mode release-framework-local`; it permits the local fake command-runner harness path and does not enable live
  execution.
- `--artifact-dir` is required unless `--no-write` is set, is caller-directed, and receives `lab-controller-run.json`.
- Artifact output is sanitized/redacted before writing and must not claim live execution or live certification evidence.
- `--no-write` prints only a sanitized summary and writes no artifact files.
- `--strict` is optional and defaults to disabled; without it, a completed non-`PASS` controller decision can still
  return exit code `0` for deterministic local inspection, while artifacts and stdout retain the non-`PASS` decision.
- Live modes such as `live` or `release-framework-live` fail closed.
- Exit code `0` means the CLI completed successfully. Exit code `1` means `--strict` was set and the final controller
  decision was not `PASS`. Exit code `2` means invalid CLI usage or validation failure. Exit code `3` means artifact
  redaction or write failure.
- The script must not accept arbitrary mutation commands. It accepts known catalog scenario names and delegates later
  execution to the release framework.

### Out Of Scope For First Implementation

- Automatic live recovery.
- Destructive decommission.
- Exact JSON artifact schema finalization.
- Agent skill or Agent-specific workflow automation.
- Full live two-hub, three-managed-cluster certification.
- Production CLI or collection behavior changes.
- Release profile schema changes beyond generated files that already satisfy the current schema.
- Live execution of multiple mutating scenarios.
- Committed generated profiles, kubeconfig paths, cluster IDs, credentials, or artifacts.

### Risks And Mitigations

- **Unreliable role discovery signals**: BackupSchedule and Restore evidence alone can be misleading. Phase 1 should
  model multiple evidence fields, fail closed on disagreement, and keep the decision engine separate from raw discovery.
- **Accidental use of stale static profiles**: A successful switchover changes logical roles. The controller must
  generate a fresh role-aware profile per segment, record its hash, and reject any profile whose logical roles do not
  match the freshly discovered physical mapping.
- **Unsafe retries after mutation**: A failed mutating scenario may leave the lab in an intermediate state. Retries must
  be blocked unless fresh discovery proves the next starting state; post-mutation failures should produce
  RECOVERY_REQUIRED or NO-GO, not automatic reruns.
- **Leaking identity or kubeconfig details into artifacts**: Generated execution profiles may contain real paths. Keep
  them in ignored/private artifact directories, store only hashes and redacted summaries in publishable artifacts, and
  use the existing redaction layer for text outputs.
- **Duplicated logic between controller and pytest framework**: The controller should own sequencing, identity, and
  role safety only. Scenario selection, profile validation, adapters, baseline checks, runtime parity, summary, and
  artifact redaction should stay in the existing release framework.
- **Controller bypass of conservative matrix validation**: Segment planning must not weaken `validate_release_matrix()`.
  It should call the validator for each generated segment and add stronger known-state checks around it.
- **Operator confusion between diagnostic focused reruns and certification**: Artifacts and CLI output must say whether
  a run is certification-eligible, diagnostic-only, NO-GO, or recovery-required.

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
