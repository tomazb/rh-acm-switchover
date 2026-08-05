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
ARGOCD_RESUME = ROLES_DIR / "argocd_manage" / "tasks" / "resume.yml"


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

    def test_discover_keeps_cluster_wide_path_without_trusted_namespaces(self):
        """Default discovery must remain cluster-wide when no trusted namespace list exists."""
        cluster_wide_tasks = [
            t
            for t in self.flat_tasks
            if t.get("name") == "List Applications cluster-wide" and "kubernetes.core.k8s_info" in t
        ]
        assert cluster_wide_tasks, "discover.yml must keep a cluster-wide Application list path"
        params = cluster_wide_tasks[0]["kubernetes.core.k8s_info"]
        assert "default(omit)" in str(params.get("namespace", ""))

    def test_discover_uses_scoped_list_when_trusted_namespaces_present(self):
        """Trusted namespace hints must trigger per-namespace Application listing."""
        scoped_tasks = [
            t
            for t in self.flat_tasks
            if t.get("name") == "List Applications in trusted namespaces" and "kubernetes.core.k8s_info" in t
        ]
        assert scoped_tasks, "discover.yml must list Applications per trusted namespace when hints exist"
        assert "loop" in scoped_tasks[0]
        assert "_argocd_trusted_discovery_namespaces" in str(scoped_tasks[0]["loop"])

    def test_discover_defaults_optional_argocd_inputs_before_access(self):
        """discover.yml must not dereference acm_switchover_argocd directly when it may be undefined."""
        text = self.file_text
        assert "(acm_switchover_argocd | default({})).get('namespace', '')" in text
        assert "(acm_switchover_argocd | default({})).get('mode', 'pause')" in text


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


class TestArgoCDResumeFailClosed:
    """resume.yml must never patch spec.syncPolicy without a recoverable policy (ADR-0001, issue #184)."""

    def setup_method(self):
        self.text = ARGOCD_RESUME.read_text()
        self.tasks = _flatten_tasks(yaml.safe_load(self.text) or [])

    def _task(self, name_fragment):
        matches = [t for t in self.tasks if name_fragment in (t.get("name") or "")]
        assert matches, f"resume.yml must contain a task matching {name_fragment!r}"
        return matches[0]

    def test_restore_patch_never_defaults_policy_to_empty(self):
        """The rejected shape: default('{}') silently patches syncPolicy to {} for
        Python-paused apps (whose policy lives in the state file, not the annotation)."""
        assert "default('{}')" not in self.text, (
            "resume.yml must not default a missing original-sync-policy annotation to '{}' "
            "— that destroys the sync policy of Python-paused Applications (audit C1)"
        )

    def test_restore_patch_requires_policy_annotation(self):
        task = self._task("Restore original sync policy")
        when = _when_text(task)
        assert (
            "original-sync-policy" in when
        ), "Restore patch must be gated on the original-sync-policy annotation being present"
        assert "length" in when, "Restore patch must require a non-empty policy annotation"

    def test_unrecoverable_policy_fails_the_phase(self):
        task = self._task("Fail on unrecoverable original-sync-policy annotations")
        assert "ansible.builtin.fail" in task
        msg = str(task["ansible.builtin.fail"].get("msg", ""))
        assert (
            "--argocd-resume-only" in msg
        ), "Failure message must route Python-paused Applications to the Python resume path"
        when = _when_text(task)
        assert "paused-by" in when and "original-sync-policy" in when

    def test_orphaned_policy_annotation_is_reported(self):
        task = self._task("orphaned original-sync-policy")
        when = _when_text(task)
        assert "paused-by" in when and "original-sync-policy" in when


class TestArgoCDResumeCrdAbsent:
    """CRD invisible at resume must not be a silent no-op when a pause run_id exists.

    discover.yml's rescue sets installed=false and blanks the app lists on a
    CRD-absent error; resume.yml's main block is gated on installed. Without this
    gate that combination is ADR-0001's explicitly rejected 'clear register when
    CRD absent' behaviour: restored: 0, exit 0.
    """

    def setup_method(self):
        raw = yaml.safe_load(ARGOCD_RESUME.read_text()) or []
        self.top_level = raw
        self.tasks = _flatten_tasks(raw)

    def _gate(self):
        gates = [t for t in self.tasks if "cannot verify obligations" in (t.get("name") or "")]
        assert gates, "resume.yml must fail when CRD is absent but a run_id is known"
        return gates[0]

    def test_crd_absent_gate_exists_and_fails(self):
        assert "ansible.builtin.fail" in self._gate()

    def test_gate_uses_strong_run_id_signal(self):
        when = _when_text(self._gate())
        assert "acm_switchover_argocd" in when and "run_id" in when
        assert "acm_switchover_execution" not in when, (
            "Gate must key on acm_switchover_argocd.run_id only; the execution.run_id "
            "fallback is non-empty on every run and would hard-fail switchovers on "
            "clusters that never had Argo CD installed"
        )
        assert "acm_switchover_argocd_installed" in when

    def test_gate_is_top_level_before_installed_block(self):
        names = [t.get("name") or "" for t in self.top_level]
        gate_idx = next(i for i, n in enumerate(names) if "cannot verify obligations" in n)
        block_idx = next(i for i, n in enumerate(names) if "Resume auto-sync" in n)
        assert gate_idx < block_idx, "Gate must run before the installed-gated resume block"


class TestArgoCDRunIdLifecycle:
    """run_id must exist iff a pause may have landed (ADR-0001 obligation signal)."""

    def setup_method(self):
        self.discover_text = ARGOCD_DISCOVER.read_text()
        self.pause_text = ARGOCD_PAUSE.read_text()
        self.discover_tasks = _flatten_tasks(yaml.safe_load(self.discover_text) or [])
        self.pause_tasks = _flatten_tasks(yaml.safe_load(self.pause_text) or [])

    def test_run_id_minted_only_when_installed(self):
        mint = [t for t in self.discover_tasks if "Generate run_id" in (t.get("name") or "")]
        assert mint, "discover.yml must keep the run_id generation task"
        when = _when_text(mint[0])
        assert "acm_switchover_argocd_installed" in when, (
            "run_id must be minted only after discovery confirms Argo CD is installed; "
            "an unconditional mint makes run_id useless as an obligation signal"
        )

    def test_pause_marker_never_falls_back_to_unknown(self):
        assert "'unknown'" not in self.pause_text and '"unknown"' not in self.pause_text, (
            "pause.yml must not write paused-by: unknown — an 'unknown' marker can never "
            "be matched by any resume run_id and orphans the pause"
        )

    def test_pause_requires_run_id(self):
        req = [t for t in self.pause_tasks if "Require run_id" in (t.get("name") or "")]
        assert req and "ansible.builtin.fail" in req[0]

    def test_checkpoint_persist_has_no_execution_fallback(self):
        collection_root = ROLES_DIR.parent
        offenders = []
        for path in [
            ROLES_DIR / "primary_prep" / "tasks" / "main.yml",
            ROLES_DIR / "activation" / "tasks" / "main.yml",
            collection_root / "playbooks" / "switchover.yml",
            collection_root / "playbooks" / "restore_only.yml",
        ]:
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if "argocd_run_id:" in line and "acm_switchover_execution" in line:
                    offenders.append(f"{path.name}:{i}")
        assert not offenders, (
            f"argocd_run_id persisted with execution.run_id fallback at {offenders}; the "
            "checkpoint value must be non-empty only when an Argo CD pause run_id exists"
        )
