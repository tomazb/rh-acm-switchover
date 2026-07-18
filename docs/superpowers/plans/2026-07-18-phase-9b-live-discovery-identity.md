# Phase 9B Controller-Owned Live Discovery and Physical Identity Proof Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Apply `superpowers:test-driven-development` for every production behavior, `superpowers:systematic-debugging` for any unexpected failure, and `superpowers:verification-before-completion` before any success claim.

**Goal:** Implement the narrowly scoped Phase 9B controller-owned, explicitly opted-in, read-only live discovery path that proves two stable and distinct physical hub identities without inferring logical roles, known state, readiness, or mutation/recovery authority.

**Architecture:** Add a typed controller client in `tests/release/lab_controller/live_discovery.py`. The controller will reuse the existing pure `ReadOnlyLiveTransport` as the last pre-contact gate and implement `ReadOnlyLiveClientProtocol` through an injected typed Kubernetes/OpenShift read API. The injected API is a runtime-only object and is never serialized; the controller accepts no shell command, argv, endpoint, kubeconfig path, token, release adapter, or ambient client factory. Pagination, repeated identity collection, physical identity canonicalization, freshness/provenance validation, call tracing, and artifact publication all remain controller-owned. Existing backend and artifact modules gain only the Phase 9B contract primitives needed to expose this path while preserving their dependency-free source guards.

**Tech Stack:** Python 3 dataclasses, enums, protocols, `hashlib`, `json`, UTC/monotonic clocks, pytest, existing release lab-controller transport/backend/artifact contracts.

## Scope Lock

### Allowed and expected files

Create:

- `docs/superpowers/plans/2026-07-18-phase-9b-live-discovery-identity.md`
- `tests/release/lab_controller/live_discovery.py`
- `tests/release/test_lab_controller_phase9b_live_discovery.py`

Modify:

- `tests/release/lab_controller/read_only_live_transport.py`
- `tests/release/lab_controller/read_only_backend.py`
- `tests/release/lab_controller/artifacts.py`
- `tests/release/lab_controller/__init__.py`
- `docs/development/lab-role-controller-spec.md`
- `docs/development/release-validation-framework.md`
- `tests/release/README.md`
- `CHANGELOG.md`

Modify an existing Phase 8 transport/backend/artifact test only if a backward-compatibility assertion must be updated for an additive Phase 9B field. No other file is expected.

### Hard-fail scope expansion

Stop before editing if implementation would require any of:

- production CLI or `lib/` changes;
- Ansible Collection, playbook, role, plugin, RBAC, Helm, Kustomize, or GitOps manifest changes;
- release-profile, CI workflow, bootstrap, recovery, or mutation changes;
- Phase 9C logical-role, known-state, readiness, mutation-authorization, recovery-authorization, or executable-profile work;
- edits to `docs/ACM_SWITCHOVER_RUNBOOK.md` or `.claude/skills/**/*.skill.md`;
- a parity-status change or intentional Python/collection divergence.

## Authority Boundary

The Python lab controller owns:

- operator opt-in and authorization-reference validation;
- source revision cleanliness and binding;
- runtime-handle presence and uniqueness;
- allowlisted structured query definitions;
- the final no-contact gate decision;
- typed client construction from already supplied runtime-only API objects;
- pagination completeness and collection bounds;
- freshness, skew, provenance, and repeated-identity checks;
- physical identity canonicalization and fingerprints;
- distinctness and enrolled-fingerprint comparisons;
- recursive redaction audit;
- artifact contents and publication permission;
- final Phase 9B PASS/BLOCKED decision.

The existing release framework, production CLI, Ansible Collection, shell tooling, and release adapters do not own or execute this path. The Phase 9B client does not call them.

## Typed Client and Runtime Credential Boundary

Add these core contracts in `live_discovery.py`:

- `TypedReadRequest`: fixed query identifier; `list` verb; API group/version; resource plural; exact name selector; continuation token; consistent resource version; page size; request timeout.
- `TypedReadPage`: normalized item mappings; exact echoed requested continuation token (including the initial `None`); returned continuation token; collection resource version; remaining item count; truncation marker; evidence timestamp; evidence origin; source revision.
- `TypedReadPageReader` protocol: one method, `read_page(request) -> TypedReadPage`, invoked only after all gates. An admitted runtime binding must explicitly select the `typed_request_timeout_v1` contract and enforce `request.timeout_seconds` in the underlying typed API request.
- `TypedReadApi`: an exact controller-owned dataclass that passively binds one injected page reader to the exact access/context object identities without invoking caller code.
- `Phase9BRuntimeHandle`: public hub identifier plus the controller-owned `TypedReadApi` binding. Runtime objects have no string/path/URL coercion contract and are excluded from artifacts.
- `Phase9BIdentityEnrollment`: immutable public-hub-to-fingerprint entries bound to the clean source revision and config/profile hashes, with a controller-recomputed enrollment hash.
- `ControllerOwnedLiveDiscoveryClient`: implements `ReadOnlyLiveClientProtocol`; maps the outer transport's fixed read-only identity-bundle `get` to fixed typed API `list` requests; performs complete pagination through those lists; and returns normalized evidence.
- `Phase9BLiveDiscoveryRequest`, `Phase9BLiveDiscoveryResult`, bounds, and explicit clock protocols.

The controller accepts two explicit runtime handles and never discovers credentials from environment variables, home directories, default kubeconfig locations, current contexts, endpoints, or process state. No default client constructor exists. A caller must inject a typed API object after its own secure runtime setup. The public artifact records only safe controller-assigned hub IDs and an evidence-origin digest or safe origin ID; it never records the API object, endpoint, context, path, token, certificate, or exception text.

## Allowlisted Queries and Verbs

The outer transport uses one fixed identity-bundle `get`. The controller maps that operation to these exact-name,
cluster-facing typed API `list` queries so pagination completeness remains observable:

1. `identity.kube_system_namespace`: Kubernetes core/v1 `namespaces`, exact name `kube-system`, typed verb `list`.
2. `identity.openshift_infrastructure`: OpenShift config.openshift.io/v1 `infrastructures`, exact name `cluster`, typed verb `list`.
3. `identity.openshift_cluster_version`: OpenShift config.openshift.io/v1 `clusterversions`, exact name `version`, typed verb `list`.

Optional ACM, BackupSchedule, Restore, ManagedCluster, and GitOps observations are not collected in Phase 9B. Their fixed query vocabulary and non-authoritative observation model are deferred.

Unknown queries, `describe`, `watch`, create/update/patch/delete/apply, subresources, arbitrary group/resource/name values, command-like strings, release adapters, and mutation flags fail before `TypedReadApi.read_page` is called.

## Physical Identity Signals and Stability Rationale

Each physical hub is collected twice within the bounded evidence window. The authoritative allowlisted signals are:

- `kube-system` Namespace `metadata.uid`: cluster-scoped object identity already used by repository hub-binding safety logic.
- OpenShift `Infrastructure/cluster` `metadata.uid` plus `status.infrastructureName`: independently collected OpenShift cluster identity anchored in the config API.
- OpenShift `ClusterVersion/version` `metadata.uid`; its current desired version is recorded only as corroborating metadata, not as an identity secret.

Context names, kubeconfig paths, API endpoints, hub-a/hub-b aliases, input order, runtime handle representations, and profile role labels are excluded.

For each signal:

- require exactly one readable singleton object;
- require exact expected `apiVersion` and `kind` values; missing or mismatched discriminators block;
- normalize only explicitly allowlisted scalar fields;
- reject missing, duplicate, conflicting, non-string, blank, oversized, control-character, or structurally ambiguous values;
- compare first and second collections for exact canonical identity equality.

Canonicalization uses UTF-8 JSON with sorted keys and compact separators over the versioned allowlisted identity document. The public fingerprint is `sha256:<lowercase hex>`. Raw UIDs and infrastructure names are never published. Both hub fingerprints must be distinct and each hub's computed fingerprint must match the expected fingerprint in immutable controller enrollment for its public hub ID. The controller recomputes the enrollment hash and requires its source/config/profile bindings to match the request before contact; missing, duplicate, or tampered enrollment blocks. This detects swapped runtime handles without treating caller order or per-run handle fields as proof.

## Pagination Completeness State Machine

For each typed query:

1. Start with no continuation token and no resource-version lock.
2. Validate page type, origin, source revision, timestamp, item shape, and page resource version.
3. On the first page, bind the collection resource version.
4. On later pages, require the same resource version and a valid transition from the exact requested token.
5. Accumulate items only while total deadline, page limit, and item limit remain.
6. If `remaining_item_count > 0`, require a new non-empty continuation token.
7. If a continuation token exists, require it to be new, not equal to the requested token, and not previously seen.
8. If truncation is reported, require a valid continuation state and continue; a terminal truncated page is blocked.
9. Complete only when the continuation token is empty, remaining count is absent or zero, and the page is not truncated.

Block on missing continuation state, repeated tokens, token loops, invalid token transitions, resource-version changes, inconsistent snapshots, terminal truncation, page/item/deadline exhaustion before terminal completeness, or API errors/timeouts. The call trace records only query ID, verb, page ordinal, safe hub ID, result status, and completeness metadata.

## Freshness, Skew, Revision, and Origin Rules

- Capture controller UTC and monotonic start/end for the overall collection and every query.
- Require timezone-aware UTC evidence timestamps.
- Reject evidence older than `max_evidence_age`, farther in the future than `max_clock_skew`, or collected outside the bounded controller window plus allowed skew.
- Require the maximum timestamp spread across both hubs and both identity passes to be within `max_clock_skew`.
- Require every page's source revision to equal the controller's clean bound source revision.
- Require every page for a runtime handle to have the configured expected origin.
- Require origins to be non-empty, safe identifiers and distinct across the two handles.
- Reject any per-query, per-page, per-pass, or cross-hub mixed origin.
- Hash and record the clean source revision, relevant configuration hash, and profile hash; do not accept dirty/unbound revisions.

## Artifact and Recursive Redaction Contract

Add a strict recursive publication audit in `artifacts.py` that:

- accepts only JSON-safe dictionaries with string keys, lists, booleans, integers, finite floats, safe strings, and `None`;
- traverses every key and value;
- rejects sensitive key names, tokens/credentials, URLs/endpoints, kubeconfig/context/path references, runtime handle names/representations, exception text, bytes, and arbitrary object representations;
- rejects control characters and unsafe oversized strings;
- returns a sanitized deep copy and an audit summary only after all nodes pass.

Phase 9B artifact publication is all-or-nothing. Redaction failure yields a BLOCKED result with no artifact. Every published artifact forces:

- `purpose = "live_read_only"`
- `certification_eligible = false`
- `live_certification_evidence = false`
- `mutation_attempted = false`
- Phase 9B schema, writer, and controller revisions
- source revision/config/profile hashes
- start/end timestamps and freshness/skew outcomes
- per-query pagination completeness
- evidence source/query ID and origin proof
- redacted physical fingerprints
- recursive audit status/count

Reject caller attempts to set certification flags, logical roles, known state, readiness, mutation/recovery authorization, executable profiles, or authorization tokens.

## Test-First Sequence

### Task 1: Contract-level RED tests

**Files:**

- Create: `tests/release/test_lab_controller_phase9b_live_discovery.py`

Write deterministic fake typed API and fake clock helpers, then tests for:

- two stable distinct multi-signal hub proofs;
- source/config/profile-bound expected fingerprint enrollment, missing/tampered enrollment, and swapped-handle rejection;
- duplicate hub fingerprints;
- conflicting, changing, missing, duplicate, and unreadable identity signals;
- explicit opt-in, authorization reference, runtime handle, L0-L9, clean revision, config hash, and profile hash gates;
- no contact before all gates pass;
- ambient credential/default-client rejection;
- structured allowlist and get/list-only enforcement;
- shell, command, argv, subprocess, release-adapter, mutation-flag, and mutating-verb rejection;
- partial discovery, API failure, sanitized timeout failure;
- missing continuation, repeated token, loop, invalid transition, terminal truncation, resource-version inconsistency, page/item/deadline bound exhaustion;
- stale, future-skewed, cross-hub skewed, wrong-origin, mixed-origin, and wrong-source evidence;
- nested sensitive keys and values, credentials, tokens, URLs/endpoints, kubeconfig paths, contexts, runtime handles, exception text, bytes, and unsafe object representation;
- caller attempts to publish certification, role, known-state, readiness, mutation, recovery, executable-profile, or token claims;
- forced non-certification/non-mutation artifact flags and non-authoritative observations.

Run:

```bash
.venv/bin/python -m pytest tests/release/test_lab_controller_phase9b_live_discovery.py -q
```

Expected RED: import/collection failure because `tests.release.lab_controller.live_discovery` does not exist. Preserve the exact failure summary for the PR.

### Task 2: Additive transport/backend/artifact contracts

**Files:**

- Modify: `tests/release/lab_controller/read_only_live_transport.py`
- Modify: `tests/release/lab_controller/read_only_backend.py`
- Modify: `tests/release/lab_controller/artifacts.py`
- Test: `tests/release/test_lab_controller_phase9b_live_discovery.py`
- Test existing Phase 8 transport/backend/artifact suites.

Add controller-supplied page/item/deadline bounds to `ReadOnlyLiveClientRequest` with safe defaults, add `LIVE_READ_ONLY_PHASE_9B` to backend phase vocabulary and a narrow Phase 9B backend protocol/result boundary, and add strict recursive publication auditing. Keep pure-layer source guards green and do not import the new client or Kubernetes libraries into transport/backend/artifact modules.

Run focused tests. Expected RED failures should move from missing module to missing behavior rather than be bypassed.

### Task 3: Typed client, pagination, and provenance

**Files:**

- Create: `tests/release/lab_controller/live_discovery.py`
- Modify: `tests/release/test_lab_controller_phase9b_live_discovery.py`

Implement fixed query definitions, typed request/page/API protocols, runtime handles, gate evaluation, transport composition, complete pagination, strict origin/source checks, bounded collection, and safe call tracing.

Run focused tests after each small behavior group until pagination/provenance tests are GREEN.

### Task 4: Repeated physical identity proof

**Files:**

- Modify: `tests/release/lab_controller/live_discovery.py`
- Modify: `tests/release/test_lab_controller_phase9b_live_discovery.py`

Implement singleton parsing, three-signal canonicalization, SHA-256 fingerprinting, repeated collection, stability, distinctness, expected enrollment checks, freshness/skew, and non-authoritative observation separation.

Run focused tests until identity/freshness tests are GREEN.

### Task 5: Artifact publication and exports

**Files:**

- Modify: `tests/release/lab_controller/live_discovery.py`
- Modify: `tests/release/lab_controller/__init__.py`
- Modify: `tests/release/test_lab_controller_phase9b_live_discovery.py`

Build the forced live-read-only artifact, apply strict recursive audit as the final publication gate, return no artifact on audit failure, export the public Phase 9B entrypoint/types, and prove forbidden claims cannot be injected.

Run focused and affected Phase 8 artifact/transport/backend tests.

### Task 6: Documentation

**Files:**

- Modify: `docs/development/lab-role-controller-spec.md`
- Modify: `docs/development/release-validation-framework.md`
- Modify: `tests/release/README.md`
- Modify: `CHANGELOG.md`

Document the Phase 9B controller/transport/API ownership chain, runtime-only credential boundary, selected physical signals, complete pagination, provenance/freshness/redaction behavior, non-authoritative observation status, forced artifact flags, and Phase 9C deferrals. State that deterministic fakes are not live exit evidence.

Do not edit AGENTS.md because Phase 9A already records the durable authority boundary and this slice does not change contributor policy.

## Verification and Review

Run in order:

```bash
.venv/bin/python -m pytest tests/release/test_lab_controller_phase9b_live_discovery.py -q
.venv/bin/python -m pytest \
  tests/release/test_lab_controller_identity.py \
  tests/release/test_lab_controller_phase8c_live_config_model.py \
  tests/release/test_lab_controller_phase8e_read_only_discovery_guardrails.py \
  tests/release/test_lab_controller_phase8g_read_only_backend_interface.py \
  tests/release/test_lab_controller_phase8j_read_only_live_transport.py \
  tests/release/test_lab_controller_profiles_artifacts.py \
  tests/release/test_lab_controller_phase9b_live_discovery.py -q
.venv/bin/python -m pytest tests/release -q
./run_tests.sh
git diff --check
black --check --line-length 120 --diff acm_switchover.py lib modules ansible_collections/tomazb/acm_switchover/plugins ansible_collections/tomazb/acm_switchover/tests tests
isort --check-only --profile black --line-length 120 acm_switchover.py lib modules ansible_collections/tomazb/acm_switchover/plugins ansible_collections/tomazb/acm_switchover/tests tests
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
mypy --explicit-package-bases acm_switchover.py lib/ modules/ ansible_collections/tomazb/acm_switchover/plugins ansible_collections/tomazb/acm_switchover/tests --ignore-missing-imports --no-strict-optional
bandit --ini .bandit -f txt
```

Then:

- run `graphify update .` as the repository post-modification graph refresh;
- query the updated graph for the new controller/transport/backend/identity/artifact paths;
- verify every relevant inferred edge against source/tests;
- run the mandatory CodeRabbit review skill against uncommitted changes;
- resolve all critical/warning findings;
- rerun affected tests and CodeRabbit until no critical/warning finding remains;
- apply the verification-before-completion checklist from fresh command output.

## Live Read-Only Exit Evidence Procedure

Only if the operator separately supplies explicit authorization and external runtime-only typed handles:

1. Confirm the worktree remains clean except the intended commit and source revision equals the committed head.
2. Invoke only the controller-owned Phase 9B entrypoint.
3. Pass the two runtime-only typed API objects without serializing paths, tokens, endpoints, contexts, or private fingerprints.
4. Confirm the trace contains only fixed allowlisted `get`/`list` queries.
5. Confirm both fingerprints are stable, distinct, and bound to their enrolled physical IDs.
6. Confirm every query reached terminal pagination completeness.
7. Confirm freshness/skew/source/config/profile/origin binding.
8. Confirm recursive redaction audit passed and forced artifact flags remain non-certifying/non-mutating.
9. Retain only sanitized artifacts for the independent validator.

No authorization or runtime handles were supplied with Issue #188. Therefore this implementation must not contact a cluster, must not substitute fake evidence, and must open the PR with `PR_OPENED_BLOCKED_LIVE_EXIT_GATE`.

## Explicit Non-Goals and Phase 9C Deferrals

Phase 9B does not:

- infer physical-hub logical primary/secondary roles;
- classify known state or readiness;
- authorize mutation, recovery, or certification;
- generate executable profiles or authorization tokens;
- execute a scenario or call a release adapter;
- add bootstrap or credential-discovery logic;
- introduce cluster mutation or any production implementation change;
- change Python/Ansible parity, RBAC, GitOps manifests, release profiles, or workflows;
- claim deterministic fake evidence is live evidence.

All logical-role/known-state binding, mutation authorization, recovery decisions, and one-scenario known-state execution remain blocked for Phase 9C.
