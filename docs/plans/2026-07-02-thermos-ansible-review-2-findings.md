# Thermos Deep Review #2 — `ansible` Branch (2026-07-02)

## Scope and method

Full-branch review of `ansible` vs `main` (798 commits, 684 files changed, ~123.5k
insertions at time of review) using the `thermo-nuclear-review-subagent` (bugs,
security, breaking changes) and `thermo-nuclear-code-quality-review-subagent`
(maintainability/structure) rubrics from the Thermos Ansible review skill.

The diff was chunked into 8 functionally scoped areas and reviewed with paired
bug/security + quality subagents per chunk (14 subagent passes total, one retried
after a stall — see below). Subagents worked from `git diff main...ansible -- <path>`
plus direct file reads; they were given the prior review
([`2026-06-13-thermos-ansible-review-findings.md`](2026-06-13-thermos-ansible-review-findings.md))
and told to confirm/refute previously tracked items rather than re-report them, and
to focus on **new** issues and **new debt introduced since that review** (much of
this branch's `main`-diff predates the prior review's own base commit).

Chunks:

1. RBAC validators + supply-chain scripts (`lib/rbac_validator.py`, `check_rbac.py`,
   `deploy/rbac/`, `deploy/helm/`, `scripts/setup-rbac.sh`,
   `scripts/generate-merged-kubeconfig.sh`, collection `rbac_bootstrap` role,
   `acm_rbac_validate.py`/`acm_rbac_bootstrap.py`, `container-bootstrap/Containerfile`)
2. Core Python libraries (`lib/kube_client.py`, `lib/utils.py`, `lib/validation.py`,
   `lib/waiter.py`, `lib/argocd.py`, `lib/argocd_coordinator.py`,
   `lib/gitops_detector.py`, `lib/operation_runners.py`, `lib/cli_outcomes.py`,
   `lib/workflow.py`, `lib/constants.py`, `lib/exceptions.py`)
3. Python workflow modules (`modules/primary_prep.py`, `modules/activation.py`,
   `modules/post_activation.py`, `modules/finalization.py`,
   `modules/decommission.py`, `modules/backup_schedule.py`,
   `modules/restore_discovery.py`, `acm_switchover.py`)
4. Ansible collection roles + playbooks (`ansible_collections/tomazb/acm_switchover/roles/`,
   `ansible_collections/tomazb/acm_switchover/playbooks/`)
5. Ansible collection plugins (`plugins/modules/`, `plugins/module_utils/`,
   `plugins/action/checkpoint_phase.py`)
6. Release validation framework (`tests/release/` — orchestrator, adapters,
   checks, scenarios, profiles; ~37k lines / 163 files, sampled structurally)
7. Test suites (root `tests/` and collection `tests/unit/`) — safety-coverage pass
   (checkpoint/resume, wrong-context, check-mode, RBAC denial, destructive-op tests)
8. Docs and parity artifacts (`docs/ansible-collection/parity-matrix.md`,
   `behavior-map.md`, coexistence/cli-migration docs, `CHANGELOG.md`) — drift pass

### Coverage note

The first pass of the chunk-6 quality subagent (release validation framework)
stalled with zero recorded progress for ~50 minutes (vs. 20–56 turns for every
other pass) and was treated as hung. It was replaced with a narrower, explicitly
bounded retry (orchestrator + all of `adapters/` read in full, `checks/`/
`scenarios/` characterized by `wc -l` size distribution plus a 4-file
representative sample: `artifact_reuse.py`, `rbac_certification.py`, `soak.py`,
`catalog.py`) rather than re-running the original unbounded prompt a second
time. The retry completed successfully and its results are folded into the
findings below (R2-H4, R2-M6–R2-M8, R2-L9). `tests/release/static_gates.py`,
`lab_readiness.py`, `metadata.py`, `runtime_parity.py`, and all individual
`checks/`/`scenarios/` files beyond the 4 sampled were **not** read — this
remains a sampled, not exhaustive, pass, consistent with the prior review's own
admission that `tests/release/` is large and only lightly covered.

### Disputed-finding resolution

Chunk 5's two subagents disagreed on severity for check-mode `changed` handling
in `acm_backup_schedule.py`, `acm_restore_info.py`, and `acm_preflight_report.py`.
This was independently re-verified against source (not just subagent output) — see
finding **R2-M1** below for the resolution: the bug/security subagent's core claim
(that `acm_preflight_report.py` is a distinct, more serious bug than its siblings)
is correct and independently confirmed by reading `write_json_artifact`/`write_report`;
the quality subagent's claim that all three modules share one bug class is only
partially right — there is a real, but different and lower-severity, issue in the
other two, rooted in how their `changed` field interacts with the collection's
documented "native Ansible check mode is non-mutating even when `mode: execute`"
contract (`docs/variable-reference.md`), not in the plugins themselves.

## Findings

IDs use the `R2-` prefix (Review #2) to avoid colliding with the existing
`F1`–`F44` and `B1`/`H1`–`H3`/`M1`–`M5`/`L1`–`L7` IDs from the 2026-06-13 review,
which remain the authority for prior findings. Where a subagent reconfirmed a
prior ID with no new information, it is listed under "Reconfirmed prior findings"
instead of given a new ID.

### High severity

**R2-H1 — Unbounded delete API calls on the PRIMARY_PREP critical path can hang indefinitely**
`lib/kube_client.py:483-527` (`delete_configmap`, `delete_pod`) and
`modules/primary_prep.py:189-195` (`delete_custom_resource(...)` for the ACM
≤2.11 BackupSchedule delete-based pause) never pass a request timeout. Verified:
`delete_configmap`/`delete_pod` call `delete_namespaced_config_map`/
`delete_namespaced_pod` directly with no `_request_timeout`, unlike
`delete_custom_resource` which accepts an optional `timeout_seconds` (docstring:
"Prevents hanging on stuck API calls") — but the one safety-critical caller in
`primary_prep.py` doesn't pass it. A hung API server or network partition during
this step can block the entire PRIMARY_PREP phase with no operator-visible
timeout, indefinitely. Fix: thread `timeout_seconds` through `delete_configmap`/
`delete_pod`, and pass an explicit timeout from `primary_prep.py`'s
`delete_custom_resource` call, consistent with other mutation call sites.
Effort: S.

**R2-H2 — `MANAGED_CLUSTER_API_GROUP` constant is orphaned (used in 1 of ~48 sites)**
Verified via grep: the constant is referenced only in `modules/preflight_coordinator.py`
(2 occurrences: import + 1 use). The literal API group string it represents is
hardcoded independently across `modules/activation.py`, `modules/post_activation.py`,
`modules/finalization.py`, and `modules/decommission.py` (~48 total occurrences
of the literal per the quality subagent's count). This is worse than a simple
"still some duplication" note — the constant exists specifically to prevent this
and is not doing its job outside the one module it was added for. Fix: replace
literal occurrences with the constant across `modules/`. Low risk, high value —
good candidate for an early, easy PR. Effort: S.

**R2-H3 — Ansible RBAC validation playbook duplicates ~140 lines between primary/secondary**
`ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_rbac.yml`
(or equivalent RBAC validation task file) contains near-duplicate primary-hub and
secondary-hub validation blocks. Same root cause class as the still-open Python
`H1` (`lib/rbac_validator.py` primary/secondary duplication) but on the Ansible
side — this is new debt introduced in this branch, not previously tracked.
Fix direction: parameterize by hub role and loop, mirroring whatever unification
approach is chosen for Python `H1` to keep the two sides in parity. Effort: M.

### Medium severity

**R2-M1 — Check-mode `changed` reporting is inconsistent across three "plan" collection modules, for two different reasons**
Verified by reading source directly (not just subagent claims):

- `acm_preflight_report.py` (lines ~152-159): `write_report()`/`write_json_artifact()`
  compute an *accurate* `changed` value even under `check_mode` (the diff-based
  check happens before the check-mode gate), but `main()` then explicitly does
  `if module.check_mode: changed = False`, discarding the correct value and always
  reporting `changed=False` in check mode even when the report would genuinely be
  created/updated. Its sibling `acm_report_artifact.py` has no such override and
  reports the accurate value. This is a real, self-contained bug — one module
  computes the right answer and throws it away. **Confirmed independently by
  reading both modules side by side.**
- `acm_backup_schedule.py` (`exit_json(changed=(operation["action"] != "none")
  and not module.check_mode, ...)`) and `acm_restore_info.py` (same
  `and not module.check_mode` pattern) always force `changed=False` under Ansible's
  native check mode. Traced consumption: the owning roles
  (`primary_prep/tasks/pause_backups.yml`, `activation/tasks/activate_restore.yml`)
  do **not** rely on the plugin's own `changed` for their published
  `acm_switchover_pause_backups_result.changed` /
  `acm_switchover_restore_activation_result.changed` — those are recomputed from
  real task results plus a fallback that only fires when the collection's own
  `acm_switchover_execution.mode == 'dry_run'` (a custom variable, distinct from
  Ansible's native `--check` flag). Because `docs/variable-reference.md` explicitly
  documents "Native Ansible check mode is non-mutating even when [mode] is
  `execute`" as a supported contract, an operator running
  `ansible-playbook --check` against `mode: execute` gets a **misleadingly
  `changed: false`** published result for pause/activation even when a real run
  would change something — the dry-run fallback branch never fires because
  `acm_switchover_execution.mode` isn't `dry_run`. This is real but architecturally
  different from the `acm_preflight_report.py` bug: it requires wiring the
  role-level aggregation to also treat `ansible_check_mode`/`module.check_mode`
  as a "would change" signal, not just a plugin-level one-line fix.

  Net: the bug/security subagent's core distinction (the preflight-report bug is
  a different and more direct issue than the other two) is correct; the quality
  subagent's instinct that something is still wrong with the other two was also
  right, but for a role-aggregation reason, not a plugin-logic reason. Fix
  `acm_preflight_report.py` first (trivial), then fix or explicitly document the
  native-check-mode limitation for `pause_backups.yml`/`activate_restore.yml`.
  Effort: S (preflight-report) + M (role aggregation fix or doc caveat).

**R2-M2 — Crash-resume can bypass the pre-activation Velero-restore staleness guard**
In `modules/activation.py`, a hard crash between verifying restore freshness and
completing activation, followed by a resume, can re-enter the activation step
without re-running the staleness check that would normally catch a stale/replayed
restore. This is a genuine design gap for the crash-mid-verification case
specifically (not the already-hardened idempotent-rerun case). Needs a resume-path
re-validation of restore staleness before completing activation on resume.
Effort: M.

**R2-M3 — `_wait_for_restore_deletion` / `_wait_for_primary_restore_deletion` near-duplicate**
`modules/activation.py` and `modules/finalization.py` each contain a near-verbatim
polling method for waiting on Restore-resource deletion. Same shape as other
already-tracked duplication (`M2`); candidate for extraction into `lib/waiter.py`
alongside that work rather than as a separate one-off. Effort: S.

**R2-M4 — `lib/utils.py` `REPORT_PHASE_NAMES` and `lib/workflow.py` `_CANONICAL_RESUME_START_PHASES` are byte-identical duplicate dicts**
Straightforward extraction into a single shared constant/mapping. Low risk,
mechanical. Effort: S.

**R2-M5 — Ansible summary-path resolution logic duplicated across 4 role/playbook locations**
Quality subagent identified the same `report_dir`/summary-path Jinja resolution
logic copy-pasted in four places across collection roles/playbooks. Fix:
factor into a single `set_fact`/filter used everywhere. Effort: M (touches
several roles; needs parity-preserving verification against
`docs/ansible-collection/parity-matrix.md` shared-report-artifact behavior).

### High severity (release validation framework, chunk 6 bounded retry)

**R2-H4 — `tests/release/orchestrator.py` is 1199 lines on its first commit, with a 335-line god-function containing a triplicated short-circuit pattern**
Verified by the bounded retry subagent reading the file in full. `_run_release_certification` (lines ~835-1170) has three near-identical early-return blocks (lines ~899-921, 956-974, 1017-1039) that each build a `not_applicable` runtime-parity/final-baseline artifact pair and call `_finalize_run` with the same long argument list, differing only in the `mandatory_argocd` expression and how far `results` had accumulated. Unlike the Python CLI's `H3` (legacy files that grew over time), this file crossed the 1000-line threshold and introduced this duplication pattern in its very first commit on this branch — pure new debt, not inherited. Fix: extract a `_short_circuit_finalize(...)` helper; the three call sites collapse to one line each. Effort: S (1-2 hours per the subagent's estimate).

### Medium severity (release validation framework, chunk 6 bounded retry)

**R2-M6 — Three release-adapter implementations (`ansible.py`, `bash.py`, `python_cli.py`) duplicate ~70% of their `execute()` logic despite an existing shared contract in `adapters/common.py`**
`common.py` already defines the right abstraction (`StreamAdapter` Protocol, `StreamResult`/`AssertionRecord`/`ReportArtifact` dataclasses), but the actual subprocess-run/timeout/artifact-write/assertion-build logic was never factored into it — each adapter re-implements it nearly line-for-line (`ansible.py:178-306`, `bash.py:76-203`, `python_cli.py:167-295`). A future bugfix (timeout wording, a new assertion type, redaction handling) has to be applied identically in three places. Fix: add a `run_stream_subprocess(...)` helper to `common.py`; each adapter's `execute()` shrinks to building its command/env and calling the shared helper. Existing adapter unit tests assert on `StreamResult` fields, not implementation, so they should catch regressions. Effort: M (~half a day).

**R2-M7 — Primary/secondary RBAC certification handling duplicated inline inside `orchestrator.py`'s main function**
Lines ~1057-1081 and ~1083-1107: identical `_rbac_certification_scope` → `certify_rbac_permissions` → field-renaming-list-comprehension blocks for primary and secondary hubs, embedded directly in the orchestration function rather than behind their own helper. Fix: extract `_certify_hub_rbac(hub_name, ...)` and loop over `("primary", "secondary")`. Effort: S (~1 hour).

**R2-M8 — `rbac_certification.py`'s required-vs-forbidden permission evaluation loops are the same algorithm with polarity flipped, duplicated in full**
Lines ~422-470 (required) and ~472-520 (forbidden) both call `_check_permission_via_sar` and branch on `allowed`/`denied`, differing only in which boolean counts as "passed". Fix: one `_evaluate_permissions(permissions, *, expect_allowed: bool, ...)` helper called twice. Effort: S (1-2 hours).

### Low severity

- **R2-L1** — `lib/waiter.py`'s newer generic wait abstraction hasn't been
  retrofitted onto `kube_client.py`'s older bespoke `wait_for_pods_ready` poll
  loop; the two now coexist with duplicated retry/backoff logic. Effort: M.
- **R2-L2** — `StateManager` (`lib/utils.py`) continues to grow (~660 lines /
  ~37 methods spanning persistence, phase tracking, hub-identity binding, and
  step completion). Not urgent, but flagged as approaching god-class territory;
  same theme as the already-tracked `H3` line-count concern for `modules/`.
  Effort: L (structural, not for this queue).
- **R2-L3** — `WaitConditionResult.public_detail` and
  `decommission.py`'s cluster-name-list log line are unsanitized/untruncated;
  a hub with an unusually large number of managed clusters could produce an
  oversized single log line. Cosmetic/operational, not a security issue.
  Effort: S.
- **R2-L4** — Manual `sys.argv` pre-scan in `acm_switchover.py` duplicates
  argparse's own `required=` contract and doesn't respect argparse's built-in
  abbreviation matching, so error messages for abbreviated flags can be
  inconsistent between the pre-scan and argparse itself. Effort: S.
- **R2-L5** — `acm_klusterlet_probe.py` never fails its own Ansible task even on
  a hard probe failure, unlike sibling `acm_klusterlet_remediate.py`. Currently
  mitigated by caller-side checks, but a fragile pattern if a future caller
  forgets the check. Effort: S (add an explicit `failed_when`/module-level fail
  path, or document the intentional soft-fail contract).
- **R2-L6** — `decommission` role does not follow the repo's own
  `discover_resources.yml` → `main.yml` convention used by every other role,
  which breaks the test-seeding pattern documented in `AGENTS.md` ("Adding to
  the Collection" section). Effort: S–M (bring into line with the convention).
- **R2-L7** — Several small Ansible duplication items (repeated `when:` block
  5x in `argocd_resume.yml`; observability rollout logic computed twice in one
  file — this one is borderline Medium since a desync between the two
  computations could affect polling behavior, worth a look during the RBAC/Argo
  parity pass rather than standalone); Helm chart RBAC rule block duplicated a
  third time (test-guarded); `rbac_bootstrap` role hardcodes the SA-name mapping
  in 3 places. Bundled here as low-effort cleanup candidates, not blocking.
- **R2-L8** — `generate-merged-kubeconfig.sh`'s sed-escape fallback is
  inconsistent with the (deprecated) `setup-rbac.sh`'s equivalent logic. Low
  priority given `setup-rbac.sh`'s deprecated status.
- **R2-L9** — `orchestrator.py`'s `_as_dict(value)` (lines ~223-228) is a
  `hasattr`-based duck-typing dispatcher unifying three result shapes at the
  last possible moment, when its single call site already knows it receives a
  `StreamResult` (which has `.to_dict()`). Fix: call `.to_dict()` directly and
  delete `_as_dict`. Effort: trivial (~15 minutes); flagged for awareness, not
  urgent.

### Reconfirmed prior findings (no new information)

All still-open items from the 2026-06-13 review were checked for regressions or
resolution and found unchanged unless noted:

- **Still open, confirmed unresolved as before:** `H1` (RBAC validator
  primary/secondary duplication — quantified this pass at ~139 lines, 60-70%
  identical, unification now *more* feasible per the quality subagent since
  related code has since stabilized), `H2` (hardcoded API-group/version literals
  — quantified at 48 occurrences in `modules/`, only 1 routed through the
  constant, see **R2-H2** above for the sharper framing), `H3` (`post_activation.py`
  1619 lines, `finalization.py` 1593 lines — `acm_switchover.py` itself has
  since been meaningfully decomposed via `operation_runners.py`/`cli_outcomes.py`
  and is down to 1301 lines with 0 `noqa:C901` in the CLI entrypoint itself,
  though `modules/` still carries 7 `noqa:C901` suppressions), `M1` (same
  operator/validator RBAC table duplicated on the collection side, parity-tested),
  `M2` (**worse**: quality subagent found this duplication is now 59 lines,
  entirely new code added *within this branch*, not legacy debt — reprioritize
  upward), `M3` (`dry_run_skip` decorator and inline `if self.dry_run` checks
  still coexist), `M4` (`finalization.py:570` still lacks an accessor), `M5`
  (confirmed still fully new/untested-for-duplication code, 283/283 lines net
  new this branch — reprioritize given fresh + safety-relevant nature), `L2`,
  `L4`, `L6`, `L7` (all confirmed present at the same locations).
- **Confirmed intact / correctly fixed, no regression:** `B1` (finalization
  dead-field removal holds), `F2`, `F3`, `F5`, `F6`, `F8`, `F9`, `F16`–`F23`,
  `F26`, `F28`, `F29`, `F31`–`F41`, `F44`. Notably, `F41`'s merge-patch fix
  (explicitly setting the automation-disable annotation key to `None` to force
  deletion under JSON merge-patch semantics, rather than relying on a
  dict-comprehension exclusion that wouldn't actually delete the key) was
  independently re-verified this pass as a real, correctly-fixed historical bug
  — worth calling out as a genuine win, not just a checkbox.
- **PR #105's Option-B activation resume fix** (from the prior review's
  follow-up work) was reconfirmed complete; only a cosmetic gap remains in the
  rollback failure message not covering a double-failure edge case (see R2-L7
  neighborhood — not worth a standalone ID).

### Verified-good call-outs

- `lib/kube_client.py` per-instance TLS configuration (no global side effects),
  `lib/argocd.py` fail-closed blockers on ambiguous GitOps ownership, and the
  `StateManager` flush/dirty-write batching all held up under scrutiny with no
  new issues.
- `modules/activation.py`'s restore delete-then-recreate rollback path and
  `modules/decommission.py`'s new `preserveOnDelete` check are correctly
  implemented.
- The Ansible collection's checkpoint/resume identity-validation plugin
  (`acm_checkpoint_identity_validate`) and hub-identity binding logic have no
  new findings — consistent, fail-closed behavior confirmed again this pass.
- Release validation framework (from the bounded retry pass): `adapters/common.py`'s
  typed contract (frozen `StreamResult`/`AssertionRecord`/`ReportArtifact`
  dataclasses plus a `StreamAdapter` Protocol) is exactly right — the gap is
  that the adapters don't lean on it hard enough for execution logic (R2-M6).
  `scenarios/catalog.py` (495 lines) is legitimately large but well-structured:
  real dataclasses (`ScenarioLifecycle`/`ScenarioSupport`/`ScenarioDefinition`)
  with centrally registered lifecycle rules, not ad-hoc dicts or string
  comparisons — no action needed. `orchestrator.py`'s private-helper
  decomposition (`_aggregate_status`, `_discover_fingerprint`,
  `_run_static_gates`, etc.) shows real intent toward single-purpose
  functions; its problem is concentrated in one oversized top-level function
  and its short-circuit/RBAC blocks (R2-H4, R2-M7), not a general lack of
  decomposition. `checks/artifact_reuse.py` and `scenarios/soak.py` (17 lines
  each) are minimal, single-purpose, no-dependency modules — the target shape
  for the larger files. `checks/`/`scenarios/` size distribution is a clean
  power-law (17-74 line utility modules up to two legitimately complex domain
  files) with no sprawl signal — see R2-H4/M6-M8 above for the two outliers'
  actual issues (duplication, not raw size). This was a sampled, not
  exhaustive, pass (see Coverage note above); `static_gates.py`,
  `lab_readiness.py`, `metadata.py`, and `runtime_parity.py` were not read.
- Test-suite safety-coverage pass (chunk 7) found existing coverage for
  checkpoint/resume, wrong-context, and RBAC-denial scenarios adequate for the
  behavior touched by this branch; no new safety-test gaps beyond what's already
  tracked.
- Docs/parity drift pass (chunk 8) found `parity-matrix.md` and `behavior-map.md`
  consistent with current code for the capabilities touched by this branch; no
  new drift beyond the already-tracked M2/M4-adjacent duplication findings above.

## Summary table

| ID | Severity | Area | New or reprioritized? |
| --- | --- | --- | --- |
| R2-H1 | High | Python / kube_client + primary_prep | New |
| R2-H2 | High | Python / modules constants | New (sharper framing of prior H2) |
| R2-H3 | High | Ansible / preflight RBAC | New |
| R2-M1 | Medium | Ansible collection plugins | New (resolves subagent conflict) |
| R2-M2 | Medium | Python / activation.py | New |
| R2-M3 | Medium | Python / activation+finalization | New |
| R2-M4 | Medium | Python / utils+workflow | New |
| R2-M5 | Medium | Ansible roles/playbooks | New |
| R2-H4 | High | Release validation framework | New |
| R2-M6 | Medium | Release validation framework | New |
| R2-M7 | Medium | Release validation framework | New |
| R2-M8 | Medium | Release validation framework | New |
| R2-L1..L9 | Low | Mixed | New |
| M2, M5 | Medium (reprioritized) | Python | Carried over, now higher priority |
| H1, H2, H3, M1, M3, M4, L2, L4, L6, L7 | (as previously assessed) | Mixed | Confirmed still open, unchanged |
