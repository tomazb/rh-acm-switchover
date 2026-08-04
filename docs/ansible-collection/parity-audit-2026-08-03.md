# Python ↔ Ansible Feature Parity Audit — 2026-08-03

Audited tree: branch `feat/run-record-spec` at `bc4bafbd` — since squash-merged
to `ansible` as `21978adc` (PR #215), with the shim removal following as
`e7bcad94` (PR #213); neither merge changed audited behaviour, so findings
apply to the current `ansible` tip directly. Method: four parallel
read-only audits — workflow coverage, shared-twin modules, behavioral/safety
semantics, and parity-guardrail coverage — each with file:line evidence,
synthesized and de-duplicated here. This document records findings only; no
fixes were applied.

## Verdict

**Workflow-level parity is close to complete; behavioral parity is not.**
Every operator-facing Python mode (switchover, restore-only, validate-only,
dry-run, decommission, RBAC setup, Argo CD pause/standalone resume) has a
collection entrypoint, the five phases and their step inventories align, and
the headline safety logic (ClusterDeployment `preserveOnDelete` gating, Argo CD
marker ownership, checkpoint hub-identity binding, restore-freshness
re-validation) is mirrored rather than approximated. But the two
implementations diverge in safety-relevant edge semantics — most seriously
around the Argo CD pause record, checkpoint defaults, and interrupted-run
obligations — and the parity guardrails cover a much smaller surface than the
governance docs claim. An operator who assumes "both tools do the same thing"
can be burned in the specific ways listed below.

Severity legend: **C** = data loss / silent wrong outcome on a plausible
operator path · **H** = divergent verdict or missing safety net · **M** =
drift risk / guardrail illusion · **D** = documentation defect.

---

## 1. Critical findings

### C1. Cross-runtime Argo CD resume can destroy an Application's sync policy

Python's pause writes only the `paused-by` marker to the cluster; the
`original_sync_policy` lives in the state-file register
(`lib/argocd.py:637-640`, ADR-0001). The collection's resume reads
`metadata.annotations['…original-sync-policy'] | default('{}') | from_json`
and patches `spec.syncPolicy` to that value
(`roles/argocd_manage/tasks/resume.yml:70-74`). Resume a Python-paused
Application with the collection — a path the collection's own error text
invites, since it tells operators to find the `run_id` "in the switchover
report artifact", which the Python report publishes
(`lib/report_artifacts.py:150-153`) — and the Application's sync policy is
patched to `{}` while being counted `restored`. No test on either side covers
this. Same mechanism fires collection-to-collection when only the
`original-sync-policy` annotation is lost.

### C2. The collection has no pause register at all — ADR-0001 unimplemented (issue #207 understated)

`grep argocd_paused_apps` over `ansible_collections/` returns nothing. The
collection's only pause record is the annotation pair written *with* the pause
patch (`roles/argocd_manage/tasks/pause.yml:40-53`). Consequences, scored
against ADR-0001's invariants:

- **CRD invisible at resume → silent no-op success** (`restored: 0`, exit 0):
  `roles/argocd_manage/tasks/discover.yml:288-299` blanks the app list on a
  string-matched CRD error and `resume.yml:3` skips the block. This is the
  ADR's explicitly rejected option ("Clear register when CRD absent"),
  implemented verbatim.
- **Marker lost → app paused forever, policy unrecoverable**: `resume.yml:83`
  skips silently; the warn path fires only on *mismatched* run_id.
- No provisional/unknown states; `run_id` is generated in memory
  (`discover.yml:59-69`) and persisted only at phase end — after the first
  patch — so a crash between patch and persist leaves an untracked pause.
- `module_utils/argocd.py:151-159 build_pause_patch()` is production-dead:
  only unit tests import it, and it omits the `original-sync-policy`
  annotation the shipped inline patch writes. The tested helper is not the
  shipped behaviour.

### C3. Interrupted collection run leaves `autoImportStrategy=ImportAndSync` permanently

Python persists the reset obligation at mutation time with fsync
(`modules/activation.py:635` → `lib/run_record.py`). The collection holds it
in a `set_fact` (`roles/activation/tasks/manage_auto_import.yml:105-108`)
persisted only at end of phase — and only when `checkpoint.enabled` is true,
which is **false by default**. Kill the run mid-`wait_for_restore` and rerun:
finalization's `reset_auto_import.yml:18,32` skips, reports `pass`, and the
next preflight also scores it `pass`. The hub stays in `ImportAndSync`
silently. (Shared latent gap: neither side captures a pre-existing ConfigMap
value before overwriting.)

### C4. Collection checkpointing — and with it hub-identity binding — is off by default

`checkpoint.enabled: false` in all five role defaults and
`examples/group_vars/all.yml:35-40`. At shipped defaults the collection has no
resume, no persisted `argocd_run_id`, and **no cluster-UID identity check**;
Python always has all three (`StateManager` unconditional,
`lib/utils.py:743-768`). When enabled, the collection's identity binding is
actually *stricter* than Python's (it also pins method, activation_method,
restore_only, old_hub_action — `module_utils/checkpoint.py:41-52`); but the
default posture inverts the safety story operators expect from the CLI.

---

## 2. High findings — divergent verdicts on the same cluster state

| # | Semantic | Python | Collection | Burn scenario |
|---|---|---|---|---|
| H1 | Managed-cluster enforcement on resume | `acm_switchover.py:876-881`: a resumed run whose state lacks the expectation record silently disables **all** name/count enforcement | asserts the checkpoint carries the expectation, hard-fails otherwise (`roles/preflight/tasks/main.yml:26-51`) | Python resume from ACTIVATION verifies nothing about expected clusters |
| H2 | Zero managed clusters post-restore | warns, "informational only" (`modules/activation.py:1080-1087`), post-activation returns complete | hard-fails unless `allow_zero_managed_clusters` (`plugins/modules/acm_cluster_verify.py:74-82`) | empty restore passes Python, fails collection |
| H3 | Backup freshness proof | prefers `status.completionTimestamp`; resume shortcut accepts recorded name on ownership+phase alone (`modules/finalization.py:158-179,444-463`) | `creationTimestamp >= baseline` only (`roles/finalization/tasks/verify_backups.yml:69-76`) | pre-activation backup completing late: Python accepts as post-switchover proof, collection fails |
| H4 | BackupSchedule collision repair | raises, leaves hub with **no** BackupSchedule (`modules/finalization.py:1349-1351`) | rollback path restores it (`repair_backup_schedule_collision.yml:100-134`) | collection recovers where Python strands |
| H5 | RBAC: missing hub namespace | preflight fails (`lib/rbac_validator.py:633-638`) | no namespace-existence check; SSAR answers `allowed` regardless | hub missing `open-cluster-management-backup` passes collection preflight |
| H6 | RBAC: SSAR API failure | distinct `ValidationError` abort ("cannot determine" ≠ "denied") | folded into ordinary denials (`roles/preflight/tasks/run_ssar.yml:36-50`) | both fail closed, but diagnosis differs |
| H7 | Preflight scope | `thanos-object-storage` secret check, **critical** (`modules/preflight/namespace_validators.py:137-170`) — collection has zero equivalent | spoke RBAC validated inside preflight — Python only via standalone `check_rbac.py --managed-cluster` | secondary missing the Thanos secret passes collection preflight, fails Python's |
| H8 | Klusterlet restart failure | warn-and-continue, remediation reported done (`modules/post_activation.py:1305-1306`) | cluster marked `failed` (`module_utils/klusterlet.py:563-571`) | opposite fail-open/fail-closed postures |
| H9 | Klusterlet drift cluster | `acm-switchover/restart` epoch annotation; single hub-route expected-hub; malformed spoke kubeconfig = skip | `acm-switchover/restartedAt` ISO annotation; per-cluster import-secret expected-hub; malformed kubeconfig = hard fail; no secret-visibility wait | neither runtime sees the other's restarts; same cluster can be `verified` on one and `wrong_hub` on the other |
| H10 | Python `--dry-run` durable-state leak | `ensure_contexts` resets and flushes a real in-progress state file on context mismatch **before** the dry-run snapshot (`lib/utils.py:696-713` via `acm_switchover.py:1103`); `--dry-run --reset-state` really deletes | collection never writes under dry_run/validate/check (`checkpoint_phase.py:85-88,169,182-186`) | the one case where a dry run destroys durable data — Python-side bug, found incidentally |
| H11 | Report artifacts | full schema: `status`, `summary`, `hubs`, `errors`, uniform `phases`, `generated_at` (`lib/report_artifacts.py:101-156`) | playbook-inlined: no `status`/`summary`/`hubs`/`errors`; `operation` is a string vs Python's dict; `preflight` absent from `phases`; decommission artifact needs an explicit `summary_path` (never derives from `report_dir`); standalone argocd resume writes **no artifact** | any downstream consumer keyed on the Python schema breaks on collection output; `runtime_parity.py:137-170` — the supposed contract check — reads keys **neither** side emits |
| H12 | Dead operator knobs | `--force` covers legacy-identity backfill, stale-COMPLETED rerun, unknown-phase reset | `acm_switchover_execution.force` and `.verbose` declared in six files, mapped in `cli-migration-map.md:17`, **consumed nowhere**; `--check` mode unusable (SSAR all-denied, `validate_tooling.yml:23-31` crashes on skipped command) | operator sets `force: true` expecting Python semantics, gets silence |

Python-only stale-state guard (H13): 6-hour COMPLETED-state age gate
(`lib/workflow.py:125-152`) has no collection analogue — a weeks-old completed
checkpoint resumes silently. Conversely `checkpoint.reset_from` (selective
downstream prune) has no Python analogue.

---

## 3. Medium findings — drift risks and guardrail illusions

- **M1. `ARGOCD_PAUSED_BY_ANNOTATION` is triple-defined and unpinned**:
  `lib/argocd.py:37`, `lib/constants.py:218`, `module_utils/constants.py:55`
  — a comment saying "must match the Ansible role" is the only guard. Drift
  silently orphans every paused Application.
- **M2. Constants parity is an allowlist with no completeness check** (30
  pinned pairs); unguarded shared values include `REPORT_SCHEMA_VERSION`,
  `GLOBAL_SET_NAMESPACE` (collection hardcodes the literal), and the
  namespace regex. One pinned pair is a **phantom**: `SECRET_VISIBILITY_*`
  values are asserted equal (`tests/test_constants_parity.py:17-18`) but the
  collection never implements the wait they configure — a green test for
  absent behaviour.
- **M3. Guardrails that are weaker than their names**:
  `test_argocd_constants_parity.py:54-88` compares `build_pause_patch` to a
  Jinja re-implementation written inside the test, never reading `pause.yml`;
  namespace parity is a one-way subset (`:19-34`); the collection's
  `test_preflight_parity.py` is 301 lines of collection-only text assertions
  that never import `lib/` — the filename is unearned.
- **M4. The real parity enforcement is `tests/properties/`** (path safety,
  Argo CD predicates, RBAC expansion, validation choice-domains, report
  writers, backup schedule — genuine cross-form asserts) — and it is
  **undocumented** in both `AGENTS.md` and `parity-matrix.md`. The documented
  contract points at the weaker layer.
- **M5. `path_safety` twins are behaviourally identical today** (verified by a
  23-case differential harness: all verdicts match) but drift structurally:
  name/arity (`validate_safe_filesystem_path(path, field)` vs
  `validate_safe_path(path)`), unrelated exception classes (collection's local
  `ValidationError` loses the SECURITY severity signal), artifact-path
  validation stricter on the collection side, and the collection's error
  message omits cwd from the allowed-roots list it actually enforces. No
  direct guardrail; the shared fixture's 10 path cases have no symlink or
  directory-validator coverage and pin only lowest-common-denominator
  substrings.
- **M6. Validation surface asymmetry the fixture can't see**: the four
  Kubernetes name/namespace/label validators and all CLI-combination rules are
  Python-only; `validate_context_name` is duplicated with zero fixture cases;
  `min_managed_clusters` accepts numeric strings on the collection side and
  rejects them on Python's.
- **M7. State/checkpoint schemas are disjoint by design but unfenced**:
  Python `version: "1.0"` / `current_phase` / `completed_steps` / `config` vs
  collection `schema_version: "2.0"` / `phase` / `completed_phases` /
  `operational_data`. **Python silently loads a collection checkpoint as a
  fresh run** (`_validate_loaded_state` defaults a missing `current_phase` to
  `init`) — no guard, no doc claiming or disclaiming interop. Collection has
  no run lock (`StateManager` flocks; `checkpoint_phase` doesn't) and no
  error timestamps. `resume_summary` merge-vs-replace and `config` vs
  `operational_data` location split confirmed (#214).
- **M8. Phase-name vocabulary** (`CANONICAL_PHASE_NAMES` vs `KNOWN_PHASES`)
  coincides by maintenance, not by test; preflight result-ID vocabularies are
  structurally disjoint (Python slugifies check names,
  `lib/report_artifacts.py:43`; collection hardcodes ~25 literals).
- **M9. Transport parity**: no collection counterpart to `KubeClient` —
  86 `k8s_info` call sites with no transport retry (Python: tenacity on
  5xx/429/network), no timeouts on `kubernetes.core` tasks, dry-run enforced
  by ~130 hand-written `when:` guards vs one decorator seam;
  `klusterlet.py` remediation takes no dry-run parameter at all.
- **M10. Dry-run semantics differ**: Python dry-run still validates and
  reports (mutations suppressed); collection `dry_run` **skips whole phases**
  (`roles/post_activation/tasks/main.yml:13-52` publishes `skipped`). Neither
  side has a test asserting absence of mutation under dry-run.
- **M11. CI trigger asymmetry**: root CI runs on push to `ansible` (the main
  dev branch); the collection foundation workflow runs only on PRs and pushes
  to `main` — direct pushes to `ansible` skip collection unit/integration
  jobs (root-side parity tests still import the collection, so the hole is
  partial).

---

## 4. Documentation defects

- **D1. `AGENTS.md` capability surface (`:34-49`)**: overstates preflight,
  post-activation (no metrics-collection/Grafana check in the collection;
  klusterlet remediation only for explicitly-supplied kubeconfigs), "shared
  machine-readable reports" (3 of 4 report types), and checkpoints (`--force`
  has no collection implementation). It also **omits `--restore-only`** —
  which is fully dual-supported — and omits the Python-only surfaces
  (`show_state.py`, standalone `check_rbac.py` diagnostic postures incl.
  `--role validator`).
- **D2. Of the 12 claimed dual-supported capabilities, only ~4 have any
  automated cross-form guardrail** (RBAC matrix — the one genuinely strong
  test, `tests/test_rbac_collection_parity.py`; Argo CD predicates; reports
  and checkpoints via `tests/properties/` only). The other eight rest
  entirely on the Approval Gate and reviewer discipline.
- **D3. `docs/ansible-collection/parity-matrix.md`**: "~18 shared constants"
  (actual: 30); "shared validation fixture keeps safe-path policy aligned"
  (the real safe-path guard is the undocumented property suite);
  `build_pause_patch` "match" claim (see M3); no rows for restore-only, the
  observability-prereq delta, spoke-RBAC delta, or `lib/run_record.py`.
- **D4. `docs/ansible-collection/feature-inventory.md` (2026-05-12) is stale**:
  claims dry-run/checkpoints are "contract only / schema only" (both now
  implemented), omits four standalone playbooks and ~9 mapped CLI flags.
- **D5. `cli-migration-map.md:17,35`** maps `--force` and
  `--skip-kubeconfig-generation` as if functional/equivalent; `force` is
  dead (only `variable-reference.md:72` is honest), and RBAC bootstrap
  defaults are inverted (Python generates kubeconfigs by default, collection
  defaults `generate_kubeconfigs: false`).
- **D6. `behavior-map.md:47`** equates the diagnostic `check_rbac.py` with the
  provisioning `rbac_bootstrap` playbook.
- **D7. `AGENTS.md:418`** ("root tests do not install ansible-core") is
  factually stale — `requirements-dev.txt` pins it, so the defensive stub in
  the RBAC parity test is dead in current CI.
- **D8.** `ARGOCD_RESUME_ON_FAILURE_REQUIRES_MANAGE_MESSAGE` is the one
  same-name/different-value constant pair (CLI flag text vs playbook key
  text) — intentional, recorded nowhere.

---

## 5. What genuinely holds

- Phase flow and step inventories: full parity, including
  `--min-managed-clusters` derivation and restore-only end-to-end.
- Restore-freshness re-validation (Thermos R2-M2): matches on both sides,
  including resume; the collection additionally has the only end-to-end
  regression fixture.
- RBAC permission matrices: entry-for-entry identical across all nine tables,
  pinned by a real cross-implementation guardrail with full tuple-expansion
  equality — the strongest parity test in the repo.
- Decommission safety gates (`preserveOnDelete`, unverified-relationship
  blocks): mirrored.
- Hub-identity binding shape (kube-system UID, fail-closed): matches when the
  collection checkpoint is enabled — and is stricter there.
- `path_safety` verdict behaviour: currently identical across 23 adversarial
  cases (drift risk is structural, not present-day behavioural).
- Preflight fail-closed two-tier model: matches.

## 6. Already tracked vs. newly surfaced

Tracked before this audit: #207 (collection register convergence — this audit
shows it is *absence*, not divergence), #208/#210/#211 (register seam), #214
(checkpoint convergence + resume_summary merge-vs-replace), path_safety parity
fixture gap (2026-08-02 architecture review).

Newly surfaced here and not yet tracked: C1 (cross-runtime resume data loss),
C3 (auto-import obligation loss at shipped defaults), C4 (checkpoint-off
default posture), H1 (Python resume disables MC enforcement), H3 (backup
freshness verdict split), H10 (Python dry-run state wipe via
`ensure_contexts`), H11 (report schema non-interchangeability + phantom
normalizer), H12 (dead `force`/`verbose` knobs, unusable `--check`), M1
(paused-by annotation unpinned), M2 (phantom `SECRET_VISIBILITY` pin), M3/M4
(guardrail illusions / undocumented property-suite enforcement), M7 (Python
loads collection checkpoint as fresh run), and the D-series doc defects.

## 7. Suggested prioritization (not executed)

1. **C1/C2** — the Argo CD record is the sharpest cross-runtime data-loss
   path; converging the collection on ADR-0001 (issue #207) subsumes both.
2. **C3/C4** — decide the collection's default checkpoint posture (or persist
   the auto-import obligation outside the checkpoint), since three findings
   inherit from `enabled: false`.
3. **H1, H10** — two Python-side safety bugs independent of parity.
4. **H11 + M8** — pick one report contract, fix or delete the phantom
   `runtime_parity` normalizer, align result-ID vocabularies.
5. **M1/M2/M5 + D-series** — cheap guardrail and documentation fixes
   (pin the annotation, meta-check the constants map, path_safety fixture,
   correct AGENTS.md/parity-matrix, document the property-suite layer).
