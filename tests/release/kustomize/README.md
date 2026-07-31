# Release Lab Kustomize Fixtures

This tree provides deterministic static fixtures for a release lab shaped as 2 hubs and 3 managed SNO clusters:
`hub-a`, `hub-b`, `mc-1`, `mc-2`, and `mc-3`.

The hub and managed-cluster names are operator-facing labels only. They are not identity proof and must not be treated
as replacements for live identity evidence such as cluster UID, API endpoint fingerprint, or controller discovery.

These fixtures are not live ACM certification evidence. They do not authorize live mutation, do not apply production
switchover behavior, and do not enable decommission paths. They are meant for static release-lab guardrails and future
controller dry-run integration only.

## Apply Model

Each overlay is intended to be rendered per cluster context by an operator-controlled harness, not applied by this PR:

- `overlays/hubs/hub-a-primary` and `overlays/hubs/hub-a-secondary` model `hub-a` in each logical role.
- `overlays/hubs/hub-b-primary` and `overlays/hubs/hub-b-secondary` model `hub-b` in each logical role.
- `overlays/managed/mc-1`, `overlays/managed/mc-2`, and `overlays/managed/mc-3` model managed SNO labels.
- `overlays/scenarios/*` model GitOps interference modes for later controller tests.

All scenario manifests are static YAML. Kustomize rendering can be checked locally, but server-side live validation is Phase 9 work because many ACM and Argo CD resource kinds may not have CRDs in CI.

## GitOps Modes

- `gitops-observe-only`: Argo CD exists, but the fixture Application does not own ACM-like resources.
- `gitops-owns-acm-autosync-off`: Argo CD owns ACM-like fixture resources with automated sync explicitly disabled.
- `gitops-owns-acm-selfheal-on`: hostile fixture with automated self-heal enabled.
- `gitops-owns-acm-prune-on`: hostile fixture with automated prune enabled.
- `gitops-owns-acm-appset-child`: ApplicationSet child Application ownership case.
- `gitops-pause-required-before-switchover`: hostile fixture combining ACM ownership signals that require GitOps pause.

Safe modes should be used for static discovery and dry-run planning. Hostile modes are intentionally labeled and isolated
under hostile scenario overlays. They are not safe defaults and must not be applied to production clusters.

The autosync-off fixture intentionally omits `spec.syncPolicy.automated` instead of using
`spec.syncPolicy.automated.enabled: false`. The ACM 2.12+ test target spans OpenShift GitOps / Argo CD combinations
where that field may not exist, so the generic safe fixture uses the lowest-common-denominator representation. Phase
8P/8Q controller work must detect this capability from live CRD/schema or version evidence before relying on
`automated.enabled`. `prune` and `selfHeal` are modeled only in explicit hostile fixtures.

## Fixture Safety

The fixtures use Namespace and ConfigMap sentinels where possible. ACM-like objects are represented as ConfigMap data so
CI can parse and statically inspect them without CRDs or API-server validation. Selected ACM-like ConfigMaps include
`argocd.argoproj.io/tracking-id` annotations so future controller work can test Argo CD resource tracking discovery.

The tree intentionally excludes real API endpoints, cluster IDs, live import assets, pull assets, S3 storage settings,
and backup storage settings. Placeholder Git repository references use `example.invalid`.

## Controller Consumption

Phase 8P/8Q consumes these checked-in manifests as deterministic static inputs for lab-controller GitOps ownership
evidence and dry-run/materialized artifact summaries. The controller resolves Kustomization `resources` entries and
parses YAML only; it does not shell out to Kustomize, contact a cluster, discover live Argo CD CRDs, or validate these
resources server-side. Capability evidence for `spec.syncPolicy.automated.enabled` is explicit non-live evidence, not an
ACM/OpenShift GitOps version assumption. Phase 9 remains responsible for live CRD/schema and server-side validation.

## Follow-Up Boundary

Phase 8P/8Q wires these fixtures into the lab controller model and dry-run artifact flow as provisional, non-live
evidence only. Live Argo CD pause/resume behavior, production switchover runtime changes, and live ACM certification
evidence remain outside this fixture tree.
