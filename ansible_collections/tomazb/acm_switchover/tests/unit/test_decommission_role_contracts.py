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

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    KNOWN_PHASES,
    build_operation_identity,
    reset_completed_phases_from,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    DECOMMISSION_SUBSTEP_OUTCOMES,
)

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
PLAYBOOKS_DIR = pathlib.Path(__file__).resolve().parents[2] / "playbooks"
DECOMMISSION_PLAYBOOK = PLAYBOOKS_DIR / "decommission.yml"
DECOMMISSION_MAIN = ROLES_DIR / "decommission" / "tasks" / "main.yml"
DELETE_OBSERVABILITY = ROLES_DIR / "decommission" / "tasks" / "delete_observability.yml"
DELETE_MANAGED_CLUSTERS = ROLES_DIR / "decommission" / "tasks" / "delete_managed_clusters.yml"
DELETE_MCH = ROLES_DIR / "decommission" / "tasks" / "delete_multiclusterhub.yml"
VALIDATE_RBAC = ROLES_DIR / "decommission" / "tasks" / "validate_rbac.yml"
DECOMMISSION_TASKS_DIR = ROLES_DIR / "decommission" / "tasks"

#: The collection modules that implement native check mode, as verified by a test in
#: THIS repository. ``acm_uid_guarded_delete`` is deliberately ABSENT: it does not
#: exist yet, and "native check mode" cannot be assumed -- with the role's three
#: ``not ansible_check_mode`` guards removed, ``kubernetes.core.k8s`` under ``--check``
#: issued three real DELETEs to the fake API. PR C must add the module, this exemption,
#: and a module-level test proving it issues no DELETE in check mode, in the same PR --
#: the same rule as the harness raising on an unrecognised keyword. PR E adds
#: ``acm_pod_owner_classify`` on the same terms.
CHECK_MODE_NATIVE_MODULES = frozenset(
    {
        "tomazb.acm_switchover.acm_k8s_read_outcome",
        "tomazb.acm_switchover.acm_uid_guarded_delete",
    }
)

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

#: The task that refuses an execute-mode decommission without durable checkpoint state.
_CHECKPOINT_GATE_TASK_NAME = "Require durable checkpoint state before the first decommission delete"


def _include_file(task: dict) -> str:
    """Extract filename from ansible.builtin.include_tasks regardless of string vs dict form."""
    val = task.get("ansible.builtin.include_tasks", "")
    if isinstance(val, str):
        return val
    return val.get("file", "") if isinstance(val, dict) else ""


def _load_tasks(path: pathlib.Path) -> list:
    """Parse one role task file, flattening block/rescue/always into one ordered list."""
    return _flatten_tasks(yaml.safe_load(path.read_text()) or [])


#: The task files B4.1 names, under the keys B4.1 gives them.
_NAMED_TASK_FILES = {
    "main": DECOMMISSION_MAIN,
    "observability": DELETE_OBSERVABILITY,
    "managed_clusters": DELETE_MANAGED_CLUSTERS,
    "multiclusterhub": DELETE_MCH,
    "validate_rbac": VALIDATE_RBAC,
}


def _role_task_file_paths() -> list:
    """Every YAML task file in the role, at any depth and with either YAML suffix.

    ``rglob`` and both suffixes are deliberate: an ``iterdir``/``*.yml`` form let a
    ``probe_new.yaml`` and a ``tasks/sub/foo.yml`` pass the whole suite. Ruling C15
    closed the identical hole in the Task B2 checkpoint guardrail; this is the same
    hole class, fixed in the same general form.
    """
    return sorted(path for path in DECOMMISSION_TASKS_DIR.rglob("*") if path.suffix in {".yml", ".yaml"})


#: EVERY decommission role task file, parsed and flattened once. ``validate_rbac`` is
#: included so a mutating task added there cannot evade the guardrails, and any file
#: not named by B4.1 is parsed under its path relative to ``tasks/`` -- so a task file
#: added later is inside every guardrail from the moment it exists, not from the moment
#: someone remembers to register it here.
decommission_task_files = {name: _load_tasks(path) for name, path in _NAMED_TASK_FILES.items()}
for _extra_path in _role_task_file_paths():
    if _extra_path not in _NAMED_TASK_FILES.values():
        decommission_task_files[str(_extra_path.relative_to(DECOMMISSION_TASKS_DIR))] = _load_tasks(_extra_path)


def decommission_playbook_tasks() -> list:
    """The standalone playbook's own task list, RAW -- nested blocks not expanded.

    ``.get("tasks", [])`` rather than ``play["tasks"]`` is deliberate: a playbook
    still in the bare ``roles:`` form has no ``tasks`` key, and a ``KeyError`` would
    turn an expected assertion failure into a collection ERROR.

    This is a DISTINCT parsed surface from ``decommission_task_files``, which stays
    scoped to the shared role. The playbook owns the standalone phase lifecycle
    (section 10.3.5a); the role owns none of it.

    Every play is scanned, and ``pre_tasks`` and ``post_tasks`` alongside ``tasks``.
    Scanning only ``plays[0]["tasks"]`` would let an ordinary, non-standalone
    checkpoint transition be added to ``pre_tasks:`` or to a second play and escape
    every lifecycle guard below.
    """
    plays = yaml.safe_load(DECOMMISSION_PLAYBOOK.read_text()) or []
    collected: list = []
    for play in plays:
        if not isinstance(play, dict):
            continue
        for section in ("pre_tasks", "tasks", "post_tasks"):
            collected.extend(play.get(section) or [])
    return collected


def all_checkpoint_phase_tasks(tasks: list) -> list:
    """Every ``checkpoint_phase`` task, descending into block/rescue/always.

    Matches the FQCN, because ``index_of_task_using`` compares raw task keys exactly
    (``_task_actions`` returns ``task`` keys, and membership is ``in`` on that list).
    A short-name match would silently find nothing.
    """
    return [task for task in _flatten_tasks(tasks) if _CHECKPOINT_WRITER_MODULE in _task_actions(task)]


def rescue_tasks_of(tasks: list) -> list:
    """The flattened task list of every ``rescue:`` in ``tasks``."""
    rescued: list = []
    for task in _flatten_tasks(tasks):
        if "rescue" in task:
            rescued.extend(_flatten_tasks(task["rescue"]))
    return rescued


def _status_of(task: dict) -> str:
    """The ``status`` argument of a ``checkpoint_phase`` task."""
    return task.get(_CHECKPOINT_WRITER_MODULE, {}).get("status", "")


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
    return [
        task
        for tasks in task_files.values()
        for task in tasks
        if _CHECKPOINT_WRITER_MODULE in _task_actions(task)
        and not task[_CHECKPOINT_WRITER_MODULE].get("read_facts", False)
    ]


def mutating_tasks(task_files: dict) -> list:
    """Every parsed task that can mutate the cluster OR the controller filesystem.

    Inverted allowlist: a task counts as mutating unless every action it invokes is in
    ``_READ_ONLY_ACTIONS``. Block/rescue/always wrappers invoke no action and are skipped.

    The inversion widens the old denylist on purpose, so read the return value as
    "everything that persists something". It returns ``acm_report_artifact``
    (a controller-side file write, which is why that task carries a check-mode guard)
    and, since Task B5 wired it in, ``tomazb.acm_switchover.checkpoint_phase`` -- the
    retired ``_MUTATING_MODULES`` denylist explicitly excluded that module. It is NOT
    in ``_READ_ONLY_ACTIONS``: it writes durable state, so classifying it read-only to
    quiet ``test_every_mutating_task_is_check_mode_guarded`` would be a lie. B5's two
    transitions satisfy that guardrail by carrying ``not ansible_check_mode``.
    """
    return [
        task
        for tasks in task_files.values()
        for task in tasks
        if _task_actions(task)
        and not set(_task_actions(task)).issubset(_READ_ONLY_ACTIONS)
        and not (_CHECKPOINT_WRITER_MODULE in task and task[_CHECKPOINT_WRITER_MODULE].get("read_facts") is True)
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
    # Case-sensitive vocabulary: a lowercase-only pattern let 'Completed' through,
    # because the task's other branch kept the captured set non-empty and in-vocabulary.
    return set(re.findall(r"'([A-Za-z_][A-Za-z0-9_]*)'", recorded)) - set(_OUTCOME_FAMILIES)


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

    def test_observability_substep_always_loads_its_durable_record(self):
        """A stale discovery flag cannot hide a pending recorded obligation."""
        include_tasks = [t for t in self.tasks if "ansible.builtin.include_tasks" in t]
        obs_includes = [t for t in include_tasks if _include_file(t) == "delete_observability.yml"]
        assert obs_includes, "decommission/tasks/main.yml must include delete_observability.yml"
        for task in obs_includes:
            assert "when" not in task

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

    def test_delete_goes_through_one_uid_guarded_module(self):
        """A name-only delete can remove a replacement object under the same name."""
        guarded = [task for task in self.tasks if "tomazb.acm_switchover.acm_uid_guarded_delete" in task]
        assert len(guarded) == 1
        assert not [task for task in self.tasks if task.get("kubernetes.core.k8s", {}).get("state") == "absent"]

    def test_guarded_delete_binds_identity_routing_and_bounds(self):
        task = next(task for task in self.tasks if "tomazb.acm_switchover.acm_uid_guarded_delete" in task)
        args = task["tomazb.acm_switchover.acm_uid_guarded_delete"]
        assert args["api_version"] == "observability.open-cluster-management.io/v1beta2"
        assert args["kind"] == "MultiClusterObservability"
        assert args["resource_name"] == "multiclusterobservabilities"
        assert args["name"] == "observability"
        assert "expected_uid" in args
        assert "primary.kubeconfig" in str(args["kubeconfig"])
        assert "primary.context" in str(args["context"])
        for timeout in ("request_timeout", "wait_timeout", "wait_sleep"):
            assert timeout in args
            assert str(args[timeout]).strip()
        assert task.get("no_log") is True
        assert "ansible_check_mode" in str(task.get("check_mode", ""))
        assert "dry_run" in str(task.get("check_mode", ""))

    def test_every_discovery_backed_read_supplies_a_canonical_resource_name(self):
        reads = read_outcome_tasks(self.tasks)
        assert reads
        allowed = {"multiclusterobservabilities", "pods", "namespaces"}
        for task in reads:
            args = task["tomazb.acm_switchover.acm_k8s_read_outcome"]
            assert args.get("resource_name") in allowed

    def test_mco_inventory_read_fails_closed(self):
        read = task_named(self.tasks, "Read the source MultiClusterObservability inventory")
        assert read["tomazb.acm_switchover.acm_k8s_read_outcome"]["read_mode"] == "list"
        failure = task_named(self.tasks, "Fail closed when the source MCO inventory is unverifiable")
        when = _when_text(failure)
        assert "read_status" in when
        assert "ok" in when
        assert "kind_not_served" in when
        assert "not in" in when

    def test_drain_wait_is_selector_scoped_bounded_and_absorbs_no_failure(self):
        wait = task_named(self.tasks, "Wait for observability pods to terminate")
        args = wait["tomazb.acm_switchover.acm_k8s_read_outcome"]
        assert args["read_mode"] == "list"
        assert args["resource_name"] == "pods"
        assert args["label_selectors"] == ["{{ _acm_mco_pod_selector }}"]
        identity = task_named(self.tasks, "Define the fixed MultiClusterObservability teardown identity")
        assert (
            identity["ansible.builtin.set_fact"]["_acm_mco_pod_selector"]
            == "observability.open-cluster-management.io/name=observability"
        )
        assert "retries" in wait and "delay" in wait and "until" in wait
        assert wait.get("failed_when") is not False
        assert "ignore_errors" not in wait
        assert "default([])" not in str(wait.get("until", ""))

    def test_every_mco_checkpoint_writer_is_execute_and_check_mode_guarded(self):
        writers = checkpoint_writer_tasks({"observability": self.tasks})
        assert writers
        for task in writers:
            when = _when_text(task)
            assert "not ansible_check_mode" in when
            assert "execute" in when

    def test_mco_checkpoint_calls_do_not_own_a_phase_lifecycle(self):
        calls = checkpoint_writer_tasks({"observability": self.tasks})
        assert calls
        for task in calls:
            args = task[_CHECKPOINT_WRITER_MODULE]
            assert "phase" not in args
            assert "status" not in args


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
    {
        "name": "pods",
        "singularName": "pod",
        "namespaced": True,
        "kind": "Pod",
        "verbs": ["get", "list", "delete"],
    },
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
        pods: Optional[list] = None,
        named_read_status_by_plural: Optional[dict] = None,
        retain_after_delete_by_plural: Optional[dict] = None,
        post_delete_read_status_by_plural: Optional[dict] = None,
        read_status_sequences_by_plural: Optional[dict] = None,
    ):
        self.store: Dict[str, List[dict]] = {
            "multiclusterobservabilities": copy.deepcopy(multiclusterobservabilities),
            "multiclusterhubs": copy.deepcopy(multiclusterhubs),
            "managedclusters": copy.deepcopy(managedclusters),
            "namespaces": copy.deepcopy(namespaces),
            "clusterdeployments": [],
            "pods": copy.deepcopy(pods or []),
        }
        self.delete_status_by_plural = dict(delete_status_by_plural)
        self.read_status_by_plural = dict(read_status_by_plural)
        self.named_read_status_by_plural = dict(named_read_status_by_plural or {})
        self.retain_after_delete_by_plural = dict(retain_after_delete_by_plural or {})
        self.post_delete_read_status_by_plural = dict(post_delete_read_status_by_plural or {})
        self.read_status_sequences_by_plural = {
            plural: list(statuses) for plural, statuses in (read_status_sequences_by_plural or {}).items()
        }
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

    def _record(self, method: str, path: str, **details) -> None:
        with self._lock:
            self._requests.append({"method": method, "path": path, **details})

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
            return {
                "kind": "APIVersions",
                "versions": ["v1"],
                "serverAddressByClientCIDRs": [],
            }
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
                        "preferredVersion": {
                            "groupVersion": f"{group}/{version}",
                            "version": version,
                        },
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
                api._record("GET", path, query=urlsplit(self.path).query)
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
                if name is None and api.read_status_sequences_by_plural.get(plural):
                    read_status = api.read_status_sequences_by_plural[plural].pop(0)
                else:
                    read_status = (
                        api.named_read_status_by_plural.get(plural, 200)
                        if name is not None
                        else api.read_status_by_plural.get(plural, 200)
                    )
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
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw_body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = None
                expected_uid = (
                    body.get("preconditions", {}).get("uid")
                    if isinstance(body, dict) and isinstance(body.get("preconditions"), dict)
                    else None
                )
                api._record("DELETE", path, body=body, expected_uid=expected_uid)
                parsed = _split_resource_path(path)
                if parsed is None:
                    self._write_json(_status_body(404, f"unhandled path {path}"), status=404)
                    return
                plural, namespace, name = parsed
                status = api.delete_status_by_plural.get(plural, 200)
                if status != 200:
                    self._write_json(
                        _status_body(status, f"fixture refused DELETE of {plural}/{name}"),
                        status=status,
                    )
                    return
                selected = api._select(plural, namespace, name)
                if not selected:
                    self._write_json(_status_body(404, f"{plural} {name} not found"), status=404)
                    return
                victim = selected[0]
                actual_uid = victim.get("metadata", {}).get("uid")
                if expected_uid != actual_uid:
                    self._write_json(
                        _status_body(
                            409,
                            f"UID precondition {expected_uid!r} did not match {actual_uid!r}",
                        ),
                        status=409,
                    )
                    return
                if not api.retain_after_delete_by_plural.get(plural, False):
                    with api._lock:
                        api.store[plural] = [item for item in api.store[plural] if item is not victim]
                if plural in api.post_delete_read_status_by_plural:
                    api.named_read_status_by_plural[plural] = api.post_delete_read_status_by_plural[plural]
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
            "task_args": task.args,
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

_VALID_OUTCOMES = (
    "not_requested",
    "precondition_noop",
    "completed",
    "refused",
    "failed",
)


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


def _mco_object(name: str, uid: str = "mco-uid-1") -> dict:
    return {
        "apiVersion": "observability.open-cluster-management.io/v1beta2",
        "kind": "MultiClusterObservability",
        "metadata": {"name": name, "uid": uid, "resourceVersion": "1"},
    }


def _mch_object(name: str) -> dict:
    return {
        "apiVersion": "operator.open-cluster-management.io/v1",
        "kind": "MultiClusterHub",
        "metadata": {
            "name": name,
            "namespace": "open-cluster-management",
            "resourceVersion": "1",
        },
    }


def _managed_cluster_object(name: str) -> dict:
    return {
        "apiVersion": "cluster.open-cluster-management.io/v1",
        "kind": "ManagedCluster",
        "metadata": {"name": name, "resourceVersion": "1"},
    }


def _observability_pod_object(name: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": "open-cluster-management-observability",
            "resourceVersion": "1",
            "labels": {"observability.open-cluster-management.io/name": "observability"},
        },
    }


#: The canonical TWO-HUB operation identity the harness seeds. Built through the real
#: ``build_operation_identity`` contract rather than hand-written, so a change to the
#: identity schema is reflected here instead of silently drifting. Named at module
#: scope so a test can compare the stored record against it as a whole object after a
#: run, which is what section 10.3.7 item 20's "keeps its two-hub checkpoint identity
#: valid" clause actually requires.
_HARNESS_TWO_HUB_IDENTITY = build_operation_identity(
    hubs={
        "primary": {"context": "primary-hub"},
        "secondary": {"context": "secondary-hub"},
    },
    operation={},
    collection_version="",
    hub_identities={
        "primary": {"cluster_uid": "harness-primary-uid"},
        "secondary": {"cluster_uid": "harness-secondary-uid"},
    },
)


def _namespace_object(name: str, uid: Optional[str] = None) -> dict:
    metadata: Dict[str, Any] = {"name": name, "resourceVersion": "1"}
    if uid is not None:
        metadata["uid"] = uid
    return {"apiVersion": "v1", "kind": "Namespace", "metadata": metadata}


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
    mco_inventory: Optional[List[dict]] = None,
    mco_record: Optional[dict] = None,
    mco_named_read_status: int = 200,
    mco_retain_after_delete: bool = False,
    mco_post_delete_read_status: Optional[int] = None,
    observability_pods: Optional[List[str]] = None,
    pod_read_statuses: Optional[List[int]] = None,
    configured_has_observability: Optional[Union[bool, str]] = None,
    checkpoint_available: bool = True,
    standalone_playbook: bool = False,
    integrated_finalization: bool = False,
    primary_cluster_uid: Optional[str] = "harness-standalone-primary-uid",
    seeded_operation_identity: Optional[dict] = None,
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
    if standalone_playbook and integrated_finalization:
        raise ValueError("standalone_playbook and integrated_finalization are different entry points; pick one")
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
            mco_present = False
            observability_namespace = "absent"
        else:
            if mco_present is False:
                _conflict("mco_present", observability_outcome, mco_present)
            if observability_namespace == "absent":
                _conflict(
                    "observability_namespace",
                    observability_outcome,
                    observability_namespace,
                )
            mco_present = True
            if observability_outcome == "failed":
                obs_delete_status = 500
    if mco_present is None:
        mco_present = True
    if configured_has_observability is not None:
        has_observability = configured_has_observability
    if mco_inventory is not None and mco_present is not True:
        raise ValueError("mco_inventory supplies the inventory and requires mco_present=True")

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
    # The standalone identity read is a real GET of v1/Namespace kube-system. Serving
    # it unconditionally keeps the fake API honest for role-only runs too: if the role
    # ever starts issuing that read, the request log shows it rather than a 404.
    # ``primary_cluster_uid=None`` deliberately serves the namespace WITHOUT a uid, so
    # the empty/malformed-uid refusal can be exercised against a real response.
    namespaces.append(_namespace_object("kube-system", uid=primary_cluster_uid))

    repo_root = ROLES_DIR.parents[3]
    workspace = pathlib.Path(tempfile.mkdtemp(prefix="acm-decommission-harness-"))
    api = FakeDecommissionAPI(
        multiclusterobservabilities=(
            copy.deepcopy(mco_inventory)
            if mco_inventory is not None
            else ([_mco_object("observability")] if mco_present else [])
        ),
        multiclusterhubs=[_mch_object("multiclusterhub")] if mch_present else [],
        managedclusters=[_managed_cluster_object("local-cluster")]
        + [_managed_cluster_object(name) for name in managed_clusters],
        namespaces=namespaces,
        pods=[_observability_pod_object(name) for name in (observability_pods or [])],
        delete_status_by_plural={
            "multiclusterobservabilities": obs_delete_status,
            "managedclusters": managed_clusters_delete_status,
            "multiclusterhubs": mch_delete_status,
        },
        read_status_by_plural={"multiclusterobservabilities": observability_read_status},
        named_read_status_by_plural={"multiclusterobservabilities": mco_named_read_status},
        retain_after_delete_by_plural={"multiclusterobservabilities": mco_retain_after_delete},
        post_delete_read_status_by_plural=(
            {"multiclusterobservabilities": mco_post_delete_read_status}
            if mco_post_delete_read_status is not None
            else {}
        ),
        read_status_sequences_by_plural={"pods": pod_read_statuses or []},
    )
    try:
        kubeconfig = workspace / "primary.kubeconfig"
        _write_fixture_kubeconfig(kubeconfig, "primary-hub", api.url)

        summary_path = workspace / "decommission-summary.json"
        checkpoint_path = workspace / "checkpoint.json"
        seeded_operational_data: Dict[str, Any] = {"harness_seed": "unchanged"}
        if mco_record is not None:
            seeded_operational_data["decommission_teardown_records"] = {
                "observability.open-cluster-management.io/v1beta2/MultiClusterObservability//observability": copy.deepcopy(
                    mco_record
                )
            }
        # A standalone run must establish its own primary-only identity from empty
        # state -- that is the whole acceptance case, and a pre-seeded
        # ``build_operation_identity`` payload is exactly the supplemental-only
        # evidence that let the first runtime attempt reach B6 with the blocker
        # undetected. ``seeded_operation_identity`` exists only so the two-hub
        # downgrade and reset-bypass rows can seed a DELIBERATE established identity.
        if seeded_operation_identity is not None:
            checkpoint_path.write_text(
                json.dumps(
                    {
                        "schema_version": "2.0",
                        "phase": "finalization",
                        "completed_phases": ["preflight"],
                        "operational_data": seeded_operational_data,
                        "operation_identity": seeded_operation_identity,
                        "errors": [],
                        "report_refs": [],
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        elif checkpoint_available and not standalone_playbook:
            # Schema 2.0 with an established operation identity, NOT the schema 1.0
            # record this harness first seeded. Once Task B5 wired `checkpoint_phase`
            # into the role, `is_unsafe_legacy_checkpoint` refused a 1.0 record that
            # carries completed phases, so every execute run failed on the enter. The
            # identity is canonical (`_canonical_established_operation_identity`
            # requires all nine fields, with only `collection_version` allowed empty),
            # so the enter adopts it instead of demanding the preflight barrier.
            checkpoint_path.write_text(
                json.dumps(
                    {
                        "schema_version": "2.0",
                        "phase": "finalization",
                        "completed_phases": ["preflight"],
                        "operational_data": seeded_operational_data,
                        "operation_identity": _HARNESS_TWO_HUB_IDENTITY,
                        "errors": [],
                        "report_refs": [],
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

        execution: Dict[str, Any] = {
            "mode": execution_mode,
            "report_dir": str(workspace / "artifacts"),
        }
        if checkpoint_available:
            execution["checkpoint"] = {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_path),
            }
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
        if integrated_finalization:
            # This is what routes handle_old_hub.yml into its decommission branch.
            # The finalization role's own defaults say `secondary`; extra vars win.
            vars_payload["acm_switchover_operation"] = {
                "restore_only": False,
                "method": "passive",
                "old_hub_action": "decommission",
                "activation_method": "patch",
            }
        vars_file = workspace / "vars.yml"
        vars_file.write_text(yaml.safe_dump(vars_payload, sort_keys=False), encoding="utf-8")

        playbook_path = workspace / "decommission-harness.yml"
        if integrated_finalization:
            # Section 10.3.7 item 20 requires the REAL integrated path to execute:
            # finalization -> handle_old_hub.yml -> include_role decommission. This
            # loads the repository's actual task file through the real finalization
            # role via `tasks_from`; nothing from that file is copied here, so the
            # test tracks the shipped file rather than a snapshot of it.
            playbook_path.write_text(
                yaml.safe_dump(
                    [
                        {
                            "hosts": "localhost",
                            "connection": "local",
                            "gather_facts": False,
                            "tasks": [
                                {
                                    "name": f"{_HARNESS_TASK_PREFIX}run the real handle_old_hub path",
                                    "ansible.builtin.include_role": {
                                        "name": "tomazb.acm_switchover.finalization",
                                        "tasks_from": "handle_old_hub.yml",
                                    },
                                }
                            ],
                        }
                    ],
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
        elif standalone_playbook:
            # The REAL entry point, not a synthetic include. Section 10.3.7 items 14/15
            # are satisfied only by exercising the actual action path this playbook
            # drives; a harness that includes the role alone cannot prove the
            # lifecycle, because the role deliberately owns none of it.
            playbook_path = DECOMMISSION_PLAYBOOK
        else:
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

        discovery_cache_dir = workspace / "discovery-cache"
        discovery_cache_dir.mkdir(parents=True, exist_ok=True)

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
                # Isolate the dynamic client's API discovery cache to THIS run.
                #
                # kubernetes.dynamic caches discovery at
                # `<tempdir>/osrcp-<md5(host)>.json`, keyed by API-server host:port.
                # The fake API binds an ephemeral port, and when the OS recycles a
                # port an earlier test used, the new run finds that earlier run's
                # stale cache in the shared /tmp and skips its `GET /api/v1`
                # discovery request. That silently changes the recorded API
                # footprint, which made
                # `test_the_only_new_api_operation_is_the_primary_kube_system_namespace_get`
                # pass alone and fail in the full lane.
                #
                # An audit that measures real requests has to control what makes
                # those requests vary; filtering discovery out of the comparison
                # would defeat the point of measuring it.
                "TMPDIR": str(discovery_cache_dir),
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
                "completed_phases": _read_completed_phases(checkpoint_path),
                # SKIPPED entries are excluded on purpose: the harness callback emits a
                # record for a skipped task too, so an unfiltered list would report the
                # check-mode-guarded enter/exit as "phases that ran". `phases` means
                # "checkpoint transitions this run actually performed".
                "phases": [
                    entry
                    for entry in records
                    if entry["module"] == "tomazb.acm_switchover.checkpoint_phase"
                    and not entry["skipped"]
                    and not {
                        "read_facts",
                        "teardown_record",
                    }.intersection(entry.get("task_args", {}))
                ],
            },
            "delete_calls": api.delete_calls,
            # The whole request log, so "the identity read happened, exactly N times,
            # and before the first delete" is assertable rather than assumed.
            "requests": api.requests,
            "operation_identity": _read_operation_identity(checkpoint_path),
            "seeded_operation_identity": (
                seeded_operation_identity
                if seeded_operation_identity is not None
                else (_HARNESS_TWO_HUB_IDENTITY if checkpoint_available and not standalone_playbook else None)
            ),
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


def _read_operation_identity(checkpoint_path) -> dict:
    if not checkpoint_path.exists():
        return {}
    try:
        return json.loads(checkpoint_path.read_text(encoding="utf-8")).get("operation_identity") or {}
    except ValueError:
        return {}


def _kube_system_reads(requests: list) -> list:
    """Every live primary identity read: GET of the kube-system Namespace."""
    return [r for r in requests if r["method"] == "GET" and "namespaces/kube-system" in r["path"]]


def _read_completed_phases(checkpoint_path) -> list:
    if not checkpoint_path.exists():
        return []
    try:
        return json.loads(checkpoint_path.read_text(encoding="utf-8")).get("completed_phases", [])
    except ValueError:
        return []


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
    """A task file added later is auto-parsed AND must be classified deliberately.

    Auto-parsing puts a new file inside the guards immediately; this assertion then
    fails so the addition is noticed rather than absorbed silently. It covers ``.yml``
    and ``.yaml`` at any depth -- an ``iterdir``/``*.yml`` form let both a
    ``probe_new.yaml`` and a ``tasks/sub/foo.yml`` evade the entire suite.
    """
    assert set(_role_task_file_paths()) == set(_NAMED_TASK_FILES.values()), (
        "an unregistered decommission role task file exists; it is already parsed into "
        "decommission_task_files, but add it to _NAMED_TASK_FILES so its role is explicit"
    )


def test_no_substep_records_the_refused_outcome():
    """`refused` is unreachable: the role is non-interactive behind its confirmed-gate."""
    for tasks in decommission_task_files.values():
        for task in tasks:
            assert "'refused'" not in str(task.get("ansible.builtin.set_fact", ""))


def test_every_mutating_task_is_check_mode_guarded():
    """Native check mode must persist nothing (amendment section 14).

    A module in ``CHECK_MODE_NATIVE_MODULES`` is exempt, because section 14.2 requires
    a natively check-mode-safe module to keep RUNNING under ``--check`` rather than be
    skipped. Today that set holds only ``acm_k8s_read_outcome``, which is read-only by
    contract and therefore already in ``_READ_ONLY_ACTIONS`` -- so **the exemption is
    currently a no-op**, and it is kept as the seam PR C and PR E extend.

    Extending it is not free: ``acm_uid_guarded_delete`` is deliberately NOT exempt
    here, because it does not exist yet and native check-mode safety is not assumable.
    Removing this role's three guards put three real DELETEs on the fake API under
    ``--check``. PR C adds the module, its exemption, and a module-level test proving it
    issues no DELETE in check mode, in one PR.
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


def test_decommission_is_the_last_known_checkpoint_phase():
    """The role can only enter a phase the action plugin accepts.

    ``checkpoint_phase`` hard-rejects any phase outside ``KNOWN_PHASES``, so the
    role's ``phase: decommission`` depends on this membership. LAST is load-bearing
    too: ``reset_completed_phases_from`` prunes the named phase and everything after
    it, and decommission is downstream of finalization -- inserting it anywhere else
    would make a ``reset_from: finalization`` retain a completed decommission.

    Kill condition: removing ``decommission`` from ``KNOWN_PHASES``, or moving it
    ahead of ``finalization``.
    """
    assert KNOWN_PHASES[-1] == "decommission"
    assert "decommission" not in reset_completed_phases_from(list(KNOWN_PHASES), "finalization")
    assert reset_completed_phases_from(list(KNOWN_PHASES), "decommission") == list(KNOWN_PHASES[:-1])


def test_the_playbook_has_exactly_three_checkpoint_transitions_and_records_nothing():
    """B5 wires the standalone phase and nothing else: enter, pass, fail.

    PRs C, D and E write teardown records INSIDE this phase. Until then a task
    carrying ``operational_data`` would be a record write smuggled into B.

    There is deliberately no ``reset`` transition. The action must ACCEPT
    ``status: reset`` on the standalone path (section 10.3.2), but PR B wires no task
    that issues one, and the B5 Files table forbids adding one here.

    Kill condition: adding a fourth transition, adding a ``reset`` task, changing any
    status, or attaching ``operational_data`` to any of them.
    """
    transitions = all_checkpoint_phase_tasks(decommission_playbook_tasks())
    assert [_status_of(task) for task in transitions] == ["enter", "pass", "fail"]
    assert {task[_CHECKPOINT_WRITER_MODULE]["phase"] for task in transitions} == {"decommission"}
    for task in transitions:
        assert "operational_data" not in task[_CHECKPOINT_WRITER_MODULE]


def test_playbook_enters_the_decommission_phase_before_including_the_role():
    """The identity map must be durable BEFORE the first DELETE, and the role's
    deletes are only reachable through the include.

    Flatten first: section 10.3.5a's playbook nests ``include_role`` inside a
    ``block:``, so the raw play task list is ``[enter, block-parent]`` and a flat walk
    returns -1 for the include. Match the EXACT task key: ``index_of_task_using``
    tests ``action in _task_actions(task)`` and ``_task_actions`` returns the task's
    raw mapping keys, so the short names never match.

    Kill condition: moving the enter below the include, or dropping either.
    """
    tasks = _flatten_tasks(decommission_playbook_tasks())
    enter = index_of_task_using(tasks, "tomazb.acm_switchover.checkpoint_phase")
    include = index_of_task_using(tasks, "ansible.builtin.include_role")
    assert enter != -1 and include != -1 and enter < include


def test_playbook_passes_the_phase_only_after_the_role_succeeds():
    """A successful standalone run records ``status: pass``, and it must sit AFTER
    the include so a failed teardown cannot reach it.

    Kill condition: dropping the pass, or hoisting it above the include.
    """
    tasks = _flatten_tasks(decommission_playbook_tasks())
    passes = [task for task in all_checkpoint_phase_tasks(tasks) if _status_of(task) == "pass"]
    assert passes, "a successful standalone run must record status: pass"
    include = index_of_task_using(tasks, "ansible.builtin.include_role")
    assert include != -1
    assert all(tasks.index(task) > include for task in passes)


def test_playbook_fails_the_phase_from_a_rescue():
    """A failed teardown records ``status: fail`` from a rescue, never a pass.

    Kill condition: moving the fail out of the rescue, or replacing the rescue with
    an ``always`` -- which would record a FAILED teardown as a completed phase.
    """
    rescue = rescue_tasks_of(decommission_playbook_tasks())
    assert [task for task in all_checkpoint_phase_tasks(rescue) if _status_of(task) == "fail"]
    assert not [task for task in all_checkpoint_phase_tasks(rescue) if _status_of(task) == "pass"]


def test_the_playbook_has_no_always_block():
    """An ``always`` completion task records a failed teardown as a completed phase.

    This is the trap the first runtime attempt hit; section 10.3.5a names it.

    Kill condition: adding an ``always:`` to the lifecycle block.
    """
    tasks = _flatten_tasks(decommission_playbook_tasks())
    # Without this the test passes vacuously against a playbook that has no
    # lifecycle at all, which is exactly the state this task must not be green in.
    assert all_checkpoint_phase_tasks(tasks), "the playbook must own a lifecycle to guard"
    assert not [task for task in tasks if "always" in task]


def test_every_standalone_transition_carries_the_explicit_identity_argument():
    """Standalone identity is EXPLICIT, never inferred (section 10.3.1).

    Kill condition: dropping the argument from any transition, or relying on the
    action to infer standalone mode from a missing secondary or from the caller.
    """
    transitions = all_checkpoint_phase_tasks(decommission_playbook_tasks())
    assert transitions, "the standalone playbook must own its checkpoint transitions"
    for task in transitions:
        args = task[_CHECKPOINT_WRITER_MODULE]
        assert args.get("standalone_decommission_identity") is True
        assert args.get("phase") == "decommission"
        assert "identity_barrier" not in args


def test_the_shared_role_owns_no_checkpoint_phase_lifecycle():
    """The shared role may write teardown data but owns no caller lifecycle."""
    calls = []
    for name, tasks in decommission_task_files.items():
        for task in all_checkpoint_phase_tasks(tasks):
            calls.append(task)
            args = task[_CHECKPOINT_WRITER_MODULE]
            assert args.get("read_facts") is True or "teardown_record" in args, name
            assert not {
                "phase",
                "status",
                "standalone_decommission_identity",
                "identity_barrier",
            }.intersection(args)
    assert calls, "the MCO teardown must use the existing checkpoint facade for durable data"


def test_the_checkpoint_availability_gate_precedes_the_first_teardown_include():
    """The fail-closed gate is worthless if it runs after a delete.

    Kill condition: moving the gate below any ``delete_*.yml`` include, or deleting it.
    """
    tasks = decommission_task_files["main"]
    gate = task_named(tasks, _CHECKPOINT_GATE_TASK_NAME)
    assert tasks.index(gate) < index_of_first_include(tasks, "delete_")
    when = _when_text(gate)
    assert "not ansible_check_mode" in when
    assert "execute" in when


def test_every_checkpoint_writer_task_is_guarded_by_check_mode():
    """Check mode must persist NO checkpoint state.

    Scans the PLAYBOOK, which is where the writers now live. Scanning
    ``decommission_task_files`` here would iterate an empty list and pass vacuously,
    because the role owns no checkpoint writer by design.

    Kill condition: dropping ``not ansible_check_mode`` from any transition.
    """
    writers = all_checkpoint_phase_tasks(decommission_playbook_tasks())
    assert writers, "the standalone playbook must own at least one checkpoint transition"
    for task in writers:
        assert "not ansible_check_mode" in _when_text(task)


def test_execute_mode_fails_closed_when_checkpointing_is_unavailable():
    """Execute mode without durable state issues NO delete and fails the play.

    The brief asked for ``result["...decommission_result"]["status"] == "fail"``. The
    gate fires BEFORE the substep block, so the block's ``always`` never publishes the
    result fact and that key does not exist -- exactly as the pre-existing confirmed
    gate behaves. Moving the gate inside the block to obtain a status would be worse:
    B4's status derivation would publish ``pass`` for a play that failed. The failure
    is therefore asserted on the channels that do exist.

    Kill condition: deleting the gate, or weakening it so a missing checkpoint config
    still reaches the deletes.
    """
    result = run_decommission_role(checkpoint_available=False, execution_mode="execute")

    assert result["returncode"] != 0
    assert result["delete_calls"] == []
    assert result["checkpoint"]["phases"] == []
    assert result["acm_switchover_decommission_result"] == {}
    failed = [task for task in result["tasks"] if task["failed"]]
    assert [task["name"] for task in failed] == [_CHECKPOINT_GATE_TASK_NAME]
    # `result["msg"]` ONLY, never the whole result dict: an assert result also carries
    # `assertion: "acm_switchover_execution.checkpoint.enabled | ..."`, which contains
    # the word "checkpoint", so a substring search over json.dumps(result) passed even
    # with `fail_msg` deleted outright. The operator-facing text is `msg`.
    message = failed[0]["result"]["msg"]
    assert "durable checkpoint state" in message
    assert "acm_switchover_execution.checkpoint.enabled" in message
    # The shipped file that sets it to false, named so an operator hitting this cold
    # knows what to change.
    assert "examples/group_vars/all.yml" in message


def test_validate_mode_is_refused_and_issues_no_delete():
    """Validate mode has no non-mutating path in this role, so it is refused outright.

    Probed before this fix: ``execution_mode="validate"`` with no checkpointing issued
    THREE real DELETEs (MultiClusterObservability, ManagedCluster, MultiClusterHub),
    exited 0, recorded no checkpoint phase, and published ``status: pass`` with
    ``changed: true`` -- a full teardown reported as a passed validation with no
    durable identity map. The cause is a mode-vocabulary mismatch: the gate keyed on
    ``mode == 'execute'`` while every delete guard in the role keys on
    ``mode != 'dry_run'``.

    Kill condition: dropping the ``!= 'validate'`` conjunct from the gate, or
    narrowing the gate's ``when`` back to ``mode == 'execute'``.
    """
    result = run_decommission_role(execution_mode="validate", checkpoint_available=False)

    assert result["returncode"] != 0
    assert result["delete_calls"] == []
    assert result["checkpoint"]["phases"] == []
    failed = [task for task in result["tasks"] if task["failed"]]
    assert [task["name"] for task in failed] == [_CHECKPOINT_GATE_TASK_NAME]
    assert "validate mode" in failed[0]["result"]["msg"]


def test_validate_mode_is_refused_even_when_checkpointing_is_available():
    """The refusal is about the MODE, not about the checkpoint configuration.

    This is the configuration that rules out the tempting alternative fix. Widening
    the gate to ``mode != 'dry_run'`` rather than refusing validate would let this run
    straight through -- both transitions execute, but ``checkpoint_phase`` classifies
    validate as non-mutating, so ``completed_phases`` never gains the phase while the
    deletes go ahead. That is strictly worse than the bug it would replace.

    Kill condition: replacing the ``!= 'validate'`` conjunct with a checkpoint
    availability requirement.
    """
    result = run_decommission_role(execution_mode="validate")

    assert result["returncode"] != 0
    assert result["delete_calls"] == []
    assert "decommission" not in result["checkpoint"]["completed_phases"]
    failed = [task for task in result["tasks"] if task["failed"]]
    assert [task["name"] for task in failed] == [_CHECKPOINT_GATE_TASK_NAME]
    assert "validate mode" in failed[0]["result"]["msg"]


def test_check_mode_does_not_require_checkpointing_and_writes_nothing():
    """A preview is never refused by the gate.

    ONE conjunct discriminates here: ``returncode == 0``, which fails if the gate
    loses its ``not ansible_check_mode`` guard. The ``phases`` and ``operational_data``
    conjuncts do NOT discriminate in this configuration -- with
    ``checkpoint_available=False`` the transitions are skipped by their ``enabled``
    conjunct whatever the mode, so they would stay empty even with the check-mode
    guards removed. ``test_b_stage_check_mode_writes_no_checkpoint_or_outcome`` is the
    test that actually pins "check mode writes no checkpoint state", because it runs
    WITH checkpointing available; it was the one that fired under that mutation.

    Kill condition: dropping ``not ansible_check_mode`` from the gate.
    """
    result = run_decommission_role(checkpoint_available=False, check_mode=True)
    checkpoint = result["checkpoint"]
    assert result["returncode"] == 0
    assert result["delete_calls"] == []
    assert result["acm_switchover_decommission_result"]["changed"] is False
    # Kept as consistency checks, not as coverage; see the docstring.
    assert checkpoint["operational_data"] == checkpoint["before_operational_data"]
    assert checkpoint["phases"] == []


def test_dry_run_execution_mode_does_not_require_checkpointing():
    """A dry run needs no durable state either.

    Kill condition: adding ``dry_run`` to the gate's mode list, or dropping the mode
    condition from its ``when`` entirely -- either fail-closes every dry run.
    """
    result = run_decommission_role(checkpoint_available=False, execution_mode="dry_run")
    assert result["returncode"] == 0
    assert result["acm_switchover_decommission_result"]["status"] != "fail"
    assert result["delete_calls"] == []


def test_a_standalone_dry_run_writes_no_checkpoint_state_and_reads_no_identity():
    """Section 10.3.7 item 11: dry-run persists nothing and performs NO identity read.

    Unlike ``test_check_mode_does_not_require_checkpointing_and_writes_nothing``, the
    transitions really DO run here, and the first assertion proves it -- so "nothing
    was persisted" is a statement about the action plugin's non-mutating contract, not
    an artefact of skipped tasks.

    Kill condition: making a transition mutate durable state in a non-mutating mode,
    or performing the live identity read outside execute mode.
    """
    result = run_decommission_role(standalone_playbook=True, execution_mode="dry_run")
    checkpoint = result["checkpoint"]
    assert len(checkpoint["phases"]) == 2, "enter and pass must actually run in dry-run mode"
    assert checkpoint["operational_data"] == checkpoint["before_operational_data"]
    assert "decommission" not in checkpoint["completed_phases"]
    assert result["delete_calls"] == []
    assert _kube_system_reads(result["requests"]) == [], "dry-run must perform no identity read"


def test_a_clean_standalone_execute_run_completes_the_phase_durably_from_empty_state():
    """Section 10.3.7 items 1, 14 and 15 -- the mandatory acceptance case.

    Runs the REAL playbooks/decommission.yml against the REAL checkpoint action with
    NO pre-seeded operation identity, which is what the first runtime attempt never
    did. Proves the whole same-run lifecycle: standalone enter -> live kube-system UID
    read -> primary-only identity persisted BEFORE the first delete -> role succeeds ->
    standalone pass -> a SECOND fresh UID read -> stored identity matches -> the
    decommission phase is completed.

    The second read is what proves the enter-only contradiction is gone: the
    completion transition re-proves physical identity rather than trusting the value
    stored at enter.

    Kill condition: removing the pass transition; removing ``decommission`` from
    ``KNOWN_PHASES``; making the completion transition reuse the enter-time identity
    instead of re-reading; or persisting identity after the first delete.
    """
    result = run_decommission_role(standalone_playbook=True)
    checkpoint = result["checkpoint"]

    assert result["returncode"] == 0
    assert result["acm_switchover_decommission_result"]["status"] == "pass"
    assert result["delete_calls"], "the clean execute run must actually delete something"
    assert [entry["result"].get("checkpoint", {}).get("phase_status") for entry in checkpoint["phases"]] == [
        None,
        "pass",
    ]
    assert "decommission" in checkpoint["completed_phases"]

    reads = _kube_system_reads(result["requests"])
    assert len(reads) == 2, "enter and pass must each re-prove physical identity"

    # Identity is durable BEFORE the first delete, not merely by the end of the run.
    first_delete = result["requests"].index(result["delete_calls"][0])
    assert result["requests"].index(reads[0]) < first_delete

    identity = result["operation_identity"]
    assert identity["primary_cluster_uid"] == "harness-standalone-primary-uid"
    assert identity["primary_context"] == "primary-hub"
    # One-hub semantics: no secondary is required, requested, or recorded.
    assert identity["secondary_cluster_uid"] == ""
    assert identity["secondary_context"] == ""


def _standalone_identity(primary_uid, primary_context="primary-hub"):
    return build_operation_identity(
        hubs={"primary": {"context": primary_context}},
        operation={},
        collection_version="",
        hub_identities={"primary": {"cluster_uid": primary_uid}},
    )


def test_a_standalone_resume_with_the_same_context_and_uid_is_accepted():
    """Section 10.3.7 item 5: resume against unchanged live truth proceeds."""
    result = run_decommission_role(
        standalone_playbook=True,
        seeded_operation_identity=_standalone_identity("harness-standalone-primary-uid"),
    )
    assert result["returncode"] == 0
    assert "decommission" in result["checkpoint"]["completed_phases"]
    assert result["delete_calls"], "an accepted resume still performs the teardown"


def test_a_standalone_resume_against_a_different_cluster_uid_fails_before_any_mutation():
    """Section 10.3.7 item 6: the SAME context now pointing at a DIFFERENT physical
    cluster must fail before any delete.

    This is the whole reason identity is bound to a live kube-system UID rather than
    to a context name: a repointed kubeconfig keeps the name and changes the cluster.
    """
    result = run_decommission_role(
        standalone_playbook=True,
        seeded_operation_identity=_standalone_identity("a-different-cluster-uid"),
    )
    assert result["returncode"] != 0
    assert result["delete_calls"] == [], "a mismatched identity must delete nothing"
    assert "decommission" not in result["checkpoint"]["completed_phases"]


def test_a_standalone_resume_from_a_different_context_fails_before_any_mutation():
    """Section 10.3.7 item 7: a changed context is a changed identity."""
    result = run_decommission_role(
        standalone_playbook=True,
        seeded_operation_identity=_standalone_identity(
            "harness-standalone-primary-uid", primary_context="some-other-hub"
        ),
    )
    assert result["returncode"] != 0
    assert result["delete_calls"] == []
    assert "decommission" not in result["checkpoint"]["completed_phases"]


def test_an_established_two_hub_checkpoint_cannot_be_downgraded_into_standalone_identity():
    """Section 10.3.7 item 10, end to end.

    A checkpoint carrying a real two-hub identity must not be silently rebound to a
    primary-only one. Exact normalized equality refuses the pair, and nothing is
    deleted.
    """
    two_hub = build_operation_identity(
        hubs={
            "primary": {"context": "primary-hub"},
            "secondary": {"context": "secondary-hub"},
        },
        operation={},
        collection_version="",
        hub_identities={
            "primary": {"cluster_uid": "harness-standalone-primary-uid"},
            "secondary": {"cluster_uid": "harness-secondary-uid"},
        },
    )
    result = run_decommission_role(standalone_playbook=True, seeded_operation_identity=two_hub)
    assert result["returncode"] != 0
    assert result["delete_calls"] == []
    assert "decommission" not in result["checkpoint"]["completed_phases"]
    # The stored two-hub identity survives untouched.
    assert result["operation_identity"] == two_hub


def test_a_standalone_validate_run_is_refused_and_touches_nothing():
    """Section 10.3.7 item 11a, asserted on the FULL playbook.

    The playbook's enter runs BEFORE the role's validate refusal, so the role-only
    harness is the wrong surface for this row: it cannot show what the enter did.
    Validate must perform no identity read, write no checkpoint, and issue no DELETE.
    """
    result = run_decommission_role(standalone_playbook=True, execution_mode="validate")
    checkpoint = result["checkpoint"]

    assert result["returncode"] != 0, "validate must be refused, not previewed"
    assert result["delete_calls"] == []
    assert _kube_system_reads(result["requests"]) == [], "a refused mode reads no identity"
    assert checkpoint["completed_phases"] == []
    assert checkpoint["operational_data"] == checkpoint["before_operational_data"]


def test_the_only_new_api_operation_is_the_primary_kube_system_namespace_get():
    """Section 10.3.7 item 12: audit the actual API expansion, do not assert it.

    Diffs the real request log of a standalone playbook run against a role-only run.
    Anything the standalone lifecycle adds beyond the kube-system Namespace GET --
    a new kind, a new verb, a secondary read -- shows up here as an unexplained
    request.

    Kill condition: adding any other API call to the standalone path.
    """
    role_only = run_decommission_role()
    standalone = run_decommission_role(standalone_playbook=True)

    def _shape(requests):
        return {(r["method"], r["path"].split("?")[0]) for r in requests}

    added = _shape(standalone["requests"]) - _shape(role_only["requests"])
    assert added == {("GET", "/api/v1/namespaces/kube-system")}, added


def test_the_decommission_permission_set_already_grants_the_namespace_read():
    """Section 10.3.7 item 13: the existing RBAC permission suffices.

    No RBAC artifact is authorized to change for this work, so this pins that none
    needs to: the namespace read the identity barrier performs is already granted.
    """
    from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_rbac_validate import (
        DECOMMISSION_CLUSTER_PERMISSIONS,
    )

    assert ("", "namespaces", ["get"]) in [
        (group, resource, list(verbs)) for group, resource, verbs in DECOMMISSION_CLUSTER_PERMISSIONS
    ]


#: Task names that exist ONLY in roles/finalization/tasks/handle_old_hub.yml. Their
#: presence in the callback record is what proves the real integrated task file
#: executed, as opposed to the decommission role being included directly.
_HANDLE_OLD_HUB_TASK_NAMES = (
    "Build embedded decommission settings",
    "Decommission old hub",
    "Determine old hub decommission completion",
    "Determine old hub disposition",
)


def test_the_real_integrated_finalization_path_runs_decommission_without_standalone_identity():
    """Section 10.3.7 item 20, executed rather than parsed.

    Drives the REAL path -- finalization -> handle_old_hub.yml -> include_role
    decommission -- by loading the repository's actual task file through the real
    finalization role with ``tasks_from``. Nothing from that file is copied into the
    harness, so this test tracks the shipped file rather than a snapshot of it.

    Item 20 is the one case a role-only run cannot close: the shared role is included
    from TWO places, and the risk being tested is that the integrated caller
    accidentally acquires standalone one-hub semantics. Proving the role alone
    behaves is necessary but not sufficient -- the caller has to be exercised.

    The checkpoint is seeded with a canonical TWO-HUB identity and checkpointing is
    enabled, so "the identity survived" is a meaningful statement rather than a
    vacuous one about an absent record.

    Kill condition, proven by experiment rather than asserted: bypass
    handle_old_hub.yml and include the decommission role directly, and the
    handle-old-hub-specific task records and disposition fact disappear, so this test
    fails. DELETE calls alone cannot satisfy it -- the role-only harness produces
    those too.
    """
    result = run_decommission_role(integrated_finalization=True)
    checkpoint = result["checkpoint"]
    # RAN, not merely recorded: the harness callback emits a record for a SKIPPED
    # task too, so asserting mere presence would pass against a handle_old_hub.yml
    # whose decommission branch was skipped entirely.
    ran = {task["name"] for task in result["tasks"] if not task["skipped"]}

    # 1. the play succeeds
    assert result["returncode"] == 0, result.get("stderr", "")

    # 2. the REAL handle_old_hub.yml executed -- not the role on its own. These task
    #    names exist nowhere else in the collection.
    for name in _HANDLE_OLD_HUB_TASK_NAMES:
        assert name in ran, f"{name!r} did not RUN: the real handle_old_hub.yml decommission branch was not taken"

    # 3. the shared decommission role was actually reached THROUGH it, and did work
    assert result["delete_calls"], "the integrated path must actually perform the teardown"
    assert result["acm_switchover_decommission_result"]["status"] == "pass"

    # 4. the disposition fact handle_old_hub.yml owns
    disposition = result["facts"]["acm_switchover_old_hub_disposition"]
    assert disposition["action"] == "decommission"

    # 5. integrated completion facts are produced
    assert result["facts"]["_old_hub_decommission_completed"] is True
    assert result["facts"]["_acm_switchover_embedded_decommission"]["confirmed"] is True

    # 6/7. NO standalone identity read is issued by the integrated path. The primary
    #      kube-system GET belongs to the standalone playbook only.
    assert _kube_system_reads(result["requests"]) == [], "the integrated path must read no standalone identity"

    # 8/9/10. no standalone decommission phase, and no checkpoint transition at all
    assert checkpoint["phases"] == [], "the integrated path must record no standalone transition"
    assert "decommission" not in checkpoint["completed_phases"]

    # 11. whole-object identity equality, not a single field
    seeded = result["seeded_operation_identity"]
    assert seeded is not None
    assert result["operation_identity"] == seeded, "the two-hub identity must survive byte-identical"

    # 12. and the secondary half is genuinely still there
    assert result["operation_identity"]["secondary_context"] == "secondary-hub"
    assert result["operation_identity"]["secondary_cluster_uid"] == "harness-secondary-uid"


def test_a_direct_role_include_cannot_satisfy_the_integrated_path_evidence():
    """The discriminator for the test above.

    A role-only run performs the same deletes and leaves the same two-hub identity
    intact, so those facts alone cannot prove the integrated caller ran. This pins
    that the handle-old-hub-specific evidence is absent without it -- which is why
    the item 20 test asserts on that evidence rather than on DELETE calls.

    Kill condition: if these task names ever appear in a role-only run, the item 20
    test has stopped discriminating and its evidence must be re-chosen.
    """
    result = run_decommission_role()
    task_names = {task["name"] for task in result["tasks"]}

    assert result["delete_calls"], "the role-only run still deletes -- which is the point"
    for name in _HANDLE_OLD_HUB_TASK_NAMES:
        assert name not in task_names, f"{name!r} must come from handle_old_hub.yml, not the role"
    # Not even as a skipped record: the file is never loaded on this path.
    assert "acm_switchover_old_hub_disposition" not in result["facts"]


def test_the_shared_role_alone_reads_no_identity_and_leaves_a_two_hub_checkpoint_intact():
    """Section 10.3.7 item 20, behaviourally.

    Integrated finalization includes the shared decommission role directly, from
    inside an established two-hub `finalization` checkpoint. This runs the role that
    same way -- a plain include, no standalone playbook -- against a checkpoint the
    harness seeds with a REAL two-hub operation identity, and proves the role in
    isolation:

    - performs no primary kube-system identity read (that belongs to the standalone
      playbook, which the integrated path never invokes);
    - records no checkpoint transition of its own;
    - enters no `decommission` phase;
    - leaves the established two-hub identity byte-identical.

    The last assertion is the one that matters: it is the difference between "the
    integrated path is probably unaffected" and evidence that a two-hub identity
    survives a decommission run untouched.

    Kill condition: putting any checkpoint transition or identity read back into the
    shared role, where it would fire for the integrated caller too.
    """
    result = run_decommission_role()
    checkpoint = result["checkpoint"]

    assert result["returncode"] == 0
    assert result["delete_calls"], "the integrated-style run must still perform the teardown"
    assert _kube_system_reads(result["requests"]) == [], "the role must not perform the standalone identity read"
    assert checkpoint["phases"] == [], "the role must own no checkpoint transition"
    assert "decommission" not in checkpoint["completed_phases"]

    identity = result["operation_identity"]
    assert identity["primary_cluster_uid"] == "harness-primary-uid"
    assert identity["secondary_cluster_uid"] == "harness-secondary-uid", "the two-hub identity must survive intact"
    assert identity["secondary_context"] == "secondary-hub"


def test_integrated_finalization_never_uses_the_standalone_identity_mode():
    """Section 10.3.7 item 20: the integrated path stays two-hub.

    handle_old_hub.yml includes the SAME decommission role from inside an established
    two-hub finalization checkpoint. It must include the role, set no standalone
    argument, and enter no standalone decommission phase.

    Kill condition: adding standalone_decommission_identity anywhere on the
    integrated path, or entering a decommission phase from finalization.
    """
    handle_old_hub = _load_tasks(ROLES_DIR / "finalization" / "tasks" / "handle_old_hub.yml")

    includes = [
        task
        for task in handle_old_hub
        if task.get("ansible.builtin.include_role", {}).get("name") == "tomazb.acm_switchover.decommission"
    ]
    assert includes, "the integrated path must still include the shared decommission role"

    for task in all_checkpoint_phase_tasks(handle_old_hub):
        args = task[_CHECKPOINT_WRITER_MODULE]
        assert "standalone_decommission_identity" not in args
        assert args.get("phase") != "decommission"

    # And no OTHER role may quietly adopt the standalone mode either: the standalone
    # playbook is the single owner.
    for path in sorted(ROLES_DIR.rglob("*.yml")):
        assert "standalone_decommission_identity" not in path.read_text(encoding="utf-8"), path


def test_a_failed_standalone_substep_records_fail_and_leaves_the_phase_incomplete():
    """Section 10.3.7 item 16: a failed teardown is never a completed phase.

    The rescue records ``status: fail`` and re-raises. Kill condition: moving the pass
    transition into an ``always``, where it would run after a failed substep and mark
    the phase passed.
    """
    result = run_decommission_role(standalone_playbook=True, observability_outcome="failed")
    checkpoint = result["checkpoint"]

    assert result["returncode"] != 0
    assert result["acm_switchover_decommission_result"]["status"] == "fail"
    assert "decommission" not in checkpoint["completed_phases"]
    statuses = [entry["result"].get("checkpoint", {}).get("phase_status") for entry in checkpoint["phases"]]
    assert statuses == [None, "fail"], "the rescue must record a fail, and no pass"


def test_actual_change_is_never_derived_from_check_mode():
    """The published `changed` must carry the check-mode exclusion in the YAML itself."""
    publish = task_named(decommission_task_files["main"], "Publish decommission result")
    changed = str(publish["ansible.builtin.set_fact"]["acm_switchover_decommission_result"]["changed"])
    assert "not ansible_check_mode" in changed


def test_mco_prediction_is_aggregated_separately_from_actual_change():
    """C4 contributes its fresh per-family prediction to the shared result."""
    publish = task_named(decommission_task_files["main"], "Publish decommission result")
    result = publish["ansible.builtin.set_fact"]["acm_switchover_decommission_result"]
    assert "_acm_mco_would_change" in str(result["would_change"])


def test_summary_keeps_its_published_artifact_keys():
    """`phase`, `mode` and `has_observability` are consumed by existing integration tests."""
    publish = task_named(decommission_task_files["main"], "Publish decommission result")
    result = publish["ansible.builtin.set_fact"]["acm_switchover_decommission_result"]
    for key in (
        "phase",
        "mode",
        "has_observability",
        "status",
        "substeps",
        "changed",
        "would_change",
    ):
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
    assert summary["substeps"] == {
        "observability": "completed",
        "managed_clusters": "failed",
    }
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


def test_check_mode_reports_no_actual_change_and_fresh_mco_prediction():
    """A CLEAN preview must report `pass` and exit zero.

    The other direction of the F4 honesty rule: an artifact that cries failure on every
    preview is as dishonest as one that always says pass. Without these two assertions,
    initialising `_acm_decommission_substep_rescued` to `ansible_check_mode` instead of
    `false` left the whole suite green while every operator preview published
    `status: fail` with a non-zero return code.
    """
    result = run_decommission_role(check_mode=True)
    summary = result["acm_switchover_decommission_result"]
    assert summary["status"] == "pass"
    assert result["returncode"] == 0
    assert summary["changed"] is False
    assert summary["would_change"] is True
    assert not [task for task in result["tasks"] if task["changed"]]


def test_check_mode_preserves_operational_data_and_records_no_outcome():
    """C4 reads durable data in check mode but writes neither records nor outcomes."""
    result = run_decommission_role(check_mode=True)
    checkpoint = result["checkpoint"]
    assert checkpoint["operational_data"] == checkpoint["before_operational_data"]
    assert result["acm_switchover_decommission_result"]["substeps"] == {}


def test_b_stage_check_mode_issues_no_delete():
    result = run_decommission_role(check_mode=True)
    assert result["delete_calls"] == []


def test_dry_run_records_no_substep_outcome():
    """A dry run previews; a later live run must trust nothing it observed.

    `status == "pass"` and `returncode == 0` also pin the over-correction direction: a
    clean dry run must not be published as a failure.
    """
    result = run_decommission_role(execution_mode="dry_run")
    summary = result["acm_switchover_decommission_result"]
    assert summary["substeps"] == {}
    assert summary["status"] == "pass"
    assert result["returncode"] == 0
    assert summary["changed"] is False
    assert result["delete_calls"] == []
