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
    return yaml.safe_load(
        (ROLES_DIR / "argocd_manage" / "tasks" / "discover.yml").read_text()
    )


def _get_discovery_block(tasks):
    """Return the 'Discover Argo CD Applications from cluster' block."""
    for task in tasks:
        if task.get("name", "") == "Discover Argo CD Applications from cluster":
            return task
    raise AssertionError(
        "Could not find 'Discover Argo CD Applications from cluster' block"
    )


def _get_rescue_tasks(tasks):
    """Return the rescue tasks from the discovery block."""
    block = _get_discovery_block(tasks)
    rescue = block.get("rescue")
    assert rescue is not None, "Discovery block must have a rescue section"
    return rescue


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
            "Rescue block must have more than one task to distinguish "
            "CRD-absent from unexpected errors"
        )


class TestMarkNotInstalledIsConditional:
    """The 'mark not installed' task must be conditional, not a catch-all."""

    def _find_mark_not_installed_task(self, rescue):
        for task in rescue:
            sf = task.get("ansible.builtin.set_fact", {})
            if isinstance(sf, dict) and "acm_switchover_argocd_installed" in sf:
                return task
        raise AssertionError(
            "Rescue block must contain a set_fact task for "
            "acm_switchover_argocd_installed"
        )

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
            "Rescue block must contain an ansible.builtin.fail task "
            "for unexpected (non-CRD) errors"
        )

    def test_fail_task_exists(self):
        rescue = _get_rescue_tasks(_load_discover_tasks())
        self._find_fail_task(rescue)

    def test_fail_task_has_when(self):
        rescue = _get_rescue_tasks(_load_discover_tasks())
        task = self._find_fail_task(rescue)
        assert "when" in task, (
            "The fail task must have a 'when' condition "
            "(inverse of the mark-not-installed condition)"
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
        assert " in " in mark_when and " or " in mark_when, (
            f"mark-not-installed 'when' should use 'in' with 'or': {mark_when}"
        )
        assert "not in" in fail_when and " and " in fail_when, (
            f"fail 'when' should use 'not in' with 'and': {fail_when}"
        )

    def test_fail_task_message_includes_error(self):
        """The fail message must include the actual error for debugging."""
        rescue = _get_rescue_tasks(_load_discover_tasks())
        task = self._find_fail_task(rescue)
        msg = str(task["ansible.builtin.fail"].get("msg", ""))
        assert "_argocd_discovery_error" in msg, (
            "Fail task msg must include {{ _argocd_discovery_error }} "
            "so operators can diagnose the real failure"
        )


class TestErrorCapture:
    """The rescue must capture the error before inspecting it."""

    def test_error_capture_is_first_rescue_task(self):
        """First rescue task should capture the error into a variable."""
        rescue = _get_rescue_tasks(_load_discover_tasks())
        first = rescue[0]
        sf = first.get("ansible.builtin.set_fact", {})
        assert isinstance(sf, dict) and "_argocd_discovery_error" in sf, (
            "First rescue task must capture the error into "
            "_argocd_discovery_error via set_fact"
        )
