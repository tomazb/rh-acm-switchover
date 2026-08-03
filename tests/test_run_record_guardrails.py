"""Seam lock for the run record (spec 2026-08-02-run-record-design.md).

The config-key vocabulary belongs to lib/run_record.py. Production code
reaches it two ways, and the spec (section "Migration", step 4) forbids
both from outside the seam: calling StateManager's storage accessors, and
reading a named key straight off a state snapshot. The pause-register
modules keep a narrow, documented allowance (their seam converges
separately under issue #208).
"""

import pathlib
import re

from lib.utils import StateManager

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

# Reading a named config key straight off a state snapshot bypasses the accessors
# entirely, so the accessor scan alone would let `snapshot["config"]["primary_version"]`
# through. Match any single-expression access to the config bag — subscript or
# `.get("config", ...)` — chained to a literal key (subscript or `.get`).
# Key-agnostic reads (`state.get("config", {})` handed on whole, or iterated
# generically) are the supported way to touch the config bag from outside the
# seam and do not match. Known limit: a two-step read split across lines
# (`config = snapshot["config"]` then `config["key"]`) needs an AST visitor,
# not a longer regex — see the pattern regression test below.
_CONFIG_BAG = r"(?:\[\s*[\"']config[\"']\s*\]|\.get\(\s*[\"']config[\"'][^)]*\))"
_LITERAL_KEY = r"\s*(?:\[\s*[\"']|\.get\(\s*[\"'])"
RAW_CONFIG_KEY = re.compile(_CONFIG_BAG + _LITERAL_KEY)


def _production_files():
    for root in PRODUCTION_ROOTS:
        path = REPO / root
        assert path.exists(), root
        if path.is_file():
            yield path
        else:
            yield from sorted(path.rglob("*.py"))


def _scan(pattern):
    offenders = []
    for path in _production_files():
        if path in ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()}")
    return offenders


def test_config_accessors_only_used_by_allowed_modules():
    offenders = _scan(ACCESSOR)
    assert not offenders, "config accessors outside the run-record seam:\n" + "\n".join(offenders)


def test_raw_config_keys_only_read_by_allowed_modules():
    offenders = _scan(RAW_CONFIG_KEY)
    assert not offenders, "raw config-key reads outside the run-record seam:\n" + "\n".join(offenders)


def test_raw_config_key_pattern_catches_known_bypass_shapes():
    """Regression probe for the detector itself (external review, PR #215).

    Every shape that reads a named key off the config bag in one expression
    must match; the two supported key-agnostic shapes must not. A two-step
    read split across lines (`config = snapshot["config"]` then
    `config["key"]`) is a documented limitation of line-based scanning.
    """
    bypasses = [
        'snapshot["config"]["primary_version"]',
        "snapshot['config']['primary_version']",
        'snapshot.get("config", {})["primary_version"]',
        'snapshot.get("config", {}).get("primary_version")',
        'snapshot["config"].get("primary_version", None)',
        'state.get("config").get("saved_backup_schedule")',
    ]
    for line in bypasses:
        assert RAW_CONFIG_KEY.search(line), f"detector must catch: {line}"

    key_agnostic = [
        'config = state_snapshot.get("config", {}) or {}',
        'config = state.get("config", {})',
        "for key, value in config.items():",
        "argocd_status = PauseRegisterStore.status_from_state_config(config)",
        "summary = RunSummary.from_snapshot(state_snapshot)",
    ]
    for line in key_agnostic:
        assert not RAW_CONFIG_KEY.search(line), f"false positive on supported shape: {line}"


def test_public_accessors_are_gone():
    utils_src = (REPO / "lib" / "utils.py").read_text(encoding="utf-8")
    assert "def set_config(" not in utils_src
    assert "def get_config(" not in utils_src
    assert "def _set_config(" in utils_src
    assert "def _get_config(" in utils_src
    # Source text alone would miss an alias (`set_config = _set_config`) or a
    # re-export, so assert against the imported class too.
    assert not hasattr(StateManager, "set_config")
    assert not hasattr(StateManager, "get_config")
    assert hasattr(StateManager, "_set_config")
    assert hasattr(StateManager, "_get_config")
