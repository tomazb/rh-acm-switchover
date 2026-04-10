"""ManagedCluster condition summarization helpers."""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule


def summarize_cluster(cluster: dict) -> dict:
    conditions = cluster.get("status", {}).get("conditions", [])
    return {
        "name": cluster.get("metadata", {}).get("name", "unknown"),
        "joined": any(item.get("type") == "ManagedClusterJoined" and item.get("status") == "True" for item in conditions),
        "available": any(
            item.get("type") == "ManagedClusterConditionAvailable" and item.get("status") == "True"
            for item in conditions
        ),
    }


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "clusters": {"type": "list", "elements": "dict", "default": []},
        },
        supports_check_mode=True,
    )
    cluster_status = [summarize_cluster(c) for c in module.params["clusters"]]
    module.exit_json(changed=False, cluster_status=cluster_status)


if __name__ == "__main__":
    main()
