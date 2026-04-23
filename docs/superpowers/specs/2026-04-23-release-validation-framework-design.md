# Release Validation Framework Design

Date: 2026-04-23
Status: Approved design for planning
Scope: Reusable release-certification framework for Bash, Python CLI, and Ansible collection validation on real ACM hub lab environments

## Summary

This design adds a dedicated `tests/release/` framework for release certification. The framework is separate from the existing unit, integration, scenario, and general E2E suites. It reuses current helpers where they fit, but owns release policy, profile-driven lab contracts, multi-stream execution, automatic baseline recovery, and release-grade reporting.

The framework must support:

- repeated switchovers on reusable two-hub labs
- certification across Bash, Python, and Ansible form factors
- mandatory Argo CD-managed validation
- profile-driven environment configuration without hardcoded context names in code
- automatic diagnosis and bounded recovery after failures

## Problem

The repository already has strong local test coverage and a Python-centered real-cluster E2E harness, but release confidence still depends on manual interpretation and ad hoc lab usage.

Current gaps:

- no single release-certification control plane for all three streams
- no formal repo-owned environment contract describing what a reusable lab must look like
- no unified way to certify ordinary and Argo CD-managed environments together
- no explicit baseline convergence model before and after mutating scenarios
- no normalized release output that answers whether parity-sensitive capabilities passed across streams

## Goals

- create a reusable release-validation framework that can be rerun for future releases
- keep environment-specific details in profile files, not in runner code
- certify Bash, Python CLI, and Ansible collection entrypoints on the same real lab
- treat Argo CD support as a mandatory release gate
- support repeated switchover cycles with automatic baseline recovery
- emit unified artifacts and a release summary suitable for go/no-go decisions

## Non-Goals

- replace existing unit, integration, parity, or general E2E suites
- reconstruct arbitrary external Git repositories or full GitOps desired state not represented in the profile contract
- modify protected runbook or SKILL documents
- introduce intentional parity divergence between Python and Ansible for dual-supported capabilities

## Recommended Approach

Create a dedicated `tests/release/` layer that sits above the existing test suites.

Why this approach:

- it reuses current Python E2E helpers without overloading `tests/e2e/` with release-policy concerns
- it keeps release certification artifacts and semantics separate from ordinary development E2E runs
- it provides one place to define the lab contract, baseline recovery, stream adapters, and release reporting
- it scales better than separate real-cluster harnesses for Bash, Python, and Ansible

## High-Level Architecture

```text
tests/release/
├── profiles/
│   └── *.yaml
├── contracts/
│   ├── schema.py
│   ├── loader.py
│   └── models.py
├── adapters/
│   ├── bash.py
│   ├── python_cli.py
│   ├── ansible.py
│   └── common.py
├── baseline/
│   ├── discovery.py
│   ├── converge.py
│   ├── recovery.py
│   └── safety.py
├── scenarios/
│   ├── catalog.py
│   └── assertions.py
├── reporting/
│   ├── artifacts.py
│   ├── summary.py
│   └── render.py
├── conftest.py
└── test_release_certification.py
```

Responsibilities:

- `profiles/`: repo-owned lab definitions and release policies
- `contracts/`: schema validation and normalized internal models
- `adapters/`: stream-specific execution for Bash, Python CLI, and Ansible
- `baseline/`: discovery, convergence, recovery, and bounded healing rules
- `scenarios/`: release scenario catalog and cross-stream assertions
- `reporting/`: manifests, per-scenario artifacts, summaries, and human-readable output

Existing `tests/e2e/` modules remain reusable implementation details, especially for orchestration, helpers, monitoring, and cluster inspection.

## Profile Contract

Each release run must load a repo-owned profile file from `tests/release/profiles/`. Profiles are the source of truth for lab-specific values and expected release behavior.

Each profile defines:

- lab topology
  - logical primary hub and secondary hub
  - concrete kube contexts for the selected lab
  - expected managed-cluster inventory or count contract
- required streams
  - whether Bash, Python, and Ansible are all mandatory for the profile
- scenario coverage
  - passive switchover
  - full restore
  - restore-only
  - checkpoint or resume flows
  - decommission
  - resilience and soak scenarios
- Argo CD contract
  - namespaces to inspect
  - app selection expectations
  - required pause and resume behavior
  - allowed role-conflicting resources the framework may clean automatically
- baseline hub state
  - which hub must begin as primary
  - expected backup and restore posture
  - observability expectations
  - RBAC/bootstrap prerequisites
- release limits
  - max cycles
  - soak duration
  - cooldown
  - max tolerated failures
  - artifact retention policy
- recovery policy
  - actions the framework may auto-heal
  - actions that must be treated as a hard stop

Important boundary:

- profiles define the intended lab contract
- the framework restores the lab to that declared contract
- the framework does not pretend to recreate external GitOps repositories that are not represented in the contract

## Baseline Contract

The baseline is a full lab contract, not only a switchover role check.

Required baseline dimensions:

- correct primary and secondary hub roles
- healthy backup schedule and restore state
- expected managed-cluster presence on the active primary
- expected observability posture
- expected RBAC/bootstrap readiness
- mandatory Argo CD expectations for detection and pause/resume safety
- machine-readable assertions proving the lab is inside contract before certification starts

The baseline must be enforced:

- before the first certification scenario
- after any failed mutating scenario
- before soak loops continue
- at end of run, to prove the lab was left in a safe reusable state

## Scenario Model

Release certification is driven by a common scenario catalog rather than separate per-stream test lists.

Scenario families:

- preflight-only
- standard switchover
- restore-only
- checkpoint and resume recovery
- decommission
- Argo CD-managed switchover
- failure-injection and resilience
- multi-cycle soak

Each scenario declares:

- required baseline state
- applicable streams: `bash`, `python`, `ansible`
- whether parity assertions are required
- whether the scenario mutates the lab
- whether recovery is mandatory after execution
- expected artifacts and success signals

This allows one scenario to run Python and Ansible and compare report contracts, while another validates only Bash and Python for script-oriented operational surfaces.

## Stream Adapters

### Bash Adapter

The Bash adapter certifies real-lab operator shell surfaces, including discovery, preflight, postflight, and setup or RBAC-related script flows that remain release-relevant. Bash is primarily a validation and control-surface stream, not the primary switchover mutation engine.

### Python CLI Adapter

The Python adapter remains the richest mutating execution path. It covers ordinary switchovers, resume behavior, restore-only, resilience cases, repeated cycles, and recovery-oriented certification by reusing the strongest existing E2E helpers where appropriate.

### Ansible Adapter

The Ansible adapter certifies collection playbooks and role orchestration on the same lab contract. It must cover preflight, switchover, restore-only, decommission, checkpoint behavior, and Argo CD management entrypoints where applicable.

### Cross-Stream Assertions

Parity-sensitive areas must be checked across streams where dual support is required:

- preflight behavior
- activation and finalization outcomes
- RBAC validation and bootstrap outcomes
- Argo CD detection and pause/resume behavior
- discovery behavior
- machine-readable report contracts
- decommission semantics where dual-supported

## Execution Flow

Each release run follows this flow:

1. Load and validate the selected profile.
2. Discover current lab state across both hubs and managed clusters.
3. Converge the lab to the declared baseline.
4. Run fast static gates first:
   - relevant unit tests
   - integration tests
   - scenario tests
   - parity tests
5. Run the real-cluster certification matrix across Bash, Python, and Ansible according to the selected profile.
6. Execute cross-stream assertions for parity-sensitive capabilities.
7. On failure, capture diagnostics, classify the failure, run bounded recovery, and continue or stop according to policy.
8. Run repeated switchover or soak cycles as declared by the profile.
9. Emit final release summary and confirm whether the lab ended in baseline-compliant state.

This creates a certification pipeline instead of a single monolithic command with implicit operator judgment.

## Recovery, Diagnostics, And Safety

Automatic recovery is required, but it must be bounded and explicit.

On every mutating scenario:

- capture pre-run snapshot
- capture post-run or post-failure snapshot
- store stream output, report artifacts, and cluster facts

Failure classification categories:

- code or test failure
- lab drift or stale resources
- backup or restore health issue
- Argo CD conflict or incomplete resume
- RBAC/bootstrap issue
- unrecoverable environmental fault

Allowed bounded recovery actions include:

- cleaning stale role-conflicting `BackupSchedule` or `Restore` resources allowed by profile policy
- restoring expected primary and secondary hub roles
- waiting for passive restore to settle
- restoring required Argo CD pause/resume expectations
- revalidating managed-cluster presence and observability posture
- rerunning discovery and preflight checks to prove the lab is back inside contract

Safety constraints:

- each recovery step has retry limits
- recovery has a total time budget
- if the lab cannot return to baseline, the run hard-fails
- deletions and other healing actions are allowlisted by profile
- the framework must not improvise destructive cleanup beyond declared resource classes

## Artifacts And Reporting

The release framework emits:

- run manifest with git SHA, tool version, selected profile, and environment facts
- per-scenario artifacts
  - stdout and stderr
  - machine-readable reports
  - snapshots
  - recovery actions
  - timing
- per-cycle artifacts for repeated switchover and soak runs
- normalized summary file showing:
  - stream pass/fail
  - parity-sensitive capability results
  - mandatory Argo CD certification result
  - recovery count and recovery failures
  - final baseline compliance
- human-readable release report for operator go/no-go review

## Operator Workflow

Expected operator workflow:

1. Select a release profile.
2. Run the full certification set for release readiness.
3. If failures occur, inspect structured artifacts.
4. Fix code, rerun failed scenarios or resume certification.
5. Use the same framework for future releases by updating profile values and scenario selection rather than rewriting orchestration logic.

The framework must support focused reruns for debugging and full certification runs for release sign-off.

## Incremental Delivery Plan

Recommended implementation sequence:

1. Add profile schema, loader, and normalized contract model.
2. Add baseline discovery and convergence.
3. Add the release runner and shared artifact model.
4. Reuse existing Python E2E helpers to power Python release scenarios first.
5. Add Ansible stream adapters and parity assertions.
6. Add Bash real-lab adapters for release-relevant script flows.
7. Add bounded recovery automation and soak controls.
8. Add release summary rendering and operator-facing documentation.

This order gets value quickly without blocking on full cross-stream completion before the framework becomes usable.

## Design Decisions

- Use a dedicated `tests/release/` layer rather than expanding `tests/e2e/` into the release control plane.
- Keep environment-specific names and expectations in repo-owned profile files.
- Treat Argo CD support as a mandatory release gate.
- Use a full lab baseline contract, not only switchover role checks.
- Perform automatic diagnosis and bounded recovery after failures.
- Reuse existing E2E helpers as implementation details, but keep release policy separate.

## Success Criteria

This design is successful when the repository can:

- certify Bash, Python, and Ansible release surfaces on a real two-hub lab
- run repeated switchovers without manual lab rebuild between cycles
- validate both ordinary and Argo CD-managed behavior as part of release gating
- recover from bounded failures back to declared baseline
- emit enough structured evidence for a confident release decision

