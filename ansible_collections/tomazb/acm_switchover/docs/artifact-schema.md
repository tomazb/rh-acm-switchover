# Artifact Schema

## Preflight Report Contract

- Path: `{{ acm_switchover_execution.report_dir }}/preflight-report.json`
- Written before the role fails on critical findings
- Path is validated with the collection safe-path policy before any controller-side file write
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
- Path is validated with the collection safe-path policy before any controller-side file write
- `schema_version: "1.0"`, `source: tomazb.acm_switchover`

```json
{
  "schema_version": "1.0",
  "source": "tomazb.acm_switchover",
  "argocd": {
    "run_id": "9f2e4c13b8aa",
    "summary": {"paused": 3, "restored": 0}
  },
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

The optional `argocd` object records pause metadata needed for later explicit
resume when Argo CD management is enabled. `run_id` matches the
`acm-switchover.argoproj.io/paused-by` marker written to Applications.

## Report Artifact

Required fields:

- `schema_version`
- `generated_at`
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

Implemented in Phase 4. Path: controlled by `acm_switchover_execution.checkpoint.path`.

Written by the `tomazb.acm_switchover.checkpoint_phase` action plugin after each phase during live execution.
When `acm_switchover_execution.mode` is `dry_run`, the plugin reports the simulated transition without writing the
checkpoint file or appending `completed_phases`.

```json
{
  "schema_version": "1.0",
  "completed_phases": ["preflight", "primary_prep", "activation"],
  "phase_status": "pass",
  "operational_data": {
    "argocd_run_id": "9f2e4c13b8aa"
  },
  "errors": [],
  "report_refs": [
    {"phase": "preflight", "path": "/artifacts/preflight-report.json", "kind": "json-report"}
  ],
  "locked_by": "ansible-run-2026-01-01T00:00:00",
  "updated_at": "2026-01-01T00:00:00+00:00"
}
```

Fields:

- `schema_version` — always `"1.0"`
- `completed_phases` — ordered list of phase names that have passed; used to skip phases on resume
- `phase_status` — last recorded phase outcome (`"pass"` or `"fail"`)
- `operational_data` — runtime state carried across resumes (for example `argocd_run_id` and backup verification baselines)
- `errors` — list of `{phase, error}` objects recorded on failure
- `report_refs` — list of `{phase, path, kind}` report artifact references (preflight only at present)
- `locked_by` — identifier of the active run holding the checkpoint lock; prevents concurrent switchover executions from corrupting state. Null or absent when no run is active
- `updated_at` — ISO-8601 UTC timestamp of last write

Enabling checkpoints requires `acm_switchover_execution.checkpoint.enabled: true` and
a writable `acm_switchover_execution.checkpoint.path`.

## Compatibility Rule

If exact compatibility with Python artifacts is not feasible, a documented schema mapping or translation note is required before rollout.
