# R3-01 / TR2D-01 Argo CD Scoped-Discovery Design

**Status:** Approved

**Revision:** `R3-01-TR2D-01-DESIGN-A1`

**Approved base:** `17c9589d41767ce582fe46444f5e1feb07af0d30`

**Issue:** [#199](https://github.com/tomazb/rh-acm-switchover/issues/199)

## Scope

Correct the Ansible collection's scoped Argo CD Application discovery for the
combined `R3-01 / TR2D-01` boundary. This resolves findings `R3-A1`,
`TR2D-M1`, and `TR2D-L1` exactly once.

The correction must:

- prevent a skipped cluster-wide task from overwriting a scoped aggregate;
- prove positive success for every requested namespace before aggregation;
- reject missing, malformed, failed, skipped, unreachable, incomplete, and
  mixed-success results;
- prevent partial discovery from reaching filtering, checkpoint persistence,
  pause, or resume mutation;
- preserve sanitized strict and advisory behavior;
- cover primary-prep checkpoint retry/re-pause and standalone two-hub resume;
- count only patch results whose `changed` value is exactly Boolean `true`;
- restore collection parity without changing the Python implementation.

## Selected Design

Use the minimal guarded-publication approach:

1. Keep the existing role structure and mock path.
2. Use distinct variables for the scoped live query, cluster-wide live query,
   scoped validation, and published discovery result.
3. Normalize trusted namespace input before the query.
4. Validate the complete scoped result set before aggregating any resources.
5. Publish a resource list only after an all-present result.
6. Publish empty lists and prevent mutation for absent or error results.

The internal variables are:

```yaml
_argocd_scoped_live_query: {}
_argocd_cluster_live_query: {}
_argocd_scoped_validation:
  status: ok | absent | error
  resources: []
_argocd_published_discovery:
  resources: []
```

No conditional API task may register into `_argocd_published_discovery`.

## Namespace Input Contract

Input precedence remains:

1. explicit `acm_switchover_argocd.namespace`;
2. the current hub's persisted discovery namespace list;
3. cluster-wide discovery when neither supplies a non-empty set.

An explicit namespace and every persisted entry must be a non-empty string
after trimming. Persisted values must be lists. Normalize the selected set by
trimming, deduplicating, and sorting. An absent or genuinely empty list retains
the existing cluster-wide fallback. A non-empty malformed list fails closed.
Advisory `discover` mode remains cluster-wide.

## Positive Present Predicate

A scoped namespace result is present only when:

- the result is a mapping;
- it corresponds to the expected normalized namespace at the same loop
  position;
- `failed`, `skipped`, and `unreachable` are not true;
- `api_found` is exactly Boolean `true`;
- `resources` exists and is a list.

`msg` is not authoritative.

## Strict Absent Predicate

A scoped operation is absent only when all of the following hold:

- the top-level result is a mapping;
- `results` exists as a list with cardinality exactly matching the normalized
  requested namespace list;
- the top-level result is not failed, skipped, or unreachable;
- every per-namespace result is a mapping;
- every result corresponds to the expected requested namespace at the same
  loop position;
- no per-namespace result is failed, skipped, or unreachable;
- every `api_found` value is exactly Boolean `false`;
- each `resources` value is either absent or exactly an empty list.

A non-list or non-empty `resources` value, contradictory fields, an unknown
shape, or any mixture of present, absent, failed, skipped, unreachable, or
malformed results is a sanitized discovery error. It must not be classified as
absent, aggregated, filtered, persisted as successful discovery, or allowed to
reach pause/resume mutation.

No optional unknown field is rejected merely because it exists. Any additional
field used by the implementation as a rejection predicate requires captured
supported-runtime evidence and dedicated positive and negative fixtures.

## Aggregation and Error Invariants

No resource list is flattened, published, filtered, persisted, paused, or
resumed until every requested namespace passes the present predicate. There is
no partial-success mode.

Strict failures use stable sanitized operator text. Advisory failures publish
`status: error`, `category: api-discovery`, empty Application lists, and perform
no patch. Raw API data remains behind existing `no_log` handling.

An all-absent result publishes the existing absent status and performs no
mutation. A mixture of present and absent is an error.

## Retry and Standalone Resume

Non-mock integration tests use a stateful fake Kubernetes HTTP API:

- Primary-prep initially pauses three applicable Applications, persists the run
  ID and normalized namespace map on a later controlled failure, then retries.
- Reconciliation re-enables two applicable Applications. Retry must re-pause
  exactly those two, leave one already-paused Application unchanged, and never
  touch an unrelated Application.
- Standalone resume validates checkpoint identity before either patch, restores
  one same-run Application on each hub, and leaves foreign-run Applications
  unchanged.

Standalone resume publishes:

```yaml
acm_switchover_argocd_resume_result:
  changed: true | false
  restored: <integer>
  restored_by_hub:
    primary: <integer>
    secondary: <integer>
```

`restored` is derived once from the two per-hub buckets after both role
invocations. The two-hub proof is `primary=1`, `secondary=1`, `restored=2`,
with exactly two patch-ledger entries.

## Changed Reporting

Pause and resume summaries use a predicate equivalent to:

```jinja2
selectattr('changed', 'defined')
| selectattr('changed', 'sameas', true)
```

Only Boolean `true` counts. Boolean `false`, integer `1`, strings, mappings,
lists, missing values, skipped results, and other truthy non-Boolean values do
not count. The same delta drives global and per-hub summaries.

Repository dry-run remains non-mutating and reports zero actual patches. Native
Ansible check mode retains the documented would-change behavior.

## Parity and Exclusions

Python already stops scoped aggregation on unexpected namespace API failures
and normalizes trusted namespace sets. No Python change or parity-status change
is required.

Excluded:

- `TR2D-02` resume optimistic concurrency/fresh re-read;
- `R3-01b` finalization register clobber;
- Flake8 exclusion housekeeping and `setup.cfg`;
- other Review #3/SSA findings;
- general Argo CD role decomposition;
- RBAC changes;
- protected runbook and skill files;
- live lab mutation/certification;
- Phase 9 controller expansion.

Rollback is limited to the scoped validator/publication tasks, exact changed
counting, standalone summary, tests, and slice-specific documentation. It does
not alter checkpoint identity, marker ownership, resume OCC, cluster-wide
discovery semantics, mock behavior, finalization, or RBAC.
