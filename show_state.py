#!/usr/bin/env python3
"""
ACM Switchover State File Viewer

A utility to inspect and explain the content of switchover state files.
Useful for debugging, auditing, and understanding switchover progress.

Usage:
    ./show_state.py [state_file]
    ./show_state.py --list
    ./show_state.py --json [state_file]
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ANSI colors for terminal output
COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[0;31m",
    "green": "\033[0;32m",
    "yellow": "\033[1;33m",
    "blue": "\033[0;34m",
    "cyan": "\033[0;36m",
    "gray": "\033[0;90m",
}

# Phase descriptions
PHASE_INFO = {
    "init": ("Not started", "Switchover has not begun"),
    "preflight_validation": ("Pre-flight", "Validating prerequisites on both hubs"),
    "primary_preparation": ("Primary Prep", "Preparing primary hub for handover"),
    "secondary_verification": ("Secondary Verify", "Verifying secondary hub readiness"),
    "activation": ("Activation", "Activating secondary hub as new primary"),
    "post_activation_verification": ("Post-Activation", "Verifying switchover success"),
    "finalization": ("Finalization", "Completing switchover tasks"),
    "completed": ("Completed", "Switchover finished successfully"),
    "rollback": ("Rollback", "Rolling back to primary hub"),
    "failed": ("Failed", "Switchover encountered a fatal error"),
}

# Step descriptions
STEP_INFO = {
    # Preflight steps
    "preflight_validation": "Validated prerequisites on both hubs",
    # Primary preparation steps
    "pause_backup_schedule": "Paused BackupSchedule on primary hub",
    "add_disable_auto_import": "Added disable-auto-import annotations to ManagedClusters",
    "scale_down_thanos_compactor": "Scaled down Thanos compactor on primary hub",
    # Activation steps
    "verify_passive_sync": "Verified passive sync restore is running",
    "activate_managed_clusters": "Patched restore to activate managed clusters",
    "create_full_restore": "Created full restore resource (Method 2)",
    "wait_restore_completion": "Waited for restore to complete",
    # Post-activation steps
    "verify_clusters_connected": "Verified ManagedClusters are connected",
    "verify_auto_import_cleanup": "Verified disable-auto-import annotations removed",
    "restart_observatorium_api": "Restarted observatorium-api deployment",
    "verify_observability_pods": "Verified Observability pods are healthy",
    "verify_metrics_collection": "Verified metrics collection is working",
    # Finalization steps
    "enable_backup_schedule": "Enabled BackupSchedule on new hub",
    "verify_backup_schedule_enabled": "Verified BackupSchedule is active",
    "fix_backup_collision": "Fixed BackupSchedule collision issue",
    "verify_new_backups": "Verified new backups are being created",
    "verify_mch_health": "Verified MultiClusterHub health",
    "handle_old_hub": "Handled old hub (secondary/decommission/none)",
    "reset_auto_import_strategy": "Reset auto-import strategy to default",
}


def color(text: str, color_name: str, use_color: bool = True) -> str:
    """Apply color to text if colors are enabled."""
    if not use_color or color_name not in COLORS:
        return text
    return f"{COLORS[color_name]}{text}{COLORS['reset']}"


def format_timestamp(iso_timestamp: str) -> str:
    """Format ISO timestamp to human-readable format."""
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - dt

        if delta.days > 0:
            age = f"{delta.days}d ago"
        elif delta.seconds >= 3600:
            age = f"{delta.seconds // 3600}h ago"
        elif delta.seconds >= 60:
            age = f"{delta.seconds // 60}m ago"
        else:
            age = "just now"

        return f"{dt.strftime('%Y-%m-%d %H:%M:%S')} ({age})"
    except (ValueError, TypeError):
        return iso_timestamp or "unknown"


def find_state_files(state_dir: str = ".state") -> List[str]:
    """Find all state files in the state directory."""
    pattern = os.path.join(state_dir, "switchover-*.json")
    return sorted(glob.glob(pattern))


def load_state(state_file: str) -> Optional[Dict[str, Any]]:
    """Load state from file."""
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: State file not found: {state_file}")
        return None
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in state file: {e}")
        return None


def print_header(title: str, use_color: bool = True):
    """Print a section header."""
    line = "═" * 60
    print()
    print(color(f"╔{line}╗", "blue", use_color))
    print(color(f"║  {title:<56}  ║", "blue", use_color))
    print(color(f"╚{line}╝", "blue", use_color))


def print_section(title: str, use_color: bool = True):
    """Print a subsection header."""
    print()
    print(color(f"━━━ {title} ━━━", "cyan", use_color))


def print_state(state: Dict[str, Any], use_color: bool = True):
    """Print formatted state information."""

    # Header
    print_header("ACM Switchover State", use_color)

    # Basic info
    print_section("Overview", use_color)
    print(f"  Version:      {state.get('version', 'unknown')}")
    print(f"  Created:      {format_timestamp(state.get('created_at', ''))}")
    print(f"  Last Updated: {format_timestamp(state.get('last_updated', ''))}")

    # Contexts
    contexts = state.get("contexts", {})
    if contexts:
        print(f"  Primary:      {contexts.get('primary') or 'not set'}")
        print(f"  Secondary:    {contexts.get('secondary') or 'not set'}")

    # Current phase
    print_section("Current Phase", use_color)
    phase = state.get("current_phase", "unknown")
    phase_name, phase_desc = PHASE_INFO.get(phase, (phase, "Unknown phase"))

    phase_colors = {
        "completed": "green",
        "failed": "red",
        "rollback": "yellow",
    }
    phase_color = phase_colors.get(phase, "blue")

    print(f"  {color(phase_name, phase_color, use_color)}: {phase_desc}")

    # Completed steps
    print_section("Completed Steps", use_color)
    steps = state.get("completed_steps", [])
    if steps:
        for i, step in enumerate(steps, 1):
            step_name = step.get("name", "unknown")
            step_time = format_timestamp(step.get("timestamp", ""))
            step_desc = STEP_INFO.get(step_name, step_name)
            print(f"  {color('✓', 'green', use_color)} {i:2}. {step_desc}")
            print(f"       {color(step_time, 'gray', use_color)}")
    else:
        print(f"  {color('No steps completed yet', 'gray', use_color)}")

    # Configuration
    config = state.get("config", {})
    if config:
        print_section("Configuration", use_color)
        for key, value in config.items():
            if key == "archived_restores":
                print(f"  {key}:")
                print_archived_restores(value, use_color)
            elif isinstance(value, dict):
                print(f"  {key}:")
                for k, v in value.items():
                    print(f"    {k}: {v}")
            elif isinstance(value, list):
                print(f"  {key}: [{len(value)} items]")
            else:
                print(f"  {key}: {value}")

    # Errors
    errors = state.get("errors", [])
    if errors:
        print_section(f"Errors ({len(errors)})", use_color)
        for error in errors:
            err_phase = error.get("phase", "unknown")
            err_msg = error.get("error", "unknown error")
            err_time = format_timestamp(error.get("timestamp", ""))
            print(f"  {color('✗', 'red', use_color)} [{err_phase}] {err_msg}")
            print(f"       {color(err_time, 'gray', use_color)}")

    print()


def print_archived_restores(restores: List[Dict], use_color: bool = True):
    """Print archived restore details."""
    if not restores:
        print(f"    {color('No archived restores', 'gray', use_color)}")
        return

    for i, restore in enumerate(restores, 1):
        name = restore.get("name", "unknown")
        phase = restore.get("phase", "unknown")
        created = format_timestamp(restore.get("creation_timestamp", ""))
        archived = format_timestamp(restore.get("archived_at", ""))

        phase_color = "green" if phase in ("Finished", "Completed") else "yellow"

        print(f"    [{i}] {color(name, 'bold', use_color)}")
        print(f"        Phase: {color(phase, phase_color, use_color)}")
        print(f"        UID: {restore.get('uid', 'N/A')}")
        print(f"        Created: {created}")
        print(f"        Archived: {archived}")

        # Velero backups
        velero_backups = restore.get("velero_backups", {})
        if velero_backups:
            print("        Velero Backups:")
            for k, v in velero_backups.items():
                if v:
                    print(f"          {k}: {v}")

        # Velero restores created
        for key in [
            "velero_managed_clusters_restore_name",
            "velero_credentials_restore_name",
            "velero_resources_restore_name",
        ]:
            val = restore.get(key)
            if val:
                short_key = key.replace("velero_", "").replace("_restore_name", "")
                print(f"        Velero Restore ({short_key}): {val}")

        # Last message
        last_msg = restore.get("last_message")
        if last_msg:
            print(f"        Last Message: {last_msg[:60]}...")


def list_state_files(use_color: bool = True):
    """List all available state files."""
    state_files = find_state_files()

    if not state_files:
        print("No state files found in .state/ directory")
        return

    print_header("Available State Files", use_color)
    print()

    for state_file in state_files:
        state = load_state(state_file)
        if state:
            phase = state.get("current_phase", "unknown")
            phase_name, _ = PHASE_INFO.get(phase, (phase, ""))
            updated = format_timestamp(state.get("last_updated", ""))
            contexts = state.get("contexts", {})
            primary = contexts.get("primary", "?")
            secondary = contexts.get("secondary", "?")

            phase_colors = {"completed": "green", "failed": "red", "rollback": "yellow"}
            phase_color = phase_colors.get(phase, "blue")

            print(f"  {color(os.path.basename(state_file), 'bold', use_color)}")
            print(f"    Contexts: {primary} → {secondary}")
            print(f"    Phase: {color(phase_name, phase_color, use_color)}")
            print(f"    Updated: {updated}")
            print()


def main():
    parser = argparse.ArgumentParser(
        description="View and explain ACM switchover state files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # View most recent state file
  %(prog)s

  # View specific state file
  %(prog)s .state/switchover-primary__secondary.json

  # List all state files
  %(prog)s --list

  # Output as JSON
  %(prog)s --json
        """,
    )

    parser.add_argument(
        "state_file",
        nargs="?",
        help="Path to state file (default: most recent in .state/)",
    )
    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="List all available state files",
    )
    parser.add_argument(
        "--json",
        "-j",
        action="store_true",
        help="Output raw JSON instead of formatted view",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )

    args = parser.parse_args()
    use_color = not args.no_color and sys.stdout.isatty()

    if args.list:
        list_state_files(use_color)
        return 0

    # Find state file
    if args.state_file:
        state_file = args.state_file
    else:
        state_files = find_state_files()
        if not state_files:
            print(
                "No state files found. Run a switchover first or specify a state file path."
            )
            return 1
        # Use most recently modified
        state_file = max(state_files, key=os.path.getmtime)
        print(f"Using: {state_file}")

    # Load and display state
    state = load_state(state_file)
    if state is None:
        return 1

    if args.json:
        print(json.dumps(state, indent=2))
    else:
        print_state(state, use_color)

    return 0


if __name__ == "__main__":
    sys.exit(main())
