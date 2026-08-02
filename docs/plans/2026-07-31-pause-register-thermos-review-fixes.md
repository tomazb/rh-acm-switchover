# Pause Register — Thermos Review Fixes (PR #206)

External Thermos review requested changes on PR #206. All five findings independently verified
against the source before planning. Findings 1 and 2 are real data-loss defects introduced by this
branch; finding 1 was also found independently by CodeRabbit.

Work is split into **Round A** (correctness + semantics, T1-T4) and **Round B** (structure, T5).
Round A must land first — Round B restructures the code Round A corrects.

## Verified evidence

| # | Sev | Verified at | Status |
|---|-----|-------------|--------|
| 1 | High | `lib/argocd_register.py:565-580` — `_handle_no_applications_crd` gates on `_applied_entries(entries)`; a register holding only provisional/`pause_state="unknown"` entries has `applied == []` and falls through to `self._clear()` | CONFIRMED |
| 2 | High | `lib/argocd_register.py:328-331` — `elif is_resume_noop(result): self._forget(...)`; `is_resume_noop` is true on marker-missing alone (`lib/argocd.py:168-170`), and the no-marker branch (`lib/argocd.py:800-807`) never inspects `spec.syncPolicy` | CONFIRMED |
| 3 | Medium | `lib/argocd_register.py:76-85` docstring and `docs/adr/0001-pause-register-invariant.md` claim "entries are exactly the Applications currently paused", while `_mark_unknown` and the provisional upsert deliberately persist unresolved outcomes | CONFIRMED |
| 4 | Medium | `lib/argocd_register.py` 585 lines / `ArgocdPauseRegister` 510 lines; `tests/test_argocd_register.py` 1703 lines | CONFIRMED |
| 5 | Low | `resume()` persists `[]` via `_forget` then calls `_clear()`; a crash between leaves run-id set with an empty register, and `run_argocd_resume_only`'s precheck treats that as an error | CONFIRMED |

## Root cause

Findings 1 and 2 are both consequences of finding 3. I documented the register as "exactly the
Applications currently paused", then wrote code that persists *unresolved* outcomes for crash
safety. Under the wrong definition, discarding an unconfirmed entry looks like tidying; under the
correct one it is destroying the only record of an obligation. Fix the definition first (T1), and
both bugs become obvious.

---

## Round A

### T1 — Redefine the register as unresolved resume obligations (finding 3)

**Files:** `docs/adr/0001-pause-register-invariant.md`, `lib/argocd_register.py` (class docstring),
`CONTEXT.md`, `ansible_collections/tomazb/acm_switchover/docs/coexistence.md`

The register holds **unresolved resume obligations**: Applications this tool may have paused and has
not yet confirmed resumed. Entries carry a resolution state:

- **confirmed** (`pause_applied=True`) — the pause patch is known to have landed.
- **provisional** (`pause_applied=False`) — recorded before the patch; the patch may or may not have landed.
- **unknown** (`pause_state="unknown"`) — the patch returned an ambiguous error; it may have landed.

An entry leaves the register only when resume is **proven** complete — never because it is
unconfirmed. ADR-0001 is unmerged (this PR), so correct its text in place and add a short
"Correction" note recording that the original "exactly paused" wording was wrong and why. Keep the
three original decisions (removal-on-success, dry-run records nothing, never cleared on CRD loss) —
they are unchanged; only the definition of what an entry *means* is corrected.

Do **not** introduce a typed state enum in Round A — that is `PauseEntry` work (deferred). Document
the three states precisely; T5 may revisit.

- [ ] Update the ADR, class docstring, `CONTEXT.md` "Pause register" term, and the coexistence
      "Pause register invariant" paragraph so all four agree.
- [ ] Commit: `docs: define the pause register as unresolved resume obligations (Thermos 3)`

### T2 — CRD loss must preserve every non-empty register (finding 1, High)

**Files:** `lib/argocd_register.py` (`_handle_no_applications_crd`), `tests/test_argocd_register.py`

Gate on **all sanitized entries**, not just applied ones. Clear only when the register is truly
empty. Report both counts so the operator knows what is being kept.

```python
def _handle_no_applications_crd(self) -> PauseSummary:
    """Preserve any non-empty register when the CRD is not visible; clear only a truly empty one.

    Entries are unresolved resume obligations (ADR-0001). A provisional or
    unknown entry means the pause may have landed, so discarding it destroys
    the only record needed to put the Application back.
    """
    entries = self._load_entries()
    run_id = self.state.get_config(STATE_KEY_ARGOCD_RUN_ID)
    if entries:
        confirmed = len(self._applied_entries(entries))
        logger.warning(
            "Argo CD Applications CRD not visible on any hub but the pause register holds %d "
            "unresolved entr(ies) (%d confirmed paused); keeping the register (see ADR-0001). "
            "Resume with --argocd-resume-only, or clear the state file manually if Argo CD was "
            "permanently removed.",
            len(entries),
            confirmed,
        )
        return PauseSummary(applications_crd_visible=False, run_id=run_id, dry_run=self.dry_run)
    logger.info("Argo CD Applications CRD not found on any hub; skipping Argo CD pause")
    self._clear()
    return PauseSummary(applications_crd_visible=False, run_id=None, dry_run=self.dry_run)
```

- [ ] **Failing tests first**, each asserting the register and run id survive:
      - register holding only a provisional entry (`pause_applied=False`)
      - register holding only an unknown entry (`pause_state="unknown"`, `pause_run_id` set)
      - mixed confirmed + unknown
      - still clears when genuinely empty (existing test must stay green)
- [ ] Implement; verify the existing `test_no_crd_preserves_nonempty_register` and
      `test_no_crd_clears_empty_register` still pass.
- [ ] Commit: `fix(argocd): preserve unresolved register entries on CRD loss (Thermos 1, High)`

### T3 — Missing marker is not proof of restored auto-sync (finding 2, High)

**Files:** `lib/argocd.py` (`ResumeResult`, `resume_autosync`), `lib/argocd_register.py` (`resume`),
`tests/test_argocd_register.py`, `tests/test_argocd.py`

`is_resume_noop` is true whenever the marker is absent. Two very different situations share that
skip reason:

1. marker absent **and auto-sync enabled** — genuinely resumed by someone else; forgetting is correct.
2. marker absent **and auto-sync still disabled** — the Application is still paused; forgetting
   destroys the saved `original_sync_policy` and the obligation to restore it.

`resume_autosync` already holds the live Application (`current`) at that point, so the observation
is free.

**Parity constraint:** `pause_autosync` / `resume_autosync` *behavior* is parity-bound with the
collection. Adding an **observational field** that records what the function already read changes no
patch decision, no marker semantics, and no cluster interaction — it is additive and parity-safe.
Do not alter any existing branch, return value, or patch condition. Re-run the collection unit
tests to confirm.

```python
@dataclass
class ResumeResult:
    """Result of resuming one Application."""

    namespace: str
    name: str
    restored: bool
    skip_reason: Optional[str] = None
    # Observation only, never a patch decision: whether the live Application had
    # auto-sync enabled when it was read. None means not observed (fetch failed).
    autosync_enabled: Optional[bool] = None
```

Populate it on the marker-missing and marker-mismatch returns from the `current` already in scope
(`is_autosync_enabled(current)`). Then, register-side:

```python
elif argocd_lib.is_resume_noop(result):
    if result.autosync_enabled:
        summary.already_resumed += 1
        self._forget(entries, hub, ns, name)
        logger.info("  Already resumed %s/%s on %s", ns, name, hub)
    else:
        summary.failed += 1
        logger.warning(
            "  %s/%s on %s: pause marker is gone but auto-sync is still disabled — the "
            "Application is still paused. Keeping the register entry; restore its sync policy "
            "manually or re-run --argocd-resume-only once the marker is understood.",
            ns, name, hub,
        )
```

`autosync_enabled is None` (unobserved) must take the **keep** branch — absence of evidence is not
proof of restoration.

- [ ] **Rewrite** `tests/test_argocd_register.py:1533 test_resume_removes_marker_missing_noop_entry`
      — it currently asserts the unsafe behavior. Split into two tests:
      - marker missing + auto-sync **enabled** → `already_resumed == 1`, entry removed (old behavior, still right)
      - marker missing + auto-sync **disabled** → `failed == 1`, entry and run id **retained**,
        `original_sync_policy` intact
      Both must be written to fail first against current code.
- [ ] Add a `lib/argocd.py` test that `resume_autosync` populates `autosync_enabled` on the
      marker-missing path without changing `restored` / `skip_reason`.
- [ ] Verify the marker-mismatch-with-automated cleanup path (which also returns MARKER_MISSING
      after clearing a stale marker) reports `autosync_enabled=True` so it still forgets correctly —
      this is the one case where marker-missing legitimately means resumed.
- [ ] Run `python -m pytest ansible_collections/ -q` to confirm collection parity is untouched.
- [ ] Commit: `fix(argocd): require proof of restored auto-sync before forgetting an entry (Thermos 2, High)`

### T4 — Crash-consistency window in final cleanup (finding 5, Low)

**Files:** `lib/argocd_resume.py` (`run_argocd_resume_only` precheck), `tests/test_main_argocd_resume.py`

`resume()` persists the emptied list, then `_clear()` removes the run id. A crash between the two
leaves run-id-set + empty-register, which the resume-only precheck currently reports as an error
("No Argo CD paused apps in state file"), so a *successful* cleanup exits non-zero forever.

Do **not** reorder the writes: clearing the run id first would leave entries with no run id, which
is strictly worse (unresumable). Instead treat run-id-plus-empty-register as **successful
idempotent cleanup**:

```python
status = register.status()
if status.run_id and not status.entry_count:
    logger.info("Argo CD pause register is already empty (run_id=%s); nothing to resume.", status.run_id)
    register._clear()   # finish the interrupted cleanup  [use a public method if T5 adds one]
    return True
if not status.run_id or not status.entry_count:
    logger.error("No Argo CD paused apps in state file (argocd_run_id or argocd_paused_apps missing).")
    return False
```

Prefer completing the interrupted cleanup over leaving the stale run id behind. If `_clear()` is
still private at this point, add a small public `finish_cleanup()` (or make T5's store expose it)
rather than reaching into the private method from another module.

- [ ] Failing test: state with a run id and an empty register → resume-only returns **True**, logs
      the idempotent-cleanup message, and the run id is cleared.
- [ ] Confirm the genuinely-empty case (no run id, no entries) still returns False.
- [ ] Commit: `fix(argocd): treat run-id-with-empty-register as completed cleanup (Thermos 5)`

### Round A gate

- [ ] `./run_tests.sh` green; `black --line-length 120 --check lib/ modules/ acm_switchover.py tests/`
- [ ] `python -m pytest ansible_collections/ -q` green (parity)
- [ ] Push; PR #206 updates automatically.

---

## Round B

### T5 — Split the register (finding 4, Medium)

**Only after Round A is green and pushed.** This is the deferred OO-002 from the internal review,
now required by an external maintainability threshold. Round A deliberately does not restructure, so
the correctness fixes stay reviewable in isolation.

**Target split** — `lib/argocd_register.py` (585 lines, 510-line class) becomes:

- `lib/argocd_register_store.py` — the durable-state codec/store: entry schema, `_sanitize_entries`
  (legacy migration), load/persist, entry find/upsert/remove/mark helpers, `RegisterStatus`,
  `status_from_state_config`, `_clear`/`finish_cleanup`, discovery-namespace persistence. Depends on
  `StateManager` and `lib/constants` only — **not** on `lib/argocd` or `lib/kube_client`. This also
  resolves the deferred CA-009 (report artifacts stop transitively importing the Kubernetes SDK).
- `lib/argocd_register.py` — `ArgocdPauseRegister`: cluster orchestration (discover, collect, pause,
  reconcile, apply result, resume) holding a store by composition. `PauseSummary` stays here.

Re-export whatever `lib/report_artifacts.py`, `lib/argocd_resume.py`, `modules/primary_prep.py` and
`acm_switchover.py` import so call sites change as little as possible; update imports where a
module genuinely belongs against the store.

**Test split** — `tests/test_argocd_register.py` (1703 lines) becomes:

- `tests/test_argocd_register_store.py` — state/status/migration/persistence
- `tests/test_argocd_register_pause.py` — pause, discovery, blockers, CRD paths
- `tests/test_argocd_register_resume.py` — resume, obligations, cleanup

Move tests verbatim where possible; do not rewrite assertions during the split.

- [ ] Split with **no behavior change** — every existing test must pass unmodified except for
      imports and file location.
- [ ] `./run_tests.sh` green; collection tests green.
- [ ] Commit separately from Round A: `refactor(argocd): split the pause register store from cluster orchestration (Thermos 4)`

## Constraints

- Branch `argocd-pause-register-ansible`, base `origin/ansible`. Add commits; never amend or force-push.
- `pause_autosync` / `resume_autosync` behavior unchanged (T3's field is observational and additive).
- State key strings unchanged.
- `black --line-length 120`; no Co-Authored-By trailers.
- Every High fix needs a test written to fail first against current code.
