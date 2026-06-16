from pathlib import Path

import pytest

from tests.release.contracts import (
    LoadProfileResult,
    ProfileValidationError,
    load_profile,
)
from tests.release.scenarios.catalog import SCENARIOS_BY_ID


def test_contracts_public_api_imports() -> None:
    assert callable(load_profile)
    assert ProfileValidationError.__name__ == "ProfileValidationError"
    assert LoadProfileResult.__name__ == "LoadProfileResult"
    assert Path("tests/release/contracts").exists()


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
    assert loaded.profile.hubs["primary"].timeout_minutes == 120
    assert loaded.profile.managed_clusters.expected_count == 2
    assert loaded.profile.managed_clusters.require_observability is True
    assert loaded.profile.argocd.mandatory is True
    assert loaded.profile.limits.default_timeout_minutes == 120
    assert loaded.profile.artifacts.redaction.required is True


def test_load_profile_parses_live_rbac_certification_scope(tmp_path: Path) -> None:
    profile_text = VALID_PROFILE.replace(
        "- id: final-baseline-check",
        """- id: rbac-bootstrap-live
    required: true
    rbac_certification:
      primary:
        role: operator
        namespace: acm-switchover-custom
        service_account: custom-operator
        include_decommission: true
        include_old_hub_finalization: true
      secondary:
        role: validator
        include_decommission: false
        include_old_hub_finalization: false
  - id: final-baseline-check""",
    )
    profile_path = write_profile(tmp_path / "profile.yaml", profile_text)

    loaded = load_profile(profile_path)
    scenario = next(item for item in loaded.profile.scenarios if item.id == "rbac-bootstrap-live")

    assert scenario.rbac_certification is not None
    assert scenario.rbac_certification.primary.role == "operator"
    assert scenario.rbac_certification.primary.namespace == "acm-switchover-custom"
    assert scenario.rbac_certification.primary.service_account == "custom-operator"
    assert scenario.rbac_certification.primary.include_decommission is True
    assert scenario.rbac_certification.primary.include_old_hub_finalization is True
    assert scenario.rbac_certification.secondary.role == "validator"
    assert scenario.rbac_certification.secondary.include_decommission is False


def test_load_profile_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    profile_path = write_profile(tmp_path / "profile.yaml", VALID_PROFILE + "\nunknown: true\n")

    with pytest.raises(ProfileValidationError, match="unknown top-level key.*unknown"):
        load_profile(profile_path)


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
        VALID_PROFILE.replace(
            "name: lab-passive-release",
            "name: lab-passive-release\nrelease:\n  expected_version: '-----BEGIN CERTIFICATE-----'",
        ),
    )

    with pytest.raises(ProfileValidationError, match="release.expected_version.*pem"):
        load_profile(profile_path)


@pytest.mark.parametrize(
    "profile_name",
    [
        "full-release.example.yaml",
        "full-release-with-rbac-cert.example.yaml",
        "argocd-release.example.yaml",
        "dev-minimal.example.yaml",
    ],
)
def test_checked_in_example_profiles_load(profile_name: str) -> None:
    loaded = load_profile(Path("tests/release/profiles") / profile_name)

    assert loaded.profile.profile_version == 1
    assert loaded.profile.name
    assert loaded.sha256


@pytest.mark.parametrize(
    "profile_name",
    [
        "full-release.example.yaml",
        "full-release-with-rbac-cert.example.yaml",
        "argocd-release.example.yaml",
    ],
)
def test_checked_in_mutating_profiles_declare_secondary_final_primary(profile_name: str) -> None:
    loaded = load_profile(Path("tests/release/profiles") / profile_name)
    mutating_scenarios = [
        scenario.id for scenario in loaded.profile.scenarios if SCENARIOS_BY_ID[scenario.id].mutates_lab
    ]

    assert mutating_scenarios
    assert loaded.profile.baseline.final_primary == "secondary"
