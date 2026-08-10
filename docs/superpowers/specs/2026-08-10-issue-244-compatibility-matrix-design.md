# Ansible/AAP Compatibility Matrix — Design

Status: design for issue #244 (compatibility and CI contract).
Primary deliverable: a new collection compatibility authority document, plus the
metadata, dependency, CI, and guardrail-test changes that enforce it.

**Date:** 2026-08-10
**Issue:** #244 — Compatibility: define and enforce the supported Ansible/AAP dependency matrix
**Branch:** `feat/issue-244-compatibility-matrix`

## Problem

The collection has no single compatibility authority. Five surfaces each state a
different supported combination, and they contradict each other:

| Surface | Current claim | Reality (see [Upstream evidence](#upstream-evidence)) |
|---|---|---|
| `meta/runtime.yml` | `requires_ansible: ">=2.15.0"`, no upper bound | `ansible-core` 2.15 reached EOL in November 2024 |
| `requirements.yml` / `galaxy.yml` | `kubernetes.core: ">=3.0.0"`, no upper bound | resolves to 6.5.0, which declares `requires_ansible: ">=2.16.0"` |
| `.github/workflows/ansible-collection-foundation.yml` | `pip install ansible-core==2.15.*`, one lane, Python 3.11 | emits `Collection kubernetes.core does not support Ansible version 2.15.13` during playbook syntax checks **and still reports success** |
| `execution-environment.yml` | base image `quay.io/ansible/ansible-runner:stable-2.15-latest` | that tag does not exist; the quay repository is unmaintained since 2022 |
| CI branch triggers | `push: branches: [main]` | repository policy makes `ansible` the primary development and integration branch, so collection CI never runs post-merge |

Two consequences follow. First, the repository's only automated collection lane
passes while simultaneously proving its own combination unsupported. Second, no
document distinguishes a combination this repository actually tests from one
that is merely expected to work upstream, and the collection is described as
"production-ready … for `ansible-core` CLI and Ansible Automation Platform"
without saying which versions of either.

## Goal

One authoritative, evidence-based compatibility policy for the collection,
enforced consistently in metadata, dependency constraints, CI, tests, and
documentation, with support claims graded into three explicit tiers:

1. **Repository-tested** — a CI lane runs against this exact combination.
2. **Upstream-compatible, not repository-tested** — permitted by declared
   metadata and upstream support statements, but not exercised here.
3. **Formally certified** — none. No certification claim is made.

## Decisions

### D1 — Supported `ansible-core` range: `>=2.16.0,<2.22`

`ansible-core` 2.16 is the default core in every currently supported Ansible
Automation Platform release (AAP 2.5, 2.6, and 2.7), and Red Hat maintains it
downstream on its own schedule to cover the RHEL 8 lifecycle. It is also exactly
the floor declared by the current `kubernetes.core` 6.x line. Those two facts
coincide, which makes 2.16 the only floor that keeps the collection installable
on a stock AAP execution environment while still permitting a current
`kubernetes.core`.

2.16 is nonetheless EOL upstream (July 2025). The compatibility document states
this contrast directly rather than eliding it: 2.16 is repository-tested and
AAP-default, and simultaneously unmaintained by the upstream community project.
That is precisely the repository-tested versus upstream-compatible distinction
the issue requires.

The upper bound `<2.22` names the newest tested series (2.21, the current
upstream stable) and excludes 2.22, which is still in development. Support means
**both** conditions: tested by this repository *and* permitted by upstream
metadata. Combinations meeting only the second condition are documented as
upstream-compatible and are not claimed as supported.

Rejected alternatives:

- **Floor 2.19** (oldest upstream-maintained core). A cleaner upstream story, but
  it drops the default core of every supported AAP release; the collection would
  no longer install on a stock AAP 2.6/2.7 execution environment.
- **Retain the 2.15 floor**, pinning `kubernetes.core<=5.4.3` (the last release
  accepting core 2.15). This removes the warning but preserves a core that has
  been EOL since November 2024 and is reachable only through the AAP 2.4 EUS
  add-on, and it freezes the collection on a maintenance branch.

### D2 — Supported `kubernetes.core` range: `>=6.0.0,<7.0.0`

Declared identically in `requirements.yml` and `galaxy.yml`. Every 6.x release
declares `requires_ansible: ">=2.16.0"`, so the constraint is satisfiable on both
tested lanes, and bounding the major series prevents an unreleased 7.x from
being resolved into a lane it has never been validated against. No 7.x exists at
the time of writing.

Dependency resolution is **bounded**, not pinned: CI resolves the newest release
inside the bound on each lane, and a guardrail test proves the resolved release
is compatible with that lane's core. Pinning an exact version would hide
upstream floor changes rather than detect them — `kubernetes.core` raised its
floor inside a patch release (5.4.3 → 5.4.4), so floor changes are not confined
to major bumps and must be caught by a check rather than assumed.

### D3 — Python runtime

Control-node Python is determined by the `ansible-core` lane, not chosen freely:

| Lane | `ansible-core` | Control-node Python supported upstream | Lane Python |
|---|---|---|---|
| min | 2.16.x | 3.10 – 3.12 | **3.11** |
| current | 2.21.x | 3.12 – 3.14 | **3.12** |

The two ranges intersect only at 3.12, so a single-Python matrix cannot cover
both lanes; the workflow's present hard-coded `python-version: "3.11"` is
incompatible with any current core. The collection declares no `python_requires`
of its own and does not introduce one: its supported Python surface is whatever
the selected core supports, and the tested surface is the two lane values above.

The repository's own Python CLI targets 3.10–3.12 (`setup.cfg`, `ci-cd.yml`);
that range is unaffected by this slice and remains separate from the collection
control-node range.

### D4 — AAP and execution-environment posture

- **Repository-tested:** nothing on AAP. The repository runs no AAP job and
  builds no execution-environment image. Stated plainly.
- **Upstream-compatible, not repository-tested:** AAP 2.5, 2.6, and 2.7, on the
  basis that each defaults to `ansible-core` 2.16, which *is* a repository-tested
  lane. AAP 2.4 is outside the supported surface: it defaults to core 2.15, below
  the declared floor, and is EUS-only since 2026-07-01.
- **Formally certified:** none, and none claimed. Certification is a Red Hat
  partner program requiring a partner agreement, a granted Automation Hub
  namespace, and Red Hat-side acceptance; it cannot be self-asserted.
- **Execution environment:** `execution-environment.yml` is repaired and declared
  a *build input only*, not a repository-tested artifact. Its base image is
  repointed to `docker.io/redhat/ubi9:latest` (the base used in the upstream
  ansible-builder v3 minimal example, pullable without a subscription), and the
  core version is pinned explicitly through `dependencies.ansible_core.package_pip`
  so the EE cannot silently drift from the declared policy. AAP operators are
  directed to substitute their subscription `ee-minimal-rhel9` base.

  No CI build lane is added. Building the EE requires container tooling that this
  slice does not otherwise need, and an unbuilt-but-honest definition is
  preferable to a broken one either way; the "not repository-tested" label is the
  accurate description of what CI proves.

### D5 — CI matrix and branch triggers

Two lanes, both running on pull requests and on push to `ansible`:

| Lane | `ansible-core` | Python | Purpose |
|---|---|---|---|
| `min` | `2.16.*` | 3.11 | the declared floor and the AAP default |
| `current` | `2.21.*` | 3.12 | the newest tested series |

Two lanes are sufficient because the declared range's compatibility risk is
concentrated at its endpoints: the floor is where dependency resolution fails,
and the newest series is where upstream behaviour changes land. A third
intermediate lane (2.19) was considered and rejected — it adds roughly 50% CI
time per pull request while testing a series that neither anchors the range nor
matches any AAP default.

`push: branches: [main, ansible]` closes the post-merge gap: today the collection
workflow's push trigger names only `main`, so under the `ansible` branch policy
it fires exclusively on the pre-merge pull-request event and merge commits on the
integration branch receive no collection validation at all.

**Unsupported-version warnings become hard failures** through two independent
mechanisms:

1. *Primary, deterministic.* A guardrail check runs after
   `ansible-galaxy collection install`, reads the **installed**
   `kubernetes.core` `meta/runtime.yml`, and asserts that the lane's running
   `ansible-core` version satisfies the declared `requires_ansible`. This
   evaluates the same condition Ansible itself evaluates when it emits the
   warning, but as a test rather than as log output, so it also catches a future
   upstream floor bump the moment it is resolved.
2. *Backstop.* The playbook syntax checks tee their combined output to a file,
   and the step fails if the output matches `does not support Ansible version`.
   This catches the same class of warning for any collection, including ones
   pulled in transitively, without the guardrail needing to enumerate them.

The primary mechanism is the contract; the backstop exists because log-visible
warnings that no test models should still not pass silently.

### D6 — Collection release metadata relationship

The collection's `galaxy.yml` version **follows the repository-wide release
version** and does not have an independent lifecycle. This is recorded as a
deliberate decision rather than inferred from the fact that both currently read
`1.7.10`. The coupling is already enforced by
`tests/unit/test_collection_metadata.py`, which asserts equality between
`galaxy.yml`, `lib/__init__.py`'s `__version__`, the Helm chart, and five other
release surfaces. No change to versioning behaviour results from this slice; the
decision is written down so a future reader need not re-derive it.

## Upstream evidence

All sources accessed **2026-08-10**. These facts change over time; the
compatibility document carries the same list with the same access date and is the
surface to revisit.

| Fact | Source |
|---|---|
| `ansible-core` GA/EOL dates and control-node Python per version: 2.15 EOL Nov 2024; 2.16 EOL Jul 2025; 2.17 EOL Nov 2025; 2.18 EOL May 2026; 2.19 EOL Nov 2026 (oldest maintained); 2.21 latest stable; 2.22 in development. Python: 2.16 → 3.10–3.12, 2.19 → 3.11–3.13, 2.21 → 3.12–3.14. Upstream has no LTS track. | <https://docs.ansible.com/ansible-core/devel/reference_appendices/release_and_maintenance.html> |
| AAP lifecycle: 2.7 and 2.6 in full support; 2.5 in maintenance; 2.4 EUS-only since 2026-07-01. | <https://access.redhat.com/support/policy/updates/ansible-automation-platform> |
| AAP default `ansible-core`: 2.16 for AAP 2.5, 2.6, and 2.7 ("to be able to continue to support RHEL8 for its entire lifecycle"); 2.15 for AAP 2.4; core 2.17 no longer supported. | <https://access.redhat.com/support/policy/updates/ansible-automation-platform-execution-environments> |
| `kubernetes.core` `requires_ansible` by release: `>=2.14.0` (3.x, 4.x), `>=2.15.0` (5.0.0–5.4.3), `>=2.16.0` (5.4.4 and all 6.x). Newest release is 6.5.0; no 7.x exists. All majors require python `kubernetes>=24.2.0`. | Galaxy v3 API `.../collections/index/kubernetes/core/versions/` and `https://raw.githubusercontent.com/ansible-collections/kubernetes.core/<tag>/meta/runtime.yml` |
| `quay.io/ansible/ansible-runner` is unmaintained ("This repository is no longer maintained. Consider using ansible-builder >= 3.0"); all ten active tags last modified 2022-04-29; newest is `stable-2.12-latest`; `stable-2.15-latest` does not exist. | <https://quay.io/api/v1/repository/ansible/ansible-runner> and `.../tag/?onlyActiveTags=true` |
| ansible-builder v3 definition schema: `images.base_image.name`, `dependencies.{ansible_core,ansible_runner}.package_pip` (any pip-compatible specifier), `dependencies.{galaxy,python,system}`. Upstream minimal example uses `docker.io/redhat/ubi9:latest`. | <https://docs.ansible.com/projects/builder/en/latest/definition/> |
| Certified collection status requires a Red Hat/partner shared statement of support, a granted Automation Hub namespace, at least two supported `ansible-core` versions, and passing galaxy-importer, `ansible-test sanity`, and ansible-lint. It cannot be self-asserted. | <https://access.redhat.com/articles/4916901> |

## Changed files

| File | Change |
|---|---|
| `ansible_collections/tomazb/acm_switchover/meta/runtime.yml` | `requires_ansible: ">=2.16.0,<2.22"` |
| `ansible_collections/tomazb/acm_switchover/requirements.yml` | `kubernetes.core: ">=6.0.0,<7.0.0"` |
| `ansible_collections/tomazb/acm_switchover/galaxy.yml` | `dependencies.kubernetes.core` matched to the same constraint |
| `ansible_collections/tomazb/acm_switchover/requirements.txt` | `kubernetes>=28.0.0`, unifying with the root `requirements.txt` and the CI install (`kubernetes.core` needs only `>=24.2.0`, so this is a tightening) |
| `ansible_collections/tomazb/acm_switchover/execution-environment.yml` | real base image, explicit `ansible_core`/`ansible_runner` pins, header comment recording the not-repository-tested posture |
| `.github/workflows/ansible-collection-foundation.yml` | two-lane matrix, `push: [main, ansible]`, per-lane Python, compatibility guardrail step, syntax-check warning backstop |
| `ansible_collections/tomazb/acm_switchover/tests/unit/test_compatibility_contract.py` | new guardrail tests (see below) |
| `ansible_collections/tomazb/acm_switchover/tests/unit/test_collection_metadata.py` | replace the `requires_ansible.startswith(">=")` assertion, which tested the constraint's shape rather than its value and is why this drift went unnoticed |
| `ansible_collections/tomazb/acm_switchover/docs/compatibility.md` | new — the single compatibility authority |
| `ansible_collections/tomazb/acm_switchover/docs/distribution.md` | EE row corrected to a build input that is not repository-tested; link to `compatibility.md` |
| `ansible_collections/tomazb/acm_switchover/docs/coexistence.md` | the `ansible-core 2.15–2.18` range aligned to the 2.16 floor |
| `ansible_collections/tomazb/acm_switchover/README.md` | short Compatibility section linking `compatibility.md` |
| `CHANGELOG.md` | `[Unreleased] → Changed` entry; the supported dependency and runtime contract changed |

### Guardrail tests

`test_compatibility_contract.py` holds the policy in one module-level constant
block — the single place the matrix is expressed in code — and asserts:

- `meta/runtime.yml` declares exactly the policy constraint;
- `galaxy.yml` and `requirements.yml` declare the same `kubernetes.core`
  constraint (no test references `requirements.yml` today, which is how those two
  files were free to diverge);
- the EE definition does not reference the unmaintained `ansible/ansible-runner`
  repository, its `ansible_core` pin agrees with `requires_ansible`, and its
  galaxy/python/system inputs name files that exist;
- the workflow's parsed matrix lanes equal the documented lanes, and each lane's
  core satisfies both the policy floor and the `kubernetes.core` floor — this is
  what prevents CI, metadata, and documentation from drifting apart silently;
- (runtime-only, skipped when the collection is not installed) the installed
  `kubernetes.core` accepts the running `ansible-core`.

Tests follow the accumulate-then-assert style of `tests/test_constants_parity.py`
and reuse `tests/unit/yaml_contract_helpers.py`.

## Non-goals

- No production switchover behaviour change, no Python CLI change, no RBAC change.
- No AAP job, no live lab mutation, no certification execution.
- No `AGENTS.md` edit. Issue #245 is the repository-wide `AGENTS.md` refresh and
  is blocked on this issue; it must reference this document rather than copy the
  matrix. Because `AGENTS.md:301-307` currently holds the collection's local
  validation commands and is off-limits here, the CI-matching local validation
  guidance lands in `compatibility.md` instead.
- No change to `requirements-dev.txt`. Its `ansible-core>=2.17.14,<2.18` (Python
  < 3.11) and `>=2.18.1` (Python ≥ 3.11) pins already resolve inside the new
  policy range. The relationship is recorded, not adjusted: the dev pin does not
  reach the 2.16 min lane, so reproducing that lane locally needs a dedicated
  virtual environment, which `compatibility.md` documents.
- No protected-file edit (`docs/ACM_SWITCHOVER_RUNBOOK.md`,
  `.claude/skills/**/*.skill.md`).
- No unrelated collection refactor; `build_ignore` and the collection's missing
  Galaxy metadata fields are out of scope.

## Verification

Per lane, locally and on exact-head CI:

```bash
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q
ANSIBLE_COLLECTIONS_PATH="$PWD:$HOME/.ansible/collections" \
  python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/ \
                   ansible_collections/tomazb/acm_switchover/tests/scenario/ -q
ansible-galaxy collection install -r ansible_collections/tomazb/acm_switchover/requirements.yml
for p in ansible_collections/tomazb/acm_switchover/playbooks/*.yml; do
  ansible-playbook "$p" --syntax-check
done
(cd ansible_collections/tomazb/acm_switchover && \
   ansible-galaxy collection build --output-path /tmp/dist --force)
python -m pytest tests/ -q
git diff --check
```

Plus the pre-push gates from `AGENTS.md` (`black --line-length 120`, `isort`,
`flake8`, `mypy`, `bandit`) over the changed Python surface.

The shared `.venv` is Python 3.14 with `ansible-core` 2.21 and can express only
the `current` lane; the `min` lane needs a separate Python 3.11 environment.

**Falsification.** The guard is proven by reverting the `kubernetes.core`
constraint to `>=3.0.0` on the min lane and confirming the lane *fails* rather
than warns. A change that leaves that lane green has not implemented this design.

## Governance

Governed slice under the repository's Builder → Independent Validator →
PR-comment Resolver workflow, terminating per
[Terminal Validation and Review Convergence](../../../AGENTS.md#terminal-validation-and-review-convergence).
The acceptance gate is falsifiable: the two lanes exist and pass, the falsification
case above fails, and the guardrail tests hold the metadata, workflow, and
document to the same matrix.
