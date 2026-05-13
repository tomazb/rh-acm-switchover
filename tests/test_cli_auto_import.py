"""
Tests for CLI auto-import strategy flag and related functionality.

This file tests the --manage-auto-import-strategy CLI flag and its
interaction with the switchover workflow.
"""

import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from acm_switchover import _run_phase_activation, _run_phase_finalization, parse_args


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

    def test_getattr_fallback_defaults_to_false(self):
        """getattr fallback for manage_auto_import_strategy must be False.

        When args Namespace lacks manage_auto_import_strategy (e.g., from a
        partial Namespace), the getattr fallback in _run_phase_activation
        and _run_phase_finalization must default to False (matching the CLI
        store_true default), not True.
        """
        # Namespace WITHOUT manage_auto_import_strategy to exercise getattr fallback
        args = Namespace(
            method="full",
            dry_run=False,
            old_hub_action="none",
            restore_only=False,
        )
        state = MagicMock()
        state.get_config = MagicMock(side_effect=lambda key, default=None: default)
        secondary = MagicMock()

        # Activation: SecondaryActivation constructor receives the fallback
        with patch("acm_switchover.SecondaryActivation") as mock_cls:
            mock_cls.return_value.activate.return_value = True
            _run_phase_activation(args, state, None, secondary, MagicMock())
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["manage_auto_import_strategy"] is False, "Activation getattr fallback must be False"

        # Finalization: Finalization constructor receives the fallback
        with patch("acm_switchover.Finalization") as mock_cls:
            mock_cls.return_value.finalize.return_value = True
            _run_phase_finalization(args, state, None, secondary, MagicMock())
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["manage_auto_import_strategy"] is False, "Finalization getattr fallback must be False"
