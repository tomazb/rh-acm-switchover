# Graphify-Assisted Critical Review Design

## Summary

Use Graphify as a triage layer for finding high-risk review leads in the ACM switchover codebase, then verify every lead directly against source code and tests before reporting it. The review must produce three outputs:

- a review design that explains how Graphify guides the work
- a reusable checklist for future agents
- confirmed critical or important findings only

Graphify relationships marked `INFERRED` or `AMBIGUOUS` are hypothesis sources, not evidence. A lead becomes a finding only when source code and tests show a credible correctness, safety, parity, performance, or destructive-operation risk.

## Scope

The review focuses on safety-critical and parity-sensitive areas:

- state, checkpoint, and resume behavior
- wrong-hub, wrong-context, and wrong-namespace mutation risk
- klusterlet and post-activation fail-closed behavior
- activation, finalization, restore, BackupSchedule, and decommission mutations
- RBAC validation, RBAC manifests, and generated permission surfaces
- Argo CD pause, resume, resume-on-failure, and generated Application handling
- Python CLI and Ansible collection parity for dual-supported capabilities
- tests that claim safety coverage but do not exercise the relevant implementation path

The review intentionally excludes cosmetic style issues, low-risk duplication, and speculative refactors unless they can hide critical or important behavior risk.

## Graphify Signals

Use these Graphify signals as lead generators:

- High-degree nodes as blast-radius indicators. Current examples include `StateManager`, `KubeClient`, `SecondaryActivation`, `PostActivationVerification`, and checkpoint utilities.
- Bridge nodes as places where behavior contracts cross module boundaries.
- Community clusters around state, checkpoints, klusterlet, RBAC, Argo CD, activation, finalization, decommission, backup validation, and restore behavior.
- Graphify `surprises` that connect documentation to code, or Python CLI concepts to Ansible collection concepts.
- Paths between paired concepts, such as Python post-activation verification and collection klusterlet modules.

Ignore these signals unless source confirms a real issue:

- generic keyword hits such as `files`, `Exception`, or broad inferred relationships
- `INFERRED` or `AMBIGUOUS` edges without concrete code paths
- isolated maintainability smells that do not affect safety, parity, correctness, or operator impact

## Verification Rules

A candidate is reportable only when source evidence shows at least one of these risks:

- It can mutate the wrong hub, namespace, cluster, Argo CD Application, restore, BackupSchedule, or RBAC resource.
- It can resume from stale, mismatched, or unverifiable state.
- It can fail open after an API/client error, timeout, malformed input, or partial result.
- It creates Python CLI and Ansible collection parity drift for a dual-supported capability.
- It reports success or skips validation without checking the real condition.
- It creates meaningful performance risk in realistic operator workflows, such as unbounded waits, repeated broad discovery, or unnecessarily serial Kubernetes API calls.
- Its tests claim to cover safety-critical behavior while missing the relevant implementation path.

Severity is assigned as follows:

- `Critical`: credible path to destructive wrong-target mutation, unsafe resume, data loss, or broad privilege/resource misconfiguration.
- `Important`: credible fail-open behavior, parity drift, major operator misreporting, or workflow performance likely to affect real runs.

## Review Flow

1. Read the existing `graphify-out/` graph in the current workspace. Do not move to a worktree because the local graph data is part of the review context.
2. Extract Graphify hubs, bridge nodes, surprises, and relevant community clusters.
3. Review focused slices:
   - state, checkpoint, and resume
   - klusterlet and post-activation
   - activation, finalization, restore, and BackupSchedule handling
   - RBAC and decommission
   - Argo CD pause, resume, and resume-on-failure
   - Python CLI versus Ansible collection parity tests
4. For each slice, use Graphify to identify connected files, then inspect source with targeted `rg` and file reads.
5. Compare Python and Ansible implementations where the parity matrix marks a capability `dual-supported`.
6. Verify candidate findings against existing tests. If coverage is missing or misleading, inspect whether that gap hides a real risk.
7. Report only confirmed critical or important findings with exact file and line references.
8. For reviewed slices with no confirmed issue, briefly record residual risk instead of inventing findings.

## Reusable Checklist

- Start in the repository root that contains the current `graphify-out/` directory.
- Check `git status` and avoid modifying unrelated Graphify cache artifacts.
- Read `graphify-out/GRAPH_REPORT.md`, `graphify-out/analysis.json`, and targeted `graphify explain` output for high-risk nodes.
- Treat Graphify as a hypothesis generator only.
- Prioritize safety hubs: state, checkpoints, resume, mutation wrappers, activation, post-activation, finalization, decommission, RBAC, Argo CD, and BackupSchedule.
- Follow Graphify surprises and bridge nodes into source files, then verify manually.
- Check Python CLI and Ansible collection behavior together for dual-supported surfaces.
- Verify tests reach the risky code path and assert fail-closed behavior.
- Report only critical or important issues with source evidence.
- Do not report style, naming, or generic complexity unless it materially affects safety or correctness.

## Deliverable Format

The final review output should contain:

- confirmed findings first, ordered by severity
- file and line references for each finding
- a short note for reviewed areas with no confirmed issue
- the reusable checklist
- any verification commands run, including failures or skipped checks

If no critical or important findings are confirmed, state that explicitly and identify remaining residual risks or coverage gaps.
