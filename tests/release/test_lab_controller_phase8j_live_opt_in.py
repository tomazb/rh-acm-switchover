"""Opt-in scaffolding for the Phase 8J read-only live transport pilot.

This module proves the read-only live transport pilot is **opt-in and disabled by default**, and it
documents how an operator would wire a real read-only client without ever shipping one.

Hard rules enforced here:

- The pilot tests are skipped unless ``ACM_ENABLE_LAB_CONTROLLER_LIVE_TRANSPORT_PILOT`` is explicitly
  set to a truthy value. Normal CI never sets it, so the pilot tests never run by default.
- Even when explicitly enabled, these tests use a **fake injected client only**. They never read a
  kubeconfig, never read credentials, never contact a cluster, never run ``oc``/``kubectl``/
  ``ansible-playbook``, and never call a release adapter.
- No real path, URL, credential, or cluster identifier is embedded.
- Nothing here claims live certification evidence.

A real read-only live pilot (a later, separately audited phase) would inject a real
``ReadOnlyLiveClientProtocol`` implementation supplied at runtime by the operator. That client is
intentionally **not** part of this repository.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import pytest

from tests.release.lab_controller.read_only_discovery import required_read_only_discovery_gate_ids
from tests.release.lab_controller.read_only_live_transport import (
    RawReadOnlyLiveResponse,
    ReadOnlyLiveClientRequest,
    ReadOnlyLiveTransport,
    ReadOnlyLiveTransportOptions,
    ReadOnlyLiveTransportStatus,
    RuntimeOnlyLiveHubHandle,
    RuntimeOnlyLiveTransportContext,
    build_example_transport_context,
)
from tests.release.lab_controller.read_only_transport import (
    ReadOnlyTransportDecision,
    build_example_transport_query,
)

_LIVE_PILOT_ENV_VAR = "ACM_ENABLE_LAB_CONTROLLER_LIVE_TRANSPORT_PILOT"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _live_pilot_opt_in_enabled() -> bool:
    """Return True only when the operator has explicitly enabled the opt-in live pilot."""
    return os.environ.get(_LIVE_PILOT_ENV_VAR, "").strip().lower() in _TRUTHY


_requires_opt_in = pytest.mark.skipif(
    not _live_pilot_opt_in_enabled(),
    reason=f"{_LIVE_PILOT_ENV_VAR} is not set; read-only live transport pilot is opt-in and disabled by default",
)


class _FakeReadOnlyLiveClient:
    """A deterministic fake read-only live client. It never contacts a real cluster."""

    def __init__(self, payload: Mapping[str, Any] | None = None) -> None:
        self._payload = dict(payload or {"observed_identity_summary": "redacted-summary", "signal_count": 2})
        self.requests: list[ReadOnlyLiveClientRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def execute_read_query(self, request: ReadOnlyLiveClientRequest) -> RawReadOnlyLiveResponse:
        self.requests.append(request)
        return RawReadOnlyLiveResponse(query_id=request.query_id, payload=dict(self._payload))


def test_live_transport_pilot_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The opt-in gate must default closed and only open on an explicit truthy value.

    This test always runs (it is not skipped) so the default-closed contract is enforced in normal CI.
    """
    monkeypatch.delenv(_LIVE_PILOT_ENV_VAR, raising=False)
    assert _live_pilot_opt_in_enabled() is False

    for falsy in ("", "0", "false", "no", "off", "maybe"):
        monkeypatch.setenv(_LIVE_PILOT_ENV_VAR, falsy)
        assert _live_pilot_opt_in_enabled() is False

    for truthy in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv(_LIVE_PILOT_ENV_VAR, truthy)
        assert _live_pilot_opt_in_enabled() is True


def test_pilot_scaffolding_uses_no_real_cluster_inputs() -> None:
    """The pilot example context carries only runtime-only handles, never real paths or URLs."""
    context = build_example_transport_context()
    summary_text = repr(context.to_artifact_safe_summary())

    assert "/home/" not in summary_text
    assert "://" not in summary_text
    assert ".kube" not in summary_text


@_requires_opt_in
def test_live_transport_pilot_opt_in_path_with_fake_client() -> None:
    """When explicitly enabled, the opt-in path executes against a fake client only.

    Even under opt-in, this never contacts a real cluster: the injected client is a deterministic
    fake. A real pilot would inject an operator-supplied ``ReadOnlyLiveClientProtocol`` here instead.
    """
    client = _FakeReadOnlyLiveClient()
    transport = ReadOnlyLiveTransport(context=build_example_transport_context(), client=client)

    result = transport.execute(build_example_transport_query())

    assert result.decision is ReadOnlyTransportDecision.PASS
    assert result.status is ReadOnlyLiveTransportStatus.SUCCESS
    assert client.call_count == 1
    assert result.live_contact_attempted is True
    assert result.live_certification_evidence is False
    assert result.mutation_attempted is False


@_requires_opt_in
def test_live_transport_pilot_still_blocks_without_opt_in_flags() -> None:
    """Even with the env var set, the opt-in flags are still required: the env var is not a backdoor."""
    not_opted_in = ReadOnlyLiveTransportOptions(allow_live_contact=False, allow_read_only_queries=False)
    handle = RuntimeOnlyLiveHubHandle(
        physical_label="primary",
        kubeconfig_ref="runtime-kubeconfig-handle",
        context_ref="runtime-context-handle",
    )
    context = RuntimeOnlyLiveTransportContext(
        handle=handle,
        options=not_opted_in,
        gate_ids=tuple(required_read_only_discovery_gate_ids()),
    )
    client = _FakeReadOnlyLiveClient()
    transport = ReadOnlyLiveTransport(context=context, client=client)

    result = transport.execute(build_example_transport_query())

    assert result.decision is ReadOnlyTransportDecision.BLOCKED
    assert result.live_contact_attempted is False
    assert client.call_count == 0
