# Plan: Documentation Strategy—Reorganize, Consolidate, and Maintain Markdown Docs

Create a durable documentation information architecture for `rh-acm-switchover` that:
- Makes it easy for **operators** to run a switchover safely
- Makes it easy for **cluster admins** to deploy RBAC via Helm/Kustomize/ACM Policy
- Makes it easy for **developers** to understand architecture, validate changes, and run tests
- Reduces duplication and “which doc is canonical?” confusion
- Preserves stable links via compatibility stubs, minimizing churn

This plan is aligned with the current repo state (Python tool + bash scripts + deploy assets) and with existing docs already referenced from `README.md` and `docs/README.md`.

---

## Current repo facts (baseline)

### Where docs live today

- Root-level Markdown:
  - `README.md` (main entrypoint; already links into `docs/`)
  - `SECURITY.md` (policy)
  - `CHANGELOG.md`
  - `CONTRIBUTING.md`
  - `LICENSE`

- `docs/` contains many “product docs” (operator guides, architecture, PRD, etc):
  - `docs/README.md` (documentation index)
  - `docs/ACM_SWITCHOVER_RUNBOOK.md`
  - `docs/getting-started/install.md`, `docs/getting-started/container.md`
  - `docs/operations/quickref.md`, `docs/operations/usage.md`
  - `docs/deployment/rbac-requirements.md`, `docs/deployment/rbac-deployment.md`
  - `docs/development/architecture.md`, `docs/development/ci.md`, `docs/development/testing.md`, `docs/development/rbac-implementation.md`
  - `docs/project/prd.md`, `docs/project/deliverables.md`, `docs/project/summary.md`
  - `docs/reference/validation-rules.md`

- “Docs next to code” already exist and should remain close to their implementation:
  - `scripts/README.md` (bash operational scripts)
  - `tests/README.md`, `tests/README-scripts-tests.md` (test suite docs)
  - `deploy/**/README.md` (deployment method docs):
    - `deploy/kustomize/README.md`
    - `deploy/helm/acm-switchover-rbac/README.md`
    - `deploy/acm-policies/README.md`

### Common current documentation problems (expected)

- Overlap/duplication across:
  - `README.md` vs `docs/README.md`
  - `docs/operations/usage.md` vs `docs/ACM_SWITCHOVER_RUNBOOK.md`
  - Quick reference content spread across multiple places instead of one canonical doc
  - RBAC docs split between requirements/deployment/implementation summary plus deploy-method READMEs
- “Implementation-report” documents at repo root are easy to confuse with user-facing docs.

---

## Goals and non-goals

### Goals

1. **Single entrypoint**: Root `README.md` remains the “start here”.
2. **Clear taxonomy**: Group docs by audience and usage (operators, deployers, developers, project/design).
3. **Minimal duplication**: Keep “one canonical location” per topic; link instead of re-explaining.
4. **Stable permalinks**: Preserve existing links using stubs/redirect-style files during a transition period.
5. **Docs close to code**: Keep `deploy/**/README.md`, `scripts/README.md`, and `tests/*.md` where they are; reference them from the canonical index.
6. **Sane naming**: Use consistent, URL-friendly filenames.
7. **Easy maintenance**: Make it obvious where to update docs when behavior changes.

### Non-goals (unless explicitly requested)

- Do not rewrite the entire runbook for content style; focus on organization and deduplication.
- Do not introduce a docs site generator (MkDocs/Docusaurus) unless requested.
- Do not change CLI behavior; doc changes only.

---

## Proposed documentation taxonomy (end state)

### Root-level “contract” docs

Keep only repo-wide entrypoint/policy docs at repo root:
- `README.md` (entrypoint; links to the canonical index)
- `CHANGELOG.md`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `LICENSE`

Everything else belongs in `docs/` (or stays “next to code” where appropriate).

### `docs/` structure (group by audience)

Use subdirectories to make scanning easy:

- `docs/getting-started/`
  - Installation, prerequisites, local vs container quickstart

- `docs/operations/`
  - Operator-facing: runbook, usage scenarios, troubleshooting, quick reference

- `docs/deployment/`
  - RBAC model, deployment choices, and a single decision doc that links to:
    - `deploy/kustomize/README.md`
    - `deploy/helm/acm-switchover-rbac/README.md`
    - `deploy/acm-policies/README.md`

- `docs/reference/`
  - “Facts” rather than “procedures”: validation rules, CLI reference, state file reference

- `docs/development/`
  - Architecture, testing, CI, design notes; deeper internal docs not needed by operators
  - `docs/development/notes/` for implementation reports and historical rationales

- `docs/project/`
  - PRD, deliverables, project summary (planning/spec content)

### Indexing strategy

- `docs/README.md` becomes a **short, maintained table of contents** by persona.
- Each subdirectory optionally has a `README.md` (short index) if it contains multiple docs.
- Root `README.md` links to `docs/README.md` and highlights only the top 5–10 most important docs.

---

## Naming and style conventions

### File naming

- Prefer lowercase + hyphens: `docs/operations/quickref.md`, not uppercase filenames.
- Prefer stable, descriptive names (avoid version/date in filenames unless it’s an archival snapshot).
- If a doc must remain uppercase due to external references, add a stub file at the old uppercase path.

### Content organization rules

- Avoid repeating “install” instructions in multiple places; centralize and link.
- Avoid repeating RBAC manifests/values; link to `deploy/**` READMEs and keep details close to code.
- Keep command examples runnable; prefer `python3 acm_switchover.py ...` when Python is required.
- Use consistent terminology:
  - “primary hub”, “secondary hub”, “old hub”
  - “validate-only”, “dry-run”, “decommission”

### Linking rules

- Prefer relative links inside the repo (`../`, `./`), not absolute GitHub URLs.
- When moving files, update links in:
  - `README.md`
  - `docs/README.md`
  - Any doc that links to moved docs
  - `deploy/**/README.md`, `scripts/README.md`, `tests/*.md` where relevant

---

## Concrete reorg proposal (moves/merges)

### 1) Keep implementation notes under `docs/development/notes/`

Implementation notes live under:
- `docs/development/notes/validation-and-error-handling.md`
- `docs/development/notes/exception-handling.md`
- `docs/development/notes/shell-safety.md`

Optionally merge them later into one “Security & reliability notes” doc, but **do not merge** in the first pass unless it reduces duplication materially without losing important detail.

### 2) Promote changelog and contributing to root

- `docs/CHANGELOG.md` → `CHANGELOG.md`
- `docs/CONTRIBUTING.md` → `CONTRIBUTING.md`

Keep `docs/` copies as compatibility stubs (see “Compatibility stubs”).

### 3) Merge quick references

Create one canonical quick reference:
- Use `docs/operations/quickref.md` for concise commands (include both “Local install” and “Container” sections)
- Link to `docs/operations/usage.md` for depth

### 4) Keep runbook and usage separate, but reduce overlap

Clarify responsibilities:
- `docs/operations/usage.md` is scenario-driven commands
- `docs/ACM_SWITCHOVER_RUNBOOK.md` remains the procedural runbook + decision points

Update both docs to link to each other rather than repeating the same steps.

### 5) Restructure installation and container docs

- `docs/getting-started/install.md` is the canonical installation guide
- `docs/getting-started/container.md` is the canonical container guide

Ensure `README.md` and `docs/README.md` point to these new canonical paths.

### 6) RBAC and deployment docs

Create a high-level “which method should I use?” doc and keep deep details in their respective locations:

- `docs/RBAC_REQUIREMENTS.md` → `docs/deployment/rbac-requirements.md`
- `docs/RBAC_DEPLOYMENT.md` → `docs/deployment/rbac-deployment.md`
- Keep deploy-method READMEs in place:
  - `deploy/kustomize/README.md`
  - `deploy/helm/acm-switchover-rbac/README.md`
  - `deploy/acm-policies/README.md`
- Consider moving `docs/RBAC_IMPLEMENTATION_SUMMARY.md` to development:
  - `docs/RBAC_IMPLEMENTATION_SUMMARY.md` → `docs/development/rbac-implementation.md`

### 7) Reference docs

- `docs/VALIDATION_RULES.md` → `docs/reference/validation-rules.md`
- Add new (short, focused) reference docs if needed:
  - `docs/reference/cli.md` (canonical list of CLI flags and semantics; keep synced with `--help`)
  - `docs/reference/state-file.md` (state file location precedence, schema expectations, troubleshooting)

### 8) Developer docs

- `docs/development/architecture.md`
- `docs/development/testing.md`
- `docs/development/ci.md`

Link out to “next to code” docs:
- `scripts/README.md`
- `tests/README.md`
- `tests/README-scripts-tests.md`

### 9) Project/spec docs (optional but recommended)

Move planning/spec content out of the main operator flow:
- `docs/project/prd.md`
- `docs/project/deliverables.md`
- `docs/project/summary.md`

---

## Compatibility stubs (optional)

If stable links are required in the future, add small stubs at old locations for 1–2 releases.

- Old file path remains and contains:
  - A short notice: “This document moved to `new/path.md`.”
  - A single link to the new location
  - Optionally an “effective date” if helpful

Example stub:

```md
# Moved

This document moved to `docs/operations/quickref.md`.
```

Use this sparingly; prefer updating links directly within the repo.

---

## Execution checklist (what to do when applying this plan)

1. Create the new directory structure under `docs/`.
2. Move/rename docs (prefer `git mv` to preserve history).
3. Add compatibility stubs at old paths.
4. Update links:
   - `README.md` doc links
   - `docs/README.md` index (make it shorter and accurate)
   - Cross-links between docs
5. Ensure “next to code” docs are linked from the canonical indexes:
   - `deploy/**/README.md`, `scripts/README.md`, `tests/*.md`
6. Keep the documentation index (`docs/README.md`) accurate and concise.
7. Sanity-check that there are no broken internal links:
   - At minimum, search for old paths and update references.

---

## Acceptance criteria (definition of done)

- Root `README.md` points to the correct canonical docs and does not link to stale paths.
- `docs/README.md` is short, accurate, and organized by persona.
- `docs/operations/quickref.md` exists and replaces the previous two quickref docs.
- All moved docs have compatibility stubs (until explicitly removed in a later cleanup).
- No obviously broken relative links remain after the move (spot-check with repo-wide search).
- Deployment docs remain “next to code” and are referenced from `docs/deployment/`.

---

## Suggested follow-ups (optional)

- Add a lightweight “docs link check” script (grep-based) or CI job to prevent broken links on future changes.
- Introduce a small “docs update policy” section in `CONTRIBUTING.md` (e.g., “when you change CLI flags, update `docs/reference/cli.md`”).
