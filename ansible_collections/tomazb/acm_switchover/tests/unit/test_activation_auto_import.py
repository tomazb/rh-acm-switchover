"""Tests for activation role auto-import strategy management."""

import pathlib

import yaml

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
ACTIVATION_TASKS = ROLES_DIR / "activation" / "tasks"
FINALIZATION_TASKS = ROLES_DIR / "finalization" / "tasks"
CONSTANTS_FILE = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "module_utils" / "constants.py"
AUTO_IMPORT_SUPPORT_TASK = ACTIVATION_TASKS / "resolve_auto_import_support.yml"


def test_manage_auto_import_file_exists():
    """manage_auto_import.yml must exist in activation tasks."""
    assert (ACTIVATION_TASKS / "manage_auto_import.yml").exists()


def test_reset_auto_import_in_finalization():
    """reset_auto_import.yml must exist in finalization tasks (not activation)."""
    assert (FINALIZATION_TASKS / "reset_auto_import.yml").exists(), (
        "reset_auto_import.yml must live in finalization — the reset must happen "
        "after the post_activation cleanup_auto_import_annotations window closes"
    )


def test_main_includes_manage_after_passive_verification_before_activation():
    """activation/main.yml must verify passive sync before temporary ImportAndSync management."""
    main = yaml.safe_load((ACTIVATION_TASKS / "main.yml").read_text())
    block_tasks = None
    for item in main:
        if "block" in item:
            block_tasks = item["block"]
            break
    assert block_tasks is not None, "main.yml must have a block"

    includes = [t.get("ansible.builtin.include_tasks", "") for t in block_tasks]

    manage_idx = None
    verify_idx = None
    activate_idx = None
    reset_idx = None
    for i, inc in enumerate(includes):
        if inc == "manage_auto_import.yml":
            manage_idx = i
        elif inc == "verify_passive_sync.yml":
            verify_idx = i
        elif inc == "activate_restore.yml":
            activate_idx = i
        elif inc == "reset_auto_import.yml":
            reset_idx = i

    assert manage_idx is not None, "manage_auto_import.yml must be included in activation"
    assert verify_idx is not None, "verify_passive_sync.yml must be included in activation"
    assert verify_idx < manage_idx, "passive sync must be verified before ImportAndSync management"
    assert manage_idx < activate_idx, "manage_auto_import must come before activate_restore"
    assert reset_idx is None, (
        "reset_auto_import.yml must NOT be in activation/tasks/main.yml — "
        "it belongs in finalization to match Python CLI timing"
    )


def test_finalization_includes_reset_after_discover():
    """finalization/main.yml must include reset_auto_import after discover_resources."""
    main = yaml.safe_load((FINALIZATION_TASKS / "main.yml").read_text())
    block_tasks = None
    for item in main:
        if "block" in item:
            block_tasks = item["block"]
            break
    assert block_tasks is not None, "finalization/main.yml must have a block"

    includes = [t.get("ansible.builtin.include_tasks", "") for t in block_tasks]

    discover_idx = None
    reset_idx = None
    for i, inc in enumerate(includes):
        if inc == "discover_resources.yml":
            discover_idx = i
        elif inc == "reset_auto_import.yml":
            reset_idx = i

    assert reset_idx is not None, "reset_auto_import.yml must be included in finalization"
    assert discover_idx is not None, "discover_resources.yml must be in finalization"
    assert reset_idx > discover_idx, "reset_auto_import must come after discover_resources"


def test_activation_persists_auto_import_reset_flag_in_checkpoint():
    """activation/main.yml must persist auto-import reset intent for resumed finalization."""
    text = (ACTIVATION_TASKS / "main.yml").read_text()
    assert "operational_data:" in text
    assert "auto_import_strategy_changed" in text, (
        "activation/main.yml must write auto_import_strategy_changed into checkpoint operational_data "
        "so finalization can still reset ImportAndSync after a resumed run"
    )


def test_activation_result_defaults_unknown_changed_to_false():
    """Activation result should not report changed=true when no mutation result exists."""
    text = (ACTIVATION_TASKS / "main.yml").read_text()

    assert "acm_switchover_restore_activation_result.changed | default(false)" in text
    assert "acm_switchover_restore_activation_result.changed | default(true)" not in text


def test_finalization_restores_auto_import_reset_flag_from_checkpoint():
    """finalization/main.yml must rehydrate auto-import reset intent before reset runs."""
    text = (FINALIZATION_TASKS / "main.yml").read_text()
    assert "(_checkpoint_enter | default({})).get('facts', {})" in text
    assert "auto_import_strategy_changed" in text, (
        "finalization/main.yml must restore auto_import_strategy_changed from checkpoint facts "
        "before including reset_auto_import.yml"
    )


def test_apply_immediate_import_is_not_a_stub():
    """apply_immediate_import.yml must contain real k8s tasks, not just set_fact."""
    content = (ACTIVATION_TASKS / "apply_immediate_import.yml").read_text()
    assert "kubernetes.core.k8s_info" in content, "Must query k8s for import config"
    assert "kubernetes.core.k8s" in content, "Must patch ManagedClusters"
    assert "local-cluster" in content, "Must filter out local-cluster"


def test_apply_immediate_import_does_not_swallow_patch_failures():
    """activation must fail if immediate-import annotations cannot be applied."""
    content = (ACTIVATION_TASKS / "apply_immediate_import.yml").read_text()
    assert (
        "ignore_errors: true" not in content
    ), "apply_immediate_import.yml must not ignore ManagedCluster patch failures"


def test_manage_auto_import_preserves_python_guards_and_detect_only_mode():
    """Activation auto-import management must mirror Python _maybe_set_auto_import_strategy()."""
    content = (ACTIVATION_TASKS / "manage_auto_import.yml").read_text()
    support_content = AUTO_IMPORT_SUPPORT_TASK.read_text()

    assert "acm_secondary_version" in support_content
    assert "version('2.14.0', '>=')" in support_content
    assert "_acm_secondary_supports_auto_import" in content
    assert "old_hub_action" in content
    assert "local-cluster" in content
    assert "rejectattr('metadata.name', 'equalto', 'local-cluster')" in content
    assert "manage_auto_import_strategy" in content
    assert "Detect-only" in content
    assert "ImportAndSync" in content


def test_auto_import_support_handles_missing_mch_discovery_fact():
    """The support resolver should fail predictably instead of raising UndefinedError."""
    support_content = AUTO_IMPORT_SUPPORT_TASK.read_text()

    assert "(acm_activation_mch_info | default({}, true)).resources" in support_content


def test_manage_auto_import_initializes_strategy_for_dry_run_paths():
    """Dry-run skips live ConfigMap discovery, but later when clauses still need a strategy value."""
    tasks = yaml.safe_load((ACTIVATION_TASKS / "manage_auto_import.yml").read_text())
    init_task = next(task for task in tasks if task.get("name") == "Initialize auto-import strategy management state")

    assert init_task["ansible.builtin.set_fact"]["_auto_import_current_strategy"] == "default"


def test_manage_auto_import_creates_missing_configmap_like_python():
    """Default strategy is represented by an absent ConfigMap, so manage mode must create it."""
    tasks = yaml.safe_load((ACTIVATION_TASKS / "manage_auto_import.yml").read_text())
    strategy_task = next(task for task in tasks if task.get("name") == "Set autoImportStrategy to ImportAndSync")
    module_args = strategy_task["kubernetes.core.k8s"]

    assert module_args["state"] == "present"
    assert module_args["definition"]["metadata"]["name"] == "import-controller-config"
    assert module_args["definition"]["metadata"]["namespace"] == "multicluster-engine"
    assert module_args["definition"]["data"]["autoImportStrategy"] == "ImportAndSync"


def test_manage_auto_import_omits_missing_secondary_context():
    """Secondary hub context is optional and should be omitted when unset."""
    tasks = yaml.safe_load((ACTIVATION_TASKS / "manage_auto_import.yml").read_text())
    kube_tasks = [
        task
        for task in tasks
        if task.get("kubernetes.core.k8s_info", {}).get("kubeconfig")
        == "{{ acm_switchover_hubs.secondary.kubeconfig }}"
        or task.get("kubernetes.core.k8s", {}).get("kubeconfig") == "{{ acm_switchover_hubs.secondary.kubeconfig }}"
    ]

    assert kube_tasks
    for task in kube_tasks:
        module_args = task.get("kubernetes.core.k8s_info", {}) or task.get("kubernetes.core.k8s", {})
        assert module_args["context"] == "{{ acm_switchover_hubs.secondary.context | default(omit) }}"


def test_apply_immediate_import_requires_acm_214_or_newer():
    """Immediate-import annotations are an ACM 2.14+ behavior and must be version-gated."""
    content = (ACTIVATION_TASKS / "apply_immediate_import.yml").read_text()
    support_content = AUTO_IMPORT_SUPPORT_TASK.read_text()

    assert "acm_secondary_version" in support_content
    assert "version('2.14.0', '>=')" in support_content
    assert "_acm_secondary_supports_auto_import" in content


def test_apply_immediate_import_fails_closed_before_managed_cluster_work():
    """Only explicit absence or valid ConfigMap evidence may reach ManagedClusters."""
    tasks = yaml.safe_load((ACTIVATION_TASKS / "apply_immediate_import.yml").read_text())
    config_task = next(task for task in tasks if task.get("name") == "Get current autoImportStrategy")
    content = (ACTIVATION_TASKS / "apply_immediate_import.yml").read_text()

    module_args = config_task["tomazb.acm_switchover.acm_k8s_read_outcome"]
    assert module_args["read_mode"] == "get"
    assert module_args["api_version"] == "v1"
    assert module_args["kind"] == "ConfigMap"
    assert module_args["name"] == "import-controller-config"
    assert module_args["namespace"] == "multicluster-engine"
    assert module_args["kubeconfig"] == ("{{ acm_switchover_hubs.secondary.kubeconfig }}")
    assert module_args["context"] == "{{ acm_switchover_hubs.secondary.context }}"
    assert config_task.get("no_log") is True

    evidence_task = next(
        task for task in tasks if "_import_cm_for_annotation_valid" in task.get("ansible.builtin.set_fact", {})
    )
    evidence = str(evidence_task["ansible.builtin.set_fact"]["_import_cm_for_annotation_valid"])
    assert "read_status == 'ok'" in evidence
    assert "resources is defined" in evidence
    assert "resources | type_debug" in evidence
    assert "resources | length) == 1" in evidence
    assert "resources[0] is mapping" in evidence
    assert ".apiVersion" in evidence and "'v1'" in evidence
    assert ".kind" in evidence and "'ConfigMap'" in evidence
    assert ".metadata.name" in evidence and "'import-controller-config'" in evidence
    assert ".metadata.namespace" in evidence and "'multicluster-engine'" in evidence
    assert ".data is not defined" in evidence
    assert ".data == none" in evidence
    assert ".data is mapping" in evidence

    failure_task = next(
        task
        for task in tasks
        if "Unable to verify autoImportStrategy on the destination hub" in str(task.get("ansible.builtin.fail", {}))
    )
    failure_when = str(failure_task.get("when", ""))
    assert "read_status is not defined" in failure_when
    assert "read_status != 'not_found'" in failure_when
    assert "_import_cm_for_annotation_valid" in failure_when

    decision_task = next(
        task for task in tasks if task.get("name") == "Determine if immediate-import annotations are needed"
    )
    decision = str(decision_task["ansible.builtin.set_fact"]["_apply_immediate_import"])
    assert "read_status == 'not_found'" in decision
    assert "autoImportStrategy" in decision

    managed_cluster_index = next(
        index
        for index, task in enumerate(tasks)
        if task.get("kubernetes.core.k8s_info", {}).get("kind") == "ManagedCluster"
    )
    assert tasks.index(failure_task) < managed_cluster_index
    assert "autoImportStrategy_unavailable" not in content
    assert "resources is not defined" not in decision
    assert "failed_when: false" not in str(config_task)
    assert "reason: dry_run" in content
    assert "does not support immediate-import annotations" in content


def test_apply_immediate_import_retriggers_non_empty_annotations_like_python():
    """Python removes non-empty immediate-import markers before setting the empty trigger."""
    tasks = yaml.safe_load((ACTIVATION_TASKS / "apply_immediate_import.yml").read_text())
    patch_tasks = [task for task in tasks if task.get("kubernetes.core.k8s", {}).get("kind") == "ManagedCluster"]

    null_tasks = [
        task
        for task in patch_tasks
        if task["kubernetes.core.k8s"]["definition"]["metadata"]["annotations"].get(
            "import.open-cluster-management.io/immediate-import"
        )
        is None
    ]
    empty_tasks = [
        task
        for task in patch_tasks
        if task["kubernetes.core.k8s"]["definition"]["metadata"]["annotations"].get(
            "import.open-cluster-management.io/immediate-import"
        )
        == ""
    ]

    assert null_tasks, "apply_immediate_import.yml must clear stale non-empty markers first"
    assert empty_tasks, "apply_immediate_import.yml must then set the empty immediate-import trigger"
    assert tasks.index(null_tasks[0]) < tasks.index(empty_tasks[0])


def test_constants_include_auto_import():
    """Ansible constants must include auto-import strategy constants."""
    content = CONSTANTS_FILE.read_text()
    assert "IMPORT_CONTROLLER_CONFIG_CM" in content
    assert "AUTO_IMPORT_STRATEGY_KEY" in content
    assert "AUTO_IMPORT_STRATEGY_DEFAULT" in content
    assert "AUTO_IMPORT_STRATEGY_SYNC" in content
    assert "IMMEDIATE_IMPORT_ANNOTATION" in content
    assert "LOCAL_CLUSTER_NAME" in content


def test_import_and_sync_patch_carries_ownership_marker():
    """Issue #214 / audit C3: the obligation marker must ride the mutation itself,
    so an interruption can never separate the change from its evidence."""
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
        AUTO_IMPORT_MARKER_ANNOTATION,
        AUTO_IMPORT_MARKER_VALUE,
    )

    tasks = yaml.safe_load((ACTIVATION_TASKS / "manage_auto_import.yml").read_text())
    patch_task = next(t for t in tasks if t.get("name") == "Set autoImportStrategy to ImportAndSync")
    definition = patch_task["kubernetes.core.k8s"]["definition"]
    annotations = definition["metadata"].get("annotations", {})
    assert annotations.get(AUTO_IMPORT_MARKER_ANNOTATION) == AUTO_IMPORT_MARKER_VALUE
    assert definition["data"]["autoImportStrategy"] == "ImportAndSync"


def _load_reset_tasks():
    return yaml.safe_load((FINALIZATION_TASKS / "reset_auto_import.yml").read_text())


def _when_text(task):
    when = task.get("when", "")
    if isinstance(when, list):
        return " ".join(str(w) for w in when)
    return str(when)


def test_reset_auto_import_reads_marker_via_constants():
    """Issue #214 / audit C3: the discharge reader in reset_auto_import.yml must
    observe the same marker annotation/value that activation's ImportAndSync patch
    pins (see test_import_and_sync_patch_carries_ownership_marker above). A rename
    of either constant must fail this test rather than silently degrading discharge
    to the legacy-only fallback with no red signal."""
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
        AUTO_IMPORT_MARKER_ANNOTATION,
        AUTO_IMPORT_MARKER_VALUE,
    )

    text = (FINALIZATION_TASKS / "reset_auto_import.yml").read_text()
    assert AUTO_IMPORT_MARKER_ANNOTATION in text
    assert AUTO_IMPORT_MARKER_VALUE in text


def test_reset_read_is_not_gated_on_legacy_flag():
    """The CM read must always run in execute mode: marker observation is the
    primary discharge signal and cannot depend on in-memory state (audit C3)."""
    tasks = _load_reset_tasks()
    read_task = next(t for t in tasks if t.get("name") == "Read import-controller-config before reset")
    when = _when_text(read_task)
    assert "_auto_import_strategy_changed" not in when
    assert "!= 'dry_run'" in when


def test_reset_delete_discharges_on_marker_or_legacy_signal():
    tasks = _load_reset_tasks()
    delete_task = next(
        t for t in tasks if t.get("name") == "Delete import-controller-config to restore default autoImportStrategy"
    )
    when = _when_text(delete_task)
    assert "_auto_import_marker_present" in when
    assert "_auto_import_strategy_changed" in when
    assert "ImportAndSync" in when


def test_finalization_always_includes_reset_auto_import():
    """The include must not be fenced by feature flag or legacy fact — the
    observation inside reset_auto_import.yml decides (audit C3 orphan discharge)."""
    main = yaml.safe_load((FINALIZATION_TASKS / "main.yml").read_text())

    def _find_include(tasks):
        for task in tasks or []:
            if task.get("ansible.builtin.include_tasks") == "reset_auto_import.yml":
                return task
            for key in ("block", "rescue", "always"):
                if key in task:
                    found = _find_include(task[key])
                    if found:
                        return found
        return None

    include_task = _find_include(main)
    assert include_task is not None
    assert "when" not in include_task, "reset_auto_import include must be unconditional; inner tasks gate on mode"


def test_reset_delete_requires_execute_mode():
    """validate mode is documented read-only: the discharge delete must be
    gated on mode == 'execute', not merely != 'dry_run' (external review,
    PR #224)."""
    tasks = _load_reset_tasks()
    delete_task = next(
        t for t in tasks if t.get("name") == "Delete import-controller-config to restore default autoImportStrategy"
    )
    when = _when_text(delete_task)
    assert "== 'execute'" in when
    assert "!= 'dry_run'" not in when
