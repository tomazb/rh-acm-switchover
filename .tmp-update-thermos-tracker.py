from pathlib import Path

path = Path("thermos-resolution-plan.md")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Expected one tracker anchor, found {count}: {old[:120]!r}")
    text = text.replace(old, new, 1)


replace_once("**Last Updated:** 2026-07-20", "**Last Updated:** 2026-07-21")
replace_once(
    "## Current Completion Summary (2026-07-19)",
    "## Current Completion Summary (2026-07-21)",
)
replace_once(
    "- A 2026-07-20 source revalidation confirmed that all `SSA-01`–`SSA-10`\n"
    "  acceptance criteria remain unmet; slice statuses stay `planned` (no flips).",
    "- A 2026-07-20 source revalidation confirmed that all `SSA-01`–`SSA-10`\n"
    "  acceptance criteria remain unmet; slice statuses stay `planned` (no flips).\n"
    "- The 2026-07-21 post–Review #2 delta revalidation reconfirmed `SSA-01` and\n"
    "  `SSA-09`, added four `TR2D-*` resolution slices for newly validated Argo CD\n"
    "  and Phase 9B follow-ups, and excluded unverified or non-defect claims.",
)
replace_once(
    "- Nine findings are P2 and six are P3 after removing overstated impact and\n"
    "  accounting for existing confirmation, lifecycle, and controller gates.",
    "- Ten findings are P2 and five are P3 after removing overstated impact and\n"
    "  accounting for existing confirmation, lifecycle, and controller gates.\n"
    "  The 2026-07-21 delta revalidation raises `SSA-PY5` to P2 because the reusable\n"
    "  mutation helper directly logs bounded raw API response-body content.",
)
replace_once(
    "| SSA-09 | planned | SSA-PY5 | Remove raw API-body logging and bound or stream full-list aggregation without silently omitting safety-relevant resources. | secret handling, scale, and fail-closed review |",
    "| SSA-09 | planned | SSA-PY5 | First remove raw API/exception logging; then bound or stream full-list aggregation without silently omitting safety-relevant resources. | secret handling, scale, and fail-closed review |",
)
replace_once(
    "#### SSA-09: API Error Redaction And Resource Bounds\n\n**Resolution**\n",
    "#### SSA-09: API Error Redaction And Resource Bounds\n\n**Resolution**\n"
    "- Treat API/exception redaction as the first bounded implementation sub-slice;\n"
    "  resource-bound changes may follow separately if design confirms independent\n"
    "  rollback and verification boundaries.\n",
)
replace_once(
    "| SSA-PY5 | confirmed with workload-dependent impact, corrected P3 | SSA-09 (planned) | Some patch failures log a bounded fragment of raw API response bodies and some callers aggregate every list page without a resource bound. |",
    "| SSA-PY5 | confirmed with direct reusable-helper exposure, corrected P2 | SSA-09 (planned) | `KubeClient.patch_custom_resource()` logs status, reason, up to 500 bytes of raw API response body, and the rendered exception; full-list aggregation remains a lower-urgency resource-bound subproblem within the same design gate. |",
)

delta_section = r'''## Thermos Post–Review #2 Delta Revalidation (2026-07-21)

The operator-supplied post–Review #2 synthesis was revalidated against the exact
`ansible` range `1dbe543d…4fed598c`; current `origin/ansible` was
`4fed598cb1890959d8e8251b7c70e2eb5434b5f5`. The review remains a hypothesis
source rather than tracker authority. Only source-backed findings enter the
resolution queue, and the existing `SSA-*`, `H3`, and deferred-issue taxonomy is
reused instead of duplicated.

### Revalidated existing priorities

- `SSA-01` remains the first product-runtime priority. Python and collection
  identity checks bind each role independently but do not reject equal live
  primary/secondary cluster UIDs.
- `SSA-09` remains open. Direct source inspection raises `SSA-PY5` from P3 to P2:
  the reusable Python patch helper logs bounded raw Kubernetes API response-body
  content. Sequence redaction before the separate full-list resource-bound work.
- `H3` / issue #158 remains unchanged and continues to cover post-activation and
  finalization decomposition only. Phase 9B controller decomposition is a
  separate subsystem and must not be folded into H3.

### Delta disposition

| Finding | Validation | Status | Disposition |
| --- | --- | --- | --- |
| `TR2D-M1` | confirmed with nuance, Medium safety | planned as `TR2D-01` | Scoped collection Argo CD discovery suppresses task failure, infers failure from non-empty `msg`, and aggregates loop `resources` without positively proving every namespace read succeeded. The exact failed-result-without-`msg` runtime shape remains unverified; the missing all-results-success contract is source-confirmed. |
| `TR2D-M2` | confirmed, Medium parity/safety | planned as `TR2D-02` | Collection resume patches from the earlier discovery snapshot and has no explicit controlled refusal for missing `resourceVersion`; Python re-GETs the exact Application, validates the current marker, requires a fresh resource version, and then patches. Collection OCC exists but is farther from the write and behavior is not in parity. |
| `TR2D-Q1` | confirmed maintainability/review risk | planned as `TR2D-03` | Phase 9B `live_discovery.py` and its test module are large, tightly coupled safety-review surfaces. Decompose through characterization-first, design-gated slices before Phase 9C adds mutation authority. This is not current live-mutation severity. |
| `TR2D-Q4` | confirmed maintainability | deferred as `TR2D-04` | `validate_gitops.yml` retains primary/secondary duplication. Any extraction must preserve the primary restore-only skip and other explicit hub asymmetries. |
| `TR2D-Q2` | confirmed inventory signal only | no standalone slice | Large `rbac_validator` / `kube_client` files justify responsibility and coupling analysis, not an automatic refactor based on line count. |
| `TR2D-Q3` | confirmed low-value residual seam | merge with R2-L1/#152 if accepted | Restore-wait class wrappers now delegate to the shared waiter. Remove only if the issue #152 design proves simpler call/test seams without behavior drift. |
| `TR2D-Q6` | not confirmed as a defect | excluded | Strict and public-advisory client methods preserve a deliberate error-surface boundary: advisory retries omit exception logging because server-provided exception text may be sensitive. Consolidation must not erase that policy. |
| `TR2D-L1` | confirmed overlap | merged into `TR2D-M1` | Brittle discovery error gating and fail-closed aggregation are one correction boundary. |
| `TR2D-L2` | unverified | excluded pending reproduction | No exact preflight path, expected report reference, or failing scenario was supplied to prove that sanitized UX incorrectly loses a report path. |

### Planned delta resolution slices

No slice receives a numbered implementation PR, branch, or worktree until its
slice-specific design/spec and implementation plan pass the tracker gate.

| Slice | Status | Findings | Resolution boundary | Required review |
| --- | --- | --- | --- | --- |
| `TR2D-01` | planned | `TR2D-M1`, `TR2D-L1` | Require a positive per-namespace success contract before aggregation; fail closed on missing/malformed/failed/unreachable results; add executable mixed-success negative coverage. Keep the first correctness PR localized and split `discover.yml` only if needed for testable control flow. | Argo CD target completeness, sanitized error handling, Ansible runtime behavior, negative tests |
| `TR2D-02` | planned | `TR2D-M2` | Re-read each exact Application immediately before resume, validate the current same-run marker, require a non-empty current `resourceVersion`, patch conditionally, and classify missing/foreign markers, missing RV, conflicts, and success consistently with Python. | Python/collection parity, OCC/TOCTOU, `changed` reporting, sanitized conflict handling |
| `TR2D-03` | planned/design-gated | `TR2D-Q1` | Characterize and then separate Phase 9B immutable contracts, enrollment/trust validation, typed read/pagination, identity fingerprinting, freshness/provenance, artifact/redaction, and top-level orchestration. Do not broaden live mutation authority in this slice. | release-controller trust boundary, no-certification claims, redaction, deadlines, pagination |
| `TR2D-04` | deferred/design-gated | `TR2D-Q4` | Replace dual-hub GitOps advisory blocks with data-driven iteration only after recording all intentional hub asymmetries and preserving messages/status facts. | restore-only behavior, hub targeting, advisory continuation, operator-visible output |

### Sequence

1. Keep `SSA-01` as the first product-runtime priority.
2. Land the API/exception-redaction subpart of `SSA-09` before unrelated
   maintainability work; retain resource bounds as a separately reviewable
   sub-slice under the same design gate when appropriate.
3. Resolve `TR2D-01` scoped discovery completeness.
4. Resolve `TR2D-02` collection resume parity.
5. Complete `TR2D-03` Phase 9B decomposition before Phase 9C materially expands
   live-controller responsibilities or introduces mutation authority.
6. Keep `TR2D-04` and the remaining structural inventory behind higher-value
   safety work unless their design establishes a stronger dependency.
'''

replace_once(
    "## Security & Stability Audit Follow-Up (2026-07-19)",
    delta_section + "\n## Security & Stability Audit Follow-Up (2026-07-19)",
)

matrix_rows = r'''| TR2D-M1 | confirmed with nuance | TR2D-01 (planned) | Scoped collection discovery lacks a positive all-namespace success contract before flattening loop `resources`; the precise failed-item-without-`msg` manifestation remains unverified and must be covered by an executable mixed-success regression. |
| TR2D-M2 | confirmed | TR2D-02 (planned) | Collection resume uses the discovery-time Application snapshot and injected resource version; Python re-GETs, revalidates marker ownership, explicitly refuses missing `resourceVersion`, and patches from the fresh object. |
| TR2D-Q1 | confirmed maintainability/review risk | TR2D-03 (planned/design-gated) | Phase 9B live discovery and its test surface require characterization-first decomposition before Phase 9C expands live-controller authority; this is not a current live-mutation defect. |
| TR2D-Q4 | confirmed maintainability | TR2D-04 (deferred/design-gated) | `validate_gitops.yml` duplicates hub flows; a loop extraction must preserve restore-only and per-hub asymmetries. |
| TR2D-Q2 | confirmed inventory signal only | none | File size alone does not authorize `rbac_validator` / `kube_client` refactoring; require a responsibility/coupling design and characterization evidence. |
| TR2D-Q3 | confirmed low-value cleanup | R2-L1/#152 if accepted | Thin restore-wait wrappers may be removed only if the existing waiter issue design proves simpler stable seams. |
| TR2D-Q6 | not confirmed as a defect | none | Separate strict/advisory methods encode a deliberate no-exception-logging policy; do not collapse that security boundary merely for deduplication. |
| TR2D-L1 | confirmed overlap | TR2D-01 (planned) | Treat brittle discovery gating and fail-closed aggregation as one correction. |
| TR2D-L2 | unverified | none | Excluded until an exact code path, expected report reference, and reproducible failing scenario are supplied. |'''

replace_once("\n## PR Sequence", "\n" + matrix_rows + "\n\n## PR Sequence")

path.write_text(text, encoding="utf-8")
