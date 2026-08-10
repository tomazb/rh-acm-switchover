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
  and dispositioned as non-blocking when the governing gate says so; when no
  tracker owns the finding, an issue is filed before it is dispositioned.
  Deferred is not lost. A non-blocking comment that is only a preference or a
  nit gets a reply and nothing else — filing non-findings becomes pressure to
  reopen validation later.
- Terminal PASS means every required participant returned a merge-ready verdict
  for the frozen head. Where a workflow grades verdicts, `PASS` and `PASS WITH
  NON-BLOCKING COMMENTS` both count; `BLOCKED` and `HARD FAIL` do not.
- Once every required participant has returned a merge-ready verdict for the
  frozen head, stop: no additional reviewers, no further unscoped adversarial
  pass, no cosmetic cleanup edits that invalidate the terminal evidence.
- PASS does not authorize merge. Merge stays an operator decision under the
  normal merge gate, and terminal PASS on the frozen head satisfies that gate's
  `code-review` invocation for the same head. Comment disposition, thread
  resolution, and required CI remain mandatory.

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
| `AGENTS.md` | New `## Terminal Validation and Review Convergence` section; three appended cross-reference bullets. Additions plus one deletion — the single deletion is the Phase 9 bullet's continuation line, replaced by an extended version carrying the cross-reference. Exact counts move with each review-driven commit; read them from `git diff --numstat 16c247c5 -- AGENTS.md` at the frozen head rather than from this table. |
| `docs/superpowers/specs/2026-08-10-terminal-validation-convergence-design.md` | This design (operator-approved). |

`CHANGELOG.md` is deliberately not updated. `CONTRIBUTING.md` lists "Update
CHANGELOG.md" in the pull-request process, and `AGENTS.md` qualifies it as
recording *changelog-worthy* development changes. This slice changes agent
process instructions only: no user-facing behavior, CLI surface, module
behavior, or packaging changes. Repository precedent matches — of the twenty
most recent documentation-only merges to `ansible`, eighteen touched no
`CHANGELOG.md`, and the two that did also carried code changes. Adding a third
file would also exceed the file surface #242 authorizes.

## Verification

Documentation CI exists but does not gate this change: the "Documentation
Check" job in `.github/workflows/ci-cd.yml` omits `AGENTS.md` from its
`required_docs` existence list, and its markdown-link-check step runs
`continue-on-error: true`. Nothing in CI validates `AGENTS.md` content,
structure, or links as a blocking gate, so local verification carries the
weight:

- Baseline: branch cut from `origin/ansible` at the immutable commit
  `16c247c5`, and `AGENTS.md` confirmed byte-identical to
  `git show 16c247c5:AGENTS.md` before any edit.
- `git diff --check` clean.
- `git diff 16c247c5 -- AGENTS.md` adds the new section and the three pointer
  bullets; every existing Creation Gate and Merge Gate bullet is byte-identical.
- All four in-document anchors resolve to existing headings.
- No production, runtime, or parity-sensitive path in `git diff --name-only`.
- #242's acceptance checklist walked item by item against the actual diff.

This document records the immutable *baseline* SHA, not the candidate head: a
file cannot contain the SHA of the commit that introduces it. The frozen
candidate head, and each required validator's verdict against that head, are
recorded on the pull request at terminal validation time, which is where the
rule places them.

Gate deviation, recorded rather than resolved silently: the PR Creation Gate's
`code-review` skill was not invoked before opening the PR. The
"Prohibited Patterns" rule added here bars *repeated* full-suite reruns after
each prose-only observation; it does not waive the initial review, so this is a
deviation and not an exemption. The branch was reviewed at head `ec5a0cff` by
the repository's automated reviewers (CodeRabbit, GitHub Copilot, and Codex),
and their findings were dispositioned in a resolver pass. Whether that
substitutes for the named skill is an operator decision, flagged on the PR.

## This change under its own rule

This slice is governed by #242's acceptance checklist, which is explicit and
falsifiable. Once required validation returns PASS for a frozen head, review
stops: no additional reviewer, no unscoped adversarial pass, no cosmetic
post-PASS edits. Validation reopens only on the six enumerated conditions.
