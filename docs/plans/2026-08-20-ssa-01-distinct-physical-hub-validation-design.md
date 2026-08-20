# SSA-01 distinct physical hub validation design

**Status:** Written design awaiting operator approval

**Date:** 2026-08-20

**Governing issue:** GitHub #267, `SSA-01`

**Findings:** `SSA-A2`, `SSA-P2`

**Primary branch:** `ansible`

**Bound base:** `origin/ansible` at `7a29974c2e914af30b1d9a02ee194295bdfe0722`

**Design branch:** `ssa-01-distinct-hub-design`

## 1. Purpose

SSA-01 adds a fail-closed invariant to every normal two-hub switchover in both production form factors:

```text
primary physical Kubernetes cluster
!=
secondary physical Kubernetes cluster
```

The physical identity is the trimmed, non-empty UID of the live `kube-system` Namespace. Textually different contexts or kubeconfig paths are not proof of distinct clusters because multiple contexts can address the same Kubernetes API.

The cross-role predicate is additive to the existing per-role stored-versus-current checkpoint or resume identity binding. It does not replace, relax, or bypass that binding. The invariant must be established before any Kubernetes mutation-capable phase and before mutation-capable recovery becomes eligible.

Python and the Ansible Collection implement the rule independently. They share operator behavior and static contracts, not production runtime code.

## 2. Authorities and base binding

The design follows, in order:

1. the repository-wide policy in `AGENTS.md`;
2. issue #267 and the approved SSA-01 design review;
3. architecture, parity, coexistence, compatibility, RBAC, and testing authorities;
4. source and tests at the bound base.

The start gate for this artifact established:

- repository: `tomazb/rh-acm-switchover`;
- fetched `origin/ansible`: `7a29974c2e914af30b1d9a02ee194295bdfe0722`;
- design-branch HEAD: `7a29974c2e914af30b1d9a02ee194295bdfe0722`;
- merge base with `origin/ansible`: `7a29974c2e914af30b1d9a02ee194295bdfe0722`;
- clean isolated worktree: `.claude/worktrees/ssa-01-distinct-hub-design`;
- issue #267: open and limited to reviewed design and implementation planning;
- declared scope: `SSA-01 / issue #267 / SSA-A2 + SSA-P2`;
- pre-artifact staged, unstaged, base-relative, and protected-file diffs: empty.

The fetched base is the same SHA used for the approved architecture, so no rebase delta invalidates the design.

The compatibility authority defines these repository-tested endpoints:

- `ansible-core 2.16.*` with Python 3.11;
- `ansible-core 2.21.*` with Python 3.12.

Versions 2.17 through 2.20 are in the supported upstream-compatible interval, but the repository does not claim them as separate endpoint test lanes. The design makes no unsupported claim about repository-tested AAP combinations.

## 3. Scope and non-goals

### 3.1 In scope

- an early same-context guard for normal two-hub inputs;
- fresh, role-specific physical identity establishment;
- strict shape and value validation of required UID evidence;
- primary-versus-secondary UID comparison;
- sanitized refusal paths that do not emit raw identity-establishment diagnostics;
- preservation of existing stored-versus-current identity validation;
- an unshadowable Collection provenance boundary for safety evidence;
- a structural Collection boundary that separates pre-barrier refusal from mutation-capable recovery;
- parity, regression, adversarial, and zero-mutation tests;
- later documentation of the new operator-visible safety rule.

### 3.2 Explicit exclusions

The following are not part of SSA-01:

- restore-only primary-versus-secondary distinction, because restore-only is secondary-only;
- standalone or single-hub decommission distinction or target hardening, which belongs to SSA-02;
- setup;
- standalone Argo CD resume, except for preserving existing identity regression coverage;
- checkpoint schema redesign;
- correction of `reset_from` behavior, which remains owned by `R3-06`;
- general Argo CD transaction or recovery redesign;
- klusterlet work;
- backup journaling;
- release or lab-controller behavior changes;
- production import or reuse of `tests/release/lab_controller/` code;
- released-version changes or tags;
- intentional Python/Collection parity divergence;
- broad logging cleanup;
- new Kubernetes API permissions;
- live validation or certification;
- changes to `docs/ACM_SWITCHOVER_RUNBOOK.md` or `.claude/skills/**`.

## 4. Verified current behavior

### 4.1 Python CLI

`acm_switchover.py::validate_args` delegates CLI input checks to `lib.validation.InputValidator`. The validator verifies each context independently but does not compare primary and secondary context names.

For modes that bind state, `acm_switchover.py::_prepare_runtime` creates the state manager, binds configured contexts, initializes Kubernetes clients, and enters `lib.cli_outcomes.run_operation_mode`. That outcome path invokes `acm_switchover.py::_bind_runtime_hub_identities` before `_execute_operation`.

`_bind_runtime_hub_identities` currently obtains identities through `_collect_hub_identities` and passes them directly to `lib.utils.StateManager.ensure_hub_identities`. `StateManager` validates and persists each role independently and rejects stored-versus-current UID drift. It does not compare primary with secondary. Different contexts that resolve to the same cluster can therefore pass.

`KubeClient.get_cluster_identity` obtains `metadata.uid` from the `kube-system` Namespace. The existing generic namespace path is not suitable for the new sanitized refusal contract:

- `KubeClient.__init__` logs raw configuration exceptions before re-raising;
- the generic decorated API path can log an `ApiException` and response details;
- the normal retry decorator can log the exception string before each retry;
- translating the exception only after those lower-level emissions is too late.

`run_operation_mode` handles `StateIdentityMismatch` through an existing safe, pre-dispatch outcome. When binding fails, `_execute_operation` is not called. The normal runner otherwise reaches preflight and then `primary_prep`, the first mutation-capable phase.

Context and controller-state setup can write non-Kubernetes INIT, context, or report state before identity refusal in existing non-dry-run flows. SSA-01 does not redesign that controller-state ordering. The new ordering prevents equal physical UIDs from being persisted, prevents a mutation-capable phase from completing, and preserves dry-run snapshot restoration.

### 4.2 Ansible Collection

`roles/preflight/tasks/main.yml` currently validates inputs, includes `discover_hub_identities.yml`, and then enters the preflight checkpoint. The discovery task invokes `kubernetes.core.k8s_info`, registers Namespace results, and writes `acm_switchover_hub_identities` with `set_fact`. It checks presence but does not compare the roles.

Those variables are not a trusted provenance boundary. On both supported Ansible endpoint lanes, extra vars win when a later expression resolves a registered value, a `set_fact`, role/default/include/block/task variables, underscore-prefixed variables, and values exposed through action-plugin `task_vars`. A leading underscore is only a naming convention.

`plugins/action/checkpoint_phase.py` currently builds expected operation identity from caller-visible `acm_switchover_hubs` and `acm_switchover_hub_identities`. `plugins/module_utils/checkpoint.py::build_operation_identity` prefers `hubs.<role>.cluster_uid` over `hub_identities.<role>.cluster_uid`. Because the input module permits additional hub mapping keys, a caller can inject `acm_switchover_hubs.<role>.cluster_uid` and displace a separately supplied discovered UID at checkpoint identity construction.

`playbooks/switchover.yml` currently encloses preflight and all later phases in one block whose rescue can resume Argo CD and reset the `primary_prep` checkpoint. A preflight identity refusal can therefore enter mutation-capable recovery. A caller-shadowable Boolean cannot safely distinguish pre-barrier from post-barrier failures.

The first normal mutation-capable role is `primary_prep`; it can pause Argo CD and alter backup, auto-import, and deployment resources. Reporting in the playbook `always` path writes controller-side output and is not the Kubernetes mutation barrier.

### 4.3 Release-controller donor boundary

The release lab controller proves the reusable concept that duplicate physical identities must fail closed. Its enrollment, profile binding, multi-step discovery, fingerprint, and mutation-authorization architecture is not reusable by either production form factor. Production code must not import from `tests/release/`, and SSA-01 does not edit release-controller code.

## 5. Design alternatives

### 5.1 Python alternatives

#### Selected: pure cross-role validator invoked by the current binder

Keep discovery orchestration in `_bind_runtime_hub_identities`, add a pure validator in `lib/validation.py`, and call the existing `StateManager.ensure_hub_identities` only after the cross-role predicate passes.

This is fail closed, localized, directly testable, additive to resume binding, and independent of checkpoint persistence. It introduces no new subsystem or RBAC need.

#### Rejected: put distinction inside `StateManager`

`StateManager` owns durable per-role state and stored-versus-current matching. Giving it a cross-role live-input rule would mix input safety with persistence ownership, complicate restore-only behavior, and make the rule appear checkpoint-dependent.

#### Rejected: create a new validation subsystem

A new subsystem would duplicate the existing input, bootstrap, and binding owners without improving freshness or testability. It would increase parity and maintenance risk.

### 5.2 Collection alternatives

#### Rejected alone: dedicated identity action or module

A dedicated component could own live reads and comparison, but a returned registered result would again be caller-shadowable before checkpoint validation. Moving both discovery and checkpoint validation into a separate component would duplicate existing `checkpoint_phase` ownership.

#### Selected portion: extend `checkpoint_phase` with an action-local identity barrier

The existing action already owns checkpoint operation identity validation. Its identity-barrier path will keep fresh module results and validated UIDs in local Python variables, apply the distinctness rule, construct trusted expected operation identity, and perform the existing stored-versus-current comparison before returning.

This works when checkpointing is enabled or disabled. It does not change checkpoint schema, and its internal live results are not Ansible variables that extra vars can replace.

#### Selected together: structural pre/post block and rescue split

The switchover playbook will place identity/checkpoint establishment outside the nested block that owns mutation-capable recovery. A pre-barrier task failure cannot branch into that rescue. Later failures remain inside it and retain current recovery semantics.

#### Rejected: private-looking facts or a verified Boolean

Names such as `_acm_switchover_verified_hub_identities` and `acm_switchover_distinct_hubs_verified` remain ordinary Ansible variables. Extra vars can shadow them, so they cannot establish evidence provenance or recovery eligibility.

### 5.3 Trade-off summary

The selected combination is the smallest architecture-compatible design that satisfies all safety consumers. It adds bounded action complexity and a preflight task split, but avoids a new module, avoids duplicated checkpoint validation, works with disabled checkpoints and native check mode, makes adversarial testing direct, and preserves post-barrier recovery. It reuses the existing Namespace `get`, so RBAC is unchanged.

## 6. Operator-facing refusal contract

The normal two-hub same-context message is:

```text
Primary and secondary Kubernetes context names must differ for a normal two-hub switchover.
```

The equal-physical-UID message is:

```text
Primary and secondary hubs resolve to the same physical Kubernetes cluster. Refusing the normal two-hub switchover.
```

The role-specific evidence messages are:

```text
Unable to verify the primary hub physical identity from the live kube-system Namespace UID. Refusing the normal two-hub switchover.
```

```text
Unable to verify the secondary hub physical identity from the live kube-system Namespace UID. Refusing the normal two-hub switchover.
```

The new refusal paths do not include kubeconfig paths, credentials, tokens, Namespace UID values, Kubernetes API response bodies, raw exception text, or primary/secondary context values. Existing ordinary informational logs that name configured contexts are outside SSA-01 and remain unchanged.

The same messages and role names are pinned through parity or static-contract fixtures without importing production runtime code across form factors.

## 7. Python design

### 7.1 Applicability and input guard

`lib/validation.py::InputValidator` will reject identical context names when both roles are required for a normal two-hub switchover. The static check occurs before live UID lookup and uses the approved same-context message.

The check does not apply to restore-only, valid single-hub decommission, setup, or standalone Argo CD resume. The trusted physical predicate is not bypassed by `--force` or checkpoint reset controls.

### 7.2 Role-scoped sanitized client construction

`lib/kube_client.py::KubeClient.__init__` will gain a narrowly scoped option that suppresses its raw configuration-exception log for SSA-01 identity-establishment construction. The default behavior remains unchanged for other callers.

`lib/runtime_bootstrap.py::initialize_clients` will construct the primary and secondary clients separately. In the sanitized mode, it translates construction failure to the stable message for the affected logical role and suppresses the original exception as a displayed cause.

`acm_switchover.py::_prepare_runtime` will enable that mode only for normal two-hub flows that require the SSA-01 identity binding. It will handle the already-sanitized identity-establishment failure before its generic initialization error logger can emit raw exception text. Restore-only construction and operator behavior remain unchanged by SSA-01.

### 7.3 Silent fresh UID read

`lib/kube_client.py::KubeClient.get_cluster_identity` will use a private identity-specific Namespace read instead of the generic logged `get_namespace` path. The private path will:

- call the same `CoreV1Api.read_namespace("kube-system")` request;
- use the existing request timeout;
- retain the current retry predicate;
- retain the five-attempt limit and exponential wait;
- use `retry_api_call_advisory`, or the equivalent existing non-logging retry owner;
- never log an exception string, response body, request path, or credential-bearing diagnostic during retries.

`lib.runtime_bootstrap.collect_hub_identities` owns role-aware translation after this silent read. It raises the stable logical-role failure without exposing the original exception, using suppressed exception chaining where needed. The final outcome layer therefore receives only sanitized text; no lower layer has already emitted raw content.

Fresh reads are mandatory for normal validate-only, dry-run, and execute modes. Python has no production pre-seeded identity contract.

### 7.4 Pure cross-role validator

`lib.validation.validate_distinct_hub_identities` will be a pure helper over the collected role mapping. Its contract is:

1. the required role identity is mapping-shaped;
2. `cluster_uid` is a string;
3. its trimmed value is non-empty;
4. the two trimmed UIDs differ by exact, case-sensitive comparison;
5. malformed or missing evidence fails with the affected role message;
6. equal evidence fails with the equal-physical-cluster message.

The validator neither discovers resources nor reads or writes checkpoint state.

### 7.5 Binding and mutation barrier

The normal path becomes:

```text
validate_args / InputValidator
→ _prepare_runtime
→ initialize_clients in role-scoped sanitized mode
→ run_operation_mode
→ _bind_runtime_hub_identities
→ _collect_hub_identities
→ validate_distinct_hub_identities
→ StateManager.ensure_hub_identities
→ [BARRIER: _execute_operation has not been called]
→ _execute_operation
→ operation_runners.execute_operation
→ preflight
→ primary_prep
→ first Kubernetes mutation
```

`StateManager.ensure_hub_identities` stays unchanged as the per-role resume-binding owner. The new cross-role rule is checked first. Equal UIDs are not persisted.

A refusal is translated to `StateIdentityMismatch` or the equivalent existing safe identity outcome before dispatch. Consequently, no workflow handler, mutation helper, or mutation-capable phase runs or completes. Existing controller INIT/context/report behavior remains, and dry-run snapshot restoration remains intact.

## 8. Collection trust model

### 8.1 Evidence classes

The Collection distinguishes five classes explicitly:

```text
USER CONFIGURATION
    context, kubeconfig, execution mode, operation settings,
    checkpoint configuration, feature settings, explicit non-live test override

DISCOVERED SAFETY EVIDENCE
    actual kube-system UID module responses, validated UID values,
    primary-versus-secondary distinctness, expected current checkpoint hub identity

PERSISTED SAFETY EVIDENCE
    operation_identity loaded from checkpoint storage

POST-BARRIER CONTROL AND COMPATIBILITY DATA
    checkpoint skipped_phase and operational facts returned through _checkpoint_enter

NON-AUTHORITATIVE IDENTITY OUTPUT
    Ansible identity variables or facts returned for reporting or compatibility
```

Operator configuration is intentionally accepted through normal Ansible variables. Discovered evidence is not. Registered variables, facts, role variables, task variables, block variables, include variables, underscore names, and action `task_vars` do not preserve discovery provenance against extra vars.

The approved precedence experiments used malicious sentinels on both endpoint lanes and matched Ansible's documented extra-variable precedence:

| Candidate channel | `ansible-core 2.16.*` | `ansible-core 2.21.*` | Safety classification |
| --- | --- | --- | --- |
| registered result resolved later in Jinja | extra-var sentinel wins | extra-var sentinel wins | untrusted |
| `set_fact` resolved later in Jinja | extra-var sentinel wins | extra-var sentinel wins | untrusted |
| role/default/include/block/task variable | extra-var sentinel wins | extra-var sentinel wins | untrusted |
| underscore-prefixed variable | extra-var sentinel wins | extra-var sentinel wins | untrusted |
| value visible through action `task_vars` | winning extra-var sentinel is visible | winning extra-var sentinel is visible | untrusted |
| Python local holding `_execute_module` result | no ordinary extra-var address or substitution channel | no ordinary extra-var address or substitution channel | selected trusted evidence boundary |

The two supported lanes behaved identically for this threat model. No trust claim relies on variable naming.

`ansible_check_mode` is an Ansible magic/internal state signal, not an ordinary extra-var trust surface. The action derives native check state from `self._play_context.check_mode`, or the supported action-plugin runtime equivalent. A caller variable with the same spelling is not authoritative.

### 8.2 Literal action contract

`plugins/action/checkpoint_phase.py` gains an explicit action-only identity-barrier path. The shipped task invokes it with literal control arguments equivalent to:

```yaml
tomazb.acm_switchover.checkpoint_phase:
  identity_barrier: true
  phase: preflight
  status: enter
  checkpoint: "{{ acm_switchover_execution.checkpoint | default({}) }}"
  hubs: "{{ acm_switchover_hubs }}"
  operation: "{{ acm_switchover_operation }}"
  execution: "{{ acm_switchover_execution }}"
  test_overrides: "{{ acm_switchover_test_overrides | default({}) }}"
  collection_version: "{{ acm_switchover_collection_version | default('') }}"
register: _checkpoint_enter
```

`identity_barrier: true`, `phase: preflight`, and `status: enter` are literal task arguments, not variable-derived trust flags. The action rejects an identity-barrier request with another phase/status combination.

The nested input mappings remain operator configuration. The action allowlists the fields it needs and never treats identity-shaped values in those mappings as evidence. Task failure itself controls Ansible block flow; a caller cannot turn a failed action into a successful task by shadowing the register name.

The action preserves the existing `_checkpoint_enter` result contract. After the action completes successfully, `post_identity.yml` and restore-only may continue to read its `skipped_phase` and operational `facts` for their existing compatibility and control-flow purposes. Those fields do not establish physical identity, distinctness, execute freshness, or stored-versus-current UID validity. Those identity decisions finish inside the action before `_checkpoint_enter` exists as an Ansible variable.

The identity-barrier action runs whenever the preflight entry is invoked, regardless of checkpoint persistence enablement. It is not skipped based on `acm_input_validation`, `_checkpoint_enter`, an identity fact, or a caller-provided verified Boolean. Input validation remains the UX owner, while the action defensively enforces the safety prerequisites it consumes.

### 8.3 Static and defensive same-context checks

`plugins/modules/acm_input_validate.py` adds the same user-facing equal-context refusal for normal two-hub input. The trusted action independently compares the validated configured contexts before discovery. This small duplication is defense in depth between the input UX owner and the runtime safety owner, not a second validation subsystem.

Restore-only requires and validates only the secondary context. Decommission does not enter this two-hub action path.

### 8.4 Fresh action-local evidence

For `acm_switchover_execution.mode == "execute"`, the action invokes `kubernetes.core.k8s_info` itself for each required role and reads the `kube-system` Namespace. `_execute_module` results remain Python locals inside that action invocation until every safety decision completes.

The action does not register the raw results for later safety use, write them with `set_fact`, or read `acm_switchover_hub_identities`. Native `--check` changes mutation semantics, not execute-mode freshness: execute plus check still performs both fresh GETs and uses their results.

For validate and dry-run, fresh reads remain the default. The explicit `acm_switchover_test_overrides.non_live_hub_identities` contract may provide non-live evidence only in those two modes. The action ignores it in execute, including execute plus native check. Ordinary public identity facts never qualify as a test override or safety input.

The action handles a failed module result locally and returns only the stable role-specific refusal. It does not return or display raw module failure data, API bodies, paths, exceptions, UID values, or credentials.

### 8.5 Evidence validation

For each required role, the action fails closed unless it establishes exactly one usable Namespace identity. It rejects:

- module failure or missing result;
- zero Namespace resources;
- multiple resources where the module result makes selection ambiguous;
- missing or non-mapping metadata;
- missing UID;
- non-string UID;
- empty or whitespace-only UID;
- any otherwise non-usable result shape.

The action trims a valid UID and retains it only in a local Python variable. A normal two-hub action compares the two exact trimmed strings and refuses equality. `force`, checkpoint reset, and public facts cannot bypass the comparison.

### 8.6 Trusted UID-to-checkpoint handoff

The action must not pass caller-controlled `acm_switchover_hubs` directly to `build_operation_identity`, because the helper currently prefers `hubs.<role>.cluster_uid` over the separately supplied identity mapping.

After validating and comparing the action-local UIDs, the action constructs two new allowlisted Python-local mappings from scratch:

```python
sanitized_local_hubs = {
    "primary": {"context": validated_primary_context},
    "secondary": {"context": validated_secondary_context},
}

trusted_local_hub_identities = {
    "primary": {"cluster_uid": established_primary_uid},
    "secondary": {"cluster_uid": established_secondary_uid},
}
```

These are illustrative local names, not Ansible variables. `sanitized_local_hubs` contains only allowlisted operation-identity fields sourced intentionally from operator configuration. It contains no `cluster_uid`, kubeconfig, or arbitrary nested keys; it is neither a shallow copy nor a mutation of the caller mapping. `trusted_local_hub_identities` contains only the UIDs just established and compared by the action and is never reconstructed from `task_vars`.

The action calls the unchanged helper as follows in substance:

```python
build_operation_identity(
    hubs=sanitized_local_hubs,
    operation=validated_operator_operation,
    collection_version=collection_version,
    hub_identities=trusted_local_hub_identities,
)
```

The provenance chain is therefore:

```text
fresh or authorized non-live UID evidence
→ validate each UID
→ compare primary != secondary
→ build allowlisted local hubs without cluster_uid
→ build local hub_identities from the same trusted UIDs
→ build expected_operation_identity
→ current checkpoint identity handling:
  validate_operation_identity when no explicit reset applies,
  or the existing reset/reset_from branch
→ structural barrier
```

The same local UID values feed the cross-role comparison and the expected identity passed into the current checkpoint algorithm. When no explicit reset applies, they therefore feed stored-versus-current checkpoint validation. Under the existing `reset` or `reset_from` branch, they feed that branch's current behavior. Injected `acm_switchover_hubs.primary.cluster_uid` or `.secondary.cluster_uid` is ignored, and no split-provenance path is introduced by SSA-01. General `build_operation_identity` precedence and checkpoint schema stay unchanged.

For restore-only, the local mappings contain only the validated secondary context and the established secondary UID. The action neither reads nor synthesizes primary identity and does not perform a cross-role comparison.

### 8.7 Checkpoint-enabled behavior

The initial identity barrier always establishes current identity and, for normal two-hub flow, performs distinctness before checkpoint state can be initialized or transitioned. It then builds expected operation identity from the trusted locals and loads checkpoint state. When no explicit reset applies, it invokes the existing stored-versus-current validation. When `reset` or `reset_from` applies, it follows the current explicit-reset branch without changing that branch's semantics.

Existing initialization, legacy backfill, reset, and resume rules remain in force. The action returns skipped or resume semantics only after the identity barrier and the applicable current checkpoint branch succeed. Even if the checkpoint says preflight is complete, every new playbook invocation reruns the identity barrier before honoring that skipped state.

The barrier may initialize or backfill operation identity only where current checkpoint semantics permit. Reset controls do not bypass fresh physical discovery or the distinct-hub predicate. The barrier then applies the current checkpoint algorithm, including its existing explicit-reset branches.

SSA-01 does not change `reset_from` identity semantics. Current `checkpoint_phase._build_reset_from_checkpoint()` replaces `operation_identity` with `expected_operation_identity`; `R3-06` owns correction of that behavior. When the initial identity-barrier entry processes `reset_from`, its expected identity comes from the trusted action-local UID handoff described in section 8.6. A later `reset_from` transition keeps the repository's current replacement behavior under SSA-01. The design does not claim that such a transition preserves the stored identity or revalidates it under corrected `R3-06` semantics.

Ordinary later `checkpoint_phase` transitions, meaning transitions outside the existing explicit `reset` and `reset_from` branches, stop rebuilding physical identity from `acm_switchover_hub_identities` or `acm_switchover_hubs`. They load and carry the operation identity established by the initial barrier. Outside those existing explicit-reset branches, an execute-mode later transition with checkpoint persistence enabled and no established operation identity fails closed rather than initializing physical identity from task variables.

In native check mode, the initial barrier reads and validates existing checkpoint identity but performs no initialization, backfill, transition, or persistence. Later check-mode transitions are non-mutating and do not acquire identity from caller variables.

### 8.8 Checkpoint-disabled behavior

The identity-barrier action still performs context defense, required UID reads or authorized non-live lookup, evidence validation, and normal-flow distinctness. It performs no checkpoint load or write and does not make checkpoint enablement a safety prerequisite.

Later checkpoint transition tasks remain disabled under existing guards. The structural barrier, rather than checkpoint state, proves that the current invocation passed identity validation.

### 8.9 Preflight task split

The preflight role is split into:

- `roles/preflight/tasks/identity_barrier.yml`;
- `roles/preflight/tasks/post_identity.yml`;
- `roles/preflight/tasks/main.yml`, which includes those files in that order.

`identity_barrier.yml` initializes the current preflight result contract, runs normal input validation for operator feedback, and invokes the literal identity-barrier action unconditionally for the applicable flow. It registers the successful action result as `_checkpoint_enter`, preserving the current `skipped_phase` and operational `facts` contract. A caller-shadowed `acm_input_validation` or `_checkpoint_enter` value cannot suppress the action. Action failure ends the pre-barrier task path.

`post_identity.yml` owns the existing skipped-preflight operational-data restoration from `_checkpoint_enter`, remaining preflight checks, reporting contribution, failure publication, and successful checkpoint status behavior after trusted identity validation. Its use of `skipped_phase` and operational facts is an existing post-barrier control contract, not a physical-identity evidence path.

`main.yml` composes both entries so `playbooks/preflight.yml` retains complete standalone preflight behavior. The split moves existing tasks; it does not duplicate the checks.

`roles/preflight/tasks/discover_hub_identities.yml` is removed. Its register-and-`set_fact` path is no longer a safety owner, and preserving it would create two competing discovery paths. To preserve the current reporting/compatibility surface, the action returns a sanitized identity summary only after all safety decisions, and `identity_barrier.yml` exposes that summary as `acm_switchover_hub_identities`. The returned identity summary and public identity fact are explicitly non-authoritative and are never consumed as physical-identity evidence.

`roles/preflight/tasks/write_report.yml` remains a reporting owner, not a security owner. Report correctness does not decide freshness, distinctness, checkpoint validation, or recovery eligibility.

### 8.10 Structural recovery barrier

`playbooks/switchover.yml` adopts the following valid nested block shape. The existing recovery arguments, feature conditions, ignore-error behavior, and report contract are retained exactly inside the new nesting:

```yaml
tasks:
  - name: Run switchover phases
    block:
      - name: Establish trusted identity and checkpoint barrier
        ansible.builtin.include_role:
          name: tomazb.acm_switchover.preflight
          tasks_from: identity_barrier

      - name: Run post-barrier switchover phases
        block:
          - name: Run post-identity preflight validation
            ansible.builtin.include_role:
              name: tomazb.acm_switchover.preflight
              tasks_from: post_identity

          - name: Stop after preflight when mode is validate
            ansible.builtin.meta: end_play
            when: acm_switchover_execution.mode | default('') == 'validate'

          - name: Run primary prep
            ansible.builtin.include_role:
              name: tomazb.acm_switchover.primary_prep

          - name: Run activation
            ansible.builtin.include_role:
              name: tomazb.acm_switchover.activation

          - name: Run post activation verification
            ansible.builtin.include_role:
              name: tomazb.acm_switchover.post_activation

          - name: Run finalization
            ansible.builtin.include_role:
              name: tomazb.acm_switchover.finalization
        rescue:
          - name: Attempt Argo CD resume on secondary hub after failure
            ansible.builtin.include_role:
              name: tomazb.acm_switchover.argocd_manage
            vars:
              acm_switchover_argocd_mode_override: resume
              _argocd_discover_hub: secondary
            when:
              - acm_switchover_features.argocd.manage | default(false)
              - acm_switchover_features.argocd.resume_on_failure | default(false)
            ignore_errors: true  # noqa: ignore-errors

          - name: Attempt Argo CD resume on primary hub after failure
            ansible.builtin.include_role:
              name: tomazb.acm_switchover.argocd_manage
            vars:
              acm_switchover_argocd_mode_override: resume
              _argocd_discover_hub: primary
            when:
              - acm_switchover_features.argocd.manage | default(false)
              - acm_switchover_features.argocd.resume_on_failure | default(false)
              - acm_switchover_hubs.primary is defined
              - (acm_switchover_hubs.primary.kubeconfig | default('')) | length > 0
              - (acm_switchover_hubs.primary.context | default('')) | length > 0
            ignore_errors: true  # noqa: ignore-errors

          - name: Reset primary prep checkpoint after Argo CD resume on failure
            tomazb.acm_switchover.checkpoint_phase:
              phase: primary_prep
              checkpoint: "{{ acm_switchover_execution.checkpoint | combine({'reset_from': 'primary_prep'}) }}"
              status: reset
              operational_data:
                argocd_run_id: "{{ acm_switchover_argocd.run_id | default('') }}"
                argocd_discovery_namespaces: "{{ acm_switchover_argocd_discovery_namespaces | default({}) }}"
            when:
              - acm_switchover_features.argocd.manage | default(false)
              - acm_switchover_features.argocd.resume_on_failure | default(false)
              - acm_switchover_execution.checkpoint.enabled | default(false)
            ignore_errors: true  # noqa: ignore-errors

          - name: Re-raise original switchover failure
            ansible.builtin.fail:
              msg: "{{ ansible_failed_result.msg | default('Switchover failed') }}"
    always:
      - name: Build switchover report contract
        ansible.builtin.set_fact:
          acm_switchover_report:
            schema_version: "1.0"
            source: tomazb.acm_switchover
            argocd:
              run_id: "{{ acm_switchover_argocd.run_id | default(acm_switchover_execution.run_id | default('')) }}"
              summary: >-
                {%- set hubs = acm_switchover_argocd_summary_by_hub | default({}) -%}
                {%- if hubs | length > 0 -%}
                {%- set ns = namespace(paused=0, restored=0) -%}
                {%- for _hub_name, hub_summary in hubs.items() -%}
                {%- set ns.paused = ns.paused + (hub_summary.get('paused', 0) | int) -%}
                {%- set ns.restored = ns.restored + (hub_summary.get('restored', 0) | int) -%}
                {%- endfor -%}
                {{ {'paused': ns.paused, 'restored': ns.restored} }}
                {%- else -%}
                {{ acm_switchover_argocd_summary | default({}) }}
                {%- endif -%}
            phases: >-
              {{
                {}
                | combine({'primary_prep': acm_switchover_primary_prep_result} if acm_switchover_primary_prep_result is defined else {})
                | combine({'activation': acm_switchover_activation_result} if acm_switchover_activation_result is defined else {})
                | combine({'post_activation': acm_switchover_post_activation_result} if acm_switchover_post_activation_result is defined else {})
                | combine({'finalization': acm_switchover_finalization_result} if acm_switchover_finalization_result is defined else {})
              }}

      - name: Write switchover report artifact
        tomazb.acm_switchover.acm_report_artifact:
          path: "{{ (acm_switchover_execution.report_dir | default('./artifacts')) ~ '/switchover-report.json' }}"
          report: "{{ acm_switchover_report }}"
```

Implementation moves the current concrete rescue and report tasks into this nesting without changing their behavior.

The identity-barrier include is outside the nested rescue. Same context, unreadable or malformed evidence, missing UID, equal UID, and stored-versus-current mismatch therefore cannot invoke `primary_prep`, either Argo CD resume, checkpoint reset, or another Kubernetes mutation operation. Extra vars cannot move a failed task into a rescue block that does not enclose it.

After the identity and checkpoint barrier succeeds, the remaining preflight and mutation phases run inside the nested block. A controlled later failure reaches the same feature-gated Argo CD and checkpoint-reset recovery behavior as today. The design does not suppress valid post-barrier recovery.

The outer `always` reporting path may still write non-Kubernetes controller output after a refusal, preserving current reporting semantics.

### 8.11 Restore-only and decommission routing

Restore-only continues to use complete preflight composition through `main.yml`. The identity action establishes only secondary identity and builds an allowlisted secondary-only expected operation identity. It applies the current checkpoint algorithm, including stored-versus-current comparison when no explicit reset applies and unchanged reset behavior otherwise. It does not access primary or apply the two-hub predicate. Because the barrier preserves the `_checkpoint_enter` result name and `facts` shape, `playbooks/restore_only.yml` continues to rehydrate its existing Argo CD `run_id` and discovery namespaces without a consumer migration. Those operational facts are not physical-identity evidence. The restore-only playbook's existing recovery control flow is not redesigned.

Standalone decommission does not use the two-hub predicate. Its target-hardening work belongs to SSA-02; current behavior and tests are regression surfaces only.

## 9. Exact pre-mutation barriers

### 9.1 Python

```text
CLI input
→ InputValidator static context guard
→ _prepare_runtime role-safe client construction
→ run_operation_mode
→ _bind_runtime_hub_identities
→ fresh _collect_hub_identities
→ validate_distinct_hub_identities
→ StateManager.ensure_hub_identities
→ BARRIER: _execute_operation has not been called
→ operation_runners.execute_operation
→ preflight
→ primary_prep
→ first Kubernetes mutation
```

Failure before the barrier invokes no operation runner, workflow handler, mutation helper, or mutation-capable phase completion.

### 9.2 Collection

```text
switchover playbook
→ preflight tasks_from=identity_barrier
→ acm_input_validate static UX check
→ literal checkpoint_phase identity_barrier action
→ defensive context validation
→ fresh/authorized action-local UID establishment
→ strict per-role UID validation
→ primary != secondary
→ trusted local expected_operation_identity construction
→ current checkpoint identity handling
   (stored-versus-current validation when no explicit reset applies;
    existing reset/reset_from semantics otherwise)
→ STRUCTURAL BARRIER: nested rescue and post-barrier block not entered
→ preflight tasks_from=post_identity
→ primary_prep
→ activation and later mutation phases
```

The pre-barrier include is not enclosed by mutation-capable rescue. No action result or Boolean grants eligibility. Post-barrier recovery remains inside the nested block.

## 10. Freshness and mutation matrix

| Mode or path | Identity source | Test override | Checkpoint behavior | Mutation behavior |
| --- | --- | --- | --- | --- |
| execute | mandatory fresh action-local `kube-system` UID reads | ignored | trusted UIDs validate or initialize identity when enabled | only after barrier |
| execute plus native check | mandatory fresh action-local reads | ignored | read and validate only; no persistence | zero Kubernetes mutation |
| validate | fresh reads unless explicit non-live test override | eligible | no persistence | zero mutation |
| dry_run | fresh reads unless explicit non-live test override | eligible | no persistence | zero mutation |
| ordinary public preseed | never safety evidence | not applicable | never establishes expected identity | cannot satisfy guard |
| restore-only | trusted secondary-only identity | per approved non-live test rules | existing single-role contract | existing behavior |
| decommission | not applicable | not applicable | unchanged | unchanged |

Native Ansible check mode changes mutation semantics, not the freshness requirement for execute-mode identity validation.

## 11. Cross-form-factor parity contract

| Case | Python | Collection |
| --- | --- | --- |
| same context | reject | reject |
| different contexts, same live UID | reject | reject |
| primary UID unavailable | sanitized reject | sanitized reject |
| secondary UID unavailable | sanitized reject | sanitized reject |
| malformed or empty UID | reject | reject |
| distinct fresh UIDs | pass | pass |
| execute with stale/preseed | no production preseed | ignored; fresh reads decide |
| dry-run | enforced, zero mutation | enforced, zero mutation |
| native check | not applicable | enforced, fresh execute reads, zero mutation |
| resume role drift | existing failure | existing failure |
| restore-only | no two-hub predicate | no two-hub predicate |
| decommission | unchanged | unchanged |

There is no intentional divergence and no parity-status change. Static contracts pin the UID source, applicability, role names, messages, exclusions, and lack of production cross-imports.

## 12. Security and failure modes

| Threat or failure | Design response |
| --- | --- |
| Same context name | Static owner rejects early; trusted Collection action repeats the defense. |
| Different aliases to one cluster | Fresh UIDs compare equal and fail before mutation. |
| Stale or pre-seeded Collection identity facts | Public facts are never safety input; execute always reads live. |
| Malicious extra vars | Safety results remain action-local; structural block placement replaces a Boolean recovery flag. |
| Injected `hubs.<role>.cluster_uid` | Allowlisted local hubs omit UID; trusted local identities supply the exact fresh values. |
| Raw Python configuration exception | SSA-01 construction suppresses the lower-level raw log and translates by role before generic logging. |
| Raw Python API body or exception | Identity-specific non-logging retry/read path translates by role without a raw cause. |
| Retry log leak | Non-logging retry owner preserves retry policy without exception-string emission. |
| Malformed UID evidence | Mapping, metadata, type, and trimmed non-empty checks fail closed. |
| Native check mode | Runtime-owned check signal; execute still reads live and performs no writes. |
| Checkpoint disabled | Identity barrier still runs; persistence is not a prerequisite. |
| Skipped preflight or resume | New invocation reruns identity barrier before using skipped state. |
| Stored checkpoint drift | Same trusted live UIDs feed existing per-role validation. |
| Pre-barrier recovery | Identity include is outside mutation-capable rescue, so no recovery mutation is reachable. |
| Post-barrier failure | Existing feature-gated recovery stays eligible inside the nested rescue. |
| Restore-only | Secondary-only trusted binding; no primary access or two-role predicate. |
| Caller-supplied test override | Eligible only in validate/dry_run; ignored in execute and execute plus check. |
| Force or checkpoint reset | Neither bypasses fresh evidence or distinctness. |

## 13. Test design

Tests verify operator behavior and safety boundaries rather than only private implementation shape. Targeted tests run before expanded collection, parity, and repository gates.

### 13.1 Python coverage

Planned Python surfaces include `tests/test_validation.py`, `tests/test_kube_client.py`, `tests/test_runtime_bootstrap.py`, `tests/test_main.py`, `tests/test_cli_outcomes.py`, `tests/test_utils.py`, `tests/test_resume_safety_guards.py`, `tests/test_main_argocd_resume.py`, `tests/test_decommission.py`, and the parity fixtures/tests.

Required cases are:

- same-context rejection in a normal two-hub flow;
- same-context exclusion for restore-only and decommission;
- equal live UID refusal and distinct UID success;
- unavailable primary and unavailable secondary UID;
- malformed role mapping, non-string UID, empty UID, and whitespace-only UID;
- fresh identity reads in validate-only, dry-run, and execute;
- `_execute_operation` is not called after refusal;
- no mutation helper is called and no mutation-capable phase completes;
- existing stored-versus-current primary and secondary drift failures remain enforced;
- `--force` and reset controls do not bypass distinctness;
- client/configuration construction failures are translated separately for both roles;
- UID API-read failures are translated separately for both roles;
- restore-only, decommission, and Argo CD resume identity regressions remain valid.

Leak tests inject distinctive kubeconfig-path, token-like, API-response-body, raw-exception, and UID sentinels. Captured logs, standard output, standard error, reports, and final error text must omit every sentinel while containing the stable role-specific refusal. Retryable API failures must prove that every retry remains free of the sentinel payload.

### 13.2 Collection unit and shipped-flow coverage

Planned Collection surfaces include:

- `tests/unit/plugins/modules/test_acm_input_validate.py`;
- `tests/unit/plugins/action/test_checkpoint_phase_runtime.py`;
- preflight/checkpoint/recovery task-contract tests under `tests/unit/`;
- `tests/integration/test_preflight_role.py`;
- `tests/integration/test_switchover_roles.py`;
- `tests/integration/test_restore_only_role.py`;
- the shipped fake Kubernetes API harness in `tests/integration/argocd_fake_api.py`;
- checkpoint and core-switchover scenarios under `tests/scenario/`;
- root parity/static-contract tests and fixtures.

Required cases are:

- input-owner same-context rejection and trusted-action defensive rejection;
- equal live UIDs and distinct live UIDs;
- unavailable primary and unavailable secondary identity;
- malformed module result, missing metadata, non-string UID, empty UID, and whitespace-only UID;
- execute ignores stale public/pre-seeded facts;
- execute plus native check still performs fresh reads;
- explicit non-live override works only in validate/dry_run and is ignored in execute;
- public identity facts and returned action/report identity fields are never trusted as physical-identity evidence;
- `_checkpoint_enter.skipped_phase` and operational facts retain their existing post-barrier control-flow uses without becoming identity evidence;
- injected `acm_switchover_hubs.<role>.cluster_uid` is ignored;
- enabled checkpoint binds the same trusted UIDs used for distinctness;
- stored-versus-live primary and secondary checkpoint drift remains enforced;
- initial-barrier `reset_from` uses the trusted local expected identity while preserving current `_build_reset_from_checkpoint` replacement semantics;
- disabled checkpoint still enforces the guard;
- checkpoint-complete/skipped preflight still reruns identity validation;
- restore-only retains `_checkpoint_enter.facts` rehydration for Argo CD operational data;
- pre-barrier failure reaches no mutation-capable recovery;
- post-barrier failure preserves configured recovery;
- standalone preflight composes both task entries;
- native check mode and dry-run perform zero mutation;
- restore-only binds secondary only and never accesses primary;
- decommission remains unchanged.

The fake Kubernetes server records request methods and paths. Execute-plus-check tests require two fresh Namespace GETs while asserting zero POST, PATCH, PUT, and DELETE requests.

### 13.3 Adversarial extra-var matrix

Every adversarial playbook test runs on both repository-tested Ansible endpoints. The caller attempts to inject:

- `_acm_primary_identity_namespace`;
- `_acm_secondary_identity_namespace`;
- `acm_switchover_hub_identities`;
- `_acm_switchover_verified_hub_identities`;
- `acm_switchover_distinct_hubs_verified`;
- `_checkpoint_enter`;
- `acm_input_validation`;
- `_acm_identity_barrier_result`;
- every implementation action-result or returned identity/report fact;
- `acm_switchover_hubs.primary.cluster_uid`;
- `acm_switchover_hubs.secondary.cluster_uid`;
- `acm_switchover_test_overrides.non_live_hub_identities` in execute mode.

Ansible variables named like illustrative action-local Python variables may also be supplied to prove that the action never resolves them. They are not represented as actual provenance channels.

`_checkpoint_enter` remains a supported post-barrier carrier for skipped state and operational checkpoint facts. Adversarial tests prove that spoofing it cannot skip the literal identity-barrier task or supply physical UID evidence. They do not remove its existing operational-data consumers. `_acm_identity_barrier_result` remains in the injection set to prove that an obsolete or candidate register name has no authority.

| Case | Malicious setup | Required proof |
| --- | --- | --- |
| A: same physical cluster | live primary and secondary are `LIVE-SAME`; injected facts claim distinct values | both fresh GETs occur; equal-cluster refusal; no checkpoint completion, `primary_prep`, Argo CD recovery, checkpoint reset, or Kubernetes mutation |
| B: unavailable live UID | one live read fails; every accessible variable supplies a usable fake UID | role-specific sanitized refusal for primary and secondary variants; no fake evidence accepted; no raw body/path/error leakage |
| C: stored-versus-live drift | with no explicit reset, stored roles are `STORED-A`/`STORED-B`; fresh one-role value differs; injected hub UID and facts match stored value | expected identity contains the fresh action-local UID; existing validation rejects primary and secondary drift variants |
| D: spoofed recovery eligibility | pre-barrier identity failure plus all likely result/recovery/verified values set to pass or true | no Argo CD resume, checkpoint reset, or Kubernetes mutation because the rescue is structurally unreachable |
| E: post-barrier failure | distinct identities pass, then a controlled later task fails | configured Argo CD recovery and existing checkpoint-reset semantics remain available |
| F: execute plus native check | stale public facts and test override are injected | two fresh Namespace GETs; live result decides; zero POST/PATCH/PUT/DELETE and zero checkpoint write |

### 13.4 Parity and static contracts

Parity fixtures pin:

- applicability to normal two-hub flows and explicit exclusions;
- physical source `kube-system` Namespace UID;
- exact same-context, equal-cluster, and role-specific messages;
- primary and secondary role naming;
- fresh execute behavior and zero-mutation dry/check behavior;
- additive per-role resume binding;
- no runtime cross-import between form factors or from `tests/release/`.

### 13.5 Compatibility and gate order

Collection unit, integration, scenario, syntax, and build tests run on:

- `ansible-core 2.16.*` / Python 3.11;
- `ansible-core 2.21.*` / Python 3.12.

Implementation validation follows current `AGENTS.md` and `docs/development/testing.md`: focused tests first, then all gates invalidated by the Python, Collection, and parity-sensitive changes. That later gate set includes Python quality and security checks, Collection unit/integration/scenario/syntax/build lanes, and the parity-sensitive combined test command when still required by current authority. Live certification is explicitly excluded unless separately authorized.

## 14. RBAC impact

**Expected change: none.**

Both form factors already read the core/v1 Namespace identity and existing RBAC includes Namespace `get`. The Collection action relocates the existing `k8s_info` read into a trusted owner; it does not add an API group, resource, verb, namespace, impersonation mode, token mechanism, credential type, or controller permission. No RBAC manifest, bundled role, Helm chart, or RBAC documentation changes are required by the design.

If implementation evidence contradicts this assessment, SSA-01 must stop for scope expansion rather than change RBAC.

## 15. Planned implementation file map

This section records later implementation scope; it does not authorize edits.

### 15.1 Python production files

- `lib/validation.py`: own the normal same-context guard and pure cross-role UID validator.
- `lib/runtime_bootstrap.py`: own role-scoped client construction and live-identity failure translation.
- `lib/kube_client.py`: add bounded constructor-log suppression and the silent identity-specific Namespace read with existing retries.
- `acm_switchover.py`: enable role-safe initialization, order distinctness before `StateManager.ensure_hub_identities`, and route refusal through the existing safe outcome before dispatch.

Expected unchanged owners are `lib/utils.py`, `lib/cli_outcomes.py`, checkpoint schema, and constants.

### 15.2 Collection production files

- `plugins/modules/acm_input_validate.py`: add the normal-flow user-facing same-context check.
- `plugins/action/checkpoint_phase.py`: own the literal identity-barrier path, action-local reads and validation, distinctness, allowlisted UID-to-checkpoint handoff, check-mode authority, and ordinary later-transition use of established identity. Its current `reset_from` replacement semantics remain unchanged for `R3-06`.
- `roles/preflight/tasks/main.yml`: compose identity barrier and post-identity tasks for standalone preflight.
- `roles/preflight/tasks/identity_barrier.yml` (new): run input feedback and the unconditional literal trusted action before structural eligibility, registering the compatible result as `_checkpoint_enter`.
- `roles/preflight/tasks/post_identity.yml` (new): contain the remaining preflight, existing `_checkpoint_enter` skipped-state and operational-fact restoration, report contribution, and status behavior.
- `roles/preflight/tasks/discover_hub_identities.yml` (delete): remove the obsolete caller-shadowable register/`set_fact` safety path.
- `playbooks/switchover.yml`: establish the pre-barrier include outside the nested mutation-capable rescue and preserve current post-barrier recovery.

Expected unchanged safety owners are `plugins/module_utils/checkpoint.py` and `roles/preflight/tasks/write_report.yml`. `playbooks/preflight.yml`, `playbooks/restore_only.yml`, and decommission routing remain unchanged because `main.yml` composition, the compatible `_checkpoint_enter` result, and the action's single-role branch preserve their current integration.

### 15.3 Test files and harnesses

Python changes are planned in the current validation, client, bootstrap, CLI/outcome, state/resume, Argo CD resume, decommission, and parity test surfaces listed in section 13. Collection changes are planned in current input-module and checkpoint-action unit tests, preflight/checkpoint/recovery task contracts, preflight/switchover/restore integration tests, fake API harness, checkpoint/core scenarios, and parity/static-contract tests.

No release-controller test or implementation file is part of SSA-01.

## 16. Later documentation impact

Implementation will assess and update the following non-protected authorities where the realized behavior changes their contract:

- `thermos-resolution-plan.md`;
- `CHANGELOG.md` under Unreleased;
- root and Collection READMEs;
- `docs/development/architecture.md` and its affected Mermaid interaction diagram;
- `docs/operations/usage.md`;
- `docs/reference/validation-rules.md`;
- `docs/ansible-collection/parity-matrix.md`, with no capability status change;
- `docs/ansible-collection/behavior-map.md`;
- `ansible_collections/tomazb/acm_switchover/docs/coexistence.md`;
- the Collection variable reference;
- the CLI migration map if its operator mapping changes;
- scenario and test-migration catalogs;
- `docs/development/testing.md` if implementation introduces a named parity contract or gate.

This design task changes none of those files. Protected documents remain excluded.

## 17. Risks and mitigations

- **Action complexity:** Combining discovery and checkpoint identity validation enlarges `checkpoint_phase`. The new path is explicit, narrow, literal, and limited to identity establishment; generic phase transitions remain separate branches.
- **Ansible result leakage:** Nested module failures can contain API details. The action consumes and discards raw results locally and returns only stable messages; adversarial sentinel tests cover both roles and retry/failure variants.
- **Check-mode ambiguity:** A caller variable must not impersonate native check state. The action uses play-context runtime state and fake-server tests verify live GETs plus zero write verbs.
- **Checkpoint-complete resume:** Skipping preflight could otherwise skip identity proof. The identity barrier precedes skipped-state handling on every invocation.
- **Trusted identity loss on ordinary later transitions:** Outside current explicit-reset branches, later actions carry established identity and fail closed when an enabled execute transition lacks it; they do not rebuild physical identity from public facts.
- **Known `reset_from` identity replacement:** `_build_reset_from_checkpoint()` currently replaces operation identity with expected identity. SSA-01 leaves that behavior unchanged. `R3-06` owns revalidation-after-pruning and the replacement correction.
- **Recovery regression:** Moving rescue boundaries could suppress legitimate recovery. A post-barrier controlled-failure test proves existing recovery remains eligible.
- **Restore-only regression:** Shared action code could accidentally require primary. Single-role tests assert no primary access or cross-role comparison.
- **Returned-data provenance:** Public identity output can be spoofed and may affect report display, so no physical-identity decision consumes it. `_checkpoint_enter.skipped_phase` and operational facts remain existing post-barrier control inputs for preflight and restore-only; that compatibility use does not grant them identity authority.

No unresolved architecture decision, RBAC dependency, checkpoint schema change, or parity divergence remains in the approved design.

## 18. Acceptance-criterion traceability

| Issue #267 requirement | Design mechanism | Python owner | Collection owner | Planned verification |
| --- | --- | --- | --- | --- |
| 1. Identical context rejection | Static normal-flow guard plus Collection action defense | `lib/validation.py` | `acm_input_validate.py`, `checkpoint_phase.py` | Python validation tests; module and shipped-playbook tests |
| 2. Same physical UID rejection | Exact comparison of trimmed live Namespace UIDs | validator called by binder | action-local barrier | equal-UID unit/integration tests and adversarial case A |
| 3. Unavailable/malformed evidence sanitization | strict shape checks and stable role errors; silent Python lower layers | kube client/bootstrap/validator | action-local result parser | both-role malformed/failure tests and sentinel leak assertions |
| 4. Distinct UID success | validated unequal UID values pass to existing binding | binder and `StateManager` | action then checkpoint validation | distinct-success tests in both forms |
| 5. Execute freshness | no Python preseed; fresh reads every normal mode; Collection execute ignores overrides | bootstrap/binder | action-local `k8s_info` | mode tests, stale-preseed negatives, execute-plus-check case F |
| 6. Resume stored-versus-current preservation | cross-role check precedes existing per-role binding; current explicit `reset_from` replacement remains owned by `R3-06` | `StateManager.ensure_hub_identities` | initial barrier uses existing checkpoint algorithm over trusted locals | primary/secondary drift tests, adversarial case C, and unchanged `reset_from` boundary |
| 7. No mutation after refusal | pre-dispatch Python barrier; structural Collection pre-barrier include | `run_operation_mode` before `_execute_operation` | `switchover.yml` nesting | mutation mocks, phase markers, fake API verbs, case D |
| 8. Restore-only/decommission exclusions | secondary-only binding; no two-role decommission guard | validator applicability | action single-role branch; unchanged decommission | restore-only and decommission regressions |
| 9. Python test coverage | targeted validation, sanitization, binding, outcome, regression suite | Python test surfaces | not applicable | cases enumerated in section 13.1 |
| 10. Collection shipped-flow coverage | role/playbook integration plus fake API | not applicable | action, preflight, playbook | unit, integration, scenario, syntax, and build lanes |
| 11. Parity/static contracts | shared cases/messages without runtime cross-import | parity fixture consumer | parity fixture consumer | decision/source/message/applicability static tests |
| 12. Stale-preseed negative coverage | Python has no production preseed; Collection rejects all ordinary evidence | bootstrap | action-local evidence owner | stale facts, private-looking vars, test override, injected hub UID tests |
| 13. Dry-run/check zero mutation | enforce identity while preserving non-mutating mode | dry-run snapshot and pre-dispatch refusal | runtime check authority and non-persistent action | dry-run mocks; execute-plus-check GET/write-method assertions |
| 14. Checkpoint and Argo CD identity regression | additive stored binding and post-barrier recovery without changing `reset_from` | state and Argo resume regression tests | trusted initial expected identity, compatible `_checkpoint_enter`, and nested rescue | drift, pre/post barrier, current reset boundary, restore-only rehydration, Argo resume tests |
| 15. Compatibility lanes | endpoint matrix from compatibility authority | normal Python CI | 2.16/3.11 and 2.21/3.12 | repeat adversarial and Collection gates on both endpoints |
| 16. Targeted-before-full ordering | repository gate discipline | targeted then root/quality/security | targeted then unit/integration/scenario/syntax/build | recorded implementation verification sequence |
| 17. Protected-file exclusion | explicit read-only boundary | no protected owner | no protected owner | base-relative protected diff must be empty |

## 19. Design completion boundary

This document is a specification, not implementation authority. Creation and review of an implementation plan require separate explicit approval of this written spec. Production code, tests, implementation documentation, RBAC, protected files, live systems, commits, pushes, and pull requests remain unauthorized at this gate.
