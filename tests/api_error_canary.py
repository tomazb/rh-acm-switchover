"""Shared fixtures for #283: API error detail must never leave `KubeClient`.

`canary_api_exception` builds an `ApiException` the way the generated client does, from an
HTTP response, so the canaries sit in the body, the headers, the reason and (37.x) `data`.
It is built with keywords only and works on every supported client: 29.x reads
`getheaders()` and takes no `body`/`data` keywords, 37.x reads `headers`.
"""

import contextlib
import json
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import MagicMock, patch

from kubernetes.client.rest import ApiException

API_BODY_CANARY = "i283-api-body-canary-BD91"
API_HEADER_CANARY = "i283-api-header-canary-HD92"
API_DATA_CANARY = "i283-api-data-canary-DT93"
API_REASON_CANARY = "i283-api-reason-canary-RS94"
API_CANARIES = (API_BODY_CANARY, API_HEADER_CANARY, API_DATA_CANARY, API_REASON_CANARY)


def canary_api_exception(status: int) -> ApiException:
    """An `ApiException` carrying a canary in every server-controlled field."""
    headers = {"Content-Type": "application/json", "Audit-Id": API_HEADER_CANARY}
    http_resp = SimpleNamespace(
        status=status,
        reason=API_REASON_CANARY,
        data=json.dumps({"kind": "Status", "message": API_BODY_CANARY}).encode("utf-8"),
        headers=headers,
        getheaders=lambda: headers,
    )
    exc = ApiException(http_resp=http_resp)
    exc.data = {"kind": "Status", "message": API_DATA_CANARY}
    return exc


def assert_no_canary(*texts: str) -> None:
    """Fail when any API canary appears in any of `texts`."""
    for text in texts:
        for canary in API_CANARIES:
            assert canary not in text, f"{canary} leaked"


@contextlib.contextmanager
def real_kube_client_apis() -> Iterator[dict]:
    """Patch only the generated API classes so a real `KubeClient` can be built.

    Mirrors `tests/test_kube_client.py::mock_k8s_apis`: a mocked `KubeClient` would bypass
    the error boundary under test, so route tests must raise from the real one.
    """
    with patch("lib.kube_client.config.new_client_from_config") as new_client, patch(
        "lib.kube_client.client.CustomObjectsApi"
    ) as custom_cls, patch("lib.kube_client.client.CoreV1Api") as core_cls, patch(
        "lib.kube_client.client.AppsV1Api"
    ) as apps_cls:
        api_client = MagicMock(name="api_client", spec=["call_api", "configuration"])
        api_client.configuration = MagicMock(name="api_client_configuration")
        new_client.return_value = api_client
        yield {
            "custom_api": custom_cls.return_value,
            "core_api": core_cls.return_value,
            "apps_api": apps_cls.return_value,
        }
