"""Tests to verify ArgoCD role tasks use parameterized hub access."""

import yaml
import pathlib

ROLE_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles" / "argocd_manage" / "tasks"


def _load_yaml(name: str) -> list[dict]:
    return yaml.safe_load((ROLE_DIR / name).read_text())


def test_pause_uses_parameterized_hub():
    """pause.yml must NOT hardcode .primary or .secondary for kubeconfig/context."""
    tasks = _load_yaml("pause.yml")
    for task in tasks:
        k8s = task.get("kubernetes.core.k8s", {})
        if not k8s:
            for block_task in task.get("block", []):
                k8s = block_task.get("kubernetes.core.k8s", {})
                if k8s:
                    kc = str(k8s.get("kubeconfig", ""))
                    ctx = str(k8s.get("context", ""))
                    assert ".primary." not in kc, f"pause.yml hardcodes .primary in kubeconfig: {kc}"
                    assert ".primary." not in ctx, f"pause.yml hardcodes .primary in context: {ctx}"
                    assert ".secondary." not in kc, f"pause.yml hardcodes .secondary in kubeconfig: {kc}"
                    assert ".secondary." not in ctx, f"pause.yml hardcodes .secondary in context: {ctx}"
                    assert "_argocd_discover_hub" in kc, f"pause.yml kubeconfig should use _argocd_discover_hub: {kc}"


def test_resume_uses_parameterized_hub():
    """resume.yml must NOT hardcode .primary or .secondary for kubeconfig/context."""
    tasks = _load_yaml("resume.yml")
    for task in tasks:
        for block_task in task.get("block", []):
            k8s = block_task.get("kubernetes.core.k8s", {})
            if k8s:
                kc = str(k8s.get("kubeconfig", ""))
                ctx = str(k8s.get("context", ""))
                assert ".primary." not in kc, f"resume.yml hardcodes .primary in kubeconfig: {kc}"
                assert ".primary." not in ctx, f"resume.yml hardcodes .primary in context: {ctx}"
                assert ".secondary." not in kc, f"resume.yml hardcodes .secondary in kubeconfig: {kc}"
                assert ".secondary." not in ctx, f"resume.yml hardcodes .secondary in context: {ctx}"
                assert "_argocd_discover_hub" in kc, f"resume.yml kubeconfig should use _argocd_discover_hub: {kc}"


def test_run_id_default_is_not_empty_string():
    """defaults/main.yml run_id must not default to empty string."""
    defaults = yaml.safe_load((ROLE_DIR.parent / "defaults" / "main.yml").read_text())
    run_id = defaults.get("acm_switchover_argocd", {}).get("run_id")
    # run_id should either be absent (undefined → triggers Jinja default())
    # or be a non-empty string. Empty string breaks resume matching.
    assert run_id is None or (isinstance(run_id, str) and run_id != ""), \
        "run_id defaults to empty string, which bypasses Jinja default() filter"


def test_discover_uses_parameterized_hub():
    """discover.yml should already use _argocd_discover_hub (baseline check)."""
    tasks = _load_yaml("discover.yml")
    found = False
    for task in tasks:
        for block_task in task.get("block", []):
            k8s_info = block_task.get("kubernetes.core.k8s_info", {})
            if k8s_info:
                kc = str(k8s_info.get("kubeconfig", ""))
                assert "_argocd_discover_hub" in kc
                found = True
    assert found, "discover.yml should have at least one k8s_info task with _argocd_discover_hub"
