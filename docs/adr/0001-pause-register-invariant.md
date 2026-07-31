---
status: accepted
---

# Pause register holds unresolved resume obligations

The Argo CD pause register (`argocd_paused_apps` in the state file) records the
Applications this tool may have paused and has **not yet confirmed resumed** — its
unresolved resume obligations. An entry is not a claim that the Application is
paused right now; it is a claim that this tool owes a resume attempt because the
pause may have landed.

Entries carry a resolution state:

- **confirmed** (`pause_applied=True`) — the pause patch is known to have landed.
- **provisional** (`pause_applied=False`) — written before the patch was issued; the
  patch may or may not have landed.
- **unknown** (`pause_state="unknown"`, with `pause_run_id` recording the run) — the
  patch returned an ambiguous error, so it may have landed.

An entry leaves the register only when resume is **proven** complete — never merely
because it is unconfirmed. An unconfirmed entry is the only durable record of a pause
that may exist on the cluster, and of the `original_sync_policy` needed to undo it.

Three consequences, all deliberate:

- `resume()` removes each entry once resume is proven (patched, or the Application is
  observably resumed) — an empty register means "no outstanding obligations", not
  "resume finished". There is no in-state audit trail of resumed apps; logs and GitOps
  markers carry that history.
- Dry-run records nothing in the register (the old `argocd_pause_dry_run` key is gone).
  A register entry always means a real cluster mutation was attempted.
- When the Applications CRD is not visible but the register is non-empty, the register
  is preserved with a warning — never cleared. This holds for **every** non-empty
  register, including one containing only provisional or unknown entries.

## Correction

The original wording of this ADR said entries were "exactly the Applications currently
paused by this tool". That was wrong, and the code written against it inherited two
data-loss defects (external review findings 1 and 2 on PR #206):

- CRD-visibility loss cleared a register that held only provisional/unknown entries,
  because "not confirmed paused" read as "nothing to preserve".
- Resume forgot an entry whose pause marker was absent, without checking whether
  auto-sync had actually been restored — discarding the saved `original_sync_policy`
  for an Application that was still paused.

The register never held only confirmed pauses: `_mark_unknown` and the provisional
upsert deliberately persist unresolved outcomes for crash safety. Under the corrected
definition, discarding an unconfirmed entry is destroying an obligation, not tidying.
The three decisions above are unchanged; only the meaning of an entry is corrected.

## Considered options

- **Flag entries `resumed: true` instead of removing** — keeps in-state audit trail, but
  every reader must filter by flag and "is anything outstanding?" stops being a length
  check. The stale-register bug (resumed apps still marked paused, later re-pause
  skipped) came from exactly this ambiguity.
- **Hard-fail on non-empty register + missing CRD** — safer in theory, but a transient
  CRD-visibility blip would block an in-progress switchover.
- **Clear register when CRD absent** (previous behaviour) — destroyed the pause record
  on transient API failures, making `--argocd-resume-only` a silent no-op.
- **Keep only confirmed entries and drop unconfirmed ones** — the wording this ADR
  originally implied. Rejected: an unconfirmed entry is exactly the case where the
  cluster state is unknown and the record matters most.
