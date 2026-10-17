# Legacy klusterlet checkpoint provenance

`legacy_klusterlet_checkpoint.json` is synthetic, non-live evidence for
[#352](https://github.com/tomazb/rh-acm-switchover/issues/352). The investigator
produced it on 2026-10-16 by running the actual `modules/post_activation.py` from
commit `6a71c25a1a74111ed561f7a8d21480bc7773de94` with current supporting libraries.
The historical gate silently returned for a deliberately missing kubeconfig.
Real `StepContext` persisted the unversioned marker. A later synthetic cleanup
failure left a failed post-activation checkpoint without journal completion.

The schema-2 completed-Restore journal came from the checked-in R4-04 evidence
vector `post-activation-journal-completed`. Hub identities, endpoints, names and
credentials are synthetic. This demonstrates historical verifier/step behavior
over a compatible validated journal, not certification of an entire historical
release. Released journal-free states through 1.7.10 already fail closed on
current execute-mode entry and are covered separately.

The regression copies this file to disk, reloads it through current
`StateManager`, binds current synthetic hub UIDs, and uses the failed-state
runner and actual CLI post-activation adapter. The gate, endpoint decisions,
snapshots, official client construction, step context and journal writes stay
real. Only transport and bounded polling use fakes. No historical production
module is vendored into the tests.
