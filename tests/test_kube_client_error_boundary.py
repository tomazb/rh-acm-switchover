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
from tests.test_kube_client import _real_typed_kube_client, _RecordingPoolManager

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


def _configured_client(connect_failure):
    """A KubeClient built by `__init__` over the SDK's own pool; only `connect()` is replaced.

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

    @_ONLY_BEFORE_37
    def test_a_successful_response_body_is_not_logged_by_the_sdk(self, caplog):
        """kubernetes 29.x logs every preloaded response body at DEBUG, successes included."""
        secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "s1"}, "data": {"k": CANARY}}
        with caplog.at_level(logging.DEBUG):
            _real_typed_kube_client(_RecordingPoolManager(payload=secret)).core_v1.read_namespaced_secret("s1", "ns1")
        assert CANARY in caplog.text, "precondition: the SDK logs the body"

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
        with caplog.at_level(logging.DEBUG):
            _raise(lambda c: c.core_v1.read_namespace("ns1"), client)
        assert CANARY in caplog.text, "precondition: urllib3 logs the connect failure"

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

        def inner():
            emit("nested-" + CANARY)

        def outer():
            emit("owned-" + CANARY)
            call_sdk(inner)
            emit("after-nested-" + CANARY)
            thread = threading.Thread(target=other_thread)
            thread.start()
            thread.join()
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
        assert "unrelated-thread" in messages and "project-logger" in messages
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
            (lambda: OSError(errno.EACCES, CANARY), 1),
            (lambda: OSError(CANARY), 1),
        ],
        ids=["econnreset", "etimedout", "eacces", "no-errno"],
    )
    def test_network_and_other_os_errors_keep_their_retry_decision(self, kube_client, mock_k8s_apis, failure, attempts):
        def boom(*_args, **_kwargs):
            raise failure()

        mock_k8s_apis["core_api"].read_namespace.side_effect = boom

        raised = _raise(kube_client.get_namespace, "ns1")

        assert isinstance(raised, OSError) and raised.errno == failure().errno
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
# Configuration loading and API-object construction, not hub requests.
_EXEMPT_METHODS = {"__init__"}
# One entry per hub SDK step in KubeClient, as `_sdk_step` counts them. A change here is a
# reviewed change to the set of operations the boundary owns.
_EXPECTED_INVENTORY_SIZE = 45


def _is_sdk_root(node):
    return (
        isinstance(node, ast.Attribute)
        and node.attr in _SDK_ROOTS
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _is_boundary_call(node):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_call_sdk"


def _operations(method):
    """The closures `method` hands to `_call_sdk`: a lambda passed as its first argument, or a
    nested function passed by name as that argument and referenced nowhere else."""
    operations = set()
    for node in ast.walk(method):
        if not (_is_boundary_call(node) and node.args):
            continue
        operation = node.args[0]
        if isinstance(operation, ast.Lambda):
            operations.add(operation)
        elif isinstance(operation, ast.Name):
            loads = [n for n in ast.walk(method) if isinstance(n, ast.Name) and n.id == operation.id]
            nested = [n for n in ast.walk(method) if isinstance(n, ast.FunctionDef) and n.name == operation.id]
            if len(loads) == 1 and len(nested) == 1 and nested[0] is not method:
                operations.add(nested[0])
    return operations


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
    while isinstance(root, ast.Attribute) and not _is_sdk_root(root):
        root = root.value
    callee = ast.unparse(node.func)
    if _is_sdk_root(root) or callee in ("legacy_call_api", "raw.read"):
        return callee
    return None


def _boundary_violations(source):
    """(violations, inventory) for KubeClient: every use of an SDK object outside an operation
    handed to `_call_sdk`, and the inventory of SDK steps outside the exempt methods."""
    tree = ast.parse(source)
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "KubeClient")
    violations, inventory = [], []
    for method in klass.body:
        if not isinstance(method, ast.FunctionDef) or method.name in _EXEMPT_METHODS:
            continue
        parents = {child: parent for parent in ast.walk(method) for child in ast.iter_child_nodes(parent)}
        operations = _operations(method)

        def guarded(node):
            while node in parents:
                node = parents[node]
                if node in operations:
                    return True
            return False

        for node in ast.walk(method):
            step = _sdk_step(node)
            if step is not None:
                inventory.append(f"{method.name}: {step}")
            if _is_sdk_root(node) and not guarded(node):
                violations.append(f"{method.name}:{node.lineno} uses self.{node.attr} outside _call_sdk")
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
        )
        violations, inventory = _boundary_violations(source)
        assert violations == [
            "get:3 uses self.core_v1 outside _call_sdk",
            "aliased:5 uses self.core_v1 outside _call_sdk",
            "nested:11 uses self._api_client outside _call_sdk",
        ]
        assert len(inventory) == 3
