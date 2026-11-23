from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
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


# #371: root test jobs run without the Ansible engine. Engine comparisons live in
# tests/ansible_engine/ and run in the `ansible-engine-parity` CI job, which installs the
# foundation lane's own ansible-core next to the root requirements.
ANSIBLE_ENGINE_DISTRIBUTIONS = {"ansible", "ansible-core", "ansible-base"}


def test_root_requirements_do_not_declare_the_ansible_engine():
    for path in ("requirements.txt", "requirements-dev.txt"):
        declared = {canonicalize_name(name) for name in _load_requirements(path)}
        engine = sorted(declared & ANSIBLE_ENGINE_DISTRIBUTIONS)
        assert not engine, f"{path} declares {engine}; root test jobs must run without Ansible (#371)"
