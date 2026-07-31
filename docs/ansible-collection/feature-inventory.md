# ACM Switchover Feature Inventory

Date: 2026-05-12
Source: `acm_switchover.py`, `lib/validation.py`, `scripts/`

## Purpose

This document records the current migration-control subset of operator-facing behavior that the
collection migration must account for.

## Top-Level Variable Namespaces

- `acm_switchover_hubs`
- `acm_switchover_operation`
- `acm_switchover_features`
- `acm_switchover_execution`
- `acm_switchover_decommission`
- `acm_switchover_rbac_bootstrap`

## Core Switchover and Validation Inputs

| Current CLI Flag | Collection Variable | Current Boundary | Notes |
| --- | --- | --- | --- |
| `--primary-context` | `acm_switchover_hubs.primary.context` | yes | Required in supported flows |
| `--secondary-context` | `acm_switchover_hubs.secondary.context` | yes | Required in core switchover flows |
| `--method` | `acm_switchover_operation.method` | yes | `passive` or `full` |
| `--old-hub-action` | `acm_switchover_operation.old_hub_action` | yes | `secondary`, `decommission`, or `none` |
| `--activation-method` | `acm_switchover_operation.activation_method` | yes | `restore` only valid with passive |
| `--min-managed-clusters` | `acm_switchover_operation.min_managed_clusters` | yes | Threshold contract only in Current Boundaries |
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
| `--decommission` | `playbooks/decommission.yml` plus `acm_switchover_decommission` | 6 | Implemented as a standalone playbook |
| `--setup` | `playbooks/rbac_bootstrap.yml` plus `acm_switchover_rbac_bootstrap` | 6 | Implemented as RBAC bootstrap content |
| `--argocd-manage` | `acm_switchover_features.argocd.manage` | 5 | Implemented in core playbooks and `argocd_resume.yml` |
| `--argocd-resume-only` | `playbooks/argocd_resume.yml` | 5 | Implemented as explicit resume playbook |
| `--admin-kubeconfig` | `acm_switchover_hubs.primary.kubeconfig` | 6 | RBAC bootstrap target credential |
| `--role` | `acm_switchover_rbac_bootstrap.role` | 6 | RBAC bootstrap only |
| `--token-duration` | `acm_switchover_rbac_bootstrap.token_duration` | 6 | RBAC bootstrap only |
| `--output-dir` | `acm_switchover_rbac_bootstrap.output_dir` | 6 | RBAC bootstrap only |
| `--skip-kubeconfig-generation` | `acm_switchover_rbac_bootstrap.generate_kubeconfigs=false` | 6 | RBAC bootstrap only |
| `--include-decommission` | `acm_switchover_rbac_bootstrap.include_decommission` | 6 | RBAC bootstrap only |

## Validation Rules Preserved Conceptually

- secondary context required for normal switchovers
- `activation_method=restore` requires `method=passive`
- path validation must still block traversal and shell metacharacters
- AAP survey values and `extra_vars` are untrusted inputs
- RBAC self-validation and bootstrap remain dual-supported parity contracts

## Execution Modes

Supported in current documentation and stubs:

- `execute`
- `validate`
- `dry_run`

Standalone playbooks:

- `decommission`
- `argocd_resume`
- `rbac_bootstrap`
