# External Code Review Triage Design

**Date:** 2026-04-07
**Branch:** `claude/implement-gitops-detection-0tFI2`
**Status:** Approved design

## Goal

Define a reusable workflow for handling **pasted external code review findings** in a way that is evidence-first, minimal, and safe for this repository.

The workflow should:

1. decide what to do with each finding
2. propose code changes only for well-supported findings
3. avoid speculative or reviewer-pleasing changes that are not justified by the code

## Context

This repository favors:

- minimal, localized changes
- fail-fast behavior and explicit errors
- preserving behavior unless a real defect is demonstrated
- keeping tests and related docs aligned with behavior changes

The design therefore treats external review feedback as **input to investigate**, not as instructions to apply blindly.

## Chosen Approach

Use an **evidence-first triage workflow with an implementation tail**.

For each pasted finding:

1. normalize it into a discrete review item
2. inspect the current code, tests, and nearby behavior
3. classify the finding as `accept`, `reject`, or `needs-clarification`
4. batch only accepted findings into a minimal coherent code-change set

This approach is preferred over:

- a reviewer-first workflow, which is faster but too error-prone
- a strict two-pass workflow, which is safer than reviewer-first but adds unnecessary handoff overhead for accepted findings

## Workflow

### 1. Intake

Convert pasted feedback into individual findings with enough structure to reason about them:

- finding text
- affected file/module if known
- claimed problem
- implied fix, if the reviewer suggested one

### 2. Evidence Pass

Investigate each finding against the current branch state:

- read the referenced code path
- inspect related tests and nearby logic
- check whether the concern is already handled elsewhere
- note when the finding appears stale relative to the current branch

### 3. Decision

Each finding lands in exactly one bucket:

- **Accept** — supported by code evidence and has a clear, behavior-safe fix
- **Reject** — contradicted by the code, redundant, or likely to make the code worse
- **Needs clarification** — plausible but too ambiguous or incomplete to act on safely

### 4. Change Planning

Group accepted findings by shared file or behavior so edits stay coherent and avoid repeated churn in the same code paths.

The planned change set should stay tightly scoped to the accepted findings and any directly coupled fixes they require.

### 5. Implementation Guardrails

For accepted findings:

- keep changes minimal and localized
- preserve existing behavior unless the finding proves the behavior is wrong
- update tests when behavior or safety-critical branching changes
- update directly related docs only when the code change makes them inaccurate

## Safeguards

- No finding is implemented solely because a reviewer said so
- Ambiguous, under-specified, or stale findings stay in `needs-clarification`
- Rejected findings still get a short rationale so triage remains auditable
- Accepted findings are implemented in grouped batches, not as scattered one-off edits
- If investigation exposes a broader issue than the original finding, only the directly necessary fix is included unless scope is explicitly expanded

## Expected Output

For each review batch, the output should include:

1. a per-finding decision (`accept`, `reject`, `needs-clarification`)
2. the evidence or rationale behind that decision
3. the proposed code changes for accepted findings
4. any required tests or docs updates tied to those accepted findings

## Out of Scope

- blindly implementing all review comments
- refactoring unrelated code while addressing findings
- treating style-only suggestions as mandatory unless they match repository conventions or reveal a real maintainability issue
- guessing at intent when the review feedback is unclear
