"""Tests for the named-operation facade over checkpoint dicts (issue #214)."""

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    checkpoint_facts,
    record_resume_start_phase,
)


def test_checkpoint_facts_reads_named_values():
    checkpoint = {
        "operational_data": {
            "argocd_run_id": "run-1",
            "argocd_discovery_namespaces": {"openshift-gitops": ["app1"]},
            "auto_import_strategy_changed": True,
            "expected_managed_cluster_names": ["c1", "c2"],
            "expected_managed_cluster_count": 2,
            "primary_has_observability": True,
            "secondary_has_observability": False,
            "saved_backup_schedule": {"metadata": {"name": "sched"}},
            "backup_schedule_enabled_at": "2026-08-06T00:00:00+00:00",
            "resume_summary": {"resume_start_phase": "activation"},
        }
    }
    facts = checkpoint_facts(checkpoint)
    assert facts["argocd_run_id"] == "run-1"
    assert facts["argocd_discovery_namespaces"] == {"openshift-gitops": ["app1"]}
    assert facts["auto_import_strategy_changed"] is True
    assert facts["expected_managed_cluster_names"] == ["c1", "c2"]
    assert facts["expected_managed_cluster_count"] == 2
    assert facts["primary_has_observability"] is True
    assert facts["secondary_has_observability"] is False
    assert facts["saved_backup_schedule"] == {"metadata": {"name": "sched"}}
    assert facts["backup_schedule_enabled_at"] == "2026-08-06T00:00:00+00:00"
    assert facts["resume_start_phase"] == "activation"


def test_checkpoint_facts_degrades_malformed_shapes_to_defaults():
    malformed: tuple = (None, [], {}, {"operational_data": "bogus"}, {"operational_data": {"resume_summary": "bogus"}})
    for checkpoint in malformed:
        facts = checkpoint_facts(checkpoint)
        assert facts["argocd_run_id"] == ""
        assert facts["argocd_discovery_namespaces"] == {}
        assert facts["auto_import_strategy_changed"] is False
        assert facts["expected_managed_cluster_names"] is None
        assert facts["expected_managed_cluster_count"] is None
        assert facts["primary_has_observability"] is None
        assert facts["secondary_has_observability"] is None
        assert facts["saved_backup_schedule"] is None
        assert facts["backup_schedule_enabled_at"] == ""
        assert facts["resume_start_phase"] == ""


def test_auto_import_flag_coerces_ansible_bool_vocabulary_only():
    """The ansible-core floor (2.15-2.18, jinja2_native off) stringifies folded
    scalars, so checkpoints written there may carry "True"/"true" where 2.19+
    writes a native bool — those must keep discharging the legacy branch.
    Anything outside Ansible's boolean vocabulary (hand-edited, corrupt) must
    degrade to False, never coerce truthy: the flag feeds finalization's legacy
    discharge branch, which deletes the auto-import ConfigMap."""
    for truthy in (True, "true", "True", "TRUE", "yes", "on", "1"):
        facts = checkpoint_facts({"operational_data": {"auto_import_strategy_changed": truthy}})
        assert facts["auto_import_strategy_changed"] is True, repr(truthy)
    for falsy in (False, "false", "False", "no", "off", "0", "banana", 1, 2, [True], {"v": True}, None):
        facts = checkpoint_facts({"operational_data": {"auto_import_strategy_changed": falsy}})
        assert facts["auto_import_strategy_changed"] is False, repr(falsy)


def test_record_resume_start_phase_replaces_whole_summary():
    """Convergence on Python RunRecord.record_resume_start_phase: replace, not fill-if-unset."""
    checkpoint = {"operational_data": {"resume_summary": {"resume_start_phase": "preflight", "extra": "stale"}}}
    record_resume_start_phase(checkpoint, "activation")
    assert checkpoint["operational_data"]["resume_summary"] == {"resume_start_phase": "activation"}


def test_record_resume_start_phase_creates_operational_data():
    checkpoint: dict = {}
    record_resume_start_phase(checkpoint, "post_activation")
    assert checkpoint["operational_data"]["resume_summary"] == {"resume_start_phase": "post_activation"}


def test_auto_import_marker_constants():
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
        AUTO_IMPORT_MARKER_ANNOTATION,
        AUTO_IMPORT_MARKER_VALUE,
    )

    assert AUTO_IMPORT_MARKER_ANNOTATION == "acm-switchover.open-cluster-management.io/import-strategy-set-by"
    assert AUTO_IMPORT_MARKER_VALUE == "acm-switchover"


def test_floor_stringified_scalars_coerce_to_native_types():
    """ansible-core 2.15-2.18 (jinja2_native off) stringifies folded set_fact
    scalars before they reach checkpoint_phase — verified by a live 2.18
    round-trip: expected_managed_cluster_count lands as the string "2". Those
    checkpoints must still resume: digit strings coerce to int, Ansible bool
    vocabulary coerces to bool (external review, PR #224)."""
    facts = checkpoint_facts(
        {
            "operational_data": {
                "expected_managed_cluster_names": ["c1", "c2"],
                "expected_managed_cluster_count": "2",
                "primary_has_observability": "True",
                "secondary_has_observability": "false",
                "auto_import_strategy_changed": "True",
            }
        }
    )
    assert facts["expected_managed_cluster_names"] == ["c1", "c2"]
    assert facts["expected_managed_cluster_count"] == 2
    assert facts["primary_has_observability"] is True
    assert facts["secondary_has_observability"] is False
    assert facts["auto_import_strategy_changed"] is True


def test_wrong_typed_expectation_and_observability_values_degrade_to_none():
    """Values outside the coercible vocabulary read as never-recorded (None)
    so the roles' `is not none` guards skip them (external review, PR #224)."""
    facts = checkpoint_facts(
        {
            "operational_data": {
                "expected_managed_cluster_names": "not-a-list",
                "expected_managed_cluster_count": "not-a-number",
                "primary_has_observability": "maybe",
                "secondary_has_observability": 1,
            }
        }
    )
    assert facts["expected_managed_cluster_names"] is None
    assert facts["expected_managed_cluster_count"] is None
    assert facts["primary_has_observability"] is None
    assert facts["secondary_has_observability"] is None
    facts = checkpoint_facts(
        {
            "operational_data": {
                "expected_managed_cluster_names": ["c1"],
                "expected_managed_cluster_count": 1,
                "primary_has_observability": True,
                "secondary_has_observability": False,
            }
        }
    )
    assert facts["expected_managed_cluster_names"] == ["c1"]
    assert facts["expected_managed_cluster_count"] == 1
    assert facts["primary_has_observability"] is True
    assert facts["secondary_has_observability"] is False
    assert (
        checkpoint_facts({"operational_data": {"expected_managed_cluster_count": True}})[
            "expected_managed_cluster_count"
        ]
        is None
    )
