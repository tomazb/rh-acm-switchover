# AGENTS.md as Durable Repository Policy Authority — Design

Status: design for issue #245 (process/documentation only).
Primary deliverable: a restructured `AGENTS.md`, paired with the documentation
guardrail tests that pin its durable policy semantics.

**Date:** 2026-08-11
**Issue:** #245 — Process: refresh `AGENTS.md` as the durable repository policy authority
**Blockers:** #242 (closed 2026-08-10, PR #247), #244 (closed 2026-08-10, PR #249)

## Problem

`AGENTS.md` is the cross-agent policy authority for this repository. At 867 lines it
carries strong safety policy, but it has accumulated changing architecture inventories,
temporary phase status, exact tool commands, and tool-specific mechanics beside the
durable rules. The document is still valuable and no longer fully reliable: an agent that
follows it literally is told several things that are not true of the current repository.

Each defect below was verified against the repository at `89108fa0`, not inferred from the
issue text.

| # | Current wording | Verdict | Evidence |
| --- | --- | --- | --- |
| P1 | `AGENTS.md:7` — the Python CLI is "a monolithic orchestrator" | Stale | Orchestration is delegated: `lib/operation_runners.py:100` declares the phase flow (`:138-186`), `lib/workflow.py:213 run_phase_flow` executes it, `lib/cli_outcomes.py:142` owns outcomes. `acm_switchover.py` is argparse (`:94-359`), hook builders, and five thin phase adapters. |
| P2 | `AGENTS.md:186` — "Each phase handler checks `state.get_current_phase()` before executing" | False | Zero occurrences of `get_current_phase` in `modules/`. Handlers call `set_phase()` unconditionally (`acm_switchover.py:625,781,809`). Eligibility (`lib/workflow.py:225,240`) and durable transition verification (`:246`) are centrally owned. |
| P3 | `AGENTS.md:374-375` — "Phase 9B remains blocked until Phase 9A is merged" | Stale | #180 (9A) and #188 (9B) are both closed as completed; #192 (9C) is the open slice. |
| P4 | `AGENTS.md:305-307` — labelled "Full suite (collection + Python CLI tests together)" | Misleading | Omits collection integration, scenario, playbook syntax, and galaxy build (`.github/workflows/ansible-collection-foundation.yml:55,60,61-84`). It is not even the root CI lane: `setup.cfg` sets no `addopts`, so the command also collects `tests/release/` and `tests/e2e/`, which CI runs separately or not at all. |
| P5 | `AGENTS.md:118` — "Edits are technically blocked by a `.claude/settings.json` PreToolUse hook" | Overstated | The hook matcher is `Edit\|Write`. Bash write paths (`sed -i`, redirects, `git checkout`, `patch`) are not intercepted. The hook is defense-in-depth, not enforcement. |
| P6 | `AGENTS.md:726-734,781-794` — release governance | Incomplete | `test_all_release_version_surfaces_match_repo_release_version` (`ansible_collections/tomazb/acm_switchover/tests/unit/test_collection_metadata.py:38-53`) enforces eight version surfaces. `AGENTS.md` lists five and never mentions `galaxy.yml`. |
| P7 | `AGENTS.md:34-49`, `:371-390` | Duplicated | The dual-supported capability list duplicates `docs/ansible-collection/parity-matrix.md`; the Phase 9 block duplicates status owned by GitHub issues and the Phase 9A design. Both can drift from their authorities and P3 shows one already has. |
| P8 | Missing | Gap | No repository-wide mandatory start gate, no authority hierarchy for conflict resolution, no finding-disposition model, no verification matrix keyed to the changed surface. |
| P9 | `AGENTS.md:796-857` | Tool-coupled | Claude SKILLS tables, Claude Code hook mechanics, and graphify CLI invocation live in the shared cross-agent policy. |

Two authorities landed immediately before this slice and must be consumed rather than
restated: `AGENTS.md:558-646` (terminal validation, owned by #242) and
`ansible_collections/tomazb/acm_switchover/docs/compatibility.md` (the compatibility and
collection-version-lifecycle authority, owned by #244). PR #249 deliberately left
`AGENTS.md` untouched and recorded that #245 owns it.

## Goal

Turn `AGENTS.md` into a concise, policy-first, durable authority that:

- states repository-wide safety and process invariants;
- delegates every changing architecture, status, and compatibility detail to a named
  authority document;
- gives agents a deterministic start, implementation, validation, review-resolution, and
  merge-readiness process;
- preserves every existing safety boundary without preserving stale implementation
  snapshots.

Target: policy only — no inventories, every changing fact behind a link. The result is
574 lines; the floor is set by the mandated fifteen sections plus the 90-line #242 section
transplanted verbatim.

## Decisions

### D1 — Structured rewrite into fifteen policy sections, not incremental repair

The defects are structural: P7, P8, and P9 cannot be fixed by editing sentences. The
document is reorganised into the section order the issue specifies:

1. Repository identity and primary branch
2. Mandatory start gate
3. Authority hierarchy and conflict handling
4. Engineering and operational safety invariants
5. Protected-file policy
6. Python/Ansible independence and parity contract
7. RBAC cross-surface contract
8. Builder → Independent Validator → PR-comment Resolver workflow
9. Terminal validation and review convergence
10. Verification matrix by changed surface
11. Review priorities and finding disposition
12. Release and version governance
13. Release-validation and lab-controller authority boundary
14. Evidence rules for generated and external review
15. Authoritative document index

### D2 — The #242 section is transplanted, not rewritten

Issue #245 states it "must not absorb or rewrite that slice". Section 9 carries the
existing `## Terminal Validation and Review Convergence` block with its heading text,
subheadings, and rules unchanged. Only its position in the document changes.

### D3 — Delegation targets are verified to exist before they are linked

Every document named in section 15 was confirmed present in the working tree. Two
tempting links are deliberately absent: `docs/development/lab-phase9-readiness-checklist.md`
does not exist (tracked by open #190), and no `molecule`, `ansible-lint`, or `ansible-test`
gate exists anywhere in the workflows or `run_tests.sh` — those appear only as aspirational
text in design documents, and section 10 must not claim them.

### D4 — Architecture wording states invariants, not inventories

Fixing P1 and P2 by correcting two sentences would leave the module, plugin, and CLI
inventories that caused the drift. Sections 4 and 6 instead state ownership invariants:

- The Python CLI is a stateful entrypoint backed by modular workflow, operation-runner,
  phase, and outcome layers.
- Phase eligibility and durable post-handler transition verification are owned by the
  workflow and runner layers. Phase handlers do not self-gate.
- DRY applies within a form factor and a stable ownership boundary. Python and Ansible
  runtime code is never cross-imported to remove duplication.
- New validation work is routed to its owner: CLI input validation, Python preflight,
  collection validation, release checks, lab-controller gates, or parity tests.

Module descriptions belong to `docs/development/architecture.md`.

### D5 — The verification matrix replaces mechanical full-suite wording

Section 10 keys the required gates to the surface a change actually touches
(documentation/process-only, Python CLI, Ansible collection, dual-supported and
parity-sensitive, RBAC, release-validation framework, live lab-controller, release and
version work), under five rules: targeted tests first; run every gate the edit invalidates;
complete the relevant gate set before terminal validation; do not rerun unrelated full
suites after a prose-only review observation when a governed bounded gate exists; exact-head
CI stays mandatory for merge readiness. The mislabelled command at `AGENTS.md:305-307` is
removed rather than corrected in place — the authoritative gate inventory is
`docs/development/ci.md` plus the two workflow files.

### D6 — Release governance names all eight enforced surfaces and adopts #244's lifecycle decision

Section 12 lists the eight surfaces that
`test_all_release_version_surfaces_match_repo_release_version` enforces, including
`ansible_collections/tomazb/acm_switchover/galaxy.yml`, and records the coupling decision
by reference: the collection version follows the repository release version and has no
independent lifecycle, per
`ansible_collections/tomazb/acm_switchover/docs/compatibility.md` "Collection version
lifecycle". The compatibility matrix itself is linked, never copied.

`.claude/skills/release/SKILL.md` bumps six of those eight surfaces and is a protected
file. Issue #245 forbids expanding into it. The mismatch is filed as a dedicated follow-up
issue instead.

### D7 — Protected-file policy keeps every restriction and corrects only the enforcement claim

All six protection rules survive. Rule 1 is restated: the hook is defense-in-depth on the
`Edit|Write` tool path; the policy binds regardless of tool or write path. Added: the
builder, the independent validator, and the PR-comment resolver each independently verify
the base-relative protected-file diff, and no cosmetic or speculative protected-file edits
are permitted. The heading `## Protected Critical Files` keeps its exact text because
`.claude/settings.json` cites it by name in the hook's block message and that file is out
of scope.

### D8 — Tool mechanics move to the tool-specific document

Claude Code hook behaviour and graphify CLI invocation move to `CLAUDE.md`. Section 14
keeps only the tool-neutral requirements: applicable process skills are read and mapped to
checkpoints; generated graph output is a hypothesis generator, never an authority;
external reviews such as Thermos are hypotheses until verified against source and tests;
validators are independent.

### D9 — Guardrail tests assert durable semantics, in the same commit

`test_agents_version_policy_separates_development_from_release_work`
(`tests/test_documentation_guardrails.py:329-354`) currently slices `AGENTS.md` between the
literal headings `## Version Management` and `\n## Claude SKILLS` and asserts fourteen
literal substrings, including `issue #165 is not a release`. That test encodes the very
drift this slice removes: it pins a historical issue-specific sentence and a section
ordering the restructure changes.

It is rewritten to assert behaviour — that ordinary development work is distinguished from
explicit release work, that development PRs do not bump versions or cut tags, that release
work synchronises metadata and tags the release commit, and that `galaxy.yml` is named —
without requiring any historical sentence. The forbidden-string check is kept. New
guardrails pin the durable additions: the start gate, the five-tier authority hierarchy,
the non-universal hook wording, the verification-matrix surfaces, and that every
authority-index link resolves to a file that exists.

Because the guardrail and the document change together, renaming `## Version Management`
to a release-governance heading is safe. `tests/test_ci_guardrails.py:19-31` separately
requires `AGENTS.md` to keep documenting the isolated agent worktree directory, and that
sentence is preserved because `setup.cfg`'s flake8 excludes are coupled to it.

## Changed files

| File | Change |
| --- | --- |
| `AGENTS.md` | Restructured to the fifteen sections; 867 → 574 lines |
| `tests/test_documentation_guardrails.py` | Version-policy test rewritten to semantics; new durable-policy guardrails added |
| `CLAUDE.md` | Receives Claude Code hook and graphify invocation mechanics |
| `.github/prompts/plan-claudeSkillsFromRunbook.prompt.md`, `.github/prompts/plan-updateAcmSwitchoverForNewRunbookV2.prompt.md` | Three lines re-routed: these active templates instructed agents to re-add the SKILLS section, the phase-flow mapping, and the version checklist to `AGENTS.md`. Cross-agent routing consistency only |
| `CHANGELOG.md` | `[Unreleased]` entry |
| `docs/superpowers/specs/2026-08-11-issue-245-agents-refresh-design.md` | This document |

## Non-goals

- Rewriting or extending the #242 terminal-validation rule.
- Copying the #244 compatibility matrix into `AGENTS.md`.
- Editing `.claude/skills/release/SKILL.md` or any protected file.
- Editing `.claude/settings.json` or changing hook behaviour.
- The contributor, testing, and architecture documentation refresh — that is #246, and it
  consumes the vocabulary this slice fixes.
- Any change to code, version identifiers, or release tags. This is a process correction,
  not a release.

## Verification

```bash
# Gates this edit invalidates
python -m pytest tests/test_documentation_guardrails.py tests/test_ci_guardrails.py -q

# Formatting, CI scope and settings
black --check --line-length 120 --diff tests/test_documentation_guardrails.py
isort --check-only --profile black --line-length 120 tests/test_documentation_guardrails.py

# Root lane, CI-equivalent
python -m pytest tests/ --ignore=tests/release -m "not e2e" -q

# Every relative link and internal anchor in AGENTS.md resolves (fail-closed; the shell
# loop below is illustrative only — these two tests are the authority)
python -m pytest tests/test_documentation_guardrails.py -k "agents_document_links or agents_internal_anchors" -q

# Anchors other documents and the settings hook depend on
grep -n '^## Terminal Validation and Review Convergence$' AGENTS.md
grep -n '^## Protected Critical Files$' AGENTS.md
```

**Falsification.** This design is not implemented if, after the change: any
authority-index link is dead; the version-policy guardrail still passes only because a
historical issue-specific sentence survived; `AGENTS.md` still states a phase status,
module inventory, or version-location list that a named authority document also owns; or an
agent following section 10 alone can reach merge readiness without running a gate their
change invalidated.

## Governance

Builder → Independent Validator → PR-comment Resolver, terminating under
[Terminal Validation and Review Convergence](../../../AGENTS.md#terminal-validation-and-review-convergence).
The governing acceptance gate is issue #245's required content changes plus the
falsification criteria above. Codex acts as the external reviewer for this slice at
operator instruction; its findings are hypotheses until verified against the repository,
per the evidence rules this document installs.
