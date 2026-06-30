from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

FIXTURE_ROOT = Path(__file__).resolve().parent / "kustomize"

REQUIRED_KUSTOMIZATION_DIRS = (
    "bases/hub-common",
    "bases/managed-common",
    "bases/switchover-rbac",
    "bases/acm-dr-objects",
    "bases/argocd-acm-ownership",
    "bases/argocd-acm-safe-mode",
    "bases/argocd-acm-hostile-mode",
    "overlays/hubs/hub-a-primary",
    "overlays/hubs/hub-a-secondary",
    "overlays/hubs/hub-b-primary",
    "overlays/hubs/hub-b-secondary",
    "overlays/managed/mc-1",
    "overlays/managed/mc-2",
    "overlays/managed/mc-3",
    "overlays/scenarios/gitops-observe-only",
    "overlays/scenarios/gitops-owns-acm-autosync-off",
    "overlays/scenarios/gitops-owns-acm-selfheal-on",
    "overlays/scenarios/gitops-owns-acm-prune-on",
    "overlays/scenarios/gitops-owns-acm-appset-child",
    "overlays/scenarios/gitops-pause-required-before-switchover",
)
HOSTILE_SCENARIO_DIRS = (
    "overlays/scenarios/gitops-owns-acm-selfheal-on",
    "overlays/scenarios/gitops-owns-acm-prune-on",
    "overlays/scenarios/gitops-pause-required-before-switchover",
)
ALLOWED_HOSTILE_BASE_DIR = "bases/argocd-acm-hostile-mode"
REQUIRED_LAB_LABELS = {
    "acm-switchover.redhat-lab/topology",
}
SECRET_MARKER_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"kind:\s*Secret\b",
        r"\bstringData\s*:",
        r"\btoken\s*[:=]",
        r"\bpassword\s*[:=]",
        r"\bcredential\s*[:=]",
        r"\baws_access_key_id\b",
        r"\baws_secret_access_key\b",
        r"\bbootstrap-hub-kubeconfig\b",
        r"\bimport\.yaml\b",
        r"\bclient-key-data\b",
        r"\bclient-certificate-data\b",
        r"\bcertificate-authority-data\b",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"~/.kube/",
        r"/home/[^/\s]+/\.kube/",
        r"/tmp/[^/\s]*(?:kubeconfig|kube-config)",
        r"https?://(?![^/\s]*\.example\.invalid\b)",
    )
)


def _fixture_files() -> tuple[Path, ...]:
    assert FIXTURE_ROOT.exists(), "release-lab Kustomize fixture tree is missing"
    return tuple(sorted(path for path in FIXTURE_ROOT.rglob("*") if path.is_file()))


def _yaml_files() -> tuple[Path, ...]:
    return tuple(path for path in _fixture_files() if path.suffix in {".yaml", ".yml"})


def _docs_from(path: Path) -> tuple[dict[str, Any], ...]:
    docs = []
    for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
        if doc:
            assert isinstance(doc, dict), f"{path} contains a non-mapping YAML document"
            docs.append(doc)
    return tuple(docs)


def _resource_docs() -> tuple[tuple[Path, dict[str, Any]], ...]:
    resources: list[tuple[Path, dict[str, Any]]] = []
    for path in _yaml_files():
        for doc in _docs_from(path):
            if doc.get("kind") != "Kustomization":
                resources.append((path, doc))
    return tuple(resources)


def _automated_policy_is_hostile(policy: Any) -> bool:
    if not isinstance(policy, dict):
        return False
    return policy.get("prune") is True or policy.get("selfHeal") is True


def _has_hostile_sync_policy(doc: dict[str, Any]) -> bool:
    if doc.get("kind") == "Application":
        return _automated_policy_is_hostile(doc.get("spec", {}).get("syncPolicy", {}).get("automated"))
    if doc.get("kind") == "ApplicationSet":
        template = doc.get("spec", {}).get("template", {})
        return _automated_policy_is_hostile(template.get("spec", {}).get("syncPolicy", {}).get("automated"))
    return False


def test_automated_policy_treats_prune_and_selfheal_as_hostile_without_enabled_semantics() -> None:
    assert _automated_policy_is_hostile({"enabled": False, "prune": True})
    assert _automated_policy_is_hostile({"enabled": False, "selfHeal": True})
    assert not _automated_policy_is_hostile({"enabled": False})


def test_autosync_off_fixture_omits_automated_sync_for_gitops_compatibility() -> None:
    path = FIXTURE_ROOT / "bases" / "argocd-acm-safe-mode" / "autosync-off" / "autosync-off-application.yaml"
    docs = _docs_from(path)
    assert len(docs) == 1
    sync_policy = docs[0].get("spec", {}).get("syncPolicy", {})
    assert "automated" not in sync_policy


def _kustomization_resource_paths(kustomization_dir: Path, seen: set[Path] | None = None) -> tuple[Path, ...]:
    seen = seen or set()
    kustomization = (kustomization_dir / "kustomization.yaml").resolve()
    assert kustomization.exists(), f"{kustomization_dir.relative_to(FIXTURE_ROOT)} is missing kustomization.yaml"
    if kustomization in seen:
        return ()
    seen.add(kustomization)

    raw = yaml.safe_load(kustomization.read_text(encoding="utf-8")) or {}
    resources = raw.get("resources", ())
    assert isinstance(resources, list), f"{kustomization} resources must be a list"

    resolved: list[Path] = []
    for resource in resources:
        resource_path = (kustomization_dir / resource).resolve()
        assert resource_path.is_relative_to(FIXTURE_ROOT.resolve()), f"{resource} escapes the fixture tree"
        assert resource_path.exists(), f"{kustomization} references missing resource {resource}"
        if resource_path.is_dir():
            resolved.extend(_kustomization_resource_paths(resource_path, seen))
        else:
            resolved.append(resource_path)
    return tuple(resolved)


def test_release_lab_kustomize_tree_has_expected_overlays() -> None:
    assert (FIXTURE_ROOT / "README.md").exists()
    assert (FIXTURE_ROOT / "local" / ".gitkeep").exists()
    for relative_dir in REQUIRED_KUSTOMIZATION_DIRS:
        assert (FIXTURE_ROOT / relative_dir / "kustomization.yaml").exists(), relative_dir


def test_release_lab_kustomize_resources_resolve_static_build_inputs() -> None:
    for relative_dir in REQUIRED_KUSTOMIZATION_DIRS:
        resources = _kustomization_resource_paths(FIXTURE_ROOT / relative_dir)
        assert resources, f"{relative_dir} should resolve at least one static resource"


def test_release_lab_fixture_files_do_not_commit_secret_material_or_private_references() -> None:
    for path in _fixture_files():
        if path.name == ".gitkeep":
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in SECRET_MARKER_PATTERNS:
            assert not pattern.search(
                text
            ), f"{path.relative_to(FIXTURE_ROOT)} contains unsafe marker {pattern.pattern}"


def test_release_lab_fixtures_do_not_default_to_enforce_or_decommission() -> None:
    for path, doc in _resource_docs():
        assert doc.get("spec", {}).get("remediationAction") != "enforce", path

    for relative_dir in REQUIRED_KUSTOMIZATION_DIRS:
        for resource in _kustomization_resource_paths(FIXTURE_ROOT / relative_dir):
            assert "extensions/decommission" not in resource.as_posix()


def test_hostile_gitops_modes_are_isolated_to_explicit_hostile_fixtures() -> None:
    allowed_prefixes = HOSTILE_SCENARIO_DIRS + (ALLOWED_HOSTILE_BASE_DIR,)
    for path, doc in _resource_docs():
        labels = doc.get("metadata", {}).get("labels", {})
        mode = labels.get("acm-switchover.redhat-lab/gitops-mode", "")
        if not (str(mode).startswith("hostile-") or _has_hostile_sync_policy(doc)):
            continue
        relative = path.relative_to(FIXTURE_ROOT).as_posix()
        assert any(relative.startswith(prefix) for prefix in allowed_prefixes), relative


def test_applicationset_child_fixture_is_present_and_documented() -> None:
    appset_dir = FIXTURE_ROOT / "overlays/scenarios/gitops-owns-acm-appset-child"
    docs = [doc for path, doc in _resource_docs() if appset_dir in path.parents]
    assert any(doc.get("kind") == "ApplicationSet" for doc in docs)
    assert any(
        doc.get("kind") == "Application"
        and any(ref.get("kind") == "ApplicationSet" for ref in doc.get("metadata", {}).get("ownerReferences", ()))
        for doc in docs
    )

    readme = (FIXTURE_ROOT / "README.md").read_text(encoding="utf-8")
    assert "ApplicationSet child Application" in readme


def test_fixture_resources_carry_required_lab_labels() -> None:
    for path, doc in _resource_docs():
        labels = doc.get("metadata", {}).get("labels", {})
        missing = REQUIRED_LAB_LABELS - set(labels)
        assert not missing, f"{path.relative_to(FIXTURE_ROOT)} missing labels: {sorted(missing)}"
        assert labels["acm-switchover.redhat-lab/topology"] == "2hub-3mc-sno"


def test_release_lab_readme_documents_static_non_live_boundary() -> None:
    readme = (FIXTURE_ROOT / "README.md").read_text(encoding="utf-8")
    required_phrases = (
        "not live ACM certification evidence",
        "server-side live validation is Phase 9 work",
        "Phase 8P/8Q",
        "2 hubs and 3 managed SNO clusters",
    )
    for phrase in required_phrases:
        assert phrase in readme
