"""YAML contract tests for the argocd_manage role task files.

These tests verify the structural safety contracts of the argocd_manage role:
- Discover always runs first via import_tasks (not include_tasks).
- Pause and resume are gated on explicit mode selection.
- Mock path is cleanly separated from the live cluster path.
- Live discovery has a rescue block that handles missing CRD gracefully
  but re-fails on unexpected errors (fail closed).
- Blocked apps abort the pause operation.
- The k8s patch task skips in both dry-run and mock mode.
- Post-pause verification re-reads each app and fails if autosync remains enabled.
- All k8s tasks use the dynamic hub reference allowing secondary-hub testing.
"""

import pathlib

import yaml
from yaml_contract_helpers import _flatten_tasks, _when_text

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
ARGOCD_MAIN = ROLES_DIR / "argocd_manage" / "tasks" / "main.yml"
ARGOCD_DISCOVER = ROLES_DIR / "argocd_manage" / "tasks" / "discover.yml"
ARGOCD_PAUSE = ROLES_DIR / "argocd_manage" / "tasks" / "pause.yml"


class TestArgoCDManageMain:
    """argocd_manage/tasks/main.yml structural contract tests."""

    def setup_method(self):
        self.tasks = yaml.safe_load(ARGOCD_MAIN.read_text()) or []

    def test_file_exists(self):
        assert ARGOCD_MAIN.exists(), "argocd_manage/tasks/main.yml must exist"

    def test_discover_is_first_task_and_uses_import(self):
        """discover.yml must be the first task and use import_tasks (not include_tasks).

        import_tasks runs unconditionally and at parse time — this ensures discovery
        always sets up the ArgoCD state variables that pause/resume tasks depend on.
        Using include_tasks here would make the task's when condition able to skip discovery,
        leaving pause/resume tasks with undefined variables.
        """
        assert self.tasks, "main.yml must have tasks"
        first_task = self.tasks[0]
        assert "ansible.builtin.import_tasks" in first_task, (
            "First task in argocd_manage/main.yml must use ansible.builtin.import_tasks "
            "(not include_tasks) to ensure discovery always runs"
        )
        imported_file = first_task["ansible.builtin.import_tasks"]
        assert (
            imported_file == "discover.yml"
        ), f"First import_tasks must reference discover.yml, got: {imported_file!r}"

    def test_pause_is_conditional_on_pause_mode(self):
        """Pause task must only run when mode is 'pause'."""
        import_tasks = [t for t in self.tasks if "ansible.builtin.import_tasks" in t]
        pause_imports = [t for t in import_tasks if t["ansible.builtin.import_tasks"] == "pause.yml"]
        assert pause_imports, "main.yml must include pause.yml"
        for task in pause_imports:
            when = _when_text(task)
            assert "== 'pause'" in when, (
                "pause.yml import must be gated on mode == 'pause' to prevent accidental pausing "
                "when the playbook is invoked for resume"
            )

    def test_resume_is_conditional_on_resume_mode(self):
        """Resume task must only run when mode is 'resume'."""
        import_tasks = [t for t in self.tasks if "ansible.builtin.import_tasks" in t]
        resume_imports = [t for t in import_tasks if t["ansible.builtin.import_tasks"] == "resume.yml"]
        assert resume_imports, "main.yml must include resume.yml"
        for task in resume_imports:
            when = _when_text(task)
            assert "== 'resume'" in when, (
                "resume.yml import must be gated on mode == 'resume' to prevent accidental resuming "
                "when the playbook is invoked for pause"
            )


class TestArgoCDDiscover:
    """argocd_manage/tasks/discover.yml structural contract tests."""

    def setup_method(self):
        self.file_text = ARGOCD_DISCOVER.read_text()
        raw = yaml.safe_load(self.file_text) or []
        self.tasks = raw
        self.flat_tasks = _flatten_tasks(raw)

    def test_file_exists(self):
        assert ARGOCD_DISCOVER.exists(), "argocd_manage/tasks/discover.yml must exist"

    def test_mock_path_guarded_by_mock_apps_defined(self):
        """Mock application path must only activate when acm_switchover_argocd_mock_apps is defined."""
        block_tasks = [t for t in self.tasks if "block" in t]
        assert block_tasks, "discover.yml must have at least one block task"
        mock_path = [t for t in block_tasks if "is defined" in _when_text(t) and "mock_apps" in _when_text(t)]
        assert mock_path, (
            "discover.yml must have a block task guarded by "
            "'when: acm_switchover_argocd_mock_apps is defined' for test/mock mode"
        )

    def test_live_path_guarded_by_mock_apps_not_defined(self):
        """Live cluster discovery path must only activate when mock apps are not supplied."""
        block_tasks = [t for t in self.tasks if "block" in t]
        live_path = [t for t in block_tasks if "is not defined" in _when_text(t) and "mock_apps" in _when_text(t)]
        assert live_path, (
            "discover.yml must have a block task guarded by "
            "'when: acm_switchover_argocd_mock_apps is not defined' for live cluster discovery"
        )

    def test_live_path_has_rescue_block(self):
        """Live discovery block must have a rescue block to handle API errors gracefully."""
        block_tasks = [t for t in self.tasks if "block" in t]
        live_path = [t for t in block_tasks if "is not defined" in _when_text(t) and "mock_apps" in _when_text(t)]
        assert live_path, "Live path block must exist (see test_live_path_guarded_by_mock_apps_not_defined)"
        for task in live_path:
            assert "rescue" in task, (
                "Live Argo CD discovery block must have a rescue block to handle "
                "cases where Argo CD is not installed (missing CRD)"
            )

    def test_rescue_marks_not_installed_for_missing_crd(self):
        """Rescue block must handle missing Argo CD CRD by marking installed=false (not by failing).

        Argo CD is an optional component. If it is not installed, the rescue block must
        set acm_switchover_argocd_installed=false so downstream tasks cleanly skip,
        rather than aborting the switchover with an unexpected API error.
        """
        block_tasks = [t for t in self.tasks if "block" in t and "rescue" in t]
        assert block_tasks, "Live path block with rescue must exist"
        for block_task in block_tasks:
            rescue_tasks = block_task["rescue"]
            set_fact_in_rescue = [t for t in rescue_tasks if "ansible.builtin.set_fact" in t]
            not_installed_facts = [
                t
                for t in set_fact_in_rescue
                if t.get("ansible.builtin.set_fact", {}).get("acm_switchover_argocd_installed") is False
            ]
            assert not_installed_facts, (
                "Rescue block must have a set_fact task that sets "
                "acm_switchover_argocd_installed: false when Argo CD CRD is missing"
            )
            # Must be conditional — only for CRD-missing, not for all errors
            for task in not_installed_facts:
                when = _when_text(task)
                assert when, (
                    "The 'not installed' set_fact in rescue must have a when condition — "
                    "it should only trigger for CRD-missing errors, not all API failures"
                )

    def test_rescue_fails_on_non_crd_errors(self):
        """Rescue block must re-fail when the error is not a missing CRD.

        Silently swallowing unexpected API errors would give false confidence that
        Argo CD is simply absent when it may actually be unavailable due to an outage
        or connectivity issue, leading to an incomplete switchover.
        """
        block_tasks = [t for t in self.tasks if "block" in t and "rescue" in t]
        assert block_tasks
        for block_task in block_tasks:
            rescue_tasks = block_task["rescue"]
            fail_tasks = [t for t in rescue_tasks if "ansible.builtin.fail" in t]
            assert fail_tasks, (
                "Rescue block must have a fail task that re-raises unexpected errors "
                "that are not caused by a missing Argo CD CRD"
            )

    def test_blocked_apps_fail_gate_is_pause_mode_only(self):
        """Blocked apps fail gate must only trigger in pause mode.

        Resume mode explicitly operates on already-paused apps and should not be
        blocked by the same ApplicationSet/safety checks that guard the pause path.
        """
        fail_tasks = [t for t in self.tasks if "ansible.builtin.fail" in t]
        blocked_app_fails = [t for t in fail_tasks if "blocked" in str(t.get("ansible.builtin.fail", {}))]
        assert blocked_app_fails, "discover.yml must have a fail task that aborts when blocked apps are detected"
        for task in blocked_app_fails:
            when = _when_text(task)
            assert "== 'pause'" in when, (
                "Blocked apps fail gate must be restricted to mode == 'pause' — "
                "resume operations must not be blocked by this safety check"
            )

    def test_live_k8s_info_uses_dynamic_hub_reference(self):
        """Live Application list must use dynamic hub reference to support secondary-hub testing."""
        k8s_info_tasks = [t for t in self.flat_tasks if "kubernetes.core.k8s_info" in t]
        app_list_tasks = [
            t for t in k8s_info_tasks if t.get("kubernetes.core.k8s_info", {}).get("kind") == "Application"
        ]
        assert app_list_tasks, "discover.yml must have a k8s_info task that lists Applications"
        for task in app_list_tasks:
            params = task["kubernetes.core.k8s_info"]
            kubeconfig = str(params.get("kubeconfig", ""))
            assert "_argocd_discover_hub" in kubeconfig, (
                "Application list must use acm_switchover_hubs[_argocd_discover_hub | default('primary')] "
                "to allow the discover hub to be overridden for secondary-hub Argo CD testing"
            )


class TestArgoCDPause:
    """argocd_manage/tasks/pause.yml structural contract tests."""

    def setup_method(self):
        raw = yaml.safe_load(ARGOCD_PAUSE.read_text()) or []
        self.tasks = raw
        self.flat_tasks = _flatten_tasks(raw)

    def test_file_exists(self):
        assert ARGOCD_PAUSE.exists(), "argocd_manage/tasks/pause.yml must exist"

    def test_patch_task_skipped_in_dry_run(self):
        """k8s patch task must be guarded by execute mode — must not run in dry-run."""
        k8s_tasks = [t for t in self.flat_tasks if "kubernetes.core.k8s" in t and "kubernetes.core.k8s_info" not in t]
        patch_tasks = [t for t in k8s_tasks if t.get("kubernetes.core.k8s", {}).get("state") == "patched"]
        assert patch_tasks, "pause.yml must have a k8s state:patched task to remove autosync"
        for task in patch_tasks:
            when = _when_text(task)
            assert "!= 'dry_run'" in when, (
                "Argo CD patch task must be guarded by execute-mode check — "
                "dry-run must not patch Application resources"
            )

    def test_patch_task_skipped_in_mock_mode(self):
        """k8s patch task must not run when mock apps are supplied (no live cluster available)."""
        k8s_tasks = [t for t in self.flat_tasks if "kubernetes.core.k8s" in t and "kubernetes.core.k8s_info" not in t]
        patch_tasks = [t for t in k8s_tasks if t.get("kubernetes.core.k8s", {}).get("state") == "patched"]
        assert patch_tasks
        for task in patch_tasks:
            when = _when_text(task)
            assert "mock_apps is not defined" in when, (
                "Argo CD patch task must be skipped in mock mode " "(no live cluster is available to patch)"
            )

    def test_post_pause_reread_exists(self):
        """A k8s_info re-read must exist after the patch to verify the pause actually applied.

        Without re-reading after the patch, there is no way to detect cases where the
        Argo CD controller has already re-enabled autosync (e.g., ApplicationSet managed apps).
        """
        k8s_info_tasks = [t for t in self.flat_tasks if "kubernetes.core.k8s_info" in t]
        app_reread_tasks = [
            t for t in k8s_info_tasks if t.get("kubernetes.core.k8s_info", {}).get("kind") == "Application"
        ]
        assert app_reread_tasks, (
            "pause.yml must have a k8s_info task that re-reads Applications after patching "
            "to verify that autosync is actually disabled"
        )

    def test_fail_if_autosync_remains_after_pause(self):
        """A fail task must exist to abort if any Application still has autosync enabled post-patch.

        This is the critical safety net that catches ApplicationSet-managed apps whose
        autosync was re-enabled by the ApplicationSet controller before verification ran.
        """
        fail_tasks = [t for t in self.flat_tasks if "ansible.builtin.fail" in t]
        autosync_fail_tasks = [
            t
            for t in fail_tasks
            if "auto-sync remains" in str(t.get("ansible.builtin.fail", {}).get("msg", "")).lower()
            or "sync" in str(t.get("ansible.builtin.fail", {}).get("msg", "")).lower()
        ]
        assert autosync_fail_tasks, (
            "pause.yml must have a fail task that aborts when Application autosync "
            "remains enabled after the pause patch was applied"
        )
        # This fail task must only trigger for non-skipped non-mock loop items
        for task in autosync_fail_tasks:
            when = _when_text(task)
            assert (
                "mock_apps is not defined" in when
            ), "Autosync-remains fail task must only trigger in live mode, not mock mode"

    def test_k8s_tasks_use_dynamic_hub_reference(self):
        """All pause.yml k8s tasks must use the dynamic hub reference.

        This allows the same pause.yml to target either hub, which is needed for
        switchover scenarios where Argo CD runs on the secondary (new) hub.
        """
        for task in self.flat_tasks:
            for module in ("kubernetes.core.k8s", "kubernetes.core.k8s_info"):
                if module in task:
                    params = task[module]
                    if not isinstance(params, dict):
                        continue
                    kind = params.get("kind", "")
                    if kind not in ("Application",):
                        continue
                    kubeconfig = str(params.get("kubeconfig", ""))
                    assert "_argocd_discover_hub" in kubeconfig, (
                        f"pause.yml Application task '{task.get('name')}' must use "
                        "acm_switchover_hubs[_argocd_discover_hub | default('primary')] "
                        "to support both primary and secondary hub Argo CD targeting"
                    )
