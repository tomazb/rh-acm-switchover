# SPDX-License-Identifier: MIT
"""Snapshot-built spoke clients for the klusterlet modules (R4-06 PR 3).

Collection mirror of ``lib/kubeconfig_snapshot.py`` (the two share no code; the shared
vectors ``tests/fixtures/r4_06_credential_vectors.json`` hold them equal). Implements
docs/plans/2026-10-15-r4-06-current-base-amendment.md §6: the selected context's cluster
and user are captured once, including the contents of every file-backed credential
(``O_NOFOLLOW`` + ``fstat`` on the open descriptor), embedded ``*-data`` is decoded
strictly, unsafe credentials and TLS settings are refused before any client exists, and the
client is built from ``0600`` files in a private ``0700`` directory with the token inline
and the selected context named explicitly. The client loader never sees ``*-data``, so its
process-global, content-keyed temp-file cache is never used. Captured content is never
logged or returned in a module result.
"""

from __future__ import annotations

import base64
import binascii
import errno
import hashlib
import os
import shutil
import stat
import tempfile
import threading
from typing import Any, NamedTuple

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.endpoint_identity import (
    REASON_CLIENT_ENDPOINT_MISMATCH,
    normalized_equal,
)

REASON_TLS_VERIFICATION_UNPROVEN = "tls_verification_unproven"
REASON_EXEC_CREDENTIAL_UNSUPPORTED = "exec_credential_unsupported"
REASON_AUTH_PROVIDER_UNSUPPORTED = "auth_provider_unsupported"
REASON_CREDENTIAL_UNSUPPORTED = "credential_unsupported"
REASON_CREDENTIAL_UNREADABLE = "credential_unreadable"
REASON_CREDENTIAL_SYMLINK = "credential_symlink"
REASON_CREDENTIAL_NOT_REGULAR = "credential_not_regular"
REASON_CREDENTIAL_EMPTY = "credential_empty"
REASON_CREDENTIAL_OVERSIZED = "credential_oversized"
REASON_CLIENT_CONSTRUCTION_FAILED = "client_construction_failed"
REASON_CREDENTIAL_CLEANUP_FAILED = "credential_cleanup_failed"

_READ_CHUNK = 64 * 1024
_TRAILING_ASCII_WHITESPACE = " \t\r\n"
_FILE_FIELDS = (
    ("cluster", "certificate-authority", "ca"),
    ("user", "client-certificate", "cert"),
    ("user", "client-key", "key"),
)
# The client loader is not documented as thread-safe while it builds a client.
_CONSTRUCTION_LOCK = threading.Lock()


class SnapshotError(Exception):
    """A sanitized refusal: a stable reason and the entry kind it concerns, never content."""

    def __init__(self, reason: str, entry: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        # "cluster" or "user": which entry of the selected context was refused (provenance).
        self.entry = entry


class SpokeSnapshot(NamedTuple):
    """What one spoke client is built from. Content is bytes/str captured once."""

    context_name: str
    server: str
    ca: bytes | None
    cert: bytes | None
    key: bytes | None
    token: str | None
    username: str | None
    password: str | None
    # Provenance only (the kubeconfig the context came from): never used to build a client.
    source: str = ""

    def __repr__(self) -> str:  # never print captured material
        return f"SpokeSnapshot(context={self.context_name!r}, server={self.server!r})"


def capture_file(path: str, size_limit: int) -> bytes:
    """Read one credential file once, through a descriptor that is the file validated.

    ``O_NOFOLLOW`` refuses a credential path that is itself a symlink (parent directories
    resolve normally: the content is captured once, so they open no later window);
    ``O_NONBLOCK`` keeps a FIFO or device from blocking the open before it is refused;
    ``fstat`` on the open descriptor (never a second path lookup) requires a regular,
    non-empty file within the limit; the read is bounded on the same descriptor, so a file
    that grows after ``fstat`` is still refused.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise SnapshotError(REASON_CREDENTIAL_SYMLINK) from None
        raise SnapshotError(REASON_CREDENTIAL_UNREADABLE) from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SnapshotError(REASON_CREDENTIAL_NOT_REGULAR)
        if info.st_size == 0:
            raise SnapshotError(REASON_CREDENTIAL_EMPTY)
        if info.st_size > size_limit:
            raise SnapshotError(REASON_CREDENTIAL_OVERSIZED)
        chunks = []
        total = 0
        while total <= size_limit:
            chunk = os.read(descriptor, min(_READ_CHUNK, size_limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    except OSError:
        raise SnapshotError(REASON_CREDENTIAL_UNREADABLE) from None
    finally:
        os.close(descriptor)
    if total > size_limit:
        raise SnapshotError(REASON_CREDENTIAL_OVERSIZED)
    if total == 0:
        raise SnapshotError(REASON_CREDENTIAL_EMPTY)
    return b"".join(chunks)


def _resolve(base_dir: str | None, path: str) -> str:
    """A credential path, relative ones against the entry's own source directory only."""
    if os.path.isabs(path):
        return path
    if not base_dir:
        # Never the process working directory: the entry's source is unknown.
        raise SnapshotError(REASON_CREDENTIAL_UNREADABLE)
    return os.path.join(base_dir, path)


def _field_content(entry: dict[str, Any], field: str, base_dir: str, size_limit: int) -> bytes | None:
    data_key = f"{field}-data"
    if data_key in entry:
        value = entry[data_key]
        if not isinstance(value, str):
            raise SnapshotError(REASON_CREDENTIAL_UNREADABLE)
        try:
            # Line-wrapped data is valid (kubectl accepts it); anything else outside the
            # alphabet is refused rather than silently discarded.
            content = base64.b64decode("".join(value.split()), validate=True)
        except (binascii.Error, ValueError):
            raise SnapshotError(REASON_CREDENTIAL_UNREADABLE) from None
        if not content:
            # An empty *-data shadows the file path in the client loader: no credential at all.
            raise SnapshotError(REASON_CREDENTIAL_EMPTY)
        return content
    if field in entry:
        path = entry[field]
        if not isinstance(path, str) or not path:
            raise SnapshotError(REASON_CREDENTIAL_UNREADABLE)
        return capture_file(_resolve(base_dir, path), size_limit)
    return None


def _token(user: dict[str, Any], base_dir: str, size_limit: int) -> str | None:
    if "token" in user:
        token = user["token"]
        if not isinstance(token, str):
            raise SnapshotError(REASON_CREDENTIAL_UNREADABLE)
        if not token.strip():
            # The loader would build an anonymous client from an empty token.
            raise SnapshotError(REASON_CREDENTIAL_EMPTY)
        return token
    if "tokenFile" in user:
        path = user["tokenFile"]
        if not isinstance(path, str) or not path:
            raise SnapshotError(REASON_CREDENTIAL_UNREADABLE)
        raw = capture_file(_resolve(base_dir, path), size_limit)
        try:
            token = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise SnapshotError(REASON_CREDENTIAL_UNREADABLE) from None
        token = token.rstrip(_TRAILING_ASCII_WHITESPACE)
        if not token:
            raise SnapshotError(REASON_CREDENTIAL_EMPTY)
        return token
    return None


def build_snapshot(
    context_name: str,
    cluster: dict[str, Any],
    cluster_base_dir: str,
    user: dict[str, Any] | None,
    user_base_dir: str,
    user_reference: str | None,
    size_limit: int,
    source: str = "",
) -> SpokeSnapshot:
    """Capture one selected context, or raise ``SnapshotError`` (amendment §6).

    ``cluster``/``user`` are the entries' bodies; each base dir is the directory of the file
    that entry came from, so relative credential paths resolve against their own source.
    ``user`` is None when the context names no user, or names one that does not exist
    (``user_reference`` set): the latter is refused, never turned into an anonymous client.
    """
    try:
        _check_cluster(cluster)
    except SnapshotError as exc:
        raise SnapshotError(exc.reason, "cluster") from None
    try:
        user_body = _check_user(user, user_reference)
    except SnapshotError as exc:
        raise SnapshotError(exc.reason, "user") from None
    contents: dict[str, bytes | None] = {}
    for kind, field, name in _FILE_FIELDS:
        entry, base_dir = (cluster, cluster_base_dir) if kind == "cluster" else (user_body, user_base_dir)
        try:
            contents[name] = _field_content(entry, field, base_dir, size_limit)
        except SnapshotError as exc:
            raise SnapshotError(exc.reason, kind) from None
    try:
        token = _token(user_body, user_base_dir, size_limit)
    except SnapshotError as exc:
        raise SnapshotError(exc.reason, "user") from None
    username, password = user_body.get("username"), user_body.get("password")
    has_pair = username is not None and password is not None
    if (contents["cert"] is None) != (contents["key"] is None):
        raise SnapshotError(REASON_CREDENTIAL_UNSUPPORTED, "user")
    if token is None and not has_pair and contents["cert"] is None:
        # No credential at all would be an anonymous client: never built (amendment §6).
        raise SnapshotError(REASON_CREDENTIAL_UNSUPPORTED, "user")
    return SpokeSnapshot(
        context_name=context_name,
        server=cluster["server"],
        ca=contents["ca"],
        cert=contents["cert"],
        key=contents["key"],
        token=token,
        username=username,
        password=password,
        source=source,
    )


def _check_cluster(cluster) -> None:
    server = cluster.get("server")
    if not isinstance(server, str) or not server.lower().startswith("https://"):
        raise SnapshotError(REASON_TLS_VERIFICATION_UNPROVEN)
    if "insecure-skip-tls-verify" in cluster and cluster["insecure-skip-tls-verify"] is not False:
        # The loader applies `not value`: the string "false" or None would turn verification off.
        raise SnapshotError(REASON_TLS_VERIFICATION_UNPROVEN)
    if "tls-server-name" in cluster or "proxy-url" in cluster:
        raise SnapshotError(REASON_TLS_VERIFICATION_UNPROVEN)


def _check_user(user, user_reference: str | None):
    if user is None and user_reference:
        raise SnapshotError(REASON_CREDENTIAL_UNSUPPORTED)
    user = user or {}
    if "exec" in user:
        raise SnapshotError(REASON_EXEC_CREDENTIAL_UNSUPPORTED)
    if "auth-provider" in user:
        raise SnapshotError(REASON_AUTH_PROVIDER_UNSUPPORTED)
    if "username" in user or "password" in user:
        # A malformed pair would otherwise be dropped, leaving an anonymous client.
        username, password = user.get("username"), user.get("password")
        if not (isinstance(username, str) and username and isinstance(password, str) and password):
            raise SnapshotError(REASON_CREDENTIAL_UNSUPPORTED)
    return user


def new_private_dir() -> str:
    """A ``0700`` directory for one run's materialized credentials."""
    return tempfile.mkdtemp(prefix="acm-switchover-spoke-")


def remove_private_dir(path: str) -> None:
    """Remove the materialized credentials; an already-absent directory is fine, nothing else is."""
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        return
    except OSError:
        raise SnapshotError(REASON_CREDENTIAL_CLEANUP_FAILED) from None


def _write_private(directory: str, content: bytes) -> str:
    """A ``0600`` file holding ``content`` in the private directory, written once per content.

    Files are named by their content's digest and published with an atomic rename, so every
    client of a run built from the same snapshot reuses one file, and no reader ever sees a
    partly written one.
    """
    path = os.path.join(directory, hashlib.sha256(content).hexdigest())
    if os.path.isfile(path):
        return path
    descriptor, staging = tempfile.mkstemp(dir=directory)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
    finally:
        os.close(descriptor)
    os.replace(staging, path)
    return path


def materialize(snapshot: SpokeSnapshot, private_dir: str) -> dict[str, Any]:
    """The client dict for one snapshot: our own files, an inline token, no current-context.

    Without ``current-context`` the loader can only select the context named explicitly,
    so an omitted ``context=`` argument fails instead of defaulting to anything.
    """
    if not os.path.isabs(private_dir):
        raise SnapshotError(REASON_CLIENT_CONSTRUCTION_FAILED)
    # The loader reads CA/cert/key only for a lowercase "https" host; the scheme is validated
    # case-insensitively, so it is written in its canonical form.
    cluster: dict[str, Any] = {"server": "https://" + snapshot.server[len("https://") :]}
    user: dict[str, Any] = {}
    if snapshot.ca is not None:
        cluster["certificate-authority"] = _write_private(private_dir, snapshot.ca)
    if snapshot.cert is not None:
        user["client-certificate"] = _write_private(private_dir, snapshot.cert)
    if snapshot.key is not None:
        user["client-key"] = _write_private(private_dir, snapshot.key)
    if snapshot.token is not None:
        user["token"] = snapshot.token
    if snapshot.username is not None and snapshot.password is not None:
        user["username"] = snapshot.username
        user["password"] = snapshot.password
    return {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{"name": "spoke", "cluster": cluster}],
        "users": [{"name": "spoke", "user": user}],
        "contexts": [{"name": snapshot.context_name, "context": {"cluster": "spoke", "user": "spoke"}}],
    }


def build_api_client(snapshot: SpokeSnapshot, private_dir: str, api_url: str):
    """Construct the spoke ``ApiClient`` from the snapshot alone, and prove what it uses."""
    document = materialize(snapshot, private_dir)
    cluster = document["clusters"][0]["cluster"]
    user = document["users"][0]["user"]
    try:
        from kubernetes import config

        with _CONSTRUCTION_LOCK:
            api_client = config.new_client_from_config_dict(
                document, context=snapshot.context_name, persist_config=False
            )
    except Exception:  # noqa: BLE001 - loader messages can quote credential material
        raise SnapshotError(REASON_CLIENT_CONSTRUCTION_FAILED) from None
    configuration = api_client.configuration
    if configuration.verify_ssl is not True:
        raise SnapshotError(REASON_TLS_VERIFICATION_UNPROVEN)
    if (
        configuration.ssl_ca_cert != cluster.get("certificate-authority")
        or configuration.cert_file != user.get("client-certificate")
        or configuration.key_file != user.get("client-key")
    ):
        raise SnapshotError(REASON_CLIENT_CONSTRUCTION_FAILED)
    # The effective authentication is exactly the captured one: the token, else the basic
    # pair, else none (certificate-only or anonymous).
    bearer = (configuration.auth_settings().get("BearerToken") or {}).get("value")
    if snapshot.token is not None:
        expected = f"Bearer {snapshot.token}"
    elif snapshot.username is not None and snapshot.password is not None:
        pair = f"{snapshot.username}:{snapshot.password}".encode()
        expected = "Basic " + base64.b64encode(pair).decode()
    else:
        expected = None
    if bearer != expected:
        raise SnapshotError(REASON_CLIENT_CONSTRUCTION_FAILED)
    if not normalized_equal(configuration.host, api_url):
        raise SnapshotError(REASON_CLIENT_ENDPOINT_MISMATCH)
    return api_client
