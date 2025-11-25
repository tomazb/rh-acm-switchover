# Reliability Hardening Plan

This document tracks the progress of reliability hardening tasks for the ACM Switchover tool.

## High Priority

- [x] **1. Implement API Retry Logic** (Est: 4h)
    - Add `tenacity` dependency.
    - Create `@retry_with_backoff` decorator in `lib/kube_client.py`.
    - Apply decorator to all `KubeClient` methods (`get_`, `list_`, `patch_`, `create_`, `delete_`).
    - Handle `ApiException` (5xx) and `urllib3` connection errors.

- [x] **2. Client-Side Timeouts** (Est: 1h)
    - Update `KubeClient.__init__` to accept `request_timeout`.
    - Configure `kubernetes.client.Configuration` with default timeouts (e.g., 30s connect, 60s read).

- [ ] **3. Failure Scenario Testing** (Est: 4h)
    - Create unit tests mocking API failures (503, timeouts).
    - Verify retry logic behavior (backoff, eventual failure).
    - Ensure `SwitchoverError` is raised appropriately.

## Medium Priority

- [ ] **4. Refine Exception Handling** (Est: 3h)
    - Define custom exception hierarchy in `lib/exceptions.py` (e.g., `SwitchoverError`, `TransientError`, `FatalError`).
    - Update `modules/*.py` to catch specific exceptions instead of generic `Exception`.
    - Ensure fatal errors stop execution immediately.

## Low Priority

- [ ] **5. Structured Logging** (Est: 2h)
    - Update `lib/utils.py` to support JSON logging via `--log-format=json`.
    - Add context to log messages (cluster, resource, phase).

## Documentation Updates (Post-Implementation)

- [ ] Update `requirements.txt` with `tenacity`.
- [ ] Update `ARCHITECTURE.md` to reflect:
    - New exception handling strategy.
    - Retry logic in Kubernetes Client component.
    - Updated Mermaid diagrams if component interactions change.
- [ ] Update `PRD.md` (Reliability NFRs).
- [ ] Update `USAGE.md` (new flags like `--log-format` if added).
