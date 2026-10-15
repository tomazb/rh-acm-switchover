# ManagedCluster expectation safety — design amendment

- **Governing issue:** [#340](https://github.com/tomazb/rh-acm-switchover/issues/340)
  (split out of #332).
- **Base:** `origin/ansible` `0bce94aa`.
- **Amends:** [July migration-evidence design](2026-07-29-migration-evidence-design.md)
  §2–§3 and the [August R4-04 amendment](2026-08-27-r4-04-current-base-design-amendment.md)
  §7 and acceptance criteria 25 and 27.
- **Changes:** runtime behavior in both form factors, plus their tests and parity vectors.
- **Does not change:** RBAC, protected files, parity status, or the support boundary.

## 1. Problem

R4-04 (PR #335) correctly accepts an empty destination ManagedCluster inventory when the
resolved expectation is "nothing". That acceptance is safe only under two conditions:

1. "nothing expected" is itself proven;
2. every phase applies the same rule to it.

Neither condition holds on the base.

- **A — derivation is fail-open.** Preflight derives the expected names from reads that can
  turn a failure into zero names:
  - Python uses the ordinary LIST, which maps list 404 to `[]`, defaults a missing `items`
    to `[]`, and drops nameless entries.
  - The Collection uses plain `k8s_info` followed by
    `resources | default([]) | selectattr('metadata.name', 'defined')`.

  With no explicit floor, the floor *is* the derived count. A false zero therefore admits
  an empty destination at activation and at post-activation.
- **B — the phases disagree.** Take a run with recorded names, an explicit floor of 0, an
  activation-covering waiver, and an empty destination. Activation accepts it.
  Post-activation refuses it as `inventory_empty`. The run is stuck with no intervening
  state change.

Both are one contract: **when may an empty destination be accepted?**

## 2. Contract

### E1 — the expected set comes only from a positively complete strict read

The derived expected set is consumed through the existing seams: Python `RunRecord`
`managed_cluster_expectation`, Collection checkpoint facts
`expected_managed_cluster_names` and `expected_managed_cluster_count`. It is derived only
from one strict LIST of `managedclusters.cluster.open-cluster-management.io/v1` on the
**primary** hub, through the merged R4-03 strict-read authority:

- Python `KubeClient.list_managed_clusters_strict`;
- Collection `acm_k8s_read_outcome` with `read_mode: list`.

Pagination completeness, snapshot revision, the restart budget, and discovery gating are
owned by that authority. No new strict-read algebra and no second expectation resolver are
introduced (criterion 27).

| Read outcome | Derivation |
| --- | --- |
| `items` (Python `ITEMS`, Collection `ok`), empty | Legitimate zero. |
| `items` containing only `local-cluster` | Legitimate zero; `local-cluster` is never expected. |
| `items` with non-local members | The sorted non-local names. |
| CRD / kind not served (`crd_absent`, `kind_not_served`) | **Refuse.** |
| Discovery failure, 401/403, transport, timeout, decode | **Refuse.** These are `error`, never absence. |
| Incomplete pagination, continuation/revision mismatch | **Refuse.** Already `error` in the primitive. |
| Malformed response (`items` not a list, non-object member) | **Refuse.** Already `error` in the primitive. |
| A member with a missing, non-string, or empty `metadata.name` | **Refuse the whole derivation.** |
| Two members with the same name | **Refuse the whole derivation.** |

A refusal is a critical preflight failure with a stable, sanitized reason. Preflight fails,
and no expectation is recorded: the run cannot reach activation with an underived
expectation. This holds because the phase-entry guard already refuses a missing recorded
expectation unless an explicit floor is given (`_resolve_managed_cluster_expectation`; the
Collection's skipped-preflight assert).

**Why `crd_absent` refuses on the primary.** Outside restore-only, preflight has already
required a MultiClusterHub on the primary. An ACM hub that does not serve the
ManagedCluster API is contradictory evidence, not a legitimate empty fleet. The July
design §3 maps `crd_absent` to a typed fatal error for every migration inventory consumer;
this amendment adds the primary-side derivation as one more consumer under that rule.

**Why any malformed member invalidates the whole derivation.** A dropped member lowers
the expected set. A lowered set lowers the floor in derived mode, and the empty-destination
admission depends on that floor. Partial acceptance would reintroduce exactly the false
zero this amendment removes. Duplicates are refused for the same reason: they mean the
read is not one consistent inventory. Duplicate detection is a consumer-mapping rule of
this derivation. It does not add a new outcome to the strict-read primitive.

**Restore-only** derives nothing and keeps its existing default floor of one.

**Stale or incomplete inventory.** Freshness is the strict read's snapshot. The derivation
runs in preflight on every non-skipped preflight run. A resumed run consumes the recorded
expectation; it never re-derives it from live state (unchanged). The snapshot
`resourceVersion` is **not** persisted: no consumer needs derivation provenance, and
recording it would add persisted state (Reviewer Complexity Firewall).

### E2 — an empty destination is accepted only when nothing was ever expected

Both phases, and both form factors, apply one rule:

> An empty destination ManagedCluster inventory — no members at all, including no
> `local-cluster` — is acceptable only when the effective count floor is 0 **and** the
> recorded expected-name set is empty.

A waiver lifts only the per-name predicate, which is "every recorded name is present". It
never erases the recorded expectation (July §2), so it never turns "names were expected"
into "nothing was expected".

Meaning of `min=0`:

| Recorded names | Waiver covering the phase | Empty destination |
| --- | --- | --- |
| none | n/a | accepted (unchanged) |
| present | none | refused: the names are missing (unchanged) |
| present | covers the phase | **refused as `inventory_empty`** — post-activation unchanged, activation aligned |

Post-activation `managed_cluster_readiness` already implements this rule. Activation is
aligned to it:

- Python `SecondaryActivation._wait_for_managed_clusters_restored` and
  `_verify_managed_clusters_restored`;
- Collection `managed_clusters_poll`, `verify_managed_clusters`, and the
  `wait_for_restore.yml` wait gate.

The zero-floor shortcut applies only when no names are recorded. Otherwise the wait
continues until the inventory is non-empty, and the final verification refuses an empty
inventory with the same wording in both form factors.

**Rejected alternative: loosen post-activation.** Accepting waived names + floor 0 + an
empty destination at post-activation would make the waiver an "accept an empty hub"
switch. That would be a new operator capability with a weaker safety posture: an empty
inventory right after a restore is the primary signal that the restore brought nothing
back. The waiver was designed for "some recorded clusters will not return", not "none
will".

**Operator consequence.** A run in that state refuses at activation. Activation is the
earliest point where the destination inventory exists, and the refusal no longer falls
later in post-activation. The ways forward are:

- the clusters appear, and a retry passes; or
- the operator re-runs preflight. The expectation is then re-derived from the then-current
  primary, and if that is a strictly proven zero, the empty destination is accepted.

### Phase consistency (Q6/Q7)

For the inventory-shape predicates — names, floor, emptiness — any destination inventory
that activation accepts is not refused by post-activation for the same effective
expectation. Post-activation additionally requires every non-local cluster to be Available
and Joined. That is a later, intentionally stronger readiness predicate: clusters
reconnect after activation, and it is not structurally guaranteed to fail.

A member without a name is malformed in both phases. Activation never counts it as the proof
that the destination is non-empty.

A resume over completed activation evidence (`restore.completed_at` journaled) revalidates
the completion proof and does not re-read the ManagedCluster inventory. That is the R4-04
resume contract, and this amendment does not change it. On that path a refusal can appear
first at post-activation only after a real change in destination state, or for a journal
completed under the earlier permissive rule. Such journals exist only from unreleased R4-04
builds: R4-04 is still under `[Unreleased]`. Post-activation still refuses in both cases, so
the outcome is fail-closed and never a false success.

### Waiver audit (Q8)

Unchanged. The waiver remains journaled with `actor`, `reason`, optional `request_id`,
`scope`, and `outcome: waived`, and is never recorded as success. This amendment narrows
what a waiver can admit. It adds no new waiver surface.

### Ownership (Q9) and parity (Q10)

Derivation is owned by the existing ManagedCluster inventory interpretation module in each
form factor, next to `managed_cluster_readiness`:

- Python `lib/migration_post_activation.py::expected_managed_cluster_names(items)`, called
  by `modules/preflight_coordinator.py` after the strict read;
- Collection `plugins/module_utils/migration_post_activation.py::expected_managed_cluster_names`,
  exposed as `acm_migration_evidence` operation `expected_managed_clusters` and called by
  `roles/preflight/tasks/post_identity.yml`.

Each pure function takes only the members of a successful strict read, so both are held
equal by the shared R4-04 JSON vectors. Each caller refuses every other read outcome first
with the same code, `inventory_unreadable`. The Python caller is the coordinator; the
Collection caller is the module operation.

The two share no code. The Collection's primary ManagedCluster discovery read in
`roles/preflight/tasks/discover_resources.yml` goes through the shared strict-read seam
`roles/activation/tasks/strict_read.yml`, which gains a `hub` parameter defaulting to the
secondary. The read uses `hub: primary` and key `primary_managedclusters`. That seam already:

- registers privately with `no_log`;
- publishes only `{read_status, resources, resource_version}` of a live read, and a
  pre-seeded fixture read as given;
- honours a pre-seeded `acm_switchover_test_overrides.migration_reads` entry only in
  dry_run/validate.

So an executing run always reads the live primary, as Python always does. This follows the
AGENTS execution-time discovery rule: the derived expectation decides which destination state
the mutating phases accept. The seam publishes the outcome into the private fact
`_acm_primary_managed_clusters_read`, which is the derivation input. A copy is published as
`acm_primary_managed_clusters_info` for the other preflight checks. The public
`acm_primary_*_info` names follow the documented pre-seed pattern, so a vars file that carries
one by accident never becomes the derivation input.

The two managed-clusters backup checks are unchanged. In both form factors they still consume
the ManagedCluster inventory fail-open, which is pre-existing behavior outside this slice. They
cannot turn an unreadable inventory into a passing preflight, because the derivation's critical
result fails preflight first. Moving them to the strict authority is a paired change for both
form factors (#347). The execute-mode pre-seed seam on the other discovery facts
is likewise pre-existing and tracked separately (#346).

**Trust boundary.** Extra vars are the operator's input channel, and Ansible gives them
precedence over every play variable: registered results, `set_fact`, and private
`_acm_*` names alike. No play-level guard can therefore be immune to one. A guard variable is
itself shadowable. More directly, an operator who controls extra vars can set the derived
fact `acm_switchover_expected_managed_cluster_names` to `[]` without touching the read at all.
The collection's supported input surface is the grouped `acm_switchover_*` namespaces
(`variable-reference.md`). Result facts, discovery facts and `_acm_*` internals are outputs,
and setting them is unsupported. This slice guarantees what is enforceable in a playbook.
Every executing run performs the live strict primary read, and that read's test seam
(`migration_reads`) is honoured only in dry_run/validate. Other pre-existing test seams that
are honoured in every mode are outside this slice (#346).

Parity tests hold the two derivation functions and the two activation decisions equal over
shared cases. Required cases cover every row of both tables above, plus pagination
completeness, which is owned by and already vectored for the primitive.

## 3. Acceptance mapping

Issue #340 criteria 1–8 are the acceptance gate. Criteria 1–4 are E1, 5 is E2, 6–7 are the
regression and parity evidence, and 8 is the boundary.
