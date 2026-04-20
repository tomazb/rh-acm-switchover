"""Tests to verify ArgoCD role tasks use parameterized hub access."""

import pathlib

import yaml

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
    assert run_id is None or (
        isinstance(run_id, str) and run_id != ""
    ), "run_id defaults to empty string, which bypasses Jinja default() filter"


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
        "discover.yml still hardcodes default('argocd'); " "should use default(omit) for cluster-wide discovery"
    )
    tasks = _load_yaml("discover.yml")
    for task in tasks:
        for block_task in task.get("block", []):
            k8s_info = block_task.get("kubernetes.core.k8s_info", {})
            if k8s_info:
                ns = str(k8s_info.get("namespace", ""))
                assert "default(omit)" in ns, f"discover.yml namespace should use default(omit), got: {ns}"


ROLES_DIR = ROLE_DIR.parents[1]


def _load_role_yaml(role_name: str, task_name: str) -> list[dict]:
    return yaml.safe_load((ROLES_DIR / role_name / "tasks" / task_name).read_text())


def test_primary_prep_pauses_both_hubs():
    """primary_prep/main.yml should include argocd_manage for both primary and secondary hubs."""
    text = (ROLES_DIR / "primary_prep" / "tasks" / "main.yml").read_text()
    assert (
        text.count("argocd_manage") >= 2
    ), "primary_prep should include argocd_manage role at least twice (primary + secondary)"
    assert "_argocd_discover_hub: primary" in text, "Should pause primary hub"
    assert "_argocd_discover_hub: secondary" in text, "Should pause secondary hub"


def test_finalization_does_not_auto_resume():
    """finalization/main.yml should NOT auto-resume argocd (removed feature)."""
    text = (ROLES_DIR / "finalization" / "tasks" / "main.yml").read_text()
    resume_count = text.count("acm_switchover_argocd_mode_override: resume")
    assert resume_count == 0, f"finalization should not auto-resume argocd, found {resume_count} resume include(s)"


PLAYBOOKS_DIR = pathlib.Path(__file__).resolve().parents[2] / "playbooks"


def test_standalone_argocd_resume_covers_both_hubs():
    """argocd_resume.yml must resume on both secondary and primary hubs.

    primary_prep pauses both hubs, so the standalone resume recovery playbook
    must mirror that by resuming both. Primary resume should be guarded by
    acm_switchover_hubs.primary is defined.
    """
    text = (PLAYBOOKS_DIR / "argocd_resume.yml").read_text()
    resume_count = text.count("acm_switchover_argocd_mode_override: resume")
    assert resume_count >= 2, f"argocd_resume.yml should resume on both hubs, found {resume_count} resume block(s)"
    assert "_argocd_discover_hub: secondary" in text, "Should resume secondary hub"
    assert "_argocd_discover_hub: primary" in text, "Should resume primary hub"
    assert (
        "acm_switchover_hubs.primary is defined" in text
    ), "Primary hub resume should be guarded by acm_switchover_hubs.primary is defined"


def test_standalone_argocd_resume_restores_run_id_from_checkpoint():
    """argocd_resume.yml must seed run_id from checkpoint before resuming.

    The pause run_id is persisted in checkpoint operational_data during
    switchover/restore-only runs. Standalone resume must reload that value so
    resume.yml can match the paused-by annotation without requiring operators
    to pass run_id manually.
    """
    text = (PLAYBOOKS_DIR / "argocd_resume.yml").read_text()

    assert "acm_switchover_execution" in text and "checkpoint" in text, (
        "argocd_resume.yml must inspect the configured checkpoint path"
    )
    assert "lookup(" in text and "'file'" in text and "from_json" in text, (
        "argocd_resume.yml must load checkpoint JSON before including argocd_manage"
    )
    assert "operational_data" in text and "argocd_run_id" in text, (
        "argocd_resume.yml must read operational_data.argocd_run_id from the checkpoint"
    )
    assert "combine({" in text and "'run_id':" in text, (
        "argocd_resume.yml must seed acm_switchover_argocd.run_id from the persisted checkpoint"
    )
    assert "(acm_switchover_argocd.run_id | default('')) | length == 0" in text, (
        "argocd_resume.yml must not overwrite an explicit run_id supplied by the operator"
    )


def test_standalone_argocd_resume_guards_checkpoint_load_by_enabled_flag():
    """argocd_resume.yml must not load a stale checkpoint when checkpointing is disabled.

    A checkpoint file may exist from a previous run at the configured path even
    when checkpoint.enabled is false for the current run (no fresh write happened).
    Loading it would seed a wrong run_id. All three checkpoint pre_tasks must
    require checkpoint.enabled before touching the file.
    """
    text = (PLAYBOOKS_DIR / "argocd_resume.yml").read_text()
    enabled_guard = "acm_switchover_execution.checkpoint.enabled | default(false)"
    # Count occurrences — one per pre_task (stat, load, seed)
    count = text.count(enabled_guard)
    assert count >= 3, (
        f"argocd_resume.yml must guard all three checkpoint pre_tasks with "
        f"'{enabled_guard}', found {count} occurrence(s)"
    )


def test_discover_run_id_gated_by_resume_mode():
    """discover.yml must NOT generate run_id when mode is resume.

    When the argocd_manage role runs in resume mode without an explicit run_id,
    _argocd_expected_run_id should resolve to '' so resume.yml's safety
    fallback ('resume ALL paused apps') fires. Generating a fresh run_id
    defeats this fallback because the new UUID never matches any annotation.
    """
    tasks = _load_yaml("discover.yml")

    # Find the "Generate run_id" set_fact task
    for task in tasks:
        for block_task in task.get("block", []):
            sf = block_task.get("ansible.builtin.set_fact")
            if sf and "run_id" in str(sf):
                when = block_task.get("when", [])
                if isinstance(when, str):
                    when = [when]
                when_text = " ".join(str(w) for w in when)
                assert "resume" in when_text, (
                    "discover.yml run_id generation must be gated to exclude resume mode. "
                    f"Current when: {when}"
                )
                return

    raise AssertionError("discover.yml: Could not find 'Generate run_id' set_fact task")
