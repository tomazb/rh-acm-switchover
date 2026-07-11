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
  Python CLI; dictionaries shaped like `kubernetes.core.k8s_info` results
  for the collection) and pytest temporary directories.
- **Semantic generators**: generators produce valid-shaped domain objects
  plus targeted near-valid mutations — never arbitrary byte blobs.
- **Parity posture**: where a suite covers a dual-supported behavior, it
  targets **both** the Python CLI and the Ansible collection and asserts
  agreement. A discovered disagreement blocks the suite PR until a
  parity-preserving fix restores agreement. A temporary expected failure is
  allowed only after explicit operator approval under the `AGENTS.md`
  intentional-parity-change gate and after the approved divergence is
  recorded in the required in-repo parity documentation, as defined by the
  PR workflow. Filing a bug alone is not approval, and a property is never
  silently weakened to hide drift.
- **Root-test import safety**: property tests live in root `tests/` and must
  remain import-safe when `ansible-core` is absent. When a suite targets a
  collection entry module or action plugin that imports Ansible, it uses the
  existing lazy-import/stub pattern exemplified by
  `tests/test_rbac_collection_parity.py`; it must not make Ansible runtime
  availability a prerequisite for collecting the root test suite.
- **JSON-native values**: where a round-trip property compares values before
  and after JSON serialization, generated values are JSON-native only:
  `null`, booleans, finite numbers, strings, lists, and dictionaries with
  string keys. Merely being accepted by `json.dumps` is not enough if the
  value would change shape on load (for example, tuples or non-string keys).
- **Determinism**: suites run derandomized/seeded in CI; counterexamples are
  pinned as explicit regression examples when fixed.
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
  entry module
  `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_input_validate.py`.
- Existing example-based parity mechanism (kept, not replaced): the shared
  fixture `tests/fixtures/validation_parity_cases.yml` consumed by both
  `tests/test_validation_parity.py` and
  `ansible_collections/tomazb/acm_switchover/tests/unit/test_validation_parity_fixture.py`.

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
- **Counterexample placement**: counterexamples for dual-supported rules are
  pinned in the shared parity fixture when its schema can express the case;
  Python-only Kubernetes-name/namespace counterexamples are pinned in the
  corresponding Python validation tests rather than forced into the shared
  fixture.

**Non-goals**
- No property coverage of `validate_all_cli_args` cross-argument rules
  beyond what example tests in `tests/test_validation.py` already pin
  (argparse namespace combinatorics stay example-based).
- No testing of Ansible argument-spec plumbing (`AnsibleModule` boilerplate).

**Acceptance criteria**
- Properties above implemented for both form factors where the rule is
  dual-supported, agreement property included; no live-cluster or network
  use; suite passes derandomized; existing validation tests remain green.

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
- **Syntax/agreement candidates** assembled from benign names, `.` and `..`
  components, absolute prefixes, repeated separators, trailing separators,
  NUL and control characters, over-long components, and home (`~`) prefixes.
  This broad string domain is used for syntax totality and cross-form
  accept/reject classification even when a value cannot be represented by
  the host filesystem.
- **Filesystem-resolvable candidates** are the subset used by containment and
  symlink properties. They exclude embedded NUL and platform-overlong path
  components, remain within host filename/path limits, and are created only
  under `tmp_path` or another helper-allowed root.
- Local symlink fixtures under `tmp_path`: links pointing inside and outside
  a designated safe root. Relative parent-symlink cases are generated for
  the report-artifact validators; absolute symlink cases also exercise the
  general safe-path validators.

**Properties / invariants**
- **General absolute-path containment**: any filesystem-resolvable absolute
  path accepted by `validate_safe_filesystem_path` / `validate_safe_path`
  resolves, using the helpers' nearest-existing-ancestor rules, under an
  allowed safe root.
- **General relative-path contract**: relative candidates are subject to the
  syntax gate only. The shipped general safe-path helpers return before
  filesystem or symlink resolution for relative paths, so this suite does
  not assert relative-path containment or parent-symlink rejection for those
  two helpers.
- **Artifact-path containment**: any filesystem-resolvable relative or
  absolute path accepted by `validate_report_artifact_path` /
  `validate_report_artifact_directory` resolves under the artifact root
  selected by the helper. Relative parent symlinks and absolute symlinks
  that escape that root are rejected.
- **Traversal rejection**: every candidate containing `..` as a path
  component is rejected by the shared syntax gate. Symlink-indirection
  rejection is asserted for filesystem-resolvable artifact paths and for
  filesystem-resolvable absolute general paths, matching the behavior each
  helper actually enforces.
- **Cross-form-factor agreement**: for every syntax/agreement candidate,
  `lib/path_safety.py` and the collection `module_utils/path_safety.py`
  agree on accept/reject for corresponding general-path and artifact-path
  validators. Filesystem-based cases use identical `tmp_path` fixtures.
- **Syntax gate totality**: `validate_path_syntax` never raises anything
  other than its documented validation error type for any generated string
  input.

**Non-goals**
- No testing of OS permission errors, filesystem races, or non-POSIX
  platforms.
- No claim that the current general safe-path helpers resolve relative
  parent symlinks. Strengthening that production contract requires a
  separate parity-sensitive implementation PR before the stronger property
  can be added.
- NUL-containing and platform-overlong candidates are characterized by the
  syntax/agreement properties; no containment or successful-write property
  attempts to call host filesystem resolution on an unrepresentable value.
- No property coverage of artifact *content* (that is Suite 4).

**Acceptance criteria**
- Absolute general-path containment, relative syntax-only behavior,
  artifact containment, traversal, agreement, and totality properties are
  implemented over their documented subdomains; symlink cases are
  restricted to `tmp_path`; both form factors run on the same candidates;
  suite passes derandomized.

**Verification commands** (future PR)
```bash
pytest tests/property/test_path_safety_properties.py -q
pytest tests/test_path_safety.py -q   # existing example suite stays green
```

---

## Suite 3 — Checkpoint / resume (PBT-05)

**Target code**
- Python: `lib/utils.py` — `StateManager` (`mark_step_completed`,
  `clear_step_completed`, `is_step_completed`, `set_phase`, `set_config`,
  `get_config`, `save_state`, `flush_state`,
  `capture_state_snapshot`/`restore_state_snapshot`, `ensure_contexts`,
  `ensure_hub_identities`), `Phase` enum, `StateIdentityMismatch`.
- Collection: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/checkpoint.py`
  — `build_operation_identity`, `normalize_operation_identity`,
  `build_checkpoint_record`, `validate_operation_identity`,
  `reset_completed_phases_from`, `is_unsafe_legacy_checkpoint`,
  `should_resume_phase`, `CheckpointIdentityMismatch`; entry modules
  `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_checkpoint.py`,
  `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_checkpoint_identity_validate.py`,
  and action plugin
  `ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py`
  (`ActionModule._normalize_checkpoint_data`).

**Generated input domain**
- Sequences of `StateManager` operations (mark/clear steps, set phase,
  set/get JSON-native config values, snapshot/restore) over generated step
  names and phases drawn from the real `Phase` enum, against a `tmp_path`
  state file.
- Checkpoint records: generated completed-phase lists (subsets/permutations
  of real phase names), operation identities with matching and deliberately
  mismatched hub identifiers, schema 1.0 records with and without completed
  phases, and schema 2.0 records missing `operation_identity` with and
  without completed phases.
- Legacy operation identities containing the historical
  `primary_kubeconfig` / `secondary_kubeconfig` fields, plus ordinary
  identity fields and unrelated non-sensitive extension fields.

**Properties / invariants**
- **Round-trip durability**: after any generated operation sequence,
  writing state and constructing a fresh `StateManager` on the same file
  yields the same phase, completed steps, and generated config values.
- **Idempotence**: `mark_step_completed` twice equals once;
  `is_step_completed` reflects exactly the marked-minus-cleared set;
  setting an unchanged config value does not change durable state.
- **Snapshot isolation**: `capture_state_snapshot` returns a deep snapshot;
  later state mutations do not mutate the snapshot, and restoring it
  reinstates the captured durable state without refreshing its timestamp.
- **Resume monotonicity (collection)**: `should_resume_phase` returns True
  exactly for phases **not** recorded as completed in a safe checkpoint
  (completed phases are skipped on resume, never rerun);
  `reset_completed_phases_from(phases, p)` removes `p` and everything after
  it in workflow order and nothing before it.
- **Identity mismatch safety**: any present identity that differs from the
  expected operation identity is detected by
  `validate_operation_identity` / `CheckpointIdentityMismatch`, and by
  `StateIdentityMismatch` on the Python side via `ensure_hub_identities`.
- **Identity sanitization**: `normalize_operation_identity` removes exactly
  the two legacy kubeconfig fields, preserves ordinary/non-sensitive fields,
  does not mutate its input, and is idempotent. `build_operation_identity`
  never persists kubeconfig content or the legacy kubeconfig field names.
- **Missing-identity and legacy classification**:
  `is_unsafe_legacy_checkpoint` is True exactly for schema 1.0 checkpoints
  with a non-empty `completed_phases` list. Direct identity validation
  raises for a missing identity unless `allow_missing=True`, in which case
  it returns False. At the action-plugin normalization boundary, a schema
  2.0 checkpoint with completed phases but no identity fails closed unless
  an explicit reset/reset-from is requested, while a no-progress schema
  2.0 checkpoint may be safely backfilled with the expected identity.
- **Checkpoint record shape**: `build_checkpoint_record` emits the documented
  schema version and required top-level collections with fresh list objects
  per call; generated records are JSON-serializable.

**Non-goals**
- No signal-handling, file-locking-contention, or multi-process crash
  simulation (covered by `tests/test_reliability.py`-style example tests).
- No cross-form-factor byte-level state-file compatibility claim: the CLI
  state file and the collection checkpoint are distinct dual-supported
  mechanisms; the shared property is the *resume semantics contract*, and
  nothing in this suite changes either mechanism's parity status.

**Acceptance criteria**
- Round-trip, idempotence, snapshot, resume, identity-sanitization,
  identity-mismatch, legacy classification, missing-identity, and record
  shape properties implemented; all filesystem activity in `tmp_path`;
  phase inputs sourced from the real `Phase` enum; suite passes
  derandomized and remains root-test import-safe without `ansible-core`.

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
  `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_preflight_report.py`
  (`build_preflight_report`).

**Generated input domain**
- Validation-result dictionaries in the two shapes the normalizer supports:
  legacy `ValidationReporter` entries (`check`, `passed`, `message`,
  `critical`) and structured entries containing at least `id`, `severity`,
  `status`, and `message`, with optional JSON-native `details` and
  `recommended_action`.
- State snapshots with generated phases, completed-step lists, and aggregate
  JSON-native `errors` lists. Aggregate errors belong to the state/report
  summary domain, not to an individual legacy validation-result entry.
- Two destination subdomains derived from Suite 2:
  1. validator-rejected hostile artifact paths for no-write/error properties;
  2. validator-accepted, OS-representable paths under `tmp_path` for
     successful write/round-trip properties. The latter excludes NUL bytes
     and overlong components and stays within platform filename limits.
- File modes: valid octal strings such as `"0644"`, integer mode values in
  `0..0o777`, and malformed or out-of-range values for `_parse_file_mode`.

**Properties / invariants**
- **Serializability and round-trip**: generated reports are JSON-native;
  `write_json_report_artifact` / `write_json_artifact` followed by
  `json.load` reproduces the report exactly for the successful-write path
  domain.
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
- **Path-safety composition**: when a destination is rejected by Suite 2's
  artifact validator, each writer raises its documented validation error and
  creates no file. Successful-write properties use only accepted,
  OS-representable destinations; they do not reinterpret an operating-system
  filename error as a validator contract.
- **Mode parsing and enforcement (collection)**: `_parse_file_mode` maps
  valid octal strings and numeric integer modes to the expected mode. Files
  written by `write_json_artifact` carry exactly that mode; malformed or
  out-of-range modes raise `ArtifactWriteError` before a write.
- **Idempotent changed reporting (collection)**: a first differing write
  reports `changed=True`; repeating the same content and mode reports
  `changed=False`. Check mode predicts the same changed result without
  creating, rewriting, or chmodding the file.
- **Report-reference fidelity (collection)**: `build_report_ref` returns
  exactly the supplied phase/path/kind values and is deterministic.

**Non-goals**
- No property coverage of human-readable console report formatting
  (`modules/preflight/reporter.py` stays example-tested).
- No compatibility promises about report schema evolution beyond the stable
  keys asserted.
- No requirement to preserve arbitrary keys that are outside the two input
  shapes accepted by `_normalise_validation_result`.
- No successful-write property for OS-unrepresentable paths that happen to
  pass current syntax validation; changing that validator contract belongs
  in a separate production-hardening PR.

**Acceptance criteria**
- Round-trip, schema, normalization, state-summary, path-composition, mode,
  changed/check-mode, and report-reference properties implemented for the
  applicable form factor; writes confined to `tmp_path`; suite passes
  derandomized, remains root-test import-safe, and
  `tests/test_report_artifacts.py` stays green.

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
  `BackupScheduleManager._clean_metadata` and
  `BackupScheduleManager._restore_saved_schedule` (with a mocked
  `KubeClient` and `StateManager` on `tmp_path`).
- Collection: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_backup_schedule.py`
  — `_parse_acm_version`, `backup_schedule_pause_mode`,
  `_backup_schedule_names`, `_build_saved_schedule_body`, and
  `build_backup_schedule_operation`.

**Generated input domain**
- ACM version strings: valid `X.Y.Z`/`X.Y` forms on both sides of the 2.12
  pause boundary, including representative 2.10–2.17 values, surrounding
  whitespace, and accepted semver-style prerelease/build suffixes (for
  example `2.14.3-rc1`); plus malformed candidates (empty, non-numeric,
  extra segments, and suffixes not introduced by `-`/`+`, such as
  `2.14.3rc1`). This is a parser/threshold test domain, not an ACM support
  posture statement.
- BackupSchedule resource dictionaries: valid-shaped `BackupSchedule`
  objects with generated names, `spec.paused` booleans, schedule strings,
  and runtime metadata (`resourceVersion`, `uid`, `creationTimestamp`,
  `generation`, `managedFields`) plus top-level `status`.
- Lists of 0..N schedules for multiplicity rules, including unnamed entries.
- Saved-schedule bodies with JSON-native metadata/spec extension fields to
  verify preservation and non-mutation during recreation.

**Properties / invariants**
- **Version gate**: `acm_supports_backup_schedule_pause` returns True iff
  the parsed version is `>= (2, 12, 0)`; for any unparsable version it
  raises `SwitchoverError` (never silently picks a mutation path).
- **Version decision agreement**: for every parseable generated version,
  Python pause support agrees with collection pause mode (`True` ↔
  `"pause"`, `False` ↔ `"delete"`). For unparsable strings both form
  factors reject, but exception class/message equality is not asserted.
- **Multiplicity safety and agreement**: Python
  `fail_on_multiple_backup_schedules` raises exactly when more than one
  schedule exists. With a valid version and valid intent, the collection
  operation builder reaches the same multiplicity decision. Version-format
  rejection is tested separately so evaluation order does not create a
  false parity claim.
- **Ambiguity reporting**: generated schedule names appear in input order in
  the multiplicity error; missing names are represented as `<unnamed>`.
- **Python metadata-helper contract**: `_clean_metadata` removes exactly
  `uid`, `resourceVersion`, `creationTimestamp`, `generation`, and
  `managedFields` from `metadata`, preserves all other metadata and all
  top-level fields (including `status`), mutates only the supplied copy, and
  is idempotent. It does **not** itself remove top-level `status`.
- **Recreated-body safety and agreement**: Python
  `_restore_saved_schedule` (observed through the mocked create call) and
  collection `_build_saved_schedule_body` both operate on a deep copy,
  remove the five runtime metadata fields and top-level `status`, preserve
  `name`, `namespace`, labels, annotations, and unrelated spec fields, set
  `spec.paused` to False, and leave the original saved body unchanged.

**Non-goals**
- No property coverage of live pause/resume API sequencing, wait loops, or
  Velero behavior (mocked-client example tests and E2E own those).
- No cron-expression semantic validation beyond what the code enforces.
- No claim that Python and collection invalid-version exception types are
  identical (`SwitchoverError` versus `ValueError`).

**Acceptance criteria**
- Version, multiplicity, ambiguity-reporting, metadata-helper, recreated-body,
  and cross-form decision properties implemented; only mocked clients used;
  root test collection remains import-safe without `ansible-core`; suite
  passes derandomized and `tests/test_backup_schedule.py` stays green.

**Verification commands** (future PR)
```bash
pytest tests/property/test_backup_schedule_properties.py -q
pytest tests/test_backup_schedule.py -q
```

---

## Suite 6 — Argo CD safety (PBT-08)

**Target code**
- Python: `lib/argocd.py` — `is_autosync_enabled`,
  `has_applicationset_owner`, `_count_acm_resources`,
  `find_acm_touching_apps`, `find_argocd_pause_blockers`, `is_resume_noop`,
  `_pause_ground_truth_applied`, and the discovery/pause/resume result
  dataclasses; `lib/gitops_detector.py` — `detect_gitops_markers`;
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
  present with generated flags), and valid-shaped `status.resources` lists
  mixing ACM-relevant and unrelated kinds, including stale/absent status.
- Application lists use unique generated `(namespace, name)` identities so
  selection and blocker correspondence are unambiguous.

**Properties / invariants**
- **Pause patch safety**: for any generated `syncPolicy`,
  `build_pause_patch` output never contains an enabled `automated` sync
  policy (an existing `automated` key is nulled, never preserved enabled),
  always records the paused-by annotation with the given run id, and does
  not mutate the input sync policy.
- **Filter selection and enrichment**: let `selected` be the input
  Applications for which `is_acm_touching_application` is true, in input
  order. `filter_acm_applications(apps)` returns one enriched shallow copy
  for each item in `selected`, in the same order — not the original input
  dictionaries as a literal subset. Each output preserves the selected
  input's original top-level fields except the enrichment keys
  `acm_resource_count`, `namespace`, and `name`, which are set to the
  computed values. The function does not mutate the input list or input
  dictionaries.
- **Filter freshness boundary**: `filter_acm_applications` classifies from
  the reported `status.resources` contents and does not consult
  `observedGeneration`. Absent or empty resource lists yield no selection;
  stale lists may still yield selection when they report ACM resources. No
  property treats filter omission as proof that ACM impact is known absent.
- **Blocker completeness**: every generated Application that is
  `ApplicationSet`-owned **and** ACM-touching appears in
  `find_argocd_pause_blockers` output (regardless of auto-sync state), as
  does every auto-sync-enabled Application whose ACM impact is unknown due
  to absent, empty, or stale `status.resources`; Applications matching
  neither rule are never reported as blockers. An Application matching both
  conditions is reported once with the ApplicationSet-managed reason, which
  has precedence in the shipped helper.
- **Marker reliability contract**: `detect_gitops_markers` in both form
  factors never treats `app.kubernetes.io/instance` as a definitive marker;
  that key is returned only with the `UNRELIABLE` qualifier.
- **Resume no-op soundness**: `is_resume_noop` is True only for results
  whose outcome made no cluster-visible change.

**Non-goals**
- No property coverage of live pause/resume orchestration, retries, or the
  `scripts/argocd-manage.sh` Bash path (script tests own that).
- No modeling of Argo CD controller reconciliation behavior.

**Acceptance criteria**
- Patch-safety/non-mutation, selection/enrichment, filter-freshness, blocker,
  marker, and no-op properties implemented; both form factors covered with
  agreement asserted where the rule is shared (ACM-touching classification,
  blocker rules, marker reliability); suite passes derandomized and existing
  Argo CD tests stay green.

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
  permission catalog and `expand_rbac_requirements`.
- Existing guardrail: `tests/test_rbac_collection_parity.py` remains
  authoritative for exact expanded-permission equality in its covered
  role/scope/feature scenarios; this suite adds generated set-algebra and
  selector coverage without retiring that guardrail.

**Generated input domain**
- Synthetic operator permission tables with **unique**
  `(api_group, resource)` keys. Each row's verb list is drawn from the real
  vocabulary (read verbs plus `MUTATING_VERBS`) and may contain duplicate
  verbs, an empty verb list, or only mutating verbs.
- Matching and deliberately drifted `expected_removals` mappings, including
  varied insertion order and verb order.
- Role selectors (`operator` / `validator`), scope selectors, decommission
  and old-hub-finalization flags, observability flags, Argo CD modes, and
  install-type inputs accepted by the permission accessors/expander.

**Properties / invariants**
- **Read-only containment**: for any generated unique-key operator table,
  the derived read-only table is a subset relation per resource — every
  derived verb set is the original minus `MUTATING_VERBS`, contains no
  mutating verb, and no new `(api_group, resource)` keys appear.
- **Drift detection soundness**: `_derive_read_only_permissions` succeeds
  iff the actually stripped verbs exactly equal `expected_removals`; any
  generated drift (extra, missing, or different stripped verbs) raises
  `ValueError`.
- **Verb classification consistency**: `RBACValidator._is_write_verb`
  agrees with membership in `MUTATING_VERBS` for the whole generated verb
  vocabulary.
- **Removal-format determinism**: `_format_verb_removals` gives equivalent
  mappings the same sorted key/verb representation regardless of insertion
  order.
- **Real-table invariants**: applied to the actual shipped **cluster**
  tables — `VALIDATOR_CLUSTER_PERMISSIONS` is per resource a subset of
  `OPERATOR_CLUSTER_PERMISSIONS`, and the exceptions recorded in
  `VALIDATOR_CLUSTER_VERB_EXCEPTIONS` account for exactly the stripped
  verbs. The subset relation is **not** asserted for the hub-namespace
  tables: those are hand-maintained per role, and the validator intentionally
  holds verbs the operator lacks (for example `list` on
  `apps/deployments` and `apps/statefulsets` in the observability namespace,
  where the operator has `get`/`patch` only). Namespace-table properties are
  limited to the mutating-verb rule: validator namespace tables contain no
  verb from `MUTATING_VERBS`.
- **Cross-form-factor expanded-set agreement**: for generated valid selector
  combinations, the permission tuples returned by the collection's
  `expand_rbac_requirements` and the corresponding Python tables/accessors
  agree as sets. Invalid validator combinations (for example decommission,
  old-hub finalization, or Argo CD manage) are rejected by both sides rather
  than normalized into permission sets.

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
- Containment, drift, classification, formatting, real-table, selector-error,
  and expanded-set-agreement properties implemented over the documented
  unique-key domain; no live API calls; root collection remains import-safe
  without `ansible-core`; suite passes derandomized and existing RBAC tests
  stay green.

**Verification commands** (future PR)
```bash
pytest tests/property/test_rbac_set_properties.py -q
pytest tests/test_rbac_validator.py tests/test_rbac_collection_parity.py -q
```

---

## Scaffolding contract for PBT-02 (summary)

PBT-02 provides what every suite above assumes, and nothing more:

- The PBT dependency (Hypothesis) added to the appropriate dev/test
  requirements, constrained per the project's dependency policy.
- `tests/property/` package with a shared generator module
  (`tests/property/strategies.py`) hosting cross-suite generators
  (Kubernetes names, paths, phases, Application dicts, permission tuples).
- An import-safe collection-module loader/helper, only where needed, modeled
  on `tests/test_rbac_collection_parity.py`, so root property-test collection
  succeeds even when `ansible-core` is absent.
- The `property` pytest marker registered in `setup.cfg`, wired into the
  default `./run_tests.sh` gate, with deterministic CI profile settings.
- No suite content: PBT-02 may include at most one trivial smoke property
  proving the wiring runs in CI.

Suite PRs must not restructure the scaffolding; scaffolding changes needed
later go through their own reviewed change.
