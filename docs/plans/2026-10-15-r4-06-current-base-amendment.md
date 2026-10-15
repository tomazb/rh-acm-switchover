# R4-06 current-base amendment: operator decisions for PR 2 and PR 3

- **Governing issue:** [#353](https://github.com/tomazb/rh-acm-switchover/issues/353).
- **Amends:** [`2026-07-29-kubeconfig-ambiguity-guard-design.md`](2026-07-29-kubeconfig-ambiguity-guard-design.md)
  ("the July design"), §§2–5, Testing and Acceptance criteria.
- **Base:** `origin/ansible` `3082b843`. R4-06 PR 1 (#349, PR #351) is merged. It
  implements July §1 for the Python klusterlet path in `lib/kubeconfig_merge.py`, and the
  contract notes recorded on #349 apply here unchanged.
- **Change type:** design only. This document changes no runtime code, tests, RBAC,
  protected files, parity status or support boundary.

Where this amendment and the July design disagree, this amendment wins. Every section of
the July design not named here stands as written.

## 1. Operator decisions (2026-10-15)

1. **Collection kubeconfig resolution keeps the form-factor architecture.**
   - Python keeps its validated multi-file `KUBECONFIG` merge and cross-file
     duplicate-name rule.
   - The collection keeps its explicit `acm_switchover_managed_clusters` mapping:
     `<cluster>.kubeconfig` (one file) plus optional `<cluster>.context`. There is no
     auto-discovery and no multi-file merge.
   - The collection validates every file it consumes (§3). Both form factors guarantee the
     same outcome: ambiguous or unverifiable inputs never authorize spoke mutation.
2. **Strict normalized endpoint equality, without new automatic remediation.**
   - Endpoints are compared by July §3 canonicalization, with malformed-URL rejection and
     shared vectors.
   - A pair that the previous hostname-only comparison treated as equal, but which differs
     under normalization, is **unverified and requires operator review**. It is never
     reported as `verified` and is never remediated automatically.
   - A genuinely different-host wrong-hub result keeps the existing remediation contract.
   - There is no generic `--force`, and an acknowledgement is not proof of endpoint
     equality.
   - Default ports remain equivalent to omitted ports.
3. **No name-only context fallback.**
   - When no usable ManagedCluster API URL exists, the result is `endpoint_unavailable`.
     The verification fails, can be retried, and never inspects or mutates a spoke.
   - The collection's mapping alone is not proof of physical target identity.

## 2. Endpoint identity (amends July §3)

### 2.1 Canonicalization and rejection

July §3's canonicalization and its 14-row rejection table stand, with one change. **A
trailing slash on a non-root path is not significant**: `https://h/p/` equals `https://h/p`,
and the empty path still equals `/`.

Reason for the change: the Kubernetes client strips every trailing `/` from `server` when it
builds a configuration (`kube_config.py`, verified on kubernetes 37.0.1). The endpoint a
client actually uses therefore cannot carry one, and treating `/p/` as distinct from `/p`
would invent a mismatch the client cannot have.

Rejection is a stable reason code with entry provenance. A URL carrying userinfo is never
echoed.

### 2.2 The legacy predicate and the classification

The **legacy predicate** is the collection's existing host extraction, applied to both
sides:

```text
legacy_host(s) = re.sub(r"^https?://([^:/]+).*$", r"\1", s)
legacy_equal(a, b) = legacy_host(a) == legacy_host(b)   # case-sensitive
```

Both form factors use this one predicate. Python's previous unanchored, `https`-only
regex agrees with it on every URL that passes §2.1 validation, except the same-host
`http://` case. There Python's regex never matched, so it reported `wrong_hub`. Under the
shared predicate that case becomes `endpoint_unverified`. Adopting the shared predicate can
only turn a Python remediation into an operator review, never the reverse.

Every comparison between an expected and an observed endpoint is classified as follows:

| Validation | `normalized_equal` | `legacy_equal` | Outcome | Mutation |
| --- | --- | --- | --- | --- |
| either side fails §2.1 | — | — | `unverified`, reason `endpoint_malformed` | none |
| both valid | yes | — | `verified` | none |
| both valid | no | yes | `unverified`, reason `endpoint_mismatch_unverified` | **none** (Decision 2) |
| both valid | no | no | `wrong_hub` | existing remediation contract |

The legacy predicate exists only to keep Decision 2's promise. A pair that used to look
equal never newly triggers a bootstrap-secret patch or a klusterlet restart. For example,
the old regex collapsed `https://[2001:db8::1]` and `https://[2001:db8::2]` to the same
`[2001`, so that pair is `endpoint_unverified`, not `wrong_hub`. Two different-case spellings
of one host normalize equal and are now `verified`, which removes a remediation the old
case-sensitive compare caused.

### 2.3 Expected hub endpoint (amends July §3 "single source")

The two form factors keep their existing sources, and both validate and normalize them
under §2.1:

- **Python:** the secondary context's `cluster.server`, taken from the validated
  `KUBECONFIG` merge (PR 1). Under §4's duplicate rule, a conflicting duplicate refuses the
  merge, and identical duplicates resolve first-wins. That is exactly the entry the hub
  client's own loader selects, so this is a single source. The live
  `configuration.host` is not used, because the client loader rewrites it (it strips
  trailing slashes).
- **Collection:** per cluster, the `server` in the `bootstrap-hub-kubeconfig` Secret
  inside the secondary hub's `<cluster>-import` secret (unchanged).

The difference in source is pre-existing (parity-audit row H9) and is not widened. If the
expected endpoint itself fails §2.1, Python fails the whole step and the collection marks
that one cluster `failed`, both with reason `expected_endpoint_malformed`.

### 2.4 Python target selection (amends July §3 zero- and multi-match)

For each ManagedCluster, the API URL is the first usable entry of
`spec.managedClusterClientConfigs[].url`.

- **No usable URL** (absent, empty, or not a string): `endpoint_unavailable`. That cluster
  is `unverified`, with no context lookup and no inspection (Decision 3). The name-only
  fallback is removed.
- **URL fails §2.1:** `unverified`, reason `endpoint_malformed`.
- **Candidates:** contexts whose cluster entry has a §2.1-valid `server` that normalizes
  equal to the URL. Candidates with the same `(cluster entry, user entry)` pair are one
  identity. Several `oc login` namespace contexts for the same cluster and user are
  therefore a single candidate, and the first such context in merge order is used.
  - **Exactly one identity:** selected.
  - **More than one identity** (for example, the same server reached as two different
    users): `unverified`, reason `context_ambiguous`.
  - **No identity, but a context whose server is legacy-equal:** `unverified`, reason
    `endpoint_mismatch_unverified`. The spoke is never inspected through a context that
    only looks equal.
  - **None at all:** `no_context`. This stays the documented non-fatal skip. The operator
    has no access to that spoke, and the ManagedCluster readiness gate still applies.

    This narrows the July design's statement that "zero-match is a failure". The
    documented contract allows spoke kubeconfigs to be partial (`usage.md`). Decision 3
    makes only a *missing URL* fatal. A zero-match neither inspects nor mutates, and it
    is never reported as verified.
- A malformed `cluster.server` elsewhere in the merge excludes that entry from the
  candidates. If its legacy host equals the URL's legacy host, the cluster is `unverified`,
  reason `endpoint_malformed`. A stale, unrelated, malformed entry does not block other
  clusters.

### 2.5 Collection target identity (Decision 1 and Decision 3)

- **No mapping entry for a cluster:** `skipped`, reason `no_managed_cluster_kubeconfig`.
  This is unchanged and non-fatal.
- **A mapped cluster:** before any probe classification or mutation, the mapped context's
  §2.1-valid `cluster.server` must normalize equal to the ManagedCluster URL.
  - Missing URL: `failed`, reason `endpoint_unavailable`.
  - Legacy-equal but not normalized-equal: `failed`, reason `endpoint_mismatch_unverified`.
  - Different host: `failed`, reason `target_identity_mismatch`. The mapping points at a
    different cluster.
  - Either side fails §2.1: `failed`, reason `endpoint_malformed`.

  In each of these cases the spoke is not inspected and not mutated. Each is fatal, so a
  retry runs after the configuration is fixed.
- The hub comparison (§2.2) then applies. In the collection, `endpoint_mismatch_unverified`
  and `endpoint_malformed` are reported as `failed`. They never enter the
  remediation-candidate set and are never counted as verified.
- **Remediation-candidate rule change:** the collection role builds
  `_klusterlet_remediation_candidates` from cluster-verify `pending` clusters as well as
  probe `wrong_hub` clusters (`verify_klusterlet.yml`). From PR 2, a pending cluster is a
  remediation candidate only when its probe outcome is `wrong_hub`. A pending cluster whose
  probe is `unverified`, `failed` or `skipped` is not remediated. This applies Decision 2
  to the collection's existing pending-cluster path.

### 2.6 Outcome model and severity (both form factors)

| Outcome | Meaning | Spoke mutation | Step result |
| --- | --- | --- | --- |
| `verified` | normalized-equal hub endpoint | none | pass |
| `wrong_hub` | genuinely different host | existing remediation contract (§2.2) | pass only if re-check verifies |
| `unverified` (reason) | operator review needed | none | **fail** after all clusters are evaluated |
| `skipped` / `no_context` | no spoke access configured | none | non-fatal (documented) |
| `failed` / timeout | probe or remediation error | none for that cluster | fail |

Python adds an `unverified` bucket beside `failed`. A run with any `unverified` cluster
fails `verify()` without checkpointing the klusterlet step (the PR 1 mechanism), so a retry
re-evaluates it. Reason codes are stable identifiers:

- `endpoint_unavailable`
- `endpoint_malformed`
- `endpoint_mismatch_unverified`
- `context_ambiguous`
- `target_identity_mismatch`
- `expected_endpoint_malformed`

Diagnostics carry the cluster name, the reason, and entry provenance (kind, name, source
path, in-file index). They never carry userinfo, credential material or file content.

## 3. Collection per-file validation (Decision 1)

The collection loads each mapped kubeconfig file under the July §1 file contract,
mirrored from Python's `lib/kubeconfig_merge.py` (the two form factors share no code):

- an inclusive 10 MiB size limit with a chunked bounded read;
- duplicate YAML keys refused, and `<<` merge keys refused;
- constructor errors sanitized;
- the structural and nested-field rules recorded on #349.

On top of that:

- **Within-file duplicate names (§4).** A `(kind, name)` group with more than one distinct
  content variant fails that cluster: `failed`, reason `kubeconfig_duplicate_conflict`,
  with every occurrence located by kind, name, path, index and variant label.
  Byte-identical duplicates resolve first-wins.
- **Context selection.** The mapped `context` must name exactly one context.
  - A missing context: `failed`, reason `context_missing`.
  - A conflicting duplicate falls under the rule above.
  - An omitted `context` requires a `current-context` that names exactly one context.
    Otherwise the result is `context_ambiguous`.
- **Single-path rule.** `<cluster>.kubeconfig` is one path. It is never split on `:`,
  because a path may legitimately contain `:`.
- **Failure scope.** A failure fails that cluster only. The file is never partially used.

## 4. Duplicate-name rule placement (amends July §2)

- **Python (PR 2):** the July §2 rule applies across the whole `KUBECONFIG` chain, as
  written.
  - Entries keep provenance `(kind, name, source path, in-file index)`.
  - Identical content resolves first-wins, logged at debug.
  - More than one distinct variant in a `(kind, name)` group refuses the whole merge before
    any per-cluster work. The step fails with reason `kubeconfig_duplicate_conflict`. The
    diagnostic locates every occurrence and labels each with its variant index, and never
    shows content.
- **Collection (PR 2):** the same rule, applied within each mapped file (§3).
- **Equality:** "identical" means equal after YAML parsing, compared as canonical JSON
  with sorted keys. Formatting differences do not create a conflict.

## 5. Snapshot-built clients (amends July §4 and §5; PR 3)

July §4 stands, with these reconciliations against kubernetes 37.0.1 (verified):

- **One snapshot per selected cluster.** The snapshot holds the selected context, its
  cluster and user entries (first-wins resolved), relative credential paths absolutized
  against each entry's own source file, and file-backed credentials captured as content
  (§4 transformation table). The same snapshot drives target selection, the probe client,
  the remediation client and every re-check client. No spoke client is built from files
  after capture.
  - Python's `_build_managed_cluster_clients` takes the snapshot.
  - The collection's `build_core_v1_client` / `build_apps_v1_client` take the snapshot.
  - The path-taking forms are removed from the repair path.
- **Rejection before construction.** The loader swallows `exec` failures (it builds an
  anonymous client) and silently ignores unknown `auth-provider` names. The repair path
  therefore rejects any selected user carrying `exec` or `auth-provider` before
  construction (`exec_credential_unsupported`, `auth_provider_unsupported`), and also an
  empty `token` (the loader treats `token: ""` as present and skips `tokenFile`, which
  gives an anonymous client).
- **Temp files.** For `*-data` fields the loader writes `0600` temp files at construction
  (`FileOrData.as_file`). They live in a module-level cache shared by every thread, and
  are deleted at process exit. The repair path therefore:
  - passes `temp_file_path` pointing to a private `0700` directory created for the step;
  - removes that directory when the step ends, whether it succeeded or failed;
  - serializes client construction with one process-wide lock, because the cache is not
    thread-safe.

  Captured material exists only in memory and in those transient files, and never in
  logs, results, state, checkpoints or diagnostics. This replaces the July wording "never
  persisted".
- **Credential-file boundary:** `O_NOFOLLOW`, `fstat` on the open descriptor, a regular
  non-empty file within the limit, read once. Unchanged from July §4.
  - Failures: `credential_unreadable`, `credential_symlink`, `credential_not_regular`,
    `credential_empty`, `credential_oversized`. Each fails the cluster with zero mutation.
- **Mutation barrier (July §5).** No secret read through a spoke client, no secret patch or
  create, and no deployment patch happens until resolution and snapshot construction have
  succeeded for that cluster. A construction failure after a unique match yields zero
  mutating calls (`client_construction_failed`).
- **Version scope.** The loader behaviour above was verified on kubernetes 37.0.1. The
  minimum supported client in `requirements.txt` is `>=28.0.0`. PR 3 tests run on the
  pinned versions of each CI lane. Construction uses only
  `config.new_client_from_config_dict(config_dict, context=..., persist_config=False,
  temp_file_path=...)`, which exists across that range.

## 6. Slicing and acceptance

### R4-06 PR 2: endpoint identity (both form factors)

Scope: §2, §3, §4, and the SSA-A6 collection worker cap. A cap of 32 is enforced in
`normalize_workers`; anything above it is refused.

Acceptance:

1. Shared Python/collection accept and reject vectors for §2.1 cover every rejection row
   and every equivalence row. The classification vectors for §2.2 include, on the same
   host, a port-only difference, a different scheme, and a different path. They also
   include different hosts, IPv6 legacy collisions, a case-only difference, and a
   default-port equivalence.
2. Every port-only, scheme-only or path-only difference on the same host yields
   `endpoint_mismatch_unverified` with **zero** mutating calls, in both form factors. A
   different host still remediates under the existing contract.
3. A missing ManagedCluster URL yields `endpoint_unavailable`: no context lookup, no spoke
   inspection or mutation, the step fails, and a retry re-runs it. Name-only context
   collisions never select a context.
4. Python: conflicting cross-file and within-file duplicates refuse the merge with every
   occurrence located. Identical duplicates proceed. Multi-identity candidates yield
   `context_ambiguous`.
5. Collection:
   - a file that is invalid, carries a conflicting duplicate, or has an ambiguous or
     missing context fails that cluster with zero mutation;
   - the mapped server must prove target identity;
   - pending clusters are remediated only on `wrong_hub`.
6. **PR 2 intermediate safety.** No `endpoint_mismatch_unverified`, `endpoint_malformed`
   or `endpoint_unavailable` outcome reaches `_force_klusterlet_reconnect` or
   `remediate_one_cluster`. Tests assert zero calls to the file-reloading mutation path for
   every such vector.
7. Docs (`usage.md`, the collection `variable-reference.md`, the CHANGELOG `[Unreleased]`
   section) record the new outcomes and the input-model distinction. The parity matrix and
   behavior map record that the expected-hub source and the input models differ by design,
   with no status change.

### R4-06 PR 3: snapshot-built clients (both form factors)

Scope: §5, plus the July §4 transformation table with shared byte-level vectors.

Acceptance:

1. The client used for every spoke read and mutation is constructed from the same
   validated, credential-content-bound snapshot used for target selection. Tests replace
   and delete every credential file after capture and assert the client still uses the
   captured content, with zero post-capture file reads.
2. `current-context` pointing elsewhere never changes the target. The matched context is
   always passed explicitly.
3. `exec`, `auth-provider` and empty-`token` users are rejected with zero mutation. A plain
   user in the same merge proceeds.
4. Temp files live only in the step's private directory, which is removed afterwards.
   Concurrent workers construct clients without races.
5. No captured content appears in logs, results, state or errors.

## 7. Residuals and non-goals

- **Pre-fix completion markers:** tracked in #352. A completion marker written before the
  fix is never claimed as fresh wrong-hub verification.
- **Preflight strict load:** tracked in #350.
- **No new remediation flag.** A newly detected mismatch has no opt-in remediation, and
  repairing one requires a separately designed, identity-verified mechanism. That
  mechanism is out of scope.
- **RBAC unchanged.** PR 2 and PR 3 issue the same spoke and hub API calls through
  different clients.
