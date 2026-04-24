# Release Validation Framework Design

Date: 2026-04-23
Status: Approved design for planning
Scope: Reusable release-certification framework for Bash, Python CLI, and Ansible collection validation on real ACM hub lab environments

## Summary

This design adds a dedicated `tests/release/` framework for release certification. The framework is separate from the existing unit, integration, scenario, and general E2E suites. It reuses current helpers where they fit, but owns release policy, profile-driven lab contracts, multi-stream execution, automatic baseline recovery, and release-grade reporting.

The framework must support:

- repeated switchovers on reusable two-hub labs
- certification across Bash, Python, and Ansible form factors
- mandatory Argo CD-managed validation
- profile-driven environment configuration without hardcoded context names in code
- automatic diagnosis and bounded recovery after failures
- pytest-native execution with explicit lifecycle orchestration inside the release harness

## Problem

The repository already has strong local test coverage and a Python-centered real-cluster E2E harness, but release confidence still depends on manual interpretation and ad hoc lab usage.

Current gaps:

- no single release-certification control plane for all three streams
- no formal repo-owned environment contract describing what a reusable lab must look like
- no unified way to certify ordinary and Argo CD-managed environments together
- no explicit baseline convergence model before and after mutating scenarios
- no normalized release output that answers whether parity-sensitive capabilities passed across streams
- no explicit distinction between existing static parity tests and required real-cluster runtime parity checks

## Goals

- create a reusable release-validation framework that can be rerun for future releases
- keep environment-specific details in profile files, not in runner code
- certify Bash, Python CLI, and Ansible collection entrypoints on the same real lab
- treat Argo CD support as a mandatory release gate
- support repeated switchover cycles with automatic baseline recovery
- emit unified artifacts and a release summary suitable for go/no-go decisions
- keep existing static parity tests as a separate fast gate while adding runtime parity certification on real clusters

## Non-Goals

- replace existing unit, integration, parity, or general E2E suites
- reconstruct arbitrary external Git repositories or full GitOps desired state not represented in the profile contract
- modify protected runbook or SKILL documents
- introduce intentional parity divergence between Python and Ansible for dual-supported capabilities

## Recommended Approach

Create a dedicated `tests/release/` layer that sits above the existing test suites.

Why this approach:

- it reuses current Python E2E helpers without overloading `tests/e2e/` with release-policy concerns
- it keeps release certification artifacts and semantics separate from ordinary development E2E runs
- it provides one place to define the lab contract, baseline recovery, stream adapters, and release reporting
- it scales better than separate real-cluster harnesses for Bash, Python, and Ansible

## Execution Model

The execution model is pytest-native.

- `tests/release/` is implemented as a pytest suite with release-specific fixtures, markers, and orchestration helpers.
- The term "release runner" refers to Python library code invoked by pytest tests, not a separate standalone lifecycle owner.
- If a shell convenience wrapper is added later, it must delegate to pytest rather than introduce a second execution model.

Why this choice:

- the repository already uses pytest as the dominant test entrypoint
- `tests/e2e/` already proves long-running real-cluster flows can be orchestrated inside pytest
- fixture reuse, markers, and existing CI/test ergonomics matter more than inventing a new standalone control plane

## Pytest Collection And Invocation Contract

All release-certification tests live under `tests/release/` and must be marked with `@pytest.mark.release`. The marker is mandatory for collection, filtering, and operator intent. Release tests may use additional markers, but `release` is the common gate.

`tests/release/conftest.py` owns the release-specific pytest options:

- `--release-profile PATH`: YAML profile path. If omitted, `ACM_RELEASE_PROFILE` is used when present.
- `--release-scenario ID`: repeatable scenario filter. When supplied, only matching scenario IDs run.
- `--release-stream STREAM`: repeatable stream filter. Allowed values are `bash`, `python`, and `ansible`.
- `--release-resume-from-artifacts DIR`: resume or focused rerun context from a previous release artifact directory.
- `--release-artifact-dir DIR`: optional artifact root. Default is profile `artifacts.root`, or `artifacts/release` when the profile does not override it.

Normal developer test runs must not execute release tests accidentally:

- `pytest tests/` skips all tests marked `release` unless `--release-profile` or `ACM_RELEASE_PROFILE` is set.
- The skip reason must state that release tests require an explicit release profile.
- Supplying `--release-profile` allows release tests to run when selected by path, marker, or normal pytest discovery.
- Supplying an unknown scenario, unknown stream, unreadable profile, invalid profile, or artifact resume directory without a compatible manifest is a pytest failure before any real-cluster mutation.

Full release-certification invocation:

```bash
pytest -m release tests/release --release-profile tests/release/profiles/<name>.yaml
```

Focused rerun examples:

```bash
pytest -m release tests/release --release-profile tests/release/profiles/<name>.yaml --release-scenario python-passive-switchover
pytest -m release tests/release --release-profile tests/release/profiles/<name>.yaml --release-stream ansible
pytest -m release tests/release --release-profile tests/release/profiles/<name>.yaml --release-resume-from-artifacts artifacts/release/<run_id>
```

The release harness may run static gates through pytest subprocesses, but it must not recursively invoke the active `tests/release/` run. Static gate commands must explicitly exclude the release marker unless they intentionally target release tests.

### Artifact Resume Compatibility

`--release-resume-from-artifacts DIR` may resume or focus a rerun only when `DIR/manifest.json` is compatible with the current invocation. Compatibility is checked before any real-cluster mutation.

Required compatibility checks:

- `manifest.json` exists and has `schema_version: 1`
- `profile.name` and `profile.sha256` match the currently selected profile
- selected stream and scenario filters are the same as, or a subset of, the previous run's `selected_streams` and `selected_scenarios`
- the artifact directory contains `scenario-results.json` unless the previous run failed before scenario execution
- the stable lab identity in `environment_fingerprint` matches the current profile's hub contexts, ACM namespaces, and managed-cluster expectation contract

If the previous run failed before `environment_fingerprint` was captured, the artifact directory is diagnostic context only. It is not eligible for mutation resume.

Git commit or dirty-state differences do not block a focused rerun because the normal workflow is to fix code and rerun against the same lab contract. They must be recorded as warnings in the new manifest and summary.

### CI Posture

Initial release validation is operator-invoked only. No default CI job runs `@pytest.mark.release` tests or mutates a real cluster. A future workflow-dispatch or nightly job may pass `--release-profile`, but that must be added explicitly with artifact retention and lab ownership documented at the same time.

## High-Level Architecture

```text
tests/release/
├── profiles/
│   └── *.yaml
├── contracts/
│   ├── schema.py
│   ├── loader.py
│   └── models.py
├── adapters/
│   ├── bash.py
│   ├── python_cli.py
│   ├── ansible.py
│   └── common.py
├── baseline/
│   ├── discovery.py
│   ├── converge.py
│   ├── recovery.py
│   └── safety.py
├── scenarios/
│   ├── catalog.py
│   └── assertions.py
├── reporting/
│   ├── artifacts.py
│   ├── summary.py
│   └── render.py
├── conftest.py
└── test_release_certification.py
```

Responsibilities:

- `profiles/`: sanitized profile examples and release-policy templates
- `contracts/`: schema validation and normalized internal models
- `adapters/`: stream-specific execution for Bash, Python CLI, and Ansible
- `baseline/`: discovery, convergence, recovery, and bounded healing rules
- `scenarios/`: release scenario catalog and cross-stream assertions
- `reporting/`: manifests, per-scenario artifacts, summaries, and human-readable output

Existing `tests/e2e/` modules remain reusable implementation details, especially for orchestration, helpers, monitoring, and cluster inspection.

Load-bearing existing helpers for the Python adapter are expected to include:

- `tests/e2e/orchestrator.py`
- `tests/e2e/phase_handlers.py`
- `tests/e2e/failure_injection.py`
- `tests/e2e/monitoring.py`
- `tests/e2e/full_validation_helpers.py`

## Profile Contract

Each release run must load an explicit profile file. Profiles are the source of truth for lab-specific values and expected release behavior.

Each profile defines:

- lab topology
  - logical primary hub and secondary hub
  - concrete kube contexts for the selected lab
  - expected managed-cluster inventory or count contract
- required streams
  - which Bash, Python, and Ansible streams are enabled and fail-closed for the profile
- scenario coverage
  - passive switchover
  - full restore
  - restore-only
  - checkpoint or resume flows
  - decommission
  - resilience and soak scenarios
- Argo CD contract
  - namespaces to inspect
  - app selection expectations
  - required pause and resume behavior
  - allowed role-conflicting resources the framework may clean automatically
- baseline hub state
  - which hub must begin as primary
  - expected backup and restore posture
  - observability expectations
  - RBAC/bootstrap prerequisites
- release limits
  - max cycles
  - soak duration
  - cooldown
  - max tolerated failures
  - artifact retention policy
- recovery policy
  - actions the framework may auto-heal
  - actions that must be treated as a hard stop
  - explicit RBAC recovery vocabulary
  - explicit Argo CD resume failure policy

Important boundary:

- profiles define the intended lab contract
- the framework restores the lab to that declared contract
- the framework does not pretend to recreate external GitOps repositories that are not represented in the contract

Profile storage and secret policy:

- `tests/release/profiles/` contains checked-in examples and sanitized templates only.
- Concrete lab profiles that include operator-specific kubeconfig paths, context names, or private lab topology live outside the repo by default.
- If the implementation supports local in-repo profiles, it must use a gitignored path such as `tests/release/profiles/local/`.
- Profile files must reference kubeconfig paths; they must not embed kubeconfig contents, tokens, certificates, or cluster credentials.
- Artifact manifests record the profile path and hash for reproducibility, but release reports must not copy credential material from referenced files.

## Profile Schema V1

Profiles are YAML mappings validated before any static gate or real-cluster scenario runs. Schema V1 rejects unknown top-level keys and malformed known fields so that typos fail fast. Validation errors include the profile path, dotted field path, invalid value, and expected type or enum.

Required top-level keys:

- `profile_version`
- `name`
- `hubs`
- `managed_clusters`
- `streams`
- `scenarios`
- `argocd`
- `baseline`
- `limits`
- `recovery`
- `artifacts`

Top-level fields:

| Field | Required | Type | Contract |
| --- | --- | --- | --- |
| `profile_version` | yes | integer | Must be `1` for this design. Other versions fail validation. |
| `name` | yes | string | Stable profile name used in artifacts. Must match `^[A-Za-z0-9_.-]+$`. |
| `hubs` | yes | mapping | Must contain `primary` and `secondary` hub entries. |
| `managed_clusters` | yes | mapping | Must define either `expected_names` or `expected_count`. |
| `streams` | yes | list | Enabled certification streams. Stream IDs must be `bash`, `python`, or `ansible`. |
| `scenarios` | yes | list | Scenario selections and requiredness overrides. Scenario IDs must exist in the V1 scenario matrix. |
| `argocd` | yes | mapping | Mandatory Argo CD certification contract for full release sign-off. |
| `baseline` | yes | mapping | Initial and final lab compliance contract. |
| `limits` | yes | mapping | Run, timeout, soak, cooldown, and failure-budget limits. |
| `recovery` | yes | mapping | Allowlisted recovery actions and hard-stop policy. |
| `artifacts` | yes | mapping | Artifact root, retention, and capture options. |

`hubs` schema:

- `primary` and `secondary` are required.
- Each hub requires `kubeconfig` and `context`.
- Optional `acm_namespace` defaults to `open-cluster-management`.
- Optional `role_label_selector` defaults to the framework's built-in ACM hub role discovery.
- Optional `timeout_minutes` defaults to `limits.default_timeout_minutes`.

`managed_clusters` schema:

- Exactly one of `expected_names` or `expected_count` is required.
- `expected_names` is a non-empty list of managed-cluster names.
- `expected_count` is an integer greater than or equal to `1`.
- Optional `contexts` defaults to an empty mapping and may map managed-cluster names to kube contexts for deeper checks.
- Optional `require_observability` defaults to `baseline.observability.required`.

`streams` schema:

- Each item requires `id`.
- `id` enum: `bash`, `python`, `ansible`.
- Optional `enabled` defaults to `true`.
- Optional `required` defaults to `true` for `python` and `ansible`, and defaults to `false` for `bash` in V1. Profiles may opt Bash into fail-closed release gating by setting `required: true`.
- Optional `env` is a mapping of additional environment variables for that stream.
- Optional `extra_args` is a list of argv tokens appended only to that stream's command.

`scenarios` schema:

- Each item requires `id`.
- Optional `required` defaults to the V1 scenario matrix value.
- Optional `streams` narrows the matrix stream coverage and must be a subset of enabled streams.
- Optional `cycles` defaults to `1` and must not exceed `limits.max_cycles`.
- Optional `timeout_minutes` defaults to `limits.default_timeout_minutes`.
- Optional `skip_reason` is allowed only when `required: false`.
- Full release profiles must include every required release-gate scenario from the V1 matrix. Omitting one is a validation failure.

`argocd` schema:

- `mandatory` is optional and defaults to `true`. Full release sign-off requires `true`.
- `namespaces` is required when `mandatory: true` and must be a non-empty list.
- Optional `application_selectors` defaults to an empty list, meaning discovery uses supported ACM-touching application detection.
- Optional `expected_pause` defaults to `true`.
- Optional `expected_resume` defaults to `true`.
- Optional `managed_conflict_allowlist` defaults to an empty list. Entries identify Argo CD-managed resources the recovery policy may clean when they conflict with hub-role restoration.

`baseline` schema:

- `initial_primary` is required and must be `primary` or `secondary`.
- Optional `final_primary` defaults to `initial_primary`.
- Optional `backup_schedule.required` defaults to `true`.
- Optional `restore.required` defaults to `true`.
- Optional `observability.required` defaults to `true`.
- Optional `rbac.required` defaults to `true`.
- Optional `static_gates.required` defaults to `true`.

`limits` schema:

- Optional `max_cycles` defaults to `1`.
- Optional `default_timeout_minutes` defaults to `120`.
- Optional `cooldown_seconds` defaults to `0`.
- Optional `soak_duration_minutes` defaults to `0`.
- Optional `max_tolerated_failures` defaults to `0`.
- Optional `artifact_retention_days` defaults to `30`.

`recovery` schema:

- Optional `pre_run_heal_passes` defaults to `1` and must be `0` or `1`.
- Optional `post_failure_passes_per_mutating_scenario` defaults to `1` and must be `0` or `1`.
- Optional `total_budget_minutes` defaults to `30`. Profiles may set a lower or higher value.
- Optional `allowed_destructive_cleanup.resources` defaults to an empty list and may contain only `BackupSchedule`, `Restore`, and `ArgoCDManagedConflict`.
- Optional `rbac_actions` defaults to `["validate_only", "revalidate"]`. Allowed values are `validate_only`, `bootstrap_hub_rbac`, `bootstrap_managed_cluster_rbac`, and `revalidate`.
- Optional `hard_stop_on` defaults to `["hub_role_restore_unproven", "argocd_resume_unproven", "rbac_bootstrap_unproven", "final_baseline_unproven"]`.

`artifacts` schema:

- Optional `root` defaults to `artifacts/release`.
- Optional `capture_stdout` defaults to `true`.
- Optional `capture_stderr` defaults to `true`.
- Optional `capture_cluster_snapshots` defaults to `false`. Long-running or soak profiles may opt in when snapshot cost is justified.
- Optional `compress_after_run` defaults to `false`.
- Optional `retention_days` defaults to `limits.artifact_retention_days`.

Minimal V1 profile example:

```yaml
profile_version: 1
name: lab-passive-release
hubs:
  primary:
    kubeconfig: /path/to/primary-kubeconfig
    context: lab-primary-context
  secondary:
    kubeconfig: /path/to/secondary-kubeconfig
    context: lab-secondary-context
managed_clusters:
  expected_count: 2
streams:
  - id: bash
  - id: python
  - id: ansible
scenarios:
  - id: static-gates
  - id: baseline-check
  - id: preflight
  - id: python-passive-switchover
  - id: ansible-passive-switchover
  - id: python-restore-only
  - id: ansible-restore-only
  - id: argocd-managed-switchover
  - id: runtime-parity
  - id: final-baseline-check
argocd:
  mandatory: true
  namespaces:
    - openshift-gitops
  application_selectors:
    - match_labels:
        app.kubernetes.io/part-of: acm-release-lab
baseline:
  initial_primary: primary
  backup_schedule:
    required: true
  restore:
    required: true
  observability:
    required: true
  rbac:
    required: true
limits:
  max_cycles: 1
  default_timeout_minutes: 120
  cooldown_seconds: 0
  soak_duration_minutes: 0
  max_tolerated_failures: 0
  artifact_retention_days: 30
recovery:
  pre_run_heal_passes: 1
  post_failure_passes_per_mutating_scenario: 1
  total_budget_minutes: 30
  allowed_destructive_cleanup:
    resources:
      - BackupSchedule
      - Restore
      - ArgoCDManagedConflict
artifacts:
  root: artifacts/release
  capture_stdout: true
  capture_stderr: true
  capture_cluster_snapshots: false
```

## Baseline Contract

The baseline is a full lab contract, not only a switchover role check.

Required baseline dimensions:

- correct primary and secondary hub roles
- healthy backup schedule and restore state
- expected managed-cluster presence on the active primary
- expected observability posture
- expected RBAC/bootstrap readiness
- mandatory Argo CD expectations for detection and pause/resume safety
- machine-readable assertions proving the lab is inside contract before certification starts

The baseline must be enforced:

- before the first certification scenario
- after any failed mutating scenario
- before soak loops continue
- at end of run, to prove the lab was left in a safe reusable state

Pre-run baseline failure policy:

- if discovery shows the lab is out of contract before any certification scenario runs, the framework performs one bounded pre-run heal pass using the same allowlisted recovery vocabulary
- if the lab is still out of contract after that pre-run heal pass, the release run hard-fails before any real-cluster certification scenarios start
- this path marks the lab as unsafe for certification and still emits diagnostics and environment fingerprint artifacts

## Scenario Model

Release certification is driven by a common scenario catalog rather than separate per-stream test lists.

Scenario families:

- preflight-only
- standard switchover
- restore-only
- checkpoint and resume recovery
- decommission
- Argo CD-managed switchover
- failure-injection and resilience
- multi-cycle soak

Each scenario declares:

- required baseline state
- applicable streams: `bash`, `python`, `ansible`
- whether parity assertions are required
- whether the scenario mutates the lab
- whether recovery is mandatory after execution
- expected artifacts and success signals

This allows one scenario to run Python and Ansible and compare report contracts, while another validates only Bash and Python for script-oriented operational surfaces.

## V1 Scenario Matrix

The V1 matrix is the release profile's scenario vocabulary. Required release-gate scenarios must be present in full release profiles. Optional scenarios may be enabled by profile and become release-failing only when the profile marks them `required: true`.

| Scenario ID | Gate | Stream coverage | Mutates lab | Recovery required | Runtime parity required | Success signal |
| --- | --- | --- | --- | --- | --- | --- |
| `static-gates` | required | local pytest and static checks | no | no | static parity only | Required unit, integration, scenario, and static parity commands exit `0`. |
| `baseline-check` | required | release harness | no | pre-run heal if needed | no | Baseline assertions pass or pass after one allowed pre-run heal pass. |
| `preflight` | required | `bash`, `python`, `ansible` | no | no | yes, for Python and Ansible dual-supported preflight behavior | All required streams report preflight success, non-required stream failures are warnings, and normalized Python/Ansible assertions match. |
| `python-passive-switchover` | required | `python` | yes | yes | yes, compared with Ansible passive outcome | `acm_switchover.py` passive flow exits `0`, reports completion, and cluster state matches expected active hub role. |
| `ansible-passive-switchover` | required | `ansible` | yes | yes | yes, compared with Python passive outcome | `playbooks/switchover.yml` exits `0`, reports completion, and cluster state matches expected active hub role. |
| `python-restore-only` | required | `python` | yes | yes | yes, compared with Ansible restore-only outcome | `acm_switchover.py --restore-only` exits `0`, restore-only warnings are expected, and managed clusters connect. |
| `ansible-restore-only` | required | `ansible` | yes | yes | yes, compared with Python restore-only outcome | `playbooks/restore_only.yml` exits `0`, restore-only warnings are expected, and managed clusters connect. |
| `argocd-managed-switchover` | required | `python`, `ansible`; `bash` preflight/postflight when enabled | yes | yes | yes, for dual-supported Argo CD behavior | Required ACM-touching applications pause before mutation, switchover succeeds, and applications resume to profile-declared state. |
| `runtime-parity` | required | normalized Python and Ansible results | no | no | yes | Every dual-supported capability comparison is `passed` or explicitly `not_applicable` with profile evidence. |
| `final-baseline-check` | required | release harness | no | no | no | Final baseline compliance is proven and the lab is reusable. |
| `full-restore` | optional | `python`, `ansible` | yes | yes | yes when both streams are enabled | Full restore flow completes and normalized restore/activation outcomes match. |
| `checkpoint-resume` | optional | `python`, `ansible` | yes | yes | yes when both streams are enabled | Interrupted scenario resumes with stream-specific checkpoint mechanics and reaches the same final lab state as an uninterrupted run. Checkpoint file equivalence is not required. |
| `decommission` | optional | `python`, `ansible`; `bash` only for release-relevant setup checks | yes | profile-defined | yes when dual-supported decommission is enabled | Decommission removes expected ACM resources without removing profile-protected resources. |
| `failure-injection` | optional | `python`, `ansible` | yes | yes | no unless profile requests structured comparison | Injected failure is classified correctly, diagnostics are emitted, and recovery returns the lab to baseline. |
| `soak` | optional | `python`, `ansible` | yes | yes | aggregate parity at end of cycles | Profile-declared cycle count or duration completes within failure budget and final baseline passes. |

## Stream Adapters

### Bash Adapter

The Bash adapter certifies real-lab operator shell surfaces, including discovery, preflight, postflight, and setup or RBAC-related script flows that remain release-relevant. Bash is primarily a validation and control-surface stream, not the primary switchover mutation engine.

### Python CLI Adapter

The Python adapter remains the richest mutating execution path. It covers ordinary switchovers, resume behavior, restore-only, resilience cases, repeated cycles, and recovery-oriented certification by reusing the strongest existing E2E helpers where appropriate.

### Ansible Adapter

The Ansible adapter certifies collection playbooks and role orchestration on the same lab contract. It must cover preflight, switchover, restore-only, decommission, checkpoint behavior, and Argo CD management entrypoints where applicable.

## Adapter Result Interfaces

Each stream adapter returns a `StreamResult`. The release harness persists these records in `scenario-results.json` and uses them as the input to runtime parity assertions.

```python
StreamResult = {
    "stream": "bash" | "python" | "ansible",
    "scenario_id": str,
    "status": "passed" | "failed" | "skipped" | "error",
    "command": list[str],
    "returncode": int | None,
    "stdout_path": str | None,
    "stderr_path": str | None,
    "reports": list[ReportArtifact],
    "assertions": list[AssertionRecord],
    "started_at": str,
    "ended_at": str,
}
```

`started_at` and `ended_at` are UTC ISO-8601 timestamps. A `failed` result means the command ran and returned a non-zero exit code or produced a failing release assertion. An `error` result means the adapter or harness failed before a trustworthy stream result could be produced.

`returncode` is an integer when a stream command was spawned. It is `None` only when the stream was skipped before command execution or the adapter failed before spawning the process.

`ReportArtifact` fields:

- `type`: stable report type such as `preflight`, `switchover`, `restore`, `argocd`, `rbac`, or `discovery`
- `path`: artifact path relative to the run artifact directory
- `schema_version`: optional report schema version when known
- `required`: boolean indicating whether the report is required for pass/fail aggregation

`AssertionRecord` fields:

- `capability`: parity-matrix capability name or scenario-local assertion category
- `name`: stable assertion name
- `status`: `passed`, `failed`, `skipped`, or `not_applicable`
- `expected`: normalized expected value or category
- `actual`: normalized observed value or category
- `evidence_path`: optional artifact path supporting the assertion
- `message`: concise human-readable result detail

Adapter invocation contracts:

- Python adapter invokes `acm_switchover.py` with profile-derived kubeconfig, context, mode, Argo CD, restore-only, and checkpoint flags.
- Ansible adapter invokes `ansible-playbook` against collection playbooks such as `playbooks/preflight.yml`, `playbooks/switchover.yml`, `playbooks/restore_only.yml`, `playbooks/decommission.yml`, `playbooks/rbac_bootstrap.yml`, `playbooks/discovery.yml`, and `playbooks/argocd_resume.yml` using profile-derived variables.
- Bash adapter invokes supported scripts such as `scripts/discover-hub.sh`, `scripts/preflight-check.sh`, `scripts/postflight-check.sh`, and RBAC or setup scripts only when those scripts remain release-relevant for the selected scenario.
- Adapters must treat profile values as input data and must not hardcode lab context names, namespaces, managed-cluster names, or artifact locations.
- Adapters must write stdout and stderr to files before returning, even on failure, unless the process could not be spawned.

### Cross-Stream Assertions

Static parity tests already exist and remain separate fast gates:

- `tests/test_constants_parity.py`
- `tests/test_rbac_collection_parity.py`
- `tests/test_argocd_constants_parity.py`

Those tests cover static contract parity. The release framework adds runtime behavioral parity on real clusters.

Runtime parity assertions apply only to capabilities documented as `dual-supported` in `docs/ansible-collection/parity-matrix.md`. Bash is part of release certification, but Bash is not treated as a strict parity peer for the Python/Ansible coexistence contract unless a scenario explicitly defines a structured comparison.

Parity-sensitive runtime areas are:

- preflight behavior
- activation and finalization outcomes
- RBAC validation and bootstrap outcomes
- Argo CD detection and pause/resume behavior
- discovery behavior
- machine-readable report contracts
- decommission semantics where dual-supported

Comparison mechanics:

- Python and Ansible adapters produce normalized assertion records for each dual-supported capability
- normalized assertion records are built from shared machine-readable reports where available, and from post-run cluster-state checks where a shared report field is not sufficient
- comparisons use stable fields only:
  - capability status
  - decision flags
  - normalized role outcomes
  - normalized resource names or sets
  - pause/resume state
  - validation result categories and counts
- comparisons explicitly exclude unstable fields such as timestamps, durations, file paths, temporary artifact locations, and raw stderr text
- default comparison mode is strict equality on normalized required fields
- subset checks are allowed only where one stream intentionally emits additive metadata outside the shared contract
- Bash validation uses scenario-level contract checks against cluster outcomes and script-specific expected output; it is not required to emit the same report schema as Python and Ansible in the initial design

### Runtime Parity Normalization Contract V1

Runtime parity is a contract owned by the release harness, not an ad hoc comparison of whatever each stream happens to emit. Every dual-supported capability comparison must declare the normalized fields, source evidence, and comparison mode before implementation.

V1 normalized comparison table:

| Capability | Normalized fields | Python source | Ansible source | Comparison mode |
| --- | --- | --- | --- | --- |
| preflight validation | `status`, `critical_failure_count`, `warning_failure_count`, `check_ids`, `failed_check_ids` | `ValidationReporter.results`, validate-only output, and adapter post-processing | `preflight-report.json.summary` and `preflight-report.json.results` | Strict equality after mapping Python `critical: true/false` to collection `severity`. |
| primary prep | `backup_schedule_paused`, `auto_import_disabled_clusters`, `thanos_scaled_down`, `skipped_observability` | Python state plus post-run cluster-state discovery | Role result facts plus post-run cluster-state discovery | Strict equality on cluster-state outcomes. |
| activation | `restore_name`, `restore_phase_category`, `sync_restore_enabled`, `managed_cluster_activation_requested` | Python report/state where available plus secondary hub cluster-state discovery | Role facts/checkpoint data plus secondary hub cluster-state discovery | Strict equality on normalized restore and activation outcomes. |
| post-activation verification | `connected_managed_clusters`, `unavailable_managed_clusters`, `klusterlet_remediation_count`, `observability_status` | Python post-activation output plus active hub cluster-state discovery | Role facts plus active hub cluster-state discovery | Strict equality on names and counts, with sorted sets for resource names. |
| finalization | `backup_schedule_present`, `backup_schedule_paused`, `post_enable_backup_observed`, `old_hub_action_result` | Python finalization state/output plus cluster-state discovery | Role facts/checkpoint data plus cluster-state discovery | Strict equality, except optional old-hub actions may be `not_applicable` when profile disables them. |
| RBAC self-validation | `scope`, `subject`, `missing_permissions`, `validation_status` | `RBACValidator` result expansion | `acm_rbac_validate` result expansion | Strict equality on missing permission sets for matching scopes. |
| RBAC bootstrap | `scope`, `created_or_verified_subjects`, `applied_manifest_set`, `bootstrap_status` | Python/bootstrap script or CLI output when selected | `acm_rbac_bootstrap` and `rbac_bootstrap` role results | Strict equality on required subjects and manifest identities. |
| Argo CD management | `selected_applications`, `paused_applications`, `resumed_applications`, `resume_failures`, `conflict_allowlist_used` | Python Argo CD state and post-run Application discovery | `argocd_manage` role facts and post-run Application discovery | Strict equality on selected and final Application state sets. |
| discovery | `hub_roles`, `acm_versions`, `managed_cluster_names_or_count`, `observability_present`, `argocd_present`, `capability_flags` | Python discovery/preflight output plus cluster-state discovery | `acm_discovery` result and role discovery facts | Strict equality on profile-required fields; additive discovered metadata is ignored unless promoted to required. |
| machine-readable reports | `required_artifacts_present`, `schema_versions`, `top_level_status`, `required_assertion_count` | Python artifact paths and parsed JSON | Collection artifact paths and parsed JSON | Schema-contract comparison, not field-for-field report equality. |
| decommission | `removed_resources`, `preserved_resources`, `old_hub_mch_absent`, `managed_clusters_removed` | Python decommission output plus old hub cluster-state discovery | `decommission` role facts plus old hub cluster-state discovery | Strict equality on profile-required removal and preservation outcomes. |

Normalization rules:

- A comparison may return `not_applicable` only when the profile disables the capability or the parity matrix no longer marks it `dual-supported`.
- Missing source evidence for a required normalized field is a failed comparison, not a skipped comparison.
- Resource name sets are sorted before comparison.
- Counts may be compared instead of names only when the profile uses `managed_clusters.expected_count`.
- Normalizers must emit a concise `AssertionRecord.message` explaining every failed or `not_applicable` comparison.
- Adding a new normalized field for a dual-supported capability requires updating this table or its implemented schema equivalent in the same change.

## Execution Flow

Each release run follows this flow:

1. Load and validate the selected profile.
2. Run fast static gates first:
   - relevant unit tests
   - integration tests
   - scenario tests
   - existing static parity tests
3. Discover current lab state across both hubs and managed clusters and capture environment fingerprint data.
4. Verify the baseline contract.
5. If baseline verification fails, run one bounded pre-run heal pass.
6. If the lab is still out of contract, hard-fail before real-cluster certification.
7. Run the real-cluster certification matrix across Bash, Python, and Ansible according to the selected profile.
8. Execute runtime cross-stream assertions for dual-supported capabilities.
9. On scenario failure, capture diagnostics, classify the failure, run bounded recovery, and continue or stop according to policy.
10. Run repeated switchover or soak cycles as declared by the profile.
11. Emit final release summary and confirm whether the lab ended in baseline-compliant state.

Static-gate failure short-circuits the run before discovery, baseline recovery, or any real-cluster mutation. Recovery is for lab-state drift, not source-code or local test failures.

This creates a certification pipeline instead of a single monolithic command with implicit operator judgment.

## Recovery, Diagnostics, And Safety

Automatic recovery is required, but it must be bounded and explicit.

On every mutating scenario:

- capture pre-run baseline facts
- capture post-run or post-failure facts
- capture full cluster snapshots only when `artifacts.capture_cluster_snapshots` is enabled
- store stream output, report artifacts, and cluster facts

Failure classification categories:

- code or test failure
- lab drift or stale resources
- backup or restore health issue
- Argo CD conflict or incomplete resume
- RBAC/bootstrap issue
- unrecoverable environmental fault

Recovery vocabulary must be profile-declared rather than ad hoc.

Allowed bounded recovery actions include:

- cleaning stale role-conflicting `BackupSchedule` or `Restore` resources allowed by profile policy
- restoring expected primary and secondary hub roles
- waiting for passive restore to settle
- restoring required Argo CD pause/resume expectations
- revalidating managed-cluster presence and observability posture
- rerunning discovery and preflight checks to prove the lab is back inside contract

RBAC recovery actions are limited to this vocabulary:

- `validate_only`
- `bootstrap_hub_rbac`
- `bootstrap_managed_cluster_rbac`
- `revalidate`

Each RBAC recovery action must also declare scope: `primary`, `secondary`, `both`, or an explicit managed-cluster target set. Recovery may invoke only repo-supported bootstrap assets, scripts, or playbooks. It must never patch arbitrary RBAC rules directly.

Argo CD recovery policy must declare a hard-stop condition. At minimum, the run hard-stops when:

- required ACM-touching applications cannot be returned to the profile-declared paused or resumed state within the recovery budget
- required sync-policy shape or `paused-by` annotations remain inconsistent after one cleanup pass and one bounded retry window
- role-conflicting resources continue to be recreated by Argo CD after the allowlisted cleanup steps complete

Safety constraints:

- each recovery step has retry limits
- recovery has a total time budget
- if the lab cannot return to baseline, the run hard-fails
- deletions and other healing actions are allowlisted by profile
- the framework must not improvise destructive cleanup beyond declared resource classes

## Recovery Policy Defaults

Unless a profile overrides them, release recovery uses these defaults:

- exactly one pre-run heal pass before certification scenarios begin
- exactly one post-failure recovery pass per mutating scenario
- total recovery budget of 30 minutes for the run
- no destructive cleanup unless the profile allowlists the resource class and scenario context
- destructive cleanup limited to stale or conflicting `BackupSchedule`, `Restore`, and Argo CD-managed conflicting resources represented by the profile contract
- RBAC recovery limited to `validate_only`, `bootstrap_hub_rbac`, `bootstrap_managed_cluster_rbac`, and `revalidate`

Profiles may lower or raise the total recovery budget, but they cannot authorize cleanup outside the schema enum. A profile may disable pre-run or post-failure recovery by setting the corresponding pass count to `0`; doing so makes baseline or scenario failures hard failures without healing.

The run hard-stops when any of these cannot be proven inside the recovery budget:

- expected hub role restoration
- Argo CD application resume or declared paused state
- RBAC validation or bootstrap readiness required by the profile
- final baseline compliance

Recovery attempts are reported as first-class release evidence. A successful recovery can allow later scenarios to continue, but it does not erase the original scenario failure from artifacts or summary accounting.

## Artifacts And Reporting

The release framework emits:

- run manifest with git SHA, tool version, selected profile, and environment fingerprint
  - ACM version on both hubs
  - OCP/Kubernetes version on both hubs
  - managed-cluster platform or version facts captured by the profile contract
  - key discovered capability flags such as observability and Argo CD presence
- per-scenario artifacts
  - stdout and stderr
  - machine-readable reports
  - snapshots when enabled
  - recovery actions
  - timing
- per-cycle artifacts for repeated switchover and soak runs
- normalized summary file showing:
  - stream pass/fail
  - parity-sensitive capability results
  - mandatory Argo CD certification result
  - recovery count and recovery failures
  - final baseline compliance
- human-readable release report for operator go/no-go review

## Reporting Schema V1

Every release run writes one artifact directory containing these required files:

- `manifest.json`
- `scenario-results.json`
- `runtime-parity.json`
- `recovery.json`
- `summary.json`
- `release-report.md`

Common JSON requirements:

- `schema_version` is required and must be `1`.
- Timestamps are UTC ISO-8601 strings.
- Paths are relative to the run artifact directory unless explicitly marked as absolute source paths.
- Files must remain machine-readable even when the run fails before mutation starts.

`manifest.json` required fields:

- `schema_version`
- `run_id`
- `started_at`
- `completed_at`
- `status`
- `command`
- `profile.name`
- `profile.path`
- `profile.sha256`
- `git.commit`
- `git.branch`
- `git.dirty`
- `tool_version`
- `selected_scenarios`
- `selected_streams`
- `environment_fingerprint`

`environment_fingerprint` required fields:

- `generated_at`
- `hubs.primary.context`
- `hubs.primary.acm_namespace`
- `hubs.primary.acm_version`
- `hubs.primary.platform_version`
- `hubs.primary.kubernetes_version`
- `hubs.primary.hub_role`
- `hubs.primary.backup_schedule.present`
- `hubs.primary.backup_schedule.name`
- `hubs.primary.backup_schedule.paused`
- `hubs.primary.restore.present`
- `hubs.primary.restore.name`
- `hubs.primary.restore.phase`
- `hubs.primary.restore.sync_restore_enabled`
- `hubs.primary.observability.present`
- `hubs.primary.observability.status`
- `hubs.primary.argocd.present`
- `hubs.primary.argocd.namespaces`
- `hubs.primary.argocd.application_count`
- the same `hubs.secondary.*` fields for the secondary hub
- `managed_clusters.expectation_type`: `names` or `count`
- `managed_clusters.expected_names`: list, empty when the profile uses count-only matching
- `managed_clusters.expected_count`: integer or `null`
- `managed_clusters.observed_active_names`: sorted list when names are required, otherwise an empty list
- `managed_clusters.observed_active_count`
- `managed_clusters.contexts_available`
- `capabilities.observability`
- `capabilities.argocd`
- `capabilities.rbac_validation`
- `capabilities.rbac_bootstrap`
- `capabilities.decommission`

`environment_fingerprint` may include additive discovered metadata, but the fields above are the stable compatibility contract for artifact resume and release comparison.

`scenario-results.json` required fields:

- `schema_version`
- `results`: list of `StreamResult`
- `scenario_statuses`: list of scenario-level summaries with `scenario_id`, `required`, `status`, `streams`, `started_at`, `ended_at`, and `artifact_paths`

`runtime-parity.json` required fields:

- `schema_version`
- `comparisons`: list of comparison records with `capability`, `scenario_id`, `streams`, `status`, `required_fields`, `differences`, and `evidence_paths`
- `status`: `passed`, `failed`, or `not_applicable`

`recovery.json` required fields:

- `schema_version`
- `budget_minutes`
- `budget_consumed_seconds`
- `pre_run`: list of recovery attempts
- `post_failure`: list of recovery attempts keyed by `scenario_id`
- `hard_stops`: list of hard-stop records
- `status`

Each recovery attempt records `action`, `scope`, `started_at`, `ended_at`, `status`, `allowed_by_profile`, and `evidence_paths`.

`summary.json` required fields:

- `schema_version`
- `status`: `passed` or `failed`
- `required_scenarios`
- `optional_scenarios`
- `mandatory_argocd`
- `runtime_parity`
- `final_baseline`
- `recovery`
- `warnings`
- `failure_reasons`

`release-report.md` required sections:

- run identity and profile
- environment fingerprint
- required scenario results
- optional scenario results
- mandatory Argo CD certification
- runtime parity summary
- recovery summary
- final baseline result
- final go/no-go decision

Final aggregation rules:

- fail if any required scenario is `failed`, `error`, or `skipped`
- fail if any required stream for a required scenario is `failed`, `error`, or `skipped`
- warn, but do not fail, when a non-required stream such as Bash fails while required Python and Ansible gates pass
- fail if mandatory Argo CD certification fails
- fail if runtime parity fails for any dual-supported capability
- fail if final baseline compliance fails
- fail if any optional scenario marked `required: true` by the profile fails
- warn, but do not fail, for optional scenario failures when the profile leaves them optional
- warn, but do not fail, when recovery succeeds after an optional scenario failure
- pass only when all required gates pass and no hard-stop condition remains open

## Operator Workflow

Expected operator workflow:

1. Select a release profile.
2. Run the full certification set for release readiness.
3. If failures occur, inspect structured artifacts.
4. Fix code, rerun failed scenarios or resume certification.
5. Use the same framework for future releases by updating profile values and scenario selection rather than rewriting orchestration logic.

The framework must support focused reruns for debugging and full certification runs for release sign-off.

## Incremental Delivery Plan

Recommended implementation sequence:

1. Add profile schema, loader, and normalized contract model.
2. Add baseline discovery and convergence.
3. Add the release runner and shared artifact model.
4. Reuse `tests/e2e/orchestrator.py`, `phase_handlers.py`, `failure_injection.py`, `monitoring.py`, and `full_validation_helpers.py` to power Python release scenarios first.
5. Add Ansible stream adapters and parity assertions.
6. Add Bash real-lab adapters for release-relevant script flows.
7. Add bounded recovery automation and soak controls.
8. Add release summary rendering and operator-facing documentation.

This order gets value quickly without blocking on full cross-stream completion before the framework becomes usable.

## Design Decisions

- Use a dedicated `tests/release/` layer rather than expanding `tests/e2e/` into the release control plane.
- Use pytest as the only execution model for the release framework.
- Keep environment-specific names and expectations in explicit profile files, with only sanitized examples checked in.
- Treat Argo CD support as a mandatory release gate.
- Use a full lab baseline contract, not only switchover role checks.
- Distinguish existing static parity tests from new real-cluster runtime parity certification.
- Perform automatic diagnosis and bounded recovery after failures.
- Reuse existing E2E helpers as implementation details, but keep release policy separate.

## Success Criteria

This design is successful when the repository can:

- certify Bash, Python, and Ansible release surfaces on a real two-hub lab
- run repeated switchovers without manual lab rebuild between cycles
- validate both ordinary and Argo CD-managed behavior as part of release gating
- recover from bounded failures back to declared baseline
- emit enough structured evidence for a confident release decision
