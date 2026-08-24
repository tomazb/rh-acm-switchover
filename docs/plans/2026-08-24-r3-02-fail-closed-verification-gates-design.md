# R3-02 Fail-Closed Verification Gates Design

**Status:** Approved design candidate; implementation not yet authorized  
**Date:** 2026-08-24  
**Base:** `ansible@ca10bb9b616338c930378c8da0cb0f1da64dde09`  
**Governing issue:** #272  
**Findings:** `R3-A4`, `R3-A5`, `GLM-H12`

## 1. Purpose

R3-02 corrects three verification paths that can currently treat failed or
unproven Kubernetes reads as success or a benign skip:

1. primary-prep Thanos compactor Pod drain verification (`R3-A4`);
2. preflight primary/secondary hub API-connectivity reporting (`R3-A5`); and
3. activation auto-import strategy lookup before immediate-import annotation
   management (`GLM-H12`).

The design keeps the fixes local to the existing owners. It does not create a
new validation subsystem and does not use Python/Collection runtime imports to
share code. The operator-facing contract is that a safety decision passes only
from positive evidence; absence and error remain distinct outcomes.

The slice is parity-sensitive because preflight, primary prep, and activation
are all `dual-supported` capabilities. Current-base revalidation found that the
Python activation path also warns and skips when the auto-import ConfigMap read
fails, so `GLM-H12` requires a parity-preserving Python correction as part of
this slice. No intentional parity divergence is proposed.

## 2. Current-base evidence

### 2.1 `R3-A4`: compactor drain can be satisfied by missing evidence

`roles/primary_prep/tasks/scale_observability.yml` reads Thanos compactor Pods
with `failed_when: false` and uses:

```text
resources | default([]) | length == 0
```

as the retry success condition. If the module fails and the result has no
`resources` field, the default converts missing evidence into an empty list.
The later `result is failed` branch is not authoritative because
`failed_when: false` changes Ansible's failure classification.

Python already fails closed at this boundary: it calls `get_pods()` inside
`wait_for_condition`, propagates API failures, and raises if Pod termination
cannot be established. No Python runtime change is needed for `R3-A4`.

### 2.2 `R3-A5`: connectivity pass is derived from a masked failure flag

`roles/preflight/tasks/validate_kubeconfigs.yml` probes the `default` Namespace
on each required hub with `failed_when: false`, then derives the report status
from the registered result's `.failed` value. That value is not positive proof
of API reachability after failure masking. A wrong server, expired/invalid
credentials, DNS failure, TLS/transport failure, or malformed result can
therefore reach the structured preflight report as `status: pass`.

The preflight architecture already has the correct ownership boundary: it
collects validation findings, writes the report, recomputes the aggregate
summary, and only then fails the role on critical findings. R3-02 must preserve
that complete-reporting behavior rather than turn the connectivity probe into
an early task abort.

### 2.3 `GLM-H12`: auto-import read errors are treated like an unavailable feature

`roles/activation/tasks/apply_immediate_import.yml` reads the named
`multicluster-engine/import-controller-config` ConfigMap with
`failed_when: false`.

A successful Kubernetes read for a genuinely absent named ConfigMap yields a
defined empty `resources` list. That is a valid absence signal and may use the
ACM default `ImportOnly` behavior.

A module/API/RBAC/transport failure can instead produce a result without a
usable `resources` list. The current task treats this as
`reason: autoImportStrategy_unavailable`, `skipped: true`, and continues. That
is an error-to-skip conversion, not absence handling.

The Python CLI has the same operator-level weakness in the corresponding
immediate-import path. `KubeClient.get_configmap()` already distinguishes 404
from other errors: 404 returns `None`; non-404 errors propagate after the
client retry policy. However, `SecondaryActivation._get_auto_import_strategy()`
currently catches arbitrary exceptions and returns the string `"error"`, and
`_apply_immediate_import_annotations()` converts that sentinel into a warning
and skip. A Collection-only fix would create new parity drift, so this design
changes both form factors at this boundary.

## 3. Goals

R3-02 must:

- require positive evidence for every corrected pass/continue decision;
- preserve bounded retry behavior for compactor termination;
- preserve complete preflight reporting for connectivity failures;
- distinguish genuine ConfigMap absence from read failure;
- fail activation before ManagedCluster annotation mutation when the
  auto-import strategy cannot be verified;
- keep Python and Collection operator decisions aligned for the auto-import
  boundary;
- keep dry-run and native Ansible check mode non-mutating;
- preserve existing hub/context/namespace/resource targeting;
- preserve checkpoint and phase ordering semantics;
- use stable sanitized public failures and avoid echoing raw API exception
  detail through newly added failure messages;
- add discriminating runtime tests, with static contract tests only as
  supplemental guardrails.

## 4. Non-goals

This slice does not:

- change RBAC permissions or manifests;
- redesign preflight reporting;
- change checkpoint schemas or `reset_from` behavior;
- address finalization `register`/`set_fact` collisions (`R3-01b`);
- address Python fleet timeout budgeting (`R3-03`);
- implement the broader API/exception logging cleanup owned by `SSA-09`;
- redesign auto-import transaction ownership (`R4-02`);
- add a generic verification framework;
- change release/lab-controller behavior or create live certification evidence;
- modify `docs/ACM_SWITCHOVER_RUNBOOK.md` or `.claude/skills/**`.

If implementation analysis shows that new Kubernetes permissions are required,
the builder stops for explicit scope approval. No new permission is expected:
all corrected reads already exist in shipped runtime code.

## 5. Approaches considered

### 5.1 Approach A — localized positive-evidence gates (selected)

Keep the current task ownership and, where failure masking is needed to preserve
retry or report aggregation, keep `failed_when: false`. Replace negative
reasoning such as `.failed == false` or `default([])` with explicit validation
of the expected successful result shape and content. Add a stable downstream
failure owner when positive evidence is unavailable.

For Python auto-import, use the existing 404-versus-error semantics and a
sanitized read path so genuine read failures become a controlled activation
failure rather than a skip.

Advantages:

- smallest runtime diff;
- preserves current phase and report architecture;
- makes the safety predicate visible at the call site;
- follows KISS/YAGNI and current repository patterns;
- does not create a new cross-path abstraction.

### 5.2 Approach B — convert the reads to `block`/`rescue`

Remove `failed_when: false` and catch errors structurally.

This is explicit, but it is a poor fit for two of the three paths. Preflight
must retain both hub findings and write the structured report before stopping;
a direct failed task would short-circuit that owner unless additional rescue
plumbing recreates the aggregation behavior. The compactor loop also needs to
retry read failures and non-empty inventories under one bounded wait contract,
which becomes more complex with task-level rescue.

### 5.3 Approach C — introduce a shared verification classifier

Create a helper/module that normalizes all Kubernetes reads into success,
absence, malformed, and error outcomes.

This could reduce future duplication, but three local defects do not justify a
new subsystem. It would expand test and ownership surface and make simple task
predicates less auditable. R3-02 therefore rejects this approach unless future
current-tree evidence proves the local design cannot be implemented safely.

## 6. Selected design

### 6.1 Compactor termination: valid Pod inventory is the only success evidence

The existing scale-down and bounded `30 x 10s` verification loop remain in
`roles/primary_prep/tasks/scale_observability.yml`.

The Pod read may continue using `failed_when: false` so a transient read failure
can consume another retry instead of aborting immediately. The retry predicate,
however, becomes strict:

- the registered result must be a mapping;
- `resources` must be present;
- `resources` must be an actual list, not a string or other truthy sequence; and
- success requires that list to be empty.

A missing, malformed, or non-list `resources` value can never satisfy `until`.
A successful read with a non-empty list remains pending and consumes retries as
it does today.

After the bounded loop, exactly two failure classes are exposed:

1. **verification unavailable** — the final result does not contain a valid Pod
   list; fail with a stable message equivalent to "Unable to verify Thanos
   compactor pod termination after scale-down; verify API access and retry";
2. **Pods still present** — the final result contains a valid non-empty Pod
   list; preserve the current count-oriented failure behavior.

The downstream decision must not use `result is failed`, `.failed`, or
`resources | default([])` as the proof predicate.

The read result is treated as sensitive error material. The implementation
should suppress raw module error detail at this capture boundary (`no_log` or
an equivalent existing callback-safe pattern) and emit only the stable
sanitized downstream failure. This does not claim to close the repository-wide
`ApiException` logging problem owned by `SSA-09`.

No change is made to:

- scale target (`StatefulSet` on the primary hub);
- namespace or label selector;
- retry count or delay;
- execute/dry-run branching;
- the published observability scale result on successful execution.

### 6.2 Connectivity: preflight `pass` requires the exact expected Namespace

The primary and secondary connectivity probes remain non-fatal task reads so
preflight can publish complete findings before the aggregate stop.

Each hub's `pass` decision is based only on a validated successful result:

- the registered result is a mapping;
- `resources` exists and is a list;
- the list contains exactly one resource for the named lookup; and
- that resource is a mapping whose `metadata.name` is exactly `default`.

Anything else yields that hub's existing critical connectivity finding with
`status: fail` and a stable message such as `<role> hub API connectivity probe
failed`.

The result IDs, severity, details structure, restore-only primary skip, and
preflight aggregation owner remain unchanged. The failure is recorded in
`acm_switchover_validation_results`, reaches `preflight-report.json`, and is
then consumed by the existing aggregate `critical_failures` calculation.

The probe capture must not make raw API response bodies, credentials, kubeconfig
content/path, or raw exception strings part of the new public failure. Existing
safe context detail in the structured result may remain because this slice does
not redefine the report schema.

This design intentionally does not abort immediately on the first failed hub
probe. The report owner, not the `k8s_info` task, owns the final preflight
failure.

No Python change is required for `R3-A5`; current Python client/bootstrap and
preflight paths already fail closed on unavailable hub access rather than
publishing a fabricated successful connectivity result.

### 6.3 Collection auto-import: absence is valid; error is fatal

The Collection keeps the named ConfigMap read in
`roles/activation/tasks/apply_immediate_import.yml` before any ManagedCluster
read or annotation patch.

The read outcome is classified locally:

#### Valid absence

A successful result with `resources: []` means the ConfigMap is absent. This is
not an error. The effective strategy is the existing default (`ImportOnly`), so
immediate-import annotation management may proceed exactly as it does for an
absent ConfigMap today.

#### Valid present object

A successful result with one ConfigMap resource is accepted when:

- the resource is a mapping; and
- `data` is absent, `null`, or a mapping.

Absent or `null` `data` is normalized to an empty mapping for this read-only
decision. The existing `autoImportStrategy` defaulting rules are then applied.

More than one resource for the named read, a non-mapping resource, or a
non-mapping non-null `data` value is malformed evidence and fails closed.

#### Read failure / unverifiable result

A result that lacks a valid `resources` list is treated as a read failure, not
as `autoImportStrategy_unavailable`. Activation fails with a stable sanitized
message before:

- listing ManagedClusters for immediate-import work;
- clearing an existing immediate-import annotation; or
- applying the empty immediate-import trigger annotation.

The current benign execute-mode result
`reason: autoImportStrategy_unavailable` is removed for genuine read failures.
Dry-run and the ACM-version unsupported path retain their existing skip results.

As with the other masked-error reads, the captured module error is not surfaced
raw through the new failure path. The implementation uses the smallest existing
callback-safe suppression pattern and emits a stable downstream message.

### 6.4 Python auto-import: use 404-versus-error semantics and fail before mutation

Python must make the same operator decision as the Collection for the shared
activation behavior.

`KubeClient.get_configmap()` already has the important semantic split:

- 404 => `None`;
- non-404/retryable failure => exception after the existing retry contract.

The immediate-import path must stop converting the second case into the string
`"error"` and a warning skip.

To avoid widening raw exception logging as part of this safety fix, the
implementation should use a narrow ConfigMap read variant following the
existing `get_custom_resource_advisory()` pattern: retain retries and 404 =>
`None`, but do not log rendered exception bodies during retry or final failure.
The helper remains Python-local and is used only where a sanitized caller-owned
failure is required; it is not shared with the Collection.

`SecondaryActivation._get_auto_import_strategy()` then has exactly two normal
outcomes:

- ConfigMap absent => `"default"`;
- ConfigMap present => normalized configured strategy.

A genuine read failure raises a controlled `FatalError` (or the existing
activation-domain equivalent chosen by the implementation plan) with a stable
message that does not include the raw exception. `_apply_immediate_import_annotations()`
therefore never receives an `"error"` sentinel and never treats an unverified
strategy as a benign skip.

The failure must occur before `list_custom_resources(... ManagedCluster ...)`
or `patch_managed_cluster()` is invoked.

This change is limited to the immediate-import annotation decision. The broader
`_maybe_set_auto_import_strategy()` management transaction and its R4 ownership
work are not redesigned in R3-02.

### 6.5 Dry-run and native Ansible check mode

R3-02 changes read/decision semantics, not mutation authorization.

- Collection `dry_run` continues to skip immediate-import annotation management
  and compactor scale-down mutation as it does today.
- Native Ansible check mode with `execution.mode: execute` may still perform
  non-mutating reads needed to make safety decisions; Kubernetes mutation
  modules must remain non-mutating under their existing check-mode contracts.
- Connectivity validation remains a read-only preflight operation in all
  relevant modes.
- Python dry-run retains the existing KubeClient mutation guards. The changed
  Python auto-import error decision must not cause a ManagedCluster mutation in
  dry-run.

No checkpoint pass/fail transition semantics are changed by this design.

## 7. Error and information-flow contract

The three fixes use one principle without sharing runtime code:

```text
raw Kubernetes result
        |
        v
positive shape/content validation
        |
   +----+------------------+
   |                       |
valid evidence          unverified/error
   |                       |
   v                       v
existing success/       stable sanitized fail
pending/absence         owned by current phase/report
```

Rules:

- missing evidence is never rewritten to an empty-success value;
- a masked Ansible failure flag is never used as proof of success;
- a 404/empty named ConfigMap result remains distinct from an API failure;
- raw captured exception/module detail is not copied into newly introduced
  public messages or report fields;
- the implementation does not claim comprehensive log redaction beyond these
  corrected boundaries.

## 8. Parity contract

The parity matrix marks preflight, primary prep, and activation as
`dual-supported`. R3-02 leaves those statuses unchanged.

Expected shared decisions after implementation:

| Scenario | Python | Collection |
| --- | --- | --- |
| compactor Pod verification API failure | fail closed | fail closed |
| compactor Pod list empty after scale | continue | continue |
| required hub unreachable during validation | fail closed | critical preflight fail |
| auto-import ConfigMap absent | use default strategy | use default strategy |
| auto-import ConfigMap present and valid | classify configured strategy | classify configured strategy |
| auto-import ConfigMap read/API/RBAC failure | activation failure before annotation mutation | activation failure before annotation mutation |

The form factors remain independent. Shared behavior may be pinned through
fixtures/static parity assertions, but production code must not cross-import.

The existing coexistence document has an intentional read-failure divergence
for **finalization reset-obligation handling**. R3-02 does not alter that
separate contract. This design concerns the activation-time immediate-import
strategy decision where no intentional divergence is approved.

## 9. Testing strategy

Runtime tests are the primary proof. Source-text tests may pin ownership and
ordering but cannot substitute for executable failure behavior.

### 9.1 Collection compactor verification

Add a focused executable fixture around the shipped primary-prep task path.
The fixture must distinguish at least:

1. successful empty Pod list => verification succeeds;
2. successful non-empty Pod list => remains pending and ultimately fails if it
   never drains within the bounded test-configured retry window;
3. 403-style API failure => never satisfies the retry predicate and fails
   closed;
4. connection/transport-style failure or malformed result => same fail-closed
   decision;
5. failure payload containing credential/API-body sentinels => sentinels do not
   appear in public output.

The test harness may shorten retries/delay through existing test seams or a
narrow non-production fixture input; production retry constants/behavior must
not be weakened merely to make tests fast.

### 9.2 Collection connectivity reporting

Extend the existing preflight integration harness so both hub roles are covered
independently.

Required cases:

- valid `default` Namespace response => that hub's result is `pass`;
- unreachable/wrong-server/authentication-style failure => result is `fail`;
- malformed success shape => `fail`;
- the failure appears in `preflight-report.json` with the existing result ID and
  critical severity;
- normal two-hub runs test both primary and secondary failures; restore-only
  preserves the secondary-only behavior;
- raw sensitive sentinels from the simulated API failure are absent from public
  output/report fields.

### 9.3 Collection auto-import activation

Add executable activation coverage for:

- ConfigMap absent (`resources: []`) => default behavior remains reachable;
- valid ConfigMap with `ImportOnly`/default => existing annotation path remains
  reachable;
- valid ConfigMap with a non-applicable strategy => existing no-annotation
  behavior remains;
- 403/RBAC/API/transport failure => activation fails before ManagedCluster
  discovery or patch;
- malformed named-read result => fail closed;
- native check mode => reads occur as needed but no annotation mutation is
  performed.

Negative tests must make subsequent mutation observability explicit: the fake
API/harness records ManagedCluster GET/PATCH calls so a refused run proves zero
annotation mutation.

### 9.4 Python auto-import parity

Python unit tests cover:

- advisory ConfigMap read returns `None` => `"default"` behavior;
- present ConfigMap => configured strategy behavior;
- read raises an API/transport exception containing a sentinel => controlled
  activation failure with no sentinel in captured public logs/message;
- failure occurs before ManagedCluster listing;
- failure occurs before `patch_managed_cluster()`;
- dry-run remains non-mutating.

The Python KubeClient test suite also pins the new narrow advisory ConfigMap read
contract: 404 => `None`, retryable/non-404 errors propagate, and the advisory
path does not render the raw exception through its own retry/final logging.

### 9.5 Static and parity guardrails

Supplement runtime tests with minimal static assertions that make future
regression obvious:

- compactor `until` requires positively defined/list-valued `resources`;
- connectivity pass logic no longer reads `.failed` as authority;
- auto-import query error has an explicit failure barrier before ManagedCluster
  tasks;
- Python and Collection both have a regression case for ConfigMap read failure
  => activation failure.

Do not create a generalized cross-language adapter for this slice.

## 10. Expected implementation surface

The implementation plan should begin from the following likely files and add
others only when a test or documentation contract requires them.

### Collection runtime

- `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/scale_observability.yml`
- `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_kubeconfigs.yml`
- `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/apply_immediate_import.yml`

### Python runtime

- `modules/activation.py`
- `lib/kube_client.py` only for the narrow advisory ConfigMap read needed to
  avoid raw exception logging in the corrected Python decision path

### Tests

- collection unit contract tests for the three task owners;
- collection integration fixtures/tests for primary prep, preflight, and
  activation;
- `tests/test_activation.py`;
- `tests/test_kube_client.py`;
- directly relevant parity/static tests if needed to pin the shared decision.

### Documentation

The implementation PR updates:

- `CHANGELOG.md` `[Unreleased]`;
- `thermos-resolution-plan.md` for R3-02 status/evidence;
- operator/developer docs only where the visible failure behavior is currently
  described inaccurately.

No released version identifier is changed. Protected files remain untouched.

## 11. Verification matrix

The implementation plan must re-read current `AGENTS.md` and compatibility
policy at its own base and use the exact then-current command scopes. At this
design base, the compatibility authority defines repository-tested Collection
endpoint lanes as:

- `ansible-core 2.16.*` / Python 3.11;
- `ansible-core 2.21.*` / Python 3.12.

Verification proceeds targeted-first:

1. focused Python activation/KubeClient tests;
2. focused Collection unit contracts for R3-02;
3. focused Collection executable integration tests for the three failure
   boundaries;
4. directly affected scenario/check-mode tests;
5. combined Python/Collection parity-sensitive test surface because activation,
   primary prep, and preflight are dual-supported;
6. documentation guardrails and changed-file formatting/linting;
7. every broader gate invalidated by the final branch diff under the current
   `AGENTS.md` verification matrix.

Fake APIs, fixture adapters, and dry-run/check-mode evidence remain non-live and
must not be described as ACM certification evidence.

## 12. Pre-PR simplification gate

Before a future R3-02 implementation PR is opened, the builder reviews the
changed code and directly affected collaborators for avoidable complexity.

This review may simplify only safe, in-scope complexity introduced, exposed, or
made materially worse by R3-02. It does not authorize broad cleanup of the
preflight, observability, activation, or KubeClient subsystems. Any worthwhile
out-of-scope refactor is recorded/deferred under the normal finding-disposition
rules.

The PR description records either the simplifications applied or that no safe
in-scope simplification was identified.

## 13. Rollback and compatibility boundary

R3-02 is a fail-closed correctness change. Rollback means reverting the
implementation commit(s); no persisted-state or schema migration is introduced.

The design deliberately avoids:

- new checkpoint fields;
- new report schema fields;
- new public CLI/Collection variables;
- new RBAC permissions;
- new phase ordering;
- new release profile fields.

This keeps rollback localized to runtime decision semantics and their tests.

## 14. Acceptance mapping

| Governing acceptance requirement | Design owner |
| --- | --- |
| compactor 403/timeout/connection error fails phase | §6.1 + §9.1 |
| compactor loop cannot succeed without `resources` | §6.1 |
| valid empty compactor inventory succeeds | §6.1 + §9.1 |
| primary/secondary connectivity failure reaches report | §6.2 + §9.2 |
| successful connectivity still passes | §6.2 + §9.2 |
| ConfigMap absence remains default behavior | §6.3 + §9.3 |
| ConfigMap API/RBAC failure fails before annotation mutation | §6.3 + §9.3 |
| Python/Collection auto-import decision aligned | §6.4 + §8 + §9.4 |
| dry-run/check mode non-mutating | §6.5 + tests |
| stable sanitized failure boundary | §6.1–§7 + tests |
| no RBAC/protected/live scope expansion | §4 + §10–§13 |

## 15. Implementation authorization gate

This document is the design source of truth for R3-02 once approved by the
operator after repository review. It does not itself authorize runtime/test
implementation.

After written-spec approval, the next step is to use the Superpowers
`writing-plans` workflow to produce the detailed implementation plan from this
design. Runtime/test edits begin only after that implementation plan is
explicitly approved by the operator.
