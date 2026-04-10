from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule


ACM_NAMESPACES = {
    "open-cluster-management",
    "open-cluster-management-backup",
    "open-cluster-management-observability",
    "multicluster-engine",
    "open-cluster-management-global-set",
    "local-cluster",
}

ACM_KINDS = {
    "MultiClusterHub",
    "MultiClusterObservability",
    "ManagedCluster",
    "BackupSchedule",
    "Restore",
    "ClusterDeployment",
}


def is_acm_touching_application(app: dict) -> bool:
    for resource in app.get("status", {}).get("resources", []):
        if resource.get("namespace") in ACM_NAMESPACES:
            return True
        if resource.get("kind") in ACM_KINDS:
            return True
    return False


def build_pause_patch(sync_policy: dict, run_id: str) -> dict:
    sync_policy = dict(sync_policy)
    sync_policy.pop("automated", None)
    return {
        "metadata": {"annotations": {"acm-switchover.argoproj.io/paused-by": run_id}},
        "spec": {"syncPolicy": sync_policy},
    }


def main() -> None:
    module = AnsibleModule(argument_spec={}, supports_check_mode=True)
    module.exit_json(changed=False)


if __name__ == "__main__":
    main()
