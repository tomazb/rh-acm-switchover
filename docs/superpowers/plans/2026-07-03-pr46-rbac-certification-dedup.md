# PR 46: RBAC Certification Polarity Dedup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the duplicated required/forbidden permission-evaluation loops in `tests/release/checks/rbac_certification.py` with one polarity-parameterized `_evaluate_permissions(...)` helper (R2-M8), byte-identical output.

**Architecture:** Per the approved design (`docs/superpowers/specs/2026-07-03-pr46-rbac-certification-dedup-design.md`): the helper runs SAR checks over a permission iterable with `expect_allowed` polarity, deriving expected/actual/message strings so emitted `CertificationAssertion`s match today's exactly, and returns `(assertions, unexpected_count, error_count)`; `certify_rbac_permissions` calls it twice. Guarded by the existing ~27-test certification suite plus a red-first parametrized helper test.

**Tech Stack:** Python 3, pytest, black/isort (line-length 120).

## Global Constraints

- Byte-identical `CertificationAssertion` output on all branches (error/unexpected/expected × both polarities).
- `black --line-length 120` / `isort --profile black --line-length 120` on touched files.
- Base branch: `ansible` @ `7ee06d1a`; PR branch `refactor/thermos-46-rbac-certification-dedup`.

---

### Task 1: Red-first helper test

**Files:**
- Modify: `tests/release/checks/test_rbac_certification.py` (append)

- [ ] **Step 1: Append the failing parametrized test**

```python
@pytest.mark.parametrize(
    ("expect_allowed", "allowed", "error", "exp_status", "exp_actual", "exp_message", "exp_unexpected", "exp_errors"),
    [
        (True, True, None, "passed", "allowed", "Permission allowed for {sa}", 0, 0),
        (True, False, None, "failed", "denied", "Permission denied for {sa}", 1, 0),
        (True, False, "boom", "failed", "error", "SAR check failed for {sa}: boom", 0, 1),
        (False, False, None, "passed", "denied", "Forbidden permission denied for {sa}", 0, 0),
        (False, True, None, "failed", "allowed", "Forbidden permission allowed for {sa}", 1, 0),
        (False, True, "boom", "failed", "error", "SAR check failed for {sa}: boom", 0, 1),
    ],
)
def test_evaluate_permissions_polarity_matrix(
    monkeypatch, tmp_path, expect_allowed, allowed, error, exp_status, exp_actual, exp_message, exp_unexpected, exp_errors
):
    from tests.release.checks import rbac_certification as module
    from tests.release.checks.rbac_certification import SARCheckResult, _evaluate_permissions

    sa = "system:serviceaccount:ns:sa"

    def fake_sar(**kwargs):
        return SARCheckResult(allowed=allowed, evidence_path="ev.json", error=error)

    monkeypatch.setattr(module, "_check_permission_via_sar", fake_sar)
    hub = SimpleNamespace(kubeconfig="kc", context="ctx")
    permission = module._get_required_permissions(
        role="switchover", include_decommission=False, include_old_hub_finalization=False
    )[0]

    assertions, unexpected, errors = _evaluate_permissions(
        permissions=[permission],
        expect_allowed=expect_allowed,
        hub=hub,
        service_account=sa,
        artifact_dir=tmp_path,
    )

    assert len(assertions) == 1
    a = assertions[0]
    assert a.status == exp_status
    assert a.expected == ("allowed" if expect_allowed else "denied")
    assert a.actual == exp_actual
    assert a.message == exp_message.format(sa=sa)
    assert unexpected == exp_unexpected
    assert errors == exp_errors
```

Add `from types import SimpleNamespace` to the test file's imports if not present (check the top of the file; existing tests may already provide a hub stub type — reuse it if so).

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/release/checks/test_rbac_certification.py -q -k evaluate_permissions`
Expected: FAIL — `ImportError: cannot import name '_evaluate_permissions'`.

- [ ] **Step 3: Commit**

```bash
git add tests/release/checks/test_rbac_certification.py
git commit -m "test: add red polarity-matrix test for permission evaluation helper"
```

### Task 2: Extract the helper, call it twice

**Files:**
- Modify: `tests/release/checks/rbac_certification.py:415-524`

- [ ] **Step 1: Add `_evaluate_permissions` before `certify_rbac_permissions`**

Exactly the helper from the design spec's Design section.

- [ ] **Step 2: Replace both loops in `certify_rbac_permissions`**

Exactly the call-site code from the design spec (helper called with `expect_allowed=True` for `permissions`, then conditionally with `expect_allowed=False` for `_get_forbidden_permissions()`, extending assertions and accumulating `error_count`). Keep the downstream `failed_count = denied_count + forbidden_allowed_count + error_count` line working by naming the returned counts accordingly.

- [ ] **Step 3: Run the certification + orchestrator suites**

Run: `python -m pytest tests/release/checks/test_rbac_certification.py tests/release/test_orchestrator.py -q`
Expected: all PASS.

- [ ] **Step 4: Format and commit**

```bash
black --line-length 120 tests/release/checks/rbac_certification.py tests/release/checks/test_rbac_certification.py
isort --profile black --line-length 120 tests/release/checks/rbac_certification.py tests/release/checks/test_rbac_certification.py
git add -A
git commit -m "refactor: deduplicate required/forbidden permission evaluation (R2-M8)"
```

### Task 3: Full verification, tracker update, draft PR

**Files:**
- Modify: `thermos-resolution-plan.md` (row PR 46)

- [ ] **Step 1: Full gate** — `./run_tests.sh`, expect PASS, record lane counts.

- [ ] **Step 2: Tracker + push + PR**

```bash
git add thermos-resolution-plan.md
git commit -m "docs: mark Thermos PR 46 ready for review in tracker"
git push -u origin refactor/thermos-46-rbac-certification-dedup
gh pr create --draft --base ansible --title "Thermos PR 46: polarity-parameterized RBAC permission evaluation (R2-M8)" --body "<summary + verification evidence>"
```
