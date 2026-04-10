# Product Requirements Document (PRD)

## ACM Hub Switchover Automation

**Version**: 1.6.3  
**Date**: 2026-04-10  
**Status**: Feature-complete, ongoing hardening and operational validation  
**Owner**: Platform Engineering Team

---

## Executive Summary

ACM Hub Switchover Automation is a Python CLI for performing controlled failover, migration, and decommission workflows between a primary and secondary Red Hat Advanced Cluster Management (ACM) hub. The product emphasizes operator safety, resumable execution, and explicit validation over maximum automation.

An Ansible Collection rewrite is approved for planning and will deliver the same core capabilities as a second form factor targeting both `ansible-core` CLI and Ansible Automation Platform (AAP). See [Ansible Collection Rewrite Design](../superpowers/specs/2026-04-10-ansible-collection-rewrite-design.md) for the approved design. The Python CLI remains the current production implementation during the coexistence period.

The current product supports:

- Idempotent switchover execution with persisted state
- Planned maintenance switchovers and disaster-recovery-style full restores
- Post-restore verification and backup re-establishment
- Optional old-hub decommissioning
- RBAC bootstrap and validation workflows
- GitOps and Argo CD impact detection, plus optional Argo CD auto-sync pause/resume assistance

The product does **not** attempt full desired-state reconciliation for GitOps-managed hubs. It helps operators identify and temporarily control GitOps interference, but operators must still update Git or other external controllers for the new primary hub.

## Problem Statement

Manual ACM hub switchover is operationally expensive:

- It spans multiple hubs, operators, and backup/restore resources
- It is sensitive to ordering mistakes and missing prerequisites
- It is hard to resume safely after interruptions
- It often lacks consistent validation of backups, protection flags, and cluster state
- GitOps and Argo CD controllers can unintentionally revert switchover steps

## Product Goals

### Primary goals

- Reduce operator error during ACM hub switchover
- Provide a repeatable, resumable workflow across maintenance and recovery scenarios
- Fail fast when prerequisites or safety conditions are not met
- Make state, phase transitions, and destructive actions explicit
- Support least-privilege operation with documented RBAC requirements

### Non-goals

- Replacing the authoritative switchover runbook
- Automatically repairing every cluster-side issue after restore
- Acting as a full GitOps controller migration tool
- Supporting a primary-unreachable execution path that skips required old-primary interactions

## Target Users

### Primary users

- Platform engineers executing planned switchovers
- SRE teams handling hub failover and recovery events
- DevOps teams validating automation in controlled environments

### Secondary users

- Cluster administrators auditing prerequisites and RBAC
- Support engineers troubleshooting failed or partial switchovers
- Documentation and release owners tracking delivered capabilities

## Representative Use Cases

### UC-1: Planned maintenance switchover

**Actor**: Platform engineer  
**Goal**: Promote a prepared secondary hub during a maintenance window

**Success criteria**:

- Validation passes before changes start
- Managed clusters reconnect on the new primary
- Backups resume on the new primary
- Old hub is left as `secondary`, `decommission`, or `none` exactly as requested

### UC-2: Disaster-recovery full restore

**Actor**: SRE engineer  
**Goal**: Recover service on a secondary hub using one-time restore workflows

**Success criteria**:

- Required backups are found and restored
- Post-activation cluster verification succeeds or fails with actionable diagnostics
- Finalization re-establishes backup protection on the new primary

### UC-3: GitOps-aware switchover

**Actor**: Platform engineer  
**Goal**: Prevent Argo CD from reverting ACM switchover resources during execution

**Success criteria**:

- GitOps ownership markers are reported before execution
- ACM-touching Argo CD Applications are auto-detected when ArgoCD CRD is present
- Auto-sync can be paused with `--argocd-manage`
- Auto-sync can be resumed later with `--argocd-resume-after-switchover` or `--argocd-resume-only`

### UC-4: Safe dry-run or validation-only rehearsal

**Actor**: DevOps or platform engineer  
**Goal**: Rehearse a switchover without mutating clusters

**Success criteria**:

- `--validate-only` runs all preflight checks without execution
- `--dry-run` shows intended mutations without applying them
- State handling remains safe for reruns and completed runs

### UC-5: Old-hub decommission or failback preparation

**Actor**: Platform engineer  
**Goal**: Cleanly retire the old hub or keep it ready for future failback

**Success criteria**:

- Destructive actions stay interactive unless decommission-specific automation is requested
- Optional old-hub observability removal is explicit
- Reverse switchover remains possible when the old hub is retained as a secondary

## Functional Requirements

### FR-1: Preflight validation

The product must validate both hubs before mutation begins.

- Required ACM, backup, and OADP namespaces must exist
- ACM versions must be detected and validated for compatibility
- OADP and DataProtectionApplication resources must be present and healthy
- Latest backups must be complete and not obviously stale for the workflow
- ClusterDeployments must be protected with `preserveOnDelete=true`
- Passive restore prerequisites must be validated for passive method runs
- Optional components such as Observability must be detected automatically
- RBAC permissions must be validated unless explicitly skipped
- GitOps ownership markers must be collected unless `--skip-gitops-check` is set
- Argo CD discovery and ACM-impact reporting runs automatically when ArgoCD CRD is detected

### FR-2: Primary hub preparation

The product must prepare the old primary hub safely and idempotently.

- Pause `BackupSchedule` with ACM-version-aware behavior
- Disable auto-import on managed clusters
- Scale down Thanos compactor when Observability is active
- Optionally pause ACM-touching Argo CD Applications when `--argocd-manage` is enabled
- Persist every completed step so reruns skip already-finished work

### FR-3: Secondary activation

The product must support both supported activation paths.

- `--method passive` must activate the existing passive restore
- `--method full` must create and monitor a one-time restore
- `--activation-method restore` must be available for passive mode only
- Restore polling must treat failed and finished-with-errors phases as terminal failures
- The tool must optionally enforce a minimum restored non-local cluster count via `--min-managed-clusters`
- The tool must optionally manage `ImportAndSync` strategy on newer ACM versions when `--manage-auto-import-strategy` is enabled

### FR-4: Post-activation verification

The product must verify that the promoted hub is operational.

- ManagedClusters must be checked for `Available=True` and `Joined=True`
- Observability restart and health checks must run when relevant
- Operators must receive guidance when metrics validation still requires manual confirmation
- Verification failures must be explicit and phase-aware

### FR-5: Finalization and old-hub handling

The product must complete the switchover and make the requested old-hub outcome explicit.

- Re-enable or recreate `BackupSchedule` on the new primary
- Verify that post-switchover backups resume correctly
- Support `--old-hub-action secondary`, `decommission`, and `none`
- Optionally delete `MultiClusterObservability` on the old hub when keeping it as a secondary via `--disable-observability-on-secondary`
- Resume Argo CD auto-sync when `--argocd-resume-after-switchover` is requested
- Provide a completion summary with next-step guidance

### FR-6: Decommission workflow

The product must support safe teardown of the old hub as a separate operation.

- Decommission remains an explicit mode
- Destructive steps require confirmation or decommission-only automation paths
- Managed clusters, observability resources, and MultiClusterHub resources must be removed in a controlled order
- The workflow must verify that old-hub cleanup completed or fail with actionable diagnostics

### FR-7: State management and resumability

The product must preserve enough state to resume safely and explain what happened.

- Persist phase, completed steps, config, and errors in a JSON state file
- Use safe writes and locking to avoid corrupt state
- Allow reruns to skip completed steps
- Preserve completed-state behavior for recent successful runs while still allowing `--validate-only` to execute preflight
- Support explicit reset with `--reset-state`

### FR-8: Setup and RBAC bootstrap

The product must help operators bootstrap least-privilege access.

- `--setup` must orchestrate RBAC deployment and kubeconfig generation
- Setup must require an explicit admin kubeconfig
- Role selection must support `operator`, `validator`, and `both`
- Optional decommission RBAC extension support must exist via `--include-decommission`
- Kubeconfig generation and merged kubeconfig workflows must remain documented and validated

### FR-9: Operational modes

The product must expose distinct operator modes with clear validation rules.

- Standard switchover
- `--validate-only`
- `--dry-run`
- `--decommission`
- `--setup`
- `--argocd-resume-only`

Cross-argument validation must remain strict and documented.

## Non-Functional Requirements

### NFR-1: Safety

- Safety checks must be preferred over implicit recovery
- Destructive actions must remain explicit
- Error messages must identify the failed phase or validation area

### NFR-2: Reliability

- Kubernetes API interactions must retry transient failures where appropriate
- Timeouts must be enforced for long-running waits
- State must survive interruption without losing critical checkpoints

### NFR-3: Usability

- CLI help and documentation must describe supported flags and combinations accurately
- Logging must support both human-readable and structured output
- Dry-run output must clearly differentiate simulated actions from real mutations

### NFR-4: Security

- The product must enforce input validation for contexts, paths, and resource identifiers
- Kubeconfig handling must include size and safety checks
- Least-privilege RBAC remains the default operating model

## Public CLI Surface To Keep Documented

The PRD must stay aligned with the current user-facing CLI, especially:

The Ansible Collection design maps these flags to grouped collection variables; see [CLI migration map](../superpowers/specs/2026-04-10-ansible-collection-rewrite-design.md) §11.2 for the mapping.

- `--method {passive,full}`
- `--activation-method {patch,restore}`
- `--old-hub-action {secondary,decommission,none}`
- `--manage-auto-import-strategy`
- `--min-managed-clusters`
- `--skip-gitops-check`
- `--argocd-manage`
- `--argocd-resume-after-switchover`
- `--argocd-resume-only`
- `--setup`
- `--include-decommission`
- `--disable-observability-on-secondary`

## Constraints and Caveats

- The automation still depends on the primary hub being reachable for normal switchover execution
- GitOps support is operationally helpful but intentionally limited; operators must still retarget Git and external controllers
- The runbook remains the authoritative operational procedure for manual and emergency handling
- Rollback remains a runbook-led/manual or partial workflow, not a dedicated standalone CLI mode

## Future Work

The PRD should limit future work to areas not already delivered in the repo.

- Broader real-world operational validation and measurement of timing/error-rate goals
- Additional observability and reporting around switchover outcomes
- Potential future expansion of GitOps-aware workflows beyond detection and Argo CD auto-sync coordination

### Ansible Collection Migration

An Ansible Collection rewrite has been approved for planning. The collection will deliver core parity with the Python CLI as a first milestone, targeting equal first-class execution in `ansible-core` CLI and AAP / Automation Controller. Key changes from the Python implementation:

- Ansible-native idempotency by default, with optional persistent checkpoints for long-running or interrupted switchovers
- Collection-first architecture with roles, playbooks, and thin custom plugins instead of a monolithic Python CLI
- Grouped variable model (`acm_switchover_hubs`, `acm_switchover_operation`, etc.) replacing flat CLI flags
- Execution-environment packaging for AAP alongside `ansible-galaxy` distribution

The Python CLI remains the production implementation during the coexistence period. The migration follows a phased plan: foundation → preflight → switchover execution → checkpoints → Argo CD → non-core helpers. See [Ansible Collection Rewrite Design](../superpowers/specs/2026-04-10-ansible-collection-rewrite-design.md) for the full design.
