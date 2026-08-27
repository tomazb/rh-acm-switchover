# ACM Switchover — AI Agent Instructions

This file is the durable, repository-wide process and safety policy for every AI agent
working here. Policy belongs in this file; current state does not. Module inventories,
capability status, phase progress, supported versions, and exact gate commands are
delegated to the [Authoritative Document Index](#authoritative-document-index). When this
file and a named authority disagree, follow
[Authority Hierarchy and Conflict Handling](#authority-hierarchy-and-conflict-handling).

## Repository Identity and Primary Branch

This repository delivers ACM hub switchover automation in two production form factors:

1. **Python CLI** (`acm_switchover.py`) — a stateful entrypoint over modular workflow,
   operation-runner, phase, and outcome layers, with persisted state, retry logic, and a
   rich CLI surface.
2. **Ansible Collection** (`tomazb.acm_switchover`, at
   `ansible_collections/tomazb/acm_switchover/`) — a complete second form factor targeting
   the `ansible-core` CLI and Ansible Automation Platform, built from roles, playbooks, and
   thin custom plugins.

Both automate the same phased runbook workflow with idempotent execution and comprehensive
validation. They are independent codebases and cannot import from each other.

**`ansible` is the primary development branch.** Until further notice:

- Base every new branch and worktree on the latest `origin/ansible`.
- Open every PR with `--base ansible`; do not target `main`.
- `main` receives no direct feature merges while this policy is in effect.

## Mandatory Start Gate

Complete this gate before substantive analysis, implementation, validation, or PR-comment
resolution. It is not optional and it is not satisfied by a previous session.

1. Fetch current `origin/ansible`.
2. Confirm repository identity and the target branch.
3. Read the current `AGENTS.md` from `origin/ansible`, not a cached or locally stale copy.
4. Read the governing issue or specification, and the source, tests, and authority
   documents directly relevant to the declared scope.
5. When the work will mutate the repository, work in an isolated, clean branch or worktree.
   Use one isolated `.claude/worktrees/<slice>-name` worktree and one branch per PR.
6. Record, before the first edit: base SHA, head SHA, merge base, declared scope,
   working-tree cleanliness, and the protected-file boundary for the slice.

**Hard-fail — stop and return to the operator, do not proceed on assumption — when:**

- authorization for the work is missing;
- the scope is ambiguous;
- the base is stale relative to `origin/ansible`;
- an independent-validation checkout is dirty;
- mandatory evidence is unavailable.

## Authority Hierarchy and Conflict Handling

When sources disagree, precedence is:

1. **`AGENTS.md`** — durable repository-wide process and safety policy.
2. **The governing issue, design, or specification** — authorization, scope, and the
   acceptance gate for the current slice.
3. **Domain authority documents** — parity, compatibility, architecture, release
   validation, lab controller, RBAC, usage, and operational contracts.
4. **Current source, tests, and exact-head CI** — implementation and verification truth.
5. **Historical PR comments, external reviews, and generated analysis** — context or
   hypotheses only.

A lower tier never silently overrides a higher one, and a higher tier never invents current
state that tier 4 contradicts.

**On conflict: stop and surface it.** Do not silently pick a side, do not resolve it inside
a commit message, and do not copy another authority's status tables or decision vocabulary
into this file to make the conflict disappear.

## Engineering and Operational Safety Invariants

### Engineering principles

Apply YAGNI, KISS, and bounded DRY throughout design, planning, implementation, review, and
simplification.

These principles are subordinate to the repository's correctness, safety, security,
concurrency, recovery, idempotency, audit, evidence, parity, compatibility, and
operator-facing contracts. They are not authorization to weaken a required guarantee or
expand the approved scope.

#### YAGNI — You Aren't Gonna Need It

Implement only behavior authorized by the governing issue, design, or specification, an
approved implementation plan where applicable, or explicit operator direction.
Compatibility and parity contracts constrain how authorized work is implemented; they do
not independently grant implementation scope. A review or acceptance finding does not
expand implementation authority unless the work is already in scope or the operator
explicitly approves the expansion.

- Do not add speculative extensibility, generic frameworks, configuration options,
  abstraction layers, fallback modes, or future-facing APIs without a current requirement.
- Do not solve hypothetical variants while implementing a concrete supported case.
- Do not implement features or introduce abstractions solely for anticipated future
  requirements.
- Remove newly introduced unused code, options, and abstractions rather than carrying them
  forward "just in case."

#### KISS — Keep It Simple

Choose the simplest implementation that completely satisfies the approved requirements and
preserves all required guarantees.

- Prefer explicit data flow, control flow, state transitions, and failure handling over
  clever or highly generic mechanisms.
- Prefer existing repository patterns and platform primitives over introducing another
  subsystem.
- Minimize the number of concepts, states, configuration fields, persistence formats, and
  special cases needed to express the solution.
- Do not confuse fewer lines with greater simplicity. Readability and verifiability take
  priority over terseness.
- When the governing support, parity, and compatibility contracts permit a conservative
  supported subset that satisfies the requirement safely, prefer it over implementing a
  substantially more complex general case.

#### DRY — Don't Repeat Yourself, within stable ownership boundaries

Avoid duplicating the same authoritative rule, invariant, algorithm, or knowledge in
multiple places when one clear shared representation can own it.

- Apply DRY within a form factor and a stable ownership boundary. Never cross-import Python
  CLI and Ansible Collection runtime code merely to remove duplication — deliberate
  mirrored implementation plus parity tests is the repository contract.
- Prefer one authoritative schema, compatibility rule, calculation, validation, or
  state-transition implementation with callers consuming that result within its ownership
  boundary.
- Do not create an abstraction merely because two code fragments currently look similar.
- Keep duplication when the behaviors have different reasons to change or when sharing them
  would create coupling, hidden conditionals, or a harder-to-read interface.
- Documentation and examples should refer to authoritative contracts rather than becoming
  independent copies of executable rules.

#### When the principles conflict

Use this precedence:

1. Preserve correctness, safety, security, concurrency, recovery, idempotency, audit,
   evidence, parity, compatibility, and approved observable behavior.
2. Apply YAGNI: do not build requirements that do not exist.
3. Apply KISS: implement the required behavior with the smallest clear model.
4. Apply DRY where a shared abstraction removes duplicated knowledge without increasing
   coupling or complexity.

In particular, do not violate YAGNI or KISS merely to achieve DRY.

Standing engineering rules remain:

- **Fail fast with clear errors**: detect problems early; surface explicit, actionable
  messages.
- **Prefer explicit over implicit**: make control flow, side effects, and configuration
  obvious at call sites.
- **Keep changes minimal and localized**; respect existing patterns and abstractions.
- **Keep this file current** when repository-wide process or safety policy changes — not
  when implementation detail changes, which belongs to the authority documents.
- **Keep operator-facing documentation current**: when workflow, phase ordering, CLI
  branching, script checks, or operator-facing behavior changes, update the affected
  READMEs and Mermaid diagrams. They are part of the documentation contract.

### Architecture ownership invariants

- Orchestration is layered. The CLI entrypoint owns argument parsing, cross-mode wiring,
  and the phase adapters that glue the flow to the implementation; the operation-runner
  layer declares the phase flow; the workflow layer executes it; the outcome layer owns
  exit paths and reports; `modules/` owns most resource-specific phase behavior.
- **Phase eligibility and durable transition verification are owned by the workflow and
  runner layers, not by phase handlers.** Handlers do not self-gate on the current phase. A
  handler that returns success but leaves an impossible resume state is a failure, and the
  workflow layer must detect it.
- State persistence is durable and idempotent by contract: steps are guarded by completion
  markers, and critical transitions flush immediately.
- Route new validation work to its owner: CLI input validation, Python preflight,
  collection validation, release checks, lab-controller gates, or parity tests. Do not add
  a second validation surface because the correct one is inconvenient.

Module descriptions, phase tables, and code patterns live in
[`docs/development/architecture.md`](docs/development/architecture.md) and
[`docs/operations/usage.md`](docs/operations/usage.md).

### Execution-time discovery

Pre-seeded or fixture-supplied discovery is permitted **only** for explicit non-live tests,
or for a reviewed caller-owned contract that documents the reuse.

In execute mode, predicates that establish identity, authorization, the mutation target, or
the state of a mutable resource require **fresh discovery**. A cached or caller-supplied
value may satisfy them only where a reviewed freshness or cache contract explicitly permits
reuse. Stale discovery must never be able to satisfy live mutation validation.

### Test quality

Tests must verify real logic, error handling, and edge cases, not implementation detail or
trivial behavior. On safety-critical paths, missing negative coverage is a defect:
wrong-context behavior, check-mode behavior, idempotence, RBAC denial, checkpoint/resume
failure, stale Argo CD status, timeout failure, and destructive-operation confirmation.

## Protected Critical Files

The following are **safety-critical operational documents** that AI agents MUST NOT modify
without explicit operator approval:

| Protected File | Reason |
| --- | --- |
| [`docs/ACM_SWITCHOVER_RUNBOOK.md`](docs/ACM_SWITCHOVER_RUNBOOK.md) | Authoritative blueprint for manual ACM hub switchovers. Contains critical safety warnings, step-by-step procedures, and rollback instructions. Incorrect changes can lead to cluster destruction. |
| `.claude/skills/**` — both the `*.skill.md` guides and the `SKILL.md` skill definitions | Operational and troubleshooting SKILLS derived from the runbook, and the automation skills that act on release and refactoring surfaces. Must stay in sync with the runbook at all times. The `.claude/settings.json` hook matches only `*.skill.md`, so `SKILL.md` files are covered by this policy and not by the hook — see rule 1. |

### Protection rules

1. **Read-only by default.** A `PreToolUse` hook in `.claude/settings.json` blocks these
   paths on the `Edit`/`Write` tool path. That hook is **defense-in-depth, not universal
   enforcement**: it does not cover shell write paths or other tools. The policy binds
   regardless of tool or write path — an unblocked edit is still a policy violation.
2. **Explicit operator approval required.** Modify only when the operator explicitly
   requests the change and understands the implications.
3. **Careful line-by-line review.** Present every proposed change as a diff for operator
   review before committing. Do not batch protected-file changes with unrelated edits.
4. **Justification required.** State why the change is necessary and its operational
   impact.
5. **Runbook ↔ SKILLS sync obligation.** Changes to the runbook require corresponding
   SKILLS updates, and vice versa. Never update one without the other.
6. **No speculative or cosmetic edits.** Do not reformat, reorganize, or "improve" these
   files unless the operator specifically asks.
7. **Independent verification.** The builder, the independent validator, and the PR-comment
   resolver each verify the base-relative protected-file diff themselves. A clean report
   from an earlier role does not discharge the check.

## Python and Ansible Independence and Parity Contract

The two form factors are independent codebases, but many operator-facing capabilities
remain **dual-supported** during coexistence. **Drift is not allowed by default.**

- **Default rule**: if a capability is documented as `dual-supported`, update both
  implementations and their tests and docs together, unless an intentional divergence is
  explicitly approved and documented first.
- **Independence is not an exception**: "cannot import from each other" means parity is
  maintained deliberately through docs, tests, and mirrored implementation work.
- **Constants ownership**: Python-only constants live in `lib/constants.py`; collection-only
  constants live in the collection's `module_utils/constants.py`. Shared cross-form-factor
  constants are updated on both sides and held in parity by the parity tests. Never
  hard-code a shared namespace or resource name that a centralized constant already owns.
- **Variable namespace**: all collection variables use the `acm_switchover_` prefix.
- **Supported versions are not restated here.** The supported `ansible-core`, AAP,
  `kubernetes.core`, Python, and execution-environment matrix is owned by
  [`ansible_collections/tomazb/acm_switchover/docs/compatibility.md`](ansible_collections/tomazb/acm_switchover/docs/compatibility.md).

Status and mapping authorities:
[parity matrix](docs/ansible-collection/parity-matrix.md) (capability status),
[behavior map](docs/ansible-collection/behavior-map.md) (Python source → collection target),
[coexistence policy](ansible_collections/tomazb/acm_switchover/docs/coexistence.md)
(shared-behavior contract).

### Approval gate for intentional parity changes

**Explicit operator approval is required before implementing an intentional parity change**
— one that would leave a `dual-supported` capability intentionally different, change a
capability's documented parity status, or knowingly defer realignment of the other
implementation. It does not apply to ordinary parity-preserving fixes where both
implementations are updated together.

A request for approval states: the affected capabilities; their current documented status;
the proposed status or divergence; why parity cannot or should not be preserved now;
operational and user impact; test and documentation impact; and what must be realigned
later if the divergence is temporary.

Approved changes are recorded in the repository, not only in a PR or commit message: update
the parity matrix, the behavior map, the coexistence policy, the collection's
[CLI migration map](ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md),
the affected [scenario](docs/ansible-collection/scenario-catalog.md) or
[test-migration](docs/ansible-collection/test-migration-catalog.md) catalogs,
[`CHANGELOG.md`](CHANGELOG.md), and any domain document in the impacted support surface.

## RBAC Cross-Surface Contract

RBAC changes are parity-sensitive even when the code edit is indirect. If RBAC behavior,
permissions, or resources change, review and realign every affected surface:

- Python RBAC validation ([`lib/rbac_validator.py`](lib/rbac_validator.py)) and collection
  RBAC validation
  ([`plugins/modules/acm_rbac_validate.py`](ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_validate.py)).
- Collection task wiring that consumes the RBAC matrix (`preflight`, `decommission`,
  `rbac_bootstrap`).
- Root RBAC manifests in [`deploy/rbac/`](deploy/rbac/), the collection-bundled copies under
  [`roles/rbac_bootstrap/files/deploy/rbac/`](ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/files/deploy/rbac/),
  and the Helm chart in
  [`deploy/helm/acm-switchover-rbac/`](deploy/helm/acm-switchover-rbac/).
- RBAC documentation: [requirements](docs/deployment/rbac-requirements.md),
  [deployment](docs/deployment/rbac-deployment.md),
  [implementation](docs/development/rbac-implementation.md), and
  [live certification](docs/deployment/rbac-live-certification.md).
- RBAC tests on both sides, plus affected parity and static-contract tests.

Indirect changes that still require this review include: adding a Kubernetes API call, verb,
resource kind, or namespace; changing Argo CD integration in a way that alters required
permissions; changing bootstrap-applied manifests or asset selection; and changing
decommission privileges or support boundaries.

An intentional RBAC divergence requires operator approval first, and documentation of
exactly which permissions, resources, or workflows now differ and why.

## Builder, Independent Validator, and Resolver Workflow

Governed work runs as three roles with separated responsibilities:

- **Builder** — implements the slice within the authorized scope, runs the gates its change
  invalidates, and records evidence.
- **Independent validator** — validates the frozen head against the governing acceptance
  criteria from a clean checkout. Independence is a requirement: the validator does not
  inherit the builder's working tree, assumptions, or unverified claims. Its only permitted
  PR mutation is publishing its terminal exact-head report as a new top-level PR comment;
  it never implements fixes, pushes commits, changes PR metadata or state, resolves threads,
  marks the PR ready, or merges it.
- **PR-comment resolver / final validator** — dispositions every comment and review thread,
  applies accepted changes, and confirms merge readiness.

Each role independently performs the mandatory start gate and the protected-file diff check.
Each treats the previous role's conclusions as claims to verify, not as facts.

## Terminal Validation and Review Convergence

This section applies only to a **governed slice**: work whose governing issue
or specification defines an explicit, falsifiable acceptance gate. Work without
such a gate follows the Pull Request Creation Gate and Pull Request Merge Gate
below unchanged.

Governed review must terminate. When every valid-but-non-blocking observation
triggers a repair, and every repair invalidates exact-head validation, review
never converges: earned PASS verdicts are voided, the diff or document grows,
and the slice cannot ship. The rules below define the termination condition.

### Terminal Validation

- Freeze the candidate head before terminal validation begins, and record that
  exact SHA.
- Run every required validator and reviewer against that same exact head.
- Validators evaluate only the acceptance criteria defined by the governing
  issue or specification. Do not silently add new merge criteria during review.
- Record valid findings that fall outside the current slice in their owning
  tracker, and disposition them as non-blocking when the governing gate says so.
  When no tracker owns the finding, file an issue before dispositioning it.
  Deferred is not lost.
- A non-blocking comment that is a preference, a nit, or already correct as
  written needs a reply and nothing else. Do not file it; a backlog of
  non-findings becomes pressure to reopen validation later.
- Before publishing its terminal report, the Independent Validator must re-fetch
  the PR and re-check that the current head SHA is still the validated SHA and
  that the governing base relationship, including the merge base, is unchanged.
  If the head or governing base relationship changed, the Validator must not
  publish the prior result as a current PASS; it must revalidate the new exact
  state first.
- After that final re-check succeeds, the Independent Validator publishes its
  terminal result as a **new top-level PR comment**. The comment must record:
  the verdict; base SHA, head SHA, and merge-base SHA; changed-file scope;
  protected-file result; applicable validation results; CI status; review-thread
  status; merge-readiness assessment; and confidence.
- Terminal PASS means every required participant has returned a merge-ready
  verdict for the frozen head. Where a workflow defines graded verdicts, both
  `PASS` and `PASS WITH NON-BLOCKING COMMENTS` are merge-ready; non-blocking
  comments are dispositioned, not re-reviewed. `BLOCKED` and `HARD FAIL` are not
  terminal.
- Once terminal PASS is reached, stop. Do not solicit additional reviewers, do
  not start another unscoped adversarial pass, and do not make cosmetic cleanup
  edits that invalidate the terminal evidence.
- PASS does not authorize merge. Merge remains an operator decision under the
  [Pull Request Merge Gate](#pull-request-merge-gate) rules below.
- For a governed slice, terminal PASS on the frozen head satisfies that gate's
  `code-review` invocation for the same head. Comment disposition, review-thread
  resolution, and required CI checks remain mandatory before merge.

### Reopening Validation

Reopen validation only when one of the following occurs:

1. The candidate head changes.
2. The target/base relationship materially changes.
3. Required CI becomes invalid or failing.
4. A previously unresolved actionable thread is discovered.
5. Genuinely new blocking evidence arrives before merge.
6. The operator explicitly reopens validation.

### Safety Boundary

This rule is not "ignore comments after PASS". It does not suppress findings
discovered before terminal validation finishes, does not permit merging while an
actionable thread remains unresolved, and does not relax the CI requirements in
the merge gate below.

If new evidence appears before merge and demonstrates a real violation of the
governing acceptance criteria, a safety boundary, a correctness contract, an
unresolved actionable thread, or required CI state, disposition it, and reopen
validation when it meets a condition listed above. This rule stops actively
generated serial review after terminal PASS. It never permits knowingly ignoring
a defect.

### Prohibited Patterns

- Serially inviting a new reviewer after each PASS.
- Treating "zero possible observations" as an acceptance criterion.
- Converting downstream or out-of-scope findings into blockers when the
  governing slice defines them as deferred.
- Making cosmetic post-PASS edits that force exact-head revalidation.
- Silently expanding a falsifiable acceptance gate during review.
- Running generic full-suite or toolchain reruns after every prose-only review
  observation when the governed process already defines a bounded
  terminal-validation gate.

### Three-Prompt Workflow Convergence

The builder, independent validator, and PR-comment-resolver/final-validator
three-prompt workflow follows the same convergence rule. After a governed
terminal PASS on the frozen head, the independent validator and the PR-comment
resolver stop and hand control back to the operator. Neither invokes another
reviewer nor runs an unscoped "one more review" pass.

[`docs/testing/property-based-testing-pr-workflow.md`](docs/testing/property-based-testing-pr-workflow.md)
specifies that workflow in detail for the property-based-testing initiative. Its
exact-head verdict discipline is the model for this section; its PBT scoping
stands, and it is not by itself a repository-wide authority.

## Verification Matrix by Changed Surface

Run the gates your change actually invalidates. The authoritative gate inventory and exact
commands live in [`docs/development/testing.md`](docs/development/testing.md); the workflow
files under `.github/workflows/` are ground truth.

| Changed surface | Gates that must run |
| --- | --- |
| Documentation / process only | Documentation and CI guardrail tests; link resolution for changed links. No unrelated full-suite rerun. |
| Python CLI | Targeted module tests, then the root test lane, plus formatting, import-order, type, and security gates. |
| Ansible Collection | Collection unit **and** integration **and** scenario tests, playbook syntax check, and collection build. The supported-version lanes are defined by the compatibility authority. |
| Dual-supported / parity-sensitive | Both form factors' relevant tests plus the parity and static-contract tests. |
| RBAC | Python and collection RBAC tests, affected parity tests, and manifest/chart consistency checks. |
| Release-validation framework | The release test lane. Live certification runs only under an explicit profile. |
| Live lab-controller work | The controller's own gates plus the authority boundary below. Non-live evidence never substitutes. |
| Release / version work | Every version-surface guardrail, the changelog gates, and the full release gate set. |

Rules:

1. **Targeted tests first**, then widen to the gate set the edit invalidates.
2. **Run every gate the actual edit invalidates** — not a habitual subset, and not a
   habitual superset.
3. **Complete the relevant gate set before terminal validation**, so the frozen head is
   validated once.
4. **Do not rerun unrelated full suites after a prose-only review observation** when the
   governed process already defines a bounded terminal-validation gate.
5. **Exact-head CI remains mandatory for merge readiness**, regardless of local results.
6. **Never mislabel or omit a surface.** A command that runs root and collection unit tests
   is not a full suite: collection integration, scenario, syntax, and build surfaces are
   separate gates and must be named as such when relevant.

Additional standing CI constraints:

- Root `tests/` jobs do not install `ansible-core`. Top-level tests may import collection
  helpers, but must not hard-require `ansible.module_utils` at import time.
- Scope formatting and lint commands to tracked source trees. Never run repo-wide
  formatting that can walk `.venv/` or other generated directories.
- Reproduce CI's formatter configuration exactly; do not rely on an editor auto-format hook,
  which only touches files edited in-session.
- Prefer stable, sanitized status text in shared helpers and test assertion messages —
  static analysis flags raw URL-like literals and unsanitized configuration values.

## Review Priorities and Finding Disposition

Prioritize correctness and operational safety over style. Treat a finding as high priority
when the diff introduces or preserves a credible risk of:

- mutating the wrong cluster, hub, namespace, Kubernetes context, or managed resource;
- running a destructive operation without explicit confirmation, safe dry-run behavior, or
  meaningful check-mode handling;
- reporting `changed=true` from an Ansible module when no mutation occurred, or missing,
  misleading, or unsafe `check_mode` behavior;
- granting broad RBAC permissions not justified by the operation;
- checkpoint/resume logic that can skip unsafe phases, resume from an invalid state, or hide
  a partially failed switchover;
- Argo CD pause/resume logic that can affect the wrong Application, generated child
  Application, or ApplicationSet-managed resource;
- logging, exposing, or writing kubeconfigs, tokens, secrets, or credentials unsafely;
- timeout, polling, or wait logic that can hang indefinitely or silently ignore failure;
- behavior divergence from the other form factor where parity is claimed;
- missing negative tests for safety-critical behavior touched by the diff.

For Ansible code specifically, check `argument_spec` correctness, idempotence, accurate
`changed_when`/`failed_when`, meaningful failure messages, bounded retries and waits, safe
defaults, FQCN usage, stable return values, and log output that is useful in Automation
Platform jobs.

Do not spend review budget on cosmetic formatting unless it affects behavior, operator
comprehension, generated documentation, or the maintainability of a safety-critical path.

### Finding disposition

Every finding is dispositioned against the **governing acceptance gate**, not blocked merely
because a reviewer labelled it a warning:

| Disposition | Meaning | Required action |
| --- | --- | --- |
| **Blocking, in scope** | Violates the governing gate, a safety boundary, or a correctness contract. | Fix before the slice ships. |
| **Valid, deferred** | Real, but owned by another slice or tracker. | File or update the owning tracker, then reply with the tracker reference. |
| **Non-blocking observation** | Preference, nit, or already correct as written. | Reply. Do not file, do not repair. |
| **Invalid / not applicable** | Not true of this repository at this head. | Reply with the concrete technical reason and the evidence. |

**A deferral is complete only when it is filed in the receiving tracker.** A PR reply alone
is not durable tracking.

### Builder Simplification Gate

Before declaring builder completion, freezing the candidate head for terminal validation, or
opening a pull request, the builder must review the changed code and the directly affected
collaborators needed to understand the change for avoidable complexity introduced or
materially worsened by the implementation.

Apply safe, behavior-preserving simplifications when they are local, within the approved
scope, and materially improve the changed implementation. Examples include duplicated
logic, unnecessarily complex control flow, unclear local interfaces, avoidable indirection,
and mixed responsibilities directly involved in the change.

- Inspection may extend to directly affected collaborators when necessary to understand the
  changed implementation, but this does not expand edit authority. Do not edit outside the
  governing scope. Record worthwhile out-of-scope simplifications for disposition under the
  governing issue, design, or plan rather than implementing them.
- Do not turn a scoped feature, correctness, safety, or review fix into a general refactoring
  effort. Pre-existing complexity alone is not authorization to restructure it.
- Do not weaken safety, security, concurrency, recovery, idempotency, audit, evidence,
  parity, compatibility, or operator-facing guarantees for the sake of fewer lines, files,
  branches, or abstractions.
- Preserve public and operator-facing interfaces, persisted state and checkpoint contracts,
  errors, and observable outcomes unless the approved change explicitly includes their
  revision.
- Prefer readability, explicit control flow, and clear responsibilities over minimizing
  line count or abstraction count.
- After any simplification, rerun targeted tests first, then every verification gate
  invalidated by the resulting changes. Do not declare builder completion, freeze the
  candidate head, or open the pull request while the required local gate set is failing.
- Record the simplification review in the builder completion report. Summarize
  simplifications applied, or state that no safe in-scope simplification was identified. If
  the work results in a pull request, also record that outcome in the PR description.

### Pull Request Creation Gate

Before creating any PR: run the `code-review` skill against the completed branch changes;
address all critical and warning findings or record a concrete technical reason; re-run
`code-review` after review-driven changes; keep local verification evidence ready for the PR
body. For a governed slice, see
[Terminal Validation and Review Convergence](#terminal-validation-and-review-convergence).

### Pull Request Merge Gate

Before merging any PR:

- Run `code-review` again on the current PR head.
- Address all critical and warning findings, or record a concrete technical reason.
- Fetch and review every top-level PR comment, review, and review thread, and validate each
  actionable comment against the codebase before changing code.
- Address each accepted comment with code, docs, or tests, or reply with a concrete
  technical reason; resolve a thread only after the change or reply is pushed.
- Re-fetch comments and threads after addressing feedback. Do not merge while an actionable
  thread remains unresolved.
- Confirm the latest Independent Validator terminal report is a top-level PR comment with a
  `PASS` or `PASS WITH NON-BLOCKING COMMENTS` verdict that still matches the current head
  SHA and governing base/merge-base relationship. A report for an older head or superseded
  base relationship is stale evidence and requires fresh exact-head validation before merge
  readiness can be claimed.
- Check CI immediately before merge. Do not merge with failing, cancelled, or pending
  required checks.

## Release and Version Governance

Version management has two distinct modes: ordinary development work and explicit
release/version-bump work.

### Ordinary Development Work

- Ordinary development PRs may modify code, tests, documentation, and tooling.
- Record changelog-worthy development changes under `CHANGELOG.md` `## [Unreleased]`.
- Do not change released version identifiers or create release tags unless the work is
  explicitly scoped as a release/version-bump PR.
- PATCH/MINOR/MAJOR guidance selects the next explicit release version from accumulated
  changes; it does not require every individual development PR to bump a version.
- A governance, process, or documentation correction is not a release. It must not change
  version identifiers or create a release tag.

### Explicit Release Work

- Python and Bash released versions must match whenever a version bump is performed.
- Synchronize **every** version surface in the same release commit. `lib/__init__.py`
  (`__version__`, `__version_date__`) is the source of truth; these surfaces must equal it:

| Surface | File |
| --- | --- |
| Bash scripts | `scripts/constants.sh` (`SCRIPT_VERSION`, `SCRIPT_VERSION_DATE`) |
| Packaging metadata | `setup.cfg` |
| README badge | `README.md` |
| Container image label | `container-bootstrap/Containerfile` |
| Helm chart | `deploy/helm/acm-switchover-rbac/Chart.yaml` (`version` and `appVersion`) |
| **Ansible collection** | `ansible_collections/tomazb/acm_switchover/galaxy.yml` |
| Release profiles | `tests/release/profiles/full-release.example.yaml`, `tests/release/profiles/argocd-release.example.yaml` |

  The collection version **follows the repository release version and has no independent
  release lifecycle** — see
  [collection version lifecycle](ansible_collections/tomazb/acm_switchover/docs/compatibility.md).
  These surfaces are enforced by the collection metadata guardrail tests; a partial bump
  fails them.

- Update the changelog release heading and the changelog comparison links together with the
  version surfaces. Promote accumulated `[Unreleased]` entries into a
  `## [X.Y.Z] - YYYY-MM-DD` heading, and keep the reference-link block current.
- Update `scripts/README.md` when script features or checks changed.
- Run the release gates after all release metadata is synchronized, then create and push a
  matching `vX.Y.Z` tag for the exact release commit.
- **A partial metadata bump, or a version bump without the matching release tag, is an
  incomplete release.**

Version selection: PATCH for fixes and documentation, MINOR for new checks or features,
MAJOR for breaking changes to behavior or output format.

## Release-Validation and Lab-Controller Authority Boundary

Release validation lives under `tests/release/`. Live certification requires an explicit
profile; it never runs implicitly. See
[`docs/development/release-validation-framework.md`](docs/development/release-validation-framework.md)
and [`docs/deployment/rbac-live-certification.md`](docs/deployment/rbac-live-certification.md).

For live lab work, the following are durable invariants:

- **The Python lab controller owns truth**: physical identity, logical roles, profile
  binding, mutation authorization, recovery decisions, and GO/NO-GO. An agent provides
  orchestration and explanation only. It must never issue an ad hoc live mutation or
  override a controller decision.
- A known-state segment may contain **at most one lab-mutating scenario**, and fresh
  physical-identity and logical-role proof is required before every mutation.
- **Fake, dry-run, static-fixture, injected-fake, and local-harness evidence is not live
  certification evidence.**
- Every live-enablement slice requires its own governing issue and the builder → independent
  validator → resolver workflow, terminating under
  [Terminal Validation and Review Convergence](#terminal-validation-and-review-convergence).
- The protected-file policy applies in full to every live-enablement slice.

Design and sequencing authority:
[Phase 9A — RC hardening re-baseline and gated live lab-controller design](docs/plans/2026-07-17-phase-9a-rc-hardening-rebaseline-and-live-controller-design.md)
and [`docs/development/lab-role-controller-spec.md`](docs/development/lab-role-controller-spec.md).
Non-live orchestration guidance:
[`docs/development/lab-role-controller-agent-instructions.md`](docs/development/lab-role-controller-agent-instructions.md).
**Current phase status is owned by the GitHub issues, not by this file.** Read the RC
hardening umbrella issue [#121](https://github.com/tomazb/rh-acm-switchover/issues/121) and
the open Phase 9 slice issue — [#192](https://github.com/tomazb/rh-acm-switchover/issues/192)
at the time of writing. If the tracker and that number disagree, the tracker is correct:
list the open `Phase 9` issues rather than trusting any status sentence written here.

## Evidence Rules for Generated and External Review

- **Read the applicable process skills and map them to checkpoints** in the work. Skills
  describe how to proceed; they do not replace the gates in this file.
- **Generated graph and cross-reference analysis is a hypothesis generator, never an
  authority.** Relationships marked inferred or ambiguous are leads. Verify each against
  source and tests before reporting it as a finding.
- **External reviews are hypotheses until verified.** The operator-supplied Thermos Ansible
  review and any external or AI reviewer produce candidate findings; validate each against
  source, tests, and documentation before treating it as a repository defect. Track Thermos
  follow-up state in [`thermos-resolution-plan.md`](thermos-resolution-plan.md), one branch
  and one tracker row per PR, each based on the latest merged `ansible` unless the tracker
  records a stacked dependency.
- **Validators must be independent.** A validator that reuses the builder's working tree,
  evidence, or conclusions is not performing independent validation.
- Do not fold unrelated advisory refactors into a safety fix.

Tool-specific invocation mechanics belong in the tool's own instruction file — see
[`CLAUDE.md`](CLAUDE.md) — not here.

## Authoritative Document Index

| Domain | Authority |
| --- | --- |
| Capability parity status | [`docs/ansible-collection/parity-matrix.md`](docs/ansible-collection/parity-matrix.md) |
| Python → collection behavior mapping | [`docs/ansible-collection/behavior-map.md`](docs/ansible-collection/behavior-map.md) |
| Coexistence / shared-behavior contract | [`ansible_collections/tomazb/acm_switchover/docs/coexistence.md`](ansible_collections/tomazb/acm_switchover/docs/coexistence.md) |
| Supported ansible-core, AAP, and dependency matrix | [`ansible_collections/tomazb/acm_switchover/docs/compatibility.md`](ansible_collections/tomazb/acm_switchover/docs/compatibility.md) |
| Collection distribution and packaging | [`ansible_collections/tomazb/acm_switchover/docs/distribution.md`](ansible_collections/tomazb/acm_switchover/docs/distribution.md) |
| Collection variable surface | [`ansible_collections/tomazb/acm_switchover/docs/variable-reference.md`](ansible_collections/tomazb/acm_switchover/docs/variable-reference.md) |
| Architecture and module design | [`docs/development/architecture.md`](docs/development/architecture.md) |
| CI/CD pipeline and container publishing setup | [`docs/development/ci.md`](docs/development/ci.md) |
| Verification gate inventory and test suite structure | [`docs/development/testing.md`](docs/development/testing.md) |
| Release validation framework | [`docs/development/release-validation-framework.md`](docs/development/release-validation-framework.md) |
| Lab role controller | [`docs/development/lab-role-controller-spec.md`](docs/development/lab-role-controller-spec.md) |
| Operator CLI and workflows | [`docs/operations/usage.md`](docs/operations/usage.md) |
| Validation rule reference | [`docs/reference/validation-rules.md`](docs/reference/validation-rules.md) |
| RBAC requirements and deployment | [`docs/deployment/rbac-requirements.md`](docs/deployment/rbac-requirements.md), [`docs/deployment/rbac-deployment.md`](docs/deployment/rbac-deployment.md) |
| Manual switchover procedure (protected) | [`docs/ACM_SWITCHOVER_RUNBOOK.md`](docs/ACM_SWITCHOVER_RUNBOOK.md) |
| Architecture decisions | [`docs/adr/`](docs/adr/) |
| Project orientation | [`CONTEXT.md`](CONTEXT.md) |
| Contributor workflow | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| Issue tracking conventions | [`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md) |
| Domain vocabulary | [`docs/agents/domain.md`](docs/agents/domain.md) |
| Change history | [`CHANGELOG.md`](CHANGELOG.md) |
| Thermos follow-up state | [`thermos-resolution-plan.md`](thermos-resolution-plan.md) |
