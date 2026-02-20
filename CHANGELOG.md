# Changelog

All notable changes to the ACM Switchover Automation project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

### Changed

### Fixed

## [1.5.5] - 2026-02-03

### Changed

- **Docs**: Updated architecture and findings report for GitOps detection/reporting and passive sync phase handling.

### Fixed

- **Argo CD auto-sync pause**: Treat `spec.syncPolicy.automated: {}` as enabled so pause removes auto-sync for those Applications.
- **Argo CD pause scope**: Check Applications CRD per hub to avoid skipping secondary pause when primary lacks Argo CD.
- **Validate-only guard**: Reject `--argocd-resume-only` with `--validate-only` to enforce no-change validation runs.
- **Argo CD install reporting (scripts)**: Avoid labeling operator installs as vanilla and only fall back to cluster-wide scans when no ArgoCD instances are detected.
- **Postflight ACM version detection (scripts)**: Fall back to jsonpath when MCH JSON is unavailable so auto-import strategy check doesn't fail spuriously.
- **Argo CD resume-only**: Return non-success when state lacks `argocd_run_id` or paused apps to avoid false positives.
- **Argo CD resume auto-sync**: Handle unexpected patch errors without propagating exceptions.
- **Preflight GitOps marker collection (scripts)**: Run ClusterDeployment loop in current shell so detections persist.
- **Postflight auto-import strategy (scripts)**: Skip strategy checks when the new hub ACM version is unknown.

## [1.5.4] - 2026-01-31

### Added

- **GitOps marker detection**: Detect ArgoCD/Flux-managed resources and support `--skip-gitops-check` to suppress drift warnings.

### Changed

- **GitOps MCO handling**: Removed per-resource warning during MCO deletion in favor of consolidated reporting.

### Fixed

- **GitOps managed-by matching**: Avoid substring false positives for `app.kubernetes.io/managed-by`.
- **GitOps report counters (bash)**: Prevent `set -e` exits on arithmetic increments.
- **Passive sync phase validation**: Accept `Completed` as valid passive sync restore state alongside `Enabled` and `Finished`.

## [1.5.3] - 2026-01-29

### Fixed

- **Backup integrity recency guard**: Skip backup age enforcement until a new backup is observed after re-enabling the BackupSchedule (avoids false failures on long cadences).
- **Schedule-aware backup timeouts**: Derive backup verification timeouts from BackupSchedule cadence to avoid premature failures on longer schedules.
- **Schedule-aware backup age threshold**: Derive backup age enforcement from BackupSchedule cadence to avoid false failures on long schedules or slow backups.

## [1.5.1] - 2026-01-29

### Changed

- **Restore activation guidance**: Documented deletion propagation wait and `FinishedWithErrors` handling for activation restores.

### Fixed

- **Restore activation race**: Wait for passive restore deletion before creating `restore-acm-activate` and treat
  `FinishedWithErrors`/`FailedWithErrors` as fatal restore phases.

## [1.5.0] - 2026-01-28

### Added

- **Activation method selection**: Added `--activation-method {patch,restore}` to support both passive activation options, including creation of `restore-acm-activate`.
- **Immediate-import annotations**: Automatically annotate non-local ManagedClusters when `autoImportStrategy=ImportOnly` (ACM 2.14+).
- **Optional MCO deletion**: Added `--disable-observability-on-secondary` to delete MultiClusterObservability on the old hub (non-decommission flows).
- **Backup integrity verification**: Finalization now validates latest backup status, age, and Velero logs.
- **Fast polling support**: `wait_for_condition` now supports fast polling intervals for quick operations (e.g., Velero restores).

### Changed

- **Auto-import strategy cleanup guard**: ImportAndSync ConfigMap is removed only when set by this switchover (state flag).
- **Preflight/postflight scripts**: Added immediate-import guidance and backup age/log checks aligned with runbook v2.
- **Timeout behavior**: Post-timeout success is now configurable and disabled by default.

### Fixed

- **Waiter timeout semantics**: Prevents silent success after timeouts unless explicitly enabled.

## [1.4.13] - 2026-01-27

### Changed

- **KISS refactor: `PostActivationVerification.verify()`**: Decomposed 75-line method into three focused sub-methods (`_verify_cluster_connections()`, `_verify_auto_import_cleanup_step()`, `_verify_observability_full()`) for improved readability and testability.

- **KISS refactor: `Finalization._verify_old_hub_state()`**: Extracted 88-line observability scale-down logic into three helper methods (`_scale_down_old_hub_observability()`, `_wait_for_observability_scale_down()`, `_report_observability_scale_down_status()`) for clearer separation of concerns.

- **Backup in-progress timeouts (scripts)**: `BACKUP_IN_PROGRESS_WAIT_SECONDS` and `BACKUP_IN_PROGRESS_POLL_SECONDS` can now be overridden via environment variables to tune preflight wait behavior.

### Fixed

- **Preflight backup in-progress handling**: Preflight now waits for in-progress backups to complete before failing, reducing false negatives during rapid E2E cycles.
- **Token expiration check**: Fixed kubeconfig token expiration check to use the correct Kubernetes client configuration class.
- **Observability pod detection**: Updated observability pod selector to a label that exists on ACM observability pods.
- **BackupSchedule deletion race condition (findings #8)**: Verify schedule UID before deletion, refresh spec from the latest object, and handle 404s cleanly during recreation.
- **Kubeconfig loading performance (findings #10)**: Added caching with mtime-based invalidation to `_load_kubeconfig_data()` in PostActivationVerification. Reduces repeated file I/O during cluster verification.
- **Resource list memory bounds (findings #12)**: Single-item list lookups now pass `max_items=1` (e.g., BackupSchedule, MCH, DPA) to avoid unnecessary pagination.
- **Deletion timeout support (findings #14)**: `delete_custom_resource()` supports request timeouts and decommission/finalization deletes now pass timeouts to avoid hanging API calls.

### Validated

- **Real-cluster preflight (mgmt1/mgmt2)**: `discover-hub.sh --auto --run` verified both hubs on ACM 2.14.1 / OCP 4.19.21 with 38/38 preflight checks passing (2026-01-28).

## [1.4.11] - 2026-01-19

### Added

- **StateManager write optimization**: Implemented dirty state tracking with `save_state()` (conditional writes) and `flush_state()` (critical checkpoints) to reduce disk I/O.

- **Automatic state protection**: Added signal handlers (SIGTERM/SIGINT) and atexit handlers to flush dirty state on program termination, preventing data loss even on unexpected exits. Includes temporary file cleanup to prevent orphaned files.

- **KubeClient `get_statefulset()` method**: Added new method to retrieve StatefulSet resources by name and namespace, complementing existing `get_deployment()` method.

- **Kubeconfig size limit**: Added `MAX_KUBECONFIG_SIZE` constant (10MB default, configurable via `ACM_KUBECONFIG_MAX_SIZE` environment variable) to prevent memory exhaustion when loading large kubeconfig files.

- **Backup schedule caching in finalization**: Implemented caching for backup schedule lookups in `Finalization` module to reduce redundant API calls during verification steps.

- **Enhanced patch verification**: Improved patch verification in activation module with better error messages distinguishing between API caching issues and incorrect patch values. Added tracking of resourceVersion changes for more accurate diagnostics.

### Changed

- **Preflight script node checking**: Optimized node health checking in `preflight-check.sh` to use single JSON API call instead of multiple `oc get` commands, reducing API calls and improving performance. Improved ClusterOperator checking with cached JSON output.

- **Preflight script variable naming**: Improved variable naming in `preflight-check.sh` to avoid conflicts (e.g., `ACM_PRIMARY_VERSION` vs `PRIMARY_VERSION`, `PRIMARY_OCP_VERSION` vs `PRIMARY_VERSION`).

- **Preflight script multiple BackupSchedule handling**: Added warning when multiple BackupSchedules are detected, checking the first one only.

### Fixed

- **State durability on termination**: Persist step completion and config updates immediately to avoid losing dirty state between phase transitions or during abrupt termination.

- **Klusterlet verification kubeconfig coverage**: Added regression coverage to ensure size limits are bypassed for klusterlet verification, preventing large kubeconfigs from being skipped.

- **State persistence in tests**: Updated test cases to use `flush_state()` for critical checkpoints and `save_state()` for non-critical updates, ensuring proper state persistence testing.

## [1.4.10] - 2026-01-05

### Added

- **Python E2E Orchestrator (Phase 1)**: Replaced bash-based E2E orchestration with pytest-native `E2EOrchestrator` class featuring automated context swapping, per-cycle manifest generation, and structured timing instrumentation. Added 56 CI-friendly dry-run tests that validate workflow without cluster changes.

- **Soak Testing Controls (Phase 2)**: Added enterprise-grade soak testing capabilities with `--run-hours` (time-boxed execution), `--max-failures` (stop after N failures), and `--resume` (continue from last successful cycle). Implemented 570-line `ResourceMonitor` for background polling of ManagedClusters, Backups, Restores, and Observability components with alert detection.

- **JSONL Metrics Export (Phase 2)**: Added `MetricsLogger` with streaming JSONL time-series output capturing `cycle_start`, `cycle_end`, `phase_result`, `resource_snapshot`, and `alert` events. Enables run-to-run analysis with P50/P90/P95 percentile calculations.

- **Failure Injection Framework (Phase 3)**: Implemented `FailureInjector` chaos engineering toolkit with 4 scenarios (`pause-backup`, `scale-down-velero`, `kill-observability-pod`, `random`) for resilience testing. Added 22 resilience tests validating recovery from injected failures.

- **Enhanced E2E Test Coverage**: Total of 99+ E2E tests across 4 test suites including monitoring tests (21 tests) and resilience tests (22 tests). All tests use pytest markers (`@pytest.mark.e2e`, `@pytest.mark.e2e_dry_run`) for selective execution.

### Changed

- **E2E Test Infrastructure**: Migrated from bash (`quick_start_e2e.sh`, `e2e_test_orchestrator.sh`) to pytest-native execution with `pytest -m e2e tests/e2e/`. Enhanced analyzer supports `--compare` mode for run-to-run analysis and graceful degradation without pandas/matplotlib.

### Deprecated

- **Bash E2E Scripts**: `quick_start_e2e.sh`, `e2e_test_orchestrator.sh`, and `phase_monitor.sh` are deprecated and will be removed in v2.0.0. Migration guide available in `tests/e2e/MIGRATION.md`. Scripts now emit deprecation warnings when executed.

### Fixed

- **E2E Resume State**: Fixed bug where `--resume` didn't preserve `swap_contexts_each_cycle` flag, causing incorrect context usage after process restart (commit `82ddb66`).

### Validated

- **4h40m Real-World Soak Test**: Validated on live ACM clusters (mgmt1↔mgmt2, ACM 2.12.7, OCP 4.16.54) with 46 completed cycles achieving 84.8% success rate. Confirmed resume capability (recovered from crash at cycle 4), 100% success for cycles 16-35 after initial timing races, and captured 300+ JSONL events. 

## [1.4.9] - 2026-01-03

### Added

- **`discover-hub.sh` context deduplication and API server display**: The hub discovery script now detects when multiple kubeconfig contexts point to the same cluster (by comparing API server URLs), groups them together in output, and displays the API server URL for each unique hub. Uses the shortest context name as the canonical name for proposed commands. Includes RBAC validation hints suggesting `check_rbac.py` commands.

### Fixed

- **Finalization respects `--old-hub-action none`**: Fixed regression where `_verify_old_hub_state()` was unconditionally scaling down observability components (thanos-compact, observatorium-api) even when `--old-hub-action none` was specified. Now properly skips old hub modifications when action is `none`, honoring the documented "leaves it unchanged for manual handling" contract.

- **ManagedClusterBackupValidator timestamp comparison**: Restored missing logic that compares each joined ManagedCluster's `creationTimestamp` against the latest backup's `completionTimestamp`. Clusters imported after the last backup now cause a **critical preflight failure** (not just a warning), preventing data loss during switchover.

- **`--force` properly resets COMPLETED state**: Fixed issue where using `--force` with a stale COMPLETED state would silently no-op instead of re-running the switchover. Now resets phase to INIT when `--force` is used with stale completed state, ensuring all phases execute.

- **Dry-run reporting for observability pod cleanup**: Fixed misleading log messages when `dry_run=True` that incorrectly reported observability components as "scaled down" even though no scaling occurred. Now properly reports `[DRY-RUN] Would scale down...` messages instead.

- **Token expiring soon is now a warning, not failure**: Reverted to pre-refactor behavior where tokens expiring within 4 hours produce a non-critical warning (`passed=True, critical=False`) instead of failing preflight. Only already-expired tokens cause critical failures.

## [1.4.8] - 2025-12-30

### Added

- **Modular pre-flight validation architecture**: Decomposed monolithic `preflight_validators.py` (1,282 lines) into focused modules under `modules/preflight/` with `BaseValidator` class and `ValidationReporter` for extensible validation framework.

- **New validator modules**: Created `backup_validators.py`, `cluster_validators.py`, `namespace_validators.py`, `version_validators.py`, and `reporter.py` with clear separation of concerns.

- **`PreflightValidator` coordinator**: New orchestrator class in `modules/preflight_coordinator.py` for managing modular validators.

- **Modular preflight tests**: Added `tests/test_preflight_modular.py`, `tests/test_preflight_backward_compat.py`, and `tests/test_preflight_validators_unit.py` for comprehensive testing of new structure.

- **Consolidated `@api_call` decorator in `KubeClient`**: Added new `@api_call` decorator that combines retry logic with standard exception handling (404 → return value, 5xx/429 → retry, other → log and re-raise). Refactored 8 methods to use this decorator, reducing ~60 lines of repetitive try/except blocks.

### Changed

- **`modules/preflight_validators.py` deprecated**: Now a backward-compatibility shim that imports from `modules.preflight` and emits `DeprecationWarning` on import.

- **Updated architecture documentation**: Refreshed `docs/development/architecture.md` and `docs/project/prd.md` to reflect the new modular preflight structure.

## [1.4.7] - 2025-12-26

### Added

- **RBAC bootstrap script (`setup-rbac.sh`)**: New automated script that deploys RBAC manifests, generates SA kubeconfigs with unique user names, and validates permissions using `check_rbac.py` - all in one command. Requires explicit `--admin-kubeconfig` flag for safety.

- **Merged kubeconfig generator (`generate-merged-kubeconfig.sh`)**: New script that generates and merges kubeconfigs for multiple clusters/contexts into a single file. Accepts comma-separated `context:role` pairs (e.g., `hub1:operator,hub2:operator`) and creates unique user names to prevent credential collisions.

- **`--user` and `--token-duration` flags for `generate-sa-kubeconfig.sh`**: Enhanced the kubeconfig generator with `--user <name>` flag to specify custom user names (default: `<context>-<sa-name>`) and `--token-duration <dur>` flag (default: 48h) for explicit token lifetime control. This prevents credential collisions when merging kubeconfigs from multiple clusters.

- **`--setup` mode for `acm_switchover.py`**: Added `--setup` flag as a mutually exclusive mode that orchestrates RBAC deployment and kubeconfig generation using the bootstrap scripts. Requires `--admin-kubeconfig` flag.

- **Kubeconfig validation in preflight**: New `KubeconfigValidator` that checks for duplicate user credentials across merged configs, expired/near-expiry SA tokens (parsed from JWT), and API connectivity issues - with actionable remediation messages.

### Changed

- **Default token duration increased to 48h**: Changed the default token validity from 24h to 48h in `generate-sa-kubeconfig.sh` to accommodate longer switchover operations on large clusters.

- **Updated documentation**: Enhanced `docs/deployment/rbac-deployment.md` with new "Automated Setup" quick start section and comprehensive documentation for new scripts and flags. Updated `scripts/README.md` with documentation for `setup-rbac.sh` and `generate-merged-kubeconfig.sh`.

### Notes

- Version 1.5.x is reserved for packaging and distribution work.

## [1.4.6] - 2025-12-25

### Fixed

- **State file writes serialized with file locking**: Added best-effort locking around state writes to reduce concurrent write clobbering.

- **Context reset for missing contexts in in-progress state**: Prevents resuming with stale state when contexts were never persisted.

- **Auto-import strategy flag cleared after reset**: Avoids repeated cleanup on later runs after restoring the default strategy.

## [1.4.5] - 2025-12-23

### Added

- **Role-aware RBAC validation with `--role` flag**: The `check_rbac.py` tool now supports `--role operator` (default) and `--role validator` flags to validate permissions for the appropriate service account role. Validators have read-only access while operators have full switchover permissions.

- **Managed cluster RBAC Policy**: New ACM Policy (`deploy/acm-policies/policy-managed-cluster-rbac.yaml`) to automatically deploy RBAC resources to managed clusters for klusterlet reconnection operations. Includes both operator (full access) and validator (read-only) roles in the `open-cluster-management-agent` namespace.

- **Managed cluster RBAC validation with `--managed-cluster` flag**: The `check_rbac.py` tool now supports `--managed-cluster` flag to validate permissions in the `open-cluster-management-agent` namespace on spoke clusters. This enables validation of RBAC deployed via ACM Policy on managed clusters.

- **Validator secrets access in backup namespace**: Added `secrets:get` permission for the validator role in the backup namespace, enabling validators to read backup-related secrets for validation purposes.

### Fixed

- **Missing `statefulsets/scale` permission for Thanos compactor**: Fixed RBAC to include the `/scale` subresource permission for StatefulSets in the observability namespace. The Kubernetes Python client uses `patch_namespaced_stateful_set_scale()` which requires this subresource permission.

- **Policy name length compliance**: Shortened managed cluster RBAC policy name from `policy-acm-switchover-managed-cluster-rbac` to `policy-switchover-mc-rbac` to comply with the 62-character limit for ACM policy names.

### Changed

- **RBAC documentation updates**: Enhanced `docs/deployment/rbac-deployment.md` with kubeconfig merge best practices, guidance on generating unique user names to prevent credential collisions, and troubleshooting section for ACM governance addon issues.

## [1.4.4] - 2025-12-23

### Fixed

- **Activation idempotent when already completed**: Fixed an issue where the tool would fail patch verification if `veleroManagedClustersBackupName` was already set to `latest` from a previous run. The tool now recognizes this as a valid idempotent state and skips the patch, allowing resume scenarios to succeed.

- **Klusterlet fix triggered automatically when clusters don't connect**: Improved post-activation verification to automatically fix klusterlet connections when managed clusters don't connect after a brief initial wait (120s). Previously, the tool would wait the full 600s timeout and fail. Now it checks if klusterlets are pointing to the old hub and applies import manifests to update them before waiting again. This is especially important for switchovers where `useManagedServiceAccount` may not have been configured on previous backups.

## [1.4.3] - 2025-12-22

### Added

- **BackupSchedule useManagedServiceAccount preflight check (CRITICAL)**: Added a new preflight check that validates the `useManagedServiceAccount` setting in the BackupSchedule resource. This setting is critical for the passive sync method - when enabled, the hub creates a ManagedServiceAccount for each managed cluster, allowing klusterlet agents to automatically reconnect to the new hub after switchover. Without this setting, managed clusters would require manual re-import because the klusterlet bootstrap-hub-kubeconfig still points to the old hub. Both Python preflight validation and bash `preflight-check.sh` (Check 10) now verify this setting.

### Changed

- **Renumbered preflight checks**: The new BackupSchedule check is Check 10, shifting ClusterDeployment (now 11), Method-specific (12), Observability (13), Secondary Hub MCs (14), and Auto-Import Strategy (15) accordingly.

## [1.4.2] - 2025-12-22

### Fixed

- **Decommission fails with "ManagedCluster resource(s) exist" error**: Fixed a race condition in the decommission workflow where the MultiClusterHub deletion was attempted before ManagedCluster finalizers completed. The MCH admission webhook would reject the deletion because ManagedClusters were still present (in deletion but not fully removed). The tool now waits up to 300 seconds for all ManagedClusters (except local-cluster) to be fully deleted before attempting to delete the MultiClusterHub.

- **Decommission considers operator pods as failure**: Fixed an issue where the decommission completion check would report failure if ACM operator pods (`multiclusterhub-operator-*`) were still running. These pods are expected to remain after MCH deletion because the operator is installed separately from the MultiClusterHub. The tool now excludes operator pods from the removal check and logs a clear message that they remain as expected.

## [1.4.1] - 2025-12-22

### Fixed

- **BackupSchedule not created on new hub in passive sync mode**: Fixed a bug where the finalization phase would fail with "No BackupSchedule found while verifying finalization" when the secondary hub was in passive sync mode (had only a Restore resource, no BackupSchedule). The tool now saves the BackupSchedule from the primary hub during PRIMARY_PREP phase (for all ACM versions, not just 2.11), allowing it to be restored on the new hub during finalization.

## [1.4.0] - 2025-12-22

### Added

- **Parallel cluster verification**: Klusterlet connection verification and reconnection now run in parallel using `ThreadPoolExecutor` with up to 10 concurrent workers (configurable via `CLUSTER_VERIFY_MAX_WORKERS`). This significantly speeds up post-activation for environments with many managed clusters.

- **New timeout constants** in `lib/constants.py`:
  - `OBSERVABILITY_POD_TIMEOUT` (300s) - Observability pod readiness wait
  - `VELERO_RESTORE_TIMEOUT` (300s) - Velero restore wait
  - `SECRET_VISIBILITY_TIMEOUT` (10s) - Bootstrap secret visibility polling
  - `SECRET_VISIBILITY_INTERVAL` (1s) - Polling interval for secret checks
  - `CLUSTER_VERIFY_MAX_WORKERS` (10) - Max parallel workers for cluster verification

### Changed

- **Replaced `time.sleep()` with proper polling**: The klusterlet reconnect flow now uses `wait_for_condition()` to poll for the `bootstrap-hub-kubeconfig` secret visibility instead of a hardcoded 2-second sleep.

- Hardcoded timeout values in `post_activation.py` now use centralized constants from `lib/constants.py`.

- **`discover-hub.sh` now displays OCP version and update channel**:
  - For ACM hubs: shows OCP version and channel alongside ACM version in Discovered ACM Hubs section
  - For non-ACM clusters: displays OCP version and channel in the "not an ACM hub (skipped)" message
  - Uses OpenShift ClusterVersion resource with fallback to server version for non-OCP clusters

## [1.3.3] - 2025-12-15

### Fixed

- Security: pin `urllib3>=2.5.0` to address CVE-2025-50181 and CVE-2025-50182.

### Changed

- Replace Safety CLI with `pip-audit` in `run_tests.sh` (avoids interactive auth prompts and deprecated `safety check`).

## [1.3.2] - 2025-12-15

### Added

- State file defaults now honor `ACM_SWITCHOVER_STATE_DIR` when `--state-file` is not provided; explicit `--state-file` always takes precedence. The state viewer aligns with the same default, and docs were updated to describe the order.

- **Atomic state file persistence**: State files are now written atomically using a temp-file + rename pattern. If the process crashes during write, the previous valid state is preserved instead of leaving a corrupted file.

- **KUBECONFIG multi-file support**: The `_load_kubeconfig_data()` method now properly handles colon-separated `KUBECONFIG` paths (e.g., `/path/one:/path/two`), merging contexts, clusters, and users from all files. Missing files are gracefully skipped.

- **Standardized dry-run handling**: Converted 8 methods across `finalization.py` and `post_activation.py` to use the `@dry_run_skip` decorator for consistent dry-run behavior:
  - `_verify_new_backups`
  - `_verify_backup_schedule_enabled`
  - `_verify_multiclusterhub_health`
  - `_decommission_old_hub`
  - `_setup_old_hub_as_secondary`
  - `_fix_backup_schedule_collision`
  - `_verify_managed_clusters_connected`
  - `_verify_klusterlet_connections`

### Changed

- Improved state file write safety with `fsync()` before atomic rename
- Better error handling for kubeconfig file loading edge cases

- Scripts use fully qualified API group names for `oc`/`kubectl` resources to avoid ambiguity.

### Fixed

- Security: sanitize kubeconfig path logging.
- Security: avoid logging secret identifiers (CodeQL-driven hardening).

#### Documentation

- Added RBAC section to main README
- Updated prerequisites to include RBAC permissions
- Added links to RBAC deployment guides

- Runbook clarifications around re-enabling Observatorium API and rollback guidance.

## [1.3.1] - 2025-12-11

### Added

#### Script Version Tracking

- **Version display in all scripts**: Scripts now display version number in output header for troubleshooting
  - `preflight-check.sh v1.3.1 (2025-12-11)` shown after banner
  - `postflight-check.sh v1.3.1 (2025-12-11)` shown after banner
  - `discover-hub.sh v1.3.1 (2025-12-11)` shown after banner
- **Version constants in `constants.sh`**:
  - `SCRIPT_VERSION="1.3.1"` - Semantic version number
  - `SCRIPT_VERSION_DATE="2025-12-11"` - Version release date
- **Version helper functions in `lib-common.sh`**:
  - `print_script_version()` - Print version line for script headers

#### Python Version Tracking

- **Version constants in `lib/__init__.py`**:
  - `__version__ = "1.3.1"` - Semantic version number
  - `__version_date__ = "2025-12-11"` - Version release date
- **Version display at startup**: `ACM Hub Switchover Automation v1.3.1 (2025-12-11)`
- **Version stored in state files**: `tool_version` field added to state JSON for troubleshooting

#### Hub Summary Section in Preflight

- **New Hub Summary display**: After ACM version checks, shows a clear summary of both hubs:
  ```
  Hub Summary
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    ● primary-hub
      Role:     primary
      Version:  2.11.8
      Clusters: 5/5 (available/total)
      State:    Active primary hub (BackupSchedule running)

    ● secondary-hub
      Role:     secondary
      Version:  2.11.8
      Clusters: 0/8 (available/total)
      State:    Secondary hub (clusters in Unknown state)
  ```
- **New helper functions in `lib-common.sh`**:
  - `get_total_mc_count()` - Get total managed cluster count (excluding local-cluster)
  - `get_available_mc_count()` - Get count of available/connected managed clusters
  - `get_backup_schedule_state()` - Get BackupSchedule state (running/paused/none)
  - `print_hub_summary()` - Print a hub summary card with role, version, clusters, state

#### Improved Secondary Hub Managed Cluster Check

- **New Section 13: "Checking Secondary Hub Managed Clusters"**: Separate check for pre-existing clusters
  - Shows available/total count (e.g., "0/8 available") for better visibility
  - Specific messaging based on cluster state (all Unknown, some Unknown, all available)
  - Runs for all ACM versions (not just 2.14+)
- **Section 14: Auto-Import Strategy** now only shows for ACM 2.14+
  - Clear skip message for older versions: "ACM 2.11.8 (autoImportStrategy not applicable, requires 2.14+)"

### Fixed

- **Managed cluster count parsing**: Fixed issue where cluster count could include newlines causing arithmetic errors
  - Changed `grep -cv` to `grep -v | wc -l` for more reliable counting
  - Added `tr -d '[:space:]'` to sanitize output
  - Added `${count:-0}` fallback for empty results

 
#### Documentation

- Added RBAC section to main README
- Updated prerequisites to include RBAC permissions
- Added links to RBAC deployment guides

#### Preflight Script Enhancements

- **Check 7: BackupStorageLocation validation**: Verifies BSL is in "Available" phase on both hubs before switchover
- **Check 8: Cluster Health validation**: Comprehensive cluster health checks per runbook requirements
  - Verifies all nodes are in Ready state on both hubs
  - Checks ClusterOperators are healthy (Available=True, Degraded=False) on OpenShift clusters
  - Validates no cluster upgrade is in progress via ClusterVersion status
  - Displays cluster version information
- Added RBAC permissions for new checks: `nodes`, `clusteroperators`, `clusterversions`, `backupstoragelocations`

 
#### Postflight Script Enhancements  

- **Check 5b: BackupStorageLocation validation**: Verifies BSL is "Available" on new hub after switchover

 
- #### KubeClient Improvements

- **`get_secret()` method**: New method to retrieve Kubernetes secrets with proper validation, retry logic, and 404→None handling
- **Per-instance TLS configuration**: Each KubeClient instance now uses its own Configuration object, preventing `--disable-hostname-verification` from affecting other clients process-wide

 
- #### Patch Verification Improvements

- **Retry loop with resourceVersion**: Patch verification now uses a bounded retry loop (5 attempts) instead of a single sleep, comparing `resourceVersion` to detect when the API has processed the patch
- **Better error messages**: Patch verification errors now include resourceVersion information for debugging

- #### Test Coverage

- Added unit tests for `get_secret()` and `get_secret_not_found` scenarios
- Added tests for `_force_klusterlet_reconnect` functionality

### Fixed

#### Security & Robustness

- **Nested retry prevention**: Removed `@retry_api_call` decorator from `secret_exists()` to avoid 5×5=25 retry attempts (it calls `get_secret()` which already has retries)
- **Explicit boolean check in dry-run decorator**: Changed from truthy check (`if obj:`) to explicit (`if obj is True:`) to prevent skipping execution when dot-path resolves to a truthy non-boolean object
- **YAML import safety**: Moved `import yaml` to module scope in `post_activation.py` to prevent `NameError` if import fails inside try block
- **Missing ApiException import**: Added missing import in `primary_prep.py` that would cause `NameError` at runtime
- **Consistent error logging**: Replaced `print()` with `logger.error()` in validation error handling for proper JSON logging support

#### Error Handling

- **Proper ApiException checks**: Changed from substring matching (`"not found" in str(e)`) to explicit status code checks (`e.status == 404`) in `primary_prep.py`
- **Domain-specific exceptions**: Replaced generic `Exception` raises with `SwitchoverError` in `post_activation.py` for better error taxonomy

#### Performance

- **Label selectors for pod queries**: Added `label_selector="app.kubernetes.io/part-of=observability"` when querying observability pods to reduce data volume

### Changed

#### Constants Centralization

- Added `THANOS_COMPACTOR_STATEFULSET` and `THANOS_COMPACTOR_LABEL_SELECTOR` to `lib/constants.py`
- Added `PATCH_VERIFY_MAX_RETRIES` and `PATCH_VERIFY_RETRY_DELAY` constants
- Replaced hard-coded namespace/name strings in `primary_prep.py` with constants

#### Path Validation

- Expanded allowed absolute paths to include current working directory and `$HOME` in addition to `/tmp` and `/var`
- Updated `K8S_NAME_PATTERN` to require first segment start with a letter (stricter DNS-1123 compliance)

### Documentation

- **TLS hostname verification**: Added dedicated section in `SECURITY.md` documenting security implications and recommendations for `--disable-hostname-verification`
- **Path validation rules**: Updated `docs/reference/validation-rules.md` with expanded path allowances

## [1.3.0] - 2025-12-03

### Added

#### ACM Version Display in Hub Discovery

- **Enhanced discover-hub.sh output**: Now displays ACM version for each discovered hub during analysis
  - Shows version inline with detection message: `ACM hub detected (version 2.11.8)`
  - Stores version in `HUB_VERSIONS` array for potential future use
  - Helps quickly identify version mismatches across hubs

#### Auto-Import Strategy Validation (ACM 2.14+)
- **New preflight check (Check 11)**: Validates `autoImportStrategy` configuration on both hubs
  - Warns if non-default strategy is configured (should be temporary)
  - For secondary hubs with existing managed clusters: provides guidance to temporarily change to `ImportAndSync` before restore
  - Links to official Red Hat documentation for explanation
- **New postflight check (Check 9)**: Ensures `autoImportStrategy` is reset to default after switchover
  - Warns if non-default strategy remains configured
  - Provides command to reset to default
- **New constants**: Added `MCE_NAMESPACE`, `IMPORT_CONTROLLER_CONFIGMAP`, `AUTO_IMPORT_STRATEGY_*` constants
- **New helper functions**: Added `get_auto_import_strategy()` and `is_acm_214_or_higher()` to `lib-common.sh`
- **Documentation**: Updated runbook prerequisites and verification checklist for ACM 2.14+ autoImportStrategy

#### RBAC Model and Security

- **Comprehensive RBAC model**: Complete role-based access control for least privilege access
  - `docs/deployment/rbac-requirements.md`: Detailed RBAC requirements documentation
  - `docs/deployment/rbac-deployment.md`: Step-by-step deployment guide
  - `lib/rbac_validator.py`: RBAC permission validation module
  - `check_rbac.py`: Standalone RBAC checker tool

#### Deployment Options

- **Kustomize integration**: Base and overlay configurations in `deploy/kustomize/`
  - Base RBAC manifests for all environments
  - Production and development overlays
  - Comprehensive Kustomize README
  
- **Helm chart**: Full-featured Helm chart in `deploy/helm/acm-switchover-rbac/`
  - Templated RBAC resources
  - Configurable values for all settings
  - Production-ready defaults
  - Detailed Helm chart README

### Breaking Changes

#### Python 3.9+ Required

- **Minimum Python version**: Python 3.9 is now the minimum supported version
- **Package enforcement**: Added `python_requires=">=3.9"` to `setup.cfg` so pip will refuse installation on older Python versions
- **Migration**: Users on Python 3.8 or earlier must upgrade. See [Migrating from Python 3.8 or Earlier](#migrating-from-python-38-or-earlier) in the Migration Guides section below.

### Removed

#### Rollback Feature

- **Removed `--rollback` CLI option**: The automated rollback feature has been removed as it was complex and error-prone
- **Removed `modules/rollback.py`**: Rollback module and associated tests removed
- **Removed `ROLLBACK` phase**: No longer tracked in state management

### Changed

#### Reverse Switchover (Replaces Rollback)

- **Recommended approach**: To return to the original hub, perform a reverse switchover by swapping `--primary-context` and `--secondary-context` values
- **`--old-hub-action secondary` emphasized**: This option is now marked as **recommended** as it enables seamless reverse switchover by setting up passive sync on the old hub
- **Documentation updated**: All rollback references replaced with reverse switchover guidance

#### Passive Sync Restore Discovery

- **Dynamic restore discovery**: The passive sync restore is now discovered dynamically by looking for a Restore with `spec.syncRestoreWithNewBackups=true` instead of requiring a hardcoded name
- **Backward compatibility**: Falls back to well-known name `restore-acm-passive-sync` if no restore with `syncRestoreWithNewBackups=true` is found
- **Finalization cleanup**: During finalization, all Restore resources in the backup namespace are now listed and cleaned up dynamically

### Fixed

#### Hub Discovery Script
- **Fixed duplicate output in get_total_mc_count**: Changed from `grep -c -v` with `|| echo "0"` fallback to `grep -v | wc -l` to prevent duplicate "0" output when no managed clusters exist
  - Previously, `grep -c` would output "0" and exit with status 1, triggering the fallback which added another "0"
  - This caused a stray "0" line to appear in the hub discovery output for hubs with zero managed clusters

### Added

#### Hub Discovery Improvements
- **Klusterlet verification**: When both hubs report clusters as available (during transition period), the script now verifies actual klusterlet connections by checking `hub-kubeconfig-secret` on each managed cluster
- **`--verbose` option**: Show detailed cluster status for each hub
- **`--auto` option documentation**: Clarified that `--auto` is required for auto-discovery

#### Post-Activation Verification
- **Klusterlet connection verification**: Python tool now verifies that klusterlet agents on managed clusters are connected to the new hub (non-blocking, requires managed cluster contexts in kubeconfig)
- **Automatic klusterlet reconnection**: When a managed cluster's klusterlet is connected to the wrong hub (can happen when passive sync restores cluster resources to both hubs), the tool automatically fixes this by:
  1. Deleting the `bootstrap-hub-kubeconfig` secret on the managed cluster
  2. Re-applying the import manifest from the new hub to recreate the secret
  3. Restarting the klusterlet deployment to pick up the new hub connection

#### Finalization Improvements
- **Proactive BackupSchedule recreation**: Changed from reactive collision detection to proactive recreation during switchover. The BackupSchedule is now always recreated to prevent the race condition where `BackupCollision` appears after Velero schedules run.

### Fixed

#### Dry-Run Mode
- Fixed dry-run mode to properly skip all verification waits:
  - `_wait_for_restore_completion()` in activation module
  - `_verify_managed_clusters_connected()` in post-activation module  
  - `_verify_disable_auto_import_cleared()` in post-activation module
  - `_verify_multiclusterhub_health()` in finalization module
- All skipped operations now log `[DRY-RUN]` messages for visibility
- Fixed unit tests to properly mock `dry_run=False` to prevent Mock objects from being truthy

## [1.2.0] - 2025-11-27

### Added

#### Pre-flight Validation
- **ManagedClusterBackupValidator**: New validation to ensure all joined ManagedClusters are included in the latest backup before switchover. Prevents data loss when clusters were imported after the last backup.

#### Old Hub Handling
- **`--old-hub-action` parameter** (REQUIRED): Explicit choice for handling old primary hub after switchover:
  - `secondary`: Set up old hub with passive sync restore for failback capability
  - `decommission`: Remove ACM components from old hub automatically
  - `none`: Leave old hub unchanged for manual handling

#### Finalization Improvements
- **BackupSchedule collision fix**: Automatically detect and fix `BackupCollision` state by recreating the BackupSchedule
- **Passive sync setup**: Automatically create passive sync restore on old hub for failback capability
- **Restore cleanup**: Delete active restore resources before enabling BackupSchedule (required by ACM backup operator)

#### Dry-Run Support
- Comprehensive dry-run support across all modules:
  - `PrimaryPreparation`: Skip Thanos pod verification wait
  - `BackupScheduleManager`: Log schedule modifications
  - `Decommission`: Log all delete operations without making changes
  - `Finalization`: Full dry-run support for old hub handling

#### Activation Improvements  
- **Patch verification**: Verify that passive sync restore patch was actually applied by re-reading the resource
- **Detailed logging**: Added debug logging to `KubeClient.patch_custom_resource` for troubleshooting
- **Passive sync state**: Accept both "Enabled" and "Finished" states as valid for activation

### Changed

- `--old-hub-action` is now a **required** parameter (no default) to force explicit user choice
- `--method` is now a **required** parameter (no default) to force explicit switchover method choice
- All modules now consistently support dry-run mode with clear `[DRY-RUN]` log messages
- Improved error messages with more context for troubleshooting

#### Code Quality & Refactoring
- **Centralized constants**: Extracted magic strings to `lib/constants.py` for better maintainability:
  - `RESTORE_PASSIVE_SYNC_NAME`, `RESTORE_FULL_NAME`, `BACKUP_SCHEDULE_DEFAULT_NAME`
  - `SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME`, `SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS`
  - `VELERO_BACKUP_LATEST`, `VELERO_BACKUP_SKIP`
- **Dry-run decorator**: New `dry_run_skip` decorator in `lib/utils.py` for consistent dry-run handling
- Updated `modules/activation.py`, `modules/finalization.py`, `modules/backup_schedule.py` to use centralized constants

#### Shell Scripts
- `scripts/preflight-check.sh`: `--method` is now a required parameter (no default)
- `scripts/preflight-check.sh`: Added ManagedClusterBackup validation using timestamp comparison
- `scripts/postflight-check.sh`: Added BackupSchedule collision detection (`BackupCollision` state)
- `scripts/postflight-check.sh`: Added passive sync restore check on old hub for failback capability
- `scripts/postflight-check.sh`: Added old hub ACM decommission status check

### Fixed

- Fixed passive sync validation to accept "Finished" state (not just "Enabled")
- Fixed dry-run mode not being passed to all sub-modules
- Fixed ManagedClusterBackup validation in shell script (was using wrong backup metadata)

## [1.1.0] - 2025-11-19

### Changed

#### Directory Structure

- Moved container build files (`Containerfile`, `get-pip.py`, `.containerignore`) to `container-bootstrap/` directory to declutter root.
- Moved container documentation to `docs/` folder.

#### Scripts

- Refactored `scripts/preflight-check.sh` and `scripts/postflight-check.sh` to use shared configuration from `scripts/constants.sh`.
- Improved maintainability by centralizing color codes and common variables.

#### Testing

- Updated `tests/test_scripts_integration.py` to support dynamic restore finding and better observability mocking.
- Enhanced test coverage for script integration.

#### Documentation

- Updated `scripts/README.md` with corrected Mermaid diagrams for preflight and postflight workflows.
- Updated `docs/getting-started/container.md` to reflect new build context location.
- Updated `.github/workflows/container-build.yml` to use `container-bootstrap/` context.

### Added

- `scripts/constants.sh` for shared script variables.
- `container-bootstrap/` directory for container build artifacts.

## [1.0.0] - 2025-11-18

### Added

#### Core Features
- Complete automation of ACM hub switchover from primary to secondary
- Idempotent execution with JSON state tracking
- Resume capability from last successful step after interruption
- Support for two switchover methods:
  - Method 1: Continuous passive restore (recommended)
  - Method 2: One-time full restore
- Comprehensive pre-flight validation with 15+ checks
- Reverse switchover capability (swap contexts to return to original hub)
- Interactive decommission mode for old hub cleanup

#### Safety & Validation
- Critical validation for ClusterDeployment `preserveOnDelete=true`
- Backup status and completion verification
- ACM version matching between hubs
- OADP operator and DataProtectionApplication checks
- Passive sync restore verification (Method 1)
- Data protection measures throughout workflow

#### Auto-Detection
- ACM version detection (2.11 vs 2.12+)
- Version-specific BackupSchedule handling (pause vs delete)
- Automatic Observability detection and graceful handling
- Optional component detection with skip capability

#### Operational Features
- Dry-run mode to preview actions without execution
- Validate-only mode for pre-flight checks without changes
- Verbose logging for debugging and audit trail
- Custom state file support for parallel switchovers
- State reset capability
- Non-interactive mode for automation

#### Modules
- `preflight.py` - Pre-flight validation (366 lines)
- `primary_prep.py` - Primary hub preparation (143 lines)
- `activation.py` - Secondary hub activation (169 lines)
- `post_activation.py` - Post-activation verification (218 lines)
- `finalization.py` - Finalization and old hub handling (237 lines)
- `decommission.py` - Old hub decommission (144 lines)

#### Libraries
- `utils.py` - State management, logging, helpers (203 lines)
- `kube_client.py` - Kubernetes API wrapper (358 lines)

#### Documentation
- README.md - Project overview
- docs/README.md - Documentation index
- docs/operations/quickref.md - Command reference card
- docs/operations/usage.md - Detailed usage examples and scenarios
- docs/development/architecture.md - Design and implementation details
- docs/getting-started/install.md - Installation and deployment guide
- docs/getting-started/container.md - Container usage guide
- CONTRIBUTING.md - Development guidelines
- docs/project/summary.md - Comprehensive project summary
- docs/project/prd.md - Product requirements document

#### Tools
- `quick-start.sh` - Interactive setup wizard
- Main script with comprehensive CLI (318 lines)

### Technical Details

#### Dependencies
- kubernetes>=28.0.0 - Kubernetes API client
- PyYAML>=6.0 - YAML parsing
- rich>=13.0.0 - Rich text formatting

#### Supported Environments
- Python 3.9+
- ACM 2.11 and 2.12+
- OpenShift 4.x
- Both kubectl and oc CLI

#### Workflow Phases
1. Pre-flight validation
2. Primary hub preparation
3. Secondary hub activation
4. Post-activation verification
5. Finalization

#### State Management
- JSON state file at `.state/switchover-<primary>__<secondary>.json`
- Tracks current phase, completed steps, configuration
- Records errors for debugging
- Enables resume operations

#### Validation Checks
- Namespace existence (both hubs)
- ACM version detection and matching
- OADP operator presence and health
- DataProtectionApplication configuration
- Backup completion status
- ClusterDeployment preserveOnDelete (CRITICAL)
- Passive sync status (Method 1)
- Observability component detection

#### Kubernetes Resources Managed
- BackupSchedule (pause/unpause/delete/create)
- ManagedCluster (annotations, deletion)
- Restore (create/patch/monitor)
- StatefulSet (Thanos compactor scaling)
- Deployment (observatorium-api restart)
- MultiClusterObservability (deletion)
- MultiClusterHub (deletion)
- ClusterDeployment (preserveOnDelete patching)

### Performance

#### Typical Execution Timeline
- Pre-flight validation: 2-3 minutes
- Primary preparation: 1-2 minutes
- Activation: 5-15 minutes
- Post-activation: 10-15 minutes
- Finalization: 5-10 minutes
- **Total: 30-45 minutes**

#### Polling Intervals
- Restore completion: 30 seconds
- ManagedCluster connection: 30 seconds
- Pod readiness: 5 seconds
- Backup creation: 30 seconds

[Unreleased]: https://github.com/tomazb/rh-acm-switchover/compare/v1.5.5...HEAD
[1.5.5]: https://github.com/tomazb/rh-acm-switchover/compare/v1.5.4...v1.5.5
[1.5.4]: https://github.com/tomazb/rh-acm-switchover/compare/v1.5.3...v1.5.4
[1.5.3]: https://github.com/tomazb/rh-acm-switchover/compare/v1.5.1...v1.5.3
[1.5.1]: https://github.com/tomazb/rh-acm-switchover/compare/v1.5.0...v1.5.1
[1.5.0]: https://github.com/tomazb/rh-acm-switchover/compare/v1.4.13...v1.5.0

#### Timeouts
- Restore completion: 30 minutes
- Cluster connection: 10 minutes
- Pod readiness: 5 minutes
- Backup verification: 10 minutes

### Security

#### Authentication
- Uses existing Kubernetes context credentials
- No credentials stored in script or state file
- Relies on RBAC permissions

#### Required RBAC Permissions
- Namespace read access
- Pod listing for health checks
- ACM custom resource read/write
- Deployment/StatefulSet scaling
- Backup/Restore resource management

#### Data Protection
- ClusterDeployment preserveOnDelete verification prevents cluster destruction
- Dry-run mode prevents accidental changes
- Validate-only mode for safety checks
- Interactive confirmations for destructive operations
- State file contains no sensitive data

### Known Limitations

#### Version Support
- Tested with ACM 2.11.x and 2.12.x
- May require updates for future ACM versions

#### Network Requirements
- Requires network access to both hub clusters
- Managed clusters must be able to reach secondary hub

#### Concurrent Operations
- Not designed for parallel switchovers to same secondary hub
- Use separate state files for parallel operations

#### Recovery
- Manual intervention required for catastrophic failures
- Reverse switchover (swap contexts) available for returning to original hub

### Troubleshooting

Common issues documented in docs/operations/usage.md:
- Clusters stuck in "Pending Import"
- No metrics in Grafana after switchover
- Restore stuck in "Running" phase
- ClusterDeployments missing preserveOnDelete

## Future Enhancements

### Planned for v1.1.0
- [ ] Parallel validation checks for faster pre-flight
- [ ] Progress bars using rich library
- [ ] Email/Slack notification support
- [ ] Metrics collection and reporting
- [ ] Enhanced structured logging (JSON output)

### Under Consideration
- [ ] Web UI for monitoring
- [ ] Multi-hub batch switchover
- [ ] Automated post-switchover testing
- [ ] Prometheus metrics export
- [ ] Helm chart for Kubernetes Job deployment
- [ ] Pre-switchover etcd snapshots

## Contributors

- Initial implementation based on ACM switchover runbook (November 2025)

## License

This project is licensed under the MIT License - see the LICENSE file for details.

---

---

## Migration Guides

### Migrating from Python 3.8 or Earlier

Starting with this release, ACM Switchover requires **Python 3.9 or later**. Python 3.8 reached end-of-life in October 2024 and is no longer supported.

#### Option 1: Upgrade System Python (Recommended)

**RHEL 8 / CentOS 8:**
```bash
# Install Python 3.9 from AppStream
sudo dnf install python39 python39-pip

# Use python3.9 explicitly
python3.9 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**RHEL 9 / Fedora:**
```bash
# Python 3.9+ is available by default
python3 --version  # Should show 3.9+
```

**Ubuntu 20.04+:**
```bash
sudo apt install python3.9 python3.9-venv
python3.9 -m venv venv
source venv/bin/activate
```

#### Option 2: Use Container Image

The container image includes Python 3.9 and all dependencies:

```bash
podman run --rm -it \
  -v ~/.kube:/root/.kube:ro \
  quay.io/tomazborstnar/acm-switchover:latest \
  --help
```

#### Option 3: Use pyenv for Multiple Python Versions

```bash
# Install pyenv
curl https://pyenv.run | bash

# Install Python 3.9+
pyenv install 3.11.0
pyenv local 3.11.0

# Create virtual environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### Official Python Upgrade Resources

- [Python Downloads](https://www.python.org/downloads/)
- [Red Hat Python Guide](https://access.redhat.com/documentation/en-us/red_hat_enterprise_linux/8/html/configuring_basic_system_settings/assembly_installing-and-using-python_configuring-basic-system-settings)
- [pyenv Installation](https://github.com/pyenv/pyenv#installation)

---

## Version History

### Version Numbering

- **Major version**: Breaking changes, significant architecture changes
- **Minor version**: New features, backward compatible
- **Patch version**: Bug fixes, documentation updates

### Release Process

1. Update CHANGELOG.md with new version
2. Tag release in git: `git tag -a v1.0.0 -m "Release v1.0.0"`
3. Push tag: `git push origin v1.0.0`
4. Create GitHub release with changelog excerpt

---

[1.4.11]: https://github.com/tomazb/rh-acm-switchover/compare/v1.4.10...v1.4.11
[1.4.10]: https://github.com/tomazb/rh-acm-switchover/compare/v1.4.9...v1.4.10
[1.4.9]: https://github.com/tomazb/rh-acm-switchover/compare/v1.4.8...v1.4.9
[1.4.8]: https://github.com/tomazb/rh-acm-switchover/compare/v1.4.7...v1.4.8
[1.4.7]: https://github.com/tomazb/rh-acm-switchover/compare/v1.4.6...v1.4.7
[1.4.6]: https://github.com/tomazb/rh-acm-switchover/compare/v1.4.5...v1.4.6
[1.4.5]: https://github.com/tomazb/rh-acm-switchover/compare/v1.4.4...v1.4.5
[1.4.4]: https://github.com/tomazb/rh-acm-switchover/compare/v1.4.3...v1.4.4
[1.4.3]: https://github.com/tomazb/rh-acm-switchover/compare/v1.4.2...v1.4.3
[1.4.2]: https://github.com/tomazb/rh-acm-switchover/compare/v1.4.1...v1.4.2
[1.4.1]: https://github.com/tomazb/rh-acm-switchover/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/tomazb/rh-acm-switchover/compare/v1.3.3...v1.4.0
[1.3.3]: https://github.com/tomazb/rh-acm-switchover/compare/v1.3.2...v1.3.3
[1.3.2]: https://github.com/tomazb/rh-acm-switchover/compare/v1.3.1...v1.3.2
[1.3.1]: https://github.com/tomazb/rh-acm-switchover/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/tomazb/rh-acm-switchover/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/tomazb/rh-acm-switchover/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/tomazb/rh-acm-switchover/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/tomazb/rh-acm-switchover/releases/tag/v1.0.0
