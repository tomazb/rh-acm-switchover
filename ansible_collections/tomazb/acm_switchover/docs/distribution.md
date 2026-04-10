# Distribution and Packaging Strategy

## Targets

- Ansible Galaxy compatible packaging
- Automation Hub compatible packaging
- execution environment for AAP

## AAP Contract

- same playbooks as local CLI usage
- same variable model as local CLI usage
- survey and `extra_vars` values treated as untrusted input

## Lock Model

Phase 1 defines the rule only:

- local file-backed checkpoints require advisory locking
- shared or controller-backed checkpoints require a Lease-style or equivalent coordination mechanism
- lock failures must be explicit and operator-visible
