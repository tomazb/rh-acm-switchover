"""Generate tests/fixtures/r4_04_migration_evidence_vectors.json.

Run ``python tests/fixtures/r4_04_migration_evidence_vectors_gen.py >
tests/fixtures/r4_04_migration_evidence_vectors.json`` after changing a vector.
The JSON is committed; tests/test_migration_evidence_parity.py requires it to
equal build() byte for byte, so the fixture is never hand-edited.
"""

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
