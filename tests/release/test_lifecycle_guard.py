from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.release.conftest import BASELINE_MANAGER_SKIP_REASON

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_release_lifecycle_skips_without_live_baseline_manager() -> None:
    env = os.environ.copy()
    env["ACM_RELEASE_PROFILE"] = str(REPO_ROOT / "tests/release/profiles/dev-minimal.example.yaml")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/release/test_release_certification.py",
            "-q",
            "-rs",
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0
    assert "skipped" in completed.stdout
    assert BASELINE_MANAGER_SKIP_REASON in completed.stdout
