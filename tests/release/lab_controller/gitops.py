from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from .artifacts import sanitize_artifact_payload, sanitize_artifact_text
from .models import (
    ApplicationSetOwnershipEvidence,
    ArgoCDApplicationEvidence,
    ArgoCDInterferenceMode,
    CoordinationStrategy,
    GitOpsCapabilityEvidence,
    GitOpsOwnershipEvidence,
    GitOpsRiskDecision,
    GitOpsTrackedResource,
    SegmentDecision,
)

TRACKING_ID_ANNOTATION = "argocd.argoproj.io/tracking-id"
ACM_OBJECT_LABEL = "acm-switchover.redhat-lab/acm-object"
ACM_NAMESPACES = {"open-cluster-management", "open-cluster-management-backup"}
APPLICATION_KIND = "Application"
APPLICATIONSET_KIND = "ApplicationSet"
AUTOMATED_ENABLED_FIELD_PATH = "spec.syncPolicy.automated.enabled"


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _metadata(doc: Mapping[str, Any]) -> dict[str, Any]:
    return _as_mapping(doc.get("metadata"))


def _labels(doc: Mapping[str, Any]) -> dict[str, Any]:
    return _as_mapping(_metadata(doc).get("labels"))


def _annotations(doc: Mapping[str, Any]) -> dict[str, Any]:
    return _as_mapping(_metadata(doc).get("annotations"))


def _resource_group(api_version: Any) -> str:
    if not isinstance(api_version, str) or "/" not in api_version:
        return ""
    return api_version.split("/", 1)[0]


def _resource_key(group: str, kind: str, namespace: str | None, name: str) -> tuple[str, str, str | None, str]:
    return group, kind, namespace, name


def _tracking_application(tracking_id: str | None) -> str | None:
    if not tracking_id or ":" not in tracking_id:
        return None
    app_name = tracking_id.split(":", 1)[0].strip()
    return app_name or None


def _doc_resource(doc: Mapping[str, Any]) -> GitOpsTrackedResource | None:
    kind = doc.get("kind")
    metadata = _metadata(doc)
    name = metadata.get("name")
    if not isinstance(kind, str) or not isinstance(name, str):
        return None
    namespace = metadata.get("namespace")
    namespace_text = namespace if isinstance(namespace, str) else None
    tracking_id = _annotations(doc).get(TRACKING_ID_ANNOTATION)
    tracking_text = tracking_id if isinstance(tracking_id, str) else None
    labels = _labels(doc)
    acm_object = bool(labels.get(ACM_OBJECT_LABEL)) or namespace_text in ACM_NAMESPACES
    return GitOpsTrackedResource(
        group=_resource_group(doc.get("apiVersion")),
        kind=kind,
        namespace=namespace_text,
        name=name,
        tracking_id=tracking_text,
        owning_application=_tracking_application(tracking_text),
        acm_object=acm_object,
    )


def _application_status_resources(doc: Mapping[str, Any]) -> tuple[GitOpsTrackedResource, ...]:
    resources = []
    for item in _as_list(_as_mapping(doc.get("status")).get("resources")):
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        name = item.get("name")
        if not isinstance(kind, str) or not isinstance(name, str):
            continue
        group = item.get("group", "")
        namespace = item.get("namespace")
        namespace_text = namespace if isinstance(namespace, str) else None
        resources.append(
            GitOpsTrackedResource(
                group=group if isinstance(group, str) else "",
                kind=kind,
                namespace=namespace_text,
                name=name,
                acm_object=namespace_text in ACM_NAMESPACES,
            )
        )
    return tuple(resources)


def _resource_paths(kustomization_dir: Path, root: Path, seen: set[Path] | None = None) -> tuple[Path, ...]:
    seen = seen or set()
    kustomization = (kustomization_dir / "kustomization.yaml").resolve()
    if not kustomization.exists():
        raise ValueError(f"{kustomization_dir} is missing kustomization.yaml")
    if kustomization in seen:
        return ()
    seen.add(kustomization)

    raw = yaml.safe_load(kustomization.read_text(encoding="utf-8"))
    resources = _as_mapping(raw).get("resources", ())
    if not isinstance(resources, list):
        raise ValueError(f"{kustomization} resources must be a list")

    resolved: list[Path] = []
    for resource in resources:
        if not isinstance(resource, str):
            raise ValueError(f"{kustomization} contains a non-string resource entry")
        resource_path = (kustomization_dir / resource).resolve()
        if not resource_path.is_relative_to(root):
            raise ValueError(f"{resource} escapes the fixture tree")
        if not resource_path.exists():
            raise ValueError(f"{kustomization} references missing resource {resource}")
        if resource_path.is_dir():
            resolved.extend(_resource_paths(resource_path, root, seen))
        else:
            resolved.append(resource_path)
    return tuple(resolved)


def _yaml_docs(paths: Iterable[Path]) -> tuple[dict[str, Any], ...]:
    docs: list[dict[str, Any]] = []
    for path in paths:
        if path.suffix not in {".yaml", ".yml"}:
            continue
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if doc is None:
                continue
            if not isinstance(doc, dict):
                raise ValueError(f"{path} contains a non-mapping YAML document")
            docs.append(doc)
    return tuple(docs)


def _child_appset_parent(doc: Mapping[str, Any]) -> tuple[str | None, bool]:
    for owner_ref in _as_list(_metadata(doc).get("ownerReferences")):
        if not isinstance(owner_ref, dict) or owner_ref.get("kind") != APPLICATIONSET_KIND:
            continue
        name = owner_ref.get("name")
        uid_present = isinstance(owner_ref.get("uid"), str)
        return name if isinstance(name, str) else None, uid_present
    return None, False


def _resource_lookup(
    docs: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str, str | None, str], GitOpsTrackedResource]:
    lookup: dict[tuple[str, str, str | None, str], GitOpsTrackedResource] = {}
    for doc in docs:
        if doc.get("kind") in {"Kustomization", APPLICATION_KIND, APPLICATIONSET_KIND}:
            continue
        resource = _doc_resource(doc)
        if resource is None:
            continue
        lookup[_resource_key(resource.group, resource.kind, resource.namespace, resource.name)] = resource
    return lookup


def load_gitops_ownership_from_fixture(
    kustomization_dir: Path,
    *,
    automated_enabled_capability: GitOpsCapabilityEvidence | None = None,
    coordinated_appsets: Sequence[str] = (),
) -> GitOpsOwnershipEvidence:
    """Load deterministic GitOps ownership evidence from checked-in release-lab fixture YAML."""
    resolved_dir = kustomization_dir.resolve()
    root = resolved_dir.parents[2]
    docs = _yaml_docs(_resource_paths(resolved_dir, root))
    resource_lookup = _resource_lookup(docs)
    coordinated = set(coordinated_appsets)

    application_sets = []
    for doc in docs:
        if doc.get("kind") != APPLICATIONSET_KIND:
            continue
        metadata = _metadata(doc)
        name = metadata.get("name")
        if not isinstance(name, str):
            continue
        application_sets.append(
            ApplicationSetOwnershipEvidence(
                name=name,
                namespace=metadata.get("namespace") if isinstance(metadata.get("namespace"), str) else None,
                child_applications=(),
                parent_level_coordination=name in coordinated,
                owner_uid_identity_evidence="not_used",
            )
        )

    applications = []
    tracked_resources: list[GitOpsTrackedResource] = []
    for doc in docs:
        if doc.get("kind") != APPLICATION_KIND:
            continue
        metadata = _metadata(doc)
        name = metadata.get("name")
        if not isinstance(name, str):
            continue
        parent_name, owner_uid_present = _child_appset_parent(doc)
        status_resources = []
        for status_resource in _application_status_resources(doc):
            matched = resource_lookup.get(
                _resource_key(
                    status_resource.group,
                    status_resource.kind,
                    status_resource.namespace,
                    status_resource.name,
                )
            )
            resource = matched or status_resource
            resource = GitOpsTrackedResource(
                group=resource.group,
                kind=resource.kind,
                namespace=resource.namespace,
                name=resource.name,
                tracking_id=resource.tracking_id,
                owning_application=name,
                acm_object=resource.acm_object,
            )
            status_resources.append(resource)
            if resource.acm_object:
                tracked_resources.append(resource)
        applications.append(
            ArgoCDApplicationEvidence(
                name=name,
                namespace=metadata.get("namespace") if isinstance(metadata.get("namespace"), str) else None,
                owns_acm_resources=any(resource.acm_object for resource in status_resources),
                tracked_resources=tuple(status_resources),
                sync_policy=_as_mapping(_as_mapping(doc.get("spec")).get("syncPolicy")),
                sync_options=tuple(
                    option
                    for option in _as_list(
                        _as_mapping(_as_mapping(doc.get("spec")).get("syncPolicy")).get("syncOptions")
                    )
                    if isinstance(option, str)
                ),
                applicationset_parent=parent_name,
                applicationset_owner_uid_present=owner_uid_present,
                applicationset_owner_uid_is_identity_evidence=False,
            )
        )

    children_by_parent: dict[str, list[str]] = {}
    for application in applications:
        if application.applicationset_parent:
            children_by_parent.setdefault(application.applicationset_parent, []).append(application.name)
    application_sets = [
        ApplicationSetOwnershipEvidence(
            name=appset.name,
            namespace=appset.namespace,
            child_applications=tuple(sorted(children_by_parent.get(appset.name, ()))),
            parent_level_coordination=appset.parent_level_coordination,
            owner_uid_identity_evidence="not_used",
        )
        for appset in application_sets
    ]

    return GitOpsOwnershipEvidence(
        evaluated=True,
        source=resolved_dir.relative_to(root).as_posix(),
        applications=tuple(applications),
        application_sets=tuple(application_sets),
        tracked_resources=tuple(tracked_resources),
        automated_enabled_capability=automated_enabled_capability or GitOpsCapabilityEvidence.unknown(),
    )


def _nested_path_exists(value: Mapping[str, Any], path: Sequence[str]) -> bool:
    current: Any = value
    for item in path:
        current = _as_mapping(current).get(item)
        if current is None:
            return False
    return True


def load_automated_enabled_capability_from_crd(path: Path) -> GitOpsCapabilityEvidence:
    """Extract automated.enabled support from deterministic CRD schema evidence."""
    docs = _yaml_docs((path,))
    if not docs:
        return GitOpsCapabilityEvidence(
            automated_enabled_supported=None,
            source="crd_schema",
            detail="CRD schema evidence file was empty",
        )
    crd = docs[0]
    versions = _as_list(_as_mapping(crd.get("spec")).get("versions"))
    supported = False
    for version in versions:
        schema = _as_mapping(_as_mapping(version).get("schema")).get("openAPIV3Schema")
        if not isinstance(schema, dict):
            continue
        if _nested_path_exists(
            schema,
            (
                "properties",
                "spec",
                "properties",
                "syncPolicy",
                "properties",
                "automated",
                "properties",
                "enabled",
            ),
        ):
            supported = True
            break
    return GitOpsCapabilityEvidence(
        automated_enabled_supported=supported,
        source="crd_schema",
        detail=(
            "Application CRD schema contains automated.enabled"
            if supported
            else "Application CRD schema does not contain automated.enabled"
        ),
    )


def _pass(mode: ArgoCDInterferenceMode, strategy: CoordinationStrategy, reason: str) -> GitOpsRiskDecision:
    return GitOpsRiskDecision(
        decision=SegmentDecision.PASS,
        safe_to_continue=True,
        interference_mode=mode,
        coordination_strategy=strategy,
        reason=reason,
    )


def _block(
    mode: ArgoCDInterferenceMode,
    strategy: CoordinationStrategy,
    reason: str,
) -> GitOpsRiskDecision:
    return GitOpsRiskDecision(
        decision=SegmentDecision.NO_GO,
        safe_to_continue=False,
        interference_mode=mode,
        coordination_strategy=strategy,
        reason=reason,
        blocking_reason=reason,
    )


def _automated_policy(sync_policy: Mapping[str, Any]) -> tuple[bool, Mapping[str, Any] | None, str | None]:
    automated = sync_policy.get("automated")
    if automated is None:
        return False, None, None
    if not isinstance(automated, Mapping):
        return False, None, "spec.syncPolicy.automated must be a mapping when present"
    return True, automated, None


def _enabled_false_is_supported(capability: GitOpsCapabilityEvidence) -> bool:
    return capability.automated_enabled_supported is True


def _application_interference(
    application: ArgoCDApplicationEvidence,
    capability: GitOpsCapabilityEvidence,
) -> GitOpsRiskDecision | None:
    sync_policy = application.sync_policy
    if not isinstance(sync_policy, Mapping):
        return _block(
            ArgoCDInterferenceMode.UNKNOWN,
            CoordinationStrategy.BLOCKED_UNKNOWN,
            f"GitOps ownership evidence for Application {application.name} has malformed syncPolicy",
        )

    automated_present, automated, malformed_reason = _automated_policy(sync_policy)
    if malformed_reason is not None:
        return _block(ArgoCDInterferenceMode.UNKNOWN, CoordinationStrategy.BLOCKED_UNKNOWN, malformed_reason)
    if not automated_present or automated is None:
        return None

    enabled = automated.get("enabled")
    if enabled is False:
        if not _enabled_false_is_supported(capability):
            return _block(
                ArgoCDInterferenceMode.UNKNOWN,
                CoordinationStrategy.BLOCKED_UNKNOWN,
                "GitOps decision would require spec.syncPolicy.automated.enabled support, but capability evidence "
                "is unknown or unsupported",
            )
        return None
    if enabled not in {None, True}:
        return _block(
            ArgoCDInterferenceMode.UNKNOWN,
            CoordinationStrategy.BLOCKED_UNKNOWN,
            "spec.syncPolicy.automated.enabled must be true, false, or null when present",
        )
    if automated.get("selfHeal") is True:
        return _block(
            ArgoCDInterferenceMode.AUTOMATED_SELF_HEAL,
            CoordinationStrategy.APPLICATION_COORDINATION_REQUIRED,
            f"Application {application.name} owns ACM resources with automated selfHeal enabled",
        )
    if automated.get("prune") is True:
        return _block(
            ArgoCDInterferenceMode.AUTOMATED_PRUNE,
            CoordinationStrategy.APPLICATION_COORDINATION_REQUIRED,
            f"Application {application.name} owns ACM resources with automated prune enabled",
        )
    return _block(
        ArgoCDInterferenceMode.AUTOMATED_SYNC,
        CoordinationStrategy.APPLICATION_COORDINATION_REQUIRED,
        f"Application {application.name} owns ACM resources with automated sync enabled",
    )


def classify_gitops_ownership(evidence: GitOpsOwnershipEvidence) -> GitOpsRiskDecision:
    """Classify deterministic GitOps ownership evidence and fail closed on ambiguity."""
    if evidence.live_certification_evidence:
        return _block(
            ArgoCDInterferenceMode.UNKNOWN,
            CoordinationStrategy.BLOCKED_UNKNOWN,
            "GitOps fixture evidence must not claim live certification evidence",
        )
    if not evidence.evaluated:
        return _pass(
            ArgoCDInterferenceMode.NOT_OWNED,
            CoordinationStrategy.NOT_REQUIRED,
            evidence.unknown_reason or "GitOps evidence was not evaluated",
        )
    if evidence.unknown_reason:
        return _block(
            ArgoCDInterferenceMode.UNKNOWN,
            CoordinationStrategy.BLOCKED_UNKNOWN,
            f"GitOps ownership evidence is unknown: {evidence.unknown_reason}",
        )

    owning_applications = tuple(application for application in evidence.applications if application.owns_acm_resources)
    if not owning_applications:
        if evidence.applications:
            return _pass(
                ArgoCDInterferenceMode.OBSERVE_ONLY,
                CoordinationStrategy.OBSERVE_ONLY,
                "Argo CD Applications are present but do not own ACM resources",
            )
        return _pass(
            ArgoCDInterferenceMode.NOT_OWNED,
            CoordinationStrategy.NOT_REQUIRED,
            "no Argo CD tracked ACM resources were found",
        )

    parent_coordination = {appset.name: appset.parent_level_coordination for appset in evidence.application_sets}
    appset_children = tuple(application for application in owning_applications if application.applicationset_parent)
    uncoordinated_child = next(
        (
            application
            for application in appset_children
            if not parent_coordination.get(str(application.applicationset_parent), False)
        ),
        None,
    )
    if uncoordinated_child is not None:
        return _block(
            ArgoCDInterferenceMode.APPLICATIONSET_CHILD,
            CoordinationStrategy.APPLICATIONSET_PARENT_COORDINATION_REQUIRED,
            "ApplicationSet child Application owns ACM resources without parent-level coordination evidence",
        )

    for application in owning_applications:
        decision = _application_interference(application, evidence.automated_enabled_capability)
        if decision is not None:
            return decision

    if appset_children:
        return _pass(
            ArgoCDInterferenceMode.APPLICATIONSET_CHILD,
            CoordinationStrategy.PARENT_LEVEL_COORDINATION,
            "ApplicationSet child ACM ownership has explicit parent-level coordination evidence",
        )
    return _pass(
        ArgoCDInterferenceMode.OWNED_AUTOSYNC_OFF,
        CoordinationStrategy.NOT_REQUIRED,
        "ACM resources are owned by Argo CD Applications without automated sync",
    )


def _sync_policy_summary(application: ArgoCDApplicationEvidence) -> dict[str, Any]:
    sync_policy = application.sync_policy if isinstance(application.sync_policy, Mapping) else {}
    automated = sync_policy.get("automated")
    automated_mapping = automated if isinstance(automated, Mapping) else {}
    return {
        "automated_present": "automated" in sync_policy,
        "automated_enabled": automated_mapping.get("enabled"),
        "prune": automated_mapping.get("prune") is True,
        "selfHeal": automated_mapping.get("selfHeal") is True,
        "sync_options": list(application.sync_options),
    }


def build_gitops_artifact_summary(
    evidence: GitOpsOwnershipEvidence,
    decision: GitOpsRiskDecision,
) -> dict[str, Any]:
    """Build a provisional redaction-safe GitOps artifact section."""
    payload = {
        "evaluated": evidence.evaluated,
        "source": sanitize_artifact_text(evidence.source),
        "tracked_acm_resources": [
            {
                "ref": sanitize_artifact_text(resource.ref),
                "kind": resource.kind,
                "namespace": sanitize_artifact_text(resource.namespace),
                "name": sanitize_artifact_text(resource.name),
                "owning_application": sanitize_artifact_text(resource.owning_application),
                "tracking_method": "annotation" if resource.tracking_id else "status",
            }
            for resource in evidence.tracked_resources
        ],
        "application_evidence": [
            {
                "name": sanitize_artifact_text(application.name),
                "namespace": sanitize_artifact_text(application.namespace),
                "owns_acm_resources": application.owns_acm_resources,
                "tracked_acm_resource_count": sum(
                    1 for resource in application.tracked_resources if resource.acm_object
                ),
                "sync_policy": _sync_policy_summary(application),
                "applicationset_parent": sanitize_artifact_text(application.applicationset_parent),
                "applicationset_owner_uid_present": application.applicationset_owner_uid_present,
                "applicationset_owner_uid_identity_evidence": application.applicationset_owner_uid_is_identity_evidence,
            }
            for application in evidence.applications
        ],
        "application_set_evidence": [
            {
                "name": sanitize_artifact_text(appset.name),
                "namespace": sanitize_artifact_text(appset.namespace),
                "child_applications": [sanitize_artifact_text(child) for child in appset.child_applications],
                "parent_level_coordination": appset.parent_level_coordination,
                "owner_uid_identity_evidence": appset.owner_uid_identity_evidence,
            }
            for appset in evidence.application_sets
        ],
        "sync_policy_classification": {
            "interference_mode": decision.interference_mode.value,
            "coordination_strategy": decision.coordination_strategy.value,
        },
        "automated_enabled_capability": {
            "source": evidence.automated_enabled_capability.source,
            "automated_enabled_supported": evidence.automated_enabled_capability.automated_enabled_supported,
            "field_path": evidence.automated_enabled_capability.field_path,
            "detail": sanitize_artifact_text(evidence.automated_enabled_capability.detail),
        },
        "coordination_strategy": decision.coordination_strategy.value,
        "final_decision": decision.decision.name,
        "safe_to_continue": decision.safe_to_continue,
        "blocking_reason": sanitize_artifact_text(decision.blocking_reason),
        "reason": sanitize_artifact_text(decision.reason),
        "live_certification_evidence": False,
        "not_live_acm_certification_evidence": True,
    }
    return sanitize_artifact_payload(payload)
