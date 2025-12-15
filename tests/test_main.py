"""Unit tests for acm_switchover.py (main script).

Tests argument parsing and basic entry point logic.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from acm_switchover import parse_args


@pytest.mark.unit
class TestArgParsing:
    """Tests for command line argument parsing."""

    def test_required_args(self):
        """Test that primary-context, old-hub-action, and method are required."""
        with patch("sys.argv", ["script.py"]):
            with pytest.raises(SystemExit):
                parse_args()

        # old-hub-action is also required
        with patch("sys.argv", ["script.py", "--primary-context", "p1", "--method", "passive"]):
            with pytest.raises(SystemExit):
                parse_args()

        # method is also required
        with patch(
            "sys.argv",
            ["script.py", "--primary-context", "p1", "--old-hub-action", "secondary"],
        ):
            with pytest.raises(SystemExit):
                parse_args()

    def test_validate_only_mode(self):
        """Test parsing validate-only mode."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--old-hub-action",
                "secondary",
                "--method",
                "passive",
                "--validate-only",
            ],
        ):
            args = parse_args()
            assert args.validate_only is True
            assert args.dry_run is False
            assert args.primary_context == "p1"
            assert args.old_hub_action == "secondary"

    def test_dry_run_mode(self):
        """Test parsing dry-run mode."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--old-hub-action",
                "none",
                "--method",
                "passive",
                "--dry-run",
            ],
        ):
            args = parse_args()
            assert args.dry_run is True
            assert args.validate_only is False
            assert args.old_hub_action == "none"

    def test_decommission_mode(self):
        """Test parsing decommission mode."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--old-hub-action",
                "none",
                "--method",
                "passive",
                "--decommission",
            ],
        ):
            args = parse_args()
            assert args.decommission is True

    def test_mutually_exclusive_modes(self):
        """Test that mutually exclusive flags raise error."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--old-hub-action",
                "none",
                "--method",
                "passive",
                "--dry-run",
                "--validate-only",
            ],
        ):
            with pytest.raises(SystemExit):
                parse_args()

    def test_method_selection(self):
        """Test method selection argument."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--old-hub-action",
                "decommission",
                "--method",
                "full",
            ],
        ):
            args = parse_args()
            assert args.method == "full"
            assert args.old_hub_action == "decommission"

    def test_method_choices(self):
        """Test method only accepts valid choices."""
        # Valid choices
        for method in ["passive", "full"]:
            with patch(
                "sys.argv",
                [
                    "script.py",
                    "--primary-context",
                    "p1",
                    "--old-hub-action",
                    "secondary",
                    "--method",
                    method,
                ],
            ):
                args = parse_args()
                assert args.method == method

        # Invalid choice
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--old-hub-action",
                "secondary",
                "--method",
                "invalid",
            ],
        ):
            with pytest.raises(SystemExit):
                parse_args()

    def test_old_hub_action_choices(self):
        """Test old-hub-action only accepts valid choices."""
        # Valid choices
        for action in ["secondary", "decommission", "none"]:
            with patch(
                "sys.argv",
                [
                    "script.py",
                    "--primary-context",
                    "p1",
                    "--old-hub-action",
                    action,
                    "--method",
                    "passive",
                ],
            ):
                args = parse_args()
                assert args.old_hub_action == action

        # Invalid choice
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--old-hub-action",
                "invalid",
                "--method",
                "passive",
            ],
        ):
            with pytest.raises(SystemExit):
                parse_args()
