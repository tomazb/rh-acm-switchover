# PR26 Runtime Parity Depth Design

## Goal

Deepen release runtime parity and certification guardrails for Argo CD management, checkpoint/resume, and RBAC/bootstrap so release certification fails on real behavior drift instead of passing on artifact-shape equality alone, while keeping supporting output changes minimal and localized.

## Problem

`F43` is not asking for more JSON for its own sake. The current release harness still validates mostly artifact-shape fields:

- `preflight`, `switchover`, `restore-only`, `decommission`, and generic report path-safety fields
- `checkpoints` only as artifact presence plus completed-phase count
- `RBAC/bootstrap artifacts` only as asset count, `include_decommission`, and report filename

That misses the newly hardened behavior this queue has already landed:

- `tests/release/scenarios/runtime_parity.py` already contains `normalize_argocd_management()`, but `CAPABILITY_REQUIRED_FIELDS` never includes Argo CD and `tests/release/orchestrator.py::_normalized_runtime_sources()` never populates it.
- `_normalized_runtime_sources()` only loads final report JSONs plus raw `state.json` / `checkpoint.json`; it does not compare resume decisions, run-id preservation semantics, or cluster-observed Argo CD outcomes.
- The current required release scenario is `argocd-managed-switchover`. The scenario catalog defines it as Argo CD pause and failure recovery, including `resume_on_failure` with the expectation that the pause `run_id` survives retry wiring.
- Successful Argo CD resume clears the `acm-switchover.argoproj.io/paused-by` annotation, so a naive "always discover final apps by run_id" design is wrong for resumed applications.
- The Python CLI already persists rich durable state (`argocd_paused_apps`, `argocd_run_id`, `hub_identities`, `completed_steps`, `errors`). The collection checkpoint already persists `operation_identity`, `completed_phases`, `phase_status`, `errors`, and `operational_data.argocd_run_id`. The missing piece is a small mirrored resume summary the harness can compare directly instead of reverse-engineering logs.
- `rbac-bootstrap` is currently ansible-only in the release adapters, so the present runtime-parity record devolves into shallow metadata or `not_applicable` instead of catching drift in manifest identity or live-certification consistency.

## Scope

- Release harness code in `tests/release/orchestrator.py`, `tests/release/scenarios/runtime_parity.py`, `tests/release/test_orchestrator.py`, and `tests/release/scenarios/test_runtime_parity.py`
- Release certification helper coverage in `tests/release/test_release_certification.py`
- Minimal supporting metadata additions only where the harness cannot already reconstruct behavior:
  - Python durable state/config in `lib/utils.py` and related orchestration helpers if a mirrored resume summary is needed
  - Collection checkpoint operational data in `ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py`
- Tracker updates in `thermos-resolution-plan.md` for the PR26 spec and verification state

## Non-Goals

- No `F44` file-decomposition or broad refactor work
- No large operator-facing report-schema rewrite
- No change to the actual switchover, restore, Argo CD, or RBAC decision logic beyond emitting tiny comparison metadata
- No new release scenario family unless a tiny harness-visible artifact is required to observe an already-supported path
- No intentional Python/collection parity divergence

## Parity And Guardrail Constraints

1. PR26 must improve failure detection without making release certification depend on unstable log parsing.
2. Existing operator-visible report contracts stay stable unless a new field is the smallest safe way to expose already-existing behavior.
3. Argo CD comparison must respect current marker cleanup behavior: `paused-by` can prove paused state, but it cannot be the only evidence source for successful resume.
4. Checkpoint parity remains behavioral, not file-format equality. Python `state.json` and collection `checkpoint.json` do not need to match field-for-field.
5. RBAC bootstrap guardrails may use live-certification evidence when available, but PR26 should not require adding a new Python bootstrap adapter.

## Approaches Considered

### Approach 1: Harness-only deepening

Use only current report/state/checkpoint artifacts and extend the normalizers/tests.

Pros:

- Minimal product-surface churn
- Lowest risk to operator-facing outputs

Cons:

- Still cannot compare resume decisions directly
- Argo CD pause/retry behavior becomes partially reconstructable at best when final marker state is gone
- Leaves too much implicit logic in the harness

### Approach 2: Harness-first plus minimal mirrored resume metadata

Deepen the harness first, and add only the smallest persisted metadata needed to make resume behavior directly comparable.

Pros:

- Catches the real `F43` gap without rewriting report schemas
- Lets Argo CD and checkpoint parity use stable evidence instead of log parsing
- Keeps changes localized to release/testing surfaces plus tiny state/checkpoint helpers

Cons:

- Slightly more coordination between the harness and persisted operational data
- Requires careful scoping so metadata stays release-oriented and does not sprawl

### Approach 3: Broad artifact enrichment

Expand Python and collection reports/checkpoints to expose many more runtime details everywhere.

Pros:

- Maximally observable

Cons:

- Too much operator-facing churn for this Thermos slice
- Higher docs/review burden
- Unnecessary before `F44`

## Recommendation

Use Approach 2.

That means:

- deepen the release harness normalizers and comparisons first
- add Argo CD runtime guardrails only for evidence the current release scenario can prove safely
- add a tiny mirrored resume metadata block to Python durable state and collection checkpoint operational data so resume behavior is directly comparable
- strengthen RBAC/bootstrap guardrails with richer normalized artifact identity and live-certification consistency checks, without inventing a new Python bootstrap adapter

## Design

### 1. Argo CD runtime guardrail: scenario-aware and harness-owned

PR26 should add an Argo CD comparison path, but not by blindly applying the older design note that assumed the pause marker always survives the run.

Design rules:

- Introduce an Argo CD normalized comparison record in `tests/release/scenarios/runtime_parity.py`.
- Keep the release harness as the owner of this comparison logic.
- Source priority:
  1. persisted `run_id` from Python report/state and collection report/checkpoint
  2. persisted namespace hints already carried by Python state / collection checkpoint for same-run discovery scoping
  3. cluster discovery of `Application` resources carrying `acm-switchover.argoproj.io/paused-by=<run_id>` when the scenario ends with apps still paused
- For `argocd-managed-switchover`, compare only fields the current scenario can prove safely:
  - `run_id_present`
  - `paused_application_names` when final marker state exists
  - `paused_application_count`
  - `run_id_preserved_for_retry` when checkpoint/state evidence shows the same run id survives recovery wiring
- When a particular run never enters the rescue or retry path, normalize `run_id_preserved_for_retry` as `not_applicable` rather than failed.
- Do not require PR26 to prove generic `resumed_applications` name sets after successful cleanup, because the current implementation intentionally removes the marker on resume.
- Treat resumed-app safety as part of the checkpoint/resume guardrail in this PR rather than as pure final-cluster Argo CD discovery.

This turns Argo CD from a dormant normalizer into a scenario-aware guardrail that matches real implementation semantics.

### 2. Checkpoint and resume parity: compare decisions, not just file presence

PR26 should replace the current shallow checkpoint comparison with a normalized behavioral summary.

Add a small mirrored `resume` metadata block carried in durable runtime state:

- Python: persisted in state config or another existing durable state location read by the release harness
- Collection: persisted in checkpoint `operational_data` via the checkpoint action flow

Normalized fields:

- `resume_start_phase`
- `skipped_phases`
- `checkpoint_error_count`
- `identity_bound` (derived from existing Python `hub_identities` / collection `operation_identity` cluster UID binding, no new field required if derivable)
- `phase_completion_surface` (`completed_steps` or `completed_phases` normalized to phase names and counts)

Rules:

- Populate `resume_start_phase` only when the run actually resumes from previous durable state rather than starting fresh.
- Record `skipped_phases` as the phases intentionally bypassed because the durable checkpoint already marked them complete.
- Record `checkpoint_error_count` from the persisted error list already written to state/checkpoint.
- Prefer emitting this summary from the places that already decide resume/skip behavior instead of reconstructing it later from logs.

Likely implementation points:

- Python phase-flow or failed-state/resume wiring in `lib/workflow.py` and related CLI orchestration
- Collection checkpoint flow in `ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py`, which already knows when `skipped_phase` is true on `status: enter`

This is the only product-surface addition PR26 should assume by default.

### 3. RBAC and bootstrap guardrails: richer normalized identity plus live consistency

The current RBAC/bootstrap comparison is too shallow because it only checks asset count and filename.

PR26 should strengthen it in two layers.

Layer A: richer normalized bootstrap artifact identity

- normalize exact applied manifest identities from `assets_applied` as a sorted set
- keep `include_decommission`
- compare `bootstrap_status`
- optionally track whether generated kubeconfig output was requested or present only if both sides expose it stably

Layer B: live-certification consistency when `rbac-bootstrap-live` is selected

- add a release-certification guardrail that checks the live certification scenario result is consistent with the bootstrap artifact expectations for the same run
- use this as a release guardrail, not as a forced cross-stream parity comparison when no Python bootstrap stream is present

Design rules:

- Do not add a new Python bootstrap adapter in PR26.
- Do not convert missing second-stream bootstrap evidence into a mandatory certification failure for default profiles.
- Do ensure that when live RBAC certification is explicitly selected, the release harness can catch contradictions between the bootstrapped artifact claims and the live permission result.

### 4. Orchestrator changes

`tests/release/orchestrator.py` should be extended in narrowly-scoped helpers rather than by turning `_runtime_parity()` into one large function.

Recommended helper breakdown:

- `_normalized_runtime_sources(results)` continues collecting report-derived sources
- add helper(s) for Argo CD scenario evidence and checkpoint/resume summary extraction
- add helper(s) for optional live RBAC consistency evidence
- let `_runtime_parity()` assemble comparison records from those helper outputs

This keeps the PR localized and avoids starting the `F44` refactor early.

## File Impact

Likely modified files:

- `tests/release/scenarios/runtime_parity.py`
- `tests/release/orchestrator.py`
- `tests/release/scenarios/test_runtime_parity.py`
- `tests/release/test_orchestrator.py`
- `tests/release/test_release_certification.py`

Possible small supporting files:

- `lib/workflow.py`
- `lib/utils.py`
- `lib/report_artifacts.py` only if the smallest safe Python-side resume metadata home is the report path rather than state config
- `ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py`
- `ansible_collections/tomazb/acm_switchover/docs/artifact-schema.md` if checkpoint `operational_data` gains a documented `resume` sub-block
- `thermos-resolution-plan.md`

## Test Design

### Baseline

Keep the existing narrow baseline as the spec's starting point:

- `python -m pytest tests/release/scenarios/test_runtime_parity.py tests/release/test_release_certification.py -q`

Current branch baseline on `test/thermos-26-runtime-parity-depth`:

- `11 passed, 1 skipped`

### Unit and helper coverage

Add targeted tests for:

- Argo CD runtime normalization using persisted run id plus cluster discovery when markers remain
- graceful behavior when Argo CD markers are absent after successful resume
- checkpoint/resume normalization for Python state and collection checkpoint examples
- richer RBAC/bootstrap artifact normalization (exact manifest set, not just count)

### Orchestrator coverage

Add orchestrator tests that prove:

- Argo CD runtime guardrails are populated for `argocd-managed-switchover`
- resume metadata is collected from both Python and collection durable artifacts
- missing required resume evidence becomes a failed guardrail rather than silently `not_applicable`
- live RBAC certification consistency checks affect the release summary only when the scenario is selected

### Release certification coverage

Add focused certification tests that prove:

- a run with mismatched Argo CD runtime evidence fails the release summary
- a run with mismatched resume metadata fails the runtime parity artifact
- a selected live RBAC certification scenario can fail release certification even when the bootstrap artifact itself looks syntactically valid

## Documentation Impact

- Write this spec to `docs/superpowers/specs/2026-06-06-pr26-runtime-parity-depth-design.md`
- Update `thermos-resolution-plan.md` as PR26 progresses
- Update `ansible_collections/tomazb/acm_switchover/docs/artifact-schema.md` only if collection checkpoint `operational_data` gains a documented `resume` block
- No parity-matrix status changes are expected

## Acceptance Criteria

1. Release runtime parity no longer treats Argo CD as an unimplemented normalizer; it evaluates the currently supported managed Argo CD scenario with stable evidence.
2. Checkpoint/resume comparison uses explicit resume metadata plus existing identity/error state, not only artifact presence and completed count.
3. RBAC/bootstrap guardrails compare richer manifest identity and, when selected, check live-certification consistency instead of only artifact metadata.
4. Default certification profiles remain usable without adding a new Python bootstrap adapter.
5. Supporting output changes stay minimal and localized to durable runtime state/checkpoint metadata rather than broad report-schema expansion.
6. The narrow release helper suite continues to pass before any broader verification:
   - `python -m pytest tests/release/scenarios/test_runtime_parity.py tests/release/test_release_certification.py -q`
