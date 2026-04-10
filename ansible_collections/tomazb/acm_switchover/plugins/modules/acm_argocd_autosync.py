from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.argocd import (
    ACM_KINDS,
    ACM_NAMESPACES,
    is_acm_touching_application,
)


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
