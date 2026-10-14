"""R4-04 PR C Task 9: the shape of the collection's post-activation completion evidence.

The decisions are held equal to the Python CLI by tests/test_post_activation_transaction_parity.py
and exercised against a fake hub by tests/integration/test_post_activation_evidence_runtime.py;
these structural checks pin the ordering, strict-read and preview rules the YAML owns.
"""

import pathlib

import yaml

TASKS = pathlib.Path(__file__).resolve().parents[2] / "roles" / "post_activation" / "tasks"
EVIDENCE = "tomazb.acm_switchover.acm_migration_evidence"
WRITER = "tomazb.acm_switchover.checkpoint_phase"
STRICT_SEAM = "{{ role_path }}/../activation/tasks/strict_read.yml"


def _load(name):
    return yaml.safe_load((TASKS / name).read_text(encoding="utf-8")) or []


def _flat(tasks):
    for task in tasks:
        yield task
        for section in ("block", "rescue", "always"):
            yield from _flat(task.get(section) or [])


def _phase_block():
    (block,) = [task for task in _load("main.yml") if task.get("name") == "Run post_activation phase"]
    return block


def test_no_post_activation_task_reads_managed_clusters_non_strictly():
    for path in sorted(TASKS.glob("*.yml")):
        for task in _flat(_load(path.name)):
            info = task.get("kubernetes.core.k8s_info")
            if isinstance(info, dict):
                assert info.get("kind") != "ManagedCluster", f"{path.name}: {task.get('name')}"


def test_every_managed_cluster_read_goes_through_the_r4_03_strict_seam():
    for name in ("read_managed_clusters.yml", "poll_managed_cluster_readiness.yml"):
        reads = [task for task in _flat(_load(name)) if task.get("ansible.builtin.include_tasks") == STRICT_SEAM]
        assert len(reads) == 1, name
        assert reads[0]["vars"]["_acm_read"]["kind"] == "ManagedCluster"
    for name in ("discover_resources.yml", "cleanup_auto_import_annotations.yml"):
        includes = [task.get("ansible.builtin.include_tasks") for task in _flat(_load(name))]
        assert "read_managed_clusters.yml" in includes, name


def test_discovery_never_reuses_a_pre_seeded_inventory():
    text = (TASKS / "discover_resources.yml").read_text(encoding="utf-8")
    assert "is not defined" not in text
    assert "kubernetes.core.k8s_info" not in text


def test_the_evidence_inputs_are_proven_before_any_work():
    names = [task.get("name") for task in _phase_block()["block"]]
    resolve = names.index("Resolve the post-activation migration evidence inputs")
    assert resolve < names.index("Discover secondary hub resources")
    tasks = {task.get("name"): task for task in _flat(_load("resolve_migration_evidence.yml"))}
    required = tasks["Require the completed migration journal of an executing run"]
    assert required[EVIDENCE]["operation"] == "require_completed_restore"
    assert required["when"] == "not (_acm_preview | bool)"
    waiver = tasks["Resolve the waiver covering the post-activation name check"]
    assert waiver[EVIDENCE]["operation"] == "post_activation_waiver"


def test_name_evidence_follows_connectivity_and_completion_is_the_last_evidence():
    block = _phase_block()["block"]
    names = [task.get("name") for task in block]
    names_evidence = names.index("Record the post-activation ManagedCluster name evidence")
    completion = names.index("Record post-activation completion last")
    assert names.index("Re-verify managed clusters after klusterlet remediation") < names_evidence
    assert names_evidence < names.index("Clean stale disable-auto-import annotations")
    for later in ("Verify observability", "Fail if post activation checks did not pass"):
        assert names.index(later) < completion
    assert completion == len(block) - 1
    assert "_acm_mc_readiness.ready | bool" in block[names_evidence]["when"]
    for index in (names_evidence, completion):
        assert "not (_acm_preview | bool)" in block[index]["when"]
        operations = [task[EVIDENCE]["operation"] for task in block[index]["block"] if EVIDENCE in task]
        writes = [task[WRITER] for task in block[index]["block"] if WRITER in task]
        assert operations == [
            "post_activation_names_evidence" if index == names_evidence else "post_activation_completion"
        ]
        assert [(write["phase"], write["status"]) for write in writes] == [("post_activation", "update")]


def test_both_evidence_writes_run_under_the_phase_failure_rescue():
    phase = _phase_block()
    writers = [task for task in _flat(phase["block"]) if WRITER in task]
    assert [task[WRITER]["status"] for task in writers] == ["update", "update"]
    (fail,) = [task for task in _flat(phase["rescue"]) if WRITER in task]
    assert fail[WRITER]["status"] == "fail" and "argocd_repause" in fail[WRITER]


def test_w1_each_poll_guards_itself_at_execution_time_and_keeps_the_accepted_decision():
    (poll,) = _load("poll_managed_cluster_readiness.yml")
    assert poll["when"] == "_acm_mc_readiness_state != 'ready'"
    decide = next(task for task in poll["block"] if EVIDENCE in task)
    record = poll["block"][-1]["ansible.builtin.set_fact"]
    assert decide["register"] != "_acm_mc_readiness"
    assert record["_acm_mc_readiness"] == "{{ %s }}" % decide["register"]


def test_b3_klusterlet_verification_sees_the_accepted_poll_inventory():
    tasks = _load("verify_managed_clusters.yml")
    names = [task.get("name") for task in tasks]
    publish = names.index("Publish the accepted ManagedCluster inventory for klusterlet verification")
    assert names.index("Wait for managed clusters to become available and joined") < publish
    assert tasks[publish]["ansible.builtin.set_fact"] == {
        "acm_secondary_managed_clusters_info": "{{ _acm_mc_readiness_read }}"
    }
    block = [task.get("name") for task in _phase_block()["block"]]
    assert block.index("Verify managed clusters") < block.index("Verify klusterlet status")


def test_b2_a_completed_marker_is_honoured_only_with_its_evidence():
    tasks = _load("main.yml")
    names = [task.get("name") for task in tasks]
    require = tasks[names.index("Require the evidence a completed post_activation marker claims")]
    assert require[EVIDENCE]["operation"] == "require_post_activation_completed"
    assert "(_checkpoint_enter | default({})).skipped_phase | default(false)" in require["when"]
    assert "not ansible_check_mode" in require["when"]
    assert names.index("Require the evidence a completed post_activation marker claims") < names.index(
        "Run post_activation phase"
    )


def test_an_invalid_journal_is_refused_in_every_mode_before_the_dry_run_result():
    block = _phase_block()["block"]
    names = [task.get("name") for task in block]
    refusal = names.index("Refuse an invalid recorded migration journal")
    assert names.index("Guard the post_activation role entry") < refusal
    assert refusal < names.index("Publish dry-run post activation result")
    assert "mode" not in str(block[refusal]["when"])
