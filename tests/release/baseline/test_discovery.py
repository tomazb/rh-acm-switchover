from tests.release.baseline.discovery import HubDiscoveryClient, discover_hub_facts


class FakeHubDiscoveryClient(HubDiscoveryClient):
    def __init__(self) -> None:
        self.resources = {
            "multiclusterhubs": [
                {
                    "metadata": {"name": "multiclusterhub"},
                    "status": {"currentVersion": "2.12.0"},
                }
            ],
            "backupschedules": [
                {
                    "metadata": {"name": "acm-backup"},
                    "spec": {"paused": False},
                }
            ],
            "restores": [
                {
                    "metadata": {"name": "restore-primary"},
                    "status": {"phase": "Finished"},
                    "spec": {"syncRestoreWithNewBackups": True},
                }
            ],
            "managedclusters": [
                {"metadata": {"name": "cluster-a"}},
                {"metadata": {"name": "cluster-b"}},
            ],
            "applications.argoproj.io": [
                {
                    "metadata": {"name": "acm-app", "namespace": "openshift-gitops"},
                }
            ],
        }

    def list_resources(self, resource: str, namespace: str | None = None) -> list[dict]:
        # namespace is intentionally ignored; all resources are namespace-agnostic in this fake
        return self.resources.get(resource, [])


def test_discover_hub_facts_normalizes_core_fields() -> None:
    facts = discover_hub_facts(
        client=FakeHubDiscoveryClient(),
        context="primary",
        acm_namespace="open-cluster-management",
        argocd_namespaces=("openshift-gitops",),
    )

    assert facts.context == "primary"
    assert facts.acm_version == "2.12.0"
    assert facts.hub_role == "primary"
    assert facts.backup_schedule["present"] is True
    assert facts.restore["sync_restore_enabled"] is True
    assert facts.managed_cluster_names == ("cluster-a", "cluster-b")
    assert facts.argocd["application_count"] == 1


def test_discover_hub_facts_ignores_malformed_managed_clusters() -> None:
    client = FakeHubDiscoveryClient()
    client.resources["managedclusters"] = [
        {"metadata": {"name": "cluster-a"}},
        {"metadata": {}},
        {},
        {"metadata": {"name": "cluster-b"}},
    ]

    facts = discover_hub_facts(
        client=client,
        context="primary",
        acm_namespace="open-cluster-management",
        argocd_namespaces=("openshift-gitops",),
    )

    assert facts.managed_cluster_names == ("cluster-a", "cluster-b")
