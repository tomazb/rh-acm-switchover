"""Tests for check_rbac.py CLI tool."""

import sys
from unittest.mock import MagicMock, patch

import pytest


class TestParseArgs:
    """Tests for CLI argument parsing."""

    def test_default_args(self):
        """Test default argument values."""
        with patch("sys.argv", ["check_rbac.py"]):
            from check_rbac import parse_args

            args = parse_args()
            assert args.context is None
            assert args.role == "operator"
            assert args.verbose is False
            assert args.include_decommission is False
            assert args.skip_observability is False
            assert args.managed_cluster is False

    def test_context_flag(self):
        """Test --context flag."""
        with patch("sys.argv", ["check_rbac.py", "--context", "my-hub"]):
            from check_rbac import parse_args

            args = parse_args()
            assert args.context == "my-hub"

    def test_role_validator(self):
        """Test --role validator flag."""
        with patch("sys.argv", ["check_rbac.py", "--role", "validator"]):
            from check_rbac import parse_args

            args = parse_args()
            assert args.role == "validator"

    def test_verbose_short_flag(self):
        """Test -v verbose flag."""
        with patch("sys.argv", ["check_rbac.py", "-v"]):
            from check_rbac import parse_args

            args = parse_args()
            assert args.verbose is True

    def test_dual_hub_contexts(self):
        """Test --primary-context and --secondary-context flags together."""
        with patch(
            "sys.argv",
            ["check_rbac.py", "--primary-context", "hub1", "--secondary-context", "hub2"],
        ):
            from check_rbac import parse_args

            args = parse_args()
            assert args.primary_context == "hub1"
            assert args.secondary_context == "hub2"


class TestMain:
    """Tests for main() entry point."""

    @patch("check_rbac.KubeClient")
    @patch("check_rbac.RBACValidator")
    @patch("check_rbac.setup_logging")
    def test_single_context_success(self, mock_logging, mock_rbac_cls, mock_kube_cls):
        """Test successful single-context RBAC validation."""
        mock_validator = MagicMock()
        mock_validator.validate_all_permissions.return_value = (True, [])
        mock_validator.generate_permission_report.return_value = "All OK"
        mock_rbac_cls.return_value = mock_validator

        with patch("sys.argv", ["check_rbac.py", "--context", "test-hub"]):
            from check_rbac import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    @patch("check_rbac.KubeClient")
    @patch("check_rbac.RBACValidator")
    @patch("check_rbac.setup_logging")
    def test_single_context_failure(self, mock_logging, mock_rbac_cls, mock_kube_cls):
        """Test failed single-context RBAC validation exits with code 1."""
        mock_validator = MagicMock()
        mock_validator.validate_all_permissions.return_value = (False, ["missing perm"])
        mock_validator.generate_permission_report.return_value = "FAILED"
        mock_rbac_cls.return_value = mock_validator

        with patch("sys.argv", ["check_rbac.py", "--context", "test-hub"]):
            from check_rbac import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    @patch("check_rbac.KubeClient")
    @patch("check_rbac.RBACValidator")
    @patch("check_rbac.setup_logging")
    def test_validator_role_with_decommission_rejected(self, mock_logging, mock_rbac_cls, mock_kube_cls):
        """Test that --role validator --include-decommission exits with error."""
        with patch(
            "sys.argv",
            ["check_rbac.py", "--role", "validator", "--include-decommission"],
        ):
            from check_rbac import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
            # KubeClient should never be instantiated for this case
            mock_kube_cls.assert_not_called()

    @patch("check_rbac.KubeClient")
    @patch("check_rbac.RBACValidator")
    @patch("check_rbac.setup_logging")
    def test_managed_cluster_mode(self, mock_logging, mock_rbac_cls, mock_kube_cls, capsys):
        """Test managed cluster validation path."""
        mock_validator = MagicMock()
        mock_validator.validate_managed_cluster_permissions.return_value = (True, [])
        mock_rbac_cls.return_value = mock_validator

        with patch("sys.argv", ["check_rbac.py", "--managed-cluster"]):
            from check_rbac import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
            captured = capsys.readouterr()
            assert "ALL PERMISSIONS VALIDATED" in captured.out

    @patch("check_rbac.KubeClient")
    @patch("check_rbac.RBACValidator")
    @patch("check_rbac.setup_logging")
    def test_managed_cluster_failure_shows_errors(self, mock_logging, mock_rbac_cls, mock_kube_cls, capsys):
        """Test managed cluster validation failure output."""
        mock_validator = MagicMock()
        mock_validator.validate_managed_cluster_permissions.return_value = (
            False,
            ["missing get on pods"],
        )
        mock_rbac_cls.return_value = mock_validator

        with patch("sys.argv", ["check_rbac.py", "--managed-cluster"]):
            from check_rbac import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "PERMISSION VALIDATION FAILED" in captured.out
            assert "missing get on pods" in captured.out

    @patch("check_rbac.KubeClient", side_effect=Exception("connection refused"))
    @patch("check_rbac.setup_logging")
    def test_exception_during_validation(self, mock_logging, mock_kube_cls):
        """Test graceful handling of unexpected exceptions."""
        with patch("sys.argv", ["check_rbac.py", "--context", "bad-hub"]):
            from check_rbac import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
