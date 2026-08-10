# Logic-Error Finding Owner Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three validated `LER-*` findings to the Thermos tracker and assign them to `R4-05`, `R3-10b`, and `R3-06` without creating new implementation slices or changing historical Thermos review counts.

**Architecture:** `thermos-resolution-plan.md` remains the single self-contained resolution source. A new dated revalidation section records provenance and stable finding IDs, while the existing slice table, detailed requirements, priority table, and validation matrix carry the ownership and delivery obligations.

**Tech Stack:** Markdown, `rg`, Git, pytest documentation guardrails.

## Global Constraints

- Implement the approved design in `docs/superpowers/specs/2026-08-10-logic-error-owner-extension-design.md` exactly.
- Keep the change documentation-only: no production code, tests, role behavior, timeout values, or checkpoint schemas change.
- Do not create new implementation slices; assign `LER-01` to `R4-05`, `LER-02` to `R3-10b`, and `LER-03` to `R3-06`.
- Do not change historical Thermos Reviews #1-#4 finding counts or dispositions.
- Do not broaden `R2-L7c`/issue #156.
- Preserve the dual-supported parity contract for the future `LER-02` implementation.
- Do not modify `docs/ACM_SWITCHOVER_RUNBOOK.md` or `.claude/skills/**/*.skill.md`.
- Use `apply_patch` for the tracker edit.

---

### Task 1: Record the findings and extend their existing owners

**Files:**
- Modify: `thermos-resolution-plan.md:47`
- Modify: `thermos-resolution-plan.md:932-945`
- Modify: `thermos-resolution-plan.md:1155-1168`
- Modify: `thermos-resolution-plan.md:1237-1238`
- Modify: `thermos-resolution-plan.md:1611-1627`
- Modify: `thermos-resolution-plan.md:1760-1784`
- Modify: `thermos-resolution-plan.md:1935`
- Modify: `thermos-resolution-plan.md:2057-2068`

**Interfaces:**
- Consumes: the approved `LER-01`, `LER-02`, and `LER-03` definitions from `docs/superpowers/specs/2026-08-10-logic-error-owner-extension-design.md`.
- Produces: stable tracker IDs mapped to existing slices, with source evidence, detailed acceptance criteria, priority, and validation-matrix records.

- [ ] **Step 1: Confirm the pre-edit tracker state**

Run:

```bash
rg -n "LER-01|LER-02|LER-03" thermos-resolution-plan.md
rg -n '^\| (R3-06|R3-10b|R4-05) \|' thermos-resolution-plan.md
```

Expected:

- The first command exits with status 1 and prints no matches.
- The second command prints the three existing planned owner rows, each without an `LER-*` identifier.

- [ ] **Step 2: Update tracker metadata and add the revalidation source section**

Use `apply_patch` to change:

```markdown
**Last Updated:** 2026-08-10
```

Immediately after the existing R4 count-reconciliation paragraph and before the
`Design-hardening ledger` heading, add:

```markdown
## Logic Error Analysis Revalidation (2026-08-10)

An operator-supplied logic-error report was treated as a hypothesis source and
revalidated against the current `ansible` source, focused tests, and direct
fault injection. Three actionable findings remain. They receive independent
`LER-*` identifiers so the historical Thermos Review #1-#4 counts remain
unchanged, and each is assigned to an existing resolution slice rather than
creating duplicate implementation work.

| Finding | Severity | Surface | Existing owner | Validated evidence |
| --- | --- | --- | --- | --- |
| LER-01 | High | Python | R4-05 | `lib/utils.py:490-514`: `StateManager.restore_runtime_checkpoint()` and `restore_state_snapshot()` clear `_dirty` before `_write_state`; an injected write failure propagates while leaving `_dirty == False`, so later flushing has no retry obligation. |
| LER-02 | Medium | Python + collection | R3-10b | `acm_switchover.py:1060-1066`, `scripts/setup-rbac.sh:247-373`, `ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/tasks/generate_kubeconfigs.yml:55-76`, and its `files/scripts/generate-sa-kubeconfig.sh:98-132`: Python setup mode invokes the helper through an unbounded `subprocess.run`, and the Python and collection helper Kubernetes calls likewise lack a complete timeout contract, permitting an indefinitely hung RBAC bootstrap. |
| LER-03 | Medium | Collection | R3-06 | `ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py:275-306`: unsafe schema-1.0 state with completed phases is rebuilt only for `status: enter`; a direct `pass` or `fail` with explicit `reset`/`reset_from` can retain unsafe legacy state instead of rebuilding or failing closed. Bundled roles enter first, but the reusable action boundary does not enforce that sequence. |
```

Keep the existing R4 count paragraph byte-for-byte unchanged. The new paragraph
must say explicitly that `LER-*` identifiers are outside historical Review #1-#4
counts.

- [ ] **Step 3: Extend the planned-slice owner rows**

Use `apply_patch` to make the owner rows express these exact boundaries:

```markdown
| R3-06 | planned | R3-A6, LER-03 | Scope the `reset_from` identity bypass to the pruned phase, revalidate identity after pruning instead of overwriting it, and require every accepted unsafe-legacy transition to rebuild safely or fail closed. | checkpoint identity binding; legacy transition convergence; interaction with `SSA-01` |
| R3-10b | planned | R3-P6, LER-02 | Bound owner-scoped blocking operations: correct the missing `KubeClient` request timeout and bound Python/collection RBAC setup subprocess and Kubernetes-helper execution, including the folded misleading-doc evidence formerly labelled `R3-P6b`. | API/subprocess timeout and failure semantics; RBAC bootstrap parity |
```

Update the existing R4 owner row without otherwise changing its long proposed
resolution boundary:

```markdown
| R4-05 | planned | R4-E1, R4-E2, R4-E3, R4-E4, R4-E5, R4-E6, LER-01 | `docs/plans/2026-07-29-state-integrity-residuals-design.md` | Full-fidelity simulation snapshot with crash marker, parent-dir fsync on both the rename and the absent-file unlink path, per-hub `coordination.k8s.io/v1` Lease locks with a post-acquisition UID revalidation barrier (requires a coordinated RBAC update — see `R4-E4`), reset-under-lock with narrowed `--force`, run contract with atomic committed contract transitions, and restore-write failure semantics that retain an in-memory retry obligation and never report successful restoration. |
```

- [ ] **Step 4: Extend the detailed acceptance criteria**

Under `R3-06: Scoped reset_from Identity Validation`, add these acceptance
criteria:

```markdown
- `LER-03`: An unsafe schema-1.0 checkpoint with completed phases and explicit `reset` or
  `reset_from` is rebuilt as current-schema state before any accepted `enter`,
  `pass`, or `fail` transition, or the action fails before persistence.
- Direct action-plugin `pass` and `fail` calls cannot preserve unsafe schema-1.0
  state; tests cover both statuses as well as the normal role-driven `enter`
  sequence.
```

Replace the `R3-10b` residual-inventory bullet with:

```markdown
- `R3-10b` / `R3-P6` + `LER-02`: bound owner-scoped blocking operations and
  correct timeout documentation. `delete_custom_resource` uses the client
  request-timeout contract; Python setup mode has an explicit whole-operation
  deadline and terminates its process tree on expiry; every invoked Kubernetes
  helper call is independently bounded. The collection RBAC bootstrap command
  and helper path receives parity-equivalent bounds and sanitized timeout
  reporting, with constants kept local to each form factor.
```

Under `Area E — state integrity (R4-05)`, add:

```markdown
- `LER-01`: Checkpoint and snapshot restoration preserve `_dirty == True` whenever the
  durable write raises, propagate the original write exception, and allow the
  next flush to retry the restored state. Fault injection covers both
  `restore_runtime_checkpoint()` and `restore_state_snapshot()` and proves no
  failed write is reported as a successful restoration.
```

- [ ] **Step 5: Extend prioritization and the validation matrix**

Add `LER-01`, `LER-02`, and `LER-03` to the P2 row. Extend its rationale with
the three mitigating preconditions: storage-write failure, hung RBAC helper/API
call, and direct action-plugin sequencing outside the bundled enter-first role
flow.

Add these rows to `Finding Validation Matrix` without renumbering any R3/R4
finding:

```markdown
| LER-01 | confirmed, High | R4-05 (planned) | A restore write failure propagates after `_dirty` was cleared, suppressing the later flush retry obligation. |
| LER-02 | confirmed, Medium | R3-10b (planned) | Python and collection RBAC setup/helper paths have no complete bounded-execution contract; a hung subprocess or Kubernetes request can block indefinitely. |
| LER-03 | confirmed, Medium | R3-06 (planned) | Explicit reset permits unsafe legacy state, but only `enter` rebuilds it; direct `pass`/`fail` can retain schema 1.0. |
```

- [ ] **Step 6: Run the section-aware placement and historical-count audits**

Run:

```bash
python - <<'PY'
from pathlib import Path
import re

text = Path("thermos-resolution-plan.md").read_text(encoding="utf-8")


def between(start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def finding_count(value: str, finding: str) -> int:
    pattern = rf"(?<![A-Za-z0-9_-]){re.escape(finding)}(?![A-Za-z0-9_-])"
    return len(re.findall(pattern, value))


r3_10b = "- `R3-10b`" + text.split("- `R3-10b`", 1)[1].split("- `R3-10c`", 1)[0]
sections = {
    "source": between("## Logic Error Analysis Revalidation (2026-08-10)", "### Design-hardening ledger"),
    "R3-06 owner": "\n".join(line for line in text.splitlines() if line.startswith("| R3-06 |")),
    "R3-10b owner": "\n".join(line for line in text.splitlines() if line.startswith("| R3-10b |")),
    "R4-05 owner": "\n".join(line for line in text.splitlines() if line.startswith("| R4-05 |")),
    "R3-06 detail": between("#### R3-06:", "#### R3-07:"),
    "R3-10b detail": r3_10b,
    "R4-05 detail": between("*Area E — state integrity (`R4-05`):*", "*Area F —"),
    "priority": next(line for line in text.splitlines() if line.startswith("| P2 |")),
    "matrix": between("\n## Finding Validation Matrix\n", "\n## PR Sequence"),
}
expected = {
    "LER-01": ("source", "R4-05 owner", "R4-05 detail", "priority", "matrix"),
    "LER-02": ("source", "R3-10b owner", "R3-10b detail", "priority", "matrix"),
    "LER-03": ("source", "R3-06 owner", "R3-06 detail", "priority", "matrix"),
}
for finding, placements in expected.items():
    for placement in placements:
        count = finding_count(sections[placement], finding)
        assert count == 1, f"{finding}: expected once in {placement}, found {count}"
    assert finding_count(text, finding) == 5, f"{finding}: unexpected occurrence outside required placements"
PY
```

Expected: exit status 0. Each identifier occurs exactly once in the dated source
table, its existing owner row, its detailed requirements, the P2 priority row,
and the validation matrix, with no extra tracker occurrences.

Run the historical-line preservation audit against the branch's `ansible` merge
base:

```bash
python - <<'PY'
from collections import Counter
from pathlib import Path
import re
import subprocess

merge_base = subprocess.run(
    ["git", "merge-base", "HEAD", "ansible"], check=True, capture_output=True, text=True
).stdout.strip()
baseline = subprocess.run(
    ["git", "show", f"{merge_base}:thermos-resolution-plan.md"],
    check=True,
    capture_output=True,
    text=True,
).stdout.splitlines()
current = Path("thermos-resolution-plan.md").read_text(encoding="utf-8").splitlines()
protected = re.compile(
    r"Review #[1-4]|count reconciliation|unique IDs|raw claims?|original claims?|"
    r"validated hypotheses|R4 rows|external-hypothesis accounting|exclusive dispositions?|"
    r"confirmed|partially[- ]amended|rejected/non-actionable|optional hardening|routed"
)
before = Counter(line for line in baseline if protected.search(line))
after = Counter(line for line in current if protected.search(line))
allowed_additions = Counter(
    {
        "`LER-*` identifiers so the historical Thermos Review #1-#4 counts remain": 1,
        (
            "| LER-01 | confirmed, High | R4-05 (planned) | A restore write failure propagates after "
            "`_dirty` was cleared, suppressing the later flush retry obligation. |"
        ): 1,
        (
            "| LER-02 | confirmed, Medium | R3-10b (planned) | Python and collection RBAC setup/helper "
            "paths have no complete bounded-execution contract; a hung subprocess or Kubernetes request "
            "can block indefinitely. |"
        ): 1,
        (
            "| LER-03 | confirmed, Medium | R3-06 (planned) | Explicit reset permits unsafe legacy state, "
            "but only `enter` rebuilds it; direct `pass`/`fail` can retain schema 1.0. |"
        ): 1,
    }
)
assert after == before + allowed_additions, "historical count/disposition lines differ from the approved delta"

diff = subprocess.run(
    ["git", "diff", "--unified=0", merge_base, "--", "thermos-resolution-plan.md"],
    check=True,
    capture_output=True,
    text=True,
).stdout
hunk_pattern = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
hunks = [
    (int(old), int(old_count or 1), int(new), int(new_count or 1))
    for old, old_count, new, new_count in hunk_pattern.findall(diff)
]
expected_hunks = [
    (47, 1, 47, 1),
    (932, 1, 932, 1),
    (945, 1, 945, 1),
    (1171, 0, 1172, 6),
    (1237, 2, 1243, 7),
    (1611, 1, 1622, 1),
    (1627, 0, 1639, 15),
    (1779, 0, 1806, 5),
    (1935, 1, 1966, 1),
    (2068, 0, 2100, 3),
]
assert hunks == expected_hunks, f"tracker changes escaped approved ranges: {hunks!r}"
PY
```

Expected: exit status 0. Every pre-edit line carrying a Review #1-#4 or R4
count/disposition statement remains present byte-for-byte, and the only new
matching lines are the four explicitly approved independent `LER-*` records.
The exact-hunk allowlist also fails if any change escapes the ten approved
tracker edit ranges, including multiline historical claims not matched by the
line filter.

- [ ] **Step 7: Run documentation verification**

Run:

```bash
python -m pytest tests/test_documentation_guardrails.py -q
git diff --check
```

Expected:

- `tests/test_documentation_guardrails.py`: 61 passed, 0 failed.
- `git diff --check`: exit status 0 with no output.

- [ ] **Step 8: Review the final tracker diff**

Run:

```bash
git diff -- thermos-resolution-plan.md
git status --short
```

Expected: only `thermos-resolution-plan.md` is modified relative to the committed
spec/plan baseline; no production, protected, or unrelated file is changed.

- [ ] **Step 9: Commit the tracker extension**

```bash
git add thermos-resolution-plan.md
git commit -m "docs: extend Thermos owners for validated logic errors"
```

Expected: one documentation-only commit containing the tracker update.
