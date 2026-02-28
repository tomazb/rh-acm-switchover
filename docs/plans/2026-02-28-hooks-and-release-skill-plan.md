# Hooks & Release Skill Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Claude Code hooks for auto-formatting and file protection, plus a `/release` skill for automated version bumps.

**Architecture:** Two independent deliverables — a `.claude/settings.json` with PostToolUse/PreToolUse hooks, and a `.claude/skills/release/SKILL.md` with version bump instructions. No shared state between them.

**Tech Stack:** Claude Code hooks (JSON config), Claude skills (Markdown with YAML frontmatter), black, isort

---

### Task 1: Create `.claude/settings.json` with hooks

**Files:**
- Create: `.claude/settings.json`

**Step 1: Create the settings file with both hooks**

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "file=\"$CLAUDE_FILE_PATH\"; if [[ \"$file\" == *.py ]]; then black --quiet \"$file\" 2>/dev/null && isort --quiet \"$file\" 2>/dev/null; fi"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "file=\"$CLAUDE_FILE_PATH\"; case \"$file\" in */completions/*|*get-pip.py|*.lock) echo 'BLOCK: This is a protected file (generated/vendored/lock). Do not edit.' >&2; exit 2;; esac"
          }
        ]
      }
    ]
  }
}
```

**Step 2: Verify the JSON is valid**

Run: `python3 -c "import json; json.load(open('.claude/settings.json')); print('Valid JSON')"`
Expected: `Valid JSON`

**Step 3: Commit**

```bash
git add .claude/settings.json
git commit -m "chore: add Claude Code hooks for auto-format and file protection"
```

---

### Task 2: Create release skill

**Files:**
- Create: `.claude/skills/release/SKILL.md`

**Step 1: Create the skill directory and file**

Write `.claude/skills/release/SKILL.md` with:
- YAML frontmatter: name `release`, description, `disable-model-invocation: true`
- Instructions for Claude to:
  1. Parse the version argument and validate it matches `X.Y.Z`
  2. Compute today's date as `YYYY-MM-DD`
  3. Edit each of the 6 files with exact patterns to match and replace
  4. Show `git diff` at the end

The skill content:

````markdown
---
name: release
description: Bump version across all project files (Python, Bash, Container, Helm, README, CHANGELOG). Usage - /release 1.6.0
disable-model-invocation: true
---

# Release Version Bump

Automate version bumps across all 6 project version locations.

## Arguments

The user provides a semver version string as the argument: `X.Y.Z` (e.g., `1.6.0`).

If no argument is provided or it doesn't match the `X.Y.Z` pattern, ask the user for the version.

## Procedure

Set `NEW_VERSION` to the argument and `NEW_DATE` to today's date (`YYYY-MM-DD`).

Read each file first, then use the Edit tool to make targeted replacements. Do NOT use sed or other Bash tools — use the Edit tool for all changes.

### 1. `lib/__init__.py`

Replace the `__version__` and `__version_date__` values:

```
__version__ = "<OLD>" → __version__ = "<NEW_VERSION>"
__version_date__ = "<OLD>" → __version_date__ = "<NEW_DATE>"
```

### 2. `scripts/constants.sh`

Replace the exported version variables:

```
export SCRIPT_VERSION="<OLD>" → export SCRIPT_VERSION="<NEW_VERSION>"
export SCRIPT_VERSION_DATE="<OLD>" → export SCRIPT_VERSION_DATE="<NEW_DATE>"
```

### 3. `README.md`

Replace the version line at the top (line 3):

```
**Version X.Y.Z** (YYYY-MM-DD) → **Version <NEW_VERSION>** (<NEW_DATE>)
```

### 4. `CHANGELOG.md`

Promote unreleased content into a new version section. The current structure is:

```markdown
## [Unreleased]

### Added
<content>
### Changed
<content>
### Fixed
<content>
```

Replace with:

```markdown
## [Unreleased]

### Added

### Changed

### Fixed

## [<NEW_VERSION>] - <NEW_DATE>

### Added
<content>
### Changed
<content>
### Fixed
<content>
```

Keep any content that was under the old `[Unreleased]` subsections — move it to the new version section. If the unreleased subsections are empty, still create the new version section with those empty subsections.

### 5. `container-bootstrap/Containerfile`

Replace the version label:

```
version="<OLD>" → version="<NEW_VERSION>"
```

### 6. `deploy/helm/acm-switchover-rbac/Chart.yaml`

Replace both version fields:

```
version: <OLD> → version: <NEW_VERSION>
appVersion: "<OLD>" → appVersion: "<NEW_VERSION>"
```

## After All Edits

Run `git diff` to show all changes as a summary for the user to review.

Do NOT commit, tag, or push. Tell the user they can use `/commit` when ready.
````

**Step 2: Verify the skill file exists and has valid frontmatter**

Run: `head -5 .claude/skills/release/SKILL.md`
Expected: YAML frontmatter with `name: release`

**Step 3: Commit**

```bash
git add .claude/skills/release/SKILL.md
git commit -m "feat: add /release skill for automated version bumps"
```

---

### Task 3: Update CLAUDE.md with skill reference

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Add the release skill to the skills documentation**

In the CLAUDE.md section that lists skills (under "## Claude SKILLS"), add a reference to the new release skill. Find the appropriate table and add a row.

Also mention the hooks in a new subsection or note so future contributors know about auto-formatting.

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document release skill and hooks in CLAUDE.md"
```
