# Collection strict-read route confirmation (#320, #322) — design

- **Status:** accepted for implementation under the operator authorization for #320 and #322
  (strict-read prerequisite convergence before R4-04).
- **Base:** `origin/ansible` `70d422bbce058f27d52105143d5f2d66b53991ca`.
- **Governing issues:** #320 (stale cached scope on a namespaced LIST), #322 (successful
  custom-resource named GET published without a live discovery proof).
- **Context only:** #317 / PR #319 (named-404 classifier), #321 (Python `groupVersion`),
  #323 (cross-group fallback on the success path), #282 (name/kind consistency).
- **Upstream authorities:** `AGENTS.md` "Execution-time discovery";
  `docs/plans/2026-07-29-decommission-completion-design.md` §3 "Shared strict-list contract"
  (no implicit fallback to a different group or version; `object_absent` only after discovery
  succeeded); `docs/plans/2026-08-31-r4-03-current-base-design-amendment.md`;
  `ansible_collections/tomazb/acm_switchover/docs/coexistence.md` (the three #317
  divergences).

## 1. Problem

`plugins/module_utils/k8s_read.py::strict_read` is the collection's one strict read. It
resolves the requested kind through kubernetes.core (`api_client.resource(kind, api_version)`),
which can answer from its shared on-disk discovery cache, and the dynamic client then builds
the request route from the resolved resource's `group_version`, `name` (plural) and
`namespaced` flag. Live discovery is read only after a resolution failure and after a named
404 (#317). The success paths trust the resolved route unconditionally:

1. **#320.** `Resource.path` uses the namespaced URL only `if self.namespaced and namespace`.
   A cached `namespaced=False` for a namespaced kind drops the namespace, the LIST goes to the
   cluster-wide URL, and the cluster-wide inventory is published as `ok` for the requested
   namespace. An empty cluster-wide answer becomes a positive empty-inventory proof.
2. **#322.** For a custom resource, Python proves the resource name served by live discovery
   before any object request (`_discovery_serves`), so unreadable discovery is `ERROR`. The
   collection publishes a successful GET as `ok` without reading discovery. The same gap
   exists for a custom-resource LIST: Python returns `ERROR` for unreadable discovery and
   `CRD_ABSENT` for an omitted resource name, while the collection publishes `ok` from the
   cached route (or `error` when the stale route 404s).

## 2. Invariant

> A live strict read publishes `ok` only for a request routed to exactly the requested
> group/version, canonical resource name and scope. For a custom resource, that route must
> also be confirmed by a current, readable live discovery document for the requested
> group/version before the object request is sent, exactly as Python requires.

Consequences:

- The dynamic client's resolution may not silently change the requested group/version or
  resource name (#317's fallback, #323's cross-group fallback).
- A namespaced request is never routed to a cluster-wide URL.
- Discovery, authorization, transport and decode failure is never absence and never a
  success.

## 3. Alternatives considered

| Option | Verdict |
| --- | --- |
| **A. Confirm the resolved route; fail closed on mismatch** (this design) | Chosen. No new abstraction, no cache writes, one bounded request added only where Python already reads discovery. |
| B. Build the request route from the request (bypass the resolved `Resource`) | Rejected. Reverses the recorded #317 posture ("the route is not retried or rerouted"), couples the collection to `DynamicClient.request` internals across the supported client range, and would rewrite all three approved #317 divergence records. |
| C. `invalidate_cache()` per read | Rejected in #319: unbounded discovery requests, rewrites the shared cache file, and leaves a window between discovery and the request. |
| D. Post-read membership check (`item.metadata.namespace == namespace`) | Not adopted. It fires only on a non-conformant server, Python has no mirror (a new divergence of the #317 `groupVersion` shape), and the scope guard in §4.1 already prevents the cluster-wide route; a 200 at the namespaced URL is the server's own evidence of namespaced scope and of namespace-filtered membership. |
| E. Pre-request discovery for every kind, built-ins included | Rejected. Python's typed built-in reads (`get_namespace_strict`, `list_pods_strict`, `get_deployment_strict`, `get_replicaset_strict`, the `import-controller-config` ConfigMap read) never read discovery. Adding it would make unreadable `/api/v1` or `/apis/apps/v1` discovery an `error` in the collection while Python succeeds: a new live-state divergence. |

## 4. Design

### 4.1 Local route-identity guard (every kind, both read modes, no request)

Immediately after successful resolution and before any request, `strict_read` requires:

- `resource.group_version == api_version` (no cross-group or cross-version fallback);
- `resource.name == resource_name` (the canonical plural the caller named);
- `resource.namespaced` is a `bool`;
- when a `namespace` is requested, `resource.namespaced is True`.

Any failure returns `error` with no object request. Identity failure is not routed to the
`kind_not_served` proof: the requested kind *was* resolved, just to a route that does not
match the request, and `error` matches the existing named-404 posture (#317).

Not checked here: `resource.kind == kind` (kubernetes.core resolves by kind, then name,
singular name and short names; name/kind consistency is #282's scope), and a namespaced kind
read with no namespace (a LIST without a namespace is a legitimate all-namespaces read; the
named-404 rule for it is unchanged).

### 4.2 Discovery before the request for custom resources

The built-in boundary mirrors Python's typed clients exactly. A new collection constant
`STRICT_READ_BUILTIN_API_VERSIONS = ("v1", "apps/v1")` in the collection's
`module_utils/constants.py` names the group/versions Python reads through fixed typed routes
without discovery. It is collection-only, like `STRICT_READ_REQUEST_TIMEOUT`: Python expresses
the same boundary by which method it calls, so the constant is not added to the shared-constant
mapping in `tests/test_constants_parity.py`. Its comment names the Python typed readers it
mirrors, and a parity-module test pins its value.

For any other `api_version` (a custom resource), after §4.1 and before the object request:

1. Read `_live_discovery_resources(api_client, api_version)` — the existing prover, with its
   whole-document validation and `groupVersion` check. `None` → `error`.
2. No entry named `resource_name` → `kind_not_served` (Python: `CRD_ABSENT`, no GET).
3. Only then send the GET or the first LIST page.

The live entry's `namespaced` field gates nothing before the request (amended after pre-PR
review, see §10). §4.1 already refuses a namespaced request on a cluster-scoped route, and the
server answers every other scope mismatch at the route itself: a namespaced URL for a
cluster-scoped kind and a cluster URL for a named namespaced object are 404 (verified read-only
on `prod2`), and a LIST with no namespace is the same all-namespaces read Python makes. A
namespaced 200 is therefore the server's own evidence of the requested scope. Gating on the
field would only fail, on a non-conformant document that omits or mistypes it, a read Python
completes — a new divergence.

No `entry["kind"] == kind` check is added before the request (Python has none; #282). The
existing named-404 kind check is unchanged.

### 4.3 Named GET 404

For a custom resource the discovery entries read in §4.2 are passed to `_named_404_status`
instead of being re-read, so a custom named 404 costs the same two requests it costs today.
Its rules are unchanged: requested kind on the served entry, live scope equal to the routed
scope, a namespaced kind read in a namespace → `not_found`; otherwise `error`. For built-ins
the post-404 live read is unchanged (#319).

### 4.4 Outcome table

`R` = resolved route, `L` = live discovery for the requested group/version. "Base" is the
collection at `70d422bb`.

| # | Case | Base | New collection | Python |
| --- | --- | --- | --- | --- |
| 1 | Built-in, R matches request, request 200 | `ok` | `ok` | `ITEMS` |
| 2 | Any kind, namespaced request, R cached `namespaced=False` (#320) | `ok` + cluster-wide superset | `error`, no request | `ITEMS` (fixed route) |
| 3 | Built-in, R group/version ≠ request (#323 fallback) | `ok` from foreign group | `error`, no request | `ITEMS` |
| 4 | Any kind, R plural ≠ `resource_name` | `ok` from R's route | `error`, no request | n/a (fixed route) |
| 5 | Custom, L unreadable/malformed, GET would 200 (#322) | `ok` | `error`, no object request | `ERROR` |
| 6 | Custom, L unreadable/malformed, LIST | `ok` | `error`, no object request | `ERROR` |
| 7 | Custom, L omits `resource_name`, R resolved from cache | GET: `ok` or `kind_not_served`; LIST: `ok` or `error` | `kind_not_served`, no object request | `CRD_ABSENT` |
| 8 | Custom, L lists it, request 200 | `ok` | `ok` (+1 discovery GET) | `ITEMS` |
| 9 | Custom, L lists it, L scope ≠ R scope, request allowed by §4.1 | GET 404 `error`; success `ok` | unchanged: the server answers at R's route; a 404 is classified by §4.3 | `ITEMS` / `OBJECT_ABSENT` |
| 10 | Custom, named 404, R = L, namespaced in namespace | `not_found` | `not_found` (no second discovery read) | `OBJECT_ABSENT` |
| 11 | Resolution failure | discovery proof | unchanged | n/a |
| 12 | Any auth/transport/decode/page failure | `error` | `error` | `ERROR` |

Rows 1, 5, 6, 7, 8, 10, 12 are equal on both form factors (row 9 keeps its base outcomes: equal
on success, the approved #317 named-404 divergence on a 404) and gain equality parity vectors
where none exists (5, 6, 7). Rows 2, 3 and 4 are reachable only when the resolved route
does not match the live API server (a stale or foreign cache entry, or kubernetes.core's
core-`v1` fallback after a core discovery read failed during resolution): the collection
fails closed where it previously published an inventory or object that was not the
requested one. See §6.

### 4.5 Request bounds and cache side effects

| Read | Base requests | New requests |
| --- | --- | --- |
| Built-in GET / LIST / named 404 | unchanged | unchanged |
| Custom named GET, 200 | 1 | 2 |
| Custom named GET, 404 | 2 | 2 |
| Custom LIST | pages | 1 + pages |
| Any §4.1 failure | ≥1 | 0 |

The added request uses the existing `STRICT_READ_REQUEST_TIMEOUT`. No discovery cache is
invalidated, written or refreshed, and no route is retried or rebuilt. Page and restart bounds
are unchanged. kubernetes.core's own resolution requests are unchanged.

### 4.6 Check mode and callers

`acm_k8s_read_outcome` and `acm_pod_owner_classify` are read-only and already run in check
mode; check mode gains the same one discovery GET for custom resources and no writes. Every
current consumer already treats `error` as fail-closed and `kind_not_served` as a
positive-absence outcome where it accepts one: MCO/MCH/ManagedCluster/ClusterDeployment/CSV
decommission reads, the destination observability gate, observability and MCH Pod drains,
primary-prep observability scale, and activation auto-import. None of them issues a different
request because of this change. The builder re-runs each consumer's tests.

### 4.7 Error output

All new outcomes are the existing sanitized `read_status` values with empty `resources` and a
null revision. No message, exception text or response body is added to module output.

## 5. Interaction boundaries

- **#317 / #319.** The named-404 classifier and its three approved divergences are unchanged
  in behavior. §4.1 now also enforces, before the request, the group/version and plural checks
  #319 applied only after a 404.
- **#321.** §4.2 uses the shared prover, which rejects a missing, empty, non-string or
  mismatched `groupVersion`. Until #321 aligns Python, rows 5–6 widen the approved #317
  `groupVersion` divergence to custom-resource success paths: a malformed `groupVersion` is
  `error` in the collection while Python reads the object or inventory. The condition is the
  one already approved (a malformed or non-conformant discovery document); #321 lands next in
  this tranche and removes it. Malformed-`groupVersion` equality vectors stay excluded until
  then.
- **#323.** §4.1's group/version check makes the cross-group success path `error`. That is an
  incidental effect of this invariant; #323 is not a governing scope here, it is commented on
  with evidence, and it is not closed by this slice.
- **#282.** No kind cross-check is added to any discovery proof.
- **Guard ordering.** §4.1 runs before §4.2, so a custom resource resolved to a non-canonical
  plural is `error` even when live discovery would show the kind unserved (Python:
  `CRD_ABSENT`). That is the case the approved third #317 paragraph already records ("the
  omitted-kind case stays aligned when the resolved plural is canonical"); row 7 is the
  aligned, canonical-plural case.

## 6. Parity consequences

For identical live API state and a resolved route that matches it, every outcome is equal on
both form factors, and three previously unequal cases (rows 5, 6, 7) become equal. The
remaining differences (rows 2, 3, 4, 9) exist only when the collection's resolved route does
not match the live server — a collection-internal input Python does not have. At base those
cases were already unequal and unsafe (the collection published another scope's, group's or
route's result as `ok`); they now fail closed. They are recorded in `coexistence.md`, the
parity matrix and the behavior map as a stale-or-foreign resolved-route outcome owned by
#320, in the same form as #317's third paragraph, with no equality vector. The coexistence
sentence claiming a stale cache cannot mismatch built-in scope is corrected: a foreign or
corrupted cache file can, and the collection now fails closed on it. No capability changes
status; both stay `dual-supported`.

Documentation surfaces updated with the code: the `acm_k8s_read_outcome` `RETURN` block
(`ok` for a custom resource now requires a live discovery confirmation, and `kind_not_served`
can be reported before any object request); the `k8s_read.py` module docstring; the
coexistence #321 `groupVersion` paragraph (the prover now also governs the pre-request proof,
still removed by #321); a new coexistence paragraph for #320; the parity matrix and behavior
map rows that describe strict reads; `CHANGELOG.md` `[Unreleased]`. `thermos-resolution-plan.md`
carries no row for #317/#319 and none is added for this non-Thermos follow-up; the R4-04
Task 0 assessment records the prerequisite state.

## 7. Tests

Unit (`tests/unit/test_k8s_read_outcome.py`, collection lane), each with a request log:

- stale `namespaced=False` on a namespaced LIST with foreign members → `error`, no LIST sent;
- the same with an empty cluster-wide answer → `error` (never a positive empty inventory);
- stale `namespaced=False` on a namespaced named GET → `error`, no GET sent;
- resolved group/version ≠ request, resolved plural ≠ `resource_name`, non-bool `namespaced`
  → `error`, no request (GET and LIST);
- #323's exact condition: requested `api_version="v1"`, resolved `Resource` with
  `prefix="apis"` and `group_version="foo.io/v1"`, request would return 200 → `error`, zero
  requests (GET and LIST). No runtime test is added for it (that would widen into #323);
- custom named GET, discovery 503 / undecodable / malformed entries / missing `groupVersion`,
  object would return 200 → `error`, no GET sent;
- custom LIST, same discovery failures → `error`, no LIST sent;
- custom GET and LIST, discovery omits the name → `kind_not_served`, no object request;
- custom, live `namespaced` true / missing / non-bool while the route is cluster-scoped and no
  namespace is requested → `ok` after exactly one discovery read (the field gates nothing);
- custom named 404 → `not_found` with exactly one discovery read (reuse);
- positive controls: built-in GET/LIST with no discovery read; custom GET/LIST with exactly
  one discovery read; complete pagination, 410 restart and snapshot revision unchanged.

Runtime (`tests/integration/test_k8s_read_outcome_runtime.py`, real `ansible-playbook` and
kubernetes.core through the shared TMPDIR cache, as in #317's regression):

- #320: both runs share one `_ansible_env(...)` (one TMPDIR, so one cache file). Run 1 is a
  ConfigMap named GET (`ok`, positive control) against an `/api/v1` document whose `pods`
  entry says `namespaced: false`; kubernetes.core caches the whole group/version document.
  The fake then serves Pods as namespaced. Run 2 is the namespaced Pod LIST. On run 2's
  request log: no `/api` and no `/apis` (cache precondition), neither `/api/v1/pods` nor
  `/api/v1/namespaces/test-ns/pods` (the guard fired before any request), and
  `READ_STATUS=error`. Kill condition: at base run 2 requests `/api/v1/pods` and publishes
  `ok` with the foreign-namespace Pod placed in `pod_list_body`.
- #322: run 1 caches ManagedCluster discovery; the fake then answers
  `/apis/cluster.open-cluster-management.io/v1` with 503; run 2 (named GET of an existing
  ManagedCluster) must be `error` and must not request the object. Base publishes `ok`.
- Custom LIST positive control and check-mode no-write assertion.

Parity (`tests/test_strict_read_parity.py`): add equality vectors for rows 5, 6 and 7 driven
through the real Python `KubeClient` and the real collection module with a *successfully
resolved* route; extend the header comment with the #320 divergence. Pin
`STRICT_READ_BUILTIN_API_VERSIONS` against the Python typed-reader set.

Downstream: rerun the collection unit tests for pod owner classification, decommission role
contracts, UID-guarded delete, activation and primary-prep auto-import, and the root parity
and decommission suites.

Every RED test is shown failing at base for the intended assertion before production code
changes; tests green on arrival carry a recorded kill condition.

Read-only live diagnostics on `prod2` (no ACM installed; diagnostic tier, not certification):
`/api/v1` and `/apis/apps/v1` declare their `groupVersion`, and a cluster-scoped kind read at a
namespaced path (`/apis/rbac.authorization.k8s.io/v1/namespaces/default/clusterroles`) returns
404 — the server itself refuses a scope-mismatched route, which §3-D relies on.

## 8. Supported-behavior preservation

Correctly routed reads keep their outcomes, resources and revisions. Built-in reads keep their
request sequence exactly. Custom reads add one discovery GET before the object request. The
module interface (`argument_spec`, return values, choices) is unchanged. No RBAC change: the
discovery GET is a non-resource URL read already performed by the same client for resolution
and after named 404s.

## 9. Out of scope

Python runtime (unchanged here; #321 follows), post-read membership checks, kind
cross-checks (#282), cache invalidation, rerouting, RBAC, protected files, live mutation.

## 10. Amendment after pre-PR review

The first candidate (`b7b80dc5`) also required, before a custom-resource request, the live
entry's `namespaced` to be a boolean equal to the resolved scope. Independent review showed it
adds no safety (§4.2) and fails closed where Python, which never reads the field, completes the
read when a non-conformant discovery document omits or mistypes it. It was removed before the PR
was opened; rows 8 and 9 of §4.4 and the §7 test list reflect the amended rule.
