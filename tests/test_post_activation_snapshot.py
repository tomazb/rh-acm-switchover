"""R4-06 PR 3 acceptance for the Python klusterlet path (amendment 2026-10-15 §6, §7 PR 3).

Every spoke client in a run is built by the real Kubernetes loader from the one snapshot the
cluster was selected with. These tests replace and delete the credential files between the
stages (selection, probe, remediation, re-check) and prove the captured content is what every
client uses, that a refused credential builds no client, and that the run's private
credential directory is removed afterwards.
"""

import base64
import logging
import os
from unittest.mock import Mock, patch

import kubernetes.config.kube_config as kube_config_module
import pytest
import yaml

import lib.kubeconfig_snapshot as snapshot_module
from lib.exceptions import SwitchoverError
from lib.strict_read import StrictReadOutcome
from lib.utils import StateManager
from modules.post_activation import PostActivationVerification

NEW_HUB = "https://api.new-hub.example:6443"
OLD_HUB = "https://api.old-hub.example:6443"
SPOKE = "https://api.spoke.example:6443"
CA = b"-----BEGIN CERTIFICATE-----\nMIIBcaptured\n-----END CERTIFICATE-----\n"
STEP = "verify_klusterlet_connections_v2"


def _b64(text):
    return base64.b64encode(text.encode()).decode()


def _import_secret(server):
    manifest = yaml.safe_dump(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "bootstrap-hub-kubeconfig", "namespace": "open-cluster-management-agent"},
            "data": {"kubeconfig": _b64(yaml.safe_dump({"clusters": [{"cluster": {"server": server}}]}))},
        }
    )
    return {"data": {"import.yaml": _b64(manifest)}}


class Spoke:
    """The managed cluster behind every client; `on_read` lets a test act between stages."""

    def __init__(self, hub_server):
        self.hub_server = hub_server
        self.reads = 0
        self.mutations = []
        self.on_read = None

    def read_namespaced_secret(self, name, namespace, **_kwargs):
        self.reads += 1
        if self.on_read:
            self.on_read(self.reads)
        if name == "hub-kubeconfig-secret":
            kubeconfig = yaml.safe_dump({"clusters": [{"cluster": {"server": self.hub_server}}]})
            return Mock(data={"kubeconfig": _b64(kubeconfig)})
        return Mock(data={})

    def patch_namespaced_secret(self, name, namespace, body, **_kwargs):
        self.mutations.append("patch_secret")
        embedded = yaml.safe_load(base64.b64decode(body["data"]["kubeconfig"]))
        self.hub_server = embedded["clusters"][0]["cluster"]["server"]

    def patch_namespaced_deployment(self, name, namespace, body, **_kwargs):
        self.mutations.append("restart")


@pytest.fixture
def spoke_dir(tmp_path):
    directory = tmp_path / "creds"
    directory.mkdir()
    (directory / "ca.crt").write_bytes(CA)
    (directory / "token").write_bytes(b"captured-token\n")
    return directory


def _write_kubeconfig(tmp_path, users=None, extra_clusters=(), extra_contexts=(), current=None):
    document = {
        "apiVersion": "v1",
        "clusters": [
            {"name": "spoke", "cluster": {"server": SPOKE, "certificate-authority": "creds/ca.crt"}},
            *extra_clusters,
        ],
        "contexts": [{"name": "spoke-admin", "context": {"cluster": "spoke", "user": "admin"}}, *extra_contexts],
        "users": users or [{"name": "admin", "user": {"tokenFile": "creds/token"}}],
    }
    if current:
        document["current-context"] = current
    path = tmp_path / "kubeconfig.yaml"
    path.write_text(yaml.safe_dump(document))
    return path


@pytest.fixture
def verifier(tmp_path, monkeypatch):
    def _make(kubeconfig_path, managed_clusters):
        monkeypatch.setenv("KUBECONFIG", str(kubeconfig_path))
        secondary = Mock()
        secondary.context = "secondary-ctx"
        secondary.list_managed_clusters_strict.return_value = StrictReadOutcome.from_items(managed_clusters)
        secondary.get_secret.side_effect = lambda namespace, name: _import_secret(NEW_HUB)
        state = StateManager(str(tmp_path / "state.json"))
        return (
            PostActivationVerification(secondary_client=secondary, state_manager=state, has_observability=False),
            state,
        )

    return _make


def _managed_cluster(name, url=SPOKE):
    return {"metadata": {"name": name, "uid": f"{name}-uid"}, "spec": {"managedClusterClientConfigs": [{"url": url}]}}


class Constructions:
    """Wrap the real loader: record what every spoke client was built with."""

    def __init__(self, monkeypatch):
        self.records = []
        self.private_dirs = []
        real = kube_config_module.new_client_from_config_dict
        real_new_dir = snapshot_module.new_private_dir

        def recording(document, context=None, persist_config=True, **kwargs):
            api_client = real(document, context=context, persist_config=persist_config, **kwargs)
            configuration = api_client.configuration
            ca = None
            if configuration.ssl_ca_cert:
                with open(configuration.ssl_ca_cert, "rb") as handle:
                    ca = handle.read()
            self.records.append(
                {
                    "context": context,
                    "host": configuration.host,
                    "bearer": configuration.auth_settings()["BearerToken"]["value"],
                    "ca": ca,
                    "ca_path": configuration.ssl_ca_cert,
                }
            )
            return api_client

        def recording_dir():
            path = real_new_dir()
            self.private_dirs.append(path)
            return path

        monkeypatch.setattr(snapshot_module.config, "new_client_from_config_dict", recording)
        monkeypatch.setattr("modules.post_activation.new_private_dir", recording_dir)


def _run(pav, spoke):
    with patch("modules.post_activation.client.CoreV1Api", return_value=spoke), patch(
        "modules.post_activation.client.AppsV1Api", return_value=spoke
    ), patch(
        "modules.post_activation.wait_for_condition",
        side_effect=lambda description, condition_fn, **kwargs: condition_fn().done,
    ):
        with pav.state.step(STEP, logging.getLogger("acm_switchover")) as should_run:
            if should_run:
                pav._verify_klusterlet_connections()


def test_every_stage_uses_the_content_captured_at_selection(tmp_path, spoke_dir, verifier, monkeypatch):
    """PR 3 #1: replace and delete the credential files between stages; nothing is re-read."""
    pav, state = verifier(_write_kubeconfig(tmp_path), [_managed_cluster("a")])
    built = Constructions(monkeypatch)
    spoke = Spoke(OLD_HUB)

    def tamper(read_count):
        # After the probe's first read: swap the token, delete the CA.
        if read_count == 1:
            (spoke_dir / "token").write_bytes(b"attacker-token\n")
            (spoke_dir / "ca.crt").unlink()

    spoke.on_read = tamper
    _run(pav, spoke)

    assert spoke.mutations == ["patch_secret", "restart"]
    assert len(built.records) >= 3  # probe, remediation, re-check
    for record in built.records:
        assert record["context"] == "spoke-admin"
        assert record["bearer"] == "Bearer captured-token"
        assert record["ca"] == CA
        assert os.path.dirname(record["ca_path"]) == built.private_dirs[0]
    assert state.is_step_completed(STEP)
    assert not os.path.exists(built.private_dirs[0])


def test_a_current_context_pointing_elsewhere_never_changes_the_target(tmp_path, spoke_dir, verifier, monkeypatch):
    """PR 3 #2: the matched context is named explicitly; current-context is never consulted."""
    other = {"name": "other", "cluster": {"server": "https://api.other.example:6443"}}
    kubeconfig = _write_kubeconfig(
        tmp_path,
        extra_clusters=(other,),
        extra_contexts=({"name": "other-admin", "context": {"cluster": "other", "user": "admin"}},),
        current="other-admin",
    )
    pav, _state = verifier(kubeconfig, [_managed_cluster("a")])
    built = Constructions(monkeypatch)

    _run(pav, Spoke(NEW_HUB))

    assert [record["host"] for record in built.records] == [SPOKE]


@pytest.mark.parametrize(
    ("user", "reason"),
    [
        ({"exec": {"command": "get-token"}}, "exec_credential_unsupported"),
        ({"auth-provider": {"name": "oidc"}}, "auth_provider_unsupported"),
        ({"token": ""}, "credential_empty"),
        ({"client-certificate-data": ""}, "credential_empty"),
    ],
)
def test_a_refused_credential_builds_no_client_while_a_plain_user_proceeds(
    tmp_path, spoke_dir, verifier, monkeypatch, user, reason, caplog
):
    """PR 3 #3: zero constructions, reads and mutations for the refused cluster."""
    b_cluster = {"name": "spoke-b", "cluster": {"server": "https://api.spoke-b.example:6443"}}
    kubeconfig = _write_kubeconfig(
        tmp_path,
        users=[{"name": "admin", "user": user}, {"name": "plain", "user": {"token": "plain-token"}}],
        extra_clusters=(b_cluster,),
        extra_contexts=({"name": "b-plain", "context": {"cluster": "spoke-b", "user": "plain"}},),
    )
    pav, state = verifier(
        kubeconfig, [_managed_cluster("a"), _managed_cluster("b", "https://api.spoke-b.example:6443")]
    )
    built = Constructions(monkeypatch)
    spoke = Spoke(NEW_HUB)
    caplog.set_level(logging.DEBUG, logger="acm_switchover")

    with pytest.raises(SwitchoverError, match=rf"a \({reason}\)"):
        _run(pav, spoke)

    assert [record["context"] for record in built.records] == ["b-plain"]
    assert spoke.mutations == []
    assert not state.is_step_completed(STEP)
    assert not os.path.exists(built.private_dirs[0])
    assert "get-token" not in caplog.text


@pytest.mark.parametrize(
    "cluster_extra",
    [{"insecure-skip-tls-verify": "false"}, {"insecure-skip-tls-verify": True}, {"tls-server-name": "x"}],
)
def test_tls_settings_that_void_verification_are_refused(tmp_path, spoke_dir, verifier, monkeypatch, cluster_extra):
    kubeconfig = _write_kubeconfig(tmp_path)
    document = yaml.safe_load(kubeconfig.read_text())
    document["clusters"][0]["cluster"].update(cluster_extra)
    kubeconfig.write_text(yaml.safe_dump(document))
    pav, _state = verifier(kubeconfig, [_managed_cluster("a")])
    built = Constructions(monkeypatch)

    with pytest.raises(SwitchoverError, match=r"a \(tls_verification_unproven\)"):
        _run(pav, Spoke(OLD_HUB))

    assert built.records == []


def test_no_captured_material_leaks_into_logs_errors_or_state(tmp_path, spoke_dir, verifier, monkeypatch, caplog):
    """PR 3 #5."""
    pav, state = verifier(_write_kubeconfig(tmp_path), [_managed_cluster("a"), _managed_cluster("b", "https://x:6443")])
    Constructions(monkeypatch)
    caplog.set_level(logging.DEBUG)
    spoke = Spoke("https://api.new-hub.example:443")

    with pytest.raises(SwitchoverError) as excinfo:
        _run(pav, spoke)

    state.flush_state()
    with open(tmp_path / "state.json") as handle:
        state_text = handle.read()
    for secret in ("captured-token", "MIIBcaptured"):
        assert secret not in caplog.text
        assert secret not in str(excinfo.value)
        assert secret not in state_text


def test_a_remediation_finishing_after_its_timeout_is_reported_and_outlives_cleanup(
    tmp_path, spoke_dir, verifier, monkeypatch, caplog
):
    """PR 3 #3: the late worker's real change is reported; its credential files exist until it ends."""
    import modules.post_activation as post_activation_module

    pav, _state = verifier(_write_kubeconfig(tmp_path), [_managed_cluster("a")])
    built = Constructions(monkeypatch)
    spoke = Spoke(OLD_HUB)
    seen_material = []
    calls = {"wait": 0}
    real_wait = post_activation_module.wait

    def timing_out_wait(futures, timeout):
        calls["wait"] += 1
        if calls["wait"] == 2:  # the remediation batch
            return set(), set(futures)
        return real_wait(futures, timeout=timeout)

    real_restart = Spoke.patch_namespaced_deployment

    def slow_restart(self, name, namespace, body, **kwargs):
        seen_material.append(os.path.isdir(built.private_dirs[0]) and bool(os.listdir(built.private_dirs[0])))
        return real_restart(self, name, namespace, body, **kwargs)

    monkeypatch.setattr(Spoke, "patch_namespaced_deployment", slow_restart)
    monkeypatch.setattr(post_activation_module, "wait", timing_out_wait)
    caplog.set_level(logging.WARNING, logger="acm_switchover")

    with pytest.raises(SwitchoverError, match="Klusterlet remediation failed for cluster"):
        _run(pav, spoke)

    assert spoke.mutations == ["patch_secret", "restart"]
    assert seen_material == [True]
    assert "changed the cluster before it was reported as failed" in caplog.text
    assert not os.path.exists(built.private_dirs[0])


def test_a_timed_out_check_worker_keeps_its_material_until_it_finishes(tmp_path, spoke_dir, verifier, monkeypatch):
    """The check executor waits for every worker before the credential directory is removed."""
    import time

    import modules.post_activation as post_activation_module

    pav, _state = verifier(_write_kubeconfig(tmp_path), [_managed_cluster("a")])
    built = Constructions(monkeypatch)
    spoke = Spoke(NEW_HUB)
    seen = []

    def slow_read(read_count):
        time.sleep(0.3)
        seen.append(bool(os.listdir(built.private_dirs[0])) if os.path.isdir(built.private_dirs[0]) else False)

    spoke.on_read = slow_read
    real_wait = post_activation_module.wait
    monkeypatch.setattr(post_activation_module, "wait", lambda futures, timeout: real_wait(futures, timeout=0.05))

    with pytest.raises(SwitchoverError, match="timed out"):
        _run(pav, spoke)

    assert seen and all(seen)
    assert not os.path.exists(built.private_dirs[0])


CERT = b"-----BEGIN CERTIFICATE-----\nMIIBclient\n-----END CERTIFICATE-----\n"
KEY = b"-----BEGIN PRIVATE KEY-----\nMIIEkey\n-----END PRIVATE KEY-----\n"


@pytest.mark.parametrize("field", ["ca.crt", "client.crt", "client.key", "token"])
@pytest.mark.parametrize("action", ["replace", "delete"])
def test_each_file_backed_credential_is_bound_across_every_stage(
    tmp_path, spoke_dir, verifier, monkeypatch, field, action
):
    """PR 3 #1 for all four file-backed fields: the captured bytes reach every client."""
    (spoke_dir / "client.crt").write_bytes(CERT)
    (spoke_dir / "client.key").write_bytes(KEY)
    users = [
        {
            "name": "admin",
            "user": {
                "tokenFile": "creds/token",
                "client-certificate": "creds/client.crt",
                "client-key": "creds/client.key",
            },
        }
    ]
    pav, _state = verifier(_write_kubeconfig(tmp_path, users=users), [_managed_cluster("a")])
    built = []
    real = kube_config_module.new_client_from_config_dict

    def recording(document, context=None, persist_config=True, **kwargs):
        api_client = real(document, context=context, persist_config=persist_config, **kwargs)
        configuration = api_client.configuration
        contents = {}
        for name, path in (
            ("ca.crt", configuration.ssl_ca_cert),
            ("client.crt", configuration.cert_file),
            ("client.key", configuration.key_file),
        ):
            with open(path, "rb") as handle:
                contents[name] = handle.read()
        contents["token"] = configuration.auth_settings()["BearerToken"]["value"]
        built.append(contents)
        return api_client

    monkeypatch.setattr(snapshot_module.config, "new_client_from_config_dict", recording)
    spoke = Spoke(OLD_HUB)

    def tamper(read_count):
        if read_count == 1:
            target = spoke_dir / field
            if action == "delete":
                target.unlink()
            else:
                target.write_bytes(b"REPLACED\n")

    spoke.on_read = tamper
    _run(pav, spoke)

    assert spoke.mutations == ["patch_secret", "restart"]
    assert len(built) >= 3
    for contents in built:
        assert contents == {"ca.crt": CA, "client.crt": CERT, "client.key": KEY, "token": "Bearer captured-token"}


def test_a_partial_change_that_then_fails_is_reported(tmp_path, spoke_dir, verifier, monkeypatch, caplog):
    """Codex PR 3: a secret patch that succeeds before a later failure is reported, not lost."""
    pav, _state = verifier(_write_kubeconfig(tmp_path), [_managed_cluster("a")])
    Constructions(monkeypatch)
    spoke = Spoke(OLD_HUB)
    caplog.set_level(logging.WARNING, logger="acm_switchover")

    def no_visibility(*_args, **_kwargs):
        raise SwitchoverError("bootstrap-hub-kubeconfig secret not visible")

    monkeypatch.setattr(PostActivationVerification, "_wait_for_secret_visibility", no_visibility)
    with pytest.raises(SwitchoverError, match="Klusterlet remediation failed for cluster"):
        _run(pav, spoke)

    assert spoke.mutations == ["patch_secret"]
    assert "changed the cluster before it was reported as failed" in caplog.text


def test_a_refused_credential_is_located_for_the_operator(tmp_path, spoke_dir, verifier, monkeypatch, caplog):
    users = [{"name": "admin", "user": {"exec": {"command": "get-token", "env": [{"name": "SECRET", "value": "zzz"}]}}}]
    kubeconfig = _write_kubeconfig(tmp_path, users=users)
    pav, _state = verifier(kubeconfig, [_managed_cluster("a")])
    Constructions(monkeypatch)
    caplog.set_level(logging.WARNING, logger="acm_switchover")

    with pytest.raises(SwitchoverError, match=r"a \(exec_credential_unsupported\)"):
        _run(pav, Spoke(OLD_HUB))

    assert f"Kubeconfig user 'admin' (context 'spoke-admin', from {kubeconfig})" in caplog.text
    assert "zzz" not in caplog.text and "get-token" not in caplog.text


def test_a_cleanup_failure_fails_a_passing_step(tmp_path, spoke_dir, verifier, monkeypatch):
    pav, state = verifier(_write_kubeconfig(tmp_path), [_managed_cluster("a")])
    Constructions(monkeypatch)
    monkeypatch.setattr(
        "modules.post_activation.remove_private_dir",
        lambda path: (_ for _ in ()).throw(snapshot_module.SnapshotError("credential_cleanup_failed")),
    )
    with pytest.raises(SwitchoverError, match="credential_cleanup_failed"):
        _run(pav, Spoke(NEW_HUB))
    assert not state.is_step_completed(STEP)


def test_a_cleanup_failure_never_masks_the_primary_error(tmp_path, spoke_dir, verifier, monkeypatch, caplog):
    pav, _state = verifier(_write_kubeconfig(tmp_path), [_managed_cluster("a")])
    Constructions(monkeypatch)
    monkeypatch.setattr(
        "modules.post_activation.remove_private_dir",
        lambda path: (_ for _ in ()).throw(snapshot_module.SnapshotError("credential_cleanup_failed")),
    )
    caplog.set_level(logging.ERROR, logger="acm_switchover")
    with pytest.raises(SwitchoverError, match="endpoint_mismatch_unverified"):
        _run(pav, Spoke("https://api.new-hub.example:443"))
    assert "Could not remove klusterlet credential material" in caplog.text


@pytest.mark.parametrize("field", ["ca.crt", "client.crt", "client.key", "token"])
def test_a_change_between_selection_and_the_probe_is_never_seen(tmp_path, spoke_dir, verifier, monkeypatch, field):
    """PR 3 #1: the stage between snapshot capture and the first probe client."""
    import modules.post_activation as post_activation_module

    (spoke_dir / "client.crt").write_bytes(CERT)
    (spoke_dir / "client.key").write_bytes(KEY)
    users = [
        {
            "name": "admin",
            "user": {
                "tokenFile": "creds/token",
                "client-certificate": "creds/client.crt",
                "client-key": "creds/client.key",
            },
        }
    ]
    pav, _state = verifier(_write_kubeconfig(tmp_path, users=users), [_managed_cluster("a")])
    built = Constructions(monkeypatch)
    real_build = post_activation_module.build_snapshot

    def capture_then_tamper(*args, **kwargs):
        snapshot = real_build(*args, **kwargs)
        (spoke_dir / field).write_bytes(b"REPLACED\n")
        return snapshot

    monkeypatch.setattr(post_activation_module, "build_snapshot", capture_then_tamper)
    _run(pav, Spoke(NEW_HUB))

    assert [record["bearer"] for record in built.records] == ["Bearer captured-token"]
    assert built.records[0]["ca"] == CA
