"""
Tests for CLI auto-import strategy flag and related functionality.

This file tests the --manage-auto-import-strategy CLI flag and its
interaction with the switchover workflow.
"""

import sys
from pathlib import Path
from unittest.mock import patch

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
                "--primary-context",
                "p1",
                "--secondary-context",
                "p2",
                "--method",
                "passive",
                "--old-hub-action",
                "secondary",
            ],
        ):
            args = parse_args()
            assert args.manage_auto_import_strategy is False

    def test_activation_method_default_patch(self):
        """Activation method should default to patch."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--secondary-context",
                "p2",
                "--method",
                "passive",
                "--old-hub-action",
                "secondary",
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
                "--primary-context",
                "p1",
                "--secondary-context",
                "p2",
                "--method",
                "passive",
                "--old-hub-action",
                "secondary",
                "--activation-method",
                "restore",
            ],
        ):
            args = parse_args()
            assert args.activation_method == "restore"
