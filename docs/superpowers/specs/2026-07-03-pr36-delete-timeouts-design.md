# PR 36 Design: Request Timeouts for Delete Calls on the PRIMARY_PREP Path (R2-H1)

**Date:** 2026-07-03
**Finding:** `R2-H1` from
[`docs/plans/2026-07-02-thermos-ansible-review-2-findings.md`](../../plans/2026-07-02-thermos-ansible-review-2-findings.md)
**Tracker row:** PR 36 in [`thermos-resolution-plan.md`](../../../thermos-resolution-plan.md)
**Branch:** `fix/thermos-36-delete-timeouts`

## Problem

Verified against source at `ansible` @ `de943b0c`:

- `lib/kube_client.py:502` (`delete_configmap`) and `:525` (`delete_pod`) call
  `delete_namespaced_config_map` / `delete_namespaced_pod` with **no request
  timeout**, unlike every sibling core_v1 call in the file, which passes
  `**self._request_timeout_kwargs()`. A hung API server blocks these calls
  indefinitely (tenacity retries only fire after the call returns/raises).
- `modules/primary_prep.py:189-195` — the ACM ≤2.11 BackupSchedule
  delete-based pause calls `delete_custom_resource(...)` without
  `timeout_seconds`, even though the method accepts it precisely to prevent
  hanging, and the equivalent delete sites in `modules/activation.py:289,774`
  and `modules/decommission.py:134,204,408` all pass
  `timeout_seconds=DELETE_REQUEST_TIMEOUT` (`lib/constants.py:90`, 30s).

Blast radius: an un-timed hang during PRIMARY_PREP stalls the whole
switchover with no operator-visible timeout. `delete_configmap`'s one caller
is `finalization.py:1570`; `delete_pod`'s only production caller is the e2e
failure-injection harness — both still deserve the client-default timeout.

## Approaches considered

1. **File-idiom fix (chosen)** — append `**self._request_timeout_kwargs()`
   to the two core_v1 delete calls (client default, per-instance
   `request_timeout`, 30s unless configured) and pass
   `timeout_seconds=DELETE_REQUEST_TIMEOUT` at the `primary_prep.py` call,
   matching the five existing delete sites. Minimal, consistent.
2. **Add `timeout_seconds` parameters to `delete_configmap`/`delete_pod`**
   mirroring `delete_custom_resource` — API flexibility with exactly one
   caller each; YAGNI. The client-level `request_timeout` already provides
   per-instance configurability.
3. **Repo-wide un-timed-call audit** — broader than this slice; R2-H1 is
   scoped to these three sites, and the review found no other un-timed
   mutation sites on critical paths.

## Design

- `lib/kube_client.py`:
  - `delete_configmap`: `self.core_v1.delete_namespaced_config_map(name=name,
    namespace=namespace, **self._request_timeout_kwargs())`
  - `delete_pod`: `self.core_v1.delete_namespaced_pod(name=name,
    namespace=namespace, **self._request_timeout_kwargs())`
- `modules/primary_prep.py` ACM ≤2.11 branch: add
  `timeout_seconds=DELETE_REQUEST_TIMEOUT` to the `delete_custom_resource`
  call; import the constant.

Timeout behavior on expiry: the kubernetes client raises a network/timeout
error, `is_retryable_error` classifies it retryable, tenacity retries up to
5 attempts with backoff, then the error propagates and fails the phase —
identical to the already-hardened delete sites. No new error-handling code.

### Tests (red-first)

- `tests/test_kube_client.py`: extend the existing delete tests to assert
  the mock was called with a `_request_timeout` kwarg equal to the client's
  `request_timeout` (both methods). Red before the fix (no kwarg passed).
- `tests/test_primary_prep.py`: extend the ACM 2.11 delete-path test to
  assert `delete_custom_resource` was called with
  `timeout_seconds=DELETE_REQUEST_TIMEOUT`. Red before the fix.

## Interaction with PR 34

PR 34 (`fix/thermos-34-managed-cluster-constant`, #127) rewrites the
`group=`/`version=`/`plural=` lines of the same `primary_prep.py` call to
constants. Both branch from the same `ansible` base; whichever merges second
resolves a trivial adjacent-line conflict at that one call site. No semantic
interaction.

## Acceptance criteria

1. Both core_v1 delete methods pass `_request_timeout`; the
   `primary_prep.py` delete passes `timeout_seconds=DELETE_REQUEST_TIMEOUT`.
2. New/extended tests pass; `tests/test_kube_client.py`,
   `tests/test_primary_prep.py` suites pass.
3. Touched-file `black`/`isort` (line-length 120), `git diff --check` clean;
   full `./run_tests.sh` passes.

## Assumptions (autonomous session)

Operator pre-approved this slice via the tracker queue; design gate
satisfied by this spec. Client-default timeout (30s) chosen for the two
core_v1 deletes rather than `DELETE_REQUEST_TIMEOUT` because that is what
every other core_v1 call in the file uses; the two values are currently both
30 seconds.
