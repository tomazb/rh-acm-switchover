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

## Core Switchover Report Contract

- Path: `{{ acm_switchover_execution.report_dir }}/switchover-report.json`
- Written in an `always` block — present even when the play fails on post-activation
- `schema_version: "1.0"`, `source: tomazb.acm_switchover`

```json
{
  "schema_version": "1.0",
  "source": "tomazb.acm_switchover",
  "phases": {
    "primary_prep": {"phase": "primary_prep", "status": "pass|fail", "changed": true},
    "activation":   {"phase": "activation",   "status": "pass|fail", "changed": true},
    "post_activation": {
      "phase": "post_activation",
      "status": "pass|fail",
      "changed": false,
      "summary": {"passed": true, "total": 2, "pending": []}
    },
    "finalization": {
      "phase": "finalization",
      "status": "pass",
      "changed": true,
      "old_hub_action": "secondary|decommission|none"
    }
  }
}
```

Only phases that ran before any failure are included in `phases`.

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
