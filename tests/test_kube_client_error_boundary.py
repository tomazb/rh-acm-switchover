"""#283: one boundary owns every KubeClient SDK operation.

Server-controlled text (a response body, header or status line) and transport-error text must
not leave `KubeClient` in an exception, its chain, a traceback, a log record, the state file or
the report, while callers keep their catch families, retry decisions and attempt counts.

Only the HTTP transport is faked here: the urllib3 pool manager, a connection's socket, or a
connection's `connect()`. The installed kubernetes client and urllib3 raise and log every
failure themselves. Each canary test first proves that the raw SDK call really carries the
canary in this lane, so it cannot pass vacuously.
"""

import ast
import contextlib
import errno
import inspect
import io
import json
import logging
import socket
import ssl
import sys
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any, FrozenSet, NamedTuple, Tuple
from unittest.mock import Mock, patch

import kubernetes
import pytest
import urllib3
from kubernetes.client import ApiClient, Configuration
from kubernetes.client.rest import ApiException

import lib.kube_client as kube_client_module
from lib.constants import STRICT_READ_REASON_MALFORMED_RESPONSE
from lib.exceptions import MalformedResponseError, PreconditionConflict, TargetDisappeared
from lib.kube_client import KubeClient, is_retryable_error
from lib.strict_read import StrictReadStatus
from tests.api_error_canary import real_kube_client_apis
from tests.test_kube_client import _real_api_client, _real_typed_kube_client, _RecordingPoolManager, _UnreadableBody

CANARY = "i283-boundary-canary-5c1e"
_MAJOR = int(kubernetes.__version__.split(".")[0])
_ONLY_37 = pytest.mark.skipif(
    _MAJOR < 37,
    reason=f"kubernetes {kubernetes.__version__} ignores a response charset; only 37.x decodes with it",
)
_ONLY_BEFORE_37 = pytest.mark.skipif(
    _MAJOR >= 37,
    reason=f"kubernetes {kubernetes.__version__} has no `kubernetes.client.rest` response-body debug log",
)
_SDK_LOGGERS = (
    "kubernetes.client.rest",
    "urllib3.connection",
    "urllib3.connectionpool",
    "urllib3.poolmanager",
    "urllib3.response",
    "urllib3.util.retry",
)
_NAMESPACE = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "ns1", "uid": "u1", "resourceVersion": "7"}}


@pytest.fixture
def mock_k8s_apis():
    """The generated API classes mocked under a real KubeClient (see `real_kube_client_apis`)."""
    with real_kube_client_apis() as apis:
        yield apis


@pytest.fixture
def kube_client(mock_k8s_apis):
    return KubeClient(context="i283", dry_run=False)


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    """Retry without sleeping: every decorated method and the named create share tenacity's sleep."""
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _seconds: None)


def _chain(exc):
    seen, pending, found = set(), [exc], []
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        found.append(current)
        pending.extend((current.__cause__, current.__context__))
    return found


def _rendered(exc):
    """Everything an operator, a log or a state file could render from `exc`."""
    texts = ["".join(traceback.format_exception(exc))]
    for link in _chain(exc):
        texts.extend((str(link), repr(link), repr(vars(link)) if hasattr(link, "__dict__") else ""))
        errors = getattr(link, "errors", None)
        if callable(errors):
            texts.append(repr(errors()))
    return texts


def _assert_no_canary(*texts):
    for text in texts:
        assert CANARY not in text, "server-controlled text left KubeClient"


def _assert_only_filtered_loggers(records):
    """Every record the raw SDK emitted came from a logger the boundary filters."""
    emitters = {record.name for record in records if not record.name.startswith("acm_switchover")}
    assert emitters <= set(_SDK_LOGGERS), f"unfiltered SDK loggers: {sorted(emitters - set(_SDK_LOGGERS))}"


def _raise(call, *args):
    with pytest.raises(Exception) as exc_info:
        call(*args)
    return exc_info.value


class _RaisingPool:
    """A pool manager whose every request raises what `failure()` builds."""

    def __init__(self, failure):
        self._failure = failure
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append(method)
        raise self._failure()


class _FakeSocket:
    def __init__(self, wire):
        self._wire = wire

    def makefile(self, *args, **kwargs):
        return io.BytesIO(self._wire)

    def sendall(self, *args):
        pass

    def settimeout(self, *args):
        pass

    def close(self):
        pass


_WIRES = {
    # A status line that is not HTTP: the server's bytes end up in http.client's BadStatusLine.
    "bad-status": CANARY.encode() + b"\r\n\r\n",
    # A chunk-size line that is not hexadecimal: the bytes end up in the chunk parser's ValueError.
    "bad-chunk": b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n"
    + CANARY.encode()
    + b"\r\n",
}


class _WirePool:
    """Serves raw bytes through urllib3's own connection pool and Python's HTTP parser."""

    def __init__(self, wire):
        self._wire = wire
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append(method)
        wire = self._wire

        class WireConnection(urllib3.connection.HTTPConnection):
            def connect(self):
                self.sock = _FakeSocket(wire)

        pool = urllib3.HTTPConnectionPool("fake.invalid", retries=False)
        pool.ConnectionCls = WireConnection
        return pool.urlopen(method, "/", preload_content=kwargs.get("preload_content", True), retries=False)


def _configured_client(connect_failure=None):
    """A KubeClient built by `__init__` over the SDK's own pool; only `connect()` is replaced, and
    only when `connect_failure` is given.

    The SDK builds its pool before KubeClient sets `configuration.retries = 0`, so this pool keeps
    urllib3's own retries: what it does on a connect failure is the production transport.
    """
    configuration = Configuration()
    configuration.host = "http://fake.invalid"
    api_client = ApiClient(configuration=configuration)
    with patch("lib.kube_client.config.new_client_from_config", return_value=api_client):
        client = KubeClient(context="i283")
    pool = api_client.rest_client.pool_manager.connection_from_url(configuration.host)
    connects = []

    class FailingConnection(urllib3.connection.HTTPConnection):
        def connect(self):
            connects.append(1)
            raise connect_failure()

    if connect_failure is not None:
        pool.ConnectionCls = FailingConnection
    return client, connects


# ---------------------------------------------------------------------------
# Failures the installed client and urllib3 raise themselves
# ---------------------------------------------------------------------------

_SERVER_TEXT_FAILURES = [
    pytest.param(
        lambda: _RecordingPoolManager(payload=_NAMESPACE, content_type="application/json; charset=" + CANARY),
        lambda c: c.core_v1.read_namespace("ns1"),
        lambda c: c.get_namespace("ns1"),
        LookupError,
        1,
        id="typed-read-unknown-charset",
        marks=_ONLY_37,
    ),
    pytest.param(
        lambda: _RecordingPoolManager(payload=_NAMESPACE, content_type="application/json; charset=" + CANARY),
        lambda c: c.custom_api.get_namespaced_custom_object("g.io", "v1", "ns1", "things", "t1"),
        lambda c: c.get_custom_resource("g.io", "v1", "things", "t1", namespace="ns1"),
        MalformedResponseError,
        1,
        id="custom-read-unknown-charset",
        marks=_ONLY_37,
    ),
    pytest.param(
        lambda: _WirePool(_WIRES["bad-status"]),
        lambda c: c.core_v1.read_namespace("ns1"),
        lambda c: c.get_namespace("ns1"),
        urllib3.exceptions.HTTPError,
        5,
        id="malformed-status-line",
    ),
    pytest.param(
        lambda: _WirePool(_WIRES["bad-chunk"]),
        lambda c: c.core_v1.read_namespace("ns1"),
        lambda c: c.get_namespace("ns1"),
        urllib3.exceptions.HTTPError,
        5,
        id="malformed-chunk-size",
    ),
    pytest.param(
        lambda: _RaisingPool(lambda: urllib3.exceptions.SSLError("certificate verify failed: " + CANARY)),
        lambda c: c.core_v1.read_namespace("ns1"),
        lambda c: c.get_namespace("ns1"),
        ApiException,
        1,
        id="tls-status-0",
    ),
    pytest.param(
        lambda: _RaisingPool(lambda: KeyError(CANARY)),
        lambda c: c.core_v1.read_namespace("ns1"),
        lambda c: c.get_namespace("ns1"),
        KeyError,
        1,
        id="unexpected-sdk-error",
    ),
    pytest.param(
        lambda: _WirePool(_WIRES["bad-status"]),
        lambda c: c.custom_api.delete_namespaced_custom_object("g.io", "v1", "ns1", "things", "t1"),
        lambda c: c.delete_custom_resource_preconditioned("g.io", "v1", "things", "t1", uid="u1", namespace="ns1"),
        urllib3.exceptions.HTTPError,
        1,
        id="undecorated-preconditioned-delete-malformed-status-line",
    ),
]


@pytest.mark.unit
class TestServerTextNeverLeavesKubeClient:
    """The exception, its chain, its traceback and the log carry no server text; the caller's
    catch family, retry decision and attempt count are those of the raw failure."""

    @pytest.mark.parametrize("make_pool,raw_call,kube_call,family,attempts", _SERVER_TEXT_FAILURES)
    def test_no_canary_and_unchanged_family_and_attempts(
        self, caplog, make_pool, raw_call, kube_call, family, attempts
    ):
        raw = _raise(raw_call, _real_typed_kube_client(make_pool()))
        assert any(CANARY in text for text in _rendered(raw)), "precondition: the raw SDK failure carries the canary"

        pool = make_pool()
        client = _real_typed_kube_client(pool)
        with caplog.at_level(logging.DEBUG):
            raised = _raise(kube_call, client)

        assert isinstance(raised, family)
        assert len(pool.calls) == attempts
        if family is not MalformedResponseError:
            assert is_retryable_error(raised) == is_retryable_error(raw)
        _assert_no_canary(caplog.text, *_rendered(raised))

    def test_a_tls_failure_keeps_status_0_and_a_tls_diagnosis(self, caplog):
        """Grok R2: a TLS failure must not be blanked to `(0) Reason: None`."""
        failure = lambda: urllib3.exceptions.SSLError("certificate verify failed: " + CANARY)  # noqa: E731
        raw = _raise(lambda c: c.core_v1.read_namespace("ns1"), _real_typed_kube_client(_RaisingPool(failure)))
        assert raw.status == 0 and CANARY in str(raw)

        with caplog.at_level(logging.DEBUG):
            raised = _raise(lambda c: c.get_namespace("ns1"), _real_typed_kube_client(_RaisingPool(failure)))

        assert type(raised) is ApiException and raised.status == 0
        assert isinstance(raised.reason, str) and "TLS" in raised.reason
        assert "TLS" in str(raised) and "Reason: None" not in str(raised)
        assert is_retryable_error(raised) is False
        _assert_no_canary(caplog.text, *_rendered(raised))

    def test_tls_through_the_sdk_built_pool_keeps_its_transport_attempts(self, caplog):
        """The SDK's own pool retries a connect three times (four connects) and raises
        MaxRetryError(SSLError); KubeClient retries that five times. Both stay, diagnosed as TLS."""
        failure = lambda: ssl.SSLError("synthetic certificate failure " + CANARY)  # noqa: E731
        client, connects = _configured_client(failure)
        with caplog.at_level(logging.DEBUG):
            raw = _raise(lambda c: c.core_v1.read_namespace("ns1"), client)
        assert isinstance(raw, urllib3.exceptions.MaxRetryError) and len(connects) == 4
        assert CANARY in caplog.text, "precondition: urllib3 logs the connect failure while retrying"

        client, connects = _configured_client(failure)
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            raised = _raise(lambda c: c.get_namespace("ns1"), client)

        assert isinstance(raised, urllib3.exceptions.HTTPError)
        assert is_retryable_error(raised) is True
        assert len(connects) == 20, "5 KubeClient attempts x 4 connects, as before"
        assert "TLS" in str(raised)
        _assert_no_canary(caplog.text, *_rendered(raised))

    @pytest.mark.parametrize(
        "socket_failure,raw_reason,diagnosis",
        [
            (lambda: socket.timeout("timed out " + CANARY), urllib3.exceptions.ConnectTimeoutError, "timed out"),
            # NewConnectionError subclasses ConnectTimeoutError: it must still read as a connection error.
            (
                lambda: ConnectionRefusedError(errno.ECONNREFUSED, CANARY),
                urllib3.exceptions.NewConnectionError,
                "connection error",
            ),
        ],
        ids=["connect-timeout", "connection-refused"],
    )
    def test_timeout_and_refusal_through_the_sdk_built_pool(self, caplog, socket_failure, raw_reason, diagnosis):
        """Faked at the socket factory urllib3's own connection calls, on the SDK-built pool."""
        connects = []

        def create_connection(*_args, **_kwargs):
            connects.append(1)
            raise socket_failure()

        with patch("urllib3.util.connection.create_connection", side_effect=create_connection):
            raw = _raise(lambda c: c.core_v1.read_namespace("ns1"), _configured_client()[0])
            assert isinstance(raw, urllib3.exceptions.MaxRetryError) and type(raw.reason) is raw_reason
            assert len(connects) == 4
            connects.clear()
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                raised = _raise(lambda c: c.get_namespace("ns1"), _configured_client()[0])

        assert isinstance(raised, urllib3.exceptions.HTTPError) and is_retryable_error(raised)
        assert len(connects) == 20, "5 KubeClient attempts x 4 connects, as before"
        assert str(raised) == "Kubernetes API request failed: " + diagnosis
        _assert_no_canary(caplog.text, *_rendered(raised))

    def test_a_body_read_timeout_is_diagnosed_as_a_timeout(self, caplog):
        def make_pool():
            return _RecordingPoolManager(body=_UnreadableBody(socket.timeout("read " + CANARY)))

        raw = _raise(lambda c: c.core_v1.read_namespace("ns1"), _real_typed_kube_client(make_pool()))
        assert isinstance(raw, urllib3.exceptions.ReadTimeoutError)

        pool = make_pool()
        with caplog.at_level(logging.DEBUG):
            raised = _raise(lambda c: c.get_namespace("ns1"), _real_typed_kube_client(pool))

        assert isinstance(raised, urllib3.exceptions.TimeoutError) and is_retryable_error(raised)
        assert len(pool.calls) == 5
        assert str(raised) == "Kubernetes API request failed: timed out"
        _assert_no_canary(caplog.text, *_rendered(raised))

    @_ONLY_BEFORE_37
    def test_a_successful_response_body_is_not_logged_by_the_sdk(self, caplog):
        """kubernetes 29.x logs every preloaded response body at DEBUG, successes included."""
        secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "s1"}, "data": {"k": CANARY}}
        with caplog.at_level(logging.DEBUG):
            _real_typed_kube_client(_RecordingPoolManager(payload=secret)).core_v1.read_namespaced_secret("s1", "ns1")
        assert CANARY in caplog.text, "precondition: the SDK logs the body"
        _assert_only_filtered_loggers(caplog.records)

        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            result = _real_typed_kube_client(_RecordingPoolManager(payload=secret)).get_secret("ns1", "s1")

        assert result["data"] == {"k": CANARY}, "the response itself is returned unchanged"
        _assert_no_canary(caplog.text)


# Routes that swallow the exception into an outcome: only their logs can leak.
_OUTCOME_ROUTES = [
    pytest.param(lambda c: c.get_namespace_strict("ns1"), id="get_namespace_strict"),
    pytest.param(lambda c: c.list_pods_strict("ns1"), id="list_pods_strict"),
    pytest.param(lambda c: c.get_deployment_strict("d1", "ns1"), id="get_deployment_strict"),
    pytest.param(lambda c: c.get_replicaset_strict("r1", "ns1"), id="get_replicaset_strict"),
    pytest.param(lambda c: c.api_resource_served("g.io", "v1", "things"), id="discovery"),
    pytest.param(lambda c: c.list_custom_resources_strict("g.io", "v1", "things", "ns1"), id="strict-list"),
    pytest.param(lambda c: c.get_custom_resource_strict("g.io", "v1", "things", "t1", "ns1"), id="strict-get"),
    pytest.param(
        lambda c: c.json_patch_custom_resource_guarded(
            "r1",
            namespace="ns1",
            uid="u1",
            resource_version="7",
            expected_managed_clusters_backup_name="b1",
            replacement_managed_clusters_backup_name="latest",
        ),
        id="guarded-patch",
    ),
    pytest.param(
        lambda c: c.delete_restore_guarded("r1", namespace="ns1", uid="u1", resource_version="7"), id="guarded-delete"
    ),
    pytest.param(
        lambda c: c.create_restore_guarded({"metadata": {"name": "r1"}}, namespace="ns1"), id="guarded-create"
    ),
]


@pytest.mark.unit
class TestSdkLogsAreDroppedDuringOwnedCalls:
    @pytest.mark.parametrize("route", _OUTCOME_ROUTES)
    def test_outcome_routes_log_no_transport_text(self, caplog, route):
        failure = lambda: ssl.SSLError("synthetic certificate failure " + CANARY)  # noqa: E731
        client, _ = _configured_client(failure)
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            _raise(lambda c: c.core_v1.read_namespace("ns1"), client)
        assert CANARY in caplog.text, "precondition: urllib3 logs the connect failure"
        _assert_only_filtered_loggers(caplog.records)

        client, connects = _configured_client(failure)
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            outcome = route(client)

        assert connects, "the route reached the transport"
        if hasattr(outcome, "status"):
            assert outcome.status is StrictReadStatus.ERROR
        else:
            assert outcome["accepted"] is False and outcome["reason"] == "unverifiable"
        _assert_no_canary(caplog.text)

    @pytest.mark.parametrize(
        "raw_call",
        [
            pytest.param(
                lambda: _configured_client(lambda: ssl.SSLError(CANARY))[0].core_v1.read_namespace("ns1"),
                id="sdk-pool-tls",
            ),
            pytest.param(
                lambda: _configured_client(lambda: TimeoutError(CANARY))[0].core_v1.read_namespace("ns1"),
                id="sdk-pool-timeout",
            ),
            pytest.param(
                lambda: _real_typed_kube_client(_WirePool(_WIRES["bad-status"])).core_v1.read_namespace("ns1"),
                id="malformed-status-line",
            ),
            pytest.param(
                lambda: _real_typed_kube_client(_WirePool(_WIRES["bad-chunk"])).core_v1.read_namespace("ns1"),
                id="malformed-chunk-size",
            ),
            pytest.param(
                lambda: _real_typed_kube_client(_RecordingPoolManager(payload=_NAMESPACE)).core_v1.read_namespace(
                    "ns1"
                ),
                id="success",
            ),
            pytest.param(
                lambda: _real_typed_kube_client(
                    _RecordingPoolManager(status=403, payload={"m": CANARY})
                ).core_v1.read_namespace("ns1"),
                id="http-403",
            ),
        ],
    )
    def test_the_raw_sdk_logs_only_through_the_filtered_loggers(self, caplog, raw_call):
        """The six filtered loggers are the SDK's emitting loggers on this lane: whatever the raw
        client logs for these requests comes from one of them."""
        for name in (*_SDK_LOGGERS, "urllib3", "kubernetes"):
            caplog.set_level(logging.DEBUG, logger=name)
        with caplog.at_level(logging.DEBUG):
            try:
                raw_call()
            except Exception:  # noqa: BLE001 - only the log records are under test
                pass
        _assert_only_filtered_loggers(caplog.records)

    def test_each_sdk_logger_carries_one_filter(self):
        boundary_filters = [
            [f for f in logging.getLogger(name).filters if isinstance(f, type(kube_client_module._SDK_LOG_FILTER))]
            for name in _SDK_LOGGERS
        ]
        assert all(filters == [kube_client_module._SDK_LOG_FILTER] for filters in boundary_filters)

    def test_records_drop_inside_an_owned_call_and_pass_outside(self, caplog):
        """Every level is dropped during the call; nested calls stay suppressed; another thread,
        the project logger and anything after the call (success or failure) still log."""
        call_sdk = kube_client_module._call_sdk
        seen_in_thread = []

        def emit(tag):
            for name in _SDK_LOGGERS:
                sdk_logger = logging.getLogger(name)
                for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL):
                    sdk_logger.log(level, "%s %s", tag, name)

        def other_thread():
            logging.getLogger("urllib3.connectionpool").warning("unrelated-thread")
            seen_in_thread.append(True)

        def concurrent_owned_call():
            call_sdk(lambda: emit("concurrent-owned-" + CANARY))
            logging.getLogger("urllib3.connectionpool").warning("concurrent-after-its-call")

        def inner():
            emit("nested-" + CANARY)

        def outer():
            emit("owned-" + CANARY)
            call_sdk(inner)
            emit("after-nested-" + CANARY)
            thread = threading.Thread(target=other_thread)
            thread.start()
            thread.join()
            # Another thread enters and leaves its own owned call while this one is still inside.
            thread = threading.Thread(target=concurrent_owned_call)
            thread.start()
            thread.join()
            emit("after-concurrent-" + CANARY)
            logging.getLogger("acm_switchover").warning("project-logger")
            return "ok"

        def failing():
            emit("failing-" + CANARY)
            raise urllib3.exceptions.ProtocolError(CANARY)

        for name in _SDK_LOGGERS:
            caplog.set_level(logging.DEBUG, logger=name)
        with caplog.at_level(logging.DEBUG):
            emit("before")
            assert call_sdk(outer) == "ok"
            with pytest.raises(urllib3.exceptions.HTTPError):
                call_sdk(failing)
            emit("after")

        assert seen_in_thread == [True]
        messages = [record.getMessage() for record in caplog.records]
        assert {"unrelated-thread", "concurrent-after-its-call", "project-logger"} <= set(messages)
        assert sum(m.startswith("before ") for m in messages) == len(_SDK_LOGGERS) * 5
        assert sum(m.startswith("after ") for m in messages) == len(_SDK_LOGGERS) * 5
        _assert_no_canary(caplog.text)


# ---------------------------------------------------------------------------
# Caller compatibility: catch families, retries, statuses, reasons, causes
# ---------------------------------------------------------------------------

_CALLER_ROOTS = ("lib", "modules", "acm_switchover.py", "check_rbac.py")
_REPO = Path(kube_client_module.__file__).resolve().parent.parent
# Except clauses that never wrap a KubeClient call: each guards a local file decode.
_LOCAL_DECODE_CLAUSES = {
    ("UnicodeDecodeError",): {("lib/kubeconfig_merge.py", "_load_document"), ("lib/kubeconfig_snapshot.py", "_token")},
    ("UnicodeDecodeError", "json.JSONDecodeError"): {("lib/utils.py", "_load_state")},
}


def _caller_except_clauses():
    """(clause names, file, enclosing function) for every except clause in the callers."""
    found = []
    for root in _CALLER_ROOTS:
        path = _REPO / root
        for file in [path] if path.is_file() else sorted(path.rglob("*.py")):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            enclosing = {}
            for function in ast.walk(tree):
                if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for node in ast.walk(function):
                        enclosing[node] = function.name
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and node.type is not None:
                    items = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
                    names = tuple(sorted(ast.unparse(item) for item in items))
                    found.append((names, str(file.relative_to(_REPO)), enclosing.get(node, "<module>")))
    return found


def _resolve(name):
    import builtins
    import json as json_module

    import lib.exceptions as project_exceptions
    from lib.validation import ValidationError

    spaces = [
        vars(builtins),
        vars(urllib3.exceptions),
        vars(project_exceptions),
        {
            "ApiException": ApiException,
            "ValidationError": ValidationError,
            "Urllib3TimeoutError": urllib3.exceptions.TimeoutError,
            "json.JSONDecodeError": json_module.JSONDecodeError,
        },
    ]
    for space in spaces:
        if isinstance(space.get(name), type):
            return space[name]
    return None


def _catching_clauses(exc):
    caught = set()
    for names, _file, _function in _caller_except_clauses():
        if names in _LOCAL_DECODE_CLAUSES:
            continue
        classes = tuple(cls for cls in map(_resolve, names) if cls is not None and issubclass(cls, BaseException))
        if classes and isinstance(exc, classes):
            caught.add(names)
    return caught


_COMPATIBILITY_FAILURES = [
    pytest.param(lambda: _RecordingPoolManager(status=403, payload={"message": CANARY}), id="http-403"),
    pytest.param(lambda: _RecordingPoolManager(status=409, payload={"message": CANARY}), id="http-409"),
    pytest.param(lambda: _RecordingPoolManager(status=503, payload={"message": CANARY}), id="http-503"),
    pytest.param(lambda: _RecordingPoolManager(status=599, payload={"message": CANARY}), id="http-599"),
    pytest.param(lambda: _RecordingPoolManager(body=b"{not json " + CANARY.encode()), id="200-invalid-json"),
    pytest.param(lambda: _RecordingPoolManager(body=CANARY.encode() + b"\xff"), id="200-invalid-utf8"),
    pytest.param(
        lambda: _RecordingPoolManager(payload=_NAMESPACE, content_type="application/x-" + CANARY),
        id="200-unsupported-content-type",
    ),
    pytest.param(
        lambda: _RecordingPoolManager(payload={**_NAMESPACE, "metadata": {"name": "ns1", "creationTimestamp": CANARY}}),
        id="200-unparseable-timestamp",
    ),
    pytest.param(lambda: _WirePool(_WIRES["bad-status"]), id="malformed-status-line"),
    pytest.param(lambda: _RaisingPool(lambda: urllib3.exceptions.SSLError(CANARY)), id="tls"),
    pytest.param(
        lambda: _RaisingPool(lambda: urllib3.exceptions.MaxRetryError(None, "/x", urllib3.exceptions.SSLError(CANARY))),
        id="max-retries-tls",
    ),
    pytest.param(
        lambda: _RaisingPool(lambda: urllib3.exceptions.ReadTimeoutError(None, "/x", CANARY)), id="read-timeout"
    ),
    pytest.param(
        lambda: _RaisingPool(lambda: urllib3.exceptions.NewConnectionError(None, CANARY)), id="new-connection"
    ),
    pytest.param(lambda: _RaisingPool(lambda: ConnectionResetError(errno.ECONNRESET, CANARY)), id="connection-reset"),
    pytest.param(lambda: _RaisingPool(lambda: OSError(errno.EACCES, CANARY)), id="oserror-eacces"),
    pytest.param(lambda: _RaisingPool(lambda: TimeoutError(CANARY)), id="socket-timeout"),
    pytest.param(lambda: _RaisingPool(lambda: ValueError(CANARY)), id="value-error"),
    pytest.param(lambda: _RaisingPool(lambda: RuntimeError(CANARY)), id="runtime-error"),
    pytest.param(lambda: _RaisingPool(lambda: TypeError(CANARY)), id="type-error"),
]


@pytest.mark.unit
class TestCallersSeeTheSameFailure:
    @pytest.mark.parametrize("make_pool", _COMPATIBILITY_FAILURES)
    def test_catch_clauses_retry_decision_and_attempts_match_the_raw_failure(self, make_pool):
        pool = make_pool()
        try:
            _real_typed_kube_client(pool).core_v1.read_namespace("ns1")
        except Exception as raw_exc:  # noqa: BLE001 - the raw SDK outcome under comparison
            raw = raw_exc
        else:
            pytest.skip(f"kubernetes {kubernetes.__version__} accepts this response")

        pool = make_pool()
        raised = _raise(lambda c: c.get_namespace("ns1"), _real_typed_kube_client(pool))

        assert _catching_clauses(raised) == _catching_clauses(raw)
        assert is_retryable_error(raised) == is_retryable_error(raw)
        assert len(pool.calls) == (5 if is_retryable_error(raw) else 1)
        assert getattr(raised, "status", None) == getattr(raw, "status", None)
        if isinstance(raised, ApiException):
            assert type(raised) is type(raw), "the SDK's own ApiException class is kept"
            assert isinstance(raised.reason, str) and raised.reason
        _assert_no_canary(*_rendered(raised))

    def test_local_decode_clauses_still_wrap_no_kubeclient_call(self):
        """The two clauses this comparison skips must stay where they are: local file decodes."""
        sites = {}
        for names, file, function in _caller_except_clauses():
            if names in _LOCAL_DECODE_CLAUSES:
                sites.setdefault(names, set()).add((file, function))
        assert sites == _LOCAL_DECODE_CLAUSES

    @pytest.mark.parametrize("status", [0, 403, 499, 999])
    def test_reason_is_a_fixed_phrase_never_none_or_server_text(self, status):
        response = _RecordingPoolManager(status=status, payload={"message": CANARY})
        raised = _raise(
            lambda c: c.get_custom_resource("g.io", "v1", "things", "t1", namespace="ns1"),
            _real_typed_kube_client(response),
        )
        assert isinstance(raised, ApiException) and raised.status == status
        assert isinstance(raised.reason, str) and raised.reason
        _assert_no_canary(*_rendered(raised))

    @pytest.mark.parametrize("status", [None, "503", True])
    def test_an_invalid_status_is_not_retried(self, kube_client, mock_k8s_apis, status):
        failure = ApiException(reason=CANARY)
        failure.status = status
        mock_k8s_apis["core_api"].read_namespace.side_effect = failure

        raised = _raise(kube_client.get_namespace, "ns1")

        assert isinstance(raised, ApiException) and raised.status == 0
        assert isinstance(raised.reason, str) and raised.reason
        assert mock_k8s_apis["core_api"].read_namespace.call_count == 1
        _assert_no_canary(*_rendered(raised))

    @pytest.mark.parametrize(
        "failure,attempts",
        [
            (lambda: OSError(errno.ECONNRESET, CANARY), 5),
            (lambda: OSError(errno.ETIMEDOUT, CANARY), 5),
            # Retried by errno alone: BlockingIOError is not a ConnectionError or TimeoutError.
            (lambda: OSError(errno.EAGAIN, CANARY), 5),
            (lambda: OSError(errno.EACCES, CANARY), 1),
            (lambda: OSError(CANARY), 1),
            # Its errno would make `OSError(errno, ...)` a retryable BrokenPipeError or a
            # FileNotFoundError: the replacement keeps the raw class family instead.
            (lambda: ssl.SSLError(errno.EPIPE, CANARY), 1),
            (lambda: ssl.SSLError(errno.ENOENT, CANARY), 1),
        ],
        ids=["econnreset", "etimedout", "eagain", "eacces", "no-errno", "ssl-epipe", "ssl-enoent"],
    )
    def test_network_and_other_os_errors_keep_their_retry_decision(self, kube_client, mock_k8s_apis, failure, attempts):
        def boom(*_args, **_kwargs):
            raise failure()

        mock_k8s_apis["core_api"].read_namespace.side_effect = boom

        raised = _raise(kube_client.get_namespace, "ns1")

        assert isinstance(raised, OSError) and raised.errno == failure().errno
        assert _catching_clauses(raised) == _catching_clauses(failure())
        assert is_retryable_error(raised) == is_retryable_error(failure())
        assert mock_k8s_apis["core_api"].read_namespace.call_count == attempts
        _assert_no_canary(*_rendered(raised))

    def test_a_process_interrupt_is_not_projected(self, kube_client, mock_k8s_apis):
        interrupt = KeyboardInterrupt(CANARY)
        mock_k8s_apis["core_api"].read_namespace.side_effect = interrupt
        with pytest.raises(KeyboardInterrupt) as exc_info:
            kube_client.get_namespace("ns1")
        assert exc_info.value is interrupt

    @pytest.mark.parametrize(
        "status,expected", [(409, PreconditionConflict), (412, PreconditionConflict), (404, TargetDisappeared)]
    )
    def test_guarded_delete_keeps_its_domain_error_and_safe_cause(self, status, expected):
        pool = _RecordingPoolManager(status=status, payload={"message": CANARY})
        raised = _raise(
            lambda c: c.delete_custom_resource_preconditioned("g.io", "v1", "things", "t1", uid="u1", namespace="ns1"),
            _real_typed_kube_client(pool),
        )
        assert type(raised) is expected and len(pool.calls) == 1
        assert isinstance(raised.__cause__, ApiException) and raised.__cause__.status == status
        _assert_no_canary(*_rendered(raised))

    @pytest.mark.parametrize(
        "status,reason",
        [
            (409, "precondition_failed"),
            (412, "precondition_failed"),
            (422, "precondition_failed"),
            (404, "not_found"),
            (403, "unverifiable"),
        ],
    )
    def test_guarded_patch_maps_statuses_as_before(self, caplog, status, reason):
        pool = _RecordingPoolManager(status=status, payload={"message": CANARY})
        with caplog.at_level(logging.DEBUG):
            result = _real_typed_kube_client(pool).json_patch_custom_resource_guarded(
                "r1",
                namespace="ns1",
                uid="u1",
                resource_version="7",
                expected_managed_clusters_backup_name="b1",
                replacement_managed_clusters_backup_name="latest",
            )
        assert result["reason"] == reason and result["accepted"] is False and len(pool.calls) == 1
        _assert_no_canary(caplog.text)

    def test_named_create_still_reconciles_a_409_by_reread(self):
        class _CreateThenRead(_RecordingPoolManager):
            def request(self, method, url, **kwargs):
                if method == "POST":
                    self.calls.append(method)
                    return _RecordingPoolManager(status=409, payload={"message": CANARY}).request(method, url)
                return super().request(method, url, **kwargs)

        pool = _CreateThenRead(payload={"metadata": {"name": "t1"}, "spec": {"x": 1}})
        result = _real_typed_kube_client(pool).create_custom_resource(
            "g.io", "v1", "things", {"metadata": {"name": "t1"}, "spec": {"x": 1}}, namespace="ns1"
        )
        assert result["spec"] == {"x": 1}
        assert [c if isinstance(c, str) else c["method"] for c in pool.calls] == ["POST", "GET"]

    def test_strict_list_restarts_on_an_expired_continuation(self):
        discovery = {
            "kind": "APIResourceList",
            "groupVersion": "g.io/v1",
            "resources": [{"name": "things", "kind": "T"}],
        }
        pages = [
            _RecordingPoolManager(payload=discovery),
            _RecordingPoolManager(payload={"items": [], "metadata": {"resourceVersion": "1", "continue": "c1"}}),
            _RecordingPoolManager(status=410, payload={"message": CANARY}),
            _RecordingPoolManager(
                payload={"items": [{"metadata": {"name": "a"}}], "metadata": {"resourceVersion": "2"}}
            ),
        ]

        class _Sequence:
            calls = 0

            def request(self, method, url, **kwargs):
                _Sequence.calls += 1
                return pages.pop(0).request(method, url, **kwargs)

        outcome = _real_typed_kube_client(_Sequence()).list_custom_resources_strict("g.io", "v1", "things", "ns1")
        assert outcome.status is StrictReadStatus.ITEMS and outcome.resource_version == "2"
        assert _Sequence.calls == 4


@pytest.mark.unit
class TestNoInheritedContextLeavesKubeClient:
    """A caller handling its own exception must not find it chained to KubeClient's failure,
    neither from the boundary's raise nor from tenacity's final re-raise."""

    @pytest.mark.parametrize(
        "call,status,attempts",
        [
            (lambda c: c.get_namespace("ns1"), 503, 5),
            (lambda c: c.get_namespace("ns1"), 403, 1),
            (lambda c: c.create_custom_resource("g.io", "v1", "things", {"metadata": {"name": "t1"}}, "ns1"), 503, 5),
            (
                lambda c: c.delete_custom_resource_preconditioned(
                    "g.io", "v1", "things", "t1", uid="u1", namespace="ns1"
                ),
                500,
                1,
            ),
            (lambda c: c.wait_for_pods_ready("ns1", "app=x", timeout=5), 403, 1),
        ],
        ids=["retried-exhausted", "non-retryable", "named-create-retry", "undecorated-delete", "pods-wait"],
    )
    def test_the_published_exception_has_no_inherited_context(self, call, status, attempts):
        pool = _RecordingPoolManager(status=status, payload={"message": "x"})
        client = _real_typed_kube_client(pool)
        try:
            raise RuntimeError("caller's own " + CANARY)
        except RuntimeError:
            raised = _raise(call, client)

        assert isinstance(raised, ApiException) and raised.status == status
        assert len(pool.calls) == attempts
        assert raised.__context__ is None and raised.__cause__ is None
        _assert_no_canary(*_rendered(raised))


# ---------------------------------------------------------------------------
# Retry logging
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_retry_log_names_the_status_or_fixed_category_only(caplog):
    with caplog.at_level(logging.DEBUG, logger="acm_switchover"):
        _raise(lambda c: c.get_namespace("ns1"), _real_typed_kube_client(_WirePool(_WIRES["bad-status"])))
        _raise(
            lambda c: c.get_namespace("ns1"),
            _real_typed_kube_client(_RecordingPoolManager(status=503, payload={"message": CANARY})),
        )
    retries = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Retrying")]
    assert len(retries) == 8
    assert all("HTTP 503" in m for m in retries[4:])
    _assert_no_canary(caplog.text)


# ---------------------------------------------------------------------------
# State file, report and operator log, default and verbose
# ---------------------------------------------------------------------------

_CLI_FAILURES = [
    pytest.param(lambda: _RaisingPool(lambda: urllib3.exceptions.SSLError(CANARY)), "TLS", id="tls"),
    pytest.param(lambda: _WirePool(_WIRES["bad-status"]), "connection", id="malformed-status-line"),
    pytest.param(lambda: _WirePool(_WIRES["bad-chunk"]), "connection", id="malformed-chunk-size"),
    pytest.param(
        lambda: _RecordingPoolManager(payload=_NAMESPACE, content_type="application/json; charset=" + CANARY),
        "Malformed Kubernetes API response: undecodable body (LookupError)",
        id="unknown-charset",
        marks=_ONLY_37,
    ),
]


@pytest.mark.unit
class TestOperatorOutputs:
    @pytest.mark.parametrize("verbose", [False, True], ids=["default", "verbose"])
    @pytest.mark.parametrize("make_pool,diagnosis", _CLI_FAILURES)
    def test_state_report_and_log_carry_a_fixed_diagnosis(self, tmp_path, caplog, make_pool, diagnosis, verbose):
        from lib.cli_outcomes import CliOperationHooks, run_operation_mode, write_python_report
        from lib.utils import StateManager

        client = _real_typed_kube_client(make_pool())
        state_path = tmp_path / "state.json"
        report_dir = tmp_path / "reports"
        state = StateManager(str(state_path))
        args = SimpleNamespace(
            argocd_resume_only=False,
            verbose=verbose,
            state_file=str(state_path),
            report_dir=str(report_dir),
            validate_only=False,
            decommission=False,
            restore_only=False,
        )
        hooks = CliOperationHooks(
            bind_runtime_hub_identities=Mock(),
            run_argocd_resume_only=Mock(),
            execute_operation=lambda *_args: client.get_namespace("ns1"),
            write_python_report=write_python_report,
            gitops_reporter_factory=Mock,
        )
        with caplog.at_level(logging.DEBUG):
            exit_code = run_operation_mode(
                args,
                state,
                client,
                None,
                logging.getLogger("acm_switchover"),
                should_bind_state=False,
                should_record_state_errors=True,
                hooks=hooks,
                exit_success=0,
                exit_failure=1,
                exit_interrupt=130,
            )

        assert exit_code == 1
        state_text = state_path.read_text(encoding="utf-8")
        report_text = next(report_dir.iterdir()).read_text(encoding="utf-8")
        errors = [entry["error"] for entry in json.loads(state_text)["errors"]]
        assert len(errors) == 1 and diagnosis in errors[0] and "Reason: None" not in errors[0]
        assert ("Traceback" in caplog.text) is verbose
        _assert_no_canary(state_text, report_text, caplog.text)


# ---------------------------------------------------------------------------
# An operator can still tell failures apart (grok-r3)
# ---------------------------------------------------------------------------

_REQUEST_FAILED = "Kubernetes API request failed"


def _undecodable(cause):
    return f"Malformed Kubernetes API response: undecodable body ({cause})"


def _off_model(cause):
    return f"Malformed Kubernetes API response: body does not match the expected model ({cause})"


# A 2xx response the typed (core/apps) client could not decode or deserialize into its model.
# Each case names, per lane, the class the raw SDK raises for it: the precondition that the lane
# really produces that failure.
_MALFORMED_TYPED_READS = [
    pytest.param(
        b'{"not": ' + CANARY.encode(),
        "application/json",
        lambda c: c.core_v1.read_namespace("ns1"),
        lambda c: c.get_namespace("ns1"),
        {37: "JSONDecodeError"},
        ValueError,
        _undecodable("JSONDecodeError"),
        id="namespace-invalid-json",
        marks=pytest.mark.skipif(
            _MAJOR < 37,
            reason=f"kubernetes {kubernetes.__version__} reads an undecodable namespace body as an empty model",
        ),
    ),
    pytest.param(
        b"\xff\xfe" + CANARY.encode(),
        "application/json",
        lambda c: c.core_v1.read_namespace("ns1"),
        lambda c: c.get_namespace("ns1"),
        {29: "UnicodeDecodeError", 37: "UnicodeDecodeError"},
        UnicodeError,
        _undecodable("UnicodeDecodeError"),
        id="namespace-invalid-utf8",
    ),
    pytest.param(
        json.dumps(_NAMESPACE).encode(),
        "application/json; charset=" + CANARY,
        lambda c: c.core_v1.read_namespace("ns1"),
        lambda c: c.get_namespace("ns1"),
        {37: "LookupError"},
        LookupError,
        _undecodable("LookupError"),
        id="namespace-unknown-charset",
        marks=_ONLY_37,
    ),
    pytest.param(
        json.dumps({"kind": "PodList", "metadata": {"name": CANARY}}).encode(),
        "application/json",
        lambda c: c.core_v1.list_namespaced_pod("ns1"),
        lambda c: c.get_pods("ns1"),
        {29: "ValueError", 37: "ValidationError"},
        ValueError,
        {29: _off_model("ValueError"), 37: _off_model("ValidationError")},
        id="pod-list-without-items",
    ),
    pytest.param(
        json.dumps({"spec": {"selector": CANARY}}).encode(),
        "application/json",
        lambda c: c.apps_v1.read_namespaced_deployment("d1", "ns1"),
        lambda c: c.get_deployment("d1", "ns1"),
        {29: "ValueError", 37: "ValidationError"},
        ValueError,
        {29: _off_model("ValueError"), 37: _off_model("ValidationError")},
        id="deployment-off-model",
    ),
    pytest.param(
        json.dumps({"metadata": {"name": "ns1", "creationTimestamp": CANARY}}).encode(),
        "application/json",
        lambda c: c.core_v1.read_namespace("ns1"),
        lambda c: c.get_namespace("ns1"),
        # 29.x raises its own status-0 ApiException for a timestamp it cannot parse.
        {29: "ApiException", 37: "ValidationError"},
        {29: ApiException, 37: ValueError},
        {29: _off_model("ApiException"), 37: _off_model("ValidationError")},
        id="namespace-bad-timestamp",
    ),
]


@pytest.mark.unit
class TestMalformedTypedReadsAreNamed:
    """grok-r3 finding 1: a 2xx the typed client could not decode or deserialize reads as a
    malformed response, not as the generic request failure, and keeps its catch family."""

    @pytest.mark.parametrize("body,content_type,raw_call,kube_call,raw_class,family,message", _MALFORMED_TYPED_READS)
    def test_a_malformed_typed_read_names_a_malformed_response(
        self, caplog, body, content_type, raw_call, kube_call, raw_class, family, message
    ):
        def make_pool():
            return _RecordingPoolManager(body=body, content_type=content_type)

        raw = _raise(raw_call, _real_typed_kube_client(make_pool()))
        assert type(raw).__name__ == raw_class[_MAJOR], "precondition: this lane's SDK fails on this response"

        pool = make_pool()
        with caplog.at_level(logging.DEBUG):
            raised = _raise(kube_call, _real_typed_kube_client(pool))

        expected = message[_MAJOR] if isinstance(message, dict) else message
        family = family[_MAJOR] if isinstance(family, dict) else family
        assert type(raised) is family, "the catch family callers see is unchanged"
        if isinstance(raised, ApiException):
            assert raised.status == 0 and raised.reason == expected
        else:
            assert str(raised) == expected
        assert _REQUEST_FAILED not in str(raised)
        assert is_retryable_error(raised) is is_retryable_error(raw) is False
        assert len(pool.calls) == 1
        _assert_no_canary(caplog.text, *_rendered(raised))

    def test_a_request_side_value_error_is_not_a_malformed_response(self):
        """Argument validation fails before any response exists: it stays a request failure."""
        client = _real_typed_kube_client(_RecordingPoolManager(payload=_NAMESPACE))
        raw = _raise(lambda c: c.core_v1.read_namespace(None), client)
        assert isinstance(raw, ValueError), "precondition: the SDK rejects the argument itself"

        raised = _raise(lambda c: kube_client_module._call_sdk(lambda: c.core_v1.read_namespace(None)), client)

        assert type(raised) is ValueError and str(raised) == _REQUEST_FAILED
        assert client._api_client.rest_client.pool_manager.calls == []
        _assert_no_canary(*_rendered(raised))


_DISCOVERY_DOCUMENT = {
    "kind": "APIResourceList",
    "groupVersion": "g.io/v1",
    "resources": [{"name": "things", "kind": "Thing"}],
}


class _ServedDiscovery:
    """Answers discovery for g.io/v1 itself and hands every other request to `inner`, so a
    strict read reaches the request after discovery."""

    def __init__(self, inner):
        self._inner = inner

    def request(self, method, url, **kwargs):
        if url.split("?", 1)[0].endswith("/apis/g.io/v1"):
            return _RecordingPoolManager(payload=_DISCOVERY_DOCUMENT).request(method, url)
        return self._inner.request(method, url, **kwargs)


def _wait_for_pods(client):
    """One pod-list attempt inside `wait_for_pods_ready`, on a clock that then runs out."""
    ticks = iter(range(100))
    clock = SimpleNamespace(time=lambda: next(ticks) * 0.4, sleep=lambda _seconds: None)
    with patch.object(kube_client_module, "time", clock):
        return client.wait_for_pods_ready("ns1", "app=x", timeout=1)


def _guarded_patch(client):
    return client.json_patch_custom_resource_guarded(
        "r1",
        namespace="ns1",
        uid="u1",
        resource_version="7",
        expected_managed_clusters_backup_name="b1",
        replacement_managed_clusters_backup_name="latest",
    )


# Every KubeClient site that catches a boundary replacement and returns an outcome or keeps
# polling instead of raising it. Strict custom reads get discovery answered so their own request runs.
_SWALLOWING_SITES = [
    pytest.param(lambda c: c.api_resource_served("g.io", "v1", "things"), False, id="discovery"),
    pytest.param(lambda c: c.list_custom_resources_strict("g.io", "v1", "things", "ns1"), True, id="strict-list"),
    pytest.param(lambda c: c.get_custom_resource_strict("g.io", "v1", "things", "t1", "ns1"), True, id="strict-get"),
    pytest.param(lambda c: c.get_namespace_strict("ns1"), False, id="strict-namespace"),
    pytest.param(lambda c: c.list_pods_strict("ns1"), False, id="strict-pods"),
    pytest.param(lambda c: c.get_deployment_strict("d1", "ns1"), False, id="strict-deployment"),
    pytest.param(lambda c: c.get_replicaset_strict("r1", "ns1"), False, id="strict-replicaset"),
    pytest.param(_guarded_patch, False, id="guarded-patch"),
    pytest.param(
        lambda c: c.delete_restore_guarded("r1", namespace="ns1", uid="u1", resource_version="7"),
        False,
        id="guarded-delete",
    ),
    pytest.param(
        lambda c: c.create_restore_guarded({"metadata": {"name": "r1"}}, namespace="ns1"), False, id="guarded-create"
    ),
    pytest.param(_wait_for_pods, False, id="pod-wait"),
]

_SOCKET_FAILURES = {
    "connection-refused": lambda: ConnectionRefusedError(errno.ECONNREFUSED, "refused " + CANARY),
    "dns-failure": lambda: socket.gaierror(socket.EAI_NONAME, "Name or service not known " + CANARY),
    "connect-timeout": lambda: socket.timeout("timed out " + CANARY),
}
# What urllib3 itself makes of each failure: the raw reason class, checked as a precondition.
_RAW_REASONS = {
    "tls": urllib3.exceptions.SSLError,
    "connection-refused": urllib3.exceptions.NewConnectionError,
    "dns-failure": urllib3.exceptions.NameResolutionError,
    "connect-timeout": urllib3.exceptions.ConnectTimeoutError,
}
_DIAGNOSES = {
    "tls": "TLS error",
    "connection-refused": "connection error",
    "dns-failure": "connection error",
    "connect-timeout": "timed out",
}


class _transport_failing:
    """The SDK-built pool with its connect failing as `kind`: a TLS failure at `connect()`, the
    others at the socket factory urllib3's own connection calls."""

    def __init__(self, kind, served_discovery=False):
        self._kind = kind
        self._served_discovery = served_discovery
        self._patch = None
        self.connects = []

    def __enter__(self):
        if self._kind == "tls":
            client, self.connects = _configured_client(lambda: ssl.SSLError("synthetic certificate failure " + CANARY))
        else:

            def create_connection(*_args, **_kwargs):
                self.connects.append(1)
                raise _SOCKET_FAILURES[self._kind]()

            self._patch = patch("urllib3.util.connection.create_connection", side_effect=create_connection)
            self._patch.start()
            client = _configured_client()[0]
        if self._served_discovery:
            rest = client._api_client.rest_client
            rest.pool_manager = _ServedDiscovery(rest.pool_manager)
        return client

    def __exit__(self, *exc_info):
        if self._patch is not None:
            self._patch.stop()


def _project_log(caplog):
    return [record.getMessage() for record in caplog.records if record.name == "acm_switchover"]


@pytest.mark.unit
class TestSwallowedFailuresKeepTheirDiagnosis:
    """grok-r3 finding 2: a site that turns a failure into an outcome logs its fixed category, so
    a TLS failure, a refused connection and a timeout read differently."""

    @pytest.mark.parametrize("kind", sorted(_RAW_REASONS))
    def test_the_raw_transport_fails_as_the_kind_says(self, kind):
        with _transport_failing(kind) as client:
            raw = _raise(lambda c: c.core_v1.read_namespace("ns1"), client)
        assert isinstance(raw, urllib3.exceptions.MaxRetryError) and isinstance(raw.reason, _RAW_REASONS[kind])

    @pytest.mark.parametrize("kind", sorted(_DIAGNOSES))
    @pytest.mark.parametrize("route,served_discovery", _SWALLOWING_SITES)
    def test_the_site_logs_the_fixed_category(self, caplog, route, served_discovery, kind):
        failing = _transport_failing(kind, served_discovery)
        with failing as client:
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                outcome = route(client)

        assert failing.connects, "the site reached the failing transport"
        if outcome is False:
            pass  # pod-wait gave up when its budget ran out
        elif hasattr(outcome, "status"):
            assert outcome.status is StrictReadStatus.ERROR
        else:
            assert outcome["accepted"] is False and outcome["reason"] == "unverifiable"
        lines = _project_log(caplog)
        assert any(_DIAGNOSES[kind] in line for line in lines), f"no {_DIAGNOSES[kind]!r} in {lines}"
        for other in set(_DIAGNOSES.values()) - {_DIAGNOSES[kind]}:
            assert not any(other in line for line in lines), f"{other!r} logged for {kind}: {lines}"
        _assert_no_canary(caplog.text)

    @pytest.mark.parametrize("status", [401, 403])
    @pytest.mark.parametrize("route,served_discovery", [p for p in _SWALLOWING_SITES if p.id != "pod-wait"])
    def test_the_site_logs_the_http_status(self, caplog, route, served_discovery, status):
        pool = _RecordingPoolManager(status=status, payload={"kind": "Status", "message": CANARY})
        client = _real_typed_kube_client(_ServedDiscovery(pool) if served_discovery else pool)
        with caplog.at_level(logging.DEBUG):
            route(client)

        lines = _project_log(caplog)
        assert any(f"HTTP {status}" in line for line in lines), lines
        assert not any(f"HTTP {401 + 403 - status}" in line for line in lines)
        _assert_no_canary(caplog.text)

    @pytest.mark.parametrize("route,served_discovery", [p for p in _SWALLOWING_SITES if p.id != "pod-wait"])
    def test_a_status_0_tls_failure_logs_its_category(self, caplog, route, served_discovery):
        """The SDK's own status-0 ApiException for a TLS failure is not logged as a bare `HTTP 0`."""
        pool = _RaisingPool(lambda: urllib3.exceptions.SSLError("certificate verify failed: " + CANARY))
        raw = _raise(lambda c: c.core_v1.read_namespace("ns1"), _real_typed_kube_client(pool))
        assert isinstance(raw, ApiException) and raw.status == 0, "precondition: the SDK raises status 0"

        with caplog.at_level(logging.DEBUG):
            outcome = route(_real_typed_kube_client(_ServedDiscovery(pool) if served_discovery else pool))

        if hasattr(outcome, "status"):
            assert outcome.status is StrictReadStatus.ERROR
        else:
            assert outcome["reason"] == "unverifiable"
        assert any("HTTP 0 (TLS error)" in line for line in _project_log(caplog)), _project_log(caplog)
        _assert_no_canary(caplog.text)


class _NoMessageError(Exception):
    """A class outside the replacement modules: the boundary falls back to plain Exception."""


@pytest.mark.unit
@pytest.mark.parametrize(
    "operation,category",
    [
        (lambda: _raise_now(ApiException(status=403, reason=CANARY)), "HTTP error"),
        (lambda: _raise_now(urllib3.exceptions.SSLError(CANARY)), "TLS error"),
        (lambda: _raise_now(urllib3.exceptions.ReadTimeoutError(None, "/x", CANARY)), "timed out"),
        (lambda: _raise_now(urllib3.exceptions.ProtocolError(CANARY)), "connection error"),
        (lambda: _raise_now(json.JSONDecodeError(CANARY, CANARY, 0)), "malformed response"),
        (lambda: _raise_now(KeyError(CANARY)), "request failed"),
        (lambda: _raise_now(_NoMessageError(CANARY)), "request failed"),
    ],
    ids=["http", "tls", "timeout", "connection", "undecodable", "other", "fallback-class"],
)
def test_every_replacement_carries_one_category_of_the_closed_set(operation, category):
    raised = _raise(kube_client_module._call_sdk, operation)
    assert getattr(raised, kube_client_module._FAILURE_CATEGORY_ATTRIBUTE) == category
    assert kube_client_module._failure_category_of(raised) == category
    _assert_no_canary(*_rendered(raised))


@pytest.mark.skipif(
    _MAJOR < 37,
    reason=f"kubernetes {kubernetes.__version__} returns an undecodable custom-object body as a string",
)
@pytest.mark.unit
def test_a_custom_object_malformed_response_carries_the_malformed_category():
    def make_client():
        return _real_typed_kube_client(_RecordingPoolManager(body=b"{not json " + CANARY.encode()))

    raw = _raise(
        lambda c: c.custom_api.get_namespaced_custom_object("g.io", "v1", "ns1", "things", "t1"), make_client()
    )
    assert isinstance(raw, json.JSONDecodeError), "precondition: this lane's SDK fails to decode the body"
    raised = _raise(lambda c: c.get_custom_resource("g.io", "v1", "things", "t1", namespace="ns1"), make_client())
    assert isinstance(raised, MalformedResponseError)
    assert kube_client_module._failure_category_of(raised) == "malformed response"
    _assert_no_canary(*_rendered(raised))


def _raise_now(exc):
    raise exc


# A 2xx a strict read could not decode or deserialize: still READ_FAILED or discovery-unverifiable
# (the reason codes do not change), now logged as a malformed response.
_MALFORMED_ON_STRICT_SITES = [
    pytest.param(lambda c: c.api_resource_served("g.io", "v1", "things"), False, b"{not json", id="discovery"),
    pytest.param(
        lambda c: c.list_custom_resources_strict("g.io", "v1", "things", "ns1"), True, b"{not json", id="strict-list"
    ),
    pytest.param(
        lambda c: c.get_custom_resource_strict("g.io", "v1", "things", "t1", "ns1"), True, b"{not json", id="strict-get"
    ),
    pytest.param(lambda c: c.list_pods_strict("ns1"), False, b"{}", id="strict-pods"),
    pytest.param(
        lambda c: c.get_namespace_strict("ns1"),
        False,
        json.dumps({"metadata": {"name": "ns1", "creationTimestamp": CANARY}}).encode(),
        id="strict-namespace-bad-timestamp",
    ),
    pytest.param(
        lambda c: c.get_deployment_strict("d1", "ns1"),
        False,
        json.dumps({"spec": {"selector": CANARY}}).encode(),
        id="strict-deployment",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize("route,served_discovery,body", _MALFORMED_ON_STRICT_SITES)
def test_a_strict_read_logs_a_malformed_response(caplog, route, served_discovery, body):
    pool = _RecordingPoolManager(body=body)
    client = _real_typed_kube_client(_ServedDiscovery(pool) if served_discovery else pool)
    with caplog.at_level(logging.DEBUG):
        outcome = route(client)

    assert outcome.status is StrictReadStatus.ERROR
    lines = _project_log(caplog)
    if outcome.reason == STRICT_READ_REASON_MALFORMED_RESPONSE:
        # kubernetes 29.x hands an undecodable custom-object body back as a string, so no
        # failure is caught: the strict read's own reason code already names it.
        assert _MAJOR < 37 and served_discovery and lines == []
    else:
        assert any("malformed response" in line for line in lines), lines
    _assert_no_canary(caplog.text)


# ---------------------------------------------------------------------------
# Decommission through a real KubeClient
# ---------------------------------------------------------------------------

_MC_PATH = "/apis/cluster.open-cluster-management.io/v1"


class _HubPool:
    """A routed fake hub: two ManagedClusters, no Hive ClusterDeployments. The first DELETE is
    accepted; the second fails in transport. A deleted ManagedCluster then reads as 404."""

    def __init__(self, transport_failure):
        self._failure = transport_failure
        self.deleted = set()
        self.deletes = []

    @staticmethod
    def _respond(status, payload):
        return _RecordingPoolManager(status=status, payload=payload).request("GET", "/")

    def request(self, method, url, **kwargs):
        path = url.split("://", 1)[-1].split("/", 1)[-1].split("?", 1)[0]
        path = "/" + path
        if path == _MC_PATH:
            return self._respond(
                200,
                {
                    "kind": "APIResourceList",
                    "groupVersion": "cluster.open-cluster-management.io/v1",
                    "resources": [{"name": "managedclusters", "kind": "ManagedCluster"}],
                },
            )
        if path == "/apis/hive.openshift.io/v1":
            return self._respond(
                200,
                {
                    "kind": "APIResourceList",
                    "groupVersion": "hive.openshift.io/v1",
                    "resources": [{"name": "clusterdeployments", "kind": "ClusterDeployment"}],
                },
            )
        if path == "/apis/hive.openshift.io/v1/clusterdeployments":
            return self._respond(200, {"items": [], "metadata": {"resourceVersion": "1"}})
        if path == _MC_PATH + "/managedclusters":
            items = [self._cluster(n) for n in ("mc-a", "mc-b") if n not in self.deleted]
            return self._respond(200, {"items": items, "metadata": {"resourceVersion": "5"}})
        name = path.rsplit("/", 1)[-1]
        if method == "DELETE":
            self.deletes.append(name)
            if name == "mc-b":
                raise self._failure()
            self.deleted.add(name)
            return self._respond(200, {"kind": "Status", "status": "Success"})
        if name in self.deleted:
            return self._respond(404, {"kind": "Status", "message": CANARY})
        return self._respond(200, self._cluster(name))

    @staticmethod
    def _cluster(name):
        return {
            "apiVersion": "cluster.open-cluster-management.io/v1",
            "kind": "ManagedCluster",
            "metadata": {"name": name, "uid": "uid-" + name, "resourceVersion": "9"},
        }


@pytest.mark.unit
@pytest.mark.parametrize(
    "transport_failure,type_name",
    [
        (lambda: urllib3.exceptions.SSLError("certificate verify failed: " + CANARY), "API error 0"),
        (lambda: urllib3.exceptions.ProtocolError("aborted", ValueError(CANARY)), "transport error"),
        (lambda: urllib3.exceptions.MaxRetryError(None, "/x", urllib3.exceptions.SSLError(CANARY)), "transport error"),
    ],
    ids=["tls-status-0", "protocol-error", "max-retries-tls"],
)
def test_decommission_keeps_prior_changes_and_survivors_after_a_transport_failure(
    tmp_path, caplog, monkeypatch, transport_failure, type_name
):
    from lib.decommission_outcome import SubstepOutcome
    from lib.run_record import RunRecord
    from lib.utils import StateManager
    from modules.decommission import Decommission

    monkeypatch.setattr("lib.waiter.time.sleep", lambda _seconds: None)
    pool = _HubPool(transport_failure)
    client = _real_typed_kube_client(pool)
    state = StateManager(str(tmp_path / "state.json"))

    with caplog.at_level(logging.DEBUG):
        result = Decommission(primary_client=client, has_observability=False, run_record=RunRecord(state)).decommission(
            interactive=False
        )

    assert pool.deletes == ["mc-a", "mc-b"], "one DELETE per cluster, the failed one not retried"
    assert result.substeps["managed_clusters"] is SubstepOutcome.FAILED
    assert result.changed is True, "mc-a's accepted delete survives mc-b's transport failure"
    assert "multiclusterhub" in result.not_attempted
    assert "survivors" in caplog.text and "mc-b" in caplog.text
    assert f"Failed to delete ManagedCluster mc-b: {type_name}" in caplog.text
    _assert_no_canary(caplog.text, (tmp_path / "state.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Guardrail: every SDK operation in KubeClient runs inside the boundary helper
# ---------------------------------------------------------------------------

_SDK_ROOTS = ("core_v1", "apps_v1", "custom_api", "_api_client")
# The one method allowed to touch the SDK objects outside `_call_sdk`, and then only to build
# them and to read or set their configuration (`_constructor_exempt`); it issues no requests.
_CONSTRUCTOR = "__init__"
# One entry per hub SDK step in KubeClient, as `_sdk_step` counts them. A change here is a
# reviewed change to the set of operations the boundary owns.
_EXPECTED_INVENTORY_SIZE = 45
_CALLABLES = (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)


def _is_sdk_object(node):
    """A reference to one of KubeClient's SDK objects, on `self` or on any other name."""
    return isinstance(node, ast.Attribute) and node.attr in _SDK_ROOTS


def _is_boundary_call(node):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_call_sdk"


def _body(function):
    """The nodes that run when `function` is called, not when it is created."""
    return [function.body] if isinstance(function, ast.Lambda) else function.body


def _is_generator(function):
    """Whether calling `function` only creates a generator, deferring its body."""
    pending = list(_body(function))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.Yield, ast.YieldFrom)):
            return True
        if not isinstance(node, _CALLABLES):
            pending.extend(ast.iter_child_nodes(node))
    return False


def _operations(method):
    """The callables whose body `_call_sdk` itself runs: a lambda passed as its first argument,
    or an undecorated nested function passed by name as that argument and referenced nowhere
    else; neither may be a generator. A decorator could run or replace the function before
    `_call_sdk` sees it, and a generator's body runs only when iterated, after the boundary."""
    operations = set()
    for node in ast.walk(method):
        if not (_is_boundary_call(node) and node.args):
            continue
        operation = node.args[0]
        if isinstance(operation, ast.Name):
            loads = [n for n in ast.walk(method) if isinstance(n, ast.Name) and n.id == operation.id]
            nested = [n for n in ast.walk(method) if isinstance(n, _CALLABLES[1:]) and n.name == operation.id]
            if not (len(loads) == 1 and len(nested) == 1 and nested[0] is not method):
                continue
            operation = nested[0]
        if isinstance(operation, ast.Lambda) or (
            isinstance(operation, ast.FunctionDef) and not operation.decorator_list
        ):
            if not _is_generator(operation):
                operations.add(operation)
    return operations


def _inside_boundary(node, parents, operations):
    """Whether `node` executes while `_call_sdk` runs an operation, judged by when Python runs
    it rather than where it is written. Walking outwards: code in a callable's body runs when
    that callable is called, so it is inside only if the callable is an operation; a callable's
    defaults, decorators and annotations run where it is created, so the walk continues there;
    a generator expression runs everything but its first iterable lazily, after the boundary;
    list, set and dict comprehensions run on the spot."""
    child = node
    while child in parents:
        parent = parents[child]
        if isinstance(parent, _CALLABLES) and any(child is n for n in _body(parent)):
            return parent in operations
        if isinstance(parent, ast.GeneratorExp) and child is parent.elt:
            return False
        if isinstance(parent, ast.comprehension) and isinstance(parents[parent], ast.GeneratorExp):
            if not (parent is parents[parent].generators[0] and child is parent.iter):
                return False
        child = parent
    return False


def _chain_root(node):
    """The object an attribute, subscript or call chain starts from."""
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    if isinstance(node, ast.Call) and ast.unparse(node.func) == "getattr" and node.args:
        return _chain_root(node.args[0])
    return node


def _constructor_exempt(node, parents):
    """In the constructor, an SDK object that is assigned (`self.core_v1 = ...`) or whose
    configuration is read or set (`self.core_v1.api_client.configuration.timeout`), but not
    called through."""
    parent = parents.get(node)
    if isinstance(node.ctx, ast.Store) and isinstance(parent, (ast.Assign, ast.AnnAssign)):
        return True
    configuration = False
    while isinstance(parent, ast.Attribute) and parent.value is node:
        configuration = configuration or parent.attr == "configuration"
        node, parent = parent, parents.get(parent)
    return configuration and not (isinstance(parent, ast.Call) and parent.func is node)


def _constructor_requests(method, inside):
    """Violations for calls the constructor makes on an object it built, or on anything reached
    from one: `api_client.call_api(...)`, `client.CoreV1Api(api_client).read_namespace(...)`.
    The constructor may build objects, hand them on and set their attributes, but calling into
    what it built is a request issued under another name."""
    constructed = set()
    while True:
        names = {
            target.id
            for node in ast.walk(method)
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
            and (
                isinstance(_chain_root(node.value), ast.Call)
                or (isinstance(_chain_root(node.value), ast.Name) and _chain_root(node.value).id in constructed)
            )
        }
        if names <= constructed:
            break
        constructed |= names
    violations = []
    for node in ast.walk(method):
        if not isinstance(node, ast.Call) or ast.unparse(node.func) == "getattr":
            continue
        if isinstance(node.func, ast.Name):
            # A name bound from a constructed client (`read = client.CoreV1Api(c).read_namespace`)
            # is that client's bound method: calling it is a request under a local name.
            if node.func.id in constructed and not inside(node):
                violations.append(
                    f"{method.name}:{node.lineno} calls {node.func.id}, bound from a constructed client,"
                    " outside _call_sdk"
                )
            continue
        if not isinstance(node.func, ast.Attribute) and not (
            isinstance(node.func, ast.Call) and ast.unparse(node.func.func) == "getattr"
        ):
            continue
        receiver = _chain_root(node.func.value if isinstance(node.func, ast.Attribute) else node.func)
        built = isinstance(receiver, ast.Call) or (isinstance(receiver, ast.Name) and receiver.id in constructed)
        if built and not inside(node):
            violations.append(
                f"{method.name}:{node.lineno} calls {ast.unparse(node.func)} on a constructed client outside _call_sdk"
            )
    return violations


def _called_in_place(node, parents, member, inside):
    """Whether an SDK object referenced inside an operation is used there and then: called
    through (`self.core_v1.read_namespace(...)`, `getattr(self.apps_v1, reader)(...)`), probed
    with `hasattr`, or bound to a local alias whose every use is a call inside the boundary.
    Anything else (returning it, wrapping it uncalled) lets the request run after `_call_sdk`
    has returned."""
    top, parent = node, parents.get(node)
    while isinstance(parent, ast.Attribute) and parent.value is top:
        top, parent = parent, parents.get(parent)
    if isinstance(parent, ast.Call) and parent.func is top:
        return True
    if isinstance(parent, ast.Call) and parent.args and parent.args[0] is top:
        if ast.unparse(parent.func) == "hasattr":
            return True
        if ast.unparse(parent.func) == "getattr":
            caller = parents.get(parent)
            return isinstance(caller, ast.Call) and caller.func is parent
    if isinstance(parent, ast.Assign) and len(parent.targets) == 1:
        alias = parent.targets[0]
    elif isinstance(parent, ast.AnnAssign):
        alias = parent.target
    else:
        return False
    if not isinstance(alias, ast.Name):
        return False
    uses = [n for n in ast.walk(member) if isinstance(n, ast.Name) and n.id == alias.id and isinstance(n.ctx, ast.Load)]
    return bool(uses) and all(isinstance(parents.get(n), ast.Call) and parents[n].func is n and inside(n) for n in uses)


def _sdk_step(node):
    """The callee text of an SDK step: a call through an SDK object (directly, through `getattr`
    or through the `legacy_call_api` alias), or a raw response body read. Else None."""
    if isinstance(node, ast.Attribute) and ast.unparse(node) == "raw.data":
        return "raw.data"
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Call) and ast.unparse(func.func) == "getattr" and func.args:
        func = func.args[0]
    root = func
    while isinstance(root, ast.Attribute) and not _is_sdk_object(root):
        root = root.value
    callee = ast.unparse(node.func)
    if _is_sdk_object(root) or callee in ("legacy_call_api", "raw.read"):
        return callee
    return None


def _boundary_violations(source):
    """(violations, inventory) for lib/kube_client.py.

    Violations: an SDK object used outside KubeClient; inside KubeClient (every method and
    every other class-body statement), an SDK object used, or an SDK step that reaches no SDK
    object itself (a raw body read, the legacy alias) run, where it does not execute inside an
    operation `_call_sdk` runs (`_inside_boundary`), or that does execute there but is handed
    out uncalled (`_called_in_place`). The constructor may additionally build the SDK objects
    and read or set their configuration, but may call nothing it built. The inventory lists
    every SDK step in KubeClient."""
    tree = ast.parse(source)
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "KubeClient")
    in_class = set(ast.walk(klass))
    violations = [
        f"<module>:{node.lineno} uses .{node.attr} outside KubeClient"
        for node in ast.walk(tree)
        if _is_sdk_object(node) and node not in in_class
    ]
    inventory = []
    for member in klass.body:
        name = member.name if isinstance(member, _CALLABLES[1:]) else "<KubeClient>"
        constructor = isinstance(member, ast.FunctionDef) and member.name == _CONSTRUCTOR
        parents = {child: parent for parent in ast.walk(member) for child in ast.iter_child_nodes(parent)}
        operations = _operations(member)

        def inside(node):
            return _inside_boundary(node, parents, operations)

        if constructor:
            violations.extend(_constructor_requests(member, inside))
        for node in ast.walk(member):
            step = _sdk_step(node)
            if step is not None:
                inventory.append(f"{name}: {step}")
                if not any(_is_sdk_object(n) for n in ast.walk(node)) and not inside(node):
                    violations.append(f"{name}:{node.lineno} runs {step} outside _call_sdk")
            if not _is_sdk_object(node):
                continue
            if not inside(node):
                if not (constructor and _constructor_exempt(node, parents)):
                    violations.append(f"{name}:{node.lineno} uses .{node.attr} outside _call_sdk")
            elif not _called_in_place(node, parents, member, inside):
                violations.append(f"{name}:{node.lineno} hands .{node.attr} out of _call_sdk uncalled")
    return violations, inventory


def _kube_client_source():
    return Path(kube_client_module.__file__).read_text(encoding="utf-8")


@pytest.mark.unit
class TestBoundaryGuardrail:
    def test_every_sdk_operation_runs_inside_the_boundary(self):
        violations, inventory = _boundary_violations(_kube_client_source())
        assert violations == []
        assert len(inventory) == _EXPECTED_INVENTORY_SIZE, "\n".join(inventory)

    def test_the_guardrail_rejects_a_direct_sdk_call(self):
        source = (
            "class KubeClient:\n"
            "    def get(self):\n"
            "        return self.core_v1.read_namespace('a')\n"
            "    def aliased(self):\n"
            "        read = self.core_v1.read_namespace\n"
            "        return _call_sdk(lambda: read('a'))\n"
            "    def ok(self):\n"
            "        return _call_sdk(lambda: self.core_v1.read_namespace('a'))\n"
            "    def nested(self):\n"
            "        def operation():\n"
            "            return self._api_client.call_api('a')\n"
            "        operation()\n"
            "        return _call_sdk(operation)\n"
            "    def body_read_outside(self):\n"
            "        raw = _call_sdk(lambda: self._api_client.call_api('a'))\n"
            "        return raw.read()\n"
            "def module_helper(kube):\n"
            "    return kube.core_v1.read_namespace('a')\n"
        )
        violations, inventory = _boundary_violations(source)
        assert sorted(violations) == sorted(
            [
                "<module>:18 uses .core_v1 outside KubeClient",
                "get:3 uses .core_v1 outside _call_sdk",
                "aliased:5 uses .core_v1 outside _call_sdk",
                "nested:11 uses ._api_client outside _call_sdk",
                "body_read_outside:16 runs raw.read outside _call_sdk",
            ]
        )
        assert len(inventory) == 5

    def test_the_guardrail_rejects_a_request_made_while_creating_the_operation(self):
        # Defaults, decorators and annotations are evaluated when the callable is created,
        # before `_call_sdk` runs it; only the callable's body runs inside the boundary.
        source = (
            "class KubeClient:\n"
            "    def positional_default(self):\n"
            "        return _call_sdk(lambda r=self.core_v1.read_namespace('a'): r.to_dict())\n"
            "    def keyword_only_default(self):\n"
            "        return _call_sdk(lambda *, r=self.core_v1.read_namespace('a'): r)\n"
            "    def nested_default(self):\n"
            "        def operation(r=self.core_v1.read_namespace('a')):\n"
            "            return r\n"
            "        return _call_sdk(operation)\n"
            "    def nested_keyword_only_default(self):\n"
            "        def operation(*, r=self.apps_v1.read_namespaced_deployment('a', 'b')):\n"
            "            return r\n"
            "        return _call_sdk(operation)\n"
            "    def annotations(self):\n"
            "        def operation(r: self.core_v1.read_namespace('a') = None) -> self.custom_api.get_x('b'):\n"
            "            return r\n"
            "        return _call_sdk(operation)\n"
            "    def decorator_argument(self):\n"
            "        @register(self.core_v1.read_namespace('a'))\n"
            "        def operation():\n"
            "            return None\n"
            "        return _call_sdk(operation)\n"
            "    def decorated_operation(self):\n"
            "        @functools.cache\n"
            "        def operation():\n"
            "            return self.core_v1.read_namespace('a')\n"
            "        return _call_sdk(operation)\n"
            "    def default_inside_an_outer_operation(self):\n"
            "        return _call_sdk(lambda: _call_sdk(lambda r=self.core_v1.read_namespace('a'): r))\n"
            "    def body_only(self):\n"
            "        def operation(r=None):\n"
            "            return self.core_v1.read_namespace('a')\n"
            "        return _call_sdk(operation)\n"
        )
        violations, inventory = _boundary_violations(source)
        assert sorted(violations) == sorted(
            [
                "positional_default:3 uses .core_v1 outside _call_sdk",
                "keyword_only_default:5 uses .core_v1 outside _call_sdk",
                "nested_default:7 uses .core_v1 outside _call_sdk",
                "nested_keyword_only_default:11 uses .apps_v1 outside _call_sdk",
                "annotations:15 uses .core_v1 outside _call_sdk",
                "annotations:15 uses .custom_api outside _call_sdk",
                "decorator_argument:19 uses .core_v1 outside _call_sdk",
                "decorated_operation:26 uses .core_v1 outside _call_sdk",
            ]
        )
        assert len(inventory) == 10

    def test_the_guardrail_rejects_a_request_deferred_past_the_boundary(self):
        # Code the operation only defines (a generator's lazy parts, an inner callable, a
        # generator or coroutine operation's body) runs after `_call_sdk` has returned.
        source = (
            "class KubeClient:\n"
            "    def generator_element(self, names):\n"
            "        return _call_sdk(lambda: (self.core_v1.read_namespace(n) for n in names))\n"
            "    def generator_condition(self, names):\n"
            "        return _call_sdk(lambda: (n for n in names if self.core_v1.read_namespace(n)))\n"
            "    def generator_later_iterable(self, names):\n"
            "        return _call_sdk(lambda: (p for n in names for p in self.core_v1.list_x(n)))\n"
            "    def generator_first_iterable(self):\n"
            "        return _call_sdk(lambda: (n for n in self.core_v1.list_namespace().items))\n"
            "    def comprehensions(self, names):\n"
            "        return _call_sdk(lambda: ([self.core_v1.read_namespace(n) for n in names],"
            " {n: self.core_v1.read_namespace(n) for n in names}))\n"
            "    def returned_lambda(self):\n"
            "        return _call_sdk(lambda: lambda: self.core_v1.read_namespace('a'))\n"
            "    def returned_helper(self):\n"
            "        def operation():\n"
            "            def helper():\n"
            "                return self.core_v1.read_namespace('a')\n"
            "            return helper\n"
            "        return _call_sdk(operation)\n"
            "    def generator_operation(self):\n"
            "        def operation():\n"
            "            yield self.core_v1.read_namespace('a')\n"
            "        return _call_sdk(operation)\n"
            "    def generator_lambda(self):\n"
            "        return _call_sdk(lambda: (yield self.core_v1.read_namespace('a')))\n"
            "    def coroutine_operation(self):\n"
            "        async def operation():\n"
            "            return self.core_v1.read_namespace('a')\n"
            "        return _call_sdk(operation)\n"
        )
        violations, inventory = _boundary_violations(source)
        assert sorted(violations) == sorted(
            [
                "generator_element:3 uses .core_v1 outside _call_sdk",
                "generator_condition:5 uses .core_v1 outside _call_sdk",
                "generator_later_iterable:7 uses .core_v1 outside _call_sdk",
                "returned_lambda:13 uses .core_v1 outside _call_sdk",
                "returned_helper:17 uses .core_v1 outside _call_sdk",
                "generator_operation:22 uses .core_v1 outside _call_sdk",
                "generator_lambda:25 uses .core_v1 outside _call_sdk",
                "coroutine_operation:28 uses .core_v1 outside _call_sdk",
            ]
        )
        assert len(inventory) == 11

    def test_the_guardrail_rejects_an_operation_that_is_also_run_elsewhere(self):
        source = (
            "class KubeClient:\n"
            "    def bound_lambda(self):\n"
            "        operation = lambda: self.core_v1.read_namespace('a')\n"
            "        operation()\n"
            "        return _call_sdk(operation)\n"
            "    def walrus(self):\n"
            "        _call_sdk(operation := lambda: self.core_v1.read_namespace('a'))\n"
            "        return operation()\n"
            "    def handed_elsewhere(self):\n"
            "        def operation():\n"
            "            return self.core_v1.read_namespace('a')\n"
            "        threading.Thread(target=operation).start()\n"
            "        return _call_sdk(operation)\n"
            "    def only_the_boundary_runs_it(self):\n"
            "        def operation():\n"
            "            return self.core_v1.read_namespace('a')\n"
            "        return _call_sdk(operation)\n"
        )
        violations, inventory = _boundary_violations(source)
        assert sorted(violations) == sorted(
            [
                "bound_lambda:3 uses .core_v1 outside _call_sdk",
                "walrus:7 uses .core_v1 outside _call_sdk",
                "handed_elsewhere:11 uses .core_v1 outside _call_sdk",
            ]
        )
        assert len(inventory) == 4

    def test_the_guardrail_limits_the_constructor_to_building_and_configuring_clients(self):
        # Mirrors KubeClient.__init__: building the API objects and setting or reading their
        # configuration is allowed; any request on them, or on what built them, is not.
        source = (
            "class KubeClient:\n"
            "    def __init__(self, request_timeout):\n"
            "        api_client = config.new_client_from_config(context=None, persist_config=False)\n"
            "        configuration = api_client.configuration\n"
            "        configuration.retries = 0\n"
            "        if hasattr(configuration, 'assert_hostname'):\n"
            "            configuration.assert_hostname = False\n"
            "        self.core_v1 = client.CoreV1Api(api_client)\n"
            "        self._api_client = api_client\n"
            "        self.core_v1.api_client.configuration.timeout = request_timeout\n"
            "        self.timeout = self._api_client.configuration.timeout\n"
            "        logger.info('Initialized %s', request_timeout)\n"
            "        self.core_v1.read_namespace('ns1')\n"
            "        alias = self.apps_v1\n"
            "        register(self.custom_api)\n"
            "        self._api_client.configuration.refresh_api_key_hook(None)\n"
            "        api_client.call_api('/x', 'GET')\n"
            "        rest = api_client.rest_client\n"
            "        rest.request('GET', '/x')\n"
            "        getattr(api_client, 'call_api')('/x', 'GET')\n"
            "        client.CoreV1Api(api_client).read_namespace('x')\n"
            "        self.ready = _call_sdk(lambda: api_client.call_api('/healthz', 'GET'))\n"
        )
        violations, inventory = _boundary_violations(source)
        assert sorted(violations) == sorted(
            [
                "__init__:13 uses .core_v1 outside _call_sdk",
                "__init__:14 uses .apps_v1 outside _call_sdk",
                "__init__:15 uses .custom_api outside _call_sdk",
                "__init__:16 uses ._api_client outside _call_sdk",
                "__init__:17 calls api_client.call_api on a constructed client outside _call_sdk",
                "__init__:19 calls rest.request on a constructed client outside _call_sdk",
                "__init__:20 calls getattr(api_client, 'call_api') on a constructed client outside _call_sdk",
                "__init__:21 calls client.CoreV1Api(api_client).read_namespace on a constructed client"
                " outside _call_sdk",
            ]
        )
        assert inventory == [
            "__init__: self.core_v1.read_namespace",
            "__init__: self._api_client.configuration.refresh_api_key_hook",
        ]

    def test_the_guardrail_rejects_a_bound_method_alias_called_outside_an_operation(self):
        # astra-r4 H1: a bound method taken from a constructed client and called through a local
        # name is a request. Called inside an operation it is allowed; outside the constructor,
        # taking the alias from an SDK attribute is already a use outside the boundary.
        source = (
            "class KubeClient:\n"
            "    def __init__(self):\n"
            "        api_client = config.new_client_from_config(context=None, persist_config=False)\n"
            "        read_namespace = client.CoreV1Api(api_client).read_namespace\n"
            "        read_namespace('ns1')\n"
            "        call = api_client.call_api\n"
            "        call('/x', 'GET')\n"
            "        send = api_client.rest_client.request\n"
            "        send('GET', '/x')\n"
            "        ready = client.CoreV1Api(api_client).read_namespace\n"
            "        self.ready = _call_sdk(lambda: ready('ns1'))\n"
            "    def aliased_outside_an_operation(self):\n"
            "        read = self.core_v1.read_namespace\n"
            "        return read('ns1')\n"
            "    def built_and_aliased_outside_an_operation(self):\n"
            "        read = client.CoreV1Api(self._api_client).read_namespace\n"
            "        return read('ns1')\n"
        )
        violations, inventory = _boundary_violations(source)
        assert sorted(violations) == sorted(
            [
                "__init__:5 calls read_namespace, bound from a constructed client, outside _call_sdk",
                "__init__:7 calls call, bound from a constructed client, outside _call_sdk",
                "__init__:9 calls send, bound from a constructed client, outside _call_sdk",
                "aliased_outside_an_operation:13 uses .core_v1 outside _call_sdk",
                "built_and_aliased_outside_an_operation:16 uses ._api_client outside _call_sdk",
            ]
        )
        assert inventory == []

    def test_the_guardrail_rejects_an_sdk_object_handed_out_of_the_boundary_uncalled(self):
        # A reference evaluated inside the operation but called after `_call_sdk` returns makes
        # its request outside the boundary; inside an operation an SDK object is called in place.
        source = (
            "class KubeClient:\n"
            "    def returned_object(self):\n"
            "        return _call_sdk(lambda: self.core_v1).read_namespace('a')\n"
            "    def returned_method(self):\n"
            "        return _call_sdk(lambda: self.core_v1.read_namespace)('a')\n"
            "    def returned_partial(self):\n"
            "        return _call_sdk(lambda: functools.partial(self.core_v1.read_namespace, 'a'))()\n"
            "    def returned_alias(self):\n"
            "        def operation():\n"
            "            read = self.core_v1.read_namespace\n"
            "            return read\n"
            "        return _call_sdk(operation)('a')\n"
            "    def called_in_place(self, reader):\n"
            "        def operation():\n"
            "            if hasattr(self._api_client, 'param_serialize'):\n"
            "                return getattr(self.apps_v1, reader)('a')\n"
            "            call: Callable[..., Any] = self._api_client.call_api\n"
            "            return call('/x', 'GET')\n"
            "        return _call_sdk(operation)\n"
        )
        violations, inventory = _boundary_violations(source)
        assert sorted(violations) == sorted(
            [
                "returned_object:3 hands .core_v1 out of _call_sdk uncalled",
                "returned_method:5 hands .core_v1 out of _call_sdk uncalled",
                "returned_partial:7 hands .core_v1 out of _call_sdk uncalled",
                "returned_alias:10 hands .core_v1 out of _call_sdk uncalled",
            ]
        )
        assert inventory == ["called_in_place: getattr(self.apps_v1, reader)"]

    def test_the_guardrail_covers_the_whole_class_body(self):
        source = (
            "class KubeClient:\n"
            "    probe = staticmethod(lambda kube: kube.core_v1.read_namespace('a'))\n"
            "    async def fetch(self):\n"
            "        return self.core_v1.read_namespace('a')\n"
            "    async def fetch_inside(self):\n"
            "        return _call_sdk(lambda: self.core_v1.read_namespace('a'))\n"
        )
        violations, inventory = _boundary_violations(source)
        assert sorted(violations) == sorted(
            [
                "<KubeClient>:2 uses .core_v1 outside _call_sdk",
                "fetch:4 uses .core_v1 outside _call_sdk",
            ]
        )
        assert len(inventory) == 3


# ---------------------------------------------------------------------------
# Runtime invariant: every request that reaches the transport is made inside `_call_sdk`
# ---------------------------------------------------------------------------
#
# The static guardrail above reads source, and no static reading of Python is complete. This
# layer checks the property itself: under a real, installed ApiClient whose only fake is the
# urllib3 pool manager, every request and every response-body read records whether
# `_SDK_CALL_ACTIVE` is set at that moment. Every KubeClient is built by the real `__init__`,
# and each public method is driven through a success, an HTTP error and a transport error,
# plus scripted paths whose second and later requests (pages, restarts, re-reads, polls,
# retries) are the ones that answer or fail. Failure answers carry the canary, and what the
# method raises, returns or logs must not. It sees only the paths it drives; the status-branch
# tests below derive, from the source, the status handlers it must drive and check that it does.

_INV_NS = "ns1"
_INV_CUSTOM = ("g.io", "v1", "things")
# Every failure answer carries the canary, so what leaves the boundary is checked as well.
_INV_STATUS = {"kind": "Status", "apiVersion": "v1", "status": "Failure", "message": CANARY}
_INV_BODY = {
    "apiVersion": "g.io/v1",
    "kind": "Thing",
    "metadata": {"name": "t1", "namespace": _INV_NS},
    "spec": {"a": 1},
}
_INV_CLUSTER_BODY = {"apiVersion": "g.io/v1", "kind": "Thing", "metadata": {"name": "t1"}, "spec": {"a": 1}}
_INV_RESTORE = {
    "apiVersion": "cluster.open-cluster-management.io/v1beta1",
    "kind": "Restore",
    "metadata": {"name": "r1"},
}
_INV_GUARD = {"namespace": _INV_NS, "uid": "uid-r1", "resource_version": "9"}


def _inv_object(name, namespace=None, **fields):
    metadata = {"name": name, "uid": "uid-" + name, "resourceVersion": "9"}
    if namespace:
        metadata["namespace"] = namespace
    return {**fields, "metadata": metadata}


def _inv_page(items, **metadata):
    return {"items": items, "metadata": {"resourceVersion": "9", **metadata}}


_INV_READY_POD = _inv_object("p1", _INV_NS, status={"conditions": [{"type": "Ready", "status": "True"}]})
_INV_CONTINUED = (200, _inv_page([_inv_object("t0", _INV_NS)], **{"continue": "c1"}))


def _inv_success(method, path, body):
    """A well-formed 2xx answer to any request KubeClient makes, by path."""
    parts = path.strip("/").split("/")
    if parts[0] == "api":  # api/v1/namespaces[/<ns>[/<kind>[/<name>[/log]]]]
        rest = parts[2:]
        if len(rest) == 1:
            return _inv_page([_inv_object(_INV_NS)])
        if len(rest) == 2:
            return _inv_object(rest[1])
        if rest[-1] == "log":
            return "log line"
        if rest[2] == "pods" and len(rest) == 3:
            return _inv_page([_INV_READY_POD])
        return _inv_object(rest[3] if len(rest) > 3 else "cm1", rest[1])
    if parts[1] == "apps":  # apis/apps/v1/namespaces/<ns>/<kind>/<name>[/scale]
        spec = {"replicas": 0} if parts[-1] == "scale" else {"selector": {}, "template": {}, "serviceName": "s"}
        return _inv_object(parts[6], parts[4], spec=spec)
    group, version, rest = parts[1], parts[2], parts[3:]
    if not rest:
        resources = [
            {"name": plural, "kind": "Kind", "namespaced": True} for plural in (_INV_CUSTOM[2], "managedclusters")
        ]
        return {"kind": "APIResourceList", "groupVersion": f"{group}/{version}", "resources": resources}
    namespace = rest[1] if rest[0] == "namespaces" else None
    rest = rest[2:] if namespace else rest
    if len(rest) == 1 and method == "POST":
        created = json.loads(body)
        created["metadata"].update(uid="uid-" + created["metadata"]["name"], resourceVersion="9")
        return created
    if len(rest) == 1:
        page = _inv_page([_inv_object("t1", namespace, apiVersion=f"{group}/{version}", kind="Thing")])
        return {"apiVersion": f"{group}/{version}", "kind": "KindList", **page}
    if method == "DELETE":
        return {"kind": "Status", "apiVersion": "v1", "status": "Success"}
    return _inv_object(rest[1], namespace, apiVersion=f"{group}/{version}", kind="Thing", spec={"a": 1})


class _ProbedBody(io.BytesIO):
    """A response body that records whether the boundary is active whenever it is read."""

    def __init__(self, data, reads):
        super().__init__(data)
        self._reads = reads

    def read(self, *args):
        self._reads.append(kube_client_module._SDK_CALL_ACTIVE.get())
        return super().read(*args)

    def read1(self, *args):
        self._reads.append(kube_client_module._SDK_CALL_ACTIVE.get())
        return super().read1(*args)

    def readinto(self, buffer):
        self._reads.append(kube_client_module._SDK_CALL_ACTIVE.get())
        return super().readinto(buffer)


class _BoundaryProbe:
    """The urllib3 pool manager under a real ApiClient. Request `i` is answered by `script(i)`
    when that gives an answer, else by `mode`: "ok" (a well-formed 200, or 201 to a create),
    "http" (500) or "transport" (a dropped connection); a `(status, payload)` answer is served
    as is. Failure answers carry the canary."""

    def __init__(self, mode, script=None):
        self._mode = mode
        self._script = script or (lambda _index: None)
        self.requests = []
        self.reads = []

    def request(self, method, url, **kwargs):
        path = url.split("://", 1)[-1].split("/", 1)[-1].split("?", 1)[0]
        self.requests.append((method, "/" + path, kube_client_module._SDK_CALL_ACTIVE.get()))
        answer = self._script(len(self.requests) - 1) or self._mode
        if answer == "transport":
            raise urllib3.exceptions.ProtocolError("Connection aborted.", ConnectionResetError(CANARY))
        if answer == "http":
            answer = (500, {**_INV_STATUS, "code": 500})
        if answer == "ok":
            answer = (201 if method == "POST" else 200, _inv_success(method, "/" + path, kwargs.get("body")))
        status, payload = answer
        return urllib3.HTTPResponse(
            body=_ProbedBody(json.dumps(payload).encode("utf-8"), self.reads),
            status=status,
            reason="Fake",
            headers={"Content-Type": "application/json"},
            preload_content=False,
        )

    def outside(self):
        """Every request and body read that happened with the boundary flag unset."""
        found = [f"{method} {path}" for method, path, active in self.requests if not active]
        return found + [f"body read #{i}" for i, active in enumerate(self.reads) if not active]

    def fails_from(self, index, horizon=32):
        """Whether request `index` and every later one would be answered by a failure carrying
        the canary. The scripts are finite or periodic, so a bounded horizon decides it."""
        if self._mode not in ("http", "transport"):
            return False
        for later in range(index, index + horizon):
            answer = self._script(later)
            if answer is None or answer in ("http", "transport"):
                continue
            if not (isinstance(answer, tuple) and answer[0] >= 400 and CANARY in json.dumps(answer[1])):
                return False
        return True


def _probed_construction(probe, **kwargs):
    """`KubeClient(**kwargs)` through the real `__init__`, its kubeconfig client on `probe`."""
    api_client = _real_api_client(probe)
    api_client.configuration.host = "https://hub.invalid"
    with patch.object(kube_client_module.config, "new_client_from_config", return_value=api_client):
        return KubeClient(**kwargs)


class _FakeClock:
    """`time` for `lib.kube_client`: `sleep` advances `time`, so polling ends without waiting."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    @staticmethod
    def strftime(fmt):
        return "20261123000000"


def _drive(method, mode, script, args, kwargs, recorder=None):
    """Run one case through a probed KubeClient: (probe, result, raised). With a `recorder`,
    the status sites the method enters are recorded against the probe's request count."""
    probe = _BoundaryProbe(mode, script)
    result = raised = None
    client = _probed_construction(probe, context="inv")
    recording = contextlib.nullcontext()
    if recorder is not None:
        recorder.probe = probe
        recording = _recording(recorder)
    with recording:
        try:
            result = getattr(client, method)(*args, **kwargs)
        except Exception as exc:  # failure modes raise; callers judge the invariant either way
            raised = exc
    return probe, result, raised


# method: (args, kwargs, requests on success, on an HTTP 500, on a transport error). A retried
# method makes 5 attempts; a strict custom read stops at a failed discovery.
_INV_DRIVES = {
    "get_namespace": ((_INV_NS,), {}, 1, 5, 5),
    "namespace_exists": ((_INV_NS,), {}, 1, 5, 5),
    "get_cluster_identity": ((), {}, 1, 5, 5),
    "list_namespaces": ((), {}, 1, 5, 5),
    "get_secret": ((_INV_NS, "s1"), {}, 1, 5, 5),
    "secret_exists": ((_INV_NS, "s1"), {}, 1, 5, 5),
    "get_configmap": ((_INV_NS, "cm1"), {}, 1, 5, 5),
    "get_configmap_advisory": ((_INV_NS, "cm1"), {}, 1, 5, 5),
    "exists_configmap": ((_INV_NS, "cm1"), {}, 1, 5, 5),
    "create_or_patch_configmap": ((_INV_NS, "cm1", {"k": "v"}), {}, 1, 5, 5),
    "delete_configmap": ((_INV_NS, "cm1"), {}, 1, 5, 5),
    "delete_pod": ((_INV_NS, "p1"), {}, 1, 5, 5),
    "get_route_host": ((_INV_NS, "route1"), {}, 1, 5, 5),
    "get_custom_resource": ((*_INV_CUSTOM, "t1"), {"namespace": _INV_NS}, 1, 5, 5),
    "get_custom_resource_advisory": ((*_INV_CUSTOM, "t1"), {"namespace": _INV_NS}, 1, 5, 5),
    "list_custom_resources": (_INV_CUSTOM, {"namespace": _INV_NS}, 1, 5, 5),
    "list_custom_resources_advisory": (_INV_CUSTOM, {"namespace": _INV_NS}, 1, 5, 5),
    "list_custom_resources_strict": (_INV_CUSTOM, {"namespace": _INV_NS}, 2, 1, 1),
    "list_optional_namespaced_resources_strict": (_INV_CUSTOM, {"kind": "Kind"}, 2, 1, 1),
    "get_custom_resource_strict": ((*_INV_CUSTOM, "t1"), {"namespace": _INV_NS}, 2, 1, 1),
    "get_namespace_strict": ((_INV_NS,), {}, 1, 1, 1),
    "list_pods_strict": ((_INV_NS,), {}, 1, 1, 1),
    "get_deployment_strict": (("d1", _INV_NS), {}, 1, 1, 1),
    "get_replicaset_strict": (("rs1", _INV_NS), {}, 1, 1, 1),
    "patch_custom_resource": ((*_INV_CUSTOM, "t1", {"spec": {"a": 2}}), {"namespace": _INV_NS}, 1, 5, 5),
    "json_patch_custom_resource_guarded": (
        ("r1",),
        {
            **_INV_GUARD,
            "expected_managed_clusters_backup_name": "b1",
            "replacement_managed_clusters_backup_name": "latest",
        },
        1,
        1,
        1,
    ),
    "delete_restore_guarded": (("r1",), _INV_GUARD, 1, 1, 1),
    "create_restore_guarded": ((_INV_RESTORE,), {"namespace": _INV_NS}, 1, 1, 1),
    "api_resource_served": (_INV_CUSTOM, {}, 1, 1, 1),
    "create_custom_resource": ((*_INV_CUSTOM, _INV_BODY), {"namespace": _INV_NS}, 1, 5, 5),
    "delete_custom_resource_preconditioned": ((*_INV_CUSTOM, "t1"), {"uid": "uid-t1", "namespace": _INV_NS}, 1, 1, 1),
    "delete_custom_resource": ((*_INV_CUSTOM, "t1"), {"namespace": _INV_NS}, 1, 5, 5),
    "list_managed_clusters": ((), {}, 1, 5, 5),
    "list_managed_clusters_strict": ((), {}, 2, 1, 1),
    "patch_managed_cluster": (("mc1", {"spec": {"hubAcceptsClient": True}}), {}, 1, 5, 5),
    "get_deployment": (("d1", _INV_NS), {}, 1, 5, 5),
    "get_statefulset": (("ss1", _INV_NS), {}, 1, 5, 5),
    "scale_deployment": (("d1", _INV_NS, 0), {}, 1, 5, 5),
    "scale_statefulset": (("ss1", _INV_NS, 0), {}, 1, 5, 5),
    "rollout_restart_deployment": (("d1", _INV_NS), {}, 1, 5, 5),
    "get_pods": ((_INV_NS,), {}, 1, 5, 5),
    "list_pods": ((_INV_NS,), {}, 1, 5, 5),
    "get_pod_logs": (("p1", _INV_NS), {"container": "c", "tail_lines": 10}, 1, 5, 5),
    # Polls every 5s of a 12s budget: three failed polls, then the budget is spent.
    "wait_for_pods_ready": ((_INV_NS, "app=x"), {"timeout": 12}, 1, 3, 3),
}

# Public methods that make no SDK request: name -> (reason, args, kwargs). Each is asserted at
# runtime to reach the transport zero times in every mode.
_INV_REQUEST_FREE = {}


def _every_other(answer):
    """A script whose even requests get `answer` and odd ones the mode: each retried attempt
    first succeeds partway, then fails on its later request."""
    return lambda index: answer if index % 2 == 0 else None


_CLUSTER = {"namespace": None}
# (method, scenario, mode, script, args, kwargs, requests): paths whose later requests matter.
_INV_SCRIPTED = [
    ("create_or_patch_configmap", "409-then-patch", "ok", {0: (409, _INV_STATUS)}.get, None, None, 2),
    ("create_or_patch_configmap", "409-then-patch-fails", "http", _every_other((409, _INV_STATUS)), None, None, 10),
    ("create_custom_resource", "409-then-reread", "ok", {0: (409, _INV_STATUS)}.get, None, None, 2),
    ("create_custom_resource", "409-then-reread-fails", "transport", _every_other((409, _INV_STATUS)), None, None, 10),
    ("create_custom_resource", "cluster-scoped", "ok", None, (*_INV_CUSTOM, _INV_CLUSTER_BODY), _CLUSTER, 1),
    ("create_custom_resource", "cluster-scoped-http", "http", None, (*_INV_CUSTOM, _INV_CLUSTER_BODY), _CLUSTER, 5),
    ("create_custom_resource", "unnamed-not-retried", "http", None, (*_INV_CUSTOM, {"kind": "Thing"}), None, 1),
    ("list_custom_resources", "second-page", "ok", {0: _INV_CONTINUED}.get, None, None, 2),
    ("list_custom_resources", "second-page-fails", "transport", _every_other(_INV_CONTINUED), None, None, 10),
    ("list_custom_resources", "cluster-scoped", "ok", None, None, _CLUSTER, 1),
    ("list_custom_resources_advisory", "second-page-fails", "http", _every_other(_INV_CONTINUED), None, None, 10),
    ("list_custom_resources_strict", "second-page", "ok", {1: _INV_CONTINUED}.get, None, None, 3),
    (
        "list_custom_resources_strict",
        "expired-continuation",
        "ok",
        {1: _INV_CONTINUED, 2: (410, _INV_STATUS)}.get,
        None,
        None,
        4,
    ),
    ("list_custom_resources_strict", "page-fails-after-discovery", "http", {0: "ok"}.get, None, None, 2),
    ("list_custom_resources_strict", "second-page-fails", "transport", {0: "ok", 1: _INV_CONTINUED}.get, None, None, 3),
    ("list_custom_resources_strict", "cluster-scoped", "ok", None, None, _CLUSTER, 2),
    ("get_custom_resource_strict", "get-fails-after-discovery", "transport", {0: "ok"}.get, None, None, 2),
    ("get_custom_resource_strict", "absent-after-discovery", "ok", {1: (404, _INV_STATUS)}.get, None, None, 2),
    ("get_custom_resource_strict", "cluster-scoped", "ok", None, None, _CLUSTER, 2),
    ("list_pods_strict", "second-page", "ok", {0: _INV_CONTINUED}.get, None, None, 2),
    ("list_pods_strict", "expired-continuation", "ok", {0: _INV_CONTINUED, 1: (410, _INV_STATUS)}.get, None, None, 3),
    ("list_pods_strict", "404-then-namespace-read", "ok", {0: (404, _INV_STATUS)}.get, None, None, 2),
    ("list_pods_strict", "404-then-namespace-read-fails", "transport", {0: (404, _INV_STATUS)}.get, None, None, 2),
    ("get_custom_resource", "cluster-scoped", "ok", None, None, _CLUSTER, 1),
    ("get_custom_resource_advisory", "cluster-scoped-http", "http", None, None, _CLUSTER, 5),
    ("patch_custom_resource", "cluster-scoped", "ok", None, None, _CLUSTER, 1),
    ("delete_custom_resource", "cluster-scoped", "ok", None, None, _CLUSTER, 1),
    ("delete_custom_resource_preconditioned", "cluster-scoped", "ok", None, None, _CLUSTER, 1),
    ("wait_for_pods_ready", "polls-until-ready", "ok", {0: (200, _inv_page([]))}.get, None, {"expected_count": 1}, 2),
    ("wait_for_pods_ready", "fails-then-ready", "ok", {0: "transport", 1: "http"}.get, None, None, 3),
]


_OTHER = "other"


def _answer_then_fail(*answers):
    """A script answering request i with `answers[i]`, and every later request by the mode."""
    return dict(enumerate(answers)).get


def _always(answer):
    return lambda _index: answer


def _status(code):
    return (code, {**_INV_STATUS, "code": code})


# Status branches (see "Status branches" below): each case answers one status where a branch of
# the source applies and fails every further request with the canary (mode "http"), so a
# request added inside that branch reaches the probe and is judged. (method, status label,
# scenario, script, args, kwargs, requests). A label is a status, a status range, or a
# `StrictReadStatus` member, exactly as `_status_sites` derives it, or `_OTHER`: a status the
# branch's test does not select, which takes the path after it.
_INV_BRANCHES = [
    # astra-r5 H1: the preconditioned DELETE's 409/412 handler, namespaced and cluster-scoped.
    ("delete_custom_resource_preconditioned", "409", "409-conflict", _answer_then_fail(_status(409)), None, None, 1),
    (
        "delete_custom_resource_preconditioned",
        "409",
        "409-conflict-cluster",
        _answer_then_fail(_status(409)),
        None,
        _CLUSTER,
        1,
    ),
    ("delete_custom_resource_preconditioned", "412", "412-conflict", _answer_then_fail(_status(412)), None, None, 1),
    (
        "delete_custom_resource_preconditioned",
        "412",
        "412-conflict-cluster",
        _answer_then_fail(_status(412)),
        None,
        _CLUSTER,
        1,
    ),
    ("delete_custom_resource_preconditioned", "404", "already-absent", _answer_then_fail(_status(404)), None, None, 1),
    ("json_patch_custom_resource_guarded", "409", "conflict", _answer_then_fail(_status(409)), None, None, 1),
    ("json_patch_custom_resource_guarded", "412", "precondition", _answer_then_fail(_status(412)), None, None, 1),
    ("json_patch_custom_resource_guarded", "422", "test-op-failed", _answer_then_fail(_status(422)), None, None, 1),
    ("json_patch_custom_resource_guarded", "404", "absent", _answer_then_fail(_status(404)), None, None, 1),
    ("delete_restore_guarded", "409", "conflict", _answer_then_fail(_status(409)), None, None, 1),
    ("delete_restore_guarded", "412", "precondition", _answer_then_fail(_status(412)), None, None, 1),
    ("delete_restore_guarded", "404", "absent", _answer_then_fail(_status(404)), None, None, 1),
    ("create_restore_guarded", "409", "name-taken", _answer_then_fail(_status(409)), None, None, 1),
    ("create_custom_resource", "409", "409-then-reread", _answer_then_fail(_status(409)), None, None, 6),
    (
        "create_custom_resource",
        "404",
        "409-then-reread-absent",
        _answer_then_fail(_status(409), _status(404)),
        None,
        None,
        2,
    ),
    ("create_or_patch_configmap", "409", "409-then-patch", _answer_then_fail(_status(409)), None, None, 6),
    ("get_namespace_strict", "404", "absent", _answer_then_fail(_status(404)), None, None, 1),
    ("get_deployment_strict", "404", "absent", _answer_then_fail(_status(404)), None, None, 1),
    ("get_replicaset_strict", "404", "absent", _answer_then_fail(_status(404)), None, None, 1),
    ("get_custom_resource_strict", "404", "absent", _answer_then_fail("ok", _status(404)), None, None, 2),
    ("get_custom_resource_strict", _OTHER, "fails-after-discovery", _answer_then_fail("ok"), None, None, 2),
    ("list_managed_clusters_strict", _OTHER, "fails-after-discovery", _answer_then_fail("ok"), None, None, 2),
    ("list_pods_strict", "404", "pods-404", _answer_then_fail(_status(404)), None, None, 2),
    (
        "list_pods_strict",
        "NAMESPACE_ABSENT",
        "pods-404-namespace-404",
        _answer_then_fail(_status(404), _status(404)),
        None,
        None,
        2,
    ),
    ("list_pods_strict", "410", "expired-continuation", _answer_then_fail(_INV_CONTINUED, _status(410)), None, None, 3),
    (
        "list_custom_resources_strict",
        "410",
        "expired-continuation",
        _answer_then_fail("ok", _INV_CONTINUED, _status(410)),
        None,
        None,
        4,
    ),
    (
        "list_managed_clusters_strict",
        "410",
        "expired-continuation",
        _answer_then_fail("ok", _INV_CONTINUED, _status(410)),
        None,
        None,
        4,
    ),
    (
        "list_optional_namespaced_resources_strict",
        "404",
        "discovery-404-index-failure",
        _answer_then_fail(_status(404)),
        None,
        None,
        2,
    ),
    (
        "list_optional_namespaced_resources_strict",
        "410",
        "expired-continuation",
        _answer_then_fail(
            "ok", (200, {"kind": "KindList", "apiVersion": "g.io/v1", **_INV_CONTINUED[1]}), _status(410)
        ),
        None,
        None,
        4,
    ),
    (
        "list_optional_namespaced_resources_strict",
        _OTHER,
        "fails-after-discovery",
        _answer_then_fail("ok"),
        None,
        None,
        2,
    ),
    ("wait_for_pods_ready", "404", "pods-404", _answer_then_fail(_status(404)), None, None, 3),
]
# Methods that answer a 404 with a not-found value (the `api_call` decorator's
# `not_found_value`, or their own raw helper's), in one request.
_INV_NOT_FOUND = (
    "get_namespace",
    "namespace_exists",
    "list_namespaces",
    "get_secret",
    "secret_exists",
    "get_configmap",
    "exists_configmap",
    "delete_configmap",
    "delete_pod",
    "get_route_host",
    "get_custom_resource",
    "get_custom_resource_advisory",
    "list_custom_resources",
    "list_custom_resources_advisory",
    "list_managed_clusters",
    "get_configmap_advisory",
    "delete_custom_resource",
    "get_deployment",
    "get_statefulset",
    "get_pods",
    "list_pods",
    "get_pod_logs",
)
# Methods that retry on a status: a 429 is retried like a 5xx (it is answered to every attempt),
# and a 403 is not retried, which takes the path after their retry branch.
_INV_RETRYING = (
    "get_namespace",
    "namespace_exists",
    "get_cluster_identity",
    "list_namespaces",
    "get_secret",
    "secret_exists",
    "get_configmap",
    "get_configmap_advisory",
    "exists_configmap",
    "create_or_patch_configmap",
    "delete_configmap",
    "delete_pod",
    "get_route_host",
    "get_custom_resource",
    "get_custom_resource_advisory",
    "list_custom_resources",
    "list_custom_resources_advisory",
    "patch_custom_resource",
    "create_custom_resource",
    "delete_custom_resource",
    "list_managed_clusters",
    "patch_managed_cluster",
    "get_deployment",
    "get_statefulset",
    "scale_deployment",
    "scale_statefulset",
    "rollout_restart_deployment",
    "get_pods",
    "list_pods",
    "get_pod_logs",
    "wait_for_pods_ready",
)


def _inv_branch_cases():
    """`_INV_BRANCHES` plus the 404, 429 and 403 cases, as (method, label, scenario, script,
    args, kwargs, requests)."""
    cases = list(_INV_BRANCHES)
    cases += [(m, "404", "404-not-found", _answer_then_fail(_status(404)), None, None, 1) for m in _INV_NOT_FOUND]
    cases += [
        (m, "429", "429-rate-limited", _always(_status(429)), None, None, _INV_DRIVES[m][3]) for m in _INV_RETRYING
    ]
    # get_cluster_identity's retry decision is tenacity's alone: no branch of its own follows it.
    not_retried = [m for m in _INV_RETRYING if m != "get_cluster_identity"]
    cases += [(m, _OTHER, "403-not-retried", _answer_then_fail(_status(403)), None, None, 1) for m in not_retried]
    return cases


def _inv_cases():
    """Every runtime case as (method, scenario, mode, script, args, kwargs, requests)."""
    cases = []
    for method, (args, kwargs, ok, http, transport) in _INV_DRIVES.items():
        for mode, requests in (("ok", ok), ("http", http), ("transport", transport)):
            cases.append((method, mode, mode, None, args, kwargs, requests))
    scripted = list(_INV_SCRIPTED)
    scripted += [(m, f"branch-{scenario}", "http", *rest) for m, _label, scenario, *rest in _inv_branch_cases()]
    for method, scenario, mode, script, args, kwargs, requests in scripted:
        base_args, base_kwargs = _INV_DRIVES[method][:2]
        merged = {**base_kwargs, **(kwargs or {})}
        cases.append((method, scenario, mode, script, base_args if args is None else args, merged, requests))
    return cases


# (method, scenario) -> the status label that case is declared to drive.
_INV_BRANCH_LABELS = {(m, f"branch-{scenario}"): label for m, label, scenario, *_rest in _inv_branch_cases()}


_INV_CASES = _inv_cases()
_INV_CONSTRUCTIONS = {
    "default": {},
    "named-context": {"context": "c1"},
    "dry-run": {"context": "c1", "dry_run": True},
    "insecure-hostname": {"disable_hostname_verification": True},
    "request-timeout": {"request_timeout": 5},
    "quiet-config-errors": {"log_config_errors": False},
}


# ---------------------------------------------------------------------------
# Status branches: every branch the source takes on a status is driven
# ---------------------------------------------------------------------------
#
# The runtime invariant judges only the requests it drives: a request added inside a
# status-specific handler that no case reaches passes it (astra-r5 H1). So the handlers are
# derived from the source. A status site is an `if` whose test compares some `.status` with a
# constant, a range or a membership set (an HTTP status, or a `StrictReadStatus` member) or
# branches on `is_retryable_error(...)`, plus `is_retryable_error`'s own status classes. A public
# method owns every site in the code it can reach: methods and module functions it names,
# decorators included. An `if` site's labels are the statuses its test selects plus `_OTHER`, a
# status it does not select, which takes the path after it (the code after `if
# is_retryable_error(e): raise`, an `else`). For each derived (public method, site, label), some
# case must answer that status where the test applies and every further request with a canary
# failure, and the site must be seen taking that side with that status. A request added on
# either side of the test then reaches the probe.

_RETRY_DECISION = "is_retryable_error"
_STATUS_SITE_TOOL = "kube-client-status-sites"
# Status comparisons that are not a branch a public method takes outside the boundary, and why.
# A change here is a reviewed change to what the status-branch property leaves out.
_EXCLUDED_STATUS_COMPARISONS = {
    "_transport_category: current.status == 0": "only _call_sdk reaches it",
    "_is_unsupported_content_type: exc.status == 0": "only _call_sdk reaches it",
    "_malformed_body: http_error.status > 0": "only _call_sdk reaches it",
    "_malformed_body: 200 <= http_error.status <= 299": "only _call_sdk reaches it",
    "_request_bytes: 200 <= raw.status <= 299": "inside a _call_sdk operation",
    "wait_for_pods_ready: is_retryable_error(exc)": "an earlier handler takes every ApiException",
}


class _StatusSite(NamedTuple):
    owner: str  # the module function or KubeClient method containing the site
    key: str  # "owner: test", stable across line moves
    labels: Tuple[str, ...]
    lines: FrozenSet[int]  # executing any of these proves the branch was entered
    test_lines: FrozenSet[int]  # the `if` test's lines: where a status it does not select is seen
    operand: Any  # compiled: the status (or, for a retry decision, the exception) branched on
    of_exception: bool  # the operand is an exception whose `.status` is read if it is an ApiException


def _unclassified(owner, node):
    raise AssertionError(
        f"{owner}:{node.lineno} compares a status in a shape the deriver does not read: {ast.unparse(node)}"
    )


def _status_labels(owner, compare):
    """The labels a `.status` comparison selects: '404', a range '500-599', or a member name."""
    operands = [compare.left, *compare.comparators]
    if len(compare.ops) == 2 and all(isinstance(op, (ast.Lt, ast.LtE)) for op in compare.ops):
        low, _status_operand, high = operands
        if not all(isinstance(bound, ast.Constant) and type(bound.value) is int for bound in (low, high)):
            _unclassified(owner, compare)
        low = low.value + isinstance(compare.ops[0], ast.Lt)
        high = high.value - isinstance(compare.ops[1], ast.Lt)
        return (f"{low}-{high}",)
    if len(compare.ops) != 1:
        _unclassified(owner, compare)
    op, (left, right) = compare.ops[0], operands
    if isinstance(op, ast.Eq) and isinstance(left, ast.Constant):
        left, right = right, left
    if isinstance(op, ast.Eq) and isinstance(right, ast.Constant) and type(right.value) is int:
        return (str(right.value),)
    if isinstance(op, ast.In):
        if isinstance(right, (ast.Tuple, ast.Set, ast.List)) and all(isinstance(e, ast.Constant) for e in right.elts):
            values = [element.value for element in right.elts]
        elif isinstance(right, ast.Name):
            values = list(getattr(kube_client_module, right.id))
        else:
            _unclassified(owner, compare)
        if not all(type(value) is int for value in values):
            _unclassified(owner, compare)
        return tuple(str(value) for value in sorted(values))
    if isinstance(op, (ast.Is, ast.IsNot)) and isinstance(right, ast.Attribute):
        return (right.attr,) if isinstance(op, ast.Is) else (f"not {right.attr}",)
    _unclassified(owner, compare)


def _label_holds(label, observed):
    if isinstance(observed, StrictReadStatus):
        return label == observed.name or (label.startswith("not ") and label[4:] != observed.name)
    if type(observed) is not int:
        return False
    if "-" in label:
        low, high = map(int, label.split("-"))
        return low <= observed <= high
    return label == str(observed)


def _branch_of(node, parents):
    """The `if` whose test is `node`, alone or as one operand of an `and`, else None."""
    while isinstance(parents.get(node), ast.BoolOp) and isinstance(parents[node].op, ast.And):
        node = parents[node]
    parent = parents.get(node)
    return parent if isinstance(parent, ast.If) and parent.test is node else None


def _receives_no_api_exception(node, parents):
    """Whether `node` sits in an `except Exception` handler that an earlier `except ApiException`
    handler of the same `try` already relieves of every ApiException."""
    while node in parents and not isinstance(node, ast.ExceptHandler):
        node = parents[node]
    if not isinstance(node, ast.ExceptHandler) or node.type is None or ast.unparse(node.type) != "Exception":
        return False
    handlers = parents[node].handlers
    earlier = handlers[: handlers.index(node)]
    return any(h.type is not None and "ApiException" in ast.unparse(h.type) for h in earlier)


def _is_returned(node, parents):
    """Whether `node` is the returned value, alone or as one operand of an `or`."""
    while isinstance(parents.get(node), ast.BoolOp) and isinstance(parents[node].op, ast.Or):
        node = parents[node]
    return isinstance(parents.get(node), ast.Return)


def _owners(tree):
    """Module functions, module-level names and KubeClient methods, by name."""
    module, methods = {}, {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            module[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(target, ast.Name):
                    module[target.id] = node
        elif isinstance(node, ast.ClassDef) and node.name == "KubeClient":
            methods = {m.name: m for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert not set(module) & set(methods), "a method shares a module function's name"
    return module, methods


def _references(node, module, methods):
    """The owners `node` names: module names it loads, and `self.<method>` it reaches."""
    found = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load) and child.id in module:
            found.add(child.id)
        elif (
            isinstance(child, ast.Attribute)
            and isinstance(child.value, ast.Name)
            and child.value.id == "self"
            and child.attr in methods
        ):
            found.add(child.attr)
    return found


def _status_sites(source):
    """(sites, excluded, reachable) for lib/kube_client.py.

    `excluded` maps each status comparison that is not a branch a public method's own code takes
    outside the boundary to why: it runs inside a `_call_sdk` operation, or in a function only
    `_call_sdk` reaches (its failure classification, while the boundary flag is still set), or it
    is a retry decision that receives no ApiException. Any other status comparison in a shape the
    deriver does not read fails. `reachable` maps each public method to the owners it reaches."""
    tree = ast.parse(source)
    module, methods = _owners(tree)
    owners = {**module, **methods}
    references = {name: _references(node, module, methods) for name, node in owners.items()}

    def closure(start):
        seen, pending = set(), [start]
        while pending:
            name = pending.pop()
            if name not in seen:
                seen.add(name)
                pending.extend(references[name])
        return seen

    boundary = closure("_call_sdk") - {"_call_sdk"}
    while True:
        referenced_elsewhere = set().union(*(references[n] for n in owners if n not in boundary | {"_call_sdk"}))
        if not boundary & referenced_elsewhere:
            break
        boundary -= referenced_elsewhere

    sites, excluded = [], {}
    for owner, node in owners.items():
        parents = {child: parent for parent in ast.walk(node) for child in ast.iter_child_nodes(parent)}
        operations = _operations(node)
        for child in _status_nodes(node):
            where = f"{owner}: {ast.unparse(child)}"
            if _inside_boundary(child, parents, operations):
                excluded[where] = "inside a _call_sdk operation"
            elif owner in boundary:
                excluded[where] = "only _call_sdk reaches it"
            elif isinstance(child, ast.Call) and _receives_no_api_exception(child, parents):
                excluded[where] = "an earlier handler takes every ApiException"
            else:
                site = _status_site(owner, child, parents)
                if site is not None:
                    taken = sum(other.key == site.key or other.key.startswith(site.key + " #") for other in sites)
                    sites.append(site._replace(key=f"{site.key} #{taken + 1}") if taken else site)
    retry_labels = tuple(label for site in sites if site.owner == _RETRY_DECISION for label in site.labels)
    assert retry_labels, f"{_RETRY_DECISION} retries no status class the deriver can read"
    sites = [site._replace(labels=retry_labels) if site.labels is None else site for site in sites]

    public = {name for name in methods if not name.startswith("_")}
    reachable = {name: closure(name) for name in public}
    return sites, excluded, reachable


def _status_nodes(node):
    """Every `.status` comparison and every `is_retryable_error(...)` call under `node`, in
    source order."""
    found = [
        child
        for child in ast.walk(node)
        if (
            isinstance(child, ast.Compare)
            and any(isinstance(o, ast.Attribute) and o.attr == "status" for o in (child.left, *child.comparators))
        )
        or (isinstance(child, ast.Call) and ast.unparse(child.func) == _RETRY_DECISION)
    ]
    return sorted(found, key=lambda child: (child.lineno, child.col_offset))


def _status_site(owner, child, parents):
    """The site `child` makes: an `if` branching on it, or one of `is_retryable_error`'s own
    status classes. None for the retry decision handed to tenacity (`_should_retry` returns it).
    A retry branch's labels are filled in from `is_retryable_error` afterwards."""
    branch = _branch_of(child, parents)
    if isinstance(child, ast.Call):
        if branch is None and _is_returned(child, parents):
            return None
        if branch is None:
            _unclassified(owner, child)
        operand, of_exception, labels = child.args[0], True, None
    else:
        operand = next(o for o in (child.left, *child.comparators) if isinstance(o, ast.Attribute))
        of_exception, labels = False, _status_labels(owner, child)
        if branch is None and owner == _RETRY_DECISION and _is_returned(child, parents):
            # The decision itself: each status class it retries, seen where it is computed.
            key = f"{owner}: {ast.unparse(child)}"
            return _StatusSite(owner, key, labels, frozenset({child.lineno}), frozenset(), _compiled(operand), False)
        if branch is None:
            _unclassified(owner, child)
    first = branch.body[0]
    lines = frozenset(range(first.lineno, first.end_lineno + 1))
    test_lines = frozenset(range(branch.test.lineno, branch.test.end_lineno + 1))
    key = f"{owner}: {ast.unparse(branch.test)}"
    return _StatusSite(owner, key, labels, lines, test_lines, _compiled(operand), of_exception)


def _compiled(expression):
    return compile(ast.Expression(body=expression), kube_client_module.__file__, "eval")


def _derived_status_branches():
    """Every (public method, site key, label) the source treats specially."""
    sites, _excluded, reachable = _status_sites(_kube_client_source())
    return {
        (method, site.key, label)
        for method, owners in reachable.items()
        for site in sites
        if site.owner in owners
        for label in ((*site.labels, _OTHER) if site.test_lines else site.labels)
    }


class _BranchRecorder:
    """While active, records the status sites lib/kube_client.py enters as (site key, label,
    requests made so far): on a branch's body, each label its status operand holds there; on
    an `if` test, `_OTHER` when the operand is a status that holds none of them."""

    def __init__(self, sites):
        self.probe = None
        self._by_line = {}
        for site in sites:
            for line in site.lines:
                self._by_line.setdefault(line, []).append((site, True))
            for line in site.test_lines:
                self._by_line.setdefault(line, []).append((site, False))
        self.entered = set()

    def on_line(self, frame, line):
        for site, body in self._by_line.get(line, ()):
            try:
                observed = eval(site.operand, frame.f_globals, frame.f_locals)  # noqa: S307 - our own source
            except Exception:  # noqa: BLE001 - not this site's frame (a lambda on a body line)
                continue
            if site.of_exception:
                if not isinstance(observed, ApiException):
                    continue
                observed = observed.status
            if not isinstance(observed, (int, StrictReadStatus)):
                continue
            held = [label for label in site.labels if _label_holds(label, observed)]
            for label in held if body else ([] if held else [_OTHER]):
                self.entered.add((site.key, label, len(self.probe.requests)))


def _kube_client_code_objects():
    """Every code object compiled from lib/kube_client.py: module functions and KubeClient
    methods under their decorators, and every function, lambda and comprehension inside them."""
    filename = kube_client_module.__file__
    pending = []
    for value in (*vars(kube_client_module).values(), *vars(KubeClient).values()):
        value = getattr(value, "__func__", value)
        if callable(value):
            value = inspect.unwrap(value)
        code = getattr(value, "__code__", None)
        if code is not None and code.co_filename == filename:
            pending.append(code)
    found = set()
    while pending:
        code = pending.pop()
        if code not in found:
            found.add(code)
            pending.extend(const for const in code.co_consts if inspect.iscode(const))
    return found


@contextlib.contextmanager
def _recording(recorder):
    """LINE events on lib/kube_client.py's code through `sys.monitoring` (Python 3.12+), or
    through `sys.settrace` when no other tracer owns it; otherwise the test is skipped."""
    monitoring = getattr(sys, "monitoring", None)
    if monitoring is not None:
        tool = next(t for t in (4, 3) if monitoring.get_tool(t) is None)  # neither a debugger's nor coverage's
        codes = _kube_client_code_objects()
        monitoring.use_tool_id(tool, _STATUS_SITE_TOOL)
        try:
            monitoring.register_callback(
                tool, monitoring.events.LINE, lambda _code, line: recorder.on_line(sys._getframe(1), line)
            )
            for code in codes:
                monitoring.set_local_events(tool, code, monitoring.events.LINE)
            yield
        finally:
            for code in codes:
                monitoring.set_local_events(tool, code, 0)
            monitoring.register_callback(tool, monitoring.events.LINE, None)
            monitoring.free_tool_id(tool)
        return
    if sys.gettrace() is not None:
        pytest.skip(f"Python {sys.version_info[:2]} has no sys.monitoring and another tracer owns sys.settrace")
    filename = kube_client_module.__file__

    def local(frame, event, _arg):
        if event == "line":
            recorder.on_line(frame, frame.f_lineno)
        return local

    sys.settrace(lambda frame, _event, _arg: local if frame.f_code.co_filename == filename else None)
    try:
        yield
    finally:
        sys.settrace(None)


@pytest.mark.unit
class TestRuntimeBoundaryInvariant:
    @pytest.fixture(autouse=True)
    def _isolated(self, monkeypatch):
        """A fake clock for polling; and a request sent through any transport but the probe (a
        second ApiClient, a bare urllib3 pool) is recorded in `self.foreign` and fails, never
        reaching a socket."""
        monkeypatch.setattr(kube_client_module, "time", _FakeClock())
        self.foreign = []

        def urlopen(pool, method, url, *_args, **_kwargs):
            self.foreign.append(f"{method} {pool.host}{url} (not through the probed client)")
            raise urllib3.exceptions.ProtocolError("request outside the probed transport")

        monkeypatch.setattr(urllib3.connectionpool.HTTPConnectionPool, "urlopen", urlopen)

    def test_the_probe_sees_a_request_made_outside_the_boundary(self):
        """Positive control: the same read is seen unset raw and set through KubeClient."""
        probe = _BoundaryProbe("ok")
        client = _probed_construction(probe, context="inv")
        client.core_v1.read_namespace(_INV_NS)
        assert [active for _, _, active in probe.requests] == [False]
        assert probe.reads and not any(probe.reads)

        probe = _BoundaryProbe("ok")
        _probed_construction(probe, context="inv").get_namespace(_INV_NS)
        assert [active for _, _, active in probe.requests] == [True]
        assert probe.reads and all(probe.reads)

        for mode in ("http", "transport"):
            raw = _raise(lambda c: c.core_v1.read_namespace(_INV_NS), _probed_construction(_BoundaryProbe(mode)))
            assert any(CANARY in text for text in _rendered(raw)), f"precondition: a raw {mode} failure carries text"

        foreign = ApiClient(Configuration(host="http://foreign.invalid"))
        _raise(lambda: kubernetes.client.CoreV1Api(foreign).read_namespace(_INV_NS))
        assert self.foreign, "a request through another client is seen too"

    @pytest.mark.parametrize("mode", ["ok", "http", "transport"])
    @pytest.mark.parametrize("path", sorted(_INV_CONSTRUCTIONS))
    def test_construction_makes_no_request_outside_the_boundary(self, path, mode):
        probe = _BoundaryProbe(mode)
        raised = None
        try:
            _probed_construction(probe, **_INV_CONSTRUCTIONS[path])
        except Exception as exc:  # the invariant is judged first, whatever construction raised
            raised = exc
        outside = probe.outside() + self.foreign
        assert outside == [], f"KubeClient.__init__ ({path}, {mode}) reached the transport outside _call_sdk"
        assert raised is None and probe.requests == [], "the constructor issues no request"

    @pytest.mark.parametrize("log_config_errors", [True, False])
    def test_a_kubeconfig_failure_makes_no_request(self, log_config_errors):
        from kubernetes.config.config_exception import ConfigException

        failing = patch.object(kube_client_module.config, "new_client_from_config", side_effect=ConfigException("x"))
        with failing, pytest.raises(ConfigException):
            KubeClient(context="c1", log_config_errors=log_config_errors)
        assert self.foreign == [], "KubeClient.__init__ (kubeconfig failure) reached a transport"

    @pytest.mark.parametrize(
        "method,scenario,mode,script,args,kwargs,requests",
        [pytest.param(*case, id=f"{case[0]}-{case[1]}") for case in _INV_CASES],
    )
    def test_every_request_is_made_inside_the_boundary(
        self, caplog, method, scenario, mode, script, args, kwargs, requests
    ):
        with caplog.at_level(logging.DEBUG):
            probe, result, raised = _drive(method, mode, script, args, kwargs)
        outside = probe.outside() + self.foreign
        assert outside == [], f"KubeClient.{method} ({scenario}) reached the transport outside _call_sdk"
        assert len(probe.requests) == requests, f"KubeClient.{method} ({scenario}) did not drive the expected path"
        if mode == "ok":
            assert raised is None, f"KubeClient.{method} ({scenario}) failed on its success path: {raised!r}"
        # A request made inside the boundary whose raw failure is kept and released afterwards.
        released = _rendered(raised) if raised is not None else [repr(result)]
        assert not any(
            CANARY in text for text in (*released, caplog.text)
        ), f"KubeClient.{method} ({scenario}) let server or transport text out of _call_sdk"

    def test_status_sites_are_derived_and_every_branch_case_names_one(self):
        """Static half: the source is read without an unclassified status comparison, the
        comparisons left out are the reviewed ones, every site is on code the recorder watches,
        and every branch case names a (method, status) pair the source still has."""
        sites, excluded, _reachable = _status_sites(_kube_client_source())
        assert excluded == _EXCLUDED_STATUS_COMPARISONS
        watched = {line for code in _kube_client_code_objects() for *_span, line in code.co_lines() if line}
        assert all(site.lines & watched for site in sites), "a status site is on code the recorder does not watch"
        named = {(method, label) for method, _key, label in _derived_status_branches()}
        stale = sorted(pair for pair in _INV_BRANCH_LABELS.items() if (pair[0][0], pair[1]) not in named)
        assert stale == [], f"branch cases for a (method, status) the source no longer treats specially: {stale}"

    def test_every_status_branch_is_driven(self):
        """Runtime half: every derived (public method, site, status) is entered by some case at
        that status while every further request fails with the canary, no case enters a site
        its method cannot reach statically, and every branch case enters its declared status."""
        sites, _excluded, _reachable = _status_sites(_kube_client_source())
        derived = _derived_status_branches()
        driven, idle = set(), []
        for method, scenario, mode, script, args, kwargs, _requests in _INV_CASES:
            if mode == "ok":
                continue  # its further requests succeed: nothing it enters counts
            self.foreign.clear()
            recorder = _BranchRecorder(sites)
            probe, _result, _raised = _drive(method, mode, script, args, kwargs, recorder)
            armed = {(method, key, label) for key, label, made in recorder.entered if probe.fails_from(made)}
            driven |= armed
            declared = _INV_BRANCH_LABELS.get((method, scenario))
            if declared is not None and not any(label == declared for _m, _k, label in armed):
                idle.append(f"{method} ({scenario}): {declared}")
        assert idle == [], f"branch cases that do not enter their declared status branch: {idle}"
        assert driven - derived == set(), "a case entered a status site its method does not reach statically"
        undriven = sorted(derived - driven)
        assert undriven == [], "status branches no case drives with every further request failing:\n" + "\n".join(
            map(str, undriven)
        )

    def test_every_public_method_is_driven_or_justified(self):
        public = {
            name
            for name, member in vars(KubeClient).items()
            if not name.startswith("_") and (inspect.isroutine(member) or isinstance(member, property))
        }
        driven = {}
        for method, scenario, mode, *_rest, requests in _INV_CASES:
            assert requests >= 1, f"KubeClient.{method} ({scenario}) drives no request; justify it as request-free"
            driven.setdefault(method, set()).add(mode)
        assert set(driven) <= public, f"cases for methods KubeClient lacks: {sorted(set(driven) - public)}"
        assert set(_INV_REQUEST_FREE) <= public and not set(_INV_REQUEST_FREE) & set(driven)
        unaccounted = sorted(public - set(driven) - set(_INV_REQUEST_FREE))
        assert unaccounted == [], f"public KubeClient methods neither driven nor justified: {unaccounted}"
        partial = {method: sorted(modes) for method, modes in driven.items() if modes != {"ok", "http", "transport"}}
        assert partial == {}, "every driven method needs a success, an HTTP error and a transport error"
        for name, (_reason, args, kwargs) in _INV_REQUEST_FREE.items():
            for mode in ("ok", "http", "transport"):
                probe = _BoundaryProbe(mode)
                getattr(_probed_construction(probe, context="inv"), name)(*args, **kwargs)
                assert probe.requests == [], f"KubeClient.{name} is listed as request-free but reached the transport"
