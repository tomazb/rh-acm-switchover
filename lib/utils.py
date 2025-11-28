"""
Common utilities for ACM switchover automation.
"""

import functools
import json
import logging
import os
import stat
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple, TypeVar

# Type variable for generic return type
T = TypeVar("T")


def dry_run_skip(
    message: str = "Skipping in dry-run mode",
    return_value: Any = None,
    dry_run_attr: str = "dry_run",
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator that skips function execution in dry-run mode.

    This decorator checks if the instance has dry_run=True and if so,
    logs a message and returns a default value instead of executing
    the decorated function.

    Args:
        message: Message to log when skipping (will be prefixed with "[DRY-RUN]")
        return_value: Value to return when skipping (default: None)
        dry_run_attr: Name of the attribute to check for dry-run mode.
                     Can be a dot-separated path like "client.dry_run"

    Returns:
        Decorated function that skips execution in dry-run mode

    Example:
        class MyClass:
            def __init__(self, dry_run: bool = False):
                self.dry_run = dry_run

            @dry_run_skip(message="Would perform action", return_value=True)
            def perform_action(self):
                # This code only runs when dry_run=False
                return do_something()
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs) -> T:
            # Navigate through dot-separated attribute path
            obj = self
            for attr_name in dry_run_attr.split("."):
                obj = getattr(obj, attr_name, None)
                if obj is None:
                    break

            if obj:
                logger = logging.getLogger("acm_switchover")
                logger.info("[DRY-RUN] %s", message)
                return return_value

            return func(self, *args, **kwargs)

        return wrapper

    return decorator


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
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError as e:
                logging.warning("Corrupted state file %s: %s, starting fresh", self.state_file, e)
            except OSError as e:
                logging.error("Failed to read state file %s: %s", self.state_file, e)

        # If state file is missing or unreadable, create a new state file.
        state = self._new_state()
        self._write_state(state)
        return state

    def _new_state(self) -> Dict[str, Any]:
        """Return a fresh state structure."""
        return {
            "version": "1.0",
            "created_at": _utc_timestamp(),
            "current_phase": Phase.INIT.value,
            "completed_steps": [],
            "config": {},
            "errors": [],
            "last_updated": _utc_timestamp(),
            "contexts": {"primary": None, "secondary": None},
        }

    def _ensure_state_dir(self) -> None:
        state_dir = os.path.dirname(self.state_file)
        if state_dir:
            os.makedirs(state_dir, exist_ok=True)

    def _write_state(self, state: Dict[str, Any]) -> None:
        """Write the provided state dict to disk without modifying it."""
        self._ensure_state_dir()
        # Use restrictive permissions (owner read/write only)
        fd = os.open(self.state_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception:
            os.close(fd)
            raise

    def save_state(self) -> None:
        """Persist current state to disk."""
        self.state["last_updated"] = _utc_timestamp()
        self._write_state(self.state)

    def set_phase(self, phase: Phase) -> None:
        """Update current phase."""
        self.state["current_phase"] = phase.value
        self.save_state()

    def mark_step_completed(self, step_name: str) -> None:
        """Mark a step as completed."""
        if not self.is_step_completed(step_name):
            self.state["completed_steps"].append({"name": step_name, "timestamp": _utc_timestamp()})
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
        self.state["errors"].append(
            {
                "error": error,
                "phase": phase or self.state["current_phase"],
                "timestamp": _utc_timestamp(),
            }
        )
        self.save_state()

    def reset(self) -> None:
        """Reset state to initial."""
        self.state = self._new_state()
        self.save_state()

    def get_current_phase(self) -> Phase:
        """Get current phase as enum."""
        raw_phase = self.state.get("current_phase", Phase.INIT.value)
        try:
            return Phase(raw_phase)
        except ValueError:
            logging.warning(
                "Unknown phase '%s' in state file %s. Falling back to INIT.",
                raw_phase,
                self.state_file,
            )
            self.state["current_phase"] = Phase.INIT.value
            self.save_state()
            return Phase.INIT

    def ensure_contexts(self, primary_context: str, secondary_context: Optional[str]) -> None:
        """Ensure stored contexts match the ones provided on the CLI."""
        stored = self.state.get("contexts") or {}
        desired = {"primary": primary_context, "secondary": secondary_context}

        if stored and (
            stored.get("primary") not in (None, primary_context)
            or stored.get("secondary") not in (None, secondary_context)
        ):
            logging.warning(
                "Stored state contexts (%s/%s) differ from current invocation (%s/%s). "
                "Resetting state to avoid mixing runs.",
                stored.get("primary"),
                stored.get("secondary"),
                primary_context,
                secondary_context,
            )
            self.state = self._new_state()

        self.state["contexts"] = desired
        self.save_state()


class JSONFormatter(logging.Formatter):
    """Format logs as JSON for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_record = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_record)


def setup_logging(verbose: bool = False, log_format: str = "text") -> logging.Logger:
    """
    Configure logging with rich formatting.

    Args:
        verbose: Enable debug logging
        log_format: 'text' or 'json'
    """
    level = logging.DEBUG if verbose else logging.INFO
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler()

    if log_format.lower() == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    root_logger.addHandler(handler)

    return logging.getLogger("acm_switchover")


def parse_acm_version(version_string: str) -> Optional[Tuple[int, int, int]]:
    """
    Parse ACM version string to tuple for comparison.

    Args:
        version_string: Version like "2.12.0" or "2.11.3"

    Returns:
        Tuple of (major, minor, patch)
    """
    try:
        parts = [int(p) for p in version_string.strip().split(".")]
        while len(parts) < 3:
            parts.append(0)
        return (parts[0], parts[1], parts[2])
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
        elif response in ("y", "yes"):
            return True
        elif response in ("n", "no"):
            return False
        else:
            print("Please answer 'y' or 'n'")
