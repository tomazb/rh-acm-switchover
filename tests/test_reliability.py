"""
Tests for reliability features (retries, timeouts).
"""

import unittest
from unittest.mock import MagicMock, patch

from kubernetes.client.rest import ApiException
from urllib3.exceptions import HTTPError

from lib.kube_client import KubeClient, is_retryable_error


class TestReliability(unittest.TestCase):
    """Test reliability features."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_config_load = patch("kubernetes.config.load_kube_config").start()
        self.mock_core_v1 = patch("kubernetes.client.CoreV1Api").start()
        self.mock_apps_v1 = patch("kubernetes.client.AppsV1Api").start()
        self.mock_custom_api = patch("kubernetes.client.CustomObjectsApi").start()

        # Initialize client
        self.client = KubeClient(context="test-context")

    def tearDown(self):
        """Tear down test fixtures."""
        patch.stopall()

    def test_is_retryable_error(self):
        """Test retryable error detection."""
        # Retryable errors
        self.assertTrue(is_retryable_error(ApiException(status=500)))
        self.assertTrue(is_retryable_error(ApiException(status=503)))
        self.assertTrue(is_retryable_error(ApiException(status=504)))
        self.assertTrue(is_retryable_error(ApiException(status=429)))
        self.assertTrue(is_retryable_error(HTTPError()))

        # Non-retryable errors
        self.assertFalse(is_retryable_error(ApiException(status=400)))
        self.assertFalse(is_retryable_error(ApiException(status=401)))
        self.assertFalse(is_retryable_error(ApiException(status=403)))
        self.assertFalse(is_retryable_error(ApiException(status=404)))
        self.assertFalse(is_retryable_error(ValueError()))

    @patch("lib.kube_client.wait_exponential")
    def test_retry_logic_success_after_failure(self, mock_wait):
        """Test that API call retries and eventually succeeds."""
        # Mock wait to speed up test
        mock_wait.return_value = lambda *args, **kwargs: 0

        # Mock API to fail twice then succeed
        mock_api = self.client.core_v1.read_namespace
        mock_api.side_effect = [
            ApiException(status=503),
            ApiException(status=500),
            MagicMock(to_dict=lambda: {"metadata": {"name": "test"}}),
        ]

        # Call method
        result = self.client.get_namespace("test")

        # Verify result
        self.assertEqual(result["metadata"]["name"], "test")
        
        # Verify retries occurred (3 calls total)
        self.assertEqual(mock_api.call_count, 3)

    @patch("lib.kube_client.wait_exponential")
    def test_retry_logic_max_retries_exceeded(self, mock_wait):
        """Test that API call fails after max retries."""
        # Mock wait to speed up test
        mock_wait.return_value = lambda *args, **kwargs: 0

        # Mock API to fail consistently
        mock_api = self.client.core_v1.read_namespace
        mock_api.side_effect = ApiException(status=503)

        # Call method and expect failure
        with self.assertRaises(ApiException):
            self.client.get_namespace("test")

        # Verify retries occurred (initial + 5 retries = 6 calls)
        # Note: tenacity stop_after_attempt(5) means 5 attempts total
        self.assertEqual(mock_api.call_count, 5)

    def test_non_retryable_error_fails_immediately(self):
        """Test that non-retryable errors fail immediately."""
        # Mock API to fail with 403
        mock_api = self.client.core_v1.read_namespace
        mock_api.side_effect = ApiException(status=403)

        # Call method and expect failure
        with self.assertRaises(ApiException):
            self.client.get_namespace("test")

        # Verify no retries (1 call total)
        self.assertEqual(mock_api.call_count, 1)

    def test_404_returns_none_without_retry(self):
        """Test that 404 returns None immediately without retry."""
        # Mock API to fail with 404
        mock_api = self.client.core_v1.read_namespace
        mock_api.side_effect = ApiException(status=404)

        # Call method
        result = self.client.get_namespace("test")

        # Verify result is None
        self.assertIsNone(result)

        # Verify no retries (1 call total)
        self.assertEqual(mock_api.call_count, 1)
