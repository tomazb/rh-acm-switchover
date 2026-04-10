# Artifact Schema

## Preflight Report Contract

- Path: `{{ acm_switchover_execution.report_dir }}/preflight-report.json`
- Written before the role fails on critical findings
- `status=pass` means no critical findings failed
- Warning-only failures remain visible in `results` but do not fail the role
- Each result entry uses the stable schema:
  - `id`
  - `severity`
  - `status`
  - `message`
  - `details`
  - `recommended_action`

## Report Artifact

Required fields:

- `schema_version`
- `timestamp`
- `phase`
- `status`
- `results`

Each result entry must support:

- `id`
- `severity`
- `status`
- `message`
- `details`
- `recommended_action`

## Checkpoint Contract

Phase 1 defines only the contract:

- current phase
- completed high-risk checkpoints
- operational data needed for resume or reversal
- Argo CD pause metadata
- structured error history
- report artifact references
- lock ownership metadata

Runtime checkpoint implementation is deferred to a later plan.

## Compatibility Rule

If exact compatibility with Python artifacts is not feasible, a documented schema mapping or translation note is required before rollout.
