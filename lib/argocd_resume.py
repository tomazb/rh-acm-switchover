from __future__ import annotations

import logging
from typing import Any, Optional

from lib import argocd as argocd_lib
from lib import runtime_bootstrap
from lib.argocd_coordinator import clear_argocd_pause_state
from lib.constants import (
    HUB_ROLE_PRIMARY,
    HUB_ROLE_SECONDARY,
    STATE_KEY_ARGOCD_PAUSE_DRY_RUN,
    STATE_KEY_ARGOCD_PAUSED_APPS,
    STATE_KEY_ARGOCD_RUN_ID,
    STEP_PAUSE_ARGOCD_APPS,
)
from lib.kube_client import KubeClient
from lib.utils import Phase


def prepare_argocd_resume_clients(
    args: Any,
    state: Any,
    paused_apps: list[dict[str, Any]],
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    logger: logging.Logger,
    *,
    allow_primary_load_from_state: bool,
) -> tuple[Optional[KubeClient], Optional[KubeClient]]:
    """Resolve client mapping and validate hub identity bindings before resume."""
    resume_primary = primary
    resume_secondary = secondary

    stored_primary_ctx, stored_secondary_ctx = runtime_bootstrap.state_contexts(state)
    current_primary_ctx = getattr(args, "primary_context", None) or runtime_bootstrap.client_context_name(primary)
    current_secondary_ctx = getattr(args, "secondary_context", None) or runtime_bootstrap.client_context_name(secondary)

    if stored_primary_ctx or stored_secondary_ctx:
        if stored_primary_ctx == current_secondary_ctx and stored_secondary_ctx == current_primary_ctx:
            logger.info("Argo CD resume contexts are reversed from the recorded state; swapping client mapping.")
            resume_primary, resume_secondary = secondary, primary
        elif (current_primary_ctx is not None and stored_primary_ctx != current_primary_ctx) or (
            current_secondary_ctx is not None and stored_secondary_ctx != current_secondary_ctx
        ):
            if not getattr(args, "force", False):
                raise ValueError(
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

    primary_apps_recorded = any(isinstance(item, dict) and item.get("hub") == HUB_ROLE_PRIMARY for item in paused_apps)
    if primary_apps_recorded and resume_primary is None:
        if not stored_primary_ctx:
            raise ValueError(
                "Argo CD resume state references Applications paused on the primary hub, "
                "but the recorded primary context is missing. Pass --primary-context or "
                "--state-file for a valid switchover state."
            )
        if stored_primary_ctx == current_secondary_ctx and secondary is not None:
            resume_primary = secondary
        elif allow_primary_load_from_state:
            logger.info(
                "Argo CD resume primary context omitted; loading recorded primary hub client: %s",
                stored_primary_ctx,
            )
            resume_primary = KubeClient(stored_primary_ctx, dry_run=getattr(args, "dry_run", False))

    stored_identities = runtime_bootstrap.stored_hub_identities(state)
    if stored_identities.get(HUB_ROLE_PRIMARY) and resume_primary is None and stored_primary_ctx:
        if stored_primary_ctx == current_secondary_ctx and secondary is not None:
            resume_primary = secondary
        elif allow_primary_load_from_state:
            logger.info(
                "Argo CD resume identity validation loading recorded primary hub client: %s",
                stored_primary_ctx,
            )
            resume_primary = KubeClient(stored_primary_ctx, dry_run=getattr(args, "dry_run", False))

    if not stored_identities:
        if not getattr(args, "force", False):
            raise ValueError(
                "Argo CD resume state is missing hub identity data for recorded paused Applications. "
                "Refusing to resume because context names alone cannot prove the same live clusters. "
                "Use --force after manual verification to bind this legacy state to the current hubs."
            )
        logger.warning(
            "Argo CD resume state is missing hub identity data; "
            "--force used, binding legacy state to the current hubs for this resume attempt."
        )

    live_identities = runtime_bootstrap.collect_hub_identities(resume_primary, resume_secondary)
    known_hub_roles = {HUB_ROLE_PRIMARY, HUB_ROLE_SECONDARY}
    required_roles = {
        entry.get("hub") for entry in paused_apps if isinstance(entry, dict) and entry.get("hub") in known_hub_roles
    }
    required_roles.update(role for role in stored_identities.keys() if role in known_hub_roles)
    missing_roles = sorted(role for role in required_roles if role not in live_identities)
    if missing_roles:
        raise ValueError(
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
) -> bool:
    """Load state and restore Argo CD auto-sync for previously paused Applications, then exit."""
    if state.get_config(STATE_KEY_ARGOCD_PAUSE_DRY_RUN, False):
        logger.error(
            "Argo CD resume requested, but the pause step was run in dry-run mode. "
            "Re-run pause without --dry-run to generate resumable state."
        )
        return False
    run_id = state.get_config(STATE_KEY_ARGOCD_RUN_ID)
    paused_apps = state.get_config(STATE_KEY_ARGOCD_PAUSED_APPS) or []
    if not run_id or not paused_apps:
        logger.error("No Argo CD paused apps in state file (argocd_run_id or argocd_paused_apps missing).")
        return False
    logger.info(
        "Resuming Argo CD auto-sync from state (run_id=%s, %d app(s))",
        run_id,
        len(paused_apps),
    )
    try:
        resume_primary, resume_secondary = prepare_argocd_resume_clients(
            args,
            state,
            paused_apps,
            primary,
            secondary,
            logger,
            allow_primary_load_from_state=True,
        )
    except Exception as exc:
        logger.error("Resume-only hub identity validation failed: %s", exc)
        return False

    summary = argocd_lib.resume_recorded_applications(
        paused_apps,
        run_id,
        resume_primary,
        resume_secondary,
        logger,
    )
    logger.info(
        "Restored %d and already resumed %d of %d Application(s).",
        summary.restored,
        summary.already_resumed,
        len(paused_apps),
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
) -> None:
    """Best-effort resume of paused ArgoCD Applications after a switchover failure."""
    if not getattr(args, "argocd_resume_on_failure", False):
        return

    paused_apps = state.get_config(STATE_KEY_ARGOCD_PAUSED_APPS) or []
    run_id = state.get_config(STATE_KEY_ARGOCD_RUN_ID)
    if not paused_apps or not run_id:
        return

    logger.warning(
        "Switchover failed — attempting to resume %d paused Argo CD Application(s) (run_id=%s)...",
        len(paused_apps),
        run_id,
    )
    try:
        resume_primary, resume_secondary = prepare_argocd_resume_clients(
            args,
            state,
            paused_apps,
            primary,
            secondary,
            logger,
            allow_primary_load_from_state=False,
        )
        summary = argocd_lib.resume_recorded_applications(
            paused_apps,
            run_id,
            resume_primary,
            resume_secondary,
            logger,
        )
        logger.info(
            "Argo CD resume-on-failure: restored=%d, already_resumed=%d, failed=%d",
            summary.restored,
            summary.already_resumed,
            summary.failed,
        )
        if summary.failed:
            logger.warning(
                "Argo CD resume-on-failure: %d Application(s) could not be resumed. "
                "Use --argocd-resume-only to retry manually.",
                summary.failed,
            )
            return

        accounted_for = int(summary.restored or 0) + int(summary.already_resumed or 0)
        if accounted_for < len(paused_apps):
            logger.warning(
                "Argo CD resume-on-failure accounted for %d of %d recorded Application(s). "
                "Preserving pause state for --argocd-resume-only retry.",
                accounted_for,
                len(paused_apps),
            )
            return

        state.clear_step_completed(STEP_PAUSE_ARGOCD_APPS)
        clear_argocd_pause_state(state)
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
