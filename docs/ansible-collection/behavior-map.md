# ACM Switchover Behavior Map

Date: 2026-04-10
Source: `lib/`, `modules/`, `scripts/`

## Mapping Rules

- workflow sequencing belongs in playbooks and roles
- API-heavy normalization, retry behavior, polling semantics, and version interpretation belong in later custom plugins
- Phase 1 documents those boundaries but does not implement the plugins
- prefer `kubernetes.core.k8s` and `kubernetes.core.k8s_info` wherever they are sufficient

## Current-to-Collection Mapping

| Current Source | Collection Target | Phase |
| --- | --- | --- |
| `acm_switchover.py` | `playbooks/preflight.yml`, `playbooks/switchover.yml` | 1 |
| `modules/preflight_coordinator.py` and `modules/preflight/` | `roles/preflight/` | 2 |
| `modules/primary_prep.py` | `roles/primary_prep/` | 3 |
| `modules/activation.py` | `roles/activation/` | 3 |
| `modules/post_activation.py` | `roles/post_activation/` | 3 |
| `modules/finalization.py` | `roles/finalization/` | 3 |
| `modules/decommission.py` | `roles/decommission/` | 6 |
| `lib/rbac_validator.py` | `roles/preflight/` validation behavior | 2 |
| `lib/validation.py` | centralized collection validation layer | 2 |
| `lib/kube_client.py` | stock `kubernetes.core` usage plus later helper code | 2-3 |
| `lib/utils.py` checkpoint semantics | documented checkpoint contract only | 1, runtime in 4 |
| `lib/argocd.py` | `roles/argocd_manage/` and deferred playbook | 5 |
| `lib/gitops_detector.py` | preflight detection and warnings | 5 |
| `scripts/discover-hub.sh` | supported migration bridge, not rewritten in Phase 1 | coexistence |
| `scripts/setup-rbac.sh` | `rbac_bootstrap` later | 6 |

## ACM Version Gates to Preserve

- ACM 2.11 BackupSchedule delete semantics
- ACM 2.12+ BackupSchedule pause semantics
- ACM 2.14+ `autoImportStrategy`

Roles must not hard-code scattered version comparisons. Phase 1 should document normalized facts only:

- `backup_schedule_pause_mode`
- `supports_auto_import_strategy`
- `supports_managed_service_account`

## Explicit Deferral

This behavior map intentionally does not schedule checkpoint backends, action plugins, or custom module implementation in Phase 1.
