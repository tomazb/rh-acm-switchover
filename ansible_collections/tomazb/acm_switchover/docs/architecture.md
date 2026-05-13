# Collection Architecture

## Foundations

- collection-first migration
- controller-side execution for both CLI and AAP
- explicit phases as operator-facing boundaries
- stock `kubernetes.core` first
- thin custom plugins for report artifacts, checkpoints, validation, restore planning, RBAC, and safe paths

## Current Boundaries

The collection defines:

- collection layout
- variable contract
- playbook entrypoints
- role boundaries
- schema-versioned report artifacts
- optional checkpoint contract
- RBAC bootstrap and validation contracts
- decommission observability autodetection
- release parity guardrails for report, checkpoint, decommission, restore-only, switchover, and RBAC/bootstrap artifacts

The collection deliberately does not implement full kubeconfig/context enumeration. Use `scripts/discover-hub.sh` for that bridge workflow during coexistence.

## Parity Notes

- Preflight, activation, post-activation, finalization, RBAC validation, Argo CD management, discovery classification, decommission, reports, and checkpoint behavior are dual-supported.
- `kubernetes.core` tasks plus Ansible `until` loops replace Python's `KubeClient` and waiter helpers where they preserve the same operator-facing behavior.
- Constants live in `plugins/module_utils/constants.py`; the collection cannot import Python CLI constants.
