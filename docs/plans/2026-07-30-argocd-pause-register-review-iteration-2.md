# Review Feedback — Iteration 2

Combined verdict from three reviewers on the ArgocdPauseRegister change (branch `argocd-pause-register-ansible`, commits `d493fd92..a7f00d4f`).

- Object Oriented Design: **CHANGES_REQUESTED** — 1 critical, 5 warnings, 3 suggestions
- Clean Architecture: **CHANGES_REQUESTED** — 0 critical, 3 warnings, 4 suggestions
- API Design: **CHANGES_REQUESTED** — 1 critical, 6 warnings, 2 suggestions

Three reviewers converged independently on the same two root causes: `pause_hubs` returns an
untyped tuple whose meaning changes per exit path (causing a false operator log on the very
path ADR-0001 introduced), and callers still bypass the register to read its state keys.

## IN SCOPE for this iteration

### F1 — `pause_hubs()` returns an overloaded tuple; operator log is wrong on the preserve path
**(API-001 CRITICAL, OO-006 WARNING, CA-003 WARNING — highest priority, this is a real regression)**

`pause_hubs -> Tuple[List[Dict[str, Any]], int]`. The list means four different things depending
on the exit taken: pre-existing entries on the no-CRD preserve path (`argocd_register.py:232`),
empty after a clear (`:236`), the whole register on the blocker exit (`:270`), the post-pause
register (`:378`). In dry-run it is an unpersisted hypothetical. The int is `len(pause_blockers)`
on one path and `pause_failures` on another, so callers cannot distinguish "an ApplicationSet
owns this app, we refused" from "the patch failed".

Both callers then log `len(paused_apps)` as this run's outcome:
- `modules/primary_prep.py:128-132` — `"Argo CD: %d Application(s) paused (run_id=%s)"`
- `acm_switchover.py:499-503` — `"%d Application(s) paused on secondary hub"`

On the ADR-0001 preserve path the tool therefore announces "N Application(s) paused" for a run
that paused nothing and never reached a hub.

**Fix:** return a `PauseSummary` dataclass mirroring the existing `ResumeSummary` idiom, with the
distinctions callers actually need:

```python
@dataclass
class PauseSummary:
    """Aggregated result of one pause_hubs() run."""

    newly_paused: int = 0
    already_paused: int = 0
    recovered: int = 0
    failed: int = 0
    blocked: int = 0
    applications_crd_visible: bool = True
    run_id: Optional[str] = None
```

Do not return the entry list at all — neither caller uses it for anything but `len()`. Update both
call sites to report `summary.newly_paused` (so the preserve path correctly reports 0) and to
distinguish `summary.blocked` from `summary.failed` in the error they raise.

### F2 — Callers bypass the register and read its state keys directly
**(CA-002 WARNING, OO-005 WARNING)**

`status()` was added precisely so this would stop, but no pause call site uses it:
- `modules/primary_prep.py:126` — `self.state.get_config("argocd_run_id")` — a **bare string
  literal**, not even the constant.
- `acm_switchover.py:496` — `state.get_config(STATE_KEY_ARGOCD_RUN_ID)`.
- `lib/report_artifacts.py:119-124` — reads bare literals `"argocd_run_id"` and
  `"argocd_paused_apps"`, then reports `"paused": len(paused_apps)`.

The `report_artifacts.py` case has operator-visible consequence: its count includes provisional
`pause_applied=False` entries, whereas `status().paused_count` filters through `_is_pause_applied`.
The incident report and the register disagree about how many Applications are paused.

**Fix:** route all three through `ArgocdPauseRegister(state).status()` (`run_id` for the logs,
`paused_count` for the report artifact). `report_artifacts.py` currently takes a `config` dict —
adapt minimally; if it has no `StateManager` in scope, add a small register-owned helper it can
call rather than re-parsing raw keys. After this, `STATE_KEY_ARGOCD_RUN_ID` and
`STATE_KEY_ARGOCD_PAUSED_APPS` must have no importers outside `lib/argocd_register.py`.
The key *strings* stay unchanged — collection parity is unaffected.

### F3 — `pause_hubs()` does four separable jobs in ~175 lines
**(OO-001 CRITICAL, CA-005 SUGGESTION)**

Nesting reaches six levels with eight distinct exits. The four jobs change for different reasons
(topology support / the ADR / `PauseResult` states). Decompose along the seams the reviewer
identified, keeping `pause_hubs` as a ~25-line coordinator — **no behavior change**:

- `_discover(hubs) -> List[Tuple[client, label, discovery]]`
- `_handle_no_applications_crd() -> PauseSummary` (the ADR-0001 block, lines 222-236)
- `_collect_applications(discoveries) -> Tuple[apps_by_hub, blockers]` (lines 249-267)
- `_pause_application(client, hub_label, impact, paused_apps, run_id, summary) -> None` (lines 275-376)
- `_apply_pause_result(entry, result, paused_apps, hub, ns, name, summary) -> None` (the four-way
  branch, lines 335-376)

### F4 — Entry schema leaks out of the register
**(CA-001 WARNING, API-005 WARNING, OO-004 WARNING — take the cheap fix, not the full dataclass)**

`load_entries()` hands out raw dicts and `lib/argocd_resume.py` digs into them:
`_required_resume_roles` (:23-29) and `prepare_argocd_resume_clients` (:133-157) do
`entry.get(STATE_KEY_ARGOCD_PAUSED_APP_HUB)` plus an `isinstance(entry, dict)` guard the register
already applied.

**Fix (narrow):** add `ArgocdPauseRegister.paused_hub_roles() -> set[str]` returning the hub roles
present in applied entries. Change `prepare_argocd_resume_clients` / `_required_resume_roles` to
take `paused_hub_roles: set[str]` instead of `paused_apps: list[Any]`. Then rename `load_entries`
to `_load_entries` (private) — verified: its only external consumers are `argocd_resume.py:226`
and `:278`, both of which this change removes.

Do **not** introduce a full `PauseEntry` dataclass this iteration (see deferred).

### F5 — `resume()`'s `logger` parameter shadows the module logger
**(CA-006 SUGGESTION, API-003 WARNING)**

`lib/argocd_register.py:25` defines a module logger that `pause_hubs` and every private helper use.
`resume(self, primary, secondary, logger)` alone takes one positionally and shadows it for the whole
method body. It exists only because the deleted free function had that signature; both call sites
pass `acm_switchover` descendants, so it buys nothing.

**Fix:** drop the parameter, use the module logger. Update both call sites in `lib/argocd_resume.py`.

### F6 — `ResumeSummary` reports dry-run simulations as real restorations
**(API-007 WARNING)**

In dry-run, `resume()` increments `summary.restored` without touching a cluster while
`summary.remaining` still counts every entry. `run_argocd_resume_only` then logs
`"Restored %d and already resumed %d Application(s); %d remaining"` — self-contradictory.

**Fix:** add `dry_run: bool = False` to `ResumeSummary`, set it from `self.dry_run`, and have the
caller phrase the message accordingly (e.g. "Would restore N"). Consistent with ADR-0001's
"dry-run records nothing", the summary must not report work as done.

### F7 — Duplicated entry-mutation blocks
**(OO-003 WARNING)**

The "confirm this entry" block is copy-pasted at `:131-136`, `:297-300`, `:336-339`, `:357-361`, and
the copies **have already drifted** — `:336-339` omits `entry.pop("dry_run", None)` while the others
include it. The `_remove_pause_entry(...)` + `_persist_paused_apps(...)` pair appears five times.

**Fix:** add `_mark_confirmed(entry, original_sync_policy)`, `_mark_unknown(entry,
original_sync_policy, run_id)`, and `_forget(paused_apps, hub, ns, name)` (remove + persist), and
route all sites through them.

### F8 — Small cleanups
- **CA-004 SUGGESTION**: convert `clear_argocd_pause_state` to a private method `_clear(self)` with
  the `if self.dry_run: return` guard *inside* it (currently both call sites remember the guard
  externally; a third would be a silent dry-run state-corruption bug). Verified: no consumers
  outside `lib/argocd_register.py` and `tests/test_argocd_register.py:1069-1083` — update that test.
- **OO-009 SUGGESTION**: `_is_pause_applied`'s `not entry.get("dry_run", False)` fallback is dead —
  `load_entries` already drops truthy-`dry_run` entries, so it always evaluates `True`. Reduce to
  `entry.get("pause_applied", True)` and move the legacy explanation into the `_load_entries`
  docstring.
- **API-009 SUGGESTION**: make `dry_run` keyword-only: `def __init__(self, state, *, dry_run=False)`.
  Fix the positional call at `modules/primary_prep.py:119` (which also still names its local
  variable `coordinator` after the rename — rename to `register`).
- **API-008 SUGGESTION**: drop the unnecessary quotes on `-> "argocd_lib.ResumeSummary"`
  (`argocd_lib` is imported eagerly at line 13). Rename `ResumeSummary.remaining` to
  `remaining_in_register`, and `RegisterStatus.paused_count` to `confirmed_paused_count` — both
  current names under-specify what they count.

## DEFERRED — do NOT do this iteration

Record these as accepted trade-offs; they are structurally larger than the defects they address.

- **OO-002** (extract a `PauseRegisterStore` collaborator, dry-run as Null Object) — a real
  observation, but a second class plus a null-object hierarchy is disproportionate for this
  codebase's scale. F3's decomposition plus F8's `_clear` guard capture most of the practical risk.
- **OO-004 / API-005 full `PauseEntry` dataclass** — the narrow F4 fix removes the cross-module
  leak, which is the part that actually hurts. A full dataclass migration touches persistence,
  `argocd_resume`, `report_artifacts`, and ~20 test fixtures; worth its own change.
- **API-006** (`HubRole` StrEnum) — touches `lib/constants.py`, which is covered by
  `tests/test_argocd_constants_parity.py` and mirrored in the Ansible collection. Parity-sensitive;
  do it as a deliberate cross-form-factor change.
- **API-002** (full `pause_hubs`/`resume` naming and hub-shape symmetry) — F1 and F5 fix the
  substantive half. A rename of `pause_hubs` churns call sites and tests for cosmetics.
- **OO-007** (inject an `ops` collaborator for `lib/argocd` functions) — reviewer says it buys
  little without OO-002.
- **OO-008** (extract shared resume orchestration) — two instances, below the Rule of Three; the
  reviewer explicitly says not to introduce a Template Method here.

## Constraints (unchanged)

- Base `origin/ansible`; branch `argocd-pause-register-ansible`.
- `pause_autosync` / `resume_autosync` in `lib/argocd.py` are parity-bound with the Ansible
  collection (`ansible_collections/tomazb/acm_switchover/docs/coexistence.md`) — behavior must not
  change.
- State key *strings* unchanged (collection parity). Only who reads them changes.
- ADR-0001 invariant must still hold; `docs/adr/0001-pause-register-invariant.md` is binding.
- `black --line-length 120`; `./run_tests.sh` must pass.
- TDD: every behavioral fix (F1, F2, F6) needs a test asserting the corrected behavior — especially
  a test that the preserve path reports **zero** newly-paused applications.
