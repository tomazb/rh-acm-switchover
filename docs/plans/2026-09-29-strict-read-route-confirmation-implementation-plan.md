# Strict-read route confirmation (#320, #322) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`)
> syntax for tracking.

**Goal:** make the collection's one strict read publish `ok` only for a request routed to
exactly the requested group/version, plural and scope, and, for custom resources, only after a
live discovery proof, mirroring Python.

**Architecture:** two checks added inside `plugins/module_utils/k8s_read.py::strict_read`
between resolution and the first request: a request-free route-identity guard for every kind,
and a pre-request live discovery proof for group/versions outside the Python typed-client set
(`v1`, `apps/v1`). The named-404 classifier reuses the pre-request discovery entries.

**Tech Stack:** Python 3.10–3.12, ansible-core 2.16+, kubernetes.core 6.x, python-kubernetes,
pytest.

**Spec:** `docs/plans/2026-09-29-strict-read-route-confirmation-design.md`

## Global Constraints

- Collection runtime only; no Python (`lib/`) runtime change (#321 follows).
- No RBAC, protected-file (`docs/ACM_SWITCHOVER_RUNBOOK.md`, `.claude/skills/**`) or
  live-cluster change.
- No discovery-cache invalidation, write, refresh, retry or reroute.
- No post-read membership check; no pre-request `kind` check (#282).
- Module interface (`argument_spec`, return keys, `read_status` choices) unchanged.
- New outcomes are existing sanitized statuses with `resources == []` and a null revision.
- `STRICT_READ_BUILTIN_API_VERSIONS = ("v1", "apps/v1")`, collection-only, not added to the
  shared mapping in `tests/test_constants_parity.py`.
- Black `--line-length 120`, isort, flake8 per `docs/development/testing.md`.

## Review Focus

1. A namespaced request whose resolved route is cluster-scoped must never send a request — not
   only never publish `ok` (a sent cluster-wide LIST is already an over-read).
2. An empty wrong-route answer must never surface as `ok` with zero items.
3. A custom named 404 must still read discovery exactly once (no regression to two reads).
4. Built-in reads must keep their exact request sequence (no discovery read on success).
5. Check mode must add no write and must still read.

---

### Task 1: Unit RED — route-identity guard (#320, #323 condition)

**Files:**
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_k8s_read_outcome.py`

**Interfaces:**
- Consumes: existing `_FakeClient`, `_FakeDynamicClient`, `_ResolvedResource`, `_DictResult`,
  `_page`, `_run_module`.
- Produces: tests only.

- [ ] **Step 1: Write the failing tests** (append after `test_a_named_get_is_bounded`)

```python
# #320 / design §4.1: the resolved route must be the requested route before any request is sent.

_FOREIGN_POD_PAGE = {
    "kind": "PodList",
    "metadata": {"resourceVersion": "9"},
    "items": [{"kind": "Pod", "metadata": {"name": "other", "namespace": "elsewhere"}}],
}


@pytest.mark.parametrize("items", [_FOREIGN_POD_PAGE["items"], []])
def test_a_namespaced_list_on_a_cluster_scoped_route_is_error_before_any_request(monkeypatch, items):
    """A cached `namespaced=False` would drop the namespace and LIST cluster-wide (#320)."""
    page = dict(_FOREIGN_POD_PAGE, items=items)
    client = _FakeClient(resource=_ResolvedResource("pods", namespaced=False), pages=[_DictResult(page)])
    result = _run_module(
        monkeypatch,
        params={"read_mode": "list", "api_version": "v1", "kind": "Pod", "namespace": "ns", "resource_name": "pods"},
        client=client,
    )
    assert result["read_status"] == "error"
    assert result["resources"] == [] and result["resource_version"] is None
    assert client.get_calls == 0


def test_a_namespaced_named_get_on_a_cluster_scoped_route_is_error_before_any_request(monkeypatch):
    cm = {"kind": "ConfigMap", "metadata": {"name": "cfg", "resourceVersion": "1"}}
    client = _FakeClient(resource=_ResolvedResource("configmaps", namespaced=False), get_result=_DictResult(cm))
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "get",
            "api_version": "v1",
            "kind": "ConfigMap",
            "namespace": "ns",
            "name": "cfg",
            "resource_name": "configmaps",
        },
        client=client,
    )
    assert result["read_status"] == "error"
    assert client.get_calls == 0


@pytest.mark.parametrize("read_mode", ["get", "list"])
@pytest.mark.parametrize(
    "route",
    [
        _ResolvedResource("configmaps", namespaced=True, group_version="foo.io/v1"),  # #323 fallback
        _ResolvedResource("configmap", namespaced=True),  # non-canonical plural
        _ResolvedResource("configmaps", namespaced=None),  # scope unknown
    ],
    ids=["foreign-group", "foreign-plural", "non-bool-scope"],
)
def test_a_route_that_is_not_the_requested_route_is_error_before_any_request(monkeypatch, read_mode, route):
    cm = {"kind": "ConfigMap", "metadata": {"name": "cfg", "resourceVersion": "1"}}
    client = _FakeClient(
        resource=route,
        get_result=_DictResult(cm),
        pages=[_DictResult({"kind": "ConfigMapList", "items": [cm], "metadata": {"resourceVersion": "1"}})],
    )
    params = {"read_mode": read_mode, "api_version": "v1", "kind": "ConfigMap", "namespace": "ns"}
    params["resource_name"] = "configmaps"
    if read_mode == "get":
        params["name"] = "cfg"
    result = _run_module(monkeypatch, params=params, client=client)
    assert result["read_status"] == "error"
    assert client.get_calls == 0


def test_a_cluster_wide_list_of_a_namespaced_kind_is_still_ok(monkeypatch):
    """Positive control: no namespace requested is a legitimate all-namespaces LIST."""
    client = _FakeClient(
        resource=_ResolvedResource("pods", namespaced=True),
        pages=[_DictResult(_FOREIGN_POD_PAGE)],
    )
    result = _run_module(
        monkeypatch,
        params={"read_mode": "list", "api_version": "v1", "kind": "Pod", "resource_name": "pods"},
        client=client,
    )
    assert result["read_status"] == "ok"
    assert result["resources"] == _FOREIGN_POD_PAGE["items"]
```

`_FakeClient` stores `pages` and `get_result`; with `pages` set, `get` pops pages, so the
parametrized GET case must not pass `pages`. Adjust: build the client with `pages` only when
`read_mode == "list"` and `get_result` only when `read_mode == "get"`.

- [ ] **Step 2: Run to verify RED at base**

Run (collection unit lane, from repo root):
`cd ansible_collections/tomazb/acm_switchover && python -m pytest tests/unit/test_k8s_read_outcome.py -k "cluster_scoped_route or not_the_requested_route or all_namespaces or cluster_wide_list" -v`
Expected: the guard tests FAIL on `read_status == "ok"` / `get_calls == 1`; the positive
control PASSES (green on arrival; kill condition recorded in Task 3 step 4).

- [ ] **Step 3: Commit** `test(collection): RED route-identity guard for strict reads (#320)`

### Task 2: Unit RED — custom-resource discovery before the request (#322)

**Files:**
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_k8s_read_outcome.py`

- [ ] **Step 1: Write the failing tests**

```python
# #322 / design §4.2: a custom resource is read only after live discovery proves the route.

_MC_API = "cluster.open-cluster-management.io/v1"
_MC_ROUTE = _ResolvedResource("managedclusters", namespaced=False, group_version=_MC_API)
_MC_OBJECT = {"kind": "ManagedCluster", "metadata": {"name": "c1", "resourceVersion": "5"}}
_MC_SERVED = {
    "kind": "APIResourceList",
    "groupVersion": _MC_API,
    "resources": [{"name": "managedclusters", "kind": "ManagedCluster", "namespaced": False}],
}


def _mc_params(read_mode):
    params = {"read_mode": read_mode, "api_version": _MC_API, "kind": "ManagedCluster"}
    params["resource_name"] = "managedclusters"
    if read_mode == "get":
        params["name"] = "c1"
    return params


def _mc_client(read_mode, dynamic):
    if read_mode == "get":
        return _FakeClient(resource=_MC_ROUTE, get_result=_DictResult(_MC_OBJECT), dynamic=dynamic)
    page = _DictResult({"kind": "ManagedClusterList", "items": [_MC_OBJECT], "metadata": {"resourceVersion": "5"}})
    return _FakeClient(resource=_MC_ROUTE, pages=[page], dynamic=dynamic)


@pytest.mark.parametrize("read_mode", ["get", "list"])
@pytest.mark.parametrize(
    "dynamic",
    [
        _FakeDynamicClient(discovery_error=_api_error(ApiException, 503)),
        _FakeDynamicClient(discovery=b"not json"),
        _FakeDynamicClient(discovery={"kind": "APIResourceList", "groupVersion": _MC_API, "resources": [{"name": 7}]}),
        _FakeDynamicClient(discovery=dict(_MC_SERVED, groupVersion="")),
    ],
    ids=["503", "undecodable", "malformed-entry", "empty-groupVersion"],
)
def test_a_custom_resource_read_with_unverifiable_discovery_is_error_before_any_request(monkeypatch, read_mode, dynamic):
    client = _mc_client(read_mode, dynamic)
    result = _run_module(monkeypatch, params=_mc_params(read_mode), client=client)
    assert result["read_status"] == "error"
    assert result["resources"] == [] and result["resource_version"] is None
    assert client.get_calls == 0


@pytest.mark.parametrize("read_mode", ["get", "list"])
def test_a_custom_resource_live_discovery_omits_is_kind_not_served_before_any_request(monkeypatch, read_mode):
    dynamic = _FakeDynamicClient(discovery=dict(_MC_SERVED, resources=[{"name": "other", "kind": "Other"}]))
    client = _mc_client(read_mode, dynamic)
    result = _run_module(monkeypatch, params=_mc_params(read_mode), client=client)
    assert result["read_status"] == "kind_not_served"
    assert client.get_calls == 0


@pytest.mark.parametrize("read_mode", ["get", "list"])
@pytest.mark.parametrize("live_namespaced", [True, None])
def test_a_custom_resource_whose_live_scope_differs_from_its_route_is_error(monkeypatch, read_mode, live_namespaced):
    entry = {"name": "managedclusters", "kind": "ManagedCluster", "namespaced": live_namespaced}
    client = _mc_client(read_mode, _FakeDynamicClient(discovery=dict(_MC_SERVED, resources=[entry])))
    result = _run_module(monkeypatch, params=_mc_params(read_mode), client=client)
    assert result["read_status"] == "error"
    assert client.get_calls == 0


@pytest.mark.parametrize("read_mode", ["get", "list"])
def test_a_confirmed_custom_resource_read_is_ok_after_exactly_one_discovery_read(monkeypatch, read_mode):
    dynamic = _FakeDynamicClient(discovery=_MC_SERVED)
    client = _mc_client(read_mode, dynamic)
    result = _run_module(monkeypatch, params=_mc_params(read_mode), client=client)
    assert result["read_status"] == "ok"
    assert result["resources"] == [_MC_OBJECT]
    assert [call["path"] for call in dynamic.request_calls] == [f"/apis/{_MC_API}"]
    assert client.get_calls == 1


def test_a_custom_named_404_reads_discovery_once(monkeypatch):
    dynamic = _FakeDynamicClient(discovery=_MC_SERVED)
    client = _FakeClient(resource=_MC_ROUTE, get_error=_api_error(NotFoundError, 404), dynamic=dynamic)
    result = _run_module(monkeypatch, params=_mc_params("get"), client=client)
    assert result["read_status"] == "not_found"
    assert len(dynamic.request_calls) == 1


@pytest.mark.parametrize("read_mode", ["get", "list"])
def test_a_built_in_read_never_reads_discovery_on_success(monkeypatch, read_mode):
    dynamic = _FakeDynamicClient(discovery_error=AssertionError("built-in success must not read discovery"))
    cm = {"kind": "ConfigMap", "metadata": {"name": "cfg", "resourceVersion": "1"}}
    route = _ResolvedResource("configmaps", namespaced=True)
    if read_mode == "get":
        client = _FakeClient(resource=route, get_result=_DictResult(cm), dynamic=dynamic)
    else:
        page = _DictResult({"kind": "ConfigMapList", "items": [cm], "metadata": {"resourceVersion": "1"}})
        client = _FakeClient(resource=route, pages=[page], dynamic=dynamic)
    params = {"read_mode": read_mode, "api_version": "v1", "kind": "ConfigMap", "namespace": "ns"}
    params["resource_name"] = "configmaps"
    if read_mode == "get":
        params["name"] = "cfg"
    result = _run_module(monkeypatch, params=params, client=client)
    assert result["read_status"] == "ok"
    assert dynamic.request_calls == []
```

Use the module's existing exception helpers (`_api_error`, `ApiException`, `NotFoundError`
imports at the top of the file); do not add new imports if equivalents exist.

- [ ] **Step 2: Run to verify RED at base**
Expected: the unverifiable/omitted/scope tests FAIL on `ok`; the custom-404-once test FAILS on
two discovery reads; the positive controls PASS on arrival (the "exactly one discovery read"
control FAILS at base, where no discovery is read on success).

- [ ] **Step 3: Commit** `test(collection): RED custom-resource discovery before the request (#322)`

### Task 3: Runtime RED through the shared kubernetes.core cache

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/tests/integration/r3_02_fake_api.py`
  (add `custom_discovery_status: int = 200` knob to `__init__`; in the
  `/apis/cluster.open-cluster-management.io/v1` branch answer
  `status_payload(api.custom_discovery_status)` with that status when it is not 200)
- Modify: `ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/run_k8s_read_outcome.yml`
  (`api_version: "{{ r3_02_api_version | default('v1') }}"`,
  `namespace: "{{ r3_02_namespace | default('test-ns') if r3_02_namespace | default('test-ns') else omit }}"`)
- Modify: `ansible_collections/tomazb/acm_switchover/tests/integration/test_k8s_read_outcome_runtime.py`
  (`_run_module` gains `api_version: str = "v1"`, `namespace: str = "test-ns"` written to the vars file)

- [ ] **Step 1: Write the failing runtime tests**

```python
def test_runtime_stale_cluster_scoped_cache_never_publishes_a_namespaced_list(tmp_path):
    """A namespaced LIST is never routed cluster-wide by a stale cached scope (#320)."""
    stale_core = [
        {"name": "pods", "singularName": "pod", "namespaced": False, "kind": "Pod", "verbs": ["get", "list"]},
        {"name": "configmaps", "singularName": "configmap", "namespaced": True, "kind": "ConfigMap",
         "verbs": ["get", "list"]},
    ]
    foreign = {"apiVersion": "v1", "kind": "PodList", "metadata": {"resourceVersion": "3"},
               "items": [{"metadata": {"name": "foreign", "namespace": "elsewhere", "resourceVersion": "3"}}]}
    api = FakeR302API(core_resources=stale_core, pod_list_body=foreign)
    shared_env = _ansible_env(_repo_root(), tmp_path)
    try:
        first = _run_module(tmp_path, server=api.url, read_mode="get", kind="ConfigMap",
                            resource_name="configmaps", name="test-config", env=shared_env)
        first_requests = api.requests
        api.core_resources = None  # the live server now serves Pods namespaced
        second = _run_module(tmp_path, server=api.url, read_mode="list", kind="Pod",
                             resource_name="pods", env=shared_env)
        second_requests = api.requests[len(first_requests):]
    finally:
        api.close()
    assert "READ_STATUS=ok COUNT=1 CHANGED=False" in _output(first), _output(first)
    # Precondition: run 2 resolved Pods from run 1's cache.
    assert {"method": "GET", "path": "/api"} not in second_requests, second_requests
    assert {"method": "GET", "path": "/apis"} not in second_requests, second_requests
    # The guard fired before any Pod request, namespaced or cluster-wide.
    assert not any(r["path"].endswith("/pods") for r in second_requests), second_requests
    assert second.returncode == 0, _output(second)
    assert "READ_STATUS=error COUNT=0 CHANGED=False" in _output(second)


def test_runtime_custom_resource_get_with_unreadable_discovery_is_error(tmp_path):
    """An existing ManagedCluster is not `ok` when live discovery cannot be read (#322)."""
    api = FakeR302API()
    shared_env = _ansible_env(_repo_root(), tmp_path)

    def read(**kwargs):
        return _run_module(tmp_path, server=api.url, read_mode="get", kind="ManagedCluster",
                           resource_name="managedclusters", name="cluster-a",
                           api_version="cluster.open-cluster-management.io/v1", namespace="", env=shared_env)

    try:
        first = read()
        first_requests = api.requests
        api.custom_discovery_status = 503
        second = read()
        second_requests = api.requests[len(first_requests):]
    finally:
        api.close()
    assert "READ_STATUS=ok COUNT=1 CHANGED=False" in _output(first), _output(first)
    assert {"method": "GET", "path": "/apis"} not in second_requests, second_requests
    object_get = {"method": "GET", "path": "/apis/cluster.open-cluster-management.io/v1/managedclusters/cluster-a"}
    assert object_get not in second_requests, second_requests
    assert second.returncode == 0, _output(second)
    assert "READ_STATUS=error COUNT=0 CHANGED=False" in _output(second)


def test_runtime_custom_resource_list_reads_discovery_then_the_list(tmp_path):
    api = FakeR302API()
    try:
        completed = _run_module(tmp_path, server=api.url, read_mode="list", kind="ManagedCluster",
                                resource_name="managedclusters",
                                api_version="cluster.open-cluster-management.io/v1", namespace="")
        requests = api.requests
        writes = api.writes
    finally:
        api.close()
    assert "READ_STATUS=ok COUNT=1 CHANGED=False" in _output(completed), _output(completed)
    discovery = {"method": "GET", "path": "/apis/cluster.open-cluster-management.io/v1"}
    listing = {"method": "GET", "path": "/apis/cluster.open-cluster-management.io/v1/managedclusters"}
    assert requests.index(listing) > max(i for i, r in enumerate(requests) if r == discovery)
    assert writes == []
```

Note: with kubernetes.core's own resolution, a first run also requests the group/version
discovery; the ordering assertion requires only that a discovery read precedes the LIST.
The playbook's `label_selectors` for list mode is `app=r3-02`; the fake ignores selectors.

- [ ] **Step 2: Run to verify RED at base**

Run: `cd ansible_collections/tomazb/acm_switchover && python -m pytest tests/integration/test_k8s_read_outcome_runtime.py -v -k "stale_cluster_scoped or unreadable_discovery or reads_discovery_then"`
Expected at base: #320 test FAILS (a `/api/v1/pods` request and `READ_STATUS=ok COUNT=1`);
#322 test FAILS (`READ_STATUS=ok COUNT=1`); the LIST control PASSES (green on arrival, kill
condition: remove the pre-request discovery and assert it still passes only because
kubernetes.core's resolution read precedes the LIST — therefore also assert in Task 4 via the
unit test that exactly one strict-read discovery read occurs).

- [ ] **Step 3: Commit** `test(collection): RED runtime stale-cache regressions for #320/#322`

### Task 4: Production change and fixture migration (GREEN)

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/constants.py`
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/k8s_read.py`
- Modify (fixtures only, no assertion changes): `tests/unit/test_k8s_read_outcome.py`,
  `tests/unit/test_pod_owner_classify.py`, root `tests/test_strict_read_parity.py`

- [ ] **Step 1: Constant**

```python
# Group/versions the Python CLI reads through fixed typed routes with no discovery proof
# (`get_namespace_strict`, `list_pods_strict`, `get_deployment_strict`,
# `get_replicaset_strict`, the `import-controller-config` ConfigMap read). Every other
# group/version is a custom resource, which Python proves served by live discovery before
# any object request, and the collection's strict read does the same (#322).
# Collection-only: Python expresses this boundary by which method it calls.
STRICT_READ_BUILTIN_API_VERSIONS = ("v1", "apps/v1")
```

- [ ] **Step 2: `k8s_read.py`**

```python
def _route_is_requested(resource, api_version: str, resource_name: str, namespace: str | None) -> bool:
    """Whether the resolved route is exactly the requested one (#320).

    kubernetes.core may resolve the kind from its shared on-disk discovery cache, or, after a
    core `v1` lookup miss, in another group at `v1`, and the dynamic client routes by what it
    resolved. A namespaced request on a route resolved as cluster-scoped would drop the
    namespace and read cluster-wide.
    """
    namespaced = getattr(resource, "namespaced", None)
    return (
        getattr(resource, "group_version", None) == api_version
        and getattr(resource, "name", None) == resource_name
        and isinstance(namespaced, bool)
        and (namespaced or not namespace)
    )
```

In `strict_read`, after the resolution `try/except`:

```python
    if not _route_is_requested(resource, api_version, resource_name, namespace):
        return "error", [], None

    discovered = None
    if api_version not in STRICT_READ_BUILTIN_API_VERSIONS:
        # A custom resource is read only after live discovery proves its route, as Python
        # proves it before any object request (#322); a route live discovery does not
        # confirm is never read.
        discovered = _live_discovery_resources(api_client, api_version)
        if discovered is None:
            return "error", [], None
        entry = next((entry for entry in discovered if entry["name"] == resource_name), None)
        if entry is None:
            return "kind_not_served", [], None
        if entry.get("namespaced") is not resource.namespaced:
            return "error", [], None
```

> **Amended after pre-PR review (design §10):** the `entry.get("namespaced")` comparison above
> was removed; a custom resource is gated only on the served name, and the two pre-request
> checks live in one helper, `_confirm_route`.

`_named_404_status` gains `resources: list[dict] | None = None` and reads live discovery only
when it is `None`; the named-GET 404 branch passes `resources=discovered`. Update the module
docstring's discovery sentence to: "a live route proof before any custom-resource request,
before any kind is reported as not served and before any named 404 is reported as an absent
object".

- [ ] **Step 3: Migrate fixtures** — replace every `resource=object()` and every resolved
fake that lacks `group_version`/`name`/`namespaced` with a route matching the test's request
(`_ResolvedResource(<resource_name>, namespaced=<bool matching the request>,
group_version=<api_version>)`); give custom-resource fixtures a `_FakeDynamicClient` serving
the resource. In `test_pod_owner_classify.py`, extend `_Resource` with `group_version`, `name`
(canonical plural for its kind: `pods`, `deployments`, `replicasets`,
`clusterserviceversions`, `multiclusterhubs`) and `namespaced`, and make `_LiveDiscovery`
answer the pre-request proof. In `tests/test_strict_read_parity.py`, give collection-runner
fixtures matching routes. No assertion may change; a test that needed an assertion change is a
behavior change and must be reported, not edited.

- [ ] **Step 4: Run GREEN and record kill conditions**

Run: collection unit lane for `test_k8s_read_outcome.py`, `test_pod_owner_classify.py`,
`test_uid_guarded_delete.py`, `test_decommission_role_contracts.py`,
`test_activation_auto_import.py`, `test_primary_prep_auto_import.py`; root
`tests/test_strict_read_parity.py`, `tests/test_constants_parity.py`; runtime file.
Expected: PASS. Kill conditions (each applied, proven present, run, reverted):
K1 drop `and (namespaced or not namespace)` → #320 unit + runtime tests fail;
K2 drop the group_version clause → foreign-group test fails;
K3 drop the custom-resource block → #322 unit + runtime tests fail;
K4 pass `resources=None` to the 404 classifier → discovery-once test fails;
K5 add `"cluster.open-cluster-management.io/v1"` to the built-in tuple → #322 tests fail.

- [ ] **Step 5: Commit** `fix(collection): confirm the strict-read route before any request (#320, #322)`

### Task 5: Parity vectors and constant pin

**Files:**
- Modify: `tests/test_strict_read_parity.py`

- [ ] **Step 1:** add three `VECTORS` entries with runners on both sides, the collection side
using a successfully resolved, matching route (not `resource_error`):
`("custom_get_success_discovery_unverifiable", "api failure", ERROR, "error", None)` —
Python: `call_api` raises 503, `get_custom_resource_strict`, assert no object GET; collection:
route resolved, discovery 503, object would return 200.
`("custom_list_discovery_unverifiable_resolved_route", "api failure", ERROR, "error", None)`.
`("custom_list_kind_not_served_resolved_route", "positive kind-not-served", CRD_ABSENT,
"kind_not_served", None)`.
- [ ] **Step 2:** add `test_builtin_api_versions_mirror_the_python_typed_readers` asserting
`STRICT_READ_BUILTIN_API_VERSIONS == ("v1", "apps/v1")` and that `KubeClient` exposes
`get_namespace_strict`, `list_pods_strict`, `get_deployment_strict`, `get_replicaset_strict`
(the typed readers the tuple mirrors). Import the collection constant the way this module
already imports collection code (root tests must not require `ansible-core`).
- [ ] **Step 3:** extend the header comment with the #320 stale/foreign-route divergence.
- [ ] **Step 4:** run `python -m pytest tests/test_strict_read_parity.py -v`; show the three
new collection runners fail at the pre-Task-4 source (stash-free: `git show <base>:<path>`
into a temp copy is not needed — record the base failure by running the new vectors on a
detached base worktree).
- [ ] **Step 5: Commit** `test(parity): strict-read route confirmation vectors (#322)`

### Task 6: Documentation

**Files:** `plugins/modules/acm_k8s_read_outcome.py` (`RETURN.read_status`),
`docs/coexistence.md`, `docs/ansible-collection/parity-matrix.md`,
`docs/ansible-collection/behavior-map.md`, `CHANGELOG.md`.

- [ ] `RETURN`: `ok` for a custom resource requires live discovery confirmation of the route;
`kind_not_served` may be reported before any object request; a route that is not the
requested one is `error`.
- [ ] `coexistence.md`: new paragraph "Intentional fail-closed outcome on a stale or foreign
resolved route (#320)" per design §6; update the #321 paragraph (prover now also governs the
pre-request proof); correct the built-in paragraph's "a stale cache cannot mismatch them".
- [ ] Parity matrix / behavior map: update the strict-read rows to reference #320/#322.
- [ ] `CHANGELOG.md` `[Unreleased]` → `Fixed`: one entry for #320 and #322.
- [ ] Run documentation guardrails (see Task 7). Commit `docs: strict-read route confirmation (#320, #322)`.

### Task 7: Gates

Per `docs/development/testing.md` for a dual-supported / collection change: collection unit,
integration and scenario lanes; playbook syntax; collection build; root suite
(`./run_tests.sh` or its documented equivalent); parity and static-contract tests;
documentation guardrails; black/isort/flake8 on changed files; mypy/bandit where the lane
requires. Builder simplification gate, then pre-PR code review.
