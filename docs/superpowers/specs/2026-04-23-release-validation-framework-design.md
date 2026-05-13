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
- `--release-mode MODE`: release intent. Allowed values are `certification`, `focused-rerun`, and `debug`. The default is `certification` when no scenario or stream filter is supplied, and `focused-rerun` when a scenario or stream filter is supplied unless explicitly overridden.
- `--release-scenario ID`: repeatable scenario filter. When supplied, requested IDs narrow the selected scenario set, but non-filterable prerequisites and required post-mutation checks are still added according to the scenario selection rules below.
- `--release-stream STREAM`: repeatable stream filter. Allowed values are `bash`, `python`, and `ansible`.
- `--release-resume-from-artifacts DIR`: resume a partial release run from a compatible previous release artifact directory.
- `--release-rerun-from-artifacts DIR`: start a fresh focused rerun while using a compatible previous release artifact directory only for lab identity and compatibility checks.
- `--release-artifact-dir DIR`: optional artifact root. Default is profile `artifacts.root`, or `artifacts/release` when the profile does not override it.
- `--allow-dirty`: allow a certification-impacting run to proceed when the current git checkout has uncommitted changes. This must be explicit and recorded in the emitted artifacts.

Normal developer test runs must not execute release tests accidentally:

- `pytest tests/` skips all tests marked `release` unless `--release-profile` or `ACM_RELEASE_PROFILE` is set.
- The skip reason must state that release tests require an explicit release profile.
- Supplying `--release-profile` allows release tests to run when selected by path, marker, or normal pytest discovery.
- Supplying an unknown scenario, unknown stream, unreadable profile, invalid profile, or artifact reuse directory without a compatible manifest is a pytest failure before any real-cluster mutation.

Full release-certification invocation:

```bash
pytest -m release tests/release --release-profile tests/release/profiles/full-release.example.yaml --release-mode certification
```

Focused rerun and resume examples:

```bash
pytest -m release tests/release --release-profile tests/release/profiles/full-release.example.yaml --release-mode focused-rerun --release-scenario python-passive-switchover
pytest -m release tests/release --release-profile tests/release/profiles/full-release.example.yaml --release-mode focused-rerun --release-stream ansible
pytest -m release tests/release --release-profile tests/release/profiles/full-release.example.yaml --release-mode certification --release-resume-from-artifacts artifacts/release/<run_id>
pytest -m release tests/release --release-profile tests/release/profiles/full-release.example.yaml --release-mode focused-rerun --release-rerun-from-artifacts artifacts/release/<run_id> --release-scenario python-passive-switchover
```

The release harness may run static gates through pytest subprocesses, but it must not recursively invoke the active `tests/release/` run. Static gate commands must explicitly exclude the release marker unless they intentionally target release tests.

### Release Sign-Off Modes

Release execution mode controls whether a run may produce release sign-off evidence. The mode is recorded in `manifest.json`, `summary.json`, and `release-report.md`.

| Mode | Intended use | Sign-off behavior | Artifact reuse behavior |
| --- | --- | --- | --- |
| `certification` | Final release readiness decision. | Only this mode may emit `summary.status: passed` for release sign-off. It must run the full required scenario matrix for the active profile or resume a previous certification attempt that is compatible under the strict certification rules below. | May skip previously completed scenarios only when `git.commit`, `profile.sha256`, selected scenario matrix, selected streams, release metadata hash, and certification fingerprint all match the previous artifact. |
| `focused-rerun` | Fresh execution of selected scenarios after a failure or code change. | Produces diagnostic evidence only unless the selected matrix is the full certification matrix and all certification rules are met. | Never skips completed scenarios. Previous artifacts may be used only for lab identity checks and comparison context. |
| `debug` | Local harness development, profile debugging, or non-release experimentation. | Never produces final release sign-off. Required gates may still fail-closed when the profile requests them, but the report must label the run as non-certification. | May be looser than certification, but every compatibility relaxation must be recorded as a warning. |

A final release pass is valid only when all required certification scenarios pass on one clean git commit, with one profile hash, one selected matrix hash, one release metadata hash, and one compatible certification fingerprint. Cross-commit resume may be used for debugging and lab triage, but it must not skip already completed required scenarios for final certification.

### Scenario Test Topology

Release certification uses one lifecycle-owning pytest test, not one pytest test per scenario.

`tests/release/test_release_certification.py` contains a single `@pytest.mark.release` test function named `test_release_certification`. That test receives a session-scoped release run context, executes the selected scenario catalog in the order declared by the V1 matrix and profile filters, and records per-scenario status in `scenario-results.json`.

Why this is required:

- real-cluster mutation and recovery need one owner for baseline state, recovery budget, and final cleanup
- static gates, baseline checks, mutating scenarios, runtime parity, and final baseline checks have strict ordering that should not depend on pytest test ordering plugins
- scenario-level pass/fail details belong in release artifacts so focused reruns can reason about previous state without depending on pytest node IDs

The release suite may contain ordinary unit tests for contract modules, normalizers, artifact validators, and helper functions. Those tests are not release-certification lifecycle owners and must not mutate a real lab.

Scenario execution order:

1. `static-gates`
2. `lab-readiness`
3. `baseline-check`
4. non-mutating stream checks such as `preflight`
5. mutating scenarios in profile order, constrained by the V1 scenario matrix
6. `runtime-parity`
7. `final-baseline-check`

The scenario catalog, not pytest dependency plugins, enforces this order. `static-gates`, `lab-readiness`, and `baseline-check` are non-filterable prerequisites for every mutating scenario. When any mutating scenario is selected, the selected matrix must also append `runtime-parity` and `final-baseline-check` so mutation is always followed by parity evaluation and proof that the lab returned to baseline. A scenario that depends on earlier failed state is marked `skipped` with a message explaining the blocking prerequisite according to the final aggregation rules, while the pytest test itself continues only when the recovery policy allows it.

`--release-stream` filters only stream adapters (`bash`, `python`, `ansible`). It does not disable `local` static gates or harness-owned baseline/parity/finalization checks. Profiles may make individual static gate groups optional only through the static gate contract below.

### Release Fixture Lifecycle

`tests/release/conftest.py` owns option parsing, default resolution, validation, and construction of a release run context. Fixture scope is part of the contract:

| Fixture | Scope | Depends on | Lifecycle contract |
| --- | --- | --- | --- |
| `release_options` | session | pytest config and `ACM_RELEASE_PROFILE` | Resolves raw CLI/env options. Does not read clusters or mutate artifacts. |
| `release_profile` | session | `release_options` | Loads, validates, and content-scans the profile. Immutable after creation. |
| `selected_release_matrix` | session | `release_profile`, `release_options` | Applies scenario and stream filters, adds non-filterable prerequisites and post-mutation checks, validates unknown IDs, freezes execution order, and computes the selected matrix hash. |
| `release_artifacts` | session | `release_profile`, `release_options`, git state | Creates the run artifact directory, writes early failed manifests when validation fails, and owns path normalization. |
| `artifact_reuse_context` | session | `release_artifacts`, `release_profile`, `release_options`, `selected_release_matrix` | Validates artifact resume or rerun compatibility, release mode semantics, selected matrix hash, and dirty-checkout policy before any cluster access. |
| `baseline_manager` | session | `release_profile`, `release_artifacts` | Owns discovery, baseline assertions, convergence, and final baseline checks. Mutable only through explicit baseline/recovery methods. |
| `recovery_budget` | session | `release_profile`, `release_artifacts` | Tracks total recovery budget and per-scenario recovery attempts. |
| `adapter_factory` | session | `release_profile`, `release_artifacts` | Builds stream adapters from immutable profile data. Adapters are instantiated per scenario execution to avoid stale command/result state. |
| `release_run_context` | session | all fixtures above | Passed to `test_release_certification`; exposes only orchestrator-level methods and immutable configuration. |

`pytest.UsageError` is reserved for pytest configuration or session-start option validation failures, after writing a failed manifest when the artifact root can be resolved safely. Missing release profile is different: collected `@pytest.mark.release` tests are skipped with the documented skip reason when no profile path and no `ACM_RELEASE_PROFILE` are present.

Runtime fixture failures, including cluster discovery, baseline validation, artifact writes after setup, and adapter preparation, must write the appropriate failed manifest or partial artifact first and then fail through an explicit fixture or test failure. They must not be reported as `pytest.UsageError`, because those failures describe release execution state rather than invalid pytest invocation.

### Artifact Reuse Compatibility

Artifact reuse has two modes with different semantics:

- `--release-resume-from-artifacts DIR` is partial-run resume. It may skip scenarios already recorded as completed in `DIR/scenario-results.json` only when the active release mode permits skip reuse. In `certification` mode, skip reuse is allowed only for the same git commit, same clean checkout state, same profile hash, same selected matrix hash, same release metadata hash, and same certification fingerprint.
- `--release-rerun-from-artifacts DIR` is a fresh run. It never skips completed scenarios and never carries forward recovery budget. The previous artifact directory is used only to validate profile compatibility, stable lab identity, and focused debugging context.

Both modes require `DIR/manifest.json` to be compatible with the current invocation before any real-cluster mutation.

Required compatibility checks for any artifact-backed execution:

- `manifest.json` exists and has `schema_version: 1`
- `profile.name` and `profile.sha256` match the currently selected profile
- selected stream and scenario filters are the same as, or a subset of, the previous run's `selected_streams` and `selected_scenarios`
- `selected_matrix_hash` exists and either matches the active selected matrix or the active mode is `debug` and records the mismatch as a warning
- `release_metadata_hash` exists for certification artifacts and matches the active release metadata hash
- the artifact directory contains `scenario-results.json` unless the previous run failed before scenario execution
- the stable lab identity in `environment_fingerprint` matches the current profile's hub contexts, ACM namespaces, and managed-cluster expectation contract

Resume mode additionally requires enough scenario status evidence to determine completed scenario skip behavior and enough recovery evidence to compute the remaining recovery budget. Rerun mode does not use previous scenario status or recovery budget for execution, but it still requires compatibility evidence before mutation.

If the previous run failed before `environment_fingerprint` was captured, the artifact directory is diagnostic context only. It is not eligible for mutation resume or artifact-backed rerun.

Git commit differences from the previous artifact do not block `focused-rerun` or `debug` execution, because those modes are meant for fixing code and retesting the same lab contract. They must be recorded as warnings in the new manifest and summary. Git commit differences do block certification skip reuse: a certification run may use previous cross-commit artifacts for diagnostics, but it must re-run every required scenario before a final release pass can be emitted.

Current checkout dirty state is stricter. A run is certification-impacting when it runs the full certification set or selects any scenario or stream that is required by the active profile. For certification-impacting runs, including artifact resume and artifact rerun modes, `git.dirty: true` is a blocking validation error unless the operator supplies `--allow-dirty`. A dirty checkout with `--allow-dirty` may execute for diagnostics, but `certification` mode must emit `summary.status: failed` and `certification_eligible: false`. The same artifact compatibility validator that emits manifest and summary warnings owns this gate. Without `--allow-dirty`, it writes a failed manifest and summary with the dirty-state failure reason and exits before any real-cluster mutation. With `--allow-dirty`, the run may proceed, the manifest records `git.allow_dirty: true`, and both the manifest and summary retain a warning that release certification was forced from an unclean checkout.

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
├── checks/
│   ├── static_gates.py
│   ├── metadata.py
│   └── lab_readiness.py
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
│   ├── redaction.py
│   ├── summary.py
│   └── render.py
├── conftest.py
└── test_release_certification.py
```

Responsibilities:

- `profiles/`: sanitized profile examples and release-policy templates
- `contracts/`: schema validation and normalized internal models
- `checks/`: fail-closed static gates, release metadata validation, and lab-readiness checks
- `adapters/`: stream-specific execution for Bash, Python CLI, and Ansible
- `baseline/`: discovery, convergence, recovery, and bounded healing rules
- `scenarios/`: release scenario catalog and cross-stream assertions
- `reporting/`: sanitized manifests, per-scenario artifacts, summaries, and human-readable output

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

- release identity
  - expected release version
  - metadata files that must agree with the release version
  - whether the run is eligible for final certification sign-off
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

- `tests/release/profiles/` contains checked-in examples and sanitized templates only. Required checked-in examples are `full-release.example.yaml`, `argocd-release.example.yaml`, and `dev-minimal.example.yaml`.
- Concrete lab profiles that include operator-specific kubeconfig paths, context names, or private lab topology live outside the repo by default.
- If the implementation supports local in-repo profiles, `tests/release/profiles/local/` must be gitignored before profile loading support ships.
- Profile files must reference kubeconfig paths; they must not embed kubeconfig contents, tokens, certificates, or cluster credentials.
- Artifact manifests record the profile path and hash for reproducibility, but release reports must not copy credential material from referenced files.

The profile loader enforces this policy, not just the documentation. `load_profile()` must parse YAML, validate the V1 schema, and then call a schema-level content validator such as `ProfileSchema.validate_contents()` or `validate_profile_contents()` before returning a profile model. The content validator recursively scans every string value and rejects credential-like material, including PEM headers such as `-----BEGIN CERTIFICATE-----` and private-key headers, kubeconfig keys such as `token:`, `client-key-data:`, `client-certificate-data:`, and `certificate-authority-data:`, and long base64 certificate or key payloads associated with credential-looking field names. Validation errors must include the profile path, dotted field path, and matched credential class so artifact generation and release reporting refuse unsafe profiles before any cluster access.

If a caller needs a non-failing inspection path, the contracts package may expose `sanitize_profile()` to return a masked copy plus warnings. Certification execution must not use that sanitized warning path as a bypass for invalid profile contents.

## Profile Schema V1

Profiles are YAML mappings validated before any static gate or real-cluster scenario runs. Schema V1 rejects unknown top-level keys and malformed known fields so that typos fail fast. Validation errors include the profile path, dotted field path, invalid value, and expected type or enum.

Profile `profile_version` governs the YAML profile contract. It is intentionally separate from release artifact `schema_version`, which governs generated files such as `manifest.json` and `runtime-parity.json`.

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

Optional top-level keys:

- `release`

Top-level fields:

| Field | Required | Type | Contract |
| --- | --- | --- | --- |
| `profile_version` | yes | integer | Must be `1` for this design. Other versions fail validation. |
| `name` | yes | string | Stable profile name used in artifacts. Must match `^[A-Za-z0-9_.-]+$`. |
| `release` | no | mapping | Release identity and metadata consistency contract. Required for full certification profiles. |
| `hubs` | yes | mapping | Must contain `primary` and `secondary` hub entries. |
| `managed_clusters` | yes | mapping | Must define either `expected_names` or `expected_count`. |
| `streams` | yes | list | Enabled certification streams. Stream IDs must be `bash`, `python`, or `ansible`. |
| `scenarios` | yes | list | Scenario selections and requiredness overrides. Scenario IDs must exist in the V1 scenario matrix. |
| `argocd` | yes | mapping | Mandatory Argo CD certification contract for full release sign-off. |
| `baseline` | yes | mapping | Initial and final lab compliance contract. |
| `limits` | yes | mapping | Run, timeout, soak, cooldown, and failure-budget limits. |
| `recovery` | yes | mapping | Allowlisted recovery actions and hard-stop policy. |
| `artifacts` | yes | mapping | Artifact root, retention, capture, and redaction options. |

`release` schema:

- Optional `expected_version` is a string using the repository's release-version format. It is required for `certification` mode and optional for `focused-rerun` and `debug` mode.
- Optional `candidate_tag` records the expected Git tag or release-candidate tag. When supplied, the current checkout must resolve to that tag or include it in the release metadata warning set.
- Optional `metadata_files` defaults to `README.md`, `CHANGELOG.md` when present, `setup.cfg` or `pyproject.toml` when present, and `ansible_collections/tomazb/acm_switchover/galaxy.yml` when the Ansible stream is enabled.
- Optional `allow_non_authoritative_metadata` defaults to an empty list. Entries identify stale or legacy metadata files that may disagree with `expected_version` without failing certification. Every entry must include a `path` and `reason`, and the generated report must display the exemption.
- Certification mode computes `release_metadata_hash` from the normalized values of all authoritative metadata files, the selected profile hash, and the selected matrix hash.

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
- Optional `required` defaults to `true` for `python` and `ansible`, and defaults to `false` for `bash` in V1. Profiles may opt Bash into fail-closed release gating by setting `required: true`. Full release profiles must make an explicit Bash support decision: either set Bash `required: true` and treat Bash as a supported release surface, or leave Bash non-required and have `release-report.md` state that Bash was compatibility-only and not part of final sign-off.
- Optional `env` is a mapping of additional environment variables for that stream.
- Optional `extra_args` is a list of argv tokens appended only to that stream's command.
- `local` is not a profile stream. It is reserved for harness-owned static gate results recorded in `scenario-results.json`.
- Stream requiredness gates scenarios that include that stream after scenario-level stream narrowing is applied. A scenario-level `streams` list may exclude a profile-required stream from that scenario, and that excluded stream is then not required for that scenario.

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
- Optional `lab_readiness.required` defaults to `true` for full certification profiles.
- Optional `lab_readiness.required_crds` defaults to the CRDs needed by the selected scenario matrix, including ACM Backup/OADP resources and Argo CD Applications when `argocd.mandatory` is true.
- Optional `lab_readiness.backup_storage_location.required` defaults to `true`. When required, both hubs must expose a healthy BackupStorageLocation or equivalent profile-declared backup target before mutation.
- Optional `lab_readiness.argocd_fixture.required` defaults to `argocd.mandatory`. When required, at least one profile-selected or auto-detected ACM-touching Application must be present before the Argo CD scenario runs.
- Optional `static_gates.required` defaults to `true`.
- Optional `static_gates.optional_gate_ids` defaults to an empty list. Full release profiles must leave this empty. Development profiles may include only gate IDs defined by the Static Gates Contract.

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
- Optional `rbac_actions` defaults to `["no_bootstrap", "revalidate"]`. Allowed values are `no_bootstrap`, `bootstrap_hub_rbac`, `bootstrap_managed_cluster_rbac`, and `revalidate`.
- Optional `hard_stop_on` defaults to `["hub_role_restore_unproven", "argocd_resume_unproven", "rbac_bootstrap_unproven", "final_baseline_unproven"]`.

`artifacts` schema:

- Optional `root` defaults to `artifacts/release`.
- Optional `capture_stdout` defaults to `true`.
- Optional `capture_stderr` defaults to `true`.
- Optional `capture_cluster_snapshots` defaults to `false`. Long-running or soak profiles may opt in when snapshot cost is justified.
- Optional `cluster_snapshot_mode` defaults to `allowlist`. Raw namespace dumps are not valid release artifacts unless a future profile schema explicitly grants them with a documented redaction policy.
- Optional `redaction.required` defaults to `true` and must remain `true` for full release profiles.
- Optional `redaction.fail_on_unredacted_secret` defaults to `true`. When true, artifact persistence fails if the sanitizer detects unredacted kubeconfig content, bearer tokens, PEM material, Kubernetes `Secret.data` values, cloud credential fields, or command-line API tokens.
- Optional `compress_after_run` defaults to `false`.
- Optional `retention_days` defaults to `limits.artifact_retention_days`.

Minimal V1 profile example:

```yaml
profile_version: 1
name: lab-passive-release
release:
  expected_version: 1.7.6
  metadata_files:
    - README.md
    - CHANGELOG.md
    - setup.cfg
    - ansible_collections/tomazb/acm_switchover/galaxy.yml
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
  - id: lab-readiness
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
  namespaces:
    - openshift-gitops
baseline:
  initial_primary: primary
  lab_readiness:
    required: true
    backup_storage_location:
      required: true
    argocd_fixture:
      required: true
limits: {}
recovery: {}
artifacts:
  redaction:
    required: true
```

## Baseline Contract

The baseline is a full lab contract, not only a switchover role check.

Required baseline dimensions:

- correct primary and secondary hub roles
- healthy backup schedule, restore state, OADP posture, and BackupStorageLocation readiness
- expected managed-cluster presence on the active primary
- expected observability posture
- expected RBAC/bootstrap readiness
- mandatory Argo CD expectations for detection, pause/resume safety, and release fixture presence
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

### Lab Readiness Contract

`lab-readiness` is a non-mutating gate that runs after local static gates and before baseline convergence. It proves the selected lab can execute the release matrix; it does not attempt to heal the lab. Any failed required readiness assertion blocks mutation and emits diagnostics.

Required readiness assertions for full certification profiles:

- both hub contexts are reachable with the profile-declared kubeconfigs and namespaces
- ACM MultiClusterHub or equivalent ACM version source is discoverable on both hubs
- ACM Backup and OADP CRDs needed by the selected scenario matrix exist
- the profile-required BackupStorageLocation or equivalent backup target exists and reports a healthy or acceptable profile-declared state on both hubs
- passive restore, restore-only, and BackupSchedule resources required by the selected matrix are discoverable or explicitly allowed to be absent before creation
- expected managed clusters exist by name or count according to the profile contract
- required observability resources exist when `baseline.observability.required` is true
- required RBAC validation and bootstrap entrypoints are available before scenarios that need them
- Argo CD Applications CRD and at least one profile-selected or auto-detected ACM-touching Application exist when `argocd.mandatory` is true
- destructive cleanup allowlists are internally consistent with the Argo CD managed-conflict allowlist and recovery policy

Readiness output is recorded in `scenario-results.json` as `scenario_id: lab-readiness` with `stream: local`. The environment fingerprint includes readiness evidence paths, but readiness failure must not be hidden as a baseline-convergence failure.

### Baseline Convergence Algorithm

Baseline convergence is deterministic and bounded. The pre-run heal pass and post-failure recovery pass use the same ordered algorithm, with actions skipped when the profile does not allow them.

Order of operations:

1. Discover current hub facts for both hubs and classify primary, secondary, or standby role.
2. Verify the profile's stable lab identity: hub contexts, ACM namespaces, and managed-cluster expectation contract.
3. Wait for passive restore resources that are already in progress to reach a stable accepted phase before deciding whether cleanup is needed.
4. Validate required RBAC readiness. If the profile permits RBAC recovery, run only allowlisted `rbac_actions` and then revalidate.
5. Verify Argo CD pause/resume expectations. If Argo CD-managed resources conflict with expected hub roles, clean only `ArgoCDManagedConflict` entries explicitly represented in `argocd.managed_conflict_allowlist`.
6. Clean stale role-conflicting `BackupSchedule` or `Restore` resources only when their resource class is present in `recovery.allowed_destructive_cleanup.resources` and the discovered hub role proves they conflict with the profile baseline.
7. Re-run discovery and all baseline assertions.
8. Emit a recovery attempt record for every action, including skipped actions that were considered but not allowed by profile.

The algorithm must not patch arbitrary resource fields to force a role. Role restoration is proven by repo-supported switchover, restore, Argo CD, RBAC bootstrap, or cleanup entrypoints plus a follow-up baseline assertion. If step 7 still fails, the run hard-fails before the next mutating scenario.

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
| `static-gates` | required | local pytest and static checks | no | no | static parity only | Required fail-closed static, packaging, syntax, metadata, and parity gates exit `0`. |
| `lab-readiness` | required | release harness | no | no | no | Required CRDs, ACM Backup/OADP posture, BackupStorageLocation, Argo CD fixture, RBAC, observability, and managed-cluster fixture checks pass before baseline convergence or mutation. |
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

### Static Gates Contract

`static-gates` runs before discovery, lab-readiness checks, baseline convergence, or real-cluster mutation. It records results as `StreamResult` entries with `stream: local` and `scenario_id: static-gates`.

Static gates are fail-closed release checks. The harness must not delegate pass/fail semantics to a convenience wrapper that masks non-zero returns, uses `|| true`, or uses flags such as `--exit-zero` for required gates. A wrapper may install dependencies or prepare paths, but each release gate command below must be recorded and evaluated by its real process return code.

The `Required when` column applies when `baseline.static_gates.required` is true. When `baseline.static_gates.required` is false, the harness may still run the default gate groups, but their failures are warnings and the profile is not valid for full release sign-off.

| Gate ID | Required when | Scope contract |
| --- | --- | --- |
| `root-non-e2e-tests` | always when `baseline.static_gates.required` is true | Run the root non-E2E, non-release pytest suite that validates ordinary local behavior without real-cluster release lifecycle ownership. |
| `static-parity-tests` | always when Python and Ansible streams are both enabled | Run the existing static Python/collection parity tests for shared constants, RBAC contracts, and Argo CD contracts. |
| `python-style-security-gates` | Python stream is enabled | Run formatting, import-order, lint, type, and security checks for tracked Python paths with fail-closed return codes. |
| `python-cli-smoke` | Python stream is enabled | Prove the CLI imports, compiles, and exposes stable non-mutating help/argument parsing before any real-cluster run. |
| `collection-ansible-test-sanity` | Ansible stream is enabled | Run Ansible collection sanity tests from the collection root. |
| `collection-unit-tests` | Ansible stream is enabled | Run collection unit tests without mutating a real lab. |
| `collection-integration-scenario-tests` | Ansible stream is enabled and the gate ID is not listed in `baseline.static_gates.optional_gate_ids` | Run collection integration and scenario tests that are safe as static release prerequisites and do not own the release lifecycle. |
| `collection-build-install` | Ansible stream is enabled | Build the collection tarball, install it into a temporary collection path, and prove the installed artifact resolves as `tomazb.acm_switchover`. |
| `collection-playbook-syntax` | Ansible stream is enabled | Run syntax checks for release-relevant collection playbooks from the installed or source collection path. |
| `release-metadata-consistency` | always in `certification` mode | Verify README, changelog when present, Python packaging metadata when present, collection Galaxy metadata, selected profile, generated manifest, and candidate Git tag agree with `release.expected_version` unless a profile-declared exemption marks a file non-authoritative. |

Default V1 command contracts are intentionally explicit. Implementations may split a row into multiple subprocesses for artifact readability, but the aggregate gate is failed when any required command returns non-zero.

| Gate ID | Canonical command contract |
| --- | --- |
| `root-non-e2e-tests` | `python -m pytest tests/ -m "not e2e and not release"` |
| `static-parity-tests` | `python -m pytest tests/test_constants_parity.py tests/test_rbac_collection_parity.py tests/test_argocd_constants_parity.py` |
| `python-style-security-gates` | `black --check --line-length 120 acm_switchover.py lib/ modules/`; `isort --check-only --profile black --line-length 120 acm_switchover.py lib/ modules/`; `flake8 acm_switchover.py lib/ modules/`; `mypy acm_switchover.py lib/ modules/ --ignore-missing-imports --no-strict-optional`; `bandit --ini .bandit -ll`; `pip-audit` |
| `python-cli-smoke` | `python -m py_compile acm_switchover.py`; `python -m py_compile lib/*.py`; `python -m py_compile modules/*.py`; `python acm_switchover.py --help` |
| `collection-ansible-test-sanity` | from `ansible_collections/tomazb/acm_switchover`: `ansible-test sanity --docker default -v` |
| `collection-unit-tests` | from `ansible_collections/tomazb/acm_switchover`: `ansible-test units --docker default -v` plus any repo-local pytest collection unit suite that is not covered by `ansible-test` |
| `collection-integration-scenario-tests` | `python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration ansible_collections/tomazb/acm_switchover/tests/scenario` and, when `tests/integration/targets` exists, `ansible-test integration --docker default -v` from the collection root |
| `collection-build-install` | from `ansible_collections/tomazb/acm_switchover`: `ansible-galaxy collection build --force`; install the produced `tomazb-acm_switchover-*.tar.gz` into a temporary path with `ansible-galaxy collection install <tarball> -p <tmp-collections-path>`; run `ansible-galaxy collection list tomazb.acm_switchover` against that path |
| `collection-playbook-syntax` | `ansible-playbook --syntax-check` for `playbooks/preflight.yml`, `playbooks/switchover.yml`, `playbooks/restore_only.yml`, `playbooks/decommission.yml`, `playbooks/rbac_bootstrap.yml`, `playbooks/discovery.yml`, and `playbooks/argocd_resume.yml` with the collection path set to the installed artifact when possible |
| `release-metadata-consistency` | harness-owned metadata validator over `release.metadata_files`; it must parse authoritative version fields and fail on mismatches unless the profile declares `allow_non_authoritative_metadata` for the exact path |

The release harness runs each gate implementation as a subprocess with the current checkout as working directory, captures stdout and stderr to sanitized artifact files, and records one `AssertionRecord` per gate command:

- `capability`: `static-gates`
- `name`: gate ID or gate ID plus command label
- `status`: `passed` when return code is `0`, otherwise `failed`
- `expected`: `returncode=0`
- `actual`: observed return code and command label
- `evidence_path`: sanitized stdout/stderr artifact path or a small gate result JSON file

Static gate failure short-circuits the release run before cluster discovery or mutation. The harness still emits `manifest.json`, `scenario-results.json`, `summary.json`, and `release-report.md` with the failed gate evidence. `runtime-parity.json` and `recovery.json` are emitted with `status: not_applicable` and an explanatory warning because no real-cluster scenario ran.

Profiles may make `collection-integration-scenario-tests` optional for a narrow development profile, but full release profiles must keep all default gate groups required. Optional gate failures are warnings; required gate failures fail the run.

### Soak Aggregation Contract

Soak scenarios use fail-closed aggregation. Every required cycle must pass. Runtime parity fails if any required Python/Ansible cycle pair has a failed or missing comparison for a dual-supported capability.

`scenario-results.json` records one `scenario_statuses` entry for the overall `soak` scenario plus per-cycle artifact paths. Per-cycle `StreamResult` entries use stable scenario IDs of the form `soak/cycle-<n>/<stream>`. `runtime-parity.json` may include both per-cycle comparisons and aggregate comparisons, but aggregate success cannot hide a failed required cycle. Majority vote, last-cycle-only success, or "last N cycles" success are not valid release aggregation modes in V1.

## Stream Adapters

### Bash Adapter

The Bash adapter certifies real-lab operator shell surfaces, including discovery, preflight, postflight, and setup or RBAC-related script flows that remain release-relevant. Bash is primarily a validation and control-surface stream, not the primary switchover mutation engine.

V1 Bash result handling is assertion-only:

- every invoked script returns a normal `StreamResult`
- stdout and stderr are always captured to artifact files
- return code `0` is required for a passing required Bash stream
- script output is scanned only for stable script-level success/failure signals already asserted by existing tests, such as summary lines reporting zero failed checks
- cluster-state assertions after the script provide release evidence for hub role, BackupSchedule, Restore, and ManagedCluster outcomes
- Bash does not emit runtime parity comparison records unless a future scenario adds a structured Bash comparison contract

For V1, Bash `AssertionRecord.capability` values are scenario-local names such as `bash-preflight`, `bash-postflight`, and `bash-discovery`, not parity-matrix capability names. This keeps Bash certification useful without making shell output a third strict parity schema.

### Python CLI Adapter

The Python adapter remains the richest mutating execution path. It covers ordinary switchovers, resume behavior, restore-only, resilience cases, repeated cycles, and recovery-oriented certification by reusing the strongest existing E2E helpers where appropriate.

### Ansible Adapter

The Ansible adapter certifies collection playbooks and role orchestration on the same lab contract. It must cover preflight, switchover, restore-only, decommission, checkpoint behavior, and Argo CD management entrypoints where applicable.

## Adapter Result Interfaces

Each stream adapter returns a `StreamResult`. The release harness persists these records in `scenario-results.json` and uses them as the input to runtime parity assertions.

```python
StreamResult = {
    "stream": "local" | "bash" | "python" | "ansible",
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
- Scenario state must be isolated. Every Python scenario invocation gets a unique `--state-file` path under that scenario's artifact directory. Every Ansible collection scenario gets a unique checkpoint path under that scenario's artifact directory through the collection's checkpoint variables. No scenario may reuse another scenario's state or checkpoint file unless the scenario explicitly tests resume behavior and records the source state file in its artifacts.

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
- optional checkpoint and resume behavior
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
| preflight validation | `status`, `critical_failure_count`, `warning_failure_count`, `check_ids`, `failed_check_ids` | `ValidationReporter.results[*].check`, validate-only output, and adapter post-processing | `preflight-report.json.summary` and `preflight-report.json.results[*].id` | Strict equality after mapping Python `critical: true/false` to collection `severity`. |
| primary prep | `backup_schedule_paused`, `auto_import_disabled_clusters`, `thanos_scaled_down`, `skipped_observability` | Python state plus post-run cluster-state discovery | Role result facts plus post-run cluster-state discovery | Strict equality on cluster-state outcomes. |
| activation | `restore_name`, `restore_phase_category`, `sync_restore_enabled`, `managed_cluster_activation_requested` | Python report/state where available plus secondary hub cluster-state discovery | Role facts/checkpoint data plus secondary hub cluster-state discovery | Strict equality on normalized restore and activation outcomes. |
| post-activation verification | `connected_managed_clusters`, `unavailable_managed_clusters`, `klusterlet_remediation_count`, `observability_status` | Python post-activation output plus active hub cluster-state discovery | Role facts plus active hub cluster-state discovery | Strict equality on names and counts, with sorted sets for resource names. |
| finalization | `backup_schedule_present`, `backup_schedule_paused`, `post_enable_backup_observed`, `old_hub_action_result` | Python finalization state/output plus cluster-state discovery | Role facts/checkpoint data plus cluster-state discovery | Strict equality, except optional old-hub actions may be `not_applicable` when profile disables them. |
| RBAC self-validation | `scope`, `subject`, `missing_permissions`, `validation_status` | `RBACValidator` result expansion | `acm_rbac_validate` result expansion | Strict equality on missing permission sets for matching scopes. |
| RBAC bootstrap | `scope`, `created_or_verified_subjects`, `applied_manifest_set`, `bootstrap_status` | Python/bootstrap script or CLI output when selected | `acm_rbac_bootstrap` and `rbac_bootstrap` role results | Strict equality on required subjects and manifest identities. |
| Argo CD management | `selected_applications`, `paused_applications`, `resumed_applications`, `resume_failures`, `conflict_allowlist_used` | Python `argocd.run_id` plus post-run Application discovery keyed by `acm-switchover.argoproj.io/paused-by` and run ID; Python summaries provide counts only | Collection `acm_switchover_argocd.run_id` plus post-run Application discovery keyed by `acm-switchover.argoproj.io/paused-by` and run ID; role summary facts provide counts only | Strict equality on discovered Application name sets and final pause/resume state. |
| discovery | `hub_roles`, `acm_versions`, `managed_cluster_names_or_count`, `observability_present`, `argocd_present`, `capability_flags` | Python discovery/preflight output plus cluster-state discovery | `acm_discovery` result and role discovery facts | Strict equality on profile-required fields; additive discovered metadata is ignored unless promoted to required. |
| machine-readable reports | `required_artifacts_present`, `schema_versions`, `top_level_status`, `required_assertion_count` | Python artifact paths and parsed JSON | Collection artifact paths and parsed JSON | Schema-contract comparison, not field-for-field report equality. |
| optional checkpoints | `completed_phases`, `resume_start_phase`, `skipped_phases`, `checkpoint_errors`, `final_lab_state` | Python `StateManager` state plus resume execution output and post-run cluster-state discovery | Collection checkpoint JSON, `checkpoint_phase` transitions, playbook output, and post-run cluster-state discovery | Behavioral equality. Checkpoint file formats are intentionally not field-for-field comparable. |
| decommission | `removed_resources`, `preserved_resources`, `old_hub_mch_absent`, `managed_clusters_removed` | Python decommission output plus old hub cluster-state discovery | `decommission` role facts plus old hub cluster-state discovery | Strict equality on profile-required removal and preservation outcomes. |

Normalization rules:

- A comparison may return `not_applicable` only when the profile disables the capability or the parity matrix no longer marks it `dual-supported`.
- Missing source evidence for a required normalized field is a failed comparison, not a skipped comparison.
- Resource name sets are sorted before comparison.
- Counts may be compared instead of names only when the profile uses `managed_clusters.expected_count`.
- Preflight `check_ids` and `failed_check_ids` are normalized sets. Python values are derived from `results[*].check`; collection values are derived from `results[*].id`. Failed IDs include only checks whose normalized status is failed or error.
- Argo CD Application name sets are normalized from post-run Kubernetes Application discovery, not from stream summary facts. Discovery is limited to Applications carrying `acm-switchover.argoproj.io/paused-by` for the relevant run ID plus any profile-selected ACM-touching Applications that should have been managed during the scenario.
- Normalizers must emit a concise `AssertionRecord.message` explaining every failed or `not_applicable` comparison.
- Adding a new normalized field for a dual-supported capability requires updating this table or its implemented schema equivalent in the same change.

## Execution Flow

Each release run follows this flow:

1. Load and validate the selected profile, release mode, profile hash, and selected matrix hash.
2. Validate release metadata consistency and compute `release_metadata_hash` when the run is certification-impacting.
3. Run fail-closed static gates first:
   - root non-E2E tests
   - static parity tests
   - Python style, type, security, and CLI smoke checks
   - Ansible collection sanity, unit, integration/scenario, build/install, and playbook syntax checks when the Ansible stream is enabled
4. Discover current lab state across both hubs and managed clusters and capture environment fingerprint data.
5. Run `lab-readiness` to prove required CRDs, BackupStorageLocation/OADP posture, Argo CD fixture state, observability, RBAC, and managed-cluster fixture availability.
6. Verify the baseline contract.
7. If baseline verification fails, run one bounded pre-run heal pass.
8. If the lab is still out of contract, hard-fail before real-cluster certification.
9. Run the real-cluster certification matrix across Bash, Python, and Ansible according to the selected profile.
10. Execute runtime cross-stream assertions for dual-supported capabilities.
11. On scenario failure, capture diagnostics, classify the failure, run bounded recovery, and continue or stop according to policy.
12. Run repeated switchover or soak cycles as declared by the profile.
13. Sanitize all persisted outputs, emit redaction evidence, emit final release summary, and confirm whether the lab ended in baseline-compliant state.

Static-gate or lab-readiness failure short-circuits the run before baseline recovery or any real-cluster mutation. Recovery is for lab-state drift, not source-code, package metadata, static test, or local tooling failures.

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

- `no_bootstrap`
- `bootstrap_hub_rbac`
- `bootstrap_managed_cluster_rbac`
- `revalidate`

`no_bootstrap` records that the recovery attempt intentionally does not apply RBAC assets before checking the current state. `revalidate` remains the read-only diagnostic action used to prove the current RBAC state after any action. Each RBAC recovery action must also declare scope: `primary`, `secondary`, `both`, or an explicit managed-cluster target set. Recovery may invoke only repo-supported bootstrap assets, scripts, or playbooks. It must never patch arbitrary RBAC rules directly.

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

Recovery defaults are defined once in the Profile Schema V1 `recovery` fields above. The implementation must derive defaults from that schema model rather than duplicating a second default table in recovery code.

Default recovery semantics are intentionally conservative:

- no destructive cleanup runs unless the profile allowlists the resource class and scenario context
- destructive cleanup is limited to stale or conflicting `BackupSchedule`, `Restore`, and Argo CD-managed conflicting resources represented by the profile contract
- RBAC recovery is limited to `no_bootstrap`, `bootstrap_hub_rbac`, `bootstrap_managed_cluster_rbac`, and `revalidate`

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
  - sanitized stdout and stderr
  - machine-readable reports after sanitizer pass
  - allowlisted snapshots when enabled
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

### Artifact Redaction Contract

Artifact sanitization is a release gate, not a best-effort post-processing step. All stdout, stderr, parsed reports, rendered release reports, and optional cluster snapshots must pass through `reporting/redaction.py` before persistence or upload.

The sanitizer must redact or reject at least these classes of sensitive material:

- kubeconfig contents, including `client-key-data`, `client-certificate-data`, `certificate-authority-data`, user tokens, and embedded clusters/users
- PEM blocks for certificates, private keys, and public keys
- bearer tokens, API tokens, cookies, and Authorization headers
- Kubernetes `Secret.data` and `Secret.stringData` values
- cloud or S3 credential fields such as access keys, secret keys, session tokens, and backup target credentials
- command lines that include token-like arguments or values

Cluster snapshots are allowlist-based. A snapshot may include resource names, namespaces, phases, labels relevant to the release contract, and selected status fields; it must not include raw object specs for Secrets, ConfigMaps with credential-looking keys, kubeconfigs, service-account tokens, or arbitrary namespace dumps.

Every run emits a redaction summary. If `artifacts.redaction.fail_on_unredacted_secret` is true and unredacted sensitive material is detected after sanitizer processing, the run fails before the affected artifact is written.

## Reporting Schema V1

Every release run writes one artifact directory containing these required files:

- `manifest.json`
- `scenario-results.json`
- `runtime-parity.json`
- `recovery.json`
- `redaction.json`
- `summary.json`
- `release-report.md`

Common JSON requirements:

- `schema_version` is required and must be `1`.
- Timestamps are UTC ISO-8601 strings.
- Paths are relative to the run artifact directory unless explicitly marked as absolute source paths.
- Files must remain machine-readable even when the run fails before mutation starts.
- Files containing command output, source reports, or cluster snapshots must be sanitized before their paths are referenced from a required JSON artifact.

### Source Report Schema Appendix

Release artifact schema V1 is separate from source report schemas emitted by the Python CLI and Ansible collection. Source reports may use their existing schema version style, including the collection's current `"1.0"` string value. The release harness normalizes source reports into release artifact schema V1; it must not assume source report `schema_version` has the same type as release artifact `schema_version`.

Ansible source report paths and required fields used by V1 normalizers:

| Source artifact or fact | Required field paths | Used for |
| --- | --- | --- |
| `preflight-report.json` | `schema_version`, `status`, `summary.passed`, `summary.critical_failures`, `summary.warning_failures`, `results[].id`, `results[].severity`, `results[].status`, `results[].message`, `hubs` | Preflight validation and machine-readable report comparisons. |
| `acm_switchover_preflight_result` fact | `phase`, `status`, `changed`, `report`, `path` | Preflight report artifact discovery and checkpoint report references. |
| `switchover-report.json` | `schema_version`, `source`, `argocd.run_id`, `argocd.summary`, `phases.primary_prep.status`, `phases.activation.status`, `phases.post_activation.status`, `phases.finalization.status` | Switchover, Argo CD, phase outcome, and machine-readable report comparisons. |
| `restore-only-report.json` | `schema_version`, `source`, `operation`, `argocd.run_id`, `argocd.summary`, `phases.activation.status`, `phases.post_activation.status`, `phases.finalization.status` | Restore-only, Argo CD, phase outcome, and machine-readable report comparisons. |
| `argocd_manage` role facts | `acm_switchover_argocd_summary.paused`, `acm_switchover_argocd_summary.restored`, `acm_switchover_argocd_summary_by_hub.*.paused`, `acm_switchover_argocd_summary_by_hub.*.restored`, `acm_switchover_argocd.run_id` | Argo CD pause/resume count checks and run ID extraction. Application name sets come from release harness Application discovery. |
| `acm_rbac_validate` module result | `permissions`, `passed`, `critical_failures`, `results[].id`, `results[].status`, `results[].details.denied_permissions` | RBAC self-validation normalization. |
| `acm_rbac_bootstrap` module and `rbac_bootstrap` role results | created or verified service account subjects, applied manifest identities, bootstrap status | RBAC bootstrap normalization. Implementation must pin concrete field paths when the adapter is added. |
| `acm_discovery` result and discovery role facts | `hub_role`, `status` | Discovery role classification. Full environment fingerprint fields still come from release harness cluster-state discovery because the current discovery role is intentionally minimal. |
| collection checkpoint JSON | `schema_version`, `completed_phases`, `phase_status`, `operational_data`, `errors`, `report_refs`, `updated_at` | Optional checkpoint/resume behavioral parity. |

The implementation must add collection-side schema stability tests for every source field path consumed by release normalizers. If the collection changes a source report path, the same change must update this appendix or the implemented schema equivalent, the normalizer, and the schema stability test.

For Argo CD source reports and role facts, summary fields are count evidence only. The authoritative `selected_applications`, `paused_applications`, and `resumed_applications` name sets are produced by release harness post-run Application discovery keyed by `acm-switchover.argoproj.io/paused-by` and the source run ID.

### Schema Versioning And Migration

Schema V1 is the only version accepted by this design until a later release introduces another artifact schema version. Missing or unknown future `schema_version` values are rejected by the artifact validator before loading and before any real-cluster mutation.

Future schema-version support is deferred until a concrete artifact compatibility need exists. That future design must define the readable version window, loader behavior, operator migration path, deprecation timeline, and changelog requirements before accepting more than one release artifact schema version. Artifacts must never be mutated in place by default.

Runtime behavior is fail-closed. Unsupported versions are rejected with a clear error naming the file path, observed `schema_version`, and supported versions.

`manifest.json` required fields:

- `schema_version`
- `run_id`
- `started_at`
- `completed_at`
- `status`
- `command`
- `release_mode`
- `certification_eligible`
- `profile.name`
- `profile.path`
- `profile.sha256`
- `git.commit`
- `git.branch`
- `git.dirty`
- `git.allow_dirty`
- `tool_version`
- `selected_scenarios`
- `selected_streams`
- `selected_matrix_hash`
- `release_metadata_hash`
- `environment_fingerprint`
- `artifact_redaction.status`
- `warnings`
- `failure_reasons`

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
- `hubs.primary.backup_storage_location.present`
- `hubs.primary.backup_storage_location.health`
- `hubs.primary.oadp.present`
- `hubs.primary.oadp.status`
- `hubs.primary.restore.present`
- `hubs.primary.restore.name`
- `hubs.primary.restore.phase`
- `hubs.primary.restore.sync_restore_enabled`
- `hubs.primary.observability.present`
- `hubs.primary.observability.status`
- `hubs.primary.argocd.present`
- `hubs.primary.argocd.namespaces`
- `hubs.primary.argocd.application_count`
- `hubs.primary.argocd.fixture_application_count`
- the same `hubs.secondary.*` fields for the secondary hub
- `managed_clusters.expectation_type`: `names` or `count`
- `managed_clusters.expected_names`: list, empty when the profile uses count-only matching
- `managed_clusters.expected_count`: integer or `null`
- `managed_clusters.observed_active_names`: sorted list when names are required, otherwise an empty list
- `managed_clusters.observed_active_count`
- `managed_clusters.contexts_available`
- `lab_readiness.status`
- `lab_readiness.required_crds_present`
- `lab_readiness.evidence_paths`
- `capabilities.observability`
- `capabilities.argocd`
- `capabilities.rbac_validation`
- `capabilities.rbac_bootstrap`
- `capabilities.decommission`

Environment fingerprint producer contract:

| Field group | Primary producer | Failure handling |
| --- | --- | --- |
| `generated_at` | release harness clock | Always required. |
| `hubs.*.context`, `hubs.*.acm_namespace` | normalized profile model | Missing or invalid values are profile validation failures before discovery. |
| `hubs.*.acm_version` | Kubernetes API discovery of MultiClusterHub or ACM version source used by existing preflight checks | Required for baseline certification. If unavailable, mutation is blocked. |
| `hubs.*.platform_version`, `hubs.*.kubernetes_version` | Kubernetes API server version and OpenShift version discovery when available | Required for full release profiles. Development profiles may record `unknown` only when they are non-certification-impacting. |
| `hubs.*.hub_role` | baseline discovery role classifier using Restore, BackupSchedule, and ManagedCluster facts | Required before any mutating scenario. |
| `hubs.*.backup_schedule.*` | Kubernetes API discovery of `BackupSchedule` resources | Required when `baseline.backup_schedule.required` is true; otherwise may be present with `present: false`. |
| `hubs.*.backup_storage_location.*`, `hubs.*.oadp.*` | Kubernetes API discovery of OADP and backup storage resources required by the profile | Required when `baseline.lab_readiness.backup_storage_location.required` is true; otherwise may record `unknown` only for non-certification runs. |
| `hubs.*.restore.*` | Kubernetes API discovery of ACM `Restore` resources | Required when `baseline.restore.required` is true; otherwise may be present with `present: false`. |
| `hubs.*.observability.*` | Kubernetes API discovery and existing E2E monitoring helper logic where reusable | Required when observability is required by profile; otherwise may record `present: false` or `unknown`. |
| `hubs.*.argocd.*` | Argo CD discovery used by Python/collection Argo CD management plus profile namespaces | Required when `argocd.mandatory` is true. |
| `managed_clusters.*` | profile model plus active-hub ManagedCluster discovery | Expected fields come from profile; observed fields are required before mutating scenarios. |
| `lab_readiness.*` | release harness readiness checks | Required before any mutating scenario. Failure blocks mutation and is not converted into baseline-convergence failure. |
| `capabilities.*` | release harness derived from profile, repository feature presence, and successful discovery of required APIs | Missing capability evidence for required scenarios blocks mutation. |
| `discovered.<producer>.*` | optional additive data from Python, Ansible, Bash, or harness discovery | Never required for resume compatibility unless promoted through `runtime-parity.json.required_fields`. |

Partial discovery failures are allowed only for pre-mutation diagnostics. A release run may emit a failed fingerprint with unavailable fields marked `unknown`, but it must not start a mutating scenario unless all profile-required stable identity and baseline fields are populated and pass validation.

`environment_fingerprint` may include additive discovered metadata, but strict compatibility ignores unknown additive fields by default. Debug and focused-rerun artifact reuse compatibility is determined by these stable lab-contract fields:

- `hubs.primary.context`
- `hubs.primary.acm_namespace`
- `hubs.secondary.context`
- `hubs.secondary.acm_namespace`
- `managed_clusters.expectation_type`
- `managed_clusters.expected_names`
- `managed_clusters.expected_count`

Certification skip reuse is stricter. In addition to the stable lab-contract fields above, certification resume must match:

- `git.commit`
- `git.dirty: false`
- `profile.sha256`
- `selected_matrix_hash`
- `release_metadata_hash`
- `hubs.primary.acm_version` and `hubs.secondary.acm_version`
- `hubs.primary.platform_version` and `hubs.secondary.platform_version`
- `hubs.primary.kubernetes_version` and `hubs.secondary.kubernetes_version`
- `hubs.*.backup_storage_location.health` when required by profile
- `hubs.*.argocd.namespaces` and Argo CD fixture application count when `argocd.mandatory` is true
- `baseline.observability.required` and observed observability state when observability is required
- required stream and scenario sets

Release comparison uses the same stable lab-contract fields unless a comparison explicitly declares additional required fields in `runtime-parity.json.required_fields`. Additive metadata must be surfaced under `environment_fingerprint.discovered.<producer>.*` so consumers can opt into non-strict comparisons without changing the default contract. Comparison code normalizes fingerprints by stripping unknown keys, canonicalizing scalar types, and sorting fields whose contract requires sorted lists before evaluating compatibility.

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

`redaction.json` required fields:

- `schema_version`
- `status`: `passed`, `failed`, or `not_applicable`
- `scanned_artifacts`: list of scanned artifact paths and artifact types
- `redacted_counts_by_class`
- `rejected_artifacts`: list of artifact paths rejected before persistence
- `warnings`

`summary.json` required fields:

- `schema_version`
- `status`: `passed` or `failed`
- `certification_eligible`
- `release_mode`
- `required_scenarios`
- `optional_scenarios`
- `mandatory_argocd`
- `release_metadata`
- `runtime_parity`
- `artifact_redaction`
- `final_baseline`
- `recovery`
- `warnings`
- `failure_reasons`

`release-report.md` required sections:

- run identity, release mode, and profile
- release metadata consistency
- environment fingerprint
- required scenario results
- optional scenario results
- mandatory Argo CD certification
- runtime parity summary
- recovery summary
- artifact redaction summary
- final baseline result
- final go/no-go decision

Final aggregation rules:

- fail if `release_mode` is not `certification` and the run attempts to emit final release sign-off
- fail if `certification` mode does not run, or strictly resume, the full required matrix for one clean git commit, one profile hash, one selected matrix hash, one release metadata hash, and one compatible certification fingerprint
- fail if the checkout is dirty, even when `--allow-dirty` lets a diagnostic run continue
- fail if release metadata consistency fails for any authoritative metadata file
- fail if any required static gate command returns non-zero
- fail if `lab-readiness` fails or is skipped for a full certification profile
- fail if any required scenario is `failed`, `error`, or `skipped`
- fail if any required stream for a required scenario is `failed`, `error`, or `skipped`
- warn, but do not fail, when a non-required stream such as Bash fails while required Python and Ansible gates pass; the report must state whether Bash was excluded from sign-off
- fail if mandatory Argo CD certification fails
- fail if runtime parity fails for any dual-supported capability
- fail if artifact redaction fails or detects unredacted sensitive material after sanitizer processing
- fail if final baseline compliance fails
- fail if any optional scenario marked `required: true` by the profile fails
- warn, but do not fail, for optional scenario failures when the profile leaves them optional
- warn, but do not fail, when recovery succeeds after an optional scenario failure
- pass only when all required gates pass, `certification_eligible` is true, and no hard-stop condition remains open

## Operator Workflow

Expected operator workflow:

1. Select a sanitized release profile and set `release.expected_version` for the candidate.
2. Run the full certification set with `--release-mode certification` from a clean checkout.
3. If failures occur, inspect structured artifacts, including static gate output, lab-readiness evidence, recovery evidence, redaction evidence, and runtime parity output.
4. Fix code or lab drift, then use `--release-mode focused-rerun` for targeted debugging. Focused reruns may compare against previous artifacts, but they do not produce final release sign-off unless the full certification matrix is rerun under certification rules.
5. Re-run or strictly resume certification only when the same commit, profile hash, selected matrix hash, release metadata hash, and certification fingerprint are compatible.
6. Use the same framework for future releases by updating profile values and scenario selection rather than rewriting orchestration logic.

The framework must support focused reruns for debugging and full certification runs for release sign-off, while preventing mixed-commit or dirty-checkout artifacts from being mistaken for final certification evidence.

## Incremental Delivery Plan

Recommended implementation sequence:

1. Add profile schema, loader, normalized contract model, checked-in example profiles, and profile secret scanning.
2. Add release mode handling, selected matrix hashing, release metadata consistency checks, and fail-closed static gate execution.
3. Add artifact model, artifact redaction, and schema validators that can run in non-mutating CI.
4. Add lab-readiness discovery and baseline discovery/convergence.
5. Reuse `tests/e2e/orchestrator.py`, `phase_handlers.py`, `failure_injection.py`, `monitoring.py`, and `full_validation_helpers.py` to power Python release scenarios first.
6. Add Ansible stream adapters, `ansible-test`/collection packaging gates, playbook syntax gates, and parity assertions.
7. Add Bash real-lab adapters for release-relevant script flows and make the Bash sign-off policy explicit in full release profiles.
8. Add bounded recovery automation and soak controls.
9. Add release summary rendering and operator-facing documentation.

This order gets value quickly without blocking on full cross-stream completion before the framework becomes usable.

## Design Decisions

- Use a dedicated `tests/release/` layer rather than expanding `tests/e2e/` into the release control plane.
- Use pytest as the only execution model for the release framework.
- Keep environment-specific names and expectations in explicit profile files, with only sanitized examples checked in.
- Separate `certification`, `focused-rerun`, and `debug` modes so diagnostic artifact reuse cannot masquerade as release sign-off.
- Treat Argo CD support as a mandatory release gate.
- Use a full lab readiness and baseline contract, not only switchover role checks.
- Make static gates, Ansible collection packaging, metadata consistency, and artifact redaction fail-closed for certification.
- Distinguish existing static parity tests from new real-cluster runtime parity certification.
- Perform automatic diagnosis and bounded recovery after failures, but never let recovery erase the original scenario result.
- Reuse existing E2E helpers as implementation details, but keep release policy separate.

## Success Criteria

This design is successful when the repository can:

- certify Bash, Python, and Ansible release surfaces on a real two-hub lab, with an explicit Bash sign-off policy
- run repeated switchovers without manual lab rebuild between cycles
- validate both ordinary and Argo CD-managed behavior as part of release gating
- prove package, syntax, static parity, runtime parity, release metadata, and artifact redaction gates before final sign-off
- recover from bounded failures back to declared baseline without hiding the original failure
- emit enough sanitized structured evidence for a same-commit, same-profile, same-matrix release decision
