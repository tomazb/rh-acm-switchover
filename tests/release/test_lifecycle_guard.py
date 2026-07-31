from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.release.conftest import RELEASE_PROFILE_SKIP_REASON

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_release_lifecycle_skips_without_profile() -> None:
    env = dict(os.environ)
    env.pop("ACM_RELEASE_PROFILE", None)
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
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=120,
        env=env,
    )

    assert completed.returncode == 0
    assert "skipped" in completed.stdout
    assert RELEASE_PROFILE_SKIP_REASON in completed.stdout
