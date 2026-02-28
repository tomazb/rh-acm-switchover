# Design: Claude Code Hooks & Release Skill

**Date**: 2026-02-28
**Status**: Approved

## Overview

Add two Claude Code automations to the project:
1. **Hooks** in `.claude/settings.json` — auto-format Python files and block edits to protected files
2. **Release skill** in `.claude/skills/release/SKILL.md` — automate version bumps across all 6 project locations

## Hooks

### File: `.claude/settings.json`

#### Hook 1: Auto-format Python files (PostToolUse)

- **Trigger**: After `Edit` or `Write` tool calls
- **Condition**: File path ends in `.py`
- **Action**: Run `black --quiet <file>` then `isort --quiet <file>`
- **Behavior**: Silent on success, only surfaces errors

#### Hook 2: Block protected file edits (PreToolUse)

- **Trigger**: Before `Edit` or `Write` tool calls
- **Condition**: File matches `*/completions/*`, `*get-pip.py`, or `*.lock`
- **Action**: Exit code 2 with descriptive block message
- **Rationale**: completions/ are generated, get-pip.py is vendored, lock files are managed by tools

No permissions overrides — those stay user-controlled.

## Release Skill

### File: `.claude/skills/release/SKILL.md`

- **Invocation**: `/release 1.6.0` (user-only via `disable-model-invocation: true`)
- **Argument**: Semver version string (`X.Y.Z`)

### Behavior

1. Validate version matches `X.Y.Z` format
2. Set version date to today (`YYYY-MM-DD`)
3. Update all 6 version locations:
   - `lib/__init__.py` — `__version__` and `__version_date__`
   - `scripts/constants.sh` — `SCRIPT_VERSION` and `SCRIPT_VERSION_DATE`
   - `README.md` — version badge line at top
   - `CHANGELOG.md` — promote `[Unreleased]` content into `[X.Y.Z] - date`, reset unreleased template
   - `container-bootstrap/Containerfile` — `version` label
   - `deploy/helm/acm-switchover-rbac/Chart.yaml` — `version` and `appVersion`
4. Show `git diff` summary for review
5. Do NOT commit or tag — leave that to user

### Design Choices

- Uses Claude's `Edit` tool for each file (benefits from auto-format hook on `.py` files)
- Changelog handling: promotes unreleased content, resets `[Unreleased]` with empty Added/Changed/Fixed subsections
- No helper script — explicit skill instructions are sufficient and avoid maintenance overhead
