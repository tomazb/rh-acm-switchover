# Thermos PR 39 — Ansible RBAC Validation Hub-Loop Deduplication (Design/Spec)

- **Finding:** `R2-H3` (Thermos Review #2) — `roles/preflight/tasks/validate_rbac.yml`
  duplicates ~140 lines between its primary-hub and secondary-hub validation blocks.
- **Tracker row:** `PR 39` in [`thermos-resolution-plan.md`](../../../thermos-resolution-plan.md).
- **Prerequisite:** Python `H1` is merged into `ansible`
  (PR #148, merge `0afeea52`). `lib/rbac_validator.py` now validates hubs through
  `_validate_hub(...)` driven by a loop over an explicit per-hub data table. The H1
  design spec ([`2026-07-05-python-h1-rbac-unification-design.md`](2026-07-05-python-h1-rbac-unification-design.md))
  states the shape PR 39 must mirror: *"loop over a hub-role list feeding one
  parameterized include/task block, preserving registered-fact contracts."*
- **Scope guard:** No Python file changes. No behavior changes to effective RBAC
  permissions, verbs, resources, namespaces, validation strictness, fail-closed
  behavior, check-mode behavior, or operator-facing summary shape.

## Problem: exact duplicated blocks in `validate_rbac.yml`

The file (318 lines) runs the same 9-task sequence once per hub:

| # | Task (current names) | Primary lines | Secondary lines |
| --- | --- | --- | --- |
| 1 | Default Argo CD install type when checks disabled | 15–20 | 155–158 |
| 2 | Detect Argo CD Applications CRD | 22–33 | 160–169 |
| 3 | Detect Argo CD operator CRD | 35–48 | 171–183 |
| 4 | Fail closed on authorization denied (401/unauthorized) | 50–62 | 185–196 |
| 5 | Fail on unexpected CRD discovery error (non-403) | 64–74 | 198–207 |
| 6 | Record Argo CD install type | 76–93 | 209–223 |
| 7 | Expand required RBAC permissions (`acm_rbac_validate`) | 119–128 | 225–232 |
| 8 | Run SSAR checks (`include_tasks: run_ssar.yml`) | 130–135 | 234–239 |
| 9 | Collect denied permissions | 137–141 | 241–243 |
| 10 | Summarize (`acm_rbac_validate` with `denied_permissions`) | 143–153 | 245–253 |

Differences between the two copies are limited to: the hub name in task names,
messages, kubeconfig/context references, and register/fact suffixes; the
`when: not (acm_switchover_operation.restore_only | default(false))` gate on
every primary task; the primary-only `include_decommission` /
`include_old_hub_finalization` module arguments; explicit `_ssar_target_*` vars
on the secondary SSAR include (the primary relies on `run_ssar.yml` defaults,
which are the primary hub's kubeconfig/context — same values); and a cosmetic
Jinja difference in task 6 (primary sets `app_msg` via `{%- set -%}`, secondary
inlines the same expression — identical output).

Non-duplicated content that stays in the main file:

- Derive requested Argo CD RBAC mode (`_rbac_requested_argocd_mode`, lines 2–13).
- Determine primary old-hub finalization requirement
  (`_rbac_include_old_hub_finalization_primary`, lines 95–106, primary-only input).
- Determine Observability RBAC skip state (`_rbac_skip_observability`,
  lines 108–117, computed once, reused by both hubs and by nothing else).
- Managed-cluster RBAC validation (lines 255–287) — separate loop over
  `validate_managed_cluster_rbac.yml`, distinct `scope: managed_cluster`
  contract; mirrors Python's separate managed-cluster validation scope.
- Merge into the preflight accumulator (lines 289–313) and the
  `_acm_preflight_rbac_validation_completed` marker (lines 315–317).

## Registered facts and their consumers

| Fact | Registered/set where today | Consumed where |
| --- | --- | --- |
| `_rbac_requested_argocd_mode` | main file | both hub blocks (stays in main file) |
| `_rbac_argocd_app_crd_primary` / `_secondary` | CRD detect | same hub block only (tasks 3–6) |
| `_rbac_argocd_instance_crd_primary` / `_secondary` | CRD detect | same hub block only (task 6) |
| `_rbac_argocd_install_type_primary` / `_secondary` | tasks 1/6 | same hub block only (tasks 7, 10) |
| `_rbac_include_old_hub_finalization_primary` | main file | primary tasks 7, 10 |
| `_rbac_skip_observability` | main file | both hub blocks (tasks 7, 10); pinned by `test_preflight_parity.py` |
| `_rbac_expanded_primary` / `_secondary` | task 7 | same hub block (task 8) |
| `_rbac_denied_permissions` | `run_ssar.yml` | same hub block (task 9); generic, overwritten per hub today already |
| `_rbac_denied_permissions_primary` / `_secondary` | task 9 | same hub block (task 10) |
| `acm_primary_rbac_validation` / `acm_secondary_rbac_validation` | task 10 | merge task in main file; result IDs `preflight-rbac-primary`/`-secondary` asserted end-to-end by `tests/integration/test_preflight_role.py::test_preflight_rbac_failure_still_reports_backup_findings` |
| `acm_managed_cluster_rbac_validation` | managed-cluster summarize | merge task (unchanged) |
| `_acm_preflight_rbac_validation_completed` | main file | `roles/preflight/tasks/main.yml` (unchanged) |

No consumer outside `validate_rbac.yml` reads any `_rbac_*_primary` /
`_rbac_*_secondary` fact (verified by repo-wide grep). The externally meaningful
outputs are `acm_primary_rbac_validation`, `acm_secondary_rbac_validation`,
`acm_managed_cluster_rbac_validation`, and
`_acm_preflight_rbac_validation_completed`. All per-hub names are preserved
anyway (see below) so tests and future consumers keep the same contract.

## Chosen design: hub-data table + `include_tasks` loop over a shared hub task file

Mirror of H1's `hub_validations` table + `_validate_hub(...)` loop:

**`validate_rbac.yml` (main file) keeps, in order:**

1. Derive requested Argo CD RBAC mode (unchanged).
2. Determine primary old-hub finalization requirement (unchanged, primary-only,
   still gated on `not restore_only`).
3. Determine Observability RBAC skip state (unchanged — computed once).
4. **New:** Build the per-hub validation table:

```yaml
- name: Build hub RBAC validation table
  ansible.builtin.set_fact:
    _rbac_hub_validations:
      # Asymmetries are explicit data, mirroring Python H1's hub_validations
      # table in lib/rbac_validator.py:
      # - the primary hub is skipped entirely in restore-only mode;
      # - decommission/old-hub-finalization checks apply to the primary hub only;
      # - the secondary hub never runs decommission.
      - hub: primary
        enabled: "{{ not (acm_switchover_operation.restore_only | default(false)) }}"
        kubeconfig: >-
          {{
            ''
            if (acm_switchover_operation.restore_only | default(false))
            else acm_switchover_hubs.primary.kubeconfig
          }}
        context: >-
          {{
            ''
            if (acm_switchover_operation.restore_only | default(false))
            else acm_switchover_hubs.primary.context
          }}
        include_decommission: "{{ acm_switchover_operation.old_hub_action | default('secondary') == 'decommission' }}"
        include_old_hub_finalization: "{{ _rbac_include_old_hub_finalization_primary | default(false) }}"
      - hub: secondary
        enabled: true
        kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
        context: "{{ acm_switchover_hubs.secondary.context }}"
        include_decommission: false  # secondary hub never runs decommission
        include_old_hub_finalization: false
```

5. **New:** the loop (the analog of H1's `for ... in hub_validations`):

```yaml
- name: Validate RBAC permissions per hub
  ansible.builtin.include_tasks: validate_rbac_hub.yml
  loop: "{{ _rbac_hub_validations }}"
  loop_control:
    loop_var: rbac_hub
    label: "{{ rbac_hub.hub }}"
  when: rbac_hub.enabled | bool
```

The per-item `when:` skip is the Ansible analog of H1's
"Primary hub not available; skipping primary RBAC validation" branch — the
skipped iteration is visible in playbook output with the hub label.

6. Managed-cluster section, merge task, completion marker (all unchanged).

**New shared `validate_rbac_hub.yml`** (parameterized by `rbac_hub`) holds the
10-task sequence once. Intermediate registers use generic names (safe: each is
consumed within the same iteration before the next hub starts, exactly like the
already-generic `_rbac_denied_permissions` today), and per-hub facts are
re-published with templated `set_fact` keys so every existing name survives:

- `Default Argo CD install type for {{ rbac_hub.hub }} hub when checks are disabled`
  → sets `_rbac_argocd_install_type_{{ rbac_hub.hub }}: unknown`
  (`when: _rbac_requested_argocd_mode == 'none'`; restore-only gating comes from
  the loop-level `enabled`).
- CRD detects register `_rbac_argocd_app_crd` / `_rbac_argocd_instance_crd`
  (generic), then re-publish `_rbac_argocd_app_crd_{{ rbac_hub.hub }}` /
  `_rbac_argocd_instance_crd_{{ rbac_hub.hub }}` for contract continuity.
- Fail-closed tasks keep the exact message text with the hub name templated:
  `Authorization denied while inspecting Argo CD CRDs on the {{ rbac_hub.hub }} hub (HTTP 401). …`
  and `Unable to inspect applications.argoproj.io on the {{ rbac_hub.hub }} hub during RBAC preflight: …`
  — rendered output byte-identical to today for both hubs. `when:` logic
  unchanged: 401/unauthorized substring match on `.msg` fails closed; the
  unexpected-error task still excludes only 403/forbidden (deferred to RBAC
  validation) and still gates on `.msg` presence, never `.failed`.
- Record install type using the primary variant's `{%- set app_msg -%}` template
  (output-identical to the secondary variant), publishing
  `_rbac_argocd_install_type_{{ rbac_hub.hub }}`.
- Expand permissions with
  `include_decommission: "{{ rbac_hub.include_decommission }}"`,
  `include_old_hub_finalization: "{{ rbac_hub.include_old_hub_finalization }}"`
  (module default is `false`, so passing explicit `false` for the secondary is
  behavior-identical to today's omission), registering `_rbac_expanded_hub`,
  re-published as `_rbac_expanded_{{ rbac_hub.hub }}`.
- SSAR include passes explicit
  `_ssar_target_kubeconfig: "{{ rbac_hub.kubeconfig }}"` /
  `_ssar_target_context: "{{ rbac_hub.context }}"` for both hubs. For the
  primary these equal `run_ssar.yml`'s defaults, so behavior is unchanged; the
  hub file no longer depends on positional defaults.
- Collect denied → `_rbac_denied_permissions_{{ rbac_hub.hub }}`.
- Summarize registers `_rbac_hub_validation_result`, re-published as
  `acm_{{ rbac_hub.hub }}_rbac_validation` — preserving the exact names the
  merge task and integration assertions consume.

Nested-loop safety: the outer loop uses `loop_var: rbac_hub`; the inner loops in
`run_ssar.yml` and the managed-cluster file keep `item` /
`managed_cluster_target` — no loop-variable collision (same pattern the file
already uses for managed clusters).

### Rejected alternatives

- **Two explicit `include_tasks` calls with `vars:`, no loop.** Slightly simpler
  variable scoping, but the H1 spec rejected the no-loop shape on the Python
  side precisely because PR 39 must mirror the loop-over-hub-table pattern; two
  bare calls also leave the asymmetries scattered across call sites instead of
  in one table.
- **Per-task `loop: [primary, secondary]` inside the existing file.** A
  `register:` under `loop` produces a `.results` list, destroying every
  registered-fact contract; every task would need per-hub `when:` gating; the
  primary/secondary interleaving would be even harder to read. Strictly worse.
- **Folding the managed-cluster section into the same loop.** Rejected: it has a
  different module contract (`scope: managed_cluster`, its own `result_id`,
  `failure_message`), a different iteration axis (clusters, not hub roles), and
  Python keeps managed-cluster validation as a separate scope too. It stays a
  separate, unchanged section.

## Primary/secondary asymmetry table

| Asymmetry | Today | After refactor |
| --- | --- | --- |
| Restore-only skips primary validation entirely | `when: not (…restore_only…)` on every primary task | `enabled: not restore_only` on the primary table entry; loop-level `when: rbac_hub.enabled` |
| Decommission permissions primary-only | inline `include_decommission:` expression on primary calls; literal `false` + comment on secondary | table data: primary expression, secondary `false` with the same comment |
| Old-hub finalization primary-only | `_rbac_include_old_hub_finalization_primary` computed in main file, passed on primary calls only; secondary omits the arg | same fact computed in main file (unchanged); table data primary `{{ _rbac_include_old_hub_finalization_primary \| default(false) }}`, secondary `false` (module default — identical) |
| SSAR target vars | secondary passes `_ssar_target_*`; primary relies on defaults (which are the primary values) | both hubs pass explicit `_ssar_target_*` from table data; rendered values unchanged |
| Secondary error-count failure message | lives in `acm_rbac_validate` (`hub:` param drives message/result shape), not the task file | unchanged — module behavior, `hub: "{{ rbac_hub.hub }}"` |
| Install-type override (Python `secondary_argocd_install_type`) | no Ansible analog: each hub detects its own install type independently | unchanged — per-hub detection inside the shared block |
| Observability skip state | computed once in main file, consumed by both hub blocks | unchanged — computed once, consumed via the global fact inside the shared file |

## Fail-closed preservation table

| Fail-closed path | Today | After refactor |
| --- | --- | --- |
| 401/unauthorized during CRD discovery | dedicated `ansible.builtin.fail` per hub, gated on `.msg` substring match | same task once in the hub file, message hub-templated, gating expression unchanged; runs for each enabled hub |
| Unexpected (non-403) CRD discovery error | dedicated fail per hub, excludes only 403/forbidden, gates on `.msg` presence | identical, hub-templated |
| 403/forbidden during CRD discovery | not fatal — install type records `unknown` (wider permission set), deferred to RBAC validation | identical (record task's `msg`-nonempty branch) |
| SSAR request failure / missing status | `run_ssar.yml` counts it as a denied permission (fail-closed) | `run_ssar.yml` untouched |
| Denied permissions | summarize module returns `passed: false`, critical failures merge into the preflight accumulator; preflight fails | untouched — merge task unchanged, module unchanged |
| Unknown/unreadable authorization state | never silently passes (denied-collection default branch in `run_ssar.yml`) | untouched |

## Relationship to Python H1

| Python H1 (`lib/rbac_validator.py`) | PR 39 Ansible mirror |
| --- | --- |
| `hub_validations` tuple table with explicit per-hub data | `_rbac_hub_validations` list-of-dicts fact |
| `for … in hub_validations:` loop | `include_tasks: validate_rbac_hub.yml` with `loop:` |
| `_validate_hub(...)` shared body | `validate_rbac_hub.yml` shared task file |
| primary-`None` skip + log | primary `enabled: false` in restore-only + loop `when:` skip (visible in output) |
| asymmetries as table data (decommission, old-hub finalization, install type, error-count message) | asymmetries as table data (decommission, old-hub finalization, kubeconfig/context); error-count message stays module-side (`hub:` param) |

## Test plan

Updated (parser tests follow the structure; assertions strengthened, not weakened):

- `test_ansible_resilience_contracts.py::test_preflight_validate_rbac_fails_closed_on_argocd_401`
  — parse `validate_rbac_hub.yml` for the fail tasks; keep every existing
  gating assertion (`.msg` not `.failed`, 403-only exclusion, 401-before-
  unexpected ordering); add assertions that the shared file is driven by a loop
  in `validate_rbac.yml` whose table contains both `hub: primary` and
  `hub: secondary` entries, and that fail messages template the hub role.
- `test_ansible_resilience_contracts.py::test_preflight_validate_rbac_detects_argocd_install_type`
  — CRD-probe assertions move to the hub file; mode-derivation assertions
  (`'check'`, `skip_gitops_check`) stay against the main file; the
  `argocd_install_type: unknown` hardcoding ban now covers both files.
- `test_ansible_resilience_contracts.py::test_collection_rbac_validation_runs_ssars_in_dry_run`
  — add the hub file to the no-dry-run-gate list; `run_ssar` presence asserted
  in the hub file (preflight) and unchanged for decommission.
- `test_preflight_parity.py::test_preflight_rbac_skips_observability_permissions_when_observability_absent`
  — skip-state computation asserted in the main file (unchanged);
  `skip_observability: "{{ _rbac_skip_observability }}"` consumption asserted in
  the hub file.

New contract tests (in `test_preflight_parity.py`):

1. One shared hub-parameterized path: `validate_rbac.yml` contains exactly one
   `include_tasks: validate_rbac_hub.yml` task, with `loop_control.loop_var:
   rbac_hub`, a per-item `enabled` gate, and a table whose entries are
   `primary` and `secondary` in that order (primary gated on restore-only,
   secondary unconditional) — mirroring the Python H1 hub table.
2. Registered-fact preservation: the hub file re-publishes
   `_rbac_argocd_install_type_`, `_rbac_expanded_`, `_rbac_denied_permissions_`
   suffixed facts and `acm_<hub>_rbac_validation` via `{{ rbac_hub.hub }}`
   -templated keys, and the main-file merge task still consumes
   `acm_primary_rbac_validation` / `acm_secondary_rbac_validation` /
   `acm_managed_cluster_rbac_validation` by their exact names.
3. Asymmetry pinning: primary table entry carries the decommission expression
   and `_rbac_include_old_hub_finalization_primary`; the secondary entry pins
   `include_decommission: false` and `include_old_hub_finalization: false`;
   old-hub-finalization determination stays primary-only in the main file.
4. Managed-cluster separation: `validate_managed_cluster_rbac.yml` is still a
   separate include, not routed through `validate_rbac_hub.yml`.

Behavioral guard (already exists, no change needed):
`tests/integration/test_preflight_role.py::test_preflight_rbac_failure_still_reports_backup_findings`
runs the real role via `ansible-playbook` and asserts both
`preflight-rbac-primary` and `preflight-rbac-secondary` fail with unreachable
hubs — this executes the new loop, the templated `set_fact` keys, and the merge
task end-to-end.

## Verification plan

- `git diff --check`
- `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_parity.py ansible_collections/tomazb/acm_switchover/tests/unit/test_ansible_resilience_contracts.py -q`
- `python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py -q`
- `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q`
- Parity-sensitive root RBAC suites:
  `python -m pytest tests/test_rbac_validator.py tests/test_check_rbac.py tests/test_rbac_collection_parity.py tests/test_rbac_integration.py tests/release/checks/test_rbac_certification.py -q`
- Full gate: `./run_tests.sh`

## Rollback plan

The change is two task files plus test updates in one commit series on
`refactor/thermos-39-ansible-rbac-dedup`; no data migrations, no manifest,
module, or Python changes. Rollback = revert the PR merge commit; the previous
inline primary/secondary blocks return untouched. No state files, checkpoints,
or operator artifacts change shape, so mixed-version rollback is a no-op.
