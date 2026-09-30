"""Generate tests/fixtures/r4_04_migration_evidence_vectors.json.

Run ``python tests/fixtures/r4_04_migration_evidence_vectors_gen.py >
tests/fixtures/r4_04_migration_evidence_vectors.json`` after changing a vector.
The JSON is committed; tests/test_migration_evidence_parity.py requires it to
equal build() byte for byte, so the fixture is never hand-edited.
"""

import copy
import hashlib
import json
import sys

NS = "open-cluster-management-backup"
ALL = ["2.12", "2.13", "2.14", "2.15", "2.16", "2.17"]
LEGACY = ALL[:5]
ABSENT = object()

LANES = {
    "2.12": ("74b54988a5bd6712ea3fe3e9ceb770e06db91e8b", "legacy_2_12_2_16"),
    "2.13": ("7a7b240b3df71105da3f15620e4116498f9e2a23", "legacy_2_12_2_16"),
    "2.14": ("8b489db488739e7d9adca50cb3be0eae79293f22", "legacy_2_12_2_16"),
    "2.15": ("25b28b762355a14b4fb7f145efe173f73659e740", "legacy_2_12_2_16"),
    "2.16": ("9efe77eaec2139f106c957051e2297dafc84b482", "legacy_2_12_2_16"),
    "2.17": ("c8578f94df09deab561e1aa5a7e9fc9b57f7d113", "active_2_17"),
}

# Per-lane pinned source ranges.
TOKENS = {"2.12": "58-65", "2.13": "58-65", "2.14": "65-72", "2.15": "65-72", "2.16": "65-72", "2.17": "65-72"}
LATEST = {
    "2.12": "502-529",
    "2.13": "502-529",
    "2.14": "509-536",
    "2.15": "509-536",
    "2.16": "509-536",
    "2.17": "597-624",
}
SORT = {
    "2.12": "385-386",
    "2.13": "385-386",
    "2.14": "488-489",
    "2.15": "488-489",
    "2.16": "488-489",
    "2.17": "611-612",
}
CORRELATED = {
    "2.12": "537-587",
    "2.13": "537-587",
    "2.14": "544-594",
    "2.15": "544-594",
    "2.16": "544-594",
    "2.17": "630-674",
}
TIMESTAMP = {
    "2.12": "132-139",
    "2.13": "132-139",
    "2.14": "141-148",
    "2.15": "140-147",
    "2.16": "140-147",
    "2.17": "140-147",
}


def refs(lanes, *kinds):
    out = []
    for lane in lanes:
        for kind in kinds:
            if kind == "tokens":
                out.append(f"{lane}:restore.go:{TOKENS[lane]}")
            elif kind == "latest":
                out.append(f"{lane}:restore.go:{LATEST[lane]}")
            elif kind == "sort":
                out.append(f"{lane}:restore_controller.go:{SORT[lane]}")
            elif kind == "correlated":
                out.append(f"{lane}:restore.go:{CORRELATED[lane]}")
            elif kind == "timestamp":
                out.append(f"{lane}:utils.go:{TIMESTAMP[lane]}")
    return out


def backup(
    name,
    phase="Completed",
    start="2024-01-01T12:00:00Z",
    completion="2024-01-01T12:05:00Z",
    uid=None,
    namespace=NS,
    errors=ABSENT,
    warnings=ABSENT,
):
    status = {}
    if phase is not ABSENT:
        status["phase"] = phase
    if start is not ABSENT:
        status["startTimestamp"] = start
    if completion is not ABSENT:
        status["completionTimestamp"] = completion
    if errors is not ABSENT:
        status["errors"] = errors
    if warnings is not ABSENT:
        status["warnings"] = warnings
    metadata = {"name": name, "uid": uid if uid is not None else f"uid-{name}"}
    if namespace is not ABSENT:
        metadata["namespace"] = namespace
    return {"metadata": metadata, "status": status}


def projection(raw, errors=0, warnings=0):
    return {
        "namespace": NS,
        "name": raw["metadata"]["name"],
        "uid": raw["metadata"]["uid"],
        "phase": "Completed",
        "completed_at": raw["status"]["completionTimestamp"],
        "errors": errors,
        "warnings": warnings,
    }


cases = []


def case(case_id, lanes, source_refs, function, inp, expect, r4_stricter=False):
    cases.append(
        {
            "id": case_id,
            "lanes": lanes,
            "source_refs": source_refs,
            "function": function,
            "input": inp,
            "expect": expect,
            "r4_stricter": r4_stricter,
        }
    )


def ok(result):
    return {"result": result}


def err(code):
    return {"error_code": code}


# --- controller contract matrix (August §5) ---------------------------------------------
for minor, (_, contract) in LANES.items():
    case(
        f"contract-{minor}",
        [minor],
        [],
        "controller_contract_for_acm_minor",
        {"minor": minor},
        ok(contract),
    )
for label, minor in [
    ("future", "2.18"),
    ("older", "2.11"),
    ("patch-level", "2.12.3"),
    ("empty", ""),
    ("float", 2.12),
    ("null", None),
]:
    case(
        f"contract-unknown-{label}",
        [],
        [],
        "controller_contract_for_acm_minor",
        {"minor": minor},
        err("unknown_acm_minor"),
        True,
    )

# --- seven-field projection and eligibility (August §3) ---------------------------------
good = backup("acm-managed-clusters-schedule-20240101120000")
fn = "normalize_backup_evidence"
case("normalize-absent-counters-are-zero", ALL, [], fn, {"raw": good, "namespace": NS}, ok(projection(good)))
raw = backup("acm-managed-clusters-schedule-20240101120000", errors=0, warnings=0)
case("normalize-present-zero-counters-kept", ALL, [], fn, {"raw": raw, "namespace": NS}, ok(projection(raw)))
raw = backup("acm-managed-clusters-schedule-20240101120000", errors=0, warnings=7)
case("normalize-positive-warnings-kept", ALL, [], fn, {"raw": raw, "namespace": NS}, ok(projection(raw, 0, 7)))
raw = backup("acm-managed-clusters-schedule-20240101120000", completion="2024-01-01T14:05:00.25+02:00")
case("normalize-offset-completion-kept-verbatim", ALL, [], fn, {"raw": raw, "namespace": NS}, ok(projection(raw)))
raw = backup("acm-managed-clusters-schedule-20240101120000", errors=2)
case("normalize-errors-positive-rejected", ALL, [], fn, {"raw": raw, "namespace": NS}, err("backup_has_errors"), True)
for counter in ("errors", "warnings"):
    for label, value in [
        ("null", None),
        ("true", True),
        ("false", False),
        ("string", "0"),
        ("float", 0.0),
        ("negative", -1),
        ("list", []),
    ]:
        raw = backup("acm-managed-clusters-schedule-20240101120000", **{counter: value})
        case(
            f"normalize-{counter}-{label}-rejected",
            ALL,
            [],
            fn,
            {"raw": raw, "namespace": NS},
            err("malformed_backup_counter"),
            True,
        )
for label, phase in [("partially-failed", "PartiallyFailed"), ("failed", "Failed"), ("in-progress", "InProgress")]:
    raw = backup("acm-managed-clusters-schedule-20240101120000", phase=phase)
    case(
        f"normalize-phase-{label}-rejected",
        ALL,
        [],
        fn,
        {"raw": raw, "namespace": NS},
        err("backup_not_completed"),
        True,
    )
raw = backup("acm-managed-clusters-schedule-20240101120000", phase=ABSENT)
case("normalize-phase-absent-rejected", ALL, [], fn, {"raw": raw, "namespace": NS}, err("backup_not_completed"), True)
raw = backup("acm-managed-clusters-schedule-20240101120000", namespace="velero")
case(
    "normalize-namespace-mismatch-rejected",
    ALL,
    [],
    fn,
    {"raw": raw, "namespace": NS},
    err("backup_namespace_mismatch"),
    True,
)
raw = backup("acm-managed-clusters-schedule-20240101120000", namespace=ABSENT)
case(
    "normalize-namespace-absent-rejected",
    ALL,
    [],
    fn,
    {"raw": raw, "namespace": NS},
    err("backup_namespace_mismatch"),
    True,
)
for label, value in [
    ("absent", ABSENT),
    ("null", None),
    ("space-separator", "2024-01-01 12:05:00Z"),
    ("no-zone", "2024-01-01T12:05:00"),
    ("lowercase-zone", "2024-01-01T12:05:00z"),
    ("date-only", "2024-01-01"),
    ("out-of-range", "2024-13-01T12:05:00Z"),
    ("integer", 1704110700),
    ("empty", ""),
]:
    raw = backup("acm-managed-clusters-schedule-20240101120000", completion=value)
    case(
        f"normalize-completion-{label}-rejected",
        ALL,
        [],
        fn,
        {"raw": raw, "namespace": NS},
        err("malformed_completion_timestamp"),
        True,
    )
raw = backup("", uid="uid-1")
case("normalize-empty-name-rejected", ALL, [], fn, {"raw": raw, "namespace": NS}, err("malformed_backup"), True)
raw = backup("acm-managed-clusters-schedule-20240101120000")
raw["metadata"]["uid"] = ""
case("normalize-empty-uid-rejected", ALL, [], fn, {"raw": raw, "namespace": NS}, err("malformed_backup"), True)
raw = backup("acm-managed-clusters-schedule-20240101120000")
del raw["metadata"]["uid"]
case("normalize-missing-uid-rejected", ALL, [], fn, {"raw": raw, "namespace": NS}, err("malformed_backup"), True)
case("normalize-non-object-rejected", ALL, [], fn, {"raw": [], "namespace": NS}, err("malformed_backup"), True)
case(
    "normalize-status-not-object-rejected",
    ALL,
    [],
    fn,
    {"raw": {"metadata": good["metadata"], "status": "Completed"}, "namespace": NS},
    err("malformed_backup"),
    True,
)

# --- direct `latest` prediction (amendment-2 §1) ----------------------------------------
fn = "predict_latest_backup"
L = refs(ALL, "tokens", "latest", "sort")


def mc(stamp, **kw):
    return backup(f"acm-managed-clusters-schedule-{stamp}", **kw)


case(
    "latest-empty-inventory-none", ALL, L, fn, {"inventory": [], "resource_type": "ManagedClusters"}, ok(["none", None])
)
older = mc("20240101110000", start="2024-01-01T11:00:00Z")
newer = mc("20240101120000", start="2024-01-01T12:00:00Z")
case(
    "latest-selects-newest-start",
    ALL,
    L,
    fn,
    {"inventory": [older, newer], "resource_type": "ManagedClusters"},
    ok(["selected", newer]),
)
other = backup("acm-credentials-schedule-20240101130000", start="2024-01-01T13:00:00Z")
case(
    "latest-other-type-none",
    ALL,
    L,
    fn,
    {"inventory": [other], "resource_type": "ManagedClusters"},
    ok(["none", None]),
)
# HasPrefix, not Contains: the token carries no trailing hyphen.
glued = backup("acm-resources-schedulex-20240101130000", start="2024-01-01T13:00:00Z")
ordinary = backup("acm-resources-schedule-20240101120000", start="2024-01-01T12:00:00Z")
embedded = backup("x-acm-resources-schedule-20240101140000", start="2024-01-01T14:00:00Z")
generic = backup("acm-resources-generic-schedule-20240101150000", start="2024-01-01T15:00:00Z")
case(
    "latest-prefix-without-hyphen-included",
    ALL,
    L,
    fn,
    {"inventory": [ordinary, glued], "resource_type": "Resources"},
    ok(["selected", glued]),
)
case(
    "latest-embedded-token-excluded",
    ALL,
    L,
    fn,
    {"inventory": [ordinary, embedded], "resource_type": "Resources"},
    ok(["selected", ordinary]),
)
case(
    "latest-generic-not-an-ordinary-resources-candidate",
    ALL,
    L,
    fn,
    {"inventory": [ordinary, generic], "resource_type": "Resources"},
    ok(["selected", ordinary]),
)
case(
    "latest-generic-selects-generic",
    ALL,
    L,
    fn,
    {"inventory": [ordinary, generic], "resource_type": "ResourcesGeneric"},
    ok(["selected", generic]),
)
partial = mc("20240101120000", phase="PartiallyFailed", start="2024-01-01T12:00:00Z")
case(
    "latest-newer-partially-failed-selected",
    ALL,
    L,
    fn,
    {"inventory": [older, partial], "resource_type": "ManagedClusters"},
    ok(["selected", partial]),
)
case(
    "latest-selected-partially-failed-fails-eligibility",
    ALL,
    L,
    "normalize_backup_evidence",
    {"raw": partial, "namespace": NS},
    err("backup_not_completed"),
    True,
)
failed = mc("20240101120000", phase="Failed", start="2024-01-01T12:00:00Z")
case(
    "latest-raw-failed-excluded",
    ALL,
    L,
    fn,
    {"inventory": [older, failed], "resource_type": "ManagedClusters"},
    ok(["selected", older]),
)
no_status = {"metadata": {"name": "acm-managed-clusters-schedule-20240101130000", "uid": "u", "namespace": NS}}
case(
    "latest-status-absent-excluded",
    ALL,
    L,
    fn,
    {"inventory": [older, no_status], "resource_type": "ManagedClusters"},
    ok(["selected", older]),
)
late_start = mc("20240101110000", start="2024-01-01T12:30:00Z", completion="2024-01-01T12:31:00Z")
early_start = mc("20240101120000", start="2024-01-01T12:00:00Z", completion="2024-01-01T13:00:00Z")
case(
    "latest-orders-by-start-not-completion",
    ALL,
    L,
    fn,
    {"inventory": [early_start, late_start], "resource_type": "ManagedClusters"},
    ok(["selected", late_start]),
)
tie_a = mc("20240101120000", start="2024-01-01T12:00:00Z")
tie_b = mc("20240101120001", start="2024-01-01T12:00:00Z")
case(
    "latest-tie-at-maximum-blocks",
    ALL,
    L,
    fn,
    {"inventory": [older, tie_a, tie_b], "resource_type": "ManagedClusters"},
    err("latest_ambiguous"),
    True,
)
tie_offset = mc("20240101120002", start="2024-01-01T14:00:00+02:00")
case(
    "latest-tie-same-instant-different-offset-blocks",
    ALL,
    L,
    fn,
    {"inventory": [tie_a, tie_offset], "resource_type": "ManagedClusters"},
    err("latest_ambiguous"),
    True,
)
old_a = mc("20240101100000", start="2024-01-01T10:00:00Z")
old_b = mc("20240101100001", start="2024-01-01T10:00:00Z")
case(
    "latest-older-tie-selects-maximum",
    ALL,
    L,
    fn,
    {"inventory": [old_a, newer, old_b], "resource_type": "ManagedClusters"},
    ok(["selected", newer]),
)
case(
    "latest-tie-partially-failed-and-completed-blocks",
    ALL,
    L,
    fn,
    {"inventory": [tie_a, mc("20240101120009", phase="PartiallyFailed")], "resource_type": "ManagedClusters"},
    err("latest_ambiguous"),
    True,
)
for label, value in [
    ("missing", ABSENT),
    ("null", None),
    ("malformed", "yesterday"),
    ("no-zone", "2024-01-01T13:00:00"),
    ("integer", 1704114000),
]:
    case(
        f"latest-start-{label}-blocks",
        ALL,
        L,
        fn,
        {"inventory": [newer, mc("20240101130000", start=value)], "resource_type": "ManagedClusters"},
        err("latest_start_timestamp_invalid"),
        True,
    )
case(
    "latest-start-missing-on-excluded-phase-ignored",
    ALL,
    L,
    fn,
    {"inventory": [newer, mc("20240101130000", phase="Failed", start=ABSENT)], "resource_type": "ManagedClusters"},
    ok(["selected", newer]),
)
case(
    "latest-start-missing-on-other-type-ignored",
    ALL,
    L,
    fn,
    {
        "inventory": [newer, backup("acm-credentials-schedule-20240101130000", start=ABSENT)],
        "resource_type": "ManagedClusters",
    },
    ok(["selected", newer]),
)
cred = backup("acm-credentials-schedule-20240101120000")
hive = backup("acm-credentials-hive-schedule-20240101130000", start="2024-01-01T13:00:00Z")
case(
    "latest-credentials-ignores-hive-prefix",
    ALL,
    L,
    fn,
    {"inventory": [cred, hive], "resource_type": "Credentials"},
    ok(["selected", cred]),
)
for rtype in ("CredentialsHive", "CredentialsCluster", "CredentialsActive", "ResourcesGenericActive", "Validation"):
    case(
        f"latest-unsupported-type-{rtype}",
        ALL,
        [],
        fn,
        {"inventory": [newer], "resource_type": rtype},
        err("unsupported_resource_type"),
        True,
    )
case(
    "latest-inventory-not-a-list",
    ALL,
    [],
    fn,
    {"inventory": {"items": []}, "resource_type": "ManagedClusters"},
    err("malformed_inventory"),
    True,
)
case(
    "latest-item-without-name",
    ALL,
    [],
    fn,
    {"inventory": [newer, {"metadata": {}, "status": {}}], "resource_type": "ManagedClusters"},
    err("malformed_inventory"),
    True,
)
case(
    "latest-duplicate-name",
    ALL,
    [],
    fn,
    {"inventory": [newer, dict(newer)], "resource_type": "ManagedClusters"},
    err("malformed_inventory"),
    True,
)

# --- correlated prediction (amendment-2 §2) ---------------------------------------------
fn = "predict_correlated_backup"
C = refs(ALL, "tokens", "correlated", "timestamp")
CL = refs(LEGACY, "tokens", "correlated", "timestamp")
C17 = refs(["2.17"], "tokens", "correlated", "timestamp")
SRC = "acm-resources-schedule-20240101120000"


def gen(name_suffix, **kw):
    return backup(f"acm-resources-generic-schedule-{name_suffix}", **kw)


exact = gen("20240101120000", start="2024-01-01T12:00:40Z")
near = gen("20240101120005", start="2024-01-01T12:00:05Z")
near2 = gen("20240101120010", start="2024-01-01T12:00:10Z")


def corr(inventory, source=SRC, rtype="ResourcesGeneric"):
    return {"inventory": inventory, "source_name": source, "resource_type": rtype}


case("correlated-exact-wins-over-fallbacks", ALL, C, fn, corr([near, exact, near2]), ok(["selected", exact]))
exact_bad = gen("20240101120000", phase="Failed", start=ABSENT)
case(
    "correlated-exact-failing-eligibility-still-selected",
    ALL,
    C,
    fn,
    corr([near, exact_bad]),
    ok(["selected", exact_bad]),
)
case(
    "correlated-exact-failing-eligibility-blocks",
    ALL,
    C,
    "normalize_backup_evidence",
    {"raw": exact_bad, "namespace": NS},
    err("backup_not_completed"),
    True,
)
manual_exact = backup("acm-resources-generic-schedule-manual")
case(
    "correlated-exact-with-unparseable-suffix-selected",
    ALL,
    C,
    fn,
    corr([manual_exact], source="acm-resources-schedule-manual"),
    ok(["selected", manual_exact]),
)
case(
    "correlated-no-hyphen-none",
    ALL,
    C,
    fn,
    corr([near, gen("x", start="2024-01-01T12:00:00Z")], source="acmresourcesschedule20240101120000"),
    ok(["none", None]),
)
for label, source in [
    ("non-digit", "acm-resources-schedule-manual"),
    ("short", "acm-resources-schedule-2024010112"),
    ("long", "acm-resources-schedule-202401011200000"),
    ("trailing-hyphen", "acm-resources-schedule-20240101120000-"),
    ("bad-month", "acm-resources-schedule-20241301120000"),
    ("bad-day", "acm-resources-schedule-20240230120000"),
    ("bad-second", "acm-resources-schedule-20240101120060"),
    ("zero-time", "acm-resources-schedule-00010101000000"),
]:
    case(f"correlated-source-{label}-none", ALL, C, fn, corr([near], source=source), ok(["none", None]))
# Only the text after the last hyphen is parsed; earlier hyphens are irrelevant.
case(
    "correlated-parses-after-last-hyphen-only",
    ALL,
    C,
    fn,
    corr([near], source="acm-resources-schedule--20240101120000"),
    ok(["selected", near]),
)
for label, start, expected in [
    ("plus-30-included", "2024-01-01T12:00:30Z", True),
    ("minus-30-included", "2024-01-01T11:59:30Z", True),
    ("plus-31-excluded", "2024-01-01T12:00:31Z", False),
    ("minus-31-excluded", "2024-01-01T11:59:29Z", False),
    ("plus-30-fraction-excluded", "2024-01-01T12:00:30.5Z", False),
    ("offset-within-range", "2024-01-01T14:00:20+02:00", True),
]:
    cand = gen("20240101115959", start=start)
    case(
        f"correlated-window-{label}",
        ALL,
        C,
        fn,
        corr([cand]),
        ok(["selected", cand] if expected else ["none", None]),
    )
nil_start = gen("20240101120003", start=None)
missing_start = gen("20240101120004", start=ABSENT)
case("correlated-nil-start-excluded", ALL, C, fn, corr([nil_start, missing_start]), ok(["none", None]))
case(
    "correlated-malformed-start-blocks",
    ALL,
    C,
    fn,
    corr([gen("20240101120003", start="noon")]),
    err("malformed_inventory"),
    True,
)
# Go zero time is no candidate even when a Backup starts within 30 s of it.
zero_near = gen("zero", start="0001-01-01T00:00:10Z")
case(
    "correlated-zero-time-none-despite-nearby-start",
    ALL,
    C,
    fn,
    corr([zero_near], source="acm-resources-schedule-00010101000000"),
    ok(["none", None]),
)
case(
    "correlated-empty-string-start-blocks",
    ALL,
    C,
    fn,
    corr([gen("20240101120003", start="")]),
    err("malformed_inventory"),
    True,
)
contains = backup("restore-acm-resources-generic-schedule-copy", start="2024-01-01T12:00:02Z")
case("correlated-non-prefix-contains-selected", ALL, C, fn, corr([contains]), ok(["selected", contains]))
not_generic = backup("acm-resources-schedule-20240101120001", start="2024-01-01T12:00:01Z")
case("correlated-zero-raw-none", ALL, C, fn, corr([not_generic]), ok(["none", None]))
failed_near = gen("20240101120005", phase="Failed", start="2024-01-01T12:00:05Z")
case("correlated-one-raw-any-phase-selected", ALL, C, fn, corr([failed_near]), ok(["selected", failed_near]))
case("correlated-two-raw-blocks", ALL, C, fn, corr([near, near2]), err("correlated_ambiguous"), True)
case(
    "correlated-two-raw-one-failed-blocks",
    ALL,
    C,
    fn,
    corr([failed_near, near2]),
    err("correlated_ambiguous"),
    True,
)
case(
    "correlated-target-from-name-not-source-status",
    ALL,
    C,
    fn,
    corr([backup(SRC, start="2024-01-01T13:00:00Z"), near]),
    ok(["selected", near]),
)
case("correlated-empty-inventory-none", ALL, C, fn, corr([]), ok(["none", None]))
cred_src = backup("acm-credentials-schedule-20240101120000")
case(
    "correlated-credentials-active-exact-is-source",
    ["2.17"],
    C17,
    fn,
    corr([cred_src], source=cred_src["metadata"]["name"], rtype="CredentialsActive"),
    ok(["selected", cred_src]),
)
case(
    "correlated-generic-active-exact",
    ["2.17"],
    C17,
    fn,
    corr([near, exact], rtype="ResourcesGenericActive"),
    ok(["selected", exact]),
)
hive_exact = backup("acm-credentials-hive-schedule-20240101120000")
case(
    "correlated-hive-exact-selected",
    LEGACY,
    CL,
    fn,
    corr([cred_src, hive_exact], source=cred_src["metadata"]["name"], rtype="CredentialsHive"),
    ok(["selected", hive_exact]),
)
cluster_near = backup("acm-credentials-cluster-schedule-20240101120010", start="2024-01-01T12:00:10Z")
case(
    "correlated-cluster-fallback-selected",
    LEGACY,
    CL,
    fn,
    corr([cred_src, cluster_near], source=cred_src["metadata"]["name"], rtype="CredentialsCluster"),
    ok(["selected", cluster_near]),
)
case(
    "correlated-hive-none",
    LEGACY,
    CL,
    fn,
    corr([cred_src], source=cred_src["metadata"]["name"], rtype="CredentialsHive"),
    ok(["none", None]),
)
for rtype in ("ManagedClusters", "Credentials", "Resources", "Validation"):
    case(
        f"correlated-unsupported-type-{rtype}",
        ALL,
        [],
        fn,
        corr([near], rtype=rtype),
        err("unsupported_resource_type"),
        True,
    )
for label, source in [("empty", ""), ("null", None), ("integer", 20240101120000)]:
    case(
        f"correlated-source-{label}-rejected",
        ALL,
        [],
        fn,
        corr([near], source=source),
        err("malformed_source_name"),
        True,
    )
case("correlated-inventory-not-a-list", ALL, [], fn, corr("nope"), err("malformed_inventory"), True)
case(
    "correlated-duplicate-exact-name",
    ALL,
    [],
    fn,
    corr([exact, dict(exact)]),
    err("malformed_inventory"),
    True,
)

# --- Task 2a review fixes -----------------------------------------------------------------
# Go time.Parse accepts a fractional-second tail ('.' or ',' then digits, only the first nine
# significant) after the seconds even though the layout 20060102150405 omits it.
fn = "predict_correlated_backup"
FRACTION = "acm-resources-schedule-20240101120000.5"
for label, source, start, expected in [
    ("period-inclusive-bound", FRACTION, "2024-01-01T12:00:30.5Z", True),
    ("period-past-bound", FRACTION, "2024-01-01T12:00:30.6Z", False),
    ("period-lower-bound", FRACTION, "2024-01-01T11:59:30.5Z", True),
    ("comma", "acm-resources-schedule-20240101120000,5", "2024-01-01T12:00:30.5Z", True),
    ("nine-digits", "acm-resources-schedule-20240101120000.123456789", "2024-01-01T12:00:30.123456789Z", True),
    ("tenth-digit-dropped", "acm-resources-schedule-20240101120000.1234567891", "2024-01-01T12:00:30.123456789Z", True),
    (
        "tenth-digit-past-bound",
        "acm-resources-schedule-20240101120000.1234567891",
        "2024-01-01T12:00:30.12345679Z",
        False,
    ),
    ("zero-date-with-fraction", "acm-resources-schedule-00010101000000.5", "0001-01-01T00:00:10Z", True),
]:
    cand = gen("fallback", start=start)
    case(
        f"correlated-source-fraction-{label}",
        ALL,
        C,
        fn,
        corr([cand], source=source),
        ok(["selected", cand] if expected else ["none", None]),
    )
for label, source in [
    ("separator-only", "acm-resources-schedule-20240101120000."),
    ("double-separator", "acm-resources-schedule-20240101120000..5"),
    ("trailing-text", "acm-resources-schedule-20240101120000.5x"),
    ("signed", "acm-resources-schedule-20240101120000.-5"),
    ("short-seconds", "acm-resources-schedule-2024010112000.5"),
]:
    case(
        f"correlated-source-fraction-{label}-none",
        ALL,
        C,
        fn,
        corr([gen("fallback", start="2024-01-01T12:00:00Z")], source=source),
        ok(["none", None]),
    )
cred_fraction = backup("acm-credentials-schedule-20240101120000.5")
hive_near = backup("acm-credentials-hive-schedule-manual", start="2024-01-01T12:00:20Z")
case(
    "correlated-hive-fraction-source-selected",
    LEGACY,
    CL,
    fn,
    corr([cred_fraction, hive_near], source=cred_fraction["metadata"]["name"], rtype="CredentialsHive"),
    ok(["selected", hive_near]),
)

# A present status.phase that is not a string fails the controller's typed LIST decode, so it
# is malformed inventory wherever it appears; an absent or null phase decodes to "".
for label, value in [("list", []), ("object", {}), ("integer", 42), ("boolean", True)]:
    bad = mc("20240101130000", phase=value)
    case(
        f"latest-phase-{label}-blocks",
        ALL,
        L,
        "predict_latest_backup",
        {"inventory": [newer, bad], "resource_type": "ManagedClusters"},
        err("malformed_inventory"),
        True,
    )
    case(
        f"latest-phase-{label}-on-other-type-blocks",
        ALL,
        L,
        "predict_latest_backup",
        {
            "inventory": [newer, backup("acm-credentials-schedule-20240101130000", phase=value)],
            "resource_type": "ManagedClusters",
        },
        err("malformed_inventory"),
        True,
    )
    case(
        f"correlated-phase-{label}-blocks",
        ALL,
        C,
        fn,
        corr([gen("20240101120005", phase=value, start="2024-01-01T12:00:05Z")]),
        err("malformed_inventory"),
        True,
    )
case(
    "latest-phase-null-excluded",
    ALL,
    L,
    "predict_latest_backup",
    {"inventory": [older, mc("20240101130000", phase=None)], "resource_type": "ManagedClusters"},
    ok(["selected", older]),
)

# Sub-second start ordering.
tenth = mc("20240101120001", start="2024-01-01T12:00:00.1Z")
fifth = mc("20240101120002", start="2024-01-01T12:00:00.2Z")
case(
    "latest-sub-second-newer-selected",
    ALL,
    L,
    "predict_latest_backup",
    {"inventory": [fifth, tenth], "resource_type": "ManagedClusters"},
    ok(["selected", fifth]),
)
for label, spelling in [("offset", "2024-01-01T14:00:00.5+02:00"), ("trailing-zero", "2024-01-01T12:00:00.50Z")]:
    case(
        f"latest-sub-second-tie-{label}-blocks",
        ALL,
        L,
        "predict_latest_backup",
        {
            "inventory": [mc("20240101120003", start="2024-01-01T12:00:00.5Z"), mc("20240101120004", start=spelling)],
            "resource_type": "ManagedClusters",
        },
        err("latest_ambiguous"),
        True,
    )

# Eligibility-applying selection: the only entry points whose result may be journaled.
fn = "select_latest_evidence"
case(
    "select-latest-returns-projection",
    ALL,
    L,
    fn,
    {"inventory": [older, newer], "resource_type": "ManagedClusters", "namespace": NS},
    ok(["selected", projection(newer)]),
)
case(
    "select-latest-none",
    ALL,
    L,
    fn,
    {"inventory": [], "resource_type": "ManagedClusters", "namespace": NS},
    ok(["none", None]),
)
case(
    "select-latest-partially-failed-blocks",
    ALL,
    L,
    fn,
    {"inventory": [older, partial], "resource_type": "ManagedClusters", "namespace": NS},
    err("backup_not_completed"),
    True,
)
case(
    "select-latest-namespace-mismatch-blocks",
    ALL,
    L,
    fn,
    {"inventory": [older, newer], "resource_type": "ManagedClusters", "namespace": "velero"},
    err("backup_namespace_mismatch"),
    True,
)
case(
    "select-latest-ambiguous-blocks",
    ALL,
    L,
    fn,
    {"inventory": [tie_a, tie_b], "resource_type": "ManagedClusters", "namespace": NS},
    err("latest_ambiguous"),
    True,
)
fn = "select_correlated_evidence"
case(
    "select-correlated-exact-returns-projection",
    ALL,
    C,
    fn,
    dict(corr([near, exact]), namespace=NS),
    ok(["selected", projection(exact)]),
)
case("select-correlated-none", ALL, C, fn, dict(corr([]), namespace=NS), ok(["none", None]))
case(
    "select-correlated-failed-exact-blocks",
    ALL,
    C,
    fn,
    dict(corr([near, exact_bad]), namespace=NS),
    err("backup_not_completed"),
    True,
)
case(
    "select-correlated-failed-fallback-blocks",
    ALL,
    C,
    fn,
    dict(corr([failed_near]), namespace=NS),
    err("backup_not_completed"),
    True,
)
case(
    "select-correlated-ambiguous-blocks",
    ALL,
    C,
    fn,
    dict(corr([near, near2]), namespace=NS),
    err("correlated_ambiguous"),
    True,
)

# --- Task 2b: child evidence (August §5, amendment-2 §§3-4) --------------------------------
V17 = ["2.17"]
REF_TABLES = {
    "name": {"2.12": "utils.go:118-125", "2.13": "utils.go:118-125", "2.14": "utils.go:127-134"},
    "owner": {"2.12": "restore.go:735", "2.13": "restore.go:735", "2.17": "restore.go:828"},
    "index": {"2.12": "restore_controller.go:353-366", "2.13": "restore_controller.go:353-366"},
    "status": {"2.12": "restore_controller.go:472-496", "2.13": "restore_controller.go:472-496"},
    "phases": {"2.17": "restore_types.go:31-46"},
    "cohort": {"2.12": "restore.go:185-273", "2.13": "restore.go:185-273", "2.17": "restore.go:206-256"},
    "only_mc": {"2.12": "restore.go:607-621", "2.13": "restore.go:607-621", "2.17": "restore.go:692-731"},
    "one_shot_types": {"2.12": "restore.go:609-621", "2.13": "restore.go:609-621", "2.17": "restore.go:692-723"},
    "substitute": {"2.12": "restore.go:667-670", "2.13": "restore.go:667-670"},
    "ignore_missing": {"2.12": "restore.go:700-719", "2.13": "restore.go:700-719"},
    "hive": {"2.12": "restore.go:503-539", "2.13": "restore.go:503-539"},
}
for lane in ("2.14", "2.15", "2.16"):
    REF_TABLES["owner"][lane] = "restore.go:742"
    REF_TABLES["index"][lane] = "restore_controller.go:393-406"
    REF_TABLES["status"][lane] = "restore_controller.go:575-599"
    REF_TABLES["cohort"][lane] = "restore.go:192-280"
    REF_TABLES["only_mc"][lane] = "restore.go:614-628"
    REF_TABLES["one_shot_types"][lane] = "restore.go:616-628"
    REF_TABLES["substitute"][lane] = "restore.go:674-677"
    REF_TABLES["ignore_missing"][lane] = "restore.go:707-726"
    REF_TABLES["hive"][lane] = "restore.go:510-546"
for lane in ("2.15", "2.16", "2.17"):
    REF_TABLES["name"][lane] = "utils.go:126-133"
for lane in LEGACY:
    REF_TABLES["phases"][lane] = "restore_types.go:29-41"
REF_TABLES["index"]["2.17"] = "restore_controller.go:516-529"
REF_TABLES["status"]["2.17"] = "restore_controller.go:745-767"
REF_TABLES["active"] = {"2.17": "restore_controller.go:803-823"}


def refs2(lanes, *kinds):
    return [f"{lane}:{REF_TABLES[kind][lane]}" for lane in lanes for kind in kinds if lane in REF_TABLES[kind]]


R = "acm-restore"
RUID = "uid-acm-restore"
ACM_API = "cluster.open-cluster-management.io/v1beta1"


def owner_ref(api=ACM_API, kind="Restore", name=R, uid=RUID, controller=True):
    ref = {"apiVersion": api, "kind": kind, "name": name, "uid": uid, "blockOwnerDeletion": True}
    if controller is not ABSENT:
        ref["controller"] = controller
    return ref


def child(name, backup_name, phase="Completed", refs=None, namespace=NS, uid=None):
    metadata = {"name": name, "uid": uid if uid is not None else f"uid-{name}", "namespace": namespace}
    metadata["ownerReferences"] = [owner_ref()] if refs is None else refs
    status = {} if phase is ABSENT else {"phase": phase}
    return {
        "apiVersion": "velero.io/v1",
        "kind": "Restore",
        "metadata": metadata,
        "spec": {"backupName": backup_name},
        "status": status,
    }


def entry(raw):
    return {
        "namespace": raw["metadata"]["namespace"],
        "name": raw["metadata"]["name"],
        "uid": raw["metadata"]["uid"],
        "backup_name": raw["spec"]["backupName"],
        "phase": "Completed",
    }


def lists(**by_category):
    out = {key: [] for key in VELERO_LISTS}
    for key, raws in by_category.items():
        out[key] = sorted((entry(raw) for raw in raws), key=lambda item: item["name"])
    return out


VELERO_LISTS = [
    "managed_clusters",
    "credentials",
    "resources",
    "resources_generic",
    "activation_credentials",
    "activation_resources",
    "activation_resources_generic",
]
MC_B = "acm-managed-clusters-schedule-20240101120000"
CRED_B = "acm-credentials-schedule-20240101120000"
RES_B = "acm-resources-schedule-20240101120000"
GEN_B = "acm-resources-generic-schedule-20240101120000"


def gname(backup_name, active=False):
    return (f"{R}-{backup_name}")[:252] + ("-active" if active else "")


def proj(name):
    return projection(backup(name))


def status4(mc="", cred="", res="", gen=""):
    return {
        "veleroManagedClustersRestoreName": mc,
        "veleroCredentialsRestoreName": cred,
        "veleroResourcesRestoreName": res,
        "veleroGenericResourcesRestoreName": gen,
    }


# generated_child_name ------------------------------------------------------------------------
fn = "generated_child_name"
case("child-name-plain", ALL, refs2(ALL, "name"), fn, {"acm_restore_name": R, "backup_name": MC_B}, ok(gname(MC_B)))
long_restore = "r" * 200
long_backup = "b" * 51
case(
    "child-name-252-kept",
    ALL,
    refs2(ALL, "name"),
    fn,
    {"acm_restore_name": long_restore, "backup_name": long_backup},
    ok(long_restore + "-" + long_backup),
)
case(
    "child-name-253-truncated",
    ALL,
    refs2(ALL, "name"),
    fn,
    {"acm_restore_name": long_restore, "backup_name": long_backup + "c"},
    ok(long_restore + "-" + long_backup),
)
case(
    "child-name-active-appended-after-truncation",
    V17,
    refs2(V17, "name", "active"),
    fn,
    {"acm_restore_name": long_restore, "backup_name": long_backup + "cdef", "active_suffix": True},
    ok(long_restore + "-" + long_backup + "-active"),
)
for label, restore_name, backup_name in [("empty-restore", "", MC_B), ("empty-backup", R, ""), ("null", None, MC_B)]:
    case(
        f"child-name-{label}-rejected",
        ALL,
        [],
        fn,
        {"acm_restore_name": restore_name, "backup_name": backup_name},
        err("malformed_child_name_input"),
        True,
    )

# validate_velero_child / is_owned_by -----------------------------------------------------------
OWN = refs2(ALL, "owner", "index")
fn = "validate_velero_child"
good_child = child(gname(MC_B), MC_B)


def vchild(raw, expected=MC_B, owner_name=R, owner_uid=RUID, namespace=NS):
    return {
        "raw": raw,
        "namespace": namespace,
        "owner_name": owner_name,
        "owner_uid": owner_uid,
        "expected_backup_name": expected,
    }


case("child-valid-five-fields", ALL, OWN, fn, vchild(good_child), ok(entry(good_child)))
for label, api in [("v1", "cluster.open-cluster-management.io/v1"), ("v2", "cluster.open-cluster-management.io/v2")]:
    raw = child(gname(MC_B), MC_B, refs=[owner_ref(api=api)])
    case(f"child-owner-served-version-{label}-accepted", ALL, OWN, fn, vchild(raw), ok(entry(raw)), True)
raw = child(
    gname(MC_B),
    MC_B,
    refs=[owner_ref(controller=False), owner_ref(api="v1", kind="ConfigMap", name="x", controller=False)],
)
raw["metadata"]["ownerReferences"].append(owner_ref())
case("child-owner-non-controller-refs-ignored", ALL, OWN, fn, vchild(raw), ok(entry(raw)), True)
for label, ref in [
    ("group", owner_ref(api="velero.io/v1")),
    ("core-group", owner_ref(api="v1")),
    ("no-version", owner_ref(api="cluster.open-cluster-management.io/")),
    ("kind", owner_ref(kind="BackupSchedule")),
    ("name", owner_ref(name="other-restore")),
    ("uid", owner_ref(uid="uid-other")),
]:
    raw = child(gname(MC_B), MC_B, refs=[ref])
    case(f"child-owner-wrong-{label}", ALL, OWN, fn, vchild(raw), err("velero_restore_owner_mismatch"), True)
for label, owner_refs in [
    ("no-refs", []),
    ("controller-absent", [owner_ref(controller=ABSENT)]),
    ("controller-false", [owner_ref(controller=False)]),
]:
    raw = child(gname(MC_B), MC_B, refs=owner_refs)
    case(f"child-owner-{label}", ALL, OWN, fn, vchild(raw), err("velero_restore_owner_missing"), True)
raw = child(gname(MC_B), MC_B, refs=[owner_ref(controller="true")])
case("child-owner-controller-not-boolean", ALL, OWN, fn, vchild(raw), err("malformed_velero_restore"), True)
raw = child(gname(MC_B), MC_B)
del raw["metadata"]["ownerReferences"]
case("child-owner-refs-absent", ALL, OWN, fn, vchild(raw), err("velero_restore_owner_missing"), True)
for label, owner_refs in [
    ("duplicate", [owner_ref(), owner_ref()]),
    ("conflicting", [owner_ref(), owner_ref(name="other-restore", uid="uid-other")]),
]:
    raw = child(gname(MC_B), MC_B, refs=owner_refs)
    case(f"child-owner-{label}-controllers", ALL, OWN, fn, vchild(raw), err("velero_restore_owner_ambiguous"), True)
raw = child(gname(MC_B), CRED_B)
case("child-wrong-backup-name", ALL, OWN, fn, vchild(raw), err("velero_restore_backup_mismatch"), True)
for label, phase in [
    ("partially-failed", "PartiallyFailed"),
    ("failed", "Failed"),
    ("failed-validation", "FailedValidation"),
    ("in-progress", "InProgress"),
    ("new", "New"),
    ("empty", ""),
    ("lowercase", "completed"),
]:
    raw = child(gname(MC_B), MC_B, phase=phase)
    case(f"child-phase-{label}", ALL, OWN, fn, vchild(raw), err("velero_restore_not_completed"), True)
for label, phase in [("absent", ABSENT), ("null", None)]:
    raw = child(gname(MC_B), MC_B, phase=phase)
    case(f"child-phase-{label}", ALL, OWN, fn, vchild(raw), err("velero_restore_phase_missing"), True)
for label, phase in [("list", []), ("integer", 1), ("object", {})]:
    raw = child(gname(MC_B), MC_B, phase=phase)
    case(f"child-phase-{label}-malformed", ALL, OWN, fn, vchild(raw), err("velero_restore_phase_malformed"), True)
raw = child(gname(MC_B), MC_B, namespace="velero")
case("child-namespace-mismatch", ALL, OWN, fn, vchild(raw), err("velero_restore_namespace_mismatch"), True)
raw = child(gname(MC_B), MC_B, uid="")
case("child-empty-uid", ALL, OWN, fn, vchild(raw), err("malformed_velero_restore"), True)
raw = child("", MC_B)
case("child-empty-name", ALL, OWN, fn, vchild(raw), err("malformed_velero_restore"), True)
raw = child(gname(MC_B), MC_B)
del raw["spec"]["backupName"]
case("child-backup-name-absent", ALL, OWN, fn, vchild(raw), err("malformed_velero_restore"), True)
raw = child(gname(MC_B), MC_B, refs="not-a-list")
case("child-owner-refs-not-a-list", ALL, OWN, fn, vchild(raw), err("malformed_velero_restore"), True)
case("child-not-an-object", ALL, OWN, fn, vchild([]), err("malformed_velero_restore"), True)
case(
    "child-empty-expectation-rejected",
    ALL,
    OWN,
    fn,
    vchild(good_child, owner_uid=""),
    err("malformed_child_expectation"),
    True,
)

fn = "is_owned_by"
for label, owner_refs, expected in [
    ("exact", [owner_ref()], True),
    ("same-uid-other-kind", [owner_ref(kind="BackupSchedule")], True),
    ("same-name-other-uid", [owner_ref(uid="uid-previous")], True),
    ("same-name-other-group", [owner_ref(api="velero.io/v1", uid="uid-other")], False),
    ("unrelated", [owner_ref(name="other-restore", uid="uid-other")], False),
    ("non-controller", [owner_ref(controller=False)], False),
    ("no-refs", [], False),
]:
    case(
        f"owned-by-{label}",
        ALL,
        OWN,
        fn,
        {"raw": child(gname(MC_B), MC_B, refs=owner_refs), "owner_name": R, "owner_uid": RUID},
        ok(expected),
        label in ("same-uid-other-kind", "same-name-other-uid"),
    )
case(
    "owned-by-malformed-refs",
    ALL,
    OWN,
    fn,
    {"raw": child(gname(MC_B), MC_B, refs=[None]), "owner_name": R, "owner_uid": RUID},
    err("malformed_velero_restore"),
    True,
)

# passive_patch_cohort ---------------------------------------------------------------------------
fn = "passive_patch_cohort"
hist_failed = child(f"{R}-acm-credentials-schedule-20231231120000", "acm-credentials-schedule-20231231120000", "Failed")
cur_mc = child(gname(MC_B), MC_B)
cur_cred = child(gname(CRED_B), CRED_B)
cur_cred_active = child(gname(CRED_B, True), CRED_B)
case(
    "cohort-legacy-is-entire-owner-list",
    LEGACY,
    refs2(LEGACY, "cohort", "index"),
    fn,
    {
        "controller_contract": "legacy_2_12_2_16",
        "owner_children": [hist_failed, cur_mc],
        "status_names": status4(mc=gname(MC_B)),
    },
    ok([hist_failed, cur_mc]),
)
case(
    "cohort-2.17-all-status-empty-is-entire-owner-list",
    V17,
    refs2(V17, "cohort"),
    fn,
    {"controller_contract": "active_2_17", "owner_children": [hist_failed, cur_mc], "status_names": status4()},
    ok([hist_failed, cur_mc]),
)
case(
    "cohort-2.17-current-and-active-variants",
    V17,
    refs2(V17, "cohort"),
    fn,
    {
        "controller_contract": "active_2_17",
        "owner_children": [cur_cred_active, hist_failed, cur_mc, cur_cred],
        "status_names": status4(mc=gname(MC_B), cred=gname(CRED_B)),
    },
    ok([cur_cred_active, cur_mc, cur_cred]),
)
case(
    "cohort-2.17-active-status-includes-base",
    V17,
    refs2(V17, "cohort"),
    fn,
    {
        "controller_contract": "active_2_17",
        "owner_children": [cur_cred, cur_cred_active, hist_failed],
        "status_names": status4(cred=gname(CRED_B, True)),
    },
    ok([cur_cred, cur_cred_active]),
)
x2 = child("x-active-active", CRED_B)
x1 = child("x-active", CRED_B)
x0 = child("x", CRED_B)
case(
    "cohort-2.17-active-stripped-once",
    V17,
    refs2(V17, "cohort"),
    fn,
    {
        "controller_contract": "active_2_17",
        "owner_children": [x0, x1, x2],
        "status_names": status4(cred="x-active-active"),
    },
    ok([x2]),
)
case(
    "cohort-2.17-active-base-matches-double",
    V17,
    refs2(V17, "cohort"),
    fn,
    {"controller_contract": "active_2_17", "owner_children": [x0, x1, x2], "status_names": status4(cred="x-active")},
    ok([x0, x1]),
)
inner = child("a-active-b", CRED_B)
inner_stripped = child("a-b", CRED_B)
case(
    "cohort-2.17-inner-active-not-stripped",
    V17,
    refs2(V17, "cohort"),
    fn,
    {
        "controller_contract": "active_2_17",
        "owner_children": [inner, inner_stripped],
        "status_names": status4(cred="a-active-b"),
    },
    ok([inner]),
)
case(
    "cohort-unknown-contract",
    ALL,
    [],
    fn,
    {"controller_contract": "legacy", "owner_children": [], "status_names": status4()},
    err("unknown_controller_contract"),
    True,
)
case(
    "cohort-status-names-extra-key",
    ALL,
    [],
    fn,
    {"controller_contract": "active_2_17", "owner_children": [], "status_names": dict(status4(), phase="Enabled")},
    err("malformed_status_names"),
    True,
)
case(
    "cohort-status-name-not-string",
    ALL,
    [],
    fn,
    {
        "controller_contract": "active_2_17",
        "owner_children": [],
        "status_names": dict(status4(), veleroResourcesRestoreName=None),
    },
    err("malformed_status_names"),
    True,
)
case(
    "cohort-duplicate-child-name",
    ALL,
    [],
    fn,
    {"controller_contract": "legacy_2_12_2_16", "owner_children": [cur_mc, cur_mc], "status_names": status4()},
    err("malformed_owner_children"),
    True,
)

# acm_phase_accepts ------------------------------------------------------------------------------
fn = "acm_phase_accepts"
for contract, lanes in (("legacy_2_12_2_16", LEGACY), ("active_2_17", V17)):
    for kind in ("passive_restore", "full_restore", "passive_patch"):
        for phase in (
            "Finished",
            "Enabled",
            "EnabledWithErrors",
            "FinishedWithErrors",
            "Error",
            "Unknown",
            "Running",
            "",
            None,
            7,
        ):
            accepted = phase == "Finished" or (kind == "passive_patch" and phase == "Enabled")
            case(
                f"acm-phase-{contract}-{kind}-{phase}",
                lanes,
                refs2(lanes, "phases"),
                fn,
                {"mutation_kind": kind, "controller_contract": contract, "phase": phase},
                ok(accepted),
                not accepted,
            )
case(
    "acm-phase-unknown-kind",
    ALL,
    [],
    fn,
    {"mutation_kind": "sync", "controller_contract": "active_2_17", "phase": "Finished"},
    err("unsupported_mutation_kind"),
    True,
)

# one_shot_required_predictions / freeze_one_shot_backups -----------------------------------------
fn = "one_shot_required_predictions"


def req(resource_type, selection, decision, freeze_as, source_category=None):
    return {
        "resource_type": resource_type,
        "selection": selection,
        "source_category": source_category,
        "decision": decision,
        "freeze_as": freeze_as,
    }


PASSIVE_LEGACY_REQS = [
    req("ManagedClusters", "latest", "required", "managed_clusters"),
    req("Credentials", "latest", "required", "activation_credentials"),
    req("ResourcesGeneric", "latest", "optional", "activation_resources_generic"),
]
FULL_COMMON_REQS = [
    req("ManagedClusters", "concrete", "required", "managed_clusters"),
    req("Credentials", "concrete", "required", "credentials"),
    req("Resources", "concrete", "required", "resources"),
    req("ResourcesGeneric", "correlated", "required", "resources_generic", "resources"),
]
case(
    "predictions-passive-restore-legacy",
    LEGACY,
    refs2(LEGACY, "one_shot_types", "substitute", "ignore_missing"),
    fn,
    {"mutation_kind": "passive_restore", "controller_contract": "legacy_2_12_2_16"},
    ok(PASSIVE_LEGACY_REQS),
)
case(
    "predictions-passive-restore-2.17",
    V17,
    refs2(V17, "one_shot_types"),
    fn,
    {"mutation_kind": "passive_restore", "controller_contract": "active_2_17"},
    ok([req("ManagedClusters", "latest", "required", "managed_clusters")]),
)
case(
    "predictions-full-restore-legacy",
    LEGACY,
    refs2(LEGACY, "hive", "ignore_missing"),
    fn,
    {"mutation_kind": "full_restore", "controller_contract": "legacy_2_12_2_16"},
    ok(
        FULL_COMMON_REQS
        + [
            req("CredentialsHive", "correlated", "none_required", None, "credentials"),
            req("CredentialsCluster", "correlated", "none_required", None, "credentials"),
        ]
    ),
    True,
)
case(
    "predictions-full-restore-2.17",
    V17,
    refs2(V17, "one_shot_types", "active"),
    fn,
    {"mutation_kind": "full_restore", "controller_contract": "active_2_17"},
    ok(
        FULL_COMMON_REQS
        + [
            req("CredentialsActive", "correlated", "equals", "credentials", "credentials"),
            req("ResourcesGenericActive", "correlated", "equals", "resources_generic", "resources"),
        ]
    ),
)
for label, kind, contract, code in [
    ("passive-patch", "passive_patch", "active_2_17", "unsupported_mutation_kind"),
    ("unknown-kind", "restore", "active_2_17", "unsupported_mutation_kind"),
    ("unknown-contract", "full_restore", "active_2_18", "unknown_controller_contract"),
]:
    case(
        f"predictions-{label}-rejected",
        ALL,
        [],
        fn,
        {"mutation_kind": kind, "controller_contract": contract},
        err(code),
        True,
    )

fn = "freeze_one_shot_backups"
PR_L = refs2(LEGACY, "one_shot_types", "substitute", "ignore_missing") + refs(LEGACY, "latest")
mc_b = backup(MC_B)
cred_b = backup(CRED_B)
res_b = backup(RES_B)
gen_b = backup(GEN_B)
gen_new = backup("acm-resources-generic-schedule-20240101130000", start="2024-01-01T13:00:00Z")


def freeze(kind, contract, inventory, concrete=None):
    return {
        "mutation_kind": kind,
        "controller_contract": contract,
        "inventory": inventory,
        "namespace": NS,
        "concrete_backups": concrete,
    }


case(
    "freeze-passive-legacy-with-generic",
    LEGACY,
    PR_L,
    fn,
    freeze("passive_restore", "legacy_2_12_2_16", [mc_b, cred_b, gen_b, res_b]),
    ok(
        {
            "managed_clusters": projection(mc_b),
            "activation_credentials": projection(cred_b),
            "activation_resources_generic": projection(gen_b),
        }
    ),
)
case(
    "freeze-passive-legacy-without-generic",
    LEGACY,
    PR_L,
    fn,
    freeze("passive_restore", "legacy_2_12_2_16", [mc_b, cred_b, res_b]),
    ok({"managed_clusters": projection(mc_b), "activation_credentials": projection(cred_b)}),
)
case(
    "freeze-passive-legacy-generic-newest-of-two",
    LEGACY,
    PR_L,
    fn,
    freeze("passive_restore", "legacy_2_12_2_16", [mc_b, cred_b, gen_b, gen_new]),
    ok(
        {
            "managed_clusters": projection(mc_b),
            "activation_credentials": projection(cred_b),
            "activation_resources_generic": projection(gen_new),
        }
    ),
)
case(
    "freeze-passive-legacy-generic-tie-blocks",
    LEGACY,
    PR_L,
    fn,
    freeze("passive_restore", "legacy_2_12_2_16", [mc_b, cred_b, gen_b, backup(GEN_B + "0")]),
    err("latest_ambiguous"),
    True,
)
case(
    "freeze-passive-legacy-credentials-missing-blocks",
    LEGACY,
    PR_L,
    fn,
    freeze("passive_restore", "legacy_2_12_2_16", [mc_b, gen_b]),
    err("required_prediction_missing"),
)
case(
    "freeze-passive-2.17-only-managed-clusters",
    V17,
    refs2(V17, "one_shot_types") + refs(V17, "latest"),
    fn,
    freeze("passive_restore", "active_2_17", [mc_b, cred_b, gen_b, res_b]),
    ok({"managed_clusters": projection(mc_b)}),
)
case(
    "freeze-passive-2.17-managed-clusters-missing-blocks",
    V17,
    refs2(V17, "one_shot_types") + refs(V17, "latest"),
    fn,
    freeze("passive_restore", "active_2_17", [cred_b]),
    err("required_prediction_missing"),
)
case(
    "freeze-passive-rejects-concrete-backups",
    V17,
    [],
    fn,
    freeze("passive_restore", "active_2_17", [mc_b], {"managed_clusters": projection(mc_b)}),
    err("concrete_backups_invalid"),
    True,
)
FULL_CONCRETE = {
    "managed_clusters": projection(mc_b),
    "credentials": projection(cred_b),
    "resources": projection(res_b),
}
FULL_FROZEN = dict(FULL_CONCRETE, resources_generic=projection(gen_b))
for lanes, contract in ((LEGACY, "legacy_2_12_2_16"), (V17, "active_2_17")):
    tag = "legacy" if contract.startswith("legacy") else "2.17"
    case(
        f"freeze-full-{tag}",
        lanes,
        refs(lanes, "correlated"),
        fn,
        freeze("full_restore", contract, [mc_b, cred_b, res_b, gen_b], FULL_CONCRETE),
        ok(FULL_FROZEN),
    )
    case(
        f"freeze-full-{tag}-generic-missing-blocks",
        lanes,
        refs(lanes, "correlated"),
        fn,
        freeze("full_restore", contract, [mc_b, cred_b, res_b], FULL_CONCRETE),
        err("required_prediction_missing"),
        True,
    )
    case(
        f"freeze-full-{tag}-concrete-missing-category",
        lanes,
        [],
        fn,
        freeze("full_restore", contract, [mc_b, cred_b, res_b, gen_b], {"managed_clusters": projection(mc_b)}),
        err("concrete_backups_invalid"),
        True,
    )
hive_b = backup("acm-credentials-hive-schedule-20240101120000")
cluster_b = backup("acm-credentials-cluster-schedule-20240101120010", start="2024-01-01T12:00:10Z")
cluster_failed = backup("acm-credentials-cluster-schedule-20240101120000", phase="Failed")
for label, extra in [("hive-exact", hive_b), ("cluster-fallback", cluster_b), ("cluster-failed-exact", cluster_failed)]:
    case(
        f"freeze-full-legacy-{label}-selected-blocks",
        LEGACY,
        refs2(LEGACY, "hive") + refs(LEGACY, "correlated"),
        fn,
        freeze("full_restore", "legacy_2_12_2_16", [mc_b, cred_b, res_b, gen_b, extra], FULL_CONCRETE),
        err("legacy_credential_variant_selected"),
        True,
    )
case(
    "freeze-full-2.17-ignores-hive-backup",
    V17,
    refs(V17, "correlated"),
    fn,
    freeze("full_restore", "active_2_17", [mc_b, cred_b, res_b, gen_b, hive_b], FULL_CONCRETE),
    ok(FULL_FROZEN),
)
odd_cred = backup("acm-credentials-schedule-manual-x")
odd_active = backup("acm-credentials-schedule-x")
case(
    "freeze-full-2.17-active-prediction-differs-blocks",
    V17,
    refs2(V17, "active") + refs(V17, "correlated"),
    fn,
    freeze(
        "full_restore",
        "active_2_17",
        [mc_b, odd_cred, res_b, gen_b, odd_active],
        dict(FULL_CONCRETE, credentials=projection(odd_cred)),
    ),
    err("active_prediction_mismatch"),
    True,
)
nohyphen_cred = backup("acmcredentials")
case(
    "freeze-full-2.17-active-prediction-none-blocks",
    V17,
    refs2(V17, "active") + refs(V17, "correlated"),
    fn,
    freeze(
        "full_restore",
        "active_2_17",
        [mc_b, nohyphen_cred, res_b, gen_b],
        dict(FULL_CONCRETE, credentials=projection(nohyphen_cred)),
    ),
    err("active_prediction_mismatch"),
    True,
)

# predict_one_shot_child_names ------------------------------------------------------------------
fn = "predict_one_shot_child_names"
PASSIVE_LEGACY_FROZEN = {
    "managed_clusters": proj(MC_B),
    "activation_credentials": proj(CRED_B),
    "activation_resources_generic": proj(GEN_B),
}


def names_input(kind, contract, frozen, restore_name=R):
    return {
        "mutation_kind": kind,
        "controller_contract": contract,
        "acm_restore_name": restore_name,
        "frozen_backups": frozen,
    }


case(
    "names-passive-legacy",
    LEGACY,
    refs2(LEGACY, "name", "status"),
    fn,
    names_input("passive_restore", "legacy_2_12_2_16", PASSIVE_LEGACY_FROZEN),
    ok({"ManagedClusters": gname(MC_B), "Credentials": gname(CRED_B), "ResourcesGeneric": gname(GEN_B)}),
)
case(
    "names-full-2.17",
    V17,
    refs2(V17, "name", "active"),
    fn,
    names_input("full_restore", "active_2_17", FULL_FROZEN),
    ok(
        {
            "ManagedClusters": gname(MC_B),
            "Credentials": gname(CRED_B),
            "Resources": gname(RES_B),
            "ResourcesGeneric": gname(GEN_B),
            "CredentialsActive": gname(CRED_B, True),
            "ResourcesGenericActive": gname(GEN_B, True),
        }
    ),
)
# 248 characters + "-acm" fills the 252-character budget, so every role truncates to one name.
colliding_restore = "r" * 248
for lanes, kind, contract, frozen in [
    (LEGACY, "passive_restore", "legacy_2_12_2_16", PASSIVE_LEGACY_FROZEN),
    (LEGACY, "full_restore", "legacy_2_12_2_16", FULL_FROZEN),
    (V17, "full_restore", "active_2_17", FULL_FROZEN),
]:
    tag = "legacy" if contract.startswith("legacy") else "2.17"
    case(
        f"names-{kind}-{tag}-truncation-collision-blocks",
        lanes,
        refs2(lanes, "name"),
        fn,
        names_input(kind, contract, frozen, colliding_restore),
        err("generated_name_collision"),
        True,
    )
case(
    "names-passive-2.17-single-role-never-collides",
    V17,
    refs2(V17, "name"),
    fn,
    names_input("passive_restore", "active_2_17", {"managed_clusters": proj(MC_B)}, colliding_restore),
    ok({"ManagedClusters": (colliding_restore + "-" + MC_B)[:252]}),
)
case(
    "names-frozen-category-not-allowed",
    V17,
    [],
    fn,
    names_input("passive_restore", "active_2_17", PASSIVE_LEGACY_FROZEN),
    err("frozen_backups_invalid"),
    True,
)

# one_shot_completion -----------------------------------------------------------------------------
fn = "one_shot_completion"
c_mc = child(gname(MC_B), MC_B)
c_cred = child(gname(CRED_B), CRED_B)
c_res = child(gname(RES_B), RES_B)
c_gen = child(gname(GEN_B), GEN_B)
c_cred_act = child(gname(CRED_B, True), CRED_B)
c_gen_act = child(gname(GEN_B, True), GEN_B)


def one_shot(kind, contract, frozen, status, children, phase="Finished"):
    return {
        "mutation_kind": kind,
        "controller_contract": contract,
        "acm_restore_name": R,
        "acm_restore_uid": RUID,
        "namespace": NS,
        "frozen_backups": frozen,
        "status_names": status,
        "owner_children": children,
        "acm_phase": phase,
    }


OS_L = refs2(LEGACY, "status", "owner", "one_shot_types")
OS_17 = refs2(V17, "status", "owner", "one_shot_types", "active")
PL = ("passive_restore", "legacy_2_12_2_16")
PASSIVE_LEGACY_STATUS = status4(mc=gname(MC_B), cred=gname(CRED_B), gen=gname(GEN_B))
case(
    "one-shot-passive-legacy-with-generic",
    LEGACY,
    OS_L,
    fn,
    one_shot(*PL, PASSIVE_LEGACY_FROZEN, PASSIVE_LEGACY_STATUS, [c_gen, c_mc, c_cred]),
    ok(lists(managed_clusters=[c_mc], activation_credentials=[c_cred], activation_resources_generic=[c_gen])),
)
PASSIVE_LEGACY_NO_GEN = {"managed_clusters": proj(MC_B), "activation_credentials": proj(CRED_B)}
case(
    "one-shot-passive-legacy-without-generic",
    LEGACY,
    OS_L,
    fn,
    one_shot(*PL, PASSIVE_LEGACY_NO_GEN, status4(mc=gname(MC_B), cred=gname(CRED_B)), [c_mc, c_cred]),
    ok(lists(managed_clusters=[c_mc], activation_credentials=[c_cred])),
)
case(
    "one-shot-passive-legacy-generic-absent-but-published-blocks",
    LEGACY,
    OS_L,
    fn,
    one_shot(*PL, PASSIVE_LEGACY_NO_GEN, PASSIVE_LEGACY_STATUS, [c_mc, c_cred, c_gen]),
    err("unexpected_status_name"),
    True,
)
case(
    "one-shot-passive-legacy-generic-absent-but-owner-child-blocks",
    LEGACY,
    OS_L,
    fn,
    one_shot(*PL, PASSIVE_LEGACY_NO_GEN, status4(mc=gname(MC_B), cred=gname(CRED_B)), [c_mc, c_cred, c_gen]),
    err("owner_child_unfrozen_backup"),
    True,
)
no_backup_child = child(f"{R}-extra", MC_B)
del no_backup_child["spec"]["backupName"]
case(
    "one-shot-passive-legacy-owner-child-without-backup-name-blocks",
    LEGACY,
    OS_L,
    fn,
    one_shot(*PL, PASSIVE_LEGACY_NO_GEN, status4(mc=gname(MC_B), cred=gname(CRED_B)), [c_mc, c_cred, no_backup_child]),
    err("malformed_velero_restore"),
    True,
)
case(
    "one-shot-passive-legacy-credentials-status-missing-blocks",
    LEGACY,
    OS_L,
    fn,
    one_shot(*PL, PASSIVE_LEGACY_NO_GEN, status4(mc=gname(MC_B)), [c_mc, c_cred]),
    err("required_status_name_missing"),
    True,
)
case(
    "one-shot-passive-legacy-status-child-not-owned-blocks",
    LEGACY,
    OS_L,
    fn,
    one_shot(*PL, PASSIVE_LEGACY_NO_GEN, status4(mc=gname(MC_B), cred=gname(CRED_B)), [c_mc]),
    err("required_child_missing"),
    True,
)
case(
    "one-shot-passive-legacy-empty-owner-list-blocks",
    LEGACY,
    OS_L,
    fn,
    one_shot(*PL, PASSIVE_LEGACY_NO_GEN, status4(mc=gname(MC_B), cred=gname(CRED_B)), []),
    err("required_child_missing"),
    True,
)
newer_mc = child(f"{R}-acm-managed-clusters-schedule-20240101130000", "acm-managed-clusters-schedule-20240101130000")
case(
    "one-shot-passive-legacy-status-child-bound-to-unfrozen-backup-blocks",
    LEGACY,
    OS_L,
    fn,
    one_shot(
        *PL, PASSIVE_LEGACY_NO_GEN, status4(mc=newer_mc["metadata"]["name"], cred=gname(CRED_B)), [newer_mc, c_cred]
    ),
    err("velero_restore_backup_mismatch"),
    True,
)
case(
    "one-shot-passive-legacy-extra-owner-child-unfrozen-backup-blocks",
    LEGACY,
    OS_L,
    fn,
    one_shot(*PL, PASSIVE_LEGACY_NO_GEN, status4(mc=gname(MC_B), cred=gname(CRED_B)), [c_mc, c_cred, newer_mc]),
    err("owner_child_unfrozen_backup"),
    True,
)
case(
    "one-shot-passive-legacy-finished-with-errors-blocks",
    LEGACY,
    OS_L,
    fn,
    one_shot(
        *PL, PASSIVE_LEGACY_NO_GEN, status4(mc=gname(MC_B), cred=gname(CRED_B)), [c_mc, c_cred], "FinishedWithErrors"
    ),
    err("acm_phase_not_accepted"),
    True,
)
case(
    "one-shot-passive-legacy-enabled-blocks",
    LEGACY,
    OS_L,
    fn,
    one_shot(*PL, PASSIVE_LEGACY_NO_GEN, status4(mc=gname(MC_B), cred=gname(CRED_B)), [c_mc, c_cred], "Enabled"),
    err("acm_phase_not_accepted"),
    True,
)
case(
    "one-shot-passive-2.17-only-managed-clusters",
    V17,
    OS_17,
    fn,
    one_shot("passive_restore", "active_2_17", {"managed_clusters": proj(MC_B)}, status4(mc=gname(MC_B)), [c_mc]),
    ok(lists(managed_clusters=[c_mc])),
)
case(
    "one-shot-passive-2.17-credentials-status-blocks",
    V17,
    OS_17,
    fn,
    one_shot(
        "passive_restore",
        "active_2_17",
        {"managed_clusters": proj(MC_B)},
        status4(mc=gname(MC_B), cred=gname(CRED_B)),
        [c_mc, c_cred],
    ),
    err("unexpected_status_name"),
    True,
)
case(
    "one-shot-passive-2.17-legacy-category-rejected",
    V17,
    [],
    fn,
    one_shot("passive_restore", "active_2_17", PASSIVE_LEGACY_NO_GEN, status4(mc=gname(MC_B)), [c_mc]),
    err("frozen_backups_invalid"),
    True,
)
failed_mc = child(gname(MC_B), MC_B, phase="PartiallyFailed")
case(
    "one-shot-passive-2.17-partially-failed-blocks",
    V17,
    OS_17,
    fn,
    one_shot("passive_restore", "active_2_17", {"managed_clusters": proj(MC_B)}, status4(mc=gname(MC_B)), [failed_mc]),
    err("velero_restore_not_completed"),
    True,
)
FULL_STATUS = status4(mc=gname(MC_B), cred=gname(CRED_B), res=gname(RES_B), gen=gname(GEN_B))
FL = ("full_restore", "legacy_2_12_2_16")
F17 = ("full_restore", "active_2_17")
case(
    "one-shot-full-legacy",
    LEGACY,
    OS_L,
    fn,
    one_shot(*FL, FULL_FROZEN, FULL_STATUS, [c_mc, c_cred, c_res, c_gen]),
    ok(lists(managed_clusters=[c_mc], credentials=[c_cred], resources=[c_res], resources_generic=[c_gen])),
)
case(
    "one-shot-full-legacy-generic-status-missing-blocks",
    LEGACY,
    OS_L,
    fn,
    one_shot(*FL, FULL_FROZEN, dict(FULL_STATUS, veleroGenericResourcesRestoreName=""), [c_mc, c_cred, c_res, c_gen]),
    err("required_status_name_missing"),
    True,
)
FULL_17_CHILDREN = [c_mc, c_cred, c_cred_act, c_res, c_gen, c_gen_act]
case(
    "one-shot-full-2.17",
    V17,
    OS_17,
    fn,
    one_shot(*F17, FULL_FROZEN, FULL_STATUS, FULL_17_CHILDREN),
    ok(
        lists(
            managed_clusters=[c_mc],
            credentials=[c_cred, c_cred_act],
            resources=[c_res],
            resources_generic=[c_gen, c_gen_act],
        )
    ),
)
case(
    "one-shot-full-2.17-credentials-active-missing-blocks",
    V17,
    OS_17,
    fn,
    one_shot(*F17, FULL_FROZEN, FULL_STATUS, [c for c in FULL_17_CHILDREN if c is not c_cred_act]),
    err("active_child_missing"),
    True,
)
case(
    "one-shot-full-2.17-generic-active-missing-blocks",
    V17,
    OS_17,
    fn,
    one_shot(*F17, FULL_FROZEN, FULL_STATUS, [c for c in FULL_17_CHILDREN if c is not c_gen_act]),
    err("active_child_missing"),
    True,
)
c_cred_act_failed = child(gname(CRED_B, True), CRED_B, phase="Failed")
case(
    "one-shot-full-2.17-credentials-active-failed-blocks",
    V17,
    OS_17,
    fn,
    one_shot(*F17, FULL_FROZEN, FULL_STATUS, [c_mc, c_cred, c_cred_act_failed, c_res, c_gen, c_gen_act]),
    err("velero_restore_not_completed"),
    True,
)
c_gen_act_dup = child(gname(GEN_B, True) + "-2", GEN_B)
case(
    "one-shot-full-2.17-generic-active-duplicated-blocks",
    V17,
    OS_17,
    fn,
    one_shot(*F17, FULL_FROZEN, FULL_STATUS, FULL_17_CHILDREN + [c_gen_act_dup]),
    err("active_child_ambiguous"),
    True,
)
c_cred_act_foreign = child(gname(CRED_B, True), CRED_B, refs=[owner_ref(uid="uid-other")])
case(
    "one-shot-full-2.17-active-child-foreign-owner-blocks",
    V17,
    OS_17,
    fn,
    one_shot(*F17, FULL_FROZEN, FULL_STATUS, [c_mc, c_cred, c_cred_act_foreign, c_res, c_gen, c_gen_act]),
    err("velero_restore_owner_mismatch"),
    True,
)
case(
    "one-shot-full-2.17-running-blocks",
    V17,
    OS_17,
    fn,
    one_shot(*F17, FULL_FROZEN, FULL_STATUS, FULL_17_CHILDREN, "Running"),
    err("acm_phase_not_accepted"),
    True,
)
case(
    "one-shot-full-legacy-status-name-resolves-to-other-role-blocks",
    LEGACY,
    OS_L,
    fn,
    one_shot(
        *FL, FULL_FROZEN, dict(FULL_STATUS, veleroResourcesRestoreName=gname(CRED_B)), [c_mc, c_cred, c_res, c_gen]
    ),
    err("velero_restore_backup_mismatch"),
    True,
)
case(
    "one-shot-passive-patch-kind-rejected",
    ALL,
    [],
    fn,
    one_shot("passive_patch", "active_2_17", {"managed_clusters": proj(MC_B)}, status4(mc=gname(MC_B)), [c_mc]),
    err("unsupported_mutation_kind"),
    True,
)

# passive_patch_completion ---------------------------------------------------------------------------
fn = "passive_patch_completion"
ACT_CRED_B = "acm-credentials-schedule-20240102120000"
ACT_RES_B = "acm-resources-schedule-20240102120000"
ACT_GEN_B = "acm-resources-generic-schedule-20240102120000"
PATCH_FROZEN = {
    "managed_clusters": proj(MC_B),
    "activation_credentials": proj(ACT_CRED_B),
    "activation_resources": proj(ACT_RES_B),
    "activation_resources_generic": proj(ACT_GEN_B),
}
# Children from the sync cycle before the patch, bound to older Backups.
old_cred = child(gname(CRED_B), CRED_B)
old_res = child(gname(RES_B), RES_B)
old_gen = child(gname(GEN_B), GEN_B)
old_failed = child(f"{R}-acm-resources-schedule-20231231120000", "acm-resources-schedule-20231231120000", "Failed")
PRE = status4(cred=gname(CRED_B), res=gname(RES_B), gen=gname(GEN_B))
p_mc = child(gname(MC_B), MC_B)
p_cred = child(gname(ACT_CRED_B), ACT_CRED_B)
p_gen = child(gname(ACT_GEN_B), ACT_GEN_B)
p_res = child(gname(ACT_RES_B), ACT_RES_B)
p_cred_act = child(gname(ACT_CRED_B, True), ACT_CRED_B)
p_gen_act = child(gname(ACT_GEN_B, True), ACT_GEN_B)


def patch(contract, status, children, phase="Enabled", pre=PRE, frozen=PATCH_FROZEN):
    return {
        "controller_contract": contract,
        "acm_restore_name": R,
        "acm_restore_uid": RUID,
        "namespace": NS,
        "frozen_backups": frozen,
        "precondition_status_names": pre,
        "status_names": status,
        "owner_children": children,
        "acm_phase": phase,
    }


PP_L = refs2(LEGACY, "only_mc", "status", "cohort", "owner")
PP_17 = refs2(V17, "only_mc", "status", "cohort", "owner", "active")
LEG = "legacy_2_12_2_16"
A17 = "active_2_17"
LEG_POST = status4(mc=gname(MC_B), cred=gname(ACT_CRED_B), res=gname(RES_B), gen=gname(ACT_GEN_B))
LEG_CHILDREN = [old_cred, old_res, old_gen, p_mc, p_cred, p_gen]
for phase in ("Enabled", "Finished"):
    case(
        f"patch-legacy-happy-{phase}",
        LEGACY,
        PP_L,
        fn,
        patch(LEG, LEG_POST, LEG_CHILDREN, phase),
        ok(lists(managed_clusters=[p_mc], activation_credentials=[p_cred], activation_resources_generic=[p_gen])),
    )
case(
    "patch-legacy-changed-resources-bound",
    LEGACY,
    PP_L,
    fn,
    patch(LEG, dict(LEG_POST, veleroResourcesRestoreName=gname(ACT_RES_B)), LEG_CHILDREN + [p_res]),
    ok(
        lists(
            managed_clusters=[p_mc],
            activation_credentials=[p_cred],
            activation_resources=[p_res],
            activation_resources_generic=[p_gen],
        )
    ),
)
case(
    "patch-legacy-changed-resources-wrong-backup-blocks",
    LEGACY,
    PP_L,
    fn,
    patch(
        LEG,
        dict(LEG_POST, veleroResourcesRestoreName=gname(RES_B) + "-x"),
        LEG_CHILDREN + [child(gname(RES_B) + "-x", RES_B)],
    ),
    err("velero_restore_backup_mismatch"),
    True,
)
case(
    "patch-legacy-historical-failure-blocks",
    LEGACY,
    PP_L,
    fn,
    patch(LEG, LEG_POST, LEG_CHILDREN + [old_failed]),
    err("velero_restore_not_completed"),
    True,
)
case(
    "patch-legacy-unchanged-credentials-old-backup-blocks",
    LEGACY,
    PP_L,
    fn,
    patch(LEG, dict(LEG_POST, veleroCredentialsRestoreName=gname(CRED_B)), LEG_CHILDREN),
    err("velero_restore_backup_mismatch"),
    True,
)
case(
    "patch-legacy-generic-status-missing-blocks",
    LEGACY,
    PP_L,
    fn,
    patch(LEG, dict(LEG_POST, veleroGenericResourcesRestoreName=""), LEG_CHILDREN, pre=status4(cred=gname(CRED_B))),
    err("required_status_name_missing"),
    True,
)
case(
    "patch-legacy-missing-mc-association-blocks",
    LEGACY,
    PP_L,
    fn,
    patch(LEG, dict(LEG_POST, veleroManagedClustersRestoreName=""), LEG_CHILDREN),
    err("required_status_name_missing"),
    True,
)
case(
    "patch-legacy-enabled-with-errors-blocks",
    LEGACY,
    PP_L + refs2(LEGACY, "phases"),
    fn,
    patch(LEG, LEG_POST, LEG_CHILDREN, "EnabledWithErrors"),
    err("acm_phase_not_accepted"),
    True,
)
case(
    "patch-precondition-mc-not-empty-blocks",
    LEGACY,
    [],
    fn,
    patch(LEG, LEG_POST, LEG_CHILDREN, pre=dict(PRE, veleroManagedClustersRestoreName="old")),
    err("precondition_status_invalid"),
    True,
)
case(
    "patch-missing-activation-category-blocks",
    LEGACY,
    [],
    fn,
    patch(LEG, LEG_POST, LEG_CHILDREN, frozen={k: v for k, v in PATCH_FROZEN.items() if k != "activation_resources"}),
    err("frozen_backups_invalid"),
    True,
)
# 2.17: the sync cycle before the patch left credentials/resources/generic children and statuses.
P17_POST = dict(PRE, veleroManagedClustersRestoreName=gname(MC_B))
P17_CHILDREN = [old_cred, old_res, old_gen, old_failed, p_mc, p_cred_act, p_gen_act]
for phase in ("Enabled", "Finished"):
    case(
        f"patch-2.17-happy-{phase}",
        V17,
        PP_17,
        fn,
        patch(A17, P17_POST, P17_CHILDREN, phase),
        ok(
            lists(
                managed_clusters=[p_mc],
                activation_credentials=[p_cred_act],
                activation_resources_generic=[p_gen_act],
            )
        ),
    )
case(
    "patch-2.17-enabled-with-errors-blocks",
    V17,
    PP_17 + refs2(V17, "phases"),
    fn,
    patch(A17, P17_POST, P17_CHILDREN, "EnabledWithErrors"),
    err("acm_phase_not_accepted"),
    True,
)
case(
    "patch-2.17-enabled-without-active-child-proof-blocks",
    V17,
    PP_17,
    fn,
    patch(A17, P17_POST, [c for c in P17_CHILDREN if c is not p_gen_act]),
    err("active_child_missing"),
    True,
)
case(
    "patch-2.17-enabled-with-running-active-child-blocks",
    V17,
    PP_17,
    fn,
    patch(
        A17,
        P17_POST,
        [c for c in P17_CHILDREN if c is not p_cred_act] + [child(gname(ACT_CRED_B, True), ACT_CRED_B, "InProgress")],
    ),
    err("velero_restore_not_completed"),
    True,
)
case(
    "patch-2.17-active-child-bound-to-wrong-backup-blocks",
    V17,
    PP_17,
    fn,
    patch(A17, P17_POST, [c for c in P17_CHILDREN if c is not p_cred_act] + [child(gname(CRED_B, True), CRED_B)]),
    err("active_child_missing"),
    True,
)
case(
    "patch-2.17-missing-mc-association-blocks",
    V17,
    PP_17,
    fn,
    patch(A17, PRE, P17_CHILDREN),
    err("required_status_name_missing"),
    True,
)
case(
    "patch-2.17-changed-generic-bound",
    V17,
    PP_17,
    fn,
    patch(A17, dict(P17_POST, veleroGenericResourcesRestoreName=gname(ACT_GEN_B)), P17_CHILDREN + [p_gen]),
    ok(
        lists(
            managed_clusters=[p_mc],
            activation_credentials=[p_cred_act],
            activation_resources_generic=[p_gen, p_gen_act],
        )
    ),
)
case(
    "patch-2.17-changed-resources-wrong-backup-blocks",
    V17,
    PP_17,
    fn,
    patch(
        A17,
        dict(P17_POST, veleroResourcesRestoreName=gname(RES_B) + "-x"),
        P17_CHILDREN + [child(gname(RES_B) + "-x", RES_B)],
    ),
    err("velero_restore_backup_mismatch"),
    True,
)
case(
    "patch-2.17-cleared-association-blocks",
    V17,
    PP_17,
    fn,
    patch(A17, dict(P17_POST, veleroCredentialsRestoreName=""), P17_CHILDREN),
    err("status_name_cleared"),
    True,
)
# Current cohort: MC plus the status-tracked sync children and their -active variants. A failure in
# a current-cohort member blocks; the historical failure outside it does not.
old_gen_failed = child(gname(GEN_B), GEN_B, "Failed")
case(
    "patch-2.17-current-cohort-failure-blocks",
    V17,
    PP_17,
    fn,
    patch(A17, P17_POST, [c for c in P17_CHILDREN if c is not old_gen] + [old_gen_failed]),
    err("velero_restore_not_completed"),
    True,
)
case(
    "patch-2.17-foreign-owner-in-list-blocks",
    V17,
    PP_17,
    fn,
    patch(A17, P17_POST, P17_CHILDREN + [child("stray", ACT_CRED_B, refs=[owner_ref(uid="uid-previous")])]),
    err("velero_restore_owner_mismatch"),
    True,
)


# --- Task 2c: migration journal (July §§1/1a/4/4a, August §§2-6 and 10, amendment-2 §§3-5) -------------
# Journal vectors cite the design documents as "<lane>:<document>:<lines>": J is the July design,
# A the August amendment and C the controller child-evidence amendment, all under docs/plans/.
DOCS = {
    "J": "2026-07-29-migration-evidence-design.md",
    "A": "2026-08-27-r4-04-current-base-design-amendment.md",
    "C": "2026-09-30-r4-04-controller-child-evidence-amendment.md",
}


def drefs(lanes, *spots):
    out = []
    for lane in lanes:
        for spot in spots:
            doc, lines = spot.split(":")
            out.append(f"{lane}:{DOCS[doc]}:{lines}")
    return out


DELETE = object()
ACT = "active_2_17"
JRUN = "5b7c9a1e-3f2d-4c8b-9e6a-0d1f2a3b4c5d"
JOP = "9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b6a"
OTHER_UUID = "00000000-0000-4000-8000-000000000001"
MC_F = "veleroManagedClustersBackupName"
CREDS_F = "veleroCredentialsBackupName"
RES_F = "veleroResourcesBackupName"
MINOR = {LEG: "2.14", ACT: "2.17"}
JLANES = {LEG: LEGACY, ACT: V17}
KINDS = [
    ("passive_patch", LEG),
    ("passive_patch", ACT),
    ("passive_restore", LEG),
    ("passive_restore", ACT),
    ("full_restore", LEG),
    ("full_restore", ACT),
]
T_RESOLVED = "2024-01-01T12:30:00Z"
T_VERIFIED = "2024-01-01T13:00:00Z"
T_COMPLETED = "2024-01-01T13:05:00Z"
T_NAMES = "2024-01-01T13:06:00Z"
T_POST_NAMES = "2024-01-01T14:00:00Z"
T_POST_DONE = "2024-01-01T14:05:00Z"
T_REVALIDATED = "2024-01-01T15:00:00Z"
T_INTENT = "2024-01-01T15:01:00Z"
T_ACCEPTED = "2024-01-01T15:02:00Z"
T_ABSENT = "2024-01-01T15:03:00Z"
T_RECOVERY = "2024-01-01T15:04:00Z"
T_REPAIR = "2024-01-01T16:00:00+02:00"
NORMALIZED = {MC_F: "skip", CREDS_F: "latest", RES_F: "latest"}
COPIED = ["namespace", "name", "uid", "generation", "activation_method", "mutation_kind"]
COPIED += ["cleanup_before_restore", "spec_fingerprint"]
WAIVED_FLAG = "skip-managed-cluster-expectations"
REPAIR = {
    "actor": "operator@example.com",
    "acknowledged_at": T_REPAIR,
    "reason": "the Restore was deleted by this run; confirmed from the audit log",
    "run_id": JRUN,
    "operation_id": JOP,
    "inspected_evidence": ["audit-log:restore-delete", "oc get restore"],
}


def tag(kind, contract):
    return f"{kind.replace('_', '-')}-{'legacy' if contract == LEG else '2.17'}"


def waiver(scope, **kw):
    out = {
        "flag": WAIVED_FLAG,
        "journaled_at": T_NAMES,
        "actor": "operator@example.com",
        "reason": "the expected ManagedCluster list is stale",
        "request_id": None,
        "scope": scope,
        "outcome": "waived",
    }
    out.update(kw)
    return {key: value for key, value in out.items() if value is not DELETE}


def precondition(**kw):
    out = {
        "generation": 3,
        "resource_version": "1001",
        "backup_fields_raw": {MC_F: "skip", CREDS_F: "latest", RES_F: "latest"},
        "backup_fields_normalized": dict(NORMALIZED),
        "status_restore_names": PRE,
    }
    out.update(kw)
    return out


def jbackups(kind, contract):
    if kind == "passive_patch":
        return copy.deepcopy(PATCH_FROZEN)
    if kind == "passive_restore":
        out = {"managed_clusters": proj(MC_B)}
        if contract == LEG:
            out.update(activation_credentials=proj(CRED_B), activation_resources_generic=proj(GEN_B))
        return out
    return {
        "managed_clusters": proj(MC_B),
        "credentials": proj(CRED_B),
        "resources": proj(RES_B),
        "resources_generic": proj(GEN_B),
    }


def completed_children(kind, contract):
    """Return the raw children of a completed transaction, per child list."""
    if kind == "passive_patch":
        active = contract == ACT
        return {
            "managed_clusters": [p_mc],
            "activation_credentials": [p_cred_act if active else p_cred],
            "activation_resources_generic": [p_gen_act if active else p_gen],
        }
    out = {"managed_clusters": [child(gname(MC_B), MC_B)]}
    if kind == "passive_restore":
        if contract == LEG:
            out["activation_credentials"] = [child(gname(CRED_B), CRED_B)]
            out["activation_resources_generic"] = [child(gname(GEN_B), GEN_B)]
        return out
    out["credentials"] = [child(gname(CRED_B), CRED_B)]
    out["resources"] = [child(gname(RES_B), RES_B)]
    out["resources_generic"] = [child(gname(GEN_B), GEN_B)]
    if contract == ACT:
        out["credentials"].append(child(gname(CRED_B, True), CRED_B))
        out["resources_generic"].append(child(gname(GEN_B, True), GEN_B))
    return out


def fingerprint(restore):
    """Compute the spec fingerprint independently of both modules (August §6)."""
    keys = ("activation_method", "mutation_kind", "backup_fields", "cleanup_before_restore")
    text = json.dumps({key: restore[key] for key in keys}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def cleanup_record(state, restore=None, accepted=None, reason="absent_without_completion"):
    out = {
        "operation_id": None,
        "state": state,
        "namespace": None,
        "name": None,
        "uid": None,
        "generation": None,
        "activation_method": None,
        "mutation_kind": None,
        "cleanup_before_restore": None,
        "spec_fingerprint": None,
        "backup_fields": {},
        "intent_at": None,
        "final_get_resource_version": None,
        "delete_accepted_at": None,
        "absence_verified_at": None,
        "completed_at": None,
        "recovery": None,
        "repair": None,
    }
    if state == "not_started":
        return out
    out.update({key: restore[key] for key in COPIED})
    out.update(operation_id=JOP, intent_at=T_INTENT, backup_fields=copy.deepcopy(restore["backup_fields"]))
    if accepted is None:
        accepted = state in ("delete_accepted", "completed")
    if accepted:
        out.update(final_get_resource_version="2002", delete_accepted_at=T_ACCEPTED)
    if state == "completed":
        out.update(absence_verified_at=T_ABSENT, completed_at=T_ABSENT)
    if state in ("recovery_required", "repaired"):
        out["recovery"] = {
            "required_at": T_RECOVERY,
            "reason_code": reason,
            "observed_uid": None,
            "observed_resource_version": None,
        }
    if state == "repaired":
        out["repair"] = copy.deepcopy(REPAIR)
    return out


def journal(kind, contract, stage="pre", state="not_started", accepted=None):
    """Return a valid journal: pre-mutation, completed, post (post-activation done) or revalidated."""
    restore = {
        "namespace": NS,
        "name": R,
        "uid": RUID if kind == "passive_patch" else None,
        "generation": None,
        "activation_method": "full" if kind == "full_restore" else "passive",
        "mutation_kind": kind,
        "acm_minor": MINOR[contract],
        "controller_contract": contract,
        "backup_fields": {MC_F: MC_B, CREDS_F: CRED_B, RES_F: RES_B} if kind == "full_restore" else {MC_F: "latest"},
        "cleanup_before_restore": "CleanupRestored",
        "spec_fingerprint": None,
        "backup_names_verified_at": None,
        "completed_at": None,
        "names_verified_at": None,
        "teardown_revalidated_at": None,
        "velero_restores": lists(),
    }
    if kind == "passive_patch":
        restore["passive_patch_precondition"] = precondition()
    doc = {
        "schema_version": 2,
        "run_id": JRUN,
        "resolved_at": T_RESOLVED,
        "backups": jbackups(kind, contract),
        "restore": restore,
        "cleanup": cleanup_record("not_started"),
        "post_activation": {"names_verified_at": None, "completed_at": None},
        "waiver": None,
    }
    if stage == "pre":
        return doc
    restore.update(uid=RUID, generation=4, backup_names_verified_at=T_VERIFIED, completed_at=T_COMPLETED)
    restore.update(names_verified_at=T_NAMES, spec_fingerprint=fingerprint(restore))
    restore["velero_restores"] = lists(**completed_children(kind, contract))
    if stage in ("post", "revalidated"):
        doc["post_activation"] = {"names_verified_at": T_POST_NAMES, "completed_at": T_POST_DONE}
    if stage == "revalidated":
        restore["teardown_revalidated_at"] = T_REVALIDATED
        doc["cleanup"] = cleanup_record(state, restore, accepted)
    return doc


def at(doc, path):
    target = doc
    for part in path.split("."):
        target = target[int(part)] if part.isdigit() else target[part]
    return target


def mut(doc, **changes):
    """Return a deep copy with each dotted path ("__" separated) set, or removed with DELETE."""
    out = copy.deepcopy(doc)
    for path, value in changes.items():
        *parents, last = path.split("__")
        target = at(out, ".".join(parents)) if parents else out
        if last.isdigit():
            last = int(last)
        if value is DELETE:
            del target[last]
        else:
            target[last] = value
    return out


def jcase(case_id, lanes, spots, candidate, expect, r4_stricter=False):
    result = ok(candidate) if expect is None else err(expect)
    case(
        case_id,
        lanes,
        drefs(lanes, *spots),
        "validate_migration_journal",
        {"candidate": candidate},
        result,
        r4_stricter,
    )


FULL_L = journal("full_restore", LEG, "completed")
FULL_17 = journal("full_restore", ACT, "completed")
PATCH_L = journal("passive_patch", LEG, "completed")
PATCH_17 = journal("passive_patch", ACT, "completed")
PR_LEG = journal("passive_restore", LEG, "completed")
DONE_L = journal("full_restore", LEG, "revalidated")

# validate_migration_journal: accepted lifecycle states ------------------------------------------------
SHAPE = ("J:96-149", "A:152-169", "C:277-284")
for kind, contract in KINDS:
    lanes = JLANES[contract]
    if kind != "passive_patch":
        jcase(
            f"journal-pre-mutation-{tag(kind, contract)}", lanes, SHAPE + ("J:238-243",), journal(kind, contract), None
        )
    jcase(
        f"journal-completed-{tag(kind, contract)}",
        lanes,
        ("J:290-317", "A:544-612", "C:186-234"),
        journal(kind, contract, "completed"),
        None,
    )
for contract in (LEG, ACT):
    jcase(
        f"journal-passive-patch-pre-patch-null-generation-{tag('x', contract)[2:]}",
        JLANES[contract],
        ("C:267-272", "A:327-357"),
        journal("passive_patch", contract),
        None,
    )
    jcase(
        f"journal-passive-patch-accepted-patch-{tag('x', contract)[2:]}",
        JLANES[contract],
        ("C:267-272", "J:238-243"),
        mut(journal("passive_patch", contract), restore__generation=4),
        None,
    )
PARTIAL = mut(
    journal("full_restore", LEG),
    restore__uid=RUID,
    restore__generation=4,
    restore__backup_names_verified_at=T_VERIFIED,
    restore__velero_restores__managed_clusters=[entry(child(gname(MC_B), MC_B))],
)
PARTIAL["restore"]["spec_fingerprint"] = fingerprint(PARTIAL["restore"])
jcase("journal-partial-bundle", LEGACY, ("J:305-317", "J:345-366"), PARTIAL, None)
jcase(
    "journal-partial-bundle-children-only",
    LEGACY,
    ("J:345-366", "A:537-542"),
    mut(journal("full_restore", LEG), restore__velero_restores__credentials=[entry(child(gname(CRED_B), CRED_B))]),
    None,
)
jcase(
    "journal-passive-restore-legacy-without-generic-completed",
    LEGACY,
    ("C:161-167", "C:245-250", "C:285-289"),
    mut(
        PR_LEG,
        backups__activation_resources_generic=DELETE,
        restore__velero_restores__activation_resources_generic=[],
    ),
    None,
)
jcase(
    "journal-full-2.17-three-credentials-before-completion",
    V17,
    ("C:186-234", "A:537-542"),
    mut(
        journal("full_restore", ACT),
        restore__velero_restores__credentials=[
            entry(child(gname(CRED_B), CRED_B)),
            entry(child(gname(CRED_B, True), CRED_B)),
            entry(child(gname(CRED_B) + "-x", CRED_B)),
        ],
    ),
    None,
)
jcase(
    "journal-passive-patch-activation-resources-consumed",
    LEGACY,
    ("A:572-580",),
    mut(PATCH_L, restore__velero_restores__activation_resources=[entry(p_res)]),
    None,
)
jcase(
    "journal-passive-patch-completed-activation-waived",
    LEGACY,
    ("A:624-635", "J:454-470"),
    mut(PATCH_L, restore__names_verified_at=None, waiver=waiver("activation")),
    None,
)
jcase(
    "journal-post-activation-completed",
    LEGACY,
    ("J:545-552",),
    journal("full_restore", LEG, "post"),
    None,
)
jcase(
    "journal-post-activation-waived",
    LEGACY,
    ("J:545-552", "J:566-571"),
    mut(
        journal("full_restore", LEG, "post"),
        post_activation__names_verified_at=None,
        waiver=waiver("both", request_id="CHG-1234"),
    ),
    None,
)
jcase(
    "journal-teardown-revalidated",
    LEGACY,
    ("J:614-653",),
    journal("full_restore", LEG, "revalidated"),
    None,
)
NON_ASCII = {
    "managed_clusters": "acm-managed-clusters-schedule-čćž-20240101120000",
    "credentials": "acm-credentials-schedule-ü-20240101120000",
    "resources": "acm-resources-schedule-日本-20240101120000",
}
FULL_UNICODE = copy.deepcopy(FULL_L)
for category, name in NON_ASCII.items():
    FULL_UNICODE["backups"][category]["name"] = name
    FULL_UNICODE["restore"]["velero_restores"][category] = [entry(child(gname(name), name))]
FULL_UNICODE["restore"]["backup_fields"] = {
    MC_F: NON_ASCII["managed_clusters"],
    CREDS_F: NON_ASCII["credentials"],
    RES_F: NON_ASCII["resources"],
}
FULL_UNICODE["restore"]["spec_fingerprint"] = fingerprint(FULL_UNICODE["restore"])
jcase("journal-full-non-ascii-backup-names", LEGACY, ("J:275-288", "A:662-686"), FULL_UNICODE, None)
CLEANUP_SPOTS = ("J:203-228", "J:657-672")
for state in ("intent_persisted", "delete_accepted", "recovery_required", "completed", "repaired"):
    jcase(
        f"journal-cleanup-{state.replace('_', '-')}",
        LEGACY,
        CLEANUP_SPOTS,
        journal("full_restore", LEG, "revalidated", state),
        None,
    )
for state in ("recovery_required", "repaired"):
    jcase(
        f"journal-cleanup-{state.replace('_', '-')}-after-accepted-delete",
        LEGACY,
        CLEANUP_SPOTS,
        journal("full_restore", LEG, "revalidated", state, accepted=True),
        None,
    )
jcase(
    "journal-cleanup-recovery-with-observed-replacement",
    V17,
    ("J:198-201", "J:746-748"),
    mut(
        journal("passive_patch", ACT, "revalidated", "recovery_required"),
        cleanup__recovery__reason_code="replacement_uid",
        cleanup__recovery__observed_uid="uid-replacement",
        cleanup__recovery__observed_resource_version="3003",
    ),
    None,
)
jcase(
    "journal-precondition-upstream-normalization-accepted",
    ALL,
    ("A:359-384",),
    mut(
        journal("passive_patch", LEG),
        restore__passive_patch_precondition__backup_fields_raw={
            MC_F: "\xa0SK\u0130P\t",
            CREDS_F: " Latest\u3000",
            RES_F: "LATEST\n",
        },
    ),
    None,
)

# validate_migration_journal: rejects, in check order -----------------------------------------------------
PRE_L = journal("full_restore", LEG)
jcase("journal-not-an-object", ALL, SHAPE, [PRE_L], "malformed_journal")
jcase("journal-missing-waiver-key", ALL, SHAPE, mut(PRE_L, waiver=DELETE), "malformed_journal")
jcase(
    "journal-schema-version-1",
    ALL,
    ("A:165-167", "C:277-279"),
    mut(PRE_L, schema_version=1),
    "unsupported_schema_version",
)
jcase("journal-schema-version-bool", ALL, SHAPE, mut(PRE_L, schema_version=True), "unsupported_schema_version")
jcase("journal-schema-version-string", ALL, SHAPE, mut(PRE_L, schema_version="2"), "unsupported_schema_version")
jcase("journal-run-id-not-uuid", ALL, SHAPE, mut(PRE_L, run_id="run-1"), "malformed_journal")
jcase("journal-resolved-at-not-rfc3339", ALL, SHAPE, mut(PRE_L, resolved_at="2024-01-01 12:30:00"), "malformed_journal")
jcase("journal-backups-not-an-object", ALL, SHAPE, mut(PRE_L, backups=[]), "malformed_journal")
jcase("journal-waiver-wrong-type", ALL, SHAPE, mut(PRE_L, waiver=False), "malformed_journal")
for name, path, value in (
    ("missing-velero-restores", "restore__velero_restores", DELETE),
    ("missing-cleanup-before-restore", "restore__cleanup_before_restore", DELETE),
    ("missing-acm-minor", "restore__acm_minor", DELETE),
    ("unknown-mutation-kind", "restore__mutation_kind", "passive"),
    ("unknown-acm-minor", "restore__acm_minor", "2.18"),
    ("unknown-controller-contract", "restore__controller_contract", "legacy"),
    ("unknown-activation-method", "restore__activation_method", "patch"),
    ("empty-namespace", "restore__namespace", ""),
    ("empty-uid", "restore__uid", ""),
    ("bool-generation", "restore__generation", True),
    ("string-generation", "restore__generation", "4"),
    ("uppercase-fingerprint", "restore__spec_fingerprint", "A" * 64),
    ("short-fingerprint", "restore__spec_fingerprint", "a" * 63),
    ("malformed-completed-at", "restore__completed_at", "2024-01-01T13:05:00"),
    ("cleanup-before-restore-none", "restore__cleanup_before_restore", "None"),
    ("backup-fields-not-an-object", "restore__backup_fields", [MC_B]),
    ("velero-restores-missing-list", "restore__velero_restores__activation_resources", DELETE),
    ("velero-restores-extra-list", "restore__velero_restores__credentials_active", []),
    ("velero-restores-list-not-a-list", "restore__velero_restores__resources", {}),
    ("precondition-on-full-restore", "restore__passive_patch_precondition", precondition()),
):
    jcase(f"journal-restore-{name}", ALL, SHAPE + ("A:309-325",), mut(PRE_L, **{path: value}), "malformed_restore")
jcase(
    "journal-restore-passive-patch-without-precondition",
    ALL,
    ("A:327-357",),
    mut(journal("passive_patch", LEG), restore__passive_patch_precondition=DELETE),
    "malformed_restore",
)
jcase(
    "journal-full-restore-with-passive-method",
    ALL,
    ("J:166-171", "A:309-325"),
    mut(PRE_L, restore__activation_method="passive"),
    "activation_method_mismatch",
)
jcase(
    "journal-passive-restore-with-full-method",
    ALL,
    ("J:166-171", "A:309-325"),
    mut(journal("passive_restore", ACT), restore__activation_method="full"),
    "activation_method_mismatch",
)
jcase(
    "journal-contract-not-the-minor-contract",
    ALL,
    ("A:309-325", "C:284"),
    mut(PRE_L, restore__acm_minor="2.17"),
    "controller_contract_mismatch",
)
CATEGORY_SPOTS = ("A:213-242", "C:285-289")
for name, doc, path, value in (
    ("full-missing-resources-generic", PRE_L, "backups__resources_generic", DELETE),
    ("full-with-activation-credentials", PRE_L, "backups__activation_credentials", proj(CRED_B)),
    ("full-unknown-category", PRE_L, "backups__credentials_hive", proj(CRED_B)),
    ("patch-missing-activation-resources", journal("passive_patch", LEG), "backups__activation_resources", DELETE),
    ("patch-with-credentials", journal("passive_patch", LEG), "backups__credentials", proj(CRED_B)),
    (
        "passive-restore-legacy-missing-credentials",
        journal("passive_restore", LEG),
        "backups__activation_credentials",
        DELETE,
    ),
    (
        "passive-restore-legacy-with-resources",
        journal("passive_restore", LEG),
        "backups__activation_resources",
        proj(RES_B),
    ),
    (
        "passive-restore-2.17-with-credentials",
        journal("passive_restore", ACT),
        "backups__activation_credentials",
        proj(CRED_B),
    ),
    (
        "passive-restore-2.17-with-generic",
        journal("passive_restore", ACT),
        "backups__activation_resources_generic",
        proj(GEN_B),
    ),
):
    # Only a legacy passive_restore requires activation_credentials (amendment-2 §5.4).
    lanes = V17 if "2.17" in name else LEGACY if name == "passive-restore-legacy-missing-credentials" else ALL
    jcase(f"journal-categories-{name}", lanes, CATEGORY_SPOTS, mut(doc, **{path: value}), "invalid_category_set")
for name, path, value, stricter in (
    ("extra-field", "backups__credentials__start", "2024-01-01T12:00:00Z", False),
    ("missing-warnings", "backups__credentials__warnings", DELETE, False),
    ("errors-nonzero", "backups__credentials__errors", 1, False),
    ("warnings-negative", "backups__credentials__warnings", -1, False),
    ("warnings-bool", "backups__credentials__warnings", True, False),
    ("phase-partially-failed", "backups__credentials__phase", "PartiallyFailed", False),
    ("completed-at-malformed", "backups__credentials__completed_at", "yesterday", False),
    ("empty-namespace", "backups__credentials__namespace", "", False),
    ("empty-uid", "backups__credentials__uid", "", False),
    ("name-latest", "backups__credentials__name", "latest", False),
    ("name-skip", "backups__credentials__name", "skip", False),
    ("name-normalizes-to-latest", "backups__credentials__name", " Latest", True),
):
    jcase(
        f"journal-backup-projection-{name}",
        ALL,
        ("A:173-211", "J:160-164"),
        mut(PRE_L, **{path: value}),
        "malformed_backup_projection",
        stricter,
    )
for name, doc, fields in (
    ("passive-restore-concrete", journal("passive_restore", LEG), {MC_F: MC_B}),
    ("passive-patch-uppercase-latest", journal("passive_patch", LEG), {MC_F: "Latest"}),
    ("passive-with-skipped-field", journal("passive_restore", ACT), {MC_F: "latest", CREDS_F: "skip"}),
    ("full-latest", PRE_L, {MC_F: "latest", CREDS_F: CRED_B, RES_F: RES_B}),
    ("full-not-the-frozen-name", PRE_L, {MC_F: MC_B, CREDS_F: CRED_B, RES_F: RES_B + "-x"}),
    ("full-missing-resources", PRE_L, {MC_F: MC_B, CREDS_F: CRED_B}),
    ("full-with-generic-key", PRE_L, {MC_F: MC_B, CREDS_F: CRED_B, RES_F: RES_B, "veleroGenericBackupName": GEN_B}),
):
    jcase(
        f"journal-backup-fields-{name}",
        ALL,
        ("J:151-196", "A:399-402", "C:109-122"),
        mut(doc, restore__backup_fields=fields),
        "invalid_backup_fields",
    )
PP_PRE = journal("passive_patch", LEG)
PC = "restore__passive_patch_precondition"
for name, changes, stricter in (
    ("generation-zero", {f"{PC}__generation": 0}, False),
    ("generation-bool", {f"{PC}__generation": True}, False),
    ("resource-version-empty", {f"{PC}__resource_version": ""}, False),
    ("extra-key", {f"{PC}__uid": RUID}, False),
    ("raw-missing-key", {f"{PC}__backup_fields_raw__{RES_F}": DELETE}, False),
    ("raw-empty", {f"{PC}__backup_fields_raw__{MC_F}": "", f"{PC}__backup_fields_normalized__{MC_F}": ""}, False),
    ("raw-not-normalized-value", {f"{PC}__backup_fields_raw__{MC_F}": "latest"}, False),
    (
        "normalized-latest-managed-clusters",
        {f"{PC}__backup_fields_raw__{MC_F}": "latest", f"{PC}__backup_fields_normalized__{MC_F}": "latest"},
        False,
    ),
    ("raw-with-non-go-space", {f"{PC}__backup_fields_raw__{MC_F}": "skip\x1c"}, False),
    (
        "status-managed-clusters-non-empty",
        {f"{PC}__status_restore_names__veleroManagedClustersRestoreName": "x"},
        False,
    ),
    ("status-name-not-a-string", {f"{PC}__status_restore_names__veleroResourcesRestoreName": None}, False),
    ("status-missing-generic", {f"{PC}__status_restore_names__veleroGenericResourcesRestoreName": DELETE}, False),
):
    jcase(
        f"journal-precondition-{name}",
        ALL,
        ("A:327-357", "A:372-384"),
        mut(PP_PRE, **changes),
        "invalid_precondition",
        stricter,
    )
CHILD_SPOTS = ("A:511-542", "C:277-284")
VR = "restore__velero_restores"
MC_ENTRY = entry(child(gname(MC_B), MC_B))
for name, doc, changes, code in (
    ("entry-extra-field", FULL_L, {f"{VR}__managed_clusters__0__owner": R}, "malformed_child_entry"),
    ("entry-missing-uid", FULL_L, {f"{VR}__managed_clusters__0__uid": DELETE}, "malformed_child_entry"),
    ("entry-failed-phase", FULL_L, {f"{VR}__resources__0__phase": "Failed"}, "malformed_child_entry"),
    ("entry-not-an-object", FULL_L, {f"{VR}__resources__0": gname(RES_B)}, "malformed_child_entry"),
    ("entry-other-namespace", FULL_L, {f"{VR}__resources__0__namespace": "default"}, "child_namespace_mismatch"),
    (
        "list-for-absent-category",
        FULL_L,
        {f"{VR}__activation_credentials": [entry(child(gname(CRED_B), CRED_B))]},
        "child_list_not_permitted",
    ),
    (
        "passive-restore-credentials-in-full-list",
        journal("passive_restore", LEG),
        {f"{VR}__credentials": [entry(child(gname(CRED_B), CRED_B))]},
        "child_list_not_permitted",
    ),
    (
        "passive-restore-generic-without-category",
        mut(journal("passive_restore", LEG), backups__activation_resources_generic=DELETE),
        {f"{VR}__activation_resources_generic": [entry(child(gname(GEN_B), GEN_B))]},
        "child_list_not_permitted",
    ),
    ("entry-other-backup", FULL_L, {f"{VR}__resources__0__backup_name": GEN_B}, "child_backup_mismatch"),
    (
        "unsorted",
        FULL_17,
        {f"{VR}__credentials": list(reversed(FULL_17["restore"]["velero_restores"]["credentials"]))},
        "child_list_unsorted",
    ),
    ("identical-duplicate", FULL_L, {f"{VR}__managed_clusters": [MC_ENTRY, MC_ENTRY]}, "duplicate_child_name"),
    (
        "same-name-different-uid",
        FULL_L,
        {f"{VR}__managed_clusters": [MC_ENTRY, dict(MC_ENTRY, uid="uid-other")]},
        "duplicate_child_name",
    ),
):
    jcase(f"journal-children-{name}", ALL, CHILD_SPOTS, mut(doc, **changes), code)
jcase(
    "journal-waiver-missing-actor",
    ALL,
    ("J:454-470",),
    mut(PRE_L, waiver=waiver("both", actor=DELETE)),
    "malformed_waiver",
)
jcase(
    "journal-waiver-empty-reason", ALL, ("J:454-470",), mut(PRE_L, waiver=waiver("both", reason="")), "malformed_waiver"
)
jcase("journal-waiver-unknown-scope", ALL, ("J:454-470",), mut(PRE_L, waiver=waiver("all")), "malformed_waiver")
jcase(
    "journal-waiver-outcome-passed",
    ALL,
    ("J:454-470",),
    mut(PRE_L, waiver=waiver("both", outcome="passed")),
    "malformed_waiver",
)
jcase(
    "journal-waiver-extra-key", ALL, ("J:454-470",), mut(PRE_L, waiver=waiver("both", expected=[])), "malformed_waiver"
)
jcase(
    "journal-waiver-request-id-empty",
    ALL,
    ("J:454-470",),
    mut(PRE_L, waiver=waiver("both", request_id="")),
    "malformed_waiver",
)
jcase(
    "journal-post-activation-missing-completed-at",
    ALL,
    ("J:135-137",),
    mut(PRE_L, post_activation__completed_at=DELETE),
    "invalid_post_activation",
)
jcase(
    "journal-post-activation-malformed-timestamp",
    ALL,
    ("J:135-137",),
    mut(PRE_L, post_activation__names_verified_at="soon"),
    "invalid_post_activation",
)
for name, path, value in (
    ("unknown-state", "cleanup__state", "deleting"),
    ("missing-mutation-kind", "cleanup__mutation_kind", DELETE),
    ("missing-cleanup-before-restore", "cleanup__cleanup_before_restore", DELETE),
    ("operation-id-not-uuid", "cleanup__operation_id", "op-1"),
    ("string-generation", "cleanup__generation", "4"),
    ("backup-fields-null", "cleanup__backup_fields", None),
    ("backup-field-empty-value", "cleanup__backup_fields", {MC_F: ""}),
    ("recovery-not-an-object", "cleanup__recovery", "absent"),
):
    jcase(
        f"journal-cleanup-shape-{name}",
        ALL,
        ("J:118-149", "C:282-283"),
        mut(PRE_L, **{path: value}),
        "malformed_cleanup",
    )
RR = journal("full_restore", LEG, "revalidated", "recovery_required")
RP = journal("full_restore", LEG, "revalidated", "repaired")
for name, changes in (
    ("unknown-reason", {"cleanup__recovery__reason_code": "operator_deleted"}),
    ("missing-required-at", {"cleanup__recovery__required_at": DELETE}),
    ("extra-key", {"cleanup__recovery__detail": "x"}),
    ("half-observed-pair", {"cleanup__recovery__observed_uid": "uid-replacement"}),
):
    jcase(f"journal-recovery-{name}", ALL, ("J:133", "J:198-201"), mut(RR, **changes), "malformed_recovery")
for name, changes in (
    ("empty-inspected-evidence", {"cleanup__repair__inspected_evidence": []}),
    ("inspected-evidence-empty-reference", {"cleanup__repair__inspected_evidence": [""]}),
    ("missing-actor", {"cleanup__repair__actor": DELETE}),
    ("extra-key", {"cleanup__repair__state": "repaired"}),
    ("run-id-not-uuid", {"cleanup__repair__run_id": "run"}),
):
    jcase(f"journal-repair-{name}", ALL, ("J:134", "J:766-787"), mut(RP, **changes), "malformed_repair")
DA = journal("full_restore", LEG, "revalidated", "delete_accepted")
CO = journal("full_restore", LEG, "revalidated", "completed")
IP = journal("full_restore", LEG, "revalidated", "intent_persisted")
for name, doc, changes in (
    ("not-started-with-operation-id", PRE_L, {"cleanup__operation_id": JOP}),
    ("not-started-with-backup-fields", PRE_L, {"cleanup__backup_fields": {MC_F: MC_B}}),
    ("not-started-with-mutation-kind", PRE_L, {"cleanup__mutation_kind": "full_restore"}),
    ("not-started-with-cleanup-before-restore", PRE_L, {"cleanup__cleanup_before_restore": "CleanupRestored"}),
    ("intent-without-intent-at", IP, {"cleanup__intent_at": None}),
    ("intent-without-cleanup-before-restore", IP, {"cleanup__cleanup_before_restore": None}),
    ("intent-with-empty-backup-fields", IP, {"cleanup__backup_fields": {}}),
    ("intent-with-accepted-delete", IP, {"cleanup__delete_accepted_at": T_ACCEPTED}),
    ("intent-with-recovery", IP, {"cleanup__recovery": RR["cleanup"]["recovery"]}),
    ("delete-accepted-without-resource-version", DA, {"cleanup__final_get_resource_version": None}),
    ("delete-accepted-with-absence", DA, {"cleanup__absence_verified_at": T_ABSENT}),
    ("recovery-with-half-accepted-pair", RR, {"cleanup__delete_accepted_at": T_ACCEPTED}),
    ("recovery-without-recovery", RR, {"cleanup__recovery": None}),
    ("recovery-with-repair", RR, {"cleanup__repair": REPAIR}),
    ("completed-without-absence", CO, {"cleanup__absence_verified_at": None}),
    ("completed-with-recovery", CO, {"cleanup__recovery": RR["cleanup"]["recovery"]}),
    ("repaired-with-absence", RP, {"cleanup__absence_verified_at": T_ABSENT}),
    ("repaired-with-completion", RP, {"cleanup__completed_at": T_ABSENT}),
    ("repaired-without-repair", RP, {"cleanup__repair": None}),
):
    jcase(
        f"journal-cleanup-state-{name}", ALL, ("J:203-220", "C:282-283"), mut(doc, **changes), "invalid_cleanup_state"
    )
jcase(
    "journal-passive-patch-without-uid",
    ALL,
    ("C:267-272",),
    mut(journal("passive_patch", LEG), restore__uid=None),
    "patch_identity_missing",
)
jcase(
    "journal-fingerprint-mismatch",
    ALL,
    ("J:275-288", "A:662-686"),
    mut(PARTIAL, restore__spec_fingerprint="0" * 64),
    "fingerprint_mismatch",
)
jcase(
    "journal-fingerprint-without-mutation-kind",
    ALL,
    ("A:662-686",),
    mut(
        PARTIAL,
        restore__spec_fingerprint=hashlib.sha256(
            json.dumps(
                {
                    "activation_method": "full",
                    "backup_fields": PARTIAL["restore"]["backup_fields"],
                    "cleanup_before_restore": "CleanupRestored",
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest(),
    ),
    "fingerprint_mismatch",
)
for name in ("uid", "generation", "spec_fingerprint", "backup_names_verified_at"):
    jcase(
        f"journal-completed-without-{name.replace('_', '-')}",
        ALL,
        ("J:305-317", "J:345-352"),
        mut(FULL_L, **{f"restore__{name}": None}),
        "incomplete_bundle",
    )
COUNT_SPOTS = ("C:186-234", "A:544-612")
for name, doc, lanes, changes in (
    ("full-legacy-no-generic-child", FULL_L, LEGACY, {f"{VR}__resources_generic": []}),
    ("full-legacy-no-managed-clusters-child", FULL_L, LEGACY, {f"{VR}__managed_clusters": []}),
    ("full-2.17-one-credentials-child", FULL_17, V17, {f"{VR}__credentials__1": DELETE}),
    ("full-2.17-one-generic-child", FULL_17, V17, {f"{VR}__resources_generic__1": DELETE}),
    (
        "full-2.17-three-credentials-children",
        FULL_17,
        V17,
        {
            f"{VR}__credentials": FULL_17["restore"]["velero_restores"]["credentials"]
            + [entry(child(gname(CRED_B, True) + "x", CRED_B))]
        },
    ),
    ("passive-restore-legacy-no-generic-child", PR_LEG, LEGACY, {f"{VR}__activation_resources_generic": []}),
    ("passive-restore-legacy-no-credentials-child", PR_LEG, LEGACY, {f"{VR}__activation_credentials": []}),
    (
        "passive-restore-2.17-no-child",
        journal("passive_restore", ACT, "completed"),
        V17,
        {f"{VR}__managed_clusters": []},
    ),
    ("passive-patch-legacy-no-generic-child", PATCH_L, LEGACY, {f"{VR}__activation_resources_generic": []}),
    ("passive-patch-2.17-no-credentials-child", PATCH_17, V17, {f"{VR}__activation_credentials": []}),
):
    jcase(f"journal-completion-count-{name}", lanes, COUNT_SPOTS, mut(doc, **changes), "completion_child_count")
jcase(
    "journal-passive-patch-completed-names-unverified",
    ALL,
    ("A:624-635",),
    mut(PATCH_L, restore__names_verified_at=None),
    "activation_names_unverified",
)
jcase(
    "journal-passive-patch-completed-post-activation-waiver-only",
    ALL,
    ("A:624-635", "J:459-466"),
    mut(PATCH_L, restore__names_verified_at=None, waiver=waiver("post_activation")),
    "activation_names_unverified",
)
jcase(
    "journal-teardown-revalidated-before-post-activation",
    ALL,
    ("J:614-619", "J:647-649"),
    mut(FULL_L, restore__teardown_revalidated_at=T_REVALIDATED),
    "teardown_revalidation_premature",
)
jcase(
    "journal-teardown-revalidated-before-completion",
    ALL,
    ("J:614-619",),
    mut(PRE_L, restore__teardown_revalidated_at=T_REVALIDATED, post_activation__completed_at=T_POST_DONE),
    "teardown_revalidation_premature",
)
POST = journal("full_restore", LEG, "post")
jcase(
    "journal-post-activation-completed-names-unverified",
    ALL,
    ("J:545-552",),
    mut(POST, post_activation__names_verified_at=None),
    "post_activation_names_unverified",
)
jcase(
    "journal-post-activation-completed-activation-waiver-only",
    ALL,
    ("J:545-552", "J:459-466"),
    mut(POST, post_activation__names_verified_at=None, waiver=waiver("activation")),
    "post_activation_names_unverified",
)
jcase(
    "journal-repair-run-id-mismatch",
    ALL,
    ("J:216-218", "J:774"),
    mut(RP, cleanup__repair__run_id=OTHER_UUID),
    "repair_identity_mismatch",
)
jcase(
    "journal-repair-operation-id-mismatch",
    ALL,
    ("J:216-218", "J:774"),
    mut(RP, cleanup__repair__operation_id=OTHER_UUID),
    "repair_identity_mismatch",
)
for name, path, value in (
    ("namespace", "cleanup__namespace", "default"),
    ("uid", "cleanup__uid", "uid-other"),
    ("generation", "cleanup__generation", 5),
    ("activation-method", "cleanup__activation_method", "passive"),
    ("mutation-kind", "cleanup__mutation_kind", "passive_restore"),
    ("spec-fingerprint", "cleanup__spec_fingerprint", "0" * 64),
    ("backup-fields", "cleanup__backup_fields", {MC_F: MC_B}),
):
    jcase(
        f"journal-cleanup-copy-{name}",
        ALL,
        ("J:187-196", "J:220-228", "C:282-283"),
        mut(IP, **{path: value}),
        "cleanup_copy_mismatch",
    )
for name, changes in (
    ("teardown-revalidation", {"restore__teardown_revalidated_at": None}),
    ("post-activation-completion", {"restore__teardown_revalidated_at": None, "post_activation__completed_at": None}),
):
    jcase(
        f"journal-cleanup-without-{name}",
        ALL,
        ("J:614-619", "J:621-653"),
        mut(IP, **changes),
        "cleanup_prerequisite_missing",
    )

# canonical_restore_projection and restore_spec_fingerprint (July §1a, August §6) --------------------------
FP_SPOTS = ("J:275-288", "A:662-686")
for label, doc in (
    ("passive-patch", journal("passive_patch", LEG)),
    ("passive-restore", journal("passive_restore", ACT)),
    ("full-restore", FULL_L),
    ("non-ascii", FULL_UNICODE),
):
    restore = doc["restore"]
    keys = ("activation_method", "mutation_kind", "backup_fields", "cleanup_before_restore")
    case(
        f"projection-{label}",
        ALL,
        drefs(ALL, *FP_SPOTS),
        "canonical_restore_projection",
        {"journal": doc},
        ok({key: restore[key] for key in keys}),
    )
    case(
        f"fingerprint-golden-{label}",
        ALL,
        drefs(ALL, *FP_SPOTS),
        "restore_spec_fingerprint",
        {"journal": doc},
        ok(fingerprint(restore)),
    )
for name, doc, code in (
    ("not-an-object", "journal", "malformed_restore"),
    ("missing-restore", {"backups": {}}, "malformed_restore"),
    ("missing-mutation-kind", mut(PRE_L, restore__mutation_kind=DELETE), "malformed_restore"),
    ("backup-field-not-a-string", mut(PRE_L, restore__backup_fields={MC_F: 1}), "malformed_restore"),
):
    for label, fn in (("projection", "canonical_restore_projection"), ("fingerprint", "restore_spec_fingerprint")):
        case(f"{label}-{name}", ALL, drefs(ALL, *FP_SPOTS), fn, {"journal": doc}, err(code))

# normalize_child_list (August §5 "Journaled child evidence", amendment-2 §5.3) ------------------------
fn = "normalize_child_list"
E_MC, E_CRED = entry(child(gname(MC_B), MC_B)), entry(child(gname(CRED_B), CRED_B))
E_EARLY = entry(child("a-" + gname(CRED_B), CRED_B))
NCL = drefs(ALL, "A:537-542", "C:280-281")
case("child-list-sorts", ALL, NCL, fn, {"entries": [E_MC, E_CRED, E_EARLY]}, ok([E_EARLY, E_CRED, E_MC]))
case("child-list-collapses-identical-duplicate", ALL, NCL, fn, {"entries": [E_MC, E_CRED, E_MC]}, ok([E_CRED, E_MC]))
case("child-list-empty", ALL, NCL, fn, {"entries": []}, ok([]))
case(
    "child-list-conflicting-duplicate-blocks",
    ALL,
    NCL,
    fn,
    {"entries": [E_MC, dict(E_MC, uid="uid-other")]},
    err("conflicting_child_evidence"),
)
case("child-list-not-a-list", ALL, NCL, fn, {"entries": E_MC}, err("malformed_child_entry"))
case("child-list-entry-without-name", ALL, NCL, fn, {"entries": [dict(E_MC, name="")]}, err("malformed_child_entry"))

# validate_cleanup_transition (July §4a state machine) --------------------------------------------------
fn = "validate_cleanup_transition"
CT = drefs(ALL, "J:657-672")
C_NS = cleanup_record("not_started")
DR = DONE_L["restore"]
C_IP = cleanup_record("intent_persisted", DR)
C_DA = cleanup_record("delete_accepted", DR)
C_RR = cleanup_record("recovery_required", DR)
C_RR_ACC = cleanup_record("recovery_required", DR, accepted=True)
C_CO = cleanup_record("completed", DR)
C_RP = cleanup_record("repaired", DR)
C_RP_ACC = cleanup_record("repaired", DR, accepted=True)
C_DA_RETRY = dict(C_DA, final_get_resource_version="2010", delete_accepted_at="2024-01-01T15:10:00Z")


def ctrans(case_id, previous, candidate, expect):
    result = ok(candidate) if expect is None else err(expect)
    case(case_id, ALL, CT, fn, {"previous_cleanup": previous, "candidate_cleanup": candidate}, result)


for name, previous, candidate in (
    ("not-started-idempotent", C_NS, C_NS),
    ("not-started-to-intent", C_NS, C_IP),
    ("intent-idempotent", C_IP, C_IP),
    ("intent-to-delete-accepted", C_IP, C_DA),
    ("intent-to-recovery", C_IP, C_RR),
    ("delete-accepted-retry", C_DA, C_DA_RETRY),
    ("delete-accepted-to-completed", C_DA, C_CO),
    ("delete-accepted-to-recovery", C_DA, C_RR_ACC),
    ("recovery-idempotent", C_RR, C_RR),
    ("recovery-to-repaired", C_RR, C_RP),
    ("recovery-after-accepted-to-repaired", C_RR_ACC, C_RP_ACC),
    ("completed-idempotent", C_CO, C_CO),
    ("repaired-idempotent", C_RP, C_RP),
):
    ctrans(f"cleanup-edge-{name}", previous, candidate, None)
for name, previous, candidate in (
    ("not-started-to-delete-accepted", C_NS, C_DA),
    ("not-started-to-completed", C_NS, C_CO),
    ("intent-to-completed", C_IP, C_CO),
    ("intent-to-repaired", C_IP, C_RP),
    ("intent-to-not-started", C_IP, C_NS),
    ("delete-accepted-to-intent", C_DA, C_IP),
    ("delete-accepted-to-repaired", C_DA, C_RP_ACC),
    ("recovery-to-completed", C_RR_ACC, C_CO),
    ("recovery-to-intent", C_RR, C_IP),
    ("completed-to-recovery", C_CO, C_RR_ACC),
    ("completed-to-not-started", C_CO, C_NS),
    ("repaired-to-recovery", C_RP, C_RR),
    ("repaired-to-completed", C_RP_ACC, C_CO),
):
    ctrans(f"cleanup-edge-{name}-blocks", previous, candidate, "invalid_cleanup_transition")
for name, previous, candidate in (
    ("intent-rewrites-intent-at", C_IP, dict(C_IP, intent_at="2024-01-01T15:09:00Z")),
    ("intent-to-delete-accepted-new-operation", C_IP, dict(C_DA, operation_id=OTHER_UUID)),
    ("retry-rewrites-intent-at", C_DA, dict(C_DA_RETRY, intent_at="2024-01-01T15:09:00Z")),
    ("completed-rewrites-accepted-pair", C_DA, dict(C_CO, delete_accepted_at="2024-01-01T15:10:00Z")),
    ("recovery-drops-accepted-pair", C_DA, C_RR),
    ("recovery-invents-accepted-pair", C_IP, C_RR_ACC),
    ("recovery-rewritten", C_RR, dict(C_RR, recovery=dict(C_RR["recovery"], required_at=T_REPAIR))),
    ("repaired-rewrites-recovery", C_RR, dict(C_RP, recovery=dict(C_RP["recovery"], reason_code="replacement_uid"))),
    ("repaired-drops-accepted-pair", C_RR_ACC, C_RP),
    ("completed-rewritten", C_CO, dict(C_CO, completed_at="2024-01-01T15:30:00Z")),
):
    ctrans(f"cleanup-edge-{name}-blocks", previous, candidate, "frozen_field_changed")
ctrans("cleanup-edge-invalid-candidate-blocks", C_IP, dict(C_DA, delete_accepted_at=None), "invalid_cleanup_state")
ctrans("cleanup-edge-invalid-previous-blocks", dict(C_NS, state="deleting"), C_IP, "malformed_cleanup")

# validate_journal_transition (July §§1a/4a, August §§4-5 and 10) --------------------------------------
fn = "validate_journal_transition"
JT = ("J:220-228", "J:354-366", "A:537-542", "A:777-788")


def jtrans(case_id, lanes, spots, previous, candidate, expect):
    result = ok(candidate) if expect is None else err(expect)
    case(case_id, lanes, drefs(lanes, *spots), fn, {"previous": previous, "candidate": candidate}, result)


for kind, contract in KINDS:
    jtrans(
        f"transition-freeze-write-{tag(kind, contract)}",
        JLANES[contract],
        ("A:152-163", "J:92-94"),
        None,
        journal(kind, contract),
        None,
    )
jtrans("transition-freeze-write-completed-blocks", ALL, ("A:152-163",), None, FULL_L, "invalid_freeze_write")
jtrans("transition-freeze-write-with-cleanup-blocks", ALL, ("A:152-163",), None, DONE_L, "invalid_freeze_write")
jtrans("transition-freeze-write-invalid-blocks", ALL, ("A:152-163",), None, mut(PRE_L, run_id=""), "malformed_journal")
jtrans("transition-invalid-previous-blocks", ALL, ("A:165-169",), mut(PRE_L, run_id=""), PRE_L, "malformed_journal")
PP_PATCHED = mut(journal("passive_patch", LEG), restore__generation=4)
for name, lanes, previous, candidate in (
    ("idempotent", ALL, PRE_L, PRE_L),
    ("pre-to-partial", LEGACY, PRE_L, PARTIAL),
    ("partial-to-completed", LEGACY, PARTIAL, FULL_L),
    ("pre-to-completed", LEGACY, PRE_L, FULL_L),
    ("passive-patch-accepted", LEGACY, journal("passive_patch", LEG), PP_PATCHED),
    ("passive-patch-completed", LEGACY, PP_PATCHED, PATCH_L),
    ("completed-to-post", LEGACY, FULL_L, POST),
    ("post-to-revalidated", LEGACY, POST, journal("full_restore", LEG, "revalidated")),
    ("cleanup-intent", LEGACY, journal("full_restore", LEG, "revalidated"), IP),
    ("cleanup-delete-accepted", LEGACY, IP, DA),
    ("cleanup-retry", LEGACY, DA, mut(DA, cleanup=C_DA_RETRY)),
    ("cleanup-completed", LEGACY, DA, CO),
    ("cleanup-repaired", LEGACY, RR, RP),
    (
        "child-added-before-existing",
        LEGACY,
        PARTIAL,
        mut(PARTIAL, **{f"{VR}__managed_clusters": [entry(child("a-" + gname(MC_B), MC_B)), MC_ENTRY]}),
    ),
    ("waiver-recorded", LEGACY, PRE_L, mut(PRE_L, waiver=waiver("both"))),
):
    jtrans(f"transition-{name}", lanes, JT, previous, candidate, None)
for name, previous, candidate in (
    ("run-id", PRE_L, mut(PRE_L, run_id=OTHER_UUID)),
    ("resolved-at", PRE_L, mut(PRE_L, resolved_at=T_VERIFIED)),
    ("backup-uid", PRE_L, mut(PRE_L, backups__credentials__uid="uid-replacement")),
    ("backup-warnings", PRE_L, mut(PRE_L, backups__credentials__warnings=1)),
    (
        "category-removed",
        journal("passive_restore", LEG),
        mut(journal("passive_restore", LEG), backups__activation_resources_generic=DELETE),
    ),
    (
        "category-added",
        mut(journal("passive_restore", LEG), backups__activation_resources_generic=DELETE),
        journal("passive_restore", LEG),
    ),
    ("restore-name", PRE_L, mut(PRE_L, restore__name="acm-restore-2")),
    ("acm-minor", PRE_L, mut(PRE_L, restore__acm_minor="2.15")),
    (
        "precondition",
        journal("passive_patch", LEG),
        mut(journal("passive_patch", LEG), restore__passive_patch_precondition__resource_version="1002"),
    ),
    ("restore-uid", PARTIAL, mut(PARTIAL, restore__uid="uid-replacement")),
    ("restore-generation", PARTIAL, mut(PARTIAL, restore__generation=5)),
    ("restore-uid-cleared", PARTIAL, mut(PARTIAL, restore__uid=None)),
    ("backup-names-verified-at", PARTIAL, mut(PARTIAL, restore__backup_names_verified_at=T_COMPLETED)),
    ("completed-at", FULL_L, mut(FULL_L, restore__completed_at=T_NAMES)),
    ("names-verified-at", FULL_L, mut(FULL_L, restore__names_verified_at=T_COMPLETED)),
    ("post-activation-completed-at", POST, mut(POST, post_activation__completed_at=T_REVALIDATED)),
    ("post-activation-names-cleared", POST, mut(POST, post_activation__names_verified_at=None, waiver=waiver("both"))),
    (
        "teardown-revalidated-at",
        journal("full_restore", LEG, "revalidated"),
        mut(journal("full_restore", LEG, "revalidated"), restore__teardown_revalidated_at=T_INTENT),
    ),
):
    jtrans(f"transition-{name}-changed-blocks", ALL, JT, previous, candidate, "frozen_field_changed")
for name, previous, candidate in (
    ("child-removed", PARTIAL, mut(PARTIAL, **{f"{VR}__managed_clusters": []})),
    (
        "child-uid-rewritten",
        PARTIAL,
        mut(PARTIAL, **{f"{VR}__managed_clusters__0__uid": "uid-other"}),
    ),
):
    jtrans(f"transition-{name}-blocks", ALL, JT, previous, candidate, "child_entry_rewritten")
jtrans(
    "transition-cleanup-skips-intent-blocks",
    ALL,
    ("J:657-672",),
    journal("full_restore", LEG, "revalidated"),
    DA,
    "invalid_cleanup_transition",
)
jtrans("transition-cleanup-terminal-blocks", ALL, ("J:668-670",), CO, RR, "invalid_cleanup_transition")
jtrans(
    "transition-cleanup-retry-rewrites-operation-blocks",
    ALL,
    ("J:663",),
    DA,
    mut(DA, cleanup=dict(C_DA_RETRY, operation_id=OTHER_UUID)),
    "frozen_field_changed",
)

# validate_waiver (July §2 and §4) ---------------------------------------------------------------------
fn = "validate_waiver"
WV = drefs(ALL, "J:454-470", "J:566-571")
NAMES = ["cluster-a", "cluster-b"]


def wcase(case_id, candidate, expected_names, scope, expect):
    result = ok(candidate) if expect is None else err(expect)
    inp = {"candidate": candidate, "expected_names": expected_names, "scope": scope}
    case(case_id, ALL, WV, fn, inp, result)


wcase("waiver-activation", waiver("activation"), NAMES, "activation", None)
wcase("waiver-both-covers-post-activation", waiver("both", request_id="CHG-1"), NAMES, "post_activation", None)
wcase("waiver-both-covers-both", waiver("both"), NAMES, "both", None)
wcase("waiver-null-blocks", None, NAMES, "activation", "malformed_waiver")
wcase("waiver-missing-reason-blocks", waiver("activation", reason=DELETE), NAMES, "activation", "malformed_waiver")
wcase(
    "waiver-journaled-at-malformed-blocks",
    waiver("activation", journaled_at="now"),
    NAMES,
    "activation",
    "malformed_waiver",
)
wcase("waiver-scope-not-covered-blocks", waiver("activation"), NAMES, "post_activation", "waiver_scope_mismatch")
wcase("waiver-partial-scope-for-both-blocks", waiver("post_activation"), NAMES, "both", "waiver_scope_mismatch")
wcase("waiver-unknown-requested-scope-blocks", waiver("both"), NAMES, "teardown", "waiver_scope_mismatch")
wcase("waiver-no-expected-names-blocks", waiver("both"), [], "activation", "waiver_expected_names_empty")
wcase("waiver-expected-names-not-a-list-blocks", waiver("both"), "cluster-a", "activation", "malformed_expected_names")
wcase("waiver-expected-name-empty-blocks", waiver("both"), ["cluster-a", ""], "activation", "malformed_expected_names")

# validate_repair (July §4a "Resume and operator repair") ------------------------------------------------
fn = "validate_repair"
RPS = drefs(ALL, "J:216-218", "J:766-787")
RR_ACC = journal("full_restore", LEG, "revalidated", "recovery_required", accepted=True)


def rcase(case_id, candidate, doc, expect):
    result = expect if isinstance(expect, dict) else err(expect)
    case(case_id, ALL, RPS, fn, {"candidate": candidate, "journal": doc}, result)


rcase("repair-recovery-required", REPAIR, RR, ok(dict(RR["cleanup"], state="repaired", repair=REPAIR)))
rcase(
    "repair-preserves-accepted-delete",
    REPAIR,
    RR_ACC,
    ok(dict(RR_ACC["cleanup"], state="repaired", repair=REPAIR)),
)
rcase("repair-delete-accepted-blocks", REPAIR, DA, "repair_not_permitted")
rcase("repair-completed-blocks", REPAIR, CO, "repair_not_permitted")
rcase("repair-repaired-blocks", REPAIR, RP, "repair_not_permitted")
rcase("repair-missing-actor-blocks", dict(REPAIR, actor=""), RR, "malformed_repair")
rcase("repair-extra-key-blocks", dict(REPAIR, absence_verified_at=T_ABSENT), RR, "malformed_repair")
rcase("repair-no-inspected-evidence-blocks", dict(REPAIR, inspected_evidence=[]), RR, "malformed_repair")
rcase("repair-run-id-mismatch-blocks", dict(REPAIR, run_id=OTHER_UUID), RR, "repair_identity_mismatch")
rcase("repair-operation-id-mismatch-blocks", dict(REPAIR, operation_id=OTHER_UUID), RR, "repair_identity_mismatch")
rcase("repair-invalid-journal-blocks", REPAIR, mut(RR, cleanup__recovery=None), "invalid_cleanup_state")


def build():
    """Return the fixture text: the vector document as indented JSON."""
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case id")
    doc = {
        "fixture_format": 1,
        "lanes": {m: {"controller_sha": sha, "controller_contract": contract} for m, (sha, contract) in LANES.items()},
        "cases": cases,
    }
    return json.dumps(doc, indent=2) + "\n"


if __name__ == "__main__":
    sys.stdout.write(build())
