"""GLM-H3 / R4-06 PR 1 (#349): klusterlet verification never silently skips on a bad kubeconfig.

A kubeconfig that cannot be loaded and validated, or (R4-06 PR 2) a cluster whose expected
hub endpoint cannot be determined from its import secret, fails post-activation instead of
skipping the wrong-hub gate: verify() returns False, no spoke cluster is read or mutated, and
the verify_klusterlet_connections step is not checkpointed, so a resume runs it again. These
tests drive the real loader over real files; nothing in the load path is mocked.
"""

import copy
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import yaml

from lib.exceptions import SwitchoverError
from lib.strict_read import StrictReadOutcome
from lib.utils import Phase, StateManager
from modules.post_activation import PostActivationVerification

HUB = "https://api.new-hub.example:6443"
STEP = "verify_klusterlet_connections"


def _kubeconfig(*, hub_context="secondary-ctx", server=HUB, extra_contexts=()):
    return {
        "apiVersion": "v1",
        "clusters": [{"name": "new-hub", "cluster": {"server": server}}],
        "contexts": [{"name": hub_context, "context": {"cluster": "new-hub", "user": "admin"}}, *extra_contexts],
        "users": [{"name": "admin", "user": {"token": "fixture-token"}}],
    }


def _write(path: Path, data) -> str:
    path.write_text(data if isinstance(data, str) else yaml.safe_dump(data), encoding="utf-8")
    return str(path)


def _secondary():
    client = Mock()
    client.context = "secondary-ctx"
    client.list_managed_clusters_strict.return_value = StrictReadOutcome.from_items(
        [
            {
                "metadata": {"name": "spoke-a"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.spoke-a.example:6443"}]},
            }
        ]
    )
    return client


def _pav(state):
    return PostActivationVerification(secondary_client=_secondary(), state_manager=state, has_observability=False)


@pytest.fixture
def state(tmp_path):
    manager = StateManager(str(tmp_path / "state.json"))
    manager.flush_state()
    return manager


FAILURES = {
    "missing": lambda tmp: str(tmp / "absent.yaml"),
    "yaml_invalid": lambda tmp: _write(tmp / "bad.yaml", "clusters: [\n  - name: x\n    cluster: {server: "),
    "structure_invalid": lambda tmp: _write(tmp / "list.yaml", "- just\n- a list\n"),
    "duplicate_yaml_key": lambda tmp: _write(tmp / "dup.yaml", "clusters: []\nclusters: []\n"),
}


@pytest.mark.parametrize("code", sorted(FAILURES))
def test_an_unloadable_kubeconfig_fails_the_klusterlet_step_and_is_not_checkpointed(tmp_path, state, code, monkeypatch):
    """GLM-H3 1/3: the wrong-hub gate is never skipped silently, and a resume runs it again."""
    good = _write(tmp_path / "good.yaml", _kubeconfig())
    monkeypatch.setenv("KUBECONFIG", f"{good}:{FAILURES[code](tmp_path)}")
    pav = _pav(state)

    with patch.object(pav, "_verify_managed_clusters_connected"), patch.object(
        pav, "_build_managed_cluster_clients"
    ) as build:
        with pytest.raises(SwitchoverError, match=rf"kubeconfig_load_failed \({code}\)"):
            pav._verify_cluster_connections()

    assert not state.is_step_completed(STEP)
    build.assert_not_called()


def test_a_per_file_failure_never_degrades_to_an_empty_merge(tmp_path, state, monkeypatch):
    """GLM-H3 4: a bad later file used to discard everything merged before it, returning a
    truthy empty merge that turned every cluster into a silent "unreachable"."""
    good = _write(tmp_path / "good.yaml", _kubeconfig())
    scalar = _write(tmp_path / "scalar.yaml", "just-a-string\n")
    monkeypatch.setenv("KUBECONFIG", f"{good}:{scalar}")
    pav = _pav(state)

    with patch.object(pav, "_check_klusterlet_targets") as check:
        with pytest.raises(SwitchoverError, match=r"kubeconfig_load_failed \(structure_invalid\)"):
            pav._verify_klusterlet_connections()

    check.assert_not_called()


SPOKE_CONTEXT = {"name": "spoke-a-admin", "context": {"cluster": "spoke-a", "user": "admin"}}
SPOKE_CLUSTER = {"name": "spoke-a", "cluster": {"server": "https://api.spoke-a.example:6443"}}


def _with_spoke():
    data = _kubeconfig(extra_contexts=(SPOKE_CONTEXT,))
    data["clusters"].append(SPOKE_CLUSTER)
    return data


def test_an_undeterminable_expected_hub_endpoint_fails_and_is_not_checkpointed(tmp_path, state, monkeypatch):
    """R4-06 PR 2 (amendment §2.4): the expected hub is the cluster's import-secret server.

    Without an import secret the cluster is `unverified` (`expected_endpoint_unavailable`):
    the step fails before any spoke client exists and a resume runs it again. The operator's
    secondary-context server is no longer consulted at all.
    """
    monkeypatch.setenv("KUBECONFIG", _write(tmp_path / "spoke.yaml", _with_spoke()))
    pav = _pav(state)
    pav.secondary.get_secret.return_value = None

    with patch.object(pav, "_verify_managed_clusters_connected"), patch.object(
        pav, "_build_managed_cluster_clients"
    ) as build:
        with pytest.raises(SwitchoverError, match=r"spoke-a \(expected_endpoint_unavailable\)"):
            pav._verify_cluster_connections()

    assert not state.is_step_completed(STEP)
    build.assert_not_called()


def test_the_operator_hub_context_is_no_longer_required(tmp_path, state, monkeypatch):
    """The secondary context's own server was the PR 1 expected endpoint; it no longer gates anything."""
    data = _with_spoke()
    data["contexts"][0]["name"] = "some-other-ctx"
    monkeypatch.setenv("KUBECONFIG", _write(tmp_path / "spoke.yaml", data))
    pav = _pav(state)

    with patch.object(pav, "_check_klusterlet_targets", return_value=([], [], ["spoke-a"], [], [], [])) as check:
        pav._verify_klusterlet_connections()

    check.assert_called_once()


def test_verify_reports_failure_when_the_klusterlet_gate_cannot_run(tmp_path, monkeypatch):
    """GLM-H3 2: verify() returns False with a phase-tagged error instead of True."""
    monkeypatch.setenv("KUBECONFIG", str(tmp_path / "absent.yaml"))
    state = Mock()
    state.is_step_completed.return_value = False
    state.step.side_effect = lambda name, logger=None: _RealStep()
    pav = PostActivationVerification(secondary_client=_secondary(), state_manager=state, has_observability=False)

    with patch.object(pav, "_load_post_activation_journal"), patch.object(pav, "_post_activation_waiver"), patch.object(
        pav, "_verify_managed_clusters_connected"
    ), patch.object(pav, "_record_names_evidence"), patch.object(pav, "_verify_auto_import_cleanup_step"), patch.object(
        pav, "_record_completion_evidence"
    ) as complete:
        assert pav.verify() is False

    complete.assert_not_called()
    (message, phase), _ = state.add_error.call_args
    assert "kubeconfig_load_failed (missing)" in message and phase == Phase.POST_ACTIVATION.value


class _RealStep:
    def __enter__(self):
        return True

    def __exit__(self, *exc):
        return False


def test_a_valid_kubeconfig_still_runs_the_klusterlet_gate(tmp_path, state, monkeypatch):
    monkeypatch.setenv("KUBECONFIG", _write(tmp_path / "good.yaml", _kubeconfig()))
    pav = _pav(state)

    with patch.object(pav, "_check_klusterlet_targets", return_value=(["spoke-a"], [], [], [], [], [])) as check:
        pav._verify_klusterlet_connections()

    targets, kubeconfig_data = check.call_args.args
    assert targets == [("spoke-a", "https://api.spoke-a.example:6443", None)]
    assert [ctx["name"] for ctx in kubeconfig_data["contexts"]] == ["secondary-ctx"]


def test_an_ignored_size_override_is_reported_through_the_run_logger(tmp_path, state, monkeypatch, caplog):
    """The warning is emitted at load time (not at import), so it reaches the run's handlers."""
    monkeypatch.setenv("KUBECONFIG", _write(tmp_path / "good.yaml", _kubeconfig()))
    monkeypatch.setenv("ACM_KUBECONFIG_MAX_SIZE", "0")
    pav = _pav(state)

    with caplog.at_level("WARNING", logger="acm_switchover"):
        pav._load_kubeconfig_data()

    assert "ACM_KUBECONFIG_MAX_SIZE must be a positive byte count" in caplog.text


SECRET_TOKEN = "sha256~SUPERSECRETTOKENVALUE"
PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----MIIEpAIBAAKCAQEAsecretkeymaterial"


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(
            f"users:\n- name: u\n  user:\n    token: {SECRET_TOKEN}\n    client-key-data: {PRIVATE_KEY}\n  bad: [\n",
            id="yaml-invalid-near-secrets",
        ),
        pytest.param(
            f"users:\n- name: u\n  user: {{token: {SECRET_TOKEN}}}\nusers:\n- {{client-key-data: {PRIVATE_KEY}}}\n",
            id="duplicate-key-near-secrets",
        ),
        pytest.param(f"users:\n- name: ''\n  user: {{token: {SECRET_TOKEN}}}\n", id="structure-near-secrets"),
    ],
)
def test_a_load_failure_never_leaks_key_material(tmp_path, state, monkeypatch, caplog, content):
    """Design §1: only a reason code, the file path and a line/column are ever reported -
    through verify(), which logs the failure and records it in the persisted state."""
    monkeypatch.setenv("KUBECONFIG", _write(tmp_path / "secrets.yaml", content))
    pav = _pav(state)

    with caplog.at_level("DEBUG"), patch.object(pav, "_load_post_activation_journal"), patch.object(
        pav, "_post_activation_waiver"
    ), patch.object(pav, "_verify_managed_clusters_connected"):
        assert pav.verify() is False

    errors = state.state.get("errors") or []
    assert any("kubeconfig_load_failed" in json.dumps(error) for error in errors), errors
    rendered = caplog.text + json.dumps(copy.deepcopy(state.state), default=str)
    assert SECRET_TOKEN not in rendered and "SUPERSECRET" not in rendered
    assert "PRIVATE KEY" not in rendered and "secretkeymaterial" not in rendered


def test_verify_never_records_key_material_from_a_constructor_error(tmp_path, monkeypatch, caplog):
    """A YAML constructor error (e.g. `!!int <token>`) quotes the scalar; verify() logs and records
    the failure into state, so the sanitized message is the only thing that may reach either."""
    bad = _write(tmp_path / "tag.yaml", f"users:\n- name: u\n  user: {{token: !!int {SECRET_TOKEN}}}\n")
    monkeypatch.setenv("KUBECONFIG", bad)
    state = Mock()
    state.is_step_completed.return_value = False
    state.step.side_effect = lambda name, logger=None: _RealStep()
    pav = PostActivationVerification(secondary_client=_secondary(), state_manager=state, has_observability=False)

    with caplog.at_level("DEBUG"), patch.object(pav, "_load_post_activation_journal"), patch.object(
        pav, "_post_activation_waiver"
    ), patch.object(pav, "_verify_managed_clusters_connected"):
        assert pav.verify() is False

    (message, _phase), _ = state.add_error.call_args
    assert "kubeconfig_load_failed (yaml_invalid)" in message
    assert "SUPERSECRET" not in message + caplog.text and SECRET_TOKEN not in message + caplog.text
