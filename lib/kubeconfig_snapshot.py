"""Snapshot-built spoke clients for the klusterlet path (R4-06 PR 3).

Implements docs/plans/2026-07-29-kubeconfig-ambiguity-guard-design.md §4 as amended by
docs/plans/2026-10-15-r4-06-current-base-amendment.md §6:

- ``build_snapshot`` captures the selected context's cluster and user, including the
  *contents* of every file-backed credential (``certificate-authority``,
  ``client-certificate``, ``client-key``, ``tokenFile``) read once through ``O_NOFOLLOW``
  and ``fstat`` on the open descriptor. Embedded ``*-data`` is decoded strictly here too
  (the amendment supersedes July's "pass ``*-data`` through unchanged": the client loader
  never sees ``*-data``, so its process-global, content-keyed temp-file cache is never
  used). Everything that cannot be proven safe is refused before any client exists.
- ``build_api_client`` materializes the snapshot into ``0600`` files inside a private
  ``0700`` directory, passes the token inline, and constructs the client from a dict that
  names the selected context explicitly and has no ``current-context``. It then proves the
  client uses exactly that material: effective TLS verification, our CA/certificate/key
  paths, the captured token, and the proven endpoint.

The snapshot is the only source for every spoke client of a cluster in one run; files are
never re-read after capture. Captured content is never logged or reported. The collection
mirrors this module in ``plugins/module_utils/kubeconfig_snapshot.py``; shared vectors
(``tests/fixtures/r4_06_credential_vectors.json``) hold the two equal.
"""

import base64
import binascii
import errno
import os
import shutil
import stat
import tempfile
import threading
from typing import Any, Dict, NamedTuple, Optional

from kubernetes import config

from lib.endpoint_identity import normalized_equal

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
REASON_CLIENT_ENDPOINT_MISMATCH = "client_endpoint_mismatch"

_READ_CHUNK = 64 * 1024
_TOKEN_TRAILING_WHITESPACE = " \t\r\n"
_FILE_FIELDS = (
    ("cluster", "certificate-authority", "ca"),
    ("user", "client-certificate", "cert"),
    ("user", "client-key", "key"),
)
# The client loader is not documented as thread-safe while it builds a client.
_CONSTRUCTION_LOCK = threading.Lock()


class SnapshotError(Exception):
    """A sanitized refusal: a stable reason, never credential content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SpokeSnapshot(NamedTuple):
    """What one spoke client is built from. Content is bytes/str captured once."""

    context_name: str
    server: str
    ca: Optional[bytes]
    cert: Optional[bytes]
    key: Optional[bytes]
    token: Optional[str]
    username: Optional[str]
    password: Optional[str]
    # Provenance only (the kubeconfig the context came from): never used to build a client.
    source: str = ""

    def __repr__(self) -> str:  # never print captured material
        return f"SpokeSnapshot(context={self.context_name!r}, server={self.server!r})"


def capture_file(path: str, size_limit: int) -> bytes:
    """Read one credential file once, through a descriptor that is the file validated.

    ``O_NOFOLLOW`` refuses a symlink; ``fstat`` on the open descriptor (never a second
    path lookup) requires a regular, non-empty file within the limit; the read is bounded
    on the same descriptor, so a file that grows after ``fstat`` is still refused.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
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


def _field_content(entry: Dict[str, Any], field: str, base_dir: str, size_limit: int) -> Optional[bytes]:
    data_key = f"{field}-data"
    if data_key in entry:
        value = entry[data_key]
        if not isinstance(value, str):
            raise SnapshotError(REASON_CREDENTIAL_UNREADABLE)
        try:
            content = base64.b64decode(value, validate=True)
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
        return capture_file(os.path.join(base_dir, path), size_limit)
    return None


def _token(user: Dict[str, Any], base_dir: str, size_limit: int) -> Optional[str]:
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
        raw = capture_file(os.path.join(base_dir, path), size_limit)
        try:
            token = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise SnapshotError(REASON_CREDENTIAL_UNREADABLE) from None
        token = token.rstrip(_TOKEN_TRAILING_WHITESPACE)
        if not token:
            raise SnapshotError(REASON_CREDENTIAL_EMPTY)
        return token
    return None


def build_snapshot(
    context_name: str,
    cluster: Dict[str, Any],
    cluster_base_dir: str,
    user: Optional[Dict[str, Any]],
    user_base_dir: str,
    user_reference: Optional[str],
    size_limit: int,
    source: str = "",
) -> SpokeSnapshot:
    """Capture one selected context, or raise ``SnapshotError`` (amendment §6).

    ``cluster``/``user`` are the entries' bodies; each base dir is the directory of the file
    that entry came from, so relative credential paths resolve against their own source.
    ``user`` is None when the context names no user, or names one that does not exist
    (``user_reference`` set): the latter is refused, never turned into an anonymous client.
    """
    if user is None and user_reference:
        raise SnapshotError(REASON_CREDENTIAL_UNSUPPORTED)
    user = user or {}
    if "exec" in user:
        raise SnapshotError(REASON_EXEC_CREDENTIAL_UNSUPPORTED)
    if "auth-provider" in user:
        raise SnapshotError(REASON_AUTH_PROVIDER_UNSUPPORTED)
    server = cluster.get("server")
    if not isinstance(server, str) or not server.lower().startswith("https://"):
        raise SnapshotError(REASON_TLS_VERIFICATION_UNPROVEN)
    if "insecure-skip-tls-verify" in cluster and cluster["insecure-skip-tls-verify"] is not False:
        # The loader applies `not value`: the string "false" or None would turn verification off.
        raise SnapshotError(REASON_TLS_VERIFICATION_UNPROVEN)
    if "tls-server-name" in cluster or "proxy-url" in cluster:
        raise SnapshotError(REASON_TLS_VERIFICATION_UNPROVEN)
    contents: Dict[str, Optional[bytes]] = {}
    for kind, field, name in _FILE_FIELDS:
        entry, base_dir = (cluster, cluster_base_dir) if kind == "cluster" else (user, user_base_dir)
        contents[name] = _field_content(entry, field, base_dir, size_limit)
    username = user.get("username")
    password = user.get("password")
    return SpokeSnapshot(
        context_name=context_name,
        server=server,
        ca=contents["ca"],
        cert=contents["cert"],
        key=contents["key"],
        token=_token(user, user_base_dir, size_limit),
        username=username if isinstance(username, str) else None,
        password=password if isinstance(password, str) else None,
        source=source,
    )


def new_private_dir() -> str:
    """A ``0700`` directory for one run's materialized credentials."""
    return tempfile.mkdtemp(prefix="acm-switchover-spoke-")


def remove_private_dir(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _write_private(directory: str, content: bytes) -> str:
    descriptor, path = tempfile.mkstemp(dir=directory)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
    finally:
        os.close(descriptor)
    return path


def materialize(snapshot: SpokeSnapshot, private_dir: str) -> Dict[str, Any]:
    """The client dict for one snapshot: our own files, an inline token, no current-context.

    Without ``current-context`` the loader can only select the context named explicitly,
    so an omitted ``context=`` argument fails instead of defaulting to anything.
    """
    if not os.path.isabs(private_dir):
        raise SnapshotError(REASON_CLIENT_CONSTRUCTION_FAILED)
    cluster: Dict[str, Any] = {"server": snapshot.server}
    user: Dict[str, Any] = {}
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
    bearer = (configuration.auth_settings().get("BearerToken") or {}).get("value")
    if snapshot.token is not None and bearer != f"Bearer {snapshot.token}":
        raise SnapshotError(REASON_CLIENT_CONSTRUCTION_FAILED)
    if not normalized_equal(configuration.host, api_url):
        raise SnapshotError(REASON_CLIENT_ENDPOINT_MISMATCH)
    return api_client
