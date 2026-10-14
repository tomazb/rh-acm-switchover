from __future__ import annotations

import logging
from datetime import datetime, timezone
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
from lib.run_record import RunRecord, migration_journal_present
from lib.utils import Phase

# PR C decisions amendment section 2.2: the phases a journal can exist in when they fail.
_JOURNAL_RESCUE_PHASES = (Phase.ACTIVATION, Phase.POST_ACTIVATION, Phase.FINALIZATION)


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
    if status.run_id and not status.entry_count and not status.discarded_entry_count:
        # resume() empties the register and clears the run id as two writes; a crash
        # between them leaves this state. Nothing was discarded, so the register was
        # emptied by a successful resume -- finish its cleanup rather than failing
        # forever. A register whose records were dropped as unresumable is a
        # different situation and still falls through to the error below.
        # finish_cleanup() is a no-op in dry-run, so say what will actually happen
        # rather than claiming a write that dry-run never performs.
        logger.info(
            "Argo CD pause register is already empty (run_id=%s); previous resume completed. %s",
            status.run_id,
            (
                "[DRY-RUN] Would clear leftover run metadata."
                if getattr(args, "dry_run", False)
                else "Clearing leftover run metadata."
            ),
        )
        register.finish_cleanup()
        return True
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
    if migration_journal_present(state.capture_state_snapshot()):
        _resume_on_failure_under_journal(
            args, state, primary, secondary, logger, register, status.entry_count, kube_client_factory
        )
        return
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


def _resume_on_failure_under_journal(
    args: Any,
    state: Any,
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    logger: logging.Logger,
    register: ArgocdPauseRegister,
    entry_count: int,
    kube_client_factory: type[KubeClient],
) -> None:
    """Resume Argo CD after a failure that left a migration journal (PR C decisions amendment 2.2).

    No rewind to a pre-freeze phase: the pause step stays completed and the retry
    stays at the failed phase. The re-pause marker is durable before any resume
    mutation; every failure is logged and recorded against the failed phase, so
    no caller records a phase-less error that would lose the retry position.
    """
    failed_phase = state.get_last_error_phase()
    if failed_phase not in _JOURNAL_RESCUE_PHASES:
        logger.error(
            "Argo CD resume-on-failure refused: a migration journal exists but the failed phase (%s) "
            "cannot be determined. No Application was resumed; use --argocd-resume-only after review.",
            failed_phase.value if failed_phase else "unknown",
        )
        return
    if getattr(args, "dry_run", False):
        logger.info(
            "[DRY-RUN] Would record the Argo CD re-pause marker, resume %d Application(s), and re-pause "
            "them before %s continues on retry.",
            entry_count,
            failed_phase.value,
        )
        return

    run_record = RunRecord(state)
    try:
        run_record.record_argocd_repause_required(failed_phase.value, datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        _record_rescue_failure(
            state,
            logger,
            failed_phase,
            f"Argo CD resume-on-failure refused: the re-pause marker could not be recorded durably ({exc}). "
            "No Application was resumed; they stay paused.",
        )
        return

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
    except Exception as exc:
        _record_rescue_failure(
            state,
            logger,
            failed_phase,
            f"Argo CD resume-on-failure failed: {exc}. The retry re-pauses Argo CD before {failed_phase.value} "
            "continues; use --argocd-resume-only to resume manually.",
        )
        return
    if summary.failed or summary.remaining_in_register:
        _record_rescue_failure(
            state,
            logger,
            failed_phase,
            f"Argo CD resume-on-failure left {summary.remaining_in_register} Application(s) paused "
            f"(failed={summary.failed}). The retry re-pauses Argo CD before {failed_phase.value} continues; "
            "use --argocd-resume-only to resume manually.",
        )
        return
    logger.info(
        "Argo CD resume-on-failure restored %d and already resumed %d Application(s); the migration journal "
        "is kept and the retry re-pauses them before %s continues.",
        summary.restored,
        summary.already_resumed,
        failed_phase.value,
    )


def _record_rescue_failure(state: Any, logger: logging.Logger, failed_phase: Phase, message: str) -> None:
    """Log a rescue failure and record it against the failed phase, never against FAILED."""
    logger.error(message)
    try:
        state.add_error(message, phase=failed_phase.value)
    except Exception as exc:
        logger.error("The rescue failure could not be recorded in the state file: %s", exc)


def repause_argocd_after_rescue(
    args: Any,
    state: Any,
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    logger: logging.Logger,
) -> bool:
    """Re-pause Argo CD before any phase work when a rescue left the re-pause marker.

    PR C decisions amendment section 2.2 rule 3: the same register and pause
    semantics as the original pause (both hubs for a switchover, the secondary
    hub for restore-only), bypassing the completed pause step and never
    replaying the rest of primary preparation. The marker is cleared only after
    the re-pause succeeds; a failure fails the run at the recorded phase.
    """
    run_record = RunRecord(state)
    marker_phase = run_record.argocd_repause_required()
    if marker_phase is None:
        return True
    failed_phase = Phase(marker_phase)
    hubs = [(secondary, HUB_ROLE_SECONDARY)]
    if not getattr(args, "restore_only", False):
        hubs.insert(0, (primary, HUB_ROLE_PRIMARY))
    roles = ", ".join(role for _, role in hubs)
    if getattr(args, "dry_run", False):
        logger.info("[DRY-RUN] Would re-pause Argo CD auto-sync on the %s hub(s) before %s.", roles, marker_phase)
        return True

    logger.info("Re-pausing Argo CD auto-sync on the %s hub(s) before %s (resume-on-failure).", roles, marker_phase)
    try:
        summary = ArgocdPauseRegister(state).pause_hubs(hubs)
    except Exception as exc:
        return _fail_repause(state, logger, failed_phase, f"Argo CD re-pause failed: {exc}")
    if summary.blocked or summary.failed:
        return _fail_repause(
            state,
            logger,
            failed_phase,
            f"Argo CD re-pause failed (blocked={summary.blocked}, failed={summary.failed}); "
            "pause the owning ApplicationSets or the Applications first",
        )
    try:
        run_record.clear_argocd_repause_required()
    except Exception as exc:
        return _fail_repause(
            state, logger, failed_phase, f"Argo CD re-pause succeeded but its marker could not be cleared ({exc})"
        )
    return True


def _fail_repause(state: Any, logger: logging.Logger, failed_phase: Phase, message: str) -> bool:
    message += f"; the retry stays at {failed_phase.value} and re-pauses again."
    logger.error(message)
    state.add_error(message, phase=failed_phase.value)
    state.set_phase(Phase.FAILED)
    return False
