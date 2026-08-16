# Property-Based Testing — PR Workflow and Process Contract

Status: PBT-01 (documentation only). Part of issue #136.
Companion documents: [`property-based-testing.md`](property-based-testing.md)
(plan) and [`property-based-testing-spec.md`](property-based-testing-spec.md)
(suite specifications).

This document is the process contract for delivering the PBT initiative. It
defines the nine expected PRs, their dependency graph, the three-prompt
workflow (Builder, Independent Validator, PR Comments Resolver) used for
each PR, the hard-fail rules, and the merge-readiness requirements.

## The nine PRs

| PR | Branch scope | Delivers | Spec authority |
| --- | --- | --- | --- |
| PBT-01 | docs only | This documentation set | issue #136 |
| PBT-02 | scaffolding | Hypothesis dependency, `tests/property/` layout, shared strategies, `property` marker, CI wiring | spec §"Scaffolding contract" |
| PBT-03 | tests only | Suite 1 — validation parity properties | spec §Suite 1 |
| PBT-04 | tests only | Suite 2 — path-safety properties | spec §Suite 2 |
| PBT-05 | tests only | Suite 3 — checkpoint/resume properties | spec §Suite 3 |
| PBT-06 | tests only | Suite 4 — report artifact properties | spec §Suite 4 |
| PBT-07 | tests only | Suite 5 — BackupSchedule properties | spec §Suite 5 |
| PBT-08 | tests only | Suite 6 — Argo CD safety properties | spec §Suite 6 |
| PBT-09 | tests only | Suite 7 — RBAC set-property tests | spec §Suite 7 |

## Dependency graph

```text
PBT-01 docs/spec/workflow
  └── PBT-02 scaffolding
        ├── PBT-03 validation parity properties
        ├── PBT-04 path-safety properties
        ├── PBT-05 checkpoint/resume properties
        ├── PBT-06 report artifact properties
        ├── PBT-07 BackupSchedule properties
        ├── PBT-08 Argo CD safety properties
        └── PBT-09 RBAC set-property tests
```

PBT-02 must merge before any suite PR opens. PBT-03 through PBT-09 are
mutually independent and may proceed in parallel once PBT-02 is merged.

## Hard-gate expectations for every future PR

Every PBT PR (02 through 09) must document, in its PR body, its own
hard-gate expectations:

1. **Base branch**: `ansible`. The working branch is created from the
   current `origin/ansible`; the PR targets `ansible`, never `main`.
2. **Allowed / forbidden files**: an explicit allowed-file list for that PR
   and confirmation that nothing outside it was touched. Always forbidden:
   `docs/ACM_SWITCHOVER_RUNBOOK.md`, `.claude/skills/**` (protected
   critical files per `AGENTS.md`), and — for suite PRs — any production
   code change (a property that fails against current behavior is reported,
   not "fixed" inside the suite PR).
3. **Parity statement**: an explicit statement of the PR's parity impact.
   PBT PRs must not change any capability's parity status and must not
   imply divergence between the Python CLI and the Ansible collection. If a
   property exposes a genuine disagreement in dual-supported behavior, the
   suite PR is `BLOCKED` until a separate parity-preserving fix restores
   agreement and the property passes. Because suite PRs cannot modify
   production code, that fix lands through the normal `AGENTS.md` parity
   process with both implementations, tests, and documentation updated
   together. A temporary expected failure may merge only after explicit
   operator approval under the `AGENTS.md` intentional-parity-change gate
   and after the approved divergence is recorded in the required in-repo
   parity documentation. Filing a parity bug alone is not approval.
4. **Verification evidence**: the exact commands run (from the spec's
   per-suite verification commands plus the repo-standard gates, e.g.
   `./run_tests.sh`) and their observed results, pasted or summarized in
   the PR body.

## The three-prompt workflow

Each PR is driven by three separately-prompted agent roles with strict
separation of duties. Prompts are the operational contract; PR bodies
describe the change, not the prompting machinery.

### Builder prompt contract

The Builder implements exactly one PR from the table above.

- Works in an isolated worktree on a branch created from `origin/ansible`.
- Verifies prerequisites first (readable `AGENTS.md`, clean tree, correct
  base) and hard-fails without making changes if they are not met.
- Touches only that PR's allowed files.
- Runs the PR's verification commands and records results.
- Self-reviews the final diff before pushing.
- Commits with repo-style messages and **no AI-attribution trailers**
  (no `Co-Authored-By` or similar AI-attribution lines; this workflow
  contract is the normative statement of that prohibition for PBT PRs).
- Pushes the branch and opens a ready (non-draft) PR against `ansible`
  containing the hard-gate documentation defined above.
- Returns a structured report (status, PR URL, branch, base commit, files,
  verification evidence, self-review findings).

### Independent Validator prompt contract

The Validator reviews a Builder PR it did not write.

- Uses a fresh clean checkout/worktree and records the exact PR head SHA and
  current `origin/ansible` SHA it validated.
- Read-only with respect to the branch and PR state: the Validator **never
  pushes** commits, never amends, never rebases, never implements fixes,
  never edits PR metadata, never resolves threads, never marks the PR ready,
  and never merges it. Its only permitted PR mutation is publishing the
  terminal validation report as a new top-level PR comment.
- Independently re-derives the gate checks: base branch correctness,
  allowed-file compliance, protected files untouched, parity statement
  accuracy against the actual diff, spec-section satisfaction, and
  verification evidence reproducibility (re-running the stated commands).
- Treats its verdict as applying only to the recorded head SHA. Any later
  commit invalidates that merge-readiness verdict and requires a fresh
  Independent Validator pass on the new head.
- Issues exactly one verdict:
  - `PASS` — merge-ready as-is at the recorded head.
  - `PASS WITH NON-BLOCKING COMMENTS` — merge-ready at the recorded head;
    comments are suggestions only.
  - `BLOCKED` — specific, actionable defects must be resolved before
    merge; each defect cites file/line or a failing command.
  - `HARD FAIL` — a gate violation (wrong base, forbidden file touched,
    parity misstatement, unreproducible evidence); the PR must not merge
    and the Builder or Resolver must remediate from the gate level up.
- Immediately before publishing the terminal report, re-fetches the PR and
  confirms that the current head SHA is still the validated SHA and that the
  governing base relationship, including merge base, is unchanged. If either
  changed, the Validator does **not** publish the prior result as a current
  PASS; it revalidates the new exact state first.
- Publishes the terminal report as a **new top-level PR comment**. The comment
  records: verdict; base SHA, head SHA, and merge-base SHA; changed-file
  scope; protected-file result; applicable validation results; CI status;
  review-thread status; merge-readiness assessment; and confidence.

### PR Comments Resolver prompt contract

The Resolver drives an already-open PR to merge-readiness after review
feedback exists.

1. **Fetch everything first**: top-level issue-style PR comments, review
   comments, review submissions, review threads (including resolution
   state), and CI/check status for the exact head commit. Treat Independent
   Validator terminal comments as evidence tied to the exact head/base
   relationship they record; never silently reuse a stale report.
2. **Validate before changing**: every actionable comment is validated
   against the actual source before any edit — the Resolver confirms the
   claim is true (or false) in the code, and never applies a suggested
   change solely because a reviewer asserted it.
3. **Apply only in-scope fixes**: fixes must stay within the PR's allowed
   files and spec scope. Out-of-scope requests get a reply with rationale
   and, where warranted, a filed follow-up issue — not a code change.
4. **Rerun checks**: after any change, rerun the PR's verification commands
   and confirm CI/checks on the new exact head commit.
5. **Re-fetch feedback**: after pushing, re-fetch top-level comments,
   reviews, and review threads to catch new or updated feedback.
6. **Resolve/reply discipline**: a thread is resolved or replied to only
   after the corresponding fix or rationale is pushed — never
   preemptively.
7. **Re-validate the new head**: after substantive fixes, obtain a fresh
   Independent Validator verdict covering the exact new head and governing
   base relationship, published by that Validator as a new top-level PR
   comment. A verdict/comment on an earlier head cannot be reused or edited
   into current evidence by the Resolver.
8. **Hard fail** if actionable feedback cannot be resolved within scope, if
   required checks are failing, pending, or unknown, or if a current-head
   Validator terminal comment is missing at the end of the pass.

### Hard-fail message format

All three roles use the same format when a gate cannot be satisfied. The
role stops, makes no (further) changes, and returns:

```text
HARD FAIL — <reason category>

Missing prerequisites:
- <missing item>
- <blocked checkpoint>

No code/docs changes were made.
Next required operator action:
- <action>
```

For a missing, unreadable, or unusable required Superpowers/Obra skill, the
reason category is exactly `Superpowers skill prerequisite`.

(For the Resolver, "No code/docs changes were made" covers changes beyond
those already pushed and reported.)

## Merge-readiness requirements

A PBT PR may merge only when all of the following hold:

- Base branch is `ansible` and the branch is current enough to merge
  cleanly.
- All required CI checks on the exact head commit are green — not pending,
  not unknown.
- The most recent Independent Validator terminal report is a top-level PR
  comment that records the current exact head and governing base/merge-base
  relationship, and its verdict is `PASS` or `PASS WITH NON-BLOCKING
  COMMENTS`; a later commit, changed base relationship, `BLOCKED`, or `HARD
  FAIL` bars merging until the current exact state is re-validated and a new
  terminal comment is published.
- No property covering dual-supported behavior remains an expected failure
  for a parity disagreement unless the operator explicitly approved the
  intentional divergence under the `AGENTS.md` parity gate and the required
  in-repo parity documentation records that approval.
- Every review thread is handled: resolved with a pushed fix, or answered
  with a pushed/pinned rationale that the thread participant can evaluate;
  no actionable comment is left unaddressed or silently dismissed.
- The PR body carries the hard-gate documentation: base branch,
  allowed/forbidden files, parity statement, and verification evidence.
- The diff touches only the PR's allowed files; protected critical files
  are untouched.

## Skills as process mechanics

The required Superpowers/Obra process skills are **execution-environment
prerequisites**, not files vendored by this repository. The authoritative
source is the agent host's installed skill registry/catalog, or an explicit
skill path/URI supplied by the operator in the prompt or environment. The
protected repository `.claude/skills/**` tree contains project operational
skills and must not be assumed to provide these process skills.

Before acting, each role must:

1. Query the host agent's available-skill registry/catalog (or inspect the
   explicit operator-supplied skill path/URI).
2. Resolve each required checkpoint below to an installed skill by exact
   identifier or documented alias.
3. Open/read that skill's instructions.
4. Record the resolved skill identifier and source path/URI in the role's
   structured report, together with the concrete checkpoint it governs.

Preferred upstream identifiers include `writing-plans`,
`using-git-worktrees`, `verification-before-completion`,
`requesting-code-review`, `receiving-code-review`, and
`systematic-debugging`; hosts may expose documented aliases, but matching a
name without opening the underlying instructions is not sufficient.

Each role must also read the current `AGENTS.md` from `ansible`. If the host
provides no skill registry/catalog, an identifier cannot be resolved to
readable instructions, or the instructions cannot be applied, the role
hard-fails with `HARD FAIL — Superpowers skill prerequisite` before making
changes. Manual substitution does not satisfy this prerequisite. This is an
intentional fail-closed gate from issue #136; the repository does not bundle
placeholder copies merely to bypass it.

Each prompt stage uses the resolved applicable skills as **process
mechanics** mapped to concrete checkpoints — they structure how the agent
works, and they are **not PR-body content** (PR bodies never narrate skill
usage):

- **Builder**: planning (a written plan before edits — `writing-plans`),
  git/worktree hygiene (isolated worktree and correct base —
  `using-git-worktrees`), testing (commands executed before completion
  claims — `verification-before-completion`, plus test-driven habits for
  suite PRs), and final self-review (`requesting-code-review`).
- **Validator**: fresh-context independent review, code review, test-quality
  review, and parity/safety review. Resolve the installed review skills that
  cover those checkpoints and record their identifiers/sources; do not infer
  that a similarly named repository document is a process skill.
- **Resolver**: comment-resolution mechanics (`receiving-code-review`),
  root-cause validation (`systematic-debugging`), scoped fixes, testing, and
  final completion proof (`verification-before-completion`).
