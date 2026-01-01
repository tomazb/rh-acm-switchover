"""
Tests for reliability features (retries, timeouts).
"""

from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.rest import ApiException
from urllib3.exceptions import HTTPError

from lib.kube_client import KubeClient, is_retryable_error


@pytest.fixture
def kube_client():
    """Fixture to provide a mocked KubeClient."""
    with (
        patch("kubernetes.config.load_kube_config"),
        patch("kubernetes.client.CoreV1Api"),
        patch("kubernetes.client.AppsV1Api"),
        patch("kubernetes.client.CustomObjectsApi"),
    ):
        yield KubeClient(context="test-context")


def test_is_retryable_error():
    """Test retryable error detection."""
    # Retryable errors
    assert is_retryable_error(ApiException(status=500))
    assert is_retryable_error(ApiException(status=503))
    assert is_retryable_error(ApiException(status=504))
    assert is_retryable_error(ApiException(status=429))
    assert is_retryable_error(HTTPError())

    # Non-retryable errors
    assert not is_retryable_error(ApiException(status=400))
    assert not is_retryable_error(ApiException(status=401))
    assert not is_retryable_error(ApiException(status=403))
    assert not is_retryable_error(ApiException(status=404))
    assert not is_retryable_error(ValueError())


@patch("tenacity.nap.sleep")
def test_retry_logic_success_after_failure(mock_sleep, kube_client):
    """Test that API call retries and eventually succeeds."""
    # Mock API to fail twice then succeed
    mock_api = kube_client.core_v1.read_namespace
    mock_api.side_effect = [
        ApiException(status=503),
        ApiException(status=500),
        MagicMock(to_dict=lambda: {"metadata": {"name": "test"}}),
    ]

    # Call method
    result = kube_client.get_namespace("test")

    # Verify result
    assert result["metadata"]["name"] == "test"

    # Verify retries occurred (3 calls total)
    assert mock_api.call_count == 3


@patch("tenacity.nap.sleep")
def test_retry_logic_max_retries_exceeded(mock_sleep, kube_client):
    """Test that API call fails after max retries."""
    # Mock API to fail consistently
    mock_api = kube_client.core_v1.read_namespace
    mock_api.side_effect = ApiException(status=503)

    # Call method and expect failure
    with pytest.raises(ApiException):
        kube_client.get_namespace("test")

    # Verify retries occurred (initial + 4 retries = 5 calls)
    assert mock_api.call_count == 5


def test_non_retryable_error_fails_immediately(kube_client):
    """Test that non-retryable errors fail immediately."""
    # Mock API to fail with 403
    mock_api = kube_client.core_v1.read_namespace
    mock_api.side_effect = ApiException(status=403)

    # Call method and expect failure
    with pytest.raises(ApiException):
        kube_client.get_namespace("test")

    # Verify no retries (1 call total)
    assert mock_api.call_count == 1


def test_404_returns_none_without_retry(kube_client):
    """Test that 404 returns None immediately without retry."""
    # Mock API to fail with 404
    mock_api = kube_client.core_v1.read_namespace
    mock_api.side_effect = ApiException(status=404)

    # Call method
    result = kube_client.get_namespace("test")

    # Verify result is None
    assert result is None

    # Verify no retries (1 call total)
    assert mock_api.call_count == 1
