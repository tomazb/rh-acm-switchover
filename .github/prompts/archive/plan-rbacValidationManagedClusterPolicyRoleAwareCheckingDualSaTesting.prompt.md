## Plan: RBAC Validation with Managed Cluster Policy, Role-Aware Checking, and Dual-SA Testing

This plan creates managed cluster RBAC via ACM Policy (Option B), enhances `check_rbac.py` to differentiate expected vs unexpected denials based on role, adds validator secrets permissions, and establishes comprehensive testing for mgmt1/mgmt2 hubs with prod1-prod3 managed clusters.

### Steps

1. **Create managed cluster RBAC policy** - Add new [`deploy/acm-policies/policy-managed-cluster-rbac.yaml`](deploy/acm-policies/) with ConfigurationPolicy for `open-cluster-management-agent` namespace Role/RoleBinding containing `secrets: [create, delete]` and `deployments: [patch]`; set PlacementRule to target managed clusters (exclude local-cluster); reference existing policy structure from [`policy-rbac.yaml`](deploy/acm-policies/policy-rbac.yaml).

2. **Add `--role` flag to check_rbac.py** - Extend [`check_rbac.py`](check_rbac.py) argument parser with `--role {operator,validator}` option; update validation logic to differentiate expected denials (write ops for validator) vs unexpected denials; modify report generation in [`RBACValidator.generate_permission_report()`](lib/rbac_validator.py#L268) to show "✓ Expected denied" vs "✗ Unexpected denied".

3. **Refactor RBACValidator with role-aware permissions** - Add `VALIDATOR_PERMISSIONS` dict to [`lib/rbac_validator.py`](lib/rbac_validator.py) containing read-only subset of `NAMESPACE_PERMISSIONS`; add `validate_for_role(role: str)` method that checks operator permissions fully or validator permissions (read-only subset); update `NAMESPACE_PERMISSIONS` to add `secrets: [get]` for validator in backup namespace.

4. **Split hub vs managed cluster namespace permissions** - Rename `NAMESPACE_PERMISSIONS` to `HUB_NAMESPACE_PERMISSIONS` in [`RBACValidator`](lib/rbac_validator.py); add `MANAGED_CLUSTER_NAMESPACE_PERMISSIONS` with `open-cluster-management-agent` entry only; add `validate_managed_cluster_permissions(context: str)` method; update existing `validate_namespace_permissions()` to use `HUB_NAMESPACE_PERMISSIONS` and skip agent namespace.

5. **Update RBAC manifests for validator secrets** - Add `secrets: [get]` rule to validator Role in `open-cluster-management-backup` namespace in [`deploy/rbac/role.yaml`](deploy/rbac/role.yaml) (after line 145); mirror in [`deploy/helm/acm-switchover-rbac/templates/role.yaml`](deploy/helm/acm-switchover-rbac/templates/role.yaml) validator backup Role section.

6. **Add comprehensive RBAC integration tests** - Extend [`tests/test_rbac_integration.py`](tests/test_rbac_integration.py) with: test that validator backup Role includes secrets permission; test that MCE permissions match between code and manifests; test that `--role validator` produces expected denial report format; test managed cluster policy includes agent namespace Role.

### Further Considerations

1. **Managed cluster policy PlacementRule targeting** - The new managed cluster RBAC policy should use a PlacementRule that matches all managed clusters but excludes `local-cluster` (the hub itself). Example selector: `matchExpressions: [{key: "local-cluster", operator: "NotIn", values: ["true"]}]`. Verify this works in the test environment with prod1-prod3.

2. **Validator role expected permissions matrix** - Document clearly which permissions are expected to pass/fail for validator:
   - **Expected PASS**: all `get`, `list` verbs
   - **Expected DENIED**: `create`, `patch`, `delete` verbs (except where explicitly granted)
   - **Unexpected DENIED**: any read operation that fails
   - This matrix should be encoded in `RBACValidator` for automated checking.

3. **Environment validation command sequence** - Final test sequence for proving the model:
   ```
   1. Deploy hub RBAC to mgmt1, mgmt2
   2. Deploy managed cluster policy to mgmt1 (propagates to prod1-prod3)
   3. Generate operator kubeconfig for mgmt1, mgmt2
   4. Generate validator kubeconfig for mgmt1, mgmt2
   5. Run: check_rbac.py --role operator --primary-context mgmt1 --secondary-context mgmt2 (expect all pass)
   6. Run: check_rbac.py --role validator --primary-context mgmt1 (expect read pass, write denied as expected)
   7. Run: acm_switchover.py --dry-run with operator kubeconfig (expect success)
   8. Reset to snapshot, run full switchover with operator kubeconfig (prove least-privilege works)
   ```
