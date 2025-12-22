"""
Tests for version management and --version CLI flag.

These tests ensure:
1. Version constants are properly defined
2. All version sources are in sync
3. --version flag works on all CLIs
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

# Get the project root
PROJECT_ROOT = Path(__file__).parent.parent


@pytest.mark.unit
class TestVersionConstants:
    """Test version constants are properly defined."""

    def test_lib_version_defined(self):
        """Test that lib/__init__.py has __version__ defined."""
        from lib import __version__, __version_date__

        assert __version__ is not None
        assert isinstance(__version__, str)
        assert len(__version__) > 0

        assert __version_date__ is not None
        assert isinstance(__version_date__, str)
        # Date should be in YYYY-MM-DD format
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", __version_date__)

    def test_version_format_semver(self):
        """Test that version follows semantic versioning."""
        from lib import __version__

        # Should match X.Y.Z pattern
        assert re.match(r"^\d+\.\d+\.\d+$", __version__), (
            f"Version '{__version__}' doesn't match semver X.Y.Z format"
        )

    def test_version_date_valid(self):
        """Test that version date is a valid date."""
        from datetime import datetime

        from lib import __version_date__

        # Should be parseable as a date
        try:
            parsed = datetime.strptime(__version_date__, "%Y-%m-%d")
            # Date shouldn't be in the future (with 1 day tolerance)
            assert parsed <= datetime.now(), "Version date is in the future"
        except ValueError:
            pytest.fail(f"Version date '{__version_date__}' is not valid YYYY-MM-DD")


@pytest.mark.unit
class TestVersionSync:
    """Test that all version sources are in sync."""

    def test_canonical_version_file_exists(self):
        """Test that the canonical VERSION file exists."""
        version_file = PROJECT_ROOT / "packaging" / "common" / "VERSION"
        assert version_file.exists(), "packaging/common/VERSION should exist"

        version_date_file = PROJECT_ROOT / "packaging" / "common" / "VERSION_DATE"
        assert version_date_file.exists(), "packaging/common/VERSION_DATE should exist"

    def test_canonical_version_matches_lib(self):
        """Test that canonical VERSION matches lib/__init__.py."""
        from lib import __version__, __version_date__

        version_file = PROJECT_ROOT / "packaging" / "common" / "VERSION"
        version_date_file = PROJECT_ROOT / "packaging" / "common" / "VERSION_DATE"

        if version_file.exists():
            canonical_version = version_file.read_text().strip()
            assert canonical_version == __version__, (
                f"VERSION file ({canonical_version}) doesn't match "
                f"lib/__version__ ({__version__})"
            )

        if version_date_file.exists():
            canonical_date = version_date_file.read_text().strip()
            assert canonical_date == __version_date__, (
                f"VERSION_DATE file ({canonical_date}) doesn't match "
                f"lib/__version_date__ ({__version_date__})"
            )

    def test_setup_cfg_version_matches(self):
        """Test that setup.cfg version matches lib version."""
        from lib import __version__

        setup_cfg = PROJECT_ROOT / "setup.cfg"
        if setup_cfg.exists():
            content = setup_cfg.read_text()
            # Extract version from setup.cfg
            match = re.search(r"^version\s*=\s*(.+)$", content, re.MULTILINE)
            if match:
                setup_version = match.group(1).strip()
                assert setup_version == __version__, (
                    f"setup.cfg version ({setup_version}) doesn't match "
                    f"lib.__version__ ({__version__})"
                )

    def test_constants_sh_version_matches(self):
        """Test that scripts/constants.sh version matches lib version."""
        from lib import __version__, __version_date__

        constants_sh = PROJECT_ROOT / "scripts" / "constants.sh"
        if constants_sh.exists():
            content = constants_sh.read_text()

            # Extract SCRIPT_VERSION
            match = re.search(
                r'^export\s+SCRIPT_VERSION="(.+)"$', content, re.MULTILINE
            )
            if match:
                script_version = match.group(1)
                assert script_version == __version__, (
                    f"SCRIPT_VERSION ({script_version}) doesn't match "
                    f"lib.__version__ ({__version__})"
                )

            # Extract SCRIPT_VERSION_DATE
            match = re.search(
                r'^export\s+SCRIPT_VERSION_DATE="(.+)"$', content, re.MULTILINE
            )
            if match:
                script_date = match.group(1)
                assert script_date == __version_date__, (
                    f"SCRIPT_VERSION_DATE ({script_date}) doesn't match "
                    f"lib.__version_date__ ({__version_date__})"
                )


@pytest.mark.integration
class TestVersionCLI:
    """Test --version flag on CLI tools."""

    def test_acm_switchover_version_flag(self):
        """Test acm_switchover.py --version output."""
        from lib import __version__, __version_date__

        result = subprocess.run(
            [sys.executable, "acm_switchover.py", "--version"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )

        assert result.returncode == 0, f"--version failed: {result.stderr}"
        assert __version__ in result.stdout, (
            f"Version {__version__} not in output: {result.stdout}"
        )
        assert __version_date__ in result.stdout, (
            f"Date {__version_date__} not in output: {result.stdout}"
        )

    def test_check_rbac_version_flag(self):
        """Test check_rbac.py --version output."""
        from lib import __version__, __version_date__

        result = subprocess.run(
            [sys.executable, "check_rbac.py", "--version"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )

        assert result.returncode == 0, f"--version failed: {result.stderr}"
        assert __version__ in result.stdout, (
            f"Version {__version__} not in output: {result.stdout}"
        )
        assert __version_date__ in result.stdout, (
            f"Date {__version_date__} not in output: {result.stdout}"
        )

    def test_show_state_version_flag(self):
        """Test show_state.py --version output."""
        from lib import __version__, __version_date__

        result = subprocess.run(
            [sys.executable, "show_state.py", "--version"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )

        assert result.returncode == 0, f"--version failed: {result.stderr}"
        assert __version__ in result.stdout, (
            f"Version {__version__} not in output: {result.stdout}"
        )
        assert __version_date__ in result.stdout, (
            f"Date {__version_date__} not in output: {result.stdout}"
        )

    def test_version_output_format(self):
        """Test that version output follows expected format."""
        result = subprocess.run(
            [sys.executable, "acm_switchover.py", "--version"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )

        # Expected format: "scriptname X.Y.Z (YYYY-MM-DD)"
        pattern = r"^[\w_]+\.py \d+\.\d+\.\d+ \(\d{4}-\d{2}-\d{2}\)$"
        output = result.stdout.strip()
        assert re.match(pattern, output), (
            f"Version output '{output}' doesn't match expected format"
        )


@pytest.mark.integration
class TestValidateVersionsScript:
    """Test the validate-versions.sh script."""

    def test_validate_versions_script_exists(self):
        """Test that validate-versions.sh exists and is executable."""
        script = PROJECT_ROOT / "packaging" / "common" / "validate-versions.sh"
        assert script.exists(), "validate-versions.sh should exist"
        assert script.stat().st_mode & 0o111, "validate-versions.sh should be executable"

    def test_validate_versions_passes(self):
        """Test that validate-versions.sh passes (all versions in sync)."""
        script = PROJECT_ROOT / "packaging" / "common" / "validate-versions.sh"

        if not script.exists():
            pytest.skip("validate-versions.sh not found")

        result = subprocess.run(
            [str(script)],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )

        assert result.returncode == 0, (
            f"validate-versions.sh failed:\n{result.stdout}\n{result.stderr}"
        )
        assert "All versions are consistent" in result.stdout
