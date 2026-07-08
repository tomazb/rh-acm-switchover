# Property-Based Testing (PBT) Plan

Status: PBT-01 (documentation only). Part of issue #136.

This document is the high-level plan for introducing property-based testing
(PBT) into the ACM switchover project. Two companion documents complete the
PBT-01 deliverable:

- [`property-based-testing-spec.md`](property-based-testing-spec.md) — the
  implementation specification for the seven planned PBT suites.
- [`property-based-testing-pr-workflow.md`](property-based-testing-pr-workflow.md) —
  the process contract for the nine-PR rollout and the three-prompt
  (Builder / Validator / Resolver) PR workflow.

PBT-01 adds **no code, no dependencies, no CI changes**. It defines what the
follow-up PRs (PBT-02 through PBT-09) will build and how they will be
reviewed and merged.

## Why property-based testing

The project maintains two form factors under a deliberate dual-support
parity contract (see `AGENTS.md`):

1. The **Python CLI** (`acm_switchover.py`, `lib/`, `modules/`).
2. The **Ansible collection** (`ansible_collections/tomazb/acm_switchover/`),
   with thin plugins and shared logic in `plugins/module_utils/`.

The codebases cannot import from each other, so parity is maintained
deliberately through mirrored implementations and guardrail tests such as
`tests/test_constants_parity.py`, `tests/test_argocd_constants_parity.py`,
and `tests/test_rbac_collection_parity.py`. Today's example-based unit tests
verify parity and behavior at hand-picked points. Property-based tests
strengthen this in three ways:

- **Parity as a property.** Instead of asserting that both implementations
  accept `my-cluster` and reject `-bad-name`, a PBT suite generates thousands
  of valid-shaped and near-valid inputs and asserts that both form factors
  make the *same* accept/reject decision on every one. Divergence anywhere in
  the input domain becomes a test failure, not a latent bug.
- **Invariants over examples.** Safety-critical logic — path traversal
  rejection in `lib/path_safety.py`, checkpoint/resume semantics in
  `lib/utils.py` (`StateManager`) and
  `plugins/module_utils/checkpoint.py`, Argo CD pause safety in
  `lib/argocd.py` and `plugins/module_utils/argocd.py` — has invariants
  ("a validated path never resolves outside a safe root", "a pause patch
  never enables auto-sync") that hold for *all* inputs. PBT states them once
  and searches for counterexamples automatically.
- **Edge-case discovery.** Generated inputs routinely find boundary bugs
  (length limits, unicode, empty collections, metadata corner cases) that
  hand-written examples miss.

## Goals

- Encode the dual-support parity contract as executable properties wherever
  both form factors implement the same rule, so the Python CLI and the
  Ansible collection are continuously checked for behavioral agreement.
  PBT **reinforces** the parity contract; it never replaces or relaxes it.
- State and enforce safety invariants for the highest-risk pure logic:
  input validation, path safety, checkpoint/resume, report artifacts,
  BackupSchedule handling, Argo CD safety filtering, and RBAC permission
  set derivation.
- Keep the suites fast and deterministic enough to run in the default test
  gate alongside the existing unit tests.
- Roll out incrementally in nine small, independently reviewable PRs
  (see the rollout sequence below).

## Non-goals

- **No live-cluster testing.** PBT suites never talk to a Kubernetes API
  server, real or otherwise (see the safety model below).
- **No replacement of existing tests.** Existing example-based unit tests,
  parity guardrail tests, release tests, and E2E tests all remain. The
  deliberate dual-support parity tests (e.g. `tests/test_constants_parity.py`,
  `tests/test_rbac_collection_parity.py`) are guardrails and are not retired.
- **No fuzzing of the CLI process or Ansible runtime.** Properties target
  importable pure functions and classes, not subprocess entry points or
  playbook execution.
- **No speculative abstractions.** Per the project's YAGNI principle, no
  generator or helper is added until a suite in the spec needs it.
- **No coverage-percentage targets.** Suites are scoped by invariant value,
  not by line coverage.

## Safety model

Property-based tests are, by construction, incapable of touching live
clusters:

- **Pure functions and local fixtures only.** Every property targets logic
  that is either a pure function (e.g. `lib/path_safety.py`,
  `plugins/module_utils/checkpoint.py`, `plugins/module_utils/argocd.py`)
  or a class exercised against mocked clients and temporary directories
  (e.g. `StateManager` with a `tmp_path` state file,
  `BackupScheduleManager` with a mocked `KubeClient`, following the
  mocked-`KubeClient` fixture pattern used throughout `tests/`).
- **No kubeconfig, no network, no live-cluster requirement.** Suites must
  run to completion on a developer laptop and in CI with no cluster access
  whatsoever. Any suite that would require a cluster is out of scope for PBT
  and belongs to the existing E2E/live validation layers instead.
- **No mutation of real operator state.** Filesystem interaction is limited
  to pytest-managed temporary directories.
- **Deterministic CI behavior.** Suites run with fixed derandomization
  settings in CI so failures are reproducible; discovered counterexamples
  are pinned as regression examples.

## The semantic generator principle

Generators produce **domain objects, not arbitrary byte blobs**. Random
bytes overwhelmingly exercise the trivial "reject garbage" branch and never
reach the interesting logic. Instead, each suite defines generators that
know the domain's shape and then perturb it:

- **Valid-shaped ManagedCluster / Kubernetes resource names**: RFC 1123
  labels of varying lengths, plus targeted near-valid mutations (uppercase
  characters, leading/trailing hyphens, over-length names, dotted subdomain
  forms) to probe both sides of every rule in
  `lib/validation.py` (`InputValidator`) and
  `plugins/module_utils/validation.py`.
- **BackupSchedule specs**: dictionaries shaped like real
  `cluster.open-cluster-management.io/v1beta1 BackupSchedule` resources with
  generated `spec` fields, schedule strings, paused flags, and runtime
  metadata, matched with generated ACM version strings for the
  `acm_supports_backup_schedule_pause` version gate in
  `modules/backup_schedule.py`.
- **Checkpoint states**: operation-identity dictionaries, completed-phase
  lists drawn from the real `Phase` enum names, and generated sequences of
  `StateManager` operations, so resume semantics are tested over realistic
  state histories.
- **Argo CD Applications**: dictionaries with generated `syncPolicy`,
  `status.resources`, and `ownerReferences` structures matching what
  `kubernetes.core.k8s_info` and `KubeClient` return.

The full generator domains per suite are defined in the
[spec](property-based-testing-spec.md).

## Rollout sequence

The rollout is nine PRs. Full details, prompt contracts, and gating rules
are in [`property-based-testing-pr-workflow.md`](property-based-testing-pr-workflow.md).

| PR | Scope |
| --- | --- |
| PBT-01 | This documentation set (plan, spec, PR workflow). No code. |
| PBT-02 | Scaffolding: PBT dependency, test layout, shared generator/helper module, pytest marker and CI wiring. |
| PBT-03 | Suite 1 — validation parity properties. |
| PBT-04 | Suite 2 — path-safety properties. |
| PBT-05 | Suite 3 — checkpoint/resume properties. |
| PBT-06 | Suite 4 — report artifact properties. |
| PBT-07 | Suite 5 — BackupSchedule properties. |
| PBT-08 | Suite 6 — Argo CD safety properties. |
| PBT-09 | Suite 7 — RBAC set-property tests. |

PBT-02 depends on PBT-01. PBT-03 through PBT-09 each depend only on PBT-02
and are otherwise independent of one another.

## Relationship to existing test layers

PBT is an additional layer, not a replacement. The layers and their roles:

| Layer | Location / entry point | Role | PBT relationship |
| --- | --- | --- | --- |
| Python unit/integration tests | `tests/` via `./run_tests.sh` or `pytest tests/` | Example-based verification of business logic with mocked `KubeClient` | PBT suites live alongside them and reuse the mocked-client fixture pattern; counterexamples found by PBT get pinned here as regression examples |
| Collection unit tests | `ansible_collections/tomazb/acm_switchover/tests/unit/` (plain pytest) | Example-based verification of plugins and `module_utils` | Parity properties import the same `module_utils` functions these tests cover |
| Parity guardrail tests | e.g. `tests/test_constants_parity.py`, `tests/test_rbac_collection_parity.py`, `tests/test_argocd_constants_parity.py` | Deliberate dual-support guardrails pinning cross-form-factor agreement | PBT generalizes point checks into generated-input agreement properties; the guardrails remain authoritative and are never retired by PBT work |
| Release tests | `tests/release/` | Release validation framework | Unchanged; PBT does not gate releases beyond the normal test run |
| E2E tests | `tests/e2e/`, opt-in via `RUN_E2E=1 ./run_tests.sh` | End-to-end workflow verification | Unchanged; anything needing a cluster stays here, never in PBT |
| Live validation | `scripts/preflight-check.sh`, `scripts/postflight-check.sh`, `--validate-only` | Operator-run checks against real hubs | Entirely out of PBT scope by the safety model |

## Parity statement

Every PBT suite that targets dual-supported behavior tests **both** form
factors and, where the behavior is a shared rule, asserts cross-form-factor
agreement. Nothing in this plan changes any capability's parity status as
recorded in `docs/ansible-collection/parity-matrix.md`, and no PBT PR may
imply divergence between the Python CLI and the Ansible collection. If a
property ever exposes a real behavioral difference, that is a parity bug to
be fixed under the normal parity rules in `AGENTS.md` — not a reason to
weaken the property.
