"""Shared helpers for acm_switchover entrypoint tests."""

from types import SimpleNamespace
from unittest.mock import Mock

from lib.utils import Phase

PHASE_BY_HANDLER = {
    "preflight": Phase.PREFLIGHT,
    "primary_prep": Phase.PRIMARY_PREP,
    "activation": Phase.ACTIVATION,
    "post_activation": Phase.POST_ACTIVATION,
    "finalization": Phase.FINALIZATION,
}


def make_switchover_args(**overrides):
    defaults = dict(
        force=False,
        validate_only=False,
        state_file="state.json",
        method="passive",
        skip_rbac_validation=True,
        skip_observability_checks=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_restore_only_args(**overrides):
    defaults = dict(
        restore_only=True,
        primary_context=None,
        secondary_context="new-hub",
        method=None,
        old_hub_action=None,
        validate_only=False,
        dry_run=False,
        force=False,
        state_file="state.json",
        skip_rbac_validation=True,
        skip_observability_checks=False,
        skip_gitops_check=True,
        argocd_manage=False,
        argocd_resume_on_failure=False,
        activation_method="patch",
        manage_auto_import_strategy=False,
        min_managed_clusters=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def phase_stub(name, call_order, succeeds=True):
    target_phase = PHASE_BY_HANDLER[name]

    def stub(_args, state, *_rest, **_kwargs):
        call_order.append(name)
        state.set_phase(target_phase)
        return succeeds

    return stub


def failing_phase_stub(name, call_order):
    target_phase = PHASE_BY_HANDLER[name]

    def stub(_args, state, *_rest, **_kwargs):
        from acm_switchover import _fail_phase

        call_order.append(name)
        state.set_phase(target_phase)
        return _fail_phase(state, f"{name} failed!", Mock())

    return stub


def make_resume_on_failure_args(*, argocd_resume_on_failure=True, restore_only=False):
    return SimpleNamespace(argocd_resume_on_failure=argocd_resume_on_failure, restore_only=restore_only)


def make_resume_only_context_args(primary_context, secondary_context, force=False):
    return SimpleNamespace(
        primary_context=primary_context,
        secondary_context=secondary_context,
        force=force,
    )
