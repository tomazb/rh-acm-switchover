# Implementation Report: Review Findings for Argo CD Resume/Discovery and Passive-Sync Finalization

Date: 2026-04-15

## Scope

This report validates the three review findings against the current codebase and translates them into a concrete implementation plan. The goal is not to restate the review verbatim, but to map each issue to:

- current code behavior
- production impact
- minimal corrective change
- tests that should be added or updated

No product code was changed while preparing this report.

## Inputs Reviewed

- `ansible_collections/tomazb/acm_switchover/playbooks/argocd_resume.yml`
- `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/main.yml`
- `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/main.yml`
- `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/discover.yml`
- `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/resume.yml`
- `modules/finalization.py`
- `modules/activation.py`
- `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py`
- `tests/test_finalization.py`

## Executive Summary

All three review findings are valid.

1. The standalone Ansible resume playbook only resumes the secondary hub, even though `primary_prep` pauses Argo CD applications on both hubs.
2. Argo CD application discovery is hardcoded to a single namespace defaulting to `argocd`, which misses real deployments that use `openshift-gitops` or other namespaces.
3. Python finalization deletes `restore-acm-passive-sync` and recreates it immediately without waiting for Kubernetes deletion to finish, which can produce an `AlreadyExists` failure in repeat or failback flows.

The fixes are localized and low-risk if implemented narrowly. The main work is in tightening test coverage so these regressions cannot reappear.

## Finding 1: Standalone `argocd_resume.yml` only resumes the secondary hub

### Validation

The finding is correct.

`primary_prep` explicitly pauses Argo CD on both hubs:

- `roles/primary_prep/tasks/main.yml` includes `argocd_manage` once with `_argocd_discover_hub: primary`
- the same file includes it again with `_argocd_discover_hub: secondary`

The standalone resume entrypoint does not mirror that behavior. `playbooks/argocd_resume.yml:6-11` contains only one include:

- resume on `secondary`
- no corresponding resume on `primary`

This is narrower than the main finalization flow. `roles/finalization/tasks/main.yml` already resumes both hubs when `resume_after_switchover` is enabled, so the defect is specific to the standalone recovery/resume playbook.

### Operational Impact

This breaks the normal manual follow-up path where operators:

1. pause ACM-touching Argo CD applications during switchover
2. retarget Git after the hub role swap
3. run `argocd_resume.yml` to restore autosync

In that workflow, applications paused on the old primary remain paused indefinitely because the standalone playbook never touches that hub. The result is asymmetric recovery:

- new primary resumes
- old primary stays disabled

That is especially problematic for failback readiness and for any application inventory still expected to reconcile on the old primary as the new passive hub.

### Minimal Corrective Change

Update `ansible_collections/tomazb/acm_switchover/playbooks/argocd_resume.yml` to resume both hubs, mirroring the structure already used in `finalization`.

Recommended shape:

- keep the existing secondary resume block
- add a second `include_role` for `_argocd_discover_hub: primary`
- guard the primary include with `acm_switchover_hubs.primary is defined`

This should stay intentionally simple. There is no need to refactor shared task includes unless more playbooks need the same behavior later.

### Test Updates

Current coverage misses this because the existing unit test checks `finalization/main.yml`, not the standalone playbook.

Add a unit test in `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py` or a nearby playbook-focused test file that asserts:

- `playbooks/argocd_resume.yml` contains a resume include for `secondary`
- `playbooks/argocd_resume.yml` contains a resume include for `primary`

If an integration test is added later, it should verify the playbook invokes both hub variants, but the immediate regression guard can be a YAML-structure unit test.

## Finding 2: Argo CD discovery searches only one namespace

### Validation

The finding is correct.

`roles/argocd_manage/tasks/discover.yml:29-35` uses:

```yaml
namespace: "{{ acm_switchover_argocd.namespace | default('argocd') }}"
```

That means live discovery only looks in one namespace unless the operator overrides it. The rest of the role assumes this discovery result is authoritative:

- `pause.yml` patches whatever `discover.yml` found
- `resume.yml` resumes whatever `discover.yml` found
- switchover and restore-only flows depend on this protection before ACM resources are mutated

If real Argo CD `Application` objects live in `openshift-gitops`, `gitops`, or any other namespace, discovery silently returns no relevant applications and the pause/resume protection effectively disappears.

### Operational Impact

This is a real functional gap, not just a usability issue.

The role can report a clean run while failing to pause any ACM-touching GitOps applications. In production, that means Argo CD can continue reconciling against pre-switchover desired state while ACM resources are being moved, which is exactly the class of interference the role is meant to prevent.

The bad outcome is silent:

- no hard failure
- no explicit warning that discovery scope was too narrow
- operators believe GitOps has been paused when it has not

### Minimal Corrective Change

The safest minimal change is to stop defaulting to a single namespace.

Recommended implementation:

```yaml
namespace: "{{ acm_switchover_argocd.namespace | default(omit) }}"
```

That preserves explicit namespace overrides while allowing default behavior to query applications cluster-wide when no namespace is configured.

Why this is preferable to keeping a hardcoded default:

- preserves backward compatibility for users who explicitly set a namespace
- fixes the default behavior for multi-namespace and OpenShift GitOps deployments
- keeps the change isolated to discovery without touching pause/resume patch logic

Avoid bundling this with a broader "Argo CD installation detection" refactor. The reviewer finding is about search scope, and that is the part that needs immediate correction.

### Test Updates

Current unit coverage only verifies that `discover.yml` uses `_argocd_discover_hub` for kubeconfig/context. It does not guard namespace behavior.

Add or update a unit test to assert one of the following:

- `discover.yml` does not hardcode `default('argocd')`, or
- `discover.yml` uses `default(omit)` for namespace handling

If there is appetite for stronger coverage, add a role-level test with mocked `Application` objects from multiple namespaces and assert they survive discovery and filtering.

## Finding 3: Finalization deletes and recreates passive-sync restore without waiting

### Validation

The finding is correct.

In `modules/finalization.py:1130-1153`, `_setup_old_hub_as_secondary()`:

1. reads the existing passive-sync restore
2. deletes it when `veleroManagedClustersBackupName != skip`
3. immediately creates a new restore with the same name

There is no wait between delete and recreate.

This differs from the activation flow, which already handles restore deletion asynchronously. `modules/activation.py` explicitly calls `_wait_for_restore_deletion()` after deleting a restore and before recreating or replacing it.

The current finalization unit test also reflects the gap. `tests/test_finalization.py` verifies delete-then-create, but not wait-then-create.

### Operational Impact

This creates a real race with Kubernetes object deletion semantics.

`delete_custom_resource()` submits the delete request, but object disappearance is asynchronous. If the old primary already contains `restore-acm-passive-sync` in activated `latest` mode, repeated switchovers or failback operations can hit this sequence:

1. delete requested
2. old restore still exists momentarily
3. create new restore with same name
4. API returns `AlreadyExists`
5. finalization aborts

The failure is intermittent, which makes it harder to diagnose in production and more likely to surface only in repeat DR exercises or failback validation.

### Minimal Corrective Change

Add an explicit restore-deletion wait in `Finalization`, analogous to the activation path.

Recommended approach:

1. Import the restore wait constants already used by activation:
   - `RESTORE_WAIT_TIMEOUT`
   - `RESTORE_POLL_INTERVAL`
   - `RESTORE_FAST_POLL_INTERVAL`
   - `RESTORE_FAST_POLL_TIMEOUT`
2. Add a small helper in `modules/finalization.py`, for example `_wait_for_primary_restore_deletion(restore_name, timeout=RESTORE_WAIT_TIMEOUT)`.
3. Poll `self.primary.get_custom_resource(...)` until the restore is absent.
4. Call the helper immediately after successful deletion and before `create_custom_resource(...)`.

The helper should use `wait_for_condition`, matching the existing project pattern.

Important implementation detail:

- this wait must poll the old primary client (`self.primary`), not `self.secondary`

Optional consistency improvement:

- pass `timeout_seconds=DELETE_REQUEST_TIMEOUT` to `delete_custom_resource()` here as well, matching other delete paths

That consistency change is reasonable, but the must-have fix is the post-delete wait.

### Test Updates

At minimum, extend `tests/test_finalization.py` with:

- a test that asserts the new wait helper is called after deleting a stale active restore and before recreation
- a test that ensures timeout or failed disappearance propagates as an error instead of attempting recreate immediately

Better behavioral coverage would mock `primary.get_custom_resource()` to return:

1. restore present
2. restore still present during polling
3. `None` once deletion completes

Then assert create happens only after the helper path completes.

The existing test `test_setup_old_hub_as_secondary_resets_stale_active_restore` should be updated rather than duplicated if possible.

## Recommended File Touch List

If these fixes are implemented now, the expected minimal write set is:

- `ansible_collections/tomazb/acm_switchover/playbooks/argocd_resume.yml`
- `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/discover.yml`
- `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py`
- `modules/finalization.py`
- `tests/test_finalization.py`

Additional test files are optional, not required.

## Suggested Implementation Order

1. Fix Argo CD discovery namespace scope.
   This provides the biggest operational safety improvement because it restores the pause/resume guard in real GitOps deployments.

2. Fix standalone Argo CD resume coverage across both hubs.
   This is a small change with direct operator-facing impact.

3. Add the finalization restore deletion wait.
   This is localized but needs careful test updates because it changes the control flow around object recreation.

That order keeps the Ansible functional gaps together and finishes with the Python retry/race correction.

## Verification Plan After Implementation

Run targeted tests first:

```bash
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py -q
python -m pytest tests/test_finalization.py -q
```

Then run the normal combined unit suite:

```bash
source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ tests/ -q
```

If a live or mocked playbook contract check is available, include it after the unit tests:

```bash
python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_argocd_manage_role.py -q
```

## Conclusion

The review surfaced three concrete defects, and the codebase inspection confirms each one.

The fixes do not require architectural refactoring. They are best handled as small, direct corrections with strong regression tests:

- resume both hubs in the standalone Argo CD resume playbook
- discover Argo CD applications across namespaces by default
- wait for passive-sync restore deletion before recreating it during finalization
