% ACM-SWITCHOVER(1) Version 1.5.0 | ACM Switchover User Manual

# NAME

acm-switchover - Automated Red Hat Advanced Cluster Management hub switchover tool

# SYNOPSIS

**acm-switchover** [*OPTIONS*] **--primary-context** *CONTEXT* **--secondary-context** *CONTEXT* **--method** *METHOD* **--old-hub-action** *ACTION*

**acm-switchover** **--decommission** **--primary-context** *CONTEXT* [*OPTIONS*]

# DESCRIPTION

**acm-switchover** is an automated, idempotent tool for switching over Red Hat Advanced Cluster Management (ACM) from a primary hub to a secondary hub cluster. It orchestrates a phased workflow with comprehensive validation and state tracking.

The tool supports:

- Pre-flight validation of both hub clusters
- Pausing backups and scaling down Thanos on the primary hub
- Activating managed clusters on the secondary hub via Velero restore
- Post-activation verification of cluster connections
- Setting up the old hub as a secondary or decommissioning it

# OPTIONS

## Required Options (Switchover Mode)

**--primary-context** *CONTEXT*
:   Kubernetes context name for the current primary hub.

**--secondary-context** *CONTEXT*
:   Kubernetes context name for the secondary hub (new primary).

**--method** *METHOD*
:   Switchover method: **passive-sync** (recommended) or **full-restore**.

**--old-hub-action** *ACTION*
:   Action for old hub after switchover: **secondary** (set up passive sync), **decommission** (remove ACM), or **none** (leave unchanged).

## Required Options (Decommission Mode)

**--decommission**
:   Run ACM decommission workflow only (removes ACM from the specified hub).

**--primary-context** *CONTEXT*
:   Kubernetes context name for the hub to decommission.

## Optional Options

**--state-file** *PATH*
:   Path to state file for tracking progress. Default: **.state/switchover-<primary>__<secondary>.json** or uses **ACM_SWITCHOVER_STATE_DIR** if set.

**--dry-run**
:   Simulate operations without making changes.

**--reset-state**
:   Reset the state file and start fresh.

**--skip-preflight**
:   Skip pre-flight validation checks.

**--non-interactive**
:   Skip all confirmation prompts (only valid with **--decommission**).

**--verbose**, **-v**
:   Enable verbose logging.

**--log-format** *FORMAT*
:   Log format: **text** (default) or **json**.

**--disable-hostname-verification**
:   Disable TLS hostname verification (not recommended for production).

**--version**
:   Show version information and exit.

**--help**, **-h**
:   Show help message and exit.

# WORKFLOW PHASES

The switchover executes the following phases sequentially:

1. **PREFLIGHT** - Validate both hubs, check ACM versions, verify backups
2. **PRIMARY_PREP** - Pause BackupSchedule, add disable-auto-import annotations, scale Thanos
3. **ACTIVATION** - Patch restore with veleroManagedClustersBackupName: latest
4. **POST_ACTIVATION** - Wait for ManagedClusters to connect, verify klusterlet agents
5. **FINALIZATION** - Configure old hub as secondary or prepare for decommission

Each phase is idempotent; if the operation is interrupted, re-running resumes from the last successful step.

# ENVIRONMENT

**ACM_SWITCHOVER_STATE_DIR**
:   Directory for state files when **--state-file** is not specified. Default: **.state/** (relative to current directory).

**KUBECONFIG**
:   Path to kubeconfig file(s). Supports colon-separated paths.

**CLUSTER_VERIFY_MAX_WORKERS**
:   Maximum parallel workers for cluster verification (default: 10).

# FILES

**.state/switchover-<primary>__<secondary>.json**
:   Default state file location for tracking switchover progress.

**/var/lib/acm-switchover/**
:   State directory for RPM/DEB/container installations.

# EXIT STATUS

**0**
:   Success

**1**
:   Failure

**130**
:   Interrupted by user (Ctrl+C)

# EXAMPLES

Perform a switchover using passive-sync method:

    acm-switchover \\
        --primary-context hub1 \\
        --secondary-context hub2 \\
        --method passive-sync \\
        --old-hub-action secondary

Dry-run to see what would happen:

    acm-switchover \\
        --primary-context hub1 \\
        --secondary-context hub2 \\
        --method passive-sync \\
        --old-hub-action secondary \\
        --dry-run

Decommission an old hub:

    acm-switchover \\
        --decommission \\
        --primary-context old-hub \\
        --non-interactive

Resume an interrupted switchover:

    acm-switchover \\
        --primary-context hub1 \\
        --secondary-context hub2 \\
        --method passive-sync \\
        --old-hub-action secondary

# SEE ALSO

**acm-switchover-rbac**(1), **acm-switchover-state**(1)

# AUTHORS

Tomaz Borstnar <tomaz@borstnar.com>

# BUGS

Report bugs at <https://github.com/tomazb/rh-acm-switchover/issues>
