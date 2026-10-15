# R4-06 PR 2 and PR 3: implementation plan

- **Design authority:** [`2026-07-29-kubeconfig-ambiguity-guard-design.md`](2026-07-29-kubeconfig-ambiguity-guard-design.md),
  as amended by [`2026-10-15-r4-06-current-base-amendment.md`](2026-10-15-r4-06-current-base-amendment.md).
  Section numbers below (`A§x`) refer to the amendment.
- **Governing issues:** #353 for the amendment. PR 2 and PR 3 each get a governing issue
  that cites the amendment's acceptance lists (A§7). Implementation starts only after the
  amendment PR is merged.
- **Workflow per PR:**
  - builder;
  - targeted then full gates;
  - Codex adversarial review;
  - CC `/code-review` at xhigh;
  - independent validator from a clean exact-head worktree;
  - PR-comment resolver;
  - exact-head convergence, then merge.

  Write tests first: every behaviour change starts as a failing test. Kill-condition
  mutations run on the final head.
- **Ground rules.**
  - Python and the collection share no code. Mirrored logic is held equal by shared
    vectors (A§5).
  - No RBAC, protected-file, parity-status or support-boundary change. If one becomes
    necessary, stop.

## PR 2: endpoint identity (both form factors)

### Task 1: endpoint canonicalization and hub classification (A§2.1–A§2.3)

- **New modules:**
  - Python `lib/endpoint_identity.py`;
  - collection `plugins/module_utils/endpoint_identity.py`.

  The two are mirrors. Each provides:
  - `parse_endpoint(value) -> Endpoint | rejection reason`: the July §3 rejection table,
    with the A§2.1 trailing-slash rule;
  - `canonical_host`;
  - `normalized_equal`;
  - `py_legacy_host`, `col_legacy_host` and `legacy_equal` (A§2.2);
  - `classify_hub(expected, observed) -> verified | wrong_hub | unverified(reason)`
    (A§2.3, including the "absent" passthrough);
  - `redact(value)`: userinfo removed, rejected values never echoed.
- **Shared vectors:** `tests/fixtures/r4_06_endpoint_vectors.json`, holding canonicalization
  accept/reject cases and classification cases, each annotated with the expected
  comparator outputs. Both modules must agree with them through
  `tests/test_r4_06_endpoint_parity.py`. The collection also runs them in a unit test.
- **Write first:** one vector per rejection row; every equivalence row; port-only,
  scheme-only and path-only same-host cases; different hosts; IPv6 collisions; the
  `HTTPS://hub/https://other:6443` legacy case; a case-only host difference; default ports.

### Task 2: loader extensions (A§3, A§4)

- **Python `lib/kubeconfig_merge.py`:**
  - refuse `!!set` values and recursive aliases (`structure_invalid`);
  - add type-tagged canonical equality;
  - apply the cross-file and within-file duplicate rule with provenance
    `(kind, name, path, index)` and variant labels. A conflict refuses the merge with
    `kubeconfig_duplicate_conflict`; identical entries resolve first-wins.
  - The merged result keeps provenance, and resolves first-wins duplicates to one entry
    per `(kind, name)`.
- **Collection `plugins/module_utils/kubeconfig_file.py` (new):**
  - mirror the single-file contract (chunked bounded read, duplicate and merge keys,
    sanitized constructor errors, structure, nested fields, sets and recursive aliases);
  - apply the within-file duplicate rule;
  - `select_context(document, context)` returns the context or `context_missing` /
    `context_ambiguous` (A§4);
  - refuse a mapped path containing `:` with `kubeconfig_path_unsupported`.
- **Shared vectors:**
  - single-file contract;
  - duplicate equality, including `1` vs `true`, int vs str keys and dates;
  - within-file conflicts;
  - variant labelling.
- **Python-only vectors:** cross-file duplicates, which extend `tests/test_kubeconfig_merge.py`.

### Task 3: Python discovery, expected hub and classification (A§2.4, A§2.5, A§2.7, A§2.8)

All in `modules/post_activation.py`:

- **`_klusterlet_cluster_targets`:** take the first entry of
  `managedClusterClientConfigs[]` whose `url` is a non-empty string. A malformed shape
  yields `endpoint_unavailable`, sanitized rather than raised.
- **`_find_context_by_api_url` becomes `_select_spoke_context`:** this applies the A§2.5
  candidate, grouping and near-match rules and returns `(context | outcome)`. The name
  fallback is deleted.
- **Expected hub per cluster:** read the import-secret bootstrap server through
  `self.secondary`, reusing `_get_import_secret`, and classify it with `classify_hub`.
  - `_get_hub_api_server` is no longer used for the expected endpoint, so it is removed.
  - The validated merge is still loaded for contexts.
- **Classification:** `_check_klusterlet_connection` uses `classify_hub`.
- **Client endpoint check:** after `_build_managed_cluster_clients`, the client's
  configuration host must equal the proven endpoint before any read
  (`client_endpoint_mismatch`).
- **Buckets and ordering:**
  - Add an `unverified` bucket.
  - In `_verify_klusterlet_connections`, any `unverified`, `failed` or timeout raises
    **before** `_remediate_wrong_hub_klusterlets`.
  - The remediation re-check uses `classify_hub` against the import-secret endpoint.
- **Log sanitization:** every URL log line goes through `redact`.
- **Tests:**
  - invert the name-fallback, port-tolerance and hostname tests;
  - assert zero constructions and zero reads for every pre-construction refusal (A§7
    PR 2 item 4);
  - pair an unverified cluster with a wrong-hub peer and assert zero mutation (item 5);
  - show that a remediation writing the import-secret server re-verifies (item 6);
  - cover multi-user selection and `context_ambiguous`;
  - add the IPv6 discovery regression;
  - show that retry and resume re-run the step.

### Task 4: collection probe, remediation and role (A§2.3, A§2.6, A§2.7, A§2.8)

- **`plugins/module_utils/klusterlet.py`, `probe_one_cluster`:**
  1. read the ManagedCluster fresh through the secondary client and apply the URL rule;
  2. refuse a `:` path;
  3. load the file with `kubeconfig_file`, select the context and prove identity;
  4. build the client, still path-based in PR 2, through the validated literal file, and
     check the client endpoint;
  5. classify the hub;
  6. return sanitized result fields.
- **`remediate_one_cluster`:** run the same proof and classification inside the same
  invocation, on the client it mutates with, and mutate only on `wrong_hub`.
- **`normalize_workers`:** cap at 32 with an actionable message.
- **Modules:** the argument specs are unchanged. The status vocabulary gains reasons, and
  `unverified` reasons are reported under `failed_clusters` with their reason.
- **Role `verify_klusterlet.yml`:** a pending cluster is a remediation candidate only when
  its probe result is `wrong_hub`. The existing fail-before-remediation on
  `failed_clusters` now also covers the unverified reasons.
- **Tests:**
  - direct-module calls with bare names mutate nothing;
  - stale-URL regression: the URL changes between probe and remediate;
  - a `:` path;
  - an invalid file, a conflicting duplicate, and an ambiguous or missing context;
  - identity mismatch;
  - client endpoint mismatch;
  - zero constructions and reads for refusals;
  - the worker cap and its scale test;
  - static role tests for the candidate rule.

### Task 5: documentation and recording (A§7 PR 2 item 11)

- **Operator docs:**
  - `docs/operations/usage.md`: the new outcomes, reasons and recovery for
    `endpoint_unavailable`;
  - the collection `docs/variable-reference.md`: the worker cap and the mapping identity
    rule;
  - `CHANGELOG.md` `[Unreleased]`.
- **By-design differences:** the parity matrix, the behavior map, `coexistence.md`, the
  CLI migration map, and the scenario and test-migration catalogs record the input-model
  differences, with no status change.

### Task 6: gates

- Root lane.
- Collection unit, integration and scenario lanes.
- black and isort at line length 120, blocking flake8, mypy, bandit, playbook
  syntax-check and galaxy build.
- The parity tests.
- Exact-head kill-condition mutations for:
  - the classification table rows;
  - the legacy OR;
  - the name fallback;
  - URL checks;
  - the duplicate rule;
  - the remediation gate in each form factor;
  - the client endpoint check;
  - the role candidate filter.

## PR 3: snapshot-built clients (both form factors)

### Task 1: credential capture and snapshot (A§6, July §4 table)

- **New modules:**
  - Python `lib/kubeconfig_snapshot.py`;
  - collection `plugins/module_utils/kubeconfig_snapshot.py`.

  The two are mirrors. Each:
  - builds the per-cluster snapshot (context, cluster and user entries);
  - absolutizes paths against each entry's own source file;
  - captures through `O_NOFOLLOW` and `fstat`, requiring a regular, non-empty file within
    the limit;
  - applies the transformation table (raw base64 for the CA, certificate and key; a
    strict-UTF-8, trailing-whitespace-stripped token);
  - refuses the A§6 list: `exec`, `auth-provider`, an empty token, empty or
    shadowing-empty `*-data`, a dangling user, and TLS-voiding settings including
    non-boolean `insecure-skip-tls-verify`.
- **`materialize(snapshot, private_dir) -> config_dict`:** writes `0600` files in a `0700`
  directory that we own, and passes the token inline. The loader's `*-data` temp cache is
  never used.
- **Shared byte-level vectors:**
  - a CRLF PEM;
  - a certificate with no trailing newline;
  - a token with and without a trailing newline;
  - token vs `tokenFile` precedence;
  - empty values;
  - a symlink, a directory, an empty file and an oversized file;
  - modify-after-capture and delete-after-capture.

### Task 2: Python wiring

- **`_build_managed_cluster_clients(snapshot)`:** calls
  `config.new_client_from_config_dict(config, context=<matched>, persist_config=False)`
  under a process-wide lock, then asserts effective `verify_ssl` and the client endpoint
  check.
- **Snapshot threading:** the snapshot is passed through check, fix and re-check; nothing
  is rebuilt from files.
- **Private directory:** one per verification run, removed in a `finally` after every
  worker has finished.
- **Tests:**
  - replace and delete each credential file between selection, probe, remediation and
    re-check;
  - a `current-context` pointing elsewhere;
  - zero constructions for every rejection;
  - leak scans across logs, state and errors.

### Task 3: collection wiring

- **Factories take the snapshot**
  (`build_core_v1_client(snapshot, context)` / `build_apps_v1_client(...)`), and the
  path forms are removed from the repair path.
- **Per invocation:** probe and remediate build a snapshot and a private directory. The
  executor waits for workers, and timed-out clusters keep their real `changed` value and
  progress.
- **Tests:**
  - replace and delete files between selection and mutation inside one remediate call;
  - a pre-populated loader cache;
  - late mutation after a timeout;
  - cleanup ordering.

### Task 4: minimum-client CI lane (A§6)

- Add a matrix lane pinning `kubernetes==28.*` to the root and collection unit workflows.
  It runs the real-loader snapshot, credential, token, TLS and cleanup regressions.
- No support-boundary change: the declared floor stays `>=28.0.0`, and the lane verifies
  it.

### Task 5: documentation and gates

- **Documentation:**
  - `usage.md`: the credential and TLS rejections, and the temporary private-directory
    material;
  - CHANGELOG;
  - recording of the parity vectors.
- **Gates:** as in PR 2, plus the minimum-client lane. Exact-head kill-condition mutations
  for capture, rejections, explicit context, the lock and cleanup, and the snapshot
  threading in each form factor.
