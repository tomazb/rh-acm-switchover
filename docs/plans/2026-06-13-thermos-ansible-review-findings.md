# Thermos Review #1 — `ansible` branch vs `main`

**Date:** 2026-06-13
**Scope:** Entire `ansible` branch relative to `main` (744 commits, 573 files, ~88,850 insertions / 10,794 deletions).
**Method:** Two parallel thermo-nuclear review passes — a security/bug branch audit and a code-quality audit — synthesized and deduplicated below.

> **Revision (post external review):** An external reviewer validated the findings against
> code, docs, parity tests, and Graphify. **B1 is downgraded from BLOCKER to minor cleanup** —
> the auto-MCO-delete behavior is already documented (CHANGELOG.md:173,
> `docs/deployment/rbac-requirements.md:203,259`, CLI help, and a collection parity test), so
> the original "undocumented, must document before merge" basis was incorrect. The only real
> residue is the dead instance field. **There is no ship-blocker on this branch.** H1 is also
> adjusted: parity tests are kept (they encode this repo's deliberate dual-support contract),
> not retired. Changes are reflected inline below.

---

## Executive summary

The branch is **unusually defensive**. The overwhelming majority of diffs *harden* the
system rather than introduce risk: `umask 077` on kubeconfig temp dirs and outputs,
per-context API clients that no longer mutate global config, an SSAR subresource fix, a
`preserveOnDelete` infrastructure-destruction safety gate, cluster-identity binding on
Argo CD resume, `--force` gates on stale/failed completed-state reruns, and shorter-lived
service-account tokens (48h → 24h).

**No Critical or High security/correctness bugs were found.** The real risk on this branch
is **structural debt**: duplicated sources of truth, heavy copy-pasted custom-resource access
boilerplate, and several files that have grown past the 1,000-line maintainability threshold
into god-modules.

**No ship-blocker.** The originally-flagged item (B1, deprecated
`--disable-observability-on-secondary`) is a minor cleanup: the behavior change is already
documented, and only a dead instance field remains. The highest-value work is debt reduction
(H2/H1), not a merge gate.

### Coverage and limitations

- **Fully inspected:** RBAC manifests (`deploy/rbac/`, `deploy/helm/`, `deploy/acm-policies/policy-rbac.yaml` added verbs), kubeconfig/token scripts, `lib/kube_client.py`, `lib/rbac_validator.py`, `lib/path_safety.py`, `lib/validation.py`, `lib/argocd_coordinator.py`, `lib/argocd_resume.py`, `lib/runtime_bootstrap.py`, `lib/workflow.py`, `lib/operation_runners.py`, `lib/report_artifacts.py`, `lib/constants.py`, and the high-churn modules `modules/finalization.py`, `modules/post_activation.py`, `modules/primary_prep.py`, `modules/decommission.py`. Sampled `modules/activation.py`, `acm_switchover.py`.
- **Not fully covered:** ~50k lines of test churn under `tests/`, `tests/release/` orchestrator framework, `modules/activation.py` internals, preflight validator internals, full `acm-policies/policy-rbac.yaml` body, and `lib/kube_client.py` / `lib/utils.py` / `lib/argocd.py` in depth.
- `gh` was denied in the review sandbox, so **no PR-thread / BugBot deduplication** was possible.

---

## Findings

Severity legend: **High** (plan before debt calcifies) · **Medium** · **Low/informational**.
Findings flagged by *both* review passes are marked **(both)**.

### Low — minor cleanup (was BLOCKER, downgraded after external review)

#### B1 — Dead instance field `disable_observability_on_secondary` **(both)**

`modules/finalization.py:197` changed the observability-teardown gate from:

```python
if self.disable_observability_on_secondary:
```

to:

```python
if self.primary and self.old_hub_action == "secondary" and self.primary_has_observability:
```

The flag is still parsed (`acm_switchover.py:284`) and stored (`finalization.py:85`) but is
**never read** by `finalize()`.

**What is real:** the stored `self.disable_observability_on_secondary` is dead state that
implies behavior it no longer has. This is the only residue worth fixing.

**What the original review got wrong:** the auto-MCO-delete behavior is **not** undocumented,
and the change is intentional and parity-aligned, not an accidental regression. Verified:

- `CHANGELOG.md:173` — "Python finalization and collection finalization now delete
  `MultiClusterObservability` automatically when the old hub is kept as `secondary` … the
  legacy `--disable-observability-on-secondary` switch is now a deprecated compatibility flag."
- `docs/deployment/rbac-requirements.md:203, 259`, `docs/operations/usage.md`,
  `docs/operations/quickref.md`, `docs/reference/validation-rules.md`, and collection docs
  document the new behavior.
- CLI help (`acm_switchover.py:287`) marks the flag deprecated and states deletion is automatic.
- A collection parity test explicitly asserts old-hub MCO deletion is **not** gated by
  `disable_observability_on_secondary`; targeted validation passed (10 passed).

**Fix (non-blocking):** remove the dead `self.disable_observability_on_secondary` assignment,
or replace it with a clear backward-compatibility comment. Keep the CLI flag and its
validation for compatibility; do **not** reintroduce gating on the deprecated flag. No runbook
change required (protected-runbook rules apply) unless an operator explicitly requests one.

---

### High — structural debt

#### H1 — Duplicated RBAC source-of-truth policed by parity tests **(both)**

`lib/rbac_validator.py:52-97` defines `OPERATOR_CLUSTER_PERMISSIONS` and
`VALIDATOR_CLUSTER_PERMISSIONS` as near-identical literal copies — the only delta is `patch`
on `managedclusters`. The same permission truth is mirrored a third and fourth time in
`scripts/setup-rbac.sh` and `deploy/rbac/*` manifests.

The branch adds four parity-test files (`test_constants_parity.py`,
`test_argocd_constants_parity.py`, `test_rbac_collection_parity.py`,
`test_validation_parity.py`, ~590 new lines) that keep these copies in sync. **Every
permission change is currently a 3-place edit.**

**Correction (post external review):** the parity tests are **not** a band-aid to retire —
they encode this repo's deliberate dual-support contract, where the Python tool, the Ansible
collection, and the deployed manifests are *independently owned* and must demonstrably agree.
The duplication to remove is the *intra-Python* copy (validator table vs operator table); the
cross-surface parity tests stay as guardrails. See the revised fix in H1 of the resolution
plan.

#### H2 — Massively duplicated custom-resource access boilerplate

The literal block

```python
group="cluster.open-cluster-management.io", version="v1beta1",
plural="restores", namespace=BACKUP_NAMESPACE
```

(and siblings for `backupschedules`, `backups`, `managedclusters`) is repeated **20 times in
`modules/finalization.py` alone**, **17 times in `modules/activation.py`**, and 30+ times
across modules. Representative sites in `finalization.py`: lines 295-300, 334-341, 433-439,
736-742, 789-795, 816-824, 1159-1165, 1175-1182, 1207-1213, 1295-1301, 1323-1330, 1372-1378,
1419-1425, 1449-1455.

A typo in any `version` or `plural` string is invisible to the type system. This is the
single highest-leverage simplification available.

#### H3 — Five files breach the 1,000-line rule; two are god-modules

| File | Lines | Problem |
|------|-------|---------|
| `modules/post_activation.py` | 1619 | Single `PostActivationVerification` class doing cluster-connection polling, klusterlet remediation, observability scale-up/restart/pod-health, metrics, auto-import cleanup, **plus** a 124-line kubeconfig loader/merger/cacher (`_load_kubeconfig_data`, lines 1331-1454, generic K8s plumbing belonging in `lib/`) and a ~320-line klusterlet-remediation subsystem (lines 747-1066). |
| `modules/finalization.py` | 1589 | One `Finalization` class spanning backup-schedule enable/verify/repair, restore cleanup/archival, MCH health, observability teardown, old-hub handling, auto-import reset, **plus** a hand-rolled, self-admittedly-incomplete cron parser (`_parse_cron_interval_seconds`, lines 610-659). |
| `lib/kube_client.py` | 1357 | Borderline; not decomposed in this review. |
| `modules/activation.py` | 1112 | Breach. |
| `lib/rbac_validator.py` | 1054 | Breach (see H1). |

`acm_switchover.py` (1301 lines, ~25 top-level `run_*`/`_phase_*` functions, lines 407-1129)
is a borderline entry point. Across `finalization.py`, `post_activation.py`, `validation.py`,
and `backup_validators.py`, **8 functions carry `# noqa: C901`** complexity suppressions —
suppressing the linter instead of decomposing is the tell that complexity is unmanaged.

---

### Medium

#### M1 — Parallel-but-divergent workflow definitions

`lib/operation_runners.py` — `run_switchover_impl` (lines 100-197) and
`run_restore_only_impl` (lines 200-286) are ~90% structurally identical: same
`handle_completed_state` → `handle_failed_state` → `validate_only` → `phase_flow` →
`dry_run` → `log_operation_completion` skeleton, differing only in the phase-flow tuple
(139-166 vs 241-255) and message constants. A change to the completion/failure sequence must
currently be made twice.

#### M2 — `lib/constants.py` absorbing UI string-table sprawl

`lib/constants.py:15-72` now holds 27 `WORKFLOW_*` message constants plus `OPERATION_LABEL`,
`OPERATION_NOUN`, `PHASE_FLOW_NAME`, and `*_NEXT_STEP_MESSAGES` tuples. Hoisting every log
line into a constant for parity-testability (feeds H1) bloats `constants.py` into a
presentation layer and makes `workflow.py`'s import block a 30-line wall (lines 9-40).
Banner / "next steps" text is presentation, not configuration.

#### M3 — Inconsistent dry-run guards

Two idioms coexist for the same concern: the declarative `@dry_run_skip(...)` decorator
(used throughout `post_activation.py` / `finalization.py`) **and** inline `if self.dry_run:`
early-returns (`post_activation.py:462-464, 690-692`; `finalization.py:1034-1036, 1053-1055,
1220-1222`). A reader cannot trust the decorator alone to determine dry-run behavior.

#### M4 — Leaky abstraction reaching into `StateManager` internals

`finalization.py:566` —
`getattr(self.state, "state", {}).get("completed_steps", [])` reaches past `StateManager`'s
public API into its private `.state` dict and iterates raw step records. The defensive
`getattr(..., "state", {})` signals the author knew this was reaching through the
abstraction. `StateManager` should expose `get_completed_steps()` / `get_step_timestamp(name)`.

#### M5 — 160-line method with deep state-reconciliation nesting

`lib/argocd_coordinator.py` — `ArgoCDPauseCoordinator.pause_hubs` (lines 120-283) nests
`for hub` → `for impact` → 4-deep branching on
`existing_entry` / `pause_applied` / `has_automated` / `result.patched` / `result.error` /
`patch_applied`. The result-handling block (239-281) with its tri-state
`patch_applied is True/False/None` mutation should be extracted to a
`_reconcile_pause_result` method. High cyclomatic complexity in a correctness-critical
durable-pause-state path.

---

### Low / informational

- **L1** — `path_safety.validate_safe_filesystem_path` loosened the parent-existence
  requirement: `_nearest_existing_ancestor` now walks up to any existing ancestor and permits
  arbitrarily deep non-existent suffixes. The safe-root containment check (`_allowed_root` via
  `commonpath`) and symlink-escape rejection still hold, so net security posture is preserved
  — but confirm the relaxation is intended and covered by `tests/test_path_safety.py`.
- **L2** — `post_activation.py` `_restart_klusterlet` now derives the klusterlet Deployment
  namespace from the bootstrap-secret namespace rather than the hardcoded
  `MANAGED_CLUSTER_AGENT_NAMESPACE`. Correct for standard ACM (co-resident); add a one-line
  comment noting the co-residence assumption.
- **L3** — `deploy/helm/.../namespace.yaml` dropped `app.kubernetes.io/managed-by: Helm`
  during the inline-labels refactor. Cosmetic; may affect tooling that filters Helm-managed
  namespaces by selector.
- **L4** — Host extraction via `re.sub(r"https://([^:/]+).*", r"\1", url)` is duplicated 4×
  (`post_activation.py:1480, 1487, 1605-1606`) and silently returns the input unchanged on
  non-match (e.g. `http://` or bare host). Extract `lib.utils.host_from_url()` with explicit
  handling.
- **L5** — `operation_runners.py` type hints are almost entirely `Any`
  (`args: Any, state: Any, primary: Optional[Any]`, lines 77-108) despite the real types
  (`argparse.Namespace`, `StateManager`, `KubeClient`) being known and used precisely in
  `workflow.py`.
- **L6** — `post_activation.py:1286` uses `import time as time_module` inside
  `_restart_klusterlet` while the module already imports `time` at top (line 12). Needless
  local shadow-avoidance import.
- **L7** — `finalization.py:1311-1316, 1388-1392` use `%`-style formatting inside
  `raise SwitchoverError(... % (...))` while the rest of the file uses f-strings.

---

## Verified-good (high-risk areas confirmed safe; no action)

- `lib/kube_client.py`: per-context `new_client_from_config(persist_config=False)` removes
  global-config mutation; `create_custom_resource` retries only *named* creates (unnamed
  creates no longer retry, eliminating duplicate-resource risk on transient errors) with
  proper 409 re-read reconciliation.
- `lib/rbac_validator.py`: SSAR `resource`/`subresource` split fixes `statefulsets/scale`
  checks; validator secret verbs `get`/`create`/`patch` match the post_activation
  patch-or-create refactor.
- `modules/decommission.py`: `preserveOnDelete=true` safety gate refuses ManagedCluster
  deletion when matching Hive ClusterDeployments are unsafe or the relationship is ambiguous.
- `lib/argocd_resume.py`: resume binds to live `kube-system` UID identities and `--force`-gates
  legacy/wrong-hub state, preventing resume against the wrong cluster.
- Scripts: `umask 077` on temp dirs and merged-kubeconfig output, `install -d -m 700`,
  CA-data validation in `generate-sa-kubeconfig.sh`, `setup-rbac.sh` deprecation warning,
  token default centralized to `DEFAULT_TOKEN_DURATION=24h` (down from 48h).
- `lib/workflow.py`: stale and failed completed-state reruns both require `--force` (raise
  `SwitchoverError` otherwise).
- RBAC additions across raw/helm/policy are read-mostly (`get`/`list`); scoped `delete`/`patch`
  grants match documented operations; no wildcards; validator custom rules are
  Helm-enforced read-only.

---

## Proposed resolution plan

Ordered by leverage (code deleted + risk removed per unit effort). Items 2 and 3 delete the
most code. **No item is a merge gate.**

### Phase 0 — B1 cleanup (non-blocking)

1. **Resolve B1.** Remove the dead `self.disable_observability_on_secondary` assignment in
   `finalization.py:85` (and any other dead references), or replace it with a clear
   backward-compatibility comment. Keep the CLI flag, its validation, and the deprecated help
   text. Do **not** reintroduce gating on the flag. No CHANGELOG/runbook change needed — the
   behavior is already documented (CHANGELOG.md:173, `docs/deployment/rbac-requirements.md`,
   CLI help) and asserted by a collection parity test.

### Phase 1 — High-leverage debt reduction

2. **Collapse the CR-access boilerplate (H2).** Introduce thin typed accessors on
   `KubeClient` (or a small resource registry), e.g. `client.restores(namespace=...)`,
   `client.backupschedules(...)`, `client.managedclusters()`. Each accessor encapsulates the
   `group`/`version`/`plural` tuple once. Mechanically replace the ~67 call sites. This
   removes the largest typo surface and the most code on the branch.

3. **Derive the validator RBAC table from the operator table (H1).** Define
   `OPERATOR_CLUSTER_PERMISSIONS` once and compute `VALIDATOR_CLUSTER_PERMISSIONS` by
   stripping mutating verbs (with the documented `managedclusters: patch` exception expressed
   explicitly). This removes the *intra-Python* duplication only. **Keep** the Python / Ansible
   collection / manifest surfaces independently owned and **keep the cross-surface parity
   tests** as guardrails — they encode the repo's dual-support contract, not redundant
   duplication.

### Phase 2 — Decompose god-modules (H3)

4. **`post_activation.py` (1619 → target < 1000):** extract the kubeconfig loader/merger/cacher
   (lines 1305-1508) into `lib/` (it has zero post-activation specificity), and extract the
   klusterlet-remediation subsystem (lines 747-1066) into its own module/class.
5. **`finalization.py` (1589 → target < 1000):** extract restore cleanup/archival and the
   `_parse_cron_interval_seconds` cron parser (610-659) — the latter to a dedicated util or a
   real cron library — out of the `Finalization` class.
6. Re-evaluate the `# noqa: C901` suppressions after extraction; the decompositions above
   should retire most of them. Treat any remaining suppression as a follow-up decomposition
   target, not a permanent state.

### Phase 3 — Medium cleanups

7. **M1:** collapse `run_switchover_impl` / `run_restore_only_impl` into one
   flow-descriptor-driven runner taking a `(phase_flow, messages)` descriptor.
8. **M2:** move `WORKFLOW_*` / `*_NEXT_STEP_MESSAGES` presentation strings out of
   `constants.py` into a dedicated `messages.py` co-located with workflow logic.
9. **M3:** standardize on a single dry-run mechanism (the `@dry_run_skip` decorator);
   replace remaining inline `if self.dry_run:` guards.
10. **M4:** add `StateManager.get_completed_steps()` / `get_step_timestamp(name)` and remove
    the `getattr(self.state, "state", {})` reach-through at `finalization.py:566`.
11. **M5:** extract `_reconcile_pause_result` from `pause_hubs` in `argocd_coordinator.py`.

### Phase 4 — Low / informational

12. Address L1-L7 opportunistically: confirm the `path_safety` relaxation is tested (L1),
    add the namespace co-residence comment (L2), restore the Helm `managed-by` label (L3),
    extract `lib.utils.host_from_url()` (L4), tighten `operation_runners.py` type hints (L5),
    drop the needless local `time` import (L6), normalize the `%`-format `raise` sites (L7).

---

## Bottom line

**No merge gate.** The branch is defensive and well-documented; the originally-flagged B1 is a
one-line dead-field cleanup, and the behavior it touched is already documented and parity-tested.
The remaining work is structural debt — **H2 then H1 first**, because they delete the most code
and remove the most typo surface. H1 stays *inside* Python (derive validator from operator); the
cross-surface parity tests are retained as the repo's dual-support contract, not retired.
