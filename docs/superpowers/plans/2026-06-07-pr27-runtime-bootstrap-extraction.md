# PR27 Runtime Bootstrap Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the runtime/bootstrap helper cluster from `acm_switchover.py` so `main()` delegates state-manager setup, state-binding decisions, and client initialization through one focused runtime-preparation path while preserving current CLI behavior and existing `acm_switchover.*` patch/import surfaces.

**Architecture:** Add a new `lib/runtime_bootstrap.py` module for the leaf runtime helpers plus a `RuntimeContext` dataclass. Keep `_resolve_state_file()` and the early `--setup` gate ordered exactly as they are today, then add a thin `_prepare_runtime()` wrapper in `acm_switchover.py` that uses module-level helper aliases such as `_initialize_clients()` and `_collect_hub_identities()` so existing tests can keep patching `acm_switchover.*` while `main()` loses the long bootstrap block.

**Tech Stack:** Python, `argparse`, `pytest`, `lib.utils.StateManager`, `lib.KubeClient`, Thermos tracker docs, Graphify.

---

## File Map

- Create: `docs/superpowers/plans/2026-06-07-pr27-runtime-bootstrap-extraction.md`
- Create: `lib/runtime_bootstrap.py`
- Create: `tests/test_runtime_bootstrap.py`
- Modify: `acm_switchover.py`
- Modify: `tests/test_main.py`
- Modify: `thermos-resolution-plan.md`
- Test: `tests/test_main_phase_flow.py`
- Test: `tests/test_main_argocd_resume.py`
- Test: `tests/test_state_dir_env_var.py`
- Test: `tests/release/scenarios/test_runtime_parity.py`
- Test: `tests/test_documentation_guardrails.py`

### Planned Responsibilities

- `lib/runtime_bootstrap.py`: leaf runtime helpers (`resolve_state_file`, `initialize_clients`, identity/state helpers) plus `RuntimeContext`
- `acm_switchover.py`: compatibility aliases, new `_prepare_runtime()`, slimmer `main()`
- `tests/test_runtime_bootstrap.py`: direct unit coverage for the new helper module
- `tests/test_main.py`: regression coverage for `_prepare_runtime()` and preserved `main()` ordering/binding behavior
- `thermos-resolution-plan.md`: PR27 implementation verification evidence after the refactor is green

## Task 1: Create The Runtime Bootstrap Helper Module

**Files:**
- Create: `lib/runtime_bootstrap.py`
- Create: `tests/test_runtime_bootstrap.py`
- Test: `tests/test_state_dir_env_var.py`

- [ ] **Step 1: Write the failing direct unit tests for the new helper module**

```python
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lib.runtime_bootstrap import (
    client_context_name,
    collect_hub_identities,
    initialize_clients,
    resolve_state_file,
    state_contexts,
    stored_hub_identities,
)


def test_resolve_state_file_prefers_requested_path(monkeypatch):
    monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", "/tmp/acm-state")

    assert resolve_state_file(
        requested_path="custom/state.json",
        primary_ctx="hub-a",
        secondary_ctx="hub-b",
    ) == "custom/state.json"


def test_initialize_clients_passes_dry_run_to_each_context():
    args = SimpleNamespace(primary_context="hub-a", secondary_context="hub-b", dry_run=True)
    logger = Mock()

    with patch("lib.runtime_bootstrap.KubeClient") as kube_client:
        primary_client = Mock(name="primary-client")
        secondary_client = Mock(name="secondary-client")
        kube_client.side_effect = [primary_client, secondary_client]

        assert initialize_clients(args, logger) == (primary_client, secondary_client)


def test_collect_hub_identities_reads_only_present_clients():
    primary = Mock()
    primary.get_cluster_identity.return_value = {"context": "hub-a", "cluster_uid": "uid-a"}

    assert collect_hub_identities(primary, None) == {
        "primary": {"context": "hub-a", "cluster_uid": "uid-a"},
    }


def test_state_helpers_tolerate_missing_state_shapes():
    state = Mock()
    state.state = {"contexts": {"primary": "hub-a", "secondary": "hub-b"}}

    assert state_contexts(state) == ("hub-a", "hub-b")
    assert stored_hub_identities(state) == {}
    assert client_context_name(SimpleNamespace(context="hub-a")) == "hub-a"
    assert client_context_name(SimpleNamespace(context=None)) is None
```

- [ ] **Step 2: Run the new helper tests and confirm they fail before the module exists**

Run: `python -m pytest tests/test_runtime_bootstrap.py tests/test_state_dir_env_var.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'lib.runtime_bootstrap'`

- [ ] **Step 3: Create `lib/runtime_bootstrap.py` with the extracted leaf helpers**

```python
import argparse
import logging
import os
from dataclasses import dataclass
from typing import Optional

from lib import KubeClient, StateManager
from lib.validation import InputValidator

STATE_DIR_ENV_VAR = "ACM_SWITCHOVER_STATE_DIR"


@dataclass(frozen=True)
class RuntimeContext:
    state_file: str
    state: StateManager
    primary: Optional[KubeClient]
    secondary: Optional[KubeClient]
    should_bind_state: bool
    should_record_state_errors: bool


def sanitize_context_identifier(value: str) -> str:
    return InputValidator.sanitize_context_identifier(value)


def get_default_state_dir() -> str:
    env_state_dir = os.environ.get(STATE_DIR_ENV_VAR)
    if env_state_dir and env_state_dir.strip():
        return env_state_dir.strip()
    return ".state"


def build_default_state_file(primary_ctx: Optional[str], secondary_ctx: Optional[str]) -> str:
    primary_label = primary_ctx or "restore-only"
    secondary_label = secondary_ctx or "none"
    slug = f"{sanitize_context_identifier(primary_label)}__{sanitize_context_identifier(secondary_label)}"
    return os.path.join(get_default_state_dir(), f"switchover-{slug}.json")


def find_resume_state_candidates(secondary_ctx: str) -> list[str]:
    state_dir = get_default_state_dir()
    if not os.path.isdir(state_dir):
        return []

    secondary_slug = sanitize_context_identifier(secondary_ctx)
    suffix = f"__{secondary_slug}.json"
    candidates = []
    for entry in os.listdir(state_dir):
        if not entry.startswith("switchover-") or not entry.endswith(suffix):
            continue
        path = os.path.join(state_dir, entry)
        if os.path.isfile(path):
            candidates.append(path)
    return sorted(candidates)


def resolve_state_file(
    requested_path: Optional[str],
    primary_ctx: Optional[str],
    secondary_ctx: Optional[str],
    argocd_resume_only: bool = False,
) -> str:
    if requested_path:
        return requested_path

    default_path = build_default_state_file(primary_ctx, secondary_ctx)
    if not argocd_resume_only or not secondary_ctx:
        return default_path

    if primary_ctx is None:
        candidates = find_resume_state_candidates(secondary_ctx)
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise ValueError(
                "Multiple candidate state files found for --argocd-resume-only "
                f"matching secondary context {secondary_ctx}: {', '.join(candidates)}. "
                "Pass --state-file explicitly or provide --primary-context to disambiguate."
            )
        return default_path

    reversed_path = build_default_state_file(secondary_ctx, primary_ctx)
    default_exists = os.path.exists(default_path)
    reversed_exists = os.path.exists(reversed_path)

    if reversed_exists and not default_exists:
        return reversed_path

    if default_exists and reversed_exists and default_path != reversed_path:
        raise ValueError(
            "Multiple candidate state files found for --argocd-resume-only "
            f"({default_path} and {reversed_path}). "
            "Pass --state-file explicitly to choose the correct resume state."
        )

    return default_path


def initialize_clients(
    args: argparse.Namespace,
    logger: logging.Logger,
) -> tuple[Optional[KubeClient], Optional[KubeClient]]:
    primary = None
    if getattr(args, "primary_context", None):
        logger.info("Connecting to primary hub: %s", args.primary_context)
        primary = KubeClient(args.primary_context, dry_run=args.dry_run)

    secondary = None
    if args.secondary_context:
        logger.info("Connecting to secondary hub: %s", args.secondary_context)
        secondary = KubeClient(args.secondary_context, dry_run=args.dry_run)

    return primary, secondary


def collect_hub_identities(
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
) -> dict[str, dict[str, Optional[str]]]:
    identities: dict[str, dict[str, Optional[str]]] = {}
    if primary is not None:
        identities["primary"] = primary.get_cluster_identity()
    if secondary is not None:
        identities["secondary"] = secondary.get_cluster_identity()
    return identities


def stored_hub_identities(state: StateManager) -> dict:
    state_data = getattr(state, "state", {}) or {}
    if not isinstance(state_data, dict):
        return {}
    identities = state_data.get("hub_identities") or {}
    return identities if isinstance(identities, dict) else {}


def state_contexts(state: StateManager) -> tuple[Optional[str], Optional[str]]:
    state_data = getattr(state, "state", {}) or {}
    if not isinstance(state_data, dict):
        return None, None
    stored_contexts = state_data.get("contexts") or {}
    if not isinstance(stored_contexts, dict):
        return None, None
    return stored_contexts.get("primary"), stored_contexts.get("secondary")


def client_context_name(client: Optional[KubeClient]) -> Optional[str]:
    context = getattr(client, "context", None)
    return context if isinstance(context, str) and context else None
```

- [ ] **Step 4: Run the direct helper tests plus the existing `_resolve_state_file` compatibility tests**

Run: `python -m pytest tests/test_runtime_bootstrap.py tests/test_state_dir_env_var.py -q`

Expected: PASS

- [ ] **Step 5: Commit the helper-module slice**

```bash
git add lib/runtime_bootstrap.py tests/test_runtime_bootstrap.py
git commit -m "refactor: add runtime bootstrap helpers"
```

## Task 2: Add `_prepare_runtime()` And Slim `main()`

**Files:**
- Modify: `acm_switchover.py:95-95`
- Modify: `acm_switchover.py:1330-1805`
- Modify: `tests/test_main.py:1483-1900`
- Test: `tests/test_main_phase_flow.py`
- Test: `tests/test_main_argocd_resume.py`
- Test: `tests/test_state_dir_env_var.py`

- [ ] **Step 1: Add failing `_prepare_runtime()` regression tests to `tests/test_main.py`**

```python
from acm_switchover import (
    _attempt_argocd_resume_on_failure,
    _fail_phase,
    _initialize_clients,
    _prepare_runtime,
    _report_argocd_acm_impact,
    _run_argocd_resume_only,
    _run_phase_activation,
    _run_phase_finalization,
    _run_phase_post_activation,
    _run_phase_preflight,
    _run_restore_only_argocd_pause,
    main,
    parse_args,
    run_restore_only,
    run_switchover,
    validate_args,
)


@pytest.mark.unit
class TestPrepareRuntime:
    def test_prepare_runtime_binds_contexts_and_persists_hub_identities(self):
        args = TestMainGitOpsReporting._base_args()
        logger = Mock()
        state = Mock()
        primary = Mock()
        secondary = Mock()
        primary.get_cluster_identity.return_value = {"context": "primary", "cluster_uid": "uid-primary"}
        secondary.get_cluster_identity.return_value = {"context": "secondary", "cluster_uid": "uid-secondary"}

        with patch("acm_switchover.StateManager", return_value=state), patch(
            "acm_switchover._initialize_clients", return_value=(primary, secondary)
        ):
            runtime = _prepare_runtime(args, logger, "state.json")

        assert runtime.state is state
        assert runtime.primary is primary
        assert runtime.secondary is secondary
        assert runtime.should_bind_state is True
        assert runtime.should_record_state_errors is True
        state.ensure_contexts.assert_called_once_with("primary", "secondary")
        state.ensure_hub_identities.assert_called_once_with(
            {
                "primary": {"context": "primary", "cluster_uid": "uid-primary"},
                "secondary": {"context": "secondary", "cluster_uid": "uid-secondary"},
            },
            allow_legacy_backfill=False,
            persist=True,
        )

    def test_prepare_runtime_uses_non_persistent_identity_binding_for_dry_run(self):
        args = TestMainGitOpsReporting._base_args()
        args.dry_run = True
        logger = Mock()
        state = Mock()
        primary = Mock()
        secondary = Mock()
        primary.get_cluster_identity.return_value = {"context": "primary", "cluster_uid": "uid-primary"}
        secondary.get_cluster_identity.return_value = {"context": "secondary", "cluster_uid": "uid-secondary"}

        with patch("acm_switchover.StateManager", return_value=state), patch(
            "acm_switchover._initialize_clients", return_value=(primary, secondary)
        ):
            runtime = _prepare_runtime(args, logger, "state.json")

        assert runtime.should_bind_state is True
        state.ensure_hub_identities.assert_called_once_with(
            {
                "primary": {"context": "primary", "cluster_uid": "uid-primary"},
                "secondary": {"context": "secondary", "cluster_uid": "uid-secondary"},
            },
            allow_legacy_backfill=False,
            persist=False,
        )

    def test_prepare_runtime_removes_existing_state_file_before_state_manager(self, tmp_path):
        args = TestMainGitOpsReporting._base_args()
        args.reset_state = True
        state_path = tmp_path / "state.json"
        state_path.write_text("{}", encoding="utf-8")
        logger = Mock()
        state = Mock()

        with patch("acm_switchover.StateManager", return_value=state) as state_manager, patch(
            "acm_switchover._initialize_clients", return_value=(Mock(), Mock())
        ):
            runtime = _prepare_runtime(args, logger, str(state_path))

        assert runtime.state_file == str(state_path)
        assert not state_path.exists()
        state_manager.assert_called_once_with(str(state_path))
```

- [ ] **Step 2: Run the main-entry regression tests and confirm `_prepare_runtime()` is still missing**

Run: `python -m pytest tests/test_main.py -q`

Expected: FAIL with `ImportError: cannot import name '_prepare_runtime' from 'acm_switchover'`

- [ ] **Step 3: Re-export the moved helpers and add `_prepare_runtime()` in `acm_switchover.py`**

```python
from lib import runtime_bootstrap

RuntimeContext = runtime_bootstrap.RuntimeContext
_build_default_state_file = runtime_bootstrap.build_default_state_file
_client_context_name = runtime_bootstrap.client_context_name
_collect_hub_identities = runtime_bootstrap.collect_hub_identities
_find_resume_state_candidates = runtime_bootstrap.find_resume_state_candidates
_get_default_state_dir = runtime_bootstrap.get_default_state_dir
_initialize_clients = runtime_bootstrap.initialize_clients
_resolve_state_file = runtime_bootstrap.resolve_state_file
_sanitize_context_identifier = runtime_bootstrap.sanitize_context_identifier
_state_contexts = runtime_bootstrap.state_contexts
_stored_hub_identities = runtime_bootstrap.stored_hub_identities


def _prepare_runtime(
    args: argparse.Namespace,
    logger: logging.Logger,
    resolved_state_file: str,
) -> RuntimeContext:
    if getattr(args, "reset_state", False) and os.path.exists(resolved_state_file):
        logger.warning("Resetting state file: %s", resolved_state_file)
        try:
            os.remove(resolved_state_file)
        except OSError as exc:
            logger.error("Failed to remove state file: %s", exc)
            sys.exit(EXIT_FAILURE)

    try:
        state = StateManager(resolved_state_file)
    except (StateLoadError, StateLockError) as exc:
        logger.error("")
        logger.error("FATAL: Cannot initialize switchover state file.")
        logger.error("%s", exc)
        logger.error("")
        if isinstance(exc, StateLoadError):
            logger.error("To start a fresh switchover run:")
            logger.error("  --reset-state  (removes and recreates the state file)")
            logger.error("  or manually remove: %s", resolved_state_file)
        sys.exit(EXIT_FAILURE)

    should_bind_state = not getattr(args, "argocd_resume_only", False) and not getattr(args, "decommission", False)
    should_record_state_errors = not getattr(args, "decommission", False)

    if should_bind_state:
        state.ensure_contexts(getattr(args, "primary_context", None), args.secondary_context)

    try:
        primary, secondary = _initialize_clients(args, logger)
    except Exception as exc:  # pragma: no cover - fatal init error
        logger.error("Failed to initialize Kubernetes clients: %s", exc)
        sys.exit(EXIT_FAILURE)

    if should_bind_state:
        state.ensure_hub_identities(
            _collect_hub_identities(primary, secondary),
            allow_legacy_backfill=getattr(args, "force", False),
            persist=not (getattr(args, "dry_run", False) or getattr(args, "validate_only", False)),
        )

    return RuntimeContext(
        state_file=resolved_state_file,
        state=state,
        primary=primary,
        secondary=secondary,
        should_bind_state=should_bind_state,
        should_record_state_errors=should_record_state_errors,
    )


def main():  # noqa: C901
    args = parse_args()
    state: Optional[StateManager] = None
    logger = setup_logging(args.verbose, args.log_format)

    validate_args(args, logger)
    try:
        resolved_state_file = _resolve_state_file(
            args.state_file,
            getattr(args, "primary_context", None),
            args.secondary_context,
            argocd_resume_only=getattr(args, "argocd_resume_only", False),
        )
    except ValueError as exc:
        logger.error("%s", exc)
        sys.exit(EXIT_FAILURE)
    args.state_file = resolved_state_file

    logger.info("ACM Hub Switchover Automation v%s (%s)", __version__, __version_date__)
    logger.info("Started at: %s", datetime.now(timezone.utc).isoformat())
    logger.info("Using state file: %s", resolved_state_file)

    if args.skip_gitops_check:
        GitOpsCollector.get_instance().set_enabled(False)
        logger.debug("GitOps marker detection disabled")

    if args.setup:
        try:
            success = run_setup(args, logger)
        except KeyboardInterrupt:
            logger.warning("\n\nSetup interrupted by user")
            sys.exit(EXIT_INTERRUPT)
        except Exception as exc:
            logger.error("\n✗ Setup failed: %s", exc, exc_info=args.verbose)
            sys.exit(EXIT_FAILURE)

        if success:
            logger.info("\n✓ Setup completed successfully!")
            sys.exit(EXIT_SUCCESS)
        else:
            logger.error("\n✗ Setup failed!")
            sys.exit(EXIT_FAILURE)

    if getattr(args, "argocd_resume_only", False) and not os.path.exists(resolved_state_file):
        logger.error(
            "State file not found for --argocd-resume-only: %s. "
            "Run a switchover with Argo CD management first or pass --state-file explicitly.",
            resolved_state_file,
        )
        sys.exit(EXIT_FAILURE)

    runtime = _prepare_runtime(args, logger, resolved_state_file)
    state = runtime.state
    primary = runtime.primary
    secondary = runtime.secondary
    should_bind_state = runtime.should_bind_state
    should_record_state_errors = runtime.should_record_state_errors

    operation_exit_code = EXIT_FAILURE
    try:
        if getattr(args, "argocd_resume_only", False):
            success = _run_argocd_resume_only(args, state, primary, secondary, logger)
        else:
            success = _execute_operation(args, state, primary, secondary, logger)
    except KeyboardInterrupt:
        logger.warning("\n\nOperation interrupted by user")
        logger.info("State saved to: %s", args.state_file)
        logger.info("Re-run the same command to resume from last successful step")
        operation_exit_code = EXIT_INTERRUPT
    except SwitchoverError as exc:
        logger.error("\n✗ %s", exc)
        if should_record_state_errors:
            state.add_error(str(exc))
        operation_exit_code = EXIT_FAILURE
    except Exception as exc:
        logger.error("\n✗ Unexpected error: %s", exc, exc_info=args.verbose)
        if should_record_state_errors:
            state.add_error(str(exc))
        operation_exit_code = EXIT_FAILURE
    else:
        if success:
            if getattr(args, "argocd_resume_only", False):
                logger.info("\n✓ Argo CD resume completed successfully!")
            else:
                logger.info("\n✓ Operation completed successfully!")
            operation_exit_code = EXIT_SUCCESS
        else:
            if getattr(args, "argocd_resume_only", False):
                logger.error("\n✗ Argo CD resume failed or had nothing to restore.")
            else:
                logger.error("\n✗ Operation failed!")
            operation_exit_code = EXIT_FAILURE
    finally:
        _write_python_report(
            args,
            state,
            "pass" if operation_exit_code == EXIT_SUCCESS else "fail",
            logger,
        )
        GitOpsCollector.get_instance().print_report()

    sys.exit(operation_exit_code)
```

- [ ] **Step 4: Run the full runtime/bootstrap regression surface**

Run: `python -m pytest tests/test_runtime_bootstrap.py tests/test_main.py tests/test_main_phase_flow.py tests/test_main_argocd_resume.py tests/test_state_dir_env_var.py -q`

Expected: PASS

- [ ] **Step 5: Commit the `main()` integration slice**

```bash
git add acm_switchover.py tests/test_main.py
git commit -m "refactor: isolate runtime bootstrap in main"
```

## Task 3: Update The Thermos Tracker And Run Final Verification

**Files:**
- Modify: `thermos-resolution-plan.md:149-155`
- Test: `tests/release/scenarios/test_runtime_parity.py`
- Test: `tests/test_documentation_guardrails.py`

- [ ] **Step 1: Update PR27 tracker evidence after the code refactor is green**

```markdown
| 27 | ready_for_review | `refactor/thermos-27-safety-file-decomposition` | `.worktrees/thermos-27-file-decomposition` | F44 orchestrator runtime/bootstrap extraction | — | Added design spec `docs/superpowers/specs/2026-06-07-pr27-orchestrator-decomposition-design.md` and implementation plan `docs/superpowers/plans/2026-06-07-pr27-runtime-bootstrap-extraction.md`. Runtime/bootstrap helpers now live under `lib/runtime_bootstrap.py`, `main()` delegates state/bootstrap setup through `_prepare_runtime()`, and the existing `acm_switchover` patch/import surface remains stable. Verification: `python -m pytest tests/test_runtime_bootstrap.py tests/test_main.py tests/test_main_phase_flow.py tests/test_main_argocd_resume.py tests/test_state_dir_env_var.py -q` passed; `python -m pytest tests/release/scenarios/test_runtime_parity.py tests/test_documentation_guardrails.py -q` passed; `graphify update .` passed; `git diff --check` passed. |
```

- [ ] **Step 2: Run the release-parity and documentation guardrails**

Run: `python -m pytest tests/release/scenarios/test_runtime_parity.py tests/test_documentation_guardrails.py -q`

Expected: PASS

- [ ] **Step 3: Refresh the graph after code-file changes**

Run: `graphify update .`

Expected: exit code 0

- [ ] **Step 4: Run the final diff sanity check**

Run: `git diff --check`

Expected: no output, exit code 0

- [ ] **Step 5: Commit the tracker update and final verification state**

```bash
git add thermos-resolution-plan.md
git commit -m "docs: record PR27 runtime bootstrap verification"
```
