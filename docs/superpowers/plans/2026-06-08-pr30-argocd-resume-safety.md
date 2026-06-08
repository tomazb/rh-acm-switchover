# PR30 Argo CD Resume Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the fail-closed Argo CD resume trio from `acm_switchover.py` into `lib/argocd_resume.py` while preserving the current `acm_switchover.*` wrapper surface, wrong-hub protections, legacy-state fail-closed behavior, and restore-only versus switchover retry semantics.

**Architecture:** Add `lib/argocd_resume.py` as the direct owner of client-resolution, resume-only execution, and resume-on-failure cleanup. Keep `_prepare_argocd_resume_clients()`, `_run_argocd_resume_only()`, and `_attempt_argocd_resume_on_failure()` in `acm_switchover.py` as thin delegating wrappers so the runner hooks and existing tests stay stable, and leave `_run_restore_only_argocd_pause()` untouched in `acm_switchover.py`.

**Tech Stack:** Python, `pytest`, `unittest.mock`, `lib.runtime_bootstrap`, `lib.argocd`, `lib.argocd_coordinator`, Thermos tracker docs.

---

## File Map

- Create: `lib/argocd_resume.py`
- Create: `tests/test_argocd_resume_helpers.py`
- Modify: `acm_switchover.py`
- Modify: `tests/test_main.py`
- Modify: `thermos-resolution-plan.md`
- Test: `tests/test_main_argocd_resume.py`
- Test: `tests/test_main_phase_flow.py`
- Test: `tests/test_operation_runners.py`
- Test: `tests/test_documentation_guardrails.py`

### Planned Responsibilities

- `lib/argocd_resume.py`: direct owner of resume client preparation, resume-only execution, and resume-on-failure cleanup
- `acm_switchover.py`: compatibility wrappers that preserve the current patch/import surface
- `tests/test_argocd_resume_helpers.py`: direct unit coverage for the extracted module
- `tests/test_main_argocd_resume.py`: wrapper-level regression coverage for the existing `acm_switchover` entrypoints
- `tests/test_main.py`: compatibility coverage proving the wrappers delegate into `lib.argocd_resume`
- `thermos-resolution-plan.md`: PR30 spec/plan paths, scope statement, and final verification evidence

## Task 1: Create The Resume Helper Module And Direct Client-Preparation Tests

**Files:**
- Create: `lib/argocd_resume.py`
- Create: `tests/test_argocd_resume_helpers.py`

- [ ] **Step 1: Write failing direct tests for the extracted client-preparation helper**

```python
import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from lib.argocd_resume import prepare_argocd_resume_clients


def _identity_client(context, uid):
    client = Mock()
    client.context = context
    client.get_cluster_identity.return_value = {"context": context, "cluster_uid": uid}
    return client


def _mock_state(paused_apps, *, primary_ctx="hub-a", secondary_ctx="hub-b", identities=True):
    state = Mock()
    state.state = {"contexts": {"primary": primary_ctx, "secondary": secondary_ctx}}
    if identities:
        state.state["hub_identities"] = {
            "primary": {"context": primary_ctx, "cluster_uid": "uid-primary"},
            "secondary": {"context": secondary_ctx, "cluster_uid": "uid-secondary"},
        }
    state.ensure_hub_identities = Mock()
    state.get_config.side_effect = lambda key, default=None: {
        "argocd_run_id": "run-1",
        "argocd_paused_apps": paused_apps,
    }.get(key, default)
    return state


def test_prepare_resume_clients_swaps_reversed_contexts():
    args = SimpleNamespace(primary_context="hub-b", secondary_context="hub-a", dry_run=False, force=False)
    paused_apps = [{"hub": "primary", "namespace": "argocd", "name": "app-1"}]
    state = _mock_state(paused_apps)
    primary = _identity_client("hub-a", "uid-primary")
    secondary = _identity_client("hub-b", "uid-secondary")
    logger = logging.getLogger("test.prepare.swap")

    resume_primary, resume_secondary = prepare_argocd_resume_clients(
        args,
        state,
        paused_apps,
        primary,
        secondary,
        logger,
        allow_primary_load_from_state=True,
    )

    assert resume_primary is secondary
    assert resume_secondary is primary


def test_prepare_resume_clients_requires_force_for_missing_hub_identities():
    args = SimpleNamespace(primary_context="hub-a", secondary_context="hub-b", dry_run=False, force=False)
    paused_apps = [{"hub": "secondary", "namespace": "argocd", "name": "app-2"}]
    state = _mock_state(paused_apps, identities=False)
    primary = _identity_client("hub-a", "uid-primary")
    secondary = _identity_client("hub-b", "uid-secondary")
    logger = logging.getLogger("test.prepare.identity_missing")

    with pytest.raises(ValueError, match="missing hub identity data"):
        prepare_argocd_resume_clients(
            args,
            state,
            paused_apps,
            primary,
            secondary,
            logger,
            allow_primary_load_from_state=True,
        )


def test_prepare_resume_clients_loads_primary_client_only_when_allowed():
    args = SimpleNamespace(primary_context=None, secondary_context="hub-b", dry_run=False, force=False)
    paused_apps = [{"hub": "primary", "namespace": "argocd", "name": "app-1"}]
    state = _mock_state(paused_apps)
    secondary = _identity_client("hub-b", "uid-secondary")
    created_primary = _identity_client("hub-a", "uid-primary")
    logger = logging.getLogger("test.prepare.load_primary")

    with patch("lib.argocd_resume.KubeClient", return_value=created_primary) as kube_client:
        resume_primary, resume_secondary = prepare_argocd_resume_clients(
            args,
            state,
            paused_apps,
            None,
            secondary,
            logger,
            allow_primary_load_from_state=True,
        )

    kube_client.assert_called_once_with("hub-a", dry_run=False)
    assert resume_primary is created_primary
    assert resume_secondary is secondary
```

- [ ] **Step 2: Run the new direct tests and confirm the module does not exist yet**

Run: `python -m pytest tests/test_argocd_resume_helpers.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'lib.argocd_resume'`

- [ ] **Step 3: Create `lib/argocd_resume.py` with the extracted client-preparation function**

```python
from __future__ import annotations

import logging
from typing import Any, Optional

from lib.kube_client import KubeClient
from lib import runtime_bootstrap


def prepare_argocd_resume_clients(
    args: Any,
    state: Any,
    paused_apps: list[dict[str, Any]],
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    logger: logging.Logger,
    *,
    allow_primary_load_from_state: bool,
) -> tuple[Optional[KubeClient], Optional[KubeClient]]:
    """Resolve client mapping and validate hub identity bindings before resume."""
    resume_primary = primary
    resume_secondary = secondary

    stored_primary_ctx, stored_secondary_ctx = runtime_bootstrap.state_contexts(state)
    current_primary_ctx = getattr(args, "primary_context", None) or runtime_bootstrap.client_context_name(primary)
    current_secondary_ctx = getattr(args, "secondary_context", None) or runtime_bootstrap.client_context_name(secondary)

    if stored_primary_ctx or stored_secondary_ctx:
        if stored_primary_ctx == current_secondary_ctx and stored_secondary_ctx == current_primary_ctx:
            logger.info("Argo CD resume contexts are reversed from the recorded state; swapping client mapping.")
            resume_primary, resume_secondary = secondary, primary
        elif (current_primary_ctx is not None and stored_primary_ctx != current_primary_ctx) or (
            current_secondary_ctx is not None and stored_secondary_ctx != current_secondary_ctx
        ):
            if not getattr(args, "force", False):
                raise ValueError(
                    "Argo CD resume contexts "
                    f"({current_primary_ctx}/{current_secondary_ctx}) differ from recorded state "
                    f"({stored_primary_ctx}/{stored_secondary_ctx}). "
                    "This may indicate wrong-hub resume. Use --force to override, "
                    "or --state-file to specify the correct state."
                )
            logger.warning(
                "Argo CD resume contexts (%s/%s) differ from recorded state (%s/%s); "
                "--force used, preserving state and using the provided client mapping.",
                current_primary_ctx,
                current_secondary_ctx,
                stored_primary_ctx,
                stored_secondary_ctx,
            )

    primary_apps_recorded = any(isinstance(item, dict) and item.get("hub") == "primary" for item in paused_apps)
    if primary_apps_recorded and resume_primary is None:
        if not stored_primary_ctx:
            raise ValueError(
                "Argo CD resume state references Applications paused on the primary hub, "
                "but the recorded primary context is missing. Pass --primary-context or "
                "--state-file for a valid switchover state."
            )
        if stored_primary_ctx == current_secondary_ctx and secondary is not None:
            resume_primary = secondary
        elif allow_primary_load_from_state:
            logger.info(
                "Argo CD resume primary context omitted; loading recorded primary hub client: %s",
                stored_primary_ctx,
            )
            resume_primary = KubeClient(stored_primary_ctx, dry_run=getattr(args, "dry_run", False))

    stored_identities = runtime_bootstrap.stored_hub_identities(state)
    if stored_identities.get("primary") and resume_primary is None and stored_primary_ctx:
        if stored_primary_ctx == current_secondary_ctx and secondary is not None:
            resume_primary = secondary
        elif allow_primary_load_from_state:
            logger.info(
                "Argo CD resume identity validation loading recorded primary hub client: %s",
                stored_primary_ctx,
            )
            resume_primary = KubeClient(stored_primary_ctx, dry_run=getattr(args, "dry_run", False))

    if not stored_identities:
        if not getattr(args, "force", False):
            raise ValueError(
                "Argo CD resume state is missing hub identity data for recorded paused Applications. "
                "Refusing to resume because context names alone cannot prove the same live clusters. "
                "Use --force after manual verification to bind this legacy state to the current hubs."
            )
        logger.warning(
            "Argo CD resume state is missing hub identity data; "
            "--force used, binding legacy state to the current hubs for this resume attempt."
        )

    live_identities = runtime_bootstrap.collect_hub_identities(resume_primary, resume_secondary)
    known_hub_roles = {"primary", "secondary"}
    required_roles = {
        entry.get("hub") for entry in paused_apps if isinstance(entry, dict) and entry.get("hub") in known_hub_roles
    }
    required_roles.update(role for role in stored_identities.keys() if role in known_hub_roles)
    missing_roles = sorted(role for role in required_roles if role not in live_identities)
    if missing_roles:
        raise ValueError(
            "Argo CD resume hub identity validation failed: missing live client for recorded "
            + ", ".join(missing_roles)
            + " hub identity."
        )

    state.ensure_hub_identities(
        live_identities,
        allow_legacy_backfill=getattr(args, "force", False),
        persist=False,
    )
    return resume_primary, resume_secondary
```

- [ ] **Step 4: Re-run the direct tests and verify they pass**

Run: `python -m pytest tests/test_argocd_resume_helpers.py -q`

Expected: PASS

- [ ] **Step 5: Commit the extracted client-preparation slice**

```bash
git add lib/argocd_resume.py tests/test_argocd_resume_helpers.py
git commit -m "refactor: extract argocd resume client preparation"
```

## Task 2: Extract Resume-Only And Resume-On-Failure Workflow Logic

**Files:**
- Modify: `lib/argocd_resume.py`
- Modify: `tests/test_argocd_resume_helpers.py`
- Test: `tests/test_main_argocd_resume.py`

- [ ] **Step 1: Extend the direct test file with failing workflow tests**

```python
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lib.argocd_resume import attempt_argocd_resume_on_failure, run_argocd_resume_only


def test_run_argocd_resume_only_rejects_dry_run_pause_state():
    state = Mock()
    state.get_config.side_effect = lambda key, default=None: {
        "argocd_pause_dry_run": True,
        "argocd_run_id": "run-1",
        "argocd_paused_apps": [{"hub": "secondary", "namespace": "argocd", "name": "app-1"}],
    }.get(key, default)
    logger = Mock()

    assert run_argocd_resume_only(SimpleNamespace(), state, Mock(), Mock(), logger) is False


def test_run_argocd_resume_only_uses_prepare_clients_and_resume_summary():
    args = SimpleNamespace()
    state = Mock()
    paused_apps = [{"hub": "secondary", "namespace": "argocd", "name": "app-1"}]
    state.get_config.side_effect = lambda key, default=None: {
        "argocd_pause_dry_run": False,
        "argocd_run_id": "run-1",
        "argocd_paused_apps": paused_apps,
    }.get(key, default)
    primary = Mock()
    secondary = Mock()
    logger = Mock()

    with patch("lib.argocd_resume.prepare_argocd_resume_clients", return_value=(primary, secondary)) as prepare_clients, patch(
        "lib.argocd_resume.argocd_lib.resume_recorded_applications"
    ) as resume_recorded:
        resume_recorded.return_value = SimpleNamespace(restored=1, already_resumed=0, failed=0)

        assert run_argocd_resume_only(args, state, primary, secondary, logger) is True

    prepare_clients.assert_called_once_with(
        args,
        state,
        paused_apps,
        primary,
        secondary,
        logger,
        allow_primary_load_from_state=True,
    )
    resume_recorded.assert_called_once_with(paused_apps, "run-1", primary, secondary, logger)


def test_attempt_resume_on_failure_clears_pause_state_only_after_full_success():
    from lib import Phase

    args = SimpleNamespace(argocd_resume_on_failure=True, restore_only=False, force=False)
    paused_apps = [{"hub": "primary", "namespace": "argocd", "name": "app-1"}]
    state = Mock()
    state.get_config.side_effect = lambda key, default=None: {
        "argocd_run_id": "run-1",
        "argocd_paused_apps": paused_apps,
    }.get(key, default)
    primary = Mock()
    secondary = Mock()
    logger = Mock()

    with patch("lib.argocd_resume.prepare_argocd_resume_clients", return_value=(primary, secondary)), patch(
        "lib.argocd_resume.argocd_lib.resume_recorded_applications"
    ) as resume_recorded, patch("lib.argocd_resume.clear_argocd_pause_state") as clear_pause:
        resume_recorded.return_value = SimpleNamespace(restored=1, already_resumed=0, failed=0)

        attempt_argocd_resume_on_failure(args, state, primary, secondary, logger)

    state.clear_step_completed.assert_called_once_with("pause_argocd_apps")
    clear_pause.assert_called_once_with(state)
    state.add_error.assert_called_once_with(
        "Argo CD resume-on-failure completed; retry must re-run Argo CD pause before continuing.",
        phase=Phase.PRIMARY_PREP.value,
    )
```

- [ ] **Step 2: Run the direct test file and confirm the new functions are still missing**

Run: `python -m pytest tests/test_argocd_resume_helpers.py -q`

Expected: FAIL with `ImportError: cannot import name 'run_argocd_resume_only' from 'lib.argocd_resume'`

- [ ] **Step 3: Extend `lib/argocd_resume.py` with the resume-only and resume-on-failure functions**

```python
from lib import Phase
from lib import argocd as argocd_lib
from lib.argocd_coordinator import clear_argocd_pause_state


def run_argocd_resume_only(
    args: Any,
    state: Any,
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    logger: logging.Logger,
) -> bool:
    """Load state and restore Argo CD auto-sync for previously paused Applications, then exit."""
    if state.get_config("argocd_pause_dry_run", False):
        logger.error(
            "Argo CD resume requested, but the pause step was run in dry-run mode. "
            "Re-run pause without --dry-run to generate resumable state."
        )
        return False

    run_id = state.get_config("argocd_run_id")
    paused_apps = state.get_config("argocd_paused_apps") or []
    if not run_id or not paused_apps:
        logger.error("No Argo CD paused apps in state file (argocd_run_id or argocd_paused_apps missing).")
        return False

    logger.info("Resuming Argo CD auto-sync from state (run_id=%s, %d app(s))", run_id, len(paused_apps))
    try:
        resume_primary, resume_secondary = prepare_argocd_resume_clients(
            args,
            state,
            paused_apps,
            primary,
            secondary,
            logger,
            allow_primary_load_from_state=True,
        )
    except Exception as exc:
        logger.error("Resume-only hub identity validation failed: %s", exc)
        return False

    summary = argocd_lib.resume_recorded_applications(
        paused_apps,
        run_id,
        resume_primary,
        resume_secondary,
        logger,
    )
    logger.info(
        "Restored %d and already resumed %d of %d Application(s).",
        summary.restored,
        summary.already_resumed,
        len(paused_apps),
    )
    if summary.failed:
        logger.error("Argo CD auto-sync restore failed for %d Application(s).", summary.failed)
        return False
    return True


def attempt_argocd_resume_on_failure(
    args: Any,
    state: Any,
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    logger: logging.Logger,
) -> None:
    """Best-effort resume of paused ArgoCD Applications after a switchover failure."""
    if not getattr(args, "argocd_resume_on_failure", False):
        return

    paused_apps = state.get_config("argocd_paused_apps") or []
    run_id = state.get_config("argocd_run_id")
    if not paused_apps or not run_id:
        return

    logger.warning(
        "Switchover failed — attempting to resume %d paused Argo CD Application(s) (run_id=%s)...",
        len(paused_apps),
        run_id,
    )
    try:
        resume_primary, resume_secondary = prepare_argocd_resume_clients(
            args,
            state,
            paused_apps,
            primary,
            secondary,
            logger,
            allow_primary_load_from_state=False,
        )
        summary = argocd_lib.resume_recorded_applications(
            paused_apps,
            run_id,
            resume_primary,
            resume_secondary,
            logger,
        )
        logger.info(
            "Argo CD resume-on-failure: restored=%d, already_resumed=%d, failed=%d",
            summary.restored,
            summary.already_resumed,
            summary.failed,
        )
        if summary.failed:
            logger.warning(
                "Argo CD resume-on-failure: %d Application(s) could not be resumed. "
                "Use --argocd-resume-only to retry manually.",
                summary.failed,
            )
            return

        accounted_for = int(summary.restored or 0) + int(summary.already_resumed or 0)
        if accounted_for < len(paused_apps):
            logger.warning(
                "Argo CD resume-on-failure accounted for %d of %d recorded Application(s). "
                "Preserving pause state for --argocd-resume-only retry.",
                accounted_for,
                len(paused_apps),
            )
            return

        state.clear_step_completed("pause_argocd_apps")
        clear_argocd_pause_state(state)
        retry_phase = Phase.PREFLIGHT if getattr(args, "restore_only", False) else Phase.PRIMARY_PREP
        state.add_error(
            "Argo CD resume-on-failure completed; retry must re-run Argo CD pause before continuing.",
            phase=retry_phase.value,
        )
        logger.info("Argo CD resume-on-failure cleanup completed; durable pause state cleared.")
    except Exception as exc:
        logger.warning("Argo CD resume-on-failure could not complete cleanup: %s", exc)
```

- [ ] **Step 4: Run the direct tests plus the existing resume regression surface**

Run: `python -m pytest tests/test_argocd_resume_helpers.py tests/test_main_argocd_resume.py -q`

Expected: PASS

- [ ] **Step 5: Commit the extracted resume workflow slice**

```bash
git add lib/argocd_resume.py tests/test_argocd_resume_helpers.py
git commit -m "refactor: extract argocd resume safety helpers"
```

## Task 3: Wire `acm_switchover.py` Compatibility Wrappers

**Files:**
- Modify: `acm_switchover.py`
- Modify: `tests/test_main.py`
- Test: `tests/test_main_phase_flow.py`
- Test: `tests/test_operation_runners.py`

- [ ] **Step 1: Add failing compatibility-wrapper tests to `tests/test_main.py`**

```python
from acm_switchover import (
    _attempt_argocd_resume_on_failure,
    _prepare_argocd_resume_clients,
    _run_argocd_resume_only,
)


@pytest.mark.unit
class TestArgocdResumeDelegation:
    def test_prepare_argocd_resume_clients_delegates_to_lib_module(self):
        args = SimpleNamespace(primary_context="hub-a", secondary_context="hub-b", dry_run=False, force=False)
        state = Mock()
        paused_apps = [{"hub": "secondary", "namespace": "argocd", "name": "app-1"}]
        primary = Mock()
        secondary = Mock()
        logger = Mock()

        with patch(
            "acm_switchover.argocd_resume.prepare_argocd_resume_clients",
            return_value=(primary, secondary),
        ) as prepare_clients:
            result = _prepare_argocd_resume_clients(
                args,
                state,
                paused_apps,
                primary,
                secondary,
                logger,
                allow_primary_load_from_state=True,
            )

        assert result == (primary, secondary)
        prepare_clients.assert_called_once_with(
            args,
            state,
            paused_apps,
            primary,
            secondary,
            logger,
            allow_primary_load_from_state=True,
        )

    def test_run_argocd_resume_only_delegates_to_lib_module(self):
        args = SimpleNamespace()
        state = Mock()
        primary = Mock()
        secondary = Mock()
        logger = Mock()

        with patch("acm_switchover.argocd_resume.run_argocd_resume_only", return_value=True) as run_resume:
            result = _run_argocd_resume_only(args, state, primary, secondary, logger)

        assert result is True
        run_resume.assert_called_once_with(args, state, primary, secondary, logger)

    def test_attempt_argocd_resume_on_failure_delegates_to_lib_module(self):
        args = SimpleNamespace(argocd_resume_on_failure=True, restore_only=False, force=False)
        state = Mock()
        primary = Mock()
        secondary = Mock()
        logger = Mock()

        with patch(
            "acm_switchover.argocd_resume.attempt_argocd_resume_on_failure",
            return_value=None,
        ) as attempt_resume:
            _attempt_argocd_resume_on_failure(args, state, primary, secondary, logger)

        attempt_resume.assert_called_once_with(args, state, primary, secondary, logger)
```

- [ ] **Step 2: Run the new delegation tests and confirm `acm_switchover.py` does not wire the new module yet**

Run: `python -m pytest tests/test_main.py::TestArgocdResumeDelegation -q`

Expected: FAIL because `acm_switchover` does not yet import `argocd_resume`

- [ ] **Step 3: Import `argocd_resume` and delegate the three wrappers in `acm_switchover.py`**

```python
from lib import argocd_resume


def _prepare_argocd_resume_clients(
    args: argparse.Namespace,
    state: StateManager,
    paused_apps: list[dict[str, Any]],
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    logger: logging.Logger,
    *,
    allow_primary_load_from_state: bool,
) -> tuple[Optional[KubeClient], Optional[KubeClient]]:
    """Resolve client mapping and validate hub identity bindings before resume."""
    return argocd_resume.prepare_argocd_resume_clients(
        args,
        state,
        paused_apps,
        primary,
        secondary,
        logger,
        allow_primary_load_from_state=allow_primary_load_from_state,
    )


def _run_argocd_resume_only(
    args: argparse.Namespace,
    state: StateManager,
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    logger: logging.Logger,
) -> bool:
    """Load state and restore Argo CD auto-sync for previously paused Applications, then exit."""
    return argocd_resume.run_argocd_resume_only(args, state, primary, secondary, logger)


def _attempt_argocd_resume_on_failure(
    args: argparse.Namespace,
    state: StateManager,
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    logger: logging.Logger,
) -> None:
    """Best-effort resume of paused ArgoCD Applications after a switchover failure."""
    argocd_resume.attempt_argocd_resume_on_failure(args, state, primary, secondary, logger)
```

- [ ] **Step 4: Run the direct plus compatibility regression surface**

Run: `python -m pytest tests/test_argocd_resume_helpers.py tests/test_main.py tests/test_main_argocd_resume.py tests/test_main_phase_flow.py tests/test_operation_runners.py -q`

Expected: PASS

- [ ] **Step 5: Commit the wrapper-compatibility slice**

```bash
git add acm_switchover.py tests/test_main.py
git commit -m "refactor: delegate argocd resume helpers from main"
```

## Task 4: Update The Thermos Tracker And Run Final Verification

**Files:**
- Modify: `thermos-resolution-plan.md`
- Test: `tests/test_documentation_guardrails.py`

- [ ] **Step 1: Update the PR30 tracker row with the spec, implementation plan, scope note, and ready-for-review verification**

```markdown
| 30 | ready_for_review | `refactor/thermos-30-argocd-resume-safety` | `.worktrees/thermos-30-argocd-resume` | F44 Argo CD resume safety extraction | — | Added design spec `docs/superpowers/specs/2026-06-08-pr30-argocd-resume-safety-design.md` and implementation plan `docs/superpowers/plans/2026-06-08-pr30-argocd-resume-safety.md`. `lib/argocd_resume.py` now owns `_prepare_argocd_resume_clients()`, `_run_argocd_resume_only()`, and `_attempt_argocd_resume_on_failure()`. `acm_switchover.py` keeps thin compatibility wrappers, and `_run_restore_only_argocd_pause()` remains in place and out of scope for this slice. Verification: `python -m pytest tests/test_argocd_resume_helpers.py tests/test_main_argocd_resume.py tests/test_main.py tests/test_main_phase_flow.py tests/test_operation_runners.py tests/test_documentation_guardrails.py -q` passed; `graphify update .` passed; `git diff --check` passed; final `./run_tests.sh` passed. |
```

- [ ] **Step 2: Run the focused regression suite plus documentation guardrails**

Run: `python -m pytest tests/test_argocd_resume_helpers.py tests/test_main_argocd_resume.py tests/test_main.py tests/test_main_phase_flow.py tests/test_operation_runners.py tests/test_documentation_guardrails.py -q`

Expected: PASS

- [ ] **Step 3: Refresh the graph after the Python code changes**

Run: `graphify update .`

Expected: exit code 0

- [ ] **Step 4: Run the whitespace sanity check**

Run: `git diff --check`

Expected: no output, exit code 0

- [ ] **Step 5: Run the full strict verification lane**

Run: `./run_tests.sh`

Expected: PASS

- [ ] **Step 6: Commit the tracker update and final verification state**

```bash
git add thermos-resolution-plan.md
git commit -m "docs: record PR30 argocd resume extraction verification"
```
