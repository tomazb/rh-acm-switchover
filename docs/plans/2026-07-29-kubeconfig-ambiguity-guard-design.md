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

- Any path listed in KUBECONFIG (or the default path) that is missing, unreadable,
  oversized, or YAML-invalid aborts the entire repair step with the file name and error —
  before any per-cluster work. A mutating path must not run on a partial view.
- The `max_size=0` bypass is removed; the standard size limit applies to every merged file.
- YAML parsing uses a loader that **errors on duplicate mapping keys** instead of silently
  keeping the last.

### 2. Duplicate-name rule (official-loader-compatible)

- Merge keeps per-entry provenance: `(name, source_file, index)`.
- Same name, byte-identical content across files → first occurrence wins (official
  first-wins semantics), logged at debug.
- Same name, differing content → ambiguity failure naming the entry kind/name and both
  source files. No mutation for any cluster.

### 3. Endpoint matching (SSA-03 core, shared rule)

- Full normalized URL equality: scheme, lowercase host, explicit default port (`:443` for
  https), path preserved. No hostname collapse.
- Zero-match and multi-match are explicit non-mutation failures per cluster (SSA-03
  acceptance criteria, unchanged).
- The expected hub endpoint comes from the initialized secondary client's live
  configuration host (single source), normalized with the same rule — the second
  derivation at `:1608-1609` is removed.
- Collection: `server_host()` in `module_utils/klusterlet.py` is replaced by the same
  full-URL normalization; comparison at `:285-297` uses it (SSA-03 parity item).

### 4. Snapshot-built client

- The selected context's client is built with `config.new_client_from_config_dict(...)`
  from the **same merged snapshot** used for matching — the files are never re-read, so
  there is no TOCTOU window and no manual-vs-official-loader disagreement.
- Before handoff, file-referenced credentials (`certificate-authority`,
  `client-certificate`, `client-key`, `tokenFile`) are absolutized relative to their
  source file's directory — matching official loader semantics; embedded `*-data` fields
  pass through untouched; `exec` credential plugins pass through unchanged (their
  `command` resolution is the plugin runtime's concern, as with the official loader).

### 5. Mutation barrier

- Per cluster: no bootstrap-secret delete, no import-manifest apply, no klusterlet restart
  until endpoint resolution and client construction have both succeeded for that cluster.
- Global ambiguity failures (§1, §2) block all clusters; per-cluster zero/multi-match
  failures block that cluster and are reported in the summary (existing per-cluster error
  aggregation).

## Testing

- Ambiguity matrix, each case asserting **zero mutating calls**: missing file, unreadable
  file, oversized file, YAML error, duplicate YAML keys, duplicate name identical,
  duplicate name differing, zero-match, multi-match.
- Snapshot client: file modified between merge and client build → client uses snapshot
  values (assert via config dict), no re-read.
- Relative-path absolutization: CA/cert/key/tokenFile relative to a non-CWD source file
  resolve correctly; embedded `*-data` untouched; exec passthrough.
- Endpoint normalization table: default vs explicit port, host case, trailing path,
  scheme; Python and collection produce identical results (parity test).
- Expected-endpoint source: matcher compares against the secondary client's live host.
- Existing SSA-03 tests (hostname-collapse regression, zero/multi-match) unchanged.
- Version bump per repo policy (Python + collection, synced).

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

1. No mutating repair action can occur after any merge-level failure, or for any cluster
   whose resolution was not exactly one candidate.
2. The client used for mutation is provably built from the matched snapshot (no file
   re-read between match and mutate).
3. Two entries with the same name and different content anywhere in the KUBECONFIG chain
   abort repair with both files named.
4. Python and collection normalize endpoints identically (shared test vectors).
5. The `max_size=0` bypass is gone; oversized inputs fail closed.
