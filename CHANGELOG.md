# Changelog

All notable changes to the ACM Switchover Automation project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Notes

- Version 1.5.x is reserved for packaging and distribution work.

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

[Unreleased]: https://github.com/tomazb/rh-acm-switchover/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/tomazb/rh-acm-switchover/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/tomazb/rh-acm-switchover/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/tomazb/rh-acm-switchover/releases/tag/v1.0.0
