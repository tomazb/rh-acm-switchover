import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from lib import argocd as argocd_lib
from lib.argocd_register import ArgocdPauseRegister
from lib.argocd_resume import (
    _ensure_resume_identity_data,
    _required_resume_roles,
    attempt_argocd_resume_on_failure,
    prepare_argocd_resume_clients,
    run_argocd_resume_only,
)
from lib.constants import (
    HUB_ROLE_PRIMARY,
    HUB_ROLE_SECONDARY,
    STATE_KEY_ARGOCD_PAUSED_APPS,
    STATE_KEY_ARGOCD_RUN_ID,
    STEP_PAUSE_ARGOCD_APPS,
)
from lib.exceptions import SwitchoverError
from lib.kube_client import KubeClient


def _identity_client(context, uid):
    client = Mock()
    client.context = context
    client.get_cluster_identity.return_value = {"context": context, "cluster_uid": uid}
    return client


def _mock_state(paused_apps, *, primary_ctx="hub-a", secondary_ctx="hub-b", identities=True):
    state = Mock()
    state.state = {"contexts": {HUB_ROLE_PRIMARY: primary_ctx, HUB_ROLE_SECONDARY: secondary_ctx}}
    if identities:
        state.state["hub_identities"] = {
            HUB_ROLE_PRIMARY: {"context": primary_ctx, "cluster_uid": "uid-primary"},
            HUB_ROLE_SECONDARY: {"context": secondary_ctx, "cluster_uid": "uid-secondary"},
        }
    state.ensure_hub_identities = Mock()
    state._get_config.side_effect = lambda key, default=None: {
        STATE_KEY_ARGOCD_RUN_ID: "run-1",
        STATE_KEY_ARGOCD_PAUSED_APPS: paused_apps,
    }.get(key, default)
    state.clear_step_completed = Mock()
    return state


def test_prepare_resume_clients_swaps_reversed_contexts():
    args = SimpleNamespace(primary_context="hub-b", secondary_context="hub-a", dry_run=False, force=False)
    paused_apps = [{"hub": HUB_ROLE_PRIMARY, "namespace": "argocd", "name": "app-1"}]
    state = _mock_state(paused_apps)
    primary = _identity_client("hub-a", "uid-primary")
    secondary = _identity_client("hub-b", "uid-secondary")
    logger = logging.getLogger("test.prepare.swap")

    resume_primary, resume_secondary = prepare_argocd_resume_clients(
        args,
        state,
        {HUB_ROLE_PRIMARY},
        primary,
        secondary,
        logger,
        allow_primary_load_from_state=True,
    )

    assert resume_primary is secondary
    assert resume_secondary is primary


def test_prepare_resume_clients_requires_force_for_missing_hub_identities():
    args = SimpleNamespace(primary_context="hub-a", secondary_context="hub-b", dry_run=False, force=False)
    paused_apps = [{"hub": HUB_ROLE_SECONDARY, "namespace": "argocd", "name": "app-2"}]
    state = _mock_state(paused_apps, identities=False)
    primary = _identity_client("hub-a", "uid-primary")
    secondary = _identity_client("hub-b", "uid-secondary")
    logger = logging.getLogger("test.prepare.identity_missing")

    with pytest.raises(SwitchoverError, match="missing hub identity data"):
        prepare_argocd_resume_clients(
            args,
            state,
            {HUB_ROLE_SECONDARY},
            primary,
            secondary,
            logger,
            allow_primary_load_from_state=True,
        )


def test_prepare_resume_clients_loads_primary_client_only_when_allowed():
    args = SimpleNamespace(primary_context=None, secondary_context="hub-b", dry_run=False, force=False)
    paused_apps = [{"hub": HUB_ROLE_PRIMARY, "namespace": "argocd", "name": "app-1"}]
    state = _mock_state(paused_apps)
    secondary = _identity_client("hub-b", "uid-secondary")
    created_primary = _identity_client("hub-a", "uid-primary")
    kube_client_factory = Mock(return_value=created_primary)
    logger = logging.getLogger("test.prepare.load_primary")

    resume_primary, resume_secondary = prepare_argocd_resume_clients(
        args,
        state,
        {HUB_ROLE_PRIMARY},
        None,
        secondary,
        logger,
        allow_primary_load_from_state=True,
        kube_client_factory=kube_client_factory,
    )

    kube_client_factory.assert_called_once_with("hub-a", dry_run=False)
    assert resume_primary is created_primary
    assert resume_secondary is secondary


def test_required_resume_roles_combines_paused_hub_roles_and_stored_identities():
    paused_hub_roles = {HUB_ROLE_PRIMARY, "unknown"}
    stored_identities = {
        HUB_ROLE_SECONDARY: {"context": "hub-b", "cluster_uid": "uid-secondary"},
        "legacy": {"context": "legacy", "cluster_uid": "uid-legacy"},
    }

    assert _required_resume_roles(paused_hub_roles, stored_identities) == {HUB_ROLE_PRIMARY, HUB_ROLE_SECONDARY}


def test_required_resume_roles_ignores_malformed_stored_identities():
    assert _required_resume_roles({HUB_ROLE_PRIMARY}, "not-a-mapping") == {HUB_ROLE_PRIMARY}


def test_ensure_resume_identity_data_requires_force_for_missing_identities():
    args = SimpleNamespace(force=False)
    logger = logging.getLogger("test.prepare.identity_data")

    with pytest.raises(SwitchoverError, match="missing hub identity data"):
        _ensure_resume_identity_data(args, {}, logger)


def test_ensure_resume_identity_data_rejects_malformed_state_without_force():
    args = SimpleNamespace(force=False)
    logger = logging.getLogger("test.prepare.identity_data_malformed")

    with pytest.raises(SwitchoverError, match="missing hub identity data"):
        _ensure_resume_identity_data(args, "not-a-mapping", logger)


def test_ensure_resume_identity_data_allows_legacy_state_with_force():
    args = SimpleNamespace(force=True)
    logger = logging.getLogger("test.prepare.identity_data_force")

    _ensure_resume_identity_data(args, {}, logger)


def test_run_argocd_resume_only_finishes_cleanup_for_empty_register():
    """Thermos 5: a run id over a genuinely empty register is a completed resume, not a failure.

    resume() empties the register and clears the run id as two writes; a crash
    between them leaves exactly this state, and rejecting it stranded the
    operator forever. Dry-run can no longer produce it -- dry-run persists no
    run id at all.
    """
    state = Mock()
    state._get_config.side_effect = lambda key, default=None: {
        STATE_KEY_ARGOCD_RUN_ID: "run-1",
        STATE_KEY_ARGOCD_PAUSED_APPS: [],
    }.get(key, default)
    args = SimpleNamespace(dry_run=False)
    primary = Mock()
    secondary = Mock()
    logger = logging.getLogger("test.run_only.empty_register")

    with patch("lib.argocd_resume.prepare_argocd_resume_clients") as prepare_clients, patch.object(
        ArgocdPauseRegister, "resume"
    ) as register_resume:
        result = run_argocd_resume_only(args, state, primary, secondary, logger)

    assert result is True
    prepare_clients.assert_not_called()
    register_resume.assert_not_called()


def test_run_argocd_resume_only_rejects_register_of_unresumable_records():
    """Records dropped as unresumable are not a completed cleanup -- they are an error.

    Legacy dry-run entries sanitize away to an empty register, which looks
    identical to a finished resume through entry_count alone. Thermos 5's
    idempotent-cleanup path must not swallow them.
    """
    state = Mock()
    state._get_config.side_effect = lambda key, default=None: {
        STATE_KEY_ARGOCD_RUN_ID: "run-1",
        STATE_KEY_ARGOCD_PAUSED_APPS: [
            {
                "hub": "secondary",
                "namespace": "argocd",
                "name": "app-1",
                "original_sync_policy": {"automated": {}},
                "dry_run": True,
            }
        ],
    }.get(key, default)
    args = SimpleNamespace(dry_run=False)
    logger = logging.getLogger("test.run_only.unresumable_records")

    with patch("lib.argocd_resume.prepare_argocd_resume_clients") as prepare_clients, patch.object(
        ArgocdPauseRegister, "resume"
    ) as register_resume:
        result = run_argocd_resume_only(args, state, Mock(), Mock(), logger)

    assert result is False
    prepare_clients.assert_not_called()
    register_resume.assert_not_called()


def test_run_argocd_resume_only_uses_prepare_clients_and_register_resume():
    paused_apps = [{"hub": HUB_ROLE_SECONDARY, "namespace": "argocd", "name": "app-2"}]
    state = _mock_state(paused_apps)
    args = SimpleNamespace(primary_context="hub-a", secondary_context="hub-b", dry_run=False, force=False)
    primary = _identity_client("hub-a", "uid-primary")
    secondary = _identity_client("hub-b", "uid-secondary")
    logger = logging.getLogger("test.run_only.success")

    with patch(
        "lib.argocd_resume.prepare_argocd_resume_clients",
        return_value=(primary, secondary),
    ) as prepare_clients, patch.object(ArgocdPauseRegister, "resume") as register_resume:
        register_resume.return_value = argocd_lib.ResumeSummary(
            restored=1, already_resumed=0, failed=0, remaining_in_register=0
        )
        result = run_argocd_resume_only(args, state, primary, secondary, logger)

    assert result is True
    prepare_clients.assert_called_once_with(
        args,
        state,
        {HUB_ROLE_SECONDARY},
        primary,
        secondary,
        logger,
        allow_primary_load_from_state=True,
        kube_client_factory=KubeClient,
    )
    register_resume.assert_called_once_with(primary, secondary)


def test_attempt_argocd_resume_on_failure_clears_pause_state_only_after_full_success():
    paused_apps = [
        {"hub": HUB_ROLE_PRIMARY, "namespace": "argocd", "name": "app-1"},
        {"hub": HUB_ROLE_SECONDARY, "namespace": "argocd", "name": "app-2"},
    ]
    args = SimpleNamespace(argocd_resume_on_failure=True, restore_only=False, force=False)
    logger = logging.getLogger("test.attempt.clear_state")
    primary = _identity_client("hub-a", "uid-primary")
    secondary = _identity_client("hub-b", "uid-secondary")

    partial_state = _mock_state(paused_apps)
    with patch(
        "lib.argocd_resume.prepare_argocd_resume_clients",
        return_value=(primary, secondary),
    ) as prepare_clients, patch.object(ArgocdPauseRegister, "resume") as register_resume:
        register_resume.return_value = argocd_lib.ResumeSummary(
            restored=1, already_resumed=0, failed=0, remaining_in_register=1
        )
        attempt_argocd_resume_on_failure(args, partial_state, primary, secondary, logger)

    prepare_clients.assert_called_once_with(
        args,
        partial_state,
        {HUB_ROLE_PRIMARY, HUB_ROLE_SECONDARY},
        primary,
        secondary,
        logger,
        allow_primary_load_from_state=False,
        kube_client_factory=KubeClient,
    )
    partial_state.clear_step_completed.assert_not_called()

    success_state = _mock_state(paused_apps)
    with patch(
        "lib.argocd_resume.prepare_argocd_resume_clients",
        return_value=(primary, secondary),
    ) as prepare_clients, patch.object(ArgocdPauseRegister, "resume") as register_resume:
        register_resume.return_value = argocd_lib.ResumeSummary(
            restored=1, already_resumed=1, failed=0, remaining_in_register=0
        )
        attempt_argocd_resume_on_failure(args, success_state, primary, secondary, logger)

    prepare_clients.assert_called_once_with(
        args,
        success_state,
        {HUB_ROLE_PRIMARY, HUB_ROLE_SECONDARY},
        primary,
        secondary,
        logger,
        allow_primary_load_from_state=False,
        kube_client_factory=KubeClient,
    )
    success_state.clear_step_completed.assert_called_once_with(STEP_PAUSE_ARGOCD_APPS)
