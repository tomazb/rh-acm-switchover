from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

GATE_COMMAND_TIMEOUT_SECONDS = 300
TIMEOUT_RETURN_CODE = -1


@dataclass(frozen=True)
class GateCommand:
    gate_id: str
    label: str
    command: list[str]
    cwd: Path
    required: bool = True


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    label: str
    command: list[str]
    returncode: int
    status: str
    stdout_path: str
    stderr_path: str
    required: bool


def _text_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_gate_command(command: GateCommand, artifact_dir: Path) -> GateResult:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = artifact_dir / f"{command.gate_id}-{command.label}.stdout"
    stderr_path = artifact_dir / f"{command.gate_id}-{command.label}.stderr"

    try:
        completed = subprocess.run(
            command.command,
            cwd=command.cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=GATE_COMMAND_TIMEOUT_SECONDS,
        )
        stdout_text = completed.stdout
        stderr_text = completed.stderr
        returncode = completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout_text = _text_output(exc.output)
        stderr_text = _text_output(exc.stderr)
        if stderr_text and not stderr_text.endswith("\n"):
            stderr_text = f"{stderr_text}\n"
        stderr_text = f"{stderr_text}Command timed out after {exc.timeout} seconds\n"
        returncode = TIMEOUT_RETURN_CODE

    stdout_path.write_text(stdout_text, encoding="utf-8")
    stderr_path.write_text(stderr_text, encoding="utf-8")
    return GateResult(
        gate_id=command.gate_id,
        label=command.label,
        command=command.command,
        returncode=returncode,
        status="passed" if returncode == 0 else "failed",
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        required=command.required,
    )


def build_default_gate_commands(*, enabled_streams: tuple[str, ...], repo_root: Path) -> list[GateCommand]:
    gates = [
        GateCommand(
            gate_id="root-non-e2e-tests",
            label="pytest-root",
            command=["python", "-m", "pytest", "tests/", "-m", "not e2e and not release"],
            cwd=repo_root,
        )
    ]
    if {"python", "ansible"}.issubset(set(enabled_streams)):
        gates.append(
            GateCommand(
                gate_id="static-parity-tests",
                label="pytest-parity",
                command=[
                    "python",
                    "-m",
                    "pytest",
                    "tests/test_constants_parity.py",
                    "tests/test_rbac_collection_parity.py",
                    "tests/test_argocd_constants_parity.py",
                ],
                cwd=repo_root,
            )
        )
    if "python" in enabled_streams:
        gates.extend(
            [
                GateCommand(
                    "python-style-security-gates",
                    "black",
                    ["black", "--check", "--line-length", "120", "acm_switchover.py", "lib/", "modules/"],
                    repo_root,
                ),
                GateCommand(
                    "python-style-security-gates",
                    "isort",
                    [
                        "isort",
                        "--check-only",
                        "--profile",
                        "black",
                        "--line-length",
                        "120",
                        "acm_switchover.py",
                        "lib/",
                        "modules/",
                    ],
                    repo_root,
                ),
                GateCommand("python-cli-smoke", "help", ["python", "acm_switchover.py", "--help"], repo_root),
            ]
        )
    if "ansible" in enabled_streams:
        collection_root = repo_root / "ansible_collections/tomazb/acm_switchover"
        collection_path = "ansible_collections/tomazb/acm_switchover"
        gates.extend(
            [
                GateCommand(
                    "collection-ansible-test-sanity",
                    "ansible-test-sanity",
                    ["ansible-test", "sanity", "--docker", "default", "-v"],
                    collection_root,
                ),
                GateCommand(
                    "collection-unit-tests",
                    "pytest-collection-unit",
                    ["env", "PYTHONPATH=.", "python", "-m", "pytest", f"{collection_path}/tests/unit", "-q"],
                    repo_root,
                ),
                GateCommand(
                    "collection-integration-tests",
                    "pytest-collection-integration",
                    ["env", "PYTHONPATH=.", "python", "-m", "pytest", f"{collection_path}/tests/integration", "-q"],
                    repo_root,
                ),
                GateCommand(
                    "collection-scenario-tests",
                    "pytest-collection-scenario",
                    ["env", "PYTHONPATH=.", "python", "-m", "pytest", f"{collection_path}/tests/scenario", "-q"],
                    repo_root,
                ),
                GateCommand(
                    "collection-build-install",
                    "ansible-galaxy-build",
                    ["ansible-galaxy", "collection", "build", "--force"],
                    collection_root,
                ),
                GateCommand(
                    "collection-playbook-syntax",
                    "preflight",
                    [
                        "env",
                        f"ANSIBLE_COLLECTIONS_PATH={repo_root}",
                        "ansible-playbook",
                        "--syntax-check",
                        f"{collection_path}/playbooks/preflight.yml",
                    ],
                    repo_root,
                ),
                GateCommand(
                    "collection-playbook-syntax",
                    "restore-only",
                    [
                        "env",
                        f"ANSIBLE_COLLECTIONS_PATH={repo_root}",
                        "ansible-playbook",
                        "--syntax-check",
                        f"{collection_path}/playbooks/restore_only.yml",
                    ],
                    repo_root,
                ),
            ]
        )
    return gates
