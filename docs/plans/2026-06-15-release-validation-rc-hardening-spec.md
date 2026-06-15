# Release Validation RC Hardening Spec

Date: 2026-06-15
Target branch: `ansible`
Primary executor: Codex / Superpowers skills
Status: implementation specification

## 1. Purpose

Make the release validation framework ready to be used as a proper, full-scale release-candidate gate for the Red Hat ACM switchover project.

The current framework is a useful profile-driven release-validation scaffold, but it must not be treated as the sole release-candidate certification path until lifecycle isolation, scenario parity, recovery, readiness depth, runtime parity, artifact handling, RBAC end-to-end validation, and manual RC workflow gaps are closed.

This document is the base specification for the detailed plan and implementation work. Superpowers skills should use it as the canonical requirement source and then produce a phased implementation plan before editing code.

## 2. Context

The project has two production form factors:

- Python CLI: `acm_switchover.py`
- Ansible collection: `ansible_collections/tomazb/acm_switchover/`

The `AGENTS.md` parity contract says dual-supported capabilities must remain aligned across the Python CLI and Ansible collection unless an intentional divergence is explicitly approved and documented. The release-validation framework lives under `tests/release/` and is pytest-native. It already provides profile loading, release-specific options, live discovery, static gates, stream adapters, runtime parity normalization, artifact generation, redaction auditing, and summary/report output.

The target state is not merely “more tests.” The target state is a release-candidate certification harness that can safely and repeatably prove whether a candidate is releasable.

## 3. Current-state findings that drive this work

### 3.1 What is already acceptable

The framework already has several useful foundations:

- Release tests are explicitly profile-gated through `--release-profile` / `ACM_RELEASE_PROFILE`.
- Unfiltered runs default to `certification`; filtered runs default to `focused-rerun`.
- Certification eligibility fails closed when fakes are injected, release metadata fails, or the checkout is dirty unless explicitly allowed.
- The orchestrator runs static gates, live discovery, lab readiness, baseline checks, stream adapters, runtime parity, final discovery, final baseline, and summary/report generation.
- The default adapters are live Bash, Python CLI, and Ansible adapters.
- Artifact creation has a required JSON artifact contract.
- Live RBAC certification exists as an opt-in SubjectAccessReview based flow.

### 3.2 Critical gaps

The framework is not yet release-candidate ready because:

1. Destructive scenarios are not isolated from each other. Multiple mutating scenarios can run sequentially against the same lab state without an implemented reset or recovery boundary.
2. Profile controls such as `cycles`, `cooldown_seconds`, `soak_duration_minutes`, and `max_tolerated_failures` are parsed/validated but not enforced by the orchestrator.
3. Recovery is represented in the data model and artifacts, but no actual recovery engine or hard-stop execution is implemented.
4. The scenario catalog advertises Ansible support for some optional/full-scale scenarios that the Ansible adapter cannot execute.
5. Some optional Python scenarios, especially `failure-injection` and `soak`, currently behave like ordinary switchover invocations rather than true failure/soak tests.
6. Lab readiness and baseline checks are too shallow for RC confidence.
7. Runtime parity is useful but too coarse and can overwrite capability records across scenarios.
8. Artifact redaction is not comprehensive for all JSON reports and cluster evidence files.
9. RBAC live certification validates already-applied service accounts but does not prove bootstrap apply-and-validate end-to-end inside the same disposable certification flow.
10. CI runs helper tests, not a live release-candidate workflow. A manual RC workflow is missing.

## 4. High-level target state

After this work, the release validation framework must support a full release-candidate decision with these properties:

- A real RC profile can run safely against a prepared lab without relying on undocumented manual resets between scenarios.
- Every selected scenario has an explicit lifecycle boundary: preconditions, mutation behavior, recovery/reset requirement, postconditions, and artifact contract.
- Scenario catalog, profile declarations, and adapter capabilities agree.
- Configured cycles, cooldown, soak duration, and tolerated failures are actually enforced.
- Recovery actions and hard stops are implemented and auditable.
- Lab readiness and final baseline cover the operational health needed to trust a destructive switchover result.
- Runtime parity compares scenario-specific outcomes, not only stream-level report shape.
- Artifact redaction/auditing applies to every emitted evidence file that may be shared as release evidence.
- RBAC bootstrap can be certified end-to-end in disposable or explicitly prepared labs.
- A manual GitHub Actions workflow can execute a live RC run with explicit inputs and upload artifacts.
- The final release summary is a defensible GO/NO-GO artifact.

## 5. Non-goals

Do not expand the scope beyond release validation hardening.

The following are out of scope unless required to close a directly blocking RC validation gap:

- Rewriting the Python CLI or Ansible collection architecture.
- Changing the operator-facing switchover behavior without a parity-approved reason.
- Modifying protected operational runbook or `.claude/skills/**/*.skill.md` files. Those require explicit operator approval under `AGENTS.md`.
- Adding production cluster cleanup behaviors that delete resources outside a profile-declared allowlist.
- Making ordinary CI run live destructive tests by default.
- Hiding failure behind retries; recovery must be explicit, bounded, and reported.

## 6. Safety and parity constraints

Implementation must respect these constraints:

1. Keep changes minimal and localized where possible.
2. Preserve Python/Ansible parity for dual-supported capabilities.
3. If a feature is intentionally not supported by one stream, update the scenario catalog and docs so the divergence is explicit.
4. Do not modify `docs/ACM_SWITCHOVER_RUNBOOK.md` or `.claude/skills/**/*.skill.md` without separate explicit operator approval.
5. Do not commit real kubeconfig paths, cluster names that should remain private, or credentials.
6. Fail closed for unsafe or inconclusive states.
7. Any destructive cleanup must be allowlist-driven by release profile configuration.
8. All release evidence artifacts must be safe to upload/share or must be rejected and block certification.

## 7. Required workstreams

### Workstream A: Scenario lifecycle isolation

#### Problem

The current orchestrator can run several mutating scenarios against the same lab state without an implemented reset, reverse transition, or verified recovery boundary. This makes full-scale profile runs ambiguous and unsafe.

#### Required behavior

Each scenario must have a lifecycle model with:

- `requires_initial_primary`: expected active hub before execution.
- `expected_final_primary`: expected active hub after execution.
- `mutates_lab`: whether it changes cluster state.
- `requires_reset_after`: whether the lab must be reset/recovered before the next mutating scenario.
- `allowed_followups`: optional scenario IDs allowed to run after this scenario without reset.
- `recovery_strategy`: one of `none`, `verify_only`, `reverse_switchover`, `restore_baseline`, `external_reset_required`, or a better project-aligned enum.
- `destructive_cleanup_allowed`: boolean derived from profile allowlist and scenario type.

Implement this in a way that keeps the catalog simple. Prefer adding a small metadata dataclass in `tests/release/scenarios/catalog.py` instead of scattering lifecycle checks through the orchestrator.

#### Acceptance criteria

- The orchestrator refuses to run an unsafe sequence in certification mode.
- Mutating scenario sequences must either be explicitly allowed or separated by a successful recovery/reset boundary.
- Full-release example profiles must declare `final_primary` explicitly where needed.
- Focused reruns can still run a single mutating scenario with prerequisites and final checks, but the report must say whether the run is certification-eligible.
- Tests cover unsafe sequence rejection and allowed sequence execution.

#### Candidate files

- `tests/release/scenarios/catalog.py`
- `tests/release/orchestrator.py`
- `tests/release/contracts/models.py`
- `tests/release/contracts/loader.py`
- `tests/release/contracts/schema.py`
- `tests/release/profiles/*.yaml`
- `tests/release/test_orchestrator.py`
- `docs/development/release-validation-framework.md`

### Workstream B: Align scenario catalog with stream adapter support

#### Problem

The scenario catalog currently lists optional scenarios such as `full-restore`, `checkpoint-resume`, `failure-injection`, and `soak` for both Python and Ansible, but the Ansible adapter only maps a subset of scenarios to playbooks.

#### Required behavior

Make scenario availability explicit and truthful.

For each scenario, decide whether it is:

- dual-supported now;
- Python-only for now;
- Ansible-only for now;
- release-local only;
- not implemented yet.

Then update the catalog, profiles, docs, and tests accordingly.

If the implementation goal is full dual support, add the missing Ansible adapter support and playbook/task wiring. If dual support is not feasible in this change, narrow the catalog/profile streams and document the intentional support boundary.

#### Acceptance criteria

- Selecting any advertised scenario/stream combination no longer fails because the adapter does not know the scenario.
- Tests assert catalog-to-adapter consistency.
- Profiles do not request unsupported scenario/stream pairs.
- Docs clearly list scenario support by stream.

#### Candidate files

- `tests/release/scenarios/catalog.py`
- `tests/release/adapters/ansible.py`
- `tests/release/adapters/python_cli.py`
- `tests/release/profiles/*.yaml`
- `docs/development/release-validation-framework.md`
- `docs/ansible-collection/parity-matrix.md` if support posture changes
- `docs/ansible-collection/scenario-catalog.md` if present/applicable

### Workstream C: Operationalize cycles, cooldowns, soak, and tolerated failures

#### Problem

Profile controls exist but the orchestrator currently executes each scenario once and does not enforce cycles, cooldowns, soak duration, or tolerated failure budgets.

#### Required behavior

Implement a scenario execution loop that supports:

- `ScenarioProfile.cycles`
- `LimitsProfile.max_cycles`
- `LimitsProfile.cooldown_seconds`
- `LimitsProfile.soak_duration_minutes`
- `LimitsProfile.max_tolerated_failures`
- per-scenario `timeout_minutes`

Cycle execution should emit separate artifacts per cycle, for example:

```text
scenarios/<scenario_id>/<stream>/cycle-001/...
scenarios/<scenario_id>/<stream>/cycle-002/...
```

or another deterministic structure that preserves old single-cycle paths where possible.

Soak must be real. It can be implemented as periodic observation/validation for the configured duration, not as a blind sleep. The soak report should include observation intervals, health checks, failures, and elapsed time.

Failure-injection must be real. It must define which failure is injected, how it is injected, what is expected to fail/recover, and how cleanup occurs. If failure-injection cannot be made safe yet, mark it unsupported for certification and document it.

#### Acceptance criteria

- Multi-cycle scenarios produce distinct results and artifacts per cycle.
- Cooldown is enforced and recorded.
- Soak duration is enforced through observable checks, not only sleep.
- Failure budget affects certification summary correctly.
- Tests cover cycle count, timeout propagation, cooldown recording, and failure budget behavior.

#### Candidate files

- `tests/release/orchestrator.py`
- `tests/release/adapters/common.py`
- `tests/release/reporting/artifacts.py`
- `tests/release/reporting/summary.py`
- `tests/release/scenarios/runtime_parity.py`
- `tests/release/contracts/models.py`
- `tests/release/test_orchestrator.py`

### Workstream D: Implement recovery and hard stops

#### Problem

Recovery configuration exists, but the orchestrator currently initializes `recovery.json` as not applicable and does not perform pre-run heal passes, post-failure recovery, hard-stop checks, or recovery budget tracking.

#### Required behavior

Implement a bounded recovery engine in the release framework.

Recovery must support:

- pre-run heal pass, if enabled;
- post-failure recovery pass for mutating scenarios, if enabled;
- total recovery budget tracking;
- hard-stop recording and enforcement;
- allowlist-driven destructive cleanup;
- a structured recovery artifact with actions, commands, statuses, elapsed time, evidence paths, and remaining open hard stops.

Hard stops from profile defaults must be meaningful:

- `hub_role_restore_unproven`
- `argocd_resume_unproven`
- `rbac_bootstrap_unproven`
- `final_baseline_unproven`

Recovery may initially use existing CLI/playbook flows or discovery checks; avoid inventing broad cluster mutation primitives unless necessary.

#### Acceptance criteria

- If a required mutating scenario fails, recovery is attempted only when configured and safe.
- Open hard stops block certification.
- Recovery budget exhaustion blocks certification.
- Recovery actions write evidence artifacts.
- Recovery does not run destructive cleanup outside the profile allowlist.
- Unit tests cover recovery success, recovery failure, hard-stop blocking, and budget exhaustion.

#### Candidate files

- `tests/release/orchestrator.py`
- `tests/release/contracts/models.py`
- `tests/release/contracts/schema.py`
- `tests/release/reporting/summary.py`
- `tests/release/reporting/render.py`
- `tests/release/baseline/discovery.py`
- `tests/release/baseline/assertions.py`
- `tests/release/checks/lab_readiness.py`
- `tests/release/test_orchestrator.py`

### Workstream E: Deepen live discovery, lab readiness, and baseline assertions

#### Problem

The current discovery and assertions are too shallow for release-candidate confidence. Several fingerprint fields are placeholders or hardcoded.

#### Required behavior

Enhance live discovery and checks to cover at least:

- ACM/MCH presence, version, and important status conditions.
- BackupSchedule presence, paused state, schedule name, and evidence of recent successful backups where available.
- BackupStorageLocation presence and health.
- OADP/Velero presence and health where applicable.
- Restore presence, phase/status, sync restore flag, and recency where available.
- ManagedCluster count/names plus availability/connection conditions.
- Observability presence and readiness where required.
- Argo CD presence, application counts, pause/resume state, and leftover pause markers.
- Required CRDs from profile.
- Cluster version/platform version if cheaply discoverable through `oc`.
- Stale switchover resources/checkpoints that should not be present before or after certification.

Keep checks profile-aware. Do not require optional capabilities when the profile marks them not required.

#### Acceptance criteria

- Fingerprints no longer contain hardcoded capability booleans or avoidable `unknown` placeholders for fields that can be discovered safely.
- Lab readiness fails for missing required CRDs, unhealthy backup storage, missing mandatory Argo CD fixture, or no active managed clusters.
- Final baseline fails for leftover Argo CD pause markers, wrong hub role, missing expected backup/restore evidence, or managed cluster drift.
- Tests cover both passing and failing discovery payloads.

#### Candidate files

- `tests/release/baseline/discovery.py`
- `tests/release/baseline/fingerprint.py`
- `tests/release/baseline/assertions.py`
- `tests/release/checks/lab_readiness.py`
- `tests/release/orchestrator.py`
- `tests/release/contracts/models.py`
- `tests/release/contracts/schema.py`
- `tests/release/profiles/*.yaml`

### Workstream F: Harden runtime parity

#### Problem

Runtime parity is capability-level and stream-level. In larger matrices, later scenario outputs can overwrite earlier normalized sources for the same capability and stream.

#### Required behavior

Make runtime parity scenario-aware.

The normalized source key should effectively be:

```text
(scenario_id, capability, stream, cycle_id?)
```

rather than only:

```text
(capability, stream)
```

Runtime parity should compare Python and Ansible results only when both streams are expected for the same scenario/cycle and both emitted valid artifacts.

Add stronger normalized fields where appropriate:

- operation status and phase status details;
- managed cluster counts/names before/after;
- Argo CD paused/resumed state and leftover marker count;
- checkpoint identity binding details;
- report source/tool/version if present;
- RBAC bootstrap applied assets and validation state;
- decommission dry-run/apply mode and target hub.

#### Acceptance criteria

- Runtime parity does not overwrite results across scenarios.
- Missing expected source reports fail required parity in certification mode.
- Optional unsupported parity reports are explicitly not applicable with reason.
- Report includes scenario/cycle-level parity failures.
- Tests cover multiple scenarios with the same capability and both pass/fail comparisons.

#### Candidate files

- `tests/release/scenarios/runtime_parity.py`
- `tests/release/orchestrator.py`
- `tests/release/reporting/render.py`
- `tests/release/reporting/summary.py`
- `tests/release/test_orchestrator.py`

### Workstream G: Comprehensive artifact redaction and evidence integrity

#### Problem

Captured stdout/stderr are sanitized, but JSON report files and other generated evidence may be written directly. The redaction patterns are also limited.

#### Required behavior

Every artifact included in release evidence must be one of:

- written through a sanitizer;
- scanned after write and recorded clean/redacted/rejected;
- explicitly marked non-shareable and excluded from uploaded RC artifacts.

Implement a release artifact audit pass near finalization that recursively scans allowlisted artifact paths and updates `redaction.json`.

Expand redaction/rejection coverage for common sensitive material:

- kubeconfig user auth fields;
- bearer tokens;
- service account tokens;
- PEM blocks;
- Kubernetes Secret `data` / `stringData`;
- AWS/GCP/Azure credential-like keys if they may appear in backup logs;
- private registry credentials;
- known OpenShift pull secret structures.

Do not over-redact harmless status fields into unusable artifacts. Prefer fail-closed rejection for high-risk secret classes.

#### Acceptance criteria

- `summary.json` blocks certification if any artifact is rejected or unscanned when scanning is required.
- JSON reports emitted by Python and Ansible are audited.
- Static gate artifacts remain sanitized.
- SAR evidence from RBAC certification is sanitized/audited.
- Tests cover redaction, rejection, recursive scanning, and summary blocking.

#### Candidate files

- `tests/release/reporting/artifacts.py`
- `tests/release/reporting/redaction.py`
- `tests/release/reporting/summary.py`
- `tests/release/orchestrator.py`
- `tests/release/checks/rbac_certification.py`
- `tests/release/test_artifacts.py` or new tests

### Workstream H: End-to-end RBAC bootstrap certification

#### Problem

Live RBAC certification validates service accounts that already exist, while `rbac-bootstrap` currently runs dry-run and does not validate applied permissions. This does not prove the release candidate can bootstrap RBAC and then use it.

#### Required behavior

Add an end-to-end RBAC certification mode that can run in a disposable or explicitly approved lab:

1. Generate/bootstrap RBAC assets for configured scopes.
2. Apply assets only when the profile explicitly permits live RBAC apply.
3. Validate required permissions via SAR.
4. Validate forbidden permissions are denied.
5. Optionally validate operator and validator roles separately.
6. Optionally include decommission and old-hub finalization permissions for operator role.
7. Clean up only when profile cleanup allowlist permits cleanup.
8. Emit complete RBAC certification artifacts.

Keep the current safe dry-run bootstrap path for ordinary non-destructive validation.

#### Acceptance criteria

- Required live RBAC certification cannot silently skip in certification mode.
- If live RBAC apply is not explicitly enabled, the framework fails or reports not certification-eligible rather than applying anything.
- SAR failures and forbidden permission grants block certification.
- Tests cover skip/fail/pass flows with fake SAR runners and profile flags.

#### Candidate files

- `tests/release/checks/rbac_certification.py`
- `tests/release/adapters/ansible.py`
- `ansible_collections/tomazb/acm_switchover/playbooks/rbac_bootstrap.yml`
- `ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/**`
- `tests/release/contracts/models.py`
- `tests/release/contracts/schema.py`
- `tests/release/profiles/full-release-with-rbac-cert.example.yaml`
- `docs/deployment/rbac-live-certification.md`

### Workstream I: Manual RC GitHub Actions workflow

#### Problem

CI currently validates helper tests and ordinary non-live checks, but there is no explicit manual RC workflow that runs live certification with operator-supplied inputs and uploads artifacts.

#### Required behavior

Add a manual workflow, for example `.github/workflows/release-candidate-validation.yml`, with `workflow_dispatch` inputs:

- release profile path;
- release mode, default `certification`;
- scenario filter(s), optional;
- stream filter(s), optional;
- artifact retention days;
- live RBAC certification enabled flag;
- allow dirty flag, default false;
- optional dry-run/debug toggle if appropriate.

The workflow must not include real kubeconfigs. It should assume the operator supplies credentials/secrets through GitHub environment secrets or self-hosted runner configuration. Keep secret handling explicit.

The workflow should:

1. Check out the requested branch/ref.
2. Install dependencies.
3. Run static/helper tests as preflight.
4. Run release certification with supplied profile.
5. Upload the entire sanitized artifact bundle.
6. Publish a concise step summary with GO/NO-GO, artifact path, and failure reasons.

#### Acceptance criteria

- Manual workflow exists and is documented.
- Ordinary push/PR CI still does not run live destructive tests.
- Workflow uploads artifacts even on failure.
- Workflow fails when summary status is failed.
- Tests or lint checks validate workflow syntax where feasible.

#### Candidate files

- `.github/workflows/release-candidate-validation.yml`
- `.github/workflows/ci-cd.yml` only if documentation/check list needs updating
- `docs/development/release-validation-framework.md`
- `docs/development/ci.md`

### Workstream J: Reporting and operator usability

#### Problem

The final report needs to support release-candidate review by an operator who did not watch the run live.

#### Required behavior

Enhance `release-report.md`, `summary.json`, and `manifest.json` so they include:

- branch/ref/commit and dirty state;
- profile path/hash and matrix hash;
- release metadata hash;
- certification eligibility and reasons;
- scenario/cycle/stream status table;
- required vs optional scenario summary;
- runtime parity summary by scenario/cycle/capability;
- lab readiness findings;
- initial/final baseline summary;
- recovery actions and hard stops;
- artifact redaction/audit result;
- live RBAC certification result;
- final GO/NO-GO decision with specific reasons.

#### Acceptance criteria

- Report is readable without opening raw JSON first.
- Every failed summary reason has a path to detailed evidence.
- Existing report rendering tests are expanded.
- JSON schemas remain stable or include version bumps/migration notes.

#### Candidate files

- `tests/release/reporting/render.py`
- `tests/release/reporting/summary.py`
- `tests/release/reporting/artifacts.py`
- `tests/release/test_release_certification.py`
- `tests/release/test_orchestrator.py`

## 8. Suggested implementation phases

Superpowers/Codex should convert this into a detailed plan with checkpoints. The suggested order is:

### Phase 1: Make the matrix truthful and safe

- Add scenario lifecycle metadata.
- Reject unsafe mutating sequences in certification mode.
- Align catalog/profile stream support with actual adapters.
- Add tests for catalog-adapter consistency.
- Update docs.

This phase should make the framework safer even before deeper features land.

### Phase 2: Make configured controls real

- Implement cycles and per-cycle artifact layout.
- Implement cooldown tracking.
- Implement failure budget summary behavior.
- Decide whether soak/failure-injection can be real now; otherwise mark unsupported for certification.

### Phase 3: Implement recovery and hard stops

- Add recovery action model and artifact structure.
- Implement pre-run and post-failure recovery where safe.
- Enforce recovery budget and hard stops.
- Add report/summary coverage.

### Phase 4: Deepen discovery and baseline

- Expand live discovery fields.
- Add profile-aware readiness checks.
- Add final baseline checks for leftovers and role/cluster drift.

### Phase 5: Harden runtime parity and artifacts

- Make parity scenario-aware.
- Expand normalized fields.
- Add recursive artifact audit.
- Block certification on unscanned/rejected artifacts.

### Phase 6: RBAC end-to-end and manual RC workflow

- Implement explicit live RBAC apply-and-validate mode.
- Add manual RC GitHub Actions workflow.
- Update docs and examples.

## 9. Data model expectations

Prefer additive schema changes and versioned artifacts.

Potential model additions:

```python
@dataclass(frozen=True)
class ScenarioLifecycleProfile:
    requires_initial_primary: str | None = None
    expected_final_primary: str | None = None
    requires_reset_after: bool = False
    recovery_strategy: str = "none"
    allowed_followups: tuple[str, ...] = ()
```

Potential runtime result additions:

```python
@dataclass(frozen=True)
class ScenarioExecutionKey:
    scenario_id: str
    stream: str
    cycle: int = 1
```

Potential recovery artifact shape:

```json
{
  "schema_version": 2,
  "status": "passed|failed|not_applicable",
  "budget_minutes": 30,
  "budget_consumed_seconds": 0,
  "pre_run": [],
  "post_failure": [],
  "hard_stops": [],
  "actions": [
    {
      "phase": "post_failure",
      "scenario_id": "ansible-passive-switchover",
      "action": "verify_argocd_resumed",
      "status": "passed",
      "started_at": "...",
      "ended_at": "...",
      "evidence_paths": []
    }
  ]
}
```

Potential runtime parity artifact shape:

```json
{
  "schema_version": 2,
  "status": "passed|failed|not_applicable",
  "comparisons": [
    {
      "scenario_id": "argocd-managed-switchover",
      "cycle": 1,
      "capability": "Argo CD management",
      "streams": ["python", "ansible"],
      "status": "passed",
      "required_fields": [],
      "differences": [],
      "evidence_paths": []
    }
  ]
}
```

## 10. Testing requirements

At minimum, add or update tests for:

- Profile schema validation for new lifecycle/recovery/RBAC fields.
- Matrix selection with scenario filters and stream filters.
- Catalog-adapter consistency.
- Unsafe mutating sequence rejection.
- Focused rerun behavior.
- Multi-cycle execution and artifact paths.
- Cooldown/soak/failure-budget behavior.
- Recovery success/failure/budget/hard-stop behavior.
- Deepened lab readiness pass/fail cases.
- Final baseline pass/fail cases.
- Scenario-aware runtime parity pass/fail/not-applicable cases.
- Artifact scanner pass/redact/reject/unscanned cases.
- RBAC live certification skip/fail/pass and forbidden-permission checks.
- Manual workflow YAML presence/syntax where feasible.

Test commands expected before final PR:

```bash
python -m pytest tests/release -q
python -m pytest tests/ --ignore=tests/release -m "not e2e" -q
black --check --line-length 120 tests/release ansible_collections/tomazb/acm_switchover/tests
isort --check-only --profile black --line-length 120 tests/release ansible_collections/tomazb/acm_switchover/tests
```

If changing Ansible collection behavior, also run the relevant collection tests and static gates:

```bash
env PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit -q
env PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration -q
env PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/scenario -q
```

## 11. Documentation requirements

Update documentation alongside implementation:

- `docs/development/release-validation-framework.md`
- `docs/development/ci.md` if adding manual RC workflow
- `docs/deployment/rbac-live-certification.md` if changing RBAC certification behavior
- `tests/release/profiles/*.example.yaml`
- `CHANGELOG.md` if the release process or operator-visible behavior changes
- Ansible parity docs if scenario support status changes

Do not edit protected runbook or `.claude/skills/**/*.skill.md` files unless separately approved.

## 12. Definition of done

The work is complete when all of these are true:

1. A full certification profile cannot run an unsafe destructive sequence.
2. Every profile-declared scenario/stream pair is executable or explicitly unsupported with a non-certification result.
3. Cycles, cooldowns, soak, and tolerated failures are implemented or explicitly blocked from certification profiles.
4. Recovery and hard stops are implemented, bounded, and visible in artifacts/reports.
5. Lab readiness and final baseline checks are strong enough to detect common RC-blocking lab problems.
6. Runtime parity is scenario-aware and cannot hide scenario-specific drift.
7. Artifact redaction/audit covers all release evidence.
8. RBAC bootstrap certification can be run end-to-end in an explicitly approved lab mode.
9. A manual RC GitHub Actions workflow exists and uploads artifacts.
10. Tests and docs are updated.
11. `python -m pytest tests/release -q` passes.
12. Existing non-release tests continue to pass.

## 13. Codex execution prompt

Use this prompt when starting the cloud Codex implementation task:

> Work on `tomazb/rh-acm-switchover` branch `ansible`. Implement the release validation RC hardening described in `docs/plans/2026-06-15-release-validation-rc-hardening-spec.md`. Start by producing a detailed implementation plan, then execute in small commits. Respect `AGENTS.md`, especially the Python/Ansible parity contract and protected-file rules. Do not modify `docs/ACM_SWITCHOVER_RUNBOOK.md` or `.claude/skills/**/*.skill.md`. Preserve ordinary CI safety: live destructive tests must remain manual/profile-gated. Add tests for each changed behavior and update release-validation docs/profiles. Open a draft PR against `ansible` with a clear checklist, test results, and any unsupported scenario decisions called out explicitly.

## 14. Monitoring/review protocol

When Codex opens a PR:

1. Review the PR summary against this spec.
2. Check changed files for protected-file violations.
3. Inspect scenario catalog, adapters, profiles, and tests for consistency.
4. Fetch the diff and review runtime behavior changes before approving.
5. Confirm tests are present for each implemented workstream.
6. Confirm any intentionally unsupported scenario has a documented support boundary.
7. Confirm release evidence artifacts remain sanitized/audited.
8. Request changes if the implementation weakens fail-closed certification behavior.

The first PR does not need to complete every workstream if it clearly implements a safe first phase and leaves the remaining work as explicit follow-up issues. However, the framework must not be declared RC-ready until every definition-of-done item is complete.
