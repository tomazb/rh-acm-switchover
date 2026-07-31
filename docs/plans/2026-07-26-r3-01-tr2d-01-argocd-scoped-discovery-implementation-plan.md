# R3-01 / TR2D-01 Argo CD Scoped-Discovery Implementation Plan

> **For agentic workers:** Follow this plan with test-driven development,
> scoped review checkpoints, and exact-head verification.

**Status:** Approved

**Revision:** `R3-01-TR2D-01-PLAN-A2`

**Design:** `R3-01-TR2D-01-DESIGN-A1`

**Approved base:** `17c9589d41767ce582fe46444f5e1feb07af0d30`

**Issue:** [#199](https://github.com/tomazb/rh-acm-switchover/issues/199)

**Goal:** Correct collection scoped Argo CD discovery so complete positive
success is required before publication or mutation, while proving checkpoint
retry and standalone resume behavior through non-mock executable tests.

**Architecture:** Keep the existing role and mock path. Add one scoped-only
validation task boundary, give live query and publication facts distinct
ownership, and expose a stable standalone resume result derived once from
per-hub changed-patch buckets.

**Technology:** Ansible/YAML/Jinja, `kubernetes.core`, pytest, and a stateful
test-only Kubernetes HTTP API.

## Global Constraints

- Findings are exactly `R3-A1`, `TR2D-M1`, and `TR2D-L1`.
- Use the explicit present and strict absent predicates from the approved
  design; `msg` is non-authoritative.
- Do not add an unnamed or heuristic failure-marker predicate.
- Count changes with `defined` followed by `sameas true`.
- No production test hook or `acm_switchover_argocd_mock_apps` may satisfy the
  live retry/resume acceptance tests.
- Preserve cluster-wide, mock, dry-run, check-mode, checkpoint-identity, and
  sanitized error behavior.
- Do not touch Python, finalization, RBAC, `setup.cfg`, protected files, OCC
  behavior, or Phase 9 authority.

## Execution Tasks

1. Add stateful fake-API and result-shape test support.
2. Write and run failing scoped-success, clobber, malformed-result, mixed-result,
   sanitization, and no-partial-mutation tests.
3. Add scoped-only validation and distinct guarded publication.
4. Run the scoped tests green and preserve cluster-wide/mock behavior.
5. Write failing exact-Boolean pause/resume summary tests.
6. Implement one exact `defined | sameas true` delta for global and per-hub
   summaries.
7. Write failing standalone two-hub checkpoint identity/resume test.
8. Initialize standalone counters once, run both roles, and publish
   `acm_switchover_argocd_resume_result` from per-hub buckets.
9. Write failing primary-prep checkpoint retry/re-pause test, then verify it
   passes through the scoped correction without changing primary-prep
   production wiring.
10. Update the tracker, changelog, scenario catalog, coexistence guidance, and
    operator documentation.
11. Run targeted, collection, release, combined, syntax, full test, formatter,
    lint, type, and security gates.
12. Review the exact diff, commit intentionally, push, and open a draft PR
    targeting `ansible`.

## Red Evidence

Capture commands, exit status, and the expected failure reason before production
changes. Red evidence must include:

- scoped multi-namespace discovery publishes zero under the current clobber;
- malformed and mixed shapes lack the approved validation boundary;
- current truthiness-based summary selection accepts non-Boolean values;
- standalone resume lacks the approved result fact and is defeated by scoped
  clobber;
- primary-prep retry fails to re-pause reconciled Applications.

Do not publish raw seeded sensitive values in red evidence.

## Verification

Run targeted Argo CD unit/integration tests first, followed by:

```bash
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q
python -m pytest tests/release -q
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ tests/ -q
./run_tests.sh
```

Run syntax checks for `argocd_resume.yml` and `switchover.yml`, then the exact
Black, isort, Flake8, mypy, and Bandit commands from `AGENTS.md`.

Before push, verify the exact head, intended changed-file boundary, protected
files, scope exclusions, and clean worktree. The PR remains draft. The builder
must not merge, enable auto-merge, mark ready, resolve review threads, or claim
independent validation.
