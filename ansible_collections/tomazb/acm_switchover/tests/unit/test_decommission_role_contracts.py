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
import os
import pathlib
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, Dict, List, Optional, Union
from urllib.parse import unquote, urlsplit

import pytest
import yaml
from yaml_contract_helpers import _flatten_tasks, _when_text

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    KNOWN_PHASES,
    build_operation_identity,
    reset_completed_phases_from,
    teardown_key,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    ACM_NAMESPACE,
    ACM_OPERATOR_POD_PREFIX,
    CSV_API_GROUP,
    CSV_API_VERSION,
    DECOMMISSION_SUBSTEP_OUTCOMES,
    GATE_REASON_ACK_NOT_APPLICABLE,
    GATE_REASON_DESTINATION_ABSENT,
    GATE_REASON_DESTINATION_UNVERIFIABLE,
    GATE_REASON_SOURCE_AMBIGUOUS,
    GATE_REASON_SOURCE_UNVERIFIABLE,
    MCH_OWNED_CRD,
    OPERATOR_IDENTITY_DISCOVERY_METHOD,
)

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
PLAYBOOKS_DIR = pathlib.Path(__file__).resolve().parents[2] / "playbooks"
DECOMMISSION_PLAYBOOK = PLAYBOOKS_DIR / "decommission.yml"
DECOMMISSION_MAIN = ROLES_DIR / "decommission" / "tasks" / "main.yml"
DELETE_OBSERVABILITY = ROLES_DIR / "decommission" / "tasks" / "delete_observability.yml"
DESTINATION_OBSERVABILITY_GATE = ROLES_DIR / "decommission" / "tasks" / "destination_observability_gate.yml"
DELETE_MANAGED_CLUSTERS = ROLES_DIR / "decommission" / "tasks" / "delete_managed_clusters.yml"
TEARDOWN_ONE_MANAGED_CLUSTER = ROLES_DIR / "decommission" / "tasks" / "teardown_one_managed_cluster.yml"
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
        "tomazb.acm_switchover.acm_pod_owner_classify",
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
        # Same terms: read-only by module contract, and section 8 requires the
        # capture pass to keep running under --check so preview can predict. Its
        # check-mode run is proved in tests/integration/test_pod_owner_classify_runtime.py.
        "tomazb.acm_switchover.acm_pod_owner_classify",
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
    # Included by `observability` only, and only when a destination hub exists. It
    # publishes a decision fact, records no substep outcome and writes no checkpoint.
    "destination_observability_gate": DESTINATION_OBSERVABILITY_GATE,
    "managed_clusters": DELETE_MANAGED_CLUSTERS,
    # Per-target no-drain include looped by managed_clusters (R4-03 PR D).
    "teardown_one_managed_cluster": TEARDOWN_ONE_MANAGED_CLUSTER,
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


def _collapse(expression) -> str:
    """One line of an expression, so a folded YAML scalar can be compared literally."""
    return " ".join(str(expression).split())


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

    def test_the_requested_predicate_is_published_before_the_source_inventory_read(self):
        """Zero reads when nothing is requested, exactly like Python's ``_substep_requested``.

        The predicate depends only on the configured setting and the durable record, so
        it can be -- and must be -- decided before the first MultiClusterObservability
        request. Every task that consumes the read must then carry the same predicate as
        the FIRST element of its ``when``, because Ansible evaluates a ``when`` list in
        order and stops at the first false: that is what keeps the dependants from
        templating a register that a skipped read never filled.
        """
        names = [task.get("name") for task in self.tasks]
        publisher = "Publish whether this run requests a MultiClusterObservability teardown"
        read = "Read the source MultiClusterObservability inventory"
        assert names.index(publisher) < names.index(read)

        dependants = [
            read,
            "Fail closed when the source MCO inventory is unverifiable",
            "Select the fixed MultiClusterObservability target",
            "Fail closed on an ambiguous MultiClusterObservability inventory",
            "Publish the source MultiClusterObservability classification",
        ]
        for name in dependants:
            when = task_named(self.tasks, name).get("when")
            assert isinstance(when, list), f"{name!r} must carry a list `when` that short-circuits"
            assert when[0] == "_acm_mco_requested | bool", f"{name!r} must guard on the requested predicate first"

        warning = task_named(
            self.tasks,
            "Warn that GitOps-managed MultiClusterObservability deletion must be coordinated",
        )
        assert "acm_switchover_mco_read" not in str(warning["loop"]), (
            "a `loop` is templated before `when` is evaluated, so the warning must not "
            "template the skipped inventory register"
        )

    def test_the_requested_predicate_resolves_the_setting_exactly_as_main_yml(self):
        """One input, one decision: ``auto`` is the detection, anything else is ``| bool``.

        A ``!= 'false'`` string test disagrees with ``main.yml`` on every other falsey
        spelling YAML and Ansible accept (``no``, ``off``, ``0``), which would make the
        same configured value mean "no observability" to the effective-setting fact and
        "tear observability down" to this file.
        """
        main_tasks = yaml.safe_load(DECOMMISSION_MAIN.read_text()) or []
        effective = _collapse(
            task_named(main_tasks, "Publish effective observability setting")["ansible.builtin.set_fact"][
                "acm_switchover_decommission_effective_has_observability"
            ]
        )
        requested = _collapse(
            task_named(self.tasks, "Publish whether this run requests a MultiClusterObservability teardown")[
                "ansible.builtin.set_fact"
            ]["_acm_mco_requested"]
        )

        auto_probe = "(acm_switchover_decommission.has_observability | default('auto') | string | lower) == 'auto'"
        explicit = "(acm_switchover_decommission.has_observability | bool)"
        for expression in (effective, requested):
            assert auto_probe in expression
            assert explicit in expression
        assert "!= 'false'" not in requested
        assert "acm_switchover_mco_record is mapping" in requested

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


class TestDestinationObservabilityGate:
    """decommission/tasks/destination_observability_gate.yml contract tests (Task C5)."""

    def setup_method(self):
        self.observability = yaml.safe_load(DELETE_OBSERVABILITY.read_text()) or []
        self.tasks = yaml.safe_load(DESTINATION_OBSERVABILITY_GATE.read_text()) or []
        self.text = DESTINATION_OBSERVABILITY_GATE.read_text()

    def test_file_exists(self):
        assert DESTINATION_OBSERVABILITY_GATE.exists()

    def test_the_gate_is_included_at_the_ruled_position(self):
        """After the no-record clean-skip classification, before the delete_started writer.

        That is the collection's expression of the Python position: after the
        clean-skip check and the completed dispatch, before ``expected_uid`` is used
        for any durable write or DELETE.
        """
        names = [task.get("name") for task in self.observability]
        includes = [
            index
            for index, task in enumerate(self.observability)
            if _include_file(task) == "destination_observability_gate.yml"
        ]

        assert len(includes) == 1
        position = includes[0]
        assert names[position - 1] == "Publish the no-record clean-skip classification"
        assert position < names.index("Record MultiClusterObservability delete_started")
        assert position < index_of_task_using(self.observability, "tomazb.acm_switchover.acm_uid_guarded_delete")
        # Nothing durable and nothing destructive may sit between the two.
        between = self.observability[position + 1 : names.index("Record MultiClusterObservability delete_started")]
        assert not checkpoint_writer_tasks({"observability": between})
        assert not mutating_tasks({"observability": between})

    def test_the_gate_include_runs_only_when_a_delete_is_pending_on_an_integrated_run(self):
        """Python never reaches the gate without a destination client, without a
        requested substep, or when its own fresh read found no target.

        ``Decommission.decommission`` marks the observability substep NOT_REQUESTED and
        never calls ``teardown_observability`` when ``has_observability`` is false, so a
        collection gate guarded only by the destination would hard-fail a configuration
        that deletes nothing at all. ``_acm_mco_present`` is R1: a gate with no DELETE
        to authorize would block the mid-drain resume on its own source reads. The
        standalone declaration is the collection's ``--decommission``: that entry point
        has no destination hub even when one is configured in the hub vars.
        """
        include = next(
            task for task in self.observability if _include_file(task) == "destination_observability_gate.yml"
        )
        when = include.get("when")

        assert isinstance(when, list)
        assert "acm_switchover_hubs.secondary is defined" in when
        assert "_acm_mco_requested | bool" in when
        assert "_acm_mco_present | bool" in when
        assert "not (acm_switchover_standalone_decommission | default(false) | bool)" in when

    def test_the_gate_asserts_a_non_empty_destination_target(self):
        """A defined-but-blank secondary is a configuration error, not a skipped gate."""
        assertion = next(task for task in self.tasks if "ansible.builtin.assert" in task)
        that = str(assertion["ansible.builtin.assert"]["that"])

        assert "acm_switchover_hubs.secondary.kubeconfig" in that
        assert "acm_switchover_hubs.secondary.context" in that

    def test_every_gate_read_is_fresh_canonical_and_silent(self):
        reads = read_outcome_tasks(self.tasks)
        assert len(reads) == 4
        for task in reads:
            args = task["tomazb.acm_switchover.acm_k8s_read_outcome"]
            assert args["resource_name"] in {"multiclusterobservabilities", "namespaces"}
            assert task.get("no_log") is True

    def test_the_gate_reads_both_hubs_through_explicit_routing(self):
        reads = read_outcome_tasks(self.tasks)
        routes = [
            (
                "primary"
                if "primary" in str(task["tomazb.acm_switchover.acm_k8s_read_outcome"]["kubeconfig"])
                else "secondary"
            )
            for task in reads
        ]

        assert routes == ["primary", "primary", "secondary", "secondary"]

    def test_the_gate_absorbs_no_failure_and_defaults_no_inventory(self):
        assert "failed_when" not in self.text
        assert "ignore_errors" not in self.text
        assert "default([])" not in self.text

    def test_the_gate_publishes_only_a_fact_and_writes_no_checkpoint(self):
        decisions = [
            fact["acm_switchover_observability_gate"]
            for fact in (task.get("ansible.builtin.set_fact", {}) for task in self.tasks)
            if "acm_switchover_observability_gate" in fact
        ]

        assert decisions
        assert {decision["decision"] for decision in decisions} == {"proceed", "not_applicable", "blocked"}
        assert not checkpoint_writer_tasks({"gate": self.tasks})

    def test_every_blocking_reason_is_a_mirrored_constant(self):
        reasons = {
            fact["acm_switchover_observability_gate"]["reason"]
            for fact in (task.get("ansible.builtin.set_fact", {}) for task in self.tasks)
            if "acm_switchover_observability_gate" in fact
            and fact["acm_switchover_observability_gate"]["decision"] == "blocked"
        }

        assert reasons == {
            GATE_REASON_ACK_NOT_APPLICABLE,
            GATE_REASON_DESTINATION_ABSENT,
            GATE_REASON_DESTINATION_UNVERIFIABLE,
            GATE_REASON_SOURCE_AMBIGUOUS,
            GATE_REASON_SOURCE_UNVERIFIABLE,
        }

    def test_a_blocked_gate_fails_the_substep_naming_the_reason_code(self):
        failure = next(task for task in self.tasks if "ansible.builtin.fail" in task)
        message = str(failure["ansible.builtin.fail"]["msg"])

        assert "acm_switchover_observability_gate.reason" in message
        assert "blocked" in _when_text(failure)

    def test_the_acknowledgement_reads_the_decommission_variable(self):
        assert "acm_switchover_decommission.acknowledge_observability_not_migrated" in self.text

    def test_the_acknowledgement_default_is_declared_false(self):
        defaults = yaml.safe_load((ROLES_DIR / "decommission" / "defaults" / "main.yml").read_text())

        assert defaults["acm_switchover_decommission"]["acknowledge_observability_not_migrated"] is False


class TestDeleteManagedClusters:
    """decommission/tasks/delete_managed_clusters.yml contract tests (R4-03 PR D)."""

    def setup_method(self):
        self.tasks = _load_tasks(DELETE_MANAGED_CLUSTERS)
        self.one = _load_tasks(TEARDOWN_ONE_MANAGED_CLUSTER) if TEARDOWN_ONE_MANAGED_CLUSTER.exists() else []
        self.text = DELETE_MANAGED_CLUSTERS.read_text()
        self.one_text = TEARDOWN_ONE_MANAGED_CLUSTER.read_text() if TEARDOWN_ONE_MANAGED_CLUSTER.exists() else ""

    def test_file_exists(self):
        assert DELETE_MANAGED_CLUSTERS.exists(), "decommission/tasks/delete_managed_clusters.yml must exist"
        assert TEARDOWN_ONE_MANAGED_CLUSTER.exists(), "per-target ManagedCluster teardown include must exist"

    def test_delete_goes_through_uid_guarded_module(self):
        """A name-only delete can remove a replacement object under the same name."""
        guarded = [task for task in self.one if "tomazb.acm_switchover.acm_uid_guarded_delete" in task]
        assert len(guarded) == 1
        for tasks in (self.tasks, self.one):
            assert not [task for task in tasks if task.get("kubernetes.core.k8s", {}).get("state") == "absent"]

    def test_guarded_delete_binds_identity_routing_and_bounds(self):
        task = next(task for task in self.one if "tomazb.acm_switchover.acm_uid_guarded_delete" in task)
        args = task["tomazb.acm_switchover.acm_uid_guarded_delete"]
        assert args["api_version"] == "cluster.open-cluster-management.io/v1"
        assert args["kind"] == "ManagedCluster"
        assert args["resource_name"] == "managedclusters"
        assert "expected_uid" in args
        assert "_acm_mc_target_name" in str(args["name"])
        assert "primary.kubeconfig" in str(args["kubeconfig"])
        assert "primary.context" in str(args["context"])
        for timeout in ("request_timeout", "wait_timeout", "wait_sleep"):
            assert timeout in args
            assert str(args[timeout]).strip()
        assert task.get("no_log") is True
        assert "ansible_check_mode" in str(task.get("check_mode", ""))
        assert "dry_run" in str(task.get("check_mode", ""))

    def test_every_discovery_backed_read_supplies_a_canonical_resource_name(self):
        reads = read_outcome_tasks(self.tasks) + read_outcome_tasks(self.one)
        assert reads
        allowed = {"managedclusters", "clusterdeployments"}
        for task in reads:
            args = task["tomazb.acm_switchover.acm_k8s_read_outcome"]
            assert args.get("resource_name") in allowed

    def test_mc_inventory_read_fails_closed(self):
        read = task_named(self.tasks, "Read the source ManagedCluster inventory")
        assert read["tomazb.acm_switchover.acm_k8s_read_outcome"]["read_mode"] == "list"
        assert read["tomazb.acm_switchover.acm_k8s_read_outcome"]["resource_name"] == "managedclusters"
        failure = task_named(self.tasks, "Fail closed when the source ManagedCluster inventory is unverifiable")
        when = _when_text(failure)
        assert "read_status" in when
        assert "ok" in when
        assert "kind_not_served" in when
        assert "not in" in when

    def test_malformed_managed_cluster_inventory_items_fail_closed_before_name_publish(self):
        """Empty/mapping-but-nameless inventory items must not become a false-empty work set."""
        detect = task_named(self.tasks, "Detect malformed ManagedCluster inventory items")
        failure = task_named(self.tasks, "Fail closed when ManagedCluster inventory items are malformed")
        live = task_named(self.tasks, "Publish non-local live ManagedCluster names")
        assert "Cannot verify ManagedCluster inventory" in failure["ansible.builtin.fail"]["msg"]
        assert "_acm_mc_inventory_malformed" in _when_text(failure)
        detect_text = str(detect)
        assert "mc is not mapping" in detect_text or "is not mapping" in detect_text
        assert "metadata.name" in detect_text
        # Name publish must come after the malformed gate so silent drops cannot noop.
        assert self.tasks.index(failure) < self.tasks.index(live)

    def test_kind_not_served_without_durable_records_is_fatal(self):
        """Binding ruling 3: discovery miss with no MC obligation is unverifiable, not empty."""
        failure = task_named(
            self.tasks,
            "Fail closed when ManagedCluster kind is not served and no durable records exist",
        )
        when = _when_text(failure)
        assert "kind_not_served" in when
        assert "_acm_mc_work_set" in when

    def test_local_cluster_excluded_from_work_set(self):
        """local-cluster must never receive named get, delete, or teardown record."""
        assert "local-cluster" in self.text
        live = task_named(self.tasks, "Publish non-local live ManagedCluster names")
        assert "local-cluster" in str(live)
        records = task_named(self.tasks, "Publish durable ManagedCluster teardown record names")
        assert "local-cluster" in str(records)
        assert "_acm_mc_target_name" in self.one_text
        assert "name: local-cluster" not in self.one_text
        assert 'name: "local-cluster"' not in self.one_text

    def test_per_target_rescue_aggregates_only_expected_operational_failures(self):
        """Unexpected/programming failures must re-raise; only classified operational failures become survivors."""
        assert "rescue:" in self.one_text
        classify = task_named(self.one, "Classify whether this ManagedCluster failure is expected operational")
        propagate = task_named(self.one, "Propagate unexpected ManagedCluster teardown failures")
        survivor = task_named(self.one, "Record this ManagedCluster as a survivor")
        assert "_acm_mc_expected_operational_failure" in str(classify)
        assert "acm_uid_guarded_delete" in str(classify) or "reason" in str(classify)
        assert "ansible.builtin.fail" in str(classify) or "fail" in str(classify)
        assert "not (_acm_mc_expected_operational_failure" in _when_text(propagate) or (
            "not" in _when_text(propagate) and "_acm_mc_expected_operational_failure" in _when_text(propagate)
        )
        assert "_acm_mc_expected_operational_failure" in _when_text(survivor)
        assert "ignore_errors" not in self.one_text
        assert "failed_when: false" not in self.one_text
        assert "failed_when: False" not in self.one_text

    def test_clusterdeployment_safety_uses_strict_read_before_mutation(self):
        """Hive preserveOnDelete safety retained; inventory is strict, not k8s_info blindness."""
        assert "ClusterDeployment" in self.text
        assert "preserveOnDelete" in self.text
        hive = task_named(self.tasks, "Read Hive ClusterDeployments before ManagedCluster deletion")
        args = hive["tomazb.acm_switchover.acm_k8s_read_outcome"]
        assert args["read_mode"] == "list"
        assert args["resource_name"] == "clusterdeployments"
        assert args["kind"] == "ClusterDeployment"
        when = hive.get("when")
        assert isinstance(when, list)
        assert any("live" in str(clause) for clause in when)

    def test_no_drain_reads_on_managed_cluster_path(self):
        """IV-R403-01 case 3 inapplicable: ManagedCluster has no drain scope."""
        combined = self.text + self.one_text
        assert "drain_pending" not in combined
        assert "open-cluster-management-observability" not in combined
        writers = checkpoint_writer_tasks({"one": self.one})
        phases = [
            task[_CHECKPOINT_WRITER_MODULE]["teardown_record"]["phase"]
            for task in writers
            if "teardown_record" in task.get(_CHECKPOINT_WRITER_MODULE, {})
        ]
        assert "delete_started" in phases
        assert "cr_absent" in phases
        assert "completed" in phases
        assert "drain_pending" not in phases
        assert "drained" not in phases

    def test_completed_evidence_is_empty_resource_versions_and_target_cr_only(self):
        completed = task_named(self.one, "Record ManagedCluster completed")
        record = completed[_CHECKPOINT_WRITER_MODULE]["teardown_record"]
        assert (
            "{{ _acm_mc_resource_versions }}" in str(record["resource_versions"]) or record["resource_versions"] == {}
        )
        assert "{{ _acm_mc_absence_proofs }}" in str(record["absence_proofs"])
        builder = task_named(self.one, "Build ManagedCluster completion evidence")
        evidence = builder["ansible.builtin.set_fact"]
        assert evidence["_acm_mc_resource_versions"] == {}
        assert "target_cr" in str(evidence["_acm_mc_absence_proofs"])
        assert "drain_namespace" not in str(evidence["_acm_mc_absence_proofs"])

    def test_every_mc_checkpoint_writer_is_execute_and_check_mode_guarded(self):
        writers = checkpoint_writer_tasks({"family": self.tasks, "one": self.one})
        assert writers
        for task in writers:
            when = _when_text(task)
            assert "not ansible_check_mode" in when
            assert "execute" in when

    def test_mc_checkpoint_calls_do_not_own_a_phase_lifecycle(self):
        calls = checkpoint_writer_tasks({"family": self.tasks, "one": self.one})
        assert calls
        for task in calls:
            args = task[_CHECKPOINT_WRITER_MODULE]
            assert "phase" not in args or "teardown_record" in args
            assert "status" not in args

    def test_survivor_aggregation_fails_once_listing_all(self):
        failure = task_named(self.tasks, "Fail when ManagedCluster teardown left survivors")
        msg = str(failure["ansible.builtin.fail"]["msg"])
        assert "_acm_mc_survivors" in msg
        assert "survivors" in msg.lower()
        assert "ignore_errors" not in self.text
        assert "ignore_errors" not in self.one_text
        assert "failed_when: false" not in self.text
        assert "failed_when: false" not in self.one_text

    def test_per_target_include_loops_the_work_set(self):
        include = next(task for task in self.tasks if _include_file(task) == "teardown_one_managed_cluster.yml")
        assert "{{ _acm_mc_work_set" in str(include.get("loop", ""))
        assert include.get("loop_control", {}).get("loop_var") == "_acm_mc_target_name"

    def test_family_publishes_changed_and_would_change_facts(self):
        publish = task_named(self.tasks, "Publish the ManagedCluster change inputs")
        facts = publish["ansible.builtin.set_fact"]
        assert "_acm_mc_changed" in facts
        assert "_acm_mc_would_change" in facts


class _GuardStub:
    """A stand-in for any fact a task condition names, fixed to one classify outcome.

    Attribute and item lookups answer the classify pass's own fields -- ``read_status``
    is always ``error`` and ``read_error_stage`` is the stage under test -- and anything
    else resolves to another truthy stub, so an unrelated clause never decides the
    verdict. ``mode`` answers ``execute`` so an execution-mode guard passes.
    """

    def __init__(self, stage: Optional[str]):
        self._stage = stage

    def _resolve(self, name: str):
        if name == "read_status":
            return "error"
        if name == "read_error_stage":
            return self._stage
        if name == "mode":
            return "execute"
        return _GuardStub(self._stage)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._resolve(name)

    def __getitem__(self, name):
        return self._resolve(str(name))

    def get(self, name, default=None):
        return self._resolve(str(name))

    def __bool__(self) -> bool:
        return True

    def __eq__(self, other) -> bool:
        return False

    def __ne__(self, other) -> bool:
        return True

    def __hash__(self) -> int:
        return id(self)


class TestDeleteMultiClusterHubPhaseTable:
    """decommission/tasks/delete_multiclusterhub.yml contract tests (R4-03 PR E / E6).

    Replaces the retired ``TestDeleteMultiClusterHub``, whose every assertion pinned the
    mechanism E6 removes: ``kubernetes.core.k8s_info`` discovery, a generic name-only
    ``kubernetes.core.k8s`` delete, Pod-name prefix filtering as the drain predicate, and
    ``failed_when: false`` absorbing the wait. Those are inverted here into absence
    assertions; the safety properties themselves are proved at runtime by
    ``TestDeleteMultiClusterHubDurable``.
    """

    def setup_method(self):
        self.tasks = _load_tasks(DELETE_MCH)
        self.text = DELETE_MCH.read_text()

    def _module_tasks(self, module: str) -> list:
        return [task for task in self.tasks if module in _task_actions(task)]

    def test_file_exists(self):
        assert DELETE_MCH.exists(), "decommission/tasks/delete_multiclusterhub.yml must exist"

    def test_no_generic_multiclusterhub_discovery_or_delete_remains(self):
        """§5: a name-only delete can remove a replacement; k8s_info cannot fail closed."""
        assert not [
            task for task in self.tasks if task.get("kubernetes.core.k8s_info", {}).get("kind") == "MultiClusterHub"
        ]
        assert not [task for task in self.tasks if task.get("kubernetes.core.k8s", {}).get("kind") == "MultiClusterHub"]
        assert not [task for task in self.tasks if task.get("kubernetes.core.k8s", {}).get("state") == "absent"]

    def _decision_expressions(self, task: dict) -> str:
        """Everything this task DECIDES with: its loop condition, its guards, its args.

        Diagnostic text -- a `fail`/`debug` message -- is deliberately excluded: naming
        the operator Deployment in an error message is not a filter.
        """
        parts = [str(task.get("until", "")), _when_text(task), str(task.get("failed_when", ""))]
        for action in _task_actions(task):
            if action in ("ansible.builtin.fail", "ansible.builtin.debug"):
                continue
            parts.append(str(task[action]))
        return " ".join(parts)

    def test_no_pod_name_prefix_filter_decides_anything(self):
        """§5: ownership is decided by the classifier, never by a name prefix."""
        for task in self.tasks:
            expression = self._decision_expressions(task)
            assert "rejectattr" not in expression, f"task {task.get('name')!r} still filters by name"
            assert (
                ACM_OPERATOR_POD_PREFIX not in expression
            ), f"task {task.get('name')!r} still treats the operator name prefix as a signal"

    def test_no_authoritative_read_is_absorbed_or_defaulted_to_empty(self):
        """§5: `failed_when: false` and `default([])` both turn a failed read into 'nothing'.

        The `default([])` ban is scoped to what a task DECIDES with -- an authoritative
        read turned into an empty inventory -- not to the whole file text.
        """
        for task in self.tasks:
            assert task.get("failed_when") is not False, f"task {task.get('name')!r} absorbs its own failure"
            assert "ignore_errors" not in task, f"task {task.get('name')!r} absorbs its own failure"
            decisions = self._decision_expressions(task)
            assert "default([])" not in decisions, f"task {task.get('name')!r} decides on a defaulted-empty read"
        # A block-level `ignore_errors` never reaches a flattened task, so the parsed
        # check alone cannot see it.
        assert "ignore_errors" not in self.text

    def test_target_resolution_binds_the_strict_read_arguments(self):
        """§7: a namespaced strict LIST, then a strict named GET, of the MCH resource."""
        reads = [
            task
            for task in read_outcome_tasks(self.tasks)
            if task["tomazb.acm_switchover.acm_k8s_read_outcome"].get("resource_name") == "multiclusterhubs"
        ]
        assert reads, "MultiClusterHub discovery must use acm_k8s_read_outcome"
        modes = {task["tomazb.acm_switchover.acm_k8s_read_outcome"].get("read_mode") for task in reads}
        assert {"list", "get"} <= modes
        for task in reads:
            args = task["tomazb.acm_switchover.acm_k8s_read_outcome"]
            assert args["api_version"] == "operator.open-cluster-management.io/v1"
            assert args["kind"] == "MultiClusterHub"
            assert args["namespace"] == ACM_NAMESPACE
            assert "primary.kubeconfig" in str(args["kubeconfig"])

    def test_identity_capture_and_classification_go_through_the_e5_module(self):
        """§9 and §13: one classifier owns every ownership decision, and it is no_log."""
        classifiers = self._module_tasks("tomazb.acm_switchover.acm_pod_owner_classify")
        operations = [task["tomazb.acm_switchover.acm_pod_owner_classify"].get("operation") for task in classifiers]
        # `>= 1`, not `== 1`: section 8's preview capture and section 9's execute capture
        # are allowed to be two tasks with different guards.
        assert operations.count("capture_identity") >= 1
        assert operations.count("classify") >= 2, "the drain loop and the final proof are separate passes"
        for task in classifiers:
            args = task["tomazb.acm_switchover.acm_pod_owner_classify"]
            assert args["namespace"] == ACM_NAMESPACE
            assert "primary.kubeconfig" in str(args["kubeconfig"])
            assert task.get("no_log") is True

    def test_the_delete_is_uid_guarded_and_bound_to_the_durable_identity(self):
        """§11: the expected UID precondition is what makes the delete safe."""
        guarded = self._module_tasks("tomazb.acm_switchover.acm_uid_guarded_delete")
        assert len(guarded) == 1
        args = guarded[0]["tomazb.acm_switchover.acm_uid_guarded_delete"]
        assert args["api_version"] == "operator.open-cluster-management.io/v1"
        assert args["kind"] == "MultiClusterHub"
        assert args["resource_name"] == "multiclusterhubs"
        assert args["namespace"] == ACM_NAMESPACE
        assert "expected_uid" in args
        assert "primary.kubeconfig" in str(args["kubeconfig"])
        for timeout in ("request_timeout", "wait_timeout", "wait_sleep"):
            assert str(args.get(timeout, "")).strip()

    def test_every_durable_phase_of_the_table_is_written(self):
        """§10, §12, §14, §15: the phase vocabulary the resume matrix depends on.

        Lead ruling: the six phases must appear as LITERAL `teardown_record.phase`
        values across the writer tasks. A templated phase would make the static
        vocabulary -- and the guard analysis the recovery_required ruling rests on --
        unreadable, so Task 2 is bound to one writer task per phase.
        """
        writers = checkpoint_writer_tasks({"mch": self.tasks})
        phases = {
            str(task[_CHECKPOINT_WRITER_MODULE]["teardown_record"]["phase"])
            for task in writers
            if "teardown_record" in task.get(_CHECKPOINT_WRITER_MODULE, {})
        }
        assert phases, "the MultiClusterHub teardown must write durable records"
        assert not [phase for phase in phases if "{{" in phase], "each durable phase needs its own literal writer task"
        literal = {"delete_started", "cr_absent", "drain_pending", "drained", "completed", "recovery_required"}
        assert literal <= phases

    def test_the_durable_write_precedes_the_delete(self):
        """§10: the delete_started record must exist before the cluster is mutated."""
        delete_index = index_of_task_using(self.tasks, "tomazb.acm_switchover.acm_uid_guarded_delete")
        writes = [
            index
            for index, task in enumerate(self.tasks)
            if _CHECKPOINT_WRITER_MODULE in _task_actions(task)
            and task[_CHECKPOINT_WRITER_MODULE].get("teardown_record", {}).get("phase") == "delete_started"
        ]
        assert writes, "no delete_started write found"
        assert min(writes) < delete_index

    def test_the_drain_loop_is_bounded_by_the_1200_second_contract(self):
        """§13: `retries: N` is N+1 attempts with N delays, so N x delay is the budget.

        Verified against ansible-core 2.16.14 (`ansible/executor/task_executor.py:626-638`).
        The two overridable variables are resolved here the way Ansible resolves them, so
        the pinned budget is the DEFAULT budget rather than a literal in the task file.
        """
        import jinja2

        loops = self._drain_loop_tasks()
        environment = jinja2.Environment()
        for task in loops:
            # Ansible's own implicit default is `retries: 3`, which would silently cap the
            # drain at 30 seconds. The budget has to be declared, not inherited.
            assert "retries" in task, "the drain loop must declare its retries, not inherit Ansible's default of 3"
            assert "delay" in task, "the drain loop must declare its delay"
            retries = int(environment.from_string(str(task["retries"])).render())
            delay = int(environment.from_string(str(task["delay"])).render())
            assert delay == 30
            assert retries * delay == 1200

    def _drain_loop_tasks(self) -> list:
        """The classification passes that RETRY -- the drain loop, never the final proof."""
        loops = [
            task
            for task in self._module_tasks("tomazb.acm_switchover.acm_pod_owner_classify")
            if "until" in task and task["tomazb.acm_switchover.acm_pod_owner_classify"].get("operation") == "classify"
        ]
        assert loops, "the drain pass must retry under an `until` condition"
        return loops

    def test_the_drain_loop_stops_on_every_terminal_condition(self):
        """§13: only ok + consistent identity + blocking Pods may retry."""
        for task in self._drain_loop_tasks():
            until = str(task["until"])
            assert "read_status" in until
            assert "identity_status" in until
            assert "blocking_count" in until
            assert "failed_when" not in task or "read_status" not in str(task.get("failed_when"))

    def test_a_stage_less_classify_error_reaches_the_recovery_write_and_a_pod_error_does_not(self):
        """§14 and the lead ruling, pinned by EVALUATING the guard, not by reading it.

        `read_error_stage` is `null` only when the classify module never reached a read:
        client construction failed (`acm_pod_owner_classify.py:245-246`) or an unexpected
        exception escaped (`:285-286`). Both go through the SHARED primary kubeconfig and
        context, so in a role run every earlier strict read fails closed first;
        `strict_read` itself raises nothing (`k8s_read.py:214-260`), so `classify_pass`
        always sets `namespace` or `pods` on an error it produced
        (`pod_owner_classify.py:352,357`). A partial recorded identity cannot reach the
        raw `recorded["name"]` indexing either: `checkpoint.py:504` requires the exact
        `operator_deployment` field set and `:506` requires non-empty strings, and
        `acm_pod_owner_classify.py:211-212` fails the task outright. The case is
        therefore unreachable from any harness input and is pinned statically.

        `when:` is the only admissible place for this decision: `failed_when` re-labels a
        task's outcome after it ran, and module arguments cannot stop a write -- only the
        task condition decides whether the recovery_required record is persisted at all.
        """
        writers = self._recovery_required_writers()
        for task in writers:
            for stage, expected in (("pods", False), ("namespace", True), (None, True)):
                rendered = self._render_guard(task, stage)
                assert rendered is expected, (
                    f"task {task.get('name')!r}: with read_error_stage={stage!r} the recovery_required write "
                    f"is {'reached' if rendered else 'skipped'}; the ruling says it must be "
                    f"{'reached' if expected else 'skipped'}"
                )

    def test_the_recovery_guard_analysis_rejects_the_inverse_ruling(self):
        """A self-test of the machinery the ruling above rests on.

        This one passes on arrival on purpose: it proves the guard renderer can tell the
        ruling from its inverse, which a substring search could not. The kill condition
        is the renderer answering the same verdicts for all three guards below.
        """

        def verdicts(*clauses):
            task = {
                "name": "synthetic",
                "when": list(clauses),
                _CHECKPOINT_WRITER_MODULE: {"teardown_record": {"phase": "recovery_required"}},
            }
            return [self._render_guard(task, stage) for stage in ("pods", "namespace", None)]

        # The ruling: a Pod-stage error is skipped, `namespace` and stage-less both write.
        assert verdicts(
            "acm_switchover_execution.mode == 'execute'",
            "not ansible_check_mode",
            "_acm_mch_pass.read_status == 'error'",
            "_acm_mch_pass.read_error_stage != 'pods'",
        ) == [False, True, True]
        # Its exact inverse, which the retired substring assertions accepted.
        assert verdicts("_acm_mch_pass.read_error_stage == 'pods'") == [True, False, False]
        # Namespace-only: the stage-less pass would silently lose its recovery transition.
        assert verdicts("_acm_mch_pass.read_error_stage == 'namespace'") == [False, True, False]
        # The Ansible filter spellings a real guard may use still render.
        assert verdicts(
            "(_acm_mch_pass.read_error_stage | default('', true)) != 'pods'",
            "not (ansible_check_mode | bool)",
        ) == [False, True, True]

    def _recovery_required_writers(self) -> list:
        writers = [
            task
            for task in checkpoint_writer_tasks({"mch": self.tasks})
            if str(task[_CHECKPOINT_WRITER_MODULE].get("teardown_record", {}).get("phase", "")) == "recovery_required"
        ]
        assert writers, "no recovery_required write found"
        return writers

    def _render_guard(self, task: dict, stage: Optional[str]) -> bool:
        """Evaluate one task's `when` clauses for a classify error carrying ``stage``.

        Every fact the expression names is stubbed: the classify result reports
        ``read_status == 'error'`` with the stage under test, execution is a real
        execute-mode run, and anything else the guard happens to consult is truthy. So
        the only thing that can vary the verdict is the read stage.
        """
        import jinja2
        from jinja2 import meta

        clauses = task.get("when", [])
        if not isinstance(clauses, list):
            clauses = [clauses]
        assert clauses, f"task {task.get('name')!r} writes recovery_required unconditionally"

        environment = jinja2.Environment()
        # `bool` and `ternary` are Ansible filters, not Jinja builtins; a guard is allowed
        # to use them, so the shim supplies them rather than failing to render.
        environment.filters["bool"] = lambda value: bool(value) and str(value).lower() not in ("false", "no", "0")
        environment.filters["ternary"] = lambda condition, yes, no=None: yes if condition else no
        environment.tests["truthy"] = bool
        environment.tests["falsy"] = lambda value: not value

        for clause in clauses:
            source = "{{ (" + str(clause) + ") }}"
            names = meta.find_undeclared_variables(environment.parse(source))
            context = {name: _GuardStub(stage) for name in names}
            for name in names:
                if "check_mode" in name:
                    context[name] = False
            if str(environment.from_string(source).render(**context)).strip() != "True":
                return False
        return True

    def test_the_family_publishes_its_change_and_prediction_facts(self):
        """§19: the role summary aggregates facts, not a delete loop's register."""
        published = {
            name
            for task in self.tasks
            for name in (task.get("ansible.builtin.set_fact") or {})
            if name in ("_acm_mch_changed", "_acm_mch_would_change")
        }
        assert published == {"_acm_mch_changed", "_acm_mch_would_change"}
        assert "_multiclusterhub_delete_results" not in self.text


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
    # The MultiClusterHub operator-identity surface: the CSV that owns the MCH CRD, the
    # install-strategy Deployment it names, and the ReplicaSet an owned Pod's controller
    # chain walks through. `acm_pod_owner_classify` reads all three and nothing else.
    "apps": (
        "v1",
        [
            {
                "name": "deployments",
                "singularName": "deployment",
                "namespaced": True,
                "kind": "Deployment",
                "verbs": ["get", "list"],
            },
            {
                "name": "replicasets",
                "singularName": "replicaset",
                "namespaced": True,
                "kind": "ReplicaSet",
                "verbs": ["get", "list"],
            },
        ],
    ),
    "operators.coreos.com": (
        "v1alpha1",
        [
            {
                "name": "clusterserviceversions",
                "singularName": "clusterserviceversion",
                "namespaced": True,
                "kind": "ClusterServiceVersion",
                "verbs": ["get", "list"],
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
        deployments: Optional[list] = None,
        replicasets: Optional[list] = None,
        clusterserviceversions: Optional[list] = None,
        named_read_status_by_plural: Optional[dict] = None,
        named_read_status_by_object: Optional[dict] = None,
        retain_after_delete_by_plural: Optional[dict] = None,
        post_delete_read_status_by_plural: Optional[dict] = None,
        read_status_sequences_by_plural: Optional[dict] = None,
        list_inventory_sequences_by_plural: Optional[dict] = None,
        list_revision_sequences_by_plural: Optional[dict] = None,
        api_resources_by_group: Optional[dict] = None,
    ):
        #: Overridable so a hub can serve a group/version that positively does NOT
        #: serve ``multiclusterobservabilities``. That is the only way to produce a
        #: real ``kind_not_served``: the module requires a well-formed APIResourceList
        #: without the plural, and a 404 on the discovery path is `error`, not absence.
        self.api_resources_by_group = dict(api_resources_by_group or _GROUP_API_RESOURCES)
        self.store: Dict[str, List[dict]] = {
            "multiclusterobservabilities": copy.deepcopy(multiclusterobservabilities),
            "multiclusterhubs": copy.deepcopy(multiclusterhubs),
            "managedclusters": copy.deepcopy(managedclusters),
            "namespaces": copy.deepcopy(namespaces),
            "clusterdeployments": [],
            "pods": copy.deepcopy(pods or []),
            "deployments": copy.deepcopy(deployments or []),
            "replicasets": copy.deepcopy(replicasets or []),
            "clusterserviceversions": copy.deepcopy(clusterserviceversions or []),
        }
        self.delete_status_by_plural = dict(delete_status_by_plural)
        self.read_status_by_plural = dict(read_status_by_plural)
        self.named_read_status_by_plural = dict(named_read_status_by_plural or {})
        #: Per-OBJECT named-GET status, keyed ``"<plural>/<name>"``. The per-plural map
        #: cannot express "this one Namespace read fails": refusing every ``namespaces``
        #: GET also refuses the standalone kube-system identity read.
        self.named_read_status_by_object = dict(named_read_status_by_object or {})
        self.retain_after_delete_by_plural = dict(retain_after_delete_by_plural or {})
        self.post_delete_read_status_by_plural = dict(post_delete_read_status_by_plural or {})
        self.read_status_sequences_by_plural = {
            plural: list(statuses) for plural, statuses in (read_status_sequences_by_plural or {}).items()
        }
        #: Per-LIST inventories, popped one per unfiltered list read, so a Pod that
        #: appears only in a later pass can be served. The popped value replaces the
        #: whole store for that plural; the last one stays in effect.
        self.list_inventory_sequences_by_plural = {
            plural: [copy.deepcopy(items) for items in inventories]
            for plural, inventories in (list_inventory_sequences_by_plural or {}).items()
        }
        #: Per-LIST ``metadata.resourceVersion`` values, popped the same way. Without
        #: these every list read reports the same revision, so "the completed record
        #: carries the FINAL pass's revision" cannot be told from "any pass's".
        self.list_revision_sequences_by_plural = {
            plural: list(revisions) for plural, revisions in (list_revision_sequences_by_plural or {}).items()
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
        """The objects one read selects.

        A named read filters by namespace and name. A LIST filters by namespace, EXCEPT
        that malformed members -- a non-mapping item, or an item whose ``metadata`` is
        not a mapping -- are served by every LIST regardless of its namespace, because
        such an object carries no namespace to filter on and the role's fail-closed
        contracts exist precisely to observe it. This is wider than the original
        cluster-scoped-only rule, so a namespaced fixture (the MultiClusterHub LIST) now
        sees malformed members it previously never received.
        """
        items = self.store.get(plural, [])
        selected = []
        for item in items:
            # Preserve malformed inventory objects on ANY list read so role fail-closed
            # contracts can observe them (PR D Blocker 2). The namespace filter cannot
            # apply to an object with no readable metadata, and the MultiClusterHub LIST
            # is namespaced -- requiring `namespace is None` too silently dropped every
            # malformed MCH member before the role ever saw it.
            if not isinstance(item, dict):
                if name is None:
                    selected.append(item)
                continue
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                if name is None:
                    selected.append(item)
                continue
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
                    for group, (version, _) in self.api_resources_by_group.items()
                ],
            }
        for group, (version, resources) in self.api_resources_by_group.items():
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
                elif name is not None:
                    read_status = api.named_read_status_by_object.get(
                        f"{plural}/{name}",
                        api.named_read_status_by_plural.get(plural, 200),
                    )
                else:
                    read_status = api.read_status_by_plural.get(plural, 200)
                if read_status != 200:
                    self._write_json(
                        _status_body(read_status, f"fixture refused GET of {plural}"),
                        status=read_status,
                    )
                    return
                if name is None and api.list_inventory_sequences_by_plural.get(plural):
                    with api._lock:
                        api.store[plural] = api.list_inventory_sequences_by_plural[plural].pop(0)
                selected = api._select(plural, namespace, name)
                if name is not None:
                    if not selected:
                        self._write_json(_status_body(404, f"{plural} {name} not found"), status=404)
                        return
                    self._write_json(copy.deepcopy(selected[0]))
                    return
                revisions = api.list_revision_sequences_by_plural.get(plural)
                self._write_json(
                    {
                        "apiVersion": _API_VERSION_BY_PLURAL[plural],
                        "kind": f"{_KIND_BY_PLURAL[plural]}List",
                        "metadata": {"resourceVersion": revisions.pop(0) if revisions else "1"},
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
                # The real API server enforces a precondition only when the request
                # carries one. A DELETE with no ``preconditions.uid`` is name-only --
                # exactly what the guarded-delete module exists to replace -- and the
                # fake must serve it rather than answering 409, or "a name-only delete
                # removes a replacement" becomes unprovable here.
                #
                # CHANGED for E6: a name-only DELETE is no longer refused. Before the
                # fixture MultiClusterHub carried a UID, `None == None` made it succeed
                # by accident; the rule below makes that deliberate. A test that wants a
                # refusal must send a MISMATCHING uid, not omit one.
                if expected_uid is not None and expected_uid != actual_uid:
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


#: The fixture MultiClusterHub identity. A live MCH always has a UID, and the guarded
#: delete's whole contract is the precondition it carries, so the fixture object must
#: carry one too.
MCH_UID = "mch-uid-1"
#: The fixture operator-identity UIDs, shared by the Deployment/ReplicaSet/CSV builders
#: and by the recorded identity a durable record seeds.
OPERATOR_DEPLOYMENT_NAME = "multiclusterhub-operator"
OPERATOR_DEPLOYMENT_UID = "operator-deployment-uid-1"
OPERATOR_REPLICASET_NAME = "multiclusterhub-operator-7d9f"
OPERATOR_REPLICASET_UID = "operator-replicaset-uid-1"
OPERATOR_CSV_NAME = "advanced-cluster-management.v2.13.0"
OPERATOR_CSV_UID = "operator-csv-uid-1"


def _mch_object(name: str, uid: str = MCH_UID) -> dict:
    return {
        "apiVersion": "operator.open-cluster-management.io/v1",
        "kind": "MultiClusterHub",
        "metadata": {
            "name": name,
            "namespace": ACM_NAMESPACE,
            "uid": uid,
            "resourceVersion": "1",
        },
    }


#: The canonical MultiClusterHub teardown record key, built through the real helper.
MCH_KEY = teardown_key("operator.open-cluster-management.io/v1", "MultiClusterHub", ACM_NAMESPACE, "multiclusterhub")


def mch_operator_deployment_identity(
    *,
    key: str = MCH_KEY,
    expected_uid: str = MCH_UID,
    deployment_uid: str = OPERATOR_DEPLOYMENT_UID,
) -> dict:
    """A durable ``operator_deployment`` identity in the exact §10.2.2 record shape."""
    return {
        "namespace": ACM_NAMESPACE,
        "name": OPERATOR_DEPLOYMENT_NAME,
        "uid": deployment_uid,
        "discovery_method": OPERATOR_IDENTITY_DISCOVERY_METHOD,
        "captured_at": "2026-09-17T00:00:00+00:00",
        "csv": {
            "namespace": ACM_NAMESPACE,
            "name": OPERATOR_CSV_NAME,
            "uid": OPERATOR_CSV_UID,
            "owned_crd": MCH_OWNED_CRD,
        },
        "mch_teardown_key": key,
        "mch_expected_uid": expected_uid,
    }


def mch_identity_unavailable(
    *,
    key: str = MCH_KEY,
    expected_uid: str = MCH_UID,
    reason: str = "csv_absent",
) -> dict:
    """A durable ``operator_identity_unavailable`` outcome in the exact §10.2.3 shape."""
    return {
        "reason": reason,
        "discovery_method": OPERATOR_IDENTITY_DISCOVERY_METHOD,
        "captured_at": "2026-09-17T00:00:00+00:00",
        "evidence_summary": "No ClusterServiceVersion owning the MultiClusterHub CRD was found.",
        "mch_teardown_key": key,
        "mch_expected_uid": expected_uid,
    }


def mch_teardown_record(
    phase: str, *, identity: Optional[dict] = None, expected_uid: str = MCH_UID, **evidence
) -> dict:
    """One durable MCH teardown record the real validator accepts.

    An MCH record carries exactly one identity outcome at EVERY phase, so a seeded
    record without one fails validation at the checkpoint enter -- before the role
    reaches any MCH task.
    """
    record: Dict[str, Any] = {"expected_uid": expected_uid, "phase": phase}
    identity = identity if identity is not None else mch_operator_deployment_identity(expected_uid=expected_uid)
    if "reason" in identity:
        record["operator_identity_unavailable"] = identity
    else:
        record["operator_deployment"] = identity
    record.update(evidence)
    return record


def _operator_csv(
    *,
    name: str = OPERATOR_CSV_NAME,
    uid: str = OPERATOR_CSV_UID,
    deployment_names: Optional[List[str]] = None,
    owned_crd: str = MCH_OWNED_CRD,
    phase: str = "Succeeded",
) -> dict:
    """The OLM ClusterServiceVersion that identifies the MCH operator Deployment.

    The shape is the one proven on the real client in
    ``tests/integration/test_pod_owner_classify_runtime.py`` -- owned-CRD list, install
    strategy ``deployment``, ``status.phase`` -- not a new one.
    """
    deployments = [{"name": deployment} for deployment in (deployment_names or [OPERATOR_DEPLOYMENT_NAME])]
    return {
        "apiVersion": f"{CSV_API_GROUP}/{CSV_API_VERSION}",
        "kind": "ClusterServiceVersion",
        "metadata": {"name": name, "namespace": ACM_NAMESPACE, "uid": uid, "resourceVersion": "csv-3"},
        "spec": {
            "customresourcedefinitions": {"owned": [{"name": owned_crd}]},
            "install": {"strategy": "deployment", "spec": {"deployments": deployments}},
        },
        "status": {"phase": phase},
    }


def _operator_deployment(
    *,
    name: str = OPERATOR_DEPLOYMENT_NAME,
    uid: Optional[str] = OPERATOR_DEPLOYMENT_UID,
    resource_version: str = "deploy-7",
) -> dict:
    metadata: Dict[str, Any] = {"name": name, "namespace": ACM_NAMESPACE, "resourceVersion": resource_version}
    if uid is not None:
        metadata["uid"] = uid
    return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": metadata}


def _operator_replicaset(
    *,
    name: str = OPERATOR_REPLICASET_NAME,
    uid: str = OPERATOR_REPLICASET_UID,
    deployment_name: str = OPERATOR_DEPLOYMENT_NAME,
    deployment_uid: str = OPERATOR_DEPLOYMENT_UID,
) -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "ReplicaSet",
        "metadata": {
            "name": name,
            "namespace": ACM_NAMESPACE,
            "uid": uid,
            "resourceVersion": "rs-5",
            "ownerReferences": [
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": deployment_name,
                    "uid": deployment_uid,
                    "controller": True,
                }
            ],
        },
    }


def _acm_pod_object(name: str, owner: Optional[dict] = None, *, unprefixed: bool = False) -> dict:
    """One Pod in the ACM namespace, with an optional single controller owner reference.

    Every ACM-namespace Pod this harness seeds keeps the ``multiclusterhub-operator``
    name prefix unless the caller passes ``unprefixed=True``, which is admissible only
    on a run that resolves no live MultiClusterHub (see ``_acm_pod_name``). The
    LEGACY role waits for Pods with ``retries: 120, delay: 10`` and excludes only names
    matching that prefix, so one differently named Pod makes the role poll for 1200s and
    blow the 600s subprocess timeout -- a fixture-level RuntimeError instead of a failing
    assertion. The prefix is exactly what E6 retires as an ownership signal, so a spoof
    Pod that carries it is also the sharper fixture.
    """
    metadata: Dict[str, Any] = {
        "name": _acm_pod_name(name, unprefixed=unprefixed),
        "namespace": ACM_NAMESPACE,
        "resourceVersion": "1",
    }
    if owner is not None:
        metadata["ownerReferences"] = [owner]
    return {"apiVersion": "v1", "kind": "Pod", "metadata": metadata}


def _acm_pod_name(name: str, *, unprefixed: bool = False) -> str:
    """Guard the prefix rule, with a deliberate opt-out.

    ``unprefixed=True`` is safe ONLY on a run that resolves no live MultiClusterHub
    (``mch_present=False``): the legacy Pod wait is guarded on a non-empty MCH
    inventory, so it never runs there and cannot poll for 1200s.
    """
    if unprefixed or name.startswith(ACM_OPERATOR_POD_PREFIX):
        return name
    raise ValueError(
        f"ACM-namespace fixture Pod {name!r} must keep the {ACM_OPERATOR_POD_PREFIX!r} prefix, or pass "
        "unprefixed=True on a run with no live MultiClusterHub; see _acm_pod_object for why"
    )


def _operator_owned_pod(
    name: str = "multiclusterhub-operator-7d9f-abcde",
    *,
    unprefixed: bool = False,
    replicaset_name: str = OPERATOR_REPLICASET_NAME,
    replicaset_uid: str = OPERATOR_REPLICASET_UID,
) -> dict:
    """A Pod whose controller chain really reaches the recorded operator Deployment.

    ``replicaset_name``/``replicaset_uid`` name the intermediate ReplicaSet, so a rolling
    update -- two ReplicaSets of the SAME Deployment, one Pod each -- can be seeded.
    """
    return _acm_pod_object(
        name,
        owner={
            "apiVersion": "apps/v1",
            "kind": "ReplicaSet",
            "name": replicaset_name,
            "uid": replicaset_uid,
            "controller": True,
        },
        unprefixed=unprefixed,
    )


def _spoof_pod(name: str = "multiclusterhub-operator-spoof", *, unprefixed: bool = False) -> dict:
    """The operator NAME with no owner chain at all: blocking, whatever the prefix says."""
    return _acm_pod_object(name, unprefixed=unprefixed)


def _job_owned_pod(name: str = "multiclusterhub-operator-backup-job-x") -> dict:
    return _acm_pod_object(
        name,
        owner={"apiVersion": "batch/v1", "kind": "Job", "name": "mch-backup", "uid": "job-uid-1", "controller": True},
    )


def _statefulset_owned_pod(name: str = "multiclusterhub-operator-sts-0") -> dict:
    return _acm_pod_object(
        name,
        owner={
            "apiVersion": "apps/v1",
            "kind": "StatefulSet",
            "name": "mch-sts",
            "uid": "sts-uid-1",
            "controller": True,
        },
    )


def _unrelated_replicaset_pod(name: str = "multiclusterhub-operator-other-rs-1") -> dict:
    """Owned by a ReplicaSet that exists but is controlled by another Deployment."""
    return _acm_pod_object(
        name,
        owner={
            "apiVersion": "apps/v1",
            "kind": "ReplicaSet",
            "name": "unrelated-rs",
            "uid": "unrelated-rs-uid",
            "controller": True,
        },
    )


def _unrelated_replicaset() -> dict:
    return _operator_replicaset(
        name="unrelated-rs",
        uid="unrelated-rs-uid",
        deployment_name="some-other-operator",
        deployment_uid="some-other-uid",
    )


def _request_shape(request: dict) -> tuple:
    """``(VERB, API_GROUP, RESOURCE_PLURAL, NAMESPACE_OR_NONE)`` for one logged request.

    Identical to the normaliser ``tests/test_decommission.py`` builds the measured
    Python MCH request table from, so the two measured sets are directly comparable as
    sets rather than through a hand-written table. A LIST is a GET with no object name.
    """
    path = request["path"]
    parsed = _split_resource_path(path)
    if parsed is None:
        return (request["method"], None, None, None)
    plural, namespace, name = parsed
    segments = [segment for segment in path.split("/") if segment]
    group = "" if segments[0] == "api" else segments[1]
    verb = request["method"]
    if verb == "GET" and name is None:
        verb = "LIST"
    return (verb, group, plural, namespace)


def _managed_cluster_object(name: str, uid: Optional[str] = None) -> dict:
    return {
        "apiVersion": "cluster.open-cluster-management.io/v1",
        "kind": "ManagedCluster",
        "metadata": {
            "name": name,
            "uid": uid or f"mc-uid-{name}",
            "resourceVersion": "1",
        },
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


def _destination_api(destination_mco: str, destination_namespace: str) -> "FakeDecommissionAPI":
    """A second fake hub serving exactly the four reads the C5 gate performs there.

    ``absent_crd`` drops ``multiclusterobservabilities`` from the served group, which
    is the only way to produce a genuine ``kind_not_served``; ``absent`` keeps the CRD
    and serves an empty inventory. Both are positive absence to the gate, and the two
    are distinct fixtures on purpose -- the destination deliberately does NOT reuse
    the source's clean-skip rule, so both have to be exercised.
    """
    api_resources = copy.deepcopy(_GROUP_API_RESOURCES)
    if destination_mco == "absent_crd":
        api_resources["observability.open-cluster-management.io"] = (
            _GROUP_API_RESOURCES["observability.open-cluster-management.io"][0],
            [],
        )
    namespaces = [_namespace_object("kube-system", uid="harness-secondary-uid")]
    if destination_namespace == "present":
        namespaces.append(_namespace_object("open-cluster-management-observability"))
    return FakeDecommissionAPI(
        multiclusterobservabilities=(
            [_mco_object("observability", "destination-mco-uid")] if destination_mco == "present" else []
        ),
        multiclusterhubs=[],
        managedclusters=[],
        namespaces=namespaces,
        pods=[],
        delete_status_by_plural={},
        read_status_by_plural=({"multiclusterobservabilities": 403} if destination_mco == "unverifiable" else {}),
        named_read_status_by_plural=({"namespaces": 403} if destination_namespace == "unverifiable" else {}),
        api_resources_by_group=api_resources,
    )


def run_decommission_role(
    *,
    check_mode: bool = False,
    execution_mode: str = "execute",
    allow_unknown_execution_mode: bool = False,
    observability_outcome: Optional[str] = None,
    managed_clusters_outcome: Optional[str] = None,
    multiclusterhub_outcome: Optional[str] = None,
    mco_present: Optional[bool] = None,
    mch_present: Optional[bool] = None,
    managed_clusters: Optional[List[str]] = None,
    managed_cluster_objects: Optional[List[dict]] = None,
    observability_namespace: str = "present",
    observability_read_status: int = 200,
    mco_inventory: Optional[List[dict]] = None,
    mco_record: Optional[dict] = None,
    mco_named_read_status: int = 200,
    mco_retain_after_delete: bool = False,
    mco_post_delete_read_status: Optional[int] = None,
    observability_pods: Optional[List[str]] = None,
    pod_read_statuses: Optional[List[int]] = None,
    mco_read_statuses: Optional[List[int]] = None,
    configured_has_observability: Optional[Union[bool, str]] = None,
    checkpoint_available: bool = True,
    standalone_playbook: bool = False,
    integrated_finalization: bool = False,
    integrated_finalization_secondary: bool = False,
    destination_mco: Optional[str] = None,
    destination_namespace: Optional[str] = None,
    acknowledge_observability_not_migrated: bool = False,
    skip_gitops_check: bool = False,
    primary_cluster_uid: Optional[str] = "harness-standalone-primary-uid",
    seeded_operation_identity: Optional[dict] = None,
    managed_cluster_teardown_records: Optional[Dict[str, dict]] = None,
    checkpoint_dir_readonly: bool = False,
    mch_inventory: Optional[List[dict]] = None,
    mch_record: Optional[dict] = None,
    mch_teardown_records: Optional[Dict[str, dict]] = None,
    mch_read_status: int = 200,
    mch_kind_served: bool = True,
    mch_named_read_status: int = 200,
    mch_post_delete_read_status: Optional[int] = None,
    mch_drain_retries: Optional[int] = None,
    mch_drain_delay: Optional[int] = None,
    acm_pods: Optional[List[dict]] = None,
    acm_pod_passes: Optional[List[List[dict]]] = None,
    pod_list_revisions: Optional[List[str]] = None,
    operator_csvs: Optional[List[dict]] = None,
    operator_deployments: Optional[List[dict]] = None,
    operator_replicasets: Optional[List[dict]] = None,
    csv_read_status: int = 200,
    object_read_statuses: Optional[Dict[str, int]] = None,
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

    if not allow_unknown_execution_mode and execution_mode not in ("execute", "validate", "dry_run"):
        raise ValueError(f"execution_mode={execution_mode!r} is not one of ('execute', 'validate', 'dry_run')")
    entry_points = [standalone_playbook, integrated_finalization, integrated_finalization_secondary]
    if sum(1 for flag in entry_points if flag) > 1:
        raise ValueError(
            "standalone_playbook, integrated_finalization and integrated_finalization_secondary "
            "are different entry points; pick one"
        )
    _DESTINATION_STATES = ("present", "absent", "absent_crd", "unverifiable")
    for label, state in (("destination_mco", destination_mco), ("destination_namespace", destination_namespace)):
        if state is not None and state not in _DESTINATION_STATES:
            raise ValueError(f"{label}={state!r} must be one of {_DESTINATION_STATES}")
    if destination_namespace == "absent_crd":
        raise ValueError("destination_namespace has no CRD; use 'absent'")
    # A destination hub exists only when this run declares one. Every pre-C5b row keeps
    # its exact request footprint: with no secondary, delete_observability.yml never
    # includes the gate and no destination fake is ever started.
    with_destination = (
        destination_mco is not None or destination_namespace is not None or integrated_finalization_secondary
    )
    if acknowledge_observability_not_migrated and not with_destination:
        raise ValueError("acknowledge_observability_not_migrated only means something with a destination hub")
    if with_destination:
        destination_mco = destination_mco or "present"
        destination_namespace = destination_namespace or "present"
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
    if managed_cluster_objects is not None and managed_clusters is not None:
        raise ValueError("pass managed_clusters names or managed_cluster_objects, not both")
    if managed_clusters_outcome is not None:
        _check_outcome("managed_clusters_outcome", managed_clusters_outcome)
        if managed_clusters_outcome == "not_requested":
            raise ValueError(
                "managed_clusters_outcome='not_requested' is unreachable: the role includes "
                "delete_managed_clusters.yml unconditionally."
            )
        if managed_clusters_outcome == "precondition_noop":
            if managed_clusters or managed_cluster_objects:
                _conflict(
                    "managed_clusters",
                    managed_clusters_outcome,
                    managed_clusters or managed_cluster_objects,
                )
            managed_clusters = []
            managed_cluster_objects = []
        else:
            if managed_clusters == [] and managed_cluster_objects == []:
                _conflict("managed_clusters", managed_clusters_outcome, managed_clusters)
            if managed_clusters is None and managed_cluster_objects is None:
                managed_clusters = ["cluster-a"]
            if managed_clusters_outcome == "failed":
                managed_clusters_delete_status = 500
    if managed_cluster_objects is None and managed_clusters is None:
        managed_clusters = ["cluster-a"]
    if managed_clusters is None:
        managed_clusters = []

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
    if mch_inventory is not None and mch_present is not True:
        raise ValueError("mch_inventory supplies the inventory and requires mch_present=True")
    if mch_record is not None and mch_teardown_records is not None:
        raise ValueError("pass one mch_record or the mch_teardown_records mapping, not both")
    if not mch_kind_served and (mch_present or mch_inventory):
        raise ValueError("mch_kind_served=False serves no MultiClusterHub kind at all; it takes no inventory")
    if acm_pods is not None and acm_pod_passes is not None:
        raise ValueError("pass a fixed acm_pods inventory or the acm_pod_passes sequence, not both")

    namespaces = [_namespace_object("open-cluster-management")]
    if observability_namespace == "present":
        namespaces.append(_namespace_object("open-cluster-management-observability"))
    # The standalone identity read is a real GET of v1/Namespace kube-system. Serving
    # it unconditionally keeps the fake API honest for role-only runs too: if the role
    # ever starts issuing that read, the request log shows it rather than a 404.
    # ``primary_cluster_uid=None`` deliberately serves the namespace WITHOUT a uid, so
    # the empty/malformed-uid refusal can be exercised against a real response.
    namespaces.append(_namespace_object("kube-system", uid=primary_cluster_uid))

    observability_pod_store = [_observability_pod_object(name) for name in (observability_pods or [])]
    post_delete_read_statuses: Dict[str, int] = {}
    if mco_post_delete_read_status is not None:
        post_delete_read_statuses["multiclusterobservabilities"] = mco_post_delete_read_status
    if mch_post_delete_read_status is not None:
        post_delete_read_statuses["multiclusterhubs"] = mch_post_delete_read_status
    managedcluster_store = (
        copy.deepcopy(managed_cluster_objects)
        if managed_cluster_objects is not None
        else [_managed_cluster_object("local-cluster")] + [_managed_cluster_object(name) for name in managed_clusters]
    )

    repo_root = ROLES_DIR.parents[3]
    workspace = pathlib.Path(tempfile.mkdtemp(prefix="acm-decommission-harness-"))
    # Dropping the plural from the served group is the only way to produce a genuine
    # `kind_not_served`: a 404 on the discovery path is `error`, not positive absence.
    source_api_resources = copy.deepcopy(_GROUP_API_RESOURCES)
    if not mch_kind_served:
        source_api_resources["operator.open-cluster-management.io"] = (
            _GROUP_API_RESOURCES["operator.open-cluster-management.io"][0],
            [],
        )
    api = FakeDecommissionAPI(
        api_resources_by_group=source_api_resources,
        multiclusterobservabilities=(
            copy.deepcopy(mco_inventory)
            if mco_inventory is not None
            else ([_mco_object("observability")] if mco_present else [])
        ),
        multiclusterhubs=(
            copy.deepcopy(mch_inventory)
            if mch_inventory is not None
            else ([_mch_object("multiclusterhub")] if mch_present else [])
        ),
        managedclusters=managedcluster_store,
        namespaces=namespaces,
        pods=observability_pod_store + copy.deepcopy(acm_pods or []),
        deployments=copy.deepcopy(
            operator_deployments if operator_deployments is not None else [_operator_deployment()]
        ),
        replicasets=copy.deepcopy(
            operator_replicasets if operator_replicasets is not None else [_operator_replicaset()]
        ),
        clusterserviceversions=copy.deepcopy(operator_csvs if operator_csvs is not None else [_operator_csv()]),
        named_read_status_by_object=dict(object_read_statuses or {}),
        list_inventory_sequences_by_plural=(
            {"pods": [observability_pod_store + pass_pods for pass_pods in acm_pod_passes]} if acm_pod_passes else {}
        ),
        list_revision_sequences_by_plural={"pods": list(pod_list_revisions or [])},
        delete_status_by_plural={
            "multiclusterobservabilities": obs_delete_status,
            "managedclusters": managed_clusters_delete_status,
            "multiclusterhubs": mch_delete_status,
        },
        read_status_by_plural={
            "multiclusterobservabilities": observability_read_status,
            "multiclusterhubs": mch_read_status,
            "clusterserviceversions": csv_read_status,
        },
        named_read_status_by_plural={
            "multiclusterobservabilities": mco_named_read_status,
            "multiclusterhubs": mch_named_read_status,
        },
        retain_after_delete_by_plural={"multiclusterobservabilities": mco_retain_after_delete},
        post_delete_read_status_by_plural=post_delete_read_statuses,
        read_status_sequences_by_plural={
            "pods": pod_read_statuses or [],
            # Per-list-read statuses on the source hub, so the gate's own fresh read
            # can fail where the phase machine's earlier read succeeded. That is the
            # only way to reach the gate's source-unverifiable branch: the C4 read
            # fails the whole substep first when it is the one that breaks.
            "multiclusterobservabilities": mco_read_statuses or [],
        },
    )
    destination_api = _destination_api(destination_mco, destination_namespace) if with_destination else None
    try:
        kubeconfig = workspace / "primary.kubeconfig"
        _write_fixture_kubeconfig(kubeconfig, "primary-hub", api.url)
        secondary_kubeconfig = workspace / "secondary.kubeconfig"
        if destination_api is not None:
            _write_fixture_kubeconfig(secondary_kubeconfig, "secondary-hub", destination_api.url)

        summary_path = workspace / "decommission-summary.json"
        checkpoint_dir = workspace / "checkpoint-state"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = checkpoint_dir / "checkpoint.json"
        seeded_operational_data: Dict[str, Any] = {"harness_seed": "unchanged"}
        if mco_record is not None:
            seeded_operational_data["decommission_teardown_records"] = {
                "observability.open-cluster-management.io/v1beta2/MultiClusterObservability//observability": copy.deepcopy(
                    mco_record
                )
            }
        if managed_cluster_teardown_records:
            records = seeded_operational_data.setdefault("decommission_teardown_records", {})
            for key, record in managed_cluster_teardown_records.items():
                records[key] = copy.deepcopy(record)
        seeded_mch_records = (
            {MCH_KEY: mch_record} if mch_record is not None else copy.deepcopy(mch_teardown_records or {})
        )
        if seeded_mch_records:
            records = seeded_operational_data.setdefault("decommission_teardown_records", {})
            for key, record in seeded_mch_records.items():
                records[key] = copy.deepcopy(record)
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
            "acm_switchover_features": {
                "skip_rbac_validation": True,
                "skip_gitops_check": skip_gitops_check,
            },
            "acm_switchover_decommission": {
                "confirmed": True,
                "interactive": False,
                "has_observability": has_observability,
                "acknowledge_observability_not_migrated": acknowledge_observability_not_migrated,
            },
        }
        if mch_drain_retries is not None:
            vars_payload["acm_switchover_mch_drain_retries"] = mch_drain_retries
        if mch_drain_delay is not None:
            vars_payload["acm_switchover_mch_drain_delay"] = mch_drain_delay
        if destination_api is not None:
            vars_payload["acm_switchover_hubs"]["secondary"] = {
                "context": "secondary-hub",
                "kubeconfig": str(secondary_kubeconfig),
            }
        if integrated_finalization_secondary:
            vars_payload["acm_switchover_operation"] = {
                "restore_only": False,
                "method": "passive",
                "old_hub_action": "secondary",
                "activation_method": "patch",
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
        if integrated_finalization_secondary:
            # The REAL secondary-disposition adapter, loaded from the shipped
            # finalization role rather than copied here.
            playbook_path.write_text(
                yaml.safe_dump(
                    [
                        {
                            "hosts": "localhost",
                            "connection": "local",
                            "gather_facts": False,
                            "tasks": [
                                {
                                    "name": f"{_HARNESS_TASK_PREFIX}run the real old-hub observability adapter",
                                    "ansible.builtin.include_role": {
                                        "name": "tomazb.acm_switchover.finalization",
                                        "tasks_from": "disable_old_hub_observability.yml",
                                    },
                                }
                            ],
                        }
                    ],
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
        elif integrated_finalization:
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

        if checkpoint_dir_readonly and checkpoint_available:
            # Reads still succeed; per-target teardown-record writes must fail closed.
            os.chmod(checkpoint_dir, 0o555)

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
            "gate": facts.get("acm_switchover_observability_gate"),
            # The destination hub's whole request log, so "no destination request was
            # issued" is proved from traffic rather than from the absence of a fact.
            "destination_requests": destination_api.requests if destination_api is not None else [],
            # Not in the B4.1 mapping. Without it "the play still fails" is asserted
            # nowhere, and a refactor that swallowed the failure would go unnoticed.
            "returncode": completed.returncode,
        }
    finally:
        api.close()
        if destination_api is not None:
            destination_api.close()
        if checkpoint_dir_readonly and checkpoint_available:
            try:
                os.chmod(checkpoint_dir, 0o755)
            except OSError:
                pass
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


def test_playbook_declares_the_standalone_decommission_discriminator():
    """The standalone entry point names itself so validation can refuse the ack there.

    `validate_operation_inputs` reads `operation.decommission` as the collection's
    equivalent of the Python CLI's `--decommission`; nothing else in the collection
    sets it, so without this pre_task the standalone refusal is unreachable.

    Kill condition: dropping the fact, or setting a different key.
    """
    play = yaml.safe_load(DECOMMISSION_PLAYBOOK.read_text())[0]
    facts = [
        task["ansible.builtin.set_fact"] for task in play.get("pre_tasks", []) if "ansible.builtin.set_fact" in task
    ]
    operation_facts = [fact["acm_switchover_operation"] for fact in facts if "acm_switchover_operation" in fact]

    assert operation_facts, "playbooks/decommission.yml must declare the standalone discriminator"
    assert "combine({'decommission': true})" in operation_facts[0]


def test_playbook_declares_itself_standalone_with_a_scalar_play_var():
    """The gate include reads a scalar play var, not the combined operation mapping.

    A play-level var is set before any task runs and cannot be undone by an include's
    own vars, which is what lets ``delete_observability.yml`` tell the standalone
    entry point apart from an integrated switchover's decommission disposition.

    Kill condition: dropping the play var, renaming it, or setting it to false.
    """
    play = yaml.safe_load(DECOMMISSION_PLAYBOOK.read_text())[0]

    assert play.get("vars", {}).get("acm_switchover_standalone_decommission") is True


def test_playbook_refuses_an_acknowledgement_it_cannot_honour():
    """Exact Python parity: ``--decommission`` rejects
    ``--acknowledge-observability-not-migrated``.

    Kill condition: dropping the assert, or asserting a different key.
    """
    play = yaml.safe_load(DECOMMISSION_PLAYBOOK.read_text())[0]
    asserts = [task["ansible.builtin.assert"] for task in play.get("pre_tasks", []) if "ansible.builtin.assert" in task]
    acknowledgement = [
        assertion for assertion in asserts if "acknowledge_observability_not_migrated" in str(assertion.get("that"))
    ]

    assert acknowledgement, "the standalone playbook must refuse the acknowledgement"
    fail_msg = str(acknowledgement[0].get("fail_msg", ""))
    assert "acm_switchover_decommission.acknowledge_observability_not_migrated" in fail_msg


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
    """C4/D4 contribute fresh per-family predictions to the shared result."""
    publish = task_named(decommission_task_files["main"], "Publish decommission result")
    result = publish["ansible.builtin.set_fact"]["acm_switchover_decommission_result"]
    assert "_acm_mco_would_change" in str(result["would_change"])
    assert "_acm_mc_would_change" in str(result["would_change"])
    assert "_acm_mch_would_change" in str(result["would_change"])


def test_managed_cluster_actual_change_uses_family_fact_not_k8s_loop_register():
    """PR D retires `_managed_cluster_delete_results`; changed must read `_acm_mc_changed`."""
    publish = task_named(decommission_task_files["main"], "Publish decommission result")
    changed = str(publish["ansible.builtin.set_fact"]["acm_switchover_decommission_result"]["changed"])
    assert "_acm_mc_changed" in changed
    assert "_managed_cluster_delete_results" not in changed


def test_multiclusterhub_actual_change_uses_family_fact_not_k8s_loop_register():
    """E6 retires `_multiclusterhub_delete_results`; changed must read `_acm_mch_changed`."""
    publish = task_named(decommission_task_files["main"], "Publish decommission result")
    changed = str(publish["ansible.builtin.set_fact"]["acm_switchover_decommission_result"]["changed"])
    assert "_acm_mch_changed" in changed
    assert "_multiclusterhub_delete_results" not in changed


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

    # `destination_mco` was this guard's example until Task C5b added it deliberately;
    # the example has to be a keyword the harness still does not model.
    with pytest.raises(TypeError):
        run_decommission_role(clusterdeployment_preserve_on_delete=True)  # type: ignore[call-arg]


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


def test_managed_cluster_check_mode_issues_no_delete_and_predicts_change():
    """PR D dry-run/check-mode: fresh MC reads, no writers, no DELETE, would_change only."""
    result = run_decommission_role(
        check_mode=True,
        observability_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
        managed_clusters=["spoke-a", "spoke-b"],
    )
    summary = result["acm_switchover_decommission_result"]
    assert result["delete_calls"] == []
    assert summary["changed"] is False
    assert summary["would_change"] is True
    assert summary["substeps"] == {}
    assert result["returncode"] == 0
    managed_gets = [
        request for request in result["requests"] if request["method"] == "GET" and "managedclusters" in request["path"]
    ]
    assert managed_gets, "check mode must still perform a fresh ManagedCluster inventory read"


def test_managed_cluster_empty_inventory_is_precondition_noop():
    result = run_decommission_role(
        managed_clusters_outcome="precondition_noop",
        observability_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    summary = result["acm_switchover_decommission_result"]
    assert summary["status"] == "pass"
    assert summary["substeps"]["managed_clusters"] == "precondition_noop"
    assert not [call for call in result["delete_calls"] if "managedclusters" in call["path"]]


def test_managed_cluster_uid_guarded_delete_carries_recorded_uid():
    result = run_decommission_role(
        managed_clusters=["spoke-a"],
        observability_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    mc_deletes = [call for call in result["delete_calls"] if "managedclusters/spoke-a" in call["path"]]
    assert mc_deletes
    assert mc_deletes[0]["expected_uid"] == "mc-uid-spoke-a"
    assert "local-cluster" not in str(result["delete_calls"])
    summary = result["acm_switchover_decommission_result"]
    assert summary["substeps"]["managed_clusters"] == "completed"
    assert summary["changed"] is True


def test_managed_cluster_survivor_aggregation_lists_failures():
    """A failed target is collected; the family fails once rather than raising mid-batch."""
    result = run_decommission_role(
        managed_clusters=["spoke-a", "spoke-b"],
        managed_clusters_outcome="failed",
        observability_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    summary = result["acm_switchover_decommission_result"]
    assert summary["status"] == "fail"
    assert summary["substeps"]["managed_clusters"] == "failed"
    assert result["returncode"] != 0
    # Both targets were attempted (aggregation), then the family failed closed.
    assert {
        call["path"].rsplit("/", 1)[-1] for call in result["delete_calls"] if "managedclusters/" in call["path"]
    } == {
        "spoke-a",
        "spoke-b",
    }
    assert not [call for call in result["delete_calls"] if "multiclusterhubs" in call["path"]]
    combined = " ".join(str(task.get("result", {}).get("msg", "")) for task in result["tasks"] if task.get("failed"))
    assert "survivors" in combined.lower()
    assert "spoke-a" in combined and "spoke-b" in combined


def test_managed_cluster_mixed_success_and_expected_failure_preserves_changed():
    """One UID-mismatch operational failure aggregates; a successful sibling keeps changed=true."""
    result = run_decommission_role(
        managed_clusters=["spoke-a", "spoke-b"],
        observability_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
        managed_cluster_teardown_records={
            "cluster.open-cluster-management.io/v1/ManagedCluster//spoke-b": {
                "expected_uid": "not-the-live-uid",
                "phase": "delete_started",
            }
        },
    )
    summary = result["acm_switchover_decommission_result"]
    assert result["returncode"] != 0
    assert summary["status"] == "fail"
    assert summary["substeps"]["managed_clusters"] == "failed"
    assert summary["changed"] is True
    deleted = {call["path"].rsplit("/", 1)[-1] for call in result["delete_calls"] if "managedclusters/" in call["path"]}
    assert "spoke-a" in deleted
    assert "spoke-b" not in deleted
    combined = " ".join(str(task.get("result", {}).get("msg", "")) for task in result["tasks"] if task.get("failed"))
    assert "survivors" in combined.lower()
    assert "spoke-b" in combined


def test_managed_cluster_checkpoint_write_failure_aborts_rather_than_surviving():
    """A checkpoint persistence failure must not become a per-target survivor."""
    result = run_decommission_role(
        managed_clusters=["spoke-a", "spoke-b"],
        observability_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
        checkpoint_dir_readonly=True,
    )
    assert result["returncode"] != 0
    deleted = [call for call in result["delete_calls"] if "managedclusters/" in call["path"]]
    assert deleted == [], "unexpected checkpoint failure must abort before later destructive targets"
    combined = " ".join(
        str(task.get("result", {}).get("msg", "")) for task in result["tasks"] if task.get("failed")
    ).lower()
    assert "survivor" not in combined or "unexpected" in combined


def _inject_mc_teardown_fault(fault: str):
    """Temporarily insert an unexpected failure into the per-target include (restored by caller).

    The fault is scoped to ``spoke-a`` so a blanket rescue that continues the loop would
    still reach ``spoke-b`` and issue DELETE — the discrimination the contract needs.
    """
    path = TEARDOWN_ONE_MANAGED_CLUSTER
    original = path.read_text(encoding="utf-8")
    marker = "    - name: Define this ManagedCluster teardown identity\n"
    if fault == "undefined":
        injected = (
            "    - name: Deliberate undefined variable for rescue-contract test\n"
            "      ansible.builtin.set_fact:\n"
            '        _acm_mc_contract_boom: "{{ definitely_undefined_mc_rescue_contract_var }}"\n'
            "      when: _acm_mc_target_name == 'spoke-a'\n" + marker
        )
    elif fault == "unexpected_command":
        injected = (
            "    - name: Deliberate unexpected action failure for rescue-contract test\n"
            "      ansible.builtin.command: /nonexistent-acm-mc-rescue-contract-binary\n"
            "      when: _acm_mc_target_name == 'spoke-a'\n" + marker
        )
    else:
        raise ValueError(fault)
    assert marker in original
    path.write_text(original.replace(marker, injected, 1), encoding="utf-8")
    assert "rescue-contract test" in path.read_text(encoding="utf-8")
    return path, original


def test_managed_cluster_undefined_variable_failure_aborts_family():
    path, original = _inject_mc_teardown_fault("undefined")
    try:
        result = run_decommission_role(
            managed_clusters=["spoke-a", "spoke-b"],
            observability_outcome="precondition_noop",
            multiclusterhub_outcome="precondition_noop",
        )
        assert result["returncode"] != 0
        deleted = [call for call in result["delete_calls"] if "managedclusters/" in call["path"]]
        assert deleted == []
        combined = " ".join(
            str(task.get("result", {}).get("msg", "")) for task in result["tasks"] if task.get("failed")
        ).lower()
        assert "survivor" not in combined or "unexpected" in combined
    finally:
        path.write_text(original, encoding="utf-8")


def test_managed_cluster_unexpected_action_failure_aborts_family():
    path, original = _inject_mc_teardown_fault("unexpected_command")
    try:
        result = run_decommission_role(
            managed_clusters=["spoke-a", "spoke-b"],
            observability_outcome="precondition_noop",
            multiclusterhub_outcome="precondition_noop",
        )
        assert result["returncode"] != 0
        deleted = [call for call in result["delete_calls"] if "managedclusters/" in call["path"]]
        assert deleted == []
        combined = " ".join(
            str(task.get("result", {}).get("msg", "")) for task in result["tasks"] if task.get("failed")
        ).lower()
        assert "survivor" not in combined or "unexpected" in combined
    finally:
        path.write_text(original, encoding="utf-8")


def test_managed_cluster_blanket_rescue_must_not_continue_after_unclassified_failure():
    """Kill-style regression: unclassified failure must not delete a later sibling."""
    path, original = _inject_mc_teardown_fault("unexpected_command")
    try:
        result = run_decommission_role(
            managed_clusters=["spoke-a", "spoke-b"],
            observability_outcome="precondition_noop",
            multiclusterhub_outcome="precondition_noop",
        )
        assert "spoke-b" not in {
            call["path"].rsplit("/", 1)[-1] for call in result["delete_calls"] if "managedclusters/" in call["path"]
        }
    finally:
        path.write_text(original, encoding="utf-8")


def _assert_mc_inventory_fail_closed(result: dict) -> None:
    summary = result["acm_switchover_decommission_result"]
    assert result["returncode"] != 0
    assert summary["status"] == "fail"
    assert not [call for call in result["delete_calls"] if "managedclusters/" in call["path"]]
    combined = " ".join(str(task.get("result", {}).get("msg", "")) for task in result["tasks"] if task.get("failed"))
    assert "Cannot verify ManagedCluster inventory" in combined


def test_managed_cluster_malformed_only_empty_object_fails_closed():
    """items: [{}] must not become an empty work set / precondition_noop."""
    result = run_decommission_role(
        managed_cluster_objects=[{}],
        observability_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    _assert_mc_inventory_fail_closed(result)
    assert result["acm_switchover_decommission_result"]["substeps"].get("managed_clusters") != "precondition_noop"
    assert result["acm_switchover_decommission_result"]["substeps"].get("managed_clusters") != "completed"


def test_managed_cluster_malformed_missing_metadata_fails_closed():
    result = run_decommission_role(
        managed_cluster_objects=[{"apiVersion": "cluster.open-cluster-management.io/v1", "kind": "ManagedCluster"}],
        observability_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    _assert_mc_inventory_fail_closed(result)


def test_managed_cluster_malformed_non_mapping_metadata_fails_closed():
    result = run_decommission_role(
        managed_cluster_objects=[
            {
                "apiVersion": "cluster.open-cluster-management.io/v1",
                "kind": "ManagedCluster",
                "metadata": "not-a-mapping",
            }
        ],
        observability_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    _assert_mc_inventory_fail_closed(result)


@pytest.mark.parametrize(
    "bad_name",
    [None, "", "   ", 123],
    ids=["null", "empty", "whitespace", "non_string"],
)
def test_managed_cluster_malformed_metadata_name_fails_closed(bad_name):
    result = run_decommission_role(
        managed_cluster_objects=[
            {
                "apiVersion": "cluster.open-cluster-management.io/v1",
                "kind": "ManagedCluster",
                "metadata": {"name": bad_name, "uid": "mc-uid-bad"},
            }
        ],
        observability_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    _assert_mc_inventory_fail_closed(result)


def test_managed_cluster_mixed_valid_and_malformed_inventory_fails_closed():
    """One malformed item makes the entire inventory unverifiable; no deletes."""
    result = run_decommission_role(
        managed_cluster_objects=[
            _managed_cluster_object("local-cluster"),
            _managed_cluster_object("spoke-a"),
            {},
        ],
        observability_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    _assert_mc_inventory_fail_closed(result)
    assert not [call for call in result["delete_calls"] if "managedclusters/spoke-a" in call["path"]]


def test_managed_cluster_valid_inventory_still_tears_down():
    result = run_decommission_role(
        managed_cluster_objects=[
            _managed_cluster_object("local-cluster"),
            _managed_cluster_object("spoke-a"),
        ],
        observability_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    summary = result["acm_switchover_decommission_result"]
    assert result["returncode"] == 0
    assert summary["substeps"]["managed_clusters"] == "completed"
    assert summary["changed"] is True
    assert [call for call in result["delete_calls"] if "managedclusters/spoke-a" in call["path"]]
    assert not [call for call in result["delete_calls"] if "managedclusters/local-cluster" in call["path"]]


def test_managed_cluster_valid_local_cluster_only_remains_precondition_noop():
    result = run_decommission_role(
        managed_cluster_objects=[_managed_cluster_object("local-cluster")],
        observability_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    summary = result["acm_switchover_decommission_result"]
    assert result["returncode"] == 0
    assert summary["substeps"]["managed_clusters"] == "precondition_noop"
    assert not [call for call in result["delete_calls"] if "managedclusters/" in call["path"]]


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


def test_only_the_standalone_playbook_declares_the_standalone_discriminator():
    """``acm_switchover_standalone_decommission`` switches the destination gate off.

    Exactly one file may set it (``playbooks/decommission.yml``) and exactly one may read
    it (the gate include in ``delete_observability.yml``). Any other role or playbook
    naming it -- a finalization task in particular -- would silently disable the gate on
    the integrated path.

    Kill condition: adding the variable to any task, ``vars:`` or ``set_fact`` under
    ``roles/`` or ``playbooks/`` other than those two files.
    """
    allowed = {DECOMMISSION_PLAYBOOK, DELETE_OBSERVABILITY}
    yaml_files = [
        path for root in (ROLES_DIR, PLAYBOOKS_DIR) for path in root.rglob("*") if path.suffix in {".yml", ".yaml"}
    ]
    offenders = sorted(
        str(path.relative_to(ROLES_DIR.parent))
        for path in yaml_files
        if path not in allowed and "acm_switchover_standalone_decommission" in path.read_text()
    )

    assert yaml_files
    assert offenders == []


# ---------------------------------------------------------------------------
# E6 -- the MultiClusterHub durable phase table, measured at runtime.
#
# These drive the REAL role through ``run_decommission_role`` and assert on the
# durable record, the request log and the published facts. They deliberately do
# NOT assert on the classify/capture task results: those tasks are ``no_log:
# true``, so the callback record carries a censored result.
#
# Every ACM-namespace Pod fixture keeps the ``multiclusterhub-operator`` prefix;
# see ``_acm_pod_object`` for why that is load-bearing for the harness.
# ---------------------------------------------------------------------------

_ACM_PODS_PATH = f"/api/v1/namespaces/{ACM_NAMESPACE}/pods"
_ACM_NAMESPACE_PATH = f"/api/v1/namespaces/{ACM_NAMESPACE}"

#: The measured Python E4 MultiClusterHub request surface, in the 4-tuple shape
#: ``tests/test_decommission.py`` builds its own table in, so the two are diffable
#: as sets rather than through a hand-maintained comparison table.
MCH_GROUP = "operator.open-cluster-management.io"
MCH_LIST_SHAPE = ("LIST", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE)
MCH_GET_SHAPE = ("GET", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE)
MCH_GUARDED_DELETE_SHAPE = ("DELETE", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE)
CSV_LIST_SHAPE = ("LIST", CSV_API_GROUP, "clusterserviceversions", ACM_NAMESPACE)
CSV_GET_SHAPE = ("GET", CSV_API_GROUP, "clusterserviceversions", ACM_NAMESPACE)
DEPLOYMENT_GET_SHAPE = ("GET", "apps", "deployments", ACM_NAMESPACE)
REPLICASET_GET_SHAPE = ("GET", "apps", "replicasets", ACM_NAMESPACE)
NAMESPACE_GET_SHAPE = ("GET", "", "namespaces", None)
POD_LIST_SHAPE = ("LIST", "", "pods", ACM_NAMESPACE)

#: §20 scenario 1 -- a fresh captured identity carried to `completed`. The nine shapes
#: are exactly the Python E4 set measured by
#: ``tests/test_decommission.py::TestMultiClusterHubRequestShapes::
#: test_fresh_captured_identity_with_a_rolling_update`` (lines 4856-4881).
_SCENARIO_ONE_SURFACE = {
    MCH_LIST_SHAPE,
    MCH_GET_SHAPE,
    MCH_GUARDED_DELETE_SHAPE,
    CSV_LIST_SHAPE,
    CSV_GET_SHAPE,
    DEPLOYMENT_GET_SHAPE,
    REPLICASET_GET_SHAPE,
    NAMESPACE_GET_SHAPE,
    POD_LIST_SHAPE,
}


#: The ACM-namespace route of the ``apps/v1`` group, i.e. where the operator identity
#: chain is read. Deployments and ReplicaSets exist in other namespaces too (the
#: observability family patches some), so they are matched on this prefix, never on the
#: plural alone.
_ACM_APPS_PREFIX = f"/apis/apps/v1/namespaces/{ACM_NAMESPACE}/"


def _is_mch_measured(request: dict) -> bool:
    """Whether one logged request belongs to the MultiClusterHub teardown's own surface.

    Pods and Namespaces are matched on the EXACT ACM paths and the identity chain on the
    ACM-namespace ``apps/v1`` prefix: a plural-only filter also catches the observability
    namespace's Pod and Deployment reads and the kube-system identity read, which belong
    to other families.
    """
    path = request["path"]
    if path in (_ACM_PODS_PATH, _ACM_NAMESPACE_PATH):
        return True
    if path.startswith(f"{_ACM_APPS_PREFIX}deployments") or path.startswith(f"{_ACM_APPS_PREFIX}replicasets"):
        return True
    return any(f"/{plural}" in path for plural in ("multiclusterhubs", "clusterserviceversions"))


def run_mch_role(**kwargs) -> dict:
    """One decommission run with the other two families quiesced.

    The MultiClusterHub substep is what these tests measure; a live MCO or
    ManagedCluster inventory only adds requests and failure modes that belong to
    the other families' own suites.
    """
    kwargs.setdefault("observability_outcome", "precondition_noop")
    kwargs.setdefault("managed_clusters_outcome", "precondition_noop")
    return run_decommission_role(**kwargs)


def _mch_requests(result: dict) -> list:
    return [request for request in result["requests"] if "/multiclusterhubs" in request["path"]]


def _mch_deletes(result: dict) -> list:
    return [call for call in result["delete_calls"] if "/multiclusterhubs" in call["path"]]


def _csv_requests(result: dict) -> list:
    return [request for request in result["requests"] if "/clusterserviceversions" in request["path"]]


def _acm_pod_lists(result: dict) -> list:
    """Pod LISTs in the ACM namespace only -- an exact path, never a prefix match.

    ``"open-cluster-management" in path`` also matches the observability namespace.
    """
    return [request for request in result["requests"] if request["path"] == _ACM_PODS_PATH]


def _operator_identity_reads(result: dict) -> list:
    """Every Deployment/ReplicaSet read -- the ownership chain the classifier walks."""
    return [
        request
        for request in result["requests"]
        if "/deployments" in request["path"] or "/replicasets" in request["path"]
    ]


def _teardown_records(result: dict, *, before: bool = False) -> dict:
    data = result["checkpoint"]["before_operational_data" if before else "operational_data"]
    return data.get("decommission_teardown_records", {})


def _mch_record_of(result: dict, key: str = MCH_KEY):
    records = result["checkpoint"]["operational_data"].get("decommission_teardown_records", {})
    return records.get(key)


def _tasks_using(result: dict, module: str) -> list:
    """Every task that RAN the module. The callback records skipped tasks too, and a
    skipped strict read proves nothing about what the run actually did."""
    return [task for task in result["tasks"] if task["module"] == module and not task["skipped"]]


def _teardown_record_writes(result: dict) -> list:
    """Every ``checkpoint_phase`` task that RAN and carried a durable teardown record.

    The record LOAD is a ``checkpoint_phase`` call too (``read_facts: true``), and the
    playbook's own phase transitions are others, so "wrote nothing" has to be asserted on
    the tasks that carry a ``teardown_record`` argument rather than on the module.
    """
    return [
        task
        for task in _tasks_using(result, _CHECKPOINT_WRITER_MODULE)
        if "teardown_record" in (task.get("task_args") or {})
    ]


def _failed_task_names(result: dict) -> list:
    """Every failed task name, in order. Includes rescued failures -- an exhausted drain
    loop is recorded failed before its rescue decides what the exhaustion meant."""
    return [task["name"] for task in result["tasks"] if task["failed"]]


def _first_failed_task(result: dict) -> str:
    """The name of the FIRST task that failed. The substep rescue in main.yml re-fails
    afterwards, so only the first failure identifies the refusal that decided the run."""
    failed = _failed_task_names(result)
    assert failed, "the run recorded no failed task"
    return failed[0]


def _strict_mch_reads(result: dict) -> list:
    return [
        task
        for task in _tasks_using(result, "tomazb.acm_switchover.acm_k8s_read_outcome")
        if (task.get("task_args") or {}).get("resource_name") == "multiclusterhubs"
    ]


def _completed_mch_record(**overrides) -> dict:
    record = mch_teardown_record(
        "completed",
        observed_at="2026-09-17T00:00:00Z",
        resource_versions={"drain_namespace": "ns-1", "drain_pods": "pods-1", "operator_deployment": "deploy-1"},
        absence_proofs={"target_cr": {"proof_type": "object_absent", "resource_key": MCH_KEY}},
    )
    record.update(overrides)
    return record


class TestDeleteMultiClusterHubDurable:
    """Runtime contracts for the MultiClusterHub durable teardown phase table (E6)."""

    def test_drain_is_decided_by_ownership_not_the_operator_name_prefix(self):
        """§21.1: a Pod carrying the operator NAME but no owner chain still blocks."""
        result = run_mch_role(acm_pods=[_spoof_pod()], mch_drain_retries=1, mch_drain_delay=0)

        assert result["returncode"] != 0, "an unowned Pod wearing the operator name prefix must block the drain"
        record = _mch_record_of(result)
        assert record is not None, "the blocked drain must leave a durable MultiClusterHub record"
        assert record["phase"] == "drain_pending"

    def test_an_owned_pod_without_the_operator_name_prefix_is_still_excluded(self):
        """§5 converse: the name proves nothing in EITHER direction -- the chain decides.

        Safe without the prefix rule because this run resolves no live MultiClusterHub,
        so the legacy Pod wait (guarded on a non-empty MCH inventory) never runs.
        """
        result = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("drain_pending"),
            acm_pods=[_operator_owned_pod("acm-operator-xyz-1", unprefixed=True)],
        )

        record = _mch_record_of(result)
        assert record is not None
        assert record["phase"] == "completed", "an owned Pod must be excluded whatever it is called"
        assert result["returncode"] == 0

    def test_an_unowned_pod_without_the_operator_name_prefix_still_blocks(self):
        """§5 converse: an unowned Pod blocks whether or not it wears the operator name."""
        result = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("drain_pending"),
            acm_pods=[_spoof_pod("search-redisgraph-0", unprefixed=True)],
            mch_drain_retries=1,
            mch_drain_delay=0,
        )

        assert result["returncode"] != 0, "an unowned ACM Pod blocks the drain whatever it is called"
        record = _mch_record_of(result)
        assert record is not None
        assert record["phase"] == "drain_pending"

    def test_no_live_target_and_no_record_is_a_clean_precondition_noop(self):
        """§21.2 (zero) and §7: strict discovery, no Namespace read, no capture, no DELETE."""
        result = run_mch_role(mch_present=False)

        assert _strict_mch_reads(result), "MCH discovery must use acm_k8s_read_outcome, not kubernetes.core.k8s_info"
        assert not [
            task
            for task in _tasks_using(result, "kubernetes.core.k8s_info")
            if (task.get("task_args") or {}).get("kind") == "MultiClusterHub"
        ]
        assert result["acm_switchover_decommission_result"]["substeps"]["multiclusterhub"] == "precondition_noop"
        assert _csv_requests(result) == []
        assert [request for request in result["requests"] if request["path"] == _ACM_NAMESPACE_PATH] == []
        assert _mch_deletes(result) == []
        assert _mch_record_of(result) is None

    def test_strict_target_resolution_takes_the_exact_live_name(self):
        """§21.2 (one) and §7: the durable key binds the live name, not a fixed one."""
        key = teardown_key("operator.open-cluster-management.io/v1", "MultiClusterHub", ACM_NAMESPACE, "custom-hub")
        result = run_mch_role(mch_inventory=[_mch_object("custom-hub", uid="mch-uid-custom")])

        record = _mch_record_of(result, key)
        assert record is not None, f"the durable record must be keyed on the live target: {key}"
        assert record["expected_uid"] == "mch-uid-custom"
        identity = record.get("operator_deployment") or record.get("operator_identity_unavailable")
        assert identity["mch_teardown_key"] == key, "the captured identity must back-reference the dynamic key"
        assert identity["mch_expected_uid"] == "mch-uid-custom"
        deletes = _mch_deletes(result)
        assert len(deletes) == 1
        assert deletes[0]["path"].endswith("/custom-hub")

    def test_more_than_one_live_multiclusterhub_fails_closed(self):
        """§21.2 (many) and §7: ambiguous inventory mutates nothing."""
        result = run_mch_role(
            mch_inventory=[_mch_object("multiclusterhub"), _mch_object("other-hub", uid="mch-uid-2")],
        )

        assert _mch_deletes(result) == [], "an ambiguous MultiClusterHub inventory must not be deleted"
        assert result["returncode"] != 0
        assert _mch_record_of(result) is None

    def test_capture_precedes_the_durable_write_which_precedes_the_delete(self):
        """§21.3 and §9-§11: identity, then delete_started, then the guarded DELETE."""
        result = run_mch_role()

        csv_reads = _csv_requests(result)
        assert csv_reads, "the operator identity must be captured through the CSV before the delete"
        deletes = _mch_deletes(result)
        assert len(deletes) == 1
        assert result["requests"].index(csv_reads[0]) < result["requests"].index(deletes[0])
        assert deletes[0]["expected_uid"] == MCH_UID
        record = _mch_record_of(result)
        assert record is not None
        assert record["operator_deployment"]["uid"] == OPERATOR_DEPLOYMENT_UID

    def test_a_capture_error_blocks_both_the_durable_write_and_the_delete(self):
        """§21.4: an unverifiable CSV read is not an absent identity."""
        result = run_mch_role(csv_read_status=403)

        assert _mch_deletes(result) == [], "an identity capture error must block the guarded DELETE"
        assert result["returncode"] != 0
        assert _mch_record_of(result) is None

    def test_a_failed_checkpoint_write_blocks_the_delete(self):
        """§10: the delete_started write must be durable BEFORE the DELETE is issued.

        The first two assertions prove the run actually REACHED the write: a read-only
        checkpoint directory still allows the enter (reads succeed), so the strict target
        resolution and the identity capture must both have happened. Without them a
        checkpoint enter that failed early would satisfy "no DELETE" vacuously.
        """
        result = run_mch_role(checkpoint_dir_readonly=True)

        assert _strict_mch_reads(result), "the run must reach strict target resolution before the durable write"
        assert _csv_requests(result), "the run must reach the identity capture before the durable write"
        assert _mch_deletes(result) == [], "no DELETE may be issued when the durable write failed"
        assert result["returncode"] != 0

    def test_every_phase_write_carries_the_identity_the_record_was_created_with(self):
        """§21.5 and §12: the recorded identity is preserved, never re-derived."""
        identity = mch_operator_deployment_identity()
        result = run_mch_role(mch_present=False, mch_record=mch_teardown_record("drain_pending", identity=identity))

        record = _mch_record_of(result)
        assert record is not None
        assert record["phase"] == "completed", "an absent target with a drain obligation must finish it"
        assert record["operator_deployment"] == identity
        assert _mch_deletes(result) == []

    def test_a_recorded_unavailable_identity_excludes_no_pod(self):
        """§21.6 and §17: an unavailable identity is never upgraded, and owns no Pod."""
        result = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("drain_pending", identity=mch_identity_unavailable()),
            acm_pods=[_operator_owned_pod()],
            mch_drain_retries=1,
            mch_drain_delay=0,
        )

        assert result["returncode"] != 0, "under an unavailable identity every ACM Pod blocks the drain"
        record = _mch_record_of(result)
        assert record is not None
        assert record["phase"] == "drain_pending"
        assert "operator_identity_unavailable" in record

    def test_zero_pods_with_an_inconsistent_operator_deployment_records_recovery_required(self):
        """§21.7 and §14: identity inconsistency is checked BEFORE blocking_count."""
        result = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("drain_pending"),
            operator_deployments=[_operator_deployment(uid="rotated-deployment-uid")],
            acm_pods=[],
        )

        record = _mch_record_of(result)
        assert record is not None
        assert record["phase"] == "recovery_required", "zero Pods does not override an inconsistent identity"
        assert result["returncode"] != 0

    def test_a_namespace_read_error_records_recovery_required_before_failing(self):
        """§21.8 and §14: the durable transition is written first, then the failure."""
        result = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("drain_pending"),
            object_read_statuses={f"namespaces/{ACM_NAMESPACE}": 500},
        )

        record = _mch_record_of(result)
        assert record is not None
        assert record["phase"] == "recovery_required"
        assert result["returncode"] != 0

    def test_a_pod_list_error_fails_without_the_recovery_transition(self):
        """§21.9 and §14: a Pod-stage error leaves the outstanding obligation as it was."""
        result = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("drain_pending"),
            pod_read_statuses=[403],
        )

        assert result["returncode"] != 0, "an unverifiable Pod inventory must fail the substep"
        record = _mch_record_of(result)
        assert record is not None
        assert record["phase"] == "drain_pending"

    def test_a_drain_timeout_leaves_the_durable_phase_at_drain_pending(self):
        """§21.10 and §14: a timeout is not a recovery transition, and no owner is guessed."""
        result = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("drain_pending"),
            acm_pods=[_spoof_pod(), _job_owned_pod(), _statefulset_owned_pod(), _unrelated_replicaset_pod()],
            operator_replicasets=[_operator_replicaset(), _unrelated_replicaset()],
            mch_drain_retries=1,
            mch_drain_delay=0,
        )

        assert result["returncode"] != 0, "Job-, StatefulSet- and foreign-ReplicaSet-owned Pods all block the drain"
        record = _mch_record_of(result)
        assert record is not None
        assert record["phase"] == "drain_pending"

    def test_completion_mode_one_records_namespace_absence_evidence(self):
        """§21.11 mode 1 and §15: an absent Namespace discharges the drain predicate."""
        result = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("drain_pending"),
            object_read_statuses={f"namespaces/{ACM_NAMESPACE}": 404},
        )

        record = _mch_record_of(result)
        assert record is not None
        assert record["phase"] == "completed"
        assert record["resource_versions"] == {}
        assert set(record["absence_proofs"]) == {"target_cr", "drain_namespace"}

    def test_completion_mode_two_records_all_three_revisions(self):
        """§21.11 mode 2: a captured identity adds the operator Deployment revision."""
        result = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("drain_pending"),
            acm_pods=[_operator_owned_pod()],
        )

        record = _mch_record_of(result)
        assert record is not None
        assert record["phase"] == "completed"
        assert set(record["resource_versions"]) == {"drain_namespace", "drain_pods", "operator_deployment"}
        assert set(record["absence_proofs"]) == {"target_cr"}

    def test_completion_revisions_come_from_the_final_pass_only(self):
        """§21.12 and §15: a stale drain-loop revision may not certify completion."""
        result = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("drain_pending"),
            acm_pods=[_operator_owned_pod()],
            pod_list_revisions=["pods-drain-pass"] + ["pods-final-pass"] * 8,
        )

        record = _mch_record_of(result)
        assert record is not None
        assert record["phase"] == "completed"
        assert record["resource_versions"]["drain_pods"] == "pods-final-pass"

    def test_completion_mode_three_omits_the_operator_deployment_revision(self):
        """§21.11 mode 3: an unavailable identity completes only on an empty Pod inventory."""
        result = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("drain_pending", identity=mch_identity_unavailable()),
            acm_pods=[],
        )

        record = _mch_record_of(result)
        assert record is not None
        assert record["phase"] == "completed"
        assert set(record["resource_versions"]) == {"drain_namespace", "drain_pods"}

    def test_a_new_pod_in_the_final_pass_blocks_completion(self):
        """§15: the final proof is a wholly fresh pass, not the drain loop's last answer."""
        result = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("drain_pending"),
            acm_pod_passes=[[], [_spoof_pod()], [_spoof_pod()]],
            mch_drain_retries=1,
            mch_drain_delay=0,
        )

        assert result["returncode"] != 0, "a Pod appearing only in the final pass must block completion"
        record = _mch_record_of(result)
        assert record is not None
        assert record["phase"] != "completed"

    @pytest.mark.parametrize("phase", ["cr_absent", "drained", "recovery_required"])
    def test_resume_from_a_post_delete_phase_issues_no_delete(self, phase):
        """§17: each post-delete phase resumes its outstanding proof without mutating.

        `recovery_required` completes here on purpose. Section 17 says to "retry the
        outstanding proof per the established state machine", and plan section 20 allows
        no success transition until a later strict rerun obtains the missing proof --
        which this rerun does. `recovery_required` is only ever written from the drain or
        final-proof stages, so the outstanding obligation is exactly drain plus final
        proof; with the target absent, the namespace live and no blocking Pod, that proof
        now succeeds and the record may complete. Identity is never rebound.
        """
        result = run_mch_role(mch_present=False, mch_record=mch_teardown_record(phase), acm_pods=[])

        record = _mch_record_of(result)
        assert record is not None
        assert record["phase"] == "completed"
        assert _mch_deletes(result) == []

    def test_a_completed_record_is_reproved_without_any_write(self):
        """§21.13 and §16: a completed record is immutable and re-proved read-only."""
        completed = _completed_mch_record()
        result = run_mch_role(mch_present=False, mch_record=completed, acm_pods=[])

        assert _acm_pod_lists(result), "a completed reproof with a live namespace must classify a fresh pass"
        # Asserted before the outcome, so a reproof that reaches a durable writer at all
        # is caught here rather than incidentally on the return code the writer's own
        # failure produces. Content equality would also hold if a writer ran and rewrote
        # the identical record; no writer may RUN.
        assert _teardown_record_writes(result) == []
        assert result["returncode"] == 0
        assert _mch_record_of(result) == completed
        # Scoped to the durable records rather than the whole operational_data map: the
        # substep outcome lives in a play fact, not in the checkpoint (main.yml:199).
        assert _teardown_records(result) == _teardown_records(result, before=True)
        assert _mch_deletes(result) == []

    def test_a_completed_record_is_never_overwritten_with_recovery_required(self):
        """§16: a failing reproof fails the invocation; it does not downgrade the record."""
        completed = _completed_mch_record()
        result = run_mch_role(
            mch_present=False,
            mch_record=completed,
            object_read_statuses={f"namespaces/{ACM_NAMESPACE}": 500},
        )

        assert result["returncode"] != 0, "an unverifiable reproof must fail the invocation"
        assert _mch_record_of(result) == completed

    @pytest.mark.parametrize("execution_mode, check_mode", [("execute", True), ("dry_run", False)])
    def test_preview_predicts_the_change_without_writing_or_deleting(self, execution_mode, check_mode):
        """§21.14, §21.15 and §8: strict reads still run; nothing is written or deleted."""
        result = run_mch_role(execution_mode=execution_mode, check_mode=check_mode)

        assert result["facts"].get("_acm_mch_would_change") is True, "a live target must predict a change"
        assert result["facts"].get("_acm_mch_changed") is False
        assert _mch_deletes(result) == []
        assert result["checkpoint"]["operational_data"] == result["checkpoint"]["before_operational_data"]
        assert _strict_mch_reads(result)

    def test_preview_of_a_clean_absence_predicts_no_change(self):
        """§8: no target means no prospective change."""
        result = run_mch_role(check_mode=True, mch_present=False)

        assert result["facts"].get("_acm_mch_would_change") is False
        assert _mch_deletes(result) == []

    def test_actual_change_is_published_for_this_invocations_delete(self):
        """§21.15 and §19: `_acm_mch_changed` is this run's mutation truth."""
        result = run_mch_role()

        assert result["facts"].get("_acm_mch_changed") is True
        assert result["acm_switchover_decommission_result"]["changed"] is True

    def test_change_truth_survives_a_failure_after_the_delete_was_accepted(self):
        """§11: an accepted DELETE followed by a later failure still reports changed."""
        result = run_mch_role(mch_post_delete_read_status=500)

        assert result["facts"].get("_acm_mch_changed") is True, "an accepted DELETE is a real change, even on failure"
        assert result["returncode"] != 0
        assert len(_mch_deletes(result)) == 1
        assert result["acm_switchover_decommission_result"]["changed"] is True

    def test_the_list_to_get_disappearance_race_is_a_clean_noop(self):
        """§7: LIST selected one, the named GET proved it absent -- nothing to do."""
        result = run_mch_role(mch_named_read_status=404)

        assert result["acm_switchover_decommission_result"]["substeps"]["multiclusterhub"] == "precondition_noop"
        assert result["returncode"] == 0
        assert _mch_deletes(result) == []
        assert _mch_record_of(result) is None
        assert _csv_requests(result) == []

    def test_a_durable_record_never_rebinds_to_a_different_live_name(self):
        """§7 and §17: contradictory live inventory fails closed; no rediscovery."""
        result = run_mch_role(
            mch_record=mch_teardown_record("delete_started"),
            mch_inventory=[_mch_object("other-hub", uid="mch-uid-2")],
        )

        assert _mch_deletes(result) == [], "a durable record must never delete a differently named MultiClusterHub"
        assert result["returncode"] != 0

    def test_a_same_name_replacement_uid_is_refused(self):
        """§11: the replacement is left intact; a name-only delete would have removed it.

        `acm_uid_guarded_delete` reads and compares the UID before it issues anything
        (`module_utils/uid_guarded_delete.py: run_guarded_delete` raises
        `REASON_UID_MISMATCH` at `STAGE_UID_MISMATCH`), so a correct implementation
        issues NO DELETE at all here -- the empty log is reachable, not merely desirable.
        """
        result = run_mch_role(
            mch_record=mch_teardown_record("delete_started"),
            mch_inventory=[_mch_object("multiclusterhub", uid="mch-uid-replacement")],
        )

        assert _mch_deletes(result) == [], "a same-name replacement must not be deleted"
        assert result["returncode"] != 0

    def test_the_measured_request_surface_matches_the_python_e4_shapes(self):
        """§20: the role-level measured set, compared against Python's measured table."""
        result = run_mch_role(acm_pods=[_operator_owned_pod()])

        measured = {_request_shape(request) for request in result["requests"] if _is_mch_measured(request)}
        assert measured == {
            MCH_LIST_SHAPE,
            MCH_GET_SHAPE,
            MCH_GUARDED_DELETE_SHAPE,
            CSV_LIST_SHAPE,
            CSV_GET_SHAPE,
            DEPLOYMENT_GET_SHAPE,
            REPLICASET_GET_SHAPE,
            NAMESPACE_GET_SHAPE,
            POD_LIST_SHAPE,
        }

    def test_an_unknown_execution_mode_is_a_preview_not_an_unrecorded_delete(self):
        """§10: every durable writer is gated on `execute`, so the delete must be too.

        A `!= 'dry_run'` check-mode gate on the guarded delete treats an unknown mode
        string as a live run while every `== 'execute'` writer stays skipped -- a DELETE
        with no `delete_started` record, which is the one ordering section 10 forbids.
        """
        result = run_mch_role(execution_mode="bogus", allow_unknown_execution_mode=True)

        assert _strict_mch_reads(result), "an unknown mode must still perform strict discovery"
        assert _mch_deletes(result) == [], "only execute mode may delete"
        assert _mch_record_of(result) is None
        assert result["checkpoint"]["operational_data"] == result["checkpoint"]["before_operational_data"]

    def test_a_preview_of_a_completed_record_classifies_nothing(self):
        """§8 and §16: a preview reads the target strictly and stops.

        The completed reproof classifies Pods, and section 8 forbids a preview from
        classifying anything, so the reproof is execute-mode work like every other pass.
        """
        completed = _completed_mch_record()
        result = run_mch_role(check_mode=True, mch_present=False, mch_record=completed, acm_pods=[])

        assert _strict_mch_reads(result), "the strict target reads still run under --check"
        assert _acm_pod_lists(result) == [], "a preview must classify no Pod"
        assert _csv_requests(result) == []
        assert _operator_identity_reads(result) == []
        assert result["facts"].get("_acm_mch_would_change") is False
        assert result["returncode"] == 0
        assert _mch_record_of(result) == completed
        assert _teardown_records(result) == _teardown_records(result, before=True)

    def test_a_recovery_required_marker_is_never_demoted_before_new_evidence(self):
        """§17: resume retries the outstanding proof; it does not overwrite the marker.

        Writing `drain_pending` on the way in demotes the record BEFORE the pass that
        would justify it, so a Pod-stage error -- which deliberately writes no recovery
        transition -- leaves a weaker obligation recorded than the one the run started
        with.
        """
        pods = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("recovery_required"),
            pod_read_statuses=[403],
        )

        assert pods["returncode"] != 0
        assert _mch_record_of(pods)["phase"] == "recovery_required"

        namespace = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("recovery_required"),
            object_read_statuses={f"namespaces/{ACM_NAMESPACE}": 500},
        )

        assert namespace["returncode"] != 0
        assert _mch_record_of(namespace)["phase"] == "recovery_required"

    def test_resume_from_drained_runs_the_final_proof_only(self):
        """§17: a `drained` record owes the final proof, not another drain loop.

        Re-running the bounded loop there spends a second classification pass on evidence
        the record already carries, and lets a transient namespace error demote it.
        """
        result = run_mch_role(mch_present=False, mch_record=mch_teardown_record("drained"), acm_pods=[])

        assert len(_acm_pod_lists(result)) == 1, "the final verification pass is the only pass a drained record owes"
        assert _mch_record_of(result)["phase"] == "completed"
        assert result["returncode"] == 0

    def test_a_namespace_error_resuming_from_drained_records_recovery_required(self):
        """§15: an unverifiable namespace in the FINAL proof is the recovery transition."""
        result = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("drained"),
            object_read_statuses={f"namespaces/{ACM_NAMESPACE}": 500},
        )

        assert result["returncode"] != 0
        assert _mch_record_of(result)["phase"] == "recovery_required"
        assert _acm_pod_lists(result) == [], "the namespace read failed before any Pod was listed"

    # --- §22 matrix completion (Task 3) ------------------------------------

    def test_a_multiclusterhub_kind_that_is_not_served_is_a_clean_precondition_noop(self):
        """§22 (CRD absent) and §7: positive absence of the KIND is a clean skip.

        Distinct from the empty-inventory skip: discovery proves the kind is not served,
        so there is no inventory to bind and no named read to take.
        """
        result = run_mch_role(mch_present=False, mch_kind_served=False)

        assert result["returncode"] == 0
        assert result["acm_switchover_decommission_result"]["substeps"]["multiclusterhub"] == "precondition_noop"
        assert len(_strict_mch_reads(result)) == 1, "an unserved kind is resolved by the LIST alone"
        assert _mch_deletes(result) == []
        assert _mch_record_of(result) is None
        assert _csv_requests(result) == []
        assert result["facts"].get("_acm_mch_would_change") is False

    def test_an_unverifiable_multiclusterhub_inventory_fails_closed(self):
        """§22 (LIST error) and §7: an error is never an absence.

        The named read must not be attempted either: a run that cannot enumerate the
        inventory has no target to bind.
        """
        result = run_mch_role(mch_read_status=500)

        assert result["returncode"] != 0
        assert _mch_deletes(result) == []
        assert _mch_record_of(result) is None
        assert _csv_requests(result) == []
        assert len(_strict_mch_reads(result)) == 1, "no bound target may be read after an unverifiable LIST"

    @pytest.mark.parametrize(
        "member, refusing_task",
        [
            ("not-an-object", "Fail closed when the source MultiClusterHub inventory is unverifiable"),
            (
                {"apiVersion": "operator.open-cluster-management.io/v1", "kind": "MultiClusterHub", "metadata": "oops"},
                "Fail closed on a malformed MultiClusterHub inventory member",
            ),
            (
                {
                    "apiVersion": "operator.open-cluster-management.io/v1",
                    "kind": "MultiClusterHub",
                    "metadata": {"namespace": ACM_NAMESPACE, "uid": MCH_UID, "resourceVersion": "1"},
                },
                "Fail closed on a malformed MultiClusterHub inventory member",
            ),
            (
                {
                    "apiVersion": "operator.open-cluster-management.io/v1",
                    "kind": "MultiClusterHub",
                    "metadata": {"name": "multiclusterhub", "namespace": ACM_NAMESPACE, "resourceVersion": "1"},
                },
                "Fail closed on a malformed MultiClusterHub inventory member",
            ),
        ],
        ids=["non-mapping", "non-mapping-metadata", "no-name", "no-uid"],
    )
    def test_a_malformed_live_multiclusterhub_member_fails_closed(self, member, refusing_task):
        """§22 (malformed live member/name/UID): nothing is bound, captured or deleted.

        Two refusals are in play and both are fail-closed: a member that is not a mapping
        at all makes the strict LIST itself unverifiable (``_strict_list_page`` refuses
        the page), while a mapping without a usable name or UID reaches the role's own
        malformed-member task. WHICH task refused is asserted, because these members are
        defended in depth: without the named refusal the run would still fail, just later
        and on an undefined variable rather than on a decision.
        """
        result = run_mch_role(mch_inventory=[member])

        assert result["returncode"] != 0
        assert _first_failed_task(result) == refusing_task
        assert _mch_deletes(result) == []
        assert _mch_record_of(result) is None
        assert _csv_requests(result) == []

    def test_more_than_one_durable_multiclusterhub_record_fails_closed(self):
        """§22 (>1 durable record) and §6: ambiguity is refused before the first read.

        The record load precedes every MultiClusterHub request, so an ambiguous durable
        state must cost zero MultiClusterHub API traffic -- not merely zero DELETEs.
        """
        other_key = teardown_key(
            "operator.open-cluster-management.io/v1", "MultiClusterHub", ACM_NAMESPACE, "other-hub"
        )
        result = run_mch_role(
            mch_teardown_records={
                MCH_KEY: mch_teardown_record("drain_pending"),
                other_key: mch_teardown_record(
                    "drain_pending",
                    identity=mch_operator_deployment_identity(key=other_key, expected_uid="mch-uid-2"),
                    expected_uid="mch-uid-2",
                ),
            },
        )

        assert result["returncode"] != 0
        assert _mch_requests(result) == [], "an ambiguous durable record must be refused before any MCH read"
        assert _mch_deletes(result) == []
        assert _teardown_records(result) == _teardown_records(result, before=True)

    def test_a_fresh_unavailable_identity_completes_without_a_deployment_revision(self):
        """§9 and §21.11 mode 3: a capture with no CSV is a determinate durable outcome.

        The capture is not an error, so the delete proceeds; the record it creates carries
        ``operator_identity_unavailable``, and completion needs a strictly empty Pod
        inventory because that identity owns nothing.
        """
        result = run_mch_role(operator_csvs=[], acm_pods=[])

        record = _mch_record_of(result)
        assert record is not None
        assert record["phase"] == "completed"
        assert "operator_identity_unavailable" in record
        assert set(record["resource_versions"]) == {"drain_namespace", "drain_pods"}
        assert len(_mch_deletes(result)) == 1
        assert _operator_identity_reads(result) == [], "an unavailable identity reads no Deployment"

    def test_a_resumed_delete_started_record_deletes_once_without_recapturing(self):
        """§17 (resume delete_started, same UID) and §9: the recorded identity is reused."""
        identity = mch_operator_deployment_identity()
        result = run_mch_role(mch_record=mch_teardown_record("delete_started", identity=identity), acm_pods=[])

        deletes = _mch_deletes(result)
        assert len(deletes) == 1, "a delete_started record retries exactly its own delete"
        assert deletes[0]["expected_uid"] == MCH_UID
        assert _csv_requests(result) == [], "resume never recaptures the operator identity"
        record = _mch_record_of(result)
        assert record["phase"] == "completed"
        assert record["operator_deployment"] == identity
        assert result["facts"].get("_acm_mch_changed") is True

    def test_a_resumed_delete_started_record_with_an_absent_target_changes_nothing(self):
        """§19: `changed` is THIS invocation's mutation truth, not the teardown's.

        The delete already happened in an earlier run, so the record finishes its
        outstanding proof while this run reports no change at all.
        """
        result = run_mch_role(mch_present=False, mch_record=mch_teardown_record("delete_started"), acm_pods=[])

        assert result["returncode"] == 0
        assert _mch_deletes(result) == []
        assert _mch_record_of(result)["phase"] == "completed"
        assert result["facts"].get("_acm_mch_changed") is False
        assert result["acm_switchover_decommission_result"]["changed"] is False

    def test_the_authoritative_reads_precede_the_capture_which_precedes_the_delete(self):
        """§22 (request ordering): LIST, named GET, capture, then the guarded DELETE.

        The durable `delete_started` write sits between the capture and the DELETE, and
        it is not an HTTP request to this hub, so its position is asserted on the task
        log instead of on the request log -- the two logs are never cross-correlated.
        """
        result = run_mch_role(acm_pods=[])

        requests = result["requests"]
        mch = _mch_requests(result)
        csv = _csv_requests(result)
        mch_list = [request for request in mch if request["path"].endswith("/multiclusterhubs")]
        mch_get = [request for request in mch if request["path"].endswith("/multiclusterhub")]
        delete = _mch_deletes(result)
        assert mch_list and mch_get and csv and len(delete) == 1
        assert requests.index(mch_list[0]) < requests.index(mch_get[0])
        assert requests.index(mch_get[0]) < requests.index(csv[0])
        assert requests.index(csv[-1]) < requests.index(delete[0])

        names = [task["name"] for task in result["tasks"] if not task["skipped"]]
        assert names.index("Capture the MultiClusterHub operator identity") < names.index(
            "Record MultiClusterHub delete_started"
        )
        assert names.index("Record MultiClusterHub delete_started") < names.index("Delete the recorded MultiClusterHub")

    def test_a_rolling_update_of_the_operator_deployment_drains_cleanly(self):
        """§22 (rolling update): two ReplicaSets, one Deployment, every Pod owned."""
        result = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("drain_pending"),
            acm_pods=[
                _operator_owned_pod(
                    "multiclusterhub-operator-old-1", replicaset_name="mch-op-old", replicaset_uid="uid-rs-old"
                ),
                _operator_owned_pod(
                    "multiclusterhub-operator-new-1", replicaset_name="mch-op-new", replicaset_uid="uid-rs-new"
                ),
            ],
            operator_replicasets=[
                _operator_replicaset(name="mch-op-old", uid="uid-rs-old"),
                _operator_replicaset(name="mch-op-new", uid="uid-rs-new"),
            ],
        )

        # Ownership first, so a role that stops establishing it is killed HERE rather
        # than incidentally on the return code: both owner chains must really be walked.
        # Without this, a run that resolved one ReplicaSet and assumed the rest of the
        # inventory belonged to the same Deployment would still reach `completed`.
        read_paths = [request["path"] for request in _operator_identity_reads(result)]
        assert [path for path in read_paths if path.endswith("/replicasets/mch-op-old")]
        assert [path for path in read_paths if path.endswith("/replicasets/mch-op-new")]
        assert result["returncode"] == 0
        record = _mch_record_of(result)
        assert record["phase"] == "completed", "both generations of the same Deployment are owned"
        assert set(record["resource_versions"]) == {"drain_namespace", "drain_pods", "operator_deployment"}

    @pytest.mark.parametrize(
        "deployments, object_read_statuses",
        [
            ([], None),
            ([_operator_deployment(uid="rotated-deployment-uid")], None),
            (None, {f"deployments/{OPERATOR_DEPLOYMENT_NAME}": 500}),
        ],
        ids=["absent", "replaced", "read-error"],
    )
    def test_a_recorded_operator_deployment_that_cannot_be_confirmed_records_recovery_required(
        self, deployments, object_read_statuses
    ):
        """§22 (recorded Deployment absent/replaced/error) and §14.

        All three are one classifier outcome -- ``operator_identity_inconsistent``
        (``pod_owner_classify.classify_pods`` verifies the re-read Deployment's UID and
        treats absence, rotation and an unreadable read alike) -- so all three write the
        outstanding obligation and fail rather than certifying a drain.
        """
        result = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("drain_pending"),
            acm_pods=[],
            operator_deployments=deployments,
            object_read_statuses=object_read_statuses,
        )

        assert result["returncode"] != 0
        assert _mch_record_of(result)["phase"] == "recovery_required"

    def test_an_unreadable_replicaset_leaves_its_pod_blocking(self):
        """§22 (ReplicaSet error): an unverifiable owner chain never proves ownership."""
        result = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("drain_pending"),
            acm_pods=[_operator_owned_pod()],
            object_read_statuses={f"replicasets/{OPERATOR_REPLICASET_NAME}": 500},
            mch_drain_retries=1,
            mch_drain_delay=0,
        )

        assert result["returncode"] != 0, "a Pod whose ReplicaSet cannot be read must block the drain"
        # The phase alone is also what a run that failed for any other reason leaves
        # behind; the blocking-workload refusal has to be the one that stopped this run.
        # Membership, not first: the exhausted drain loop is itself recorded failed (and
        # then rescued) before the branch tasks read its result.
        assert "Fail closed when ACM workload still blocks the MultiClusterHub drain" in _failed_task_names(result)
        assert _mch_record_of(result)["phase"] == "drain_pending"

    @pytest.mark.parametrize(
        "pod",
        [_job_owned_pod(), _statefulset_owned_pod(), _unrelated_replicaset_pod()],
        ids=["job", "statefulset", "unrelated-replicaset"],
    )
    def test_each_foreign_controller_blocks_the_drain_on_its_own(self, pod):
        """§22 (Job/StatefulSet/unrelated RS owner): one owner kind at a time.

        The aggregate timeout test cannot tell which owner blocked; these can.
        """
        result = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("drain_pending"),
            acm_pods=[pod],
            operator_replicasets=[_operator_replicaset(), _unrelated_replicaset()],
            mch_drain_retries=1,
            mch_drain_delay=0,
        )

        assert result["returncode"] != 0
        assert "Fail closed when ACM workload still blocks the MultiClusterHub drain" in _failed_task_names(result)
        assert _mch_record_of(result)["phase"] == "drain_pending"

    def test_a_final_pass_identity_inconsistency_blocks_completion(self):
        """§22 (final identity failure) and §15: the FINAL pass has its own identity check.

        A `drained` record runs no drain loop, so this inconsistency can only be caught by
        the final verification pass.
        """
        result = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("drained"),
            acm_pods=[],
            operator_deployments=[_operator_deployment(uid="rotated-deployment-uid")],
        )

        assert result["returncode"] != 0
        assert _mch_record_of(result)["phase"] == "recovery_required"

    def test_a_completed_reproof_accepts_an_absent_namespace_without_writing(self):
        """§16: namespace absence re-proves completion, and still writes nothing."""
        completed = _completed_mch_record()
        result = run_mch_role(
            mch_present=False,
            mch_record=completed,
            object_read_statuses={f"namespaces/{ACM_NAMESPACE}": 404},
        )

        assert _teardown_record_writes(result) == [], "a reproof may run no durable writer at all"
        assert result["returncode"] == 0
        assert _mch_record_of(result) == completed
        assert _acm_pod_lists(result) == [], "an absent namespace ends the pass before any Pod LIST"
        assert _teardown_records(result) == _teardown_records(result, before=True)

    def test_a_completed_reproof_refuses_a_reappeared_target(self):
        """§16: a target present after its recorded delete phase fails the invocation."""
        completed = _completed_mch_record()
        result = run_mch_role(mch_record=completed, acm_pods=[])

        assert result["returncode"] != 0, "a recorded, completed MultiClusterHub that is live again is a contradiction"
        # Named, because the reproof's own absence proof would refuse this too, one stage
        # later: the contradiction must be caught before the record is acted on at all.
        assert _first_failed_task(result) == "Refuse a post-delete MultiClusterHub record whose target is present again"
        assert _mch_deletes(result) == []
        assert _mch_record_of(result) == completed
        assert _teardown_records(result) == _teardown_records(result, before=True)
        assert _teardown_record_writes(result) == []

    def test_a_completed_reproof_refuses_a_blocking_pod(self):
        """§16: the reproof classifies, so unowned ACM workload fails it -- read-only."""
        completed = _completed_mch_record()
        result = run_mch_role(mch_present=False, mch_record=completed, acm_pods=[_spoof_pod()])

        assert result["returncode"] != 0
        assert _first_failed_task(result) == "Fail closed when a MultiClusterHub-unowned Pod survives the final proof"
        assert _mch_record_of(result) == completed
        assert _teardown_records(result) == _teardown_records(result, before=True)
        assert _teardown_record_writes(result) == []

    def test_a_dry_run_without_check_mode_reproves_nothing_against_a_completed_record(self):
        """§8 and §16: the reproof gate's `mode == 'execute'` half, without `--check`.

        `--check` alone would leave a `dry_run` execution mode able to classify Pods and
        read the operator identity on a record it may not touch.
        """
        completed = _completed_mch_record()
        result = run_mch_role(execution_mode="dry_run", mch_present=False, mch_record=completed, acm_pods=[])

        assert _teardown_record_writes(result) == []
        assert result["returncode"] == 0
        assert _acm_pod_lists(result) == [], "a dry run must classify no Pod"
        assert _csv_requests(result) == []
        assert _operator_identity_reads(result) == []
        assert _mch_record_of(result) == completed
        assert _teardown_records(result) == _teardown_records(result, before=True)

    def test_a_recorded_unavailable_identity_is_never_upgraded_by_a_live_csv(self):
        """§17: the identity a record was born with survives a hub that now resolves one.

        The CSV, Deployment and ReplicaSet are all served here; the run must read none of
        them and must complete under the recorded unavailable identity.
        """
        identity = mch_identity_unavailable()
        result = run_mch_role(
            mch_present=False,
            mch_record=mch_teardown_record("drain_pending", identity=identity),
            acm_pods=[],
        )

        record = _mch_record_of(result)
        assert record["phase"] == "completed"
        assert record["operator_identity_unavailable"] == identity
        assert "operator_deployment" not in record
        assert _csv_requests(result) == [], "a recorded identity is never recaptured"
        assert _operator_identity_reads(result) == []
        assert set(record["resource_versions"]) == {"drain_namespace", "drain_pods"}

    def test_a_later_execute_run_repeats_every_authoritative_read(self):
        """§22 (later execute rereads everything): a preview leaves nothing behind.

        Two separate invocations against two separate fake hubs, which is what "later
        run" means here: the preview must write no durable state, and the execute run
        must perform the whole authoritative read set itself rather than trusting
        anything the preview observed.
        """
        preview = run_mch_role(check_mode=True)

        assert preview["checkpoint"]["operational_data"] == preview["checkpoint"]["before_operational_data"]
        assert _mch_deletes(preview) == []

        live = run_mch_role(acm_pods=[_operator_owned_pod()])

        measured = {_request_shape(request) for request in live["requests"] if _is_mch_measured(request)}
        assert measured == _SCENARIO_ONE_SURFACE, "the execute run owes the whole authoritative surface itself"
        assert _mch_record_of(live)["phase"] == "completed"

    #: The §20 measurement scenarios: ``(id, fixture factory, measured surface, outcome)``.
    #: Every surface below was MEASURED on the shipped role
    #: (see `.superpowers/sdd/e6-plan/e6-request-measurement.md`). Scenarios 1-6a have a
    #: Python E4 counterpart in
    #: ``tests/test_decommission.py::TestMultiClusterHubRequestShapes`` (lines 4853-4958)
    #: and the expected set is that measured Python set, so a Collection surface that
    #: drifts from it fails here. Scenarios 6b and 7 are Collection-only and are labelled
    #: as such in the measurement file; no Python counterpart is claimed.
    #:
    #: The fixtures are FACTORIES: a row's record and Pod objects would otherwise be one
    #: shared mutable object across every run of the parametrised test.
    #: ``outcome`` is the substep outcome the run must reach (``None`` for a preview,
    #: which records none, and ``"failed"`` for a run that must not succeed), so a
    #: scenario cannot be satisfied by an early failure that happens to issue the right
    #: requests -- 6b's empty set most of all.
    _MEASURED_SURFACES = [
        (
            "1-fresh-captured-rolling-update",
            lambda: dict(
                acm_pods=[
                    _operator_owned_pod(
                        "multiclusterhub-operator-old-1", replicaset_name="mch-op-old", replicaset_uid="uid-rs-old"
                    ),
                    _operator_owned_pod(
                        "multiclusterhub-operator-new-1", replicaset_name="mch-op-new", replicaset_uid="uid-rs-new"
                    ),
                ],
                operator_replicasets=[
                    _operator_replicaset(name="mch-op-old", uid="uid-rs-old"),
                    _operator_replicaset(name="mch-op-new", uid="uid-rs-new"),
                ],
            ),
            _SCENARIO_ONE_SURFACE,
            "completed",
        ),
        (
            "2-fresh-unavailable-identity",
            lambda: dict(operator_csvs=[], acm_pods=[]),
            {
                MCH_LIST_SHAPE,
                MCH_GET_SHAPE,
                CSV_LIST_SHAPE,
                NAMESPACE_GET_SHAPE,
                POD_LIST_SHAPE,
                MCH_GUARDED_DELETE_SHAPE,
            },
            "completed",
        ),
        (
            "3-resume-drain-pending",
            lambda: dict(
                mch_present=False,
                mch_record=mch_teardown_record("drain_pending"),
                acm_pods=[_operator_owned_pod()],
            ),
            {
                MCH_LIST_SHAPE,
                MCH_GET_SHAPE,
                DEPLOYMENT_GET_SHAPE,
                REPLICASET_GET_SHAPE,
                NAMESPACE_GET_SHAPE,
                POD_LIST_SHAPE,
            },
            "completed",
        ),
        (
            "4-completed-reproof",
            lambda: dict(
                mch_present=False,
                mch_record=_completed_mch_record(),
                acm_pods=[_operator_owned_pod()],
            ),
            {
                MCH_LIST_SHAPE,
                MCH_GET_SHAPE,
                NAMESPACE_GET_SHAPE,
                POD_LIST_SHAPE,
                DEPLOYMENT_GET_SHAPE,
                REPLICASET_GET_SHAPE,
            },
            "completed",
        ),
        (
            "5-dry-run-preview",
            lambda: dict(execution_mode="dry_run"),
            {MCH_LIST_SHAPE, MCH_GET_SHAPE, CSV_LIST_SHAPE, CSV_GET_SHAPE, DEPLOYMENT_GET_SHAPE},
            None,
        ),
        ("6a-no-target-empty-list", lambda: dict(mch_present=False), {MCH_LIST_SHAPE}, "precondition_noop"),
        # Collection-only: discovery positively refuses the kind, so the LIST is never
        # issued at all. Python's `test_no_target_clean_skip` measures {MCH_LIST_SHAPE}.
        (
            "6b-no-target-kind-not-served",
            lambda: dict(mch_present=False, mch_kind_served=False),
            set(),
            "precondition_noop",
        ),
        # Collection-only: §20 scenario 7 has no Python precedent, so this set stands on
        # its own rather than being compared.
        (
            "7a-recovery-required",
            lambda: dict(
                mch_present=False,
                mch_record=mch_teardown_record("drain_pending"),
                object_read_statuses={f"namespaces/{ACM_NAMESPACE}": 500},
            ),
            {MCH_LIST_SHAPE, MCH_GET_SHAPE, NAMESPACE_GET_SHAPE},
            "failed",
        ),
        (
            "7b-recovery-required-resume",
            lambda: dict(
                mch_present=False,
                mch_record=mch_teardown_record("recovery_required"),
                acm_pods=[],
            ),
            {MCH_LIST_SHAPE, MCH_GET_SHAPE, NAMESPACE_GET_SHAPE, POD_LIST_SHAPE, DEPLOYMENT_GET_SHAPE},
            "completed",
        ),
    ]

    @pytest.mark.parametrize(
        "fixture, expected, outcome",
        [row[1:] for row in _MEASURED_SURFACES],
        ids=[row[0] for row in _MEASURED_SURFACES],
    )
    def test_the_measured_request_surface_of_every_scenario(self, fixture, expected, outcome):
        """§20: the measured Collection surface of each scenario, scenario by scenario.

        Every §20 scenario is here, including scenario 1 with the rolling-update fixture
        Python measures it with. Each expected set is the MEASURED one, and for scenarios
        2-6a it is also the measured Python E4 set, so a Collection-only or Python-only
        request shows up here. The outcome is asserted alongside the surface: a set alone
        can be produced by a run that failed before it did the work.
        """
        result = run_mch_role(**fixture())

        assert result["acm_switchover_decommission_result"]["substeps"].get("multiclusterhub") == outcome
        assert (result["returncode"] == 0) is (outcome != "failed")
        measured = {_request_shape(request) for request in result["requests"] if _is_mch_measured(request)}
        assert measured == expected
