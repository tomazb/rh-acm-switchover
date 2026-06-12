# Klusterlet Remediation Changed-Flag Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `remediate_one_cluster()` report `changed=True` when an unexpected exception escapes to its outer handler after the bootstrap secret was already mutated.

**Architecture:** Single-function fix in the klusterlet module_utils: hoist the `changed` accumulator above the outer `try` so the outer `except` can include it in the failure result. One new regression test injects a fault between the secret apply and the deployment restart to pin the contract.

**Tech Stack:** Python (Ansible collection module_utils), pytest, black `--line-length 120`.

---

## Findings validation summary (input to this plan)

From `graphify-out/BUG_IMPACT_REPORT.md` triage, re-validated in this worktree:

1. **Confirmed (latent severity):** `remediate_one_cluster` outer `except`
   (`ansible_collections/tomazb/acm_switchover/plugins/module_utils/klusterlet.py:574`)
   returns a result without `changed`, defaulting to `False`, even when the
   bootstrap secret patch/create already succeeded (`changed = True` is scoped
   inside the `try`). Today no normal control path raises between the mutation
   and the return — every API call has its own handler — so the bug is only
   reachable via an injected fault (e.g. clock failure at the
   `datetime.now()` call building the restart annotation). It is a contract
   violation worth hardening, not an active production bug. RED test written
   and observed failing on `assert result["results"][0]["changed"] is True`.
2. **Downgraded — no action:** "thin coverage of `remediate_klusterlets`".
   `test_acm_klusterlet_modules.py` holds 20+ remediation tests (check mode,
   skip paths, patch/create/conflict retry, restart failure, strict mode,
   worker bounds, future timeouts). The graph's single-test-file signal
   undercounted because its call-edge extraction misses Ansible test imports.
3. **No repo change:** graph analysis false positives (`dry_run_skip`,
   `build_operation_identity`, `build_restore_activation_plan`, `check_rbac.py
   main()`) are Graphify extraction limits (decorators, dynamic invocation,
   `except` clauses), already documented in the report's triage notes.

Only finding 1 produces code changes.

### Task 1: Preserve `changed` in the outer exception handler

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/klusterlet.py:430-580` (`remediate_one_cluster`)
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py` (`test_remediation_unexpected_failure_after_secret_apply_still_reports_changed`)

- [x] **Step 1: Write the failing test** (done during validation)

```python
def test_remediation_unexpected_failure_after_secret_apply_still_reports_changed(monkeypatch):
    secondary = FakeCoreClient({("cluster-a", "cluster-a-import"): _import_secret("https://new.example:6443")})
    managed = FakeCoreClient(
        {
            (
                MANAGED_CLUSTER_AGENT_NAMESPACE,
                BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
            ): _hub_secret(
                "https://old.example:6443",
                name=BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
            )
        }
    )
    apps = FakeAppsClient()

    def core_client_factory(kubeconfig: str, context: str | None = None):
        return secondary if kubeconfig == "hub" else managed

    def apps_client_factory(kubeconfig: str, context: str | None = None) -> FakeAppsClient:
        return apps

    class ExplodingDatetime:
        @staticmethod
        def now(tz=None):
            raise RuntimeError("clock unavailable")

    monkeypatch.setattr(klusterlet_utils, "datetime", ExplodingDatetime)

    result = remediate_klusterlets(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"cluster-a": {"kubeconfig": "cluster-a"}},
        pending_clusters=["cluster-a"],
        workers=1,
        core_client_factory=core_client_factory,
        apps_client_factory=apps_client_factory,
    )

    assert managed.patched, "precondition: secret apply must happen before the failure"
    assert result["results"][0]["status"] == "failed"
    assert "clock unavailable" in result["results"][0]["reason"]
    assert result["results"][0]["changed"] is True
    assert result["changed"] is True
```

- [x] **Step 2: Run test to verify it fails** (done during validation)

Run: `python3 -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py::test_remediation_unexpected_failure_after_secret_apply_still_reports_changed -q`
Expected: FAIL with `assert False is True` on the `changed` assertion.

- [ ] **Step 3: Minimal implementation**

In `remediate_one_cluster`, move `changed = False` from inside the `try`
(currently next to the client factory calls, line 482) to just before the
`try:` at line 451, and pass `changed=changed` in the outer handler's
`_result` call (line 574):

```python
    changed = False
    try:
        import_secret = read_secret(
            ...
```

(delete the now-duplicate `changed = False` at the old position), and:

```python
    except Exception as exc:
        return _result(
            cluster_name,
            "failed",
            _mark_pending_not_run(steps),
            reason=error_summary(exc),
            changed=changed,
        )
```

- [ ] **Step 4: Run test to verify it passes, plus the klusterlet suites**

Run: `python3 -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py ansible_collections/tomazb/acm_switchover/tests/unit/test_klusterlet_remediation.py -q`
Expected: all pass (47 baseline + 1 new).

- [ ] **Step 5: Format and commit**

```bash
black --line-length 120 ansible_collections/tomazb/acm_switchover/plugins/module_utils/klusterlet.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py
git add ansible_collections/tomazb/acm_switchover/plugins/module_utils/klusterlet.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py docs/superpowers/plans/2026-06-12-klusterlet-changed-flag-fix.md
git commit -m "fix: report changed when remediation fails after bootstrap secret apply"
```

### Verification

- Targeted suites above, then the broader unit suite gate used by CI for the
  collection: `python3 -m pytest ansible_collections/tomazb/acm_switchover/tests/unit -q`.
- PR targets the `ansible` branch (738 commits ahead of `main`; klusterlet.py
  exists only there).
