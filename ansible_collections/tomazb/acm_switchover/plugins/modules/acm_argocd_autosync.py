from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule


def build_pause_patch(sync_policy: dict, run_id: str) -> dict:
    sync_policy = dict(sync_policy)
    sync_policy.pop("automated", None)
    return {
        "metadata": {"annotations": {"acm-switchover.argoproj.io/paused-by": run_id}},
        "spec": {"syncPolicy": sync_policy},
    }


def main() -> None:
    module = AnsibleModule(argument_spec={}, supports_check_mode=True)
    module.fail_json(msg="acm_argocd_autosync module is not yet fully implemented. "
                      "The build_pause_patch() function exists but is not called. "
                      "Use the argocd_manage role for Argo CD auto-sync operations.")


if __name__ == "__main__":
    main()
