"""Unit tests for acm_switchover.py (main script).

Tests argument parsing and basic entry point logic.
"""

import pytest
import sys
import argparse
from pathlib import Path
from unittest.mock import Mock, patch

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from acm_switchover import parse_args


@pytest.mark.unit
class TestArgParsing:
    """Tests for command line argument parsing."""

    def test_required_args(self):
        """Test that primary-context is required."""
        with patch('sys.argv', ['script.py']):
            with pytest.raises(SystemExit):
                parse_args()

    def test_validate_only_mode(self):
        """Test parsing validate-only mode."""
        with patch('sys.argv', ['script.py', '--primary-context', 'p1', '--validate-only']):
            args = parse_args()
            assert args.validate_only is True
            assert args.dry_run is False
            assert args.primary_context == 'p1'

    def test_dry_run_mode(self):
        """Test parsing dry-run mode."""
        with patch('sys.argv', ['script.py', '--primary-context', 'p1', '--dry-run']):
            args = parse_args()
            assert args.dry_run is True
            assert args.validate_only is False

    def test_rollback_mode(self):
        """Test parsing rollback mode."""
        with patch('sys.argv', ['script.py', '--primary-context', 'p1', '--secondary-context', 's1', '--rollback']):
            args = parse_args()
            assert args.rollback is True

    def test_decommission_mode(self):
        """Test parsing decommission mode."""
        with patch('sys.argv', ['script.py', '--primary-context', 'p1', '--decommission']):
            args = parse_args()
            assert args.decommission is True

    def test_mutually_exclusive_modes(self):
        """Test that mutually exclusive flags raise error."""
        with patch('sys.argv', ['script.py', '--primary-context', 'p1', '--dry-run', '--validate-only']):
            with pytest.raises(SystemExit):
                parse_args()

    def test_method_selection(self):
        """Test method selection argument."""
        with patch('sys.argv', ['script.py', '--primary-context', 'p1', '--method', 'full']):
            args = parse_args()
            assert args.method == 'full'

    def test_default_method(self):
        """Test default method is passive."""
        with patch('sys.argv', ['script.py', '--primary-context', 'p1']):
            args = parse_args()
            assert args.method == 'passive'
