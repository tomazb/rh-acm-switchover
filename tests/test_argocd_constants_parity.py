"""Parity contract: Ansible and Python ACM_KINDS / ACM_NAMESPACES must match."""

import pathlib


def test_acm_kinds_parity():
    """Ansible and Python ACM_KINDS must contain the same entries."""
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.argocd import (
        ACM_KINDS,
    )
    from lib.argocd import ARGOCD_ACM_KINDS

    python_kinds = set(ARGOCD_ACM_KINDS)
    ansible_kinds = set(ACM_KINDS)
    missing_in_ansible = python_kinds - ansible_kinds
    extra_in_ansible = ansible_kinds - python_kinds
    assert not missing_in_ansible, f"Ansible ACM_KINDS missing: {missing_in_ansible}"
    assert not extra_in_ansible, f"Ansible ACM_KINDS has extras: {extra_in_ansible}"


def test_acm_namespaces_parity():
    """Ansible and Python ACM namespaces must cover the same set."""
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.argocd import (
        ACM_NAMESPACES,
    )
    from lib.argocd import ARGOCD_ACM_NS_REGEX

    for ns in ACM_NAMESPACES:
        assert ARGOCD_ACM_NS_REGEX.match(ns), f"Ansible namespace '{ns}' not matched by Python regex"

    sub_ns_samples = [
        "open-cluster-management-agent",
        "open-cluster-management-agent-addon",
    ]
    for ns in sub_ns_samples:
        assert ARGOCD_ACM_NS_REGEX.match(ns), f"Python regex should match ACM sub-namespace '{ns}'"


def test_ansible_argocd_filters_match_acm_sub_namespaces():
    """Ansible Argo CD filtering should match the same ACM sub-namespaces as Python/Bash."""
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.argocd import (
        is_acm_touching_application,
    )

    app = {
        "status": {
            "resources": [
                {"namespace": "open-cluster-management-agent", "kind": "ConfigMap"},
            ]
        }
    }

    assert is_acm_touching_application(app) is True


_ROLE_TASKS = pathlib.Path("ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks")


def test_paused_by_annotation_identical_across_all_definitions():
    """Triple-defined constant (audit M1): drift silently orphans every paused Application."""
    import lib.argocd
    import lib.constants
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils import constants as ans_constants

    assert (
        lib.argocd.ARGOCD_PAUSED_BY_ANNOTATION
        == lib.constants.ARGOCD_PAUSED_BY_ANNOTATION
        == ans_constants.ARGOCD_PAUSED_BY_ANNOTATION
    )


def test_shipped_task_yaml_uses_the_shared_annotation_keys():
    """Guard the artifact that ships (audit M3): the inline YAML patch in the role
    task files, not a helper nothing calls. Every acm-switchover annotation literal
    in pause.yml/resume.yml must be one of the shared constants."""
    import re

    from ansible_collections.tomazb.acm_switchover.plugins.module_utils import constants as ans_constants

    pause_text = (_ROLE_TASKS / "pause.yml").read_text()
    resume_text = (_ROLE_TASKS / "resume.yml").read_text()

    for text, name in ((pause_text, "pause.yml"), (resume_text, "resume.yml")):
        assert ans_constants.ARGOCD_PAUSED_BY_ANNOTATION in text, f"{name} must use the paused-by key"
        assert ans_constants.ARGOCD_ORIGINAL_SYNC_POLICY_ANNOTATION in text, f"{name} must use the policy key"
        keys = set(re.findall(r"acm-switchover\.argoproj\.io/[a-z-]+", text))
        assert keys <= {
            ans_constants.ARGOCD_PAUSED_BY_ANNOTATION,
            ans_constants.ARGOCD_ORIGINAL_SYNC_POLICY_ANNOTATION,
        }, f"{name} uses unexpected annotation keys: {keys}"


def test_pause_patch_shape_matches_python():
    """Python pause sets syncPolicy.automated=None so merge-patch deletes the key
    (lib/argocd.py). The shipped pause.yml must do the same."""
    pause_text = (_ROLE_TASKS / "pause.yml").read_text()
    assert "combine({'automated': none})" in pause_text


def test_resume_never_defaults_missing_policy():
    """Cross-runtime data-loss guard (audit C1 / issue #184): a missing
    original-sync-policy annotation must never be defaulted to an empty policy."""
    resume_text = (_ROLE_TASKS / "resume.yml").read_text()
    assert "default('{}')" not in resume_text
