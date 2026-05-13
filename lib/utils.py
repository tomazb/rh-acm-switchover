"""
Common utilities for ACM switchover automation.
"""

import atexit
import copy
import functools
import inspect
import json
import logging
import os
import shutil
import signal
import stat
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, Literal, Optional, Set, Tuple, TypeVar

from lib.exceptions import StateLoadError, StateLockError

# File locking is best-effort; fcntl isn't available on Windows.
try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - platform-specific
    fcntl = None  # type: ignore

# Type variable for generic return type
T = TypeVar("T")

# Process-local registry for lifetime state-file run locks.
# Each entry is keyed by absolute lock-file path and stores a shared file handle
# plus a reference count so multiple StateManager instances in the same process
# can reuse the same OS lock without blocking each other.
_RUN_LOCK_REGISTRY: Dict[str, Dict[str, Any]] = {}


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
        first_param_name = next(iter(inspect.signature(func).parameters), None)

        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            # Navigate through dot-separated attribute path
            root = args[0] if args else None
            if root is None and first_param_name:
                root = kwargs.get(first_param_name)

            obj = root
            for attr_name in dry_run_attr.split("."):
                obj = getattr(obj, attr_name, None)
                if obj is None:
                    # Attribute path broken — cannot determine dry-run state.
                    # Safe default: skip execution to avoid unintended changes.
                    logger = logging.getLogger("acm_switchover")
                    logger.warning(
                        "[DRY-RUN] Cannot resolve attribute path '%s' on %s; " "skipping for safety",
                        dry_run_attr,
                        type(root).__name__,
                    )
                    if callable(return_value):
                        return return_value(*args, **kwargs)
                    return return_value

            # Explicitly check for True to avoid truthy object references
            if obj is True:
                logger = logging.getLogger("acm_switchover")
                logger.info("[DRY-RUN] %s", message)
                if callable(return_value):
                    return return_value(*args, **kwargs)
                return return_value

            return func(*args, **kwargs)

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
    FAILED = "failed"


def _utc_timestamp() -> str:
    """Return an ISO-8601 timestamp in UTC."""
    return datetime.now(timezone.utc).isoformat()


class StateManager:
    """Manages switchover state for idempotent operations."""

    def __init__(self, state_file: str = ".state/switchover-state.json"):
        self.state_file = state_file
        self._dirty = False  # Track if state has pending writes
        self._active_temp_files: Set[str] = set()  # Track active temp files for cleanup
        self._flushing = False  # Track if we're currently flushing to avoid double-write
        self._previous_signal_handlers: Dict[int, Any] = {}
        self._run_lock_path = os.path.realpath(self.state_file) + ".run.lock"
        self._run_lock_handle: Optional[Any] = None
        self._retry_error_baseline: Optional[Dict[str, Any]] = None
        # Register atexit handlers to flush pending state, clean up temp files,
        # and release the lifetime run lock on process exit.
        atexit.register(self._release_run_lock)
        atexit.register(self._flush_on_exit)
        atexit.register(self._cleanup_temp_files)

        try:
            self._acquire_run_lock()

            # Register signal handlers to flush dirty state before termination
            # This ensures state is saved even on SIGTERM/SIGINT (atexit doesn't run on SIGKILL)
            # Use wrapper functions since signal handlers must accept (signum, frame) signature
            def signal_handler(signum: int, frame: Any) -> None:
                self._flush_on_signal(signum, frame)

            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    self._previous_signal_handlers[sig] = signal.getsignal(sig)
                    signal.signal(sig, signal_handler)
                except ValueError:
                    # signal.signal() raises ValueError when called from non-main thread
                    # This is expected in test environments or when StateManager is used in workers
                    logging.debug(
                        "Cannot register signal handler for %s (not in main thread)",
                        sig,
                    )
            self.state = self._load_state()
        except Exception:
            self._release_run_lock()
            raise

    def _acquire_run_lock(self) -> None:
        """Acquire a non-blocking process-lifetime lock for this state file.

        The lock is advisory and POSIX-only. If fcntl is unavailable, the code
        falls back to best-effort behavior (write-time locking still applies).
        Multiple StateManager instances in the same process reuse the same lock.
        """
        if not fcntl:  # pragma: no cover - platform-specific
            logging.debug(
                "fcntl unavailable; process-level state lock disabled for %s",
                self.state_file,
            )
            return

        registry_entry = _RUN_LOCK_REGISTRY.get(self._run_lock_path)
        if registry_entry is not None:
            registry_entry["refcount"] += 1
            self._run_lock_handle = registry_entry["handle"]
            return

        self._ensure_state_dir()
        lock_handle = open(self._run_lock_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            lock_handle.close()
            raise StateLockError(
                f"Another switchover process is already using state file: {self.state_file}\n"
                f"Lock file: {self._run_lock_path}\n"
                "Wait for the other process to finish, or verify no stale process is still running."
            ) from exc

        lock_handle.seek(0)
        lock_handle.truncate()
        lock_handle.write(f"pid={os.getpid()}\nstate_file={os.path.abspath(self.state_file)}\n")
        lock_handle.flush()

        _RUN_LOCK_REGISTRY[self._run_lock_path] = {"handle": lock_handle, "refcount": 1}
        self._run_lock_handle = lock_handle

    def _release_run_lock(self) -> None:
        """Release the process-lifetime run lock if this is the last holder."""
        if not self._run_lock_handle:
            return

        registry_entry = _RUN_LOCK_REGISTRY.get(self._run_lock_path)
        if registry_entry is None:
            self._run_lock_handle = None
            return

        registry_entry["refcount"] -= 1
        if registry_entry["refcount"] <= 0:
            handle = registry_entry["handle"]
            try:
                if fcntl:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError as exc:
                logging.debug("Failed to unlock state run lock %s: %s", self._run_lock_path, exc)
            try:
                handle.close()
            except OSError as exc:
                logging.debug("Failed to close state run lock %s: %s", self._run_lock_path, exc)
            _RUN_LOCK_REGISTRY.pop(self._run_lock_path, None)

        self._run_lock_handle = None

    def _load_state(self) -> Dict[str, Any]:
        """Load state from file, or create a new state file when none exists.

        Raises StateLoadError if the file exists but cannot be read or parsed.
        The corrupt file is preserved (copied to *.corrupt.<timestamp>) so that
        operators can inspect it while the original path continues blocking reuse.
        Never silently replaces a corrupt state file —
        that would risk replaying mutations that were already applied to a real hub.
        """
        if not os.path.exists(self.state_file):
            state = self._new_state()
            self._write_state(state)
            return state

        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
                self._validate_loaded_state(state)
                return state
        except json.JSONDecodeError as e:
            corrupt_path = self._preserve_corrupt_state_file()
            raise StateLoadError(
                f"State file is corrupt and cannot be loaded: {self.state_file}\n"
                f"Parse error: {e}\n"
                f"The corrupt file has been preserved at: {corrupt_path}\n"
                "To start a fresh switchover, use --reset-state or remove the state file."
            ) from e
        except OSError as e:
            raise StateLoadError(
                f"State file cannot be read: {self.state_file}\n"
                f"I/O error: {e}\n"
                "Check file permissions. To start a fresh switchover, use --reset-state."
            ) from e

    def _validate_loaded_state(self, state: Any) -> None:
        """Validate persisted state before the workflow consumes it."""
        if not isinstance(state, dict):
            corrupt_path = self._preserve_corrupt_state_file()
            raise StateLoadError(
                f"State file has invalid structure: {self.state_file}\n"
                f"Expected a JSON object, got {type(state).__name__}.\n"
                f"The invalid file has been preserved at: {corrupt_path}\n"
                "To start a fresh switchover, use --reset-state or remove the state file."
            )

        raw_phase = state.get("current_phase", Phase.INIT.value)
        try:
            Phase(raw_phase)
        except ValueError as exc:
            corrupt_path = self._preserve_corrupt_state_file()
            raise StateLoadError(
                f"State file has an Unknown phase: {raw_phase}\n"
                f"State file: {self.state_file}\n"
                f"The invalid file has been preserved at: {corrupt_path}\n"
                "Do not continue with this state file. Use --reset-state or remove it after review."
            ) from exc

    def _preserve_corrupt_state_file(self) -> str:
        """Copy the corrupt state file to *.corrupt.<timestamp> for forensics.

        Returns the path of the forensic copy, or the original path if copying fails.
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        corrupt_path = f"{self.state_file}.corrupt.{ts}"
        try:
            shutil.copy2(self.state_file, corrupt_path)
        except OSError as exc:
            logging.getLogger("acm_switchover").warning(
                "Failed to preserve corrupt state file %s at %s: %s",
                self.state_file,
                corrupt_path,
                exc,
            )
            corrupt_path = self.state_file
        return corrupt_path

    def _new_state(self) -> Dict[str, Any]:
        """Return a fresh state structure."""
        # Import here to avoid circular import
        from lib import __version__

        return {
            "version": "1.0",
            "tool_version": __version__,
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
        """Write the provided state dict to disk atomically.

        Uses a write-to-temp + rename pattern to ensure the state file is
        never left in a corrupted state if the process crashes mid-write.
        """
        self._ensure_state_dir()
        temp_file = self.state_file + ".tmp"

        # Track temp file for cleanup (atexit handler will clean up if process crashes)
        self._active_temp_files.add(temp_file)

        def _write_temp_file() -> None:
            error: Optional[Exception] = None
            try:
                # Write to temporary file with restrictive permissions
                fd = os.open(
                    temp_file,
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                    stat.S_IRUSR | stat.S_IWUSR,
                )
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())

                # Atomic rename (POSIX guarantees this is atomic on same filesystem)
                os.replace(temp_file, self.state_file)
                # Success - remove from tracking since file was successfully renamed
                self._active_temp_files.discard(temp_file)

            except (OSError, ValueError, TypeError) as e:
                # Clean up temp file if it exists
                try:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                        self._active_temp_files.discard(temp_file)
                except OSError:
                    pass  # Best-effort cleanup - atexit handler will try again
                logging.error("Failed to write state file %s: %s", self.state_file, e)
                error = e

            if error is not None:
                raise error

        if fcntl:
            lock_file = self.state_file + ".lock"
            with open(lock_file, "w", encoding="utf-8") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                try:
                    _write_temp_file()
                finally:
                    try:
                        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
        else:
            _write_temp_file()

    def _do_flush(self, force: bool = False, suppress_errors: bool = False) -> bool:
        """Core flush logic shared by all flush methods.

        Args:
            force: If True, write even if not dirty. If False, only write when dirty.
            suppress_errors: If True, catch exceptions and print to stderr instead of raising.

        Returns:
            True if flush was performed, False if skipped (not dirty or already flushing).
        """
        if self._flushing:
            return False
        if not force and not self._dirty:
            return False

        self._flushing = True
        self.state["last_updated"] = _utc_timestamp()
        try:
            if suppress_errors:
                try:
                    self._write_state(self.state)
                except Exception as e:
                    import sys

                    print(f"Error flushing state: {e}", file=sys.stderr)
                    return False
            else:
                self._write_state(self.state)
            self._dirty = False
            return True
        finally:
            self._flushing = False

    def save_state(self) -> None:
        """Persist current state to disk if dirty."""
        self._do_flush(force=False)

    def flush_state(self) -> None:
        """Force immediate write of state to disk (for critical checkpoints)."""
        self._do_flush(force=True)

    def set_phase(self, phase: Phase) -> None:
        """Update current phase."""
        self.state["current_phase"] = phase.value
        self.flush_state()  # Phase transitions are critical checkpoints

    def record_retry_error_baseline(self, phase: Any, count: int) -> None:
        """Record the error baseline for a resumed retry attempt."""
        phase_value = phase.value if isinstance(phase, Phase) else str(phase)
        self._retry_error_baseline = {"phase": phase_value, "count": count}

    def get_retry_error_baseline(self) -> Optional[Dict[str, Any]]:
        """Return the current retry error baseline, if any."""
        return dict(self._retry_error_baseline) if self._retry_error_baseline is not None else None

    def capture_runtime_checkpoint(self) -> Dict[str, Any]:
        """Capture the durable state fields that validate-only must preserve."""
        return {
            "current_phase": self.state.get("current_phase", Phase.INIT.value),
            "errors": copy.deepcopy(self.state.get("errors", [])),
            "last_updated": self.state.get("last_updated"),
        }

    def restore_runtime_checkpoint(self, checkpoint: Dict[str, Any]) -> None:
        """Restore a previously captured runtime checkpoint without touching other state."""
        self.state["current_phase"] = checkpoint.get("current_phase", Phase.INIT.value)
        self.state["errors"] = copy.deepcopy(checkpoint.get("errors", []))

        last_updated = checkpoint.get("last_updated")
        if last_updated is None:
            self.state.pop("last_updated", None)
        else:
            self.state["last_updated"] = last_updated

        # Write directly (not via _do_flush) to preserve the checkpoint's
        # original last_updated timestamp instead of stamping a new one.
        self._dirty = False
        self._write_state(self.state)

    def capture_state_snapshot(self) -> Dict[str, Any]:
        """Capture the complete durable state for dry-run rollback."""
        return copy.deepcopy(self.state)

    def restore_state_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """Restore a complete durable state snapshot without refreshing timestamps."""
        self.state = copy.deepcopy(snapshot)
        self._dirty = False
        self._write_state(self.state)

    def mark_step_completed(self, step_name: str) -> None:
        """Mark a step as completed."""
        if not self.is_step_completed(step_name):
            self.state["completed_steps"].append({"name": step_name, "timestamp": _utc_timestamp()})
            self._dirty = True
            self.save_state()

    def clear_step_completed(self, step_name: str) -> None:
        """Clear a completed step marker so the step can run again."""
        completed_steps = self.state.get("completed_steps", [])
        filtered_steps = [step for step in completed_steps if step.get("name") != step_name]
        if len(filtered_steps) != len(completed_steps):
            self.state["completed_steps"] = filtered_steps
            self._dirty = True
            self.save_state()

    def is_step_completed(self, step_name: str) -> bool:
        """Check if a step was already completed."""
        return any(s["name"] == step_name for s in self.state["completed_steps"])

    def step(self, step_name: str, logger: Optional[logging.Logger] = None) -> "StepContext":
        """Context manager for idempotent step execution.

        This helper consolidates the common pattern of checking if a step is
        completed, executing it if not, and marking it completed afterward.

        Usage:
            with self.state.step("my_step", logger) as should_run:
                if should_run:
                    self._do_actual_work()

        The context manager:
        - Checks if the step is already completed
        - If completed, logs "Step already completed: {step_name}" and sets should_run=False
        - If not completed, sets should_run=True and marks the step completed on exit
        - Only marks the step completed if no exception was raised

        Args:
            step_name: Unique identifier for the step
            logger: Optional logger for "already completed" messages

        Returns:
            StepContext that yields True if step should run, False if already completed
        """
        return StepContext(self, step_name, logger)

    def set_config(self, key: str, value: Any) -> None:
        """Store configuration value."""
        if self.state["config"].get(key) == value:
            return
        self.state["config"][key] = value
        self._dirty = True
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
        self.flush_state()  # Errors are critical checkpoints

    def get_errors(self) -> list:
        """Retrieve list of recorded errors."""
        return self.state.get("errors", [])

    def get_last_error_phase(self) -> Optional[Phase]:
        """Get the phase where the last error occurred.

        Returns:
            Phase enum if there's an error with a valid phase, None otherwise.
        """
        errors = self.get_errors()
        if not errors:
            return None
        last_error = errors[-1]
        phase_str = last_error.get("phase")
        if not phase_str:
            return None
        try:
            return Phase(phase_str)
        except ValueError:
            logging.warning(
                "Unknown phase '%s' in last error, cannot determine resume point",
                phase_str,
            )
            return None

    def reset(self) -> None:
        """Reset state to initial."""
        self.state = self._new_state()
        self.flush_state()  # Reset is a critical checkpoint

    def get_current_phase(self) -> Phase:
        """Get current phase as enum."""
        raw_phase = self.state.get("current_phase", Phase.INIT.value)
        try:
            return Phase(raw_phase)
        except ValueError as exc:
            # Defense-in-depth: _validate_loaded_state already catches this at
            # load time, but we guard here as a safety net against direct
            # state dict mutation (e.g. self.state["current_phase"] = "bad").
            raise StateLoadError(
                f"State file has an Unknown phase: {raw_phase}\n"
                f"State file: {self.state_file}\n"
                "Use --reset-state or remove the state file after review."
            ) from exc

    def get_state_age(self) -> Optional[timedelta]:
        """Get the age of the state file based on last_updated timestamp.

        Returns:
            timedelta if the timestamp was successfully parsed, None if missing or invalid.
            Logs a warning for missing or unparseable timestamps.
        """
        last_updated_str = self.state.get("last_updated", "")
        if not last_updated_str:
            logging.warning("State file missing last_updated timestamp")
            return None

        try:
            # Handle both 'Z' suffix and explicit timezone offsets
            if last_updated_str.endswith("Z"):
                last_updated_str = last_updated_str[:-1] + "+00:00"
            return datetime.now(timezone.utc) - datetime.fromisoformat(last_updated_str)
        except (ValueError, TypeError) as e:
            logging.warning("Could not parse state timestamp: %s", e)
            return None

    def ensure_contexts(self, primary_context: str, secondary_context: Optional[str]) -> None:
        """Ensure stored contexts match the ones provided on the CLI."""
        stored = self.state.get("contexts") or {}
        desired = {"primary": primary_context, "secondary": secondary_context}

        stored_primary = stored.get("primary")
        stored_secondary = stored.get("secondary")
        has_progress = (
            bool(self.state.get("completed_steps"))
            or self.state.get("errors")
            or (self.state.get("current_phase") not in (None, Phase.INIT.value))
        )

        state_changed = False

        if stored_primary is None and stored_secondary is None:
            if has_progress:
                logging.warning(
                    "Stored state contexts are missing for an in-progress state. Resetting state.",
                )
                self.state = self._new_state()
                state_changed = True
        elif stored_primary != primary_context or stored_secondary != secondary_context:
            logging.warning(
                "Stored state contexts (%s/%s) differ from current invocation (%s/%s). "
                "Resetting state to avoid mixing runs.",
                stored_primary,
                stored_secondary,
                primary_context,
                secondary_context,
            )
            self.state = self._new_state()
            state_changed = True

        if self.state.get("contexts") != desired:
            self.state["contexts"] = desired
            state_changed = True

        if state_changed:
            self.flush_state()  # Context changes are critical checkpoints

    def _flush_on_signal(self, signum: int, frame: Any) -> None:
        """Flush pending state changes on termination signal (signal handler).

        This handler is registered for SIGTERM and SIGINT to ensure dirty state
        is persisted before the process terminates. This is critical because
        atexit handlers don't run on SIGKILL and may not run reliably on SIGTERM.

        Args:
            signum: Signal number (SIGTERM or SIGINT)
            frame: Current stack frame (unused)
        """
        self._do_flush(force=False, suppress_errors=True)
        self._forward_signal(signum, frame)

    def _forward_signal(self, signum: int, frame: Any) -> None:
        """Invoke the previous signal handler or restore default behavior."""
        previous = self._previous_signal_handlers.get(signum, signal.SIG_DFL)
        if previous is signal.SIG_IGN:
            return
        if callable(previous):
            previous(signum, frame)
            return
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    def _flush_on_exit(self) -> None:
        """Flush pending state changes on program exit (atexit handler)."""
        self._do_flush(force=False, suppress_errors=True)

    def _cleanup_temp_files(self) -> None:
        """Clean up any remaining temp files on program exit (atexit handler).

        This handles cases where the process crashes before temp files are cleaned up.
        """
        for temp_file in list(self._active_temp_files):
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass  # Best-effort cleanup


class StepContext:
    """Context manager for idempotent step execution."""

    def __init__(
        self,
        state_manager: "StateManager",
        step_name: str,
        logger: Optional[logging.Logger] = None,
    ):
        self._state = state_manager
        self._step_name = step_name
        self._logger = logger
        self._should_run = False

    def __enter__(self) -> bool:
        """Check if step should run.

        Returns:
            True if step should execute, False if already completed
        """
        if self._state.is_step_completed(self._step_name):
            if self._logger:
                self._logger.info("Step already completed: %s", self._step_name)
            self._should_run = False
        else:
            self._should_run = True
        return self._should_run

    def __exit__(self, exc_type, exc_val, exc_tb) -> Literal[False]:
        """Mark step completed if it ran successfully."""
        # Only mark completed if:
        # 1. The step was supposed to run (_should_run is True)
        # 2. No exception occurred (exc_type is None)
        if self._should_run and exc_type is None:
            self._state.mark_step_completed(self._step_name)
        # Don't suppress exceptions
        return False


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
