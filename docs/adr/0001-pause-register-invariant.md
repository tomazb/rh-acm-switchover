---
status: accepted
---

# Pause register holds only currently-paused Applications

The Argo CD pause register (`argocd_paused_apps` in the state file) records exactly the
Applications this tool has paused and not yet resumed. Three consequences, all deliberate:

- `resume()` removes each entry on successful resume — an empty register means "nothing
  paused", not "resume finished". There is no in-state audit trail of resumed apps;
  logs and GitOps markers carry that history.
- Dry-run records nothing in the register (the old `argocd_pause_dry_run` key is gone).
  A register entry always means a real cluster mutation happened.
- When the Applications CRD is not visible but the register is non-empty, the register
  is preserved with a warning — never cleared. Entries leave the register only via
  successful resume.

## Considered options

- **Flag entries `resumed: true` instead of removing** — keeps in-state audit trail, but
  every reader must filter by flag and "is anything paused?" stops being a length check.
  The stale-register bug (resumed apps still marked paused, later re-pause skipped) came
  from exactly this ambiguity.
- **Hard-fail on non-empty register + missing CRD** — safer in theory, but a transient
  CRD-visibility blip would block an in-progress switchover.
- **Clear register when CRD absent** (previous behaviour) — destroyed the pause record
  on transient API failures, making `--argocd-resume-only` a silent no-op.
