# PR43 Low-Severity Cleanup Design

## Goal

Implement the safe, behavior-preserving subset of Thermos Review #2 low-severity findings for PR43 from the PR #149 merge base. The batch must remain small enough to review, must not include R2-L2, must not change RBAC permissions or live/lab release certification semantics, and must preserve fail-closed behavior, check-mode behavior, idempotence, registered facts, and report schemas.

## Context

- Base: `origin/ansible` at PR #149 merge commit `79b1d92f516bfb45a5c18ff54d554044a6e80f15`.
- Branch/worktree: `chore/thermos-43-low-severity-cleanup` in `.worktrees/thermos-43-low-severity-cleanup`.
- Tracker row: PR43 is `planned` before implementation.
- Protected files remain read-only: `docs/ACM_SWITCHOVER_RUNBOOK.md` and `.claude/skills/**/*.skill.md`.
- Graphify was used as a lead source for broad cross-file review. It surfaced decommission, waiter, klusterlet, and release-orchestrator neighborhoods already found by source inspection; no finding below is based solely on inferred graph edges.

## Approach Options

### Option A: Include all R2-L1 and R2-L3 through R2-L9

This would maximize apparent tracker progress, but it mixes old Python polling logic, CLI parsing, Ansible role restructuring, Argo CD checkpoint resume tasks, Helm/RBAC templates, shell kubeconfig generation, and release-tooling cleanup in one PR. It is too broad for safe review and risks touching RBAC or destructive decommission behavior without enough design focus.

### Option B: Include only tiny non-operational cleanup

This would include only R2-L9 and maybe documentation-only R2-L5. It would be very safe, but it leaves straightforward log and CLI cleanup on the table and does not deliver enough value for the PR43 batch.

### Option C: Include local, behavior-preserving cleanups and split risky structure/RBAC work

This is the recommended design. Include deterministic log-detail truncation (R2-L3), replace the manual CLI `sys.argv` pre-scan with parse-after-parse required-argument validation (R2-L4), document and test the klusterlet probe structured failure contract (R2-L5), partially include only the local repeated-guard cleanup from R2-L7, and remove release orchestrator `_as_dict()` (R2-L9). Defer or split items whose equivalence is not obvious or whose files are safety/RBAC/destructive-operation sensitive.

## Scope Classification

| Finding | Source area | Decision | Reason | Files expected | Tests expected |
| ------- | ----------- | -------- | ------ | -------------- | -------------- |
| R2-L1 | `lib/waiter.py` / `lib/kube_client.py` | defer | `wait_for_pods_ready()` has bespoke wall-clock remaining-budget and transient-error behavior already pinned by tests. Refactoring into `wait_for_condition()` risks subtle timeout/logging changes for low-severity value. | none | Existing `tests/test_kube_client.py` and `tests/test_waiter.py` stay unchanged. |
| R2-L3 | waiter/decommission logging | include | Deterministic truncation of operator log detail is local and behavior-preserving: return values and exceptions stay unchanged, only oversized log detail is shortened. | `lib/waiter.py`, `modules/decommission.py` | `tests/test_waiter.py`, `tests/test_decommission.py` |
| R2-L4 | `acm_switchover.py` CLI pre-scan | include | Remove the manual exact-string `sys.argv` pre-scan. Parse all arguments first with argparse, then enforce the same required switchover/decommission argument contract with `parser.error()`, so argparse abbreviation handling is authoritative. | `acm_switchover.py` | `tests/test_main.py` |
| R2-L5 | `acm_klusterlet_probe.py` structured failure contract | include | Source reconciliation on the PR #149 base shows the helper already returns `failed: true` when `failed_clusters` is non-empty, which makes the Ansible task fail through the normal result contract. Document and test that module-level contract, and rely on the existing post-activation caller test that aborts on `failed_clusters` before remediation. | `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_klusterlet_probe.py`, collection unit tests | `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py`, `ansible_collections/tomazb/acm_switchover/tests/unit/test_klusterlet_remediation.py` |
| R2-L6 | decommission role discover/main convention | split | Moving live discovery from destructive decommission task files into `discover_resources.yml` can affect task order, dry-run live-read skipping, registered facts, and destructive mutation boundaries. That deserves a focused PR. | none | Existing decommission role tests stay unchanged. |
| R2-L7 | small Ansible duplication items | split; include only Argo CD resume repeated guard | The repeated checkpoint-load `when:` block in `argocd_resume.yml` is local and can be factored into one boolean fact. Observability rollout duplication affects health polling, and Helm/RBAC/bootstrap service-account mapping touches RBAC-adjacent surfaces, so those are split/deferred. | `ansible_collections/tomazb/acm_switchover/playbooks/argocd_resume.yml`, existing Argo CD resume contract tests | `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py`, `ansible_collections/tomazb/acm_switchover/tests/unit/test_ansible_resilience_contracts.py` |
| R2-L8 | kubeconfig sed escape fallback | defer | The comparison target is deprecated `setup-rbac.sh`. The active script already has restrictive umask/file-permission hardening. A sed-helper cleanup should be paired with shell/static tests in a smaller script-focused PR if still desired. | none | Existing `tests/test_generate_merged_kubeconfig_script.py` stays unchanged. |
| R2-L9 | release orchestrator `_as_dict()` cleanup | include | The release adapters implement the `StreamAdapter` protocol and return `StreamResult`. Calling `.to_dict()` directly is behavior-preserving for valid adapters and removes dead duck-typing. Release artifact schema remains unchanged. | `tests/release/orchestrator.py` | `tests/release/test_orchestrator.py`, `tests/release/test_release_certification.py` |

## Implementation Notes

- R2-L3 truncation must be deterministic, prefix-preserving, and operator-readable. It is log hygiene, not redaction. The original full values must continue flowing through exceptions and return data where those are already part of behavior.
- R2-L4 must keep `parse_args()` raising `SystemExit` for missing required switchover/decommission arguments. The difference is that required-argument evaluation happens after argparse has parsed abbreviations, not before.
- R2-L5 must not switch `acm_klusterlet_probe.py` to `fail_json()` for per-cluster probe failures in this batch. The module should continue returning the structured result with `failed: true` and `failed_clusters` so Ansible marks the task failed while callers can still inspect the payload.
- R2-L7 must not alter checkpoint identity validation, live UID reads, hub swapping, or Argo CD resume task order. Only repeated checkpoint-lookup guards may be collapsed.
- R2-L9 must not alter `StreamResult.to_dict()` output or release artifact schema.

## Validation Polish Notes

- V1 restores behavior-preserving equivalence for `argocd_resume.yml` by keeping `checkpoint.enabled` on bare Jinja truthiness instead of `| bool` coercion; review follow-up keeps that truthiness while switching the shared predicate to defensive dictionary access.
- V2 is CLI help text only; parser behavior and post-parse `parser.error()` validation remain unchanged.
- V3 strengthens the Argo CD resume guard test so matched checkpoint task names must equal the expected set.
- V4 remains documented as cosmetic and non-actionable in this pass.

## Verification Plan

Always run:

```bash
git diff --check
python -m pytest tests/test_documentation_guardrails.py -q
```

Targeted suites:

```bash
python -m pytest tests/test_waiter.py tests/test_decommission.py tests/test_main.py -q
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py ansible_collections/tomazb/acm_switchover/tests/unit/test_ansible_resilience_contracts.py -q
python -m pytest tests/release/test_orchestrator.py tests/release/test_release_certification.py -q
```

Final gate when feasible:

```bash
./run_tests.sh
```
