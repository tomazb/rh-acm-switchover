"""The Ansible-engine parity lane (#371).

These parity tests compare the Python CLI with the collection by running the real Ansible engine
(Ansible's templar, a real AnsibleModule). Root test jobs run without Ansible and pass
`--ignore=tests/ansible_engine`; the `ansible-engine-parity` CI job runs this directory on each
foundation ansible-core lane.

Without Ansible this directory must fail loudly. A skip would let the lane report green while no
engine comparison ran, so the refusal is an error raised while this conftest loads.
"""

import pytest

try:
    # A stub `ansible` package left in sys.modules by another test would not provide this.
    from ansible.release import __version__ as ANSIBLE_CORE_VERSION
except ImportError as exc:
    raise pytest.UsageError(
        "tests/ansible_engine requires the Ansible engine (ansible-core), which is not importable here: "
        f"{exc}. Install the root requirements plus the lane's ansible-core and run this directory on its "
        "own; the root test lane excludes it with --ignore=tests/ansible_engine."
    ) from exc


def pytest_report_header(config):
    return f"ansible-core: {ANSIBLE_CORE_VERSION}"
