---
name: ArgoCD detection & management
overview: Implement robust ArgoCD (operator + vanilla) detection in both Bash and Python, and add an opt-in ArgoCD “pause autosync for ACM-touching apps” workflow to prevent GitOps drift during ACM switchover. Auto-resume is explicit/opt-in to avoid reverting switchover changes.
todos:
  - id: bash-argocd-detect-vanilla
    content: Update `scripts/lib-common.sh` ArgoCD detection to support operator + vanilla installs while keeping existing ACM-impact scan.
    status: pending
  - id: bash-argocd-manage-script
    content: Add `scripts/argocd-manage.sh` to pause/resume autosync for ACM-touching Applications with a reversible state file.
    status: pending
  - id: py-argocd-module
    content: Add a new Python module (e.g. `lib/argocd.py`) implementing ArgoCD discovery, Application listing, ACM-impact detection, and pause/resume patching.
    status: pending
  - id: py-cli-and-workflow-hooks
    content: Add `--argocd-check`, `--argocd-manage` (pause-only by default), `--argocd-resume-only`, and `--argocd-resume-after-switchover`; wire detection into preflight and pause into primary_prep with StateManager tracking. Resume is explicit/opt-in.
    status: pending
  - id: rbac-updates
    content: Update RBAC manifests and RBAC validation to cover ArgoCD Application read/patch permissions when management is enabled.
    status: pending
  - id: tests-and-docs
    content: Add unit/CLI tests for ArgoCD logic; update usage/runbook/reference docs (and `.claude/skills/` if runbook changes).
    status: pending
isProject: false
---

## Goals

- Provide **best-effort ArgoCD discovery** on both hubs (and new/old hubs) across common install types.
- Provide **actionable reporting**: which ArgoCD instances exist, and which ArgoCD Applications touch ACM resources relevant to switchover.
- Provide **safe ArgoCD management** to prevent GitOps drift during switchover: **pause auto-sync** only for ACM-touching Applications (default), with **explicit opt-in** restore/resume to avoid reverting switchover changes.
- Keep **Bash and Python behavior aligned** (same ACM namespace/kind matching rules, same outputs where practical).

## Current state (what we’ll build on)

- **Bash** already has:
  - `detect_gitops_markers()` + consolidated reporting in `[scripts/lib-common.sh](scripts/lib-common.sh)`.
  - `check_argocd_acm_resources()` in `[scripts/lib-common.sh](scripts/lib-common.sh)` (scans ArgoCD Applications’ `.status.resources[]` using `ns_regex` + a fixed `kinds_json`).
  - `--argocd-check` flags in `[scripts/preflight-check.sh](scripts/preflight-check.sh)` and `[scripts/postflight-check.sh](scripts/postflight-check.sh)`.
- **Python** already has:
  - marker-based GitOps detection: `[lib/gitops_detector.py](lib/gitops_detector.py)` and `--skip-gitops-check` in `[acm_switchover.py](acm_switchover.py)`.
  - no ArgoCD-aware discovery and no management.

## Design decisions

- **Detection must support “operator + vanilla”** ArgoCD installs:
  - Operator/OpenShift GitOps: `argocds.argoproj.io` exists (Bash currently checks this).
  - Vanilla Argo CD: may not have `argocds.argoproj.io`, but typically has `applications.argoproj.io` and controller Deployments.
- **Management default scope (per your answer)**: **ACM-only** (pause only Applications that touch ACM namespaces/kinds).
- **Control mechanism**: **disable auto-sync** by removing `spec.syncPolicy.automated`. This is deterministic and minimally invasive.\n+  - **Important**: restoring auto-sync can re-apply Git’s desired state and **undo switchover** unless Git has been updated to the post-switchover desired state. Therefore, **auto-resume must be explicit/opt-in**.

## Matching rules (shared across Bash + Python)

- Reuse the Bash rule set from `check_argocd_acm_resources()`:
  - **Namespace match**: regex
    - `^(open-cluster-management($|-)|open-cluster-management-backup$|open-cluster-management-observability$|open-cluster-management-global-set$|multicluster-engine$|local-cluster$)`
  - **Kind match**: array
    - `MultiClusterHub`, `MultiClusterEngine`, `MultiClusterObservability`, `ManagedCluster`, `ManagedClusterSet`, `ManagedClusterSetBinding`, `Placement`, `PlacementBinding`, `Policy`, `PolicySet`, `BackupSchedule`, `Restore`, `DataProtectionApplication`, `ClusterDeployment`
- Treat cluster-scoped resources (`namespace` empty) as match-by-kind.

## Python implementation plan

### 1) Add an ArgoCD module (detection + targeting + patching)

- Create a new module (name TBD, likely): `[lib/argocd.py](lib/argocd.py)`.
- Core functions/classes:
  - **Discovery**
    - `detect_argocd_installation(client) -> ArgocdDiscoveryResult`
      - checks presence of `applications.argoproj.io` CRD and/or Argo CD controller deployments.
      - if `argocds.argoproj.io` exists, list ArgoCD instances for nicer reporting.
  - **Application enumeration**
    - `list_argocd_applications(client, namespaces=None) -> list[dict]`
      - list `applications.argoproj.io` across namespaces (or known namespaces if cluster-wide list is forbidden).
  - **ACM impact analysis**
    - `find_acm_touching_apps(apps) -> list[AppImpact]`
      - parses `.status.resources[]` like Bash does; fall back gracefully if `status.resources` missing.
      - returns summary suitable for both logs and state tracking.
  - **Management**
    - `pause_autosync(client, app, run_id) -> PauseResult`
      - patch `spec.syncPolicy.automated` to `null` (remove) only if present.
      - add a marker annotation like `acm-switchover.argoproj.io/paused-by=<run_id>` for safe restore.
      - returns the original `spec.syncPolicy` (or enough to restore).
    - `resume_autosync(client, app, original_sync_policy, run_id) -> ResumeResult`
      - restores original `spec.syncPolicy` only if our marker annotation is present and matches run id.

### 2) StateManager integration (idempotent + resumable)

- Store pause bookkeeping under state config keys:
  - `argocd_run_id`
  - `argocd_paused_apps` = list of `{namespace,name,original_sync_policy,paused_at}`
- Ensure idempotence:
  - “pause” step skips apps already recorded in state.
  - “resume” step only touches apps recorded in state.

### 3) CLI surface in `acm_switchover.py`

- Add flags:
  - `--argocd-check`: detect ArgoCD and print ACM-impact summary (no changes).
  - `--argocd-manage`: enable **pause automation** during switchover (pause-only by default; does not restore auto-sync automatically).
  - `--argocd-resume-after-switchover`: **explicitly opt in** to restoring auto-sync during `FINALIZATION`.\n+    - Only use after Git/desired state has been updated for the **new** hub, otherwise it may revert changes.
  - `--argocd-target=acm` (optional now; future-proof) default `acm`.
  - `--argocd-resume-only`: restore from state and exit (explicit operator action; useful for later re-enablement after retargeting Git, or for failback back to original primary).
- Validation rules (in `[lib/validation.py](lib/validation.py)` + docs):
  - `--validate-only` cannot perform management; treat `--argocd-manage` as no-op with warning, or hard error (pick one and document).
  - `--dry-run` prints intended patches but performs none.

### 4) Wire into workflow phases

- **Preflight phase**: when `--argocd-check` is set, run detection against **both hubs** and include in preflight results/logs.
- **Primary prep phase**: if `--argocd-manage`, pause ACM-touching Applications on:
  - **primary hub** (to prevent GitOps fighting “pause backups / disable auto-import” steps)
  - optionally **secondary hub** (if you expect GitOps there to mutate Restore/BackupSchedule objects pre-activation)
- **Finalization phase**:\n+  - default: **do not** resume auto-sync (leave paused)\n+  - if `--argocd-resume-after-switchover` is set: resume auto-sync using saved state\n+- **On failure**: do **not** auto-resume. Provide `--argocd-resume-only` as the explicit operator action.

### 5) RBAC and RBAC validation updates

- Update RBAC manifests under `[deploy/rbac/](deploy/rbac/)` to include:
  - read/list for CRDs (optional) and Argo resources used for detection.
  - for management: `get/list/patch` on `applications.argoproj.io`.
- Update `[lib/rbac_validator.py](lib/rbac_validator.py)` checks (if applicable) so:
  - detection failures due to RBAC become non-fatal warnings.
  - `--argocd-manage` requires patch permission on Applications and fails fast with a clear message if missing.

### 6) Tests

- Unit tests for new module:
  - parsing `.status.resources` impact detection (mirror the Bash regex/kinds behavior).
  - pause/resume patch payload generation, including “already paused” idempotence.
  - restore safety (only restore if our marker is present).
- CLI tests:
  - argument combination validation for `--argocd-check`, `--argocd-manage`, `--argocd-resume-only`.

## Bash implementation plan

### 1) Improve detection to match “operator + vanilla”

- Extend `check_argocd_acm_resources()` in `[scripts/lib-common.sh](scripts/lib-common.sh)`:
  - current gate is `crd argocds.argoproj.io`; change discovery to:
    - if `argocds.argoproj.io` exists: list instances (keep current output).
    - else if `applications.argoproj.io` exists: still run the Application scan (vanilla support).
    - else: skip with pass message.
- Keep the existing ACM resource scan logic (already strong and actionable).

### 2) Add a dedicated management script (safe + reversible)

- Add new script `[scripts/argocd-manage.sh](scripts/argocd-manage.sh)` that supports:
  - `--context <kubecontext>`
  - `--mode pause|resume`
  - `--target acm` (default)
  - `--state-file <path>` to store/consume original sync policies (JSON)
  - `--dry-run`
- Implementation approach in Bash:
  - list Applications (cluster-wide if allowed; else iterate namespaces that contain Applications).
  - select ACM-touching apps using the same jq logic already present in `check_argocd_acm_resources()`.
  - for pause:
    - fetch each app JSON, record original `.spec.syncPolicy` into state file.
    - patch to remove `.spec.syncPolicy.automated`.
    - apply a marker annotation `acm-switchover.argoproj.io/paused-by=<run_id>`.
  - for resume:
    - read state file and restore saved `.spec.syncPolicy` only when marker annotation matches.\n+    - operator guidance: only resume after Git/desired state has been updated for the target hub, otherwise resume can revert switchover changes.

### 3) Document operator workflow integration

- Update `[scripts/README.md](scripts/README.md)` with:
  - recommended sequence: run preflight with `--argocd-check`, pause with `argocd-manage.sh`, run switchover, resume.

## Documentation updates

- Update:
  - `[docs/operations/usage.md](docs/operations/usage.md)` and `[docs/operations/quickref.md](docs/operations/quickref.md)` with new flags and/or scripts.
  - `[docs/reference/validation-rules.md](docs/reference/validation-rules.md)` for new CLI validation.
  - `[docs/ACM_SWITCHOVER_RUNBOOK.md](docs/ACM_SWITCHOVER_RUNBOOK.md)` to include a clear GitOps/ArgoCD “pause autosync” step when applicable.
  - If runbook changes: sync `.claude/skills/` counterparts (per repo rule).

## Output/UX expectations

- Preflight/Postflight:
  - show ArgoCD discovery summary (install type, namespaces, instances if available).
  - show top ACM-touching Applications and “and N more” truncation.
- Main Python switchover:
  - when manage enabled: log exactly which Applications were paused, where state was stored, and that they are **left paused by default**.
  - when `--argocd-resume-after-switchover` is used: log which Applications were resumed.
  - on resume-only: print what it restored and what it skipped (and why).

