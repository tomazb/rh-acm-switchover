% ACM-SWITCHOVER-RBAC(1) Version 1.5.0 | ACM Switchover User Manual

# NAME

acm-switchover-rbac - Check RBAC permissions for ACM switchover operations

# SYNOPSIS

**acm-switchover-rbac** **--primary-context** *CONTEXT* **--secondary-context** *CONTEXT* [*OPTIONS*]

**acm-switchover-rbac** **--context** *CONTEXT* [*OPTIONS*]

# DESCRIPTION

**acm-switchover-rbac** validates that the current user or service account has the required RBAC permissions to perform ACM switchover operations. It checks permissions on one or both hub clusters.

Use this tool before running a switchover to verify that all required permissions are in place.

# OPTIONS

**--primary-context** *CONTEXT*
:   Kubernetes context for the primary hub.

**--secondary-context** *CONTEXT*
:   Kubernetes context for the secondary hub.

**--context** *CONTEXT*
:   Check permissions on a single context.

**--include-decommission**
:   Include permissions required for ACM decommission operations.

**--skip-observability**
:   Skip observability namespace permission checks.

**--verbose**, **-v**
:   Enable verbose logging.

**--version**
:   Show version information and exit.

**--help**, **-h**
:   Show help message and exit.

# EXIT STATUS

**0**
:   All required permissions are present.

**1**
:   One or more required permissions are missing.

# EXAMPLES

Check RBAC on both hubs:

    acm-switchover-rbac \\
        --primary-context hub1 \\
        --secondary-context hub2

Check RBAC on a single hub including decommission permissions:

    acm-switchover-rbac \\
        --context hub1 \\
        --include-decommission

# SEE ALSO

**acm-switchover**(1), **acm-switchover-state**(1)

# AUTHORS

Tomaz Borstnar <tomaz@borstnar.com>

# BUGS

Report bugs at <https://github.com/tomazb/rh-acm-switchover/issues>
