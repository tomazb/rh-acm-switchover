# R4-06 current-base amendment: operator decisions for PR 2 and PR 3

- **Governing issue:** [#353](https://github.com/tomazb/rh-acm-switchover/issues/353).
- **Amends:** [`2026-07-29-kubeconfig-ambiguity-guard-design.md`](2026-07-29-kubeconfig-ambiguity-guard-design.md)
  (the July design), covering §1 (collection obligations only), §§2–5, Testing and
  Acceptance criteria.
- **Base:** `origin/ansible` `3082b843`.
  - R4-06 PR 1 (#349, PR #351) is merged. It implements July §1 for the Python klusterlet
    path (`lib/kubeconfig_merge.py`), and the contract notes recorded on #349 apply here.
- **Change type:** design only. This amendment changes no runtime code, tests, RBAC,
  protected files, parity status or support boundary.

Where this amendment and the July design differ, the amendment wins. Every July statement
not superseded here stands as written.

Unless noted otherwise, line references cite `origin/ansible` `3082b843`. Loader behaviour
was verified against kubernetes 36.0.1 and 37.0.1, with spot checks on 28.1.0.

## 1. Operator decisions (2026-10-15)

1. **Collection kubeconfig resolution keeps the form-factor architecture.**
   - Python keeps its validated multi-file `KUBECONFIG` merge and its cross-file
     duplicate-name rule.
   - The collection keeps its explicit `acm_switchover_managed_clusters` mapping:
     `<cluster>.kubeconfig` (one file) and, optionally, `<cluster>.context`. There is no
     auto-discovery and no multi-file merge.
   - The collection validates every file it consumes (§4).
   - Both form factors guarantee the same safety outcome: ambiguous or unverifiable inputs
     never authorize spoke mutation.
2. **Strict normalized endpoint equality, without new automatic remediation.**
   - A pair that the previous hostname-only comparison treated as equal, but that differs
     under normalization, is **unverified and requires operator review**. It is never
     `verified` and never auto-remediated.
   - Genuinely different-host wrong-hub keeps the existing remediation contract.
   - There is no generic `--force`, and an acknowledgement is never proof.
   - Default ports equal omitted ports.
   - PR 2's merged state must not route a newly detected mismatch into the file-reloading
     mutation path.
3. **No name-only context fallback.**
   - With no usable ManagedCluster API URL there is no guessing and no inspection or
     mutation. The result is `endpoint_unavailable`, verification fails and the step can
     be retried.
   - The collection's mapping alone is not proof of physical target identity.

## 2. Endpoint identity (amends July §3)

### 2.1 Canonicalization and rejection

July §3's canonicalization and its 14-row rejection table stand, with one change: a
trailing slash on a non-root path is **not** significant.

- `https://h/p/` equals `https://h/p`, and the empty path equals `/`.
- The reason: the Kubernetes client strips every trailing `/` from `server`
  (`kube_config.py`), so the endpoint a client really uses cannot carry one.

`canonical_host(u)` is the lowercased host, with IPv6 literals in RFC 5952 form.

### 2.2 The two legacy comparators

The legacy comparators are the two hostname-only predicates in use before PR 2. Their
host-extraction regexes are applied unchanged to the raw strings on both sides:

| comparator | source | host extraction |
| --- | --- | --- |
| `py_legacy` | `modules/post_activation.py` (`_check_klusterlet_connection`, `_find_context_by_api_url`) | `re.sub(r"https://([^:/]+).*", r"\1", s)` (unanchored, `https` only) |
| `col_legacy` | collection `module_utils/klusterlet.py` `server_host` | `re.sub(r"^https?://([^:/]+).*$", r"\1", s)` |

The two pass a pair as equal under different conditions:

- `legacy_equal(a, b) = py_legacy_host(a) == py_legacy_host(b) or col_legacy_host(a) == col_legacy_host(b)`.
- Either comparator having called the pair equal is enough. This is the only reading under
  which no pair that either form factor used to verify can newly become `wrong_hub`.
- **Scope of this guarantee.** It holds for a fixed (expected, observed) pair. §2.4
  changes Python's expected-endpoint *source* to the import-secret server, so Python now
  compares a different pair and some clusters' classification can change.
  - Usually this removes a false `wrong_hub`.
  - In the reverse case, Python remediates a genuinely different-host cluster, exactly as
    the collection already does.
  - PR 2 records the change in `usage.md` and the CHANGELOG and pins the new source with a
    test.

  For example, Python's unanchored regex turns `HTTPS://hub/https://other:6443` into
  `HTTPS://hub/other`, so it called that URL equal to `HTTPS://hub/other`. The
  collection regex calls the pair unequal. For pairs whose canonical hosts differ, the
  two comparators have not been seen to disagree on validated URLs; the OR is kept as a
  defensive bound, and the vectors pin every comparator output.

### 2.3 Hub-endpoint classification (both form factors)

This classification compares the expected hub endpoint (§2.4) with the server observed in
the spoke's `hub-kubeconfig-secret` or `bootstrap-hub-kubeconfig`.

| observed / expected | normalized equal | canonical hosts differ **and** not `legacy_equal` | otherwise | mutation |
| --- | --- | --- | --- | --- |
| both pass §2.1 | `verified` | `wrong_hub` | `unverified`, reason `endpoint_mismatch_unverified` | only `wrong_hub` remediates (existing contract) |
| observed value present but fails §2.1 | — | — | `unverified`, reason `endpoint_malformed` | none |

`wrong_hub` therefore requires both a different canonical host and that **neither** legacy
comparator called the pair equal. Every other mismatch goes to operator review. Examples:

| case | outcome |
| --- | --- |
| port only, or scheme only (`http` vs `https`), same canonical host | `unverified` |
| path only, same canonical host | `unverified` |
| IPv6 literals that the old regex collapsed (`[2001`) | `unverified` |
| case-only difference in the host | `verified` (it normalizes equal, which removes a remediation the case-sensitive regex caused) |

The observed server falls into one of three categories, each handled separately:

- **Absent:** no hub secret, no `data.kubeconfig`, or no `server` key. This stays the
  documented non-fatal skip (Python `unreachable`, collection `skipped`), with the
  existing reasons.
- **Unreadable:** the embedded kubeconfig YAML cannot be parsed. Each form factor keeps
  its existing outcome, so Python returns `unreachable` (a skip) and the collection
  returns `failed`. That is a pre-existing difference, recorded, and not changed here.
- **Present but invalid:** a `server` value that is present but is not a string, or is a
  string that fails §2.1. This is `unverified`, reason `endpoint_malformed`.

### 2.4 Expected hub endpoint: one source in both form factors (amends July §3)

For each cluster, the expected endpoint is the `server` inside the `bootstrap-hub-kubeconfig`
Secret carried in that cluster's `<cluster>-import` secret.

- That secret is read through the already-initialized secondary hub client, which is the
  client remediation also uses. The collection already does this (`probe_one_cluster`).
- Python adopts the same source. Today Python expects the operator's secondary-context
  server, while remediation writes the import-secret server. Under strict equality that
  mismatch would make every successful repair fail its own re-check (for example, an
  operator reaching the hub at `https://api.hub` against an import secret using
  `https://api.hub:6443`).
- This narrows the pre-existing source divergence recorded as parity-audit row H9.
- A missing import secret, or one without a bootstrap Secret document, is `unverified`
  with reason `expected_endpoint_unavailable`. That cluster is neither inspected nor
  mutated.
- An expected value that fails §2.1 is `unverified`, reason `expected_endpoint_malformed`.
- The operator's secondary-context server (PR 1's `_get_hub_api_server`) is no longer the
  expected endpoint. The validated merge stays required, because it is the spoke-context
  source.

### 2.5 Python target selection (amends July §3 zero- and multi-match)

Per non-local ManagedCluster:

1. **API URL source.** The URL is the first entry of `spec.managedClusterClientConfigs[]`
   whose `url` is a non-empty string.
   - None: `unverified`, `endpoint_unavailable`. This includes a non-list or otherwise
     malformed `managedClusterClientConfigs`, which is sanitized rather than allowed to
     raise.
   - URL fails §2.1: `unverified`, `endpoint_malformed`.
   - Either way there is no context lookup and no inspection (Decision 3). The name-only
     fallback is removed.
2. **Candidates.** Every context whose cluster entry has a §2.1-valid `server` that
   normalizes equal to the URL. Malformed `cluster.server` entries are never candidates.
3. **Selection.** Candidates are grouped by their **cluster entry** (`name` plus content).
   - **One cluster entry:** selected. If several contexts or users reference it, the
     first context in merge order is used. Target identity is proven by the URL; the user
     only changes permissions. This keeps `oc login` namespace contexts and the repo's own
     `generate-merged-kubeconfig.sh` per-role users (`managed1:operator`,
     `managed1:validator`) working.
   - **More than one distinct cluster entry for the same server** (different CA or TLS
     material for one URL): `unverified`, `context_ambiguous`.
4. **No candidate.**
   - If some context's valid server has the same **canonical host** but differs in
     scheme, port or path: `unverified`, `endpoint_mismatch_unverified`. Discovery uses
     canonical-host equality, not the legacy regexes: discovery itself never remediates,
     and the old regex's IPv6 collapse would otherwise fail unrelated partial
     kubeconfigs.
   - Otherwise: `no_context`, the documented non-fatal skip. The operator has no spoke
     access, and the ManagedCluster readiness gate still applies. This narrows July §3
     ("zero-match is a failure") and the SSA-03 row in the same way: a zero-match is never
     inspected, never mutated and never reported verified.

### 2.6 Collection target identity (Decisions 1 and 3)

1. **Unmapped cluster.** No `acm_switchover_managed_clusters` entry: `skipped`,
   `no_managed_cluster_kubeconfig`, unchanged and non-fatal. The mapping defines which
   clusters the collection verifies, and nothing is discovered automatically. This is
   where the collection differs from Python, which checks every cluster.
2. **Mapped cluster.**
   - First, the ManagedCluster URL (rule 1 of §2.5) must be usable. If it is not, the
     cluster fails with `endpoint_unavailable` or `endpoint_malformed`, without opening
     the mapped file.
   - Then the file is loaded under §4 and the context is selected.
   - The URL must then normalize equal to the mapped context's `cluster.server`. Otherwise the cluster is `failed`, with zero inspection and zero
   mutation, for one of these reasons:
   - `endpoint_unavailable`: no URL;
   - `endpoint_malformed`: either side fails §2.1;
   - `endpoint_mismatch_unverified`: same canonical host, different scheme, port or path;
   - `target_identity_mismatch`: different host.
3. **Mapped path containing `:`.** The cluster is `failed`, reason
   `kubeconfig_path_unsupported`. The client loader splits a `config_file` path on `:`,
   so the client would read files nobody validated.
4. **Re-proof in every invocation.** Probe, remediation and re-probe are separate module
   invocations. Each one loads the mapped file afresh, selects the context, and re-proves
   identity under rule 2.
   - **Fresh URL evidence:** each invocation reads the ManagedCluster URL itself, with a
     strict read through the secondary hub client (ManagedCluster `get`/`list` are
     already granted). It never takes URLs from facts an earlier task published, so no
     caller-owned freshness contract is needed (AGENTS §Execution-time discovery).
   - **`acm_klusterlet_remediate`** re-classifies the hub endpoint (§2.3) on the same
     in-memory snapshot it mutates with, and mutates only when that classification is
     `wrong_hub`.
   - A caller that passes names without that evidence gets zero mutation.
   - The role's pending-cluster path (`verify_klusterlet.yml` adds cluster-verify
     `pending` clusters to the remediation candidates) is covered by the same rule. A
     pending cluster is remediated only if its own fresh classification is `wrong_hub`.

### 2.7 Client endpoint check (both form factors; closes the PR 2 interim window)

Before any spoke read, every spoke client is checked: its
`api_client.configuration.host`, normalized, must equal the proven endpoint. Otherwise the
cluster is `unverified`, reason `client_endpoint_mismatch`, with no read and no mutation.

- **In PR 2,** clients are still built by the official loader re-reading files. This
  check binds the client to the endpoint the validated data proved, for example when a
  `KUBECONFIG` file vanished and the loader's first-wins shifted.
- **In PR 3,** the check stays as a defense-in-depth assertion on snapshot-built clients.

### 2.8 Outcomes and run semantics (both form factors)

| outcome | spoke inspection | spoke mutation | step |
| --- | --- | --- | --- |
| `verified` | yes | none | pass |
| `wrong_hub` | yes | existing remediation, then strict re-check | pass only if the re-check verifies |
| `unverified` (reason) | none, unless the reason arose from comparing an inspected secret | none | **fail** |
| `unreachable` / `skipped` / `no_context` | as today | none | non-fatal (documented) |
| `failed` / worker timeout | as today | none **before mutation authorization**; a failure after a remediation patch keeps and reports the partial change, as today | fail |

**Ordering.** Every cluster is classified first. If any cluster is `unverified` or
`failed`, the step fails **before any remediation in that run**. This extends the existing
"failed blocks remediation" ordering (Python raises `failed` before
`_remediate_wrong_hub_klusterlets`; the collection stops after the initial probe) to
`unverified`, identically in both form factors. A retry re-evaluates every cluster.
Python does not checkpoint the step, using the PR 1 mechanism.

**Workers.** Remediation workers finish before results are returned and before any
credential cleanup (§6). A cluster whose worker timed out is still reported as `failed`,
but its `changed` value and its progress come from the worker's real result once the
worker finishes, never from a placeholder. The collection's executor currently returns
without waiting and reports placeholder results for timed-out futures; PR 3 changes
both.

**Stable reason codes:**

| group | codes |
| --- | --- |
| endpoint | `endpoint_unavailable`, `endpoint_malformed`, `endpoint_mismatch_unverified`, `expected_endpoint_unavailable`, `expected_endpoint_malformed` |
| target and client | `context_ambiguous`, `context_missing`, `target_identity_mismatch`, `client_endpoint_mismatch` |
| kubeconfig files | `kubeconfig_load_failed (<code>)`, `kubeconfig_duplicate_conflict`, `kubeconfig_path_unsupported` |
| credentials (PR 3) | `tls_verification_unproven`, `exec_credential_unsupported`, `auth_provider_unsupported`, `credential_unsupported`, `credential_unreadable`, `credential_symlink`, `credential_not_regular`, `credential_empty`, `credential_oversized`, `client_construction_failed` |

**Output sanitization (PR 2).**
- Results and logs carry endpoints only after §2.1 validation, with userinfo removed,
  never as raw strings.
- A value rejected for userinfo is never echoed.
- The collection's `current_hub_server` and `expected_hub_server` fields, and Python's
  URL log lines, follow this rule.
- Diagnostics otherwise carry the cluster, the reason and the entry provenance (kind,
  name, source path, in-file index), never credential material or file content.

## 3. Duplicate-name rule placement (amends July §2)

- **Python (PR 2).** The July §2 rule applies across the whole `KUBECONFIG` chain.
  - Entries keep provenance `(kind, name, source path, in-file index)`.
  - Identical entries are resolved first-wins, with a debug log.
  - A `(kind, name)` group with more than one distinct variant refuses the whole merge
    before any per-cluster work: the step fails with `kubeconfig_duplicate_conflict`.
  - Every occurrence is located and labelled with its variant index. Content is never
    shown.
- **Collection (PR 2).** The same rule applies within each mapped file. A conflict fails
  that cluster only (§4).
- **"Identical" means equal parsed values, compared with their types.** Formatting never
  creates a conflict. Values are compared through a recursive type-tagged canonical form:
  - `bool`, `int`, `float`, `str`, `bytes`, dates and `None` are distinct types, so
    `1` ≠ `true`;
  - mapping keys are compared with their types and sorted by `(type, value)`;
  - lists are compared in order.

  PR 2 extends the loader (both form factors) to refuse `!!set` values and recursive
  aliases with `structure_invalid`. Accepted input is then always a finite tree of
  mappings, lists and scalars, and the comparison is defined on every such value.

## 4. Collection per-file validation (Decision 1)

Each mapped file is loaded under the July §1 file contract, mirrored from
`lib/kubeconfig_merge.py` (the two form factors share no code):

- an inclusive 10 MiB size limit with a chunked bounded read;
- duplicate YAML keys and `<<` merge keys refused;
- constructor errors sanitized;
- the structural and nested-field rules recorded on #349.

Context selection:

- The mapped `context` must name exactly one context. A missing one is `context_missing`.
- A within-file conflicting duplicate is `kubeconfig_duplicate_conflict`.
- An omitted `context` requires a `current-context` naming exactly one context. Otherwise
  the result is `context_ambiguous`.

Every failure fails that cluster only, with zero inspection and zero mutation. The file is
never partially used.

## 5. Parity vectors: shared vs form-factor-specific

This table supersedes the July §1 and §2 statements that the collection applies the same
`KUBECONFIG` order and cross-file rules (Decision 1).

| vector set | shared | Python only | collection only |
| --- | --- | --- | --- |
| §2.1 canonicalization accept/reject | ✓ | | |
| §2.3 hub classification, including the legacy-comparator cases | ✓ | | |
| §3 duplicate equality (type-tagged) and within-file conflicts | ✓ | | |
| §4 single-file contract (size, duplicate keys, merge keys, structure, constructor errors) | ✓ | | |
| `KUBECONFIG` precedence, default path, cross-file duplicates | | ✓ | |
| §2.5 discovery (candidates, `no_context`, `context_ambiguous`) | | ✓ | |
| §2.6 mapping, identity proof, `kubeconfig_path_unsupported`, current-context | | | ✓ |
| §6 credential transformation table (byte-level) | ✓ (PR 3) | | |

These are recorded as by-design input-model differences, with no parity-status change.

## 6. Snapshot-built clients (amends July §4 and §5; PR 3)

July §4 stands except where it is reconciled below.

- **One snapshot per cluster per invocation.**
  - The snapshot holds the selected context, its cluster entry and its user entry.
  - Relative credential paths are absolutized against each entry's own source file.
  - Credential files are captured as content through the July §4 file boundary
    (`O_NOFOLLOW`, `fstat` on the open descriptor, a regular non-empty file within the
    limit, read once).
  - Within an invocation, that one snapshot drives target selection (§2.5/§2.6), the probe
    client, the remediation client and every re-check client.
  - Across collection module invocations, §2.6 rule 4 re-proves everything; captured
    content is never passed between invocations or persisted.
- **We materialize credentials ourselves; the loader's temp-file cache is never used.**
  The loader caches `*-data` temp files in a process-global dict keyed by content. That
  dict ignores `temp_file_path` and keeps stale paths after cleanup.
  - The snapshot therefore never hands `*-data` to the loader.
  - Captured CA, certificate and key bytes are written to `0600` files inside a private
    `0700` directory that we create per invocation.
  - The client dict references those paths. The token is passed inline as `token`.
  - The directory is removed after every worker of that invocation has finished, whether
    the step passed or failed.
  - Construction is serialized by a process-wide lock.
- **Rejected before construction, with zero inspection and zero mutation:**
  - a selected user carrying `exec` (`exec_credential_unsupported`) or `auth-provider`
    (`auth_provider_unsupported`);
  - an empty `token`;
  - an empty `*-data` field, or a `*-data` field present beside an empty one;
  - a user reference to a missing user entry (`credential_unsupported`; an anonymous
    client is never built);
  - a selected cluster with `insecure-skip-tls-verify` set to anything except absent or
    boolean `false`, an `http` server, `tls-server-name` or `proxy-url`
    (`tls_verification_unproven`). The client loader treats a string such as `"false"`
    as truthy, which turns verification off. After construction, and before any read,
    the client's effective `verify_ssl` must be true. URL identity proves nothing
    without TLS verification against the captured CA.
- **Mutation barrier (July §5).** A secret read through a spoke client, and any secret
  patch, secret create or deployment patch, happens only after resolution, snapshot
  construction and the §2.7 client endpoint check have succeeded for that cluster. A
  construction failure after a unique match gives zero mutating calls
  (`client_construction_failed`).
- **Client versions.** `requirements.txt` allows `kubernetes>=28.0.0`, and CI installs it
  unpinned. PR 3 adds a CI matrix lane that pins the minimum supported client, 28.x,
  beside the current resolution. The real-loader snapshot, credential, token and cleanup
  regressions run in both lanes. Construction uses only
  `config.new_client_from_config_dict(config_dict, context=..., persist_config=False)`,
  with file paths and an inline token.

## 7. Slicing and acceptance

### R4-06 PR 2: endpoint identity (both form factors)

Scope: §2, §3, §4, §5 (all but the credential vectors), and the SSA-A6 collection worker
cap.

Acceptance:

1. **Shared vectors.** Shared accept/reject vectors for §2.1, and shared §2.3
   classification vectors, including:
   - on the same host: a port-only difference, a different scheme, a different path;
   - different hosts;
   - IPv6 legacy collisions;
   - `HTTPS://hub/https://other`-style legacy-regex cases;
   - a case-only difference;
   - default-port equivalence.
2. **Same-host mismatches.** Every port-, scheme- or path-only mismatch on the same host
   yields `endpoint_mismatch_unverified` in both form factors, with **zero spoke
   mutations**. A different-host pair under both legacy comparators still remediates.
3. **Missing URL.** A missing URL, or a malformed `managedClusterClientConfigs`, yields
   `endpoint_unavailable`:
   - zero context lookups, spoke client constructions and spoke reads;
   - the step fails, and a retry re-runs it, for Python and for collection-mapped
     clusters.

   Name-only context collisions never select a context.
4. **Never inspected.**
   - Refusals decided before any client exists (`endpoint_unavailable`,
     `endpoint_malformed` on the URL or the mapped server, `endpoint_mismatch_unverified`
     in discovery or identity, `context_ambiguous`, `context_missing`,
     `target_identity_mismatch`, `kubeconfig_*`, `expected_endpoint_*`): tests assert
     **zero spoke client constructions and zero spoke reads**.
   - `client_endpoint_mismatch` happens after construction: tests assert **zero spoke
     reads and zero mutations**.
   - A discovery regression shows that an unrelated IPv6 literal (the old regex
     collapsed it to `[2001`) gives `no_context`, not a failing near-match.
5. **Remediation ordering.** Any `unverified` or `failed` cluster blocks all remediation in
   that run, in both form factors. A test pairs an unverified cluster with a genuine
   wrong-hub peer and asserts zero mutation.
6. **Python discovery and duplicates.**
   - Cross-file and within-file conflicting duplicates refuse the merge, with every
     occurrence located. Identical duplicates (type-tagged) proceed.
   - Multiple users on one cluster entry select the first context in merge order.
   - Distinct cluster entries for one server give `context_ambiguous`.
   - The expected hub comes from the import secret: a remediation that writes the
     import-secret server re-verifies as `verified`.
7. **Collection mapping and remediation** (including a stale-URL regression: the
   ManagedCluster URL changes between the probe and the remediate invocation, and
   remediation uses the fresh value).
   - A mapped file that is invalid, carries a conflicting duplicate, has an ambiguous or
     missing context, or has a `:` path fails that cluster with zero inspection and zero
     mutation.
   - The mapped server must prove identity.
   - `acm_klusterlet_remediate` re-proves identity and `wrong_hub` before mutating, and a
     direct call with bare names mutates nothing.
   - A pending cluster is remediated only on its own fresh `wrong_hub`.
8. **Client endpoint check.** A client whose `configuration.host` differs from the proven
   endpoint gives `client_endpoint_mismatch`, with zero reads and zero mutations.
9. **Sanitization.** No userinfo or rejected URL appears in any result or log.
10. **SSA-A6 worker cap.**
    - `normalize_workers` refuses values above 32 with an actionable message.
    - A scale test covers the bound.
    - `variable-reference.md` documents it.
11. **Recorded divergences (AGENTS §Approval gate recording).** The following are recorded
    as by-design input-model differences, with no status change:
    - Python discovery vs the collection mapping;
    - whole-merge vs per-cluster duplicate failure;
    - `endpoint_unavailable` scope (every cluster in Python, mapped clusters in the
      collection);
    - the unified expected-hub source.

    They are recorded in the parity matrix, the behavior map, `coexistence.md`, the CLI
    migration map, the affected scenario and test-migration catalogs, `usage.md`, the
    collection `variable-reference.md`, and the `CHANGELOG.md` `[Unreleased]` section.
    Recovery steps for `endpoint_unavailable` are documented: wait for the ManagedCluster
    to register its client config and re-run, or, in the collection, remove the
    cluster's mapping.

### R4-06 PR 3: snapshot-built clients (both form factors)

Scope: §6, and the July §4 transformation table with shared byte-level vectors.

Acceptance:

1. **Snapshot binding.** Every spoke read and mutation client in an invocation is built
   from the same validated, credential-content-bound snapshot used for target selection.
   Tests replace and delete every credential file between the actual stages:
   - Python: selection, probe, remediation, re-check;
   - collection: selection and mutation inside `acm_klusterlet_remediate`.

   They assert that the captured content is used and that no post-capture file read
   occurs.
2. **Explicit context.** A `current-context` pointing elsewhere never changes the target.
3. **Rejections.** Every §6 pre-construction rejection gives zero spoke client
   constructions, zero reads and zero mutations. This includes non-boolean
   `insecure-skip-tls-verify` values, checked with the real loader. A plain user in the
   same merge proceeds.
   - A timed-out remediation worker that mutates after the timeout is reported with its
     real `changed` state, and the credential directory is removed only after it
     finishes.
4. **Temp files.** Temp files appear only in the invocation's private directory, which is
   removed after its workers finish. A pre-populated loader cache (for example from the
   hub client's identical CA) is never consulted. Concurrent workers construct clients
   without races.
5. **No leakage.** No captured content appears in logs, results, state or errors.
6. **Minimum-client lane.** The minimum-client CI lane runs the real-loader regressions.

## 8. Tracker, residuals and non-goals

- **Tracker.**
  - The SSA-03 row's "zero-match is an explicit non-mutation failure" is narrowed per
    §2.5.
  - The R4-06 row records PR 1 as merged and links this amendment.
- **Residuals.**
  - Pre-fix completion markers: #352. A completion marker written before the fix is never
    claimed as fresh wrong-hub verification.
  - Preflight strict load: #350.
- **Non-goals.**
  - No new remediation flag. Repairing a newly detected mismatch needs a separately
    designed, identity-verified mechanism, which this amendment does not provide.
  - No identity-attestation API call.
  - **RBAC unchanged.** The same spoke and hub calls are made through different clients.
    The remediate module's fresh ManagedCluster read uses `get`/`list` verbs that every
    operator and validator role already grants.
