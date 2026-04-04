# Auto ArgoCD Detection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Auto-enable ArgoCD deep-dive detection during preflight when the Applications CRD exists, remove `--argocd-check` flag, and add an advisory warning when ACM-touching Applications are found without `--argocd-manage`.

**Architecture:** The preflight coordinator's `_get_argocd_rbac_mode()` changes from flag-gated to CRD-gated: it always requests "check" mode (read-only) unless `--argocd-manage` is set or `--skip-gitops-check` disables detection. The existing `_get_effective_argocd_rbac_mode()` gates on actual CRD presence — no CRD means no RBAC validation and no deep dive. In the main flow, ArgoCD detection runs automatically after preflight passes (not behind a flag). Bash scripts mirror this by probing for the CRD and running `check_argocd_acm_resources()` unconditionally.

**Tech Stack:** Python 3.9+, pytest, bash, jq

**Design doc:** `docs/plans/2026-04-04-auto-argocd-detection-design.md`

---

## Task 1: Update PreflightValidator to auto-detect ArgoCD

**Files:**
- Modify: `modules/preflight_coordinator.py:48-93`
- Test: `tests/test_preflight_coordinator.py`

**Step 1: Update existing tests to expect new behavior**

In `tests/test_preflight_coordinator.py`, the `_build_validator()` helper (line 13) accepts `argocd_check` param, and the parameterized test at line 58 tests mode mapping. Update these:

- Remove `argocd_check` parameter from `_build_validator()`
- Add `skip_gitops_check` parameter (default `False`)
- Update parameterized test cases:
  - `(argocd_manage=False, skip_gitops_check=False)` → expected mode `"check"` (was `"none"`)
  - `(argocd_manage=True, skip_gitops_check=False)` → expected mode `"manage"`
  - `(argocd_manage=False, skip_gitops_check=True)` → expected mode `"none"`
- Update `test_validate_all_skips_argocd_rbac_when_applications_crd_missing` (line 89): remove `argocd_check=True` from `_build_validator()` call (auto-detect means "check" mode is now the default)
- Update `test_validate_all_uses_requested_argocd_mode_when_discovery_is_forbidden` (line 161): same
- Update `test_validate_all_api_error_in_argocd_discovery_becomes_validation_failure` (line 218): same
- Add new test: `test_validate_all_skips_argocd_when_skip_gitops_check_set` — builds validator with `skip_gitops_check=True`, verifies mode is `"none"` and no CRD probing occurs

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_preflight_coordinator.py -v`
Expected: Multiple FAILs because `PreflightValidator.__init__` still expects `argocd_check` param

**Step 3: Implement the PreflightValidator changes**

In `modules/preflight_coordinator.py`:

- `__init__` (lines 48-64): Remove `argocd_check: bool = False` parameter, add `skip_gitops_check: bool = False`. Remove `self.argocd_check = argocd_check`, add `self.skip_gitops_check = skip_gitops_check`.

- `_get_argocd_rbac_mode()` (lines 87-93): Change to:
  ```python
  def _get_argocd_rbac_mode(self) -> str:
      """Get Argo CD RBAC validation mode. Auto-enables 'check' when not skipped."""
      if self.skip_gitops_check:
          return "none"
      if self.argocd_manage:
          return "manage"
      return "check"
  ```

- `_get_effective_argocd_rbac_mode()` (lines 95-131): Remove the early return for `requested_mode == "none"` **only when it was flag-gated**. Actually, the existing logic already works: if `_get_argocd_rbac_mode()` returns `"none"` (skip_gitops_check), it early-returns `("none", "unknown")`. If it returns `"check"` (default), it probes CRDs. No changes needed to this method.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_preflight_coordinator.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add modules/preflight_coordinator.py tests/test_preflight_coordinator.py
git commit -m "feat: auto-detect ArgoCD in preflight coordinator

Replace argocd_check flag with skip_gitops_check. Default RBAC mode
is now 'check' (read-only) — gated on CRD existence by
_get_effective_argocd_rbac_mode()."
```

---

## Task 2: Remove `--argocd-check` from Python CLI and wire auto-detection

**Files:**
- Modify: `acm_switchover.py:252-256, 508-516, 540-541, 858-863`
- Test: `tests/test_main.py`

**Step 1: Update tests in `tests/test_main.py`**

- `test_run_phase_preflight_passes_argocd_flags_to_preflight_validator` (line 1594): Remove `argocd_check=True` from the test args namespace and from the expected `PreflightValidator()` call assertion. Add `skip_gitops_check=False` to expected call.
- Remove or update all mock arg namespace setups that set `argocd_check=False` (lines 478, 506, 536, 569, 655, 791, 824, 1007, 1044, 1175, 1641, 1669) — remove the `argocd_check` attribute entirely from these namespaces.
- `test_report_argocd_impact_warns_instead_of_raising_on_list_failure` (line 1673) and `test_report_argocd_impact_warns_on_non_blocking_failures` (line 1703): These test `_report_argocd_acm_impact()` which stays — keep these tests but update if the function signature or call site changes.
- Add new test: `test_argocd_detection_runs_automatically_when_crd_found` — mock preflight passing, mock `argocd_lib.detect_argocd_installation()` returning CRD found, verify `_report_argocd_acm_impact()` is called.
- Add new test: `test_argocd_detection_skipped_when_skip_gitops_check` — set `args.skip_gitops_check=True`, verify no ArgoCD detection.

**Step 2: Run tests to verify failures**

Run: `pytest tests/test_main.py -k "argocd" -v`
Expected: FAILs due to `argocd_check` attribute references

**Step 3: Implement changes in `acm_switchover.py`**

- **Remove argparse argument** (lines 252-256): Delete the `--argocd-check` argument block.

- **Update `--skip-gitops-check` handler** (lines 858-863): Remove the `argocd_check` disabling block:
  ```python
  # REMOVE these lines:
  if getattr(args, "argocd_check", False):
      logger.warning("--argocd-check ignored because --skip-gitops-check is set.")
      args.argocd_check = False
  ```

- **Update PreflightValidator instantiation** (lines 508-516): Replace `argocd_check=getattr(args, "argocd_check", False)` with `skip_gitops_check=getattr(args, "skip_gitops_check", False)`.

- **Move detection from flag-gated to automatic** (lines 540-541): Change from:
  ```python
  if getattr(args, "argocd_check", False):
      _report_argocd_acm_impact(primary, secondary, logger)
  ```
  To (runs unless skip_gitops_check or validate_only):
  ```python
  if not args.skip_gitops_check:
      _report_argocd_acm_impact(primary, secondary, logger, argocd_manage=getattr(args, "argocd_manage", False))
  ```

- **Update `_report_argocd_acm_impact()`** (lines 551-600): Add `argocd_manage: bool = False` parameter. After reporting ACM-touching apps, add advisory warning (see Task 3).

**Step 4: Run tests**

Run: `pytest tests/test_main.py -k "argocd" -v`
Expected: All PASS

**Step 5: Run full test suite**

Run: `pytest tests/ -v --timeout=120`
Expected: All tests pass (no other code references `args.argocd_check`)

**Step 6: Commit**

```bash
git add acm_switchover.py tests/test_main.py
git commit -m "feat: remove --argocd-check flag, auto-detect ArgoCD in preflight

ArgoCD deep dive now runs automatically when Applications CRD is
detected on either hub. --skip-gitops-check disables all detection."
```

---

## Task 3: Add advisory warning for unmanaged ACM-touching Applications

**Files:**
- Modify: `acm_switchover.py` (inside `_report_argocd_acm_impact()`)
- Test: `tests/test_main.py`

**Step 1: Write failing tests**

In `tests/test_main.py`, add:

- `test_argocd_advisory_warning_shown_without_argocd_manage`: Mock `_report_argocd_acm_impact()` with ArgoCD apps found and `argocd_manage=False`. Verify logger.warning contains "Consider --argocd-manage".
- `test_argocd_advisory_warning_hidden_with_argocd_manage`: Same but `argocd_manage=True`. Verify no advisory warning.
- `test_argocd_advisory_warning_only_for_autosync_apps`: Mock apps with and without automated sync. Verify advisory only counts apps with auto-sync.

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_main.py -k "advisory" -v`
Expected: FAIL

**Step 3: Implement advisory warning**

In `_report_argocd_acm_impact()`, after reporting ACM-touching apps, add:

```python
if acm_apps and not argocd_manage:
    autosync_count = sum(
        1 for a in acm_apps
        if (a.app.get("spec", {}) or {}).get("syncPolicy", {}).get("automated")
    )
    if autosync_count:
        logger.warning(
            "\n⚠ ArgoCD advisory: %d ACM-touching Application(s) with auto-sync detected.\n"
            "  Consider --argocd-manage to pause auto-sync during switchover.\n"
            "  Without pausing, ArgoCD may revert switchover changes.\n"
            "  To suppress: --skip-gitops-check",
            autosync_count,
        )
```

**Step 4: Run tests**

Run: `pytest tests/test_main.py -k "advisory" -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add acm_switchover.py tests/test_main.py
git commit -m "feat: add advisory warning for unmanaged ArgoCD apps

When ACM-touching Applications with auto-sync are detected and
--argocd-manage is not set, emit a non-blocking advisory warning
recommending --argocd-manage."
```

---

## Task 4: Update RBAC validator tests

**Files:**
- Modify: `tests/test_rbac_validator.py:108-130`

**Step 1: Verify existing RBAC tests still pass**

The RBAC validator itself doesn't change (it accepts `argocd_mode` string, not the flag). The `"check"` mode tests should still work since that mode still exists in the RBAC validator.

Run: `pytest tests/test_rbac_validator.py -v`
Expected: All PASS (no changes needed if tests pass)

**Step 2: Add test for auto-enabled "check" mode RBAC**

Add: `test_validate_cluster_permissions_argocd_check_validates_readonly_when_crd_found` — verify that when auto-detection finds CRD, the RBAC validator receives `argocd_mode="check"` and validates `get/list` on applications.

**Step 3: Run tests**

Run: `pytest tests/test_rbac_validator.py -v`
Expected: All PASS

**Step 4: Commit**

```bash
git add tests/test_rbac_validator.py
git commit -m "test: add RBAC test for auto-enabled ArgoCD check mode"
```

---

## Task 5: Remove `--argocd-check` from bash preflight script

**Files:**
- Modify: `scripts/preflight-check.sh:42, 62-65, 67-74, 87-92, 711-723`
- Modify: `scripts/lib-common.sh` (extract `probe_argocd_crd()`)

**Step 1: Extract `probe_argocd_crd()` helper in lib-common.sh**

Add a new function before `check_argocd_acm_resources()` (before line 1071):

```bash
# Returns 0 if applications.argoproj.io CRD exists, 1 if not found,
# 2 if unable to determine (auth/API error).
probe_argocd_crd() {
    local context="$1"
    local crd_stderr
    local crd_rc=0
    crd_stderr=$("$CLUSTER_CLI_BIN" --context="$context" get crd applications.argoproj.io 2>&1 >/dev/null) || crd_rc=$?
    if [[ $crd_rc -ne 0 ]]; then
        if echo "$crd_stderr" | grep -qiE '(NotFound|not found|no matches|the server doesn.t have a resource)'; then
            return 1
        fi
        return 2
    fi
    return 0
}
```

**Step 2: Update preflight-check.sh**

- **Remove** `ARGOCD_CHECK=0` (line 42)
- **Remove** `--argocd-check)` case from argument parsing (lines 62-65)
- **Remove** `--argocd-check` from help/usage text (lines 67, 74)
- **Remove** the `ARGOCD_CHECK` conflict handling under `--skip-gitops-check` (lines 87-92):
  ```bash
  # REMOVE:
  if [[ $ARGOCD_CHECK -eq 1 ]]; then
      check_warn "--argocd-check ignored because --skip-gitops-check is set."
      ARGOCD_CHECK=0
  fi
  ```
- **Replace** the deep dive section (lines 711-723) from:
  ```bash
  if [[ $ARGOCD_CHECK -eq 1 ]]; then
      section_header "16. Checking ArgoCD GitOps Management (Optional)"
      ...
  fi
  ```
  To auto-detection:
  ```bash
  if [[ $SKIP_GITOPS_CHECK -eq 0 ]]; then
      local argocd_found=0
      probe_argocd_crd "$PRIMARY_CONTEXT" && argocd_found=1
      if [[ $argocd_found -eq 0 ]] && [[ -n "$SECONDARY_CONTEXT" ]]; then
          probe_argocd_crd "$SECONDARY_CONTEXT" && argocd_found=1
      fi
      if [[ $argocd_found -eq 1 ]]; then
          section_header "16. Checking ArgoCD GitOps Management"
          check_argocd_acm_resources "$PRIMARY_CONTEXT" "Primary hub"
          check_argocd_acm_resources "$SECONDARY_CONTEXT" "Secondary hub"
          print_argocd_advisory_warning
          print_gitops_report
      fi
  fi
  ```

**Step 3: Verify script syntax**

Run: `bash -n scripts/preflight-check.sh`
Expected: No syntax errors

**Step 4: Commit**

```bash
git add scripts/preflight-check.sh scripts/lib-common.sh
git commit -m "feat: auto-detect ArgoCD in bash preflight, remove --argocd-check

Preflight now probes for ArgoCD CRD and runs deep dive automatically.
Extracted probe_argocd_crd() helper to lib-common.sh."
```

---

## Task 6: Remove `--argocd-check` from bash postflight script

**Files:**
- Modify: `scripts/postflight-check.sh:41, 57-60, 62-68, 81-86, 644-650`

**Step 1: Apply same changes as preflight**

- **Remove** `ARGOCD_CHECK=0` (line 41)
- **Remove** `--argocd-check)` case (lines 57-60)
- **Remove** from help text (lines 62, 68)
- **Remove** conflict handling (lines 81-86)
- **Replace** deep dive section (lines 644-650) with auto-detection using `probe_argocd_crd()`:
  ```bash
  if [[ $SKIP_GITOPS_CHECK -eq 0 ]]; then
      local argocd_found=0
      probe_argocd_crd "$NEW_HUB_CONTEXT" && argocd_found=1
      if [[ $argocd_found -eq 0 ]] && [[ -n "$OLD_HUB_CONTEXT" ]]; then
          probe_argocd_crd "$OLD_HUB_CONTEXT" && argocd_found=1
      fi
      if [[ $argocd_found -eq 1 ]]; then
          section_header "10. Checking ArgoCD GitOps Management"
          check_argocd_acm_resources "$NEW_HUB_CONTEXT" "New hub"
          if [[ -n "$OLD_HUB_CONTEXT" ]]; then
              check_argocd_acm_resources "$OLD_HUB_CONTEXT" "Old hub"
          fi
          print_argocd_advisory_warning
          print_gitops_report
      fi
  fi
  ```

**Step 2: Verify script syntax**

Run: `bash -n scripts/postflight-check.sh`
Expected: No syntax errors

**Step 3: Commit**

```bash
git add scripts/postflight-check.sh
git commit -m "feat: auto-detect ArgoCD in bash postflight, remove --argocd-check"
```

---

## Task 7: Add advisory warning function to lib-common.sh

**Files:**
- Modify: `scripts/lib-common.sh`

**Step 1: Add `print_argocd_advisory_warning()` function**

Add after `print_gitops_report()` (after line 1066):

```bash
# Print advisory warning when ACM-touching ArgoCD Applications are found
# but --argocd-manage was not used. Non-blocking.
print_argocd_advisory_warning() {
    # Check if any ACM-touching apps were detected by check_argocd_acm_resources
    if [[ ${ARGOCD_ACM_APP_COUNT:-0} -gt 0 ]]; then
        echo ""
        echo -e "${YELLOW}⚠ Argo CD Applications managing ACM resources detected.${NC}"
        echo -e "${YELLOW}  Consider using --argocd-manage (Python tool) to pause auto-sync during switchover.${NC}"
        echo -e "${YELLOW}  Without pausing, Argo CD may revert switchover changes.${NC}"
        echo -e "${YELLOW}  To suppress this warning: --skip-gitops-check${NC}"
        echo ""
        ((WARNING_CHECKS+=1)) || true
        ((TOTAL_CHECKS+=1)) || true
        WARNING_MESSAGES+=("Argo CD Applications managing ACM resources detected; consider using --argocd-manage to pause auto-sync.")
    fi
}
```

Note: `ARGOCD_ACM_APP_COUNT` needs to be set by `check_argocd_acm_resources()`. Check if it already tracks a count; if not, add a counter variable at the point where ACM-touching apps are found (around line 1165-1192 of lib-common.sh).

**Step 2: Initialize counter in lib-common.sh**

Near the global variable declarations (around line 816), add:
```bash
ARGOCD_ACM_APP_COUNT=0
```

Update `check_argocd_acm_resources()` to increment `ARGOCD_ACM_APP_COUNT` when ACM-touching apps are found.

**Step 3: Verify script syntax**

Run: `bash -n scripts/lib-common.sh`
Expected: No syntax errors

**Step 4: Commit**

```bash
git add scripts/lib-common.sh
git commit -m "feat: add ArgoCD advisory warning and auto-detect helpers in lib-common"
```

---

## Task 8: Update bash script tests

**Files:**
- Modify: `tests/test_scripts.py:147, 180`
- Modify: `tests/test_scripts_integration.py`

**Step 1: Update test_scripts.py**

- `test_preflight_warns_when_argocd_check_is_ignored` (line 147): **Remove** this test entirely (flag no longer exists).
- `test_postflight_warns_when_argocd_check_is_ignored` (line 180): **Remove** this test entirely.
- Add new test: `test_preflight_auto_detects_argocd_crd` — verify that when `probe_argocd_crd` returns 0, the deep dive runs.
- Add new test: `test_preflight_skips_argocd_when_no_crd` — verify no deep dive when CRD absent.

**Step 2: Update test_scripts_integration.py**

Remove all references to `argocd_check` in test setup/mock building (9 references). Replace with the new auto-detection behavior expectations.

**Step 3: Run tests**

Run: `pytest tests/test_scripts.py tests/test_scripts_integration.py -v`
Expected: All PASS

**Step 4: Commit**

```bash
git add tests/test_scripts.py tests/test_scripts_integration.py
git commit -m "test: update bash script tests for auto ArgoCD detection"
```

---

## Task 9: Update documentation

**Files:**
- Modify: `docs/operations/usage.md:17, 32, 433, 457`
- Modify: `docs/operations/quickref.md:54, 206, 217, 263-264`
- Modify: `docs/deployment/rbac-requirements.md:181`
- Modify: `docs/development/architecture.md:305`
- Modify: `docs/reference/validation-rules.md:178, 325`
- Modify: `scripts/README.md:205, 917`
- Modify: `AGENTS.md` (CLI flags section)
- Modify: `CHANGELOG.md` (Unreleased section)

**Step 1: Update docs/operations/usage.md**

- Remove `--argocd-check` from flag list and descriptions
- Add section explaining auto-detection: "ArgoCD detection runs automatically during preflight when the Applications CRD is found on either hub. Use `--skip-gitops-check` to disable."
- Update examples that use `--argocd-check`

**Step 2: Update docs/operations/quickref.md**

- Remove `--argocd-check` references (lines 54, 206, 217, 263-264)
- Update ArgoCD quick reference section

**Step 3: Update docs/deployment/rbac-requirements.md**

- Line 181: Change "required when using `--argocd-check`" to "validated automatically when ArgoCD CRD is detected"

**Step 4: Update docs/development/architecture.md**

- Line 305: Update note about `--argocd-check` being read-only → "ArgoCD detection runs automatically (read-only)"

**Step 5: Update docs/reference/validation-rules.md**

- Lines 178, 325: Remove `--argocd-check` interaction rules

**Step 6: Update scripts/README.md**

- Line 205: Remove `--argocd-check` from flag list
- Line 917: Update example command (remove `--argocd-check` from example)
- Add note about auto-detection behavior

**Step 7: Update AGENTS.md**

- Update CLI Validation Guidance section: remove `--argocd-check` references
- Update Common Tasks section if it references `--argocd-check`

**Step 8: Update CHANGELOG.md**

Add under `## [Unreleased]`:

```markdown
### Changed
- ArgoCD deep-dive detection now runs automatically during preflight when Applications CRD is found on either hub
- Read-only ArgoCD RBAC permissions are validated automatically when CRD is detected
- Advisory warning shown when ACM-touching Applications with auto-sync are detected without `--argocd-manage`

### Removed
- `--argocd-check` CLI flag (Python tool and bash scripts) — replaced by automatic detection
```

**Step 9: Commit**

```bash
git add docs/ scripts/README.md AGENTS.md CHANGELOG.md
git commit -m "docs: update documentation for auto ArgoCD detection

Remove --argocd-check references, document automatic detection
behavior and advisory warning."
```

---

## Task 10: Version bump to 1.6.0

**Files:**
- Modify: `lib/__init__.py` (`__version__`, `__version_date__`)
- Modify: `scripts/constants.sh` (`SCRIPT_VERSION`, `SCRIPT_VERSION_DATE`)
- Modify: `container-bootstrap/Containerfile` (version label)
- Modify: `deploy/helm/acm-switchover-rbac/Chart.yaml` (`version`, `appVersion`)
- Modify: `README.md` (version badge)

**Step 1: Bump Python version**

In `lib/__init__.py`:
```python
__version__ = "1.6.0"
__version_date__ = "2026-04-04"
```

**Step 2: Bump bash version**

In `scripts/constants.sh`:
```bash
export SCRIPT_VERSION="1.6.0"
export SCRIPT_VERSION_DATE="2026-04-04"
```

**Step 3: Bump container and Helm versions**

- `container-bootstrap/Containerfile`: Update `LABEL version="1.6.0"`
- `deploy/helm/acm-switchover-rbac/Chart.yaml`: Update `version` and `appVersion` to `1.6.0`

**Step 4: Update README badge**

In `README.md`: Update version badge to `1.6.0`

**Step 5: Update CHANGELOG.md**

Rename `## [Unreleased]` to `## [1.6.0] - 2026-04-04` and add new `## [Unreleased]` above it.

**Step 6: Run full test suite**

Run: `./run_tests.sh`
Expected: All tests pass

**Step 7: Commit**

```bash
git add lib/__init__.py scripts/constants.sh container-bootstrap/Containerfile \
       deploy/helm/acm-switchover-rbac/Chart.yaml README.md CHANGELOG.md
git commit -m "release: bump version to 1.6.0

MINOR version bump for breaking change: --argocd-check flag removed,
replaced by automatic ArgoCD detection in preflight."
```

---

## Task Dependency Order

```
Task 1 (preflight_coordinator) → Task 2 (acm_switchover CLI) → Task 3 (advisory warning)
                                                                       ↓
Task 5 (bash preflight) ← Task 7 (lib-common helpers) → Task 6 (bash postflight)
                                                                       ↓
Task 4 (RBAC tests) ─────────────────────────────────→ Task 8 (bash tests)
                                                                       ↓
                                                        Task 9 (docs) → Task 10 (version bump)
```

Tasks 1-3 (Python core) and Tasks 5-7 (bash core) can run in parallel after Task 1 completes. Task 4 can run anytime. Tasks 8-10 are sequential at the end.
