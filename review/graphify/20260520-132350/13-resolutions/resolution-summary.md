# Resolution summary

## Review target

- Base ref: origin/main
- Head ref: origin/ansible
- Base SHA: aca2d296aeb22ec6bf32a2477500dd132b7b59ba
- Head SHA: e1c20860489a4fcb84918e073b01d5d79ff4bf52
- Graphify evidence directory: /home/tomaz/sources/rh-acm-switchover/review/graphify/20260520-132350

## Confirmed issues fixed

### Collection preflight ignored backups still in progress after wait

- Severity: Important
- Evidence file: review/graphify/20260520-132350/02-parity/02-preflight-activation-finalization-parity.md; review/graphify/20260520-132350/09-tests/05-negative-test-gaps.md
- Source files: ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_backups.yml; ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_parity.py
- Root cause: The collection waited for Velero backups to leave InProgress with failed_when=false, refreshed backup facts, but did not add a critical validation finding when backups remained InProgress after retry exhaustion.
- Bad scenario: A stuck older InProgress backup remains after polling while the latest backup is Completed; collection preflight can proceed, diverging from Python preflight fail-closed behavior.
- Fix: Added explicit primary and restore-only post-wait checks that record critical validation results with remaining backup names and wait attempts.
- Tests added/updated: Added test_validate_backups_records_remaining_in_progress_backups_after_wait.
- Verification commands: pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_parity.py -q; pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_parity.py ansible_collections/tomazb/acm_switchover/tests/unit/test_restore_only_recovery_contracts.py ansible_collections/tomazb/acm_switchover/tests/unit/test_ansible_resilience_contracts.py -q; pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q; coderabbit review --prompt-only -t uncommitted
- Result: Targeted test first failed, then passed after the fix. Broader collection unit suite passed with 598 tests. CodeRabbit returned only out-of-scope findings for files absent from this repository.

## Confirmed issues not fixed

### RBAC bootstrap generated kubeconfig path is not safety-validated

- Severity: Critical
- Evidence file: review/graphify/20260520-132350/08-rbac/06b-rbac-bootstrap-decommission-query.md; review/graphify/20260520-132350/06-paths/02-sensitive-artifacts.md
- Source files: ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/tasks/generate_kubeconfigs.yml; ansible_collections/tomazb/acm_switchover/plugins/module_utils/validation.py
- Reason not fixed: Not included in the approved first fix batch.
- Recommended next action: Add a failing test for unsafe output_dir/path handling, then validate the generated kubeconfig destination with the collection safe artifact path policy before directory creation and copy.

### Checkpoint file path uses weaker validation than artifact writes

- Severity: Important
- Evidence file: review/graphify/20260520-132350/03-state/03-checkpoint-resume-invalid-state.md; review/graphify/20260520-132350/06-paths/01-report-dir-path-safety.md
- Source files: ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py; ansible_collections/tomazb/acm_switchover/plugins/module_utils/validation.py
- Reason not fixed: Not included in the approved first fix batch.
- Recommended next action: Add a failing checkpoint action test for relative symlink escape, then use symlink-aware checkpoint path validation before read, write, and corrupt-file quarantine.

## Needs more evidence

- None for the first approved fix batch.

## False positive / graph-only leads

- Wrong-context and wrong-hub mutation leads: source review found explicit kubeconfig/context usage and decommission target assertions.
- Hive preserveOnDelete fail-closed decommission leads: source review found fail-closed implementation and tests.
- Argo CD ApplicationSet and stale status.resources leads: source review found pause blockers and tests.
- RBAC permission separation leads: replacement RBAC evidence and source/tests show validator/operator/decommission separation is covered.
- Report artifact path safety leads: source review found symlink-aware report path validation and tests.
