# PR 24 Conflict Resolution Design

## Context

GitHub reports PR #24, `ansible` into `main`, as conflicted. Local inspection shows the branch is clean and the conflict surface is limited to files touched by recent `main` changes:

- `lib/constants.py`
- `modules/activation.py`
- `modules/preflight/backup_validators.py`
- `tests/e2e/orchestrator.py`

The `main` side centralizes the restore status text `"already available"` as `RESTORE_ALREADY_AVAILABLE_MARKER`. The PR branch also carries Ansible collection foundation work and intentionally removed stale Argo CD auto-resume-after-switchover wiring that must stay removed.

## Chosen Approach

Merge `origin/main` into the current `ansible` branch and resolve conflicts locally.

This keeps the long-running PR history stable and avoids rewriting commits that reviewers and CI may already reference. A rebase would produce cleaner linear history, but it has higher coordination risk for this branch.

## Resolution Rules

- Keep the new `RESTORE_ALREADY_AVAILABLE_MARKER` constant from `main`.
- Update Python restore handling to use `RESTORE_ALREADY_AVAILABLE_MARKER` instead of hard-coded string checks.
- Preserve the PR branch's activation imports and behavior, including `PRE_ACTIVATION_VELERO_MANAGED_CLUSTERS_RESTORE_NAME`.
- Preserve the PR branch's removal of stale e2e `argocd_resume_after_switchover` configuration and pass-through wiring.
- Do not edit protected runbook or `.claude/skills` files.
- Keep changes limited to conflict resolution unless tests reveal a directly related issue.

## Verification

After resolving conflicts, run targeted tests around the touched behavior:

- activation restore handling tests
- preflight backup validator tests
- e2e orchestration tests or the narrowest available test target for `tests/e2e/orchestrator.py`

If the targeted tests cannot run because of environment limitations, record the exact command and failure reason.
