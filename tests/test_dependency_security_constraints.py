from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_requirements(path: str) -> dict[str, list[Requirement]]:
    requirements: dict[str, list[Requirement]] = {}

    for raw_line in (REPO_ROOT / path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        requirement = Requirement(line)
        requirements.setdefault(requirement.name.lower(), []).append(requirement)

    return requirements


def _requirements_for_python(requirements: list[Requirement], python_version: str) -> list[Requirement]:
    return [
        requirement
        for requirement in requirements
        if requirement.marker is None or requirement.marker.evaluate({"python_version": python_version})
    ]


def test_runtime_requirements_exclude_known_vulnerable_versions():
    requirements = _load_requirements("requirements.txt")

    expected_minimums = {
        "pyasn1": Version("0.6.3"),
        "pygments": Version("2.20.0"),
        "requests": Version("2.33.0"),
    }

    for name, fixed_version in expected_minimums.items():
        assert name in requirements, f"{name} must be pinned in requirements.txt"
        assert len(requirements[name]) == 1
        requirement = requirements[name][0]
        assert (
            fixed_version in requirement.specifier
        ), f"{name} constraint {requirement.specifier} does not allow fixed version {fixed_version}"


def test_dev_requirements_exclude_known_vulnerable_versions():
    requirements = _load_requirements("requirements-dev.txt")

    expected_minimums = {
        "black": Version("26.3.1"),
    }

    for name, fixed_version in expected_minimums.items():
        assert name in requirements, f"{name} must be pinned in requirements-dev.txt"
        assert len(requirements[name]) == 1
        requirement = requirements[name][0]
        assert (
            fixed_version in requirement.specifier
        ), f"{name} constraint {requirement.specifier} does not allow fixed version {fixed_version}"

    ansible_requirements = requirements.get("ansible-core", [])
    assert len(ansible_requirements) == 2

    python310_requirements = _requirements_for_python(ansible_requirements, "3.10")
    assert len(python310_requirements) == 1
    assert Version("2.17.14") in python310_requirements[0].specifier
    assert Version("2.18.0") not in python310_requirements[0].specifier

    python311_requirements = _requirements_for_python(ansible_requirements, "3.11")
    assert len(python311_requirements) == 1
    assert Version("2.18.1") in python311_requirements[0].specifier
    assert Version("2.18.0") not in python311_requirements[0].specifier
