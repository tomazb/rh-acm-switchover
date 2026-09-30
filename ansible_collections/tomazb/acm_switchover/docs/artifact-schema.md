# Artifact Schema

Python CLI `--report-dir` artifacts and collection `acm_switchover_execution.report_dir`
artifacts share schema version `"1.0"` and aligned status/report contracts, but
they are not identical across all report types. Preflight reports share the
strongest shape alignment around `status`, `summary`, `hubs`, and `results[]`.
Switchover, restore-only, and decommission reports may use different top-level
context fields to match each runtime's execution model. The collection uses
`source: tomazb.acm_switchover`; the Python CLI uses `source: python-cli`.

## Controller-Side Safe Path Policy

Python and collection artifact, checkpoint, and kubeconfig path validation share
the same controller-side policy:

- relative paths are allowed when they do not contain traversal or shell metacharacters
- absolute paths are allowed only when the resolved path is under `/tmp/`, `/var/`, the current working directory, or the controller user's home directory
- new absolute child paths are allowed when their nearest existing ancestor resolves under an allowed root
- `~`, `$`, `{`, `}`, `|`, `&`, `;`, `<`, `>`, and backticks are rejected; tilde expansion is not performed
- `..` is rejected as a path component

Use shell-expanded absolute paths or relative paths such as `./kubeconfigs/primary`
instead of `~/.kube/config`.

## Preflight Report Contract

- Path: `{{ acm_switchover_execution.report_dir }}/preflight-report.json`
- Written before the role fails on critical findings
- Path is validated with the collection safe-path policy before any controller-side file write
- `status=pass` means no critical findings failed
- Warning-only failures remain visible in `results` but do not fail the role
- The `hubs` object records hub context and live cluster UID only. It does not
  persist kubeconfig paths.
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

## Release Parity Guardrails

Release 1.7.10 validates artifact contract fields through the release runtime
parity helpers. The guardrails cover switchover reports, restore-only reports,
decommission reports, RBAC/bootstrap result artifacts, checkpoint records, and
safe-path validated report artifacts. These guardrails do not require identical
top-level report shapes across Python and Ansible, but they do require the
shared schema/status/path fields documented here to remain comparable.

## Checkpoint Contract

Path: controlled by `acm_switchover_execution.checkpoint.path`.

Written by the `tomazb.acm_switchover.checkpoint_phase` action plugin after each phase during live execution.
When `acm_switchover_execution.mode` is `validate` or `dry_run`, or when the play runs under native Ansible check mode
(`ansible-playbook --check`), the plugin reports the transition as non-mutating without creating, migrating, resetting,
copying, or mutating the checkpoint file.

```json
{
  "schema_version": "2.0",
  "phase": "activation",
  "completed_phases": ["preflight", "primary_prep", "activation"],
  "phase_status": "pass",
  "operation_identity": {
    "primary_context": "primary-hub",
    "secondary_context": "secondary-hub",
    "primary_cluster_uid": "d1f2b8a0-0000-4000-9000-111111111111",
    "secondary_cluster_uid": "e3a4c9b1-0000-4000-9000-222222222222",
    "method": "passive",
    "activation_method": "patch",
    "restore_only": false,
    "old_hub_action": "secondary",
    "collection_version": "1.7.10"
  },
  "operational_data": {
    "argocd_run_id": "9f2e4c13b8aa",
    "resume_summary": {
      "resume_start_phase": "activation"
    }
  },
  "errors": [],
  "report_refs": [
    {"phase": "preflight", "path": "/artifacts/preflight-report.json", "kind": "json-report"}
  ],
  "created_at": "2026-01-01T00:00:00+00:00",
  "updated_at": "2026-01-01T00:00:00+00:00"
}
```

Fields:

- `schema_version` — `"2.0"` for current collection checkpoints
- `phase` — last phase processed by the action plugin
- `completed_phases` — ordered list of phase names that have passed; used to skip phases on resume
- `phase_status` — last recorded phase outcome (`"pass"`, `"fail"`, or `"reset"`)
- `operation_identity` — hub and operation identity used to prevent reusing a checkpoint for a different switchover
- `operational_data` — runtime state carried across resumes (for example `argocd_run_id`, `resume_summary.resume_start_phase`, and backup verification baselines)
- `errors` — list of `{phase, error}` objects recorded on failure
- `report_refs` — list of `{phase, path, kind}` report artifact references (preflight only at present)
- `created_at` — ISO-8601 UTC timestamp of checkpoint creation
- `updated_at` — ISO-8601 UTC timestamp of last write

Enabling checkpoints requires `acm_switchover_execution.checkpoint.enabled: true` and
a writable `acm_switchover_execution.checkpoint.path`.

### Checkpoint Resume And Reset

Current schema `2.0` checkpoints are bound to the current operation identity:
primary and secondary contexts, live hub cluster UIDs, method, activation method,
restore-only mode, old-hub action, and collection version. If the stored identity
does not match the current invocation, the run fails before reusing the checkpoint.
Schema `2.0` checkpoints written by older collection builds may contain legacy
`primary_kubeconfig` and `secondary_kubeconfig` identity fields. These fields are
ignored for identity comparison and removed from the checkpoint on the next
execute-mode resume when the remaining context and cluster UID identity matches.

Use `acm_switchover_execution.checkpoint.reset: true` to start from a fresh checkpoint.
Use `acm_switchover_execution.checkpoint.reset_from` to remove the named phase and
all downstream phases from `completed_phases`. For example,
`checkpoint.reset_from: primary_prep` keeps `preflight` complete and reruns
`primary_prep`, `activation`, `post_activation`, and `finalization`.

A checkpoint file that is not valid JSON, or whose top level is not a JSON object,
is corrupt. An execute-mode run copies it to `<path>.corrupt-<UTC timestamp>`, leaves
the original in place, and fails; every later run stays blocked until the operator
repairs or removes the file or sets `checkpoint.reset: true`. If the copy fails, the
run still fails and the original is untouched. An unreadable file fails without a copy.

`operational_data.migration_backups` is reserved for the R4-04 migration journal,
which no role writes yet. Only `checkpoint_phase` `status: update` writes it: that
status needs checkpointing enabled and an existing checkpoint whose current phase is
the requested one, accepts `operational_data` only (no `error` or `report_ref`),
validates the complete journal and its transition from the stored one, and changes
only `operational_data` and `updated_at`. Other statuses refuse the key, and the
standalone decommission path refuses `status: update` outright. While a journal is
present, `reset_from: preflight` or `reset_from: primary_prep` is refused on an
`enter`/`reset` that would move the checkpoint back before the freeze: when the
named phase is still complete (so it would be pruned), or when the requested phase is
itself `preflight` or `primary_prep`. Otherwise that option is a no-op and allowed, so
a `reset_from` left in the config for the rest of a run does not block later phases,
but a rerun that starts at `preflight` with it still set is refused. A `reset_from` of
`activation` or later keeps the journal, and an invalid journal refuses every
`reset_from`. An explicit `status: reset` is checked on its own, with or without
`reset_from`: an invalid journal refuses every reset, and a valid one refuses a reset
of `preflight` or `primary_prep`. A `status: fail` of `preflight` or `primary_prep` is
refused while the journal key is present, valid or not, because a fail also
un-completes the phase; a fail of `activation` or any later phase is always recorded
and keeps the journal. For a schema `1.0` checkpoint that carries a journal, the
collection refuses the schema 1.0 rebuild that would discard operational data;
journal-preserving `reset_from` rebuilds remain allowed. Check mode previews every one
of these refusals. Only
`checkpoint.reset: true` starts a checkpoint without the journal.

During execute-mode resume, the action plugin records
`operational_data.resume_summary.resume_start_phase` the first time it enters a
phase that is not already complete. Release runtime parity uses that small
breadcrumb to compare where Python and collection executions resumed without
parsing controller logs.

The accepted `checkpoint.reset_from` values are:

- `preflight`
- `primary_prep`
- `activation`
- `post_activation`
- `finalization`

Legacy schema `1.0` checkpoints with any completed phases cannot prove operation
identity. The collection refuses to resume them unless the operator supplies
`checkpoint.reset: true` or `checkpoint.reset_from: <phase>` to select a safe
restart point.

## Compatibility Rule

If exact compatibility with Python artifacts is not feasible, a documented schema mapping or translation note is required before rollout.
