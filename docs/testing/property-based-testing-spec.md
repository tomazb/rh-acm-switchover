# Property-Based Testing — Implementation Specification

Status: PBT-01 (documentation only). Part of issue #136.
Companion documents: [`property-based-testing.md`](property-based-testing.md)
(plan) and
[`property-based-testing-pr-workflow.md`](property-based-testing-pr-workflow.md)
(process contract).

This specification defines the seven property-based testing (PBT) suites to
be implemented by PRs PBT-03 through PBT-09, after PBT-02 lands the shared
scaffolding. It is the acceptance authority for those PRs: a suite PR is
complete when it satisfies its section here.

## Conventions that apply to every suite

- **Safety model** (normative, from the plan): pure functions and local
  fixtures only. No kubeconfig, no network, no live cluster. Kubernetes
  interaction is exercised only through mocked clients (the
  mocked-`KubeClient` fixture pattern used throughout `tests/` for the
  Python CLI; dictionaries
  shaped like `kubernetes.core.k8s_info` results for the collection) and
  pytest temporary directories.
- **Semantic generators**: generators produce valid-shaped domain objects
  plus targeted near-valid mutations — never arbitrary byte blobs.
- **Parity posture**: where a suite covers a dual-supported behavior, it
  targets **both** the Python CLI and the Ansible collection and asserts
  agreement. A discovered disagreement is a parity bug to fix under the
  `AGENTS.md` parity rules, never grounds for weakening the property or for
  documenting divergence.
- **Determinism**: suites run derandomized/seeded in CI; counterexamples
  are pinned as explicit regression examples when fixed.
- **Layout** (established by PBT-02): property tests live under
  `tests/property/` with a dedicated pytest marker (`property`), so they run
  in the default gate and can also be selected in isolation.
- **Verification commands** listed per suite are for the future suite PRs.
  PBT-01 adds no tooling and no tests; the commands are not expected to work
  until PBT-02/PBT-0N land.

---

## Suite 1 — Validation parity (PBT-03)

**Target code**
- Python: `lib/validation.py` — `InputValidator.validate_kubernetes_name`,
  `validate_kubernetes_namespace`, `validate_kubernetes_label_key`,
  `validate_kubernetes_label_value`, `validate_context_name`,
  `validate_non_empty_string`, `sanitize_context_identifier`,
  `_validate_choice`-backed CLI choice validators.
- Collection: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/validation.py`
  — `validate_context_name`, `validate_operation_inputs`, `_validate_choice`;
  entry module `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_input_validate.py`.
- Existing example-based parity mechanism (kept, not replaced): the shared
  fixture `tests/fixtures/validation_parity_cases.yml` consumed by both
  `tests/test_validation_parity.py` and
  `ansible_collections/tomazb/acm_switchover/tests/unit/test_validation_parity_fixture.py`.
  This suite generalizes those hand-picked cases into generated-input
  agreement properties; counterexamples found by PBT are pinned back into
  the shared fixture.

**Generated input domain**
- Valid RFC 1123 labels/subdomains of varying lengths (1..253), plus
  near-valid mutations: uppercase characters, leading/trailing hyphens or
  dots, over-length names, embedded whitespace, unicode, empty strings.
- Kubeconfig context names: realistic `cluster/user`-style identifiers plus
  mutations with shell metacharacters, path separators, and control
  characters.
- Choice fields: values drawn from each validator's documented choice set
  plus case variants and misspellings.

**Properties / invariants**
- **Cross-form-factor agreement**: for every generated candidate, the Python
  validator and the collection validator for the same dual-supported rule
  (context names; shared choice fields validated by
  `validate_operation_inputs`) either both accept or both reject.
- **Soundness**: any string accepted as a Kubernetes name/namespace conforms
  to the RFC 1123 constraints the validator documents (charset, length,
  boundary characters).
- **Rejection is an exception, never a mutation**: validators raise
  (`SecurityValidationError` / `ValidationError`) and never return a
  "cleaned" value, except `sanitize_context_identifier`, whose property is
  idempotence (`sanitize(sanitize(x)) == sanitize(x)`) and output-charset
  safety.

**Non-goals**
- No property coverage of `validate_all_cli_args` cross-argument rules
  beyond what example tests in `tests/test_validation.py` already pin
  (argparse namespace combinatorics stay example-based).
- No testing of Ansible argument-spec plumbing (AnsibleModule boilerplate).

**Acceptance criteria**
- Properties above implemented for both form factors, agreement property
  included; no live-cluster or network use; suite passes derandomized;
  existing `tests/test_validation.py` untouched or extended only with
  pinned counterexamples.

**Verification commands** (future PR)
```bash
pytest tests/property/test_validation_properties.py -q
pytest -m property tests/property/ -q
```

---

## Suite 2 — Path safety (PBT-04)

**Target code**
- Python: `lib/path_safety.py` — `validate_path_syntax`,
  `validate_safe_filesystem_path`, `validate_report_artifact_path`,
  `validate_report_artifact_directory`. Callers reach these through two
  wrappers: `lib/report_artifacts.py` re-exports
  `validate_report_artifact_path` and `validate_report_artifact_directory`,
  and `lib/validation.py` wraps `validate_safe_filesystem_path` as
  `InputValidator.validate_safe_filesystem_path`. Import property-test
  targets from `lib.path_safety` directly.
- Collection: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/path_safety.py`
  — `validate_path_syntax`, `validate_safe_path`,
  `validate_report_artifact_path`, `validate_report_artifact_directory`;
  entry module
  `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_safe_path_validate.py`
  and filter plugin
  `ansible_collections/tomazb/acm_switchover/plugins/filter/paths.py`.

**Generated input domain**
- Path candidates assembled from generated segments: benign names, `.` and
  `..` components, absolute prefixes, repeated separators, trailing
  separators, NUL and control characters, over-long components, home (`~`)
  prefixes.
- Local symlink fixtures under `tmp_path`: links pointing inside and outside
  a designated safe root. Relative parent-symlink cases are generated for
  the report-artifact validators; absolute symlink cases also exercise the
  general safe-path validators. Symlinks are created only in pytest
  temporary directories.

**Properties / invariants**
- **General absolute-path containment**: any absolute path accepted by
  `validate_safe_filesystem_path` / `validate_safe_path` resolves, using the
  helpers' nearest-existing-ancestor rules, under an allowed safe root.
- **General relative-path contract**: relative candidates are subject to the
  syntax gate only. The shipped general safe-path helpers return before
  filesystem or symlink resolution for relative paths, so this suite does
  not assert relative-path containment or parent-symlink rejection for those
  two helpers.
- **Artifact-path containment**: any relative or absolute path accepted by
  `validate_report_artifact_path` / `validate_report_artifact_directory`
  resolves under the artifact root selected by the helper. Relative parent
  symlinks and absolute symlinks that escape that root are rejected.
- **Traversal rejection**: every candidate containing `..` as a path
  component is rejected by the shared syntax gate. Symlink-indirection
  rejection is asserted for artifact paths and for absolute general paths,
  matching the behavior each helper actually enforces.
- **Cross-form-factor agreement**: for generated candidates evaluated under
  identical safe-root fixtures, `lib/path_safety.py` and the collection
  `module_utils/path_safety.py` agree accept/reject for corresponding
  general-path and artifact-path validators.
- **Syntax gate totality**: `validate_path_syntax` never raises anything
  other than its documented validation error type, for any string input.

**Non-goals**
- No testing of OS permission errors, filesystem races, or non-POSIX
  platforms.
- No claim that the current general safe-path helpers resolve relative
  parent symlinks. Strengthening that production contract requires a
  separate parity-sensitive implementation PR before the stronger property
  can be added.
- No property coverage of artifact *content* (that is Suite 4).

**Acceptance criteria**
- Absolute general-path containment, relative syntax-only behavior,
  artifact containment, traversal, agreement, and totality properties are
  implemented; symlink cases are restricted to `tmp_path`; both form
  factors run on the same candidates; suite passes derandomized.

**Verification commands** (future PR)
```bash
pytest tests/property/test_path_safety_properties.py -q
pytest tests/test_path_safety.py -q   # existing example suite stays green
```

---

## Suite 3 — Checkpoint / resume (PBT-05)

**Target code**
- Python: `lib/utils.py` — `StateManager` (`mark_step_completed`,
  `clear_step_completed`, `is_step_completed`, `set_phase`,
  `get_current_phase`, `save_state`, `flush_state`,
  `capture_state_snapshot`/`restore_state_snapshot`,
  `ensure_contexts`, `ensure_hub_identities`), `Phase` enum,
  `StateIdentityMismatch`.
- Collection: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/checkpoint.py`
  — `build_operation_identity`, `normalize_operation_identity`,
  `build_checkpoint_record`, `validate_operation_identity`,
  `reset_completed_phases_from`, `is_unsafe_legacy_checkpoint`,
  `should_resume_phase`, `CheckpointIdentityMismatch`; entry modules
  `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_checkpoint.py`,
  `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_checkpoint_identity_validate.py`,
  and action plugin
  `ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py`.

**Generated input domain**
- Sequences of `StateManager` operations (mark/clear steps, set phase,
  snapshot/restore) over generated step names and phases drawn from the real
  `Phase` enum, against a `tmp_path` state file.
- Checkpoint records: generated completed-phase lists (subsets/permutations
  of real phase names), operation identities with matching and deliberately
  mismatched hub identifiers, schema 1.0 records with and without completed
  phases, and schema 2.0 records missing `operation_identity` with and
  without completed phases.

**Properties / invariants**
- **Round-trip durability**: after any generated operation sequence,
  writing state and constructing a fresh `StateManager` on the same file
  yields the same phase, completed steps, and config.
- **Idempotence**: `mark_step_completed` twice equals once;
  `is_step_completed` reflects exactly the marked-minus-cleared set.
- **Resume monotonicity (collection)**: `should_resume_phase` returns True
  exactly for phases **not** recorded as completed in a safe checkpoint
  (completed phases are skipped on resume, never rerun);
  `reset_completed_phases_from(phases, p)` removes `p` and everything after
  it in workflow order and nothing before it.
- **Identity mismatch safety**: any present identity that differs from the
  expected operation identity is detected by
  `validate_operation_identity` / `CheckpointIdentityMismatch`, and by
  `StateIdentityMismatch` on the Python side via `ensure_hub_identities`.
- **Missing-identity and legacy classification**:
  `is_unsafe_legacy_checkpoint` is True exactly for schema 1.0 checkpoints
  with a non-empty `completed_phases` list. Direct identity validation
  raises for a missing identity unless `allow_missing=True`, in which case
  it returns False. At the action-plugin normalization boundary, a schema
  2.0 checkpoint with completed phases but no identity fails closed unless
  an explicit reset/reset-from is requested, while a no-progress schema
  2.0 checkpoint may be safely backfilled with the expected identity.

**Non-goals**
- No signal-handling, file-locking-contention, or multi-process crash
  simulation (covered by `tests/test_reliability.py`-style example tests).
- No cross-form-factor byte-level state-file compatibility claim: the CLI
  state file and the collection checkpoint are distinct dual-supported
  mechanisms; the shared property is the *resume semantics contract*, and
  nothing in this suite changes either mechanism's parity status.

**Acceptance criteria**
- Round-trip, idempotence, resume, identity-mismatch, legacy classification,
  and missing-identity properties implemented; all filesystem activity in
  `tmp_path`; phase inputs sourced from the real `Phase` enum rather than
  hard-coded strings; suite passes derandomized.

**Verification commands** (future PR)
```bash
pytest tests/property/test_checkpoint_properties.py -q
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q  # collection suite stays green
```

---

## Suite 4 — Report artifacts (PBT-06)

**Target code**
- Python: `lib/report_artifacts.py` — `build_operation_report`,
  `write_json_report_artifact`, `_normalise_validation_result`,
  `_summarize_state`.
- Collection: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/artifacts.py`
  — `build_report_ref`, `write_json_artifact`, `_parse_file_mode`,
  `ArtifactWriteError`; entry modules
  `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_report_artifact.py`
  and
  `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_preflight_report.py`.

**Generated input domain**
- Validation-result dictionaries in the two shapes the normalizer supports:
  legacy `ValidationReporter` entries (`check`, `passed`, `message`,
  `critical`) and structured entries containing at least `id`, `severity`,
  `status`, and `message`, with optional `details` and
  `recommended_action`. Generated string fields include empty, unicode, and
  very long values while remaining JSON-serializable.
- State snapshots with generated phases, completed-step lists, and aggregate
  `errors` lists. Aggregate errors belong to the state/report summary domain,
  not to an individual legacy validation-result entry.
- Destination paths drawn from Suite 2's artifact-path generator (valid
  under a safe root, plus hostile candidates expected to be rejected).
- File modes: valid octal strings/ints plus malformed values for
  `_parse_file_mode`.

**Properties / invariants**
- **Serializability and round-trip**: `build_operation_report` output is
  always JSON-serializable; `write_json_report_artifact` /
  `write_json_artifact` followed by `json.load` reproduces the report
  exactly.
- **Schema stability**: every generated report contains the stable top-level
  keys its builder actually ships (`schema_version`, `status`, `summary`,
  and `hubs` in both form factors; the `operation` section additionally in
  Python CLI operation reports — collection preflight reports have no
  `operation` section), with types independent of input variation.
- **Normalization shape and preservation**: `_normalise_validation_result`
  accepts either supported result shape without raising. Structured entries
  are returned unchanged. Legacy entries map `check`, `passed`, `message`,
  and `critical` deterministically into the structured schema, preserve the
  message/check meaning, and do not mutate their input. No property assumes
  unsupported aggregate `errors` or `warnings` fields on a legacy entry.
- **State-summary accounting**: `_summarize_state` reports exactly the number
  of generated completed steps and state-level errors and preserves the
  generated current phase.
- **Path-safety composition**: artifact writes only ever succeed at
  destinations that pass Suite 2's artifact validators; hostile
  destinations raise the documented error and create no file.
- **Mode enforcement (collection)**: files written by `write_json_artifact`
  carry exactly the requested mode; invalid modes raise `ArtifactWriteError`
  without writing.

**Non-goals**
- No property coverage of human-readable console report formatting
  (`modules/preflight/reporter.py` stays example-tested).
- No compatibility promises about report schema evolution beyond the stable
  keys asserted.
- No requirement to preserve arbitrary keys that are outside the two input
  shapes accepted by `_normalise_validation_result`.

**Acceptance criteria**
- Round-trip, schema, normalization, state-summary, path-composition, and
  mode properties implemented for both form factors; writes confined to
  `tmp_path`; suite passes derandomized; `tests/test_report_artifacts.py`
  stays green.

**Verification commands** (future PR)
```bash
pytest tests/property/test_report_artifact_properties.py -q
pytest tests/test_report_artifacts.py -q
```

---

## Suite 5 — BackupSchedule (PBT-07)

**Target code**
- Python: `modules/backup_schedule.py` —
  `acm_supports_backup_schedule_pause`,
  `fail_on_multiple_backup_schedules`, `_backup_schedule_names`,
  `BackupScheduleManager.ensure_enabled` and `_clean_metadata` (with a
  mocked `KubeClient` and `StateManager` on `tmp_path`).
- Collection: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_backup_schedule.py`
  and its helpers.

**Generated input domain**
- ACM version strings: valid `X.Y.Z`/`X.Y` forms across the 2.10–2.14
  range, including the accepted semver-style prerelease/build suffixes
  (e.g. `2.14.3-rc1`), plus malformed candidates (empty, non-numeric,
  extra segments, suffixes not introduced by `-`/`+` such as `2.14.3rc1`).
- BackupSchedule resource dictionaries: valid-shaped
  `BackupSchedule` objects with generated names, `spec.paused` booleans,
  schedule strings, and runtime metadata
  (`resourceVersion`, `uid`, `creationTimestamp`, `managedFields`, `status`).
- Lists of 0..N schedules for the multiplicity rules.

**Properties / invariants**
- **Version gate**: `acm_supports_backup_schedule_pause` returns True iff
  the parsed version is `>= (2, 12, 0)`; for any unparsable version it
  raises `SwitchoverError` (never silently picks a mutation path).
- **Multiplicity safety**: `fail_on_multiple_backup_schedules` raises for
  every generated list with more than one schedule and never raises for
  zero or one.
- **Metadata cleaning**: `_clean_metadata` strips only runtime metadata
  (never `name`, `namespace`, labels, or `spec`), and is idempotent.
- **Cross-form-factor agreement**: for generated version strings and
  schedule sets, the Python helpers and the collection module's
  corresponding decision logic agree on pause-support and multiplicity
  outcomes.

**Non-goals**
- No property coverage of live pause/resume API sequencing, wait loops, or
  Velero behavior (mocked-client example tests and E2E own those).
- No cron-expression semantic validation beyond what the code enforces.

**Acceptance criteria**
- Version-gate, multiplicity, metadata, and agreement properties
  implemented; only mocked clients used; suite passes derandomized;
  `tests/test_backup_schedule.py` stays green.

**Verification commands** (future PR)
```bash
pytest tests/property/test_backup_schedule_properties.py -q
pytest tests/test_backup_schedule.py -q
```

---

## Suite 6 — Argo CD safety (PBT-08)

**Target code**
- Python: `lib/argocd.py` — `is_resume_noop`,
  `_pause_ground_truth_applied`, discovery/pause/resume result dataclasses;
  `lib/gitops_detector.py` — `detect_gitops_markers`;
  coordination surfaces in `lib/argocd_coordinator.py` and
  `lib/argocd_resume.py` where pure.
- Collection: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/argocd.py`
  — `is_autosync_enabled`, `is_acm_touching_application`,
  `filter_acm_applications`, `find_argocd_pause_blockers`,
  `build_pause_patch`, `has_applicationset_owner`;
  `ansible_collections/tomazb/acm_switchover/plugins/module_utils/gitops.py`;
  entry module
  `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_argocd_filter.py`.

**Generated input domain**
- Argo CD `Application` dictionaries with generated `metadata`
  (labels/annotations including GitOps markers and the unreliable
  `app.kubernetes.io/instance` label), `ownerReferences` (with and without
  `ApplicationSet` owners), `spec.syncPolicy` (absent, empty, `automated`
  present with generated flags), and `status.resources` lists mixing
  ACM-relevant and unrelated kinds, including stale/absent status.

**Properties / invariants**
- **Pause patch safety**: for any generated `syncPolicy`,
  `build_pause_patch` output never contains an enabled `automated` sync
  policy (an existing `automated` key is nulled, never preserved enabled)
  and always records the paused-by annotation with the given run id.
- **Filter selection and enrichment**: let `selected` be the input
  Applications for which `is_acm_touching_application` is true, in input
  order. `filter_acm_applications(apps)` returns one enriched shallow copy
  for each item in `selected`, in the same order — not the original input
  dictionaries as a literal subset. Each output preserves the selected
  input's original top-level fields, adds the correct `acm_resource_count`,
  `namespace`, and `name`, and the function does not mutate the input list or
  dictionaries.
- **Unknown-impact pipeline safety**: the filter alone omits Applications
  with absent or stale `status.resources` because their ACM impact is
  unknown. Every omitted unknown-impact Application with auto-sync enabled
  is surfaced by `find_argocd_pause_blockers`; unknown-impact Applications
  without auto-sync are outside the managed-pause mutation path.
- **Blocker completeness**: every generated application that is
  `ApplicationSet`-owned **and** ACM-touching appears in
  `find_argocd_pause_blockers` output (regardless of auto-sync state), as
  does every auto-sync-enabled application whose ACM impact is unknown;
  applications matching neither rule are never reported as blockers.
- **Marker reliability contract**: `detect_gitops_markers` (and the
  collection GitOps helpers) never classify an application as definitively
  GitOps-managed based solely on `app.kubernetes.io/instance` — that marker
  stays flagged `UNRELIABLE` for any generated metadata combination.
- **Resume no-op soundness**: `is_resume_noop` is True only for results
  whose outcome made no cluster-visible change.

**Non-goals**
- No property coverage of live pause/resume orchestration, retries, or the
  `scripts/argocd-manage.sh` Bash path (script tests own that).
- No modeling of Argo CD controller reconciliation behavior.

**Acceptance criteria**
- Patch-safety, selection/enrichment, unknown-impact pipeline, blocker,
  marker, and no-op properties implemented; both form factors covered with
  agreement asserted where the rule is shared (ACM-touching classification,
  blocker rules); suite passes derandomized; `tests/test_argocd.py` and
  `tests/test_argocd_constants_parity.py` stay green.

**Verification commands** (future PR)
```bash
pytest tests/property/test_argocd_safety_properties.py -q
pytest tests/test_argocd.py tests/test_argocd_constants_parity.py -q
```

---

## Suite 7 — RBAC set properties (PBT-09)

**Target code**
- Python: `lib/rbac_validator.py` — `_derive_read_only_permissions`,
  module-level `MUTATING_VERBS` and `VALIDATOR_CLUSTER_VERB_EXCEPTIONS`,
  the `RBACValidator.OPERATOR_CLUSTER_PERMISSIONS` class attribute,
  `RBACValidator._is_write_verb`,
  permission-table accessors (`_get_cluster_permissions`,
  `_get_hub_namespace_permissions`,
  `_get_managed_cluster_namespace_permissions`,
  `_get_argocd_cluster_permissions`), `_format_verb_removals`.
- Collection: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_validate.py`
  permission catalog.
- Existing guardrail: `tests/test_rbac_collection_parity.py` (remains
  authoritative for exact catalog equality; this suite adds set-algebra
  properties on top).

**Generated input domain**
- Synthetic operator permission tables with **unique**
  `(api_group, resource)` keys. Each row's verb list is drawn from the real
  vocabulary (read verbs plus `MUTATING_VERBS`) and may contain duplicate
  verbs, an empty verb list, or only mutating verbs.
- Matching and deliberately drifted `expected_removals` mappings.
- Role selectors (`operator` / `validator`) and argocd-mode inputs for the
  table accessors.

**Properties / invariants**
- **Read-only containment**: for any generated unique-key operator table,
  the derived read-only table is a subset relation per resource — every
  derived verb set is the original minus `MUTATING_VERBS`, contains no
  mutating verb, and no new `(api_group, resource)` keys appear.
- **Drift detection soundness**: `_derive_read_only_permissions` succeeds
  iff the actually-stripped verbs exactly equal `expected_removals`; any
  generated drift (extra, missing, or different stripped verbs) raises
  `ValueError`.
- **Verb classification consistency**: `RBACValidator._is_write_verb`
  agrees with membership in `MUTATING_VERBS` for the whole generated verb
  vocabulary.
- **Real-table invariants**: applied to the actual shipped **cluster**
  tables — `VALIDATOR_CLUSTER_PERMISSIONS` is per resource a subset of
  `OPERATOR_CLUSTER_PERMISSIONS`, and the exceptions recorded in
  `VALIDATOR_CLUSTER_VERB_EXCEPTIONS` account for exactly the stripped
  verbs. The subset relation is **not** asserted for the hub-namespace
  tables: those are hand-maintained per role, and the validator
  intentionally holds verbs the operator lacks (e.g. `list` on
  `apps/deployments` and `apps/statefulsets` in the observability
  namespace, where the operator has `get`/`patch` only). Namespace-table
  properties are limited to the mutating-verb rule: the validator
  namespace tables contain no verb from `MUTATING_VERBS`.
- **Cross-form-factor set agreement**: the permission sets checked by the
  collection's `acm_rbac_validate.py` and the Python tables agree as sets
  (generalizing the exact-equality guardrail; the guardrail test itself is
  not modified or retired).

**Non-goals**
- No property coverage of live `SelfSubjectAccessReview` calls,
  `check_permission` API behavior, or RBAC manifest YAML under
  `deploy/rbac/` (manifest parity stays with the existing guardrail and
  review process in `AGENTS.md`).
- No generation of arbitrary RBAC verbs outside the project vocabulary.
- No duplicate `(api_group, resource)` rows in a synthetic table. The
  current helper stores removals in a mapping keyed by resource, so duplicate
  rows overwrite prior removal bookkeeping rather than aggregating it.
  Supporting duplicate resource rows requires a separate RBAC-sensitive
  production change before an aggregation property can be added.

**Acceptance criteria**
- Containment, drift, classification, real-table, and set-agreement
  properties implemented over the documented unique-key domain; no live API
  calls; suite passes derandomized; `tests/test_rbac_validator.py` and
  `tests/test_rbac_collection_parity.py` stay green and unmodified (except
  optional pinned counterexamples added as new tests).

**Verification commands** (future PR)
```bash
pytest tests/property/test_rbac_set_properties.py -q
pytest tests/test_rbac_validator.py tests/test_rbac_collection_parity.py -q
```

---

## Scaffolding contract for PBT-02 (summary)

PBT-02 provides what every suite above assumes, and nothing more:

- The PBT dependency (Hypothesis) added to the appropriate dev/test
  requirements, pinned per the project's dependency policy.
- `tests/property/` package with a shared generator module
  (`tests/property/strategies.py`) hosting the cross-suite generators
  (Kubernetes names, paths, phases, Application dicts, permission tuples).
- The `property` pytest marker registered in `setup.cfg`, wired into the
  default `./run_tests.sh` gate, with derandomized CI profile settings.
- No suite content: PBT-02 may include at most one trivial smoke property
  proving the wiring runs in CI.

Suite PRs must not restructure the scaffolding; scaffolding changes needed
later go through their own reviewed change.
