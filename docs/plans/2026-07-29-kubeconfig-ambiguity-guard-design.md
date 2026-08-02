# Kubeconfig Ambiguity Guard — Design (extends SSA-03)

**Date:** 2026-07-29
**Branch:** `ansible` (spec branch `docs/thermos-safety-specs`)
**Status:** approved design, awaiting implementation plan
**Origin:** cross-validation of main-branch safety specs against `ansible` @ `0bf55db9`,
independently revalidated (Codex). This design **extends planned slice `SSA-03`**
(`SSA-PY2` hostname collapse, zero/multi-match failures, `SSA-A6` worker cap) with the
untracked remainder of the same resolution path, so one slice fixes it once. On approval,
the `SSA-03` tracker row references this doc as its design.

## Problem

Klusterlet repair auto-discovers spoke contexts by manually merging every KUBECONFIG file
and then mutates managed clusters (bootstrap secret delete, import manifest apply,
klusterlet restart) based on that resolution. Beyond the tracked hostname-collapse defect:

1. **Fail-open partial merge.** Missing paths, unreadable files, and YAML errors are
   debug-log skips (`modules/post_activation.py:1397-1404,1438-1444`); repair proceeds on
   whatever merged. The repair call site also passes `max_size=0`, disabling the size cap
   for every merged file (`:1311-1317,1377-1382`).
2. **No duplicate-name handling.** Contexts/clusters/users are appended blindly
   (`:1433-1437`); `yaml.safe_load` silently keeps the last duplicate mapping key; the
   merge order semantics differ from the official loader's first-wins rule.
3. **TOCTOU + two resolvers.** After manual matching, the client is built with
   `config.new_client_from_config(context=...)` (`:1118-1134`), which re-reads the files —
   a change between merge and client build, or any manual-vs-official-loader semantic
   difference, silently retargets the mutation.
4. **Second endpoint derivation.** The expected hub server is re-derived from the same
   merged dict and compared host-only (`:1608-1609`).
5. **Collection:** avoids auto-discovery (explicit `acm_switchover_managed_clusters` map)
   but shares the host-only endpoint compare (`plugins/module_utils/klusterlet.py:161-162,
   285-297`).

## Goals

1. The repair path never mutates a cluster based on a partial, ambiguous, or stale view of
   kubeconfig inputs.
2. One resolver: the client is built from the exact snapshot that matching used.
3. Endpoint identity is a full normalized URL everywhere (Python + collection).

## Non-goals

- Changing `KubeClient` hub-client initialization (`load_kube_config`) — out of scope;
  only the spoke-repair path is rebuilt.
- Auto-discovery removal or an explicit spoke map for Python (the collection's model);
  discovery stays, it just fails closed.
- Concurrency-cap work (`SSA-A6` half of SSA-03) — unchanged, implemented alongside.

## Design

### 1. Fail-closed merge

- **Input precedence is deterministic and stated once**, because merge order decides
  first-wins deduplication (§2) and each entry's credential provenance (§4). When
  `KUBECONFIG` is set and non-empty, its `:`-separated paths are processed **left to
  right**, and that order is the merge order. Empty entries produced by leading, trailing,
  or repeated separators are skipped without error — they are not the default path and do
  not abort the merge. A path repeated in the list is read once, at its first position.
  The default path (`~/.kube/config`) is used **only** when `KUBECONFIG` is unset, or is
  set but yields no non-empty entries; it is never appended to a non-empty `KUBECONFIG`
  list. Python and the collection apply this identical order, and the parity vectors cover
  reordered file lists (asserting the first-wins winner changes with order) and the
  default-path fallback cases.
- Any path listed in KUBECONFIG (or the default path) that is missing, unreadable,
  oversized, or YAML-invalid aborts the entire repair step with the file name and error —
  before any per-cluster work. A mutating path must not run on a partial view.
- **Merge-level errors are sanitized before they are returned or logged.** A raw YAML
  parser message can quote the offending line, and a kubeconfig line can be a token, a
  private key, or a `*-data` blob. Every merge failure therefore reports only: a stable
  file-level reason code (`missing`, `unreadable`, `oversized`, `yaml_invalid`,
  `duplicate_yaml_key`), the source file path, and — where the loader supplies them and
  they are safe — the line and column. The parser's own message text, the offending
  source line, and any key material are never included. Test coverage includes malformed
  YAML whose invalid region contains a token and a private key, asserting neither appears
  in the returned error, the module result, or any log line.
- **Structural schema validation of every parsed document**, before any entry is merged.
  Today an empty or `null` file decodes to an empty mapping and is accepted, malformed
  entries are appended unvalidated, and a scalar or list root surfaces as an unstructured
  `AttributeError`. The contract instead requires: the document root is a **mapping** (a
  scalar, list, `null`, or empty document is a fail-closed structural error, not an empty
  kubeconfig); `clusters`, `contexts`, and `users`, when present, are **lists**; every
  entry in them is a **mapping**; every entry has a `name` that is a **non-empty string**;
  and a `context` entry's `context` member, when present, is a mapping. Any violation
  aborts the whole merge before per-cluster work, with the same sanitized reason-code and
  provenance contract as the other §1 failures — never an `AttributeError` and never a
  silently skipped entry. Parity tests cover each violation in both form factors.
- **One shared size limit, defined once and applied everywhere.** The canonical limit is
  the Python default of `10 * 1024 * 1024` bytes. It applies **inclusively** — a file of
  exactly `limit` bytes is accepted and `limit + 1` is rejected — and it governs both
  merged kubeconfig files and the file-backed credential reads of §4, which is why §4
  refers to "the same standard size limit" rather than defining its own. The
  `ACM_KUBECONFIG_MAX_SIZE` override may raise or lower the limit but **may not disable
  it**: a value that is absent, non-numeric, zero, or negative resolves to the canonical
  default rather than to "unlimited", closing the current `<= 0` bypass alongside the
  `max_size=0` call-site bypass. The collection adopts the identical limit, override
  semantics, and boundary. Parity tests assert acceptance at exactly `limit` and rejection
  at `limit + 1` in both form factors, and that each disabling override value falls back
  to the default.
- The `max_size=0` bypass is removed; the standard size limit applies to every merged file.
- YAML parsing uses a loader that **errors on duplicate mapping keys** instead of silently
  keeping the last.

### 2. Duplicate-name rule (official-loader-compatible)

- Merge keeps per-entry provenance: `(name, source_file, index)`. `index` is the
  **zero-based position of the entry within its own source file's list** for that entry
  kind (`clusters`, `contexts`, or `users`) — not a position in the merged result. That
  definition is used verbatim by every diagnostic below, by both form factors, and by the
  parity fixtures.
- Same name, byte-identical content → first occurrence wins (official first-wins
  semantics), logged at debug. This holds whether the identical duplicates are in one
  file or in different files.
- Same name, differing content → ambiguity failure that identifies **both conflicting
  entries by complete source location**. No mutation for any cluster.
- Both rules apply **anywhere in the KUBECONFIG chain, including twice within a single
  file** — `clusters`/`contexts`/`users` are YAML lists of named entries, so a duplicate
  YAML-key loader does not catch same-file duplicates; the merge-level duplicate check
  must.

#### Duplicate-conflict failure-message contract

Naming the files alone is insufficient: when both conflicting entries live in the same
file, a file-only message repeats one path and locates neither entry. Every
differing-duplicate diagnostic therefore reports, for **each** of the two conflicting
entries:

- the entry kind (`cluster` / `context` / `user`);
- the entry name;
- the source file path;
- the zero-based list index defined above.

Consequences of that rule:

- Conflicts spanning two KUBECONFIG files report both file paths together with each
  entry's own index.
- Conflicts inside one file report that path (once or twice — either rendering is
  acceptable) and **both distinct indexes**, so the two entries are unambiguously
  locatable.
- The diagnostic carries identity and location only. Entry content, `*-data` fields,
  tokens, certificate or private-key material, `tokenFile` contents, `exec` credential
  plugin environment, and any other credential-bearing value are never included — the
  message says *that* two entries differ and *where* they are, never *how* they differ.
- Python and the collection emit equivalent structured error data (same kind, name,
  path, and index fields) so the parity fixtures can compare them directly.
- The failure aborts the complete merge before any mutation, per §5.

Three or more same-name entries are handled by the same rule, stated for the general
case: duplicates are evaluated per `(kind, name)` group across the whole KUBECONFIG
chain. Within a group, byte-identical occurrences collapse under first-wins, and the
group is an ambiguity failure whenever it contains **more than one distinct content
variant**. The diagnostic then reports the complete source location of **every
occurrence in the group** — including occurrences that are byte-identical to an earlier
one, since an operator resolving the ambiguity must see every place the name is defined,
and not an arbitrary two. For a group of three entries holding two distinct variants
(one variant appearing twice), all three locations are reported, each labelled with
which variant it carries — variants are distinguished by a stable index or digest, never
by printing their content. The two-entry case above is the common instance of this rule,
not a separate one.

### 3. Endpoint matching (SSA-03 core, shared rule)

- Full normalized URL equality. Canonicalization rules (the complete equivalence class):
  lowercase scheme and host; IPv6 literals kept bracketed, compared in RFC 5952 canonical
  text form; explicit default port (`:443` for https, `:80` for http); empty path and `/`
  are equal, otherwise trailing slashes are preserved and significant; percent-encoding
  normalized to uppercase hex before compare; a server URL carrying a query or fragment
  is rejected as malformed (never silently stripped). No hostname collapse.
- Zero-match and multi-match are explicit non-mutation failures per cluster (SSA-03
  acceptance criteria, unchanged).
- The expected hub endpoint comes from the initialized secondary client's live
  configuration host (single source), normalized with the same rule — the second
  derivation at `:1608-1609` is removed. That host is **not** a merged kubeconfig entry,
  so it carries **synthetic provenance** for diagnostics: entry kind
  `expected-endpoint`, name `secondary-hub`, source `secondary client configuration`
  (a fixed non-path literal, never a filesystem path), and index `0`. Those four fields
  populate the same structured error data as a merged entry, so the §3 rejection
  diagnostics have one shape regardless of source, and the expected-source rejection test
  asserts them together with the stable reason code.
- Collection: `server_host()` in `module_utils/klusterlet.py` is replaced by the same
  full-URL normalization; comparison at `:285-297` uses it (SSA-03 parity item).

#### Malformed server URLs fail closed before matching

Canonicalization is only defined for a well-formed absolute `https`/`http` URL. Every
`cluster.server` value — from the merged snapshot and from the expected-endpoint source
alike — is validated **before** any matching, and a rejection is a fail-closed error with
**zero mutating calls** for the affected scope. The complete rejection set:

| rejected input | reason |
| --- | --- |
| `server` key missing | no endpoint to compare |
| `server: null` | ditto |
| `server` not a string (mapping, list, number, boolean) | not a URL |
| empty or whitespace-only string | not a URL |
| relative URL (no scheme, or scheme-relative `//host`) | no authority to compare; must not be resolved against anything |
| scheme other than `http`/`https` | unsupported transport; never coerced to https |
| userinfo present (`https://user@host`, with or without a password component) | credential-bearing authority; never matched, never echoed |
| invalid port (non-numeric, empty after `:`, out of 1-65535) | unparseable authority |
| invalid IPv4 literal (out-of-range or malformed octets) | unparseable host |
| invalid IPv6 literal | unparseable host |
| malformed bracketed IPv6 (unbalanced or missing brackets, or brackets around a non-IPv6 host) | unparseable authority; also blocks the `:`-splitting ambiguity |
| malformed percent escape (`%` not followed by two hex digits) | undecodable; normalization is undefined |
| query or fragment present | as already specified above — rejected, never silently stripped |
| structurally invalid parse result (parser raises, or returns a result whose host is empty while an authority was expected) | no trustworthy authority |

Rules that apply to all of them:

- Rejection happens before endpoint matching, so no candidate is ever selected from an
  unvalidated URL, and **zero mutating calls** are issued for the affected scope: a
  malformed entry reachable by any cluster's resolution aborts that resolution under §5's
  mutation barrier.
- The error is sanitized and identifies **entry provenance** — entry kind, name, source
  file path, and the zero-based in-file index defined in §2 — plus a stable reason code.
- When userinfo is present the URL is **never echoed**, in whole or in part: the
  diagnostic names the provenance and the reason code only. This is the one rejection
  reason whose offending value is credential-bearing, and it is treated as such.
- Python and the collection use identical canonicalization **and rejection** vectors; the
  parity fixtures cover accept and reject cases together, so neither implementation can
  normalize an input the other rejects.

### 4. Snapshot-built client

- The selected context's client is built with `config.new_client_from_config_dict(...)`
  from the **same merged snapshot** used for matching — the files are never re-read, so
  there is no TOCTOU window and no manual-vs-official-loader disagreement.
- **The matched context is passed explicitly** as the loader's `context` argument. It is
  never left to default, because `new_client_from_config_dict(...)` falls back to the
  snapshot's `current-context`, which is an arbitrary entry that has nothing to do with
  the cluster matching selected — a silent wrong-cluster mutation. A regression test
  builds a snapshot whose `current-context` names a **different** cluster from the matched
  context and asserts the constructed client targets the matched cluster's server.
- Before handoff, file-referenced credentials (`certificate-authority`,
  `client-certificate`, `client-key`, `tokenFile`) are absolutized **per entry at merge
  time**, against the source file of the specific `cluster`/`user` entry that won
  first-wins deduplication — not against the selected context's file. A context, its
  cluster, and its user may each originate from different files; each entry's relative
  paths resolve against its own provenance.

#### File-backed credential contents are part of the snapshot

Absolutizing a path is **not** a snapshot. `config.new_client_from_config_dict(...)` opens
`certificate-authority`, `client-certificate`, `client-key`, and `tokenFile` when it
builds the client, so a path-only snapshot still lets a file replaced or deleted between
merge and client construction change the authenticated identity — the exact TOCTOU window
this section exists to close. The merged snapshot therefore preserves the file **contents**
captured at snapshot-construction time:

- Each of the four file-backed credential fields is read **once**, at merge time, through
  a race-resistant file boundary: open the absolutized path first and derive every check
  from that one open descriptor (`O_NOFOLLOW`, then `fstat` on the descriptor rather than
  a second path-based `stat`), so the file that is validated is the file that is read.
- Validation before use: the descriptor must refer to a **regular file**, and its size
  must be non-zero and within the same standard size limit §1 restores for merged inputs.
  A symlink, directory, device, empty file, or oversized file is a fail-closed error, and
  the read is not retried against the path.
- The captured content is converted to the appropriate in-memory representation for the
  config dict — the `*-data` form for `certificate-authority`, `client-certificate`, and
  `client-key`; the in-memory token value for `tokenFile` — and the corresponding
  file-path key is removed from the snapshot handed to the client, so client construction
  has nothing left to re-read.
- Fields already supplied as embedded `*-data` pass through **unchanged**; they are
  already content, and are never re-encoded or normalized.
- **`token` takes precedence over `tokenFile`, and the divergence from the loader is
  deliberate and recorded.** When a user entry carries both, the in-line `token` wins and
  `tokenFile` is not read — matching the official client, which selects the data key when
  present and initializes the file fallback only when it is absent. Two points must be
  settled against the **pinned** client version before implementation rather than against
  a moving `master`: first, the official file read returns the file content *without* the
  trailing-whitespace strip specified above, so this design **intentionally diverges** by
  stripping — a trailing newline in a token file would otherwise become part of the bearer
  credential — and that divergence is stated here so it is not later "fixed" back;
  second, an empty or whitespace-only value is fail-closed in both fields (an empty
  `token`, or a `tokenFile` whose content strips to empty, is a structural error, not an
  anonymous client). Python and the collection share vectors for both-present,
  `token`-only, `tokenFile`-only, empty-`token`, and empty-`tokenFile` cases.

**Exact transformation rules**, so Python and the collection produce byte-identical
snapshots. Each file is read in **binary mode**; no text decoding, universal-newline
translation, or encoding guess is applied at any point, because a DER-encoded certificate
or a CRLF-terminated PEM must survive intact:

| field | source file bytes | snapshot field | transformation |
| --- | --- | --- | --- |
| `certificate-authority` | raw bytes, unmodified | `certificate-authority-data` | standard base64 of the exact bytes, no line wrapping |
| `client-certificate` | raw bytes, unmodified | `client-certificate-data` | standard base64 of the exact bytes, no line wrapping |
| `client-key` | raw bytes, unmodified | `client-key-data` | standard base64 of the exact bytes, no line wrapping |
| `tokenFile` | raw bytes, decoded as UTF-8 | `token` | decode UTF-8 strictly (a decode failure is fail-closed, not a lossy fallback), then strip **trailing** ASCII whitespace only (`\n`, `\r`, `\t`, space) — matching how the official loader consumes a token file. Leading and interior bytes are preserved verbatim |

Trailing newlines are therefore **preserved** for the three certificate/key fields (they
are part of the PEM and base64 encodes them faithfully) and **stripped** for `tokenFile`
alone, where a trailing newline would otherwise become part of the bearer credential. No
other field-specific normalization exists. Parity tests assert both transformations
byte-for-byte across the two form factors, including a CRLF-terminated PEM, a certificate
file with no trailing newline, a token file with and without a trailing newline, and
byte-fidelity of every field after the source file is modified or deleted post-capture.
- `exec` credential plugins **cannot satisfy this section's guarantee and are therefore
  rejected on the repair path.** An exec plugin runs at client-construction time, after
  endpoint matching; its output is not part of the snapshot, can differ between
  invocations, can fail independently, and can return credentials for a different identity
  than the one matching validated. Passing it through would leave Goal 2 ("the client is
  built from the exact snapshot that matching used") and acceptance criterion 2 false for
  exactly the users most likely to be short-lived-credential based. So a selected context
  whose user is exec-based is a **fail-closed per-cluster error** under §5's mutation
  barrier: zero mutating calls for that cluster, a sanitized diagnostic naming the entry
  kind, name, source path, index, and the stable reason `exec_credential_unsupported`, and
  the cluster reported in the per-cluster summary. Other clusters are unaffected.
  Re-admitting exec users requires a designed and tested identity-attestation mechanism —
  proving the constructed client authenticates as the identity matching selected — which
  is out of scope here and not claimed. Tests assert the rejection and the zero-mutation
  property, and that a non-exec user in the same merge still proceeds.
- `auth-provider` users are rejected on the repair path for the identical reason and with
  the identical mechanics. An auth-provider block resolves or refreshes credentials at
  client-construction time — in-memory, outside the snapshot — so the constructed client
  can authenticate as an identity the snapshot never contained, leaving Goal 2 and
  acceptance criterion 2 false exactly as for exec plugins. A selected context whose user
  carries `auth-provider` is a fail-closed per-cluster error under §5's mutation barrier:
  zero mutating calls for that cluster, a sanitized diagnostic with the same entry
  metadata and the stable reason `auth_provider_unsupported`, the cluster reported in the
  per-cluster summary, and other clusters and non-auth-provider users in the same merge
  unaffected. Re-admission requires the same designed identity-attestation mechanism as
  exec, out of scope here. Tests mirror the exec vectors.
- The original absolutized paths are retained **separately**, as provenance for
  diagnostics and tests only. They are never used to reconstruct the client and never
  re-read after capture.

**The frozen snapshot must reach the collection's client factories too.**
`build_core_v1_client()` and `build_apps_v1_client()`
(`ansible_collections/tomazb/acm_switchover/plugins/module_utils/klusterlet.py:69,87`)
currently accept a kubeconfig path and call `config.new_client_from_config(**kwargs)`
(`:82,:100`), which re-reads the file and every credential path it names — so a snapshot
built upstream would be discarded at the moment the mutating client is constructed. Both
factories therefore take the merged snapshot (with credential contents already captured
and the matched context named explicitly) and construct through
`new_client_from_config_dict(...)`. A path-only invocation of either factory is not a
supported entry point for the repair path. Collection tests replace and delete each
credential file after snapshot creation and assert the mutating client still uses the
captured contents, mirroring the Python vectors.
- Captured credential contents are never logged, never returned in a module result, never
  written to state or checkpoint data, and never included in any diagnostic. Parser and
  client-construction errors that could embed the material are redacted to a stable reason
  code plus the entry provenance of §2 — never the value, and never a byte offset into it.

### 5. Mutation barrier

- Per cluster: no bootstrap-secret delete, no import-manifest apply, no klusterlet restart
  until endpoint resolution and client construction have both succeeded for that cluster.
- Global ambiguity failures (§1, §2) block all clusters; per-cluster zero/multi-match
  failures block that cluster and are reported in the summary (existing per-cluster error
  aggregation).

## Testing

- Failure matrix, each case asserting **zero mutating calls**: missing file, unreadable
  file, oversized file (at exactly `limit + 1`, with `limit` accepted), YAML error,
  duplicate YAML keys, duplicate name differing, structurally invalid document (scalar
  root, list root, empty/`null` document, non-list `clusters`/`contexts`/`users`,
  non-mapping entry, missing or non-string `name`), zero-match, multi-match,
  exec-based selected user, and **client-construction failure after a unique match** —
  `new_client_from_config_dict(...)` raising once resolution has already succeeded must
  still leave that cluster with zero bootstrap-secret deletes, zero import-manifest
  applies, and zero klusterlet restarts, per §5.
- Duplicate name identical (byte-equal) is a *valid* case: asserts first-occurrence
  selection and normal mutation proceeds.
- Snapshot client: file modified between merge and client build → client uses snapshot
  values (assert via config dict), no re-read.
- Relative-path absolutization: CA/cert/key/tokenFile relative to a non-CWD source file
  resolve correctly; context, cluster, and user entries drawn from three different files
  each resolve against their own source file; embedded `*-data` untouched; an exec-based
  selected user is rejected per §4 rather than passed through.
- Credential-content snapshot, per field (`certificate-authority`, `client-certificate`,
  `client-key`, `tokenFile`): the source file is **modified** after snapshot construction
  and the client still authenticates with the captured content; the source file is
  **deleted** after snapshot construction and client construction still succeeds with the
  captured content — both asserting zero post-capture file reads. Fail-closed vectors,
  each with zero mutating calls: symlinked path, directory, device node, empty file,
  oversized file. Assertions on the handed-off snapshot: the file-path key is gone and the
  content key is present; pre-existing embedded `*-data` is byte-unchanged; an exec-based
  selected user is rejected fail-closed with `exec_credential_unsupported` and zero
  mutating calls, an `auth-provider` selected user is rejected fail-closed with
  `auth_provider_unsupported` and zero mutating calls, and a user with neither in the
  same merge still proceeds. Redaction: no captured credential content appears in any log line, module
  result, state/checkpoint payload, or parser/client error — including a deliberately
  malformed PEM/token whose parse error is asserted to carry only a stable reason code and
  the entry provenance.
- Malformed server URLs, one vector per rejection-table row (missing, null, non-string,
  empty/whitespace, relative and scheme-relative, unsupported scheme, userinfo, invalid
  port forms, invalid IPv4, invalid IPv6, malformed bracketed IPv6, malformed percent
  escape, query, fragment, structurally invalid parse): each asserts a fail-closed error
  before matching, **zero mutating calls**, and a sanitized message carrying entry kind,
  name, source path, zero-based index, and a stable reason code. The userinfo vector
  additionally asserts no part of the URL — host, userinfo, or full value — appears in the
  message. The same vectors run against the expected-endpoint source, not only the merged
  snapshot. Python and the collection share the accept **and** reject vectors.
- Duplicate-conflict source locations, per entry kind:
  - two same-name differing entries in **two different files** → ambiguity failure whose
    message reports both file paths and each entry's zero-based index;
  - two same-name differing entries **within one file** → ambiguity failure reporting
    that one path and both distinct zero-based indexes, unambiguously locating each
    entry (the regression test for the file-only message, which could not distinguish
    them);
  - byte-identical duplicates **within one file** → first-wins, debug log, mutation
    proceeds;
  - byte-identical duplicates **across two files** → first-wins, debug log, mutation
    proceeds;
  - every ambiguity diagnostic contains entry kind, name, path, and index and contains
    no entry content, `*-data`, token, certificate, private-key, `tokenFile` content, or
    `exec` environment value;
  - three same-name entries in one `(kind, name)` group with two distinct content
    variants (one of them duplicated byte-identically) → ambiguity failure naming all
    three occurrence locations, each labelled with the variant it carries, proving the
    contract is not limited to exactly two entries and that a byte-identical repeat is
    still located;
  - Python and the collection produce equivalent structured error data for the same
    fixtures (shared parity vectors).
- Endpoint normalization vectors covering the full equivalence class: default vs explicit
  port (https/http), host and scheme case, empty vs `/` path, trailing slash on non-root
  path, IPv6 literal forms, percent-encoding case, query/fragment rejection; Python and
  collection produce identical results (parity test).
- Expected-endpoint source: matcher compares against the secondary client's live host.
- Existing SSA-03 tests (hostname-collapse regression, zero/multi-match) unchanged.

## Release/process follow-up

Version management is not a test case. Per the repository's Version Management policy in
`AGENTS.md`, the implementation PR for this slice is ordinary development work:

- it records its changelog-worthy change under `CHANGELOG.md` `## [Unreleased]`;
- it does **not** change released version identifiers and does not create a release tag;
- the synchronized Python/Bash/container/Helm/README version updates happen only in a
  separately scoped release/version-bump PR that selects the next version from the
  accumulated `[Unreleased]` entries.

This design document performs no changelog or version update itself.

## Tracker updates (same PR)

- `SSA-03` row: acceptance criteria extended with §1 (fail-closed merge + size cap), §2
  (duplicate-name rule), §4 (snapshot-built client), §5 (mutation barrier); this document
  linked as the slice design.
- New finding rows:

| id | severity | summary |
| --- | --- | --- |
| new-F1 | Medium | Repair merges kubeconfigs fail-open (debug-skip of unreadable files; `max_size=0` bypass) then mutates |
| new-F2 | Medium | Client built by re-reading files after manual matching (TOCTOU / dual-resolver disagreement) |
| new-F3 | Low | Duplicate names and duplicate YAML keys silently last-write-win in the manual merge |

## Acceptance criteria

1. No mutating repair action can occur after any merge-level failure, for any cluster
   whose resolution was not exactly one candidate, for a cluster whose selected user is
   exec-based, or for a cluster whose client construction failed after a unique match.
2. The client used for mutation is provably built from the matched snapshot (no file
   re-read between match and mutate).
3. A same-name group carrying more than one distinct content variant anywhere in the
   KUBECONFIG chain aborts repair before any mutation, and the failure identifies **every
   occurrence in that group** — including any occurrence byte-identical to an earlier one
   — by complete source location: entry kind, name, source file path, and zero-based
   in-file list index, each labelled with which variant it carries. Two files yield both
   paths with their respective indexes; two entries in one file yield that path with both
   distinct indexes; groups larger than two are located just as completely. Variants are
   distinguished by a stable index or digest; no entry content or credential material
   appears in the diagnostic.
4. Python and collection normalize endpoints identically (shared test vectors).
5. The `max_size=0` bypass is gone; oversized inputs fail closed.
6. Every malformed `cluster.server` value in the §3 rejection table fails closed before
   endpoint matching with zero mutating calls, and its diagnostic identifies the entry by
   kind, name, source path, and zero-based index without echoing a credential-bearing URL.
7. Replacing or deleting a `certificate-authority`, `client-certificate`, `client-key`, or
   `tokenFile` file after the snapshot is built cannot change the identity the mutation
   client authenticates as; captured credential contents never appear in logs, results,
   state, or errors. Exec-plugin users cannot satisfy this property and are rejected
   fail-closed on the repair path rather than exempted from it.
