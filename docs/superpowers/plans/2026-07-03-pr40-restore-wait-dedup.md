# PR 40: Shared Restore-Deletion Wait Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One `wait_for_restore_deletion(...)` in `lib/waiter.py` replaces the near-verbatim polling methods in `modules/activation.py` and `modules/finalization.py` (R2-M3 + M2), byte-identical messages.

**Architecture:** Per the approved design (`docs/superpowers/specs/2026-07-03-pr40-restore-wait-dedup-design.md`): the helper takes the client, an explicit `dry_run` flag (each caller keeps its historical flag source), and a `where` display suffix; the two private methods become one-line delegates so existing `patch.object` seams keep working.

**Tech Stack:** Python 3, pytest, black/isort (line-length 120).

## Global Constraints

- Byte-identical log/description/error messages at both call sites (`where=""` for activation, `where=" on primary"` for finalization).
- `black --line-length 120` / `isort --profile black --line-length 120` on touched files.
- Base branch: `ansible` @ `d9c982f3`; PR branch `refactor/thermos-40-restore-wait-dedup`.

---

### Task 1: Red-first waiter tests

**Files:**
- Modify: `tests/test_waiter.py` (append)

**Interfaces:**
- Consumes (from Task 2): `wait_for_restore_deletion(client, restore_name, *, dry_run, timeout=RESTORE_WAIT_TIMEOUT, where="", logger=None) -> None` raising `lib.exceptions.FatalError` on timeout.

- [ ] **Step 1: Append the failing tests**

```python
class TestWaitForRestoreDeletion:
    def _client(self, side_effect):
        client = MagicMock()
        client.get_custom_resource.side_effect = side_effect
        return client

    def test_completes_when_restore_absent(self):
        from lib.waiter import wait_for_restore_deletion

        client = self._client([None])
        wait_for_restore_deletion(client, "restore-x", dry_run=False, timeout=5)
        client.get_custom_resource.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name="restore-x",
            namespace="open-cluster-management-backup",
        )

    def test_polls_until_absent(self, monkeypatch):
        from lib.waiter import wait_for_restore_deletion

        monkeypatch.setattr("lib.waiter.time.sleep", lambda _s: None)
        client = self._client([{"status": {"phase": "Deleting"}}, None])
        wait_for_restore_deletion(client, "restore-x", dry_run=False, timeout=30)
        assert client.get_custom_resource.call_count == 2

    def test_timeout_raises_fatal_error_with_where_suffix(self, monkeypatch):
        from lib.exceptions import FatalError
        from lib.waiter import wait_for_restore_deletion

        monkeypatch.setattr("lib.waiter.time.sleep", lambda _s: None)
        clock = iter(range(0, 10_000, 60))
        monkeypatch.setattr("lib.waiter.time.monotonic", lambda: next(clock))
        client = self._client(lambda **_kw: {"status": {"phase": "Deleting"}})
        with pytest.raises(FatalError, match=r"restore restore-x to be deleted on primary after 120s"):
            wait_for_restore_deletion(client, "restore-x", dry_run=False, timeout=120, where=" on primary")

    def test_dry_run_skips_polling(self):
        from lib.waiter import wait_for_restore_deletion

        client = self._client(AssertionError("must not poll in dry run"))
        wait_for_restore_deletion(client, "restore-x", dry_run=True)
        client.get_custom_resource.assert_not_called()
```

Check the top of `tests/test_waiter.py` for existing `MagicMock`/`pytest` imports and how existing tests fake time (`lib.waiter` may use `time.monotonic` or `time.time` — mirror whatever the existing wait_for_condition tests patch; adjust the timeout test to the same mechanism).

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_waiter.py -q -k RestoreDeletion`
Expected: FAIL — `ImportError: cannot import name 'wait_for_restore_deletion'`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_waiter.py
git commit -m "test: add red tests for shared restore-deletion wait"
```

### Task 2: Implement in lib/waiter.py and delegate

**Files:**
- Modify: `lib/waiter.py` (imports + new function, exactly the code in the design spec's Design section)
- Modify: `modules/activation.py:412-444` (delegate)
- Modify: `modules/finalization.py:1230-1259` (delegate)

- [ ] **Step 1: Add the function to `lib/waiter.py`**

Imports to add:

```python
from lib.constants import (
    BACKUP_NAMESPACE,
    CLUSTER_BACKUP_API_GROUP,
    CLUSTER_BACKUP_API_VERSION,
    RESTORE_FAST_POLL_INTERVAL,
    RESTORE_FAST_POLL_TIMEOUT,
    RESTORE_PLURAL,
    RESTORE_POLL_INTERVAL,
    RESTORE_WAIT_TIMEOUT,
)
from lib.exceptions import FatalError
```

then the `wait_for_restore_deletion` function exactly as in the design spec.

- [ ] **Step 2: Delegate in `modules/activation.py`**

```python
    def _wait_for_restore_deletion(self, restore_name: str, timeout: int = RESTORE_WAIT_TIMEOUT) -> None:
        """Wait until a restore resource is fully deleted."""
        wait_for_restore_deletion(
            self.secondary, restore_name, dry_run=self.secondary.dry_run, timeout=timeout, logger=logger
        )
```

Add `wait_for_restore_deletion` to activation's `lib.waiter` import; drop now-unused names (`WaitConditionResult`, `wait_for_condition`) only if nothing else in the file uses them (grep first).

- [ ] **Step 3: Delegate in `modules/finalization.py`**

```python
    def _wait_for_primary_restore_deletion(self, restore_name: str, timeout: int = RESTORE_WAIT_TIMEOUT) -> None:
        """Wait until a restore resource is fully deleted from the old primary hub."""
        wait_for_restore_deletion(
            self.primary, restore_name, dry_run=self.dry_run, timeout=timeout, where=" on primary", logger=logger
        )
```

Same import hygiene as Step 2.

- [ ] **Step 4: Run the suites**

Run: `python -m pytest tests/test_waiter.py tests/test_activation.py tests/test_finalization.py -q`
Expected: all PASS.

- [ ] **Step 5: Format and commit**

```bash
black --line-length 120 lib/waiter.py modules/activation.py modules/finalization.py tests/test_waiter.py
isort --profile black --line-length 120 lib/waiter.py modules/activation.py modules/finalization.py tests/test_waiter.py
git add -A
git commit -m "refactor: share the restore-deletion wait in lib/waiter.py (R2-M3, M2)"
```

### Task 3: Full verification, tracker update, draft PR

**Files:**
- Modify: `thermos-resolution-plan.md` (row PR 40)

- [ ] **Step 1: Full gate** — `./run_tests.sh`, expect PASS, record lane counts.

- [ ] **Step 2: Tracker + push + PR**

```bash
git add thermos-resolution-plan.md
git commit -m "docs: mark Thermos PR 40 ready for review in tracker"
git push -u origin refactor/thermos-40-restore-wait-dedup
gh pr create --draft --base ansible --title "Thermos PR 40: shared restore-deletion wait in lib/waiter.py (R2-M3, M2)" --body "<summary + verification evidence>"
```
