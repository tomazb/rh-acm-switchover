from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from lib.runtime_bootstrap import (
    client_context_name,
    collect_hub_identities,
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
