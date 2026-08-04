"""Shared builders for the lib/argocd_register.py test modules.

Extracted verbatim when tests/test_argocd_register.py was split into its
store / pause / resume modules; the bodies are unchanged.
"""

import copy
from unittest.mock import Mock

from lib import argocd as argocd_lib
from lib.utils import StateManager


def _make_state_manager(config=None):
    """Create a mock StateManager backed by a real dict for config tracking."""
    state_config = config or {}
    mock = Mock()
    mock._get_config.side_effect = lambda key, default=None: copy.deepcopy(state_config.get(key, default))
    mock._set_config.side_effect = lambda key, value: state_config.__setitem__(key, copy.deepcopy(value))
    mock._config = state_config
    return mock


def _make_app(namespace, name, *, automated=True, resources=None, annotations=None):
    """Build a minimal Argo CD Application dict."""
    sync_policy = {"automated": {}} if automated else {}
    if resources is None:
        resources = [{"kind": "BackupSchedule", "namespace": "open-cluster-management-backup"}]
    return {
        "metadata": {"namespace": namespace, "name": name, "annotations": annotations or {}},
        "spec": {"syncPolicy": sync_policy},
        "status": {"resources": resources},
    }


def _make_impact(app):
    meta = app["metadata"]
    return argocd_lib.AppImpact(
        namespace=meta["namespace"],
        name=meta["name"],
        resource_count=1,
        app=app,
    )


def _discovery_with_crd():
    return argocd_lib.ArgocdDiscoveryResult(
        has_applications_crd=True,
        has_argocds_crd=False,
        install_type="vanilla",
    )


def _discovery_without_crd():
    return argocd_lib.ArgocdDiscoveryResult(
        has_applications_crd=False,
        has_argocds_crd=False,
        install_type="none",
    )


def _make_real_state(tmp_path):
    """Real StateManager backed by a temp state file."""
    return StateManager(str(tmp_path / "switchover-state.json"))


def _make_resume_client(apps_by_key, *, patch_error=None):
    """Fake KubeClient for resume: serves live Applications and records patches."""
    client = Mock()
    client.dry_run = False

    def get_custom_resource(group, version, plural, name, namespace=None):
        return copy.deepcopy(apps_by_key.get((namespace, name)))

    client.get_custom_resource.side_effect = get_custom_resource
    if patch_error is not None:
        client.patch_custom_resource.side_effect = patch_error
    return client


def _live_paused_app(namespace, name, run_id):
    return {
        "metadata": {
            "namespace": namespace,
            "name": name,
            "annotations": {argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: run_id},
            "resourceVersion": "10",
        },
        "spec": {"syncPolicy": {}},
    }


def _register_entry(hub, name, *, pause_applied=True):
    return {
        "hub": hub,
        "namespace": "argocd",
        "name": name,
        "original_sync_policy": {"automated": {"prune": True}},
        "pause_applied": pause_applied,
    }
