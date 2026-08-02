# R3-01b-DESIGN-B2 — Finalization Register/`set_fact` Clobbers + Collision Guardrail

**Status:** APPROVED FOR IMPLEMENTATION PLANNING (2026-07-28)
**Design identifier:** R3-01b-DESIGN-B2
**Approved base:** `0bf55db9eed76ae7d60844b806975c04cd0111e4` (merge commit for PR #201)
**Supersedes:** R3-01b-DESIGN-B1 (accepted in substance; this revision applies the six
required corrections and preserves every other B1 decision and boundary)
**Tracker row:** `R3-01b` (planned) — findings `R3-A2`, `R3-A3`
**Governance artifact created for this revision:** issue
[#202](https://github.com/tomazb/rh-acm-switchover/issues/202)
(separate preflight-collision debt)

This is a design for operator approval only. It does **not** authorize writing the
implementation plan, editing production code, creating an implementation branch,
opening the `R3-01b` PR, or changing tracker status. The only side effect taken to
prepare this revision is the creation of debt issue #202 (governance preparation,
per correction 4).

---

## 1. Scope and boundaries (preserved from B1)

**In scope (`R3-01b`):**

- `R3-A2` — `roles/finalization/tasks/cleanup_restores.yml` `register`/`set_fact`
  name collision on `_acm_secondary_restores_to_cleanup`.
- `R3-A3` — `roles/finalization/tasks/discover_resources.yml` old-hub
  `register`/`set_fact` name collision on `_old_hub_existing_restore_info`.
- A repository guardrail that fails when a `register` target collides with a
  `set_fact` name in the same task file, across a defined recursive boundary.

**Out of scope / strict separation (preserved from B1):**

- **`TR2D-02`** (fresh-read Argo CD resume OCC parity) is a distinct planned
  boundary. `R3-01b` must not be coupled to, blocked by, or fold work from
  `TR2D-02`. No `TR2D-02` behavior, test, or file is touched here.
- The Argo CD scoped-discovery regression (`R3-01` / `TR2D-01`, `R3-A1`) is
  already merged (PR #200) and is not reopened.
- The **preflight** primary-seed collision is **not fixed** by `R3-01b`. It is
  separate debt tracked by issue #202 and is only *allowlisted* by the guardrail
  (see §6.4).

---

## 2. Root cause (the skipped-register clobber family)

Ansible defines a `register` target **even when the task is skipped**, assigning
`{'changed': False, 'skipped': True, 'skip_reason': ..., 'false_condition': ...}`.
When a `register` name is reused as a `set_fact` name in the same file, a skipped
registered task overwrites the fact with a skipped result that has **no `resources`
key**. Every downstream `| default([])` then silently converts that loss into an
empty-list "success." This is the same family as merged `R3-A1`.

### 2.1 Executable evidence (this revision)

Verified locally on `ansible-core` (installed 2.21.0). [Unverified] Expected to
behave consistently on the collection floor; mandatory confirmation under
ansible-core 2.15.x remains an implementation gate (see §7).

*Ordering clobber (R3-A2 shape): dry-run `set_fact` runs first, a later skipped
same-name `register` clobbers it.*

```
restore_count=0
final={'changed': False, 'skipped': True, 'skip_reason': ..., 'false_condition': "mode != 'dry_run'"}
```

Even though the dry-run `set_fact` seeded two Restore resources, the preview
reports `restore_count=0`.

*Guard-defeat (preflight shape): a skipped `register` defines the variable, so a
later `set_fact ... when: <var> is not defined` does not fire.*

```
defined=True value={'changed': False, 'skipped': True, ...}
seed_fired=False  has_resources_key=False  resources_len=0
```

---

## 3. In-scope defects (exact locations)

### 3.1 `R3-A2` — `cleanup_restores.yml`

```yaml
# line ~3: dry-run publishes discovered/injected restores under this name
- ansible.builtin.set_fact:
    _acm_secondary_restores_to_cleanup: "{{ acm_finalization_restores_info | default({'resources': []}) }}"
  when: mode == 'dry_run'

# line ~14: execute-mode live read REUSES the same name; skipped in dry-run,
# which clobbers the set_fact above with {skipped: True}
- kubernetes.core.k8s_info: { kind: Restore, ... }
  register: _acm_secondary_restores_to_cleanup
  when: mode != 'dry_run'
```

Effect: the dry-run cleanup preview (`restore_count`, `restore_names`) is always
empty, hiding which Restore resources execute mode would delete.

### 3.2 `R3-A3` — `discover_resources.yml` (old-hub restore, lines ~55–78)

```yaml
- ansible.builtin.set_fact:
    _old_hub_existing_restore_info: { resources: [] }   # deliberate dry-run seed
  when: [_old_hub_existing_restore_info is not defined, mode == dry_run, old_hub_action == secondary, not restore_only]

- kubernetes.core.k8s_info: { kind: Restore, name: restore-acm-passive-sync, ... }
  register: _old_hub_existing_restore_info              # SAME name; skipped in dry-run
  when: [_old_hub_existing_restore_info is not defined, mode != dry_run, old_hub_action == secondary, not restore_only]
```

Effect: benign **only** on the execute-mode live path (the real result is fetched
anyway); the deliberately-seeded dry-run value and any injected fixture value are
defeated by the skipped register. The three sibling reads in the same file
(`_acm_finalization_backup_schedules_live_info`, `_acm_finalization_mch_live_info`,
`_acm_finalization_restores_live_info`) already use the correct distinct-name +
`not skipped` pattern; this block is the outlier.

---

## 4. Correction 1 — preflight collision described correctly (out of scope, debt)

The B1 characterization that the restore-only seed "wins because it appears after
the registered tasks" is **wrong** and is corrected as follows.

In `roles/preflight/tasks/discover_resources.yml`, each primary read registers to
`acm_primary_*` and the terminal seed block sets the same `acm_primary_*` names,
guarded by `when: restore_only and acm_primary_mch_info is not defined`.

In `restore_only` mode every primary read is skipped (`not restore_only` is false),
but each skipped `register` still **defines** its `acm_primary_*` variable as a
skipped result. Because `acm_primary_mch_info` is therefore already defined, the
terminal seed's `acm_primary_mch_info is not defined` guard is **False**, the seed
block never fires, and **none** of the 10 `acm_primary_*` facts receive their
`{ resources: [] }` seed. **The intended seed is defeated** (proven in §2.1).

Downstream operational impact — what restore-only preflight validation does with
primary facts that carry no `resources` key — is **not** characterized here.
Per correction 1, it **requires separate executable verification** and must not be
called benign without it. Issue #202 records this and explicitly does not pre-judge
severity.

`R3-01b` does not fix this. The guardrail records it as **debt-category** allowlist
exceptions referencing #202 (see §6.4).

---

## 5. Chosen approach — Alternative A (preserved from B1)

**Alternative A: distinct live-query and published-fact names, plus a static
pytest guardrail** (consistent with the existing YAML-contract test pattern in
`ansible_collections/tomazb/acm_switchover/tests/unit/` and
`yaml_contract_helpers.py`). Alternative B (a custom `ansible-lint` rule) and
Alternative C (runtime in-task assertions) remain rejected for the reasons recorded
in B1: Alternative A runs in the existing unit lane, needs no extra runtime
dependency, and fails red deterministically in CI.

### 5.1 Fix pattern for `R3-A2` and `R3-A3`

For each defect, split the two roles of the colliding name:

1. **Live-query name** — the `register` target of the `k8s_info` read becomes a
   distinct `_*_live_info` name (e.g. `_acm_secondary_restores_to_cleanup_live_info`,
   `_old_hub_existing_restore_live_info`).
2. **Published-fact name** — a single guarded `set_fact` publishes the
   authoritative fact (`_acm_secondary_restores_to_cleanup`,
   `_old_hub_existing_restore_info`) from exactly one validated source:
   - dry-run / test-injected discovered data, **or**
   - the live-query result, guarded by `not (<live>.skipped | default(false))`.

This mirrors the already-correct sibling reads and preserves both execute-mode
refresh and fixture-injection semantics (the tracker acceptance criteria).

---

## 6. Runtime shape validation and the guardrail

### 6.1 Runtime shape validation before authoritative publication (correction 2)

Before a fact is published as authoritative and consumed, the design **requires**
explicit shape validation (a `set_fact`/`assert` step, not a silent default):

- the selected source **must be a mapping**;
- `resources` **must be present and list-valued**;
- each Restore entry used for classification **must be a mapping**;
- a **skipped** result **cannot be authoritative** (`skipped | default(false)`
  must be false for the live path);
- **missing or malformed data must fail** with a sanitized, actionable message —
  it must **not** degrade into an empty-list success.

**Remove the `default({'resources': []})` reliance for missing `R3-A2` discovery
data.** `cleanup_restores.yml` currently does
`acm_finalization_restores_info | default({'resources': []})`; because
`acm_finalization_restores_info` is published upstream with a `not skipped` guard,
its absence or malformation is an **error condition**, not "zero restores." The fix
validates presence/shape and fails closed instead of silently defaulting.

**Exception — deliberate authoritative absence is preserved.** The old-hub dry-run
path's deliberately-seeded `_old_hub_existing_restore_info: { resources: [] }`
remains a **valid authoritative absence** and must continue to be accepted. Shape
validation distinguishes a *deliberately seeded* `{resources: []}` (valid) from a
*missing/skipped/malformed* value (fail). The distinction is: the value is a mapping
with a list-valued `resources` key and is not a skipped register result.

### 6.2 Guardrail scan boundary (correction 5)

The guardrail scans, recursively:

- `roles/**/tasks/**/*.{yml,yaml}`
- `roles/**/handlers/**/*.{yml,yaml}`
- `playbooks/**/*.{yml,yaml}`

Task lists are flattened through `block`/`rescue`/`always` (reuse
`yaml_contract_helpers._flatten_tasks`).

### 6.3 Detection rule — literal-scalar boundary (preserved from B1)

The guardrail compares **literal scalar** names only:

- collect every literal scalar `register:` value in a file;
- collect every literal scalar **key** of `set_fact` / `ansible.builtin.set_fact`
  mappings in the same file;
- a **collision** is any name present in both sets within one file.

Templated/Jinja-computed `register` or `set_fact` names are **out of the boundary**
(the guardrail does not attempt to resolve dynamic names, avoiding false precision).
The guardrail is intentionally conservative: it flags the *structural* collision
regardless of whether `when` guards make the two tasks mutually exclusive, because
skipped-register semantics make "mutually exclusive by `when`" unsafe (§2).

### 6.4 Two-category exception model with category-specific metadata (correction 6)

The guardrail reads a checked-in allowlist. Every allowlisted collision is exactly
one of two categories, with **category-specific required metadata**:

- **Intentional exception** — a collision that is safe by construction and expected
  to remain. Required fields: `path`, `variable`, `structural_rationale`,
  `category: intentional`, `approval_reference`.
- **Debt exception** — a known latent collision not yet corrected. Required fields:
  everything an intentional exception requires **plus** `issue_reference`
  (tracker/issue) and `removal_condition` (what makes the entry deletable).

The guardrail fails if: (a) a non-allowlisted collision exists, or (b) an allowlist
entry is missing a field required for its category, or (c) an allowlisted collision
no longer exists in source (stale entry). This keeps the allowlist honest.

### 6.5 Enumerated collisions in the current boundary and their disposition

A scan of the boundary (§6.2) finds **14** literal collisions today. Dispositions:

| # | File | Variable(s) | Disposition |
|---|------|-------------|-------------|
| 1 | `finalization/tasks/cleanup_restores.yml` | `_acm_secondary_restores_to_cleanup` | **Fixed** by `R3-A2` → no allowlist entry |
| 2 | `finalization/tasks/discover_resources.yml` | `_old_hub_existing_restore_info` | **Fixed** by `R3-A3` → no allowlist entry |
| 3 | `finalization/tasks/disable_old_hub_observability.yml` | `_acm_old_hub_mco_info` | **Intentional** — `register` on the execute-mode happy path inside a `block`, with a `rescue` `set_fact` fallback for an absent CRD; consumers are execute-mode-only |
| 4 | `post_activation/tasks/verify_managed_clusters.yml` | `acm_switchover_cluster_verify_result` | **Intentional** — unconditional module `register` followed by a conditional in-place `combine` refinement of the same fact (the register task has no `when`, so it cannot be skipped) |
| 5–14 | `preflight/tasks/discover_resources.yml` | `acm_primary_namespace_info`, `acm_primary_mch_info`, `acm_primary_backups_info`, `acm_primary_velero_pods_info`, `acm_primary_backup_schedules_info`, `acm_primary_bsl_info`, `acm_primary_dpa_info`, `acm_primary_cluster_deployments_info`, `acm_primary_managed_clusters_info`, `acm_primary_managed_cluster_backups_info` | **Debt** — defeated restore-only seed (§4); `issue_reference: #202`; `removal_condition: preflight primary facts seeded deterministically without a skippable same-name register (issue #202 resolved)` |

Entries 3 and 4 must have their `structural_rationale` re-confirmed during
implementation before they are written as intentional; if either turns out to have
a reachable defeat path, it is reclassified as debt with its own issue rather than
silently allowlisted as intentional.

---

## 7. ansible-core 2.15.x verification requirement (preserved from B1)

The collection floor is `requires_ansible: ">=2.15.0"`. The skipped-register-defines
-variable behavior underpinning both the defect and the fix must be **verified on
ansible-core 2.15.x** (not only on the developer's installed version), covering:

- a skipped `register` defines the variable as a skipped result with no
  module payload keys;
- a later `set_fact ... when: <var> is not defined` does not fire once the var is
  defined by a skipped register;
- the distinct-name + `not skipped` published-fact pattern behaves identically on
  2.15.x and on the CI matrix versions.

The §2.1 evidence was produced on 2.21.0; the 2.15.x confirmation is a required
implementation gate, recorded in the eventual implementation plan (not written yet).

---

## 8. Test plan

### 8.1 Guardrail tests

- Positive: the guardrail passes on a corrected tree (entries 1–2 fixed; 3–4
  intentional; 5–14 debt/#202 allowlisted).
- Red-on-regression: reintroducing either `R3-A2` or `R3-A3` collision fails the
  guardrail.
- Allowlist integrity: a debt entry missing `issue_reference` or
  `removal_condition` fails; an intentional entry missing `structural_rationale`
  or `approval_reference` fails; a stale entry (allowlisted collision absent from
  source) fails.
- Boundary coverage: a synthetic collision in a `handlers/` file and in a
  `playbooks/` file is detected (proves §6.2 globs).

### 8.2 Behavior tests for the fixes

- `R3-A2`: dry-run preview reports the true `restore_count`/`restore_names` for
  the Restore resources execute mode would delete.
- `R3-A3`: injected fixture data is preserved on the guarded dry-run/seed path,
  while required execute-mode refresh remains a live read.

### 8.3 Negative tests (correction 3)

For the published-fact/shape-validation contract (§6.1), add negative tests for
discovery/fixture data that is:

- **undefined** (variable not set at all);
- **missing** (`resources` key absent);
- **non-list** (`resources` is a string/mapping/number);
- **skipped** (a skipped-register result standing in for the source);
- **malformed** (a Restore entry that is not a mapping; a non-mapping selected
  source).

Each must **fail closed** with a sanitized message rather than produce an empty
successful result. A companion positive test asserts that a deliberately-seeded
`{ resources: [] }` on the old-hub dry-run path is accepted as authoritative
absence (§6.1 exception).

---

## 9. Acceptance criteria (tracker + corrections)

- Finalization dry-run reports the Restore resources execute mode would delete
  (no more forced `restore_count: 0`).
- Injected fixture data is preserved on guarded discovery paths while required
  execute-mode refresh remains live.
- The guardrail fails red against either reintroduced collision, and across the
  full recursive boundary (§6.2).
- Missing/malformed/skipped/non-list authoritative discovery data fails closed;
  a deliberately-seeded `{resources: []}` old-hub absence still succeeds.
- Negative tests (§8.3) exist and fail closed.
- The preflight collision is documented as debt (#202), allowlisted as
  debt-category, and its downstream impact is flagged for separate executable
  verification rather than assumed benign.
- No `TR2D-02` file, test, or behavior is modified.

---

## 10. Parity and documentation impact

- **Parity:** `R3-A2`/`R3-A3` are collection-only Ansible clobbers; the Python CLI
  finalization preview already reports real restore data, so this is a
  parity-*restoring* fix, not an intentional divergence. **No Python production or
  Python test files are modified by `R3-01b`** — the correction is collection-only,
  which preserves the approved scope and prevents unnecessary parity-test expansion.
  No approval-gated parity change is introduced. The behavior map / parity matrix
  entries for finalization dry-run preview are reviewed for accuracy; no
  support-status change.
- **Docs:** `CHANGELOG.md` `[Unreleased]` records the finalization dry-run preview
  fix and the new collision guardrail. Tracker row `R3-01b` and the finding rows
  `R3-A2`/`R3-A3` are updated **only after** approval and delivery (not in this
  design step).

---

## 11. What is explicitly NOT done in this step

- No implementation plan written *(before this approval)*.
- No production code edited.
- **No Python production or Python test files are modified by `R3-01b`
  (collection-only correction).**
- No implementation branch created.
- No `R3-01b` PR opened.
- No tracker status changed.
- No work on issue #202 (separate preflight debt).
- Only side effect: debt issue #202 created (governance preparation, correction 4).
