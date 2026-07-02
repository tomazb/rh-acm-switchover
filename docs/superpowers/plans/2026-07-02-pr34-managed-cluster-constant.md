# PR 34: ManagedCluster/Backup CR API Constants Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every hardcoded `cluster.open-cluster-management.io` API literal in `modules/*.py` (49 sites) and the two behavioral `lib/kube_client.py` helpers with constants from `lib/constants.py`, guarded by a static literal-ban test and a constants-parity entry.

**Architecture:** Purely mechanical constants routing per the approved design (`docs/superpowers/specs/2026-07-02-pr34-managed-cluster-constant-design.md`). New `CLUSTER_BACKUP_*` companion constants alias the existing `MANAGED_CLUSTER_API_GROUP`; no behavior change — every constant resolves to the exact literal it replaces.

**Tech Stack:** Python 3, pytest, black/isort (line-length 120).

## Global Constraints

- `black --line-length 120` and `isort --profile black --line-length 120` on touched Python files (no repo-level config; 120 is CI's flag).
- No behavior change: constant values must equal the replaced literals byte-for-byte.
- Do not touch `lib/rbac_validator.py`, `lib/kube_client.py:567` docstring, `tests/**` fixture literals, or the Ansible collection.
- Base branch: `ansible`; PR branch `fix/thermos-34-managed-cluster-constant`.

---

### Task 1: Red-first guardrail test

**Files:**
- Create: `tests/test_api_literal_guardrails.py`

**Interfaces:**
- Produces: static test `test_no_hardcoded_managed_cluster_api_group_in_modules` that later tasks turn green.

- [ ] **Step 1: Write the failing test**

```python
"""Static guardrails banning hardcoded ACM API-group literals outside lib/constants.py.

Thermos R2-H2: MANAGED_CLUSTER_API_GROUP exists so the API group is defined
once; workflow modules must import it (or its CLUSTER_BACKUP_* companions)
instead of repeating the literal.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULES_DIR = REPO_ROOT / "modules"
BANNED_LITERAL = "cluster.open-cluster-management.io"


def test_no_hardcoded_managed_cluster_api_group_in_modules():
    """modules/*.py must route the ACM cluster API group through lib.constants."""
    violations = []
    for path in sorted(MODULES_DIR.glob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if BANNED_LITERAL in line:
                violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not violations, (
        "Hardcoded ACM API-group literals found; import MANAGED_CLUSTER_API_GROUP / "
        "CLUSTER_BACKUP_* constants from lib.constants instead:\n  " + "\n  ".join(violations)
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_literal_guardrails.py -q`
Expected: FAIL listing 49 violations across 7 files.

- [ ] **Step 3: Commit**

```bash
git add tests/test_api_literal_guardrails.py
git commit -m "test: add red guardrail banning hardcoded ACM API-group literals in modules/"
```

### Task 2: Add companion constants and parity entry

**Files:**
- Modify: `lib/constants.py:162-165` (ManagedCluster API identifiers block)
- Modify: `tests/test_constants_parity.py` (CONSTANT_PAIRS map)

**Interfaces:**
- Produces: `CLUSTER_BACKUP_API_GROUP` (== `MANAGED_CLUSTER_API_GROUP`), `CLUSTER_BACKUP_API_VERSION = "v1beta1"`, `CLUSTER_BACKUP_API_VERSION_FULL = "cluster.open-cluster-management.io/v1beta1"`, `BACKUP_SCHEDULE_PLURAL = "backupschedules"`, `RESTORE_PLURAL = "restores"` in `lib/constants.py`.

- [ ] **Step 1: Add constants after the ManagedCluster block**

In `lib/constants.py`, extend:

```python
# ManagedCluster API identifiers
MANAGED_CLUSTER_API_GROUP = "cluster.open-cluster-management.io"
MANAGED_CLUSTER_API_VERSION = "v1"
MANAGED_CLUSTER_PLURAL = "managedclusters"

# ACM cluster-backup-operator CRDs (BackupSchedule, Restore) share the
# ManagedCluster API group but use v1beta1.
CLUSTER_BACKUP_API_GROUP = MANAGED_CLUSTER_API_GROUP
CLUSTER_BACKUP_API_VERSION = "v1beta1"
CLUSTER_BACKUP_API_VERSION_FULL = f"{CLUSTER_BACKUP_API_GROUP}/{CLUSTER_BACKUP_API_VERSION}"
BACKUP_SCHEDULE_PLURAL = "backupschedules"
RESTORE_PLURAL = "restores"
```

- [ ] **Step 2: Add the parity pair**

In `tests/test_constants_parity.py` `CONSTANT_PAIRS`, after the `# Cluster naming` entry add:

```python
    # API groups
    "MANAGED_CLUSTER_API_GROUP": "CLUSTER_OPEN_CLUSTER_MANAGEMENT_IO",
```

- [ ] **Step 3: Run parity test**

Run: `python -m pytest tests/test_constants_parity.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add lib/constants.py tests/test_constants_parity.py
git commit -m "feat: add CLUSTER_BACKUP_* API constants and group parity guard"
```

### Task 3: Swap literals in modules/ and kube_client helpers

**Files:**
- Modify: `modules/activation.py`, `modules/finalization.py`, `modules/post_activation.py`, `modules/primary_prep.py`, `modules/backup_schedule.py`, `modules/restore_discovery.py`, `modules/decommission.py`, `lib/kube_client.py:980-996`

**Interfaces:**
- Consumes: constants from Task 2.

- [ ] **Step 1: Substitute per site class**

For each file, replace (adding the needed names to the existing `from lib.constants import (...)` block, or creating one matching each file's import style):

- ManagedCluster call sites (`version="v1"`, `plural="managedclusters"`):
  ```python
  group=MANAGED_CLUSTER_API_GROUP,
  version=MANAGED_CLUSTER_API_VERSION,
  plural=MANAGED_CLUSTER_PLURAL,
  ```
- BackupSchedule call sites (`version="v1beta1"`, `plural="backupschedules"`):
  ```python
  group=CLUSTER_BACKUP_API_GROUP,
  version=CLUSTER_BACKUP_API_VERSION,
  plural=BACKUP_SCHEDULE_PLURAL,
  ```
- Restore call sites (`version="v1beta1"`, `plural="restores"`):
  ```python
  group=CLUSTER_BACKUP_API_GROUP,
  version=CLUSTER_BACKUP_API_VERSION,
  plural=RESTORE_PLURAL,
  ```
- Manifest dicts:
  ```python
  "apiVersion": CLUSTER_BACKUP_API_VERSION_FULL,
  ```
- `lib/kube_client.py` `list_managed_clusters()`/`patch_managed_cluster()`: use the three `MANAGED_CLUSTER_*` constants (import from `lib.constants`, matching the module's existing import style).

- [ ] **Step 2: Verify zero literals remain in scope**

Run: `grep -rn 'cluster\.open-cluster-management\.io' modules/ lib/kube_client.py`
Expected: exactly one hit — `lib/kube_client.py:567` docstring.

- [ ] **Step 3: Run guardrail + targeted suites**

Run: `python -m pytest tests/test_api_literal_guardrails.py tests/test_constants_parity.py tests/test_activation.py tests/test_finalization.py tests/test_post_activation.py tests/test_primary_prep.py tests/test_backup_schedule.py tests/test_decommission.py tests/test_kube_client.py -q`
Expected: all PASS.

- [ ] **Step 4: Format and commit**

```bash
black --line-length 120 modules/*.py lib/constants.py lib/kube_client.py tests/test_api_literal_guardrails.py tests/test_constants_parity.py
isort --profile black --line-length 120 modules/*.py lib/constants.py lib/kube_client.py tests/test_api_literal_guardrails.py tests/test_constants_parity.py
git add -A
git commit -m "refactor: route ACM cluster API-group literals through lib.constants (R2-H2)"
```

### Task 4: Full verification, tracker update, draft PR

**Files:**
- Modify: `thermos-resolution-plan.md` (row PR 34 + Last Updated)

- [ ] **Step 1: Full test + lint gate**

Run: `./run_tests.sh`
Expected: PASS (record lane counts). If pre-existing unrelated failures appear, reproduce on clean `ansible` before attributing.

- [ ] **Step 2: Update tracker row 34**

Set status `ready_for_review`, record branch/worktree, verification commands and results; bump `Last Updated`.

- [ ] **Step 3: Commit, push, draft PR**

```bash
git add thermos-resolution-plan.md
git commit -m "docs: mark Thermos PR 34 ready for review in tracker"
git push -u origin fix/thermos-34-managed-cluster-constant
gh pr create --draft --base ansible --title "Thermos PR 34: route ACM API-group literals through constants (R2-H2)" --body "<summary + verification evidence>"
```
