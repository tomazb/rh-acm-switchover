"""
Common utilities for ACM switchover automation.
"""

import json
import logging
import os
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


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


class StateManager:
    """Manages switchover state for idempotent operations."""
    
    def __init__(self, state_file: str = ".state/switchover-state.json"):
        self.state_file = state_file
        self.state = self._load_state()
        
    def _load_state(self) -> Dict[str, Any]:
        """Load state from file or create new state."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                logging.warning(f"Corrupted state file {self.state_file}, starting fresh")
        
        return {
            "version": "1.0",
            "created_at": datetime.utcnow().isoformat(),
            "current_phase": Phase.INIT.value,
            "completed_steps": [],
            "config": {},
            "errors": [],
            "last_updated": datetime.utcnow().isoformat()
        }
    
    def save_state(self):
        """Save current state to file."""
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        self.state["last_updated"] = datetime.utcnow().isoformat()
        
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)
    
    def set_phase(self, phase: Phase):
        """Update current phase."""
        self.state["current_phase"] = phase.value
        self.save_state()
    
    def mark_step_completed(self, step_name: str):
        """Mark a step as completed."""
        if step_name not in self.state["completed_steps"]:
            self.state["completed_steps"].append({
                "name": step_name,
                "timestamp": datetime.utcnow().isoformat()
            })
            self.save_state()
    
    def is_step_completed(self, step_name: str) -> bool:
        """Check if a step was already completed."""
        return any(s["name"] == step_name for s in self.state["completed_steps"])
    
    def set_config(self, key: str, value: Any):
        """Store configuration value."""
        self.state["config"][key] = value
        self.save_state()
    
    def get_config(self, key: str, default: Any = None) -> Any:
        """Retrieve configuration value."""
        return self.state["config"].get(key, default)
    
    def add_error(self, error: str, phase: Optional[str] = None):
        """Record an error."""
        self.state["errors"].append({
            "error": error,
            "phase": phase or self.state["current_phase"],
            "timestamp": datetime.utcnow().isoformat()
        })
        self.save_state()
    
    def reset(self):
        """Reset state to initial."""
        self.state = {
            "version": "1.0",
            "created_at": datetime.utcnow().isoformat(),
            "current_phase": Phase.INIT.value,
            "completed_steps": [],
            "config": {},
            "errors": [],
            "last_updated": datetime.utcnow().isoformat()
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


def parse_acm_version(version_string: str) -> tuple:
    """
    Parse ACM version string to tuple for comparison.
    
    Args:
        version_string: Version like "2.12.0" or "2.11.3"
    
    Returns:
        Tuple of (major, minor, patch)
    """
    try:
        parts = version_string.strip().split('.')
        return tuple(int(p) for p in parts[:3])
    except (ValueError, AttributeError):
        return (0, 0, 0)


def is_acm_version_ge(version: str, compare_to: str) -> bool:
    """
    Check if ACM version is greater than or equal to comparison version.
    
    Args:
        version: Current version string
        compare_to: Version to compare against
    
    Returns:
        True if version >= compare_to
    """
    return parse_acm_version(version) >= parse_acm_version(compare_to)


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
