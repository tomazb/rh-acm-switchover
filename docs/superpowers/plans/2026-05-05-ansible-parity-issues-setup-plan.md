# Ansible Parity Backlog — GitHub Issues Setup Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace `work-to-do.md` with 8 GitHub Issues + a milestone so progress is queryable across sessions with `gh issue list`.

**Architecture:** Create labels → milestone → move spec file → create issues in merge order with pre-ticked checkboxes → push. Each issue body contains a one-paragraph problem summary, `Depends on:` links, and full acceptance criteria. The spec file lives in `docs/superpowers/plans/` as a read-only reference.

**Tech Stack:** `gh` CLI (GitHub CLI), `git`

---

## Task 1: Create labels

**Files:** none (GitHub metadata only)

**Step 1: Create the `parity` label**
```bash
gh label create parity --description "Ansible/Python parity work" --color "0075ca"
```
Expected: `✓ Label "parity" created`

**Step 2: Create the `ansible` label**
```bash
gh label create ansible --description "Ansible collection changes" --color "e4e669"
```

**Step 3: Create priority labels**
```bash
gh label create P0 --description "Critical priority" --color "d73a4a"
gh label create P1 --description "High priority" --color "e99695"
gh label create P2 --description "Medium priority" --color "f9d0c4"
gh label create P3 --description "Low priority" --color "fef2c0"
```
Expected: 4 `✓ Label "..." created` lines

---

## Task 2: Create milestone

**Step 1: Create the milestone**
```bash
gh api repos/{owner}/{repo}/milestones \
  --method POST \
  --field title="ansible-python-parity" \
  --field description="Align the Ansible Collection with the Python CLI safety model (checkpointing, activation, resume, artifact handling, validation)."
```
Expected: JSON response with `"number": <N>` — note the milestone number.

**Step 2: Verify**
```bash
gh api repos/{owner}/{repo}/milestones | jq '.[].title'
```
Expected: `"ansible-python-parity"` in output.

---

## Task 3: Move the spec file

**Step 1: Move work-to-do.md into plans**
```bash
cd /home/tomaz/sources/rh-acm-switchover
git mv work-to-do.md docs/superpowers/plans/2026-05-05-ansible-parity-spec.md
```

**Step 2: Commit**
```bash
git commit -m "docs: move ansible parity spec out of repo root into plans"
```

**Step 3: Push**
```bash
git push origin ansible
```

---

## Task 4: Create PR-1 issue (Checkpoint/state safety)

**Step 1: Create the issue**
```bash
gh issue create \
  --title "[PR-1] Checkpoint/state safety" \
  --label "parity,ansible,P0" \
  --milestone "ansible-python-parity" \
  --body "## Problem

The Ansible checkpoint model records phase completion but is not bound to the operation identity (primary hub, secondary hub, method, restore-only mode). A checkpoint from one hub pair can be reused for another hub pair and cause Ansible to skip phases incorrectly. Additionally, checkpoint writes are not atomic, \`validate\` mode can persist checkpoint state (unlike Python), and there is no \`reset_from\` to clear downstream phases.

Full spec: \`docs/superpowers/plans/2026-05-05-ansible-parity-spec.md\` § PR-1

## Acceptance criteria

- [ ] A checkpoint created for hub pair A fails or resets when used with hub pair B.
- [ ] \`execution.mode=validate\` does not write or mutate the checkpoint file.
- [ ] \`execution.mode=dry_run\` does not write or mutate the checkpoint file.
- [ ] Interrupted writes do not leave partial JSON in the final checkpoint path.
- [ ] Corrupt checkpoint JSON is detected and moved aside.
- [ ] \`reset_from primary_prep\` removes \`primary_prep\`, \`activation\`, \`post_activation\`, and \`finalization\` from \`completed_phases\`.
- [ ] Existing schema \`1.0\` checkpoints with completed phases are rejected unless reset is explicit.
- [ ] Unit tests cover all of the above."
```
Expected: URL of the new issue — note the issue number (e.g. `#28`).

---

## Task 5: Create PR-2 issue (Activation live-read)

**Step 1: Create the issue** (replace `#28` with actual PR-1 issue number)
```bash
gh issue create \
  --title "[PR-2] Activation live-read + passive readiness" \
  --label "parity,ansible,P0" \
  --milestone "ansible-python-parity" \
  --body "## Problem

Preflight reads secondary Restore resources and stores them in \`acm_secondary_restores_info\`. Activation discovery skips a live Restore read when preflight facts already exist, so activation can use a stale Restore snapshot. Activation also does not re-assert \`restore_ready\` before mutation. Python re-verifies passive sync readiness at activation time.

> Depends on: #28 (PR-1 checkpoint identity — activation must record identity on enter)

Full spec: \`docs/superpowers/plans/2026-05-05-ansible-parity-spec.md\` § PR-2

## Acceptance criteria

- [ ] Activation always reads Restore resources live unless an explicit test override is used.
- [ ] Activation fails before mutation when the passive Restore is not activation-ready.
- [ ] Activation no longer consumes \`acm_secondary_restore_info\` from preflight.
- [x] Restore analysis exposes \`restore_phase\` and \`restore_ready\`.
- [ ] Restore analysis exposes \`restore_ready_reason\` if assertion output needs it.
- [ ] Unit tests cover:
  - [ ] passive Restore ready at preflight but failed at activation;
  - [ ] passive Restore missing at activation;
  - [ ] passive Restore present but \`syncRestoreWithNewBackups=false\`;
  - [ ] conventional-name fallback;
  - [ ] benign \`FinishedWithErrors\`;
  - [ ] hard failure phase.
- [ ] An integration-style mocked Ansible run proves preflight facts cannot bypass activation live-read."
```

---

## Task 6: Create PR-3 issue (Phase self-sufficiency)

**Step 1: Create the issue** (replace `#28`/`#29` with actual issue numbers)
```bash
gh issue create \
  --title "[PR-3] Phase self-sufficiency / fact freshness" \
  --label "parity,ansible,P1" \
  --milestone "ansible-python-parity" \
  --body "## Problem

\`primary_prep\` depends on facts gathered by preflight (\`acm_primary_mch_info\`, \`acm_primary_backup_schedules_info\`). If checkpointing skips preflight on resume, primary_prep may lack required facts or use stale ones. Python phases are self-sufficient: each phase re-reads what it needs.

> Depends on: #28 (PR-1 checkpoint — phase entry asserts require checkpoint identity)

Full spec: \`docs/superpowers/plans/2026-05-05-ansible-parity-spec.md\` § PR-3

## Acceptance criteria

- [ ] \`primary_prep\` can run after preflight was skipped by checkpoint.
- [ ] \`activation\` can run without preflight Restore facts.
- [x] \`post_activation\` can run without preflight ManagedCluster facts in execute mode.
- [ ] No phase role requires transient facts created by a previous phase.
- [ ] Tests simulate:
  - [ ] checkpoint has \`preflight\` complete;
  - [ ] no preflight facts are injected;
  - [ ] primary prep still reads its own MCH and BackupSchedules."
```

---

## Task 7: Create PR-4 issue (ArgoCD resume checkpoint semantics)

**Step 1: Create the issue**
```bash
gh issue create \
  --title "[PR-4] ArgoCD resume-on-failure checkpoint semantics" \
  --label "parity,ansible,P1" \
  --milestone "ansible-python-parity" \
  --body "## Problem

The Ansible rescue block resumes ArgoCD and then calls \`status: reset\` for only \`primary_prep\`. This leaves downstream phases (\`activation\`, \`post_activation\`, \`finalization\`) marked complete. A retry may skip activation even though ArgoCD state has changed. The fix is to use \`reset_from primary_prep\` (from PR-1) which removes all downstream phases.

> Depends on: #28 (PR-1 — requires \`reset_from\` implementation)

Full spec: \`docs/superpowers/plans/2026-05-05-ansible-parity-spec.md\` § PR-4

## Acceptance criteria

- [ ] Resume-on-failure resets checkpoint from \`primary_prep\` *and all downstream phases*, not only \`primary_prep\`.
- [ ] Completed downstream phases are removed after ArgoCD resume-on-failure.
- [x] Resume only targets Applications paused by the current run ID.
- [ ] Optional \`resume_force=true\` override is implemented if manual override remains desired.
- [ ] Tests cover:
  - [ ] failure after activation;
  - [ ] completed phases include \`primary_prep\`, \`activation\`;
  - [ ] ArgoCD resume runs;
  - [ ] checkpoint result keeps \`preflight\` but removes \`primary_prep\`, \`activation\`, \`post_activation\`, \`finalization\`."
```

---

## Task 8: Create PR-5 issue (Decommission path safety)

**Step 1: Create the issue**
```bash
gh issue create \
  --title "[PR-5] Decommission/report path safety" \
  --label "parity,ansible,P2" \
  --milestone "ansible-python-parity" \
  --body "## Problem

The decommission role writes \`summary_path\` using \`ansible.builtin.copy\` directly, bypassing the \`acm_safe_path_validate\` module that all other report artifact writes use. This means decommission summaries can be written to arbitrary paths including traversal paths.

> Depends on: #30 (PR-3 — phase self-sufficiency ensures decommission discovery is clean)

Full spec: \`docs/superpowers/plans/2026-05-05-ansible-parity-spec.md\` § PR-5

## Acceptance criteria

- [ ] Decommission summary paths go through the same safe-path validator as other report artifacts.
- [ ] Unsafe summary paths are rejected.
- [ ] Existing valid relative and absolute report paths still work.
- [ ] Unit tests cover:
  - [ ] valid relative path;
  - [ ] valid absolute path under allowed directory;
  - [ ] traversal path;
  - [ ] empty path means no artifact written."
```

---

## Task 9: Create PR-6 issue (Validation parity)

**Step 1: Create the issue**
```bash
gh issue create \
  --title "[PR-6] Python/Ansible validation parity" \
  --label "parity,ansible,P2" \
  --milestone "ansible-python-parity" \
  --body "## Problem

Python and Ansible validation rules are not fully aligned. The ArgoCD \`resume_on_failure\` cross-argument rules and safe-path policy differ between the two implementations. There is no shared fixture that runs the same cases against both.

Full spec: \`docs/superpowers/plans/2026-05-05-ansible-parity-spec.md\` § PR-6

## Acceptance criteria

- [ ] Python and Ansible validation pass/fail the same parity cases.
- [ ] ArgoCD \`resume_on_failure\` rules match in both paths.
- [ ] Path policy is identical in Python and Ansible.
- [ ] Docs describe the accepted path forms.
- [ ] CI runs the parity fixture against both implementations."
```

---

## Task 10: Create PR-7 issue (Klusterlet scalability)

**Step 1: Create the issue**
```bash
gh issue create \
  --title "[PR-7] Klusterlet remediation scalability" \
  --label "parity,ansible,P3" \
  --milestone "ansible-python-parity" \
  --body "## Problem

Klusterlet remediation in Ansible is sequential (one cluster at a time). Python uses bounded concurrency (10 workers via ThreadPoolExecutor). For large deployments, sequential remediation can be significantly slower than necessary.

> Depends on: #30 (PR-3 — phase self-sufficiency ensures post_activation discovery is clean)

Full spec: \`docs/superpowers/plans/2026-05-05-ansible-parity-spec.md\` § PR-7

## Acceptance criteria

- [ ] Klusterlet probe/remediation supports bounded concurrency.
- [ ] Default concurrency matches Python's effective behavior: 10 workers.
- [ ] Sequential behavior can be forced with \`workers=1\`.
- [ ] Module returns per-cluster structured results.
- [ ] Existing best-effort behavior remains available.
- [ ] Tests cover:
  - [ ] no pending clusters;
  - [ ] pending cluster without kubeconfig;
  - [ ] import secret missing;
  - [ ] successful remediation;
  - [ ] partial remediation failure;
  - [ ] strict and non-strict modes."
```

---

## Task 11: Create PR-8 issue (Docs and migration map)

**Step 1: Create the issue**
```bash
gh issue create \
  --title "[PR-8] Docs, migration map, and runbook updates" \
  --label "parity,ansible,P2" \
  --milestone "ansible-python-parity" \
  --body "## Problem

After PRs 1–7 land, the migration map, variable reference, and non-protected docs need updating to reflect the new checkpoint behavior, safe-path policy, and validate/dry-run semantics. The runbook and \`.claude/skills\` are protected files — any changes require explicit operator approval and must be synchronized.

> Depends on: all PRs (#28 #29 #30 #31 #32 #33 #34)

Full spec: \`docs/superpowers/plans/2026-05-05-ansible-parity-spec.md\` § PR-8

## Acceptance criteria

- [ ] Migration map no longer references stale Python option names.
- [ ] Variable reference documents all new variables.
- [ ] Non-protected docs explain validate/dry-run checkpoint behavior.
- [ ] Non-protected docs explain safe checkpoint reset.
- [ ] Runbook and \`.claude/skills\` changes are either explicitly approved and synchronized, or intentionally deferred with the reason documented.
- [ ] CHANGELOG has an operator-facing compatibility note."
```

---

## Task 12: Verify and orient

**Step 1: List all open issues in the milestone**
```bash
gh issue list --milestone ansible-python-parity --state open
```
Expected: 8 issues listed, ordered by number.

**Step 2: Confirm dependencies are visible**
```bash
gh issue view <PR-2 issue number>
```
Expected: Body contains `Depends on: #<PR-1 number>`.

**Step 3: Check milestone progress**
```bash
gh api repos/{owner}/{repo}/milestones | jq '.[] | select(.title=="ansible-python-parity") | {open: .open_issues, closed: .closed_issues}'
```
Expected: `"open": 8, "closed": 0`

---

## Session start command (save this)

```bash
# Run at the start of every session to orient
gh issue list --milestone ansible-python-parity --state open --json number,title,labels | \
  jq -r '.[] | "#\(.number) \(.title) [\(.labels | map(.name) | join(","))]"'
```
