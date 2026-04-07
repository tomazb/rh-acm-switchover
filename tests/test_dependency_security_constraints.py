from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version


REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_requirements(path: str) -> dict[str, Requirement]:
    requirements = {}

    for raw_line in (REPO_ROOT / path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        requirement = Requirement(line)
        requirements[requirement.name.lower()] = requirement

    return requirements


def test_runtime_requirements_exclude_known_vulnerable_versions():
    requirements = _load_requirements("requirements.txt")

    expected_minimums = {
        "pyasn1": Version("0.6.3"),
        "pygments": Version("2.20.0"),
        "requests": Version("2.33.0"),
    }

    for name, fixed_version in expected_minimums.items():
        assert name in requirements, f"{name} must be pinned in requirements.txt"
        assert fixed_version in requirements[name].specifier, (
            f"{name} constraint {requirements[name].specifier} does not allow fixed version {fixed_version}"
        )


def test_dev_requirements_exclude_known_vulnerable_versions():
    requirements = _load_requirements("requirements-dev.txt")

    expected_minimums = {
        "black": Version("26.3.1"),
    }

    for name, fixed_version in expected_minimums.items():
        assert name in requirements, f"{name} must be pinned in requirements-dev.txt"
        assert fixed_version in requirements[name].specifier, (
            f"{name} constraint {requirements[name].specifier} does not allow fixed version {fixed_version}"
        )
