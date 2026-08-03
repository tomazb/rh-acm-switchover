# Run record — design

Date: 2026-08-02
Status: approved (brainstorming session, 2026-08-02 architecture review candidate 1)
Execution model: Opus 5

## Problem

`StateManager`'s real interface is not its methods — it is ~15 untyped string
keys written and read across 9 files (68 call sites including tests), an
unwritten ordering invariant ("activation writes `auto_import_strategy_set`
before finalization reads it"), and four readers that re-implement the raw
state schema independently (`StateManager`, `lib/cli_outcomes.py`,
`lib/report_artifacts.py`, `show_state.py`). A typo in a key literal returns a
silent default instead of an error. `show_state.py` additionally resolves the
state directory differently from the CLI (`show_state.py:111-121` vs
`lib/runtime_bootstrap.py:32-36`), so with a rejected
`ACM_SWITCHOVER_STATE_DIR` the CLI writes state where `show_state` never looks.

## Decision summary

| Decision | Choice |
|---|---|
| Scope | Cross-phase config handoffs + the four raw-schema readers. Pause-register keys stay behind `PauseRegisterStore`. |
| Shape | New `lib/run_record.py`: `RunRecord` facade over `StateManager` (composition), plus frozen `RunSummary`. |
| Migration | Hard cut in one implementation plan; `set_config`/`get_config` go private at the end; guardrail test locks the seam. |
| Persistence | Interface-only. On-disk JSON schema and key names unchanged; every existing state file remains resumable; no migration logic. |

## Vocabulary

**Run record**: the cross-phase facts of one switchover run — what preflight
discovered, what each phase has done that a later phase or report must know —
exposed as named, typed operations. The durable file behind it is owned by
`StateManager`; the key vocabulary is owned by `RunRecord` alone.

(Add this term to `CONTEXT.md` during implementation.)

## Module

`lib/run_record.py`:

- `RunRecord` — constructed with a `StateManager`; the only component that
  touches config keys. Stateless besides the reference; all durability,
  locking, atomic write, and corruption preservation stay in `StateManager`.
- `RunSummary` — frozen dataclass: typed view of run lifecycle
  (`current_phase`, completed steps with names and phases, errors) plus
  config-derived facts. Two ways in:
  - `RunRecord.summary()` — live path, from the bound `StateManager`.
  - `RunSummary.from_snapshot(dict)` — offline path, for historical state
    files read from disk (`show_state.py`, report writers). Tolerant of
    malformed snapshots the way `cli_outcomes.phase_report_from_state` is
    today (non-dict, missing keys, wrong types → empty/defaults, never raise).
- Supporting small frozen dataclasses where a handoff carries several facts
  (`HubFacts`, `ManagedClusterExpectation`).

## Interface — one named operation per cross-phase handoff

Each operation's docstring states writer, reader, and ordering contract.
Never-recorded reads return explicit typed defaults with documented meaning.

| # | Handoff | Writer → Reader | Operations (sketch) |
|---|---|---|---|
| 1 | Hub facts | CLI preflight → reports, phases | `record_hub_facts(primary_version, secondary_version, primary_has_observability, secondary_has_observability, primary_observability_detected)` · `hub_facts() -> HubFacts` |
| 2 | Managed-cluster expectation | CLI preflight → verification | `record_managed_cluster_expectation(names, count, mode)` · `managed_cluster_expectation() -> ManagedClusterExpectation` |
| 3 | Preflight results | CLI → reports | `record_preflight_results(results)` · exposed via `summary()` |
| 4 | Auto-import override | activation → finalization | `record_auto_import_override()` · `clear_auto_import_override()` · `auto_import_override_pending() -> bool` (False = no obligation) |
| 5 | Saved backup schedule | primary_prep → backup_schedule | `record_saved_backup_schedule(spec)` · `saved_backup_schedule() -> dict \| None` |
| 6 | Backup verification | finalization internal | `record_backup_watch_started(at)` · `record_new_backup(name)` · `new_backup() -> str \| None` |
| 7 | Restore archival | finalization → reports | `record_archived_restores(names)` |
| 8 | Pre-activation Velero restore | activation internal | existing `PRE_ACTIVATION_VELERO_MANAGED_CLUSTERS_RESTORE_NAME` key moves behind `RunRecord` |
| 9 | Resume summary | workflow → reports, show_state | `record_resume_start_phase(phase)` · exposed via `summary()` |

Operation names are the contract; exact parameter shapes are settled in the
implementation plan against the current call sites. The table's writer→reader
column is normative: it is the ordering documentation that today exists
nowhere.

Existing state-key constants (`lib/constants.py:185-207`) become private
details of `lib/run_record.py` where only the Python side uses them; constants
shared with the collection parity tests keep their current home and the parity
tests are untouched.

## Readers converge

- `lib/cli_outcomes.phase_report_from_state` and
  `lib/report_artifacts._summarize_state` consume `RunSummary` instead of
  re-parsing raw dicts.
- `show_state.py` consumes `RunSummary.from_snapshot()` and adopts
  `runtime_bootstrap.get_default_state_dir()` for state-dir resolution —
  removing the divergence. Behavioural change is deliberate and documented:
  `show_state` looks where the CLI writes, including the
  `ACM_SWITCHOVER_STATE_DIR` edge case.
- `StateManager` keeps its lifecycle methods (`set_phase`, `add_completed_step`,
  `add_error`, …) — those are the write path of the same schema and already
  named operations. Only the config-dict surface changes.

## Hard cut and seam lock

1. Migrate production call sites by handoff cluster (the 9 groups above).
2. Migrate tests off string keys to named operations.
3. Rename `set_config`/`get_config` → `_set_config`/`_get_config`. Callers
   after the cut: `RunRecord`, plus `PauseRegisterStore`/`ArgocdPauseRegister`,
   which keep their three constant keys via a narrow, documented allowance
   (they are the register's own seam — out of scope here, converging under
   candidate 4 / issue #208).
4. New guardrail test (repo pattern, cf. `tests/test_documentation_guardrails.py`):
   forbids `_set_config`/`_get_config`/raw `state["config"]` access outside
   `lib/utils.py`, `lib/run_record.py`, and the two register modules.

Any straggler breaks loudly at test time — never a silent default at runtime.

## Error handling

- Unknown keys become impossible by construction (no string parameter).
- Never-recorded handoffs: typed defaults with documented semantics per
  operation (see table).
- Malformed historical snapshots: `RunSummary.from_snapshot` degrades to
  defaults, matching today's tolerant readers; it never raises on shape.
- Corruption, locking, IO: unchanged — `StateManager`'s existing behaviour.

### Tolerance changes vs. pre-RunRecord readers

The on-disk schema is unchanged and every well-formed state file behaves
identically. These are the deltas for *malformed* inputs, recorded so the
convergence is not mistaken for a pure no-op. None is exercised by the suite;
all were verified against the shipped code by inspection or reproduction.

**Malformed report inputs now degrade instead of losing the artifact.** The
report writers wrap their work in a broad `except`, so a raise anywhere inside
previously discarded the whole diagnostic artifact. Five cases in
`cli_outcomes.phase_report_from_state` / `report_artifacts.build_operation_report`:

1. Completed step with a non-`str` `name` that carries a valid recorded phase:
   the raw value used to be appended to `steps`; now `""` is (value degraded in
   place, artifact unaffected).
2. Completed step with a non-`str` `name` and no valid recorded phase:
   `fallback_phase_for_step(name)` raised `AttributeError` and the whole report
   was skipped; now the step is skipped and the report is written.
3. Completed step with an unhashable `phase` (list/dict): the frozenset
   membership test raised `TypeError` and the report was lost; now the phase
   degrades to `None` and takes the fallback path.
4. Non-dict *entry* in `config["preflight_results"]`:
   `_normalise_validation_result` raised and the report was lost; now the entry
   is skipped. (This does **not** extend to a non-dict `config`, which still
   raises in the pause-register read.)
5. Truthy non-list `config["preflight_results"]` (e.g. a string): iteration used
   to hand a fragment to `_normalise_validation_result` and raise; now
   `from_snapshot` yields `()` and the results section is silently empty.

**One case where the new code loses data the old code kept.** A `config` that is
a `Mapping` but not a `dict` (e.g. `MappingProxyType`, or any custom Mapping)
used to yield its `preflight_results` via `config.get(...)`;
`RunSummary.from_snapshot` does `if not isinstance(config, dict): config = {}`
and returns `()`. The same applies to a non-`dict` Mapping snapshot as a whole.
Theoretical for JSON-loaded snapshots (`json.load` only produces `dict`), but it
is a genuine narrowing — consider widening `from_snapshot` to
`collections.abc.Mapping` if a non-`dict` Mapping ever reaches it.

**`str()` coercion of hub versions.** `RunRecord.hub_facts()` coerces the version
fields with `str()`, so a hand-edited state file holding a numeric version now
stringifies (`2.14` float → `"2.14"`) where the old raw read passed the value
through. Production writers always persist strings, so this is unreachable in
practice.

**`show_state` state-dir validation posture (open question).** `show_state.py`
now shares `runtime_bootstrap.get_default_state_dir()` with the CLI, which fixed
the divergence that sent the viewer to a different directory than the writer.
The residue: the CLI's `validate_args` aborts on an unsafe
`ACM_SWITCHOVER_STATE_DIR` when `--state-file` is absent, while the viewer does
not validate at all — it just resolves and reads. Which posture is canonical
(viewer should also refuse unsafe values, or reading is deliberately permissive)
is not settled here.

## Testing

- `RunRecord`/`RunSummary` unit tests exercise the public interface only —
  no reaching past the seam, no raw key literals in new tests.
- Migrated existing tests: setup via named operations; assertions on raw JSON
  remain only in `StateManager`'s own persistence tests and one
  round-trip test proving interface-only persistence (same keys on disk
  before/after — a written-then-read state file from the previous release
  loads identically).
- Guardrail test as above.
- Full suite green at every plan checkpoint.

## Out of scope (deliberate)

- Pause-register keys and the register/store seam (architecture review
  candidate 4; issues #208/#210).
- Collection `module_utils/checkpoint.py` parity — file a follow-up issue at
  implementation time.
- Dry-run execution-mode seam (candidate 2).
- Any change to the state file format or location defaults beyond the
  `show_state` resolution fix.

## Rejected alternatives

- **Named methods on `StateManager`** — no new seam, but grows a ~30-method
  interface to ~45 and `lib/utils.py` past 1100 lines; the key vocabulary and
  the durability machinery stay tangled in one module.
- **Typed schema dataclass, whole-record serialization** — typed reads, but
  every write becomes load-modify-store and ordering invariants drift back to
  callers; also changes the persisted schema.
- **Deprecation window for `set_config`/`get_config`** — two vocabularies
  coexisting is the ambiguity this design removes; the repo just deleted a
  compat shim that outlived its removal date.
- **New persisted `run_record` section with migration** — cleaner file, but
  every historical state file needs migration and the collection state format
  diverges, creating a new parity surface.
