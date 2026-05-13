# Release Validation Profile Contract Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the release profile schema, normalized profile model, profile loader, checked-in example profiles, and profile secret scanning.

**Architecture:** Add a `tests/release/contracts/` package that owns profile schema V1 and returns immutable normalized objects consumed by later release fixtures. Keep schema defaults in one place so recovery, artifacts, and selection code never duplicate default values. Profile examples stay sanitized and deterministic.

**Tech Stack:** Python dataclasses, PyYAML, pytest, pathlib, re, existing repository pytest configuration.

---

## File Map

- Create: `tests/release/__init__.py` to mark the release test package.
- Create: `tests/release/contracts/__init__.py` to export the contract API.
- Create: `tests/release/contracts/models.py` for frozen dataclasses and enums.
- Create: `tests/release/contracts/schema.py` for schema/default validation and content scanning.
- Create: `tests/release/contracts/loader.py` for YAML loading, hashing, and public load functions.
- Create: `tests/release/profiles/full-release.example.yaml` for full certification examples.
- Create: `tests/release/profiles/argocd-release.example.yaml` for Argo CD focused examples.
- Create: `tests/release/profiles/dev-minimal.example.yaml` for local non-certification examples.
- Create: `tests/release/contracts/test_profiles.py` for schema and loader unit tests.
- Modify: `.gitignore` to ignore `tests/release/profiles/local/` if it is not already ignored.
- Modify: `docs/superpowers/plans/2026-04-27-release-validation-progress.md` after implementation and verification.

## Task 1: Package Skeleton And Public API

**Files:**
- Create: `tests/release/__init__.py`
- Create: `tests/release/contracts/__init__.py`
- Create: `tests/release/contracts/models.py`
- Test: `tests/release/contracts/test_profiles.py`

- [ ] **Step 1: Write the import contract test**

```python
from pathlib import Path

from tests.release.contracts import LoadProfileResult, ProfileValidationError, load_profile


def test_contracts_public_api_imports() -> None:
    assert callable(load_profile)
    assert ProfileValidationError.__name__ == "ProfileValidationError"
    assert LoadProfileResult.__name__ == "LoadProfileResult"
    assert Path("tests/release/contracts").exists()
```

- [ ] **Step 2: Run the test and confirm it fails for missing package exports**

Run: `python -m pytest tests/release/contracts/test_profiles.py::test_contracts_public_api_imports -q`

Expected: failure with `ModuleNotFoundError` or `ImportError` for `tests.release.contracts`.

- [ ] **Step 3: Add the minimal public API**

```python
# tests/release/contracts/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


class ProfileValidationError(ValueError):
    """Raised when a release profile violates schema or content policy."""


@dataclass(frozen=True)
class LoadProfileResult:
    path: Path
    sha256: str
    profile: "ReleaseProfile"


@dataclass(frozen=True)
class HubProfile:
    kubeconfig: str
    context: str
    acm_namespace: str = "open-cluster-management"
    role_label_selector: str | None = None
    timeout_minutes: int | None = None


@dataclass(frozen=True)
class ManagedClustersProfile:
    expected_names: tuple[str, ...] = ()
    expected_count: int | None = None
    contexts: Mapping[str, str] = field(default_factory=dict)
    require_observability: bool = True


@dataclass(frozen=True)
class StreamProfile:
    id: str
    enabled: bool = True
    required: bool | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    extra_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenarioProfile:
    id: str
    required: bool | None = None
    streams: tuple[str, ...] = ()
    cycles: int = 1
    timeout_minutes: int | None = None
    skip_reason: str | None = None


@dataclass(frozen=True)
class ReleaseProfile:
    profile_version: int
    name: str
    raw: Mapping[str, Any]
    hubs: Mapping[str, HubProfile]
    managed_clusters: ManagedClustersProfile
    streams: tuple[StreamProfile, ...]
    scenarios: tuple[ScenarioProfile, ...]
```

```python
# tests/release/contracts/loader.py
from __future__ import annotations

from pathlib import Path

from .models import LoadProfileResult, ProfileValidationError


def load_profile(path: str | Path) -> LoadProfileResult:
    raise ProfileValidationError(f"profile loader is not implemented for {path}")
```

```python
# tests/release/contracts/__init__.py
from .loader import load_profile
from .models import LoadProfileResult, ProfileValidationError

__all__ = ["LoadProfileResult", "ProfileValidationError", "load_profile"]
```

```python
# tests/release/__init__.py
"""Release certification test harness package."""
```

- [ ] **Step 4: Run the import test and confirm it passes**

Run: `python -m pytest tests/release/contracts/test_profiles.py::test_contracts_public_api_imports -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit the skeleton**

```bash
git add tests/release/__init__.py tests/release/contracts/__init__.py tests/release/contracts/models.py tests/release/contracts/loader.py tests/release/contracts/test_profiles.py
git commit -m "test: scaffold release profile contract package"
```

## Task 2: YAML Loading, Hashing, And Top-Level Schema

**Files:**
- Modify: `tests/release/contracts/loader.py`
- Create: `tests/release/contracts/schema.py`
- Modify: `tests/release/contracts/test_profiles.py`

- [ ] **Step 1: Add tests for valid loading and top-level rejection**

```python
from pathlib import Path

import pytest

from tests.release.contracts import ProfileValidationError, load_profile


def write_profile(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


VALID_PROFILE = """
profile_version: 1
name: lab-passive-release
hubs:
  primary:
    kubeconfig: /tmp/primary.kubeconfig
    context: primary
  secondary:
    kubeconfig: /tmp/secondary.kubeconfig
    context: secondary
managed_clusters:
  expected_count: 2
streams:
  - id: python
  - id: ansible
scenarios:
  - id: static-gates
  - id: lab-readiness
  - id: baseline-check
  - id: preflight
  - id: python-passive-switchover
  - id: ansible-passive-switchover
  - id: python-restore-only
  - id: ansible-restore-only
  - id: argocd-managed-switchover
  - id: runtime-parity
  - id: final-baseline-check
argocd:
  namespaces:
    - openshift-gitops
baseline:
  initial_primary: primary
limits: {}
recovery: {}
artifacts: {}
"""


def test_load_profile_returns_hash_and_normalized_model(tmp_path: Path) -> None:
    profile_path = write_profile(tmp_path / "profile.yaml", VALID_PROFILE)

    loaded = load_profile(profile_path)

    assert loaded.path == profile_path
    assert len(loaded.sha256) == 64
    assert loaded.profile.name == "lab-passive-release"
    assert loaded.profile.hubs["primary"].acm_namespace == "open-cluster-management"
    assert loaded.profile.managed_clusters.expected_count == 2


def test_load_profile_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    profile_path = write_profile(tmp_path / "profile.yaml", VALID_PROFILE + "\nunknown: true\n")

    with pytest.raises(ProfileValidationError, match="unknown top-level key.*unknown"):
        load_profile(profile_path)
```

- [ ] **Step 2: Run the tests and confirm loader behavior fails**

Run: `python -m pytest tests/release/contracts/test_profiles.py::test_load_profile_returns_hash_and_normalized_model tests/release/contracts/test_profiles.py::test_load_profile_rejects_unknown_top_level_key -q`

Expected: failures because `load_profile()` still raises the skeleton error.

- [ ] **Step 3: Implement YAML parsing, hashing, and top-level checks**

```python
# tests/release/contracts/schema.py
from __future__ import annotations

import re
from typing import Any, Mapping

from .models import ProfileValidationError

REQUIRED_TOP_LEVEL_KEYS = {
    "profile_version",
    "name",
    "hubs",
    "managed_clusters",
    "streams",
    "scenarios",
    "argocd",
    "baseline",
    "limits",
    "recovery",
    "artifacts",
}
OPTIONAL_TOP_LEVEL_KEYS = {"release"}
ALLOWED_TOP_LEVEL_KEYS = REQUIRED_TOP_LEVEL_KEYS | OPTIONAL_TOP_LEVEL_KEYS
PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def require_mapping(value: Any, field_path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ProfileValidationError(f"{field_path}: expected mapping, got {type(value).__name__}")
    return value


def validate_top_level(raw: Mapping[str, Any], profile_path: str) -> None:
    missing = sorted(REQUIRED_TOP_LEVEL_KEYS - raw.keys())
    if missing:
        raise ProfileValidationError(f"{profile_path}: missing required top-level keys: {', '.join(missing)}")
    unknown = sorted(raw.keys() - ALLOWED_TOP_LEVEL_KEYS)
    if unknown:
        raise ProfileValidationError(f"{profile_path}: unknown top-level key: {unknown[0]}")
    if raw["profile_version"] != 1:
        raise ProfileValidationError(f"{profile_path}: profile_version: expected 1, got {raw['profile_version']!r}")
    name = raw["name"]
    if not isinstance(name, str) or not PROFILE_NAME_RE.match(name):
        raise ProfileValidationError(f"{profile_path}: name: expected /^[A-Za-z0-9_.-]+$/")
```

```python
# tests/release/contracts/loader.py
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

import yaml

from .models import (
    HubProfile,
    LoadProfileResult,
    ManagedClustersProfile,
    ProfileValidationError,
    ReleaseProfile,
    ScenarioProfile,
    StreamProfile,
)
from .schema import require_mapping, validate_top_level


def _read_yaml(path: Path) -> tuple[Mapping[str, Any], str]:
    content = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content)
    if not isinstance(parsed, dict):
        raise ProfileValidationError(f"{path}: expected YAML mapping at document root")
    return parsed, hashlib.sha256(content.encode("utf-8")).hexdigest()


def _hub(raw: Mapping[str, Any]) -> HubProfile:
    return HubProfile(
        kubeconfig=str(raw["kubeconfig"]),
        context=str(raw["context"]),
        acm_namespace=str(raw.get("acm_namespace", "open-cluster-management")),
        role_label_selector=raw.get("role_label_selector"),
        timeout_minutes=raw.get("timeout_minutes"),
    )


def _managed_clusters(raw: Mapping[str, Any]) -> ManagedClustersProfile:
    return ManagedClustersProfile(
        expected_names=tuple(raw.get("expected_names") or ()),
        expected_count=raw.get("expected_count"),
        contexts=raw.get("contexts") or {},
        require_observability=bool(raw.get("require_observability", True)),
    )


def _stream(raw: Mapping[str, Any]) -> StreamProfile:
    stream_id = str(raw["id"])
    default_required = stream_id in {"python", "ansible"}
    return StreamProfile(
        id=stream_id,
        enabled=bool(raw.get("enabled", True)),
        required=raw.get("required", default_required),
        env=raw.get("env") or {},
        extra_args=tuple(raw.get("extra_args") or ()),
    )


def _scenario(raw: Mapping[str, Any]) -> ScenarioProfile:
    return ScenarioProfile(
        id=str(raw["id"]),
        required=raw.get("required"),
        streams=tuple(raw.get("streams") or ()),
        cycles=int(raw.get("cycles", 1)),
        timeout_minutes=raw.get("timeout_minutes"),
        skip_reason=raw.get("skip_reason"),
    )


def load_profile(path: str | Path) -> LoadProfileResult:
    profile_path = Path(path)
    raw, sha256 = _read_yaml(profile_path)
    validate_top_level(raw, str(profile_path))
    hubs_raw = require_mapping(raw["hubs"], "hubs")
    profile = ReleaseProfile(
        profile_version=1,
        name=str(raw["name"]),
        raw=raw,
        hubs={
            "primary": _hub(require_mapping(hubs_raw["primary"], "hubs.primary")),
            "secondary": _hub(require_mapping(hubs_raw["secondary"], "hubs.secondary")),
        },
        managed_clusters=_managed_clusters(require_mapping(raw["managed_clusters"], "managed_clusters")),
        streams=tuple(_stream(require_mapping(item, "streams[]")) for item in raw["streams"]),
        scenarios=tuple(_scenario(require_mapping(item, "scenarios[]")) for item in raw["scenarios"]),
    )
    return LoadProfileResult(path=profile_path, sha256=sha256, profile=profile)
```

- [ ] **Step 4: Run the loader tests**

Run: `python -m pytest tests/release/contracts/test_profiles.py::test_load_profile_returns_hash_and_normalized_model tests/release/contracts/test_profiles.py::test_load_profile_rejects_unknown_top_level_key -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit YAML loading**

```bash
git add tests/release/contracts/loader.py tests/release/contracts/schema.py tests/release/contracts/test_profiles.py
git commit -m "feat: load release validation profiles"
```

## Task 3: Field Validation And Defaults

**Files:**
- Modify: `tests/release/contracts/schema.py`
- Modify: `tests/release/contracts/loader.py`
- Modify: `tests/release/contracts/test_profiles.py`

- [ ] **Step 1: Add schema tests for required field rules**

```python
def test_managed_clusters_requires_names_or_count(tmp_path: Path) -> None:
    profile_path = write_profile(
        tmp_path / "profile.yaml",
        VALID_PROFILE.replace("managed_clusters:\n  expected_count: 2", "managed_clusters: {}"),
    )

    with pytest.raises(ProfileValidationError, match="managed_clusters: expected exactly one"):
        load_profile(profile_path)


def test_stream_id_must_be_known(tmp_path: Path) -> None:
    profile_path = write_profile(tmp_path / "profile.yaml", VALID_PROFILE.replace("- id: python", "- id: ruby"))

    with pytest.raises(ProfileValidationError, match="streams\\[0\\].id"):
        load_profile(profile_path)


def test_skip_reason_requires_optional_scenario(tmp_path: Path) -> None:
    profile_path = write_profile(
        tmp_path / "profile.yaml",
        VALID_PROFILE.replace("- id: preflight", "- id: preflight\n    skip_reason: local-only"),
    )

    with pytest.raises(ProfileValidationError, match="skip_reason"):
        load_profile(profile_path)
```

- [ ] **Step 2: Run the schema tests and confirm they fail**

Run: `python -m pytest tests/release/contracts/test_profiles.py::test_managed_clusters_requires_names_or_count tests/release/contracts/test_profiles.py::test_stream_id_must_be_known tests/release/contracts/test_profiles.py::test_skip_reason_requires_optional_scenario -q`

Expected: failures because these rules are not implemented.

- [ ] **Step 3: Add schema helper functions and call them from the loader**

```python
# tests/release/contracts/schema.py
KNOWN_STREAMS = {"bash", "python", "ansible"}
KNOWN_SCENARIOS = {
    "static-gates",
    "lab-readiness",
    "baseline-check",
    "preflight",
    "python-passive-switchover",
    "ansible-passive-switchover",
    "python-restore-only",
    "ansible-restore-only",
    "argocd-managed-switchover",
    "runtime-parity",
    "final-baseline-check",
    "full-restore",
    "checkpoint-resume",
    "decommission",
    "failure-injection",
    "soak",
}


def validate_managed_clusters(raw: Mapping[str, Any]) -> None:
    has_names = bool(raw.get("expected_names"))
    has_count = raw.get("expected_count") is not None
    if has_names == has_count:
        raise ProfileValidationError("managed_clusters: expected exactly one of expected_names or expected_count")
    if has_count and int(raw["expected_count"]) < 1:
        raise ProfileValidationError("managed_clusters.expected_count: expected integer >= 1")


def validate_stream(raw: Mapping[str, Any], index: int) -> None:
    stream_id = raw.get("id")
    if stream_id not in KNOWN_STREAMS:
        raise ProfileValidationError(f"streams[{index}].id: expected one of bash, python, ansible")


def validate_scenario(raw: Mapping[str, Any], index: int, enabled_streams: set[str]) -> None:
    scenario_id = raw.get("id")
    if scenario_id not in KNOWN_SCENARIOS:
        raise ProfileValidationError(f"scenarios[{index}].id: unknown scenario {scenario_id!r}")
    if raw.get("skip_reason") and raw.get("required", True):
        raise ProfileValidationError(f"scenarios[{index}].skip_reason: allowed only when required is false")
    narrowed = set(raw.get("streams") or ())
    if not narrowed.issubset(enabled_streams):
        unknown = sorted(narrowed - enabled_streams)
        raise ProfileValidationError(f"scenarios[{index}].streams: stream is not enabled: {unknown[0]}")
```

Update `load_profile()` so it calls:

```python
from .schema import (
    require_mapping,
    validate_managed_clusters,
    validate_scenario,
    validate_stream,
    validate_top_level,
)

managed_raw = require_mapping(raw["managed_clusters"], "managed_clusters")
validate_managed_clusters(managed_raw)
stream_items = [require_mapping(item, "streams[]") for item in raw["streams"]]
for index, item in enumerate(stream_items):
    validate_stream(item, index)
enabled_streams = {str(item["id"]) for item in stream_items if item.get("enabled", True)}
scenario_items = [require_mapping(item, "scenarios[]") for item in raw["scenarios"]]
for index, item in enumerate(scenario_items):
    validate_scenario(item, index, enabled_streams)
```

Then build streams from `stream_items`, scenarios from `scenario_items`, and managed clusters from `managed_raw`.

- [ ] **Step 4: Run the schema tests**

Run: `python -m pytest tests/release/contracts/test_profiles.py::test_managed_clusters_requires_names_or_count tests/release/contracts/test_profiles.py::test_stream_id_must_be_known tests/release/contracts/test_profiles.py::test_skip_reason_requires_optional_scenario -q`

Expected: `3 passed`.

- [ ] **Step 5: Commit validation rules**

```bash
git add tests/release/contracts/schema.py tests/release/contracts/loader.py tests/release/contracts/test_profiles.py
git commit -m "feat: validate release profile schema fields"
```

## Task 4: Credential Content Scanning

**Files:**
- Modify: `tests/release/contracts/schema.py`
- Modify: `tests/release/contracts/loader.py`
- Modify: `tests/release/contracts/test_profiles.py`

- [ ] **Step 1: Add credential rejection tests**

```python
def test_profile_rejects_embedded_token(tmp_path: Path) -> None:
    profile_path = write_profile(
        tmp_path / "profile.yaml",
        VALID_PROFILE.replace("context: primary", "context: primary\n    token: sha256~secret"),
    )

    with pytest.raises(ProfileValidationError, match="hubs.primary.token.*token"):
        load_profile(profile_path)


def test_profile_rejects_pem_material(tmp_path: Path) -> None:
    profile_path = write_profile(
        tmp_path / "profile.yaml",
        VALID_PROFILE.replace("name: lab-passive-release", "name: lab-passive-release\nrelease:\n  expected_version: '-----BEGIN CERTIFICATE-----'"),
    )

    with pytest.raises(ProfileValidationError, match="release.expected_version.*pem"):
        load_profile(profile_path)
```

- [ ] **Step 2: Run the credential tests and confirm they fail**

Run: `python -m pytest tests/release/contracts/test_profiles.py::test_profile_rejects_embedded_token tests/release/contracts/test_profiles.py::test_profile_rejects_pem_material -q`

Expected: failures because content scanning has not been added.

- [ ] **Step 3: Implement recursive profile content scanning**

```python
# tests/release/contracts/schema.py
CREDENTIAL_KEYWORDS = {
    "token": "token",
    "client-key-data": "kubeconfig-key",
    "client-certificate-data": "kubeconfig-certificate",
    "certificate-authority-data": "kubeconfig-ca",
    "access_key": "cloud-access-key",
    "secret_key": "cloud-secret-key",
    "session_token": "cloud-session-token",
}
PEM_MARKERS = {
    "-----BEGIN CERTIFICATE-----": "pem-certificate",
    "-----BEGIN PRIVATE KEY-----": "pem-private-key",
    "-----BEGIN RSA PRIVATE KEY-----": "pem-private-key",
}


def validate_profile_contents(value: Any, profile_path: str, field_path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{field_path}.{key}" if field_path else str(key)
            lowered_key = str(key).lower()
            if lowered_key in CREDENTIAL_KEYWORDS:
                raise ProfileValidationError(
                    f"{profile_path}: {child_path}: matched credential class {CREDENTIAL_KEYWORDS[lowered_key]}"
                )
            validate_profile_contents(child, profile_path, child_path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            validate_profile_contents(child, profile_path, f"{field_path}[{index}]")
        return
    if isinstance(value, str):
        for marker, credential_class in PEM_MARKERS.items():
            if marker in value:
                raise ProfileValidationError(f"{profile_path}: {field_path}: matched credential class {credential_class}")
```

Call `validate_profile_contents(raw, str(profile_path))` immediately after `validate_top_level()`.

- [ ] **Step 4: Run the credential tests**

Run: `python -m pytest tests/release/contracts/test_profiles.py::test_profile_rejects_embedded_token tests/release/contracts/test_profiles.py::test_profile_rejects_pem_material -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit content scanning**

```bash
git add tests/release/contracts/schema.py tests/release/contracts/loader.py tests/release/contracts/test_profiles.py
git commit -m "feat: reject secret material in release profiles"
```

## Task 5: Example Profiles And Local Profile Ignore

**Files:**
- Create: `tests/release/profiles/full-release.example.yaml`
- Create: `tests/release/profiles/argocd-release.example.yaml`
- Create: `tests/release/profiles/dev-minimal.example.yaml`
- Modify: `.gitignore`
- Modify: `tests/release/contracts/test_profiles.py`

- [ ] **Step 1: Add tests that checked-in examples load**

```python
import pytest


@pytest.mark.parametrize(
    "profile_name",
    [
        "full-release.example.yaml",
        "argocd-release.example.yaml",
        "dev-minimal.example.yaml",
    ],
)
def test_checked_in_example_profiles_load(profile_name: str) -> None:
    loaded = load_profile(Path("tests/release/profiles") / profile_name)

    assert loaded.profile.profile_version == 1
    assert loaded.profile.name
    assert loaded.sha256
```

- [ ] **Step 2: Run the example test and confirm it fails for missing files**

Run: `python -m pytest tests/release/contracts/test_profiles.py::test_checked_in_example_profiles_load -q`

Expected: failure with `FileNotFoundError`.

- [ ] **Step 3: Add sanitized example profiles**

Use the source spec minimal profile as the base. Keep kubeconfig paths under `/path/to/...`, contexts like `lab-primary-context`, and no credential-like keys. `full-release.example.yaml` must include Python and Ansible as required, Bash with an explicit `required: false`, all required scenarios, `argocd.namespaces: [openshift-gitops]`, `baseline.initial_primary: primary`, and `artifacts.redaction.required: true`.

Create `argocd-release.example.yaml` with the same structure but only the required matrix and an `argocd.application_selectors` entry such as:

```yaml
argocd:
  mandatory: true
  namespaces:
    - openshift-gitops
  application_selectors:
    - match_labels:
        app.kubernetes.io/part-of: acm-switchover-fixture
```

Create `dev-minimal.example.yaml` with `release` omitted, `streams` limited to Python and Ansible, `baseline.static_gates.required: false`, `argocd.mandatory: false`, and only `static-gates`, `lab-readiness`, `baseline-check`, `preflight`, and `final-baseline-check`.

- [ ] **Step 4: Ignore local lab profiles**

Add this line to `.gitignore` if it is absent:

```gitignore
tests/release/profiles/local/
```

- [ ] **Step 5: Run the example profile tests**

Run: `python -m pytest tests/release/contracts/test_profiles.py::test_checked_in_example_profiles_load -q`

Expected: `3 passed`.

- [ ] **Step 6: Commit examples**

```bash
git add .gitignore tests/release/profiles/full-release.example.yaml tests/release/profiles/argocd-release.example.yaml tests/release/profiles/dev-minimal.example.yaml tests/release/contracts/test_profiles.py
git commit -m "docs: add release validation example profiles"
```

## Final Verification

- [ ] Run the complete profile contract suite:

Run: `python -m pytest tests/release/contracts -q`

Expected: all tests pass.

- [ ] Run the normal test collection guard after Plan 02 exists:

Run: `python -m pytest tests/release -q`

Expected: release lifecycle tests are skipped without a release profile, and contract unit tests pass.

- [ ] Run the planning placeholder scan:

Run:

```bash
python - <<'PY'
from pathlib import Path
bad = ["TB" + "D", "TO" + "DO", "implement " + "later", "fill " + "in details", "handle " + "edge cases", "appropriate " + "error handling", "Similar " + "to Task"]
hits = []
for path in sorted(Path("docs/superpowers/plans").glob("*.md")):
    text = path.read_text(encoding="utf-8")
    for phrase in bad:
        if phrase in text:
            hits.append(f"{path}: contains rejected planning phrase {phrase!r}")
if hits:
    raise SystemExit("\n".join(hits))
PY
```

Expected: no matches.

- [ ] Update `docs/superpowers/plans/2026-04-27-release-validation-progress.md` with command output and status.
