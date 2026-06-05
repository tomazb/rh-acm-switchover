"""Tests to verify ArgoCD discover.yml rescue block handles errors safely.

The rescue block must distinguish between 'CRD absent' (Argo CD not installed)
and unexpected errors (RBAC denial, network timeout, transient API errors).
Only a missing CRD should set acm_switchover_argocd_installed=false; all
other errors must fail the run.
"""

import pathlib

import yaml

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"


def _load_discover_tasks():
    return yaml.safe_load((ROLES_DIR / "argocd_manage" / "tasks" / "discover.yml").read_text())


def _get_discovery_block(tasks):
    """Return the 'Discover Argo CD Applications from cluster' block."""
    for task in tasks:
        if task.get("name", "") == "Discover Argo CD Applications from cluster":
            return task
    raise AssertionError("Could not find 'Discover Argo CD Applications from cluster' block")


def _get_rescue_tasks(tasks):
    """Return the rescue tasks from the discovery block."""
    block = _get_discovery_block(tasks)
    rescue = block.get("rescue")
    assert rescue is not None, "Discovery block must have a rescue section"
    return rescue


def _walk_tasks(tasks):
    """Yield task dictionaries from a task list, including nested block/rescue/always entries."""
    for task in tasks:
        yield task
        for nested_key in ("block", "rescue", "always"):
            for nested in task.get(nested_key, []) or []:
                yield from _walk_tasks([nested])


class TestDiscoverRescueBlockExists:
    """The discovery block must have a rescue section."""

    def test_rescue_block_present(self):
        tasks = _load_discover_tasks()
        block = _get_discovery_block(tasks)
        assert "rescue" in block, "Discovery block must have a rescue section"

    def test_rescue_has_multiple_tasks(self):
        """Rescue must have more than one task (not just a blanket set_fact)."""
        rescue = _get_rescue_tasks(_load_discover_tasks())
        assert len(rescue) > 1, (
            "Rescue block must have more than one task to distinguish " "CRD-absent from unexpected errors"
        )


class TestMarkNotInstalledIsConditional:
    """The 'mark not installed' task must be conditional, not a catch-all."""

    def _find_mark_not_installed_task(self, rescue):
        for task in rescue:
            sf = task.get("ansible.builtin.set_fact", {})
            if isinstance(sf, dict) and "acm_switchover_argocd_installed" in sf:
                return task
        raise AssertionError("Rescue block must contain a set_fact task for " "acm_switchover_argocd_installed")

    def test_mark_not_installed_has_when(self):
        rescue = _get_rescue_tasks(_load_discover_tasks())
        task = self._find_mark_not_installed_task(rescue)
        assert "when" in task, (
            "The 'mark not installed' task must have a 'when' condition "
            "so it only fires for CRD-absent errors, not all failures"
        )

    def test_mark_not_installed_when_references_error(self):
        """The when condition must reference the captured error variable."""
        rescue = _get_rescue_tasks(_load_discover_tasks())
        task = self._find_mark_not_installed_task(rescue)
        when_text = str(task["when"])
        assert "_argocd_discovery_error" in when_text, (
            "The 'mark not installed' when condition must reference "
            "_argocd_discovery_error to inspect the actual failure"
        )


class TestFailOnUnexpectedError:
    """The rescue must fail on non-CRD errors (RBAC, network, etc.)."""

    def _find_fail_task(self, rescue):
        for task in rescue:
            if "ansible.builtin.fail" in task:
                return task
        raise AssertionError(
            "Rescue block must contain an ansible.builtin.fail task " "for unexpected (non-CRD) errors"
        )

    def test_fail_task_exists(self):
        rescue = _get_rescue_tasks(_load_discover_tasks())
        self._find_fail_task(rescue)

    def test_fail_task_has_when(self):
        rescue = _get_rescue_tasks(_load_discover_tasks())
        task = self._find_fail_task(rescue)
        assert "when" in task, (
            "The fail task must have a 'when' condition " "(inverse of the mark-not-installed condition)"
        )

    def test_fail_task_when_is_inverse_of_mark_not_installed(self):
        """Fail-when must use 'not in' where mark-not-installed uses 'in'."""
        rescue = _get_rescue_tasks(_load_discover_tasks())

        mark_task = None
        fail_task = None
        for task in rescue:
            sf = task.get("ansible.builtin.set_fact", {})
            if isinstance(sf, dict) and "acm_switchover_argocd_installed" in sf:
                mark_task = task
            if "ansible.builtin.fail" in task:
                fail_task = task

        assert mark_task is not None and fail_task is not None

        mark_when = str(mark_task["when"]).lower()
        fail_when = str(fail_task["when"]).lower()

        # mark-not-installed uses ' in ' (substring match); fail uses ' not in '
        assert (
            " in " in mark_when and " or " in mark_when
        ), f"mark-not-installed 'when' should use 'in' with 'or': {mark_when}"
        assert (
            "not in" in fail_when and " and " in fail_when
        ), f"fail 'when' should use 'not in' with 'and': {fail_when}"

    def test_fail_task_message_includes_error(self):
        """The fail message must include the actual error for debugging."""
        rescue = _get_rescue_tasks(_load_discover_tasks())
        task = self._find_fail_task(rescue)
        msg = str(task["ansible.builtin.fail"].get("msg", ""))
        assert "_argocd_discovery_error" in msg, (
            "Fail task msg must include {{ _argocd_discovery_error }} " "so operators can diagnose the real failure"
        )


class TestErrorCapture:
    """The rescue must capture the error before inspecting it."""

    def test_error_capture_is_first_rescue_task(self):
        """First rescue task should capture the error into a variable."""
        rescue = _get_rescue_tasks(_load_discover_tasks())
        first = rescue[0]
        sf = first.get("ansible.builtin.set_fact", {})
        assert isinstance(sf, dict) and "_argocd_discovery_error" in sf, (
            "First rescue task must capture the error into " "_argocd_discovery_error via set_fact"
        )


class TestUnsafeApplicationBlocking:
    """Argo CD management must block Applications that cannot be safely paused."""

    def _find_blocker_fail_task(self, tasks):
        for task in _walk_tasks(tasks):
            fail = task.get("ansible.builtin.fail")
            if isinstance(fail, dict) and "unsafe for automated pause" in str(fail.get("msg", "")):
                return task
        raise AssertionError("discover.yml must fail when acm_argocd_filter reports blocked Applications")

    def test_discover_fails_when_filter_reports_blocked_applications(self):
        tasks = _load_discover_tasks()
        task = self._find_blocker_fail_task(tasks)

        assert "when" in task
        when_text = str(task["when"])
        assert "acm_switchover_argocd_blocked_apps" in when_text
        assert "== 'pause'" in when_text
        assert "acm_switchover_argocd_blocked_apps" in str(task["ansible.builtin.fail"]["msg"])

    def test_pause_rereads_applications_after_patch(self):
        pause_tasks = yaml.safe_load((ROLES_DIR / "argocd_manage" / "tasks" / "pause.yml").read_text())

        reread_tasks = [
            task
            for task in _walk_tasks(pause_tasks)
            if task.get("name") == "Re-read Applications after Argo CD pause" and "kubernetes.core.k8s_info" in task
        ]
        assert reread_tasks, "pause.yml must re-read Applications after patching auto-sync"

    def test_pause_fails_when_autosync_remains_enabled(self):
        pause_tasks = yaml.safe_load((ROLES_DIR / "argocd_manage" / "tasks" / "pause.yml").read_text())

        fail_tasks = [
            task
            for task in _walk_tasks(pause_tasks)
            if "ansible.builtin.fail" in task
            and "auto-sync remains enabled after pause" in str(task["ansible.builtin.fail"].get("msg", ""))
        ]
        assert fail_tasks, "pause.yml must fail if a re-read Application still has automated sync enabled"


class TestTrustedNamespaceDiscovery:
    """Later discovery passes may aggregate namespaced reads from trusted namespace hints."""

    def test_discover_resolves_trusted_namespace_list_for_current_hub(self):
        tasks = _load_discover_tasks()
        block = _get_discovery_block(tasks)
        resolve_tasks = [
            task
            for task in block.get("block", [])
            if task.get("name") == "Resolve trusted Argo CD discovery namespaces for current hub"
        ]
        assert resolve_tasks, "discover.yml must resolve trusted namespaces before listing Applications"
        fact = resolve_tasks[0]["ansible.builtin.set_fact"]
        assert "_argocd_trusted_discovery_namespaces" in fact
        assert "acm_switchover_argocd_discovery_namespaces" in str(fact)

    def test_discover_aggregates_namespaced_k8s_info_reads(self):
        tasks = _load_discover_tasks()
        block = _get_discovery_block(tasks)
        list_tasks = [
            task
            for task in block.get("block", [])
            if task.get("name") == "List Applications in trusted namespaces" and "kubernetes.core.k8s_info" in task
        ]
        aggregate_tasks = [
            task
            for task in block.get("block", [])
            if task.get("name") == "Aggregate Applications from trusted namespaces"
        ]
        assert list_tasks, "discover.yml must list Applications per trusted namespace"
        assert aggregate_tasks, "discover.yml must aggregate namespaced Application reads"
        assert "{{ item }}" in str(list_tasks[0]["kubernetes.core.k8s_info"].get("namespace", ""))
        assert "_argocd_app_list_by_ns" in str(aggregate_tasks[0]["ansible.builtin.set_fact"])

    def test_discover_records_namespace_map_after_cluster_wide_pass(self):
        tasks = _load_discover_tasks()
        block = _get_discovery_block(tasks)
        record_tasks = [
            task
            for task in block.get("block", [])
            if task.get("name") == "Record discovered Application namespaces for current hub"
        ]
        assert record_tasks, "discover.yml must persist per-hub Application namespaces after cluster-wide discovery"
        fact = record_tasks[0]["ansible.builtin.set_fact"]
        assert "acm_switchover_argocd_discovery_namespaces" in fact
        when_text = str(record_tasks[0].get("when", ""))
        assert "_argocd_use_scoped_discovery" in when_text
        assert "== 'pause'" in when_text

    def test_discover_does_not_seed_namespaces_in_read_only_discover_mode(self):
        tasks = _load_discover_tasks()
        block = _get_discovery_block(tasks)
        record_tasks = [
            task
            for task in block.get("block", [])
            if task.get("name") == "Record discovered Application namespaces for current hub"
        ]
        assert record_tasks
        when_text = str(record_tasks[0].get("when", ""))
        assert "!= 'discover'" in when_text or "== 'pause'" in when_text

    def test_discover_keeps_advisory_discover_mode_cluster_wide(self):
        tasks = _load_discover_tasks()
        block = _get_discovery_block(tasks)
        resolve_tasks = [
            task
            for task in block.get("block", [])
            if task.get("name") == "Resolve trusted Argo CD discovery namespaces for current hub"
        ]
        assert resolve_tasks
        scoped_expr = str(resolve_tasks[0]["ansible.builtin.set_fact"]["_argocd_use_scoped_discovery"])
        assert "!= 'discover'" in scoped_expr or "== 'discover'" in scoped_expr

    def test_discover_fails_when_persisted_hub_namespaces_are_not_a_list(self):
        tasks = _load_discover_tasks()
        block = _get_discovery_block(tasks)
        fail_tasks = [
            task
            for task in block.get("block", [])
            if "ansible.builtin.fail" in task
            and "must be a list" in str(task.get("ansible.builtin.fail", {}).get("msg", "")).lower()
        ]
        assert fail_tasks, "discover.yml must fail closed when persisted hub namespace hints are malformed"
        when_text = str(fail_tasks[0].get("when", ""))
        assert "is not list" in when_text
