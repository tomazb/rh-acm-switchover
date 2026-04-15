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


def test_pause_has_clobber_guard():
    """pause.yml k8s patch task should skip apps already paused (have our annotation)."""
    tasks = _load_yaml("pause.yml")
    for task in tasks:
        for block_task in task.get("block", []):
            k8s = block_task.get("kubernetes.core.k8s", {})
            if k8s:
                when = block_task.get("when", [])
                if isinstance(when, str):
                    when = [when]
                when_text = " ".join(str(w) for w in when)
                assert "paused-by" in when_text, (
                    "pause.yml k8s patch task should check for existing paused-by "
                    f"annotation in when condition to prevent clobber. Current when: {when}"
                )


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


def test_discover_namespace_defaults_to_omit():
    """discover.yml must NOT hardcode a single default namespace like 'argocd'.

    When acm_switchover_argocd.namespace is not set, discovery should search
    cluster-wide (default(omit)) to match Bash/Python behavior.
    """
    text = (ROLE_DIR / "discover.yml").read_text()
    assert "default('argocd')" not in text, (
        "discover.yml still hardcodes default('argocd'); "
        "should use default(omit) for cluster-wide discovery"
    )
    tasks = _load_yaml("discover.yml")
    for task in tasks:
        for block_task in task.get("block", []):
            k8s_info = block_task.get("kubernetes.core.k8s_info", {})
            if k8s_info:
                ns = str(k8s_info.get("namespace", ""))
                assert "default(omit)" in ns, (
                    f"discover.yml namespace should use default(omit), got: {ns}"
                )


ROLES_DIR = ROLE_DIR.parents[1]


def _load_role_yaml(role_name: str, task_name: str) -> list[dict]:
    return yaml.safe_load((ROLES_DIR / role_name / "tasks" / task_name).read_text())


def test_primary_prep_pauses_both_hubs():
    """primary_prep/main.yml should include argocd_manage for both primary and secondary hubs."""
    text = (ROLES_DIR / "primary_prep" / "tasks" / "main.yml").read_text()
    assert text.count("argocd_manage") >= 2, \
        "primary_prep should include argocd_manage role at least twice (primary + secondary)"
    assert "_argocd_discover_hub: primary" in text, "Should pause primary hub"
    assert "_argocd_discover_hub: secondary" in text, "Should pause secondary hub"


def test_finalization_resumes_both_hubs():
    """finalization/main.yml should resume argocd on both primary and secondary hubs."""
    text = (ROLES_DIR / "finalization" / "tasks" / "main.yml").read_text()
    resume_count = text.count("acm_switchover_argocd_mode_override: resume")
    assert resume_count >= 2, \
        f"finalization should resume argocd on both hubs, found {resume_count} resume include(s)"
    assert "_argocd_discover_hub: secondary" in text, "Should resume secondary hub"
    assert "_argocd_discover_hub: primary" in text, "Should resume primary hub"


PLAYBOOKS_DIR = pathlib.Path(__file__).resolve().parents[2] / "playbooks"


def test_standalone_argocd_resume_covers_both_hubs():
    """argocd_resume.yml must resume on both secondary and primary hubs.

    primary_prep pauses both hubs, so the standalone resume recovery playbook
    must mirror that by resuming both. Primary resume should be guarded by
    acm_switchover_hubs.primary is defined.
    """
    text = (PLAYBOOKS_DIR / "argocd_resume.yml").read_text()
    resume_count = text.count("acm_switchover_argocd_mode_override: resume")
    assert resume_count >= 2, (
        f"argocd_resume.yml should resume on both hubs, found {resume_count} resume block(s)"
    )
    assert "_argocd_discover_hub: secondary" in text, "Should resume secondary hub"
    assert "_argocd_discover_hub: primary" in text, "Should resume primary hub"
    assert "acm_switchover_hubs.primary is defined" in text, (
        "Primary hub resume should be guarded by acm_switchover_hubs.primary is defined"
    )
