# PR43 Low-Severity Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the behavior-preserving PR43 subset for R2-L3, R2-L4, R2-L5, the safe Argo CD resume slice of R2-L7, and R2-L9.

**Architecture:** Keep every cleanup local to its owning surface. Logging truncation stays in Python helper functions, CLI required-argument checks move after argparse parsing, klusterlet probe hard failures remain structured module results with `failed: true` and `failed_clusters`, Argo CD resume repeated guards collapse into one boolean fact, and release stream records call `StreamResult.to_dict()` directly.

**Tech Stack:** Python 3, pytest, argparse, Ansible YAML playbooks/modules, release test helpers.

## Global Constraints

- Base is `origin/ansible` at PR #149 merge commit `79b1d92f516bfb45a5c18ff54d554044a6e80f15`.
- Do not include R2-L2.
- Do not modify protected files: `docs/ACM_SWITCHOVER_RUNBOOK.md` or `.claude/skills/**/*.skill.md`.
- Do not modify RBAC permissions, verbs, resources, namespaces, manifests, Helm chart output, or RBAC validation strictness.
- Do not modify live/lab release certification behavior except the internal `_as_dict()` cleanup; release artifact schema must stay unchanged.
- Preserve fail-closed behavior, check-mode behavior, idempotence, registered-fact contracts, and report schemas.
- Do not mark deferred/split items complete.

---

### Task 1: R2-L3 Log Detail Truncation

**Files:**
- Modify: `lib/waiter.py`
- Modify: `modules/decommission.py`
- Test: `tests/test_waiter.py`
- Test: `tests/test_decommission.py`

**Interfaces:**
- Produces: `format_public_detail(detail: str, *, max_length: int = 500) -> str`
- Produces: `format_public_list(values: list[str], *, max_items: int = 20, max_length: int = 500) -> str`
- Consumes: `WaitConditionResult.public_detail` and decommission ManagedCluster names for log/detail output only.

- [x] **Step 1: Add failing waiter truncation tests**

Add tests asserting short details pass through unchanged and long details keep a count plus suffix, for example:

```python
from lib.waiter import WaitConditionResult, format_public_detail, wait_for_condition


def test_format_public_detail_preserves_short_detail():
    assert format_public_detail("deleted") == "deleted"


def test_format_public_detail_truncates_deterministically():
    detail = "cluster-" + ("x" * 600)
    formatted = format_public_detail(detail, max_length=80)
    assert len(formatted) <= 80
    assert "truncated" in formatted
    assert formatted.endswith("chars]")
```

- [x] **Step 2: Run waiter tests red**

Run: `python -m pytest tests/test_waiter.py -q`

Expected: fail because `format_public_detail` does not exist.

- [x] **Step 3: Implement waiter formatting helper and use it for logs**

Add helpers near `WaitConditionResult` in `lib/waiter.py`:

```python
PUBLIC_DETAIL_MAX_LENGTH = 500
PUBLIC_LIST_MAX_ITEMS = 20


def format_public_detail(detail: str, *, max_length: int = PUBLIC_DETAIL_MAX_LENGTH) -> str:
    text = str(detail)
    if len(text) <= max_length:
        return text
    marker = f"... [truncated {len(text) - max_length} chars]"
    keep = max(0, max_length - len(marker))
    return text[:keep] + marker


def format_public_list(
    values: list[str], *, max_items: int = PUBLIC_LIST_MAX_ITEMS, max_length: int = PUBLIC_DETAIL_MAX_LENGTH
) -> str:
    shown = [str(value) for value in values[:max_items]]
    omitted = len(values) - len(shown)
    text = ", ".join(shown)
    if omitted > 0:
        text = f"{text}, ... ({omitted} more)"
    return format_public_detail(text, max_length=max_length)
```

Use `format_public_detail(result.public_detail)` for `logger.info`, `logger.debug`, and `logger.warning` calls in `wait_for_condition()`.

- [x] **Step 4: Add failing decommission list-format test**

Add a `tests/test_decommission.py` test that creates many remaining ManagedClusters and asserts the pending detail passed to `WaitConditionResult.pending()` is bounded and reports omitted items.

- [x] **Step 5: Wire decommission to `format_public_list`**

Import `format_public_list` in `modules/decommission.py` and use it for the pending list and preserveOnDelete success log:

```python
return WaitConditionResult.pending(
    f"{len(non_local)} ManagedCluster(s) remaining: {format_public_list(names)}"
)
```

```python
logger.info(
    "Verified ClusterDeployment preserveOnDelete safety for ManagedCluster(s): %s",
    format_public_list(managed_cluster_names),
)
```

- [x] **Step 6: Verify task tests green**

Run: `python -m pytest tests/test_waiter.py tests/test_decommission.py -q`

Expected: pass.

### Task 2: R2-L4 CLI Required-Argument Validation After Parse

**Files:**
- Modify: `acm_switchover.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Produces: `_missing_parse_required_args(args) -> list[str]`
- Consumes: parsed argparse namespace.

- [x] **Step 1: Add failing parser abbreviation tests**

Add tests proving abbreviated standalone flags are parsed before required switchover/decommission arguments are evaluated:

```python
def test_restore_only_abbreviation_does_not_require_primary_or_method(self):
    with patch("sys.argv", ["script.py", "--restore-on", "--secondary-context", "secondary"]):
        args = parse_args()
    assert args.restore_only is True
    assert args.primary_context is None
    assert args.method is None
    assert args.old_hub_action is None
```

Add a required-argument test that still raises `SystemExit` for a normal switchover missing `--method` or `--old-hub-action`.

- [x] **Step 2: Run parser tests red**

Run: `python -m pytest tests/test_main.py::TestArgParsing -q`

Expected: abbreviated restore-only test fails under the current pre-scan.

- [x] **Step 3: Remove manual pre-scan and add post-parse required check**

In `parse_args()`, delete the `sys.argv[1:]` standalone flag pre-scan. Set `required=False` for `--primary-context`, `--method`, and `--old-hub-action`. After `args = parser.parse_args()`, add:

```python
missing_required = _missing_parse_required_args(args)
if missing_required:
    parser.error("the following arguments are required: " + ", ".join(missing_required))
return args
```

Add helper:

```python
def _missing_parse_required_args(args) -> list[str]:
    standalone = args.setup or args.argocd_resume_only or args.restore_only
    missing = []
    if not (args.restore_only or args.argocd_resume_only) and not args.primary_context:
        missing.append("--primary-context")
    if not standalone:
        if not args.method:
            missing.append("--method")
        if not args.old_hub_action:
            missing.append("--old-hub-action")
    return missing
```

- [x] **Step 4: Verify parser and validation tests**

Run: `python -m pytest tests/test_main.py tests/test_validation.py -q`

Expected: pass.

### Task 3: R2-L5 Klusterlet Probe Structured Failure Contract

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_klusterlet_probe.py`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_klusterlet_remediation.py`

**Interfaces:**
- Preserves: module returns structured `failed: true` and `failed_clusters` fields through `exit_json` for per-cluster probe failures.
- Verifies: post-activation caller fails when initial probe reports `failed_clusters`.

- [x] **Step 1: Add/adjust module-entrypoint test for structured failed exit_json**

Add a `main()` test where `probe_klusterlet_connections()` returns `{"changed": False, "failed": True, "failed_clusters": ["cluster-a"]}` and assert `exit_json` receives that payload rather than `fail_json`.

- [x] **Step 2: Verify role contract test for caller hard failure**

Use the existing `test_klusterlet_remediation.py` contract test that loads `roles/post_activation/tasks/verify_klusterlet.yml` and asserts the "Fail when initial klusterlet probe reports hard errors" task exists before remediation and its `when` condition checks `failed_clusters`.

- [x] **Step 3: Document the intentional module contract**

In `acm_klusterlet_probe.py` DOCUMENTATION description, add:

```yaml
  - Probe hard failures are surfaced through C(failed=true) and C(failed_clusters), so callers may add role-specific failure messages without reclassifying probe results.
```

- [x] **Step 4: Verify klusterlet tests**

Run: `python -m pytest tests/test_post_activation.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py ansible_collections/tomazb/acm_switchover/tests/unit/test_klusterlet_remediation.py ansible_collections/tomazb/acm_switchover/tests/unit/test_post_activation_regressions.py -q`

Expected: pass.

### Task 4: R2-L7 Argo CD Resume Checkpoint Guard Dedup

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/playbooks/argocd_resume.yml`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_ansible_resilience_contracts.py`

**Interfaces:**
- Produces: `_argocd_resume_checkpoint_lookup_required` fact.
- Preserves: checkpoint path split, safe-path validation before stat/slurp, checkpoint identity validation before resume.

- [x] **Step 1: Add failing static guard-dedup test**

Add a test that asserts `_argocd_resume_checkpoint_lookup_required` exists in `argocd_resume.yml` and that the repeated four-condition checkpoint lookup guard does not appear five times.

Validation follow-up: the guard test also asserts that the matched checkpoint task names equal the expected set, so a renamed or missing checkpoint task cannot escape the guard assertion.

- [x] **Step 2: Factor the repeated guard into one fact**

At the start of `pre_tasks`, add:

```yaml
    - name: Resolve whether persisted checkpoint lookup is required
      ansible.builtin.set_fact:
        _argocd_resume_checkpoint_lookup_required: >-
          {{
            ((acm_switchover_argocd | default({})).get('run_id', '') | length) == 0
            and ((acm_switchover_execution | default({})).get('run_id', '') | length) == 0
            and (acm_switchover_execution.checkpoint.enabled | default(false))
            and (((acm_switchover_execution | default({})).get('checkpoint', {}).get('path', '')) | length) > 0
          }}
```

Replace the repeated checkpoint lookup guards on path resolve/stat/slurp/parse with:

```yaml
        - _argocd_resume_checkpoint_lookup_required | default(false)
```

Keep later `_argocd_resume_checkpoint is defined` guards unchanged.

- [x] **Step 3: Verify Argo CD static tests**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py ansible_collections/tomazb/acm_switchover/tests/unit/test_ansible_resilience_contracts.py -q`

Expected: pass.

## Recorded Deviations

- R2-L3: `format_public_detail()` preserves the leading diagnostic text and appends a bounded truncation marker with omitted character count; it does not preserve a trailing suffix.
- R2-L5: source reconciliation on the PR #149 base showed `acm_klusterlet_probe` already propagates `failed: true` for non-empty `failed_clusters`. The implementation documents and tests that structured failure contract instead of introducing a new `fail_json()` path or a soft-fail change.
- R2-L5: no new `test_post_activation_klusterlet_contracts.py` file was created because `test_klusterlet_remediation.py` already contains the caller hard-fail contract test for initial probe `failed_clusters`.
- R2-L7: the shared Argo CD resume predicate keeps the existing `acm_switchover_execution.checkpoint.enabled | default(false)` guard spelling so the checkpoint-enabled contract remains traceable in the existing playbook tests.
- Validation V1: the shared Argo CD resume predicate intentionally uses bare Jinja truthiness for `checkpoint.enabled`; no `| bool` coercion is applied.
- Validation V2: parser behavior stays unchanged; help text now labels the mode-specific required arguments that parser-level `required=` can no longer express.
- Validation V3: the Argo CD resume guard test now checks the exact matched checkpoint task-name set, not only membership for tasks that happen to match.
- Validation V4: no additional V4-specific decommission public-detail change was made in this polish pass; the double-bounding comment is cosmetic and outside the V1-V3 required scope.

### Task 5: R2-L9 Release Orchestrator `_as_dict()` Removal

**Files:**
- Modify: `tests/release/orchestrator.py`
- Test: `tests/release/test_orchestrator.py`
- Test: `tests/release/test_release_certification.py`

**Interfaces:**
- Consumes: `StreamResult.to_dict()`.
- Preserves: release stream result schema.

- [x] **Step 1: Add failing direct-conversion contract test**

Add a test proving configured fake adapters return `StreamResult` and `_execute_stream_scenarios()` emits the same dict shape as `StreamResult.to_dict()`.

- [x] **Step 2: Delete `_as_dict()` and call `.to_dict()` directly**

In `_execute_stream_scenarios()` replace:

```python
payload = _as_dict(result)
```

with:

```python
payload = result.to_dict()
```

Delete `_as_dict()` from `tests/release/orchestrator.py`. If `Any` becomes unused, remove the import.

- [x] **Step 3: Verify release tests**

Run: `python -m pytest tests/release/test_orchestrator.py tests/release/test_release_certification.py -q`

Expected: pass.

### Task 6: Tracker Update, Formatting, Review, and PR

**Files:**
- Modify: `thermos-resolution-plan.md`
- Possibly modify: `CHANGELOG.md` only if the final diff has operator-visible behavior. Expected: no changelog entry.

**Interfaces:**
- Preserves: PR43 tracker row semantics and deferred/split items.

- [ ] **Step 1: Update tracker**

Update `thermos-resolution-plan.md`:

- Set `Last Updated` to `2026-07-08`.
- Record PR #149 / PR39 as merged if still stale.
- Move PR43 from `planned` to `in_progress` during implementation, then `ready_for_review` before PR.
- Record included findings: R2-L3, R2-L4, R2-L5, R2-L7 partial, R2-L9.
- Record deferred/split findings: R2-L1, R2-L6, R2-L7 observability/Helm/RBAC/bootstrap subitems, R2-L8; R2-L2 excluded.
- Record branch, worktree, spec path, plan path, PR URL, and verification evidence.

- [ ] **Step 2: Run verification**

Run the targeted suites from the design plus:

```bash
git diff --check
python -m pytest tests/test_documentation_guardrails.py -q
```

Run `./run_tests.sh` if feasible.

- [ ] **Step 3: Review final diff**

Use the available code-review workflow because `superpowers:code-reviewing` is not installed. Self-review for scope creep, protected files, RBAC/Helm/manifests, release schema, fail-closed/check-mode/idempotence drift, and deferred-item completion claims.

- [ ] **Step 4: Commit and open draft PR**

Commit the final changes, push `chore/thermos-43-low-severity-cleanup`, and open a draft PR:

```bash
gh pr create --draft --base ansible --head chore/thermos-43-low-severity-cleanup --title "Thermos PR 43: low-severity cleanup batch (R2-L*)" --body-file /tmp/pr43-low-severity-cleanup-pr-body.md
```

Expected: draft PR URL for final response and tracker.
