# Design: GitHub Issues Work Tracking for Ansible/Python Parity

**Date:** 2026-05-05  
**Scope:** Organise the 8-PR Ansible/Python parity backlog (`work-to-do.md`) into GitHub Issues for cross-session progress tracking.

---

## Problem

`work-to-do.md` (1,832 lines) lives in the repo root and violates the AGENTS.md convention against tracking markdown in the repository. More importantly, it has no mechanism for an agent to answer "where did we leave off?" across sessions — the agent has to re-read the entire file each time.

## Solution

**GitHub Issues** — one issue per PR, grouped under a milestone. The detailed spec (`work-to-do.md`) is moved to `docs/superpowers/plans/` as a read-only reference. Each session starts with `gh issue list`.

---

## Issue structure (per issue)

```
Title:   [PR-N] <Theme>
Labels:  parity, ansible, <priority>
Body:
  One-paragraph problem summary (from work-to-do.md § N.1)
  
  > Depends on: #<issue numbers> (where applicable)
  
  ### Acceptance criteria
  - [ ] criterion 1
  - [ ] criterion 2   ← already-done items pre-ticked
  ...
  
  ### Reference
  Full spec: docs/superpowers/plans/2026-05-05-ansible-parity-spec.md § PR-N
```

---

## The 8 issues

| # | PR | Theme | Priority | Merge order | Depends on | Current status |
|---|---|---|---|---|---|---|
| TBD | PR-1 | Checkpoint/state safety | P0 | 1 | — | Pending |
| TBD | PR-2 | Activation live-read + passive readiness | P0 | 2 | PR-1 | Partial (restore_phase/restore_ready done; live-read + assertion pending) |
| TBD | PR-3 | Phase self-sufficiency / fact freshness | P1 | 3 | PR-1 | Partial (post_activation done; primary_prep gap remains) |
| TBD | PR-4 | ArgoCD resume-on-failure checkpoint semantics | P1 | 4 | PR-1 | Partial (run-ID aware; reset_from semantics pending) |
| TBD | PR-5 | Decommission/report path safety | P2 | 5 | PR-3 | Pending |
| TBD | PR-6 | Python/Ansible validation parity | P2 | 6 | — | Pending |
| TBD | PR-7 | Klusterlet scalability | P3 | 7 | PR-3 | Pending |
| TBD | PR-8 | Docs, migration map, runbook updates | P2 | 8 | all | Pending (runbook gate: requires explicit operator approval) |

---

## Dependency graph

```
PR-1 (Checkpoint) ──► PR-2 (Activation)
                  ──► PR-3 (Phase self-sufficiency) ──► PR-5 (Decommission)
                  ──► PR-4 (ArgoCD resume)           ──► PR-7 (Klusterlet)

PR-6 (Validation parity) — independent, no hard dep

PR-8 (Docs) — last, depends on all
```

---

## Labels to create

| Label | Description | Color |
|---|---|---|
| `parity` | Ansible/Python parity work | `#0075ca` |
| `ansible` | Ansible collection changes | `#e4e669` |
| `P0` | Critical priority | `#d73a4a` |
| `P1` | High priority | `#e99695` |
| `P2` | Medium priority | `#f9d0c4` |
| `P3` | Low priority | `#fef2c0` |

---

## Milestone

**Name:** `ansible-python-parity`  
**Description:** Align the Ansible Collection with the Python CLI safety model (checkpointing, activation, resume, artifact handling, validation).  
**Due date:** (none — solo, time-boxed by PR merge order)

---

## Spec file placement

| From | To | Action |
|---|---|---|
| `work-to-do.md` | `docs/superpowers/plans/2026-05-05-ansible-parity-spec.md` | Move (git mv) |

The spec file becomes read-only reference material. Issues link to it by section anchor.

---

## Agent workflow each session

```bash
# Orientation (run at start of every session)
gh issue list --milestone ansible-python-parity --state open

# Drill into the lowest-numbered open issue (highest priority, lowest merge order)
gh issue view <N>

# After finishing acceptance criteria, close via PR body:
#   Closes #N
# GitHub auto-closes on merge.
```

---

## Implementation steps

1. Create labels (`parity`, `ansible`, `P0`–`P3`)
2. Create milestone `ansible-python-parity`
3. `git mv work-to-do.md docs/superpowers/plans/2026-05-05-ansible-parity-spec.md`
4. Create 8 GitHub Issues in merge order (PR-1 first), with pre-ticked checkboxes for already-done items
5. Add `Depends on: #N` links between issues
6. Commit the spec file move and push
