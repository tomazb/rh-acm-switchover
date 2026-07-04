# PR 41: Shared Summary-Path Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 4× copy-pasted absolute-path Jinja with one `tomazb.acm_switchover.acm_abs_path` filter (R2-M5), byte-identical resolution.

**Architecture:** Per the approved design (`docs/superpowers/specs/2026-07-04-pr41-summary-path-dedup-design.md`): first collection filter plugin (`plugins/filter/paths.py`); call sites keep their `lookup('env', 'PWD')`, task names, and `when:` conditions.

**Tech Stack:** Python 3 (ansible filter plugin), pytest, YAML roles/playbooks, black/isort (line-length 120).

## Global Constraints

- Byte-identical resolution: `path if path.startswith('/') else f"{base_dir}/{path}"` — no normalization.
- `black --line-length 120` / `isort --profile black --line-length 120` on touched Python files.
- Base branch: `ansible` @ `73c76825`; PR branch `refactor/thermos-41-summary-path-dedup`.

---

### Task 1: Red-first filter unit + contract tests

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/filter/test_paths.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/test_summary_path_filter_contract.py`

**Interfaces:**
- Consumes (from Task 2): `acm_abs_path(path, base_dir) -> str` and `FilterModule` in `ansible_collections/tomazb/acm_switchover/plugins/filter/paths.py`.

- [ ] **Step 1: Write the filter unit tests**

```python
"""Unit tests for the collection's path filters."""

import pytest
from ansible.errors import AnsibleFilterError

from ansible_collections.tomazb.acm_switchover.plugins.filter.paths import FilterModule, acm_abs_path


def test_absolute_path_passes_through():
    assert acm_abs_path("/tmp/summary.json", "/work") == "/tmp/summary.json"


def test_relative_path_joins_base_dir_without_normalization():
    assert acm_abs_path("artifacts/summary.json", "/work") == "/work/artifacts/summary.json"
    # exact concatenation semantics of the historical inline expression
    assert acm_abs_path("./summary.json", "/work/") == "/work/./summary.json"


@pytest.mark.parametrize("bad", ["", None, 7])
def test_non_string_or_empty_path_raises(bad):
    with pytest.raises(AnsibleFilterError):
        acm_abs_path(bad, "/work")


def test_non_string_base_dir_raises():
    with pytest.raises(AnsibleFilterError):
        acm_abs_path("summary.json", None)


def test_filter_module_exposes_acm_abs_path():
    assert FilterModule().filters()["acm_abs_path"] is acm_abs_path
```

- [ ] **Step 2: Write the contract test**

```python
"""Contract: summary-path resolution goes through the shared filter (Thermos R2-M5)."""

from pathlib import Path

COLLECTION_ROOT = Path(__file__).resolve().parents[2]
SITES = (
    COLLECTION_ROOT / "roles" / "discovery" / "tasks" / "main.yml",
    COLLECTION_ROOT / "roles" / "decommission" / "tasks" / "main.yml",
    COLLECTION_ROOT / "roles" / "rbac_bootstrap" / "tasks" / "main.yml",
    COLLECTION_ROOT / "playbooks" / "argocd_manage_test.yml",
)


def test_all_summary_path_sites_use_the_shared_filter():
    for site in SITES:
        text = site.read_text()
        assert "acm_abs_path" in text, f"{site} must resolve the summary path via the shared filter"
        assert "startswith('/')" not in text, f"{site} still inlines the absolute-path expression"
```

- [ ] **Step 3: Run to verify they fail**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/filter/test_paths.py ansible_collections/tomazb/acm_switchover/tests/unit/test_summary_path_filter_contract.py -q`
Expected: filter tests FAIL at import (module missing); contract test FAIL on all four sites.

(If the new `tests/unit/plugins/filter/` directory needs an `__init__.py` to match sibling test packages, copy whatever `tests/unit/plugins/modules/` does.)

- [ ] **Step 4: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/tests/unit
git commit -m "test: add red tests for shared summary-path filter"
```

### Task 2: Implement the filter and convert the four sites

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/plugins/filter/paths.py` (exact code in the design spec)
- Modify: `ansible_collections/tomazb/acm_switchover/roles/discovery/tasks/main.yml:22-30`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/decommission/tasks/main.yml:83-91`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/tasks/main.yml:31-39`
- Modify: `ansible_collections/tomazb/acm_switchover/playbooks/argocd_manage_test.yml:32-40`

- [ ] **Step 1: Create the filter plugin** — code exactly as in the design spec.

- [ ] **Step 2: Convert the three `summary_path` sites**

Replace each block

```yaml
    _acm_summary_path_abs: >-
      {{
        summary_path
        if summary_path.startswith('/')
        else (lookup('env', 'PWD') ~ '/' ~ summary_path)
      }}
```

with

```yaml
    _acm_summary_path_abs: "{{ summary_path | tomazb.acm_switchover.acm_abs_path(lookup('env', 'PWD')) }}"
```

(preserving each site's indentation — the playbook site is nested two levels deeper).

- [ ] **Step 3: Convert the decommission site**

```yaml
    _acm_summary_path_abs: "{{ _acm_decommission_summary_path | tomazb.acm_switchover.acm_abs_path(lookup('env', 'PWD')) }}"
```

- [ ] **Step 4: Run the new tests + collection suite**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q`
Expected: all PASS.

- [ ] **Step 5: Format and commit**

```bash
black --line-length 120 ansible_collections/tomazb/acm_switchover/plugins/filter/paths.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/filter/test_paths.py ansible_collections/tomazb/acm_switchover/tests/unit/test_summary_path_filter_contract.py
isort --profile black --line-length 120 <same files>
git add -A
git commit -m "refactor: share summary-path resolution via acm_abs_path filter (R2-M5)"
```

### Task 3: Full verification, tracker update, draft PR

**Files:**
- Modify: `thermos-resolution-plan.md` (row PR 41)

- [ ] **Step 1: Full gate** — `./run_tests.sh`, expect PASS, record lane counts.

- [ ] **Step 2: Tracker + push + PR**

```bash
git add thermos-resolution-plan.md
git commit -m "docs: mark Thermos PR 41 ready for review in tracker"
git push -u origin refactor/thermos-41-summary-path-dedup
gh pr create --draft --base ansible --title "Thermos PR 41: shared summary-path resolution filter (R2-M5)" --body "<summary + verification evidence>"
```
