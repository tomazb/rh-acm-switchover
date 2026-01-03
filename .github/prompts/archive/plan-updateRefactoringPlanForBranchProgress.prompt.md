## Plan: Update Refactoring Plan for Branch Progress

The `swe-refactoring` branch has completed most of Phase 1 with an `@api_call` decorator implemented inline in `kube_client.py`. The refactoring plan documents need updating to reflect actual progress, architectural decisions, and remaining work.

**Files to update:**
- [REFACTORING_PLAN.md](REFACTORING_PLAN.md) — Main refactoring strategy and design decisions
- [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md) — Task tracking with completion status

### Steps

1. **Update [REFACTORING_PLAN.md](REFACTORING_PLAN.md) Phase 1 section**: Replace all `lib/decorators.py` references with inline implementation in [lib/kube_client.py](lib/kube_client.py), rename decorator from `@handle_api_exception` to `@api_call`, update parameters to `not_found_value`, `log_on_error`, `resource_desc`, and add architectural rationale (locality, tight coupling to Kubernetes API, single-use scope).

2. **Add "Methods Retaining Manual Handling" section to [REFACTORING_PLAN.md](REFACTORING_PLAN.md)**: Document 7 methods that intentionally use `@retry_api_call` with manual handling and their rationale — `create_or_patch_configmap` (complex conditional flow), `list_custom_resources` (pagination loop), `patch_custom_resource` (extended debug logging), `create_custom_resource` (create semantics), `scale_deployment`/`scale_statefulset`/`rollout_restart_deployment` (write operations where 404 is an error).

3. **Update [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md) Phase 1 items**: Check off 8 converted methods (`get_namespace`, `get_secret`, `get_configmap`, `get_route_host`, `get_custom_resource`, `list_custom_resources`, `patch_custom_resource`, `delete_custom_resource`), mark 7 methods as "N/A - intentional manual handling", remove "Create `lib/decorators.py`" item, add new item for decorator unit tests.

4. **Add decorator test specification to [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md)**: New `TestApiCallDecorator` class in [tests/test_kube_client.py](tests/test_kube_client.py) with tests for: (a) 404 returns `not_found_value`, (b) 5xx/429 re-raises for tenacity retry, (c) 4xx errors logged and re-raised when `log_on_error=True`, (d) logging suppressed when `log_on_error=False`, (e) `resource_desc` used in error messages.

5. **Update timeline and metrics in both [REFACTORING_PLAN.md](REFACTORING_PLAN.md) and [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md)**: Mark Phase 1 as ~90% complete (decorator tests remaining), Phase 2 unchanged and ready to start, revise total estimate to 5-8 days remaining.

### Implementation Instructions

After completing steps 1-5, the updated [REFACTORING_PLAN.md](REFACTORING_PLAN.md) and [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md) become the source of truth for implementation.

6. **Implement remaining Phase 1 work from [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md)**:
   - Follow project conventions in [AGENTS.md](AGENTS.md)
   - Work through unchecked items in the Phase 1 section (primarily: add `TestApiCallDecorator` tests)
   - Run tests after each change: `./run_tests.sh`
   - Mark items complete in checklist as you finish them
   - Commit changes with clear messages referencing the checklist item

7. **Proceed to Phase 2 when Phase 1 is complete**:
   - All Phase 1 checklist items must be checked off
   - Tests must pass with no regressions
   - Follow the Phase 2 implementation steps in [REFACTORING_PLAN.md](REFACTORING_PLAN.md)
   - Update [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md) as you complete each item

**Verification**: After implementation, the verifying agent will:
- Review changes against the checklist
- Run full test suite to confirm no regressions
- Validate architectural decisions match the plan
