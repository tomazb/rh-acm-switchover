# Graphify-Assisted Critical Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a Graphify-assisted review that reports only confirmed critical or important logic, safety, parity, performance, or code-smell issues.

**Architecture:** Run the review inline from the current repository root so the existing `graphify-out/` data remains available. Use Graphify only to generate review leads, then verify each candidate against source code, tests, and parity documentation before reporting it.

**Tech Stack:** Graphify graph artifacts, Python helper snippets, `rg`, `sed`, pytest source inspection, Python CLI code, Ansible collection roles/playbooks/modules.

---

## File Structure

No product files should be modified during review execution.

- Read: `docs/superpowers/specs/2026-06-03-graphify-assisted-critical-review-design.md`
- Read: `graphify-out/GRAPH_REPORT.md`
- Read: `graphify-out/analysis.json`
- Read: `graphify-out/graph.json`
- Read: `thermos-resolution-plan.md`
- Read: `docs/ansible-collection/parity-matrix.md`
- Read as needed: Python CLI files under `acm_switchover.py`, `lib/`, and `modules/`
- Read as needed: Ansible collection files under `ansible_collections/tomazb/acm_switchover/`
- Read as needed: tests under `tests/` and `ansible_collections/tomazb/acm_switchover/tests/`

The final deliverable is a chat response. If confirmed findings require code changes, create a separate fix plan after reporting findings; do not mix fixes into this review pass.

## Task 1: Establish Review Baseline

**Files:**
- Read: `docs/superpowers/specs/2026-06-03-graphify-assisted-critical-review-design.md`
- Read: `graphify-out/GRAPH_REPORT.md`
- Read: `graphify-out/analysis.json`
- Read: `thermos-resolution-plan.md`

- [ ] **Step 1: Confirm current workspace status**

Run:

```bash
git status --short
```

Expected: Existing unrelated Graphify cache artifacts may appear. Do not stage, edit, or delete them.

- [ ] **Step 2: Confirm Graphify data exists in this directory**

Run:

```bash
test -f graphify-out/graph.json && test -f graphify-out/analysis.json && test -f graphify-out/GRAPH_REPORT.md
```

Expected: exit code `0`.

- [ ] **Step 3: Extract Graphify hubs, surprises, and questions**

Run:

```bash
python3 - <<'PY'
import json
from pathlib import Path

analysis = json.loads(Path("graphify-out/analysis.json").read_text())
print("## gods")
for item in analysis.get("gods", []):
    print(item)
print("## surprises")
for item in analysis.get("surprises", []):
    print(item)
print("## questions")
for item in analysis.get("questions", []):
    print(item)
PY
```

Expected: output includes high-degree nodes such as `StateManager`, `KubeClient`, `SecondaryActivation`, and `PostActivationVerification`, plus Graphify surprise leads.

- [ ] **Step 4: Read current Thermos residual state**

Run:

```bash
sed -n '1,130p' thermos-resolution-plan.md
```

Expected: identify whether any residual Thermos follow-up remains open or recently merged so review effort does not duplicate resolved findings without source evidence.

## Task 2: Build Targeted Graphify Lead Map

**Files:**
- Read: `graphify-out/graph.json`
- Read: `graphify-out/analysis.json`
- Read: source files named by the generated lead map

- [ ] **Step 1: Generate a safety-community summary**

Run:

```bash
python3 - <<'PY'
import json
from collections import Counter, defaultdict
from pathlib import Path

graph = json.loads(Path("graphify-out/graph.json").read_text())
communities = defaultdict(list)
for node in graph["nodes"]:
    communities[node.get("community")].append(node)

keywords = [
    "state", "checkpoint", "resume", "klusterlet", "argocd", "rbac",
    "activation", "finalization", "decommission", "backup", "restore",
    "wait", "kube", "validation", "preflight",
]

for community_id, nodes in sorted(communities.items(), key=lambda item: -len(item[1])):
    labels = " | ".join(str(node.get("label", "")) for node in nodes[:10])
    if any(keyword in labels.lower() for keyword in keywords):
        sources = Counter(node.get("source_file") for node in nodes if node.get("source_file"))
        print(f"COMM {community_id} size={len(nodes)}")
        print(f"  labels={labels}")
        print(f"  sources={sources.most_common(8)}")
PY
```

Expected: output identifies source-heavy communities for the six review slices.

- [ ] **Step 2: Explain high-risk Graphify nodes**

Run:

```bash
graphify explain StateManager
graphify explain KubeClient
graphify explain SecondaryActivation
graphify explain PostActivationVerification
graphify explain ArgoCDPauseCoordinator
```

Expected: each command returns source location and connected methods/tests. Treat `INFERRED` connections as leads only.

- [ ] **Step 3: Record the six source slices for manual verification**

Use the outputs from Steps 1 and 2 to drive inspection of these slices:

```text
state/checkpoint/resume
klusterlet/post-activation
activation/finalization/restore/BackupSchedule
RBAC/decommission
Argo CD pause/resume/resume-on-failure
Python CLI versus Ansible collection parity tests
```

Expected: no finding is reported at this stage. This task only selects source areas to verify.

## Task 3: Verify State, Checkpoint, And Resume Safety

**Files:**
- Read: `lib/utils.py`
- Read: `acm_switchover.py`
- Read: `ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py`
- Read: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/checkpoint.py`
- Read: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_checkpoint_identity_validate.py`
- Read: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py`
- Read: `tests/test_utils.py`
- Read: `tests/test_main.py`

- [ ] **Step 1: Locate resume and identity validation paths**

Run:

```bash
rg -n "hub_identities|cluster_uid|identity|checkpoint|resume|force|reset_state|StateIdentityMismatch" lib/utils.py acm_switchover.py ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py ansible_collections/tomazb/acm_switchover/plugins/module_utils/checkpoint.py ansible_collections/tomazb/acm_switchover/plugins/modules/acm_checkpoint_identity_validate.py tests/test_utils.py tests/test_main.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py
```

Expected: relevant source and tests for hub identity binding, checkpoint writes, and resume validation are visible.

- [ ] **Step 2: Inspect source around matched resume paths**

Run targeted `sed` commands using line numbers from Step 1. Example:

```bash
sed -n '120,330p' lib/utils.py
sed -n '1,220p' ansible_collections/tomazb/acm_switchover/plugins/module_utils/checkpoint.py
```

Expected: determine whether resume fails closed on missing or mismatched hub identity and whether checkpoint writes are atomic and idempotent.

- [ ] **Step 3: Decide if any state/checkpoint candidate is reportable**

Report a finding only if source shows a credible path to stale resume, wrong-hub resume, non-atomic checkpoint corruption that can be accepted as valid state, or Python/Ansible checkpoint identity drift.

Expected: either a confirmed finding with file/line evidence or a brief residual-risk note.

## Task 4: Verify Klusterlet And Post-Activation Fail-Closed Behavior

**Files:**
- Read: `modules/post_activation.py`
- Read: `tests/test_post_activation.py`
- Read: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/klusterlet.py`
- Read: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_klusterlet_probe.py`
- Read: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_klusterlet_remediate.py`
- Read: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py`

- [ ] **Step 1: Locate broad exception handling and timeout handling**

Run:

```bash
rg -n "except|timeout|unreachable|skipped|failed|ThreadPool|Future|klusterlet|bootstrap-hub-kubeconfig|hub-kubeconfig-secret|wrong_hub" modules/post_activation.py tests/test_post_activation.py ansible_collections/tomazb/acm_switchover/plugins/module_utils/klusterlet.py ansible_collections/tomazb/acm_switchover/plugins/modules/acm_klusterlet_probe.py ansible_collections/tomazb/acm_switchover/plugins/modules/acm_klusterlet_remediate.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py
```

Expected: all fail-open and fail-closed decision points are visible.

- [ ] **Step 2: Inspect Python and collection behavior side by side**

Run targeted `sed` commands using Step 1 line numbers. Example:

```bash
sed -n '1,260p' modules/post_activation.py
sed -n '1,260p' ansible_collections/tomazb/acm_switchover/plugins/module_utils/klusterlet.py
```

Expected: determine whether broad API/client errors, malformed kubeconfig data, worker timeouts, and wrong-hub secrets fail closed in both implementations.

- [ ] **Step 3: Verify test claims reach implementation paths**

Run:

```bash
rg -n "fail|timeout|exception|unreachable|wrong_hub|bootstrap|malformed|skip|check_mode" tests/test_post_activation.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py
```

Expected: tests assert the same fail-closed paths found in source.

- [ ] **Step 4: Decide if any klusterlet candidate is reportable**

Report a finding only if source and tests show fail-open behavior, parity drift, or misleading test coverage around a safety-critical klusterlet path.

Expected: either a confirmed finding with file/line evidence or a brief residual-risk note.

## Task 5: Verify Activation, Finalization, Restore, And BackupSchedule Mutation Safety

**Files:**
- Read: `modules/activation.py`
- Read: `modules/finalization.py`
- Read: `modules/backup_schedule.py`
- Read: `modules/restore_discovery.py`
- Read: `modules/preflight/backup_validators.py`
- Read: `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/`
- Read: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/`
- Read: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_backup_schedule.py`
- Read: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_restore_info.py`
- Read: related tests under `tests/` and `ansible_collections/tomazb/acm_switchover/tests/`

- [ ] **Step 1: Locate mutation and wait paths**

Run:

```bash
rg -n "patch|delete|create|wait|timeout|BackupSchedule|Restore|restore|passive|full|skip|failed|changed|check_mode|veleroManagedClustersBackupName|cleanup|collision" modules/activation.py modules/finalization.py modules/backup_schedule.py modules/restore_discovery.py modules/preflight/backup_validators.py ansible_collections/tomazb/acm_switchover/roles/activation/tasks ansible_collections/tomazb/acm_switchover/roles/finalization/tasks ansible_collections/tomazb/acm_switchover/plugins/modules/acm_backup_schedule.py ansible_collections/tomazb/acm_switchover/plugins/modules/acm_restore_info.py tests/test_activation.py tests/test_finalization.py tests/test_backup_schedule.py ansible_collections/tomazb/acm_switchover/tests/unit
```

Expected: mutation paths and tests for activation, restore, finalization, and BackupSchedule behavior are visible.

- [ ] **Step 2: Inspect Python restore and BackupSchedule behavior**

Run targeted `sed` commands using Step 1 line numbers. Example:

```bash
sed -n '1,280p' modules/activation.py
sed -n '1,320p' modules/backup_schedule.py
sed -n '1,360p' modules/finalization.py
```

Expected: determine whether mutations are target-scoped, idempotent, version-aware, and fail closed after API errors or missing required restore data.

- [ ] **Step 3: Inspect collection restore and BackupSchedule behavior**

Run:

```bash
sed -n '1,260p' ansible_collections/tomazb/acm_switchover/plugins/modules/acm_restore_info.py
sed -n '1,260p' ansible_collections/tomazb/acm_switchover/plugins/modules/acm_backup_schedule.py
```

Expected: compare collection behavior with Python behavior for dual-supported activation and finalization surfaces.

- [ ] **Step 4: Decide if any activation/finalization candidate is reportable**

Report a finding only if source shows wrong-target mutation, fail-open restore handling, parity drift, check-mode mutation, idempotence breakage with operator impact, or meaningful performance risk in realistic waits/discovery.

Expected: either a confirmed finding with file/line evidence or a brief residual-risk note.

## Task 6: Verify RBAC, Decommission, And Destructive Operation Boundaries

**Files:**
- Read: `lib/rbac_validator.py`
- Read: `modules/decommission.py`
- Read: `deploy/rbac/`
- Read: `deploy/helm/acm-switchover-rbac/`
- Read: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_validate.py`
- Read: `ansible_collections/tomazb/acm_switchover/roles/decommission/tasks/`
- Read: `ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/files/deploy/rbac/`
- Read: RBAC and decommission tests under `tests/` and `ansible_collections/tomazb/acm_switchover/tests/`

- [ ] **Step 1: Locate destructive operations and RBAC permission surfaces**

Run:

```bash
rg -n "delete|remove|decommission|ClusterRole|ClusterRoleBinding|verbs|resources|apiGroups|customValidatorRules|validate|SubjectAccessReview|managedclusters|multiclusterhubs|observability|check_mode|dry_run" lib/rbac_validator.py modules/decommission.py deploy/rbac deploy/helm/acm-switchover-rbac ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_validate.py ansible_collections/tomazb/acm_switchover/roles/decommission/tasks ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/files/deploy/rbac tests ansible_collections/tomazb/acm_switchover/tests
```

Expected: destructive paths, RBAC validators, manifests, and tests are visible.

- [ ] **Step 2: Inspect RBAC validator parity**

Run targeted `sed` commands using Step 1 line numbers. Example:

```bash
sed -n '1,320p' lib/rbac_validator.py
sed -n '1,260p' ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_validate.py
```

Expected: determine whether Python and collection validate equivalent permissions for dual-supported surfaces, including decommission extensions and Argo CD modes.

- [ ] **Step 3: Inspect destructive operation gates**

Run targeted `sed` commands on decommission source and role tasks. Example:

```bash
sed -n '1,320p' modules/decommission.py
find ansible_collections/tomazb/acm_switchover/roles/decommission/tasks -maxdepth 1 -type f -print
```

Expected: determine whether dry-run/check-mode gates and preconditions prevent unintended destructive operations.

- [ ] **Step 4: Decide if any RBAC/decommission candidate is reportable**

Report a finding only if source shows under-scoped validation, over-broad generated privileges, destructive operations without required gates, parity drift, or tests that miss destructive paths.

Expected: either a confirmed finding with file/line evidence or a brief residual-risk note.

## Task 7: Verify Argo CD Pause And Resume Safety

**Files:**
- Read: `lib/argocd.py`
- Read: `lib/argocd_coordinator.py`
- Read: `acm_switchover.py`
- Read: `modules/primary_prep.py`
- Read: `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/`
- Read: `ansible_collections/tomazb/acm_switchover/playbooks/argocd_resume.yml`
- Read: Argo CD tests under `tests/` and `ansible_collections/tomazb/acm_switchover/tests/`

- [ ] **Step 1: Locate pause/resume filtering and identity checks**

Run:

```bash
rg -n "argocd|ApplicationSet|Application|pause|resume|automated|syncPolicy|run_id|hub|identity|cluster_uid|generated|ownerReferences|label|annotation|resume_on_failure|resume-only|argocd-resume" lib/argocd.py lib/argocd_coordinator.py acm_switchover.py modules/primary_prep.py ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks ansible_collections/tomazb/acm_switchover/playbooks/argocd_resume.yml tests ansible_collections/tomazb/acm_switchover/tests
```

Expected: source and tests for Argo CD selection, pause state persistence, resume behavior, and identity checks are visible.

- [ ] **Step 2: Inspect Python Argo CD coordinator behavior**

Run:

```bash
sed -n '1,320p' lib/argocd.py
sed -n '1,320p' lib/argocd_coordinator.py
```

Expected: determine whether pause/resume targets only intended Applications and preserves enough state to resume safely.

- [ ] **Step 3: Inspect collection Argo CD role and standalone resume playbook**

Run:

```bash
find ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks -maxdepth 1 -type f -print
sed -n '1,260p' ansible_collections/tomazb/acm_switchover/playbooks/argocd_resume.yml
```

Expected: determine whether collection behavior has equivalent safety checks for standalone resume and resume-on-failure.

- [ ] **Step 4: Decide if any Argo CD candidate is reportable**

Report a finding only if source shows wrong Application targeting, unsafe generated Application handling, stale identity resume, parity drift, or misleading tests around pause/resume safety.

Expected: either a confirmed finding with file/line evidence or a brief residual-risk note.

## Task 8: Verify Performance And Scalability Risks

**Files:**
- Read: `lib/waiter.py`
- Read: `lib/kube_client.py`
- Read: `modules/post_activation.py`
- Read: `modules/preflight/backup_validators.py`
- Read: `modules/finalization.py`
- Read: Ansible task files identified by Graphify communities for discovery and wait behavior

- [ ] **Step 1: Locate polling loops, broad discovery, and serial API calls**

Run:

```bash
rg -n "while|for .* in|sleep|time.sleep|wait|timeout|list_|get_|k8s_info|until:|retries:|delay:|ThreadPoolExecutor|as_completed|poll" lib/waiter.py lib/kube_client.py modules/post_activation.py modules/preflight/backup_validators.py modules/finalization.py ansible_collections/tomazb/acm_switchover/roles
```

Expected: candidate wait loops and discovery paths are visible.

- [ ] **Step 2: Inspect only operator-impacting performance candidates**

Run targeted `sed` commands using Step 1 line numbers.

Expected: ignore small test-only or one-time setup loops; focus on realistic hub/managed-cluster scaling paths that can cause long hangs, repeated broad cluster reads, or unnecessarily serial per-cluster API calls.

- [ ] **Step 3: Decide if any performance candidate is reportable**

Report a finding only if source shows an unbounded wait, repeated broad discovery in a common path, avoidably serial per-managed-cluster behavior with material runtime impact, or timeout behavior that hides failure.

Expected: either a confirmed finding with file/line evidence or a brief residual-risk note.

## Task 9: Compile Final Review Output

**Files:**
- Read: all source files inspected in Tasks 3 through 8
- Read: this plan
- Read: `docs/superpowers/specs/2026-06-03-graphify-assisted-critical-review-design.md`

- [ ] **Step 1: Rank confirmed findings**

Use this severity rule:

```text
Critical: destructive wrong-target mutation, unsafe resume, data loss, or broad privilege/resource misconfiguration.
Important: fail-open behavior, parity drift, major operator misreporting, or realistic workflow performance risk.
```

Expected: every finding has severity, exact file/line evidence, operational impact, and a minimal remediation direction.

- [ ] **Step 2: Write reviewed-no-finding notes**

For each reviewed slice with no confirmed issue, write one concise note:

```text
No confirmed critical/important issue in <slice>; residual risk is <specific remaining coverage or complexity concern>.
```

Expected: no speculative findings are included.

- [ ] **Step 3: Include reusable checklist**

Copy the checklist from the approved design, preserving the current-directory requirement:

```text
- Start in the repository root that contains the current graphify-out/ directory.
- Check git status and avoid modifying unrelated Graphify cache artifacts.
- Read graphify-out/GRAPH_REPORT.md, graphify-out/analysis.json, and targeted graphify explain output for high-risk nodes.
- Treat Graphify as a hypothesis generator only.
- Prioritize safety hubs: state, checkpoints, resume, mutation wrappers, activation, post-activation, finalization, decommission, RBAC, Argo CD, and BackupSchedule.
- Follow Graphify surprises and bridge nodes into source files, then verify manually.
- Check Python CLI and Ansible collection behavior together for dual-supported surfaces.
- Verify tests reach the risky code path and assert fail-closed behavior.
- Report only critical or important issues with source evidence.
- Do not report style, naming, or generic complexity unless it materially affects safety or correctness.
```

Expected: final response contains the review design summary, confirmed findings, no-finding notes, checklist, and verification commands run.

- [ ] **Step 4: Do not commit review output unless requested**

Run:

```bash
git status --short
```

Expected: no product files modified by review execution. If only this plan file is new, it may be committed separately before execution handoff.

## Self-Review

- Spec coverage: Tasks 1 and 2 cover Graphify lead generation. Tasks 3 through 8 cover all safety-critical and parity-sensitive slices from the spec. Task 9 covers deliverable format and reusable checklist.
- Red-flag scan: The plan contains no banned tokens or unspecified implementation steps.
- Type and command consistency: The plan uses existing repository paths and commands that read local files or produce terminal output only.
