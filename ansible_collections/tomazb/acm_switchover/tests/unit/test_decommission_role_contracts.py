"""YAML contract tests for the decommission role task files.

These tests verify the structural safety contracts of decommission role tasks:
- Confirmation gate blocks execution without explicit opt-in (bypassed only in dry-run).
- Primary hub is explicitly asserted before any destructive operations.
- RBAC is validated before destructive operations begin.
- Dry-run mode skips all live cluster reads and deletes.
- Delete loops, wait conditions, and NotFound handling are present.
- ClusterDeployment safety verification is in place before ManagedCluster deletion.
- Pod wait uses failed_when: false to warn (not fail) when pods linger.
"""

import copy
import json
import pathlib
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, Dict, List, Optional, Union
from urllib.parse import unquote, urlsplit

import yaml
from yaml_contract_helpers import _flatten_tasks, _when_text

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    DECOMMISSION_SUBSTEP_OUTCOMES,
)

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
DECOMMISSION_MAIN = ROLES_DIR / "decommission" / "tasks" / "main.yml"
DELETE_OBSERVABILITY = ROLES_DIR / "decommission" / "tasks" / "delete_observability.yml"
DELETE_MANAGED_CLUSTERS = ROLES_DIR / "decommission" / "tasks" / "delete_managed_clusters.yml"
DELETE_MCH = ROLES_DIR / "decommission" / "tasks" / "delete_multiclusterhub.yml"
VALIDATE_RBAC = ROLES_DIR / "decommission" / "tasks" / "validate_rbac.yml"

#: The collection modules that implement native check mode, as verified by a test in
#: THIS repository. ``acm_uid_guarded_delete`` is deliberately ABSENT: it does not
#: exist yet, and "native check mode" cannot be assumed -- with the role's three
#: ``not ansible_check_mode`` guards removed, ``kubernetes.core.k8s`` under ``--check``
#: issued three real DELETEs to the fake API. PR C must add the module, this exemption,
#: and a module-level test proving it issues no DELETE in check mode, in the same PR --
#: the same rule as the harness raising on an unrecognised keyword. PR E adds
#: ``acm_pod_owner_classify`` on the same terms.
CHECK_MODE_NATIVE_MODULES = frozenset({"tomazb.acm_switchover.acm_k8s_read_outcome"})

#: The ONLY actions the decommission role may run without a check-mode guard, each
#: read-only (or, for the RBAC expander, a pure controller-side computation).
#:
#: This is deliberately an allowlist rather than a list of mutating modules: a NEW
#: module added by PR C, D or E -- ``command``, ``shell``, ``uri``, ``helm``,
#: ``k8s_scale``, ``k8s_exec``, ``k8s_drain``, or anything else -- is treated as
#: mutating by default and fails ``test_every_mutating_task_is_check_mode_guarded``
#: until it is classified here deliberately. A denylist would have let all of those
#: through unchallenged.
_READ_ONLY_ACTIONS = frozenset(
    {
        "ansible.builtin.assert",
        "ansible.builtin.debug",
        "ansible.builtin.fail",
        "ansible.builtin.include_tasks",
        "ansible.builtin.set_fact",
        "kubernetes.core.k8s_info",
        # Expands/summarises the required permission set on the controller; the live
        # SelfSubjectAccessReviews live in preflight/tasks/run_ssar.yml.
        "tomazb.acm_switchover.acm_rbac_validate",
        # Read-only by module contract, and it must keep running in check mode.
        "tomazb.acm_switchover.acm_k8s_read_outcome",
    }
)

#: The action that enters, exits, or writes checkpoint ``operational_data``.
_CHECKPOINT_WRITER_MODULE = "tomazb.acm_switchover.checkpoint_phase"


def _include_file(task: dict) -> str:
    """Extract filename from ansible.builtin.include_tasks regardless of string vs dict form."""
    val = task.get("ansible.builtin.include_tasks", "")
    if isinstance(val, str):
        return val
    return val.get("file", "") if isinstance(val, dict) else ""


def _load_tasks(path: pathlib.Path) -> list:
    """Parse one role task file, flattening block/rescue/always into one ordered list."""
    return _flatten_tasks(yaml.safe_load(path.read_text()) or [])


#: EVERY decommission role task file, parsed and flattened once. ``validate_rbac``
#: is included so a mutating task added there cannot evade the guardrails.
decommission_task_files = {
    "main": _load_tasks(DECOMMISSION_MAIN),
    "observability": _load_tasks(DELETE_OBSERVABILITY),
    "managed_clusters": _load_tasks(DELETE_MANAGED_CLUSTERS),
    "multiclusterhub": _load_tasks(DELETE_MCH),
    "validate_rbac": _load_tasks(VALIDATE_RBAC),
}

#: The parsed set must be exactly the files on disk, so a task file added later is not
#: silently excluded from every guardrail above.
_ROLE_TASK_FILE_NAMES = frozenset(
    path.name for path in (ROLES_DIR / "decommission" / "tasks").iterdir() if path.suffix == ".yml"
)


def _task_actions(task: dict) -> list:
    """Every module/action key invoked by one task, ignoring task keywords."""
    keywords = {
        "name",
        "when",
        "loop",
        "loop_control",
        "register",
        "vars",
        "block",
        "rescue",
        "always",
        "retries",
        "delay",
        "until",
        "failed_when",
        "changed_when",
        "check_mode",
        "ignore_errors",
        "tags",
        "no_log",
        "delegate_to",
        "run_once",
    }
    return [key for key in task if key not in keywords]


def task_named(tasks: list, name: str) -> dict:
    """Return the single task whose ``name`` equals ``name``."""
    matches = [task for task in tasks if task.get("name") == name]
    assert matches, f"no task named {name!r}"
    assert len(matches) == 1, f"task name {name!r} is ambiguous ({len(matches)} matches)"
    return matches[0]


def index_of_task_using(tasks: list, action: str) -> int:
    """Index of the first task invoking ``action``; -1 when absent."""
    for index, task in enumerate(tasks):
        if action in _task_actions(task):
            return index
    return -1


def index_of_first_include(tasks: list, prefix: str) -> int:
    """Index of the first ``include_tasks`` whose file name starts with ``prefix``; -1 when absent."""
    for index, task in enumerate(tasks):
        if _include_file(task).startswith(prefix):
            return index
    return -1


def read_outcome_tasks(tasks: list) -> list:
    """Every task invoking ``tomazb.acm_switchover.acm_k8s_read_outcome``."""
    return [task for task in tasks if "tomazb.acm_switchover.acm_k8s_read_outcome" in _task_actions(task)]


def checkpoint_writer_tasks(task_files: dict) -> list:
    """Every task in ``task_files`` that enters, exits, or writes checkpoint operational_data."""
    return [task for tasks in task_files.values() for task in tasks if _CHECKPOINT_WRITER_MODULE in _task_actions(task)]


def mutating_tasks(task_files: dict) -> list:
    """Every parsed task in ``task_files`` whose module can mutate the cluster.

    Inverted allowlist: a task counts as mutating unless every action it invokes is in
    ``_READ_ONLY_ACTIONS``. Block/rescue/always wrappers invoke no action and are skipped.
    """
    return [
        task
        for tasks in task_files.values()
        for task in tasks
        if _task_actions(task) and not set(_task_actions(task)).issubset(_READ_ONLY_ACTIONS)
    ]


#: The families that own a substep outcome. Used to tell an outcome VALUE apart from
#: the family KEY inside a recorded ``combine`` expression.
_OUTCOME_FAMILIES = ("observability", "managed_clusters", "multiclusterhub")


def outcome_recording_tasks(task_files: dict) -> list:
    """Every task in ``task_files`` that writes the shared substep outcome mapping."""
    return [
        task
        for tasks in task_files.values()
        for task in tasks
        if "acm_switchover_decommission_outcomes" in (task.get("ansible.builtin.set_fact") or {})
        and str(task["ansible.builtin.set_fact"]["acm_switchover_decommission_outcomes"]).strip() != "{}"
    ]


def recorded_outcome_values(task: dict) -> set:
    """Every outcome VALUE literal one recording task can write, family keys removed."""
    recorded = str(task["ansible.builtin.set_fact"]["acm_switchover_decommission_outcomes"])
    return set(re.findall(r"'([a-z_]+)'", recorded)) - set(_OUTCOME_FAMILIES)


class TestDecommissionMain:
    """decommission/tasks/main.yml structural contract tests."""

    def setup_method(self):
        # Flattened: the substep includes live inside a block whose rescue records
        # the failed outcome and whose always publishes the summary artifact.
        self.tasks = _flatten_tasks(yaml.safe_load(DECOMMISSION_MAIN.read_text()) or [])

    def test_file_exists(self):
        assert DECOMMISSION_MAIN.exists(), "decommission/tasks/main.yml must exist"

    def test_confirmation_gate_exists(self):
        """A fail task must exist to block unconfirmed decommission operations.

        Without a confirmation gate, an operator who accidentally runs the decommission
        playbook without setting confirmed=true would destroy the cluster immediately.
        """
        fail_tasks = [t for t in self.tasks if "ansible.builtin.fail" in t]
        confirmation_gate = [
            t
            for t in fail_tasks
            if "confirmed" in str(t.get("ansible.builtin.fail", {}).get("msg", "")) or "confirmed" in _when_text(t)
        ]
        assert confirmation_gate, (
            "decommission/tasks/main.yml must have a confirmation gate fail task that references "
            "acm_switchover_decommission.confirmed"
        )

    def test_confirmation_gate_bypassed_only_in_dry_run(self):
        """Confirmation gate must be skipped in dry-run mode but enforced for all live operations."""
        fail_tasks = [t for t in self.tasks if "ansible.builtin.fail" in t]
        confirmation_gate = [
            t
            for t in fail_tasks
            if "confirmed" in str(t.get("ansible.builtin.fail", {}).get("msg", "")) or "confirmed" in _when_text(t)
        ]
        assert confirmation_gate, "Confirmation gate must exist (see test_confirmation_gate_exists)"
        for task in confirmation_gate:
            when = _when_text(task)
            assert "dry_run" in when and "!=" in when, (
                "Confirmation gate must allow dry-run to bypass with '!= dry_run' so dry-run "
                "can be used to preview decommission without a confirmed=true flag"
            )

    def test_primary_hub_assertion_exists(self):
        """An assert task must verify primary hub kubeconfig and context are non-empty.

        Without this safety gate, decommission could run against an implicit default kubeconfig,
        potentially destroying the wrong cluster.
        """
        assert_tasks = [t for t in self.tasks if "ansible.builtin.assert" in t]
        hub_asserts = [t for t in assert_tasks if "primary" in str(t.get("ansible.builtin.assert", {}).get("that", ""))]
        assert hub_asserts, (
            "decommission/tasks/main.yml must have an assert task verifying "
            "acm_switchover_hubs.primary kubeconfig and context are non-empty"
        )

    def test_primary_hub_assertion_checks_kubeconfig_and_context(self):
        """Primary hub assert must verify both kubeconfig AND context are non-empty strings."""
        assert_tasks = [t for t in self.tasks if "ansible.builtin.assert" in t]
        hub_asserts = [t for t in assert_tasks if "primary" in str(t.get("ansible.builtin.assert", {}).get("that", ""))]
        assert hub_asserts
        for task in hub_asserts:
            conditions = task["ansible.builtin.assert"]["that"]
            assert isinstance(conditions, list), "Assert 'that' must be a list of conditions"
            conditions_text = " ".join(str(c) for c in conditions)
            assert "kubeconfig" in conditions_text, "Primary hub assert must check kubeconfig is non-empty"
            assert "context" in conditions_text, "Primary hub assert must check context is non-empty"

    def test_rbac_validated_before_destructive_operations(self):
        """RBAC validation must run before any delete operations begin.

        If RBAC validation ran after deletes started, partial deletions could have already
        occurred before discovering the operator lacks sufficient permissions.
        """
        include_tasks = [t for t in self.tasks if "ansible.builtin.include_tasks" in t]
        filenames = [_include_file(t) for t in include_tasks]
        assert "validate_rbac.yml" in filenames, "decommission/tasks/main.yml must include validate_rbac.yml"
        rbac_idx = filenames.index("validate_rbac.yml")
        for delete_file in (
            "delete_observability.yml",
            "delete_managed_clusters.yml",
            "delete_multiclusterhub.yml",
        ):
            assert delete_file in filenames, f"Expected {delete_file} to be included"
            delete_idx = filenames.index(delete_file)
            assert rbac_idx < delete_idx, (
                f"validate_rbac.yml (position {rbac_idx}) must be included before "
                f"{delete_file} (position {delete_idx})"
            )

    def test_observability_deletion_is_conditional(self):
        """delete_observability.yml must be conditionally included, not always run.

        Not all ACM installations have MultiClusterObservability. Running the delete
        unconditionally would fail when the namespace does not exist.
        """
        include_tasks = [t for t in self.tasks if "ansible.builtin.include_tasks" in t]
        obs_includes = [t for t in include_tasks if _include_file(t) == "delete_observability.yml"]
        assert obs_includes, "decommission/tasks/main.yml must include delete_observability.yml"
        for task in obs_includes:
            assert "when" in task, (
                "delete_observability.yml include must have a 'when' condition — "
                "it should only run when observability is present"
            )
            assert "has_observability" in _when_text(
                task
            ), "delete_observability.yml include must be gated on the effective has_observability flag"

    def test_managed_clusters_and_mch_are_unconditional_includes(self):
        """delete_managed_clusters.yml and delete_multiclusterhub.yml must always be included."""
        include_tasks = [t for t in self.tasks if "ansible.builtin.include_tasks" in t]
        for required_file in (
            "delete_managed_clusters.yml",
            "delete_multiclusterhub.yml",
        ):
            matching = [t for t in include_tasks if _include_file(t) == required_file]
            assert matching, f"decommission/tasks/main.yml must include {required_file}"
            for task in matching:
                assert "when" not in task, (
                    f"{required_file} must be included unconditionally — "
                    "internal tasks already guard on dry-run and resource presence"
                )


class TestDeleteObservability:
    """decommission/tasks/delete_observability.yml contract tests."""

    def setup_method(self):
        self.tasks = yaml.safe_load(DELETE_OBSERVABILITY.read_text()) or []

    def test_file_exists(self):
        assert DELETE_OBSERVABILITY.exists(), "decommission/tasks/delete_observability.yml must exist"

    def test_dry_run_announces_without_acting(self):
        """In dry-run mode, a debug message must announce what would be deleted — no live ops."""
        debug_tasks = [t for t in self.tasks if "ansible.builtin.debug" in t]
        dry_run_announce = [t for t in debug_tasks if "dry_run" in _when_text(t) and "==" in _when_text(t)]
        assert dry_run_announce, (
            "delete_observability.yml must have a dry-run debug task that announces the deletion "
            "that would occur without actually running it"
        )

    def test_list_task_skipped_in_dry_run(self):
        """MultiClusterObservability list (k8s_info) must be guarded by execute mode."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        mco_list_tasks = [
            t
            for t in k8s_info_tasks
            if t.get("kubernetes.core.k8s_info", {}).get("kind") == "MultiClusterObservability"
        ]
        assert mco_list_tasks, "delete_observability.yml must list MultiClusterObservability resources"
        for task in mco_list_tasks:
            when = _when_text(task)
            assert (
                "!= 'dry_run'" in when
            ), "MCO list task must be guarded by execute-mode check to skip live reads in dry-run"

    def test_delete_task_guarded_by_execute_mode(self):
        """k8s state:absent delete task must not run in dry-run mode."""
        k8s_tasks = [t for t in self.tasks if "kubernetes.core.k8s" in t and "kubernetes.core.k8s_info" not in t]
        delete_tasks = [t for t in k8s_tasks if t.get("kubernetes.core.k8s", {}).get("state") == "absent"]
        assert delete_tasks, "delete_observability.yml must have a delete task (state: absent)"
        for task in delete_tasks:
            when = _when_text(task)
            assert "!= 'dry_run'" in when, (
                "Delete task must be guarded by execute-mode check — "
                "accidentally running in dry-run would destroy observability"
            )

    def test_delete_task_uses_loop(self):
        """Delete task must use a loop to delete all discovered resources."""
        k8s_tasks = [t for t in self.tasks if "kubernetes.core.k8s" in t and "kubernetes.core.k8s_info" not in t]
        delete_tasks = [t for t in k8s_tasks if t.get("kubernetes.core.k8s", {}).get("state") == "absent"]
        assert delete_tasks
        for task in delete_tasks:
            assert "loop" in task, "Delete task must use 'loop' to handle all discovered MCO instances"

    def test_wait_task_has_retries_delay_until(self):
        """Pod wait task must use polling with retries and delay."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        wait_tasks = [t for t in k8s_info_tasks if t.get("kubernetes.core.k8s_info", {}).get("kind") == "Pod"]
        assert wait_tasks, "delete_observability.yml must have a Pod wait task"
        for task in wait_tasks:
            assert "retries" in task, "Pod wait must have retries"
            assert "delay" in task, "Pod wait must have delay"
            assert "until" in task, "Pod wait must have until condition"

    def test_wait_task_handles_notfound_gracefully(self):
        """Pod wait task's failed_when must handle NotFound so pod termination is idempotent.

        Pods may be gone before the wait poll runs. A bare failed=true check would cause the
        wait to fail even when pods are already cleanly terminated (404 = success, not error).
        """
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        wait_tasks = [t for t in k8s_info_tasks if t.get("kubernetes.core.k8s_info", {}).get("kind") == "Pod"]
        assert wait_tasks
        for task in wait_tasks:
            failed_when = task.get("failed_when", "")
            assert failed_when, "Pod wait task must have a failed_when condition"
            failed_when_text = (
                " ".join(str(c) for c in failed_when) if isinstance(failed_when, list) else str(failed_when)
            )
            assert "NotFound" in failed_when_text or "not found" in failed_when_text.lower(), (
                "Pod wait failed_when must allow NotFound responses to be treated as success "
                "(pods already gone = namespace/resource already cleaned up)"
            )


class TestDeleteManagedClusters:
    """decommission/tasks/delete_managed_clusters.yml contract tests."""

    def setup_method(self):
        self.tasks = yaml.safe_load(DELETE_MANAGED_CLUSTERS.read_text()) or []

    def test_file_exists(self):
        assert DELETE_MANAGED_CLUSTERS.exists(), "decommission/tasks/delete_managed_clusters.yml must exist"

    def test_list_task_skipped_in_dry_run(self):
        """ManagedCluster list task must be guarded by execute mode."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        mc_list_tasks = [
            t for t in k8s_info_tasks if t.get("kubernetes.core.k8s_info", {}).get("kind") == "ManagedCluster"
        ]
        assert mc_list_tasks, "delete_managed_clusters.yml must list ManagedCluster resources"
        list_task = mc_list_tasks[0]
        assert "!= 'dry_run'" in _when_text(list_task), "ManagedCluster list must be guarded by execute-mode check"

    def test_local_cluster_excluded_from_deletion_targets(self):
        """local-cluster must always be excluded from the deletion targets.

        Deleting the local-cluster ManagedCluster would decommission the hub cluster's own
        ACM management, which is always the wrong behavior during decommission of spoke clusters.
        """
        set_fact_tasks = [t for t in self.tasks if "ansible.builtin.set_fact" in t]
        # Only the task that BUILDS the target list, not every task that reads it.
        target_selection = [
            t for t in set_fact_tasks if "_managed_cluster_delete_targets" in t["ansible.builtin.set_fact"]
        ]
        assert (
            target_selection
        ), "delete_managed_clusters.yml must have a set_fact task that builds the deletion targets list"
        for task in target_selection:
            task_text = str(task)
            assert "local-cluster" in task_text, "Deletion target selection must explicitly exclude 'local-cluster'"
            assert (
                "rejectattr" in task_text
            ), "Deletion target selection must use rejectattr to filter out local-cluster"

    def test_clusterdeployment_safety_verified_before_deletion(self):
        """ClusterDeployment preserveOnDelete safety must be verified before deleting ManagedClusters.

        Deleting a ManagedCluster whose matching Hive ClusterDeployment lacks preserveOnDelete=true
        will deprovision the underlying cluster infrastructure. This is non-recoverable.
        """
        file_text = DELETE_MANAGED_CLUSTERS.read_text()
        assert (
            "ClusterDeployment" in file_text
        ), "delete_managed_clusters.yml must verify ClusterDeployment safety before deleting ManagedClusters"
        assert (
            "preserveOnDelete" in file_text or "preserve_on_delete" in file_text.lower()
        ), "ClusterDeployment safety check must verify preserveOnDelete=true"


class TestDeleteMultiClusterHub:
    """decommission/tasks/delete_multiclusterhub.yml contract tests."""

    def setup_method(self):
        self.tasks = yaml.safe_load(DELETE_MCH.read_text()) or []

    def test_file_exists(self):
        assert DELETE_MCH.exists(), "decommission/tasks/delete_multiclusterhub.yml must exist"

    def test_list_and_delete_guarded_by_execute_mode(self):
        """MCH list and delete operations must not run in dry-run mode."""
        # list task
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        mch_list_tasks = [
            t for t in k8s_info_tasks if t.get("kubernetes.core.k8s_info", {}).get("kind") == "MultiClusterHub"
        ]
        assert mch_list_tasks, "delete_multiclusterhub.yml must list MultiClusterHub resources"
        for task in mch_list_tasks:
            assert "!= 'dry_run'" in _when_text(task), "MCH list must be guarded by execute-mode check"

        # delete task
        k8s_tasks = [t for t in self.tasks if "kubernetes.core.k8s" in t and "kubernetes.core.k8s_info" not in t]
        mch_delete_tasks = [t for t in k8s_tasks if t.get("kubernetes.core.k8s", {}).get("kind") == "MultiClusterHub"]
        assert mch_delete_tasks, "delete_multiclusterhub.yml must have a MultiClusterHub delete task"
        for task in mch_delete_tasks:
            assert "!= 'dry_run'" in _when_text(task), "MCH delete must be guarded by execute-mode check"

    def test_mch_operations_use_primary_hub(self):
        """All MCH operations must target the primary hub (old hub being decommissioned)."""
        for task in self.tasks:
            for module in ("kubernetes.core.k8s_info", "kubernetes.core.k8s"):
                if module in task:
                    params = task[module]
                    if isinstance(params, dict) and params.get("kind") in (
                        "MultiClusterHub",
                        "Pod",
                    ):
                        kubeconfig = str(params.get("kubeconfig", ""))
                        assert (
                            "primary" in kubeconfig
                        ), f"Task '{task.get('name')}' must use primary hub kubeconfig for MCH operations"

    def test_pod_wait_uses_failed_when_false(self):
        """ACM pod wait must use failed_when: false to warn rather than fail when pods linger.

        The pod watch may time out when some ACM components take unexpectedly long to
        terminate. Failing hard here is unhelpful — the MCH is already deleted; the operator
        should be warned and can verify manually.
        """
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        pod_wait_tasks = [t for t in k8s_info_tasks if t.get("kubernetes.core.k8s_info", {}).get("kind") == "Pod"]
        assert pod_wait_tasks, "delete_multiclusterhub.yml must have a Pod wait task"
        for task in pod_wait_tasks:
            assert task.get("failed_when") is False, (
                "ACM pod wait must use 'failed_when: false' — lingering pods should warn, "
                "not abort the decommission. The MCH is already deleted at this point."
            )

    def test_pod_wait_has_retries_delay_until(self):
        """MCH pod wait must use polling with retries and delay."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        pod_wait_tasks = [t for t in k8s_info_tasks if t.get("kubernetes.core.k8s_info", {}).get("kind") == "Pod"]
        assert pod_wait_tasks
        for task in pod_wait_tasks:
            assert "retries" in task, "Pod wait must specify retries"
            assert "delay" in task, "Pod wait must specify delay"
            assert "until" in task, "Pod wait must specify until condition"


def test_summary_status_is_not_hardcoded():
    publish = task_named(decommission_task_files["main"], "Publish decommission result")
    status = publish["ansible.builtin.set_fact"]["acm_switchover_decommission_result"]["status"]
    assert status != "pass", "status must be derived from the real substep outcomes"
    assert "acm_switchover_decommission_outcomes" in str(status)


# ---------------------------------------------------------------------------
# B4.1 -- the one collection role-result contract.
#
# ``run_decommission_role`` drives the REAL decommission role through
# ``ansible-playbook`` against a fake Kubernetes API. Nothing here simulates the
# role's YAML in Python: the outcome, the published artifact and the DELETE log
# are all produced by the shipped role talking to a real HTTP server.
# ---------------------------------------------------------------------------

_HARNESS_TASK_PREFIX = "HARNESS "

_CORE_API_RESOURCES = [
    {"name": "pods", "singularName": "pod", "namespaced": True, "kind": "Pod", "verbs": ["get", "list", "delete"]},
    {
        "name": "namespaces",
        "singularName": "namespace",
        "namespaced": False,
        "kind": "Namespace",
        "verbs": ["get", "list", "delete"],
    },
]

#: group -> (version, [APIResource, ...]). Exactly the kinds the decommission
#: role reads or deletes; nothing else is served, so an unexpected new API call
#: surfaces as a discovery failure rather than passing silently.
_GROUP_API_RESOURCES = {
    "observability.open-cluster-management.io": (
        "v1beta2",
        [
            {
                "name": "multiclusterobservabilities",
                "singularName": "multiclusterobservability",
                "namespaced": False,
                "kind": "MultiClusterObservability",
                "verbs": ["get", "list", "delete"],
            }
        ],
    ),
    "cluster.open-cluster-management.io": (
        "v1",
        [
            {
                "name": "managedclusters",
                "singularName": "managedcluster",
                "namespaced": False,
                "kind": "ManagedCluster",
                "verbs": ["get", "list", "delete"],
            }
        ],
    ),
    "hive.openshift.io": (
        "v1",
        [
            {
                "name": "clusterdeployments",
                "singularName": "clusterdeployment",
                "namespaced": True,
                "kind": "ClusterDeployment",
                "verbs": ["get", "list", "delete"],
            }
        ],
    ),
    "operator.open-cluster-management.io": (
        "v1",
        [
            {
                "name": "multiclusterhubs",
                "singularName": "multiclusterhub",
                "namespaced": True,
                "kind": "MultiClusterHub",
                "verbs": ["get", "list", "delete"],
            }
        ],
    ),
}

_KIND_BY_PLURAL: Dict[str, str] = {"pods": "Pod", "namespaces": "Namespace"}
_API_VERSION_BY_PLURAL: Dict[str, str] = {"pods": "v1", "namespaces": "v1"}
for _group, (_group_version, _group_resources) in _GROUP_API_RESOURCES.items():
    for _resource in _group_resources:
        _KIND_BY_PLURAL[str(_resource["name"])] = str(_resource["kind"])
        _API_VERSION_BY_PLURAL[str(_resource["name"])] = f"{_group}/{_group_version}"


def _status_body(code: int, message: str) -> dict:
    reasons = {403: "Forbidden", 404: "NotFound", 409: "Conflict", 500: "InternalError"}
    return {
        "apiVersion": "v1",
        "kind": "Status",
        "status": "Failure",
        "message": message,
        "reason": reasons.get(code, "Unknown"),
        "code": code,
    }


def _split_resource_path(path: str):
    """Split a Kubernetes resource path into (plural, namespace, name).

    Returns ``None`` for discovery paths, which the handler answers separately.
    """
    parts = [segment for segment in path.split("/") if segment]
    if not parts:
        return None
    if parts[0] == "api":
        rest = parts[2:]
    elif parts[0] == "apis":
        rest = parts[3:]
    else:
        return None
    if not rest:
        return None
    if rest[0] == "namespaces":
        if len(rest) == 2:
            # /api/v1/namespaces/<name> -- a Namespace object, not a scope.
            return "namespaces", None, rest[1]
        if len(rest) >= 3:
            return rest[2], rest[1], (rest[3] if len(rest) > 3 else None)
        return None
    return rest[0], None, (rest[1] if len(rest) > 1 else None)


class FakeDecommissionAPI:
    """A threaded fake Kubernetes API serving exactly the decommission role's reads.

    DELETE genuinely removes the object from the store, so ``wait: true`` on the
    role's delete tasks converges through the module's real GET polling.
    """

    def __init__(
        self,
        *,
        multiclusterobservabilities: list,
        multiclusterhubs: list,
        managedclusters: list,
        namespaces: list,
        delete_status_by_plural: dict,
        read_status_by_plural: dict,
    ):
        self.store: Dict[str, List[dict]] = {
            "multiclusterobservabilities": copy.deepcopy(multiclusterobservabilities),
            "multiclusterhubs": copy.deepcopy(multiclusterhubs),
            "managedclusters": copy.deepcopy(managedclusters),
            "namespaces": copy.deepcopy(namespaces),
            "clusterdeployments": [],
            "pods": [],
        }
        self.delete_status_by_plural = dict(delete_status_by_plural)
        self.read_status_by_plural = dict(read_status_by_plural)
        self._requests: List[dict] = []
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    @property
    def requests(self) -> list:
        with self._lock:
            return copy.deepcopy(self._requests)

    @property
    def delete_calls(self) -> list:
        return [request for request in self.requests if request["method"] == "DELETE"]

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _record(self, method: str, path: str) -> None:
        with self._lock:
            self._requests.append({"method": method, "path": path})

    def _select(self, plural: str, namespace, name):
        items = self.store.get(plural, [])
        selected = []
        for item in items:
            metadata = item.get("metadata", {})
            if namespace is not None and metadata.get("namespace") != namespace:
                continue
            if name is not None and metadata.get("name") != name:
                continue
            selected.append(item)
        return selected

    def _discovery_payload(self, path: str):
        if path == "/version":
            return {"major": "1", "minor": "28", "gitVersion": "v1.28.0"}
        if path == "/api":
            return {"kind": "APIVersions", "versions": ["v1"], "serverAddressByClientCIDRs": []}
        if path == "/api/v1":
            return {
                "kind": "APIResourceList",
                "groupVersion": "v1",
                "resources": copy.deepcopy(_CORE_API_RESOURCES),
            }
        if path == "/apis":
            return {
                "kind": "APIGroupList",
                "groups": [
                    {
                        "name": group,
                        "versions": [{"groupVersion": f"{group}/{version}", "version": version}],
                        "preferredVersion": {"groupVersion": f"{group}/{version}", "version": version},
                    }
                    for group, (version, _) in _GROUP_API_RESOURCES.items()
                ],
            }
        for group, (version, resources) in _GROUP_API_RESOURCES.items():
            if path == f"/apis/{group}/{version}":
                return {
                    "kind": "APIResourceList",
                    "groupVersion": f"{group}/{version}",
                    "resources": copy.deepcopy(resources),
                }
        return None

    def _handler(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def _write_json(self, payload: dict, status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _path(self) -> str:
                return unquote(urlsplit(self.path).path)

            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
                path = self._path()
                api._record("GET", path)
                payload = api._discovery_payload(path)
                if payload is not None:
                    self._write_json(payload)
                    return
                parsed = _split_resource_path(path)
                if parsed is None:
                    self._write_json(_status_body(404, f"unhandled path {path}"), status=404)
                    return
                plural, namespace, name = parsed
                if plural not in api.store:
                    self._write_json(_status_body(404, f"unknown resource {plural}"), status=404)
                    return
                read_status = api.read_status_by_plural.get(plural, 200)
                if read_status != 200:
                    self._write_json(
                        _status_body(read_status, f"fixture refused GET of {plural}"),
                        status=read_status,
                    )
                    return
                selected = api._select(plural, namespace, name)
                if name is not None:
                    if not selected:
                        self._write_json(_status_body(404, f"{plural} {name} not found"), status=404)
                        return
                    self._write_json(copy.deepcopy(selected[0]))
                    return
                self._write_json(
                    {
                        "apiVersion": _API_VERSION_BY_PLURAL[plural],
                        "kind": f"{_KIND_BY_PLURAL[plural]}List",
                        "metadata": {"resourceVersion": "1"},
                        "items": copy.deepcopy(selected),
                    }
                )

            def do_DELETE(self):  # noqa: N802 - BaseHTTPRequestHandler API
                path = self._path()
                api._record("DELETE", path)
                parsed = _split_resource_path(path)
                if parsed is None:
                    self._write_json(_status_body(404, f"unhandled path {path}"), status=404)
                    return
                plural, namespace, name = parsed
                status = api.delete_status_by_plural.get(plural, 200)
                if status != 200:
                    self._write_json(_status_body(status, f"fixture refused DELETE of {plural}/{name}"), status=status)
                    return
                selected = api._select(plural, namespace, name)
                if not selected:
                    self._write_json(_status_body(404, f"{plural} {name} not found"), status=404)
                    return
                victim = selected[0]
                with api._lock:
                    api.store[plural] = [item for item in api.store[plural] if item is not victim]
                self._write_json(copy.deepcopy(victim))

        return Handler


_HARNESS_CALLBACK_SOURCE = '''
from __future__ import absolute_import, division, print_function

__metaclass__ = type

import json
import os

from ansible.plugins.callback import CallbackBase

DOCUMENTATION = """
    name: harness_record
    type: aggregate
    short_description: Record every task result as JSON lines.
    description:
      - Test-only callback used by run_decommission_role to capture the real
        per-task outcome of the decommission role.
    requirements: []
"""


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "harness_record"
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self, *args, **kwargs):
        super(CallbackModule, self).__init__(*args, **kwargs)
        self._record_path = os.environ["ACM_HARNESS_RECORD_PATH"]

    def _emit(self, result, changed, skipped, failed):
        task = result._task
        raw = result._result or {}
        entry = {
            "name": task.name or task.action,
            "module": getattr(task, "resolved_action", None) or task.action,
            "changed": bool(changed),
            "skipped": bool(skipped),
            "failed": bool(failed),
            "result": raw,
        }
        with open(self._record_path, "a") as handle:
            handle.write(json.dumps(entry, default=str) + "\\n")

    def v2_runner_on_ok(self, result):
        self._emit(result, (result._result or {}).get("changed", False), False, False)

    def v2_runner_on_skipped(self, result):
        self._emit(result, False, True, False)

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._emit(result, (result._result or {}).get("changed", False), False, True)

    def v2_runner_on_unreachable(self, result):
        self._emit(result, False, False, True)
'''

_VALID_OUTCOMES = ("not_requested", "precondition_noop", "completed", "refused", "failed")


def _reject_refused(family: str, outcome: str) -> None:
    if outcome == "refused":
        raise ValueError(
            f"{family}: 'refused' is unreachable in the collection role. The role is "
            "non-interactive behind its confirmed-gate, so no substep can be declined."
        )


def _check_outcome(family: str, outcome: str) -> None:
    if outcome not in _VALID_OUTCOMES:
        raise ValueError(f"{family}: unknown outcome {outcome!r}; expected one of {_VALID_OUTCOMES}")
    _reject_refused(family, outcome)


def _conflict(option: str, outcome: str, wanted) -> None:
    raise ValueError(f"{option}={wanted!r} conflicts with the requested outcome {outcome!r}")


def _mco_object(name: str) -> dict:
    return {
        "apiVersion": "observability.open-cluster-management.io/v1beta2",
        "kind": "MultiClusterObservability",
        "metadata": {"name": name, "resourceVersion": "1"},
    }


def _mch_object(name: str) -> dict:
    return {
        "apiVersion": "operator.open-cluster-management.io/v1",
        "kind": "MultiClusterHub",
        "metadata": {"name": name, "namespace": "open-cluster-management", "resourceVersion": "1"},
    }


def _managed_cluster_object(name: str) -> dict:
    return {
        "apiVersion": "cluster.open-cluster-management.io/v1",
        "kind": "ManagedCluster",
        "metadata": {"name": name, "resourceVersion": "1"},
    }


def _namespace_object(name: str) -> dict:
    return {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": name, "resourceVersion": "1"}}


def run_decommission_role(
    *,
    check_mode: bool = False,
    execution_mode: str = "execute",
    observability_outcome: Optional[str] = None,
    managed_clusters_outcome: Optional[str] = None,
    multiclusterhub_outcome: Optional[str] = None,
    mco_present: Optional[bool] = None,
    mch_present: Optional[bool] = None,
    managed_clusters: Optional[List[str]] = None,
    observability_namespace: str = "present",
    observability_read_status: int = 200,
    checkpoint_available: bool = True,
) -> dict:
    """Run the decommission role against declared fakes and return one canonical result.

    Every option is keyword-only and explicitly declared: an unrecognised keyword
    raises ``TypeError`` so PRs C, D and E must extend this helper deliberately
    rather than silently receiving a no-op fake.
    """
    import shutil
    import subprocess
    import tempfile

    from ansible_collections.tomazb.acm_switchover.tests.conftest import (
        _ansible_env,
        _write_fixture_kubeconfig,
    )

    if execution_mode not in ("execute", "validate", "dry_run"):
        raise ValueError(f"execution_mode={execution_mode!r} is not one of ('execute', 'validate', 'dry_run')")
    if observability_namespace not in ("present", "absent"):
        raise ValueError(f"observability_namespace={observability_namespace!r} must be 'present' or 'absent'")
    if observability_read_status != 200 and observability_outcome is not None:
        raise ValueError(
            f"observability_read_status={observability_read_status!r} conflicts with "
            f"observability_outcome={observability_outcome!r}: a failed read never reaches an outcome"
        )

    # --- observability family -------------------------------------------------
    obs_delete_status = 200
    has_observability: Union[bool, str] = "auto"
    if observability_outcome is not None:
        _check_outcome("observability_outcome", observability_outcome)
        if observability_outcome == "not_requested":
            if mco_present:
                _conflict("mco_present", observability_outcome, mco_present)
            has_observability = False
            mco_present = False
        elif observability_outcome == "precondition_noop":
            if mco_present:
                _conflict("mco_present", observability_outcome, mco_present)
            if observability_namespace == "absent":
                _conflict("observability_namespace", observability_outcome, observability_namespace)
            mco_present = False
        else:
            if mco_present is False:
                _conflict("mco_present", observability_outcome, mco_present)
            if observability_namespace == "absent":
                _conflict("observability_namespace", observability_outcome, observability_namespace)
            mco_present = True
            if observability_outcome == "failed":
                obs_delete_status = 500
    if mco_present is None:
        mco_present = True

    # --- managed cluster family ----------------------------------------------
    managed_clusters_delete_status = 200
    if managed_clusters_outcome is not None:
        _check_outcome("managed_clusters_outcome", managed_clusters_outcome)
        if managed_clusters_outcome == "not_requested":
            raise ValueError(
                "managed_clusters_outcome='not_requested' is unreachable: the role includes "
                "delete_managed_clusters.yml unconditionally."
            )
        if managed_clusters_outcome == "precondition_noop":
            if managed_clusters:
                _conflict("managed_clusters", managed_clusters_outcome, managed_clusters)
            managed_clusters = []
        else:
            if managed_clusters == []:
                _conflict("managed_clusters", managed_clusters_outcome, managed_clusters)
            if managed_clusters is None:
                managed_clusters = ["cluster-a"]
            if managed_clusters_outcome == "failed":
                managed_clusters_delete_status = 500
    if managed_clusters is None:
        managed_clusters = ["cluster-a"]

    # --- multiclusterhub family ----------------------------------------------
    mch_delete_status = 200
    if multiclusterhub_outcome is not None:
        _check_outcome("multiclusterhub_outcome", multiclusterhub_outcome)
        if multiclusterhub_outcome == "not_requested":
            raise ValueError(
                "multiclusterhub_outcome='not_requested' is unreachable: the role includes "
                "delete_multiclusterhub.yml unconditionally."
            )
        if multiclusterhub_outcome == "precondition_noop":
            if mch_present:
                _conflict("mch_present", multiclusterhub_outcome, mch_present)
            mch_present = False
        else:
            if mch_present is False:
                _conflict("mch_present", multiclusterhub_outcome, mch_present)
            mch_present = True
            if multiclusterhub_outcome == "failed":
                mch_delete_status = 500
    if mch_present is None:
        mch_present = True

    namespaces = [_namespace_object("open-cluster-management")]
    if observability_namespace == "present":
        namespaces.append(_namespace_object("open-cluster-management-observability"))

    repo_root = ROLES_DIR.parents[3]
    workspace = pathlib.Path(tempfile.mkdtemp(prefix="acm-decommission-harness-"))
    api = FakeDecommissionAPI(
        multiclusterobservabilities=[_mco_object("observability")] if mco_present else [],
        multiclusterhubs=[_mch_object("multiclusterhub")] if mch_present else [],
        managedclusters=[_managed_cluster_object("local-cluster")]
        + [_managed_cluster_object(name) for name in managed_clusters],
        namespaces=namespaces,
        delete_status_by_plural={
            "multiclusterobservabilities": obs_delete_status,
            "managedclusters": managed_clusters_delete_status,
            "multiclusterhubs": mch_delete_status,
        },
        read_status_by_plural={"multiclusterobservabilities": observability_read_status},
    )
    try:
        kubeconfig = workspace / "primary.kubeconfig"
        _write_fixture_kubeconfig(kubeconfig, "primary-hub", api.url)

        summary_path = workspace / "decommission-summary.json"
        checkpoint_path = workspace / "checkpoint.json"
        seeded_operational_data = {"harness_seed": "unchanged"}
        if checkpoint_available:
            checkpoint_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "phase": "finalization",
                        "completed_phases": ["preflight"],
                        "operational_data": seeded_operational_data,
                        "errors": [],
                        "report_refs": [],
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

        execution: Dict[str, Any] = {"mode": execution_mode, "report_dir": str(workspace / "artifacts")}
        if checkpoint_available:
            execution["checkpoint"] = {"enabled": True, "backend": "file", "path": str(checkpoint_path)}
        vars_payload = {
            "acm_switchover_hubs": {"primary": {"context": "primary-hub", "kubeconfig": str(kubeconfig)}},
            "acm_switchover_execution": execution,
            "acm_switchover_features": {"skip_rbac_validation": True},
            "acm_switchover_decommission": {
                "confirmed": True,
                "interactive": False,
                "has_observability": has_observability,
            },
        }
        vars_file = workspace / "vars.yml"
        vars_file.write_text(yaml.safe_dump(vars_payload, sort_keys=False), encoding="utf-8")

        playbook_path = workspace / "decommission-harness.yml"
        playbook_path.write_text(
            yaml.safe_dump(
                [
                    {
                        "hosts": "localhost",
                        "connection": "local",
                        "gather_facts": False,
                        "tasks": [
                            {
                                "name": f"{_HARNESS_TASK_PREFIX}run the decommission role",
                                "ansible.builtin.include_role": {"name": "tomazb.acm_switchover.decommission"},
                            }
                        ],
                    }
                ],
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        callback_dir = workspace / "callback_plugins"
        callback_dir.mkdir(parents=True, exist_ok=True)
        (callback_dir / "harness_record.py").write_text(_HARNESS_CALLBACK_SOURCE, encoding="utf-8")
        record_path = workspace / "task-records.jsonl"

        before_operational_data = _read_operational_data(checkpoint_path)

        env = _ansible_env(repo_root, workspace)
        env.update(
            {
                "ANSIBLE_CALLBACK_PLUGINS": str(callback_dir),
                "ANSIBLE_CALLBACKS_ENABLED": "harness_record",
                "ACM_HARNESS_RECORD_PATH": str(record_path),
                "ANSIBLE_RETRY_FILES_ENABLED": "0",
                "PWD": str(repo_root),
            }
        )

        command = [
            "ansible-playbook",
            str(playbook_path),
            "-i",
            "ansible_collections/tomazb/acm_switchover/examples/inventory.yml",
            "-e",
            f"@{vars_file}",
            "-e",
            f"summary_path={summary_path}",
        ]
        if check_mode:
            command.append("--check")
        completed = subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=600,
        )

        records = []
        if record_path.exists():
            for line in record_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    records.append(json.loads(line))
        if not records:
            raise RuntimeError(
                "run_decommission_role captured no task results.\n"
                f"returncode={completed.returncode}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )

        facts: Dict[str, Any] = {}
        for entry in records:
            ansible_facts = entry.get("result", {}).get("ansible_facts")
            if isinstance(ansible_facts, dict):
                facts.update(ansible_facts)

        summary = facts.get("acm_switchover_decommission_result", {})
        if summary_path.exists():
            published = json.loads(summary_path.read_text(encoding="utf-8"))
            if published != summary:
                raise AssertionError(
                    "the published decommission artifact does not match the role fact it claims to "
                    f"publish.\nartifact: {published!r}\nfact: {summary!r}"
                )

        return {
            "tasks": [entry for entry in records if not entry["name"].startswith(_HARNESS_TASK_PREFIX)],
            "facts": facts,
            "acm_switchover_decommission_result": summary,
            "checkpoint": {
                "operational_data": _read_operational_data(checkpoint_path),
                "before_operational_data": before_operational_data,
                "phases": [entry for entry in records if entry["module"] == "tomazb.acm_switchover.checkpoint_phase"],
            },
            "delete_calls": api.delete_calls,
            "gate": None,
            # Not in the B4.1 mapping. Without it "the play still fails" is asserted
            # nowhere, and a refactor that swallowed the failure would go unnoticed.
            "returncode": completed.returncode,
        }
    finally:
        api.close()
        shutil.rmtree(workspace, ignore_errors=True)


def _read_operational_data(checkpoint_path) -> dict:
    if not checkpoint_path.exists():
        return {}
    try:
        return json.loads(checkpoint_path.read_text(encoding="utf-8")).get("operational_data", {})
    except ValueError:
        return {}


def test_every_substep_publishes_an_outcome():
    """Each substep file must SET the shared outcome fact, not merely mention it.

    The brief's ``in str(task)`` form would be satisfied by a debug message that
    names the fact; this requires a real ``set_fact`` whose target key is the
    outcome mapping, which is what the aggregation actually reads.
    """
    for substep in _OUTCOME_FAMILIES:
        publishers = outcome_recording_tasks({substep: decommission_task_files[substep]})
        assert publishers, f"{substep} must publish an outcome into acm_switchover_decommission_outcomes"


def test_every_recorded_outcome_value_is_in_the_vocabulary():
    """EVERY branch of EVERY recording task, not just one branch per task.

    An earlier version checked "does this task mention any vocabulary member", which a
    single mistyped branch survived: changing ``'completed'`` to ``'complete'`` in
    delete_observability.yml left it green because ``'precondition_noop'`` still matched.
    """
    recorders = outcome_recording_tasks(decommission_task_files)
    assert len(recorders) >= 5, "expected one recorder per family plus not_requested and the three rescues"
    for task in recorders:
        values = recorded_outcome_values(task)
        assert values, f"{task.get('name')!r} records no outcome value at all"
        unknown = values - set(DECOMMISSION_SUBSTEP_OUTCOMES)
        assert not unknown, f"{task.get('name')!r} records {sorted(unknown)}, which is outside the vocabulary"


def test_every_role_task_file_is_covered_by_the_guardrails():
    """A task file added later must not sit outside every contract test in this module."""
    assert _ROLE_TASK_FILE_NAMES == {
        DECOMMISSION_MAIN.name,
        DELETE_OBSERVABILITY.name,
        DELETE_MANAGED_CLUSTERS.name,
        DELETE_MCH.name,
        VALIDATE_RBAC.name,
    }, "a decommission role task file exists that decommission_task_files does not parse"


def test_no_substep_records_the_refused_outcome():
    """`refused` is unreachable: the role is non-interactive behind its confirmed-gate."""
    for tasks in decommission_task_files.values():
        for task in tasks:
            assert "'refused'" not in str(task.get("ansible.builtin.set_fact", ""))


def test_every_mutating_task_is_check_mode_guarded():
    """Native check mode must issue no cluster mutation (amendment section 14).

    A module in CHECK_MODE_NATIVE_MODULES is exempt: section 14.2 requires
    ``acm_uid_guarded_delete`` to RUN in check mode, stopping after the live read
    and UID validation and returning ``changed: false`` with an explicit
    ``would_change``. Guarding it with ``not ansible_check_mode`` would defeat the
    prediction PR C is required to produce.
    """
    mutators = [
        task
        for task in mutating_tasks(decommission_task_files)
        if not CHECK_MODE_NATIVE_MODULES.intersection(_task_actions(task))
    ]
    assert mutators, "the decommission role must have at least one mutating task"
    for task in mutators:
        assert "not ansible_check_mode" in _when_text(task), (
            f"mutating task {task.get('name')!r} must be guarded by 'not ansible_check_mode' — "
            "check mode may neither mutate the cluster nor report a prospective change"
        )


def test_summary_artifact_writer_is_check_mode_guarded():
    """The artifact writer persists a file and reports check-mode `changed: true` if it runs."""
    writer = task_named(decommission_task_files["main"], "Write decommission summary when requested")
    assert "not ansible_check_mode" in _when_text(writer)


def test_no_checkpoint_writer_exists_at_the_b_stage():
    """PR B adds no checkpoint phase to the role; Task B5 owns that wiring."""
    assert checkpoint_writer_tasks(decommission_task_files) == []


def test_actual_change_is_never_derived_from_check_mode():
    """The published `changed` must carry the check-mode exclusion in the YAML itself."""
    publish = task_named(decommission_task_files["main"], "Publish decommission result")
    changed = str(publish["ansible.builtin.set_fact"]["acm_switchover_decommission_result"]["changed"])
    assert "not ansible_check_mode" in changed


def test_b_stage_publishes_no_prediction():
    """`would_change` is a separate key and is explicitly false at the B stage."""
    publish = task_named(decommission_task_files["main"], "Publish decommission result")
    result = publish["ansible.builtin.set_fact"]["acm_switchover_decommission_result"]
    assert result["would_change"] is False


def test_summary_keeps_its_published_artifact_keys():
    """`phase`, `mode` and `has_observability` are consumed by existing integration tests."""
    publish = task_named(decommission_task_files["main"], "Publish decommission result")
    result = publish["ansible.builtin.set_fact"]["acm_switchover_decommission_result"]
    for key in ("phase", "mode", "has_observability", "status", "substeps", "changed", "would_change"):
        assert key in result, f"the published artifact must carry {key!r}"


def test_run_decommission_role_rejects_unrecognised_options():
    """PRs C, D and E must extend the harness deliberately, not get a silent no-op."""
    import pytest

    with pytest.raises(TypeError):
        run_decommission_role(destination_mco="present")  # type: ignore[call-arg]


def test_a_failed_substep_produces_a_failed_status():
    result = run_decommission_role(observability_outcome="failed")
    assert result["acm_switchover_decommission_result"]["status"] == "fail"
    assert result["returncode"] != 0, "a failed substep must still fail the play"


def test_a_failed_substep_stops_the_remaining_substeps():
    """A failed substep aborts the run; downstream families are never attempted."""
    result = run_decommission_role(observability_outcome="failed")
    substeps = result["acm_switchover_decommission_result"]["substeps"]
    assert substeps == {"observability": "failed"}
    assert [task for task in result["tasks"] if task["failed"]], "the failure must reach the task record"
    assert result["returncode"] != 0
    assert [call for call in result["delete_calls"] if "multiclusterobservabilities" in call["path"]]
    assert not [call for call in result["delete_calls"] if "managedclusters" in call["path"]]
    assert not [call for call in result["delete_calls"] if "multiclusterhubs" in call["path"]]


def test_a_failed_managed_cluster_substep_fails_the_run():
    """The MC rescue must behave like the observability one, including partial change."""
    result = run_decommission_role(managed_clusters_outcome="failed")
    summary = result["acm_switchover_decommission_result"]
    assert summary["status"] == "fail"
    assert summary["substeps"] == {"observability": "completed", "managed_clusters": "failed"}
    # The observability delete really happened: a partially torn-down hub is `changed`
    # AND unsuccessful, and the artifact must say both.
    assert summary["changed"] is True
    assert result["returncode"] != 0
    assert not [call for call in result["delete_calls"] if "multiclusterhubs" in call["path"]]


def test_a_failed_multiclusterhub_substep_fails_the_run():
    result = run_decommission_role(multiclusterhub_outcome="failed")
    summary = result["acm_switchover_decommission_result"]
    assert summary["status"] == "fail"
    assert summary["substeps"] == {
        "observability": "completed",
        "managed_clusters": "completed",
        "multiclusterhub": "failed",
    }
    assert summary["changed"] is True
    assert result["returncode"] != 0


def test_a_check_mode_read_failure_is_never_published_as_pass():
    """The honesty bug B4 exists to fix, in the mode that records no outcome.

    A 403 listing MultiClusterObservability rescues the family. Check mode records no
    outcome, so the outcome map stays empty -- and an aggregation that read only that
    map would publish `status: pass` for a preview that actually failed.
    """
    result = run_decommission_role(check_mode=True, observability_read_status=403)
    summary = result["acm_switchover_decommission_result"]
    assert summary["substeps"] == {}
    assert summary["status"] == "fail"
    assert summary["changed"] is False
    assert result["returncode"] != 0
    assert result["delete_calls"] == []


def test_outcome_values_come_from_the_collection_constants():
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils import constants

    result = run_decommission_role(observability_outcome="precondition_noop")
    assert result["acm_switchover_decommission_result"]["substeps"]["observability"] == "precondition_noop"
    assert (
        result["acm_switchover_decommission_result"]["substeps"]["observability"]
        in constants.DECOMMISSION_SUBSTEP_OUTCOMES
    )


def test_a_clean_execute_run_reports_the_real_completed_outcomes():
    """The honest positive case: every family really ran and the artifact says so."""
    result = run_decommission_role()
    summary = result["acm_switchover_decommission_result"]
    assert result["returncode"] == 0
    assert summary["status"] == "pass"
    assert summary["substeps"] == {
        "observability": "completed",
        "managed_clusters": "completed",
        "multiclusterhub": "completed",
    }
    assert summary["changed"] is True
    assert summary["would_change"] is False
    assert len(result["delete_calls"]) == 3


def test_a_skipped_observability_family_is_recorded_as_not_requested():
    result = run_decommission_role(observability_outcome="not_requested")
    summary = result["acm_switchover_decommission_result"]
    assert summary["substeps"]["observability"] == "not_requested"
    assert summary["has_observability"] is False
    assert not [call for call in result["delete_calls"] if "multiclusterobservabilities" in call["path"]]


def test_b_stage_check_mode_reports_no_actual_or_speculative_change():
    result = run_decommission_role(check_mode=True)
    summary = result["acm_switchover_decommission_result"]
    assert summary["changed"] is False
    assert summary["would_change"] is False
    assert not [task for task in result["tasks"] if task["changed"]]


def test_b_stage_check_mode_writes_no_checkpoint_or_outcome():
    """At the B stage only the ``substeps == {}`` conjunct discriminates.

    The role contains no checkpoint writer yet (Task B5 adds one), so an execute run
    and a failed run also leave ``operational_data`` untouched and ``phases`` empty.
    Those two conjuncts are kept as the assertion B5 inherits -- once the writer exists
    they become real -- but they must not be read as coverage today.
    ``test_no_checkpoint_writer_exists_at_the_b_stage`` is what actually pins their
    premise, and the discriminating rule here is that check mode records no outcome.
    """
    result = run_decommission_role(check_mode=True)
    checkpoint = result["checkpoint"]
    assert checkpoint["operational_data"] == checkpoint["before_operational_data"]
    assert checkpoint["phases"] == []
    assert result["acm_switchover_decommission_result"]["substeps"] == {}


def test_b_stage_check_mode_issues_no_delete():
    result = run_decommission_role(check_mode=True)
    assert result["delete_calls"] == []


def test_dry_run_records_no_substep_outcome():
    """A dry run previews; a later live run must trust nothing it observed."""
    result = run_decommission_role(execution_mode="dry_run")
    summary = result["acm_switchover_decommission_result"]
    assert summary["substeps"] == {}
    assert summary["status"] == "pass"
    assert summary["changed"] is False
    assert result["delete_calls"] == []
