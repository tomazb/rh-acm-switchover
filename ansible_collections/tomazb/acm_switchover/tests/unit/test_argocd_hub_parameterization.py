"""Tests to verify ArgoCD role tasks use parameterized hub access."""

import pathlib

import yaml

ROLE_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles" / "argocd_manage" / "tasks"


def _load_yaml(name: str) -> list[dict]:
    return yaml.safe_load((ROLE_DIR / name).read_text())


def test_pause_uses_parameterized_hub():
    """pause.yml must NOT hardcode .primary or .secondary for kubeconfig/context."""
    tasks = _load_yaml("pause.yml")
    for task in tasks:
        k8s = task.get("kubernetes.core.k8s", {})
        if not k8s:
            for block_task in task.get("block", []):
                k8s = block_task.get("kubernetes.core.k8s", {})
                if k8s:
                    kc = str(k8s.get("kubeconfig", ""))
                    ctx = str(k8s.get("context", ""))
                    assert ".primary." not in kc, f"pause.yml hardcodes .primary in kubeconfig: {kc}"
                    assert ".primary." not in ctx, f"pause.yml hardcodes .primary in context: {ctx}"
                    assert ".secondary." not in kc, f"pause.yml hardcodes .secondary in kubeconfig: {kc}"
                    assert ".secondary." not in ctx, f"pause.yml hardcodes .secondary in context: {ctx}"
                    assert "_argocd_discover_hub" in kc, f"pause.yml kubeconfig should use _argocd_discover_hub: {kc}"


def test_resume_uses_parameterized_hub():
    """resume.yml must NOT hardcode .primary or .secondary for kubeconfig/context."""
    tasks = _load_yaml("resume.yml")
    for task in tasks:
        for block_task in task.get("block", []):
            k8s = block_task.get("kubernetes.core.k8s", {})
            if k8s:
                kc = str(k8s.get("kubeconfig", ""))
                ctx = str(k8s.get("context", ""))
                assert ".primary." not in kc, f"resume.yml hardcodes .primary in kubeconfig: {kc}"
                assert ".primary." not in ctx, f"resume.yml hardcodes .primary in context: {ctx}"
                assert ".secondary." not in kc, f"resume.yml hardcodes .secondary in kubeconfig: {kc}"
                assert ".secondary." not in ctx, f"resume.yml hardcodes .secondary in context: {ctx}"
                assert "_argocd_discover_hub" in kc, f"resume.yml kubeconfig should use _argocd_discover_hub: {kc}"


def test_run_id_default_is_not_empty_string():
    """defaults/main.yml run_id must not default to empty string."""
    defaults = yaml.safe_load((ROLE_DIR.parent / "defaults" / "main.yml").read_text())
    run_id = defaults.get("acm_switchover_argocd", {}).get("run_id")
    # run_id should either be absent (undefined → triggers Jinja default())
    # or be a non-empty string. Empty string breaks resume matching.
    assert run_id is None or (
        isinstance(run_id, str) and run_id != ""
    ), "run_id defaults to empty string, which bypasses Jinja default() filter"


def _find_pause_application_patch_task() -> dict:
    tasks = _load_yaml("pause.yml")
    for task in tasks:
        for block_task in task.get("block", []):
            k8s = block_task.get("kubernetes.core.k8s", {})
            if k8s:
                return block_task

    raise AssertionError("pause.yml should contain an Application patch task")


def test_pause_patches_automated_apps_even_with_existing_pause_marker():
    """A stale pause marker must not skip an app that currently has automated sync."""
    patch_task = _find_pause_application_patch_task()
    when = patch_task.get("when", [])
    if isinstance(when, str):
        when = [when]
    when_text = " ".join(str(w) for w in when)

    assert "automated" in when_text
    assert "paused-by" not in when_text, (
        "pause.yml must not skip patching solely because a stale paused-by " f"annotation exists. Current when: {when}"
    )


def test_pause_skips_apps_without_non_null_automated_sync_policy():
    """Applications without non-null automated sync must not have original-sync-policy clobbered."""
    patch_task = _find_pause_application_patch_task()
    when = patch_task.get("when", [])
    if isinstance(when, str):
        when = [when]

    assert "(item.spec.syncPolicy | default({})).automated | default(none) is not none" in when


def test_pause_sets_automated_to_null_for_merge_patch_delete():
    """pause.yml must use null, not omission, to remove syncPolicy.automated via merge patch."""
    patch_task = _find_pause_application_patch_task()
    sync_policy = str(patch_task["kubernetes.core.k8s"]["definition"]["spec"]["syncPolicy"])
    assert "combine({'automated': none})" in sync_policy
    assert "rejectattr('key', 'equalto', 'automated')" not in sync_policy


def test_discover_uses_parameterized_hub():
    """discover.yml should already use _argocd_discover_hub (baseline check)."""
    tasks = _load_yaml("discover.yml")
    found = False
    for task in tasks:
        for block_task in task.get("block", []):
            k8s_info = block_task.get("kubernetes.core.k8s_info", {})
            if k8s_info:
                kc = str(k8s_info.get("kubeconfig", ""))
                assert "_argocd_discover_hub" in kc
                found = True
    assert found, "discover.yml should have at least one k8s_info task with _argocd_discover_hub"


def test_discover_namespace_defaults_to_omit():
    """discover.yml must NOT hardcode a single default namespace like 'argocd'.

    When acm_switchover_argocd.namespace is not set and no trusted namespace
    hints exist, discovery should search cluster-wide (default(omit)).
    """
    text = (ROLE_DIR / "discover.yml").read_text()
    assert "default('argocd')" not in text, (
        "discover.yml still hardcodes default('argocd'); " "should use default(omit) for cluster-wide discovery"
    )
    tasks = _load_yaml("discover.yml")
    cluster_wide_tasks = [
        block_task
        for task in tasks
        for block_task in task.get("block", [])
        if block_task.get("name") == "List Applications cluster-wide" and block_task.get("kubernetes.core.k8s_info")
    ]
    assert cluster_wide_tasks, "discover.yml must keep an explicit cluster-wide Application list task"
    ns = str(cluster_wide_tasks[0]["kubernetes.core.k8s_info"].get("namespace", ""))
    assert "default(omit)" in ns, f"cluster-wide discover namespace should use default(omit), got: {ns}"


def test_discover_mode_does_not_generate_pause_run_id():
    """Read-only Argo CD advisory discovery must not create pause metadata."""
    text = (ROLE_DIR / "discover.yml").read_text()
    assert "== 'pause'" in text
    assert "!= 'resume'" in text


def test_argocd_manage_supports_discover_only_mode():
    """The argocd_manage role should support discovery without pause/resume mutation."""
    text = (ROLE_DIR / "main.yml").read_text()
    assert "Discover Argo CD Applications" in text
    assert "discover" in text
    assert "import_tasks: pause.yml" in text
    assert "import_tasks: resume.yml" in text


ROLES_DIR = ROLE_DIR.parents[1]


def _load_role_yaml(role_name: str, task_name: str) -> list[dict]:
    return yaml.safe_load((ROLES_DIR / role_name / "tasks" / task_name).read_text())


def test_primary_prep_pauses_both_hubs():
    """primary_prep/main.yml should include argocd_manage for both primary and secondary hubs."""
    text = (ROLES_DIR / "primary_prep" / "tasks" / "main.yml").read_text()
    assert (
        text.count("argocd_manage") >= 2
    ), "primary_prep should include argocd_manage role at least twice (primary + secondary)"
    assert "_argocd_discover_hub: primary" in text, "Should pause primary hub"
    assert "_argocd_discover_hub: secondary" in text, "Should pause secondary hub"


def test_primary_prep_pauses_argocd_before_acm_mutations():
    """Argo CD auto-sync must be paused before primary_prep mutates ACM resources."""
    tasks = _load_role_yaml("primary_prep", "main.yml")
    block_tasks = next(task["block"] for task in tasks if "block" in task)

    argocd_indices = [
        index
        for index, task in enumerate(block_tasks)
        if task.get("ansible.builtin.include_role", {}).get("name") == "tomazb.acm_switchover.argocd_manage"
    ]
    mutation_indices = [
        index
        for index, task in enumerate(block_tasks)
        if task.get("ansible.builtin.include_tasks")
        in {"pause_backups.yml", "manage_auto_import.yml", "scale_observability.yml"}
    ]

    assert argocd_indices, "primary_prep must include argocd_manage pause tasks"
    assert mutation_indices, "primary_prep must still include ACM mutation tasks"
    assert max(argocd_indices) < min(mutation_indices)


def test_preflight_gitops_runs_read_only_argocd_advisory_on_expected_hubs():
    """Collection preflight must mirror Python's ACM-touching Argo CD advisory pass."""
    text = (ROLES_DIR / "preflight" / "tasks" / "validate_gitops.yml").read_text()

    assert "tomazb.acm_switchover.argocd_manage" in text
    assert "acm_switchover_argocd_mode_override: discover" in text
    assert "_argocd_discover_hub: primary" in text
    assert "_argocd_discover_hub: secondary" in text
    assert "acm_switchover_operation.restore_only" in text
    assert "acm_switchover_features.argocd.manage" in text
    assert "ACM resources detected" in text
    assert "app.namespace" in text
    assert "app.name" in text


def test_finalization_does_not_auto_resume():
    """finalization/main.yml should NOT auto-resume argocd (removed feature)."""
    text = (ROLES_DIR / "finalization" / "tasks" / "main.yml").read_text()
    resume_count = text.count("acm_switchover_argocd_mode_override: resume")
    assert resume_count == 0, f"finalization should not auto-resume argocd, found {resume_count} resume include(s)"


PLAYBOOKS_DIR = pathlib.Path(__file__).resolve().parents[2] / "playbooks"


def test_standalone_argocd_resume_covers_both_hubs():
    """argocd_resume.yml must resume on both secondary and primary hubs.

    primary_prep pauses both hubs, so the standalone resume recovery playbook
    must mirror that by resuming both. Primary resume should be guarded by
    acm_switchover_hubs.primary is defined.
    """
    text = (PLAYBOOKS_DIR / "argocd_resume.yml").read_text()
    resume_count = text.count("acm_switchover_argocd_mode_override: resume")
    assert resume_count >= 2, f"argocd_resume.yml should resume on both hubs, found {resume_count} resume block(s)"
    assert "_argocd_discover_hub: secondary" in text, "Should resume secondary hub"
    assert "_argocd_discover_hub: primary" in text, "Should resume primary hub"
    assert (
        "acm_switchover_hubs.primary is defined" in text
    ), "Primary hub resume should be guarded by acm_switchover_hubs.primary is defined"


def test_primary_prep_rehydrates_discovery_namespaces_from_checkpoint():
    """Retrying primary_prep must reuse persisted per-hub Application namespace hints."""
    text = (ROLES_DIR / "primary_prep" / "tasks" / "main.yml").read_text()
    assert "Rehydrate Argo CD discovery namespaces from checkpoint" in text
    assert "argocd_discovery_namespaces" in text
    pause_index = text.index("Pause Argo CD auto-sync on primary hub when enabled")
    rehydrate_index = text.find("Rehydrate Argo CD discovery namespaces from checkpoint")
    assert rehydrate_index != -1
    assert rehydrate_index < pause_index


def test_primary_prep_validates_rehydrated_discovery_namespace_lists():
    """Malformed checkpoint namespace hints must fail before scoped Argo CD discovery."""
    text = (ROLES_DIR / "primary_prep" / "tasks" / "main.yml").read_text()
    assert "Validate rehydrated Argo CD discovery namespaces from checkpoint" in text
    validate_index = text.find("Validate rehydrated Argo CD discovery namespaces from checkpoint")
    rehydrate_index = text.find("Rehydrate Argo CD discovery namespaces from checkpoint")
    assert validate_index != -1
    assert validate_index < rehydrate_index
    assert "(item.value | type_debug) == 'list'" in text


def test_standalone_argocd_resume_restores_discovery_namespaces_from_checkpoint():
    """Standalone resume must rehydrate trusted namespace hints before discovery."""
    text = (PLAYBOOKS_DIR / "argocd_resume.yml").read_text()
    assert "argocd_discovery_namespaces" in text
    assert "Seed Argo CD discovery namespaces from checkpoint" in text
    run_id_index = text.find("Seed Argo CD run_id from checkpoint")
    namespaces_index = text.find("Seed Argo CD discovery namespaces from checkpoint")
    resume_index = text.find("Resume autosync on secondary hub")
    assert namespaces_index != -1
    assert run_id_index != -1
    assert namespaces_index < resume_index


def test_standalone_argocd_resume_validates_rehydrated_discovery_namespace_lists():
    """Malformed checkpoint namespace hints must fail before standalone Argo CD resume."""
    text = (PLAYBOOKS_DIR / "argocd_resume.yml").read_text()
    assert "Validate rehydrated Argo CD discovery namespaces from checkpoint" in text
    validate_index = text.find("Validate rehydrated Argo CD discovery namespaces from checkpoint")
    seed_index = text.find("Seed Argo CD discovery namespaces from checkpoint")
    assert validate_index != -1
    assert validate_index < seed_index
    assert "(item.value | type_debug) == 'list'" in text


def test_standalone_argocd_resume_defaults_checkpoint_in_discovery_namespace_loop():
    """Loop expressions must default the checkpoint object before dict2items expansion."""
    text = (PLAYBOOKS_DIR / "argocd_resume.yml").read_text()
    assert (
        "(_argocd_resume_checkpoint | default({})).get('operational_data', {}).get('argocd_discovery_namespaces', {})"
        in text
    )


def test_standalone_argocd_resume_defaults_checkpoint_when_seeding_discovery_namespaces():
    """Seeding discovery namespaces must not dereference an undefined checkpoint object."""
    text = (PLAYBOOKS_DIR / "argocd_resume.yml").read_text()
    assert (
        "{{ (_argocd_resume_checkpoint | default({})).get('operational_data', {}).get('argocd_discovery_namespaces', {}) }}"
        in text
    )


def test_standalone_argocd_resume_restores_run_id_from_checkpoint():
    """argocd_resume.yml must seed run_id from checkpoint before resuming.

    The pause run_id is persisted in checkpoint operational_data during
    switchover/restore-only runs. Standalone resume must reload that value so
    resume.yml can match the paused-by annotation without requiring operators
    to pass run_id manually.
    """
    text = (PLAYBOOKS_DIR / "argocd_resume.yml").read_text()

    assert (
        "acm_switchover_execution" in text and "checkpoint" in text
    ), "argocd_resume.yml must inspect the configured checkpoint path"
    assert (
        "ansible.builtin.slurp" in text and "b64decode" in text and "from_json" in text
    ), "argocd_resume.yml must load checkpoint JSON (controller-side) before including argocd_manage"
    assert (
        "lookup('env', 'PWD')" in text and "startswith('/')" in text
    ), "argocd_resume.yml must resolve relative checkpoint paths against the run directory ($PWD)"
    assert (
        "operational_data" in text and "argocd_run_id" in text
    ), "argocd_resume.yml must read operational_data.argocd_run_id from the checkpoint"
    assert (
        "combine({" in text and "'run_id':" in text
    ), "argocd_resume.yml must seed acm_switchover_argocd.run_id from the persisted checkpoint"
    assert (
        "(acm_switchover_argocd.run_id | default('')) | length == 0" in text
    ), "argocd_resume.yml must not overwrite an explicit run_id supplied by the operator"
    assert (
        "get('run_id', '')" in text
    ), "argocd_resume.yml must not overwrite an explicit execution.run_id supplied by the operator"


def test_standalone_argocd_resume_guards_checkpoint_load_by_enabled_flag():
    """argocd_resume.yml must not load a stale checkpoint when checkpointing is disabled.

    A checkpoint file may exist from a previous run at the configured path even
    when checkpoint.enabled is false for the current run (no fresh write happened).
    Loading it would seed a wrong run_id. Checkpoint file tasks must consume the
    shared lookup predicate, which itself requires checkpoint.enabled.
    """
    playbook = yaml.safe_load((PLAYBOOKS_DIR / "argocd_resume.yml").read_text())
    pre_tasks = playbook[0].get("pre_tasks", [])
    enabled_guard = "acm_switchover_execution.checkpoint.enabled | default(false)"
    lookup_task = next(
        task
        for task in pre_tasks
        if "_argocd_resume_checkpoint_lookup_required" in task.get("ansible.builtin.set_fact", {})
    )
    lookup_expr = lookup_task["ansible.builtin.set_fact"]["_argocd_resume_checkpoint_lookup_required"]
    assert enabled_guard in lookup_expr

    checkpoint_file_task_names = {
        "Resolve checkpoint path value",
        "Resolve checkpoint path to absolute path (controller-side)",
        "Validate persisted checkpoint path",
        "Check for persisted checkpoint with Argo CD run_id",
        "Read persisted checkpoint file",
        "Parse persisted checkpoint JSON",
        "Seed Argo CD run_id from checkpoint",
    }
    for task in pre_tasks:
        if task.get("name") in checkpoint_file_task_names:
            assert "_argocd_resume_checkpoint_lookup_required | default(false)" in task.get("when", [])


def test_standalone_argocd_resume_validates_checkpoint_path_before_file_reads():
    """argocd_resume.yml must artifact-validate checkpoint.path before stat/slurp."""
    playbook = yaml.safe_load((PLAYBOOKS_DIR / "argocd_resume.yml").read_text())
    pre_tasks = playbook[0].get("pre_tasks", [])
    validate_indices = [
        idx for idx, task in enumerate(pre_tasks) if "tomazb.acm_switchover.acm_safe_path_validate" in task
    ]
    stat_indices = [idx for idx, task in enumerate(pre_tasks) if "ansible.builtin.stat" in task]

    assert validate_indices, "argocd_resume.yml must validate checkpoint.path before touching controller files"
    assert stat_indices, "argocd_resume.yml should still inspect the checkpoint file after validation"
    assert validate_indices[0] < stat_indices[0], "argocd_resume.yml must validate checkpoint.path before stat/slurp"

    validate_task = pre_tasks[validate_indices[0]]["tomazb.acm_switchover.acm_safe_path_validate"]
    assert validate_task.get("path_type") == "artifact", (
        "argocd_resume.yml must use artifact path validation before reading persisted checkpoints "
        "so symlink escapes are rejected"
    )


def test_standalone_argocd_resume_validates_live_identity_before_resume():
    """argocd_resume.yml must bind checkpoint identity to live hub UIDs before mutation."""
    playbook = yaml.safe_load((PLAYBOOKS_DIR / "argocd_resume.yml").read_text())
    pre_tasks = playbook[0].get("pre_tasks", [])
    tasks = playbook[0].get("tasks", [])
    all_tasks = [*pre_tasks, *tasks]

    primary_identity_indices = [
        idx
        for idx, task in enumerate(pre_tasks)
        if task.get("name") == "Read primary kube-system namespace identity"
        and task.get("kubernetes.core.k8s_info", {}).get("kind") == "Namespace"
        and task.get("kubernetes.core.k8s_info", {}).get("name") == "kube-system"
    ]
    secondary_identity_indices = [
        idx
        for idx, task in enumerate(pre_tasks)
        if task.get("name") == "Read secondary kube-system namespace identity"
        and task.get("kubernetes.core.k8s_info", {}).get("kind") == "Namespace"
        and task.get("kubernetes.core.k8s_info", {}).get("name") == "kube-system"
    ]
    validate_indices = [
        idx for idx, task in enumerate(pre_tasks) if "tomazb.acm_switchover.acm_checkpoint_identity_validate" in task
    ]
    resume_indices = [
        idx
        for idx, task in enumerate(all_tasks)
        if task.get("ansible.builtin.include_role", {}).get("name") == "tomazb.acm_switchover.argocd_manage"
    ]

    assert primary_identity_indices, "argocd_resume.yml must read live primary kube-system UID"
    assert secondary_identity_indices, "argocd_resume.yml must read live secondary kube-system UID"
    assert validate_indices, "argocd_resume.yml must validate checkpoint identity before standalone resume"
    assert resume_indices, "argocd_resume.yml must still include argocd_manage resume tasks"
    assert max(primary_identity_indices + secondary_identity_indices) < validate_indices[0]
    assert validate_indices[0] < resume_indices[0]

    secondary_read = pre_tasks[secondary_identity_indices[0]]
    secondary_args = secondary_read["kubernetes.core.k8s_info"]
    secondary_when = " ".join(str(item) for item in secondary_read.get("when", []))
    assert "(acm_switchover_hubs.secondary | default({})).kubeconfig | default(omit)" in secondary_args.get(
        "kubeconfig", ""
    )
    assert "(acm_switchover_hubs.secondary | default({})).context | default(omit)" in secondary_args.get("context", "")
    assert "acm_switchover_hubs.secondary is defined" in secondary_when
    assert "acm_switchover_hubs.secondary.context | default('')" in secondary_when

    publish_task = next(task for task in pre_tasks if task.get("name") == "Publish Argo CD resume live hub identities")
    secondary_context = publish_task["ansible.builtin.set_fact"]["_argocd_resume_hub_identities"]["secondary"][
        "context"
    ]
    assert "(acm_switchover_hubs.secondary | default({})).context | default('')" in secondary_context


def test_standalone_argocd_resume_uses_swapped_mapping_for_effective_hubs():
    """A swapped checkpoint match must be consumed before resume targets are chosen."""
    playbook = yaml.safe_load((PLAYBOOKS_DIR / "argocd_resume.yml").read_text())
    pre_tasks = playbook[0].get("pre_tasks", [])
    tasks = playbook[0].get("tasks", [])

    validate_task = next(
        task for task in pre_tasks if task.get("name") == "Validate checkpoint identity before Argo CD resume"
    )
    assert validate_task.get("register") == "_argocd_resume_identity_validation"

    mapping_task = next(
        task for task in pre_tasks if task.get("name") == "Resolve effective hub mapping for standalone Argo CD resume"
    )
    mapping_expr = str(mapping_task["ansible.builtin.set_fact"]["_argocd_resume_effective_hubs"])
    assert "_argocd_resume_identity_validation.matched_mapping" in mapping_expr
    assert "swapped" in mapping_expr
    assert "acm_switchover_hubs.secondary" in mapping_expr
    assert "acm_switchover_hubs.primary" in mapping_expr

    secondary_resume = next(task for task in tasks if task.get("name") == "Resume autosync on secondary hub")
    primary_resume = next(task for task in tasks if task.get("name") == "Resume autosync on primary hub")

    assert "_argocd_resume_effective_hubs" in str(secondary_resume.get("vars", {}))
    assert "_argocd_resume_effective_hubs" in str(primary_resume.get("vars", {}))
    assert "_argocd_resume_effective_hubs.primary" in " ".join(str(item) for item in primary_resume.get("when", []))


def test_discover_run_id_gated_by_resume_mode():
    """discover.yml must NOT generate run_id when mode is resume.

    When the argocd_manage role runs in resume mode without an explicit run_id,
    _argocd_expected_run_id should resolve to '' so resume.yml's safety
    fallback ('resume ALL paused apps') fires. Generating a fresh run_id
    defeats this fallback because the new UUID never matches any annotation.
    """
    tasks = _load_yaml("discover.yml")

    # Find the "Generate run_id" set_fact task
    for task in tasks:
        for block_task in task.get("block", []):
            sf = block_task.get("ansible.builtin.set_fact")
            if sf and "run_id" in str(sf):
                when = block_task.get("when", [])
                if isinstance(when, str):
                    when = [when]
                when_text = " ".join(str(w) for w in when)
                assert "resume" in when_text, (
                    "discover.yml run_id generation must be gated to exclude resume mode. " f"Current when: {when}"
                )
                return

    raise AssertionError("discover.yml: Could not find 'Generate run_id' set_fact task")
