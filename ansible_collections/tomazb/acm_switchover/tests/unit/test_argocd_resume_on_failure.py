"""Tests to verify ArgoCD resume-on-failure rescue blocks in playbooks."""

import pathlib

import yaml

PLAYBOOK_DIR = pathlib.Path(__file__).resolve().parents[2] / "playbooks"
DEFAULTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles" / "argocd_manage" / "defaults"
ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"


def _load_playbook(name: str) -> list[dict]:
    return yaml.safe_load((PLAYBOOK_DIR / name).read_text())


def _get_task_block(playbook: list[dict]) -> dict:
    """Return the first tasks-level block/rescue/always structure."""
    for play in playbook:
        for task in play.get("tasks", []):
            if "block" in task:
                return task
    raise AssertionError("No block found in playbook")


# --- defaults ---


def test_resume_on_failure_default_is_false():
    """resume_on_failure must default to false in argocd_manage defaults."""
    defaults = yaml.safe_load((DEFAULTS_DIR / "main.yml").read_text())
    resume = defaults.get("acm_switchover_features", {}).get("argocd", {}).get("resume_on_failure")
    assert resume is False, f"Expected resume_on_failure=false, got {resume!r}"


# --- switchover.yml ---


def test_switchover_has_rescue_block():
    """switchover.yml must have a rescue block."""
    playbook = _load_playbook("switchover.yml")
    task_block = _get_task_block(playbook)
    assert "rescue" in task_block, "switchover.yml main block must have a rescue section"


def test_switchover_rescue_resumes_argocd_on_secondary():
    """switchover.yml rescue must attempt ArgoCD resume on secondary hub."""
    playbook = _load_playbook("switchover.yml")
    task_block = _get_task_block(playbook)
    rescue_tasks = task_block["rescue"]

    resume_tasks = [
        t
        for t in rescue_tasks
        if t.get("ansible.builtin.include_role", {}).get("name", "") == "tomazb.acm_switchover.argocd_manage"
    ]
    assert len(resume_tasks) >= 1, "rescue must include argocd_manage role for resume"

    # Check secondary hub resume task
    secondary_task = resume_tasks[0]
    vars_ = secondary_task.get("vars", {})
    assert (
        vars_.get("acm_switchover_argocd_mode_override") == "resume"
    ), "Secondary resume task must set mode_override to 'resume'"
    assert vars_.get("_argocd_discover_hub") == "secondary", "First resume task must target secondary hub"


def test_switchover_rescue_resumes_argocd_on_primary():
    """switchover.yml rescue must attempt ArgoCD resume on primary hub."""
    playbook = _load_playbook("switchover.yml")
    task_block = _get_task_block(playbook)
    rescue_tasks = task_block["rescue"]

    resume_tasks = [
        t
        for t in rescue_tasks
        if t.get("ansible.builtin.include_role", {}).get("name", "") == "tomazb.acm_switchover.argocd_manage"
    ]
    assert len(resume_tasks) >= 2, "rescue must include argocd_manage for both hubs"

    primary_task = resume_tasks[1]
    vars_ = primary_task.get("vars", {})
    assert vars_.get("_argocd_discover_hub") == "primary", "Second resume task must target primary hub"

    # Primary hub task must also check that primary is defined
    when = primary_task.get("when", [])
    when_text = " ".join(str(w) for w in when) if isinstance(when, list) else str(when)
    assert (
        "acm_switchover_hubs.primary is defined" in when_text
    ), "Primary resume task must guard with 'acm_switchover_hubs.primary is defined'"


def test_switchover_rescue_has_resume_on_failure_guard():
    """switchover.yml rescue resume tasks must be guarded by resume_on_failure flag."""
    playbook = _load_playbook("switchover.yml")
    task_block = _get_task_block(playbook)
    rescue_tasks = task_block["rescue"]

    resume_tasks = [
        t
        for t in rescue_tasks
        if t.get("ansible.builtin.include_role", {}).get("name", "") == "tomazb.acm_switchover.argocd_manage"
    ]
    for task in resume_tasks:
        when = task.get("when", [])
        when_text = " ".join(str(w) for w in when) if isinstance(when, list) else str(when)
        assert "resume_on_failure" in when_text, f"Resume task must be guarded by resume_on_failure flag. when: {when}"


def test_switchover_rescue_resets_primary_prep_checkpoint_after_resume_on_failure():
    """After best-effort resume, retry must rerun primary_prep and re-pause GitOps."""
    playbook = _load_playbook("switchover.yml")
    task_block = _get_task_block(playbook)
    rescue_tasks = task_block["rescue"]

    reset_tasks = [
        task for task in rescue_tasks if task.get("tomazb.acm_switchover.checkpoint_phase", {}).get("status") == "reset"
    ]
    assert reset_tasks, "switchover.yml rescue must reset a checkpoint phase after resume-on-failure"

    reset_task = reset_tasks[0]
    checkpoint_args = reset_task.get("tomazb.acm_switchover.checkpoint_phase", {})
    assert checkpoint_args.get("phase") == "primary_prep"
    assert checkpoint_args.get("checkpoint") == "{{ acm_switchover_execution.checkpoint }}"

    when = reset_task.get("when", [])
    when_text = " ".join(str(w) for w in when) if isinstance(when, list) else str(when)
    assert "argocd.manage" in when_text
    assert "resume_on_failure" in when_text
    assert "checkpoint.enabled" in when_text


def test_switchover_rescue_uses_ignore_errors():
    """switchover.yml rescue resume tasks must use ignore_errors to prevent compounding failures."""
    playbook = _load_playbook("switchover.yml")
    task_block = _get_task_block(playbook)
    rescue_tasks = task_block["rescue"]

    resume_tasks = [
        t
        for t in rescue_tasks
        if t.get("ansible.builtin.include_role", {}).get("name", "") == "tomazb.acm_switchover.argocd_manage"
    ]
    for task in resume_tasks:
        assert (
            task.get("ignore_errors") is True
        ), f"Resume task must have ignore_errors: true, got: {task.get('ignore_errors')}"


def test_switchover_rescue_reraises_failure():
    """switchover.yml rescue must re-raise the original failure after resume attempt."""
    playbook = _load_playbook("switchover.yml")
    task_block = _get_task_block(playbook)
    rescue_tasks = task_block["rescue"]

    fail_tasks = [t for t in rescue_tasks if "ansible.builtin.fail" in t]
    assert len(fail_tasks) >= 1, "rescue must re-raise original failure with ansible.builtin.fail"


# --- restore_only.yml ---


def test_primary_resume_guard_checks_kubeconfig_not_empty():
    """The primary resume guard must check that kubeconfig is actually populated."""
    playbooks_to_check = [
        PLAYBOOK_DIR / "argocd_resume.yml",
        PLAYBOOK_DIR / "switchover.yml",
    ]
    for pb_path in playbooks_to_check:
        text = pb_path.read_text()
        # Verify the guard checks kubeconfig length, not just 'is defined'
        assert "kubeconfig" in text and "length" in text, (
            f"{pb_path.name}: Primary hub resume guard must check that "
            "kubeconfig is non-empty, not just 'is defined'"
        )


def test_restore_only_has_rescue_block():
    """restore_only.yml must have a rescue block."""
    playbook = _load_playbook("restore_only.yml")
    task_block = _get_task_block(playbook)
    assert "rescue" in task_block, "restore_only.yml main block must have a rescue section"


def test_restore_only_rescue_resumes_secondary_only():
    """restore_only.yml rescue must resume ArgoCD on secondary hub only (no primary)."""
    playbook = _load_playbook("restore_only.yml")
    task_block = _get_task_block(playbook)
    rescue_tasks = task_block["rescue"]

    resume_tasks = [
        t
        for t in rescue_tasks
        if t.get("ansible.builtin.include_role", {}).get("name", "") == "tomazb.acm_switchover.argocd_manage"
    ]
    assert (
        len(resume_tasks) == 1
    ), f"restore_only rescue should resume only on secondary hub, found {len(resume_tasks)} resume tasks"

    vars_ = resume_tasks[0].get("vars", {})
    assert vars_.get("_argocd_discover_hub") == "secondary", "restore_only resume task must target secondary hub"


def test_restore_only_rescue_has_resume_on_failure_guard():
    """restore_only.yml rescue resume tasks must be guarded by resume_on_failure flag."""
    playbook = _load_playbook("restore_only.yml")
    task_block = _get_task_block(playbook)
    rescue_tasks = task_block["rescue"]

    resume_tasks = [
        t
        for t in rescue_tasks
        if t.get("ansible.builtin.include_role", {}).get("name", "") == "tomazb.acm_switchover.argocd_manage"
    ]
    for task in resume_tasks:
        when = task.get("when", [])
        when_text = " ".join(str(w) for w in when) if isinstance(when, list) else str(when)
        assert "resume_on_failure" in when_text, f"Resume task must be guarded by resume_on_failure flag. when: {when}"


def test_restore_only_rescue_uses_ignore_errors():
    """restore_only.yml rescue resume tasks must use ignore_errors."""
    playbook = _load_playbook("restore_only.yml")
    task_block = _get_task_block(playbook)
    rescue_tasks = task_block["rescue"]

    resume_tasks = [
        t
        for t in rescue_tasks
        if t.get("ansible.builtin.include_role", {}).get("name", "") == "tomazb.acm_switchover.argocd_manage"
    ]
    for task in resume_tasks:
        assert (
            task.get("ignore_errors") is True
        ), f"Resume task must have ignore_errors: true, got: {task.get('ignore_errors')}"


def test_restore_only_rescue_reraises_failure():
    """restore_only.yml rescue must re-raise the original failure after resume attempt."""
    playbook = _load_playbook("restore_only.yml")
    task_block = _get_task_block(playbook)
    rescue_tasks = task_block["rescue"]

    fail_tasks = [t for t in rescue_tasks if "ansible.builtin.fail" in t]
    assert len(fail_tasks) >= 1, "rescue must re-raise original failure with ansible.builtin.fail"


def test_restore_only_pins_operation_flags():
    """restore_only.yml must enforce restore_only=true, method=full, old_hub_action=none.

    Without pinning, role defaults (restore_only=false, method=passive,
    old_hub_action=secondary) apply, breaking the restore-only contract.
    """
    playbook = _load_playbook("restore_only.yml")
    play = playbook[0]

    # The playbook must set operation flags before roles run.
    # Acceptable locations: pre_tasks or vars.
    pre_tasks = play.get("pre_tasks", [])
    play_vars = play.get("vars", {})

    # Check pre_tasks for set_fact that pins operation flags
    pins_via_pre_tasks = any(
        "acm_switchover_operation" in str(t.get("ansible.builtin.set_fact", {})) for t in pre_tasks
    )
    # Check play-level vars for operation pinning
    pins_via_vars = "acm_switchover_operation" in play_vars

    assert pins_via_pre_tasks or pins_via_vars, (
        "restore_only.yml must pin acm_switchover_operation (restore_only, method, "
        "old_hub_action) via pre_tasks or play-level vars before including roles"
    )


def test_restore_only_pins_correct_values():
    """restore_only.yml pinned values must match restore-only semantics."""
    text = (PLAYBOOK_DIR / "restore_only.yml").read_text()

    # The pinned values must include these three critical fields
    assert (
        "'restore_only': true" in text or "'restore_only': True" in text or "restore_only: true" in text
    ), "restore_only.yml must pin restore_only to true"
    assert "'method': 'full'" in text or "method: full" in text, "restore_only.yml must pin method to full"
    assert (
        "'old_hub_action': 'none'" in text or "old_hub_action: none" in text
    ), "restore_only.yml must pin old_hub_action to none"


# --- resume.yml run_id safety ---


def _load_resume_tasks():
    return yaml.safe_load((ROLES_DIR / "argocd_manage" / "tasks" / "resume.yml").read_text())


def test_resume_fails_when_run_id_is_empty():
    """resume.yml must contain a fail task that fires when _argocd_expected_run_id is empty."""
    tasks_list = _load_resume_tasks()
    block_tasks = tasks_list[0]["block"]

    fail_tasks = [t for t in block_tasks if "ansible.builtin.fail" in t]
    assert len(fail_tasks) >= 1, "resume.yml must have at least one ansible.builtin.fail task"

    # The fail task must trigger when run_id is empty
    fail_task = fail_tasks[0]
    when = fail_task.get("when", [])
    when_text = " ".join(str(w) for w in when) if isinstance(when, list) else str(when)
    assert "_argocd_expected_run_id == ''" in when_text, "fail task must trigger when _argocd_expected_run_id is empty"
    assert "acm_switchover_argocd_mock_apps is not defined" in when_text, "fail task must skip in mock mode"


def test_resume_fail_task_precedes_patch_task():
    """The run_id fail-safe must come before the live-mode patch task."""
    tasks_list = _load_resume_tasks()
    block_tasks = tasks_list[0]["block"]

    fail_idx = None
    patch_idx = None
    for i, t in enumerate(block_tasks):
        if "ansible.builtin.fail" in t and "run_id" in t.get("name", "").lower():
            fail_idx = i
        if "kubernetes.core.k8s" in t:
            patch_idx = i

    assert fail_idx is not None, "resume.yml must have a run_id fail-safe task"
    assert patch_idx is not None, "resume.yml must have a k8s patch task"
    assert fail_idx < patch_idx, f"run_id fail-safe (index {fail_idx}) must come before k8s patch (index {patch_idx})"


def test_resume_patch_has_no_empty_run_id_wildcard():
    """The patch task's when condition must NOT allow empty run_id to match all apps."""
    tasks_list = _load_resume_tasks()
    block_tasks = tasks_list[0]["block"]

    patch_tasks = [t for t in block_tasks if "kubernetes.core.k8s" in t]
    assert len(patch_tasks) == 1, "Expected exactly one k8s patch task in resume.yml"

    patch_task = patch_tasks[0]
    when = patch_task.get("when", [])
    when_text = " ".join(str(w) for w in when) if isinstance(when, list) else str(when)

    assert (
        "_argocd_expected_run_id == ''" not in when_text
    ), "patch task must not contain an empty-string wildcard for _argocd_expected_run_id"
    assert (
        "paused-by'] == _argocd_expected_run_id" in when_text
    ), "patch task must require exact run_id match on the paused-by annotation"
