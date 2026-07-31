# ACM Switchover - AI Agent Instructions

## Project Overview

This project delivers ACM hub switchover automation in two production form factors:

1. **Python CLI** (`acm_switchover.py`) — the original implementation; a monolithic orchestrator with persisted state, full retry logic, and a rich CLI surface.
2. **Ansible Collection** (`tomazb.acm_switchover` at `ansible_collections/tomazb/acm_switchover/`) — a second form factor targeting `ansible-core` CLI and Ansible Automation Platform (AAP), built with roles, playbooks, and thin custom plugins.

Both tools automate the same phased workflow for migrating from a primary ACM hub to a secondary hub with idempotent execution and comprehensive validation. They share the same runbook steps but have independent codebases and cannot import from each other.

## Engineering Principles

- **DRY**: Avoid duplication in code, tests, and documentation; prefer shared helpers or utilities where it makes sense.
- **KISS**: Prefer simple, straightforward solutions over clever or overly abstract designs.
- **YAGNI**: Do not add speculative features or abstractions until they are clearly needed.
- **Fail fast with clear errors**: Detect problems early and surface explicit, actionable error messages.
- **Prefer explicit over implicit**: Make control flow, side effects, and configuration obvious at call sites.
- **Keep changes minimal and localized**: Touch as few files and code paths as possible to implement a change.
- **Respect existing patterns and abstractions**: Align with current architecture and style unless there is a strong reason to refactor.
- **Keep AGENTS.md current**: Update this file when making significant architectural, workflow, module, or CLI changes.
- **Keep READMEs and Mermaid diagrams current**: When workflow, phase ordering, CLI branching, script checks, or operator-facing behavior changes, update the relevant README files and any Mermaid diagrams in `README.md`, `docs/`, and `scripts/README.md` that describe that flow. READMEs and diagrams are part of the documentation contract and must not drift from the implementation.

## Dual-Supported Parity Contract

The Python CLI and the Ansible collection are independent codebases, but many operator-facing capabilities remain **dual-supported** during coexistence. Drift is not allowed by default.

- **Status authority**: [`docs/ansible-collection/parity-matrix.md`](docs/ansible-collection/parity-matrix.md) defines whether a capability is `dual-supported`, `Python only`, `collection only`, `deprecated`, or `release-only`.
- **Behavior mapping authority**: [`docs/ansible-collection/behavior-map.md`](docs/ansible-collection/behavior-map.md) maps Python sources to the collection target that must be reviewed for parity.
- **Coexistence policy**: [`ansible_collections/tomazb/acm_switchover/docs/coexistence.md`](ansible_collections/tomazb/acm_switchover/docs/coexistence.md) defines the shared-behavior contract during the coexistence period.
- **Default rule**: If a capability is documented as `dual-supported`, update both implementations and their tests/docs together unless an intentional divergence is explicitly approved and documented first.
- **Independent codebases are not an exception**: "cannot import from each other" means parity must be maintained deliberately via docs, tests, and mirrored implementation work.

### Current Dual-Supported Capability Surface

Treat these as parity-sensitive unless the parity matrix says otherwise:

- preflight validation
- primary prep
- activation
- post-activation verification
- finalization
- RBAC self-validation
- RBAC bootstrap
- Argo CD management
- discovery
- decommission
- shared machine-readable reports
- optional checkpoints

### Approval Gate For Intentional Parity Changes

**Explicit operator approval is required before implementing an intentional parity change.**

This approval gate applies when a planned change would:

- leave a `dual-supported` capability intentionally different between Python and the collection
- change a capability's documented parity status (`dual-supported`, `Python only`, `collection only`, `deprecated`, `release-only`)
- knowingly defer realignment of the other implementation as follow-up work

Do **not** use this gate for ordinary parity-preserving bug fixes where both implementations are updated together.

When requesting approval for an intentional parity change, include:

1. The affected capability or capabilities
2. The current documented parity status
3. The proposed new status or intentional divergence
4. Why parity cannot or should not be preserved in the current change
5. Operational/user impact
6. Test and documentation impact
7. What must be realigned later if the divergence is temporary

### Where To Record Approved Parity Changes

Approved parity changes must be documented in the repo, not only in a PR or commit message.

- Update [`docs/ansible-collection/parity-matrix.md`](docs/ansible-collection/parity-matrix.md) whenever parity status or support posture changes.
- Update [`docs/ansible-collection/behavior-map.md`](docs/ansible-collection/behavior-map.md) when behavior ownership, mapping, or implementation target changes.
- Update [`ansible_collections/tomazb/acm_switchover/docs/coexistence.md`](ansible_collections/tomazb/acm_switchover/docs/coexistence.md) when the shared-behavior boundary or coexistence policy changes.
- Update [`ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md`](ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md) when the operator-facing CLI-to-collection capability contract changes.
- Update [`docs/ansible-collection/scenario-catalog.md`](docs/ansible-collection/scenario-catalog.md) or [`docs/ansible-collection/test-migration-catalog.md`](docs/ansible-collection/test-migration-catalog.md) when shared scenarios or parity test expectations change.
- Update [`CHANGELOG.md`](CHANGELOG.md) when the approved parity change affects supported workflows, operator-facing behavior, or deprecation/support status.
- Update the domain-specific docs as well when they are part of the impacted support surface (for example architecture, RBAC deployment/requirements, or usage docs).

### RBAC Realignment And Divergence

RBAC changes are parity-sensitive even when the code edit is indirect. If RBAC behavior, permissions, or resources change, review and realign all affected surfaces:

- Python RBAC validation logic in [`lib/rbac_validator.py`](lib/rbac_validator.py)
- Collection RBAC validation logic in [`ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_validate.py`](ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_validate.py)
- Collection task wiring that consumes the RBAC matrix (`preflight`, `decommission`, `rbac_bootstrap`)
- Root RBAC manifests in [`deploy/rbac/`](deploy/rbac/)
- Collection-bundled RBAC manifest copies in [`ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/files/deploy/rbac/`](ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/files/deploy/rbac/)
- Helm RBAC chart/templates in [`deploy/helm/acm-switchover-rbac/`](deploy/helm/acm-switchover-rbac/)
- RBAC docs in [`docs/deployment/rbac-requirements.md`](docs/deployment/rbac-requirements.md), [`docs/deployment/rbac-deployment.md`](docs/deployment/rbac-deployment.md), and [`docs/development/rbac-implementation.md`](docs/development/rbac-implementation.md)
- RBAC tests on both sides

Examples of indirect RBAC changes that still require review:

- adding a new Kubernetes API call, verb, resource kind, or namespace
- changing Argo CD integration behavior that alters required permissions
- changing bootstrap-applied manifests or asset selection
- changing decommission privileges or support boundaries

If an RBAC parity change is intentional rather than a full realignment, get operator approval first and document exactly which permissions, resources, or workflows now differ and why.

## Protected Critical Files

The following files are **safety-critical operational documents** that AI agents MUST NOT modify without explicit operator approval:

| Protected File | Reason |
| --- | --- |
| [`docs/ACM_SWITCHOVER_RUNBOOK.md`](docs/ACM_SWITCHOVER_RUNBOOK.md) | Authoritative blueprint for manual ACM hub switchovers. Contains critical safety warnings, step-by-step procedures, and rollback instructions. Incorrect changes can lead to cluster destruction. |
| `.claude/skills/**/*.skill.md` | Operational and troubleshooting SKILLS derived from the runbook. Must stay in sync with the runbook at all times. |

### Protection Rules

1. **Read-only by default**: AI agents must treat these files as read-only. Edits are technically blocked by a `.claude/settings.json` PreToolUse hook.
2. **Explicit operator approval required**: Only modify these files when the operator explicitly requests the change and understands the implications.
3. **Careful line-by-line review**: Every proposed change must be presented as a diff for the operator to review before committing. Do not batch runbook changes with other unrelated edits.
4. **Justification required**: Any proposed change must include a clear explanation of _why_ the change is necessary and what operational impact it has.
5. **Runbook ↔ SKILLS sync obligation**: Changes to the runbook require corresponding SKILLS updates, and vice versa. Never update one without the other.
6. **No speculative or cosmetic edits**: Do not reformat, reorganize, or "improve" these files unless the operator specifically asks for it.

## Architecture

**Entry Point**: `acm_switchover.py` - Main orchestrator using `Phase` enum and `StateManager`

**Core Libraries** (`lib/`):
- `kube_client.py` - Kubernetes API wrapper with `@retry_api_call` decorator, validation, dry-run support
- `utils.py` - `StateManager` for idempotent state tracking, `Phase` enum, `@dry_run_skip` decorator
- `constants.py` - Centralized namespaces, timeouts, ACM spec field names
- `exceptions.py` - Hierarchy: `SwitchoverError` → `TransientError`/`FatalError` → `ValidationError`/`ConfigurationError`
- `validation.py` - Input validation with `InputValidator` class and `SecurityValidationError`
- `rbac_validator.py` - RBAC permission checks for operator/validator roles
- `waiter.py` - Generic polling/wait utilities for async conditions
- `argocd.py` - Argo CD detection and pause/resume of Application auto-sync (supports operator and vanilla installs)
- `argocd_coordinator.py` - Centralizes ArgoCD pause coordination across hubs; used by PrimaryPrep and restore-only mode
- `gitops_detector.py` - GitOps marker detection for resources (Argo CD, Flux) to warn operators before mutations
- `report_artifacts.py` - Machine-readable report artifact helpers for the Python CLI

**Workflow Modules** (`modules/`):
- `preflight/` - Modular pre-flight validators
- `preflight_coordinator.py` - Coordinates pre-flight validation across modules
- `preflight_validators.py` - Deprecated compatibility shim (prefer `modules.preflight`)
- `backup_schedule.py` - Shared helpers for BackupSchedule management
- `primary_prep.py` - Pause backups, disable auto-import, scale down Thanos
- `activation.py` - Patch restore resource to activate managed clusters
- `post_activation.py` - Verify cluster connections, fix klusterlet agents
- `finalization.py` - Set up old hub as secondary or prepare for decommission; accepts `restore_only=True` to warn (not fail) when BackupSchedule is absent
- `decommission.py` - Remove ACM from old hub
- `restore_discovery.py` - Shared restore discovery helpers (passive sync restore lookup)

## Phase Flow

The switchover executes phases sequentially, with state tracking for resume capability:

```
INIT → PREFLIGHT → PRIMARY_PREP → ACTIVATION → POST_ACTIVATION → FINALIZATION → COMPLETED
```

Restore-only mode (`--restore-only`) uses a reduced flow, skipping PRIMARY_PREP:
```
INIT → PREFLIGHT(secondary-only) → ACTIVATION → POST_ACTIVATION → FINALIZATION(backups-only) → COMPLETED
```

> **Note**: ACM excludes `BackupSchedule` from Velero backups to prevent circular backup-of-backup issues. After `--restore-only`, no BackupSchedule exists on the hub — the operator must create one manually. `Finalization` handles this gracefully when `restore_only=True` (warns instead of failing).

Defined phases in `Phase` enum: `INIT`, `PREFLIGHT`, `PRIMARY_PREP`, `SECONDARY_VERIFY`, `ACTIVATION`, `POST_ACTIVATION`, `FINALIZATION`, `COMPLETED`, `FAILED`. The main switchover flow uses the diagram above; `FAILED` is set on errors.
|
| Phase | Runbook Steps | Module | Key Actions |
| --- | --- | --- | --- |
| `PREFLIGHT` | Step 0 | `preflight_coordinator.py` + `preflight/` | Validate both hubs, check ACM versions, verify backups |
| `PRIMARY_PREP` | Steps 1-3 / F1-F3 | `primary_prep.py` | Pause BackupSchedule, add disable-auto-import annotations, scale Thanos |
| `ACTIVATION` | Steps 4-5 / F4-F5 | `activation.py` | Verify passive sync or create full restore, activate clusters |
| `POST_ACTIVATION` | Steps 6-10 / F6 | `post_activation.py` | Wait for ManagedClusters to connect, verify klusterlet agents |
| `FINALIZATION` | Steps 11-12 | `finalization.py` | Enable backups, verify integrity, handle old hub state |
| (manual) | Step 13 | — | Inform stakeholders (out-of-band) |
| (separate) | Step 14 | `decommission.py` | Decommission old hub |
| (separate) | Rollback 1-5 | (manual/partial) | Rollback procedures |
|
Each phase handler checks `state.get_current_phase()` before executing. Failed phases set `Phase.FAILED`.

## Key Patterns

### Idempotent Step Execution
```python
if not self.state.is_step_completed("step_name"):
    self._do_step()
    self.state.mark_step_completed("step_name")
    # Note: mark_step_completed() persists via save_state() for durability
```

### State Persistence Pattern
The StateManager uses optimized write batching:
- **`save_state()`**: Writes only if state is dirty (has pending changes). Used for step/config updates.
- **`flush_state()`**: Forces immediate write. Use for critical checkpoints (phase transitions, errors, resets).

Critical operations automatically call `flush_state()`:
- `set_phase()` - Phase transitions
- `add_error()` - Error recording
- `reset()` - State resets
- `ensure_contexts()` - Context changes

Non-critical operations persist state via `save_state()`:
- `mark_step_completed()` - Step completion tracking (immediate durability via `save_state()`)
- `set_config()` - Configuration storage (immediate durability via `save_state()`)

State is automatically flushed on program termination (SIGTERM/SIGINT/atexit) to prevent data loss.

**Hub identity binding (`cluster_uid`):** `StateManager` records each hub's live cluster UID (the `kube-system` namespace UID) alongside its context name in `state["hub_identities"]`. On every load/resume, the live UIDs are re-read and compared to the stored values. Resume fails closed before any mutation if a recorded UID no longer matches the cluster behind the same context name, if hub identities are missing for an in-progress switchover, or if the live UID is unreadable. Operators recover with `--reset-state` (different cluster on purpose) or `--force` (legacy state, after manual verification). See `docs/operations/usage.md` "Hub identity binding on resume".

### Dry-Run Decorator
```python
@dry_run_skip(message="Would scale deployment", return_value={})
def scale_deployment(self, name, namespace, replicas):
    # Only executes when self.dry_run is False
```

### Exception Hierarchy
- Use `SwitchoverError` for domain-specific workflow errors
- Use `FatalError` for non-recoverable errors (e.g., missing resources)
- Wrapper methods (e.g., `secret_exists`) should NOT have `@retry_api_call` if they call methods that already have it

### Constants Usage
- Python-only constants belong in `lib/constants.py`
- Collection-only constants belong in `ansible_collections/tomazb/acm_switchover/plugins/module_utils/constants.py`
- Shared cross-form-factor constants must be updated on both sides and kept in parity via the existing parity tests
- Never hard-code shared namespaces or resource names when a centralized constant already exists

### KubeClient Pattern
- Methods return `Optional[Dict]` for get operations (None = not found)
- Use `e.status == 404` to check ApiException, not string matching
- Per-instance TLS configuration to avoid global side effects

## Ansible Collection

The collection lives at `ansible_collections/tomazb/acm_switchover/`. It is a complete, production-ready implementation — not a prototype or wrapper.

### Collection Structure

**Playbooks** (operator entrypoints, in `playbooks/`):
- `switchover.yml` — full workflow: preflight → primary_prep → activation → post_activation → finalization
- `restore_only.yml` — restore-only workflow: preflight → [ArgoCD pause] → activation → post_activation → finalization (no primary hub)
- `preflight.yml` — preflight validation only
- `decommission.yml` — decommission old hub
- `rbac_bootstrap.yml` — set up service accounts and RBAC
- `discovery.yml` — standalone resource discovery
- `argocd_resume.yml` — resume Argo CD auto-sync after switchover
- `argocd_manage_test.yml` — test Argo CD management integration

**Roles** (phase modules in `roles/`):

| Role | Python equivalent | Key actions |
| --- | --- | --- |
| `preflight` | `preflight_coordinator.py` | Validate both hubs, check ACM versions, verify backups, RBAC |
| `primary_prep` | `primary_prep.py` | Pause BackupSchedule, disable auto-import, scale Thanos |
| `activation` | `activation.py` | Verify passive sync or create full restore, activate clusters |
| `post_activation` | `post_activation.py` | Verify ManagedClusters, klusterlet agents, observability |
| `finalization` | `finalization.py` | Enable backups, verify integrity, handle old hub |
| `decommission` | `decommission.py` | Delete ManagedClusters, remove MultiClusterHub, observability |
| `argocd_manage` | `lib/argocd.py` | Discover and pause/resume Argo CD auto-sync |
| `discovery` | (shared, new) | Standalone resource discovery across both hubs |
| `rbac_bootstrap` | RBAC setup scripts | Create service accounts, roles, and kubeconfigs |

**Plugins** (in `plugins/`):
- `modules/` — custom modules: `acm_backup_schedule`, `acm_checkpoint`, `acm_checkpoint_identity_validate`, `acm_cluster_verify`, `acm_discovery`, `acm_input_validate`, `acm_klusterlet_probe`, `acm_klusterlet_remediate`, `acm_kubeconfig_inspect`, `acm_managedcluster_status`, `acm_preflight_report`, `acm_rbac_bootstrap`, `acm_rbac_validate`, `acm_report_artifact`, `acm_restore_info`, `acm_argocd_filter`, `acm_safe_path_validate`
- `module_utils/` — shared utilities: `argocd`, `artifacts`, `checkpoint`, `constants`, `gitops`, `klusterlet`, `result`, `validation`
- `action/checkpoint_phase.py` — action plugin for phase checkpointing

### Key Ansible Patterns

**`discover_resources.yml`** — The first `include_tasks` in each role's `main.yml` block. Uses `kubernetes.core.k8s_info` to fetch resources the role needs, guarded by `when: <var> is not defined` so tests can pre-seed variables without live clusters:
```yaml
- name: Get BackupSchedule on secondary hub
  kubernetes.core.k8s_info: ...
  register: acm_secondary_backup_schedule_info
  when: acm_secondary_backup_schedule_info is not defined
```
Exception: MCH discovery in `finalization` is unconditional — the MCH status changes after activation, so the preflight-cached value is always stale. The `preflight` role applies the same exception during execute-mode runs: MCH discovery refreshes even when callers pre-seed the discovery variable, preventing stale cached MCH data from satisfying live mutation validation. Tests that need to control MCH discovery must do so via fixtures keyed on execute-mode, not by pre-seeding the variable.

**Hub access** — All tasks reach hubs via `acm_switchover_hubs.primary` and `acm_switchover_hubs.secondary`, each providing `kubeconfig` and `context`:
```yaml
kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
context: "{{ acm_switchover_hubs.secondary.context }}"
```

**Variable namespacing** — All collection variables use the `acm_switchover_` prefix:
- `acm_switchover_hubs` — hub connection details (primary + secondary)
- `acm_switchover_operation` — controls mode (switchover/decommission/setup/dry_run/restore_only)
- `acm_switchover_features` — feature flags (e.g., `skip_observability_checks`)

**Constants isolation** — The collection **cannot** import from `lib/constants.py` (different Python namespace). All constants live in `plugins/module_utils/constants.py`. Never cross-import between the collection and the Python CLI.

### Ansible Collection Testing

```bash
# Collection unit tests only
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q

# Full suite (collection + Python CLI tests together)
source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ tests/ -q
```

Tests live in `tests/unit/`. They use plain `pytest` (not `ansible-test`). Mock responses mirror `kubernetes.core.k8s_info` return shapes. Integration test fixtures in `tests/integration/conftest.py` use `yaml.safe_load(...) or {}` to safely handle empty fixture files.

### Adding to the Collection

**New role task**: Follow the `discover_resources.yml` → `main.yml` pattern. Use `acm_switchover_hubs` for hub access. Guard discovery with `when: <var> is not defined`.

**New module**: Add to `plugins/modules/`, implement `run_module()` with `AnsibleModule`. Shared logic goes in `plugins/module_utils/`.

**New constant**: Add to `plugins/module_utils/constants.py`. Never add to or import from `lib/constants.py`.

## Testing

Keep tests current with any behavior changes. The default test run excludes E2E; run E2E on demand.

```bash
# Core tests with coverage (default, excludes E2E)
./run_tests.sh

# On-demand E2E
RUN_E2E=1 ./run_tests.sh

# Quick pytest run
pytest tests/ -v

# Run specific test file
pytest tests/test_kube_client.py -v

# Run with markers
pytest -m unit tests/      # Unit tests only
pytest -m integration tests/  # Integration tests
```

Tests use mocked `KubeClient` - fixture pattern in `tests/conftest.py`. Mock responses should include `resourceVersion` in metadata for patch verification tests.

### Strict Quality (default-on)

`./run_tests.sh` runs the CI-equivalent `black`, `isort`, `mypy`, and `bandit`
checks as **hard failures by default**. Set `STRICT_QUALITY=0` only for
advisory local runs (e.g., exploratory debugging) — never rely on it for
pre-push verification. Pre-push runs must use the default strict mode so local
results match CI.

### Release Validation

Release validation lives under `tests/release/`. The default `./run_tests.sh`
now runs the non-live `tests/release/` helper suite explicitly after the root
test lane; run `python -m pytest tests/release -q` directly when you need a
tighter release-framework loop. Live certification requires an explicit profile
via `--release-profile` or `ACM_RELEASE_PROFILE`. Live RBAC bootstrap
certification (`rbac-bootstrap-live`, module
`tests/release/checks/rbac_certification.py`) is opt-in behind
`ACM_ENABLE_LIVE_RBAC_CERTIFICATION=1` and is delivered with the example
profile `tests/release/profiles/full-release-with-rbac-cert.example.yaml`. See
[`docs/development/release-validation-framework.md`](docs/development/release-validation-framework.md)
and [`docs/deployment/rbac-live-certification.md`](docs/deployment/rbac-live-certification.md)
for the full contract.

Phase 7B Agent instructions for the non-live lab role controller live in
[`docs/development/lab-role-controller-agent-instructions.md`](docs/development/lab-role-controller-agent-instructions.md).
They are orchestration guidance only: the Python controller owns truth, safety,
and final decisions, and Phase 7A/7B does not authorize live execution.

#### Phase 9 Live Controller Authority

[`Phase 9A — RC Hardening Re-baseline and Gated Live Lab-Controller Design`](docs/plans/2026-07-17-phase-9a-rc-hardening-rebaseline-and-live-controller-design.md)
is the authoritative Phase 9 sequencing and live trust-boundary design. Phase
9B remains blocked until Phase 9A is merged and independently validated.

- The Python lab controller owns lab truth, physical identity, logical roles,
  profile binding, mutation authorization, recovery decisions, and GO/NO-GO.
  Agent/Codex provides orchestration and explanation only and must not issue ad
  hoc live mutations or override a controller decision.
- A known-state segment may contain at most one lab-mutating scenario. Fresh
  physical identity and logical-role proof is required before every mutation.
- Fake, dry-run, static-fixture, injected-fake, and local-harness evidence is
  not live certification evidence.
- Phases 9B-9F each require a separate issue and the builder, independent
  validator, and PR-comment-resolver/final-validator three-prompt workflow.
- The protected-file rules above remain fully applicable to every Phase 9
  slice.

### Pre-Push CI Guardrails

**Run the CI-equivalent checks locally before opening a PR and again before merging it.** The `Code Quality Checks` job fails the PR if formatting drifts, so reproduce these exact commands locally first. The simplest path is `./run_tests.sh` (strict mode on by default runs `black`, `isort`, `mypy`, and `bandit`). To run the individual gates exactly as CI does — using the same scope `acm_switchover.py lib modules ansible_collections/tomazb/acm_switchover/plugins ansible_collections/tomazb/acm_switchover/tests tests`:

```bash
# 1. Format check (this is the gate that has been failing PRs)
black --check --line-length 120 --diff acm_switchover.py lib modules ansible_collections/tomazb/acm_switchover/plugins ansible_collections/tomazb/acm_switchover/tests tests
isort --check-only --profile black --line-length 120 acm_switchover.py lib modules ansible_collections/tomazb/acm_switchover/plugins ansible_collections/tomazb/acm_switchover/tests tests

# To fix formatting (drop --check), then re-run the checks above:
black --line-length 120 acm_switchover.py lib modules ansible_collections/tomazb/acm_switchover/plugins ansible_collections/tomazb/acm_switchover/tests tests
isort --profile black --line-length 120 acm_switchover.py lib modules ansible_collections/tomazb/acm_switchover/plugins ansible_collections/tomazb/acm_switchover/tests tests

# 2. Lint
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics

# 3. Type check
mypy --explicit-package-bases acm_switchover.py lib/ modules/ ansible_collections/tomazb/acm_switchover/plugins ansible_collections/tomazb/acm_switchover/tests --ignore-missing-imports --no-strict-optional

# 4. Security scan
bandit --ini .bandit -f txt
```

A PR must be green on these locally before you open it, and must be re-verified (rebased on the target branch) before it is merged. Do not rely on the editor auto-format hook alone — it only runs on files you edit in-session, while CI checks the entire scope above.

Before pushing changes, also verify the same CI assumptions that have recently caused failures:

- **Root `tests/` jobs do not install `ansible-core`**: top-level tests under [`tests/`](tests/) may import collection helpers, but they must not hard-require `ansible.module_utils` at import time. If a parity test needs a collection module, keep the test import-safe without assuming the root Python job has Ansible installed.
- **CodeQL can flag URL-like strings in tests**: avoid putting raw host-like strings such as CRD names ending in `.argoproj.io` into assertion message text when a less URL-like description will do. Prefer messages like "applications CRD" or "argocds CRD" unless the exact literal is required by the assertion itself.
- **CodeQL can flag shared-helper logging**: do not log raw timeout/configuration/input values from reusable helpers unless the value is explicitly sanitized and operator-safe. Prefer stable public status text plus already-sanitized public detail fields.
- **Run formatting checks on tracked source trees, not the virtualenv**: scope `black`/`isort` to repository paths such as `acm_switchover.py`, `lib`, `modules`, `ansible_collections`, and `tests`. Do not run repo-wide formatting commands that can walk `.venv/` or other generated directories.
- **If CI formatting fails, fix the repo to match the pinned formatter**: the current workflows enforce `black` and `isort`, so run both locally before push when touching Python files. Match the CI scope exactly: `acm_switchover.py`, `lib`, `modules`, `ansible_collections/tomazb/acm_switchover/plugins`, `ansible_collections/tomazb/acm_switchover/tests`, and `tests`.

### Cross-Implementation Verification

For parity-sensitive changes, do not verify only one form factor.

- Run the relevant Python tests and the relevant collection tests for any `dual-supported` capability change.
- Run targeted parity/alignment tests when shared behavior, constants, or support boundaries change.
- For RBAC changes, verify both Python RBAC tests and collection RBAC tests, plus any affected parity/static-contract tests.
- If you intentionally change parity status or leave an approved divergence, verify that the docs/tests updated to reflect that decision.

### Test Quality Guidelines
- **DO NOT create meaningless or superficial tests** - tests should verify real logic and functionality
- Focus on testing actual business logic, error handling, and edge cases
- Avoid tests that only verify implementation details or trivial functionality
- Each test should provide meaningful value by catching real potential bugs

### Virtual Environment Usage
- Prefer activating an existing virtual environment before running tooling.
- The test runner (`run_tests.sh`) will detect an active `$VIRTUAL_ENV`, otherwise it will try `.venv/` first, then `venv/`, and create `.venv/` if none exist.
- Recommended setup:
    - Create `.venv` once: `python3 -m venv .venv`
    - Activate: `source .venv/bin/activate`
    - Then run: `./run_tests.sh`

## Common Tasks

**Adding a new constant**: If Python-only, add to `lib/constants.py`. If collection-only, add to `ansible_collections/tomazb/acm_switchover/plugins/module_utils/constants.py`. If shared across both form factors, update both and keep the parity tests green.
**Adding a KubeClient method**: Add `@retry_api_call` for API calls, return `Optional[Dict]` for gets, handle 404→None
**New workflow step**: Follow idempotent pattern with `state.is_step_completed()` / `mark_step_completed()`
**New validation**: Add to `lib/validation.py` with `InputValidator` static method
**Hub discovery and preflight**: Use `./scripts/discover-hub.sh --auto --run` to discover hub contexts and run smart preflight checks
**Argo CD pause/resume**: Use `python acm_switchover.py --argocd-manage` (Python CLI) or the `argocd_manage` Ansible role. See `docs/operations/usage.md` for Python flags (`--argocd-manage`, `--argocd-resume-only`, `--argocd-resume-on-failure`). The Bash script `scripts/argocd-manage.sh` is **deprecated** and will be removed in a future release. Note: `app.kubernetes.io/instance` is flagged as `UNRELIABLE` by GitOps marker detection and must not be used as a definitive GitOps signal.

### CLI Validation Guidance (Contributor note)
- CLI validation is implemented in `lib/validation.py` (class `InputValidator`). When changing existing arguments or adding new ones, update the validator accordingly and add tests in `tests/test_validation.py`.
- Current important cross-argument rules (enforced by `InputValidator.validate_all_cli_args`):
    - `--secondary-context` is required for switchover operations unless `--decommission` or `--setup` is set.
    - `--non-interactive` can only be used together with `--decommission` (it's disallowed for normal switchovers).
    - `--argocd-resume-only` requires `--secondary-context` and cannot be combined with `--validate-only`, `--decommission`, or `--setup`.
    - `--argocd-resume-on-failure` requires `--argocd-manage` and cannot be combined with `--argocd-resume-only` or `--validate-only`. Best-effort resume of paused ArgoCD Applications when a switchover fails.
    - `--setup` requires `--admin-kubeconfig` and validates `--token-duration` format (e.g., `48h`, `30m`, `3600s`).
    - `--restore-only` requires `--secondary-context`, forbids `--primary-context`, `--method passive`, `--old-hub-action`, and `--decommission`. Implies `--method full`.

## Graphify-assisted review

Use Graphify as a hypothesis generator for deep cross-file review.

Before reviewing large, safety-sensitive, or parity-sensitive changes, ask
Graphify targeted questions about:

- Python CLI vs Ansible collection parity
- checkpoint/resume and phase-transition behavior
- wrong-cluster, wrong-context, and wrong-namespace mutation risks
- destructive operations and fail-closed behavior
- RBAC scope and generated permissions
- Argo CD Application/ApplicationSet handling
- kubeconfig, token, secret, and report-path handling
- tests that claim to cover safety behavior but do not reach the relevant implementation

Important: Graphify relationships marked INFERRED or AMBIGUOUS are leads, not facts.
Verify every relationship against source code and tests before reporting it as a
review finding.

When using Codex review, convert Graphify output into concrete findings only when
the source code demonstrates a credible correctness, safety, idempotence, parity,
RBAC, check_mode, or destructive-operation risk.

## Thermos Review Resolution

The operator-supplied external Thermos Ansible review is a hypothesis source,
not an authority. The original report may exist locally as an untracked
`thermos_ansible_review.md`, but it is not required in a fresh checkout;
[`thermos-resolution-plan.md`](thermos-resolution-plan.md) is the
self-contained resolution source. Validate every finding against source code,
tests, docs, and Graphify leads before treating it as a repo issue.

Use [`thermos-resolution-plan.md`](thermos-resolution-plan.md) as the state
tracker for Thermos follow-up work:

- Update the tracker in every Thermos PR with the branch, status, verification,
  and PR URL state for that slice.
- Use one isolated `.claude/worktrees/thermos-*` worktree and one branch per PR.
- Start each PR from the latest merged `ansible` branch, unless the tracker
  explicitly records a stacked-branch dependency.
- Keep PRs scoped to the tracker row being resolved. Do not fold unrelated
  advisory refactors into safety fixes.
- Preserve the dual-supported parity contract. If a Thermos fix would
  intentionally diverge Python CLI and Ansible collection behavior, get explicit
  operator approval and record the approved divergence in the required parity
  docs before implementation.
- Keep protected runbook files and `.claude/skills/**/*.skill.md` read-only
  unless the operator explicitly approves those edits.

## Pull Request Creation Gate

Before creating any PR:

- Use the `code-review` skill against the completed branch changes.
- Address all critical and warning findings, or document a concrete technical
  reason when a finding is not applicable.
- Re-run the `code-review` skill after any review-driven changes before opening
  the PR.
- Keep local verification evidence ready for the PR body.

## Pull Request Merge Gate

Before merging any PR:

- Use the `code-review` skill again on the current PR head before merge.
- Address all critical and warning findings, or document a concrete technical
  reason when a finding is not applicable.
- Fetch and review every top-level PR comment, review, and review thread.
- Validate each actionable comment against the codebase before changing code.
- Address each accepted comment with code/docs/tests, or reply with a concrete
  technical reason when no change is appropriate.
- Resolve each review thread only after the relevant change or reply is pushed.
- Re-fetch PR comments and review threads after addressing feedback. Do not
  merge while any actionable thread remains unresolved.
- Check CI immediately before merge. Do not merge with failing, cancelled, or
  pending required checks.

## Code Review Guidelines

Prioritize correctness and operational safety over style comments.

Treat an issue as high priority when the diff introduces or preserves a credible risk of one of the following:

- Mutating the wrong cluster, hub, namespace, Kubernetes context, or managed resource.
- Running a destructive operation without explicit confirmation, safe dry-run behavior, or meaningful check-mode handling.
- Reporting `changed=true` from an Ansible module when no mutation occurred.
- Missing, misleading, or unsafe `check_mode` behavior.
- Granting broad RBAC permissions that are not justified by the operation.
- Checkpoint/resume logic that can skip unsafe phases, resume from an invalid state, or hide a partially failed switchover.
- Argo CD auto-sync pause/resume logic that can affect the wrong Application, generated child Application, or ApplicationSet-managed resource.
- Logging, exposing, or writing kubeconfigs, tokens, secrets, or credentials with unsafe permissions.
- Timeout, polling, or wait logic that can hang indefinitely or silently ignore failure.
- Behavior divergence from the existing Python CLI where parity is claimed.
- Missing negative tests for safety-critical behavior touched by the diff.

For Ansible code, specifically check:

- `argument_spec` correctness.
- Idempotence.
- Accurate `changed_when` and `failed_when`.
- Meaningful failure messages.
- Bounded retries and waits.
- Safe defaults.
- FQCN usage where appropriate.
- Stable module return values.
- Useful output for Ansible Automation Platform job logs.

For tests, prefer findings about missing safety coverage over superficial coverage comments. Important missing tests include wrong-context behavior, check-mode behavior, idempotence, RBAC denial, checkpoint/resume failure, stale Argo CD status, timeout failure, and destructive-operation confirmation.

Do not spend review budget on cosmetic formatting unless it affects behavior, operator comprehension, generated documentation, or maintainability of a safety-critical path.

## Code Review Checklist for Future Refactoring

When doing similar refactoring work:
- Compare line-by-line old vs new implementation
- Verify all conditional branches are preserved
- Check that error handling matches original behavior
- Validate critical vs non-critical failure classifications
- Ensure all imports are present in new modules
- Test with real-world scenarios, not just unit tests

## Files to Know

- `CHANGELOG.md` - Update `[Unreleased]` section for changes
- `docs/ansible-collection/parity-matrix.md` - Source of truth for parity/support status
- `docs/ansible-collection/behavior-map.md` - Python-to-collection behavior mapping
- `ansible_collections/tomazb/acm_switchover/docs/coexistence.md` - Coexistence and shared-behavior policy
- `docs/development/architecture.md` - Design decisions and module descriptions
- `lib/constants.py` - All magic strings centralized here (Python)
- `ansible_collections/tomazb/acm_switchover/plugins/module_utils/constants.py` - All collection-side constants centralized here
- `scripts/constants.sh` - All magic strings centralized here (Bash)
- `setup.cfg` - pytest, flake8, mypy configuration
- `.claude/skills/` - Claude SKILLS for switchover procedures (keep in sync with runbook)

## Version Management

Version management has two distinct modes: ordinary development work and explicit release/version-bump work.

### Ordinary Development Work

- Ordinary development PRs may modify code, tests, documentation, and tooling.
- Record changelog-worthy development changes under `CHANGELOG.md` `## [Unreleased]`.
- Do not change released version identifiers or create release tags unless the work is explicitly scoped as a release/version-bump PR.
- PATCH/MINOR/MAJOR guidance selects the next explicit release version from accumulated changes; it does not require every individual development PR to bump a version.

The governance correction for issue #165 is not a release. It must not change version identifiers or create a release tag.

### Explicit Release Work

- Python and Bash released versions must match whenever a version bump is performed.
- Update the container version, Helm chart `version` and `appVersion`, README version badge, changelog release heading, and changelog comparison links together with the Python and Bash versions.
- Run the release gates after all release metadata is synchronized.
- Create and push a matching `vX.Y.Z` tag for the exact release commit.
- A partial metadata bump, or a version bump without the matching release tag, is an incomplete release.

### Version Locations
|
| Component | File | Variables |
| --- | --- | --- |
| **Bash Scripts** | `scripts/constants.sh` | `SCRIPT_VERSION`, `SCRIPT_VERSION_DATE` |
| **Python Tool** | `lib/__init__.py` | `__version__`, `__version_date__` |
| **README** | `README.md` | Version badge at top of file |
| **Container Image** | `container-bootstrap/Containerfile` | `LABEL version` |
| **Helm Chart** | `deploy/helm/acm-switchover-rbac/Chart.yaml` | `version`, `appVersion` (appVersion = tool version) |

### Bash Scripts Version

Location: `scripts/constants.sh`

```bash
export SCRIPT_VERSION="X.Y.Z"
export SCRIPT_VERSION_DATE="YYYY-MM-DD"
```

**How to select the next explicit release version**:
- **PATCH (X.Y.Z → X.Y.Z+1)**: Bug fixes, minor improvements, documentation updates
- **MINOR (X.Y.Z → X.Y+1.0)**: New checks, new features, significant improvements
- **MAJOR (X.Y.Z → X+1.0.0)**: Breaking changes to script behavior or output format

**Files that use this version**:
- `scripts/preflight-check.sh` - displays via `print_script_version`
- `scripts/postflight-check.sh` - displays via `print_script_version`
- `scripts/discover-hub.sh` - displays via `print_script_version`

### Python Tool Version

Location: `lib/__init__.py`

```python
__version__ = "X.Y.Z"
__version_date__ = "YYYY-MM-DD"
```

**Release selection**: Use the same PATCH/MINOR/MAJOR rules as the Bash scripts when choosing the next explicit release version.

**Files that use this version**:
- `acm_switchover.py` - displays at startup: `ACM Hub Switchover Automation vX.Y.Z (YYYY-MM-DD)`
- `lib/utils.py` - stores `tool_version` in state files for troubleshooting
- `check_rbac.py` - can import and display version
- `show_state.py` - can import and display version

### Changelog Updates

Location: `CHANGELOG.md`

1. For new releases, create a new section: `## [X.Y.Z] - YYYY-MM-DD`
2. For ongoing work, add entries under `## [Unreleased]`
3. Group changes by: `### Added`, `### Changed`, `### Fixed`, `### Removed`
4. Keep the reference-link block at the bottom current: update `[Unreleased]` to compare from the new release tag to `HEAD`, add a link for the new release heading, and backfill any missing link references for headings added since the last release-link update.

### Version Update Checklist

For every explicit release/version bump (apply all steps regardless of whether the accumulated changes affect Bash scripts or Python):

1. [ ] Update `SCRIPT_VERSION` and `SCRIPT_VERSION_DATE` in `scripts/constants.sh`
2. [ ] Update `__version__` and `__version_date__` in `lib/__init__.py`
3. [ ] Keep Python and Bash versions in sync (they must always match)
4. [ ] Update container image label version in [container-bootstrap/Containerfile](container-bootstrap/Containerfile)
5. [ ] Update Helm chart `version` and `appVersion` (appVersion = tool version) in [deploy/helm/acm-switchover-rbac/Chart.yaml](deploy/helm/acm-switchover-rbac/Chart.yaml)
6. [ ] Update version badge in `README.md` (top of file)
7. [ ] Promote accumulated `[Unreleased]` entries into a new `## [X.Y.Z] - YYYY-MM-DD` release heading in `CHANGELOG.md`
8. [ ] Update the CHANGELOG reference-link block (`[Unreleased]` and the new `[X.Y.Z]` link, plus any missing recent release links)
9. [ ] Update `scripts/README.md` if new script features/checks were added
10. [ ] Run the release gates, then create and push a matching tag for the exact release commit (e.g., `git tag vX.Y.Z && git push origin vX.Y.Z`)

## Claude SKILLS

The `.claude/skills/` directory contains conversational guides for Claude to help operators through ACM switchover procedures. Each SKILL provides decision trees, commands, and troubleshooting paths.

> **Maintenance Rule**: When updating [docs/ACM_SWITCHOVER_RUNBOOK.md](docs/ACM_SWITCHOVER_RUNBOOK.md), also update the corresponding SKILLS in `.claude/skills/` to keep procedures synchronized.

### Operations SKILLS
|
| SKILL | Purpose | Runbook Reference |
| --- | --- | --- |
| [preflight-validation.skill.md](.claude/skills/operations/preflight-validation.skill.md) | Interactive pre-flight checklist with go/no-go decisions | Step 0 |
| [pause-backups.skill.md](.claude/skills/operations/pause-backups.skill.md) | Pause BackupSchedule (ACM 2.11 vs 2.12+ variants) | Step 1 |
| [activate-passive-restore.skill.md](.claude/skills/operations/activate-passive-restore.skill.md) | Method 1: Passive restore activation flow | Steps 2-5 |
| [activate-full-restore.skill.md](.claude/skills/operations/activate-full-restore.skill.md) | Method 2: One-time full restore flow | Steps F1-F5 |
| [verify-switchover.skill.md](.claude/skills/operations/verify-switchover.skill.md) | Post-activation verification (clusters, observability) | Steps 6-10 |
| [enable-backups.skill.md](.claude/skills/operations/enable-backups.skill.md) | Enable BackupSchedule on new hub | Steps 11-12 |
| [rollback.skill.md](.claude/skills/operations/rollback.skill.md) | Rollback procedure with decision tree by failure point | Rollback 1-5 |
| [decommission.skill.md](.claude/skills/operations/decommission.skill.md) | Safe decommissioning with safety checks | Step 14 |
| [restore-only.skill.md](.claude/skills/operations/restore-only.skill.md) | Single-hub restore from S3 backups (no primary) | `--restore-only` |

### Troubleshooting SKILLS
|
| SKILL | Symptoms | Resolution |
| --- | --- | --- |
| [pending-import.skill.md](.claude/skills/troubleshooting/pending-import.skill.md) | Clusters stuck in "Pending Import" | Klusterlet diagnostics, reimport |
| [grafana-no-data.skill.md](.claude/skills/troubleshooting/grafana-no-data.skill.md) | No metrics in Grafana dashboards | Observatorium restart, collector checks |
| [restore-stuck.skill.md](.claude/skills/troubleshooting/restore-stuck.skill.md) | Restore stuck in "Running" state | Velero diagnostics, storage checks |

### Automation SKILLS
|
| SKILL | Purpose | Invocation |
| --- | --- | --- |
| [release/SKILL.md](.claude/skills/release/SKILL.md) | Bump version across all 6 project version locations | `/release X.Y.Z` |

### Using SKILLS

SKILLS are designed for conversational guidance. When helping with switchover:
1. Start with `preflight-validation` to assess readiness
2. Follow the appropriate method (passive or full restore)
3. Use troubleshooting SKILLS when issues arise
4. Reference the runbook for detailed command explanations

### Claude Code Hooks

Auto-formatting and file protection hooks are configured in `.claude/settings.json`:

- **Auto-format**: After every `Edit`/`Write` on a `.py` file, `black` and `isort` run automatically
- **File protection**: Edits to `completions/`, `get-pip.py`, `*.lock`, `ACM_SWITCHOVER_RUNBOOK.md`, and `*.skill.md` files are blocked (see [Protected Critical Files](#protected-critical-files))

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, invoke the `skill` tool with `skill: "graphify"` before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- **Worktree fallback**: Only the primary worktree keeps graphify-out/ up to date. When working in a non-primary worktree (e.g., under `.worktrees/`), graphify-out/ is absent or stale — use the primary worktree's data instead. Find its path with `git worktree list | head -1 | awk '{print $1}'` and prefix graphify-out/ paths with that location (e.g., `graphify query` from there, or read `<primary>/graphify-out/GRAPH_REPORT.md`).
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues (`gh` CLI). See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context layout: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
