# Plan: Create Claude SKILLS from ACM Switchover Runbook

Extract procedural knowledge from the runbook into conversational Claude SKILLS with decision trees, referencing [docs/ACM_SWITCHOVER_RUNBOOK.md](docs/ACM_SWITCHOVER_RUNBOOK.md) for details, and using conditional sections for ACM version variations.

## Steps

1. **Create `.claude/skills/` directory structure** with `operations/` and `troubleshooting/` subdirectories alongside existing [.claude/settings.local.json](.claude/settings.local.json).

2. **Create 8 operational SKILLS** with conversational guidance and decision trees:
   - `preflight-validation.skill.md` — Interactive checklist with go/no-go decision points, links to runbook Step 0
   - `pause-backups.skill.md` — Guides user through ACM version detection, then shows 2.11 vs 2.12+ commands in conditional sections
   - `activate-passive-restore.skill.md` — Decision tree: "Is passive restore already running?" → branch to verify or create, references runbook Steps 2-5
   - `activate-full-restore.skill.md` — Decision tree for Method 2 prerequisites, references runbook Steps F1-F5
   - `verify-switchover.skill.md` — Conversational flow checking clusters, observability, with "what if X fails" branches
   - `enable-backups.skill.md` — Guides backup schedule creation with version-conditional sections
   - `rollback.skill.md` — Decision tree: "At which step did failure occur?" → targeted rollback steps
   - `decommission.skill.md` — Interactive confirmation flow with safety checks

3. **Create 3 troubleshooting SKILLS** with diagnostic decision trees:
   - `pending-import.skill.md` — "Is klusterlet running?" → "Check import secrets" → escalation paths
   - `grafana-no-data.skill.md` — "Are observatorium pods healthy?" → "Check metrics-collector" → resolution branches
   - `restore-stuck.skill.md` — Velero diagnostics flow with common resolution paths

4. **Add SKILLS section to [AGENTS.md](AGENTS.md)** with:
   - Description of `.claude/skills/` directory and purpose
   - List of available SKILLS by category
   - **Maintenance rule**: "When updating [docs/ACM_SWITCHOVER_RUNBOOK.md](docs/ACM_SWITCHOVER_RUNBOOK.md), also update the corresponding SKILLS in `.claude/skills/` to keep procedures synchronized"
   - Add to "Files to Know" table: `.claude/skills/` entry noting runbook dependency
