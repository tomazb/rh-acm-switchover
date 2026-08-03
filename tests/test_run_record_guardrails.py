"""Seam lock for the run record (spec 2026-08-02-run-record-design.md).

The config-key vocabulary belongs to lib/run_record.py. Production code
reaches it two ways, and the spec (section "Migration", step 4) forbids
both from outside the seam: calling StateManager's storage accessors, and
reading a named key straight off a state snapshot. The pause-register
modules keep a narrow, documented allowance (their seam converges
separately under issue #208).
"""

import ast
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
# seam and do not match. A two-step read split across lines
# (`config = snapshot["config"]` then `config["key"]`) is invisible to any
# line regex — the AST detector below (`_config_bag_bypasses`) covers it.
_CONFIG_BAG = r"(?:\[\s*[\"']config[\"']\s*\]|\.get\(\s*[\"']config[\"'][^)]*\))"
_LITERAL_KEY = r"\s*(?:\[\s*[\"']|\.get\(\s*[\"'])"
RAW_CONFIG_KEY = re.compile(_CONFIG_BAG + _LITERAL_KEY)


def _is_config_bag_expr(node):
    """True for an expression that produces the config bag itself:
    ``x["config"]`` or ``x.get("config"[, default])``."""
    if isinstance(node, ast.Subscript):
        return isinstance(node.slice, ast.Constant) and node.slice.value == "config"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
        return bool(node.args) and isinstance(node.args[0], ast.Constant) and node.args[0].value == "config"
    return False


def _config_bag_bypasses(source):
    """AST complement to RAW_CONFIG_KEY: finds named-key reads off the config
    bag even when the bag is first bound to a variable (the two-step read the
    line regex cannot see). Returns [(lineno, key), ...].

    Key-agnostic uses — handing the whole bag on, or iterating it — never
    subscript a literal key and are not flagged. Only simple single-name
    assignments (optionally behind ``or {}``) are tracked; a name is treated
    as the bag file-wide, which is safe because unrelated dicts named the
    same only become offenders if code reads a literal key off them, and
    tuple/attribute assignments (e.g. ``passed, config = validate_all()``)
    are deliberately not tracked.
    """
    tree = ast.parse(source)

    bag_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = node.value
            candidates = [value] + (value.values if isinstance(value, ast.BoolOp) else [])
            if any(_is_config_bag_expr(candidate) for candidate in candidates):
                bag_names.add(node.targets[0].id)

    def _is_bag(node):
        return _is_config_bag_expr(node) or (isinstance(node, ast.Name) and node.id in bag_names)

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str) and _is_bag(node.value):
                offenders.append((node.lineno, key.value))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            if node.args:
                key = node.args[0]
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and key.value != "config"
                    and _is_bag(node.func.value)
                ):
                    offenders.append((node.lineno, key.value))
    return offenders


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


def test_config_bag_detector_catches_two_step_reads():
    """Regression probe for the AST detector (Thermos external review).

    The line-based regex cannot see a read split across statements. The AST
    detector tracks names bound from a config-bag expression and flags any
    literal-key subscript or .get() on them, closing the bag-then-read gap.
    """
    bypasses = [
        # two-step: bind the bag, read a named key later
        'config = snapshot["config"]\nvalue = config["primary_version"]\n',
        'config = snapshot.get("config", {}) or {}\nvalue = config.get("saved_backup_schedule")\n',
        # single-expression forms must also be caught (parity with the regex)
        'value = snapshot["config"]["auto_import_strategy_set"]\n',
        'value = snapshot.get("config", {}).get("new_backup_detected")\n',
    ]
    for source in bypasses:
        assert _config_bag_bypasses(source), f"detector must catch:\n{source}"

    supported = [
        # hand the whole bag on, key-agnostic
        'config = state_snapshot.get("config", {}) or {}\nstatus = PauseRegisterStore.status_from_state_config(config)\n',
        # iterate generically
        'config = state.get("config", {})\nfor key, value in config.items():\n    print(key, value)\n',
        # an unrelated name called config (e.g. preflight validator results)
        'passed, config = validator.validate_all()\nversion = config["primary_version"]\n',
    ]
    for source in supported:
        assert not _config_bag_bypasses(source), f"false positive on supported shape:\n{source}"


def test_config_bag_two_step_reads_only_in_allowed_modules():
    offenders = []
    for path in _production_files():
        if path in ALLOWED:
            continue
        for lineno, key in _config_bag_bypasses(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(REPO)}:{lineno}: config-bag read of {key!r}")
    assert not offenders, "config-bag reads outside the run-record seam:\n" + "\n".join(offenders)


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
