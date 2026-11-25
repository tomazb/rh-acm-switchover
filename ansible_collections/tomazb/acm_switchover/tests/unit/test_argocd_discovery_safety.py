"""Static contracts for the Argo CD discovery safety boundaries."""

import pathlib

import yaml

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"


def _load_discover_tasks():
    return yaml.safe_load((ROLES_DIR / "argocd_manage" / "tasks" / "discover.yml").read_text())


def _get_discovery_block(tasks):
    """Return the 'Discover Argo CD Applications from cluster' block."""
    for task in tasks:
        if task.get("name", "") == "Discover Argo CD Applications from cluster":
            return task
    raise AssertionError("Could not find 'Discover Argo CD Applications from cluster' block")


def _get_rescue_tasks(tasks):
    """Return the rescue tasks from the discovery block."""
    block = _get_discovery_block(tasks)
    rescue = block.get("rescue")
    assert rescue is not None, "Discovery block must have a rescue section"
    return rescue


def _walk_tasks(tasks):
    """Yield task dictionaries from a task list, including nested block/rescue/always entries."""
    for task in tasks:
        yield task
        for nested_key in ("block", "rescue", "always"):
            for nested in task.get(nested_key, []) or []:
                yield from _walk_tasks([nested])


class TestDiscoveryFailureHandling:
    def test_rescue_only_publishes_advisory_error(self):
        rescue = _get_rescue_tasks(_load_discover_tasks())
        published_statuses = [
            task["ansible.builtin.set_fact"]["acm_switchover_argocd_discovery_status"]["status"]
            for task in rescue
            if "acm_switchover_argocd_discovery_status" in task.get("ansible.builtin.set_fact", {})
        ]
        assert published_statuses == ["error"]

    def test_fail_task_message_is_stable_and_omits_raw_error(self):
        rescue = _get_rescue_tasks(_load_discover_tasks())
        fail_tasks = [task for task in rescue if "ansible.builtin.fail" in task]
        assert len(fail_tasks) == 1
        assert fail_tasks[0]["ansible.builtin.fail"] == {
            "msg": "Argo CD discovery failed; verify controller access and input, then retry."
        }


class TestApiFailureBoundary:
    """API task failures must be normalized before Ansible renders a failed result."""

    def test_application_api_tasks_are_non_failing_and_protected(self):
        tasks = _get_discovery_block(_load_discover_tasks())["block"]
        api_actions = {"kubernetes.core.k8s_info", "tomazb.acm_switchover.acm_k8s_read_outcome"}
        api_tasks = [task for task in tasks if api_actions.intersection(task)]
        assert len(api_tasks) == 3
        for task in api_tasks:
            assert task.get("no_log") is True
            assert task.get("failed_when") is False

    def test_application_filter_tasks_are_non_failing_and_protected(self):
        tasks = list(_walk_tasks(_load_discover_tasks()))
        filter_tasks = [task for task in tasks if "tomazb.acm_switchover.acm_argocd_filter" in task]
        assert len(filter_tasks) == 2
        for task in filter_tasks:
            assert task.get("no_log") is True
            assert task.get("failed_when") is False

    def test_boundary_failures_use_fixed_public_messages(self):
        tasks = _get_discovery_block(_load_discover_tasks())["block"]
        boundary_failures = [task for task in tasks if "discovery failure handling" in task.get("name", "")]
        assert len(boundary_failures) == 3
        for task in boundary_failures:
            message = task["ansible.builtin.fail"]["msg"]
            assert message == "Argo CD discovery boundary reported a failure."
            assert "_argocd_discovery_error" not in message


class TestUnsafeApplicationBlocking:
    """Argo CD management must block Applications that cannot be safely paused."""

    def _find_blocker_fail_task(self, tasks):
        for task in _walk_tasks(tasks):
            fail = task.get("ansible.builtin.fail")
            if isinstance(fail, dict) and "unsafe for automated pause" in str(fail.get("msg", "")):
                return task
        raise AssertionError("discover.yml must fail when acm_argocd_filter reports blocked Applications")

    def test_discover_fails_when_filter_reports_blocked_applications(self):
        tasks = _load_discover_tasks()
        task = self._find_blocker_fail_task(tasks)

        assert "when" in task
        when_text = str(task["when"])
        assert "acm_switchover_argocd_blocked_apps" in when_text
        assert "== 'pause'" in when_text
        assert "acm_switchover_argocd_blocked_apps" in str(task["ansible.builtin.fail"]["msg"])

    def test_pause_rereads_applications_after_patch(self):
        pause_tasks = yaml.safe_load((ROLES_DIR / "argocd_manage" / "tasks" / "pause.yml").read_text())

        reread_tasks = [
            task
            for task in _walk_tasks(pause_tasks)
            if task.get("name") == "Re-read Applications after Argo CD pause" and "kubernetes.core.k8s_info" in task
        ]
        assert reread_tasks, "pause.yml must re-read Applications after patching auto-sync"

    def test_pause_fails_when_autosync_remains_enabled(self):
        pause_tasks = yaml.safe_load((ROLES_DIR / "argocd_manage" / "tasks" / "pause.yml").read_text())

        fail_tasks = [
            task
            for task in _walk_tasks(pause_tasks)
            if "ansible.builtin.fail" in task
            and "auto-sync remains enabled after pause" in str(task["ansible.builtin.fail"].get("msg", ""))
        ]
        assert fail_tasks, "pause.yml must fail if a re-read Application still has automated sync enabled"


class TestTrustedNamespaceDiscovery:
    """Later discovery passes may aggregate namespaced reads from trusted namespace hints."""

    def test_discover_resolves_trusted_namespace_list_for_current_hub(self):
        tasks = _load_discover_tasks()
        block = _get_discovery_block(tasks)
        resolve_tasks = [
            task
            for task in block.get("block", [])
            if task.get("name") == "Normalize trusted Argo CD discovery namespaces for current hub"
        ]
        assert resolve_tasks, "discover.yml must resolve trusted namespaces before listing Applications"
        fact = resolve_tasks[0]["ansible.builtin.set_fact"]
        assert "_argocd_trusted_discovery_namespaces" in fact
        assert "_argocd_raw_trusted_discovery_namespaces" in str(fact)
        raw_task = next(
            task
            for task in block.get("block", [])
            if task.get("name") == "Resolve raw trusted Argo CD discovery namespaces for current hub"
        )
        assert "acm_switchover_argocd_discovery_namespaces" in str(raw_task["ansible.builtin.set_fact"])

    def test_discover_aggregates_namespaced_k8s_info_reads(self):
        tasks = _load_discover_tasks()
        block = _get_discovery_block(tasks)
        list_tasks = [
            task
            for task in block.get("block", [])
            if task.get("name") == "List Applications in trusted namespaces" and "kubernetes.core.k8s_info" in task
        ]
        aggregate_tasks = [
            task
            for task in yaml.safe_load(
                (ROLES_DIR / "argocd_manage" / "tasks" / "validate_scoped_discovery.yml").read_text()
            )
            if task.get("name") == "Aggregate complete scoped Argo CD discovery resources"
        ]
        assert list_tasks, "discover.yml must list Applications per trusted namespace"
        assert aggregate_tasks, "discover.yml must aggregate namespaced Application reads"
        assert "{{ item }}" in str(list_tasks[0]["kubernetes.core.k8s_info"].get("namespace", ""))
        assert "_argocd_scoped_live_query" in str(aggregate_tasks[0]["ansible.builtin.set_fact"])

    def test_discover_records_namespace_map_after_cluster_wide_pass(self):
        tasks = _load_discover_tasks()
        block = _get_discovery_block(tasks)
        record_tasks = [
            task
            for task in block.get("block", [])
            if task.get("name") == "Record discovered Application namespaces for current hub"
        ]
        assert record_tasks, "discover.yml must persist per-hub Application namespaces after cluster-wide discovery"
        fact = record_tasks[0]["ansible.builtin.set_fact"]
        assert "acm_switchover_argocd_discovery_namespaces" in fact
        when_text = str(record_tasks[0].get("when", ""))
        assert "_argocd_use_scoped_discovery" in when_text
        assert "== 'pause'" in when_text

    def test_discover_does_not_seed_namespaces_in_read_only_discover_mode(self):
        tasks = _load_discover_tasks()
        block = _get_discovery_block(tasks)
        record_tasks = [
            task
            for task in block.get("block", [])
            if task.get("name") == "Record discovered Application namespaces for current hub"
        ]
        assert record_tasks
        when_text = str(record_tasks[0].get("when", ""))
        assert "!= 'discover'" in when_text or "== 'pause'" in when_text

    def test_discover_keeps_advisory_discover_mode_cluster_wide(self):
        tasks = _load_discover_tasks()
        block = _get_discovery_block(tasks)
        resolve_tasks = [
            task
            for task in block.get("block", [])
            if task.get("name") == "Normalize trusted Argo CD discovery namespaces for current hub"
        ]
        assert resolve_tasks
        scoped_expr = str(resolve_tasks[0]["ansible.builtin.set_fact"]["_argocd_use_scoped_discovery"])
        assert "!= 'discover'" in scoped_expr or "== 'discover'" in scoped_expr

    def test_discover_fails_when_persisted_hub_namespaces_are_not_a_list(self):
        tasks = _load_discover_tasks()
        block = _get_discovery_block(tasks)
        fail_tasks = [
            task
            for task in block.get("block", [])
            if "ansible.builtin.fail" in task
            and "must be a list" in str(task.get("ansible.builtin.fail", {}).get("msg", "")).lower()
        ]
        assert fail_tasks, "discover.yml must fail closed when persisted hub namespace hints are malformed"
        when_text = str(fail_tasks[0].get("when", ""))
        assert "_argocd_discover_hub" in when_text
        assert "in (acm_switchover_argocd_discovery_namespaces | default({}))" in when_text
        assert "type_debug" in when_text
        assert "!= 'list'" in when_text

    def test_scoped_cluster_and_published_results_have_distinct_owners(self):
        """A skipped cluster-wide register must not replace validated scoped resources."""
        block_tasks = _get_discovery_block(_load_discover_tasks())["block"]
        scoped = next(task for task in block_tasks if task.get("name") == "List Applications in trusted namespaces")
        cluster = next(task for task in block_tasks if task.get("name") == "List Applications cluster-wide")
        text = (ROLES_DIR / "argocd_manage" / "tasks" / "discover.yml").read_text()

        assert scoped["register"] == "_argocd_scoped_live_query"
        assert cluster["register"] == "_argocd_cluster_live_query"
        assert "_argocd_published_discovery" in text
        assert not any(task.get("register") == "_argocd_published_discovery" for task in block_tasks)

    def test_filter_and_publication_are_gated_on_complete_success(self):
        block_tasks = _get_discovery_block(_load_discover_tasks())["block"]
        filter_task = next(task for task in block_tasks if task.get("name") == "Filter to ACM-touching applications")
        record_task = next(task for task in block_tasks if task.get("name") == "Record discovered ACM apps")

        assert "_argocd_published_discovery.status == 'ok'" in str(filter_task.get("when"))
        assert "_argocd_published_discovery.status == 'ok'" in str(record_task.get("when"))

    def test_scoped_validation_precedes_aggregation_and_filtering(self):
        """No scoped resources may reach the filter before complete validation."""
        block_tasks = _get_discovery_block(_load_discover_tasks())["block"]
        names = [task.get("name") for task in block_tasks]

        validate_index = names.index("Validate complete scoped Argo CD discovery")
        publish_index = names.index("Publish validated Argo CD discovery")
        filter_index = names.index("Filter to ACM-touching applications")

        assert validate_index < publish_index < filter_index
        validation_task = block_tasks[validate_index]
        assert validation_task["ansible.builtin.include_tasks"] == "validate_scoped_discovery.yml"

    def test_scoped_validation_contract_is_explicit_and_msg_is_non_authoritative(self):
        """The approved present/absent predicates must not infer success from msg."""
        validation_path = ROLES_DIR / "argocd_manage" / "tasks" / "validate_scoped_discovery.yml"
        validation_text = validation_path.read_text()
        validation_tasks = yaml.safe_load(validation_text)
        decision_task_names = {
            "Validate scoped Argo CD discovery envelope",
            "Classify each scoped Argo CD discovery result",
            "Publish scoped Argo CD discovery classification",
        }
        decision_text = "\n".join(
            str(task["ansible.builtin.set_fact"])
            for task in validation_tasks
            if task.get("name") in decision_task_names
        )

        for field in ("failed", "skipped", "unreachable", "api_found", "resources", "item"):
            assert field in validation_text
        assert "type_debug" in validation_text
        assert "_argocd_scoped_validation" in validation_text
        assert decision_task_names == {
            task.get("name") for task in validation_tasks if task.get("name") in decision_task_names
        }
        assert "msg" not in decision_text
