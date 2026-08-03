"""Seam lock for the run record (spec 2026-08-02-run-record-design.md).

The config-key vocabulary belongs to lib/run_record.py. StateManager's
storage accessors are private; the pause-register modules keep a narrow,
documented allowance (their seam converges separately under issue #208).
"""

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent

PRODUCTION_ROOTS = ["lib", "modules", "acm_switchover.py", "show_state.py", "check_rbac.py"]

# The only production modules allowed to touch StateManager's storage accessors.
ALLOWED = {
    REPO / "lib" / "utils.py",  # the definitions
    REPO / "lib" / "run_record.py",  # the vocabulary owner
    REPO / "lib" / "argocd_register.py",  # register allowance (issue #208)
    REPO / "lib" / "argocd_register_store.py",  # register allowance (issue #208)
}

ACCESSOR = re.compile(r"\.(?:_set_config|_get_config|set_config|get_config)\(")


def _production_files():
    for root in PRODUCTION_ROOTS:
        path = REPO / root
        if path.is_file():
            yield path
        else:
            yield from sorted(path.rglob("*.py"))


def test_config_accessors_only_used_by_allowed_modules():
    offenders = []
    for path in _production_files():
        if path in ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if ACCESSOR.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert not offenders, "config accessors outside the run-record seam:\n" + "\n".join(offenders)


def test_public_accessors_are_gone():
    utils_src = (REPO / "lib" / "utils.py").read_text(encoding="utf-8")
    assert "def set_config(" not in utils_src
    assert "def get_config(" not in utils_src
    assert "def _set_config(" in utils_src
    assert "def _get_config(" in utils_src
