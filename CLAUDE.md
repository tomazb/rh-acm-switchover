# ACM Switchover - Claude Agent Instructions

All repository-wide process and safety policy lives in [`AGENTS.md`](AGENTS.md). Read it
first: it owns the mandatory start gate, the authority hierarchy, the protected-file
policy, the parity contract, the verification matrix, and the review and release gates.

This file holds only the Claude-specific mechanics that support that policy.

## Claude Code hooks

Hooks are configured in `.claude/settings.json`:

- **Auto-format**: after every `Edit`/`Write` on a `.py` file, `black` and `isort` run
  automatically. This covers only files edited in-session — it is not a substitute for
  running the repository formatting gates before pushing.
- **File protection**: `Edit`/`Write` on `ACM_SWITCHOVER_RUNBOOK.md`, `*.skill.md`,
  `completions/`, `get-pip.py`, and `*.lock` paths is blocked.

The protection hook is **defense-in-depth on the `Edit`/`Write` tool path only**. It does
not intercept shell write paths. The binding rule is the
[Protected Critical Files](AGENTS.md#protected-critical-files) policy in `AGENTS.md`, which
applies regardless of tool.

## Claude SKILLS

`.claude/skills/` contains conversational guides for operator-facing switchover procedures
(preflight validation, pausing backups, passive and full restore activation, verification,
enabling backups, rollback, decommission, restore-only, and troubleshooting), plus a release
automation skill.

SKILLS are derived from [`docs/ACM_SWITCHOVER_RUNBOOK.md`](docs/ACM_SWITCHOVER_RUNBOOK.md)
and are protected files. When the runbook changes, the corresponding SKILLS must change with
it, and vice versa — see the protected-file policy in `AGENTS.md`.

## graphify

This project has a knowledge graph at `graphify-out/` with god nodes, community structure,
and cross-file relationships.

When the user types `/graphify`, invoke the `skill` tool with `skill: "graphify"` before
doing anything else.

- For codebase questions, run `graphify query "<question>"` when `graphify-out/graph.json`
  exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"`
  for focused concepts. These return a scoped subgraph, usually much smaller than
  `GRAPH_REPORT.md` or raw grep output.
- **Worktree fallback**: only the primary worktree keeps `graphify-out/` up to date. In a
  non-primary worktree it is absent or stale — find the primary with
  `git worktree list | head -1 | awk '{print $1}'` and read its `graphify-out/` instead.
- Dirty `graphify-out/` files after hooks or incremental updates are expected and are not a
  reason to skip graphify. Skip it only when the task is about stale or incorrect graph
  output, or the user says not to use it.
- If `graphify-out/wiki/index.md` exists, use it for broad navigation. Read
  `graphify-out/GRAPH_REPORT.md` only when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current.

Graph output is a hypothesis generator, not an authority — see the evidence rules in
`AGENTS.md`.
