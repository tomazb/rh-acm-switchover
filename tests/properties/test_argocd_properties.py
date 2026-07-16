"""Property-based tests for Argo CD Application safety boundaries."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ansible_collections.tomazb.acm_switchover.plugins.module_utils import argocd as collection_argocd
from ansible_collections.tomazb.acm_switchover.plugins.module_utils import gitops as collection_gitops
from lib import argocd as python_argocd
from lib import gitops_detector as python_gitops
from tests.properties.strategies import (
    ArgocdApplicationCase,
    ArgocdResumeCase,
    argocd_application_cases,
    argocd_application_lists,
    argocd_resume_cases,
    argocd_run_ids,
    argocd_sync_policies,
    gitops_marker_metadata,
)

pytestmark = [pytest.mark.unit, pytest.mark.property]


class RecordingArgocdClient:
    """In-memory client that records every Application identity it touches."""

    def __init__(self, current_app: dict[str, Any]) -> None:
        self.dry_run = False
        self.current_app = deepcopy(current_app)
        self.reads: list[dict[str, Any]] = []
        self.patches: list[dict[str, Any]] = []

    def get_custom_resource(self, **kwargs: Any) -> dict[str, Any]:
        self.reads.append(deepcopy(kwargs))
        return deepcopy(self.current_app)

    def patch_custom_resource(self, **kwargs: Any) -> dict[str, Any]:
        self.patches.append(deepcopy(kwargs))
        patch = kwargs["patch"]
        annotations = patch.get("metadata", {}).get("annotations", {})
        current_annotations = self.current_app.setdefault("metadata", {}).setdefault("annotations", {})
        for key, value in annotations.items():
            if value is None:
                current_annotations.pop(key, None)
            else:
                current_annotations[key] = value
        if "syncPolicy" in patch.get("spec", {}):
            self.current_app.setdefault("spec", {})["syncPolicy"] = deepcopy(patch["spec"]["syncPolicy"])
        return deepcopy(self.current_app)


def _identity(app: dict[str, Any]) -> tuple[str, str]:
    metadata = app["metadata"]
    return metadata["namespace"], metadata["name"]


def _expected_blocker(case: ArgocdApplicationCase) -> str | None:
    if case.applicationset_owned and case.acm_resource_count > 0:
        return python_argocd.PAUSE_BLOCK_REASON_APPLICATIONSET_MANAGED
    if case.autosync_enabled and case.resource_state in {"missing", "empty", "stale"}:
        return python_argocd.PAUSE_BLOCK_REASON_UNKNOWN_ACM_IMPACT
    return None


@given(argocd_application_cases())
def test_autosync_detection_agrees(case: ArgocdApplicationCase) -> None:
    assert python_argocd.is_autosync_enabled(case.app) is case.autosync_enabled
    assert collection_argocd.is_autosync_enabled(case.app) is case.autosync_enabled


@given(
    st.one_of(
        argocd_application_cases(autosync_mode="missing", applicationset_owned=False),
        argocd_application_cases(autosync_mode="null", applicationset_owned=False),
    ),
    argocd_run_ids(),
)
def test_applications_without_automated_sync_are_not_pause_targets(
    case: ArgocdApplicationCase,
    run_id: str,
) -> None:
    client = RecordingArgocdClient(case.app)

    result = python_argocd.pause_autosync(client, case.app, run_id)

    assert result.patched is False
    assert result.skip_reason == python_argocd.PAUSE_SKIP_REASON_AUTOSYNC_DISABLED
    assert client.patches == []
    assert client.reads == []


@given(argocd_application_lists())
def test_acm_filtering_counts_semantic_resources_without_mutating_inputs(
    cases: list[ArgocdApplicationCase],
) -> None:
    apps = [case.app for case in cases]
    original = deepcopy(apps)
    expected = [case for case in cases if case.acm_resource_count > 0]

    python_filtered = python_argocd.find_acm_touching_apps(apps)
    collection_filtered = collection_argocd.filter_acm_applications(apps)

    assert [(_identity(impact.app), impact.resource_count) for impact in python_filtered] == [
        (_identity(case.app), case.acm_resource_count) for case in expected
    ]
    assert [((_item["namespace"], _item["name"]), _item["acm_resource_count"]) for _item in collection_filtered] == [
        (_identity(case.app), case.acm_resource_count) for case in expected
    ]
    assert apps == original
    assert all(output is not case.app for output, case in zip(collection_filtered, expected))
    for output, case in zip(collection_filtered, expected):
        assert {
            key: value for key, value in output.items() if key not in {"namespace", "name", "acm_resource_count"}
        } == case.app


@given(argocd_application_cases(resource_state="current", impact_mode="acm_namespace"))
def test_resources_in_acm_namespaces_are_detected(case: ArgocdApplicationCase) -> None:
    assert case.acm_resource_count >= 1
    assert python_argocd._count_acm_resources(case.app) == case.acm_resource_count
    assert collection_argocd.count_acm_resources(case.app) == case.acm_resource_count


@given(argocd_application_cases(resource_state="current", impact_mode="acm_kind"))
def test_resources_with_acm_kinds_are_detected(case: ArgocdApplicationCase) -> None:
    assert case.acm_resource_count >= 1
    assert python_argocd._count_acm_resources(case.app) == case.acm_resource_count
    assert collection_argocd.count_acm_resources(case.app) == case.acm_resource_count


@given(argocd_application_cases(resource_state="current", impact_mode="unrelated"))
def test_non_acm_resources_are_not_counted(case: ArgocdApplicationCase) -> None:
    assert case.acm_resource_count == 0
    assert python_argocd.find_acm_touching_apps([case.app]) == []
    assert collection_argocd.filter_acm_applications([case.app]) == []


@given(argocd_application_cases(resource_state="current"))
def test_acm_filtering_uses_reported_resources_even_when_observed_generation_is_stale(
    case: ArgocdApplicationCase,
) -> None:
    stale_app = deepcopy(case.app)
    stale_app["status"]["observedGeneration"] = stale_app["metadata"]["generation"] - 1

    current_python = python_argocd.find_acm_touching_apps([case.app])
    stale_python = python_argocd.find_acm_touching_apps([stale_app])
    current_collection = collection_argocd.filter_acm_applications([case.app])
    stale_collection = collection_argocd.filter_acm_applications([stale_app])

    assert [impact.resource_count for impact in stale_python] == [impact.resource_count for impact in current_python]
    assert [item["acm_resource_count"] for item in stale_collection] == [
        item["acm_resource_count"] for item in current_collection
    ]


@given(argocd_application_lists())
def test_pause_blockers_are_complete_fail_closed_and_in_cross_form_agreement(
    cases: list[ArgocdApplicationCase],
) -> None:
    apps = [case.app for case in cases]
    expected = [(_identity(case.app), reason) for case in cases if (reason := _expected_blocker(case))]

    python_blockers = python_argocd.find_argocd_pause_blockers(apps)
    collection_blockers = collection_argocd.find_argocd_pause_blockers(apps)

    assert [((blocker.namespace, blocker.name), blocker.reason) for blocker in python_blockers] == expected
    assert [((blocker["namespace"], blocker["name"]), blocker["reason"]) for blocker in collection_blockers] == expected
    assert len({_identity(case.app) for case in cases}) == len(cases)


@given(
    st.one_of(
        argocd_application_cases(
            autosync_mode="enabled",
            resource_state="missing",
            applicationset_owned=False,
        ),
        argocd_application_cases(
            autosync_mode="enabled",
            resource_state="empty",
            applicationset_owned=False,
        ),
        argocd_application_cases(
            autosync_mode="enabled",
            resource_state="stale",
            applicationset_owned=False,
        ),
    )
)
def test_missing_empty_or_stale_resources_fail_closed(case: ArgocdApplicationCase) -> None:
    python_blocker = python_argocd.find_argocd_pause_blockers([case.app])
    collection_blocker = collection_argocd.find_argocd_pause_blockers([case.app])

    assert len(python_blocker) == 1
    assert len(collection_blocker) == 1
    assert python_blocker[0].reason == python_argocd.PAUSE_BLOCK_REASON_UNKNOWN_ACM_IMPACT
    assert collection_blocker[0]["reason"] == collection_argocd.PAUSE_BLOCK_REASON_UNKNOWN_ACM_IMPACT


@given(
    st.one_of(
        argocd_application_cases(applicationset_owned=True, resource_state="current", impact_mode="acm_namespace"),
        argocd_application_cases(applicationset_owned=True, resource_state="stale", impact_mode="acm_kind"),
    )
)
def test_applicationset_owned_acm_applications_are_blockers(case: ArgocdApplicationCase) -> None:
    python_blocker = python_argocd.find_argocd_pause_blockers([case.app])
    collection_blocker = collection_argocd.find_argocd_pause_blockers([case.app])

    assert len(python_blocker) == 1
    assert len(collection_blocker) == 1
    assert python_blocker[0].reason == python_argocd.PAUSE_BLOCK_REASON_APPLICATIONSET_MANAGED
    assert collection_blocker[0]["reason"] == collection_argocd.PAUSE_BLOCK_REASON_APPLICATIONSET_MANAGED


@given(argocd_sync_policies(), argocd_run_ids())
def test_collection_pause_patch_disables_automated_sync_and_preserves_input(
    sync_policy: dict[str, Any],
    run_id: str,
) -> None:
    original = deepcopy(sync_policy)

    patch = collection_argocd.build_pause_patch(sync_policy, run_id)
    patched_policy = patch["spec"]["syncPolicy"]

    assert sync_policy == original
    assert patch["metadata"]["annotations"][collection_argocd.ARGOCD_PAUSED_BY_ANNOTATION] == run_id
    assert patched_policy.get("automated") is None
    assert {key: value for key, value in patched_policy.items() if key != "automated"} == {
        key: value for key, value in original.items() if key != "automated"
    }


@given(gitops_marker_metadata())
def test_generic_instance_marker_is_unreliable_in_both_form_factors(metadata: dict[str, Any]) -> None:
    python_markers = python_gitops.detect_gitops_markers(metadata)
    collection_markers = collection_gitops.detect_gitops_markers(metadata)

    assert python_markers == collection_markers
    instance_markers = [marker for marker in python_markers if "app.kubernetes.io/instance" in marker]
    assert len(instance_markers) == 1
    assert instance_markers[0].endswith(" (UNRELIABLE)")


@given(
    restored=st.booleans(),
    skip_reason=st.one_of(
        st.none(),
        st.sampled_from(
            (
                python_argocd.RESUME_SKIP_REASON_MARKER_MISSING,
                python_argocd.RESUME_SKIP_REASON_MARKER_MISMATCH,
                "not found",
                "patch failed",
            )
        ),
    ),
)
def test_resume_noop_classification_is_narrow(restored: bool, skip_reason: str | None) -> None:
    result = python_argocd.ResumeResult(
        namespace="argocd",
        name="app",
        restored=restored,
        skip_reason=skip_reason,
    )
    expected = not restored and skip_reason == python_argocd.RESUME_SKIP_REASON_MARKER_MISSING
    assert python_argocd.is_resume_noop(result) is expected


@given(
    argocd_application_cases(autosync_mode="enabled", applicationset_owned=False),
    argocd_run_ids(),
)
def test_pause_targets_exact_generated_application_identity(case: ArgocdApplicationCase, run_id: str) -> None:
    app = deepcopy(case.app)
    original = deepcopy(app)
    client = RecordingArgocdClient(app)

    result = python_argocd.pause_autosync(client, app, run_id)

    namespace, name = _identity(app)
    assert result.patched is True
    assert app == original
    assert [(call["namespace"], call["name"]) for call in client.patches] == [(namespace, name)]
    assert [(call["namespace"], call["name"]) for call in client.reads] == [(namespace, name)]
    assert client.patches[0]["plural"] == python_argocd.ARGOCD_APP_PLURAL
    patch = client.patches[0]["patch"]
    assert patch["metadata"]["annotations"][python_argocd.ARGOCD_PAUSED_BY_ANNOTATION] == run_id
    assert patch["spec"]["syncPolicy"]["automated"] is None


def _resume(case: ArgocdResumeCase) -> tuple[RecordingArgocdClient, python_argocd.ResumeResult]:
    client = RecordingArgocdClient(case.current_app)
    result = python_argocd.resume_autosync(
        client,
        case.namespace,
        case.name,
        case.original_sync_policy,
        case.run_id,
    )
    return client, result


@given(argocd_resume_cases(marker_mode="matches"))
def test_resume_restores_only_the_exact_application_marked_by_same_run(case: ArgocdResumeCase) -> None:
    client, result = _resume(case)

    assert [(call["namespace"], call["name"]) for call in client.reads] == [(case.namespace, case.name)]
    assert result.restored is True
    assert python_argocd.is_resume_noop(result) is False
    assert [(call["namespace"], call["name"]) for call in client.patches] == [(case.namespace, case.name)]
    patch = client.patches[0]["patch"]
    assert patch["metadata"]["resourceVersion"] == case.current_app["metadata"]["resourceVersion"]
    assert patch["metadata"]["annotations"][python_argocd.ARGOCD_PAUSED_BY_ANNOTATION] is None
    assert patch["spec"]["syncPolicy"] == case.original_sync_policy


@given(argocd_resume_cases(marker_mode="mismatches"))
def test_resume_foreign_marker_is_non_destructive_and_not_a_noop(case: ArgocdResumeCase) -> None:
    original = deepcopy(case.current_app)
    client, result = _resume(case)

    assert [(call["namespace"], call["name"]) for call in client.reads] == [(case.namespace, case.name)]
    assert client.patches == []
    assert client.current_app == original
    assert result.restored is False
    assert result.skip_reason == python_argocd.RESUME_SKIP_REASON_MARKER_MISMATCH
    assert python_argocd.is_resume_noop(result) is False


@given(argocd_resume_cases(marker_mode="missing"))
def test_resume_missing_marker_is_mutation_free_and_a_true_noop(case: ArgocdResumeCase) -> None:
    original = deepcopy(case.current_app)
    client, result = _resume(case)

    assert [(call["namespace"], call["name"]) for call in client.reads] == [(case.namespace, case.name)]
    assert client.patches == []
    assert client.current_app == original
    assert result.restored is False
    assert result.skip_reason == python_argocd.RESUME_SKIP_REASON_MARKER_MISSING
    assert python_argocd.is_resume_noop(result) is True
