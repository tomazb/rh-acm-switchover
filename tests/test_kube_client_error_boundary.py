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
import errno
import io
import json
import logging
import socket
import ssl
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import kubernetes
import pytest
import urllib3
from kubernetes.client import ApiClient, Configuration
from kubernetes.client.rest import ApiException

import lib.kube_client as kube_client_module
from lib.exceptions import MalformedResponseError, PreconditionConflict, TargetDisappeared
from lib.kube_client import KubeClient, is_retryable_error
from lib.strict_read import StrictReadStatus
from tests.api_error_canary import real_kube_client_apis
from tests.test_kube_client import _real_typed_kube_client, _RecordingPoolManager, _UnreadableBody

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
        "Kubernetes API request failed",
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
