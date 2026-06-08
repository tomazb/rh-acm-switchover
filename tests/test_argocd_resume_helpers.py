import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from lib import argocd as argocd_lib
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
    STATE_KEY_ARGOCD_PAUSE_DRY_RUN,
    STATE_KEY_ARGOCD_PAUSED_APPS,
    STATE_KEY_ARGOCD_RUN_ID,
    STEP_PAUSE_ARGOCD_APPS,
)
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
    state.get_config.side_effect = lambda key, default=None: {
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
    paused_apps = [{"hub": HUB_ROLE_SECONDARY, "namespace": "argocd", "name": "app-2"}]
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
    paused_apps = [{"hub": HUB_ROLE_PRIMARY, "namespace": "argocd", "name": "app-1"}]
    state = _mock_state(paused_apps)
    secondary = _identity_client("hub-b", "uid-secondary")
    created_primary = _identity_client("hub-a", "uid-primary")
    kube_client_factory = Mock(return_value=created_primary)
    logger = logging.getLogger("test.prepare.load_primary")

    resume_primary, resume_secondary = prepare_argocd_resume_clients(
        args,
        state,
        paused_apps,
        None,
        secondary,
        logger,
        allow_primary_load_from_state=True,
        kube_client_factory=kube_client_factory,
    )

    kube_client_factory.assert_called_once_with("hub-a", dry_run=False)
    assert resume_primary is created_primary
    assert resume_secondary is secondary


def test_required_resume_roles_combines_paused_apps_and_stored_identities():
    paused_apps = [
        {"hub": HUB_ROLE_PRIMARY, "namespace": "argocd", "name": "app-1"},
        {"hub": "unknown", "namespace": "argocd", "name": "ignored"},
        "not-a-mapping",
    ]
    stored_identities = {
        HUB_ROLE_SECONDARY: {"context": "hub-b", "cluster_uid": "uid-secondary"},
        "legacy": {"context": "legacy", "cluster_uid": "uid-legacy"},
    }

    assert _required_resume_roles(paused_apps, stored_identities) == {HUB_ROLE_PRIMARY, HUB_ROLE_SECONDARY}


def test_ensure_resume_identity_data_requires_force_for_missing_identities():
    args = SimpleNamespace(force=False)
    logger = logging.getLogger("test.prepare.identity_data")

    with pytest.raises(ValueError, match="missing hub identity data"):
        _ensure_resume_identity_data(args, {}, logger)


def test_ensure_resume_identity_data_allows_legacy_state_with_force():
    args = SimpleNamespace(force=True)
    logger = logging.getLogger("test.prepare.identity_data_force")

    _ensure_resume_identity_data(args, {}, logger)


def test_run_argocd_resume_only_rejects_dry_run_pause_state():
    paused_apps = [{"hub": HUB_ROLE_SECONDARY, "namespace": "argocd", "name": "app-2"}]
    state = Mock()
    state.get_config.side_effect = lambda key, default=None: {
        STATE_KEY_ARGOCD_PAUSE_DRY_RUN: True,
        STATE_KEY_ARGOCD_RUN_ID: "run-1",
        STATE_KEY_ARGOCD_PAUSED_APPS: paused_apps,
    }.get(key, default)
    args = SimpleNamespace()
    primary = Mock()
    secondary = Mock()
    logger = logging.getLogger("test.run_only.dry_run")

    with patch("lib.argocd_resume.prepare_argocd_resume_clients") as prepare_clients, patch(
        "lib.argocd_resume.argocd_lib.resume_recorded_applications"
    ) as resume_recorded:
        result = run_argocd_resume_only(args, state, primary, secondary, logger)

    assert result is False
    prepare_clients.assert_not_called()
    resume_recorded.assert_not_called()


def test_run_argocd_resume_only_uses_prepare_clients_and_resume_recorded_applications():
    paused_apps = [{"hub": HUB_ROLE_SECONDARY, "namespace": "argocd", "name": "app-2"}]
    state = _mock_state(paused_apps)
    args = SimpleNamespace(primary_context="hub-a", secondary_context="hub-b", dry_run=False, force=False)
    primary = _identity_client("hub-a", "uid-primary")
    secondary = _identity_client("hub-b", "uid-secondary")
    logger = logging.getLogger("test.run_only.success")

    with patch(
        "lib.argocd_resume.prepare_argocd_resume_clients",
        return_value=(primary, secondary),
    ) as prepare_clients, patch("lib.argocd_resume.argocd_lib.resume_recorded_applications") as resume_recorded:
        resume_recorded.return_value = argocd_lib.ResumeSummary(restored=1, already_resumed=0, failed=0)
        result = run_argocd_resume_only(args, state, primary, secondary, logger)

    assert result is True
    prepare_clients.assert_called_once_with(
        args,
        state,
        paused_apps,
        primary,
        secondary,
        logger,
        allow_primary_load_from_state=True,
        kube_client_factory=KubeClient,
    )
    resume_recorded.assert_called_once_with(
        paused_apps,
        "run-1",
        primary,
        secondary,
        logger,
    )


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
    ) as prepare_clients, patch("lib.argocd_resume.argocd_lib.resume_recorded_applications") as resume_recorded, patch(
        "lib.argocd_resume.clear_argocd_pause_state"
    ) as clear_pause_state:
        resume_recorded.return_value = argocd_lib.ResumeSummary(restored=1, already_resumed=0, failed=0)
        attempt_argocd_resume_on_failure(args, partial_state, primary, secondary, logger)

    prepare_clients.assert_called_once_with(
        args,
        partial_state,
        paused_apps,
        primary,
        secondary,
        logger,
        allow_primary_load_from_state=False,
        kube_client_factory=KubeClient,
    )
    clear_pause_state.assert_not_called()
    partial_state.clear_step_completed.assert_not_called()

    success_state = _mock_state(paused_apps)
    with patch(
        "lib.argocd_resume.prepare_argocd_resume_clients",
        return_value=(primary, secondary),
    ) as prepare_clients, patch("lib.argocd_resume.argocd_lib.resume_recorded_applications") as resume_recorded, patch(
        "lib.argocd_resume.clear_argocd_pause_state"
    ) as clear_pause_state:
        resume_recorded.return_value = argocd_lib.ResumeSummary(restored=1, already_resumed=1, failed=0)
        attempt_argocd_resume_on_failure(args, success_state, primary, secondary, logger)

    prepare_clients.assert_called_once_with(
        args,
        success_state,
        paused_apps,
        primary,
        secondary,
        logger,
        allow_primary_load_from_state=False,
        kube_client_factory=KubeClient,
    )
    clear_pause_state.assert_called_once_with(success_state)
    success_state.clear_step_completed.assert_called_once_with(STEP_PAUSE_ARGOCD_APPS)
