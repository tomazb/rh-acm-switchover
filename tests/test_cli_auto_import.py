"""
Tests for CLI auto-import strategy flag and related functionality.

This file tests the --manage-auto-import-strategy CLI flag and its
interaction with the switchover workflow.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from acm_switchover import parse_args


@pytest.mark.unit
class TestManageAutoImportFlag:
    """Test cases for --manage-auto-import-strategy CLI flag."""

    def test_flag_default_is_false(self):
        """Flag should be False by default."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context", "p1",
                "--secondary-context", "p2",
                "--method", "passive",
                "--old-hub-action", "secondary",
            ],
        ):
            args = parse_args()
            assert args.manage_auto_import_strategy is False

    def test_flag_enabled_when_specified(self):
        """Flag should be True when --manage-auto-import-strategy is specified."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context", "p1",
                "--secondary-context", "p2",
                "--method", "passive",
                "--old-hub-action", "secondary",
                "--manage-auto-import-strategy",
            ],
        ):
            args = parse_args()
            assert args.manage_auto_import_strategy is True

    def test_flag_works_with_full_method(self):
        """Flag should work with --method full."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context", "p1",
                "--secondary-context", "p2",
                "--method", "full",
                "--old-hub-action", "secondary",
                "--manage-auto-import-strategy",
            ],
        ):
            args = parse_args()
            assert args.manage_auto_import_strategy is True
            assert args.method == "full"

    def test_flag_works_with_validate_only(self):
        """Flag should work with --validate-only mode."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context", "p1",
                "--secondary-context", "p2",
                "--method", "passive",
                "--old-hub-action", "secondary",
                "--validate-only",
                "--manage-auto-import-strategy",
            ],
        ):
            args = parse_args()
            assert args.manage_auto_import_strategy is True
            assert args.validate_only is True

    def test_flag_works_with_dry_run(self):
        """Flag should work with --dry-run mode."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context", "p1",
                "--secondary-context", "p2",
                "--method", "passive",
                "--old-hub-action", "secondary",
                "--dry-run",
                "--manage-auto-import-strategy",
            ],
        ):
            args = parse_args()
            assert args.manage_auto_import_strategy is True
            assert args.dry_run is True

    def test_activation_method_default_patch(self):
        """Activation method should default to patch."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context", "p1",
                "--secondary-context", "p2",
                "--method", "passive",
                "--old-hub-action", "secondary",
            ],
        ):
            args = parse_args()
            assert args.activation_method == "patch"

    def test_activation_method_restore(self):
        """Activation method should parse restore option."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context", "p1",
                "--secondary-context", "p2",
                "--method", "passive",
                "--old-hub-action", "secondary",
                "--activation-method", "restore",
            ],
        ):
            args = parse_args()
            assert args.activation_method == "restore"

    def test_disable_observability_flag(self):
        """Disable observability flag should parse."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context", "p1",
                "--secondary-context", "p2",
                "--method", "passive",
                "--old-hub-action", "secondary",
                "--disable-observability-on-secondary",
            ],
        ):
            args = parse_args()
            assert args.disable_observability_on_secondary is True


@pytest.mark.unit
class TestAutoImportFlagInteraction:
    """Test interactions between auto-import flag and other options."""

    def test_flag_not_required_for_normal_operation(self):
        """Switchover should work without --manage-auto-import-strategy."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context", "p1",
                "--secondary-context", "p2",
                "--method", "passive",
                "--old-hub-action", "secondary",
            ],
        ):
            # Should not raise
            args = parse_args()
            assert args.primary_context == "p1"
            assert args.secondary_context == "p2"

    def test_flag_compatible_with_all_old_hub_actions(self):
        """Flag should work with all --old-hub-action options."""
        for action in ["secondary", "decommission", "none"]:
            with patch(
                "sys.argv",
                [
                    "script.py",
                    "--primary-context", "p1",
                    "--secondary-context", "p2",
                    "--method", "passive",
                    "--old-hub-action", action,
                    "--manage-auto-import-strategy",
                ],
            ):
                args = parse_args()
                assert args.manage_auto_import_strategy is True
                assert args.old_hub_action == action
