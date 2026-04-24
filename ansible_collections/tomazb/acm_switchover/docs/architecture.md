# Collection Architecture

## Foundations

- collection-first migration
- controller-side execution for both CLI and AAP
- explicit phases as operator-facing boundaries
- stock `kubernetes.core` first
- thin custom plugins later, not in Phase 1

## Phase 1 Boundaries

Phase 1 defines:

- collection layout
- variable contract
- playbook entrypoints
- role boundaries
- artifact schema
- lock model

Phase 1 does not implement:

- checkpoint backend code
- custom modules
- action plugins
