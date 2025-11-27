# Changelog

All notable changes to the ACM Switchover Automation project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
  - `Rollback`: Log all operations with `[DRY-RUN]` prefix
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

### Fixed

- Fixed passive sync validation to accept "Finished" state (not just "Enabled")
- Fixed dry-run mode not being passed to all sub-modules

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
- Updated `docs/CONTAINER_USAGE.md` to reflect new build context location.
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
- Rollback capability to revert to primary hub
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
- `finalization.py` - Finalization and rollback (237 lines)
- `decommission.py` - Old hub decommission (144 lines)

#### Libraries
- `utils.py` - State management, logging, helpers (203 lines)
- `kube_client.py` - Kubernetes API wrapper (358 lines)

#### Documentation
- README.md - Project overview
- QUICKREF.md - Command reference card
- USAGE.md - Detailed usage examples and scenarios
- ARCHITECTURE.md - Design and implementation details
- INSTALL.md - Installation and deployment guide
- CONTRIBUTING.md - Development guidelines
- PROJECT_SUMMARY.md - Comprehensive project summary

#### Tools
- `quick-start.sh` - Interactive setup wizard
- Main script with comprehensive CLI (318 lines)

### Technical Details

#### Dependencies
- kubernetes>=28.0.0 - Kubernetes API client
- PyYAML>=6.0 - YAML parsing
- rich>=13.0.0 - Rich text formatting

#### Supported Environments
- Python 3.8+
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
- Enables resume and rollback operations

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
- Rollback capability available for most failure scenarios

### Troubleshooting

Common issues documented in USAGE.md:
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
