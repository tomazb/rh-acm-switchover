from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from lib.exceptions import SecurityValidationError
from lib.runtime_bootstrap import (
    client_context_name,
    collect_hub_identities,
    get_default_state_dir,
    initialize_clients,
    resolve_state_file,
    state_contexts,
    stored_hub_identities,
)

pytestmark = pytest.mark.unit


def test_resolve_state_file_prefers_requested_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", "/tmp/acm-state")

    assert (
        resolve_state_file(
            requested_path="custom/state.json",
            primary_ctx="hub-a",
            secondary_ctx="hub-b",
        )
        == "custom/state.json"
    )


def test_get_default_state_dir_rejects_unsafe_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The resolver is the single owner of the ACM_SWITCHOVER_STATE_DIR posture:
    # unsafe values fail loudly for every consumer (CLI and viewer alike).
    monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", "../bad")

    with pytest.raises(SecurityValidationError):
        get_default_state_dir()


def test_get_default_state_dir_uses_valid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", "/tmp/acm-state")

    assert get_default_state_dir() == "/tmp/acm-state"


def test_get_default_state_dir_defaults_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACM_SWITCHOVER_STATE_DIR", raising=False)

    assert get_default_state_dir() == ".state"


def test_initialize_clients_passes_dry_run_to_each_context() -> None:
    args = SimpleNamespace(primary_context="hub-a", secondary_context="hub-b", dry_run=True)
    logger = Mock()

    with patch("lib.runtime_bootstrap.KubeClient") as kube_client:
        primary_client = Mock(name="primary-client")
        secondary_client = Mock(name="secondary-client")
        kube_client.side_effect = [primary_client, secondary_client]

        assert initialize_clients(args, logger) == (primary_client, secondary_client)
        kube_client.assert_any_call("hub-a", dry_run=True)
        kube_client.assert_any_call("hub-b", dry_run=True)


def test_initialize_clients_defaults_missing_optional_attributes() -> None:
    args = SimpleNamespace(primary_context="hub-a")
    logger = Mock()

    with patch("lib.runtime_bootstrap.KubeClient") as kube_client:
        primary_client = Mock(name="primary-client")
        kube_client.return_value = primary_client

        primary, secondary = initialize_clients(args, logger)

    assert primary is primary_client
    assert secondary is None
    kube_client.assert_called_once_with("hub-a", dry_run=False)


@pytest.mark.parametrize(
    ("failing_role", "failure", "expected_message"),
    [
        (
            "primary",
            "raw-primary-constructor-sentinel",
            "Unable to verify the primary hub physical identity from the live kube-system Namespace UID. "
            "Refusing the normal two-hub switchover.",
        ),
        (
            "secondary",
            "raw-secondary-constructor-sentinel",
            "Unable to verify the secondary hub physical identity from the live kube-system Namespace UID. "
            "Refusing the normal two-hub switchover.",
        ),
    ],
)
def test_initialize_clients_sanitizes_normal_two_hub_constructor_failure(
    failing_role: str,
    failure: str,
    expected_message: str,
) -> None:
    """Construction failures are translated at the affected role boundary only."""
    args = SimpleNamespace(primary_context="primary-context", secondary_context="secondary-context", dry_run=True)
    logger = Mock()
    primary_client = Mock(name="primary-client")
    client_factory = Mock(
        side_effect=RuntimeError(failure) if failing_role == "primary" else [primary_client, RuntimeError(failure)]
    )

    with pytest.raises(Exception) as exc_info:
        initialize_clients(args, logger, client_factory=client_factory, sanitize_identity_errors=True)

    error = exc_info.value
    assert type(error).__name__ == "HubIdentityVerificationError"
    assert str(error) == expected_message
    assert failure not in str(error)
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    if failing_role == "primary":
        assert client_factory.call_args_list == [
            (("primary-context",), {"dry_run": True, "log_config_errors": False}),
        ]
    else:
        assert client_factory.call_args_list == [
            (("primary-context",), {"dry_run": True, "log_config_errors": False}),
            (("secondary-context",), {"dry_run": True, "log_config_errors": False}),
        ]


def test_collect_hub_identities_reads_only_present_clients() -> None:
    primary = Mock()
    primary.get_cluster_identity.return_value = {"context": "hub-a", "cluster_uid": "uid-a"}

    assert collect_hub_identities(primary, None) == {
        "primary": {"context": "hub-a", "cluster_uid": "uid-a"},
    }


def test_state_helpers_tolerate_missing_state_shapes() -> None:
    state = Mock()
    state.state = {"contexts": {"primary": "hub-a", "secondary": "hub-b"}}

    assert state_contexts(state) == ("hub-a", "hub-b")
    assert stored_hub_identities(state) == {}
    assert client_context_name(SimpleNamespace(context="hub-a")) == "hub-a"
    assert client_context_name(SimpleNamespace(context=None)) is None
