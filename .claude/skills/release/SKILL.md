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
