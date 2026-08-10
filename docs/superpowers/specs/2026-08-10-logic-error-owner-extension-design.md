# Logic-Error Finding Owner Extension Design

**Date:** 2026-08-10

**Branch:** `docs/thermos-logic-error-owner-extensions`

**Scope:** Thermos tracker ownership and acceptance criteria only

## Objective

Record three independently reproduced logic-error findings in
`thermos-resolution-plan.md` without creating duplicate implementation slices or
changing the historical counts for Thermos Reviews #1-#4.

Each finding receives a stable `LER-*` identifier and is assigned to the existing
resolution boundary that already owns the affected failure semantics:

| Finding | Severity | Existing owner | Ownership rationale |
| --- | --- | --- | --- |
| `LER-01` | High | `R4-05` | `R4-05` owns Python state durability, restoration, and write-failure semantics. |
| `LER-02` | Medium | `R3-10b` | `R3-10b` owns bounded timeout and blocking-operation failure semantics. |
| `LER-03` | Medium | `R3-06` | `R3-06` owns collection `reset_from` safety and checkpoint identity convergence. |

## Validated Findings

### LER-01: failed restore write clears the dirty retry obligation

`StateManager.restore_runtime_checkpoint()` and
`StateManager.restore_state_snapshot()` set `_dirty = False` before calling
`_write_state()`. If the write raises, the exception propagates while `_dirty`
remains false. Later termination flushing therefore has no durable retry
obligation, unlike `_do_flush()`, which restores `_dirty = True` after a failed
write.

The defect requires a state-write failure such as storage exhaustion or an I/O
error, but it affects recovery and simulation-state restoration. It is therefore
High severity and belongs to `R4-05`, not to a new state-management slice.

### LER-02: RBAC setup has no bounded execution deadline

Python setup mode invokes `scripts/setup-rbac.sh` with `subprocess.run()` and no
timeout. The script contains multiple Kubernetes calls without an explicit
request timeout. The collection RBAC bootstrap path similarly invokes its
kubeconfig helper through `ansible.builtin.command`, while the helper's
Kubernetes calls have no explicit request timeout.

This can block an operator or AAP execution indefinitely when a helper or API
request hangs. The defect is Medium severity. `R3-10b` will be broadened from a
single missing `KubeClient` request timeout to bounded blocking-operation timeout
semantics, with explicit Python/collection parity review. This does not broaden
`R2-L7c`/issue #156, whose approved scope is service-account mapping and whose
non-goals exclude behavior changes.

### LER-03: explicit reset does not make every unsafe legacy transition safe

The collection checkpoint action permits an unsafe schema-1.0 checkpoint with
completed phases when `reset` or `reset_from` is truthy. It rebuilds that state as
schema 2.0 only for `status: enter`. A direct `pass` or `fail` transition can
therefore accept and retain unsafe legacy state instead of rebuilding or failing
closed.

Bundled roles currently enter a phase before finishing it, which mitigates the
normal playbook path, but the reusable action-plugin boundary does not enforce
that sequencing. The defect is Medium severity and is folded into `R3-06`, which
already owns the overly broad `reset_from` bypass in the same checkpoint-policy
path.

### Auditable source and red reproductions

The following read-only commands are the executable evidence recorded for
`ansible` revision `9906101f4a6f6652c31d03fc4cb4cde7a04159da`. Each command
intentionally exits nonzero on that revision with the named assertion; a future
owner implementation must replace these ad hoc reproductions with focused tests.

`LER-01` targets `StateManager.restore_runtime_checkpoint()` and
`StateManager.restore_state_snapshot()` at `lib/utils.py:490-514`. This fault
injection fails with both restore symbols in `violations`:

```bash
python - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory

from lib.utils import StateManager


def fail(_state):
    raise OSError("injected write failure")


violations = []
with TemporaryDirectory() as root:
    for capture, restore in (
        ("capture_runtime_checkpoint", "restore_runtime_checkpoint"),
        ("capture_state_snapshot", "restore_state_snapshot"),
    ):
        manager = StateManager(str(Path(root) / f"{restore}.json"))
        snapshot = getattr(manager, capture)()
        manager._dirty = True
        manager._write_state = fail
        try:
            getattr(manager, restore)(snapshot)
        except OSError:
            pass
        if manager._dirty is not True:
            violations.append(restore)

assert not violations, f"failed writes cleared dirty retry obligation: {violations}"
PY
```

`LER-02` targets `run_setup()` at `acm_switchover.py:1002-1072`, its invoked
`scripts/setup-rbac.sh:247-373` Kubernetes calls, the collection invocation at
`ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/tasks/generate_kubeconfigs.yml:55-76`,
and the complete helper path
`ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/files/scripts/generate-sa-kubeconfig.sh:98-132`.
This snapshot probe fails with all four unbounded surfaces in `failures`:

```bash
python - <<'PY'
import ast
from pathlib import Path

failures = []
tree = ast.parse(Path("acm_switchover.py").read_text(encoding="utf-8"))
run_setup = next(
    node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run_setup"
)
run_calls = [
    node
    for node in ast.walk(run_setup)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and isinstance(node.func.value, ast.Name)
    and node.func.value.id == "subprocess"
    and node.func.attr == "run"
]
if not run_calls or any(
    not any(keyword.arg == "timeout" for keyword in call.keywords) for call in run_calls
):
    failures.append("acm_switchover.py:run_setup subprocess deadline")

for path, markers in {
    "scripts/setup-rbac.sh": ("--request-timeout",),
    (
        "ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/"
        "tasks/generate_kubeconfigs.yml"
    ): ("async:", "timeout:"),
    (
        "ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/"
        "files/scripts/generate-sa-kubeconfig.sh"
    ): ("--request-timeout",),
}.items():
    source = Path(path).read_text(encoding="utf-8")
    if not any(marker in source for marker in markers):
        failures.append(path)

assert not failures, f"unbounded RBAC setup surfaces: {failures}"
PY
```

`LER-03` targets `ActionModule._normalize_checkpoint_data()` at
`ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py:265-325`.
This direct action-boundary probe fails with all four `pass`/`fail` and
`reset`/`reset_from` combinations in `violations`:

```bash
ANSIBLE_LOCAL_TEMP=/tmp python - <<'PY'
from tests.properties.test_checkpoint_properties import ActionModule

violations = []
action = ActionModule.__new__(ActionModule)
for status in ("pass", "fail"):
    for reset_from in (None, "preflight"):
        checkpoint = {
            "schema_version": "1.0",
            "phase": "finalization",
            "completed_phases": ["preflight"],
            "operational_data": {"legacy": True},
        }
        result, _changed = action._normalize_checkpoint_data(
            checkpoint_data=checkpoint,
            phase="preflight",
            status=status,
            reset_from=reset_from,
            has_explicit_reset=True,
            expected_operation_identity={},
        )
        if result.get("failed") is not True and result.get("schema_version") == "1.0":
            violations.append((status, "reset_from" if reset_from else "reset"))

assert not violations, f"unsafe legacy transitions accepted: {violations}"
PY
```

## Tracker Changes

Add a dated **Logic Error Analysis Revalidation** section containing the three
`LER-*` rows, their evidence, severity, and existing owner. This section is a new
hypothesis-source record and does not alter the historical finding counts of
earlier Thermos reviews.

Add one corresponding row per `LER-*` identifier to the Finding Validation
Matrix so status, severity, owner, and failure behavior remain independently
traceable from the source record.

Extend the existing owner rows and detailed acceptance criteria as follows.

### R4-05 extension

- Add `LER-01` to the owned-finding list.
- State that a failed checkpoint/snapshot restoration write must leave a durable
  retry obligation in memory.
- Require fault-injection coverage for both restore methods.
- Require the original write exception to propagate without reporting a
  successful restoration.
- Require a later flush to retry the restored state rather than silently treating
  it as clean.

### R3-10b extension

- Add `LER-02` to the owned-finding list.
- Rename the boundary description from request-timeout-only language to bounded
  blocking-operation timeout semantics.
- Require an explicit whole-operation deadline for Python setup mode.
- Require bounded Kubernetes calls in the invoked helper path, so an outer
  timeout is not the only protection.
- Review and preserve parity for the collection RBAC bootstrap command/helper
  path, including sanitized timeout reporting and child-process cleanup.
- Keep timeout values centralized within each independent form factor; do not
  introduce cross-imports.

### R3-06 extension

- Add `LER-03` to the owned-finding list.
- Require unsafe legacy state with explicit reset to be rebuilt before any
  accepted transition, or rejected before persistence.
- Cover direct `enter`, `pass`, and `fail` action-plugin calls.
- Preserve legitimate role-driven reset workflows.
- Keep the existing requirement to scope `reset_from` identity bypass to the
  pruned phase and revalidate identity after pruning.

## Prioritization

- `LER-01` enters the tracker's P2 operational-safety group: the consequence is
  severe, but it requires a storage/write failure.
- `LER-02` enters P2: indefinite blocking requires a hung helper or Kubernetes
  request, and affects a supported dual-form-factor capability.
- `LER-03` enters P2 with a sequencing mitigation: bundled roles call `enter`
  first, while direct action-plugin use remains fail-open.

These priority entries describe sequencing only. They do not create new slices
or change the status of `R4-05`, `R3-10b`, or `R3-06`, which remain planned until
their existing design and implementation gates are satisfied.

## Non-Goals

- No production code, tests, role behavior, timeout values, or checkpoint schema
  changes in this tracker-only update.
- No claim that adjacent existing findings already resolve the new defects.
- No reopening or renumbering of historical Thermos review counts.
- No expansion of `R2-L7c`/issue #156.
- No broad cleanup of `except Exception` handlers.
- No protected runbook or `.claude/skills/**/*.skill.md` changes.

## Verification

The tracker update must pass:

1. A focused text audit proving every `LER-*` identifier appears in its source
   row, owner row, detailed acceptance criteria, priority record, and validation
   matrix row.
2. A text audit proving historical Thermos review count statements are unchanged.
3. `python -m pytest tests/test_documentation_guardrails.py -q`.
4. `git diff --check`.

The implementation plans for the three existing owners must later define their
own red/green runtime tests and full parity verification. This documentation-only
change does not claim those runtime defects are fixed.

## Rollback

The change is documentation-only and must be reverted by reverting the complete
branch commit range atomically, including any final-review fix commit, unless
that range is intentionally squashed before integration. Reversion must remove
the spec, plan, tracker, and every `LER-*` cross-reference from the source
section, owner rows, acceptance criteria, priority table, and validation matrix
together so no dangling ownership claims remain.
