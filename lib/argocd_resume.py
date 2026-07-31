from __future__ import annotations

import logging
from typing import Any, Optional

from lib import runtime_bootstrap
from lib.argocd_register import ArgocdPauseRegister
from lib.constants import (
    HUB_ROLE_PRIMARY,
    HUB_ROLE_SECONDARY,
    STEP_PAUSE_ARGOCD_APPS,
)
from lib.exceptions import SwitchoverError
from lib.kube_client import KubeClient
from lib.utils import Phase


def _required_resume_roles(paused_hub_roles: set[str], stored_identities: Any) -> set[str]:
    """Return hub roles that need live identity validation for a resume attempt."""
    known_hub_roles = {HUB_ROLE_PRIMARY, HUB_ROLE_SECONDARY}
    required_roles = {role for role in paused_hub_roles if role in known_hub_roles}
    if isinstance(stored_identities, dict):
        required_roles.update(role for role in stored_identities.keys() if role in known_hub_roles)
    return required_roles


def _ensure_resume_identity_data(args: Any, stored_identities: Any, logger: logging.Logger) -> None:
    """Reject legacy resume state without identity data unless the operator forces it."""
    if isinstance(stored_identities, dict) and stored_identities:
        return

    if not getattr(args, "force", False):
        raise SwitchoverError(
            "Argo CD resume state is missing hub identity data for recorded paused Applications. "
            "Refusing to resume because context names alone cannot prove the same live clusters. "
            "Use --force after manual verification to bind this legacy state to the current hubs."
        )
    logger.warning(
        "Argo CD resume state is missing hub identity data; "
        "--force used, binding legacy state to the current hubs for this resume attempt."
    )


def _resolve_recorded_context_mapping(
    args: Any,
    state: Any,
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    logger: logging.Logger,
) -> tuple[Optional[KubeClient], Optional[KubeClient], Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Resolve resume client mapping from recorded and current context names."""
    resume_primary = primary
    resume_secondary = secondary

    stored_primary_ctx, stored_secondary_ctx = runtime_bootstrap.state_contexts(state)
    current_primary_ctx = getattr(args, "primary_context", None) or runtime_bootstrap.client_context_name(primary)
    current_secondary_ctx = getattr(args, "secondary_context", None) or runtime_bootstrap.client_context_name(secondary)

    if not (stored_primary_ctx or stored_secondary_ctx):
        return (
            resume_primary,
            resume_secondary,
            stored_primary_ctx,
            stored_secondary_ctx,
            current_primary_ctx,
            current_secondary_ctx,
        )

    if stored_primary_ctx == current_secondary_ctx and stored_secondary_ctx == current_primary_ctx:
        logger.info("Argo CD resume contexts are reversed from the recorded state; swapping client mapping.")
        resume_primary, resume_secondary = secondary, primary
    elif (current_primary_ctx is not None and stored_primary_ctx != current_primary_ctx) or (
        current_secondary_ctx is not None and stored_secondary_ctx != current_secondary_ctx
    ):
        if not getattr(args, "force", False):
            raise SwitchoverError(
                "Argo CD resume contexts "
                f"({current_primary_ctx}/{current_secondary_ctx}) differ from recorded state "
                f"({stored_primary_ctx}/{stored_secondary_ctx}). "
                "This may indicate wrong-hub resume. Use --force to override, "
                "or --state-file to specify the correct state."
            )
        logger.warning(
            "Argo CD resume contexts (%s/%s) differ from recorded state (%s/%s); "
            "--force used, preserving state and using the provided client mapping.",
            current_primary_ctx,
            current_secondary_ctx,
            stored_primary_ctx,
            stored_secondary_ctx,
        )

    return (
        resume_primary,
        resume_secondary,
        stored_primary_ctx,
        stored_secondary_ctx,
        current_primary_ctx,
        current_secondary_ctx,
    )


def _load_recorded_primary_client(
    args: Any,
    resume_primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    stored_primary_ctx: Optional[str],
    current_secondary_ctx: Optional[str],
    logger: logging.Logger,
    *,
    allow_primary_load_from_state: bool,
    kube_client_factory: type[KubeClient],
    log_message: str,
) -> Optional[KubeClient]:
    """Return a primary resume client from existing mapping, secondary swap, or recorded state."""
    if resume_primary is not None or not stored_primary_ctx:
        return resume_primary

    if stored_primary_ctx == current_secondary_ctx and secondary is not None:
        return secondary

    if allow_primary_load_from_state:
        logger.info(log_message, stored_primary_ctx)
        return kube_client_factory(stored_primary_ctx, dry_run=getattr(args, "dry_run", False))

    return resume_primary


def prepare_argocd_resume_clients(
    args: Any,
    state: Any,
    paused_hub_roles: set[str],
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    logger: logging.Logger,
    *,
    allow_primary_load_from_state: bool,
    kube_client_factory: type[KubeClient] = KubeClient,
) -> tuple[Optional[KubeClient], Optional[KubeClient]]:
    """Resolve client mapping and validate hub identity bindings before resume."""
    (
        resume_primary,
        resume_secondary,
        stored_primary_ctx,
        _stored_secondary_ctx,
        _current_primary_ctx,
        current_secondary_ctx,
    ) = _resolve_recorded_context_mapping(args, state, primary, secondary, logger)

    primary_apps_recorded = HUB_ROLE_PRIMARY in paused_hub_roles
    if primary_apps_recorded and resume_primary is None:
        if not stored_primary_ctx:
            raise SwitchoverError(
                "Argo CD resume state references Applications paused on the primary hub, "
                "but the recorded primary context is missing. Pass --primary-context or "
                "--state-file for a valid switchover state."
            )
        resume_primary = _load_recorded_primary_client(
            args,
            resume_primary,
            secondary,
            stored_primary_ctx,
            current_secondary_ctx,
            logger,
            allow_primary_load_from_state=allow_primary_load_from_state,
            kube_client_factory=kube_client_factory,
            log_message="Argo CD resume primary context omitted; loading recorded primary hub client: %s",
        )

    stored_identities = runtime_bootstrap.stored_hub_identities(state)
    if (
        isinstance(stored_identities, dict)
        and stored_identities.get(HUB_ROLE_PRIMARY)
        and resume_primary is None
        and stored_primary_ctx
    ):
        resume_primary = _load_recorded_primary_client(
            args,
            resume_primary,
            secondary,
            stored_primary_ctx,
            current_secondary_ctx,
            logger,
            allow_primary_load_from_state=allow_primary_load_from_state,
            kube_client_factory=kube_client_factory,
            log_message="Argo CD resume identity validation loading recorded primary hub client: %s",
        )

    _ensure_resume_identity_data(args, stored_identities, logger)
    live_identities = runtime_bootstrap.collect_hub_identities(resume_primary, resume_secondary)
    required_roles = _required_resume_roles(paused_hub_roles, stored_identities)
    missing_roles = sorted(role for role in required_roles if role not in live_identities)
    if missing_roles:
        raise SwitchoverError(
            "Argo CD resume hub identity validation failed: missing live client for recorded "
            + ", ".join(missing_roles)
            + " hub identity."
        )

    state.ensure_hub_identities(
        live_identities,
        allow_legacy_backfill=getattr(args, "force", False),
        persist=False,
    )
    return resume_primary, resume_secondary


def run_argocd_resume_only(
    args: Any,
    state: Any,
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    logger: logging.Logger,
    *,
    kube_client_factory: type[KubeClient] = KubeClient,
) -> bool:
    """Load state and restore Argo CD auto-sync for previously paused Applications, then exit."""
    register = ArgocdPauseRegister(state, dry_run=getattr(args, "dry_run", False))
    status = register.status()
    if not status.run_id or not status.entry_count:
        logger.error("No Argo CD paused apps in state file (argocd_run_id or argocd_paused_apps missing).")
        return False
    logger.info(
        "Resuming Argo CD auto-sync from state (run_id=%s, %d app(s))",
        status.run_id,
        status.entry_count,
    )
    try:
        resume_primary, resume_secondary = prepare_argocd_resume_clients(
            args,
            state,
            register.paused_hub_roles(),
            primary,
            secondary,
            logger,
            allow_primary_load_from_state=True,
            kube_client_factory=kube_client_factory,
        )
    except Exception as exc:
        logger.error("Resume-only hub identity validation failed: %s", exc)
        return False

    summary = register.resume(resume_primary, resume_secondary)
    if summary.dry_run:
        logger.info(
            "[DRY-RUN] Would restore %d and skip %d already-resumed Application(s); "
            "%d would remain in the register.",
            summary.restored,
            summary.already_resumed,
            summary.projected_remaining,
        )
    else:
        logger.info(
            "Restored %d and already resumed %d Application(s); %d remaining in register.",
            summary.restored,
            summary.already_resumed,
            summary.remaining_in_register,
        )
    if summary.failed:
        logger.error("Argo CD auto-sync restore failed for %d Application(s).", summary.failed)
        return False
    return True


def attempt_argocd_resume_on_failure(
    args: Any,
    state: Any,
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    logger: logging.Logger,
    *,
    kube_client_factory: type[KubeClient] = KubeClient,
) -> None:
    """Best-effort resume of paused ArgoCD Applications after a switchover failure."""
    if not getattr(args, "argocd_resume_on_failure", False):
        return

    register = ArgocdPauseRegister(state, dry_run=getattr(args, "dry_run", False))
    status = register.status()
    if not status.entry_count or not status.run_id:
        return

    logger.warning(
        "Switchover failed — attempting to resume %d paused Argo CD Application(s) (run_id=%s)...",
        status.entry_count,
        status.run_id,
    )
    try:
        resume_primary, resume_secondary = prepare_argocd_resume_clients(
            args,
            state,
            register.paused_hub_roles(),
            primary,
            secondary,
            logger,
            allow_primary_load_from_state=False,
            kube_client_factory=kube_client_factory,
        )
        summary = register.resume(resume_primary, resume_secondary)
        logger.info(
            "Argo CD resume-on-failure%s: restored=%d, already_resumed=%d, failed=%d",
            " [DRY-RUN, would have]" if summary.dry_run else "",
            summary.restored,
            summary.already_resumed,
            summary.failed,
        )
        if summary.failed or summary.remaining_in_register:
            logger.warning(
                "Argo CD resume-on-failure left %d Application(s) in the pause register. "
                "Use --argocd-resume-only to retry manually.",
                summary.projected_remaining,
            )
            return

        state.clear_step_completed(STEP_PAUSE_ARGOCD_APPS)
        retry_phase = Phase.PREFLIGHT if getattr(args, "restore_only", False) else Phase.PRIMARY_PREP
        state.add_error(
            "Argo CD resume-on-failure completed; retry must re-run Argo CD pause before continuing.",
            phase=retry_phase.value,
        )
        logger.info("Argo CD resume-on-failure cleanup completed; durable pause state cleared.")
    except Exception as exc:
        logger.warning(
            "Argo CD resume-on-failure failed: %s. Use --argocd-resume-only to resume manually.",
            exc,
        )
