"""R4-06 PR 3: snapshot capture parity and real-loader construction (amendment 2026-10-15 §6).

Both form factors must capture the same bytes from the same files, or refuse with the same
reason, for every case in tests/fixtures/r4_06_credential_vectors.json. The construction
tests run the real Kubernetes client loader installed in the lane (the minimum-client CI job
runs them against kubernetes 28.1.0).
"""

import base64
import json
import os
import pathlib
import threading

import kubernetes.config
import pytest
from kubernetes.config import kube_config

import ansible_collections.tomazb.acm_switchover.plugins.module_utils.kubeconfig_snapshot as col
import lib.kubeconfig_snapshot as py

VECTORS = json.loads((pathlib.Path(__file__).parent / "fixtures" / "r4_06_credential_vectors.json").read_text())
IMPLEMENTATIONS = [pytest.param(py, id="python"), pytest.param(col, id="collection")]
SERVER = "https://api.spoke.example:6443"
LIMIT = 1024 * 1024


def _write_files(directory, files):
    for name, spec in files.items():
        path = directory / name
        if isinstance(spec, dict) and spec.get("symlink_to"):
            path.symlink_to(directory / spec["symlink_to"])
        elif isinstance(spec, dict) and spec.get("directory"):
            path.mkdir()
        elif isinstance(spec, dict) and "size" in spec:
            path.write_bytes(b"x" * spec["size"])
        else:
            path.write_bytes(base64.b64decode(spec))


def _snapshot(impl, row, directory):
    return impl.build_snapshot(
        "spoke-admin",
        row["cluster"],
        str(directory),
        row["user"],
        str(directory),
        row.get("user_reference", "admin" if row["user"] is not None else None),
        row.get("size_limit", LIMIT),
    )


@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
@pytest.mark.parametrize("row", VECTORS["cases"], ids=lambda row: row["case"])
def test_credential_vector(impl, row, tmp_path):
    _write_files(tmp_path, row["files"])
    if "reject" in row:
        with pytest.raises(impl.SnapshotError) as excinfo:
            _snapshot(impl, row, tmp_path)
        assert excinfo.value.reason == row["reject"]
        assert excinfo.value.__context__ is None or excinfo.value.__suppress_context__
        return
    snapshot = _snapshot(impl, row, tmp_path)
    expect = row["expect"]
    for field in ("ca", "cert", "key"):
        value = getattr(snapshot, field)
        assert (base64.b64encode(value).decode() if value is not None else None) == expect[field], field
    assert snapshot.token == expect["token"]


@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
def test_the_snapshot_never_prints_captured_material(impl, tmp_path):
    snapshot = impl.build_snapshot(
        "c", {"server": SERVER}, str(tmp_path), {"token": "s3cr3t"}, str(tmp_path), "u", LIMIT
    )
    assert "s3cr3t" not in repr(snapshot) and "s3cr3t" not in str(snapshot)


# ---------------------------------------------------------------- real-loader construction

CA = b"-----BEGIN CERTIFICATE-----\r\nMIIBfake\r\n-----END CERTIFICATE-----\r\n"
CERT = b"-----BEGIN CERTIFICATE-----\nMIIBclient\n-----END CERTIFICATE-----"
KEY = b"-----BEGIN PRIVATE KEY-----\nMIIEkey\n-----END PRIVATE KEY-----\n"


def _file_backed(impl, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "ca.crt").write_bytes(CA)
    (source / "client.crt").write_bytes(CERT)
    (source / "token").write_bytes(b"abc.def\n")
    cluster = {"server": SERVER, "certificate-authority": "ca.crt"}
    user = {"client-certificate": "client.crt", "client-key-data": base64.b64encode(KEY).decode(), "tokenFile": "token"}
    return source, impl.build_snapshot("spoke-admin", cluster, str(source), user, str(source), "admin", LIMIT)


@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
def test_the_client_uses_only_captured_content_in_the_private_directory(impl, tmp_path):
    source, snapshot = _file_backed(impl, tmp_path)
    # Replace and delete every source file after capture: nothing is read again.
    (source / "ca.crt").write_bytes(b"REPLACED")
    (source / "client.crt").unlink()
    (source / "token").write_bytes(b"other-token\n")
    private = impl.new_private_dir()
    try:
        assert oct(os.stat(private).st_mode & 0o777) == "0o700"
        configuration = impl.build_api_client(snapshot, private, "https://API.spoke.example:6443/").configuration
        for path, content in (
            (configuration.ssl_ca_cert, CA),
            (configuration.cert_file, CERT),
            (configuration.key_file, KEY),
        ):
            assert os.path.dirname(path) == private
            assert oct(os.stat(path).st_mode & 0o777) == "0o600"
            with open(path, "rb") as handle:
                assert handle.read() == content
        assert configuration.auth_settings()["BearerToken"]["value"] == "Bearer abc.def"
        assert configuration.verify_ssl is True
    finally:
        impl.remove_private_dir(private)
    assert not os.path.exists(private)


@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
def test_a_poisoned_loader_cache_is_never_consulted(impl, tmp_path, monkeypatch):
    """The loader's process-global cache maps content to a path; ours never feeds it *-data."""
    _source, snapshot = _file_backed(impl, tmp_path)
    poisoned = dict(kube_config._temp_files)
    poisoned[str(len(CA)) + base64.standard_b64encode(CA).decode()] = str(tmp_path / "deleted")
    for content in (CA, CERT, KEY):
        poisoned[base64.standard_b64encode(content).decode()] = str(tmp_path / "deleted")
    monkeypatch.setattr(kube_config, "_temp_files", poisoned)
    private = impl.new_private_dir()
    try:
        configuration = impl.build_api_client(snapshot, private, SERVER).configuration
        assert os.path.dirname(configuration.ssl_ca_cert) == private
        assert kube_config._temp_files == poisoned
    finally:
        impl.remove_private_dir(private)


@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
def test_only_the_explicit_context_can_be_selected(impl, tmp_path):
    """There is no current-context to fall back to: an omitted context fails, never defaults."""
    _source, snapshot = _file_backed(impl, tmp_path)
    private = impl.new_private_dir()
    try:
        document = impl.materialize(snapshot, private)
        assert "current-context" not in document
        with pytest.raises(Exception):
            kube_config.new_client_from_config_dict(document, persist_config=False)
    finally:
        impl.remove_private_dir(private)


@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
def test_a_client_bound_elsewhere_is_refused(impl, tmp_path):
    _source, snapshot = _file_backed(impl, tmp_path)
    private = impl.new_private_dir()
    try:
        with pytest.raises(impl.SnapshotError) as excinfo:
            impl.build_api_client(snapshot, private, "https://api.other.example:6443")
        assert excinfo.value.reason == "client_endpoint_mismatch"
    finally:
        impl.remove_private_dir(private)


@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
def test_concurrent_construction_gives_each_worker_its_own_material(impl, tmp_path):
    snapshots = []
    for index in range(16):
        token = f"token-{index}"
        snapshots.append(
            impl.build_snapshot(
                "c",
                {"server": SERVER, "certificate-authority-data": base64.b64encode(CA).decode()},
                str(tmp_path),
                {"token": token},
                str(tmp_path),
                "u",
                LIMIT,
            )
        )
    private = impl.new_private_dir()
    results = {}

    def build(index):
        configuration = impl.build_api_client(snapshots[index], private, SERVER).configuration
        results[index] = (configuration.auth_settings()["BearerToken"]["value"], configuration.ssl_ca_cert)

    try:
        threads = [threading.Thread(target=build, args=(index,)) for index in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert {index: bearer for index, (bearer, _) in results.items()} == {
            index: f"Bearer token-{index}" for index in range(16)
        }
        assert len({ca for _, ca in results.values()}) == 16
    finally:
        impl.remove_private_dir(private)


@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
def test_a_loader_failure_is_a_sanitized_construction_failure(impl, tmp_path, monkeypatch):
    _source, snapshot = _file_backed(impl, tmp_path)

    def explode(*_args, **_kwargs):
        raise ValueError("could not parse token SECRETLEAK")

    monkeypatch.setattr(kubernetes.config, "new_client_from_config_dict", explode)
    private = impl.new_private_dir()
    try:
        with pytest.raises(impl.SnapshotError) as excinfo:
            impl.build_api_client(snapshot, private, SERVER)
        assert excinfo.value.reason == "client_construction_failed"
        assert "SECRETLEAK" not in str(excinfo.value) and excinfo.value.__suppress_context__
    finally:
        impl.remove_private_dir(private)


@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
@pytest.mark.parametrize(
    ("doctor", "reason"),
    [
        (lambda cfg: setattr(cfg, "verify_ssl", False), "tls_verification_unproven"),
        (lambda cfg: setattr(cfg, "ssl_ca_cert", "/somewhere/else/ca.crt"), "client_construction_failed"),
        (lambda cfg: setattr(cfg, "cert_file", "/somewhere/else/client.crt"), "client_construction_failed"),
        (lambda cfg: setattr(cfg, "key_file", "/somewhere/else/client.key"), "client_construction_failed"),
        (
            lambda cfg: (
                setattr(cfg, "refresh_api_key_hook", None),
                cfg.api_key.update({"BearerToken": "Bearer other", "authorization": "Bearer other"}),
            ),
            "client_construction_failed",
        ),
    ],
    ids=["verify-off", "foreign-ca", "foreign-cert", "foreign-key", "other-token"],
)
def test_a_client_that_does_not_prove_its_material_is_refused(impl, tmp_path, monkeypatch, doctor, reason):
    """The post-construction assertions are what prove the client uses the captured content."""
    _source, snapshot = _file_backed(impl, tmp_path)
    real = kube_config.new_client_from_config_dict

    def doctored(*args, **kwargs):
        api_client = real(*args, **kwargs)
        doctor(api_client.configuration)
        return api_client

    monkeypatch.setattr(kubernetes.config, "new_client_from_config_dict", doctored)
    private = impl.new_private_dir()
    try:
        with pytest.raises(impl.SnapshotError) as excinfo:
            impl.build_api_client(snapshot, private, SERVER)
        assert excinfo.value.reason == reason
    finally:
        impl.remove_private_dir(private)
