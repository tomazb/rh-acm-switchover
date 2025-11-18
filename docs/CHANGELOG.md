# Changelog

All notable changes to the ACM Switchover Automation project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial release of ACM Switchover Automation tool

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
- JSON state file at `.state/switchover-state.json`
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

[Unreleased]: https://github.com/tomazb/rh-acm-switchover/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/tomazb/rh-acm-switchover/releases/tag/v1.0.0
