# Bash Script Test Suite

This slice covers the repo's shell-centric and CLI-adjacent tooling, not just the original pre/post-flight scripts.

## Test Organization

### Script and Tool Coverage

- `test_scripts.py` covers fast argument-parsing and output-shape checks for:
  - `scripts/preflight-check.sh`
  - `scripts/postflight-check.sh`
  - shared shell helpers from `scripts/lib-common.sh`
- `test_scripts_integration.py` covers mocked end-to-end shell scenarios and RBAC packaging guidance.
- `test_check_rbac.py` covers `check_rbac.py`.
- `test_generate_merged_kubeconfig_script.py` covers `scripts/generate-merged-kubeconfig.sh`.
- `test_argocd_manage_script.py` covers failure handling and safety edges in `scripts/argocd-manage.sh`.

## What These Tests Emphasize

- Help output and argument validation
- Exit-code discipline for shell entry points
- Mocked `oc`/`kubectl`/`jq` scenarios for script behavior
- Packaging and safety regressions around generated kubeconfigs and RBAC assets
- Guardrails for deprecated or risky tool flows

## Running Tests

### Focused Shell/Tool Slice

```bash
pytest tests/test_scripts.py tests/test_scripts_integration.py \
  tests/test_check_rbac.py tests/test_generate_merged_kubeconfig_script.py \
  tests/test_argocd_manage_script.py -v
```

### Original Pre/Post-Flight Slice

```bash
pytest tests/test_scripts.py tests/test_scripts_integration.py -v
```

### Narrow Tool Checks

```bash
pytest tests/test_check_rbac.py -v
pytest tests/test_generate_merged_kubeconfig_script.py -v
pytest tests/test_argocd_manage_script.py -v
```

## Notes

- The exact number of tool-focused tests changes as new scripts and regression checks are added.
- Keep this README aligned with actual coverage whenever a new shell entry point or standalone CLI gains dedicated tests.
