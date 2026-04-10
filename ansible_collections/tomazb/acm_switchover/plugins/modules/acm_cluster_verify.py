"""Managed cluster group verification helpers."""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule


def summarize_cluster_group(clusters: list[dict], min_managed_clusters: int) -> dict:
    pending = [item["name"] for item in clusters if not (item["joined"] and item["available"])]
    return {
        "passed": len(clusters) >= min_managed_clusters and not pending,
        "total": len(clusters),
        "pending": pending,
    }


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "cluster_status": {"type": "list", "elements": "dict", "default": []},
            "min_managed_clusters": {"type": "int", "default": 1},
        },
        supports_check_mode=True,
    )
    result = summarize_cluster_group(
        module.params["cluster_status"],
        module.params["min_managed_clusters"],
    )
    module.exit_json(changed=False, **result)


if __name__ == "__main__":
    main()
