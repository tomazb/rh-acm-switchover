# PR 41 Design: Shared Summary-Path Resolution Filter (R2-M5)

**Date:** 2026-07-04
**Finding:** `R2-M5` from
[`docs/plans/2026-07-02-thermos-ansible-review-2-findings.md`](../../plans/2026-07-02-thermos-ansible-review-2-findings.md)
**Tracker row:** PR 41 in [`thermos-resolution-plan.md`](../../../thermos-resolution-plan.md)
**Branch:** `refactor/thermos-41-summary-path-dedup`

## Problem

Verified at `ansible` @ `73c76825`: the same absolute-path resolution Jinja

```jinja
{{ x if x.startswith('/') else (lookup('env', 'PWD') ~ '/' ~ x) }}
```

is copy-pasted at four locations, each under a task named
"Resolve summary path to absolute":

- `roles/discovery/tasks/main.yml:22` (input `summary_path`)
- `roles/decommission/tasks/main.yml:83` (input
  `_acm_decommission_summary_path`, after a normalize step)
- `roles/rbac_bootstrap/tasks/main.yml:31` (input `summary_path`)
- `playbooks/argocd_manage_test.yml:32` (input `summary_path`)

A change to the resolution rule (e.g. Windows separators, `~` expansion,
or trailing-slash handling) must be applied four times.

## Approaches considered

1. **Collection filter plugin (chosen)** — the collection's first
   `plugins/filter/` module, exposing
   `tomazb.acm_switchover.acm_abs_path(path, base_dir)`. Call sites become
   `{{ x | tomazb.acm_switchover.acm_abs_path(lookup('env', 'PWD')) }}`.
   The `PWD` lookup stays at the call site (filters cannot run lookups),
   the transformation logic lives once in Python and is directly
   pytest-testable. This is the "single set_fact/filter used everywhere"
   fix the finding names.
2. **Shared tasks file via `include_role` with `tasks_from`** — adds role
   plumbing and fact-name coupling at each call site for a one-expression
   dedup. Rejected.
3. **Leave inline** — keeps the 4× copy. Rejected.

## Design

New `ansible_collections/tomazb/acm_switchover/plugins/filter/paths.py`:

```python
"""Path filters for the ACM switchover collection."""

from __future__ import annotations

from ansible.errors import AnsibleFilterError


def acm_abs_path(path, base_dir):
    """Return path unchanged when absolute; otherwise join it to base_dir.

    Reproduces the historical inline expression byte-for-byte:
    path if path.startswith('/') else f"{base_dir}/{path}" — no
    normalization, no trailing-slash handling.
    """
    if not isinstance(path, str) or not path:
        raise AnsibleFilterError("acm_abs_path requires a non-empty string path")
    if not isinstance(base_dir, str):
        raise AnsibleFilterError("acm_abs_path requires a string base_dir")
    return path if path.startswith("/") else f"{base_dir}/{path}"


class FilterModule(object):
    def filters(self):
        return {"acm_abs_path": acm_abs_path}
```

All four call sites change their `set_fact` value to:

```yaml
    _acm_summary_path_abs: "{{ <input_var> | tomazb.acm_switchover.acm_abs_path(lookup('env', 'PWD')) }}"
```

with `<input_var>` = `summary_path` (three sites) or
`_acm_decommission_summary_path` (decommission). Task names, `when:`
conditions, and downstream consumers are untouched.

Error behavior note: the historical expression raised a Jinja
`UndefinedError`/attribute error on non-string input; the filter raises
`AnsibleFilterError` with a clear message — a strictly better failure on
an already-failing path (all four sites gate on the variable being
defined/non-empty).

## Testing

Red-first:

- Unit tests (pytest, direct import of the filter function):
  absolute passthrough, relative join (exact concatenation, no
  normalization), empty/non-string raises `AnsibleFilterError`, and
  `FilterModule().filters()` exposes `acm_abs_path`.
- Contract test asserting all four YAML files use `acm_abs_path` and that
  the inline `startswith('/')` resolution expression no longer appears in
  any of them.

Existing role/playbook contract suites characterize the surrounding
behavior.

## Acceptance criteria

1. One resolution implementation; `grep -rn "startswith('/')"
   ansible_collections/.../roles ansible_collections/.../playbooks`
   returns nothing.
2. New filter unit + contract tests pass; collection unit suite passes.
3. Touched-file `black`/`isort`, `git diff --check` clean; full
   `./run_tests.sh` passes.

## Assumptions (autonomous session)

Operator pre-approved via the tracker queue. Parity: the Python CLI has
no equivalent summary-path CLI surface, so no parity-matrix change; the
filter is collection-internal (documented only via its docstring, not
added to user-facing docs, since `_acm_summary_path_abs` is a private
fact).
