# Phase 9A — RC Hardening Re-baseline and Gated Live Lab-Controller Design

## Status and authority

Status: **design only; no live enablement**

This document is the authoritative Phase 9 design for incrementally enabling the lab role controller. It re-baselines
the RC-hardening umbrella in GitHub Issue
[#121](https://github.com/tomazb/rh-acm-switchover/issues/121) and the earlier
[RC-hardening specification](2026-06-15-release-validation-rc-hardening-spec.md) against:

- repository: `tomazb/rh-acm-switchover`
- branch: `origin/ansible`
- source revision: `450c8c6661bc0172000921cdb18de7b83a965b9f`
- assessment date: 2026-07-17
- Phase 9A tracker: GitHub Issue
  [#180](https://github.com/tomazb/rh-acm-switchover/issues/180)

It supersedes earlier Phase 9 numbering in
[`lab-role-controller-live-readiness-design.md`](../development/lab-role-controller-live-readiness-design.md), but
does not invalidate the safety boundaries or completed non-live Phase 1–8P/8Q work described there. In this document,
Phase 9A is the present design/re-baseline, and Phases 9B–9F are the later slices defined below.

Phase 9A adds no executable schema, controller code, release adapter, profile, fixture, workflow, manifest, cluster
access, or live command. It is not live ACM certification evidence and does not make the repository live-ready.
Phase 9B remains blocked until this design is merged and independently validated.

## Evidence rules used by this re-baseline

The classifications in this document mean:

- `IMPLEMENTED_AND_PROVEN`: repository behavior exists and focused tests prove the stated, non-live or live-safe
  contract. This label never converts fake-backed proof into live proof.
- `PARTIALLY_IMPLEMENTED`: useful implementation exists, but one or more required behaviors or proof layers are
  absent.
- `NON_LIVE_ONLY`: deterministic models, fakes, dry runs, static fixtures, or local harness behavior exist, but no
  qualifying live evidence exists.
- `DESIGNED_NOT_IMPLEMENTED`: an applicable repository design exists, but the required runtime behavior does not.
- `MISSING`: neither sufficient implementation nor a complete applicable design exists.
- `SUPERSEDED`: an earlier requirement or design has been replaced by a more precise authority.
- `BLOCKED`: the requirement cannot currently reach its definition of done because another hard gate is unmet.

`UNVERIFIED` means source or unit evidence exists but the required environment-dependent result has not been
demonstrated. `REQUIRES_LIVE_VALIDATION` means the code path cannot be considered proven for live certification until
an independently reviewed, gated lab execution supplies the required artifacts.

Fake discovery, injected fake clients, fake executors, dry-run materialization, non-executed invocation plans, local
command-runner harnesses, checked-in profiles, and static Kustomize fixtures are never live implementation or live
certification evidence. The existing release framework has a live-capable `OcDiscoveryClient`, but the current lab
controller does not integrate it as an authoritative identity collector. The Phase 8J controller transport accepts
only an injected client protocol, its repository integrations and tests use fake/injected paths, the operator
controller CLI does not integrate authoritative live discovery, and its result forcibly keeps
`live_certification_evidence=false`. Read-only transport capability is therefore not controller-owned live identity
proof or live certification authority.

## Current architecture and selected direction

Three approaches were evaluated:

1. Extend `tests/release/orchestrator.py` into a multi-mutation live controller. This would mix raw discovery,
   physical identity, logical roles, mutation authorization, scenario execution, and reporting in the existing
   profile-oriented runner. It would retain the unsafe assumption that one static primary/secondary profile can span
   role transitions.
2. Replace the pytest release framework with a new live controller. This would duplicate catalog, adapter, baseline,
   parity, and reporting logic and create two certification authorities.
3. Put the Python lab role controller around the existing release framework. The controller owns lab truth, segment
   state, role-aware profiles, mutation authorization, recovery classification, and GO/NO-GO. The existing framework
   continues to own catalog validation, scenario adapters, baseline checks, normalized results, and report rendering.

Approach 3 is selected. It preserves the current architecture while adding a single safety authority. Every later
implementation must reuse the existing catalog and framework contracts; it must not create an alternate Agent,
workflow, or adapter path that can mutate independently.

## RC-hardening requirement-status matrix

The matrix has one row per independently verifiable requirement. “PR/issue evidence” is supporting history, not a
substitute for source and tests.

| ID | Original requirement | Status | Exact source files | Exact tests | PR/issue evidence | Remaining gap | Phase 9 owner | Blocks live certification | Required independent validation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| RC-A1 | Give every scenario explicit initial/final state, mutation, reset, follow-up, and recovery metadata. | `IMPLEMENTED_AND_PROVEN` | `tests/release/scenarios/catalog.py` | `tests/release/scenarios/test_catalog.py`, `tests/release/test_lab_controller_scenarios.py` | Issue #121 workstream A; commit `c471f900` | None for catalog metadata; live state proof is tracked separately. | 9C | No | Compare every catalog ID with lifecycle metadata and reject omissions. |
| RC-A2 | Classify every scenario as non-mutating or lab-mutating and fail closed on unknown classification. | `IMPLEMENTED_AND_PROVEN` | `tests/release/scenarios/catalog.py`, `tests/release/lab_controller/decisions.py`, `tests/release/lab_controller/segments.py` | `tests/release/scenarios/test_catalog.py`, `tests/release/test_lab_controller_segments.py` | Issue #121 A; commit `c471f900` | New scenarios must remain subject to the same completeness tests. | 9C | Yes | Add a candidate unknown scenario and prove planning stops before authorization. |
| RC-A3 | Reject unsafe multi-mutation sequences until reset/known-state sequencing exists. | `IMPLEMENTED_AND_PROVEN` | `tests/release/scenarios/catalog.py`, `tests/release/orchestrator.py` | `tests/release/test_orchestrator.py::test_orchestrator_blocks_unsafe_mutating_sequence_and_reports_matrix_validation`, `tests/release/scenarios/test_catalog.py` | Issue #121 A | Rejection exists; live segmented execution does not. | 9C/9E | Yes | Prove full profiles remain blocked and a single selected mutation remains separately gated. |
| RC-A4 | Isolate each live mutation in a known-state segment with proven handoff. | `NON_LIVE_ONLY` | `tests/release/lab_controller/segments.py`, `tests/release/lab_controller/planner.py`, `tests/release/lab_controller/controller.py` | `tests/release/test_lab_controller_phase4_planner.py`, `tests/release/test_lab_controller_phase5_recovery.py` | Commits `eee218a8`, `dc67fd34`, `e97d8d69`, `7211af45` | Models and fake plans exist; no controller-gated live segment exists. | 9E/9F | Yes | Live artifacts must prove fresh start state, one mutation handoff, and final state. |
| RC-B1 | Keep catalog, release profiles, and adapters aligned for every required scenario/stream pair. | `PARTIALLY_IMPLEMENTED` | `tests/release/scenarios/catalog.py`, `tests/release/contracts/models.py`, `tests/release/adapters/python_cli.py`, `tests/release/adapters/ansible.py` | `tests/release/scenarios/test_catalog.py`, `tests/release/test_lab_controller_scenarios.py`, `tests/release/test_orchestrator.py::test_orchestrator_blocks_required_unsupported_pair_before_adapter_execution` | Issue #121 B | Unsupported pairs are truthfully blocked, but full required certification coverage is not executable. | 9F and later | Yes | Scenario-by-scenario support and execution evidence, not catalog presence alone. |
| RC-B2 | Required unsupported pairs fail; optional unsupported pairs are explicit `not_applicable`. | `IMPLEMENTED_AND_PROVEN` | `tests/release/scenarios/catalog.py`, `tests/release/orchestrator.py` | `tests/release/test_orchestrator.py::test_orchestrator_blocks_required_unsupported_pair_before_adapter_execution`, `tests/release/test_orchestrator.py::test_orchestrator_records_optional_unsupported_pair_as_not_applicable` | Issue #121 B | None in current matrix validator. | 9C | No | Re-run matrix contract tests after every catalog or adapter change. |
| RC-B3 | Checked-in full profiles complete an RC-ready certification matrix. | `BLOCKED` | `tests/release/profiles/full-release.example.yaml`, `tests/release/profiles/argocd-release.example.yaml`, `tests/release/profiles/full-release-with-rbac-cert.example.yaml` | `tests/release/contracts/test_profiles.py`, `tests/release/test_orchestrator.py` | Issue #121 B | Profiles intentionally document a multi-mutation target that the validator rejects. Static role mappings are unsafe after a role transition. | 9F and later | Yes | An independently validated segmented run must replace any claim based on one static full profile. |
| RC-C1 | Execute scenario cycles with explicit semantics. | `PARTIALLY_IMPLEMENTED` | `tests/release/contracts/models.py`, `tests/release/scenarios/soak.py`, `tests/release/orchestrator.py` | `tests/release/scenarios/test_catalog.py`, `tests/release/scenarios/test_soak.py` | Issue #121 C | Profiles can describe cycles and soak helpers can aggregate supplied results, but the orchestrator executes each selected scenario once. | Later soak slice | Yes for full RC | Prove the runner executes the configured count and records every cycle boundary. |
| RC-C2 | Enforce cooldown between cycles and record it. | `MISSING` | `tests/release/contracts/models.py`, `tests/release/orchestrator.py` | No test reaches enforced cooldown behavior. | Issue #121 C | No runner enforcement or artifact evidence. | Later soak slice | Yes for soak | Clock-controlled unit tests plus live timestamps for every cooldown. |
| RC-C3 | Implement soak semantics over repeated known-state cycles. | `NON_LIVE_ONLY` | `tests/release/scenarios/soak.py`, `tests/release/scenarios/catalog.py` | `tests/release/scenarios/test_soak.py` | Issue #121 C | Aggregation helpers exist, but `soak` is not certification-supported and no live segmented cycle engine exists. | Later soak slice | Yes for full RC | Live repeated-cycle evidence with proven state before and after every mutation. |
| RC-C4 | Enforce tolerated failure budgets while hard failures stay blocking. | `MISSING` | `tests/release/contracts/models.py`, `tests/release/reporting/summary.py` | Existing tests validate summaries, not a tolerated-failure budget engine. | Issue #121 C | No scenario-level budget execution or hard-failure precedence contract. | Later soak/failure-budget slice | Yes for full RC | Deterministic budget exhaustion tests and live per-cycle evidence. |
| RC-D1 | Classify recovery, hard stops, and retry eligibility. | `NON_LIVE_ONLY` | `tests/release/lab_controller/recovery.py`, `tests/release/lab_controller/planner.py` | `tests/release/test_lab_controller_phase5_recovery.py` | Phase 5 commits `dc67fd34`, `e97d8d69`, `7211af45` | Strong fake-backed semantics exist; they do not authorize live recovery. | 9C | Yes | Map every live evidence failure to one decision and prove no Agent override. |
| RC-D2 | Execute bounded recovery actions and prove post-recovery state. | `MISSING` | `tests/release/baseline/recovery.py`, `tests/release/orchestrator.py` | `tests/release/baseline/test_recovery.py` covers planning helpers only. | Issue #121 D | No live recovery executor; Phase 9B–9F intentionally do not add automatic recovery. | Separate later recovery slice | Yes for recovery claims | Dedicated recovery segment, fresh discovery, explicit authorization, and final proof. |
| RC-D3 | Persist recovery budgets and observed hard stops. | `PARTIALLY_IMPLEMENTED` | `tests/release/reporting/artifacts.py`, `tests/release/reporting/summary.py`, `tests/release/orchestrator.py` | `tests/release/test_orchestrator.py::test_orchestrator_writes_recovery_budget_from_profile`, `tests/release/reporting/test_summary.py` | Issue #121 D/J | Budget metadata is copied, but consumption and actual recovery enforcement are not implemented. | Later recovery slice | Yes for full RC | Demonstrate measured consumption, stop precedence, and immutable artifacts. |
| RC-E1 | Perform real initial/final release-framework discovery with bounded failures. | `PARTIALLY_IMPLEMENTED` | `tests/release/orchestrator.py`, `tests/release/baseline/discovery.py` | `tests/release/test_orchestrator.py::test_oc_discovery_client_raises_explicit_error_when_oc_is_missing`, `tests/release/test_orchestrator.py::test_orchestrator_records_discovery_failure_in_manifest`, `tests/release/baseline/test_discovery.py` | Issue #121 E | `OcDiscoveryClient` can read live resources, but role-controller integration, physical identity proof, freshness, and evidence origin are absent. `REQUIRES_LIVE_VALIDATION`. | 9B | Yes | Gated read-only run with real client, time bounds, provenance, and redacted artifacts. |
| RC-E2 | Prove stable physical hub identity rather than trusting labels, paths, or contexts. | `NON_LIVE_ONLY` | `tests/release/lab_controller/identity.py`, `tests/release/lab_controller/models.py` | `tests/release/test_lab_controller_identity.py` | Phase 1 commit `c471f900` | Required fingerprints are modeled and tested with fakes only. | 9B | Yes | Real `kube-system` UID plus independent OpenShift/API identity evidence for both hubs. |
| RC-E3 | Prove exactly one active primary and one secondary from agreeing ACM evidence. | `NON_LIVE_ONLY` | `tests/release/lab_controller/roles.py`, `tests/release/baseline/discovery.py` | `tests/release/test_lab_controller_roles.py`, `tests/release/baseline/test_discovery.py` | Phase 1 commit `c471f900` | Controller role signals are fake; baseline role inference relies primarily on BackupSchedule/Restore presence and is insufficient for mutation authorization. | 9B/9C | Yes | Both-active, neither-active, partial, disagreement, and stale evidence on a real lab. |
| RC-E4 | Bind and verify the exact expected managed-cluster set. | `PARTIALLY_IMPLEMENTED` | `tests/release/baseline/assertions.py`, `tests/release/lab_controller/roles.py`, `tests/release/lab_controller/live_config.py` | `tests/release/baseline/test_assertions.py`, `tests/release/test_lab_controller_roles.py`, `tests/release/test_lab_controller_phase8c_live_config_model.py` | Issue #121 E; Phase 8C | Exact names/counts fail closed in models, but live ownership, UIDs, availability, connection state, and drift are not authoritative. | 9B/9C | Yes | Live exact-set proof for one, two, and three managed clusters, including extra/missing/unavailable cases. |
| RC-E5 | Prove lab readiness before mutation. | `PARTIALLY_IMPLEMENTED` | `tests/release/checks/lab_readiness.py`, `tests/release/orchestrator.py` | `tests/release/checks/test_lab_readiness.py`, `tests/release/test_orchestrator.py::test_orchestrator_stops_before_mutation_when_lab_readiness_fails` | Issue #121 E | Current checks are useful but do not combine identity, compatibility, role, RBAC, GitOps ownership, profile freshness, and exact managed-cluster evidence. | 9C | Yes | One deterministic pre-mutation decision artifact containing every mandatory evidence class. |
| RC-E6 | Prove a final baseline and expected final role before handoff. | `PARTIALLY_IMPLEMENTED` | `tests/release/baseline/fingerprint.py`, `tests/release/baseline/assertions.py`, `tests/release/orchestrator.py`, `tests/release/lab_controller/recovery.py` | `tests/release/baseline/test_fingerprint.py`, `tests/release/test_orchestrator.py::test_orchestrator_stops_before_mutation_when_baseline_check_fails`, `tests/release/test_lab_controller_phase4_planner.py::test_final_pass_requires_final_proven_role_state` | Issue #121 E | Baseline fingerprints contain placeholders for some platform/OADP capabilities; controller final proof is fake-backed. | 9E/9F | Yes | Live role, managed-cluster, backup/restore, GitOps, and redaction proof after the one mutation. |
| RC-E7 | Reject stale discovery by age, skew, timestamp, and evidence origin. | `DESIGNED_NOT_IMPLEMENTED` | `docs/development/lab-role-controller-read-only-discovery-design.md`, `docs/development/lab-role-controller-read-only-live-preflight-pilot-design.md` | Guardrail tests cover modeled fields, not real timestamp enforcement. | Phases 8D–8L | No authoritative live collector stamps and enforces freshness. | 9B | Yes | Controlled stale, future-dated, mixed-origin, and excessive-skew cases. |
| RC-F1 | Normalize Python and Ansible runtime evidence before parity comparison. | `PARTIALLY_IMPLEMENTED` | `tests/release/scenarios/runtime_parity.py`, `tests/release/orchestrator.py` | `tests/release/scenarios/test_runtime_parity.py`, `tests/release/test_orchestrator.py::test_normalized_runtime_sources_populates_argocd_management_from_reports_and_pause_markers` | Issue #121 F | Normalizers exist, but orchestrator source aggregation is capability/stream keyed and can overwrite evidence from different scenarios. | 9F | Yes | Preserve scenario and segment identity through normalization. |
| RC-F2 | Require scenario-aware Python/Ansible parity for mutation and final state. | `MISSING` | `tests/release/scenarios/runtime_parity.py`, `tests/release/orchestrator.py` | No test proves same-scenario live Python/Ansible results across role transitions. | Issue #121 F | Comparisons use coarse or synthetic scenario identifiers and do not merge segment-specific live evidence. | 9F | Yes | Normalized evidence for the exact Python forward and Ansible reverse scenarios, each bound to its segment. |
| RC-G1 | Redact controller artifacts and reject unsafe controller payloads. | `NON_LIVE_ONLY` | `tests/release/lab_controller/artifacts.py`, `tests/release/lab_controller/read_only_live_transport.py` | `tests/release/test_lab_controller_profiles_artifacts.py`, `tests/release/test_lab_controller_phase8j_read_only_live_transport.py` | Phases 1–8J | Recursive controller sanitization is well tested with synthetic payloads; no real live bundle has been audited. | 9B | Yes | Gated live read artifacts with realistic unsafe responses and independent content audit. |
| RC-G2 | Recursively scan every release artifact, including JSON and nested directories. | `PARTIALLY_IMPLEMENTED` | `tests/release/reporting/artifacts.py`, `tests/release/reporting/redaction.py` | `tests/release/reporting/test_artifacts.py`, `tests/release/reporting/test_redaction.py` | Issue #121 G | Text writers scan selected content, but `write_json()` writes directly and no final recursive completeness audit proves that every file was scanned. | 9B and later | Yes | Inject secrets into JSON/nested/unregistered files and prove publishability is blocked. |
| RC-G3 | Fail certification on redaction failure and record provenance/eligibility. | `PARTIALLY_IMPLEMENTED` | `tests/release/reporting/summary.py`, `tests/release/orchestrator.py`, `tests/release/lab_controller/recovery.py` | `tests/release/reporting/test_summary.py`, `tests/release/test_lab_controller_phase5_recovery.py` | Issue #121 G/J | Fail-closed decisions exist, but live/non-live evidence classes, source revision, profile hash, and complete evidence origin are not unified. | 9B/9C | Yes | A failed redaction must force non-publishable, non-certification status at segment and run levels. |
| RC-H1 | Validate already-applied RBAC on both hubs, including deny checks. | `PARTIALLY_IMPLEMENTED` | `tests/release/checks/rbac_certification.py`, `tests/release/orchestrator.py` | `tests/release/checks/test_rbac_certification.py`, `tests/release/test_orchestrator.py::test_orchestrator_uses_profile_live_rbac_certification_scope` | Issue #121 H; prior RBAC certification work | Opt-in SAR implementation exists, but repository evidence is mocked and requires service accounts to exist already. `REQUIRES_LIVE_VALIDATION`. | 9C | Yes | Independent live SAR evidence for both hubs, expected allows, and forbidden permissions. |
| RC-H2 | Apply RBAC bootstrap and validate it end-to-end inside the disposable certification flow. | `MISSING` | `tests/release/adapters/ansible.py`, `tests/release/checks/rbac_certification.py` | Adapter tests prove dry-run/static behavior only. | Issue #121 H | `rbac-bootstrap` is dry-run and `rbac-bootstrap-live` is SAR-only. No apply-and-validate segment exists. | Separate later RBAC mutation slice | Yes for full RC | Dedicated mutating segment with reviewed manifests, apply evidence, SAR allows/denies, and reset posture. |
| RC-I1 | Provide an explicit manual RC workflow with operator inputs and artifact upload. | `MISSING` | `.github/workflows/` | No release-candidate live workflow test exists. | Issue #121 I | Existing workflows do not provide the required manual live RC path. Phase 9A forbids workflow changes. | Later workflow slice | Yes for full RC | Workflow dispatch security review, protected environment, explicit inputs, retention, and no implicit live trigger. |
| RC-J1 | Emit the complete required release artifact bundle. | `PARTIALLY_IMPLEMENTED` | `tests/release/reporting/artifacts.py`, `tests/release/reporting/render.py`, `tests/release/orchestrator.py` | `tests/release/test_orchestrator.py::test_orchestrator_writes_required_artifacts_with_fake_lab`, `tests/release/reporting/test_render.py` | Issue #121 J | Required files exist, but per-cycle timing, segment provenance, complete redaction audit, compatibility evidence, and recovery execution evidence are absent. | 9B–9F and later | Yes | Schema/contract validation against a gated live segment bundle. |
| RC-J2 | Produce a deterministic final GO/NO-GO summary from all blockers. | `PARTIALLY_IMPLEMENTED` | `tests/release/reporting/summary.py`, `tests/release/lab_controller/recovery.py` | `tests/release/reporting/test_summary.py`, `tests/release/test_lab_controller_phase5_recovery.py` | Issue #121 J | Framework and controller each summarize their own modeled evidence; no authoritative merged live decision exists. | 9C/9F | Yes | One controller-owned final decision with immutable references to all component evidence. |
| RC-K1 | Provide a two-hub, one-to-three-managed-cluster lab topology model. | `NON_LIVE_ONLY` | `tests/release/kustomize/` | Static render/contract tests under `tests/release/` | Issue #123; PR #124, commit `da2005080bcb9623d0fd976383155cfd8a7204d4` | Fixtures are intentionally static and are not applied or server-side validated. | 9D | Yes for lab bootstrap | Declarative live bootstrap artifacts kept separate from certification evidence. |
| RC-K2 | Classify GitOps ownership, hostile reconciliation, and ApplicationSet parent coordination. | `NON_LIVE_ONLY` | `tests/release/lab_controller/gitops.py`, `tests/release/kustomize/` | `tests/release/test_lab_controller_phase8pq_gitops.py` | Issue #125; PR #126, commit `1dbe543dec94c52981da803a35a974ca6a8f095d` | Static fixture parsing only; artifacts explicitly deny live certification status. | 9B/9C | Yes for GitOps lanes | Live Application/resource tracking and ApplicationSet parent/child evidence. |
| RC-K3 | Discover live Argo CD capability and validate proposed coordination server-side. | `DESIGNED_NOT_IMPLEMENTED` | `docs/development/lab-role-controller-spec.md`, `tests/release/lab_controller/gitops.py` | Static CRD fixture tests only. | Issue #125 follow-up | No live CRD/schema/version read, `automated.enabled` capability proof, or server-side dry-run validation. | 9B/9C; mutation deferred | Yes | Unknown capability/ownership must block; supported capability needs CRD and server-side proof. |
| RC-K4 | Generate a role-aware profile and reject identity/role/managed-cluster drift. | `NON_LIVE_ONLY` | `tests/release/lab_controller/profiles.py` | `tests/release/test_lab_controller_phase2_profiles.py`, `tests/release/test_lab_controller_profiles_artifacts.py` | Phase 2 implementation after commit `c471f900` | Hash and drift checks use modeled config; profiles lack issued/expiry evidence and live discovery binding. | 9C | Yes | Generate from fresh live observations and reject it after the Phase 9E role transition. |
| RC-K5 | Bind generated profiles to time, scenario, release stream, source revision, compatibility decision, and artifact directory. | `MISSING` | `tests/release/lab_controller/profiles.py` | Existing Phase 2 tests do not cover timestamps, expiry, revision, or compatibility evidence. | Issue #121 follow-up | Current hash covers profile content, identity hashes, managed clusters, roles, and configured artifact root, but not the full live binding. | 9C | Yes | Tamper and expiry tests for every binding; profile is one-use and per-segment. |
| RC-L1 | Select only officially supported OCP/ACM/OADP/GitOps combinations and distinguish historical lanes. | `MISSING` | Release profiles contain versions but no compatibility decision contract. | No compatibility-evidence contract test exists. | Issue #121 E | No current-source compatibility evidence or controller gate exists. | 9C; inputs prepared externally | Yes | Official-source audit with access dates; reject expired, contradictory, or unapproved combinations. |
| RC-L2 | Bootstrap/reset an initially empty disposable lab without conflating preparation and certification. | `MISSING` | Static `tests/release/kustomize/` models only | Static fixture tests only | Issue #123 follow-up | No live bootstrap controller, import path, reset segment, or preparation artifact contract. | 9D | Yes for reproducible full RC | Separate mutating bootstrap segments and operator-proven known-state handoff. |
| RC-L3 | Keep the Agent subordinate to the Python controller. | `IMPLEMENTED_AND_PROVEN` | `docs/development/lab-role-controller-agent-instructions.md`, `scripts/release/run_lab_role_controller.py` | `tests/test_documentation_guardrails.py::test_lab_role_controller_agent_instructions_document_non_live_authority_boundary`, `tests/release/test_lab_controller_phase7a_cli.py` | Phase 7A/7B commits `3b42351b`, `a36da8e8` | Future live instructions must retain this boundary. | All phases | Yes | Validator confirms no Agent path can authorize, retry, recover, or relabel evidence. |
| RC-L4 | Produce final RC proof satisfying every Issue #121 definition-of-done item. | `BLOCKED` | Entire release framework and controller surface | `tests/release/` | Issue #121 remains open | Cycles, cooldown, budgets, live segmented execution, recovery, comprehensive redaction, bootstrap certification, workflow, and scenario parity remain incomplete. | 9B onward and deferred slices | Yes | Independent end-to-end audit; Issue #121 must remain open until every blocker is proven. |

Totals at the Phase 9A base: `IMPLEMENTED_AND_PROVEN=5`, `PARTIALLY_IMPLEMENTED=13`,
`NON_LIVE_ONLY=9`, `DESIGNED_NOT_IMPLEMENTED=2`, `MISSING=9`, `SUPERSEDED=0`, `BLOCKED=2`.

## Authoritative live trust boundary

### Ownership

| Decision or activity | Authority | Inputs | Fail-closed result |
| --- | --- | --- | --- |
| Raw cluster reads and response adaptation | Future `tests/release/lab_controller/live_discovery.py` behind a typed, bounded client | Explicit runtime handles and allowlisted read queries | Apply the retry state machine below; never infer missing facts or retain evidence across attempts |
| Physical identity decision | Python lab controller identity decision engine | Raw identity observations plus enrolled expected fingerprints | Mismatch/duplicate is `NO_GO`; unreadable is blocking |
| Logical role and exact managed-cluster decision | Python lab controller role decision engine | Fresh normalized ACM, backup/restore, and managed-cluster evidence | Ambiguity is `NO_GO` or `RECOVERY_REQUIRED`; never default a role |
| Compatibility approval | External compatibility evidence generator plus controller validator | Current official sources, versions, support category, expiry, hash | Missing, expired, contradictory, or unapproved evidence is `BLOCKED` |
| Scenario support and command construction | Existing catalog and release framework adapters | One generated role-aware profile and one selected scenario | Unknown/unsupported pair is `BLOCKED` |
| Mutation authorization | Python lab controller | All Phase 9C evidence, operator gate, fresh profile | Any missing gate is `BLOCKED`; no adapter invocation |
| Recovery decision | Python lab controller | Fresh post-failure discovery and mutation-start marker | No automatic recovery; issue `RECOVERY_REQUIRED` |
| Reporting and normalized scenario evidence | Existing release framework, merged by the controller | Stream results, segment provenance, redaction audit | Redaction or provenance failure is `NO_GO` and ineligible |
| Explanation and orchestration | Agent/Codex | Controller commands and redacted artifacts only | Agent stops; it cannot override or mutate |

### 1. Physical hub identity

`hub-a` and `hub-b` are stable inventory labels only. Kubeconfig paths, current contexts, API URLs, and display names
are locators, not identity proof.

Each physical hub must be enrolled with a redacted expected identity fingerprint. A fresh observation must contain:

1. the `kube-system` Namespace UID;
2. the OpenShift `Infrastructure` object UID and `status.infrastructureName`;
3. a SHA-256 fingerprint of the API trust anchor used for that connection; and
4. the `ClusterVersion` object UID plus observed OpenShift version as corroborating evidence.

The publishable artifact carries only hashes and safe version fields. The raw API endpoint, certificate, kubeconfig,
credential, and private infrastructure identifier remain runtime-only.

All required fields must be readable and must agree with the enrolled record. The two labels must have distinct
identity tuples. Handling is deterministic:

- one label matching the other label's expected fingerprint: `NO_GO`, suspected swap;
- duplicate live identity for both labels: `NO_GO`;
- changed UID, trust anchor, or infrastructure identity: `NO_GO` and explicit operator re-enrollment; never silently
  update the expected record;
- unreadable required identity with no authoritative observation: `CONTACT_NOT_ESTABLISHED`; a typed transport failure
  may be reported as `INFRA_RETRYABLE` only under the bounded retry state machine below;
- any incomplete, stale, mismatched, duplicate, ambiguous, or contradictory identity observation:
  `PARTIAL_OR_CONFLICTING_EVIDENCE`; never an ordinary transport retry;
- identity loss or change after mutation was authorized: `RECOVERY_REQUIRED`;
- context-name agreement without the required tuple: `BLOCKED`.

### 2. Logical role mapping

Exactly one physical hub must be proven current primary and the other current secondary. The controller derives this
decision from normalized observations; the discovery adapter does not assign roles.

Primary proof requires all of:

- MultiClusterHub installation/status consistent with an operating hub;
- an enabled, non-paused BackupSchedule with recent successful backup evidence;
- the exact expected managed-cluster set owned by that hub and reporting required availability/connection conditions;
- no active restore state that contradicts current ownership; and
- no conflicting primary evidence on the other hub.

Secondary proof requires all of:

- MultiClusterHub installation/status consistent with the planned secondary role;
- BackupSchedule absent or explicitly paused according to the scenario's known state;
- passive/sync Restore evidence that is recent and non-terminally-failed when that method requires it;
- no proof that the secondary actively owns the expected managed-cluster set; and
- no conflicting active-primary signal.

BackupSchedule or Restore presence alone is insufficient. Required evidence families must agree:

- both hubs active: `NO_GO` for suspected split brain;
- neither active with both hubs readable: `RECOVERY_REQUIRED`;
- exactly one active but secondary evidence contradicts it: `RECOVERY_REQUIRED`;
- partial or unknown role evidence before mutation: `PARTIAL_OR_CONFLICTING_EVIDENCE`, resulting in `BLOCKED`,
  `NO_GO`, or `RECOVERY_REQUIRED` according to the evidence; never an ordinary transport retry;
- role evidence changes during the authorization window: invalidate the profile and restart discovery.

### 3. Discovery authority, origin, and freshness

Phase 9B introduces one raw adaptation boundary, provisionally
`tests/release/lab_controller/live_discovery.py`. It consumes structured allowlisted requests through an injected
typed client and returns raw observations. It must not call scenario adapters, infer roles, authorize mutation, or
write unredacted payloads.

Every observation records:

- physical label and hashed enrolled identity reference;
- collector and evidence-source type;
- query/resource family, API group/version/kind, namespace when non-sensitive, object UID, resourceVersion, and
  server response timestamp;
- collection start/end timestamps in UTC;
- controller source revision and collector contract version;
- redaction decision and raw-response retention status.

Reads are `get`/`list` only, use explicit per-request timeouts, a fixed resource/query allowlist, deterministic
pagination, bounded total results, and a controller-enforced total discovery deadline. Every list follows the
server-provided continuation token until the server proves completion. Repeated/invalid continuation tokens, reaching
the result/deadline bound before completion, or any inability to prove that the list is complete is `BLOCKED`; a
truncated list can never satisfy identity, role, inventory, or absence-of-extra-clusters assertions. No watch, exec,
proxy, logs, arbitrary URL, shell string, or inherited environment is permitted. Concrete timeout and age durations
become normative only when the implementing slice provides tests and an operational rationale; Phase 9A does not
guess a universal five-minute validity rule.

Freshness is a versioned controller policy, not an unqualified duration. Every evidence/profile artifact records
`freshness_policy_id`, `freshness_policy_version`, `collected_at`, `profile_issued_at`, `expires_at`,
`configured_max_age`, `controller_hard_max_age`, and a one-use authorization nonce. A release profile may shorten
`configured_max_age`, but it cannot extend it beyond `controller_hard_max_age`. A missing, invalid, or unrecognized
policy fails closed. `expires_at` is computed from `profile_issued_at` under the validated policy, while evidence age
is measured from collection completion; neither value permits mutation without immediate fresh revalidation.

The controller uses its local trusted wall clock for UTC artifact timestamps and a monotonic clock for elapsed-time
enforcement within one process. Server timestamps are corroborating evidence, not a replacement for the controller
clock. Clock rollback, excessive policy-defined skew, unavailable/untrusted time provenance, future-dated evidence,
mixed-run or mixed-origin evidence, and missing timestamps fail closed. Cross-process or cross-host reuse is
prohibited unless a later implementation defines and independently validates a trusted handoff protocol; no such
handoff exists in Phases 9B-9F.

The nonce consumption and immutable `mutation_started` state are persisted together in one durable transactional or
compare-and-swap journal record before the mutating adapter handoff. The handoff is prohibited unless that record is
durable. Recovery treats the presence of either field in a legacy/partial record, an indeterminate journal result, or
a failed journal read as `MUTATION_STARTED` and `RECOVERY_REQUIRED`; it never reconstructs pre-mutation authority.
Failed authorization, adapter-handoff failure, role or evidence change, retry, expiry, or prior nonce consumption
invalidates the evidence set, profile, and authorization. Immediate pre-mutation identity, role, managed-cluster,
GitOps, RBAC, backup/restore, compatibility, freshness, and nonce revalidation is mandatory regardless of artifact
age.

### 4. Managed-cluster proof

Live certification supports exactly one, two, or three expected managed clusters. Before hashing or comparison, the
controller excludes `local-cluster` and canonically sorts both expected and observed entries by managed-cluster name,
then UID hash. The generated profile binds that canonical exact set and its hash; count or caller-provided ordering is
insufficient. `local-cluster` is handled separately and cannot satisfy an expected managed-cluster entry.

For every expected cluster the controller requires:

- ManagedCluster name and UID;
- membership/ownership evidence on the active hub;
- `ManagedClusterConditionAvailable=True`;
- accepted/joined/connection conditions required by the supported ACM version;
- observed cluster identity/version where available; and
- absence of unexpected managed clusters in the certification scope.

The secondary must not independently present the same set as actively owned. Missing, extra, renamed, unavailable,
disconnected, duplicated, or ownership-conflicting entries invalidate the role-aware profile. A deliberate change to
the expected set requires new operator input, compatibility/readiness validation, fresh discovery, and a new profile;
it cannot be accepted as drift.

### 5. Backup, restore, and ACM evidence

The controller collects and correlates:

- MultiClusterHub UID, generation, observed generation, phase/conditions, and relevant version;
- BackupSchedule UID, generation, paused/enabled state, phase/conditions, last successful backup reference and time;
- relevant Backup and BackupStorageLocation health/recency;
- Restore UID, generation, phase/conditions, restore type, activation/passive intent, started/completed time, and
  referenced backup; and
- managed-cluster ownership/connection observations.

Scenario policy defines maximum acceptable backup/restore age. All generation/observed-generation and timestamp
relationships must be coherent. A stale successful object cannot override a newer failed or contradictory object.
If active/passive conclusions disagree across evidence families, no weighted guess is allowed:
`RECOVERY_REQUIRED` when the lab is readable but state is unsafe/unknown, otherwise `BLOCKED`.

### 6. Argo CD and OpenShift GitOps

The live collector must discover:

- OpenShift GitOps operator/subscription/CSV version;
- Argo CD/Application/ApplicationSet CRD served versions and structural schema;
- the live schema capability for `spec.syncPolicy.automated.enabled`;
- Application resource tracking evidence for every ACM object in scope;
- ApplicationSet owner references and generated-child relationship;
- effective automated sync, self-heal, prune, skip-reconcile, pause, and health/sync status; and
- server-side validation of any proposed coordination patch without applying it.

An ApplicationSet child is never an independent authority. Coordination must occur at the proven parent/template
level, or the lane blocks. Directly patching a child is forbidden because the parent may recreate or overwrite it.

Classification:

- observe-only/no ACM ownership: safe to observe; no coordination mutation;
- ACM ownership with autosync off and no hostile self-heal/prune: potentially safe after server-side validation;
- skip-reconcile or an approved controller-owned pause strategy: safe only when its live effect and restoration
  contract are proven;
- hostile reconciliation, self-heal, prune, parent regeneration, or uncoordinated ownership: `NO_GO`;
- unknown owner, tracking method, parent, schema, capability, or effective sync semantics: `BLOCKED`.

Static fixture capability evidence from Phase 8P/8Q remains non-live. The controller must query the live CRD/schema
and server validation; it must not assume that `automated.enabled` is supported merely because a checked-in fixture
contains it.

### 7. Role-aware profile binding

A generated live profile is one-use and binds:

- both physical labels to their fresh identity fingerprint hashes;
- exactly one logical primary and secondary;
- exact managed-cluster names/UID hashes;
- one scenario ID, its mutation classification, and one release stream;
- expected initial and final role states;
- source revision and clean-checkout proof;
- immutable compatibility-evidence hash;
- discovery evidence-set hash;
- unique segment ID and artifact directory;
- issue/approval reference;
- freshness-policy identifier/version, collection/profile timestamps, configured/controller maximum ages,
  one-use nonce, and consumption state; and
- profile schema/contract version and profile SHA-256.

The validated freshness policy determines expiry; no release profile may extend the controller hard maximum.
Immediate pre-mutation revalidation is mandatory regardless of age. Any role transition, evidence change, identity
change, managed-cluster drift, scenario/stream change, source revision change, compatibility-evidence change,
artifact-directory change, failed authorization, retry, adapter-handoff failure, expiry, or nonce consumption
invalidates the profile.

The raw generated profile stays outside version control and outside the default `.release/` output. The operator must
supply a private runtime directory outside the checkout, created with owner-only permissions. Publishable artifacts
contain only the profile hash, redacted binding summary, timestamps, and a non-dereferenceable logical reference.
Kubeconfig paths, contexts, endpoints, credentials, and raw private identifiers are never published.

### 8. Segment authorization and lifecycle

The complete lifecycle is:

```text
CREATED
  -> DISCOVERING
  -> IDENTITY_PROVEN
  -> ROLE_AND_READINESS_PROVEN
  -> PROFILE_BOUND
  -> MUTATION_AUTHORIZED
  -> MUTATION_STARTED
  -> VERIFYING_FINAL_STATE
  -> FINAL_STATE_PROVEN
  -> ARTIFACTS_REDACTED
  -> PASS
```

Any failure transitions to `NO_GO`, `RECOVERY_REQUIRED`, `INFRA_RETRYABLE`, or `BLOCKED` and ends the segment. At
most one catalog scenario classified `lab_mutating` is allowed. Non-mutating prerequisites and verification checks
may surround it, but bootstrap, reset, recovery, failure injection, and decommission each count as their own
lab-mutating scenario.

Nonce consumption and `mutation_started` are durably recorded together in the crash-consistent journal before control
is handed to a mutating adapter, not after the adapter reports its first successful API mutation. Any partial,
indeterminate, or unreadable record is treated as mutation started. Crossing this handoff permanently disables
automatic retry for the segment even when the eventual error looks transient. A later segment cannot mutate until
fresh discovery independently proves the prior final state and a newly generated profile passes all gates.

### 9. Retry state machine

Retry classification is deterministic and cannot preserve mutation authority across attempts:

1. `CONTACT_NOT_ESTABLISHED`
   - No authoritative observation was obtained, no identity or role proof exists, no profile or mutation
     authorization exists, and no mutation-start marker exists.
   - Only typed transport/infrastructure failures qualify: connection timeout/refusal/reset, DNS resolution failure,
     TLS handshake failure before a valid response, or controller/tool launch/resource failure before a response.
     Authentication/authorization denial, malformed or forbidden payloads, and safety assertions do not qualify.
   - The budget is the initial attempt plus at most one operator-authorized retry, both within the controller policy's
     discovery deadline. The retry starts a new discovery run and cannot reuse any observation.
2. `PARTIAL_OR_CONFLICTING_EVIDENCE`
   - Any incomplete, stale, mismatched, duplicate, ambiguous, malformed, mixed-origin, or contradictory identity,
     role, managed-cluster, GitOps, RBAC, backup/restore, compatibility, or freshness evidence exists.
   - It is never an ordinary transport retry. The controller emits `BLOCKED`, `NO_GO`, or `RECOVERY_REQUIRED`
     according to whether the evidence is absent/invalid, definitively unsafe, or shows a state requiring repair.
3. `PROOF_COMPLETE_PRE_MUTATION`
   - Identity and initial state were proven, but a later infrastructure failure occurred before
     `MUTATION_STARTED`.
   - The controller may report `INFRA_RETRYABLE`, but it invalidates the evidence set, profile, nonce, and
     authorization. The only permitted rerun starts again at `CONTACT_NOT_ESTABLISHED` with fresh discovery and a new
     authorization; the previous proof is never reused.
4. `MUTATION_STARTED`
   - Automatic retry is prohibited. Any failure is `RECOVERY_REQUIRED`.
   - Fresh discovery and an explicitly authorized recovery segment are required; the failed segment cannot resume.

Every attempt artifact records `retry_state`, `transport_attempt`, `transport_attempt_limit`,
`retry_reason_code`, `retry_budget_consumed`, `contact_established`, authoritative-observation count, invalidated
evidence/profile hashes, and the next permitted action. After every retry, all discovery is fresh. No retry path can
bypass physical identity proof because `CONTACT_NOT_ESTABLISHED` has no mutation authority,
`PARTIAL_OR_CONFLICTING_EVIDENCE` is not retryable, and `PROOF_COMPLETE_PRE_MUTATION` destroys its prior proof before
starting over.

### 10. Decision semantics

| Decision | Mandatory evidence and meaning | Permitted next action |
| --- | --- | --- |
| `PASS` | Every required check passed; any authorized mutation completed; expected final physical identity, logical roles, managed clusters, ACM/backup/restore/GitOps state are freshly proven; all artifacts passed complete redaction; provenance and eligibility are valid. | A later segment may begin only with new discovery and profile binding. |
| `NO_GO` | A definitive certification or safety assertion failed, such as identity mismatch, split brain, unexpected cluster ownership, hostile GitOps, unsupported scenario, failed final state, or failed redaction. | Stop. Human investigation; no retry or recovery mutation from this segment. |
| `RECOVERY_REQUIRED` | Mutation handoff occurred, or readable evidence shows an ambiguous/partial/unknown lab state requiring controlled repair. | Stop. Fresh rediscovery and a separately issued, explicitly authorized recovery segment are required. |
| `INFRA_RETRYABLE` | A typed transport/infrastructure failure occurred in `CONTACT_NOT_ESTABLISHED`, or after `PROOF_COMPLETE_PRE_MUTATION` but before `MUTATION_STARTED`; no safety assertion failed. Any prior evidence/profile/authorization is invalidated. | No Agent retry. The controller may offer one operator-authorized retry within the controller policy's bounded discovery deadline. The attempt restarts fresh discovery with no reused proof. |
| `BLOCKED` | Required input, tool, model, compatibility decision, approval, capability, freshness, or provenance is absent/invalid before mutation. | Correct the prerequisite and start a new discovery run; never infer a value. |

`INFRA_RETRYABLE` is never inferred from an exception string. During or after mutation, transient transport failure is
`RECOVERY_REQUIRED`, because the physical state may have changed.

### 11. Artifact and certification eligibility

Every artifact records an evidence class from an allowlist:

- `non_live_fake`
- `non_live_dry_run`
- `non_live_local_harness`
- `static_fixture`
- `live_read_only`
- `live_mutating_segment`
- `LAB_PREPARATION_ONLY`
- `diagnostic_live`

Only the explicitly gated future live entrypoint can emit a `live_*` class. Constructors and writers reject attempts
to relabel fake, dry-run, local-harness, static-fixture, or ordinary pytest unit output. Read-only live evidence proves
contact/observation only and cannot by itself make a run live-certification eligible.

`LAB_PREPARATION_ONLY` is the only allowed preparation class. Its writer requires
`purpose=lab_preparation`, `evidence_class=LAB_PREPARATION_ONLY`, `certification_eligible=false`,
`live_certification_evidence=false`, and `may_have_mutated_lab=true`, plus the preparation plan/reference, source
revision, controller decision, physical identity bindings, preparation actions, final observed state, redaction
status, writer/schema version, and `preparation_profile_hash`. That hash binds the preparation plan/reference, source
revision, physical identity bindings, expected action set, and preparation artifact namespace; it is not a
certification profile hash. The writer uses a separate preparation artifact namespace/directory. Writers accept only
allowlisted evidence classes and reject relabeling or merging preparation output into certification scenario results.
A certification run may reference preparation provenance, but must freshly discover identity, roles, managed
clusters, GitOps, RBAC, backup/restore, and compatibility afterward. Redaction failure blocks publication. A
successful preparation artifact never authorizes mutation or certification.

Minimum segment artifacts:

- controller/segment manifest with source revision, clean status, issue/approval reference, timestamps, evidence class;
- redacted raw-observation inventory and evidence-origin records;
- physical identity decision;
- logical-role and managed-cluster decision;
- ACM, backup/restore, RBAC, GitOps, and compatibility decision summaries;
- generated-profile hash/redacted binding and discovery hash;
- mutation-authorization record and immutable `mutation_started` marker;
- scenario invocation/result bound to segment/scenario/stream;
- final-state proof;
- recursive artifact inventory and redaction audit; and
- segment decision.

Minimum run artifacts add ordered segment references, role-transition graph, scenario-specific normalized parity,
recovery state, eligibility decision, final summary, and human-readable report.

Every artifact includes schema version, run/segment ID, source revision, the evidence-class-appropriate binding hash
(`profile_hash` for certification/diagnostic artifacts or `preparation_profile_hash` for
`LAB_PREPARATION_ONLY`), evidence-source type, collector/adapter version, started/completed timestamps, and parent
artifact hashes. Failed redaction makes the bundle non-publishable and non-certification-eligible. A dirty checkout
blocks certification. A future explicit diagnostic override may run read-only diagnostics, but the artifact class is
`diagnostic_live` and eligibility remains false.

### 12. Controller versus Agent authority

The Python lab controller owns physical identity, logical roles, lab readiness, profile generation/freshness,
mutation authorization, mutation-start recording, final-state proof, recovery decisions, and final GO/NO-GO.

Agent/Codex instructions are orchestration and explanation only. The Agent may invoke the documented controller
entrypoint and summarize redacted artifacts. It must not:

- issue ad hoc live commands;
- choose a mutation outside the controller's selected segment;
- modify profiles or evidence to make a gate pass;
- reinterpret decisions;
- retry `NO_GO`, `RECOVERY_REQUIRED`, or post-mutation failures;
- select or execute recovery commands; or
- label evidence as live or certification-eligible.

## Lab bootstrap boundary

The target lab contains two ACM hubs and one to three managed clusters, typically SNO OpenShift clusters. It may start
empty. GitOps-managed ACM resources must be testable in both safe and hostile reconciliation modes.

Preparation and certification are separate authorities and artifact classes:

| Activity | Boundary | Mutation treatment | Evidence eligibility |
| --- | --- | --- | --- |
| Infrastructure provisioning | External infrastructure system or future audited bootstrap controller | External operator-proven preparation, or its own mutating segment | `LAB_PREPARATION_ONLY` |
| OpenShift version selection/install | External provisioning plus compatibility decision | Separate preparation segment | `LAB_PREPARATION_ONLY` |
| ACM installation on each hub | Future Phase 9D declarative bootstrap | Separate mutating segment per known-state unit | `LAB_PREPARATION_ONLY` |
| Managed-cluster bootstrap/import | Future Phase 9D | Separate mutating segment; exact set re-proven afterward | `LAB_PREPARATION_ONLY` |
| Backup/OADP prerequisites | Future Phase 9D | Separate mutating segment | `LAB_PREPARATION_ONLY` |
| GitOps operator installation | Future Phase 9D | Separate mutating segment | `LAB_PREPARATION_ONLY` |
| Application/ApplicationSet creation | Future Phase 9D with parent ownership proof | Separate mutating segment | `LAB_PREPARATION_ONLY` |
| Kustomize desired-state application | Future Phase 9D | Separate mutating segment; server-side validation first | `LAB_PREPARATION_ONLY` |
| Known-state preparation/reset | Python controller, or explicitly external operator-proven handoff | One controlled mutating segment | `LAB_PREPARATION_ONLY`; may establish an entry state, never scenario certification |
| Live release certification | Python controller plus existing release framework | One selected release mutation per segment | Eligible only after all live gates and redaction pass |

Preparation writers enforce the `LAB_PREPARATION_ONLY` contract above. Preparation artifacts never qualify as release
certification artifacts, cannot be relabeled or merged as passing scenario evidence, and can be referenced only as
provenance. After any bootstrap/reset mutation, Phase 9B discovery must independently re-establish identity and Phase
9C must freshly prove roles, managed clusters, GitOps, RBAC, backup/restore, compatibility, and known state before a
certification segment can be authorized.

## Version and support policy

Phase 9A deliberately does not hard-code a guessed compatibility matrix. Compatibility is time-sensitive and is
created as external evidence immediately before a future live run.

Official sources located and assessed on 2026-07-17:

- [OpenShift Container Platform life cycle](https://access.redhat.com/support/policy/updates/openshift)
- [OpenShift non-current life-cycle policy](https://access.redhat.com/support/policy/updates/openshift_noncurrent)
- [OpenShift Operator life cycles](https://access.redhat.com/support/policy/updates/openshift_operators)
- [Red Hat Advanced Cluster Management life cycle](https://access.redhat.com/support/policy/updates/advanced-cluster-management)
- [ACM 2.12 support matrix](https://access.redhat.com/articles/7086905)
- [ACM 2.14 support matrix](https://access.redhat.com/articles/7120842)
- [ACM 2.15 support matrix](https://access.redhat.com/articles/7133095)
- [ACM 2.16 support matrix](https://access.redhat.com/articles/7136928)
- [ACM product advisories, including 2.17 GA](https://access.redhat.com/products/red-hat-advanced-cluster-management-kubernetes/)
- [OpenShift 4.20 backup and restore / OADP documentation](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/observability/backup_and_restore/index)
- [OpenShift GitOps 1.18 release notes and compatibility matrix](https://docs.redhat.com/en/documentation/red_hat_openshift_gitops/1.18/html-single/release_notes/index)

The sources demonstrate why a static “ACM 2.12–2.17 is supported” assertion is unsafe: product, platform, and
Operator lifecycles are independent, support pages update on different cadences, and a GA advisory is not itself a
complete four-product compatibility decision.

Before Phase 9C authorization, an operator-controlled compatibility evidence generator must:

1. read the current OCP lifecycle, ACM lifecycle and exact ACM support matrix, OADP release notes/support statement,
   OpenShift Operator lifecycle, and exact GitOps compatibility matrix;
2. record canonical URL, title, retrieved-at UTC time, page update/publication time when exposed, and content hash;
3. record exact OCP, ACM, MCE, OADP, and GitOps versions/channels plus architecture;
4. classify the combination as `CURRENTLY_SUPPORTED`, `MAINTENANCE_OR_EXTENDED`, `HISTORICAL_REGRESSION_ONLY`, or
   `UNSUPPORTED`;
5. state subscription/EUS assumptions and any unresolved contradiction;
6. expire the decision after 24 hours or earlier when a source explicitly changes; and
7. sign or hash the immutable evidence document.

The generated live profile carries the immutable compatibility evidence hash, classification, and expiry, not an
executable hard-coded matrix. `UNSUPPORTED` is `NO_GO`. Missing, expired, inaccessible, contradictory, or unverified
official evidence is `BLOCKED`. `HISTORICAL_REGRESSION_ONLY` is never an RC certification lane and must use disposable
infrastructure plus an ineligible artifact class. `MAINTENANCE_OR_EXTENDED` requires explicit proof that the
operator's entitlement and architecture qualify.

## Phased implementation plan

Every slice requires a dedicated issue, a fresh branch from current `origin/ansible`, the builder/independent
validator/PR-comment-resolver workflow, and a separate reviewable PR. No slice may silently absorb deferred work.

### Phase 9B — Read-only live discovery and physical identity proof

- **Purpose:** add the first real, typed read-only adapter and prove both physical hubs without enabling mutation or
  certification from discovery alone.
- **Prerequisites:** Phase 9A merged and independently validated; fresh base; explicit operator runtime handles;
  reviewed Phase 8J guardrails. Operator lab readiness for the identity-only exit gate is summarized in
  [`docs/development/lab-phase9-readiness-checklist.md`](../development/lab-phase9-readiness-checklist.md) Tier A.

- **Mutation boundary:** none. Any mutating verb, adapter, L10 use, or mutation flag blocks before contact.
- **Expected files/modules:** new `tests/release/lab_controller/live_discovery.py`; minimal integration in
  `read_only_live_transport.py`/`read_only_backend.py`; controller artifact schema/writer updates; focused tests and
  design docs. No production CLI/collection changes.
- **Tests:** typed real-client adapter with fake API server; timeouts/bounds; complete multi-page reads and
  repeated/invalid/truncated pagination rejection; swapped/duplicate/changed/unreadable identity;
  stale/skewed/mixed-origin evidence; one-to-three canonically ordered managed-cluster inventory; unsafe
  payload/redaction; no-mutation static guards.
- **Artifact contract:** `live_read_only`, source/profile/config hashes, timestamps, origin, identity evidence hashes,
  query inventory, managed-cluster observations, recursive redaction audit; always
  `live_certification_evidence=false`.
- **Entry gate:** operator opt-in, clean revision, external runtime config, read-only gates, no ambient credential
  inheritance.
- **Exit gate:** both hub identities independently proven; ambiguity/mismatch tests pass; gated live read evidence
  independently reviewed. Discovery alone cannot claim certification.
- **Recovery posture:** no recovery. `INFRA_RETRYABLE` is available only under `CONTACT_NOT_ESTABLISHED` or
  `PROOF_COMPLETE_PRE_MUTATION`; the initial attempt plus one operator-authorized retry must restart fresh discovery
  within the controller policy's bounded deadline. Partial/conflicting evidence and safety mismatches are not
  retryable.
- **Protected-file boundary:** no runbook or `.claude/skills/**/*.skill.md` changes.
- **Parity impact:** none to production Python/Ansible behavior; evidence is stream-neutral.
- **Independent-validator evidence:** source guard audit, call trace proving only allowlisted reads, redacted real-read
  bundle, exact test output.
- **Blocks next slice when:** identity is not stable/distinct, evidence provenance/freshness is incomplete, redaction
  is not recursive, or any live claim is overstated.

### Phase 9C — Live known-state preflight and mutation-authorization decision

- **Purpose:** combine fresh identity, roles, exact managed clusters, ACM, backup/restore, RBAC, GitOps,
  compatibility, and profile freshness into a deterministic decision. It may compute authorization but cannot invoke
  a switchover.
- **Prerequisites:** Phase 9B exit gate; approved compatibility evidence process; live RBAC reads; GitOps ownership and
  schema discovery.
- **Mutation boundary:** no switchover, recovery, bootstrap, pause, patch, or apply. The output authorization token is
  non-executable in 9C.
- **Expected files/modules:** normalized live observation models; identity/role/readiness decision integration;
  role-aware profile vNext with freshness-policy/nonce/source/compatibility bindings; decision and artifact contracts.
- **Tests:** all evidence agreement/disagreement states; both/neither active; backup-only/restore-only false signals;
  one-to-three exact clusters; profile tamper/expiry/role flip; unsupported/expired compatibility; unknown GitOps
  capability/owner; SAR allow/deny; decision table coverage.
- **Artifact contract:** one redacted evidence graph and `PASS`, `NO_GO`, `RECOVERY_REQUIRED`, or `BLOCKED`.
  `INFRA_RETRYABLE` is limited to the retry state machine and records attempts, reason, budget, invalidation, and
  fresh-discovery provenance. No artifact may say mutation occurred.
- **Entry gate:** fresh Phase 9B evidence and clean revision.
- **Exit gate:** repeated deterministic preflight on a live known state yields the same decision and profile hash;
  freshness policy, clock failure, nonce reuse, all four retry states, evidence invalidation, and fresh-discovery
  cases pass; every negative fixture blocks as designed; independent validator approves the non-executable
  authorization boundary.
- **Recovery posture:** no automatic or live recovery.
- **Protected-file boundary:** unchanged.
- **Parity impact:** preflight evidence must remain stream-neutral; no production behavior change.
- **Independent-validator evidence:** decision truth table, real read-only bundle, profile binding/tamper proof, no
  mutating call trace.
- **Blocks next slice when:** any mandatory evidence is unproven, compatibility process is not current, or the
  authorization result can invoke mutation.

### Phase 9D — Lab bootstrap and known-state preparation

- **Purpose:** provide declarative, bounded, auditable preparation for initially empty clusters and known-state reset,
  while keeping preparation distinct from certification.
- **Prerequisites:** Phase 9C proof engine; disposable-lab declaration; official compatibility evidence; dedicated
  bootstrap design review.
- **Mutation boundary:** each infrastructure/ACM/import/OADP/GitOps/Kustomize/reset operation is a separate
  controller-owned mutating segment or explicitly external operator-proven preparation. No combined bootstrap run.
- **Expected files/modules:** separately reviewed bootstrap controller/adapters, declarative assets, preparation
  profiles outside version control, and preparation artifacts. Exact paths require the Phase 9D design.
- **Tests:** empty-cluster progression; idempotence; partial bootstrap; exact managed-cluster import; safe and hostile
  GitOps/ApplicationSet modes; reset refusal on unknown state; bootstrap/certification artifact separation.
- **Artifact contract:** `purpose=lab_preparation`, `evidence_class=LAB_PREPARATION_ONLY`,
  `certification_eligible=false`, `live_certification_evidence=false`, `may_have_mutated_lab=true`, preparation
  plan/reference, source revision, controller decision, physical identity bindings, preparation actions, final
  observed state, redaction status, writer/schema version, `preparation_profile_hash`, and final Phase 9B rediscovery
  reference. The writer uses a separate namespace/directory and rejects relabeling or merge into certification
  scenario results.
- **Entry gate:** disposable lab and one bootstrap segment explicitly authorized.
- **Exit gate:** operator-proven preparation plus fresh Phase 9B/9C identity, role, managed-cluster, GitOps, RBAC,
  backup/restore, compatibility, and known-state proof. Preparation provenance may be referenced but never counted as
  passing certification evidence.
- **Recovery posture:** controller decides stop/reset; no Agent recovery. Partial setup is
  `RECOVERY_REQUIRED`.
- **Protected-file boundary:** runbook and skills remain protected; any required operational change becomes a later
  operator-approved dependency.
- **Parity impact:** bootstrap is controller/lab infrastructure, not Python/Ansible switchover parity.
- **Independent-validator evidence:** declarative diff, mutation audit, clean reset proof, and proof that preparation
  artifacts are ineligible.
- **Blocks next slice when:** lab state cannot be reset/proven, GitOps parent authority is unknown, or bootstrap and
  certification evidence can be confused.

### Phase 9E — First single live mutating segment

- **Purpose:** execute exactly one explicitly selected Python passive-switchover segment.
- **Prerequisites:** 9B/9C proven; 9D preparation or external operator-proven known state; dedicated approval; clean
  source; current compatibility evidence; fresh role-aware profile.
- **Mutation boundary:** one `python-passive-switchover` adapter handoff. No automatic second mutation, recovery,
  failure injection, restore-only, decommission, or hostile GitOps lane.
- **Expected files/modules:** controller-gated live entrypoint; immutable authorization/mutation marker; existing
  Python adapter integration; final-state evidence merger; no production Python CLI behavior changes.
- **Tests:** gate ordering; no adapter call before authorization; atomic nonce-consumption/mutation-start journal
  durability before handoff; partial/indeterminate/unreadable journal recovery fails closed; transport failure after
  handoff becomes `RECOVERY_REQUIRED`; exact one-call invariant; final proof and redaction failure.
- **Artifact contract:** `live_mutating_segment`, full 9C inputs, operator authorization, one invocation, mutation
  marker, Python normalized evidence, final role/cluster/backup/GitOps proof, recursive redaction audit.
- **Entry gate:** immediate full revalidation under the recognized freshness policy, with unconsumed one-use nonce,
  matching profile-bound physical identity hashes and expected initial mapping.
- **Exit gate:** fresh observations match the authorized segment contract: profile/segment/scenario identity,
  authorized physical identity hashes, `expected_final_mapping`, expected managed-cluster ownership, expected
  scenario transition, and required post-mutation evidence; artifacts are redacted and the segment is `PASS`.
  Inventory labels and context names are display aliases only and cannot decide the exit state. A label-to-identity
  mismatch is blocking, no exit decision may compare only labels or contexts, and stale label mappings cannot be
  repaired by relabeling artifacts. Phase 9F may start only from this profile-bound proven state.
- **Recovery posture:** stop on ambiguity or failure. Any failure after handoff is `RECOVERY_REQUIRED`; recovery is a
  future separately authorized segment.
- **Protected-file boundary:** unchanged.
- **Parity impact:** no parity claim yet; evidence records Python scenario only.
- **Independent-validator evidence:** operator approval, controller trace, exact one mutation, final live proof,
  artifact audit.
- **Blocks next slice when:** final state is not independently proven, redaction fails, or any second mutation was
  attempted.

### Phase 9F — Ansible reverse segment and cross-stream parity

- **Purpose:** start only from Phase 9E's proven final state, run one Ansible reverse passive-switchover segment, and
  compare scenario-aware normalized evidence.
- **Prerequisites:** Phase 9E `PASS` from profile-bound physical identity and `expected_final_mapping` proof; fresh
  rediscovery; new role mapping/profile/approval; current compatibility evidence. Label-only or relabeled state is
  not a valid handoff.
- **Mutation boundary:** one `ansible-passive-switchover` adapter handoff. It is a new segment, never part of 9E.
- **Expected files/modules:** existing Ansible adapter integration; scenario/segment-preserving normalized evidence;
  parity merger; final controller decision.
- **Tests:** stale Phase 9E profile rejected; no handoff without fresh final-state proof; one Ansible call; normalized
  Python-forward/Ansible-reverse evidence retains scenario and role direction; mismatch blocks parity.
- **Artifact contract:** new `live_mutating_segment` bundle plus parent Phase 9E hash; final role proof; merged
  scenario-aware parity and run GO/NO-GO.
- **Entry gate:** independently proven Phase 9E state and fresh Ansible-bound one-use profile.
- **Exit gate:** fresh observations match the reverse segment's authorized physical identity hashes,
  `expected_final_mapping`, managed-cluster ownership, scenario/profile identity, and post-mutation evidence; both
  segment bundles are redacted and scenario-aware evidence passes the approved parity assertions.
- **Recovery posture:** identical to 9E; no automatic recovery or third mutation.
- **Protected-file boundary:** unchanged.
- **Parity impact:** first narrow parity claim, limited to the two exact passive-switchover scenarios and normalized
  fields actually proven.
- **Independent-validator evidence:** two separate authorization records, fresh role transition between them,
  Ansible final proof, scenario-specific comparison records.
- **Blocks later work when:** state handoff, normalization, redaction, or exact scenario parity is incomplete.

Restore-only, hostile-reconciliation mutation, checkpoint/resume, failure injection, soak, RBAC mutation,
decommission, full-restore, automated recovery, and full ping-pong certification remain deferred to separately
designed slices after 9F. Their presence in the catalog or static fixtures does not pull them into 9B–9F.

## Non-negotiable hard gates

1. Phase 9B cannot start until Phase 9A is merged and independently validated.
2. No Phase 9 slice starts from a stale base; fetch, ancestry, clean checkout, and exact base SHA are recorded.
3. Every slice has a dedicated issue and builder, independent-validator, and PR-comment-resolver/final-validator
   prompts.
4. No live mutation occurs before read-only identity and role proof is implemented and validated.
5. Ambiguous, duplicated, changed, or mismatched physical identity blocks mutation.
6. Missing/unrecognized freshness policy; untrusted/rolled-back clock; excessive skew; or stale, expired, unbound,
   reused, nonce-consumed, or tampered generated profiles block mutation.
7. One known-state segment contains at most one lab-mutating scenario.
8. No later segment mutates until fresh discovery proves the prior final state against the authorized physical
   identity hashes, `expected_final_mapping`, managed-cluster ownership, scenario transition, and profile/segment
   identity. Labels and context names are non-authoritative.
9. Unknown scenario classification fails closed.
10. Unknown Argo CD ownership, ApplicationSet parent authority, or capability evidence fails closed.
11. Failed or incomplete redaction blocks publishable and certification-eligible artifacts.
12. A dirty certification checkout is blocking; diagnostic override remains ineligible.
13. Fake, dry-run, static-fixture, and local-harness evidence cannot satisfy live gates.
14. No automatic recovery follows mutation. Fresh rediscovery and an explicitly authorized recovery segment are
    mandatory.
15. No Python/Ansible parity claim is made without scenario-specific normalized evidence.
16. No full RC-ready declaration is made until every remaining Issue #121 blocker is independently proven.
17. Bootstrap/reset/preparation mutation uses `LAB_PREPARATION_ONLY`, never inherits certification eligibility, and
    cannot be relabeled or merged into passing scenario evidence.
18. Read-only live evidence proves observation only, not release certification.
19. The Agent cannot authorize, mutate, recover, retry, relabel, or override.
20. Incomplete pagination, non-canonical managed-cluster inventory, or an indeterminate nonce/mutation journal blocks
    authorization; an indeterminate journal after authorization is `RECOVERY_REQUIRED`.

## Rejected approaches

- **One linear run over one static primary/secondary profile:** a successful switchover makes the mapping stale.
- **Trusting `hub-a`/`hub-b` labels:** they identify inventory slots, not clusters.
- **Trusting kubeconfig paths or context names:** they are mutable locators and can point to the wrong cluster.
- **Agent-selected ad hoc recovery:** it bypasses controller evidence, mutation accounting, and audit.
- **Retrying a failed mutating scenario without rediscovery:** the lab may have changed despite a transient-looking
  error.
- **Reusing pre-mutation proof after a transport retry:** every retry invalidates prior evidence, profile, nonce, and
  authorization and restarts discovery.
- **Using an unexplained fixed freshness duration:** validity is controlled by a versioned policy with a
  controller-enforced maximum, trusted-clock rules, nonce consumption, and mandatory immediate revalidation.
- **Relabeling preparation evidence as certification:** `LAB_PREPARATION_ONLY` remains in a separate namespace and is
  provenance only.
- **Treating fake, dry-run, static, or local-harness artifacts as live:** their evidence origin is not a live lab.
- **Assuming unknown GitOps capability is supported:** CRD/version/schema differences can change patch semantics.
- **Patching an ApplicationSet child without parent coordination:** the parent can immediately recreate the hostile
  desired state.
- **Combining bootstrap, multiple switchovers, failure injection, and decommission in the first live slice:** it
  destroys known-state isolation and makes recovery evidence ambiguous.
- **Declaring ACM 2.12–2.17 uniformly supported:** lifecycle, OCP matrix, OADP, GitOps, entitlement, and architecture
  evidence are time-sensitive and version-specific.
- **Replacing the release framework:** it would duplicate catalog, adapters, normalization, and reporting.
- **Letting the raw discovery adapter make safety decisions:** observations and policy must remain separately
  testable.

## Graphify hypotheses and source verification

Graphify was used only as a hypothesis generator; inferred or ambiguous edges were not accepted as facts.

| Graphify question | Hypothesis | Source verification and conclusion |
| --- | --- | --- |
| Current lab-controller-to-release-framework boundaries | The controller is an outer safety model and its current framework execution paths are non-live. | `execution.py`, `invocation.py`, `harness.py`, `run_lab_role_controller.py`, and Phase 6/7 tests prove dry-run/non-executed/fake-local boundaries. `orchestrator.py` separately retains the existing profile-driven live-capable runner. |
| Live discovery and hub identity evidence flow | Identity and role models exist, but current lab-controller live evidence does not feed them authoritatively. | `identity.py`/`roles.py` use typed modeled observations; `read_only_live_transport.py` has only an injected protocol and current controller integrations are fake/injected. Separately, `orchestrator.py::OcDiscoveryClient` is live-capable for the existing profile runner. Neither it nor `baseline/discovery.py` provides integrated controller-owned proof of the required identity tuple. |
| Scenario mutation classification and sequencing | Catalog rejection and modeled segment handoff exist, but no live controller-gated mutation exists. | `catalog.py`, `segments.py`, `planner.py`, and their tests prove classification/rejection/fake handoff; CLI live modes fail closed. |
| Role-aware profile generation and freshness | Hash/drift checks exist but do not bind time or full live provenance. | `profiles.py` binds roles, identity hashes, managed clusters, scenario content, and artifact root; it has no issued/expiry/source-revision/compatibility evidence fields. |
| Artifact eligibility and redaction | Controller payloads are recursively sanitized, while the release writer has incomplete final coverage. | `lab_controller/artifacts.py` recursively sanitizes modeled payloads; `reporting/artifacts.py::write_json` writes directly and `redaction.json` tracks selected text scans rather than a final recursive inventory. |
| Argo CD ownership and ApplicationSet handling | Static hostile/safe classification is strong but not live capability proof. | `gitops.py` and `test_lab_controller_phase8pq_gitops.py` prove fixture parsing, parent coordination, and unknown-fails-closed; artifacts explicitly remain dry-run/non-live and no live API path exists. |
| Python/Ansible release-stream parity | Normalizers exist, but aggregation is not scenario-specific. | `runtime_parity.py` has normalized records; `orchestrator.py::_normalized_runtime_sources` aggregates by capability/stream and emits coarse comparison identities, so scenario-specific live parity remains missing. |

## Phase 9A acceptance and handoff

Phase 9A is acceptable only when:

- the requirement matrix remains traceable to current source/tests;
- all design-only documentation checks and the complete strict repository suite pass;
- protected files are unchanged;
- CodeRabbit reports no unresolved critical or warning findings;
- the PR remains unmerged for independent validation; and
- Issue #121 remains open as the RC-hardening umbrella.

The independent validator must re-fetch `origin/ansible`, verify the PR base/head and design-only diff, recount the
matrix, challenge every live/non-live classification, validate official-source URLs, inspect Graphify conclusions
against source, re-run required checks with strict quality enabled, and confirm that no issue, artifact, or PR text
implies live readiness. Phase 9B must not begin during validation or comment resolution.
