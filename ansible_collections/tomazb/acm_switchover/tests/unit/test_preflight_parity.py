"""Static parity tests for preflight role behavior."""

import ast
import pathlib

import pytest
import yaml
from jinja2 import Environment
from preflight_task_text import validate_backups_text

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
PREFLIGHT_TASKS = ROLES_DIR / "preflight" / "tasks"
COLLECTION_ROOT = ROLES_DIR.parent
REPOSITORY_ROOT = COLLECTION_ROOT.parents[2]


def _load_yaml(name: str) -> list[dict]:
    return yaml.safe_load((PREFLIGHT_TASKS / name).read_text())


def _include_task_names(tasks: list[dict]) -> list[str]:
    includes = []
    for task in tasks:
        include = task.get("ansible.builtin.include_tasks")
        if include:
            includes.append(include)
        if "block" in task:
            includes.extend(_include_task_names(task["block"]))
        if "rescue" in task:
            includes.extend(_include_task_names(task["rescue"]))
    return includes


def _function_source(path: pathlib.Path, function_name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == function_name)
    return ast.get_source_segment(source, function) or ""


def _production_python_files() -> list[pathlib.Path]:
    python_roots = [REPOSITORY_ROOT / "lib", REPOSITORY_ROOT / "modules"]
    files = [REPOSITORY_ROOT / "acm_switchover.py"]
    for root in python_roots:
        files.extend(root.rglob("*.py"))
    files.extend((COLLECTION_ROOT / "plugins").rglob("*.py"))
    return files


def _import_targets(node: ast.stmt) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}

    if not isinstance(node, ast.ImportFrom):
        return set()
    if not node.module:
        return {alias.name for alias in node.names}
    return {node.module, *(f"{node.module}.{alias.name}" for alias in node.names)}


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
        return f"{call.func.value.id}.{call.func.attr}"
    return None


def _direct_call(statement: ast.stmt) -> ast.Call | None:
    if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call):
        return statement.value
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
        return statement.value
    return None


def _is_normal_two_hub_guard(test: ast.expr) -> bool:
    if not isinstance(test, ast.BoolOp) or not isinstance(test.op, ast.And):
        return False

    has_not_restore_only = False
    hub_roles = set()
    for condition in test.values:
        if (
            isinstance(condition, ast.UnaryOp)
            and isinstance(condition.op, ast.Not)
            and isinstance(condition.operand, ast.Call)
            and isinstance(condition.operand.func, ast.Name)
            and condition.operand.func.id == "getattr"
            and len(condition.operand.args) >= 2
            and isinstance(condition.operand.args[0], ast.Name)
            and condition.operand.args[0].id == "args"
            and isinstance(condition.operand.args[1], ast.Constant)
            and condition.operand.args[1].value == "restore_only"
        ):
            has_not_restore_only = True
        elif (
            isinstance(condition, ast.Compare)
            and len(condition.ops) == len(condition.comparators) == 1
            and isinstance(condition.ops[0], ast.IsNot)
            and isinstance(condition.left, ast.Name)
            and isinstance(condition.comparators[0], ast.Constant)
            and condition.comparators[0].value is None
        ):
            hub_roles.add(condition.left.id)

    return has_not_restore_only and hub_roles == {"primary", "secondary"}


def _assert_python_identity_binder_contract(source: str) -> None:
    tree = ast.parse(source)
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_bind_runtime_hub_identities"
    )
    try_index, try_block = next(
        (index, statement) for index, statement in enumerate(function.body) if isinstance(statement, ast.Try)
    )
    collect_index = next(
        index
        for index, statement in enumerate(try_block.body)
        if (call := _direct_call(statement)) is not None and _call_name(call) == "_collect_hub_identities"
    )
    guard_index, guard = next(
        (index, statement)
        for index, statement in enumerate(try_block.body)
        if isinstance(statement, ast.If) and _is_normal_two_hub_guard(statement.test)
    )
    validator_call = next(
        (_direct_call(statement) for statement in guard.body if _direct_call(statement) is not None),
        None,
    )
    ensure_index = next(
        index
        for index, statement in enumerate(function.body)
        if (call := _direct_call(statement)) is not None and _call_name(call) == "state.ensure_hub_identities"
    )

    assert collect_index < guard_index
    assert validator_call is not None
    assert _call_name(validator_call) == "validate_distinct_hub_identities"
    assert try_index < ensure_index


def test_distinct_hub_runtime_owners_do_not_cross_import() -> None:
    """The two form factors share tests, never production or release-controller imports."""
    violations = []
    for path in _production_python_files():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names = _import_targets(node)
            elif isinstance(node, ast.ImportFrom):
                imported_names = _import_targets(node)
            else:
                continue

            for imported_name in imported_names:
                if path.is_relative_to(COLLECTION_ROOT):
                    prohibited = imported_name in {
                        "acm_switchover",
                        "lib",
                        "modules",
                    } or imported_name.startswith(("lib.", "modules."))
                else:
                    prohibited = imported_name == "ansible_collections" or imported_name.startswith(
                        "ansible_collections.tomazb.acm_switchover"
                    )
                if prohibited or imported_name == "tests.release" or imported_name.startswith("tests.release."):
                    violations.append(f"{path.relative_to(REPOSITORY_ROOT)}: {imported_name}")

        if "tests/release" in source:
            violations.append(f"{path.relative_to(REPOSITORY_ROOT)}: tests/release")

    assert violations == []


def test_import_target_detection_covers_from_import_aliases() -> None:
    """Alias imports cannot hide a cross-form-factor or release dependency."""
    tree = ast.parse(
        "from tests import release\n"
        "from ansible_collections.tomazb import acm_switchover\n"
        "from lib import validation\n"
    )

    assert _import_targets(tree.body[0]) == {"tests", "tests.release"}
    assert _import_targets(tree.body[1]) == {
        "ansible_collections.tomazb",
        "ansible_collections.tomazb.acm_switchover",
    }
    assert _import_targets(tree.body[2]) == {"lib", "lib.validation"}


def test_python_binds_fresh_identities_before_cross_role_validation_and_state() -> None:
    """Python must compare fresh role identities before durable state binding."""
    source = (REPOSITORY_ROOT / "acm_switchover.py").read_text(encoding="utf-8")
    input_source = _function_source(REPOSITORY_ROOT / "lib" / "validation.py", "validate_all_cli_args")

    _assert_python_identity_binder_contract(source)
    for exclusion in (
        "not is_decommission",
        "not is_setup",
        "not is_restore_only",
        "not has_argocd_resume_only",
    ):
        assert exclusion in input_source


def test_python_identity_binder_contract_rejects_synthetic_source_mutations() -> None:
    """Comments and renamed calls cannot satisfy the AST-only binding assertion."""
    source = (REPOSITORY_ROOT / "acm_switchover.py").read_text(encoding="utf-8")
    mutated_source = source.replace("validate_distinct_hub_identities(hub_identities)", "pass", 1)

    with pytest.raises(AssertionError):
        _assert_python_identity_binder_contract(mutated_source)


def test_collection_identity_barrier_keeps_namespace_evidence_action_local() -> None:
    """The barrier owns literal entry, trusted live reads, and sanitized identity construction."""
    barrier_tasks = _load_yaml("identity_barrier.yml")
    action_task = next(task for task in barrier_tasks if "tomazb.acm_switchover.checkpoint_phase" in task)
    action = action_task["tomazb.acm_switchover.checkpoint_phase"]
    action_source = COLLECTION_ROOT / "plugins" / "action" / "checkpoint_phase.py"
    read_source = _function_source(action_source, "_read_live_namespace_uid")
    identity_source = _function_source(action_source, "_run_identity_barrier")
    build_source = _function_source(action_source, "_build_trusted_operation_identity")

    assert action == {
        "identity_barrier": True,
        "phase": "preflight",
        "status": "enter",
        "checkpoint": "{{ acm_switchover_execution.checkpoint | default({}) }}",
        "hubs": "{{ acm_switchover_hubs | default({}) }}",
        "operation": "{{ acm_switchover_operation | default({}) }}",
        "execution": "{{ acm_switchover_execution | default({}) }}",
        "test_overrides": "{{ acm_switchover_test_overrides | default({}) }}",
        "collection_version": "{{ acm_switchover_collection_version | default('') }}",
    }
    assert action_task["register"] == "_checkpoint_enter"
    assert "when" not in action_task

    assert 'module_name="kubernetes.core.k8s_info"' in read_source
    for literal in (
        '"api_version": "v1"',
        '"kind": "Namespace"',
        '"name": "kube-system"',
    ):
        assert literal in read_source
    assert '"kubeconfig": hub.get("kubeconfig")' in read_source
    assert '"context": hub.get("context")' in read_source

    validation_source = _function_source(
        COLLECTION_ROOT / "plugins" / "module_utils" / "validation.py",
        "validate_operation_inputs",
    )
    variable_reference = (COLLECTION_ROOT / "docs" / "variable-reference.md").read_text(encoding="utf-8")
    assert 'execution.get("mode", "execute")' in validation_source
    assert 'execution.get("mode", "execute")' in identity_source
    assert "| `mode` | `execute`, `validate`, `dry_run` | `execute` |" in variable_reference

    assert "task_vars.get(" not in identity_source
    for caller_visible_evidence in (
        "acm_switchover_hub_identities",
        "_acm_switchover_verified_hub_identities",
        "acm_switchover_distinct_hubs_verified",
    ):
        assert caller_visible_evidence not in identity_source
    assert "sanitized_local_hubs" in build_source
    assert '"context": hubs[role]["context"]' in build_source
    assert "kubeconfig" not in build_source
    assert "hubs=sanitized_local_hubs" in build_source
    assert "hub_identities=trusted_local_hub_identities" in build_source

    barrier_text = (PREFLIGHT_TASKS / "identity_barrier.yml").read_text(encoding="utf-8")
    assert "cluster_uid" not in barrier_text
    assert "operation_identity" not in barrier_text


def test_collection_preflight_keeps_checkpoint_control_outside_identity_evidence() -> None:
    """Only post-barrier preflight may consume filtered operational checkpoint results."""
    main_text = (PREFLIGHT_TASKS / "main.yml").read_text(encoding="utf-8")
    barrier_text = (PREFLIGHT_TASKS / "identity_barrier.yml").read_text(encoding="utf-8")
    post_text = (PREFLIGHT_TASKS / "post_identity.yml").read_text(encoding="utf-8")

    for text in (main_text, barrier_text):
        assert "_checkpoint_enter.skipped_phase" not in text
        assert "_checkpoint_enter | default({})).skipped_phase" not in text
        assert "_checkpoint_enter | default({})).get('facts'" not in text

    assert "_checkpoint_enter | default({})).skipped_phase" in post_text
    assert "_checkpoint_enter | default({})).get('facts', {})" in post_text
    assert "hub_identities" not in post_text
    assert "cluster_uid" not in post_text
    assert "operation_identity" not in post_text


def test_collection_static_boundary_excludes_obsolete_discovery_and_single_hub_workflows() -> None:
    """Only normal flows use the cross-role barrier and post-barrier recovery boundary."""
    playbooks = COLLECTION_ROOT / "playbooks"
    switchover = yaml.safe_load((playbooks / "switchover.yml").read_text(encoding="utf-8"))
    outer = next(task for task in switchover[0]["tasks"] if task["name"] == "Run switchover phases with reporting")
    barrier_index = next(
        index
        for index, task in enumerate(outer["block"])
        if task["name"] == "Establish trusted identity and checkpoint barrier"
    )
    recovery_index = next(
        index for index, task in enumerate(outer["block"]) if task["name"] == "Run post-barrier switchover phases"
    )

    assert "rescue" not in outer
    assert barrier_index < recovery_index
    assert "rescue" in outer["block"][recovery_index]
    assert not (PREFLIGHT_TASKS / "discover_hub_identities.yml").exists()
    assert "discover_hub_identities.yml" not in "\n".join(
        path.read_text(encoding="utf-8") for path in PREFLIGHT_TASKS.rglob("*.yml")
    )

    restore = yaml.safe_load((playbooks / "restore_only.yml").read_text(encoding="utf-8"))
    restore_roles = [
        task["ansible.builtin.include_role"]["name"]
        for task in restore[0]["tasks"][0]["block"]
        if "ansible.builtin.include_role" in task
    ]
    decommission_text = (playbooks / "decommission.yml").read_text(encoding="utf-8")
    assert "tomazb.acm_switchover.primary_prep" not in restore_roles
    assert 'required_roles = ("secondary",) if restore_only else ("primary", "secondary")' in _function_source(
        COLLECTION_ROOT / "plugins" / "action" / "checkpoint_phase.py",
        "_run_identity_barrier",
    )
    assert "checkpoint_phase" not in decommission_text
    assert "tomazb.acm_switchover.preflight" not in decommission_text


def test_validate_kubeconfigs_uses_direct_api_probe():
    """Kubeconfig validation must not depend on MultiClusterHub discovery."""
    text = (PREFLIGHT_TASKS / "validate_kubeconfigs.yml").read_text()

    assert "kind: Namespace" in text, "validate_kubeconfigs.yml must probe a core resource directly"
    assert "name: default" in text, "validate_kubeconfigs.yml must query the default namespace for reachability"
    assert "acm_primary_mch_info" not in text, "validate_kubeconfigs.yml must not depend on MCH discovery"
    assert "acm_secondary_mch_info" not in text, "validate_kubeconfigs.yml must not depend on MCH discovery"


def test_preflight_discovers_dpa_velero_and_managed_clusters():
    """discover_resources.yml must fetch the resources needed for parity backup checks."""
    text = (PREFLIGHT_TASKS / "discover_resources.yml").read_text()

    assert "kind: DataProtectionApplication" in text, "discover_resources.yml must query DataProtectionApplications"
    assert "app.kubernetes.io/name=velero" in text, "discover_resources.yml must query Velero pods"
    assert "kind: ManagedCluster" in text, "discover_resources.yml must query ManagedClusters"


def test_preflight_mch_discovery_is_live_not_preseeded():
    """Production preflight must not let stale MCH variables satisfy version validation."""
    primary_task = next(
        task for task in _load_yaml("discover_resources.yml") if "primary MultiClusterHub" in task["name"]
    )
    secondary_task = next(
        task for task in _load_yaml("discover_resources.yml") if "secondary MultiClusterHub" in task["name"]
    )
    primary_when = " ".join(primary_task.get("when", []))
    secondary_when = str(secondary_task.get("when", ""))

    assert primary_task["register"] == "acm_primary_mch_info"
    assert secondary_task["register"] == "acm_secondary_mch_info"
    assert "mode | default('dry_run') == 'execute'" in primary_when
    assert "mode | default('dry_run') == 'execute'" in secondary_when
    assert "or (acm_primary_mch_info is not defined)" in primary_when
    assert "or (acm_secondary_mch_info is not defined)" in secondary_when


def test_preflight_persists_observability_detection_for_later_phases():
    """Collection preflight must carry Python-equivalent Observability detection through checkpoints."""
    text = (PREFLIGHT_TASKS / "post_identity.yml").read_text()

    assert "acm_switchover_primary_has_observability" in text
    assert "acm_switchover_secondary_has_observability" in text
    assert "primary_has_observability" in text
    assert "secondary_has_observability" in text


def test_preflight_rbac_skips_observability_permissions_when_observability_absent():
    """Collection RBAC checks must mirror Python's automatic Observability absence handling."""
    text = (PREFLIGHT_TASKS / "validate_rbac.yml").read_text()
    hub_text = (PREFLIGHT_TASKS / "validate_rbac_hub.yml").read_text()

    assert "_rbac_skip_observability" in text
    assert "not (acm_switchover_primary_has_observability | default(false) | bool)" in text
    assert "not (acm_switchover_secondary_has_observability | default(false) | bool)" in text
    assert 'skip_observability: "{{ _rbac_skip_observability }}"' in hub_text


def test_preflight_rbac_validates_hubs_through_shared_hub_loop():
    """Both hubs must flow through one parameterized include, mirroring Python H1's hub table + loop."""
    tasks = _load_yaml("validate_rbac.yml")

    hub_includes = [t for t in tasks if t.get("ansible.builtin.include_tasks") == "validate_rbac_hub.yml"]
    assert len(hub_includes) == 1, "validate_rbac.yml must drive hub validation through exactly one shared include"
    loop_task = hub_includes[0]
    assert loop_task["loop"] == "{{ _rbac_hub_validations }}"
    assert loop_task["loop_control"]["loop_var"] == "rbac_hub"
    assert loop_task["when"] == "rbac_hub.enabled | bool"
    assert (PREFLIGHT_TASKS / "validate_rbac_hub.yml").is_file()

    table_task = next(t for t in tasks if "_rbac_hub_validations" in t.get("ansible.builtin.set_fact", {}))
    entries = table_task["ansible.builtin.set_fact"]["_rbac_hub_validations"]
    assert [entry["hub"] for entry in entries] == ["primary", "secondary"]
    primary, secondary = entries
    assert "restore_only" in str(primary["enabled"]), "primary hub entry must be disabled in restore-only mode"
    assert secondary["enabled"] is True, "secondary hub validation must be unconditional"


def test_preflight_rbac_primary_restore_only_skip_expression_is_consistent():
    """The disabled primary row must not evaluate primary hub fields through a different restore-only rule."""
    tasks = _load_yaml("validate_rbac.yml")
    table_task = next(t for t in tasks if "_rbac_hub_validations" in t.get("ansible.builtin.set_fact", {}))
    primary = table_task["ansible.builtin.set_fact"]["_rbac_hub_validations"][0]
    restore_only_expression = "acm_switchover_operation.restore_only | default(false)"

    assert primary["enabled"] == "{{ not (%s) }}" % restore_only_expression
    for field in ("kubeconfig", "context"):
        assert "if (%s)" % restore_only_expression in primary[field]
        assert "if (%s | bool)" % restore_only_expression not in primary[field]


def test_preflight_rbac_hub_loop_preserves_registered_fact_names():
    """The shared hub file must re-publish every per-hub fact the pre-loop file registered."""
    hub_text = (PREFLIGHT_TASKS / "validate_rbac_hub.yml").read_text()
    main_text = (PREFLIGHT_TASKS / "validate_rbac.yml").read_text()

    for published in [
        '"_rbac_argocd_app_crd_{{ rbac_hub.hub }}"',
        '"_rbac_argocd_instance_crd_{{ rbac_hub.hub }}"',
        '"_rbac_argocd_install_type_{{ rbac_hub.hub }}"',
        '"_rbac_expanded_{{ rbac_hub.hub }}"',
        '"_rbac_denied_permissions_{{ rbac_hub.hub }}"',
        '"acm_{{ rbac_hub.hub }}_rbac_validation"',
    ]:
        assert published in hub_text, f"validate_rbac_hub.yml must re-publish {published}"

    assert "acm_primary_rbac_validation.results" in main_text
    assert "acm_secondary_rbac_validation.results" in main_text
    assert "acm_managed_cluster_rbac_validation.results" in main_text


def test_preflight_rbac_hub_loop_keeps_primary_only_and_secondary_only_behavior():
    """Asymmetries must live in the hub table as data, exactly like Python H1's hub_validations."""
    tasks = _load_yaml("validate_rbac.yml")
    table_task = next(t for t in tasks if "_rbac_hub_validations" in t.get("ansible.builtin.set_fact", {}))
    primary, secondary = table_task["ansible.builtin.set_fact"]["_rbac_hub_validations"]

    assert "'decommission'" in primary["include_decommission"]
    assert "_rbac_include_old_hub_finalization_primary" in primary["include_old_hub_finalization"]
    assert secondary["include_decommission"] is False
    assert secondary["include_old_hub_finalization"] is False

    old_hub_task = next(t for t in tasks if "old-hub finalization" in t["name"])
    assert "restore_only" in str(old_hub_task.get("when", "")), "old-hub finalization must stay primary-only input"


def test_preflight_rbac_managed_cluster_validation_stays_separate_from_hub_loop():
    """Managed-cluster RBAC validation keeps its own scope/loop, matching Python's separate scope."""
    tasks = _load_yaml("validate_rbac.yml")
    includes = [t.get("ansible.builtin.include_tasks") for t in tasks if t.get("ansible.builtin.include_tasks")]
    assert "validate_managed_cluster_rbac.yml" in includes

    hub_text = (PREFLIGHT_TASKS / "validate_rbac_hub.yml").read_text()
    assert "validate_managed_cluster_rbac" not in hub_text
    assert "managed_cluster" not in hub_text


def test_preflight_skip_requires_observability_checkpoint_data():
    """Skipped preflight checkpoints must not lose post-activation Observability gating inputs."""
    text = (PREFLIGHT_TASKS / "post_identity.yml").read_text()

    assert "Skipped preflight checkpoint is missing required operational metadata" in text
    assert "expected_managed_cluster_names/expected_managed_cluster_count" in text
    assert "primary_has_observability/secondary_has_observability" in text


def test_preflight_runs_auto_import_strategy_validator_after_version_checks():
    """Collection preflight must keep Python's ACM 2.14+ auto-import advisory."""
    post_identity = _load_yaml("post_identity.yml")
    include_names = _include_task_names(post_identity)

    assert "validate_versions.yml" in include_names
    assert "validate_auto_import.yml" in include_names
    assert include_names.index("validate_versions.yml") < include_names.index("validate_auto_import.yml")

    tasks = _load_yaml("validate_auto_import.yml")
    text = (PREFLIGHT_TASKS / "validate_auto_import.yml").read_text()
    assert tasks, "validate_auto_import.yml must be parseable YAML with tasks"
    assert "autoImportStrategy" in text
    assert "ImportAndSync" in text
    assert "ImportOnly" in text
    assert "local-cluster" in text
    assert "2.14.0" in text
    assert '"severity": "warning"' in text


def test_preflight_runs_controller_tooling_advisory():
    """Collection preflight should surface Python-equivalent tooling guidance without failing."""
    post_identity = _load_yaml("post_identity.yml")
    include_names = _include_task_names(post_identity)

    assert "validate_tooling.yml" in include_names

    tasks = _load_yaml("validate_tooling.yml")
    text = (PREFLIGHT_TASKS / "validate_tooling.yml").read_text()
    assert tasks, "validate_tooling.yml must be parseable YAML with tasks"
    assert "command -v oc" in text
    assert "command -v kubectl" in text
    assert "command -v jq" in text
    assert '"severity": "warning"' in text


def test_validate_versions_requires_exact_acm_version_match():
    """Collection preflight must match Python's exact ACM version equality check."""
    text = (PREFLIGHT_TASKS / "validate_versions.yml").read_text()

    assert "acm_primary_version == acm_secondary_version" in text
    assert ".split('.')[0:2]" not in text
    assert "same ACM version" in text


def test_validate_backups_enforces_backup_and_cluster_parity_checks():
    """validate_backups.yml must include the missing critical parity checks."""
    text = validate_backups_text()

    assert "InProgress" in text, "validate_backups.yml must wait for in-progress Velero backups"
    assert "until:" in text, "validate_backups.yml must poll backup state before judging latest backup phase"
    assert (
        "Read primary hub backups after in-progress wait" in text
    ), "validate_backups.yml must refresh backup facts after waiting"
    assert (
        "latest backup" in text and "unexpected state" in text
    ), "validate_backups.yml must fail when the latest Velero backup is not Completed"
    assert (
        "no managed-clusters backups found" in text
    ), "validate_backups.yml must fail when joined clusters exist without a managed-clusters backup artifact"
    assert (
        "latest managed-clusters backup" in text and "not completed" in text
    ), "validate_backups.yml must fail when the latest managed-clusters backup is not Completed"
    assert (
        "useManagedServiceAccount" in text
    ), "validate_backups.yml must enforce BackupSchedule useManagedServiceAccount"
    assert "preserveOnDelete" in text, "validate_backups.yml must enforce ClusterDeployment preserveOnDelete"
    assert "Reconciled" in text, "validate_backups.yml must verify DataProtectionApplication reconciliation"
    assert "velero_pod_count" in text, "validate_backups.yml must verify OADP/Velero presence"
    assert (
        "clusters imported after latest backup will be lost" in text
    ), "validate_backups.yml must detect clusters imported after the latest managed-clusters backup"
    msa_block = text[
        text.index('"id": "preflight-backup-schedule-use-managed-service-account"') : text.index(
            "- name: Record primary hub BackupStorageLocation health"
        )
    ]
    assert 'status": "skip"' not in msa_block
    assert "not required for full restore" not in msa_block
    assert (
        "acm_switchover_operation.method == 'full'" not in msa_block
    ), "useManagedServiceAccount validation must be critical for full and passive non-restore-only switchovers"


def test_validate_backups_is_split_into_focused_task_files():
    """The backup preflight wrapper should remain small and delegate focused validation sections."""
    tasks = yaml.safe_load((PREFLIGHT_TASKS / "validate_backups.yml").read_text())
    includes = [task.get("ansible.builtin.include_tasks") for task in tasks]

    assert includes == [
        "validate_backups/progress.yml",
        "validate_backups/artifacts.yml",
        "validate_backups/schedule_storage.yml",
        "validate_backups/infrastructure.yml",
        "validate_backups/managed_cluster_backups.yml",
    ]
    for include in includes:
        assert (PREFLIGHT_TASKS / include).is_file()


def test_validate_backups_records_remaining_in_progress_backups_after_wait():
    """Collection preflight must fail if in-progress Velero backups remain after polling."""
    text = validate_backups_text()

    assert "preflight-backup-in-progress-after-wait" in text
    assert "backup(s) still InProgress after waiting" in text
    assert "_acm_primary_backup_wait_info.attempts" in text
    assert "preflight-backup-in-progress-after-wait-restore-only" in text
    assert "restore-only target backup(s) still InProgress after waiting" in text
    assert "_acm_secondary_backup_wait_info.attempts" in text
    assert "from_json" in text


def test_validate_backups_use_managed_service_account_recommended_action_is_valid_jinja():
    """validate_backups.yml must keep the useManagedServiceAccount advisory expression parseable."""
    text = validate_backups_text()
    anchor = (
        '"recommended_action": "Set spec.useManagedServiceAccount=true in the primary BackupSchedule before '
        'switchover"'
    )
    start = text.index(anchor)
    end = text.index("else None", start) + len("else None")
    expression = text[start:end].split('"recommended_action": ', 1)[1].strip()

    Environment().compile_expression(expression)
