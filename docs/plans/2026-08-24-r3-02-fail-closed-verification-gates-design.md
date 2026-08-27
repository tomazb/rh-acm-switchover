# R3-02 Fail-Closed Verification Gates Design

**Status:** Revised design candidate; implementation not yet authorized  
**Original date:** 2026-08-24  
**Revised:** 2026-08-26  
**Base:** `ansible@3dc6778814c1e457b064e97654b6b66f03554119`  
**Governing issue:** #272  
**Findings:** `R3-A4`, `R3-A5`, `GLM-H12`

## 1. Purpose

R3-02 corrects three verification paths that can currently treat failed or
unproven Kubernetes reads as success or a benign skip:

1. primary-prep Thanos compactor Pod drain verification (`R3-A4`);
2. preflight primary/secondary hub API-connectivity reporting (`R3-A5`); and
3. activation auto-import strategy lookup before immediate-import annotation
   management (`GLM-H12`).

The operator-facing contract is that a safety decision passes only from positive
evidence. A genuinely absent object, a valid empty inventory, and a failed or
unverifiable read are different outcomes and must remain distinguishable through
the decision boundary.

This revision corrects an ambiguity in the 2026-08-24 candidate. The earlier
design treated a successful-looking `kubernetes.core.k8s_info` result with
`resources: []` as sufficient evidence for an empty Pod inventory or an absent
ConfigMap. Current `kubernetes.core` behavior can normalize at least some
non-success paths, including `BadRequestError`, into the same empty-resource
shape. Therefore `resources: []` from `k8s_info` alone is not strong enough for
those two fail-closed decisions.

The revised design keeps `k8s_info` for connectivity, where success requires the
exact expected Namespace object, and introduces one narrow Collection-local
read-outcome primitive for the compactor and auto-import paths. That primitive
preserves the distinction that `k8s_info` loses; it does not own phase policy,
reporting, retries, or mutation decisions and is not a generalized validation
framework.

The slice remains parity-sensitive because preflight, primary prep, and
activation are all `dual-supported` capabilities. Current Python activation also
warns and skips when the auto-import ConfigMap read fails, so `GLM-H12` requires
a parity-preserving Python correction. No intentional parity divergence is
proposed.

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

Python already fails closed at this boundary: it calls `get_pods()` inside the
bounded wait, propagates API failures, and raises if Pod termination cannot be
established. No Python runtime change is needed for `R3-A4`.

### 2.2 `R3-A5`: connectivity pass is derived from a masked failure flag

`roles/preflight/tasks/validate_kubeconfigs.yml` probes the `default` Namespace
on each required hub with `failed_when: false`, then derives report status from
the registered result's `.failed` value. That flag is not positive proof of API
reachability after failure masking. Wrong servers, expired credentials, DNS/TLS
or transport failures, and malformed results can therefore be published as
connectivity `pass` findings.

The preflight architecture already has the correct failure owner: collect both
hub findings, write the structured report, recompute aggregate status, and only
then fail on critical findings. R3-02 must preserve that complete-reporting
behavior.

### 2.3 `GLM-H12`: auto-import read errors are treated like an unavailable feature

`roles/activation/tasks/apply_immediate_import.yml` reads the named
`multicluster-engine/import-controller-config` ConfigMap with
`failed_when: false`.

The current path treats an undefined `resources` field as
`reason: autoImportStrategy_unavailable`, `skipped: true`, and continues. That
launders an API/RBAC/transport failure into a benign skip.

The Python CLI has the same operator-level weakness. `KubeClient.get_configmap()`
already maps a true 404 to `None` and propagates non-404 failures after its retry
contract. `SecondaryActivation._get_auto_import_strategy()` then catches an
arbitrary exception and returns `"error"`, while
`_apply_immediate_import_annotations()` converts that sentinel into a warning
and skip. A Collection-only correction would create parity drift.

### 2.4 Review-discovered `k8s_info` ambiguity

The original design assumed that a defined empty `resources` list from
`kubernetes.core.k8s_info` positively proved either an empty inventory or named
object absence. That is not a safe assumption for R3-02.

At design review, current `kubernetes.core` 6.x implementation evidence shows
that `K8sService.find()` initializes an ordinary successful result as:

```text
{"resources": [], "api_found": true}
```

and returns that same result directly on `BadRequestError`. It also returns
`resources: []` with `api_found: false` when the requested API resource cannot
be mapped. A named 404 is handled inside the waiter and also reaches the caller
as an empty resource result. The Ansible task therefore cannot reconstruct the
critical distinction between a genuine named 404 and every normalized
non-success by inspecting `resources` alone.

Consequences:

- compactor drain cannot treat `k8s_info.resources == []` alone as positive
  evidence that the Pod list request succeeded and returned no Pods;
- auto-import cannot treat `k8s_info.resources == []` alone as positive evidence
  that `import-controller-config` is genuinely absent;
- connectivity remains safe with `k8s_info` when `pass` requires the exact
  expected Namespace object, because any empty result is a failure rather than a
  successful connectivity verdict.

The five directly affected runtime owners are unchanged from the original
design base at this revision base, so this correction is a design refinement,
not a response to intervening runtime drift.

## 3. Goals

R3-02 must:

- require positive evidence for every corrected pass/continue decision;
- preserve bounded compactor retry behavior without nested retry amplification;
- preserve complete preflight reporting for connectivity failures;
- distinguish a genuine named ConfigMap 404 from API discovery, bad-request,
  RBAC, transport, timeout, malformed-response, and equivalent failures;
- distinguish a successful empty Pod list from every unverified Pod read;
- fail activation before ManagedCluster discovery or annotation mutation when
  auto-import strategy cannot be verified;
- keep Python and Collection operator decisions aligned for auto-import;
- keep dry-run and native Ansible check mode non-mutating;
- preserve hub/context/namespace/resource targeting, checkpoint semantics, phase
  ordering, retry bounds, and accurate `changed` reporting;
- use stable sanitized public failures without copying raw API/module exception
  detail into reports or newly introduced messages;
- add runtime tests that execute the shipped task paths, using static tests only
  as supplemental regression guardrails.

## 4. Non-goals

This slice does not:

- change RBAC permissions or manifests;
- redesign preflight reporting;
- change checkpoint schemas or `reset_from` behavior;
- address `R3-01b`, `R3-03`, `R3-06`, R4 transaction/evidence work, or broader
  SSA work;
- implement the repository-wide API/exception logging cleanup owned by
  `SSA-09`;
- redesign `_maybe_set_auto_import_strategy()` transaction ownership;
- add a generic verification framework or general Kubernetes abstraction;
- change release/lab-controller behavior or create live certification evidence;
- add a public workflow variable, new retry knob, checkpoint field, or report
  schema field;
- modify `docs/ACM_SWITCHOVER_RUNBOOK.md` or `.claude/skills/**`.

If implementation analysis shows that a new Kubernetes permission is required,
the builder stops for explicit scope approval. None is expected: the corrected
reads target APIs already used by shipped code and represented by current RBAC
requirements.

## 5. Approaches considered

### 5.1 Approach A — `k8s_info` plus positive result-shape checks

Keep `failed_when: false` where aggregation/retry needs it and require an
expected successful result shape instead of trusting `.failed` or
`default([])`.

**Selected for connectivity only.** Connectivity can require one exact
`default` Namespace object, so an empty or malformed result cannot become a
pass.

**Rejected for compactor and auto-import.** `k8s_info` has already normalized
some semantically different outcomes before the registered task result exists.
No Jinja predicate can recover whether an empty result came from a valid empty
list, a true named 404, or a normalized `BadRequestError` when those outcomes
share the same returned shape.

### 5.2 Approach B — `block`/`rescue` around `k8s_info`

Remove failure masking and catch errors structurally.

This is not sufficient for the two ambiguous paths. `block`/`rescue` can catch
an Ansible task failure, but it cannot recover information already normalized
inside `k8s_info` into a non-failed empty result. It would also complicate
preflight aggregation and the compactor's single bounded retry owner without
solving the underlying ambiguity.

### 5.3 Approach C — one narrow Collection read-outcome primitive

Add one read-only Collection support module, `acm_k8s_read_outcome`, used only
where R3-02 requires an outcome distinction that `k8s_info` cannot preserve.
Reuse `kubernetes.core` authentication/client construction so kubeconfig,
context, TLS, proxy, and authentication semantics are not reimplemented. Bypass
the `K8sService.find()` normalization layer for the actual read and preserve a
small explicit outcome contract.

**Selected for compactor and auto-import.** This is the smallest current-tree
solution that retains the missing information while keeping decision policy in
the existing roles.

The module is deliberately not a validation subsystem. It does not know about
ACM phases, compactor semantics, auto-import strategy, reports, checkpoints, or
mutation authorization. It performs one read attempt and returns a lossless,
sanitized classification to its caller.

### 5.4 Approach D — separate compactor and ConfigMap reader modules

Two path-specific modules would avoid a generic-looking interface, but both
would duplicate kubeconfig/client construction, exception classification, and
sanitization mechanics. That is unnecessary duplication within one form factor.
The single narrow read primitive has the smaller auditable ownership boundary.

## 6. Selected design

### 6.1 Collection read-outcome primitive

Add `plugins/modules/acm_k8s_read_outcome.py` as a read-only support primitive.
The implementation plan may add a tiny module-utils collaborator only if needed
to keep authentication/client construction testable; it must not create a
second validation framework.

The module reuses the existing `kubernetes.core` dependency's authentication and
client construction rather than parsing kubeconfig credentials itself. It
supports only the input needed by these existing reads:

- read mode: named `get` or collection `list`;
- `api_version` and `kind`;
- namespace;
- name for named `get`;
- label selectors for `list`;
- the same kubeconfig/context authentication inputs currently supplied to
  `k8s_info`.

It supports native check mode, is always read-only, and always reports
`changed: false`.

It performs exactly one Kubernetes read attempt per module invocation. It adds
no internal retry loop. The existing caller owns retry policy, which is
important for preserving the compactor's current bounded wait rather than
multiplying it by a second retry contract.

The returned semantic contract is:

1. **`read_status: ok`** — the requested API read completed successfully.
   `resources` is a list. A list read may legitimately return zero resources. A
   named get returns exactly one resource.
2. **`read_status: not_found`** — only a named get that receives an explicit
   Kubernetes NotFound/404. `resources` is an empty list. A list operation never
   maps a 404 to `not_found`; inability to list the intended collection is not
   an empty-inventory proof.
3. **`read_status: error`** — API discovery failure, BadRequest, Forbidden/RBAC,
   timeout, connection/transport failure, client construction failure,
   malformed/unexpected response, or another non-success. `resources` is not a
   success predicate and callers must not treat it as one.

The module does not return raw exception text, API response bodies, kubeconfig
content/path, tokens, certificates, or authorization headers. Call sites also
use `no_log: true` as defense in depth. The role/report owner emits the stable
operator message.

Unit tests for this primitive are part of R3-02 because its outcome semantics
are safety-critical. In particular, an explicit BadRequest must be pinned as
`error`, a named 404 as `not_found`, and a successful empty list as `ok` with an
empty `resources` list.

### 6.2 Compactor termination: only `ok + empty Pod list` means drained

Keep the existing scale-down target and the production `30 x 10s` bounded
verification loop in
`roles/primary_prep/tasks/scale_observability.yml`.

Replace the ambiguous Pod `k8s_info` capture with
`acm_k8s_read_outcome` in list mode for:

- `apiVersion: v1`;
- `kind: Pod`;
- namespace `open-cluster-management-observability`;
- label selector `app.kubernetes.io/name=thanos-compact`;
- the existing primary kubeconfig/context.

The `until` predicate succeeds only when:

- `read_status == "ok"`;
- `resources` is present and is a list; and
- that list is empty.

A successful non-empty list remains pending and consumes another attempt under
the existing bounded wait. `read_status: error` also remains pending and consumes
another attempt, allowing transient read failures to recover without adding a
nested retry layer. List-mode `not_found` is invalid by module contract and is
treated as unverified/error if it appears.

After the bounded loop, the existing role owns two stable failure classes:

1. final `read_status != "ok"` or malformed evidence => fail with a sanitized
   message equivalent to `Unable to verify Thanos compactor pod termination
   after scale-down; verify API access and retry`;
2. final `read_status == "ok"` with non-empty resources => preserve the current
   count-oriented `Thanos compactor still has N pod(s)` failure.

The downstream decision must not use task `.failed`, `is failed`, or
`resources | default([])` as verification evidence.

No change is made to scale target, namespace, selector, retry count, delay,
execute/dry-run branching, or the successful observability scale result.

### 6.3 Connectivity: exact Namespace evidence remains sufficient

Keep `kubernetes.core.k8s_info` for the primary and secondary connectivity
probes because this decision does not need to distinguish one empty-result
cause from another. Every empty or malformed result is a connectivity failure.

Each hub reports `pass` only when all of the following are true:

- the registered result is a mapping;
- `api_found` is explicitly true;
- `resources` exists and is a list;
- the list contains exactly one resource;
- the resource is a mapping with `kind: Namespace`; and
- `metadata.name` is exactly `default`.

Anything else produces that hub's existing critical connectivity finding with
`status: fail` and stable message `<role> hub API connectivity probe failed`.

The probe tasks use `no_log: true`. Result IDs, severity, safe context detail,
restore-only primary skip, report schema, and aggregation owner remain
unchanged. Both hub findings still reach `preflight-report.json` before the
existing aggregate critical-failure stop.

No Python change is required for `R3-A5`; current Python bootstrap/preflight
already fails closed on unavailable hub access rather than manufacturing a pass.

### 6.4 Collection auto-import: only explicit named 404 means absence

Keep the auto-import strategy decision in
`roles/activation/tasks/apply_immediate_import.yml` before any ManagedCluster
read or annotation patch, but use `acm_k8s_read_outcome` in named-get mode for:

- `apiVersion: v1`;
- `kind: ConfigMap`;
- name `import-controller-config`;
- namespace `multicluster-engine`;
- the existing secondary kubeconfig/context.

Classify the result as follows.

#### Explicit absence

`read_status: not_found` is the only outcome that means the ConfigMap is absent.
The effective strategy is the existing ACM default `ImportOnly`, so the current
immediate-import annotation path remains reachable.

#### Valid present ConfigMap

`read_status: ok` is accepted only when:

- exactly one resource is returned;
- it is a mapping with `apiVersion: v1` and `kind: ConfigMap`;
- `metadata.name == "import-controller-config"`;
- `metadata.namespace == "multicluster-engine"`; and
- `data` is absent, null, or a mapping.

Absent/null `data` is normalized to an empty mapping for this read-only decision.
The existing `autoImportStrategy` defaulting behavior then applies.

A wrong identity, multiple or zero resources under `ok`, a non-mapping object,
or non-mapping non-null `data` is malformed evidence and fails closed.

#### Read failure

`read_status: error` fails activation with the exact stable message:

```text
Unable to verify autoImportStrategy on the destination hub; verify API access and retry.
```

The failure occurs before:

- listing ManagedClusters for immediate-import work;
- clearing a stale non-empty immediate-import annotation; or
- applying the empty immediate-import trigger annotation.

The existing execute-mode `reason: autoImportStrategy_unavailable` result is no
longer used for genuine read failures. Dry-run and unsupported-ACM-version skip
results retain their existing meanings.

### 6.5 Python auto-import: preserve 404/error distinction and validate evidence

Python must make the same operator decision as the Collection for the shared
activation behavior.

Add `KubeClient.get_configmap_advisory(namespace, name)` following the existing
`get_custom_resource_advisory()` pattern:

- true 404 => `None`;
- retryable failures retain the existing bounded Python client retry policy;
- non-404 failure propagates;
- the advisory retry/final path does not render raw exception text into its own
  logs.

`SecondaryActivation._get_auto_import_strategy()` then has two normal outcomes:

- ConfigMap absent => `"default"`;
- ConfigMap present and valid => normalized configured strategy.

A present ConfigMap is valid only when it is a mapping whose metadata identifies
`multicluster-engine/import-controller-config`, and whose `data` is absent,
null, or a mapping. Malformed present evidence fails closed rather than reaching
an incidental `AttributeError` or an unknown-strategy skip.

A genuine read failure or malformed present object raises `FatalError` with the
same exact stable message used by the Collection:

```text
Unable to verify autoImportStrategy on the destination hub; verify API access and retry.
```

The raw exception may be retained as the exception cause but is not interpolated
into the public message. `_apply_immediate_import_annotations()` no longer
receives an `"error"` sentinel and therefore cannot treat an unverified strategy
as a benign skip.

The failure occurs before ManagedCluster listing or
`patch_managed_cluster()`.

This correction does not redesign `_maybe_set_auto_import_strategy()`; that
broader transaction remains under its existing R4 ownership.

### 6.6 Dry-run and native Ansible check mode

R3-02 changes read/decision semantics, not mutation authorization.

- Collection `dry_run` continues to skip compactor scale-down and immediate-import
  annotation management as it does today; no new mutation is introduced.
- Native Ansible check mode with `execution.mode: execute` may execute the
  read-only verification needed for a safety decision. The new read primitive
  reports `changed: false` and performs no mutation.
- Connectivity remains read-only in all relevant modes.
- Python dry-run retains existing KubeClient mutation guards. Any auto-import
  verification failure remains a failure decision but cannot cause a
  ManagedCluster mutation.

No checkpoint transition semantics change.

## 7. Error and information-flow contract

The corrected Collection flow is:

```text
Kubernetes read
    |
    v
lossless read outcome
(ok / named-not-found / error)
    |
    +-------------------------+
    |                         |
positive evidence          error/unverified
    |                         |
    v                         v
existing success /        stable sanitized fail
pending / valid absence   owned by current role/report
```

Rules:

- task-level success is not verification success;
- `resources` alone is never the outcome classifier for the two ambiguous paths;
- an empty Pod list is accepted only under `read_status: ok`;
- ConfigMap absence is accepted only under `read_status: not_found` from a named
  get;
- `BadRequest`, API discovery failure, RBAC denial, timeout, transport failure,
  and malformed evidence are never mapped to absence or empty-success;
- masked Ansible `.failed` state is never used as proof of success;
- raw captured exception/module detail is not copied into newly introduced
  public messages or report fields;
- the slice does not claim repository-wide exception/log redaction beyond these
  corrected boundaries.

## 8. Parity contract

The parity matrix keeps preflight, primary prep, and activation as
`dual-supported`.

Expected shared operator decisions after implementation:

| Scenario | Python | Collection |
| --- | --- | --- |
| compactor Pod verification API failure | fail closed | fail closed |
| verified compactor Pod inventory empty | continue | continue |
| required hub unreachable during validation | fail closed | critical preflight fail |
| auto-import ConfigMap explicit 404/absence | use default strategy | use default strategy |
| auto-import ConfigMap present and valid | classify configured strategy | classify configured strategy |
| auto-import ConfigMap bad request / API / RBAC / transport failure | activation failure before annotation mutation | activation failure before annotation mutation |
| malformed auto-import ConfigMap evidence | activation failure before annotation mutation | activation failure before annotation mutation |

The form factors remain independent and do not cross-import runtime code.
Behavioral parity is held by tests and documentation.

The existing intentional read-failure divergence for finalization
reset-obligation handling is outside R3-02 and remains unchanged.

## 9. Testing strategy

Runtime tests are the primary proof. Static/source tests are supplemental.

### 9.1 Read-outcome primitive unit contract

Add focused Collection unit coverage proving:

- successful list with zero objects => `read_status: ok`, empty resources;
- successful list with objects => `read_status: ok`, resources preserved;
- named get present => `read_status: ok`, exactly one resource;
- named get explicit 404 => `read_status: not_found`;
- list-path 404 => `read_status: error`, not empty success;
- BadRequest/400 => `read_status: error`;
- Forbidden/403 => `read_status: error`;
- timeout/connection/transport failure => `read_status: error`;
- API-resource discovery failure => `read_status: error`;
- malformed/unexpected client result => `read_status: error`;
- `changed` is always false and check mode remains read-only;
- raw sensitive exception sentinels are absent from returned/public fields.

This unit contract is required because it is the semantic barrier that replaces
the lossy `k8s_info` result for two safety decisions.

### 9.2 Collection compactor runtime

Execute the shipped primary-prep task path with a focused fixture. Required
cases:

1. `ok + []` => verification succeeds;
2. `ok + non-empty Pods` => remains pending and follows the existing bounded
   retry behavior;
3. 403-style read failure => never satisfies `until`, fails closed;
4. BadRequest-style read failure => never becomes empty/drained and fails
   closed;
5. connection/timeout-style failure => same fail-closed decision;
6. malformed helper result => fail closed;
7. failure payload containing credential/API-body sentinels => no sentinel in
   public output.

The test harness may shorten waits only through fixture/test mechanics. It must
not add or widen a production retry variable. Production remains `30 x 10s`.

### 9.3 Collection connectivity reporting

Extend the existing preflight integration harness for both hub roles.

Required cases:

- exact `default` Namespace response => that hub reports `pass`;
- wrong server/unreachable/authentication failure => `fail`;
- BadRequest/empty-result normalization => `fail`, never `pass`;
- `api_found: false` => `fail`;
- malformed or wrong Namespace identity => `fail`;
- failure reaches `preflight-report.json` with existing result ID and critical
  severity;
- normal runs exercise primary and secondary independently;
- restore-only preserves secondary-only behavior;
- raw sensitive sentinels are absent from callback output/report fields.

### 9.4 Collection auto-import runtime

Execute the shipped activation task path with coverage for:

- named ConfigMap explicit 404 => default behavior remains reachable;
- valid ConfigMap with ImportOnly/default => annotation path remains reachable;
- valid ConfigMap with non-applicable strategy => existing no-annotation path;
- BadRequest/400 => activation fails before ManagedCluster discovery or patch;
- Forbidden/403, API, timeout, and transport errors => same fail-closed barrier;
- wrong object identity or malformed `data` => fail closed;
- native check mode => verification reads may occur, but no annotation mutation.

The fake API/harness records ManagedCluster list/patch calls so every refused run
proves zero relevant mutation after the barrier.

### 9.5 Python auto-import parity

Python tests cover:

- advisory ConfigMap 404 => `None` => default strategy;
- valid present ConfigMap => configured strategy;
- malformed metadata/data => exact stable `FatalError` before ManagedCluster
  listing;
- API/transport exception with a sensitive sentinel => exact stable `FatalError`
  with no sentinel in public logs/message;
- failure before `list_custom_resources(... ManagedCluster ...)`;
- failure before `patch_managed_cluster()`;
- dry-run remains non-mutating.

`tests/test_kube_client.py` pins `get_configmap_advisory()`: 404 => `None`,
retryable/non-404 errors propagate, retries remain bounded, and advisory retry
logging does not render raw exception content.

### 9.6 Static and parity guardrails

Add only discriminating static assertions:

- compactor `until` requires `read_status == "ok"` and an empty list;
- compactor does not use `.failed` or `default([])` as drain proof;
- connectivity pass requires exact Namespace positive evidence rather than
  `.failed == false`;
- auto-import default behavior is entered only from explicit `not_found` or a
  valid present ConfigMap whose strategy defaults to ImportOnly;
- an explicit failure barrier precedes ManagedCluster tasks;
- Python and Collection regression tests both pin ConfigMap read failure =>
  activation failure.

Do not build a generalized cross-language adapter.

## 10. Expected implementation surface

The implementation plan starts from this likely surface and adds files only when
a test/documentation contract requires them.

### Collection runtime

- `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_k8s_read_outcome.py`
- `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/scale_observability.yml`
- `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_kubeconfigs.yml`
- `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/apply_immediate_import.yml`

A small Collection `module_utils` collaborator is permitted only if the
implementation plan proves it is the smallest way to reuse `kubernetes.core`
auth/client construction and test outcome classification. Do not introduce a
second kubeconfig parser or authentication stack.

### Python runtime

- `modules/activation.py`
- `lib/kube_client.py` for `get_configmap_advisory()`

### Tests

- Collection unit tests for `acm_k8s_read_outcome` and task contracts;
- Collection integration fixtures/tests for primary prep, preflight, and
  activation;
- directly affected scenario/check-mode tests;
- `tests/test_activation.py`;
- `tests/test_kube_client.py`;
- minimal parity/static tests where they add discriminating value.

### Documentation

The future implementation PR updates:

- `CHANGELOG.md` `[Unreleased]`;
- `thermos-resolution-plan.md` R3-02 status/evidence;
- unprotected operator/developer docs only where visible failure behavior is
  otherwise inaccurate.

No released version identifier changes. Protected files remain untouched.

## 11. Verification matrix

The implementation plan must re-read current `AGENTS.md`, compatibility policy,
and resolved `kubernetes.core` version at its own base before naming exact
commands or import assumptions.

At this revision base, repository-tested Collection endpoint lanes remain:

- `ansible-core 2.16.*` / Python 3.11;
- `ansible-core 2.21.*` / Python 3.12.

The new read primitive must execute under both lanes. Its reuse of
`kubernetes.core` auth/client construction is therefore validated by runtime
coverage in both endpoint lanes, not only by an import/source assertion.

Verification order is targeted-first:

1. focused read-outcome module tests;
2. focused Python activation/KubeClient tests;
3. focused Collection task/unit contracts for R3-02;
4. focused Collection executable integration tests for compactor, connectivity,
   and activation failure boundaries;
5. directly affected scenario/check-mode tests;
6. combined Python/Collection parity-sensitive surface;
7. documentation/static guardrails and formatting/linting;
8. every broader gate invalidated by the final implementation diff under the
   then-current `AGENTS.md` verification matrix.

Fake APIs and fixture adapters are non-live evidence and must not be described
as ACM certification evidence.

## 12. Pre-PR simplification gate

Before a future R3-02 implementation PR is opened, review changed code and
immediate collaborators for avoidable complexity.

For the new read primitive specifically, reject accidental framework growth:
there should be one small read contract, no policy engine, no report ownership,
no mutation methods, no retry configuration, and no unrelated Kubernetes
abstraction. If only the two R3-02 consumers need it, do not expand it for
hypothetical future users.

The PR description records simplifications applied, or explicitly records that
no safe in-scope simplification was available.

## 13. Rollback and compatibility boundary

R3-02 is a fail-closed correctness change. Rollback means reverting the future
implementation commits; no persisted-state or schema migration is introduced.

The design adds no:

- checkpoint field;
- report schema field;
- public workflow variable;
- Kubernetes permission;
- phase-ordering change;
- release-profile field;
- nested retry budget.

The Collection support module is read-only and has no persisted state. Reverting
it and its two consumers restores the prior behavior without data migration.

## 14. Acceptance mapping

| Governing acceptance requirement | Design owner |
| --- | --- |
| compactor 403/timeout/connection/equivalent error cannot satisfy drain | §6.1–§6.2 + §9.1–§9.2 |
| compactor BadRequest cannot become empty/drained | §2.4 + §6.1–§6.2 + §9.2 |
| valid empty compactor inventory succeeds | §6.2 + §9.2 |
| primary/secondary connectivity failure reaches report | §6.3 + §9.3 |
| successful connectivity still passes | §6.3 + §9.3 |
| ConfigMap genuine absence remains default behavior | §6.1 + §6.4 + §9.1 + §9.4 |
| ConfigMap BadRequest/API/RBAC/transport error fails before annotation mutation | §6.4 + §9.4 |
| malformed auto-import evidence fails before mutation | §6.4–§6.5 + §9.4–§9.5 |
| Python/Collection auto-import decision aligned | §6.4–§8 + §9.4–§9.5 |
| dry-run/check mode non-mutating | §6.6 + tests |
| stable sanitized failure boundary | §6.1–§7 + tests |
| no RBAC/protected/live scope expansion | §4 + §10–§13 |

## 15. Design self-review

This revision resolves the ambiguity that blocked approval of the 2026-08-24
candidate:

- no pass/continue decision relies on `k8s_info.resources == []` where an empty
  result has more than one semantic cause;
- connectivity remains on the simpler existing module because exact positive
  object evidence is sufficient there;
- the new Collection primitive preserves read outcome only and does not own
  phase policy or grow into a validation framework;
- compactor retry ownership remains exactly one bounded task-level loop;
- ConfigMap absence is an explicit named 404, not an empty-list inference;
- Python and Collection share the same activation fail/continue decision while
  remaining independent codebases;
- all newly introduced public failures are stable and sanitized;
- no protected file, RBAC expansion, release/lab behavior, or live execution is
  authorized by this design.

No unresolved `TBD` or `TODO` remains in the design. The exact internal import
path used to reuse `kubernetes.core` authentication/client construction belongs
in the implementation plan after it re-reads the then-resolved 6.x dependency;
the architectural requirement is fixed here: reuse that dependency's client
construction and do not implement a parallel auth stack.

## 16. Implementation authorization gate

This document becomes the R3-02 design source of truth only after operator
approval of this revised candidate.

This design revision does **not** authorize production code, test implementation,
live cluster access, a pull request, protected-file edits, or merge activity.

After operator approval, the next governed step is the Superpowers
`writing-plans` workflow to produce the detailed implementation plan from this
revision. Runtime/test edits begin only after that plan is explicitly approved
by the operator.