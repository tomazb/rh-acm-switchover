"""
Common utilities for ACM switchover automation.
"""

import json
import logging
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class Phase(Enum):
    """Switchover phases for state tracking."""
    INIT = "init"
    PREFLIGHT = "preflight_validation"
    PRIMARY_PREP = "primary_preparation"
    SECONDARY_VERIFY = "secondary_verification"
    ACTIVATION = "activation"
    POST_ACTIVATION = "post_activation_verification"
    FINALIZATION = "finalization"
    COMPLETED = "completed"
    ROLLBACK = "rollback"
    FAILED = "failed"


def _utc_timestamp() -> str:
    """Return an ISO-8601 timestamp in UTC."""
    return datetime.now(timezone.utc).isoformat()


class StateManager:
    """Manages switchover state for idempotent operations."""
    
    def __init__(self, state_file: str = ".state/switchover-state.json"):
        self.state_file = state_file
        self.state = self._load_state()
        
    def _load_state(self) -> Dict[str, Any]:
        """Load state from file or create new state."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except json.JSONDecodeError as e:
                logging.warning(f"Corrupted state file {self.state_file}: {e}, starting fresh")
            except OSError as e:
                logging.error(f"Failed to read state file {self.state_file}: {e}")
        
        return {
            "version": "1.0",
            "created_at": _utc_timestamp(),
            "current_phase": Phase.INIT.value,
            "completed_steps": [],
            "config": {},
            "errors": [],
            "last_updated": _utc_timestamp()
        }
    
    def save_state(self) -> None:
        """Save current state to file."""
        state_dir = os.path.dirname(self.state_file)
        if state_dir:  # Only create directory if path contains one
            os.makedirs(state_dir, exist_ok=True)
        self.state["last_updated"] = _utc_timestamp()
        
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, indent=2)
    
    def set_phase(self, phase: Phase) -> None:
        """Update current phase."""
        self.state["current_phase"] = phase.value
        self.save_state()
    
    def mark_step_completed(self, step_name: str) -> None:
        """Mark a step as completed."""
        if not self.is_step_completed(step_name):
            self.state["completed_steps"].append({
                "name": step_name,
                "timestamp": _utc_timestamp()
            })
            self.save_state()
    
    def is_step_completed(self, step_name: str) -> bool:
        """Check if a step was already completed."""
        return any(s["name"] == step_name for s in self.state["completed_steps"])
    
    def set_config(self, key: str, value: Any) -> None:
        """Store configuration value."""
        self.state["config"][key] = value
        self.save_state()
    
    def get_config(self, key: str, default: Any = None) -> Any:
        """Retrieve configuration value."""
        return self.state["config"].get(key, default)
    
    def add_error(self, error: str, phase: Optional[str] = None) -> None:
        """Record an error."""
        self.state["errors"].append({
            "error": error,
            "phase": phase or self.state["current_phase"],
            "timestamp": _utc_timestamp()
        })
        self.save_state()
    
    def reset(self) -> None:
        """Reset state to initial."""
        self.state = {
            "version": "1.0",
            "created_at": _utc_timestamp(),
            "current_phase": Phase.INIT.value,
            "completed_steps": [],
            "config": {},
            "errors": [],
            "last_updated": _utc_timestamp()
        }
        self.save_state()
    
    def get_current_phase(self) -> Phase:
        """Get current phase as enum."""
        return Phase(self.state["current_phase"])


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure logging with rich formatting."""
    level = logging.DEBUG if verbose else logging.INFO
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    return logging.getLogger("acm_switchover")
    from typing import Optional, Tuple


def parse_acm_version(version_string: str) -> Optional[Tuple[int, int, int]]:
    """
    Parse ACM version string to tuple for comparison.
    
    Args:
        version_string: Version like "2.12.0" or "2.11.3"
    
    Returns:
        Tuple of (major, minor, patch)
    """
    try:
        parts = [int(p) for p in version_string.strip().split('.')]
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])
    except (ValueError, AttributeError):
        return None


def is_acm_version_ge(version: str, compare_to: str) -> bool:
    """
    Check if ACM version is greater than or equal to comparison version.
    
    Args:
        version: Current version string
        compare_to: Version to compare against
    
    Returns:
        True if version >= compare_to
    """
    current = parse_acm_version(version)
    target = parse_acm_version(compare_to)

    if current is None or target is None:
        return False

    return current >= target


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}m"
    else:
        hours = seconds / 3600
        return f"{hours:.1f}h"


def confirm_action(prompt: str, default: bool = False) -> bool:
    """
    Prompt user for confirmation.
    
    Args:
        prompt: Question to ask
        default: Default answer if user just presses enter
    
    Returns:
        True if confirmed, False otherwise
    """
    suffix = " [Y/n]: " if default else " [y/N]: "
    
    while True:
        response = input(prompt + suffix).strip().lower()
        
        if not response:
            return default
        elif response in ('y', 'yes'):
            return True
        elif response in ('n', 'no'):
            return False
        else:
            print("Please answer 'y' or 'n'")
