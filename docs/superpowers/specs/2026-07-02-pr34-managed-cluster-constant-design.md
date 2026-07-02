# PR 34 Design: Route ManagedCluster/Backup CR API Literals Through Constants (R2-H2)

**Date:** 2026-07-02
**Finding:** `R2-H2` (sharper framing of the still-open `H2`) from
[`docs/plans/2026-07-02-thermos-ansible-review-2-findings.md`](../../plans/2026-07-02-thermos-ansible-review-2-findings.md)
**Tracker row:** PR 34 in [`thermos-resolution-plan.md`](../../../thermos-resolution-plan.md)
**Branch:** `fix/thermos-34-managed-cluster-constant`

## Problem

`lib/constants.py` defines `MANAGED_CLUSTER_API_GROUP = "cluster.open-cluster-management.io"`
(plus `MANAGED_CLUSTER_API_VERSION` / `MANAGED_CLUSTER_PLURAL`) specifically to
prevent hardcoded API-group literals, but only `modules/preflight_coordinator.py`
uses it. A fresh audit of `modules/*.py` (this branch, `ansible` @ `de943b0c`)
found **49 hardcoded occurrences** of the literal:

| Context | Count | CR kind(s) |
| --- | --- | --- |
| `group="cluster.open-cluster-management.io"` kwargs with `version="v1"`, `plural="managedclusters"` | 9 | ManagedCluster |
| `group=...` kwargs with `version="v1beta1"`, `plural="backupschedules"` | 14 | BackupSchedule |
| `group=...` kwargs with `version="v1beta1"`, `plural="restores"` | 21 | Restore |
| `"apiVersion": "cluster.open-cluster-management.io/v1beta1"` manifest dicts | 5 | Restore (4), BackupSchedule (1) |

Files: `activation.py` (20 lines), `finalization.py` (17), `post_activation.py` (4),
`primary_prep.py` (3), `backup_schedule.py` (3), `restore_discovery.py` (1),
`decommission.py` (1). A typo or upstream group rename would today require
touching ~49 scattered sites, and grep is the only defense.

Key nuance discovered during validation: the same API group serves three CR
kinds — ManagedCluster (`v1`) and the ACM cluster-backup-operator CRDs
BackupSchedule/Restore (`v1beta1`). Blindly substituting
`MANAGED_CLUSTER_API_GROUP` at backup/restore call sites would be misleading.

## Approaches considered

1. **Blind swap** — use `MANAGED_CLUSTER_API_GROUP` for all 49 `group=` sites,
   leave `version=`/`plural=` literals. Rejected: misleading name at
   backup/restore sites; leaves 35 `v1beta1` and 35 plural literals duplicated.
2. **Semantic constants (chosen)** — introduce backup-CR companion constants
   aliased to the same group value, plus version/plural constants, and swap all
   49 sites. Mechanical, low-risk, self-documenting call sites.
3. **Custom-resource access helpers** — e.g. `KubeClient.list_managed_clusters()`.
   Rejected for this slice: that is the separately queued `H2` follow-up
   ("add custom-resource access helpers and collapse call-site boilerplate");
   PR 34 stays a mechanical constants-routing diff.

## Design

### New constants (`lib/constants.py`, next to the existing ManagedCluster block)

```python
# ACM cluster-backup-operator CRDs (BackupSchedule, Restore) share the
# ManagedCluster API group but use v1beta1.
CLUSTER_BACKUP_API_GROUP = MANAGED_CLUSTER_API_GROUP
CLUSTER_BACKUP_API_VERSION = "v1beta1"
CLUSTER_BACKUP_API_VERSION_FULL = f"{CLUSTER_BACKUP_API_GROUP}/{CLUSTER_BACKUP_API_VERSION}"
BACKUP_SCHEDULE_PLURAL = "backupschedules"
RESTORE_PLURAL = "restores"
```

`CLUSTER_BACKUP_API_GROUP` is an alias assignment (single source of truth), not
a second copy of the string.

### Call-site substitution (all 49 sites in `modules/*.py`)

- ManagedCluster sites: `group=MANAGED_CLUSTER_API_GROUP,
  version=MANAGED_CLUSTER_API_VERSION, plural=MANAGED_CLUSTER_PLURAL`.
- BackupSchedule sites: `group=CLUSTER_BACKUP_API_GROUP,
  version=CLUSTER_BACKUP_API_VERSION, plural=BACKUP_SCHEDULE_PLURAL`.
- Restore sites: `group=CLUSTER_BACKUP_API_GROUP,
  version=CLUSTER_BACKUP_API_VERSION, plural=RESTORE_PLURAL`.
- Manifest dicts: `"apiVersion": CLUSTER_BACKUP_API_VERSION_FULL`.

Behavior is unchanged: every constant resolves to the exact literal it replaces.

### Guardrail test (new, red-first)

`tests/test_api_literal_guardrails.py`: statically scans `modules/*.py` source
text and asserts zero occurrences of the raw literal
`cluster.open-cluster-management.io` (constants must be imported from
`lib.constants`). Written before the substitution so it starts red at 49
violations and proves the sweep is complete.

### Parity hardening

Add `"MANAGED_CLUSTER_API_GROUP": "CLUSTER_OPEN_CLUSTER_MANAGEMENT_IO"` to
`CONSTANT_PAIRS` in `tests/test_constants_parity.py` so the Python group
constant can never drift from the collection's
`plugins/module_utils/constants.py` equivalent.

## Scope boundaries

- **In scope:** `modules/*.py` (49 sites), `lib/kube_client.py`
  `list_managed_clusters()`/`patch_managed_cluster()` (2 behavioral sites,
  lines 983/991 — same fix class), `lib/constants.py` additions, the two
  test files above.
- **Out of scope:** `lib/rbac_validator.py` literals (8 — RBAC permission-table
  rows, owned by the queued `H1` unification), the `lib/kube_client.py:567`
  docstring example (documentation text, not a call site),
  `tests/**` fixture literals (test data, intentionally literal), and the
  Ansible collection side (already routed through
  `CLUSTER_OPEN_CLUSTER_MANAGEMENT_IO`).
- No behavior change; no parity change (values identical on both surfaces).

## Acceptance criteria

1. `grep -rn 'cluster\.open-cluster-management\.io' modules/` returns zero rows.
2. New guardrail test passes and fails if a literal is reintroduced.
3. `tests/test_constants_parity.py` includes the group-constant pair and passes.
4. Targeted suites pass: `tests/test_activation.py`, `tests/test_finalization.py`,
   `tests/test_post_activation.py`, `tests/test_primary_prep.py`,
   `tests/test_backup_schedule.py`, `tests/test_restore_discovery.py`,
   `tests/test_decommission.py`, `tests/test_constants_parity.py`.
5. Touched-file `black --check --line-length 120` and
   `isort --check-only --profile black --line-length 120` pass;
   `git diff --check` clean.

## Assumptions (autonomous session)

- Operator pre-approved this slice via the tracker queue ordering; the design
  gate is satisfied by this spec rather than an interactive Q&A.
- Constant naming (`CLUSTER_BACKUP_*`) follows the existing
  `MANAGED_CLUSTER_*` block style; if review prefers per-kind names
  (`RESTORE_API_VERSION` etc.), the substitution is mechanical to adjust.
