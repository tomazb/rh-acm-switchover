# Lab Role Controller Agent Instructions

These instructions are for Agents using the non-live lab role controller CLI added in Phase 7A.

## Purpose

The Agent may use `scripts/release/run_lab_role_controller.py` to run deterministic, non-live release-validation planning and artifact generation. The Python lab role controller owns truth, safety, known-state decisions, recovery classification, and final PASS/NO_GO/RECOVERY_REQUIRED/INFRA_RETRYABLE/BLOCKED decisions. The Agent owns only orchestration convenience and explanation.

This is not live ACM certification. It does not execute `oc`, `kubectl`, `ansible-playbook`, live release adapters, live discovery, automatic recovery, or production switchover readiness proof. Dry-run materialization, local fake harness results, and local release-framework harness evidence are not live ACM certification evidence.

## Pre-Flight Checks

Before invoking the CLI, the Agent should verify:

- Working tree status and selected branch when running in a repository checkout.
- `scripts/release/run_lab_role_controller.py` exists.
- The requested mode is one of the supported non-live modes: `fake`, `release-framework-dry-run`, or `release-framework-local`.
- No live mode was requested.
- Any artifact directory is caller-provided, safe, and outside `.release` by default.
- No real kubeconfig path, private lab config path, API endpoint, token, credential, or private cluster identifier was supplied.

## Allowed Commands

Use the Phase 7A CLI as the only supported command boundary. Prefer temporary artifact directories.

Fake mode with artifact output:

```bash
tmpdir="$(mktemp -d)"
python scripts/release/run_lab_role_controller.py --plan ping-pong --mode fake --artifact-dir "$tmpdir" --output-format summary
```

Release-framework dry-run materialization:

```bash
tmpdir="$(mktemp -d)"
python scripts/release/run_lab_role_controller.py --plan ping-pong --mode release-framework-dry-run --artifact-dir "$tmpdir" --output-format json
```

No-write summary:

```bash
python scripts/release/run_lab_role_controller.py --plan ping-pong --mode fake --no-write --output-format summary
```

Strict mode:

```bash
tmpdir="$(mktemp -d)"
python scripts/release/run_lab_role_controller.py --plan ping-pong --mode fake --artifact-dir "$tmpdir" --strict
```

`release-framework-local` is still non-live and requires `--allow-local-execution`; it uses the fake command-runner harness only. It is local harness evidence, not live ACM certification evidence.

Release-framework local fake harness, only after an explicit human request:

```bash
tmpdir="$(mktemp -d)"
python scripts/release/run_lab_role_controller.py --plan ping-pong --mode release-framework-local --allow-local-execution --artifact-dir "$tmpdir"
```

Forbidden examples that must fail closed:

```bash
python scripts/release/run_lab_role_controller.py --plan ping-pong --mode live --artifact-dir "$tmpdir"
python scripts/release/run_lab_role_controller.py --plan ping-pong --mode release-framework-live --artifact-dir "$tmpdir"
```

## Forbidden Actions

The Agent must not:

- Run `oc`, `kubectl`, or `ansible-playbook`.
- Call live release adapters directly.
- Run arbitrary subprocess commands against clusters.
- Read, infer, or print real kubeconfig paths.
- Write artifacts under `.release` by default.
- Commit generated artifacts.
- Create or modify real lab configs with private data.
- The Agent must not override controller final decisions.
- Claim `live_certification_evidence=true`.
- Treat dry-run, materialization, fake harness, or local harness evidence as live ACM certification evidence.
- Attempt automatic recovery after RECOVERY_REQUIRED.
- Continue after NO_GO, RECOVERY_REQUIRED, or BLOCKED unless a human explicitly starts a new non-live run.

## Decision Handling

The Agent must derive summaries from the controller artifact, not intuition.

- PASS: report success for the non-live controller run only. Continue to state that `live_certification_evidence=false` unless the artifact contract changes in a future phase.
- NO_GO: stop. Summarize `first_blocking_segment`, `first_blocking_scenario`, and `first_blocking_reason`. Do not rerun automatically unless the artifact says `retry_allowed=true` and a human explicitly requests a new non-live run.
- RECOVERY_REQUIRED: stop. State that manual recovery or human inspection is required. Do not attempt recovery.
- INFRA_RETRYABLE: report that a focused retry may be possible only for the failed segment when `retry_allowed=true`. Do not retry automatically unless a human explicitly instructs it and the artifact says `retry_allowed=true`.
- BLOCKED: stop. Treat this as a plan, config, model, or input issue.

## Artifact Interpretation

When `lab-controller-run.json` is written, read these fields before summarizing:

- `final_decision`
- `safe_to_continue`
- `retry_allowed`
- `manual_recovery_required`
- `first_blocking_segment`
- `first_blocking_scenario`
- `first_blocking_reason`
- `recovery_category`
- `operator_action_hint`
- `final_state_proven`
- `segment_decisions`
- `role_transition_graph`
- `summary_counts`
- `runtime_parity`
- `redaction_status`
- `real_execution_evidence`
- `live_certification_evidence`
- `materialized_release_framework`
- `execution_harness_summary`

Interpretation rules:

- `runtime_parity.status=not_implemented` is non-authoritative.
- `live_certification_evidence` must remain false in the current non-live phases.
- Local harness evidence is not live ACM certification evidence.
- Dry-run materialization is not execution evidence.
- `materialized_release_framework` describes planned release-framework requests; it does not prove execution.
- `execution_harness_summary` describes fake or local harness activity only unless a future controller phase explicitly changes the artifact contract.
- `redaction_status` must be redacted, pass, safe, or an equivalent passing status under the current artifact contract.
- `safe_to_continue` is non-live controller metadata. It is not proof of live-cluster safety and does not authorize the Agent to run live commands.

## Output Requirements

When summarizing a run, include:

- Command run.
- Artifact path, or `not_written`.
- Final decision.
- `safe_to_continue`.
- `retry_allowed`.
- `manual_recovery_required`.
- First blocker, if any.
- Whether artifacts were written.
- `real_execution_evidence`.
- `live_certification_evidence`.
- Caveat that Phase 7A/7B remains non-live.

The Agent must not include raw kubeconfig paths, raw API endpoints, tokens, credentials, private cluster identifiers, or unredacted profile metadata.

## Hard Stop Rules

The Agent must hard stop when:

- The CLI exits non-zero due to invalid usage, unsafe artifact directory, redaction failure, or strict non-PASS.
- `final_decision` is NO_GO.
- `final_decision` is RECOVERY_REQUIRED.
- `final_decision` is BLOCKED.
- `live_certification_evidence` is unexpectedly true in the current non-live phases.
- `redaction_status` is not redacted, pass, safe, or an equivalent passing status under the current artifact contract.
- The artifact cannot be parsed.
- The artifact is missing required top-level decision fields.

## Operating Boundary

Use deterministic CLI/controller entrypoints only. The Agent must not invent ad hoc live cluster commands, bypass the controller, reinterpret PASS/NO_GO/RECOVERY_REQUIRED/INFRA_RETRYABLE/BLOCKED decisions, or present dry-run/local fake evidence as live certification evidence.
