# Issue 28 Design: checkpoint/state safety for the Ansible collection

## Problem

Issue [#28](https://github.com/tomazb/rh-acm-switchover/issues/28) covers the collection-side checkpoint safety gaps called out in the parity backlog:

- checkpoint state is not bound to the current hub pair or operation identity
- `validate` and `dry_run` must remain non-mutating
- writes are not atomic
- corrupt JSON is not quarantined
- reset semantics only remove one phase instead of downstream phases
- legacy schema `1.0` checkpoints can be resumed unsafely

The goal is to make Ansible resume behavior match the Python CLI safety model without redesigning the playbook surface.

## Approved decisions

The design below reflects the approved choices from brainstorming:

- **Identity mismatch policy:** fail by default; only reset when the operator explicitly requests `reset` or `reset_from`
- **Kubeconfig identity format:** store literal kubeconfig path strings in the checkpoint identity

## Approaches considered

### 1. Centralize policy in checkpoint helpers and the action plugin (recommended)

Keep checkpoint schema, identity handling, reset pruning, and schema-safety checks in `plugins/module_utils/checkpoint.py`. Keep file loading, corrupt-file quarantine, and atomic writes in `plugins/action/checkpoint_phase.py`.

**Why this wins**

- one policy source for all phases
- minimal YAML churn
- easy unit-test coverage for the risky logic

### 2. Push identity/reset policy into playbooks and roles

Make each checkpointed phase compute identity and enforce reset rules through YAML variables and conditionals.

**Why not**

- duplicates safety logic across roles
- increases drift risk
- makes resume behavior harder to test thoroughly

### 3. Introduce a new dedicated checkpoint manager component

Add a separate manager module or plugin and reduce the action plugin to a wrapper.

**Why not**

- larger refactor than the issue needs
- more moving parts for a P0 safety fix

## Design

### 1. Schema and state model

Move the checkpoint schema to `2.0` and add an `operation_identity` object to persisted state. The identity should include:

- `primary_context`
- `secondary_context`
- `primary_kubeconfig`
- `secondary_kubeconfig`
- `method`
- `activation_method`
- `restore_only`
- `old_hub_action`
- `collection_version`

`plugins/module_utils/checkpoint.py` should own small pure helpers:

- `build_operation_identity(hubs, operation, collection_version=None) -> dict`
- `validate_operation_identity(checkpoint, expected_identity, *, allow_missing=False) -> None`
- `reset_completed_phases_from(completed_phases, phase) -> list[str]`
- any schema helpers needed to identify legacy `1.0` checkpoints and decide whether explicit reset is required

This keeps policy deterministic and unit-testable.

### 2. Resume and migration behavior

When the action plugin loads a checkpoint for a resume-capable phase:

1. Build the expected operation identity from `acm_switchover_hubs`, `acm_switchover_operation`, and the collection version.
2. If the checkpoint is schema `2.0`, validate the stored identity against the current invocation.
3. If the checkpoint identity mismatches, fail with a clear message and instruct the operator to use `reset` or `reset_from`.
4. If the checkpoint is schema `1.0` and `completed_phases` is non-empty, reject resume unless the operator explicitly requested reset.
5. If explicit `reset` or `reset_from` is requested for a legacy checkpoint, rebuild state as schema `2.0` and continue from the reset point.

This preserves the Python CLI safety principle: stale or ambiguous state must not be resumed silently.

### 3. Reset semantics

Add `reset_from` to the checkpoint config contract. It should prune the selected phase and all later phases from `completed_phases`.

Expected ordering:

1. `preflight`
2. `primary_prep`
3. `activation`
4. `post_activation`
5. `finalization`

Example:

- `reset_from: primary_prep` removes `primary_prep`, `activation`, `post_activation`, and `finalization`
- `preflight` remains intact

This behavior should replace single-phase reset assumptions, including the existing Argo CD resume-on-failure checkpoint reset in `playbooks/switchover.yml`.

### 4. Non-mutating modes

`execution.mode=validate` and `execution.mode=dry_run` must not create, modify, truncate, migrate, or reset checkpoint files.

The action plugin may still compute the effective checkpoint view returned to the playbook, but all persistence paths must remain disabled in both modes.

### 5. Corrupt checkpoint handling

Invalid JSON should not remain in place as if it were a usable checkpoint. On load failure due to JSON corruption:

1. rename the file aside with a deterministic suffix such as `.corrupt-<timestamp>`
2. fail with an explicit message that reports both the original path and quarantine path

This makes corruption visible while preserving operator evidence for debugging.

### 6. Atomic persistence

Checkpoint writes should go to a temporary file in the same directory, then move into place with `os.replace(...)`.

This guarantees that interrupted writes do not leave partial JSON at the final checkpoint path.

## Wiring impact

The implementation should stay localized:

- extend `plugins/module_utils/checkpoint.py`
- update `plugins/action/checkpoint_phase.py`
- document `reset_from` in example/default checkpoint config
- update playbook logic that currently resets only `primary_prep`

Role tasks should continue using the existing `checkpoint_phase` action rather than adopting a new calling pattern.

## Test plan

Add or extend unit tests in the collection test suite for:

### Checkpoint helper tests

- schema `2.0` record creation
- operation identity generation
- identity mismatch detection
- downstream reset pruning
- legacy `1.0` explicit-reset requirement

### Action plugin tests

- `validate` does not mutate checkpoint files
- `dry_run` does not mutate checkpoint files
- atomic-write path replaces the target only after the temp file is written
- corrupt JSON is quarantined and reported
- mismatched identity fails with an actionable error
- explicit reset migrates legacy `1.0` state safely
- `reset_from: primary_prep` removes all downstream completed phases

## Out of scope

This change should not redesign the overall checkpoint UX, add a new backend, or bundle unrelated parity work from later backlog items.
