"""Parity contract: Ansible and Python ACM_KINDS / ACM_NAMESPACES must match."""


def test_acm_kinds_parity():
    """Ansible and Python ACM_KINDS must contain the same entries."""
    from lib.argocd import ARGOCD_ACM_KINDS
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.argocd import ACM_KINDS

    python_kinds = set(ARGOCD_ACM_KINDS)
    ansible_kinds = set(ACM_KINDS)
    missing_in_ansible = python_kinds - ansible_kinds
    extra_in_ansible = ansible_kinds - python_kinds
    assert not missing_in_ansible, f"Ansible ACM_KINDS missing: {missing_in_ansible}"
    assert not extra_in_ansible, f"Ansible ACM_KINDS has extras: {extra_in_ansible}"


def test_acm_namespaces_parity():
    """Ansible and Python ACM namespaces must cover the same set."""
    from lib.argocd import ARGOCD_ACM_NS_REGEX
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.argocd import ACM_NAMESPACES

    for ns in ACM_NAMESPACES:
        assert ARGOCD_ACM_NS_REGEX.match(ns), f"Ansible namespace '{ns}' not matched by Python regex"

    sub_ns_samples = [
        "open-cluster-management-agent",
        "open-cluster-management-agent-addon",
    ]
    for ns in sub_ns_samples:
        assert ARGOCD_ACM_NS_REGEX.match(ns), f"Python regex should match ACM sub-namespace '{ns}'"
