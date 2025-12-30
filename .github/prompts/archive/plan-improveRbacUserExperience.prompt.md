# Plan: Improve RBAC User Experience

The goal is to make RBAC setup effortless - users should be able to go from cluster-admin access to a fully configured, secure switchover environment with minimal commands. The approach starts with admin credentials to bootstrap everything else.

## Steps

1. **Add `--user` flag to `generate-sa-kubeconfig.sh`** to allow custom user names in generated kubeconfigs, preventing credential collisions during merge. Default to `{context}-{sa-name}` pattern.

2. **Create new `scripts/generate-merged-kubeconfig.sh` helper** that generates and merges kubeconfigs for multiple clusters (hubs and/or managed clusters) in one command. Accept comma-separated list of contexts with roles (e.g., `mgmt1:operator,mgmt2:operator,prod1:operator`).

3. **Create new `scripts/setup-rbac.sh` bootstrap script** that uses cluster-admin context to:
   - Deploy RBAC manifests to specified hubs
   - Optionally deploy managed cluster RBAC (direct kubectl or via ACM Policy)
   - Generate SA kubeconfigs with unique user names
   - Merge into single kubeconfig file
   - Validate with `check_rbac.py`

4. **Add `--setup` subcommand to `acm_switchover.py`** as a Python-native alternative that orchestrates RBAC deployment and kubeconfig generation using the KubeClient library.

5. **Enhance kubeconfig validation in preflight** to detect common issues: duplicate user credentials, expired tokens, missing contexts - and provide actionable remediation messages.

6. **Update documentation** (`docs/deployment/rbac-deployment.md`) with new "Quick Setup" section showing the single-command workflow.

## Further Considerations

1. **Bootstrap approach**: Should `setup-rbac.sh` require admin kubeconfig as input, or detect current context? → Recommend explicit `--admin-kubeconfig` flag for clarity and safety.

2. **Managed cluster handling**: Should the merge script support generating kubeconfigs for managed clusters too? → Yes, for users who want to validate klusterlet connectivity or run post-switchover checks.

3. **Token duration**: Should we increase default from 24h to 48h for longer operations? → Yes, and add `--token-duration` flag to setup script.
