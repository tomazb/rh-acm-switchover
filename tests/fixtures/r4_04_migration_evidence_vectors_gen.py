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
