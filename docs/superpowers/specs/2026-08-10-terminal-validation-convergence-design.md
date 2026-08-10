# Terminal Validation and Review Convergence — Design

Status: design for issue #242 (process/documentation only).
Primary deliverable: a new `AGENTS.md` section.

## Problem

Governed review in this repository could become non-terminating.

PR #237 is the worked example. The combination of an unfalsifiable review
threshold and serial post-PASS review produced this loop:

1. A reviewer returned a valid but non-blocking observation.
2. The observation triggered a repair commit.
3. The repair moved the head, which invalidated the exact-head validation that
   had already been earned.
4. Invalidated validation justified another review round, returning to step 1.

The loop ran five repair rounds. The document grew. Two previously earned
exact-head PASS verdicts were voided. Nothing in the process defined when
review was finished.

Issue #226 fixed this locally for TST-00: define a falsifiable publication
gate, freeze the candidate head, run the required reviewers against that same
head, and stop soliciting review after terminal PASS. That rule is not specific
to TST-00. It is a repository process invariant, and `AGENTS.md` should own it.

## Goal

Give a governed slice a defined termination condition for review, without
weakening correctness or safety review.

A **governed slice** is work whose governing issue or specification defines an
explicit, falsifiable acceptance gate. Work without such a gate is unaffected
and keeps the default `Pull Request Creation Gate` / `Pull Request Merge Gate`
behavior.

## The rule

**Terminal validation.**

- Freeze the candidate head before terminal validation begins; record the SHA.
- Run every required validator and reviewer against that same exact head.
- Validators evaluate only the acceptance criteria defined by the governing
  issue or specification, and do not silently add new merge criteria.
- Valid findings outside the current slice are recorded in their owning tracker
  and dispositioned as non-blocking when the governing gate says so. Deferred
  is not lost.
- Once every required participant has returned PASS for the frozen head, stop:
  no additional reviewers, no further unscoped adversarial pass, no cosmetic
  cleanup edits that invalidate the terminal evidence.
- PASS does not authorize merge. Merge stays an operator decision under the
  normal merge gate.

**Reopen conditions.** Validation reopens only when the candidate head changes,
the target/base relationship materially changes, required CI becomes invalid or
failing, a previously unresolved actionable thread is discovered, genuinely new
blocking evidence arrives before merge, or the operator explicitly reopens it.

**Safety boundary.** The rule is not "ignore comments after PASS". It does not
suppress findings discovered before terminal validation finishes, does not
permit merging with an unresolved actionable thread, and does not relax CI
requirements. New pre-merge evidence of a real violation — acceptance criteria,
safety boundary, correctness contract, unresolved actionable thread, or
required CI state — must still be dispositioned and may reopen validation. What
stops is *actively generated serial review after terminal PASS*, not the
handling of known defects.

**Prohibited patterns.** Serially inviting a new reviewer after each PASS;
treating "zero possible observations" as an acceptance criterion; converting
deferred downstream findings into blockers; cosmetic post-PASS edits that force
exact-head revalidation; silently expanding a falsifiable acceptance gate
during review; generic full-suite or toolchain reruns after every prose-only
review observation when a bounded terminal-validation gate already exists.

**Three-prompt alignment.** After a governed terminal PASS, the independent
validator and the PR-comment resolver stop and hand control back to the
operator instead of invoking another reviewer or running an unscoped "one more
review" pass.

## Design decisions

**Placement — a new H2 between `Pull Request Merge Gate` and `Code Review
Guidelines`.** The rule modulates merge-gate behavior, so it reads immediately
after it. Issue #242 suggested an `###`; that was an "e.g.". H2 matches the
surrounding gate sections, and only a top-level section can carry the stable
anchor the cross-references need.

**Pointer-only cross-references.** The existing Creation Gate and Merge Gate
bullets are left byte-identical; each gains one appended pointer bullet. The
new section carries its own applicability scoping instead of carving exceptions
into the older sections. Rationale: an issue caused by document growth and
scope creep should not be fixed by rewriting two unrelated policy sections. The
minimal diff is part of the argument.

Note the tension this resolves. The Merge Gate says "re-run the `code-review`
skill after any review-driven change". Combined with exact-head validation that
is self-defeating — each rerun's findings mutate the head and void the verdict
the rerun was meant to produce. The new section scopes that generic loop out
for governed slices only, and leaves it as the default everywhere else.

**Third cross-reference at the Phase 9 bullet.** `AGENTS.md` mentions the
three-prompt workflow in exactly one place (`Phase 9 Live Controller
Authority`), as a bare name-drop with no definition and no link. That bullet
gets the pointer, satisfying #242's "concise cross-reference in the
independent-validator / PR-comment-resolver workflow guidance".

**`docs/testing/property-based-testing-pr-workflow.md` is cited, not edited.**
That document holds the detailed three-prompt contract — exact-head verdicts,
`PASS` / `PASS WITH NON-BLOCKING COMMENTS` / `BLOCKED` / `HARD FAIL`, resolver
scope control — but it is self-scoped to the property-based-testing initiative
(issue #136). The new section cites it as the detailed PBT-scoped instance and
explicitly preserves that scoping rather than silently promoting it to
repository-wide authority. Editing it would trip #242's stop-and-obtain-
approval clause for other process documents.

**Substance transcribed near-1:1 from the issue.** Wording is restyled to house
idiom (imperative bullets, ~80-column wrap, lowercase "must/do not" rather than
RFC-2119 keywords). The substance is not creatively reworded: #242's acceptance
checklist is the falsifiable gate this change is diffed against, and paraphrase
drift would invite exactly the review loop the rule exists to end.

## Non-goals

Explicitly out of scope, per the operator's comment on #242:

- #244 — supported Ansible/AAP dependency and CI matrix.
- #245 — `AGENTS.md` as the durable repository policy authority.
- #246 — contributor, testing, and architecture doc realignment.

Also out of scope: any production or runtime code, tests, RBAC, manifests,
Helm, release-controller behavior, parity-sensitive behavior, live-lab
mutation, `docs/ACM_SWITCHOVER_RUNBOOK.md`, and `.claude/skills/**/*.skill.md`.

Absorbing any of these would recreate the scope-expansion failure this change
corrects.

## Changed files

| File | Change |
| --- | --- |
| `AGENTS.md` | New `## Terminal Validation and Review Convergence` section; three appended cross-reference bullets. No deletions. |
| `docs/superpowers/specs/2026-08-10-terminal-validation-convergence-design.md` | This design (operator-approved). |

## Verification

No blocking documentation CI exists — `.github/workflows/ci-cd.yml` omits
`AGENTS.md` from its `required_docs` list and its markdown-link-check step is
`continue-on-error: true`. Local verification is therefore the entire gate:

- `AGENTS.md` confirmed byte-identical to `origin/ansible` before editing.
- `git diff --check` clean.
- `git diff origin/ansible -- AGENTS.md` shows additions only; existing gate
  bullets unchanged.
- All three in-document anchors resolve to the new section.
- No production, runtime, or parity-sensitive path in `git diff --name-only`.
- #242's acceptance checklist walked item by item against the actual diff.
- `code-review` skill run against the branch per the PR Creation Gate.

## This change under its own rule

This slice is governed by #242's acceptance checklist, which is explicit and
falsifiable. Once required validation returns PASS for a frozen head, review
stops: no additional reviewer, no unscoped adversarial pass, no cosmetic
post-PASS edits. Validation reopens only on the six enumerated conditions.
