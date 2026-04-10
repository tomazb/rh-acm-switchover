# Ansible Collection Rewrite Design

Date: 2026-04-10
Status: Approved for planning
Scope: Rewrite the current Python-first ACM switchover automation into an Ansible Collection with core parity first

## 1. Goal

Rewrite `rh-acm-switchover` as an Ansible Collection that supports:

- equal first-class execution in `ansible-core` CLI and Ansible Automation Platform / Automation Controller
- core parity first: switchover workflow and validation workflow
- Ansible-native idempotency by default
- optional persistent checkpoints for long-running or interrupted switchovers
- modern collection practices intended for long-term maintenance

This is a collection-first migration, not a literal file-by-file translation of the current Python and Bash implementation.

## 2. Current Repo Assessment

The current repository is broader than a single CLI script. The rewrite must account for these capability areas:

- phased switchover orchestration in `acm_switchover.py`
- resumable workflow state in `lib/utils.py`
- Kubernetes and ACM API behavior in `lib/kube_client.py`
- structured validation in `modules/preflight_coordinator.py` and `modules/preflight/`
- optional Argo CD and GitOps handling in `lib/argocd.py` and `lib/gitops_detector.py`
- standalone operational scripts in `scripts/`
- deployment artifacts in `deploy/`

The core safety model today is explicit phases, validation gates, clear failure classes, re-runnable operations, and durable operator-visible state. The Ansible rewrite must preserve those properties, even if the implementation model changes.

## 3. Decisions Confirmed

- Packaging target: Ansible Collection
- Scope for first production-ready release: core parity first
- First-class execution targets: `ansible-core` CLI and AAP / Automation Controller equally
- State model: hybrid
  - default to Ansible-native idempotency
  - support an optional persistent checkpoint backend for long-running or interrupted runs
- Compatibility baseline: modern supported `ansible-core` and AAP releases
- Migration style: collection-first with thin custom plugins

## 4. Recommended Migration Approach

Three approaches were considered:

1. Role-first translation
2. Collection-first with thin custom plugins
3. Compatibility-wrapper migration

Recommended approach: collection-first with thin custom plugins.

Reasoning:

- Pure YAML will handle the easy orchestration work but will make the current state, retry, polling, and validation behavior harder to maintain.
- Wrapping the existing Python engine in Ansible would delay the real redesign and carry the current architecture forward as hidden technical debt.
- A proper collection allows durable public interfaces for playbooks, roles, and custom modules while staying compatible with both CLI and AAP execution models.

## 5. Target Collection Architecture

Recommended collection layout:

```text
ansible_collections/
  tomazb/acm_switchover/
    galaxy.yml
    meta/runtime.yml
    README.md
    playbooks/
      preflight.yml
      switchover.yml
      decommission.yml
      argocd_resume.yml
    roles/
      preflight/
      primary_prep/
      activation/
      post_activation/
      finalization/
      decommission/
      argocd_manage/
      rbac_bootstrap/
      discovery/
    plugins/
      modules/
      action/
      module_utils/
      filter/
      callback/
    docs/
    tests/
      unit/
      integration/
      scenario/
```

Recommended initial collection identifier: `tomazb.acm_switchover`.
If the project later moves under a different organizational publisher, the namespace can change at release time, but the design assumes this concrete identifier during implementation planning.

### 5.1 Mapping from the current repo

- `acm_switchover.py` becomes orchestration playbooks plus a small number of controller-side plugins
- `modules/primary_prep.py` becomes the `primary_prep` role
- `modules/activation.py` becomes the `activation` role
- `modules/post_activation.py` becomes the `post_activation` role
- `modules/finalization.py` becomes the `finalization` role
- `modules/decommission.py` becomes the `decommission` role
- `modules/preflight_coordinator.py` and `modules/preflight/` become the `preflight` role plus reusable validation modules
- `lib/kube_client.py` is decomposed into stock Kubernetes collection usage plus shared `module_utils`
- `lib/utils.py` is decomposed into checkpoint helpers, version helpers, and reporting helpers
- `lib/argocd.py` and `lib/gitops_detector.py` feed a focused `argocd_manage` role and related modules
- `scripts/` are not all translated in phase 1; only logic needed for core parity moves immediately

### 5.2 Role versus plugin boundary rules

- Put workflow sequencing, policy, and operator intent in playbooks and roles
- Put API-heavy normalization, retry behavior, polling semantics, checkpoint persistence, and ACM-specific interpretation into custom modules or action plugins
- Prefer `kubernetes.core.k8s` and `kubernetes.core.k8s_info` where they are sufficient
- Avoid large Jinja control flows or long `set_fact` chains that simulate application logic

### 5.3 ACM version-conditional behavior pattern

The current codebase has material ACM version gates, including:

- ACM 2.11 BackupSchedule delete semantics
- ACM 2.12+ `spec.paused` BackupSchedule semantics
- ACM 2.14+ `autoImportStrategy` behavior and related activation logic

The collection should not scatter raw version comparisons throughout many tasks.

Recommended pattern:

- keep simple phase branching in roles when the operator intent is different
- centralize resource-specific version semantics in custom modules and `module_utils`
- expose normalized facts such as `backup_schedule_pause_mode` and `supports_auto_import_strategy` to roles rather than repeating version expressions everywhere

This keeps roles readable and prevents version behavior from turning into repetitive `when:` fragments across the collection.

### 5.4 Discovery bridge during migration

Although discovery is not part of phase-1 core parity, operators need a usable bridge from day one.

Recommended interim approach:

- keep `scripts/discover-hub.sh` supported during the coexistence period
- document it as the supported discovery bridge until the collection-native discovery content is ready
- ensure the script’s recommended output can feed directly into the collection variable model

This avoids blocking early collection adoption on a full discovery rewrite.

### 5.5 Coexistence and migration strategy

The Python implementation and the Ansible collection will coexist for a meaningful period. The migration needs an explicit dual-maintenance model.

#### 5.5.1 Shared behavior contract

Define a machine-verifiable parity contract built around scenarios rather than implementation internals.

For each supported scenario, record:

- inputs
- initial cluster state assumptions
- expected phase outcomes
- expected validation findings
- expected mutated resources
- expected report and checkpoint artifacts

The same scenario catalog must be runnable against:

- the current Python implementation
- the new Ansible collection

This shared scenario suite is the primary parity mechanism. Feature lists alone are not enough.

#### 5.5.2 Parity matrix

Maintain a parity matrix that tracks each major capability as one of:

- Python only
- dual-supported
- collection only
- deprecated

At minimum, the matrix should track:

- preflight validation domains
- primary prep steps
- activation modes
- post-activation verification
- finalization behaviors
- RBAC validation
- Argo CD support
- discovery
- decommission
- report formats
- checkpoint compatibility

#### 5.5.3 Dual-bug-fix policy

During coexistence:

- safety and correctness defects in dual-supported features must be evaluated for both implementations
- fixes must land in both implementations unless the parity matrix explicitly marks one side as deprecated for that feature
- every such fix should update the shared scenario suite when behavior changes

This prevents silent divergence on the most operationally sensitive paths.

#### 5.5.4 Shared code policy

Do not assume a shared runtime library by default.

Recommended policy:

- share behavior specifications, schemas, test fixtures, sample artifacts, and version-rule data where useful
- avoid sharing live orchestration/runtime code between the Python CLI and Ansible collection unless it is extracted into a deliberately versioned, independently testable library with clear consumers
- prefer disciplined duplication over accidental coupling when the execution models differ

The goal is behavioral parity, not forcing both implementations through one internal code shape.

#### 5.5.5 Artifact and report compatibility

Operators must be able to compare or hand off between tools during migration.

Recommended policy:

- standardize a compatible machine-readable report schema early
- document where collection artifacts intentionally differ from current Python artifacts
- preserve checkpoint/report field compatibility where mid-incident handoff matters
- if exact compatibility is not feasible, provide an explicit translation tool or schema mapping

#### 5.5.6 Deprecation milestones

Define explicit gates rather than vague intent.

Recommended milestones:

1. Collection preview: Python remains primary; collection is non-default and feature-limited
2. Dual-supported: core parity reached and shared scenario tests pass for supported flows
3. Collection-primary: new features land in the collection first; Python enters maintenance mode
4. Python read-only: only critical fixes accepted; no feature work
5. Python retirement: remove or archive once parity matrix marks required features collection-only and operators have a migration path

## 6. Execution Model

The collection should be controller-driven in both CLI and AAP.

- Collection playbooks are the stable operator entrypoints
- Kubernetes API operations run from the control node against hub kubeconfigs or controller-managed credentials
- The collection should use controller-side execution patterns such as `delegate_to: localhost`
- AAP-specific concerns such as inventories, surveys, credentials, and artifacts must be supported without changing the core workflow contract

Dry-run behavior must be explicit rather than assumed from generic Ansible mechanics.

- all custom modules should implement `supports_check_mode = True`
- custom modules should return realistic change predictions and diff-like data where possible
- collection playbooks should support both ordinary Ansible check mode and a collection-level dry-run contract for Kubernetes custom resources where generic check mode is insufficient
- dry-run output should remain operator-meaningful for high-risk actions such as restore activation, BackupSchedule mutation, and Argo CD pause or resume

This matches the current tool’s real operating model: it orchestrates Kubernetes APIs from a control plane rather than configuring remote hosts.

## 7. State and Resume Strategy

The current `StateManager` behavior in `lib/utils.py` should not be copied literally, but its safety outcomes should be preserved.

### 7.1 Default mode

- rely on Ansible idempotency and live cluster state checks
- make re-runs safe without requiring stored workflow state

### 7.2 Optional checkpoint mode

Add a persistent checkpoint backend for long-running or interrupted switchovers.

Checkpoint data should store only facts Ansible cannot reconstruct cheaply or safely:

- current workflow phase
- completed high-risk checkpoints
- saved operational data needed for reversal or resume
- Argo CD pause metadata
- structured error history
- report artifact references

### 7.3 Checkpoint backend recommendation

Use a small plugin pair to read and write checkpoints with pluggable backends:

- file backend for local CLI runs
- artifact-friendly file backend for AAP job environments
- optional Kubernetes-native backend later only if real demand appears

Do not over-persist. The collection should store only irreducible workflow facts rather than replaying an application-style state engine inside Ansible.

### 7.4 Concurrency and locking

The current Python tool uses process-level advisory locking around the state file to prevent concurrent switchovers against the same workflow state. The collection needs an equivalent safety control.

Minimum requirements:

- file-backed checkpoints must implement advisory locking for concurrent local access
- controller or shared-backend executions must have an equivalent coordination mechanism, such as a lock record or Lease-style guard
- lock acquisition failures must be explicit, operator-visible, and non-destructive
- lock ownership metadata should identify the active process or job

Two operators must not be able to run the same switchover flow concurrently against the same target pair without a clear hard stop.

## 8. Phase Model

Keep explicit phases as operational boundaries:

- `preflight`
- `primary_prep`
- `activation`
- `post_activation`
- `finalization`

These phases should serve three purposes:

- operator-visible reporting
- optional checkpoint boundaries
- controlled partial execution and recovery

Roles should still be independently runnable for debugging and controlled recovery.

## 9. Role Breakdown

### 9.1 Core phase roles for phase 1

- `preflight`
- `primary_prep`
- `activation`
- `post_activation`
- `finalization`

### 9.2 Supporting roles for later phases

- `decommission`
- `argocd_manage`
- `discovery`
- `rbac_bootstrap`

### 9.3 Role design rules

Each role should expose:

- a narrow variable interface
- a stable result structure
- a small set of useful tags
- clear operator output and artifacts

Split role tasks by functional concern, for example:

- `validate_versions.yml`
- `pause_backups.yml`
- `activate_passive.yml`
- `verify_clusters.yml`

Do not create monolithic task files that recreate the current Python module shape in YAML.

## 10. Thin Custom Plugin Breakdown

Use custom plugins only where Ansible YAML is the wrong abstraction.

### 10.1 Recommended custom modules

- `acm_restore_info`
  - normalize passive and full restore discovery and status interpretation
- `acm_backup_schedule`
  - encapsulate ACM-version-specific BackupSchedule pause, resume, and delete behavior
- `acm_managedcluster_status`
  - normalize cluster join and availability reporting plus threshold enforcement
- `acm_cluster_verify`
  - perform parallel managed-cluster verification, klusterlet hub resolution, kubeconfig extraction, and related aggregation work now done in `post_activation.py`
- `acm_checkpoint`
  - read and write checkpoint records
- `acm_argocd_autosync`
  - pause and resume ACM-touching Argo CD Applications with reversible metadata
- `acm_preflight_report`
  - aggregate structured validation results into stable report output

### 10.2 Recommended `module_utils` areas

- Kubernetes client and auth normalization
- ACM API group and version handling
- retry and polling helpers
- adaptive polling helpers matching the current fast-then-slow wait pattern
- result normalization and common exceptions
- checkpoint backend helpers

### 10.3 Recommended action plugins

- checkpoint coordination around whole phases
- controller-side artifact and report writing
- kubeconfig or context resolution where controller-side coordination is required

Large parts of `lib/kube_client.py` should disappear into stock modules and shared helper code rather than being ported as one compatibility layer.

## 11. Variable Model and Operator Interface

The collection should not reproduce the current CLI flags as a flat top-level API. Use grouped variables that work well in inventory, `group_vars`, `extra_vars`, and AAP surveys.

Recommended top-level variable namespaces:

- `acm_switchover_hubs`
- `acm_switchover_operation`
- `acm_switchover_features`
- `acm_switchover_execution`
- `acm_switchover_rbac`

All externally supplied values must pass through an explicit validation layer before mutation starts.

That validation layer should cover at minimum:

- kubeconfig and artifact paths
- context names
- Kubernetes resource names and namespaces
- enumerated operation modes
- incompatible flag or variable combinations
- survey and `extra_vars` values treated as untrusted input

The current `InputValidator` semantics in `lib/validation.py` should be preserved conceptually, including path traversal and malformed-input protection, even if the implementation moves into a collection validation plugin or preflight validation role.

Example shape:

```yaml
acm_switchover_hubs:
  primary:
    context: primary-hub
    kubeconfig: /path/to/kubeconfig
  secondary:
    context: secondary-hub
    kubeconfig: /path/to/kubeconfig

acm_switchover_operation:
  method: passive
  old_hub_action: secondary
  activation_method: patch
  min_managed_clusters: 0

acm_switchover_features:
  manage_auto_import_strategy: false
  skip_observability_checks: false
  skip_gitops_check: false
  argocd:
    manage: false
    resume_after_switchover: false

acm_switchover_execution:
  mode: execute
  checkpoint:
    enabled: true
    backend: file
    path: .state/switchover-primary__secondary.json
  report_dir: ./artifacts
```

### 11.1 Operator entrypoints

Phase-1 collection playbooks should be:

- `preflight.yml`
- `switchover.yml`

Useful early supporting playbooks, likely after core parity:

- `resume_argocd.yml`
- `verify_post_activation.yml`

Deferred playbooks outside phase-1 core parity:

- `decommission.yml`

### 11.2 CLI migration map

Document a mapping from current CLI flags to collection variables, but do not preserve the CLI flag set as the collection’s primary public interface.

Examples:

- `--method` -> `acm_switchover_operation.method`
- `--old-hub-action` -> `acm_switchover_operation.old_hub_action`
- `--activation-method` -> `acm_switchover_operation.activation_method`
- `--argocd-manage` -> `acm_switchover_features.argocd.manage`
- `--argocd-resume-after-switchover` -> `acm_switchover_features.argocd.resume_after_switchover`

### 11.3 Reporting outputs

The collection should emit:

- human-readable summaries in play output
- machine-readable JSON or YAML report artifacts
- checkpoint artifacts when enabled

This model is more automation-friendly than the current mixed logging and JSON state output.

## 12. Validation Design

Do not reduce the current validation subsystem to scattered `assert` tasks.

Preserve the current layered model:

- input validation at playbook and role boundaries
- environment validation before mutation
- phase-local validation before dangerous actions
- post-activation verification as a first-class stage
- RBAC self-validation during preflight for every supported run, not only in later bootstrap work

The preflight role must include RBAC validation behavior equivalent in intent to the current `SelfSubjectAccessReview` checks in `lib/rbac_validator.py`.
The `rbac_bootstrap` role remains a later-phase deployment helper, but RBAC verification itself is part of core parity.

Each validation check should produce a stable result object with:

- `id`
- `severity`
- `status`
- `message`
- `details`
- `recommended_action`

That result contract should drive both operator output and machine-readable gating.

## 13. Error Handling Strategy

Preserve the intent of the current failure model:

- validation failures stop before mutation
- retryable Kubernetes and API failures are retried inside modules, not scattered across arbitrary tasks
- fatal failures include enough context for restart or resume decisions
- checkpoint mode records phase and failure metadata before exit

This is a primary justification for selective custom modules. Task-level retry loops alone are too blunt for the current semantics in `lib/kube_client.py` and `lib/waiter.py`.

## 14. Testing Strategy

The rewrite should adopt Ansible-native testing rather than only porting the existing pytest structure.

Recommended layers:

- `ansible-test sanity` for baseline quality gates
- unit tests for custom modules and `module_utils`
- integration tests for plugin behavior against disposable or mocked Kubernetes targets
- scenario tests for multi-phase switchover flows and checkpoint resume behavior
- AAP smoke validation for job-template execution assumptions, surveys, and artifacts

The current `tests/` directory remains valuable as a behavior catalog. Each existing test should be triaged into one of four buckets:

- translate directly
- convert into plugin unit tests
- convert into scenario or integration tests
- drop because it only validates Python internals that will not exist after the rewrite

## 15. Multi-Phase Migration Plan

### Phase 0: Discovery and behavioral extraction

Goals:

- inventory all current behaviors, flags, modes, and outputs
- classify logic into role-suitable versus plugin-suitable concerns
- identify the minimum core-parity surface for phase 1
- create a compatibility matrix for CLI features versus collection features
- define the shared scenario suite used to validate Python-versus-collection parity

Outputs:

- feature inventory
- behavior map from Python and Bash to collection targets
- test migration catalog
- parity matrix
- initial shared scenario catalog

### Phase 1: Collection foundation

Goals:

- scaffold the collection structure
- define runtime metadata and supported version policy
- establish CI with `ansible-test sanity` and unit test entrypoints
- define the base variable model and artifact conventions
- define the artifact schema and compatibility rules for coexistence with the Python tool
- define the lock model for checkpoint backends

Outputs:

- collection skeleton
- docs skeleton
- CI baseline
- initial examples and operator contract
- compatibility and coexistence notes

### Phase 2: Preflight and validation migration

Goals:

- port core validation behavior into the `preflight` role
- implement structured validation results and reports
- implement explicit input sanitization and RBAC self-validation
- translate or replace critical validation tests

Focus areas from current repo:

- `modules/preflight_coordinator.py`
- `modules/preflight/`
- relevant parts of `lib/validation.py`
- relevant parts of `lib/rbac_validator.py` needed for core parity
- report and artifact compatibility requirements from coexistence planning

Exit criteria:

- collection preflight gates the same critical safety conditions as the current tool for supported scenarios
- shared parity scenarios pass for supported preflight flows

### Phase 3: Switchover execution migration

Goals:

- implement `primary_prep`, `activation`, `post_activation`, and `finalization`
- introduce the thin custom modules required for restore, backup, cluster status, and reporting
- implement explicit dry-run and check-mode behavior for custom modules
- validate task idempotency and safe re-run semantics

Focus areas from current repo:

- `modules/primary_prep.py`
- `modules/activation.py`
- `modules/post_activation.py`
- `modules/finalization.py`
- `modules/backup_schedule.py`
- `modules/restore_discovery.py`
- parallel verification and kubeconfig-handling behavior from `modules/post_activation.py`
- polling behavior from `lib/waiter.py`

Exit criteria:

- a full core switchover runs end to end in CLI and AAP-supported execution models
- shared parity scenarios pass for supported core switchover flows

### Phase 4: Optional checkpoint backend

Goals:

- implement the checkpoint backend and plugin APIs
- define checkpoint schema and retention behavior
- implement concurrent-access protection and lock ownership reporting
- verify interrupted-run recovery against realistic scenarios

Focus areas from current repo:

- resumability semantics from `lib/utils.py`
- high-risk workflow checkpoints from current phase handlers

Exit criteria:

- interrupted switchovers can resume with acceptable operational confidence
- concurrency protection prevents duplicate active switchovers on the same workflow state

### Phase 5: Argo CD and GitOps behavior

Goals:

- port the core Argo CD pause and resume workflow needed for switchover safety
- keep generic GitOps drift warnings separate from Argo CD mutating behavior

Focus areas from current repo:

- `lib/argocd.py`
- `lib/gitops_detector.py`

Exit criteria:

- Argo CD-managed environments remain safe and reversible in supported flows

### Phase 6: Non-core helpers and operational extras

Goals:

- migrate or redesign discovery, decommission, and RBAC bootstrap content
- decide which shell-script behaviors should remain external helpers versus collection entrypoints
- reconcile collection distribution, execution-environment packaging, and existing deploy artifacts

Focus areas from current repo:

- `scripts/discover-hub.sh`
- `scripts/setup-rbac.sh`
- `modules/decommission.py`
- deploy assets in `deploy/`

Exit criteria:

- phase-2 features have a clear home in the collection or are explicitly deferred

## 16. Distribution and packaging strategy

The collection needs a concrete distribution story alongside the current repository artifacts.

Recommended targets:

- publish the collection through `ansible-galaxy` or Automation Hub compatible packaging
- define an execution-environment image for AAP that includes required collections and Python dependencies
- keep container packaging for the legacy Python tool only during coexistence, then reassess
- reconcile Helm chart and raw RBAC manifests with the eventual `rbac_bootstrap` role so operators do not get two conflicting deployment stories

The plan should treat collection packaging, execution environments, and RBAC deployment assets as part of the migration, not as an afterthought.

## 17. Best-Practice Rules for the Rewrite

- Prefer stock Kubernetes modules before introducing custom modules
- Keep custom plugins thin, stable, and heavily tested
- Keep operator-visible sequencing in playbooks and roles
- Avoid copying Python application architecture into YAML
- Preserve safety gates and operator clarity over reducing file count
- Design variable contracts for inventory and AAP survey use, not only for CLI convenience
- Treat reporting and artifacts as first-class outputs
- Standardize on collection-native docs, examples, and `ansible-test`
- Do not broaden scope beyond core parity until the core collection contract is stable
- Preserve the current GitOps marker caveat that `app.kubernetes.io/instance` is `UNRELIABLE` and not a definitive ownership signal

## 18. Risks and Mitigations

### Risk: Over-translating application logic into YAML

Mitigation:

- use custom modules for normalization, retries, polling, and checkpoint persistence where needed

### Risk: Losing current safety behavior during migration

Mitigation:

- treat the existing test suite as a behavior catalog
- define a parity matrix and validate critical safety cases first

### Risk: Designing only for CLI or only for AAP

Mitigation:

- enforce a single variable contract and artifact model across both execution targets

### Risk: Recreating `StateManager` too literally

Mitigation:

- persist only irreducible workflow facts
- keep Ansible idempotency as the default execution model

### Risk: Expanding phase-1 scope into a full repo rewrite

Mitigation:

- keep phase 1 limited to core parity
- explicitly defer discovery, RBAC bootstrap, and non-core helpers

## 19. Success Criteria

The rewrite is successful when:

- the collection exposes clean `preflight` and `switchover` entrypoints
- the collection runs in both `ansible-core` CLI and AAP from day one
- critical preflight and post-activation safety checks are preserved
- the core switchover flow is safely re-runnable
- optional checkpoint mode supports interrupted long-running operations
- the custom plugin surface stays small and justified
- the collection uses modern collection structure, testing, and documentation practices

## 20. Official Ansible References Consulted

- Collection guide: https://docs.ansible.com/projects/ansible-core/devel/collections_guide/index.html
- Developing collections: https://docs.ansible.com/projects/ansible-core/devel/dev_guide/developing_collections.html
- Collection structure: https://docs.ansible.com/projects/ansible-core/devel/dev_guide/developing_collections_structure.html
- Action plugins: https://docs.ansible.com/ansible/devel/plugins/action.html
- Sanity testing: https://docs.ansible.com/projects/ansible-core/devel/dev_guide/testing/sanity/index.html
- Collection docs tooling: https://docs.ansible.com/projects/antsibull-docs/collection-docs/

## 21. Recommended Next Step

The next step is to create an implementation plan that decomposes this design into concrete delivery milestones, file layout, test strategy, and phased execution order for the collection rewrite.
