"""Test-only fault injection for tests/integration (review finding 1).

When ACM_TEST_FAIL_DIRECTORY_FSYNC_AFTER_MARKER names a checkpoint file, an fsync of a
directory fails once that file holds the Argo CD re-pause marker: os.replace already made
the marker visible, but the checkpoint write cannot acknowledge its durability.
"""

import errno
import importlib.util
import os
import stat
from pathlib import Path

# This module shadows the compatibility sitecustomize the collection tests put on the path.
_COMPAT = Path(__file__).resolve().parents[2] / "support" / "python314_ast_compat" / "sitecustomize.py"
_spec = importlib.util.spec_from_file_location("_acm_compat_sitecustomize", _COMPAT)
_spec.loader.exec_module(importlib.util.module_from_spec(_spec))

_CHECKPOINT = os.environ.get("ACM_TEST_FAIL_DIRECTORY_FSYNC_AFTER_MARKER")
if _CHECKPOINT:
    _real_fsync = os.fsync

    def _fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            try:
                with open(_CHECKPOINT, encoding="utf-8") as handle:
                    marked = '"argocd_repause_required"' in handle.read()
            except OSError:
                marked = False
            if marked:
                raise OSError(errno.EIO, "injected directory fsync failure")
        return _real_fsync(fd)

    os.fsync = _fsync
