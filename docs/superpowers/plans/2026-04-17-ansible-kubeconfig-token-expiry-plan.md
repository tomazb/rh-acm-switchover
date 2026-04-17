# Ansible Kubeconfig Token Expiry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add kubeconfig token-expiry validation to the Ansible collection preflight flow, matching the Python CLI contract for expired, near-expiry, and non-decodable bearer tokens.

**Architecture:** Implement a small collection module that statically inspects the kubeconfig file and selected context without mutating global Kubernetes client state or executing auth plugins. Wire the module into preflight result reporting next to the existing connectivity checks so expired static bearer JWTs become critical failures while exec/auth-provider kubeconfigs remain advisory.

**Tech Stack:** Ansible module API (`AnsibleModule`), Python stdlib (`base64`, `datetime`, `json`, `pathlib`), PyYAML via Ansible runtime, collection validation result schema.

---

## File Structure

- Create: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_kubeconfig_inspect.py`
  Responsibility: statically inspect kubeconfig auth for one hub/context and return a structured token-expiry result.
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_kubeconfigs.yml`
  Responsibility: call the new module for primary/secondary hubs and append stable preflight report entries.
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/defaults/main.yml`
  Responsibility: expose `acm_switchover_features.token_expiry_warning_hours` with default `4`.
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_kubeconfig_inspect.py`
  Responsibility: module-level behavioral tests for kubeconfig parsing and token classification.
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_kubeconfig_token_expiry.py`
  Responsibility: static/role tests that preflight wires the new result into `validate_kubeconfigs.yml`.
- Modify: `CHANGELOG.md`
  Responsibility: note the new Ansible preflight token-expiry validation.

## Exact Module Schema

**Module name:** `tomazb.acm_switchover.acm_kubeconfig_inspect`

**Arguments**

```python
argument_spec = dict(
    kubeconfig=dict(type="path", required=True),
    context=dict(type="str", required=True),
    warning_hours=dict(type="int", default=4),
)
```

**Return shape**

```python
module.exit_json(
    changed=False,
    status="pass",  # pass | fail | warn | skip
    severity="critical",  # critical for expired static bearer token, otherwise warning/info
    auth_type="bearer_jwt",  # bearer_jwt | bearer_opaque | exec | auth_provider | client_cert | basic | unknown
    message="token valid for 23.8 hours",
    details={
        "context": "secondary-hub",
        "user": "acm-switchover-secondary",
        "expires_at": "2026-04-18T12:30:00+00:00",
        "hours_until_expiry": 23.8,
        "has_exp_claim": True,
    },
)
```

**Classification contract**

- `fail` + `severity=critical`: static bearer JWT has `exp` in the past.
- `warn` + `severity=warning`: static bearer JWT expires within `warning_hours`; exec/auth-provider auth cannot be inspected statically; token is opaque or undecodable.
- `pass` + `severity=info`: static bearer JWT valid beyond threshold; no bearer token and kubeconfig uses client cert/basic auth.
- `skip` + `severity=info`: optional only if context/user is absent and caller wants non-fatal reporting; default implementation should instead `fail_json` on malformed kubeconfig because that is input corruption, not a runtime skip.

### Task 1: Add module tests first

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_kubeconfig_inspect.py`
- Reference: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_restore_info.py`

- [ ] **Step 1: Write the failing test file skeleton**

```python
from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_kubeconfig_inspect import (
    inspect_kubeconfig_auth,
)
```

- [ ] **Step 2: Add a helper that writes kubeconfig fixtures to `tmp_path`**

```python
def write_kubeconfig(tmp_path, data):
    path = tmp_path / "kubeconfig.yaml"
    path.write_text(yaml.safe_dump(data))
    return path
```

- [ ] **Step 3: Add the expected module-level unit cases**

```python
def test_bearer_jwt_valid_returns_pass(tmp_path): ...
def test_bearer_jwt_expired_returns_critical_fail(tmp_path): ...
def test_bearer_jwt_near_expiry_returns_warn(tmp_path): ...
def test_bearer_jwt_without_exp_returns_warn(tmp_path): ...
def test_invalid_jwt_format_returns_warn(tmp_path): ...
def test_exec_auth_returns_warn_without_execution(tmp_path): ...
def test_auth_provider_returns_warn_without_execution(tmp_path): ...
def test_client_certificate_auth_returns_pass(tmp_path): ...
def test_missing_context_raises_validation_error(tmp_path): ...
def test_missing_user_raises_validation_error(tmp_path): ...
```

- [ ] **Step 4: Run the new test file to confirm import failure**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_kubeconfig_inspect.py -q`

Expected: `FAILED` with `ModuleNotFoundError` or missing symbol errors.

- [ ] **Step 5: Commit the failing tests**

```bash
git add ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_kubeconfig_inspect.py
git commit -m "test: define kubeconfig token inspection cases"
```

### Task 2: Implement the inspection module

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_kubeconfig_inspect.py`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_kubeconfig_inspect.py`

- [ ] **Step 1: Add the pure inspection helpers before `run_module()`**

```python
def _find_named(items: list[dict], name: str) -> dict | None:
    return next((item for item in items if item.get("name") == name), None)


def _decode_jwt_exp(token: str) -> tuple[datetime | None, str | None]:
    parts = token.split(".")
    if len(parts) != 3:
        return None, "invalid JWT format"
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")))
    exp = claims.get("exp")
    if exp is None:
        return None, "token has no expiration claim"
    return datetime.fromtimestamp(exp, tz=timezone.utc), None
```

- [ ] **Step 2: Implement the core inspector without loading global kube config**

```python
def inspect_kubeconfig_auth(kubeconfig: str, context: str, warning_hours: int = 4) -> dict:
    config = yaml.safe_load(Path(kubeconfig).read_text()) or {}
    contexts = config.get("contexts", [])
    users = config.get("users", [])
    ctx = _find_named(contexts, context)
    if ctx is None:
        raise ValueError(f"context '{context}' not found in kubeconfig")
    user_name = ctx.get("context", {}).get("user")
    user_entry = _find_named(users, user_name)
    if user_entry is None:
        raise ValueError(f"user '{user_name}' not found for context '{context}'")
    user_cfg = user_entry.get("user", {})
```

- [ ] **Step 3: Implement auth-type classification**

```python
    if "exec" in user_cfg:
        return {"status": "warn", "severity": "warning", "auth_type": "exec", ...}
    if "auth-provider" in user_cfg:
        return {"status": "warn", "severity": "warning", "auth_type": "auth_provider", ...}
    if "client-certificate" in user_cfg or "client-certificate-data" in user_cfg:
        return {"status": "pass", "severity": "info", "auth_type": "client_cert", ...}
    token = user_cfg.get("token") or user_cfg.get("tokenFile")
```

- [ ] **Step 4: Handle static bearer tokens with exact expiry behavior**

```python
    expires_at, decode_error = _decode_jwt_exp(token)
    if decode_error:
        return {"status": "warn", "severity": "warning", "auth_type": "bearer_opaque", ...}
    hours_until_expiry = (expires_at - datetime.now(timezone.utc)).total_seconds() / 3600
    if hours_until_expiry < 0:
        return {"status": "fail", "severity": "critical", "auth_type": "bearer_jwt", ...}
    if hours_until_expiry < warning_hours:
        return {"status": "warn", "severity": "warning", "auth_type": "bearer_jwt", ...}
    return {"status": "pass", "severity": "info", "auth_type": "bearer_jwt", ...}
```

- [ ] **Step 5: Add `run_module()` and wire errors to `fail_json()`**

```python
def run_module():
    module = AnsibleModule(argument_spec=..., supports_check_mode=True)
    try:
        result = inspect_kubeconfig_auth(...)
    except ValueError as exc:
        module.fail_json(msg=str(exc))
    module.exit_json(changed=False, **result)
```

- [ ] **Step 6: Run the module tests**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_kubeconfig_inspect.py -q`

Expected: all tests `PASS`.

- [ ] **Step 7: Commit the module**

```bash
git add ansible_collections/tomazb/acm_switchover/plugins/modules/acm_kubeconfig_inspect.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_kubeconfig_inspect.py
git commit -m "feat: inspect kubeconfig token expiry in collection preflight"
```

### Task 3: Wire the module into preflight

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_kubeconfigs.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/defaults/main.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_kubeconfig_token_expiry.py`

- [ ] **Step 1: Add the feature default**

```yaml
acm_switchover_features:
  token_expiry_warning_hours: 4
```

- [ ] **Step 2: Call the new module for the primary hub**

```yaml
- name: Inspect primary hub kubeconfig token expiry
  tomazb.acm_switchover.acm_kubeconfig_inspect:
    kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.primary.context }}"
    warning_hours: "{{ acm_switchover_features.token_expiry_warning_hours | default(4) }}"
  register: _acm_primary_token_expiry_result
  when: not (acm_switchover_operation.restore_only | default(false))
```

- [ ] **Step 3: Append the structured validation result**

```yaml
{
  "id": "preflight-kubeconfig-primary-token-expiry",
  "severity": _acm_primary_token_expiry_result.severity,
  "status": _acm_primary_token_expiry_result.status,
  "message": _acm_primary_token_expiry_result.message,
  "details": _acm_primary_token_expiry_result.details,
  "recommended_action": "Regenerate the primary hub kubeconfig or service account token before switchover"
    if _acm_primary_token_expiry_result.status in ["fail", "warn"]
    else none
}
```

- [ ] **Step 4: Mirror the same for the secondary hub**

Run the same pattern with id `preflight-kubeconfig-secondary-token-expiry`.

- [ ] **Step 5: Add static role tests**

```python
def test_validate_kubeconfigs_calls_acm_kubeconfig_inspect(): ...
def test_validate_kubeconfigs_records_primary_token_expiry_result(): ...
def test_validate_kubeconfigs_records_secondary_token_expiry_result(): ...
def test_restore_only_skips_primary_token_expiry_check(): ...
```

- [ ] **Step 6: Run only the new role tests**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_kubeconfig_token_expiry.py -q`

Expected: all tests `PASS`.

- [ ] **Step 7: Commit the preflight wiring**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/preflight/defaults/main.yml ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_kubeconfigs.yml ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_kubeconfig_token_expiry.py
git commit -m "feat: report kubeconfig token expiry in collection preflight"
```

### Task 4: Cover edge cases and repo verification

**Files:**
- Modify: `CHANGELOG.md`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_kubeconfig_inspect.py`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/`

- [ ] **Step 1: Add the two important edge-case tests that often get skipped**

```python
def test_token_file_path_is_reported_as_non_static_warn(tmp_path): ...
def test_warning_hours_zero_only_warns_after_actual_expiry(tmp_path): ...
```

- [ ] **Step 2: Update changelog**

```markdown
- **Ansible preflight**: add kubeconfig token-expiry inspection for static bearer JWT credentials; expired tokens now fail early and near-expiry tokens warn operators before switchover.
```

- [ ] **Step 3: Run the targeted collection test set**

Run: `ANSIBLE_LOCAL_TEMP=/tmp/ansible-local python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_kubeconfig_inspect.py ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_kubeconfig_token_expiry.py -q`

Expected: all tests `PASS`.

- [ ] **Step 4: Run the full collection unit suite**

Run: `ANSIBLE_LOCAL_TEMP=/tmp/ansible-local python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q`

Expected: full collection unit suite `PASS`.

- [ ] **Step 5: Commit the final verification pass**

```bash
git add CHANGELOG.md ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_kubeconfig_inspect.py ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_kubeconfig_token_expiry.py
git commit -m "test: cover kubeconfig token expiry edge cases"
```

## Expected Test Cases

- `valid static bearer JWT` → `status=pass`, `severity=info`, `auth_type=bearer_jwt`
- `expired static bearer JWT` → `status=fail`, `severity=critical`
- `JWT expiring within 4h` → `status=warn`, `severity=warning`
- `JWT with no exp claim` → `status=warn`, advisory message
- `malformed JWT payload` → `status=warn`, advisory message
- `exec-based kubeconfig user` → `status=warn`, message must state that exec auth is not executed
- `auth-provider user` → `status=warn`, same non-execution advisory
- `client-certificate auth` → `status=pass`, message states static token expiry is not applicable
- `missing requested context` → module `fail_json`
- `missing user for context` → module `fail_json`
- `restore-only preflight` → primary token-expiry task absent/skipped, secondary still reported
- `role output wiring` → validation ids `preflight-kubeconfig-primary-token-expiry` and `preflight-kubeconfig-secondary-token-expiry` appear exactly once

## Self-Review

- Spec coverage: covers exact module schema, collection wiring, severity contract, and expected tests.
- Placeholder scan: no `TBD`/`TODO`/implicit “write tests later” steps remain.
- Type consistency: module returns `status`, `severity`, `auth_type`, `message`, and `details`; the role plan uses the same keys consistently.

Plan complete and saved to `docs/superpowers/plans/2026-04-17-ansible-kubeconfig-token-expiry-plan.md`. Two execution options:

1. Subagent-Driven (recommended) - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. Inline Execution - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
