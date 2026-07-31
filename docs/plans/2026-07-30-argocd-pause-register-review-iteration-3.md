# Review Feedback — Iteration 3 (final)

Round-2 verdicts on the ArgocdPauseRegister change (branch `argocd-pause-register-ansible`):

- Object Oriented Design: **APPROVED** — 0 critical, 1 warning (OO-010), 5 suggestions
- Clean Architecture: **APPROVED** — 0 critical, 1 warning (CA-003 residual), 3 suggestions
- API Design: **CHANGES_REQUESTED** — 1 critical (API-001), 3 warnings, 3 suggestions

All three reviewers independently identified **the same single defect** (OO-010 = CA-003 = API-001).
Iteration 1's structural findings are confirmed resolved: `pause_hubs` is a 24-line coordinator,
the overloaded tuple is gone, state keys have no importers outside the register, mutation blocks
are consolidated.

This is the final iteration. Scope is deliberately tight: the convergent defect, the honesty
asymmetry it pairs with, and cheap naming/typing corrections. Everything structural stays deferred.

## G1 — Callers re-read `status().run_id` instead of `summary.run_id`; dry-run log vanishes
**(API-001 CRITICAL, OO-010 WARNING, CA-003 WARNING — all three reviewers, independently verified)**

`modules/primary_prep.py:131-139` and `acm_switchover.py:503-511`:

```python
run_id = register.status().run_id
if run_id is not None:
    logger.info("Argo CD: %d Application(s) %s (run_id=%s). ...",
                summary.newly_paused, "would be paused" if self.dry_run else "paused", run_id)
```

`pause_hubs` persists the run id only when not in dry-run (`lib/argocd_register.py:316-317`) — correct
per ADR-0001. Consequence: on a dry-run against clean state, `status().run_id` is `None`, the guard
short-circuits, and the tool prints **nothing at all** about the pause it would perform, while
`summary.run_id` held the generated id the whole time. The `"would be paused"` branch is effectively
unreachable unless a prior real run left a run id behind, and no test covers it.

This is the same root cause as the original API-001 — the caller reports a value the run did not
produce — and it reintroduces the two-sources-of-truth pattern OO-005/F2 just removed, one line
after the object that already answers the question.

**Fix:** use `summary.run_id` at both call sites; delete the `register.status()` call from the pause
path entirely (`status()` is a resume-side state query, not a run-outcome report). Keep an
`is not None` guard keyed on `summary.run_id` so the CRD-absent-and-cleared path stays quiet.

**Test (required):** a dry-run pause against empty state emits the "would be paused" line with the
generated run id. Grep confirms no current test exercises that string.

## G2 — `PauseSummary` lacks the `dry_run` field its sibling `ResumeSummary` has
**(API-007 WARNING, OO-014 SUGGESTION)**

F6 added `dry_run` to `ResumeSummary` so the result is self-describing, and `lib/argocd_resume.py`
phrases its message from `summary.dry_run`. `PauseSummary` did not get the same treatment, so both
pause callers re-derive the mode from their own flag (`self.dry_run`, `getattr(args, "dry_run", …)`).
Two idioms for one concern in one subsystem, and in dry-run `_apply_pause_result` increments
`newly_paused` for applications never patched — so every consumer must already know the register's
mode to read its central counter.

**Fix:** add `dry_run: bool = False` to `PauseSummary`, set it from `self.dry_run` in `pause_hubs`,
and have both callers phrase from `summary.dry_run`. Pairs naturally with G1 — same two lines.

**Also (API-007 second half):** the dry-run resume message is still numerically self-contradictory —
entries are never removed in dry-run, so `restored == remaining_in_register` and the log reads
"Would restore 3 … 3 would remain". Either omit the remaining count in dry-run or report the
projected figure (`entry_count - restored - already_resumed`).

## G3 — `RegisterStatus` assembly duplicated three times
**(OO-013 SUGGESTION, CA-008 SUGGESTION, API-010 WARNING)**

The applied-entry filter plus the `RegisterStatus(...)` construction appears in `status()` (:111-117),
`status_from_config()` (:121-127), and the filter again in `_handle_no_applications_crd()` (:524-525).
The *rules* are correctly shared (both go through `_sanitize_entries`/`_is_pause_applied`, which is
what closed CA-002) — it is the assembly that is copy-pasted. A fourth `RegisterStatus` field means
editing both sites, and divergence there silently reopens CA-002.

**Fix:** add `@staticmethod _applied(entries)` and a private `_status(entries, run_id) -> RegisterStatus`;
route `status()`, `status_from_config()` and `_handle_no_applications_crd()` through them.

**Also rename** `status_from_config` → `status_from_state_snapshot(snapshot: Mapping[str, Any])`.
"config" is the vaguest available word for "the persisted state-file config mapping" and reads as
"status derived from configuration settings", which is not what it does. Update `lib/report_artifacts.py`.

## G4 — Typing and naming corrections
- **API-005 / OO-014 / CA-010**: `paused_hub_roles(self) -> set` (:95) is bare while its only consumer
  declares `set[str]` (`argocd_resume.py:18,131`). Annotate `-> Set[str]` (module uses `typing` style,
  no `from __future__ import annotations`). Add a docstring line stating the roles come from **all**
  entries, not only confirmed-applied ones — that is intentional (it matches what `resume()` iterates,
  and over-approximating the identity-validation set is the safe direction) but unreadable from the name.
- **CA-010 / API-011**: `RegisterStatus.entry_count: int = 0` is the only defaulted field and the
  default is always wrong when entries exist — it permits `RegisterStatus(confirmed_paused_count=5,
  run_id="r")` with `entry_count=0`, violating the type's own invariant, and callers now gate resume
  on `entry_count` so a spurious zero means "nothing to resume". Drop the default; fix construction sites.
- **OO-011 / API-013**: `_mark_confirmed(entry, sync_policy, applied=False)` at :203 reads as a
  contradiction — it writes the *provisional* entry. Body is correct; only the name misleads. Rename to
  `_record_pause_state(entry, original_sync_policy, *, applied)`.
- **OO-004 residual**: `_pause_entry_matches` (:132) reads `entry.get("hub")` as a literal while
  `resume` (:264) and `paused_hub_roles` (:98) use `STATE_KEY_ARGOCD_PAUSED_APP_HUB` for the same field.
  Use the constant.
- **API-012**: `PauseSummary.recovered` and `already_paused` are undocumented — a reader cannot tell
  `recovered` ("register claimed a pause, live marker confirmed it") from `already_paused` ("confirmed
  entry, auto-sync already off"). One docstring line per counter.
- **OO-012 (minimum only)**: `summary` is keyword-only in `_reconcile_recorded_entry` but positional in
  `_apply_pause_result` — easy to transpose at a call site. Make it keyword-only in both.

## DEFERRED — do NOT do this iteration

Carry forward from iteration 2, plus two new ones. All are accepted trade-offs.

- **OO-002** (`PauseRegisterStore` split), **OO-004/API-005 full `PauseEntry` dataclass**,
  **API-006** (`HubRole` StrEnum — parity-sensitive), **API-002** (pause/resume naming symmetry),
  **OO-007** (ops injection seam), **OO-008** (shared resume orchestration) — unchanged rationale.
- **OO-012 full `_AppRef` parameter object** — the data clump `(paused_apps, hub, ns, name, run_id,
  summary)` threading through the new helpers is real (6-9 params each), but it is the same internal
  value-object work as the deferred `PauseEntry` and belongs with it. Take the keyword-only fix now.
- **CA-009** (`report_artifacts` now transitively imports the Kubernetes SDK via the register, so the
  artifact writer can no longer be imported without the cluster SDK) — the reviewer marks it optional;
  the dependency *direction* is correct and runtime cost is nil. Revisit only if the artifact writer
  needs to run in an SDK-free context. Do **not** fix by moving parsing back into `report_artifacts` —
  that would undo CA-002.

## Constraints (unchanged)

- Base `origin/ansible`; branch `argocd-pause-register-ansible`. Add commits, never amend/rebase.
- `pause_autosync` / `resume_autosync` behavior is parity-bound with the Ansible collection
  (`ansible_collections/tomazb/acm_switchover/docs/coexistence.md`).
- State key *strings* unchanged.
- ADR-0001 invariant binding.
- `black --line-length 120`; `./run_tests.sh` must pass.
- TDD for G1 and G2 (both are behavioral).
