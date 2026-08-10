# Compatibility and Support Policy

This document is the **single authority** for what `tomazb.acm_switchover`
supports. Collection metadata, dependency constraints, the CI matrix, and the
execution-environment build input are all derived from the values stated here,
and `tests/unit/test_compatibility_contract.py` fails if any of them drift apart
from this document.

Externally changing facts are dated. See [Upstream sources](#upstream-sources).

## Support tiers

Support claims in this repository mean one of exactly three things.

| Tier | Meaning |
| --- | --- |
| **Repository-tested** | A CI lane in `.github/workflows/ansible-collection-foundation.yml` runs the collection's unit, integration, scenario, playbook-syntax, and build checks against this exact combination on every pull request and on every push to `ansible`. |
| **Upstream-compatible, not repository-tested** | Permitted by the collection's declared metadata and by upstream support statements, but not exercised by any lane here. Expected to work; not evidence. |
| **Formally certified** | **Nothing. No certification claim is made.** See [Certification](#certification). |

## Supported versions

### `ansible-core`

Declared in `meta/runtime.yml` as `requires_ansible: ">=2.16.0,<2.22"`.

| `ansible-core` | Tier | Notes |
| --- | --- | --- |
| 2.15 and older | **Not supported** | Below the floor. EOL upstream since November 2024, and `kubernetes.core` 6.x refuses it. |
| 2.16 | **Repository-tested** (`min` lane) | The default `ansible-core` of AAP 2.5, 2.6, and 2.7. EOL upstream since July 2025, but maintained downstream by Red Hat for the RHEL 8 lifecycle. Supported here because AAP operators run it, not because upstream still ships fixes for it. |
| 2.17 – 2.20 | Upstream-compatible, not repository-tested | Inside the declared range; no lane runs them. |
| 2.21 | **Repository-tested** (`current` lane) | Latest upstream stable. |
| 2.22 and newer | **Not supported** | Above the ceiling; still in development upstream. |

"Supported" means **both** conditions: repository-tested, *and* permitted by
upstream metadata. The intermediate versions above meet only the second and are
labelled accordingly.

### `kubernetes.core`

Declared identically in `requirements.yml` and `galaxy.yml` as
`kubernetes.core: ">=6.0.0,<7.0.0"`.

Every 6.x release declares `requires_ansible: ">=2.16.0"`, so the constraint is
satisfiable on both lanes. The upper bound excludes an unreleased 7.x from being
resolved into a lane that has never validated it.

Resolution is **bounded, not pinned**: each lane installs the newest release
inside the bound, and the guardrail test proves the resolved release accepts that
lane's `ansible-core`. Pinning an exact version would conceal upstream floor
changes rather than detect them — `kubernetes.core` raised its floor inside a
patch release (5.4.3 declared `>=2.15.0`, 5.4.4 declared `>=2.16.0`), so floor
changes are not confined to major bumps.

### Python

Control-node Python follows from the `ansible-core` lane; the collection declares
no Python requirement of its own.

| Lane | `ansible-core` | Control-node Python supported upstream | Lane Python |
| --- | --- | --- | --- |
| `min` | `2.16.*` | 3.10 – 3.12 | **Python 3.11** |
| `current` | `2.21.*` | 3.12 – 3.14 | **Python 3.12** |

The two upstream ranges intersect only at 3.12, so **3.12 is the only Python
version that could run both lanes**. The lanes deliberately do not both use it:
running `min` on 3.11 and `current` on 3.12 means the matrix covers two
interpreters instead of one, and 3.11 is the Python that AAP's `ansible-core`
2.16 execution environments ship. Consolidating both lanes on 3.12 would be a
valid choice; it would trade interpreter coverage for a smaller matrix.

Note that 3.11 is a valid Python for the `min` lane only. It is below the floor
of `ansible-core` 2.21, so it cannot serve the `current` lane — which is why the
workflow's previous hard-coded `python-version: "3.11"` could not have been
carried forward unchanged.

The execution environment is a separate case: it must satisfy the *whole*
declared range from a single interpreter, so it pins 3.12 (see
[Execution environment](#execution-environment)).

The repository's Python CLI targets 3.10 – 3.12 (`setup.cfg`, `.github/workflows/ci-cd.yml`).
That is a separate surface and is unaffected by this policy.

### Python `kubernetes` client

`requirements.txt` declares `kubernetes>=28.0.0`, unified with the repository-root
`requirements.txt` and the CI lanes. `kubernetes.core` itself requires only
`>=24.2.0`; the higher floor here is a deliberate tightening, not a conflict.

## Ansible Automation Platform

**No AAP combination is repository-tested.** This repository runs no AAP job and
builds no execution-environment image.

| AAP | Default `ansible-core` | Tier |
| --- | --- | --- |
| 2.7 | 2.16 | Upstream-compatible, not repository-tested |
| 2.6 | 2.16 | Upstream-compatible, not repository-tested |
| 2.5 | 2.16 | Upstream-compatible, not repository-tested |
| 2.4 | 2.15 | **Not supported** — below the floor; EUS-only since 2026-07-01 |

AAP 2.5 through 2.7 are listed as upstream-compatible on one specific basis: each
defaults to `ansible-core` 2.16, and 2.16 *is* a repository-tested lane. That is
an inference from the tested lane, not a test of AAP itself.

### Execution environment

`execution-environment.yml` is a **build input only. It is not
repository-tested** — no CI lane builds it, and its output is not published.

Its base image is `docker.io/redhat/ubi9:latest`, the subscription-free base used
by the upstream ansible-builder v3 minimal example, with `ansible-core` pinned
explicitly through `dependencies.ansible_core.package_pip` so the EE cannot drift
from the range above.

UBI 9 provides Python **3.9** as the default `python3`, which is below the floor
of every `ansible-core` in the supported range, so the definition also declares
`dependencies.python_interpreter` (`package_system: python3.12`,
`python_path: /usr/bin/python3.12`). Without it the pip stage cannot resolve
`ansible-core` at all and the build fails. Python 3.12 is used because it is the
only version supported by every core in the range. Verified on
`docker.io/redhat/ubi9:latest` (2026-08-10): the default interpreter reports
Python 3.9.25, `python3.12`/`python3.12-pip` are available from
`ubi-9-appstream-rpms`, and `python3.12 -m pip install "ansible-core>=2.16,<2.22"`
resolves 2.21.2.

AAP operators should substitute their entitled `ee-minimal-rhel9` base for their
AAP version, which already ships a supported `ansible-core`.

The previous base image, `quay.io/ansible/ansible-runner:stable-2.15-latest`, did
not exist: that quay repository has been unmaintained since 2022, its newest tag
is `stable-2.12-latest`, and it now directs users to `ansible-builder >= 3.0`.

### Certification

**No formal certification claim is made for this collection.** Red Hat Certified
Collection status is a partner program requiring a shared statement of support
between Red Hat and the partner, a granted Automation Hub namespace, and
Red Hat-side acceptance. It cannot be self-asserted by publishing code, and none
of those steps has been taken here.

## CI lanes

Both lanes run on every pull request and on every push to `main` and `ansible`.
Two lanes are sufficient because compatibility risk in a bounded range is
concentrated at its endpoints: the floor is where dependency resolution fails,
and the newest series is where upstream behaviour changes land.

| Lane | `ansible-core` | Python | Purpose |
| --- | --- | --- | --- |
| `min` | `2.16.*` | 3.11 | the declared floor and the AAP default |
| `current` | `2.21.*` | 3.12 | the newest tested series |

Each lane runs: dependency resolution, the compatibility guardrail, unit tests,
integration tests, scenario tests, playbook syntax checks for every shipped
playbook, and the collection archive build.

### Unsupported-version warnings are failures

`ansible-playbook` prints `Collection kubernetes.core does not support Ansible
version X` and continues, so an incompatible resolution can pass CI unnoticed.
That is the defect this policy exists to close, and two independent mechanisms
prevent it:

1. **Guardrail test** (primary). After `ansible-galaxy collection install`,
   `test_resolved_kubernetes_core_supports_the_running_ansible_core` reads the
   *installed* `kubernetes.core` `meta/runtime.yml` and asserts the lane's
   running `ansible-core` satisfies it. This evaluates the same condition Ansible
   evaluates, as an assertion, so it also catches an upstream floor bump the
   moment it is resolved.
2. **Syntax-check backstop.** The syntax-check step tees its output to a file and
   fails the lane if it matches `does not support Ansible version`, for any
   collection including transitively resolved ones.

## Local validation

These commands cover the same surfaces as the CI lanes, in the same order.

```bash
# 1. Resolve dependencies exactly as a lane does
ansible-galaxy collection install -r ansible_collections/tomazb/acm_switchover/requirements.yml

# 2. Compatibility guardrail (metadata, EE, CI matrix, resolved dependency)
export ANSIBLE_COLLECTIONS_PATH="$PWD:$HOME/.ansible/collections"
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_compatibility_contract.py -q

# 3. Unit tests
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q

# 4. Integration and scenario tests
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/ \
  ansible_collections/tomazb/acm_switchover/tests/scenario/ -q

# 5. Playbook syntax checks, for every shipped playbook
for playbook in ansible_collections/tomazb/acm_switchover/playbooks/*.yml; do
  ansible-playbook "$playbook" --syntax-check
done

# 6. Collection archive build
(cd ansible_collections/tomazb/acm_switchover && \
   ansible-galaxy collection build --output-path /tmp/dist --force)
```

**Reproducing a specific lane.** The repository's shared `.venv` tracks the
`current` lane only. Reproducing the `min` lane needs its own environment:

```bash
python3.11 -m venv .venv-lane-min
source .venv-lane-min/bin/activate
pip install "ansible-core==2.16.*" pytest PyYAML "kubernetes>=28.0.0"

# The collection dependencies must be resolved again inside this environment:
# a lane is defined by its ansible-core *and* what that core resolves.
ansible-galaxy collection install -r ansible_collections/tomazb/acm_switchover/requirements.yml
export ANSIBLE_COLLECTIONS_PATH="$PWD:$HOME/.ansible/collections"
```

Then run steps 2 – 6 above unchanged; that is the full `min` lane.

`requirements-dev.txt` pins `ansible-core>=2.18.1` on Python 3.11 and newer. That
sits inside the supported range but does not reach the `min` lane floor, which is
why the separate environment above is required to reproduce it.

**Scope of this section.** The commands above reproduce the *collection* lanes in
`ansible-collection-foundation.yml`. They are not the repository's whole test
surface: the root suite (`python -m pytest tests/ -q`) runs in `ci-cd.yml` on its
own Python matrix, and it includes `tests/test_ci_guardrails.py`, which asserts
properties of the collection workflow itself. Run the root suite as well when
changing this workflow or the policy constants.

## Collection version lifecycle

The collection's `galaxy.yml` `version` **follows the repository-wide release
version**; it does not have an independent release lifecycle. This is a
deliberate decision, not a coincidence of both currently reading the same value.

`tests/unit/test_collection_metadata.py` enforces it, asserting equality between
`galaxy.yml`, `lib/__init__.py`'s `__version__`, the Helm chart, and five further
release surfaces. Changing this coupling requires reworking that test and
defining a separate collection release process.

## Upstream sources

All accessed **2026-08-10**. These facts change; re-verify them when revising
this document.

| Fact | Source |
| --- | --- |
| `ansible-core` GA/EOL dates and control-node Python per version. 2.15 EOL Nov 2024, 2.16 EOL Jul 2025, 2.17 EOL Nov 2025, 2.18 EOL May 2026, 2.19 EOL Nov 2026, 2.21 latest stable, 2.22 in development. No upstream LTS track. | <https://docs.ansible.com/ansible-core/devel/reference_appendices/release_and_maintenance.html> |
| AAP lifecycle: 2.7 and 2.6 in full support, 2.5 in maintenance, 2.4 EUS-only since 2026-07-01. | <https://access.redhat.com/support/policy/updates/ansible-automation-platform> |
| AAP default `ansible-core` per release (2.16 for AAP 2.5/2.6/2.7; 2.15 for AAP 2.4). | <https://access.redhat.com/support/policy/updates/ansible-automation-platform-execution-environments> |
| `kubernetes.core` `requires_ansible` by release, and the 5.4.3 → 5.4.4 floor bump. | Galaxy v3 API collection versions endpoint, and `https://raw.githubusercontent.com/ansible-collections/kubernetes.core/<tag>/meta/runtime.yml` |
| `quay.io/ansible/ansible-runner` is unmaintained; newest tag `stable-2.12-latest`, last modified 2022-04-29; `stable-2.15-latest` does not exist. | <https://quay.io/api/v1/repository/ansible/ansible-runner> |
| ansible-builder v3 definition schema and its minimal example base image. | <https://docs.ansible.com/projects/builder/en/latest/definition/> |
| Red Hat certification requirements for Ansible Certified Content. | <https://access.redhat.com/articles/4916901> |

## Change process

Changing any supported version means changing this document **and** the surfaces
derived from it, in the same commit:

- `meta/runtime.yml` — `requires_ansible`
- `requirements.yml` and `galaxy.yml` — the `kubernetes.core` constraint, identical in both
- `execution-environment.yml` — the `ansible_core` pin
- `.github/workflows/ansible-collection-foundation.yml` — the lane matrix
- `tests/unit/test_compatibility_contract.py` — the policy constants
- `CHANGELOG.md` — under `[Unreleased]`, because the supported contract changed

The guardrail tests fail on any partial update, which is the point.
