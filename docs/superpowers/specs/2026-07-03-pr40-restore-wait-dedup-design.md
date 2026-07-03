# PR 40 Design: Shared Restore-Deletion Wait in lib/waiter.py (R2-M3 + M2)

**Date:** 2026-07-03
**Findings:** `R2-M3` and the reprioritized `M2` from
[`docs/plans/2026-07-02-thermos-ansible-review-2-findings.md`](../../plans/2026-07-02-thermos-ansible-review-2-findings.md)
**Tracker row:** PR 40 in [`thermos-resolution-plan.md`](../../../thermos-resolution-plan.md)
**Branch:** `refactor/thermos-40-restore-wait-dedup`

## Problem

Verified at `ansible` @ `d9c982f3`:
`modules/activation.py:412-444` (`_wait_for_restore_deletion`, polling
`self.secondary`) and `modules/finalization.py:1230-1259`
(`_wait_for_primary_restore_deletion`, polling `self.primary`) are
near-verbatim ~30-line duplicates: dry-run gate → poll closure calling
`get_custom_resource` for the Restore and mapping absent →
`WaitConditionResult.complete`, present →
`pending(f"still present (phase=...)")` → `wait_for_condition` with the
same `RESTORE_*` timeout/interval constants → `FatalError` on timeout.
Review #2 confirmed this duplication is net-new code added within this
branch (the `M2` reprioritization), not legacy debt.

Differences, byte-diffed: the client polled, the dry-run flag source
(`self.secondary.dry_run` vs the Finalization instance's `self.dry_run`),
and an `" on primary"` suffix in the finalization variant's log,
description, and timeout message.

## Approaches considered

1. **Module-level `wait_for_restore_deletion(...)` in `lib/waiter.py`
   (chosen)** — the generic wait abstraction already lives there; the
   function takes the client, an explicit `dry_run` flag, and a `where`
   suffix so both call sites keep byte-identical messages. The two
   private methods stay as one-line delegates because
   `tests/test_activation.py` patches `_wait_for_restore_deletion` via
   `patch.object` (public patch seam preserved).
2. **Method on `KubeClient`** — couples a workflow-level policy (dry-run
   gate, FatalError, ACM Restore semantics) to the generic client.
   Rejected.
3. **Pass the client's own `dry_run`** inside the helper instead of an
   explicit parameter — finalization currently consults its instance
   flag, not the client's; silently switching the source risks behavior
   drift with mocked clients. Rejected in favor of the explicit
   parameter.

## Design

Add to `lib/waiter.py` (new imports: `BACKUP_NAMESPACE`,
`CLUSTER_BACKUP_API_GROUP`, `CLUSTER_BACKUP_API_VERSION`,
`RESTORE_PLURAL`, `RESTORE_WAIT_TIMEOUT`, `RESTORE_POLL_INTERVAL`,
`RESTORE_FAST_POLL_INTERVAL`, `RESTORE_FAST_POLL_TIMEOUT` from
`lib.constants`; `FatalError` from `lib.exceptions` — both acyclic):

```python
def wait_for_restore_deletion(
    client,
    restore_name: str,
    *,
    dry_run: bool,
    timeout: int = RESTORE_WAIT_TIMEOUT,
    where: str = "",
    logger: Optional[logging.Logger] = None,
) -> None:
    """Wait until an ACM Restore resource is fully deleted.

    where is a display suffix (e.g. " on primary") used in the dry-run log,
    wait description, and timeout error, matching the historical per-caller
    wording.
    """
    log = logger or logging.getLogger("acm_switchover")
    if dry_run:
        log.info("[DRY-RUN] Skipping wait for deletion of %s%s", restore_name, where)
        return

    def _poll_restore_deletion() -> WaitConditionResult:
        restore = client.get_custom_resource(
            group=CLUSTER_BACKUP_API_GROUP,
            version=CLUSTER_BACKUP_API_VERSION,
            plural=RESTORE_PLURAL,
            name=restore_name,
            namespace=BACKUP_NAMESPACE,
        )
        if not restore:
            return WaitConditionResult.complete("deleted")
        phase = restore.get("status", {}).get("phase", "unknown")
        return WaitConditionResult.pending(f"still present (phase={phase})")

    completed = wait_for_condition(
        f"deletion of restore {restore_name}{where}",
        _poll_restore_deletion,
        timeout=timeout,
        interval=RESTORE_POLL_INTERVAL,
        fast_interval=RESTORE_FAST_POLL_INTERVAL,
        fast_timeout=RESTORE_FAST_POLL_TIMEOUT,
        logger=log,
    )
    if not completed:
        raise FatalError(f"Timeout waiting for restore {restore_name} to be deleted{where} after {timeout}s")
```

Call sites become one-line delegates (patch seams preserved):

```python
# modules/activation.py
    def _wait_for_restore_deletion(self, restore_name: str, timeout: int = RESTORE_WAIT_TIMEOUT) -> None:
        """Wait until a restore resource is fully deleted."""
        wait_for_restore_deletion(
            self.secondary, restore_name, dry_run=self.secondary.dry_run, timeout=timeout, logger=logger
        )

# modules/finalization.py
    def _wait_for_primary_restore_deletion(self, restore_name: str, timeout: int = RESTORE_WAIT_TIMEOUT) -> None:
        """Wait until a restore resource is fully deleted from the old primary hub."""
        wait_for_restore_deletion(
            self.primary, restore_name, dry_run=self.dry_run, timeout=timeout, where=" on primary", logger=logger
        )
```

Dry-run message check: activation historically logged
`"[DRY-RUN] Skipping wait for deletion of %s"` and finalization
`"... of %s on primary"` — the `where` suffix reproduces both exactly.

One wording nuance: the historical finalization dry-run log said
`"of %s on primary"` (no leading space issue — `where=" on primary"`
matches); no message changes anywhere.

## Testing

Red-first unit tests in `tests/test_waiter.py` for the new function:
immediate deletion completes; present-then-absent completes; timeout
raises `FatalError` containing the `where` suffix; `dry_run=True` skips
polling entirely. Existing `tests/test_activation.py` /
`tests/test_finalization.py` suites characterize the delegating methods.

## Acceptance criteria

1. One restore-deletion polling implementation, in `lib/waiter.py`; the
   two module methods are one-line delegates.
2. New waiter tests pass; activation/finalization suites pass unchanged.
3. Touched-file `black`/`isort`, `git diff --check` clean; full
   `./run_tests.sh` passes.

## Assumptions (autonomous session)

Operator pre-approved via the tracker queue. The broader `M2`
waiter-unification theme (other bespoke poll loops, `R2-L1`) stays out of
scope — this slice covers exactly the two duplicate restore-deletion
methods the tracker row names.
