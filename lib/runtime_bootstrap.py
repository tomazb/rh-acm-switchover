import argparse
import logging
import os
from dataclasses import dataclass
from typing import Optional

from lib import KubeClient, StateManager
from lib.constants import (
    STATE_DIR_DEFAULT,
    STATE_DIR_ENV_VAR,
    STATE_FILE_NAME_PREFIX,
    STATE_FILE_PRIMARY_RESTORE_ONLY_LABEL,
    STATE_FILE_SECONDARY_NONE_LABEL,
)
from lib.validation import InputValidator


@dataclass(frozen=True)
class RuntimeContext:
    state_file: str
    state: StateManager
    primary: Optional[KubeClient]
    secondary: Optional[KubeClient]
    should_bind_state: bool
    should_record_state_errors: bool


def sanitize_context_identifier(value: str) -> str:
    return InputValidator.sanitize_context_identifier(value)


def get_default_state_dir() -> str:
    env_state_dir = os.environ.get(STATE_DIR_ENV_VAR)
    if env_state_dir and env_state_dir.strip():
        return env_state_dir.strip()
    return STATE_DIR_DEFAULT


def build_default_state_file(primary_ctx: Optional[str], secondary_ctx: Optional[str]) -> str:
    primary_label = primary_ctx or STATE_FILE_PRIMARY_RESTORE_ONLY_LABEL
    secondary_label = secondary_ctx or STATE_FILE_SECONDARY_NONE_LABEL
    slug = f"{sanitize_context_identifier(primary_label)}__{sanitize_context_identifier(secondary_label)}"
    return os.path.join(get_default_state_dir(), f"{STATE_FILE_NAME_PREFIX}{slug}.json")


def find_resume_state_candidates(secondary_ctx: str) -> list[str]:
    state_dir = get_default_state_dir()
    if not os.path.isdir(state_dir):
        return []

    secondary_slug = sanitize_context_identifier(secondary_ctx)
    suffix = f"__{secondary_slug}.json"
    candidates = []
    for entry in os.listdir(state_dir):
        if not entry.startswith(STATE_FILE_NAME_PREFIX) or not entry.endswith(suffix):
            continue
        path = os.path.join(state_dir, entry)
        if os.path.isfile(path):
            candidates.append(path)
    return sorted(candidates)


def resolve_state_file(
    requested_path: Optional[str],
    primary_ctx: Optional[str],
    secondary_ctx: Optional[str],
    argocd_resume_only: bool = False,
) -> str:
    if requested_path:
        return requested_path

    default_path = build_default_state_file(primary_ctx, secondary_ctx)
    if not argocd_resume_only or not secondary_ctx:
        return default_path

    if primary_ctx is None:
        candidates = find_resume_state_candidates(secondary_ctx)
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise ValueError(
                "Multiple candidate state files found for --argocd-resume-only "
                f"matching secondary context {secondary_ctx}: {', '.join(candidates)}. "
                "Pass --state-file explicitly or provide --primary-context to disambiguate."
            )
        return default_path

    reversed_path = build_default_state_file(secondary_ctx, primary_ctx)
    default_exists = os.path.exists(default_path)
    reversed_exists = os.path.exists(reversed_path)

    if reversed_exists and not default_exists:
        return reversed_path

    if default_exists and reversed_exists and default_path != reversed_path:
        raise ValueError(
            "Multiple candidate state files found for --argocd-resume-only "
            f"({default_path} and {reversed_path}). "
            "Pass --state-file explicitly to choose the correct resume state."
        )

    return default_path


def initialize_clients(
    args: argparse.Namespace,
    logger: logging.Logger,
    client_factory=None,
) -> tuple[Optional[KubeClient], Optional[KubeClient]]:
    if client_factory is None:
        client_factory = KubeClient

    dry_run = getattr(args, "dry_run", False)

    primary = None
    primary_context = getattr(args, "primary_context", None)
    if primary_context:
        logger.info("Connecting to primary hub: %s", primary_context)
        primary = client_factory(primary_context, dry_run=dry_run)

    secondary = None
    secondary_context = getattr(args, "secondary_context", None)
    if secondary_context:
        logger.info("Connecting to secondary hub: %s", secondary_context)
        secondary = client_factory(secondary_context, dry_run=dry_run)

    return primary, secondary


def collect_hub_identities(
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
) -> dict[str, dict[str, Optional[str]]]:
    identities: dict[str, dict[str, Optional[str]]] = {}
    if primary is not None:
        identities["primary"] = primary.get_cluster_identity()
    if secondary is not None:
        identities["secondary"] = secondary.get_cluster_identity()
    return identities


def stored_hub_identities(state: StateManager) -> dict:
    state_data = getattr(state, "state", {}) or {}
    if not isinstance(state_data, dict):
        return {}
    identities = state_data.get("hub_identities") or {}
    return identities if isinstance(identities, dict) else {}


def state_contexts(state: StateManager) -> tuple[Optional[str], Optional[str]]:
    state_data = getattr(state, "state", {}) or {}
    if not isinstance(state_data, dict):
        return None, None
    stored_contexts = state_data.get("contexts") or {}
    if not isinstance(stored_contexts, dict):
        return None, None
    return stored_contexts.get("primary"), stored_contexts.get("secondary")


def client_context_name(client: Optional[KubeClient]) -> Optional[str]:
    context = getattr(client, "context", None)
    return context if isinstance(context, str) and context else None
