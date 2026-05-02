from ansible_collections.tomazb.acm_switchover.plugins.module_utils.gitops import detect_gitops_markers


def test_detect_gitops_markers_flags_argocd_instance():
    markers = detect_gitops_markers({"labels": {"argocd.argoproj.io/instance": "acm"}})
    assert "label:argocd.argoproj.io/instance" in markers


def test_detect_gitops_markers_marks_generic_instance_unreliable():
    markers = detect_gitops_markers({"labels": {"app.kubernetes.io/instance": "something"}})
    assert "label:app.kubernetes.io/instance (UNRELIABLE)" in markers
