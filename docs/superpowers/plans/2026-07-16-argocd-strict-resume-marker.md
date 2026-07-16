# Argo CD Strict Resume Marker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Realign Python and collection Argo CD resume with strict same-run
marker ownership at discovery and mutation time, plus truthful no-op semantics.

**Architecture:** Keep `resume_autosync()` as the single Python mutation
boundary. Remove its implicit foreign-marker cleanup branch so only an exact
run-ID match reaches the patch call, then make Python and collection patches
conditional on the Application `resourceVersion` observed with that marker.
Preserve missing-marker, fetch-error, and patch-error semantics, and document
the intentional retirement of historical cleanup rather than adding a second
automated mutation interface.

**Tech Stack:** Python, pytest, `unittest.mock`, Kubernetes custom-resource client boundary, Markdown operator documentation.

## Global Constraints

- Base branch is the latest `origin/ansible` at worktree creation.
- Do not modify `.worktrees/pbt-08`.
- Argo CD management remains dual-supported; Python must match the collection strict-resume contract.
- No new CLI flag, collection variable, RBAC permission, checkpoint field, or live API test.
- Do not modify `docs/ACM_SWITCHOVER_RUNBOOK.md` or `.claude/skills/**`.
- A no-op result is valid only when no cluster-visible mutation was attempted or applied.

---

### Task 1: Lock the strict marker contract with deterministic tests

**Files:**
- Modify: `tests/test_argocd.py`

**Interfaces:**
- Consumes: `lib.argocd.resume_autosync(client, namespace, name, original_sync_policy, run_id)` and `lib.argocd.is_resume_noop(result)`.
- Produces: regression tests proving exact targeting and mutation/result behavior for matching, missing, and foreign markers.

- [ ] **Step 1: Replace the historical cleanup expectation with foreign-marker safety tests**

Add tests equivalent to:

```python
@pytest.mark.parametrize("autosync", [None, {"prune": True}])
def test_foreign_marker_never_patches_or_restores(autosync):
    client = MagicMock()
    sync_policy = {} if autosync is None else {"automated": autosync}
    client.get_custom_resource.return_value = {
        "metadata": {
            "namespace": "team-argocd",
            "name": "acm-policy-app",
            "resourceVersion": "500",
            "annotations": {argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: "foreign-run"},
        },
        "spec": {"syncPolicy": sync_policy},
    }

    result = argocd_lib.resume_autosync(
        client,
        "team-argocd",
        "acm-policy-app",
        {"automated": {"selfHeal": True}},
        "current-run",
    )

    assert result.skip_reason == argocd_lib.RESUME_SKIP_REASON_MARKER_MISMATCH
    assert argocd_lib.is_resume_noop(result) is False
    client.patch_custom_resource.assert_not_called()
```

Extend the same-run test to assert both `get_custom_resource` and
`patch_custom_resource` use `namespace="team-argocd"` and
`name="acm-policy-app"`. Keep the missing-marker test asserting zero patches
and add an explicit true no-op assertion.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python -m pytest tests/test_argocd.py::TestResumeAutosync -q
```

Expected: the foreign-marker/auto-sync-enabled case fails because
`patch_custom_resource` is called and the result is classified as marker
missing/no-op. Matching and missing-marker cases remain green.

- [ ] **Step 3: Confirm the failure is behavioral, not mock-only**

Inspect the recorded patch arguments and verify the failing call deletes only
the pause annotation on the exact generated Application. This proves the test
observes the real `resume_autosync()` branch while mocking only the external
Kubernetes client.

### Task 2: Remove implicit cleanup and restore truthful result semantics

**Files:**
- Modify: `lib/argocd.py`
- Test: `tests/test_argocd.py`

**Interfaces:**
- Consumes: the deterministic tests from Task 1.
- Produces: every non-empty foreign marker returns `RESUME_SKIP_REASON_MARKER_MISMATCH` without calling `patch_custom_resource`.

- [ ] **Step 1: Implement the minimal behavior change**

Delete the foreign-marker branch that inspects `spec.syncPolicy.automated`,
builds `cleanup_patch`, calls `patch_custom_resource`, and returns marker
missing. Leave this direct result after the missing-marker branch:

```python
return ResumeResult(
    namespace=namespace,
    name=name,
    restored=False,
    skip_reason=RESUME_SKIP_REASON_MARKER_MISMATCH,
)
```

- [ ] **Step 2: Run the focused tests and verify GREEN**

Run:

```bash
python -m pytest tests/test_argocd.py::TestResumeAutosync -q
```

Expected: all `TestResumeAutosync` tests pass.

- [ ] **Step 3: Run Python and collection parity regressions**

Run:

```bash
python -m pytest \
  tests/test_argocd.py \
  tests/test_argocd_constants_parity.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py \
  -q
```

Expected: all tests pass and the collection exact-run-ID guard remains intact.

### Task 2B: Make marker ownership conditional at mutation time

**Files:**
- Modify: `lib/argocd.py`
- Modify: `tests/test_argocd.py`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/resume.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py`

**Interfaces:**
- Consumes: the matching-marker live Application snapshot.
- Produces: conditional patches that Kubernetes rejects if the Application changes after discovery.

- [ ] **Step 1: Add deterministic RED tests**

Require the Python patch and collection patch definition to carry the observed
`metadata.resourceVersion`. Simulate a Python `409 Conflict` and assert the
result is actionable and false for `is_resume_noop()`. Assert a matching marker
without `resourceVersion` fails before the Python patch call.

- [ ] **Step 2: Add optimistic-concurrency preconditions in both form factors**

Include the observed `resourceVersion` in the Python merge patch and the
collection `kubernetes.core.k8s` patch definition. Do not retry 409 conflicts
inside resume; the caller must rediscover live ownership before another patch.

- [ ] **Step 3: Run focused tests and verify GREEN**

Run the Python resume test class and collection resume contract tests together.
Expected: matching stable objects restore, stale versions fail closed, and no
conflict result is classified as a no-op.

### Task 3: Document the operational decision

**Files:**
- Modify: `docs/operations/usage.md`
- Modify: `docs/operations/quickref.md`
- Modify: `ansible_collections/tomazb/acm_switchover/docs/coexistence.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: the final strict resume contract from Task 2.
- Produces: operator-facing guidance that distinguishes same-run, missing, and foreign markers and records removal of implicit cleanup.

- [ ] **Step 1: Update detailed usage guidance**

Add a resume safety paragraph explaining:

```text
Resume patches an Application only when its paused-by annotation exactly
matches the persisted run ID. A missing marker is an idempotent no-op. A
different run ID is left untouched and reported as a mismatch, even when
auto-sync is already enabled. Inspect a confirmed stale marker before removing
it explicitly; strict resume does not delete unowned markers.
```

- [ ] **Step 2: Update the quick reference**

Replace the broad already-resumed no-op statement with the same concise marker
ownership distinction.

- [ ] **Step 3: Update the changelog**

Under `[Unreleased]` / `Fixed`, record that Python no longer deletes foreign
markers or reports a mutation attempt as a no-op, now matching collection
resume safety. Under `Changed` or the same fixed entry, state that implicit
stale-marker cleanup from commit `73dd6c33` is intentionally retired; confirmed
stale markers require explicit operator inspection/removal.

### Task 4: Verify, review, and publish the prerequisite

**Files:**
- Review all changed files.

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: a ready PR against `ansible`, independently validated at its exact head SHA.

- [ ] **Step 1: Run repository verification**

Run targeted tests, `./run_tests.sh`, CI-equivalent black/isort/mypy/bandit
checks, and `git diff --check`. Confirm protected files are absent from the
diff.

- [ ] **Step 2: Run resilience and code-quality reviews**

Audit dependency failure, mutation idempotency, debug/result semantics,
change/rollback safety, and complexity tax. Run CodeRabbit against
`origin/ansible`, resolve validated critical/warning findings, and rerun until
clean.

- [ ] **Step 3: Commit and publish**

Commit intentionally without AI-attribution trailers, push
`fix/argocd-strict-resume-marker-contract`, and open a ready PR targeting
`ansible` that links issue #173 and PBT-08 #143.

- [ ] **Step 4: Independently validate**

Use a fresh clean worktree. Record the PR head SHA and `origin/ansible` SHA,
verify scope/parity/protected-file boundaries, reproduce tests, and issue an
independent verdict. Do not push from the validator worktree.

- [ ] **Step 5: Merge gate for PBT-08**

Resolve current-head feedback and CI, obtain a current-head validator verdict,
merge only when authorized and green, fetch `origin/ansible`, and prove the
merged fix is present before recreating or rebasing PBT-08.
