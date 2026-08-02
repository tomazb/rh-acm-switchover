# ArgocdPauseRegister Implementation Plan (retargeted to `ansible` branch)

> **Superseded on the invariant.** This plan states the register invariant as
> "entries = apps currently paused". That wording was wrong and produced two
> data-loss defects; see the Correction section of
> [`docs/adr/0001-pause-register-invariant.md`](../adr/0001-pause-register-invariant.md),
> which defines entries as **unresolved resume obligations** (confirmed,
> provisional, or unknown). The ADR is authoritative. This document is kept as
> the record of what was executed, not as a current statement of the contract.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish deepening the Argo CD pause register: rename `ArgoCDPauseCoordinator` → `ArgocdPauseRegister`, give it `resume()`/`status()`, fix the no-CRD clobber, enforce the ADR-0001 invariant (entries = apps currently paused; resume removes entries on success; dry-run records nothing).

**Architecture:** The `ansible` branch already extracted the pause seam (`lib/argocd_coordinator.py`) and resume glue (`lib/argocd_resume.py`). This plan evolves the coordinator into the register: per-entry write-back on resume, warn-and-preserve on missing CRD, zero state writes in dry-run, and deletion of the `argocd_pause_dry_run` key. Hub-identity validation and client resolution stay in `lib/argocd_resume.py` (runtime concern, not register concern); both resume entry points call `register.resume()` instead of the free `resume_recorded_applications`.

**Tech Stack:** Python 3.10+, pytest, black `--line-length 120`, isort.

## Global Constraints

- Base: `origin/ansible` (branch `argocd-pause-register-ansible`).
- `black --line-length 120` + isort on all touched `.py` files.
- Constants from `lib/constants.py` (`STATE_KEY_ARGOCD_*`); exceptions per `lib/exceptions.py` (`SwitchoverError`).
- Entry schema unchanged: `{hub, namespace, name, original_sync_policy, pause_applied[, pause_state, pause_run_id]}`. Legacy `dry_run: true` entries dropped on load.
- Parity contract: `ansible_collections/tomazb/acm_switchover/docs/coexistence.md` semantics are binding — missing marker = idempotent no-op; foreign marker left untouched; resourceVersion-conditional patches. Do not change `resume_autosync` / `pause_autosync` behaviour.
- Check `tests/test_argocd_constants_parity.py` after any constants change.
- Tests through the register interface: real `StateManager` on `tmp_path`, fake/`Mock` `KubeClient`. Existing suites to keep green: `tests/test_argocd_coordinator.py` (29), `tests/test_argocd_resume_helpers.py`, `tests/test_main_argocd_resume.py`, `tests/test_argocd.py`, `tests/test_primary_prep.py`.
- Full gate: `./run_tests.sh` (no E2E).
- Commits per task; no Co-Authored-By trailers.

## Design decisions carried over (grilling, 2026-07-30)

| Decision | Status on ansible branch |
| --- | --- |
| Whole lifecycle behind one seam | Pause done (coordinator); resume missing → this plan |
| Register invariant: entries = currently paused; remove on resume success | Missing → Task 3 |
| No-CRD: warn + preserve non-empty register | Missing (coordinator clears) → Task 2 |
| Register owns state keys | Done (STATE_KEY_* constants) |
| Both Python resume paths through register | Glue exists; re-point → Task 4 |
| Bash deprecation | Already shipped on this branch — no work |
| Dry-run records nothing; delete `argocd_pause_dry_run` | Missing → Task 5 |
| Best-effort pause + fail step | Done (coordinator) |
| Name `ArgocdPauseRegister` / "pause register" | Rename → Task 1 |

---

### Task 1: Rename to `ArgocdPauseRegister` + add `status()`

**Files:**
- Rename: `lib/argocd_coordinator.py` → `lib/argocd_register.py` (`git mv`)
- Modify: imports in `acm_switchover.py:42`, `modules/primary_prep.py:12`, `lib/argocd_resume.py:8`
- Rename: `tests/test_argocd_coordinator.py` → `tests/test_argocd_register.py` (update imports/patch targets inside)
- Check: grep repo-wide for `argocd_coordinator` and `ArgoCDPauseCoordinator` (incl. docs) and update every hit

**Interfaces:**
- Produces: `class ArgocdPauseRegister` (same ctor `(state, dry_run=False)`, same `pause_hubs`), plus:

```python
@dataclass
class RegisterStatus:
    """Snapshot of the pause register."""

    paused_count: int
    run_id: Optional[str]
```

```python
    def load_entries(self) -> List[Dict[str, Any]]:
        """Current register entries (deep copy); non-dict and legacy dry-run entries dropped."""
        raw = self.state.get_config(STATE_KEY_ARGOCD_PAUSED_APPS) or []
        return [copy.deepcopy(e) for e in raw if isinstance(e, dict) and not e.get("dry_run")]

    def status(self) -> RegisterStatus:
        applied = [e for e in self.load_entries() if self._is_pause_applied(e)]
        return RegisterStatus(paused_count=len(applied), run_id=self.state.get_config(STATE_KEY_ARGOCD_RUN_ID))
```

- Keep `clear_argocd_pause_state(state)` as module-level function in the new file (resume-on-failure still uses it).
- Internal load sites in `pause_hubs` switch to `self.load_entries()` (this is where legacy dry-run entries get dropped).

- [ ] **Step 1: Write failing tests** — in renamed `tests/test_argocd_register.py`, add `TestRegisterStatus`: empty register → `RegisterStatus(0, None)`; seeded state (2 applied + 1 `pause_applied=False` + 1 legacy `dry_run=True` + 1 string garbage) → `paused_count == 2`, run_id from state; `load_entries` returns copy (mutating result doesn't change state) and drops garbage/dry-run entries. Use real `StateManager(tmp_path)`.
- [ ] **Step 2: Run** `python -m pytest tests/test_argocd_register.py -v` — new tests FAIL (import error first: fix imports as part of rename), existing 29 must PASS after mechanical rename.
- [ ] **Step 3: Implement** — `git mv`, class rename, add `RegisterStatus`/`load_entries`/`status`, update all import sites.
- [ ] **Step 4: Run** `python -m pytest tests/test_argocd_register.py tests/test_primary_prep.py tests/test_main.py -v` — PASS.
- [ ] **Step 5: Commit** `git add -A && git commit -m "refactor: rename ArgoCDPauseCoordinator to ArgocdPauseRegister, add status()"`

---

### Task 2: No-CRD path — warn + preserve (ADR-0001)

**Files:**
- Modify: `lib/argocd_register.py` `pause_hubs` (the block currently at old lines 137-140)
- Test: `tests/test_argocd_register.py`

Replace:

```python
        if not any(discovery.has_applications_crd for _, _, discovery in discoveries):
            logger.info("Argo CD Applications CRD not found on any hub; skipping Argo CD pause")
            clear_argocd_pause_state(self.state)
            return [], 0
```

with:

```python
        if not any(discovery.has_applications_crd for _, _, discovery in discoveries):
            entries = self.load_entries()
            applied = [e for e in entries if self._is_pause_applied(e)]
            if applied:
                logger.warning(
                    "Argo CD Applications CRD not visible on any hub but %d app(s) recorded paused; "
                    "keeping pause register (see ADR-0001). Resume with --argocd-resume-only, or "
                    "clear the state file manually if Argo CD was permanently removed.",
                    len(applied),
                )
                return entries, 0
            logger.info("Argo CD Applications CRD not found on any hub; skipping Argo CD pause")
            clear_argocd_pause_state(self.state)
            return [], 0
```

(Empty register keeps the existing full clear — harmless cleanup of run_id/discovery leftovers.)

- [ ] **Step 1: Failing tests** — `test_no_crd_preserves_nonempty_register` (seed 1 applied entry + run_id; fake client with no CRD; assert entries survive in state, warning logged via `caplog`, failure count 0) and `test_no_crd_clears_empty_register` (no entries; assert run_id/discovery keys cleared). Fake client: `get_custom_resource` returns `None` for `customresourcedefinitions` lookups (match existing fixture style in the renamed test file — reuse its helpers).
- [ ] **Step 2: Run — new FAIL** (clobber still happens). Check whether an existing test asserts the old clearing behaviour with a non-empty register — if so, rewrite it to the new contract, citing ADR-0001 in its docstring.
- [ ] **Step 3: Implement** the replacement above.
- [ ] **Step 4: Run** `python -m pytest tests/test_argocd_register.py -v` — PASS.
- [ ] **Step 5: Commit** `git commit -m "fix: preserve non-empty pause register when Applications CRD not visible (ADR-0001)"`

---

### Task 3: `register.resume()` — per-entry removal on success

**Files:**
- Modify: `lib/argocd_register.py` (new method), `lib/argocd.py` (delete `resume_recorded_applications`, lines ~560-623)
- Test: `tests/test_argocd_register.py`; migrate any direct tests of `resume_recorded_applications` in `tests/test_argocd.py`

**Interfaces:**
- Consumes: `argocd_lib.resume_autosync`, `argocd_lib.is_resume_noop`, existing `ResumeSummary`.
- Produces (for Task 4): `ResumeSummary` gains `remaining: int = 0`;

```python
    def resume(
        self,
        primary: Optional[KubeClient],
        secondary: Optional[KubeClient],
        logger: logging.Logger,
    ) -> ResumeSummary:
        """
        Resume auto-sync for every registered Application.

        ADR-0001 invariant: restored and already-resumed entries are removed
        from the register immediately (persisted per entry); failures stay for
        retry.  When the register empties, all Argo CD pause state is cleared.
        """
```

Semantics (method body — implement exactly):

```python
        entries = self.load_entries()
        run_id = self.state.get_config(STATE_KEY_ARGOCD_RUN_ID)
        summary = argocd_lib.ResumeSummary()
        if not run_id or not entries:
            logger.info("No Argo CD paused apps in state; nothing to resume")
            return summary

        clients = {HUB_ROLE_PRIMARY: primary, HUB_ROLE_SECONDARY: secondary}
        for entry in list(entries):
            hub = entry.get(STATE_KEY_ARGOCD_PAUSED_APP_HUB)
            ns = entry.get("namespace")
            name = entry.get("name")
            original_sync_policy = entry.get("original_sync_policy")
            client = clients.get(hub)
            if not all([hub, ns, name, original_sync_policy is not None]) or client is None:
                summary.failed += 1
                logger.warning("  Skip entry (hub=%s, namespace=%s, name=%s): unusable record or no client", hub, ns, name)
                continue
            if self.dry_run:
                summary.restored += 1
                logger.info("  [DRY-RUN] Would resume Argo CD Application %s/%s on %s", ns, name, hub)
                continue
            result = argocd_lib.resume_autosync(client, ns, name, original_sync_policy, run_id)
            if result.restored:
                summary.restored += 1
                self._remove_pause_entry(entries, hub, ns, name)
                self._persist_paused_apps(entries)
                logger.info("  Resumed %s/%s on %s", ns, name, hub)
            elif argocd_lib.is_resume_noop(result):
                summary.already_resumed += 1
                self._remove_pause_entry(entries, hub, ns, name)
                self._persist_paused_apps(entries)
                logger.info("  Already resumed %s/%s on %s", ns, name, hub)
            else:
                summary.failed += 1
                logger.warning("  Failed %s/%s: %s", ns, name, result.skip_reason or "not restored")

        summary.remaining = len(entries)
        if not self.dry_run and not entries:
            clear_argocd_pause_state(self.state)
            logger.info("Argo CD pause register empty; cleared pause state.")
        return summary
```

Behaviour changes vs the deleted free function (intentional, note in commit body):
- `pause_applied=False` / `pause_state="unknown"` entries are **attempted**, not skipped-as-failed — `resume_autosync`'s marker check makes that a safe idempotent no-op (coexistence.md), and a `pause_state="unknown"` app may genuinely be paused.
- Legacy `dry_run` entries never reach resume (dropped by `load_entries`), replacing the old per-entry "pause was dry-run only" warning.
- Successful/no-op entries leave the register; empty register clears run_id + discovery namespaces.

Imports: `HUB_ROLE_PRIMARY`, `HUB_ROLE_SECONDARY` from `lib.constants` (already used in `argocd_resume.py`). Add `remaining: int = 0` to `ResumeSummary` in `lib/argocd.py`.

- [ ] **Step 1: Failing tests** — port the useful cases from any existing `resume_recorded_applications` tests plus new invariant tests: removal on success; removal on marker-missing no-op; failure keeps entry + `remaining`; empty-after-resume clears `STATE_KEY_ARGOCD_RUN_ID` and `STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES`; unconfirmed (`pause_applied=False`) entry is attempted (assert `resume_autosync` called; patch `lib.argocd.resume_autosync` is NOT allowed — instead fake the client's `get_custom_resource`/`patch_custom_resource` as in existing register tests); dry-run resume mutates nothing; unknown hub / missing client counts failed and stays.
- [ ] **Step 2: Run — FAIL** (`resume` missing).
- [ ] **Step 3: Implement** method; delete `resume_recorded_applications` from `lib/argocd.py`; fix any lingering references (grep).
- [ ] **Step 4: Run** `python -m pytest tests/test_argocd_register.py tests/test_argocd.py -v` — PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: ArgocdPauseRegister.resume() enforces removal-on-success invariant (ADR-0001)"`

---

### Task 4: Re-point both resume paths

**Files:**
- Modify: `lib/argocd_resume.py` — `run_argocd_resume_only` (lines ~259-275) and `attempt_argocd_resume_on_failure` (lines ~312-350)
- Test: `tests/test_argocd_resume_helpers.py`, `tests/test_main_argocd_resume.py`

`run_argocd_resume_only`: replace the `resume_recorded_applications` call with:

```python
    register = ArgocdPauseRegister(state, dry_run=getattr(args, "dry_run", False))
    summary = register.resume(resume_primary, resume_secondary, logger)
    logger.info(
        "Restored %d and already resumed %d Application(s); %d remaining in register.",
        summary.restored,
        summary.already_resumed,
        summary.remaining,
    )
    if summary.failed:
        logger.error("Argo CD auto-sync restore failed for %d Application(s).", summary.failed)
        return False
    return True
```

Keep the existing precheck but read through the register (`register.status()`) instead of raw keys, and delete the `STATE_KEY_ARGOCD_PAUSE_DRY_RUN` early-exit (Task 5 removes the key; a dry-run pause now leaves the register empty, so the "No Argo CD paused apps" branch covers it). The identity-validation call (`prepare_argocd_resume_clients`) stays exactly where it is — it still receives `paused_apps`; source that list from `register.load_entries()`.

`attempt_argocd_resume_on_failure`: same substitution; the manual `accounted_for` arithmetic and full-clear block collapse to:

```python
        summary = register.resume(resume_primary, resume_secondary, logger)
        logger.info(
            "Argo CD resume-on-failure: restored=%d, already_resumed=%d, failed=%d",
            summary.restored,
            summary.already_resumed,
            summary.failed,
        )
        if summary.failed or summary.remaining:
            logger.warning(
                "Argo CD resume-on-failure left %d Application(s) in the pause register. "
                "Use --argocd-resume-only to retry manually.",
                summary.remaining,
            )
            return
        state.clear_step_completed(STEP_PAUSE_ARGOCD_APPS)
        retry_phase = Phase.PREFLIGHT if getattr(args, "restore_only", False) else Phase.PRIMARY_PREP
        state.add_error(
            "Argo CD resume-on-failure completed; retry must re-run Argo CD pause before continuing.",
            phase=retry_phase.value,
        )
        logger.info("Argo CD resume-on-failure cleanup completed; durable pause state cleared.")
```

(`register.resume()` already cleared the pause state when the register emptied — the explicit `clear_argocd_pause_state` call goes away; keep the `STEP_PAUSE_ARGOCD_APPS` clear.)

- [ ] **Step 1: Update tests** — existing tests that patch `argocd_lib.resume_recorded_applications` switch to patching `ArgocdPauseRegister.resume` (patch target: the module where it's looked up, `lib.argocd_resume.ArgocdPauseRegister`). Add: resume-only integration-style test with real `StateManager` + fake client asserting the register is empty and run_id cleared after full success.
- [ ] **Step 2: Run — FAIL**, **Step 3: Implement**, **Step 4:** `python -m pytest tests/test_argocd_resume_helpers.py tests/test_main_argocd_resume.py tests/test_argocd_register.py -v` — PASS.
- [ ] **Step 5: Commit** `git commit -m "refactor: resume paths delegate to ArgocdPauseRegister.resume()"`

---

### Task 5: Dry-run records nothing; delete `argocd_pause_dry_run`

**Files:**
- Modify: `lib/argocd_register.py` (`pause_hubs`, `_upsert_pause_entry`, `clear_argocd_pause_state`), `lib/constants.py:204`, `lib/argocd_resume.py` (imports), `modules/primary_prep.py` (dry-run messaging if it branches on the key), any `show_state.py` rendering of the key
- Test: `tests/test_argocd_register.py`, `tests/test_argocd_constants_parity.py`, plus repo-wide grep `argocd_pause_dry_run`

`pause_hubs` dry-run contract: when `self.dry_run` is true —
- do NOT write `STATE_KEY_ARGOCD_RUN_ID`, `STATE_KEY_ARGOCD_PAUSE_DRY_RUN`, `STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES`, or any entry;
- still run discovery/listing/blocker checks and log `[DRY-RUN] Would pause ...` per eligible app (use `run_id_or_new(existing)` transiently for the `pause_autosync` call, which already no-ops via `client.dry_run`);
- return the would-pause list (unpersisted entries) and blocker/failure count so callers keep their reporting.

Implementation sketch: guard every `self.state.set_config(...)` / `self._persist_paused_apps(...)` / `self._persist_discovery_namespaces_by_hub(...)` in `pause_hubs` behind `if not self.dry_run`, and in `_upsert_pause_entry` drop the `entry["dry_run"] = True` branch entirely (entries are never persisted in dry-run, so the field is dead). Remove `STATE_KEY_ARGOCD_PAUSE_DRY_RUN` from `clear_argocd_pause_state`, `lib/constants.py`, and all imports. `_is_pause_applied` keeps its legacy-tolerant read (`entry.get("pause_applied", not entry.get("dry_run", False))`) for old state files.

- [ ] **Step 1: Failing tests** — `test_dry_run_pause_writes_no_state` (fake client with one automated app; `ArgocdPauseRegister(state, dry_run=True).pause_hubs(...)`; assert `state.get_config` returns None/empty for all four `STATE_KEY_ARGOCD_*` keys, would-pause list has 1 entry, `patch_custom_resource` not called) and `test_dry_run_pause_preserves_existing_real_state` (seed a real entry + run_id, dry-run pause, assert untouched).
- [ ] **Step 2: Run — FAIL.** Existing dry-run tests asserting entry persistence must be rewritten to the new contract (cite ADR-0001).
- [ ] **Step 3: Implement**; repo-wide `grep -rn argocd_pause_dry_run` must end at zero hits (code and tests; docs updated where mentioned).
- [ ] **Step 4: Run** `python -m pytest tests/test_argocd_register.py tests/test_argocd_constants_parity.py tests/test_primary_prep.py tests/test_main_argocd_resume.py -v` — PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: dry-run pause records nothing; drop argocd_pause_dry_run state key (ADR-0001)"`

---

### Task 6: Docs + parity + full gate

**Files:**
- Modify: `AGENTS.md` (Core Libraries: `argocd_register.py` line replacing `argocd_coordinator` mention if present; Key Patterns: remove `argocd_pause_dry_run` references)
- Modify: `ansible_collections/tomazb/acm_switchover/docs/coexistence.md` — add a short "Pause register invariant" paragraph: Python's `argocd_paused_apps` register holds exactly the currently-paused Applications; resume removes entries on success; dry-run records nothing; the register is never cleared on CRD-visibility loss (ADR-0001). Note the collection's checkpoint/cluster-as-truth model is the equivalent register and shares the marker-ownership rules already documented.
- Already in repo root: `CONTEXT.md`, `docs/adr/0001-pause-register-invariant.md` — commit them here if not yet committed.
- Modify: `docs/artifact-schema.md` equivalent references if they mention `argocd_pause_dry_run` (grep).

- [ ] **Step 1: Apply doc edits** (grep-driven).
- [ ] **Step 2: Full gate** — `./run_tests.sh`; `black --line-length 120 --check lib/ modules/ acm_switchover.py tests/`.
- [ ] **Step 3: Commit** `git commit -m "docs: record pause register invariant (ADR-0001) and parity notes"`

---

## Self-Review Notes

- All six grilling decisions mapped (table above); bash deprecation verified already shipped on `ansible` — no task.
- Deliberate deviation from grilling Q1/Q2 (recorded here): register lives in `lib/argocd_register.py` (renamed coordinator), not inside `lib/argocd.py`, and hub-identity/client-resolution glue stays in `lib/argocd_resume.py` — the branch already established those seams; forcing single-file layout would be churn without depth gain.
- Deliberate behaviour changes called out in Task 3 (unconfirmed entries attempted) and Task 5 (dry-run entries no longer persisted) — both consistent with coexistence.md marker-ownership rules.
- Do NOT modify `pause_autosync` / `resume_autosync` — parity-bound.
