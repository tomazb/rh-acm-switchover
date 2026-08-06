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
    (lib/argocd.py). The shipped pause.yml must do the same, and must stamp the
    paused-by marker with the strict run_id (no fallback that could write an
    unmatchable marker)."""
    pause_text = (_ROLE_TASKS / "pause.yml").read_text()
    assert "combine({'automated': none})" in pause_text
    assert 'acm-switchover.argoproj.io/paused-by: "{{ acm_switchover_argocd.run_id }}"' in pause_text


def _resume_restore_task():
    import yaml

    tasks = yaml.safe_load((_ROLE_TASKS / "resume.yml").read_text()) or []
    pending = list(tasks)
    while pending:
        task = pending.pop()
        if not isinstance(task, dict):
            continue
        for key in ("block", "rescue", "always"):
            pending.extend(task.get(key, []) or [])
        if "Restore original sync policy" in (task.get("name") or ""):
            return task
    raise AssertionError("resume.yml must contain the 'Restore original sync policy' task")


def test_resume_never_defaults_missing_policy():
    """Cross-runtime data-loss guard (audit C1 / issue #184): resume must read
    the original-sync-policy annotation with no fallback of any kind, and its
    guard must require the annotation to exist with a non-empty, non-'{}'
    value. A default (whether '{}', {}, or anything else) silently patches
    spec.syncPolicy for Python-paused Applications."""
    task = _resume_restore_task()

    sync_policy_template = str(task["kubernetes.core.k8s"]["definition"]["spec"]["syncPolicy"])
    assert "original-sync-policy" in sync_policy_template
    assert "from_json" in sync_policy_template
    assert (
        "default" not in sync_policy_template
    ), f"Restore template must not default the policy annotation: {sync_policy_template}"

    when = task.get("when", [])
    when_text = " ".join(str(w) for w in when) if isinstance(when, list) else str(when)
    assert "'acm-switchover.argoproj.io/original-sync-policy' in item.metadata.annotations" in when_text
    assert "| trim | length) > 0" in when_text
    assert "| trim) != '{}'" in when_text
