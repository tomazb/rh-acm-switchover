# Strict LIST continuation safety (#345)

## Authority and scope

Base: `8b88910bf280d73e611b91908854e01a20c0fa54` on `origin/ansible`.
The operator's independent safety-coordinator instruction and subsequent #345 Builder Prompt
authorize this bounded fix, its ordinary design decisions, tests, documentation,
pre-creation review and draft PR. The later prompt stops this session at builder handoff. Issue
[#345](https://github.com/tomazb/rh-acm-switchover/issues/345) specifies the accepted token
types. Current strict producers and `tests/test_strict_read_parity.py` own the implemented
complete-inventory behavior, classification, bounded pagination and sanitization. The
[R4-03 July design, section 3](2026-07-29-decommission-completion-design.md#3-shared-strict-inventory-primitive)
provides design context; historical amendments are not current runtime authorities.
This slice changes neither that algebra nor the legacy non-strict readers.

The start gate recorded base, head and merge base as the SHA above, a clean isolated
`fix/345-strict-continuation` branch, and an empty protected-file diff before edits.
The runbook and `.claude/skills/**` remain outside the edit boundary. No RBAC, support,
checkpoint-format, version, configuration or intentional parity change is authorized.

## Independent priority assessment

All candidates were open and unassigned, with no open repository PR, at reconnaissance.
The post-merge foundation run [38867727367](https://github.com/tomazb/rh-acm-switchover/actions/runs/38867727367)
passed both supported lanes at the base SHA. The previous session's worktrees and the
root checkout's untracked files were left untouched.

The ranking weighs concrete consequences, barriers and triggering conditions. It is not
a claim that a malformed API response has occurred in production.

| Order | Candidate | Mechanism, impact and trigger | Barriers, coverage and implementation boundary |
| --- | --- | --- | --- |
| 1 | #345 | Both strict readers turn false, zero, empty list or empty mapping continuation into exhaustion. An incomplete Hive ClusterDeployment inventory can pass `preserveOnDelete` before ManagedCluster deletion. Requires nonconformant API/proxy output. | UID-guarded deletion protects object identity, not omitted Hive evidence. Existing tests cover page failure, expiry and revision mismatch, but miss continuation types. Small paired reader fix; no operator decision needed. |
| 2, conditional | #352 | Python trusts a pre-fix klusterlet completion marker and skips wrong-hub verification. A local full-verifier probe passed without kubeconfig/Secret reads with a valid completed-Restore journal and legacy marker. | Journal-free legacy state is refused. Existing failure/retry tests do not seed old completion. Additive proof/revalidation requires a checkpoint compatibility decision; do not reset unrelated state. Collection has no identical Python step marker. |
| 3 | #362 | A ManagedCluster endpoint can change between Python target selection and remediation. A local orchestration probe patched/restarted the previously selected endpoint after such a change. | Target/client endpoint proof and fresh destination import Secret remain, but do not re-prove current spoke association. Cache starts empty at normal verifier entry, narrowing the reported long-lived-cache condition. Collection re-prepares between invocations and has a stale-target negative test. A fresh whole-batch pre-write inventory comparison is the bounded next candidate. |
| 4 | #342 | Python ordinary Backup LIST 404 becomes an empty baseline; a later successful poll accepts an old Backup as new. Reproduced with real raw LIST and continuity code. Recorded-name integrity skips age checks, permitting later finalization. | Needs a failed baseline followed by recovery. Collection's timestamp selection blocks this mechanism. Existing API-error tests mock above the 404 conversion. Use the strict reader with targeted resume/poll tests; preserve parity. |
| 5 | #346 | Collection execute preflight can skip live discovery when seeded output variables exist, permitting preparation on unsupported evidence. | Same-named extra vars override even private registers; setting outputs is unsupported, as the owner's issue comment states. Identity, activation and teardown perform later live checks. Missing execute seeded-versus-live regressions. Broader fixture conversion; no expanded extra-vars threat model authorized. |
| 6 | #347 | ManagedCluster-backup checks can pass vacuously on ordinary unreadable inventory. Python's later strict derivation can recover, allowing a transient false preflight pass. | Persistent unreadability blocks derivation. A focused coordinator probe confirms the transient case, not eventual migration success. Both implementations need aligned strict-input negative tests; no new RBAC. |
| 7 | #350 | Official Python kubeconfig loading can accept a chain that strict post-activation loading rejects after earlier mutations. | Strict post-activation gate blocks further work. Collection maps per-cluster credentials differently. Existing strict-loader tests cover refusal, not early preflight. Reuse strict merge without restoring the superseded secondary-context endpoint requirement. |
| 8 | #341 | Collection collision repair can delete a BackupSchedule, then fail recreation and the rescue locator read, leaving no schedule. | Loud failure, not false success. Current tests require the locator gate; Python lacks identical rescue. Needs recovery/parity design before removing any proof. |
| 9 | #284 | Some collection role paths allow mutation in validate mode; standalone Argo CD resume is reachable. | Normal switchover/restore-only stop after preflight and decommission refuses validate. Whole-collection mutation/state audit is larger; current issue explicitly withholds implementation authorization. |
| 10 | #360 | Worker-cap tests do not measure concurrent API work at the cap. | Validated cap and explicit executor limit exist; no demonstrated runtime violation. Test-only SSA-03 residual, lower operational urgency. |

The tracker-prioritized R4-01/TR2D-02 and R4-02 remain serious transaction/recovery work,
including whole-syncPolicy restoration and whole-ConfigMap deletion. Their accepted designs
require journal migration and reconciliation, not isolated one-line substitutions. R4-05
requires Lease RBAC and run-contract migration. SSA-02 needs an expected-identity/confirmation
design and immediate embedded RBAC recheck; R3-06 needs coordinated reset/checkpoint semantics.
Those boundaries are not folded into #345.

Sources were current issue bodies/comments, production paths in `lib/kube_client.py`,
`modules/{decommission,finalization,post_activation,preflight_coordinator}.py`, Python
preflight validators, collection `k8s_read.py`/`klusterlet.py`, the preflight/decommission/
finalization/Argo CD roles, existing tests, the tracker, and parity/compatibility authorities.
Four separate read-only agents checked distinct candidate groups. Graphify's retained graph
included stale `mutants/` references and was used only for leads; source tracing prevailed.
All local probes were fake-backed, not live certification. No cluster was mutated.

## Design

Alternatives considered:

1. Guard each destructive consumer. Rejected because every strict LIST consumer shares the
   same completeness contract and duplicated token parsing would leave gaps.
2. Reject malformed tokens in the two existing strict readers. Selected: enforce the
   existing rule at its owner, retain independent implementations and shared tests.
3. Replace pagination or add a new outcome/configuration. Rejected as unnecessary scope.

Read the raw `metadata.continue` before normalizing it. Missing, null and empty string
end the list. A nonempty string is opaque and is passed unchanged to the next request.
Every other type yields the existing `malformed_response` error. Never return accumulated
items, a resource version, positive absence or success after a malformed page. Never echo
the token. Stop at that page without issuing a request using the malformed value.

This agrees with Kubernetes' [ListMeta string field](https://kubernetes.io/docs/reference/kubernetes-api/definitions/list-meta-v1-meta/)
and retains the issue's explicit null compatibility. No new API request or permission is
introduced. Existing page/restart limits, timeouts, hub routing, check mode and checkpoint
behavior remain governed by their current owners.

## Acceptance and verification

- Both real producers reject falsey and truthy non-string tokens, on first and later pages.
- Failures carry no inventory or revision, and diagnostics contain no token/credential data.
- Missing, null and empty-string termination, opaque-string continuation, revision checks,
  expiry restart and bounded operations retain their existing behavior.
- A destructive consumer cannot enter target teardown with malformed Hive inventory.
- Collection module runtime covers malformed tokens in normal and native check mode,
  with read-only requests, correct context and sanitized output.
- Demonstrate RED against unchanged base production code, then GREEN. Deliberately restore
  each old normalization independently, prove the mutation applied, and observe a relevant
  regression fail before restoring the fix.
- Run root tests and quality gates, collection unit/integration/scenario/syntax/build,
  documentation/CI guardrails and shared parity tests. Hosted supported-version CI is
  mandatory. Local fake evidence is not live certification.
- Pre-creation Thermos/CodeRabbit review, then draft PR and exact-head builder handoff.
  Independent validation must use a separate clean checkout in a later stage. This builder
  does not perform validation/resolver stages, mark ready, merge or resolve review threads.

Skill checkpoints: worktrees at clean start; brainstorming at alternatives/scope; planning
at the linked implementation plan; systematic debugging at traced gate admission; TDD at
RED/GREEN; verification-before-completion at builder, draft PR and handoff claims.
