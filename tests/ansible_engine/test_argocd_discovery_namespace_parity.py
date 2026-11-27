"""Differential engine parity for Argo CD discovery namespace validation (#401).

Evaluates the Ansible Collection's ``roles/argocd_manage/tasks/discover.yml``
pre-discovery validation and resolution tasks through Ansible's real
``Templar`` over the same persisted and explicit namespace inputs exercised
against the Python runtime (`trusted_application_namespaces` /
`ArgocdPauseRegister`).
"""

from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

from lib import argocd as argocd_lib
from lib.argocd_register import ArgocdPauseRegister
from lib.constants import STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES, STATE_KEY_ARGOCD_RUN_ID
from lib.exceptions import ValidationError
from lib.utils import StateManager

try:  # ansible-core 2.19+ templates only strings marked as trusted.
    from ansible.template import trust_as_template
except ImportError:  # pragma: no cover - ansible-core < 2.19

    def trust_as_template(value):
        return value


DISCOVER_TASKS = (
    Path(__file__).resolve().parents[2]
    / "ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/discover.yml"
)
_UNSET = object()


def _pre_list_tasks() -> list[dict]:
    top_tasks = yaml.safe_load(DISCOVER_TASKS.read_text(encoding="utf-8"))
    block = next(t for t in top_tasks if t.get("name") == "Discover Argo CD Applications from cluster")["block"]
    stop_index = next(
        idx for idx, task in enumerate(block) if task.get("name") == "List Applications in trusted namespaces"
    )
    return block[:stop_index]


def _render_value(loader: DataLoader, variables: dict, value):
    if isinstance(value, str):
        return Templar(loader=loader, variables=variables).template(trust_as_template(value))
    if isinstance(value, list):
        return [_render_value(loader, variables, item) for item in value]
    if isinstance(value, dict):
        return {k: _render_value(loader, variables, v) for k, v in value.items()}
    return value


def _when_matches(loader: DataLoader, variables: dict, when_spec) -> bool:
    if when_spec is None:
        return True
    conditions = when_spec if isinstance(when_spec, list) else [when_spec]
    templar = Templar(loader=loader, variables=variables)
    for condition in conditions:
        if isinstance(condition, bool):
            if not condition:
                return False
            continue
        rendered = templar.template(trust_as_template("{{ (%s) | bool }}" % condition))
        if rendered is not True:
            return False
    return True


def _run_collection_pre_list(
    *,
    persisted_namespaces=_UNSET,
    explicit_argocd=_UNSET,
    discover_hub: str = "primary",
    mode_override: str = "pause",
) -> tuple[bool, object]:
    """Run discover.yml pre-LIST tasks using Ansible's real Templar."""
    loader = DataLoader()
    variables: dict = {
        "_argocd_discover_hub": discover_hub,
        "acm_switchover_argocd_mode_override": mode_override,
    }
    if persisted_namespaces is not _UNSET:
        variables["acm_switchover_argocd_discovery_namespaces"] = copy.deepcopy(persisted_namespaces)
    if explicit_argocd is not _UNSET:
        variables["acm_switchover_argocd"] = copy.deepcopy(explicit_argocd)

    for task in _pre_list_tasks():
        base_task_vars = task.get("vars") or {}
        if "loop" in task:
            loop_scope = dict(variables)
            for k, v in base_task_vars.items():
                loop_scope[k] = _render_value(loader, loop_scope, v)
            loop_items = _render_value(loader, loop_scope, task["loop"]) or []
        else:
            loop_items = [_UNSET]

        for loop_item in loop_items:
            scope = dict(variables)
            if loop_item is not _UNSET:
                scope["item"] = loop_item
            for k, v in base_task_vars.items():
                scope[k] = _render_value(loader, scope, v)

            if not _when_matches(loader, scope, task.get("when")):
                continue

            if "ansible.builtin.fail" in task:
                msg = _render_value(loader, scope, task["ansible.builtin.fail"].get("msg", "failed"))
                return False, str(msg)
            if "ansible.builtin.assert" in task:
                for cond in task["ansible.builtin.assert"]["that"]:
                    if not _when_matches(loader, scope, cond):
                        msg = _render_value(loader, scope, task["ansible.builtin.assert"].get("fail_msg", "failed"))
                        return False, str(msg)
            elif "ansible.builtin.set_fact" in task:
                for fact_name, expr in task["ansible.builtin.set_fact"].items():
                    variables[fact_name] = _render_value(loader, scope, expr)

    return True, {
        "trusted_namespaces": list(variables.get("_argocd_trusted_discovery_namespaces", [])),
        "use_scoped_discovery": bool(variables.get("_argocd_use_scoped_discovery", False)),
    }


@pytest.mark.parametrize(
    ("persisted_scope", "expected_namespaces", "expected_scoped"),
    [
        (_UNSET, [], False),
        ({}, [], False),
        ({"primary": []}, [], False),
        ({"primary": ["9-team"]}, ["9-team"], True),
        ({"primary": ["team-b", "9-team", "9-team"]}, ["9-team", "team-b"], True),
    ],
    ids=["absent", "empty-map", "active-empty-list", "digit-leading", "dedup-sorted"],
)
def test_valid_persisted_discovery_namespaces_match_between_engines(
    tmp_path, persisted_scope, expected_namespaces, expected_scoped
):
    state = StateManager(str(tmp_path / "state.json"))
    state._set_config(STATE_KEY_ARGOCD_RUN_ID, "run-1")
    if persisted_scope is not _UNSET:
        state._set_config(STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES, persisted_scope)

    register = ArgocdPauseRegister(state)
    py_map = register._store._get_discovery_namespaces_by_hub()
    py_trusted = argocd_lib.trusted_application_namespaces(py_map.get("primary"))

    ok, col_result = _run_collection_pre_list(persisted_namespaces=persisted_scope)
    assert ok is True, col_result
    assert col_result["use_scoped_discovery"] is expected_scoped
    assert col_result["trusted_namespaces"] == expected_namespaces
    assert (py_trusted or []) == col_result["trusted_namespaces"]
    assert (py_trusted is not None) is col_result["use_scoped_discovery"]


@pytest.mark.parametrize(
    "malformed_scope",
    [
        None,
        "not-a-mapping",
        ["9-team"],
        {1: ["9-team"]},
        {"primary": "9-team"},
        {"primary": None},
        {"primary": [""]},
        {"primary": [" "]},
        {"primary": [" 9-team "]},
        {"primary": ["9-team\n"]},
        {"primary": ["bad.ns"]},
        {"primary": ["-bad"]},
        {"primary": ["Bad"]},
        {"primary": ["9-team-"]},
        {"primary": ["9-team", "bad.ns"]},
        {"primary": ["9-team"], "secondary": ["bad.ns"]},
    ],
)
@pytest.mark.parametrize("run_id", ["run-1", None])
def test_malformed_persisted_discovery_namespaces_rejected_identically(tmp_path, malformed_scope, run_id):
    state = StateManager(str(tmp_path / "state.json"))
    if run_id is not None:
        state._set_config(STATE_KEY_ARGOCD_RUN_ID, run_id)
    state._set_config(STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES, malformed_scope)
    primary = Mock()
    secondary = Mock()

    with pytest.raises(ValidationError):
        ArgocdPauseRegister(state).pause_hubs([(primary, "primary"), (secondary, "secondary")])
    assert primary.method_calls == []
    assert secondary.method_calls == []

    ok, detail = _run_collection_pre_list(persisted_namespaces=malformed_scope)
    assert ok is False, f"Collection unexpectedly accepted malformed persisted scope {malformed_scope!r}: {detail}"


@pytest.mark.parametrize(
    ("namespace", "should_accept"),
    [
        ("9-team", True),
        ("argocd", True),
        (" ", False),
        ("", False),
        (" 9-team ", False),
        ("9-team\n", False),
        ("bad.ns", False),
        ("-bad", False),
        ("Bad", False),
        ("9-team-", False),
        (None, False),
    ],
)
def test_explicit_argocd_namespace_validation_matches_python(namespace, should_accept):
    from lib.validation import InputValidator

    if should_accept:
        assert InputValidator.validate_kubernetes_namespace(namespace) is None
        ok, result = _run_collection_pre_list(explicit_argocd={"namespace": namespace})
        assert ok is True, result
        assert result["trusted_namespaces"] == [namespace]
        assert result["use_scoped_discovery"] is True
    else:
        with pytest.raises(ValidationError):
            InputValidator.validate_kubernetes_namespace(namespace)
        ok, detail = _run_collection_pre_list(explicit_argocd={"namespace": namespace})
        assert ok is False, f"Collection unexpectedly accepted explicit namespace {namespace!r}: {detail}"
