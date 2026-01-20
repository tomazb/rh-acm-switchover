---
name: engineering-principles-compliance-review
overview: Assess current ACM Switchover codebase against documented engineering principles (DRY, KISS, YAGNI, explicitness, error handling, and localized changes) and outline focused, minimal follow-up improvements with evidence-backed findings.
todos:
  - id: verify-scope
    content: Verify key modules/tests exist and record a baseline inventory of reviewed files (read-only).
    status: pending
  - id: create-branch
    content: Create and switch to branch engineering-principles-compliance-review before any code changes.
    status: pending
  - id: survey-modules
    content: Survey workflow modules and shared libs for DRY/KISS/YAGNI and constants usage patterns (read-only).
    status: pending
  - id: classify-findings
    content: Classify findings by principle, impact, and module using a consistent evidence template.
    status: pending
  - id: draft-refactor-proposals
    content: Draft concrete, minimal refactor proposals that improve compliance without changing behavior.
    status: pending
  - id: compile-compliance-report
    content: Compile a concise engineering-principles compliance report, remediation roadmap, and mermaid diagrams.
    status: pending
---

## Engineering Principles Compliance Review Plan

### Scope

- **Codebase focus (initial)**: Core CLI (`acm_switchover.py`), shared libraries (`lib/utils.py`, `lib/kube_client.py`, `lib/constants.py`, `lib/exceptions.py`, `lib/validation.py`), key workflow modules (`modules/preflight_coordinator.py`, `modules/preflight_validators.py`, `modules/preflight/`, `modules/primary_prep.py`, `modules/activation.py`, `modules/post_activation.py`, `modules/finalization.py`, `modules/decommission.py`), and representative tests (`tests/test_kube_client.py`, `tests/test_validation.py`, `tests/test_main.py`).
- **Principles evaluated**: DRY, KISS, YAGNI, fail-fast with clear errors, explicitness, keep changes minimal/localized, respect existing patterns.
- **Branch**: All implementation work will be performed on `engineering-principles-compliance-review` (created during Step 0).

### Evidence Format (for consistent reporting)

Each finding should be recorded as:

- **File**: path
- **Function/Method**: name
- **Principle**: one or more
- **Impact**: high/medium/low
- **Finding**: concise description
- **Proposed change**: minimal, localized refactor
- **Benefit**: explicit user-facing or maintenance value

### Review Checklist & Criteria

#### DRY & Constants

- [ ] **Namespaces/resource names/timeouts**: Are they imported from `lib/constants.py` instead of hardcoded literals?
- [ ] **Repeated logic**: Is there duplication across modules where a shared helper would be appropriate (without over-abstracting)?

#### KISS & Explicitness

- [ ] **Function length**: Any functions > ~40-50 lines mixing orchestration, logging, and low-level detail?
- [ ] **Control flow clarity**: Are control flow and side effects obvious at call sites?

#### YAGNI

- [ ] **Deprecated/legacy surfaces**: Are deprecated modules (e.g., shims) still needed?
- [ ] **Unused abstractions**: Are there abstractions that provide no clear benefit?

#### Fail-Fast & Clear Errors

- [ ] **Exception types**: Domain failures use `SwitchoverError` (or subclasses) vs raw exceptions?
- [ ] **Log messages**: Actionable logs around validation/KubeClient operations?

#### State & Idempotency

- [ ] **Step completion checks**: Proper `is_step_completed` and `mark_step_completed` usage?

#### Dry-Run Semantics

- [ ] **Mutating operations**: Use `@dry_run_skip` or explicit dry-run branches with safe return values and "[DRY-RUN]" logs?

### Planned Review & Improvement Steps

**Step 0 - Confirm scope + create branch**

- Verify file existence for all expected modules/tests; adjust scope if any are missing.
- Record a baseline inventory of reviewed files.
- Create and switch to branch `engineering-principles-compliance-review` before any code changes.

**Step 1 - Deep-dive principle mapping (read-only)**

- Review each module in scope, using the checklist to collect evidence-backed findings.
- Note any repeated logic or hard-coded constants.
- Validate error handling and idempotency patterns.

**Step 2 - Classify findings**

- Tag findings using the evidence format.
- Group by module to keep remediation small and localized.

**Step 3 - Propose concrete refactors (no code changes yet)**

- For high/medium findings, outline minimal refactor proposals.
- Each proposal links to principle(s) and expected benefit.

**Step 4 - Deliver compliance report & roadmap**

- Write `docs/development/engineering-principles-compliance-report.md` with:
  - Principle ratings (strong/mixed/weak) + evidence
  - Strengths to preserve
  - Prioritized remediation list, scoped per module
  - Mermaid diagrams:
    - Phase flow with state transitions
    - Error handling touchpoints (where exceptions are raised/caught)

### Definition of Done

1. **File verification**: All mentioned modules/tests confirmed, or scope updated.
2. **Principle coverage**: Each principle has a rating and evidence.
3. **Report deliverable**: Markdown report with validated findings only, plus mermaid diagrams.
4. **Branch**: All work done on `engineering-principles-compliance-review`.

### Future Implementation (after plan approval)

- Implement refactors module-by-module, run `./run_tests.sh`, update `CHANGELOG.md` for behavior-impacting changes, and keep Python/Bash versions in sync as required.
