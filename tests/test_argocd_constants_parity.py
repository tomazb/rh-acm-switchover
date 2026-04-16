"""Parity contract: Ansible and Python ACM_KINDS / ACM_NAMESPACES must match."""


def test_acm_kinds_parity():
    """Ansible and Python ACM_KINDS must contain the same entries."""
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.argocd import ACM_KINDS
    from lib.argocd import ARGOCD_ACM_KINDS

    python_kinds = set(ARGOCD_ACM_KINDS)
    ansible_kinds = set(ACM_KINDS)
    missing_in_ansible = python_kinds - ansible_kinds
    extra_in_ansible = ansible_kinds - python_kinds
    assert not missing_in_ansible, f"Ansible ACM_KINDS missing: {missing_in_ansible}"
    assert not extra_in_ansible, f"Ansible ACM_KINDS has extras: {extra_in_ansible}"


def test_acm_namespaces_parity():
    """Ansible and Python ACM namespaces must cover the same set."""
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.argocd import ACM_NAMESPACES
    from lib.argocd import ARGOCD_ACM_NS_REGEX

    for ns in ACM_NAMESPACES:
        assert ARGOCD_ACM_NS_REGEX.match(ns), f"Ansible namespace '{ns}' not matched by Python regex"

    sub_ns_samples = [
        "open-cluster-management-agent",
        "open-cluster-management-agent-addon",
    ]
    for ns in sub_ns_samples:
        assert ARGOCD_ACM_NS_REGEX.match(ns), f"Python regex should match ACM sub-namespace '{ns}'"


def test_build_pause_patch_matches_jinja_logic():
    """Verify build_pause_patch produces same result as pause.yml Jinja template.

    pause.yml Jinja: dict2items | rejectattr('key','equalto','automated') | items2dict
    build_pause_patch: dict(syncPolicy); syncPolicy.pop('automated', None)
    Both should produce the same sync policy.
    """
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.argocd import build_pause_patch

    test_cases = [
        {"automated": {"prune": True}, "syncOptions": ["CreateNamespace=true"]},
        {"automated": {"selfHeal": True}},
        {"syncOptions": ["CreateNamespace=true"]},
        {},
    ]
    run_id = "test-run-123"

    for sync_policy in test_cases:
        patch = build_pause_patch(sync_policy, run_id)

        # Simulate Jinja: dict2items | reject 'automated' | items2dict
        jinja_sync = {k: v for k, v in sync_policy.items() if k != "automated"}

        assert patch["spec"]["syncPolicy"] == jinja_sync, (
            f"Divergence for input {sync_policy}: "
            f"build_pause_patch={patch['spec']['syncPolicy']}, jinja={jinja_sync}"
        )
        assert patch["metadata"]["annotations"]["acm-switchover.argoproj.io/paused-by"] == run_id
