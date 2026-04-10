# ACM Switchover Feature Inventory

Date: 2026-04-10
Source: `acm_switchover.py`, `lib/validation.py`, `scripts/`

## Purpose

This document records the Phase 0 migration-control subset of current operator-facing behavior that the
collection migration must account for first.

## Top-Level Variable Namespaces

- `acm_switchover_hubs`
- `acm_switchover_operation`
- `acm_switchover_features`
- `acm_switchover_execution`
- `acm_switchover_rbac`

## Core Switchover and Validation Inputs

| Current CLI Flag | Collection Variable | Phase 1 Foundation | Notes |
| --- | --- | --- | --- |
| `--primary-context` | `acm_switchover_hubs.primary.context` | yes | Required in supported flows |
| `--secondary-context` | `acm_switchover_hubs.secondary.context` | yes | Required in core switchover flows |
| `--method` | `acm_switchover_operation.method` | yes | `passive` or `full` |
| `--old-hub-action` | `acm_switchover_operation.old_hub_action` | yes | `secondary`, `decommission`, or `none` |
| `--activation-method` | `acm_switchover_operation.activation_method` | yes | `restore` only valid with passive |
| `--min-managed-clusters` | `acm_switchover_operation.min_managed_clusters` | yes | Threshold contract only in Phase 1 |
| `--validate-only` | `acm_switchover_execution.mode=validate` | yes | Playbook contract only |
| `--dry-run` | `acm_switchover_execution.mode=dry_run` | yes | Contract only; no runtime implementation here |
| `--verbose` | `acm_switchover_execution.verbose` | yes | Output contract only |
| `--force` | `acm_switchover_execution.force` | yes | Checkpoint semantics deferred |
| `--state-file` | `acm_switchover_execution.checkpoint.path` | yes | Schema and docs only |
| `--reset-state` | `acm_switchover_execution.checkpoint.reset` | yes | Schema and docs only |

## Deferred Inputs

Deferred phase numbers refer to later Ansible Collection rewrite implementation phases.

| Current CLI Flag | Collection Variable | Deferred Phase | Notes |
| --- | --- | --- | --- |
| `--decommission` | `acm_switchover_execution.mode=decommission` | 6 | Separate playbook later |
| `--setup` | `acm_switchover_execution.mode=setup` | 6 | Becomes `rbac_bootstrap` content |
| `--argocd-manage` | `acm_switchover_features.argocd.manage` | 5 | Runtime behavior deferred |
| `--argocd-resume-after-switchover` | `acm_switchover_features.argocd.resume_after_switchover` | 5 | Runtime behavior deferred |
| `--argocd-resume-only` | `acm_switchover_execution.mode=argocd_resume` | 5 | Playbook deferred |
| `--admin-kubeconfig` | `acm_switchover_rbac.admin_kubeconfig` | 6 | RBAC bootstrap only |
| `--role` | `acm_switchover_rbac.role` | 6 | RBAC bootstrap only |
| `--token-duration` | `acm_switchover_rbac.token_duration` | 6 | RBAC bootstrap only |
| `--output-dir` | `acm_switchover_rbac.output_dir` | 6 | RBAC bootstrap only |
| `--skip-kubeconfig-generation` | `acm_switchover_rbac.skip_kubeconfig_generation` | 6 | RBAC bootstrap only |
| `--include-decommission` | `acm_switchover_rbac.include_decommission` | 6 | RBAC bootstrap only |

## Validation Rules Preserved Conceptually

- secondary context required for normal switchovers
- `activation_method=restore` requires `method=passive`
- path validation must still block traversal and shell metacharacters
- AAP survey values and `extra_vars` are untrusted inputs
- RBAC self-validation remains part of core parity even though bootstrap is deferred

## Execution Modes

Supported in Phase 1 documentation and stubs:

- `execute`
- `validate`
- `dry_run`

Deferred:

- `decommission`
- `setup`
- `argocd_resume`
