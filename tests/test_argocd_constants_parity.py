"""Parity contract: Ansible and Python ACM_KINDS / ACM_NAMESPACES must match."""


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
        assert ARGOCD_ACM_NS_REGEX.match(
            ns
        ), f"Ansible namespace '{ns}' not matched by Python regex"

    sub_ns_samples = [
        "open-cluster-management-agent",
        "open-cluster-management-agent-addon",
    ]
    for ns in sub_ns_samples:
        assert ARGOCD_ACM_NS_REGEX.match(
            ns
        ), f"Python regex should match ACM sub-namespace '{ns}'"


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


def test_build_pause_patch_matches_jinja_logic():
    """Verify build_pause_patch produces same result as pause.yml Jinja template.

    pause.yml Jinja and build_pause_patch both keep existing syncPolicy keys
    but set automated to null when automated sync is present so merge patch
    semantics delete the CRD map key.
    """
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.argocd import (
        build_pause_patch,
    )
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
        ARGOCD_PAUSED_BY_ANNOTATION,
    )

    test_cases = [
        {"automated": {"prune": True}, "syncOptions": ["CreateNamespace=true"]},
        {"automated": {"selfHeal": True}},
        {"syncOptions": ["CreateNamespace=true"]},
        {},
    ]
    run_id = "test-run-123"

    for sync_policy in test_cases:
        patch = build_pause_patch(sync_policy, run_id)

        jinja_sync = dict(sync_policy)
        if "automated" in jinja_sync:
            jinja_sync["automated"] = None

        assert patch["spec"]["syncPolicy"] == jinja_sync, (
            f"Divergence for input {sync_policy}: "
            f"build_pause_patch={patch['spec']['syncPolicy']}, jinja={jinja_sync}"
        )
        assert patch["metadata"]["annotations"][ARGOCD_PAUSED_BY_ANNOTATION] == run_id
