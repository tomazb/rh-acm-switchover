% ACM-SWITCHOVER-STATE(1) Version 1.5.0 | ACM Switchover User Manual

# NAME

acm-switchover-state - View and explain ACM switchover state files

# SYNOPSIS

**acm-switchover-state** [*STATE_FILE*] [*OPTIONS*]

# DESCRIPTION

**acm-switchover-state** displays the contents of ACM switchover state files in a human-readable format. It shows the current phase, completed steps, errors, and timing information.

Without arguments, it displays the most recently modified state file in the default state directory.

# OPTIONS

*STATE_FILE*
:   Path to a specific state file. If omitted, uses the most recent file in the default state directory.

**--list**, **-l**
:   List all available state files.

**--json**, **-j**
:   Output raw JSON instead of formatted view.

**--no-color**
:   Disable colored output.

**--version**
:   Show version information and exit.

**--help**, **-h**
:   Show help message and exit.

# ENVIRONMENT

**ACM_SWITCHOVER_STATE_DIR**
:   Directory to search for state files. Default: **.state/** (relative to current directory).

# FILES

**.state/switchover-<primary>__<secondary>.json**
:   Default state file location.

**/var/lib/acm-switchover/**
:   State directory for RPM/DEB/container installations.

# EXIT STATUS

**0**
:   Success

**1**
:   State file not found or error reading file

# EXAMPLES

View the most recent state file:

    acm-switchover-state

View a specific state file:

    acm-switchover-state .state/switchover-hub1__hub2.json

List all state files:

    acm-switchover-state --list

Output as JSON:

    acm-switchover-state --json

# SEE ALSO

**acm-switchover**(1), **acm-switchover-rbac**(1)

# AUTHORS

Tomaz Borstnar <tomaz@borstnar.com>

# BUGS

Report bugs at <https://github.com/tomazb/rh-acm-switchover/issues>
