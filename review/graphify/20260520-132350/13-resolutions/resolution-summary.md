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

### RBAC bootstrap generated kubeconfig path is not safety-validated

- Severity: Critical
- Evidence file: review/graphify/20260520-132350/08-rbac/06b-rbac-bootstrap-decommission-query.md; review/graphify/20260520-132350/06-paths/02-sensitive-artifacts.md
- Source files: ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/tasks/generate_kubeconfigs.yml; ansible_collections/tomazb/acm_switchover/plugins/modules/acm_safe_path_validate.py; ansible_collections/tomazb/acm_switchover/tests/unit/test_rbac_bootstrap_tasks.py
- Root cause: The generated service-account kubeconfig destination was derived from operator-controlled output_dir/context/role values and used for directory creation and copy without first applying symlink-aware artifact path validation.
- Bad scenario: A crafted output path or symlinked parent could redirect generated kubeconfig credentials outside the expected artifact tree before operators inspect the output location.
- Fix: Extended acm_safe_path_validate with path_type=artifact and added a validation task before directory creation and kubeconfig copy.
- Tests added/updated: Added test_generate_kubeconfigs_validates_output_path_before_writing_credentials.
- Verification commands: ANSIBLE_LOCAL_TEMP=/tmp/ansible-local pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_rbac_bootstrap_tasks.py -q; ANSIBLE_LOCAL_TEMP=/tmp/ansible-local pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q; coderabbit review --prompt-only -t uncommitted
- Result: Targeted test first failed before the validation task existed, then passed after the fix. Broader collection unit suite passed with 600 tests. CodeRabbit returned only out-of-scope findings for untracked Graphify output/scripts.

### Checkpoint file path uses weaker validation than artifact writes

- Severity: Important
- Evidence file: review/graphify/20260520-132350/03-state/03-checkpoint-resume-invalid-state.md; review/graphify/20260520-132350/06-paths/01-report-dir-path-safety.md
- Source files: ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py; ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py
- Root cause: checkpoint_phase validated checkpoint.path with validate_safe_path, which rejects unsafe syntax and disallowed absolute roots but does not reject relative symlink-parent escapes before controller-side file reads, writes, and corrupt-file quarantine.
- Bad scenario: A relative checkpoint path under a symlinked parent could write or quarantine checkpoint state outside the intended artifact tree.
- Fix: Switched checkpoint_phase to validate_report_artifact_path before any checkpoint load/save path use.
- Tests added/updated: Added test_action_module_rejects_checkpoint_path_relative_symlink_escape.
- Verification commands: ANSIBLE_LOCAL_TEMP=/tmp/ansible-local pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py -q; ANSIBLE_LOCAL_TEMP=/tmp/ansible-local pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q; coderabbit review --prompt-only -t uncommitted
- Result: Targeted test first failed because the plugin proceeded without a validation failure, then passed after the fix. Broader collection unit suite passed with 600 tests. CodeRabbit returned only out-of-scope findings for untracked Graphify output/scripts.

## Confirmed issues not fixed

- None.

## Needs more evidence

- None for the first approved fix batch.

## False positive / graph-only leads

- Wrong-context and wrong-hub mutation leads: source review found explicit kubeconfig/context usage and decommission target assertions.
- Hive preserveOnDelete fail-closed decommission leads: source review found fail-closed implementation and tests.
- Argo CD ApplicationSet and stale status.resources leads: source review found pause blockers and tests.
- RBAC permission separation leads: replacement RBAC evidence and source/tests show validator/operator/decommission separation is covered.
- Report artifact path safety leads: source review found symlink-aware report path validation and tests.
