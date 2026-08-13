"""Regression guardrails for local-runner and GitHub Actions hardening."""

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_TESTS = REPO_ROOT / "run_tests.sh"
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci-cd.yml"
SECURITY_WORKFLOW = REPO_ROOT / ".github/workflows/security.yml"
TESTING_DOC = REPO_ROOT / "docs/development/testing.md"
REQUIREMENTS_DEV = REPO_ROOT / "requirements-dev.txt"

_OBSOLETE_CLI_SUBCOMMAND = re.compile(
    r"(?:python(?:3(?:\.\d+)?)?\s+)?(?:\./)?acm_switchover\.py\s+"
    r"(?P<subcommand>switchover|rollback|decommission)\b"
)
_BASH_FENCE = re.compile(r"^```bash\n(.*?)^```", re.MULTILINE | re.DOTALL)
_WHILE_LOOP = re.compile(
    r"^[ \t]*while\s+.+?;\s*do\s*$(.*?)^[ \t]*done\s*$",
    re.MULTILINE | re.DOTALL,
)
_REAL_PIPE = re.compile(r"(?<!\|)\|(?!\|)")


def _workflow_texts() -> list[tuple[Path, str]]:
    workflows = sorted((REPO_ROOT / ".github/workflows").glob("*.y*ml"))
    assert workflows, "no GitHub Actions workflows were found"
    return [(path, path.read_text(encoding="utf-8")) for path in workflows]


def test_run_tests_release_lane_ignores_inherited_release_profile():
    """The default local runner must not inherit a live release profile."""
    content = RUN_TESTS.read_text(encoding="utf-8")
    invocations = [
        line.strip()
        for line in content.splitlines()
        if "python -m pytest tests/release -q" in line and not line.lstrip().startswith("#")
    ]

    assert invocations == ["env -u ACM_RELEASE_PROFILE python -m pytest tests/release -q"], (
        "run_tests.sh must execute its release-framework lane with ACM_RELEASE_PROFILE removed; "
        f"found {invocations!r}"
    )


def test_workflows_do_not_invoke_obsolete_cli_subcommands():
    """CI must use the flag-only Python CLI surface, never fictional subcommands."""
    inspected = 0
    for path, content in _workflow_texts():
        inspected += 1
        match = _OBSOLETE_CLI_SUBCOMMAND.search(content)
        assert match is None, (
            f"{path.relative_to(REPO_ROOT)} invokes obsolete CLI subcommand "
            f"{match.group('subcommand')!r}: {match.group(0)!r}"
        )

    assert inspected, "no workflow was inspected"


def test_ci_cli_smoke_checks_real_flag_only_surface():
    """The CI smoke check must assert representative real flags are exposed by --help."""
    content = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "python acm_switchover.py --help" in content
    for flag in (
        "--primary-context",
        "--secondary-context",
        "--method",
        "--old-hub-action",
        "--validate-only",
        "--dry-run",
        "--decommission",
        "--restore-only",
        "--argocd-resume-only",
    ):
        assert flag in content, f"CI CLI smoke no longer checks the real flag {flag}"


def test_workflows_do_not_carry_dead_safety_scan_lane():
    """Safety CLI must not silently reappear without an explicit authenticated design."""
    for path, content in _workflow_texts():
        lowered = content.lower()
        assert "safety scan" not in lowered, f"{path.relative_to(REPO_ROOT)} invokes Safety CLI"
        assert "safety-report.json" not in lowered, (
            f"{path.relative_to(REPO_ROOT)} still carries a Safety report artifact"
        )

    requirements = REQUIREMENTS_DEV.read_text(encoding="utf-8")
    security = SECURITY_WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"^pip-audit\b", requirements, re.MULTILINE), "pip-audit must remain installed"
    assert "pip-audit --desc" in security, "the dependency workflow must retain pip-audit scanning"


def test_while_loop_guard_pattern_matches_documented_shell_shape():
    """The while-loop detector itself must match the shell shape this guardrail protects."""
    sample = "while read -r item; do\n  verify \"$item\" || status=1\ndone\n"
    assert _WHILE_LOOP.search(sample), "while-loop guard pattern no longer matches `while ...; do ... done`"


def test_documented_while_verification_loops_aggregate_failures():
    """Future documented while loops must fail fast or aggregate every iteration failure.

    tests/test_documentation_guardrails.py already protects documented ``for`` loops. This
    sibling guard closes the gap for ``while ...; do ... done`` without requiring a while loop
    to exist today. If one is added next to an existing for loop, it cannot escape validation.
    """
    content = TESTING_DOC.read_text(encoding="utf-8")

    for block in _BASH_FENCE.findall(content):
        for match in _WHILE_LOOP.finditer(block):
            head, body, tail = block[: match.start()], match.group(1), block[match.end() :]

            fails_inside = "|| exit 1" in body or "|| return 1" in body

            aggregated = False
            accumulator = re.search(r"\|\|\s*(\w+)=1\b", body)
            if accumulator:
                name = re.escape(accumulator.group(1))
                initialised = re.search(rf"^[ \t]*{name}=0\b", head, re.MULTILINE)
                acted_on = re.search(rf"\$\{{?{name}\b.*?\bexit\s+1\b", tail, re.DOTALL)
                aggregated = bool(initialised) and bool(acted_on)

            assert fails_inside or aggregated, (
                f"{TESTING_DOC.relative_to(REPO_ROOT)} documents a while loop that can swallow "
                f"an early failure:\n{match.group(0).strip()}"
            )

            if _REAL_PIPE.search(body):
                assert re.search(r"^[ \t]*set -o pipefail\b", head, re.MULTILINE), (
                    f"{TESTING_DOC.relative_to(REPO_ROOT)} documents a piped while-loop command "
                    "without `set -o pipefail` earlier in the same bash block"
                )
