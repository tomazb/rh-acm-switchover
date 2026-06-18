# Lab Role Controller Closeout Review

## Status

This review covers the non-live lab role controller through Phase 7B.

This review does not approve live ACM certification.

This review does not enable live cluster execution.

Live discovery, live recovery, live adapter execution, and Agent-driven live operation remain out of scope.

## Final Recommendation

READY_FOR_LIVE_READINESS_DESIGN

The completed Phase 1-7B stack is ready to serve as the deterministic, non-live foundation for live-readiness design
because it keeps known-state role decisions, generated profiles, materialized release-framework requests, recovery
classification, artifact redaction, CLI behavior, and Agent instructions inside explicit non-live boundaries. The
review found no blocking safety, documentation, or test drift in the reviewed scope; future work must design live
approval, discovery, execution, redaction, and recovery contracts before any live operation is implemented.

## Reviewed Scope

- `AGENTS.md`
- `docs/development/release-validation-framework.md`
- `docs/development/lab-role-controller-spec.md`
- `docs/development/lab-role-controller-agent-instructions.md`
- `scripts/release/run_lab_role_controller.py`
- `tests/release/lab_controller/`
- `tests/release/test_lab_controller*.py`
- `tests/test_documentation_guardrails.py`
- `tests/release/test_release_certification.py`
- `tests/release/contracts/models.py`
- `tests/release/profiles/`
- `tests/release/adapters/python_cli.py`
- `tests/release/adapters/ansible.py`
- `tests/release/scenarios/catalog.py`
- `tests/release/conftest.py` as the equivalent matrix/selector integration point

## Phase Inventory

| Phase | Implemented capability | Current evidence | Live behavior status | Notes / risks |
| --- | --- | --- | --- | --- |
| Phase 1 | Deterministic physical identity, role inference, and segment gates | `identity.py`, `roles.py`, `segments.py`, `test_lab_controller_identity.py`, `test_lab_controller_roles.py`, `test_lab_controller_segments.py` | Non-live fakes only | Identity model uses deterministic fake evidence and fails closed on missing, swapped, duplicate, or changed fingerprints. |
| Phase 2 | Role-aware profile generation and stale profile checks | `profiles.py`, `test_lab_controller_phase2_profiles.py`, `test_lab_controller_profiles_artifacts.py` | Non-live generated profile payloads only | Runtime profile data can contain placeholder kubeconfig references, but publishable metadata is redacted and hashed. |
| Phase 3 | One-segment controller wrapper | `controller.py`, `test_lab_controller_phase3_controller.py` | Fake executor only unless an explicit backend object is injected | Planning, execution summary, verification, and segment artifacts share the same controller path. |
| Phase 4 | Multi-segment ping-pong planner | `planner.py`, `test_lab_controller_phase4_planner.py` | Non-live fake plan | Handoff requires proven final state before a later segment may start. |
| Phase 5 | Recovery decision tree and run artifact contract | `recovery.py`, `planner.py`, `test_lab_controller_phase5_recovery.py` | Non-live metadata only | `safe_to_continue` and retry metadata are controller/run metadata, not live-cluster authorization. |
| Phase 6A | Execution backend abstraction | `execution.py`, `test_lab_controller_phase6_execution.py` | Dry-run release-framework requests only | Live release-framework mode fails closed. |
| Phase 6B | Materialized release-framework invocation model | `invocation.py`, `test_lab_controller_phase6b_materialization.py` | Structured non-executed argv/env/artifact plans only | Loader compatibility is explicit as `loader_compatible=false` because runtime profile files are not written. |
| Phase 6C | Explicitly gated local execution harness | `harness.py`, `ReleaseFrameworkLocalBackend`, `test_lab_controller_phase6c_harness.py` | Local fake command-runner harness only | Local harness can mark `real_execution_evidence=true`, never `live_certification_evidence=true`. |
| Phase 6D | Consolidation / architecture hardening | Centralized controller, planner, recovery, redaction, and execution modules plus regression tests | Non-live only | No separate live path was introduced during consolidation. |
| Phase 7A | Non-live CLI wrapper | `scripts/release/run_lab_role_controller.py`, `test_lab_controller_phase7a_cli.py` | Supported modes are `fake`, `release-framework-dry-run`, and explicitly gated `release-framework-local` | Artifact output requires caller-provided `--artifact-dir` unless `--no-write` is used. |
| Phase 7B | Agent operating instructions | `docs/development/lab-role-controller-agent-instructions.md`, documentation guardrail tests | Agent guidance only | Agent is explicitly subordinate to controller decisions and cannot claim live certification evidence. |

## Non-Live Capability Matrix

| Capability | Implemented | Deterministic | Live execution involved | Test coverage summary | Residual risk |
| --- | --- | --- | --- | --- | --- |
| identity verification | yes | yes | no | Missing, duplicate, swapped, changed, and context-only identity cases are tested. | Live identity proof signals remain design work. |
| logical role inference | yes | yes | no | Active/passive, both-active, neither-active, unknown role, missing/extra managed cluster cases are tested. | Real ACM signal quality is not implemented. |
| known-state segment gating | yes | yes | no | Segment start, chain handoff, ambiguous state, and stale handoff cases are tested. | Future live rediscovery boundaries must be designed. |
| role-aware profile generation | yes | yes | no | Profile mapping, hash stability, role flip, and contract compatibility are tested. | Production profile schema is not finalized. |
| stale profile detection | yes | yes | no | Role mapping drift, managed cluster drift, identity drift, and hash mismatch are tested. | Live generated profile storage policy remains design work. |
| one-segment execution wrapper | yes | yes | no | Phase 3 tests cover pass, NO_GO, RECOVERY_REQUIRED, INFRA_RETRYABLE, stale profiles, and artifacts. | Live executor integration is intentionally absent. |
| multi-segment ping-pong planner | yes | yes | no | Full fake ping-pong and blocked handoff/failure cases are tested. | Real reset/recovery sequencing is not implemented. |
| recovery/final decision logic | yes | yes | no | Phase 5 tests cover final decisions, retry eligibility, manual recovery, redaction failures, and summary counts. | Human approval semantics for live recovery remain open. |
| run-level artifact bundle | yes | yes | no | Stable top-level contract keys and rejected-artifact fallback are tested. | Contract is provisional, not a production JSON schema. |
| redaction/sensitive payload validation | yes | yes | no | Segment/run/materialized/CLI tests cover sensitive paths, URLs, keys, env, stdout, stderr, hints, and blockers. | A future live artifact allowlist must be designed. |
| dry-run release-framework request construction | yes | yes | no | Phase 6A tests cover supported fields, catalog scenarios, stream selection, and fail-closed invalid requests. | Request construction is not execution evidence. |
| materialized invocation model | yes | yes | no | Phase 6B tests cover structured argv, supported pytest flags, env/artifact safety, eligibility, and summaries. | Runtime profile loader compatibility remains explicit but not file-backed. |
| local fake harness | yes | yes | no live execution | Phase 6C tests cover local gate, fake command runner, outputs, timeouts, and live-mode rejection. | It is local harness evidence only. |
| non-live CLI | yes | yes | no | Phase 7A tests cover direct invocation, modes, `--no-write`, `--strict`, artifact safety, stdout, and no `.release`. | CLI currently supports only the built-in fake ping-pong plan. |
| Agent operating instructions | yes | yes | no | Documentation guardrails enforce non-live authority boundary, fields, examples, and human retry rules. | Future Agent skill or automation must preserve this boundary. |

## Explicitly Unsupported Behavior

The following behavior remains unsupported and must not be implied as available:

- live discovery
- live ACM certification through the lab role controller
- live release adapter execution through the lab role controller
- `oc`, `kubectl`, or `ansible-playbook` execution through the controller/Agent path
- automatic live recovery
- production JSON schema finalization
- committed runtime profiles
- `.release` default artifact output
- Agent override of controller decisions
- `live_certification_evidence=true`

## Safety Boundary Review

The controller owns truth and safety. It verifies physical hub identity, infers logical role state, gates segment
starts, generates role-aware profiles, validates profile freshness, evaluates final role state, classifies recovery,
and emits final decisions.

The Agent owns only orchestration convenience and explanation. The Phase 7B instructions require the Agent to use the
Phase 7A CLI as the command boundary, derive summaries from controller artifacts, and stop on controller hard-stop
decisions.

`PASS`, `NO_GO`, `RECOVERY_REQUIRED`, `INFRA_RETRYABLE`, and `BLOCKED` have distinct meanings. `PASS` means the
non-live controller plan completed and final state is proven in modeled evidence. `NO_GO` is a certification failure.
`RECOVERY_REQUIRED` means manual recovery or inspection is needed before further work. `INFRA_RETRYABLE` permits a
bounded retry only when pre-mutation failure is marked retryable and the initial state is proven. `BLOCKED` identifies
plan, config, model, or input defects.

`safe_to_continue` is non-live metadata, not live-cluster authorization. Dry-run materialization is not execution
evidence. Local fake harness evidence is not live ACM certification evidence. `live_certification_evidence` remains
false in current phases. Unsupported live modes fail closed in request construction, harness gates, and CLI validation.

## Artifact Contract Review

The current artifact contract is provisional and not a finalized production JSON schema.

Segment artifacts include the segment id, scenario id, scenario classification, mutation flag, identity verification
summary, observed and desired role state, generated profile hash and redacted metadata, execution summaries,
materialization summaries, managed-cluster evidence summary, controller decision, reason, recovery hint, redaction
status, `real_execution_evidence`, and `live_certification_evidence`.

Run artifacts include ordered segments, per-segment decisions, role transition graph, segment artifact summaries,
final decision, `safe_to_continue`, `retry_allowed`, `manual_recovery_required`, first blocker fields, recovery
category, operator action hint, mutation metadata, final state proof, final role state, summary counts, runtime parity
placeholder, redaction status, execution backend summary, materialized release-framework summary, and execution
harness summary.

CLI artifacts add Phase 7A metadata, artifact file list, selected plan/mode, write mode, strict flag, artifact
directory summary, and explicit `live_execution_evidence=false` and `live_certification_evidence=false`.

`redaction_status` is recorded at segment and run levels. `real_execution_evidence` can become true only for the local
fake harness path and remains non-live evidence. `live_certification_evidence` is forced false. `runtime_parity` is a
non-authoritative placeholder. `materialized_release_framework` records dry-run request materialization. The
`execution_harness_summary` records local/fake harness activity. First blocker fields and retry/recovery metadata are
present in run artifacts.

## Redaction Review

Redaction and sensitive payload handling are applied across profile metadata, segment artifacts, run artifacts, CLI
stdout/stderr summaries, CLI JSON artifacts, Agent instructions, materialized invocation summaries, env summaries,
command summaries, stdout/stderr summaries, gate reasons, and blocker reasons.

Profile metadata redacts raw context and kubeconfig references, keeps hashes, and rejects publishable metadata that
contains unredacted sensitive fields. Segment and run artifacts recursively sanitize and validate payloads, and fall
back to rejected minimal artifacts when needed. CLI stdout renders sanitized summary fields only. Agent instructions
forbid raw kubeconfig paths, raw API endpoints, tokens, credentials, private cluster identifiers, and unredacted
profile metadata.

The reviewed code blocks, redacts, or rejects these sensitive patterns in publishable controller outputs:

- `kubeconfig`: blocked or redacted outside runtime-only profile payloads
- `token`: blocked or redacted in keys, values, env summaries, and output summaries
- `password`: blocked or redacted in keys, values, env summaries, and output summaries
- `secret`: blocked or redacted in keys, values, env summaries, and output summaries
- `credential`: blocked or redacted in keys, values, env summaries, and output summaries
- raw API URLs: redacted or rejected in summaries and artifacts
- `/home/`: redacted or rejected in summaries and artifacts
- `/tmp/`: redacted or rejected when it appears as sensitive publishable metadata; CLI permits caller-provided temp
  artifact roots but summarizes only the directory name
- `~/.kube/`: redacted or rejected in summaries and artifacts
- private cluster IDs: blocked through cluster-id marker checks in publishable metadata
- unsafe generated-profile metadata: rejected unless marked redacted and all sensitive fields are redacted or hashed

## CLI Review

`scripts/release/run_lab_role_controller.py` is a thin wrapper over controller logic. The default mode is `fake`, which
uses the built-in sanitized `hub-a`/`hub-b` plus `mc-1`/`mc-2`/`mc-3` fixture. Supported modes are documented in help
and code as `fake`, `release-framework-dry-run`, and `release-framework-local`.

Live modes such as `live`, `release-framework-live`, and `release_framework_live` fail closed. `--artifact-dir` is
caller-provided and required unless `--no-write` is selected. `.release` is not used by default and is rejected as an
artifact directory component. `--no-write` writes no files. `--strict` returns deterministic non-zero status for
non-`PASS` decisions. Stdout summaries are sanitized. Direct script invocation works without `PYTHONPATH`. The CLI
reuses the controller and planner paths instead of duplicating safety logic.

## Agent Instruction Review

`docs/development/lab-role-controller-agent-instructions.md` preserves the Phase 7B non-live boundary and is covered
by documentation guardrail tests.

Confirmed:

- Agent uses the Phase 7A CLI as the only command boundary.
- Agent cannot override controller decisions.
- Agent cannot claim dry-run/local fake evidence as live certification evidence.
- Agent must stop on `NO_GO`, `RECOVERY_REQUIRED`, and `BLOCKED`.
- `INFRA_RETRYABLE` retry requires `retry_allowed=true` plus explicit human instruction.
- Examples use supported non-live CLI options.
- No valid workflow example uses `.release`, kubeconfig paths, API URLs, credentials, or live modes.

## Release-Framework Compatibility Review

The materialized argv uses the real pytest target `tests/release/test_release_certification.py` and supported release
options: `--release-profile`, `--release-mode`, `--release-scenario`, `--release-stream`, and
`--release-artifact-dir`. Scenario IDs are validated against `tests/release/scenarios/catalog.py`. Stream selectors
derive from catalog streams and generated profile enabled streams.

Generated profile compatibility is not overstated. The materialization summary records `runtime_only_profile=true`,
`contract_shape_compatible=true` for valid generated payloads, and `loader_compatible=false` with a warning that the
runtime-only profile payload was not written to disk.

Dry-run and materialized requests do not execute pytest. The local harness invokes only an injected command-runner
interface, and Phase 7A supplies a fake command runner. The harness validates argv shape, supported flags, scenario
classification, environment safety, artifact path safety, redaction, and local execution gates before any fake command
runner call.

## Validation Evidence

| Command | Result |
| --- | --- |
| `python -m pytest tests/release/test_lab_controller*.py -q` | Passed: 262 passed |
| `python -m pytest tests/release -q` | Passed: 495 passed, 1 skipped. The skipped test is the release-marked certification entrypoint without an explicit profile. |
| `python -m pytest tests/test_documentation_guardrails.py -q` | Passed: 28 passed |
| `black --check --line-length 120` on changed Python files | Not applicable: no Python files changed |
| `isort --check-only --profile black --line-length 120` on changed Python files | Not applicable: no Python files changed |
| `flake8 --jobs=1` on changed Python files | Not applicable: no Python files changed |
| `mypy` on changed Python files | Not applicable: no Python files changed |
| `bandit -r tests/release/lab_controller scripts/release` | Passed: no issues identified; 5410 lines scanned |
| `git diff --check` | Passed |
| `git diff --cached --check` if files are staged | Passed; no staged files |
| `graphify update .` if required/available by `AGENTS.md` | Passed: AST extraction updated graphify metadata; output noted semantic doc extraction would require the full assistant `/graphify --update` flow |
| CodeRabbit review if available and authenticated | Passed on rerun: `coderabbit review --agent -t uncommitted` completed with 0 findings. The initial run had 2 minor doc comments; the stale pending-validation comment was resolved, and the Phase 7C wording comment was not applied because this document is the Phase 7C closeout and the user explicitly requested confirmation that Phase 7C adds no live behavior. |

## Findings

No BLOCKER/HIGH/MEDIUM/LOW findings were found in the reviewed non-live scope.

| ID | Severity | Area | Finding | Evidence | Required action | Disposition |
| --- | --- | --- | --- | --- | --- | --- |
| INFO-01 | INFO | Live-readiness design | The controller has a strong non-live foundation, but live identity proof, approval gates, and command policy are intentionally absent. | Phase 1-7B code and docs repeatedly fail closed for live modes and keep `live_certification_evidence=false`. | Design these items in Phase 8A before implementation. | Non-blocking follow-up |
| INFO-02 | INFO | Artifact contract | The artifact contract is stable enough for current tests but remains provisional. | Specs and artifact code describe schema/final JSON contract as future work. | Finalize only after live-readiness design settles evidence requirements. | Non-blocking follow-up |
| INFO-03 | INFO | Runtime profile compatibility | Generated profiles are runtime-only and not written to disk, so loader compatibility is explicit but not file-backed. | Materialization records `loader_compatible=false` and runtime-only warnings. | Decide whether Phase 8A needs a private runtime profile materialization model. | Non-blocking follow-up |

## Follow-Ups

| ID | Description | Why it matters | Suggested next phase | Blocking |
| --- | --- | --- | --- | --- |
| FU-01 | Design human approval gates for any future live execution. | Live mutation must not be enabled by CLI flags or Agent discretion alone. | Phase 8A | no |
| FU-02 | Design live discovery identity proof signals and ambiguity thresholds. | The fake identity model must be replaced by live evidence before mutation. | Phase 8A | no |
| FU-03 | Design a live artifact redaction allowlist and audit evidence policy. | Live evidence will include higher-risk paths, contexts, and cluster-derived data. | Phase 8A | no |
| FU-04 | Decide whether runtime profile files are ever written, where, and how they are withheld from commits. | Materialized requests currently use hash references only. | Phase 8A | no |
| FU-05 | Define the first safe live scenario and rollback/no-automatic-recovery rules. | Implementation should begin with the smallest live proof path, not full multi-mutation execution. | Phase 8A | no |

## Live-Readiness Blockers

The following must be designed before any live execution phase:

- human approval gates
- kubeconfig handling model
- live discovery identity proof
- RBAC/live certification prerequisites
- live artifact redaction policy
- allowed command matrix
- forbidden command matrix
- manual recovery boundaries
- first safe live scenario
- rollback/no-automatic-recovery rules
- audit evidence requirements

These items are not implemented by Phase 7C.

## Next Recommended Phase

Phase 8A: live-readiness design

## Review Rules

This review did not identify any condition requiring `FAIL_BLOCKED`.

Confirmed:

- No live cluster commands were introduced.
- Live adapters cannot be invoked through the controller by default.
- `live_certification_evidence` cannot become true in current non-live controller modes.
- Agent instructions do not allow overriding controller decisions.
- CLI cannot write under `.release` by default.
- Reviewed controller artifacts reject or sanitize unredacted kubeconfig/API URL/token-like values.
- Tests and controller docs do not claim that the lab role controller provides live certification support.
- Protected runbooks were not modified.

Phase 7C adds no live execution behavior.
