# Strict continuation implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for
> coordinator-owned implementation. Steps use checkbox syntax for tracking.

**Goal:** Close #345 without changing the strict-read algebra or consumer interfaces.

**Architecture:** Validate raw continuation types in the existing independent Python and
collection parsers. Use the shared real-producer parity harness and consumer/runtime
regressions to prove malformed evidence cannot authorize teardown.

**Tech stack:** Python, pytest, Ansible, fake Kubernetes transports.

**Spec:** [Strict LIST continuation safety](2026-10-16-strict-continuation-safety-design.md).

## Global constraints

- Missing, null and string tokens remain accepted; all other types are malformed.
- No protected-file, RBAC, support, state-format, version or intentional parity change.
- No legacy-reader migration, new request, retry, public option or outcome.
- Keep diagnostics sanitized and abandon the entire malformed inventory.

## Review focus

Falsey type coercion; truthy tokens reaching request serialization; later-page prefix
leakage; caller success/absence despite error; check-mode and credential-output safety.

### Task 1: Token validation and regression evidence

**Files:** `lib/kube_client.py`, collection `plugins/module_utils/k8s_read.py`,
`tests/test_strict_read_parity.py`, `tests/test_strict_read_continuation_safety.py`,
collection `tests/unit/test_k8s_read_outcome.py`,
`tests/unit/test_managed_cluster_continuation_runtime.py`, and
`tests/integration/test_k8s_read_outcome_runtime.py`.

**Interfaces:** Existing strict LIST outcomes only. The Python producer returns
`StrictReadOutcome.error(malformed_response)`; collection retains its existing
`read_status: error`, empty `resources` and null `resource_version` result, without
adding a reason field. Neither publishes partial resources or revision.

- [ ] Add shared vectors for false, zero, lists, mappings, true, positive numbers and a
  sensitive sentinel token nested in a non-string value; first/later-page errors must
  stop requests, clear items/revision and never report absence.
- [ ] Add valid null/empty-string exhaustion and opaque-string forwarding controls.
- [ ] Add real-reader destructive-consumer regressions through both normal decommission
  safety paths, including a complete-empty positive control. The malformed response must
  block the real UID-guarded DELETE and leave changed false. Add collection unit and module
  runtime/check-mode tests. No production cluster access.
- [ ] Run new regressions against unchanged base code; record attributable RED failures.
- [ ] Add the two raw-token type checks; normalize only after validation.
- [ ] Run targeted regressions and existing reader/consumer tests to GREEN.
- [ ] Independently revert each guard locally as a deliberate mutant; prove source change,
  run the discriminating test, record its assertion failure, restore and recheck GREEN.

### Task 2: Delivery gates

**Files:** Coexistence/behavior documentation, `CHANGELOG.md` and
`thermos-resolution-plan.md`; design and plan above.

- [ ] Document the tightened malformed-response behavior, preserving capability status.
- [ ] Run root and collection required local gates and inspect complete results.
- [ ] Perform builder simplification review, then scoped Thermos and CodeRabbit review;
  disposition findings before PR creation. Review any relevant changed behavior again.
- [ ] Commit/push and create a draft PR targeting ansible after all required local gates
  and pre-creation review pass. Record the exact candidate head and read back the PR.
- [ ] Hand off base/head/merge-base, boundaries, evidence and outstanding hosted CI to an
  independent validator using a separate clean checkout. Stop at builder handoff.

The operator's #345 Builder Prompt supersedes the earlier coordinator's delivery authority
for this slice. The builder must not mark ready, merge, enable auto-merge, resolve threads,
or begin the Independent Validator or PR-comment resolver stages.
