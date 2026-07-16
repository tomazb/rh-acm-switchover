"""Bounded semantic strategies shared by property tests.

The generators start with valid-shaped domain values, then apply small,
targeted mutations.  This keeps counterexamples readable and exercises the
interesting boundaries instead of spending examples on arbitrary blobs.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from typing import Any

from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import KNOWN_PHASES
from lib.constants import (
    CLUSTER_BACKUP_API_GROUP,
    CLUSTER_BACKUP_API_VERSION,
    VALIDATION_ACTIVATION_METHOD_CHOICES,
    VALIDATION_METHOD_CHOICES,
    VALIDATION_OLD_HUB_ACTION_CHOICES,
)
from lib.utils import Phase

ASCII_ALNUM = string.ascii_letters + string.digits
LOWER_ALNUM = string.ascii_lowercase + string.digits
DNS_LABEL_INTERIOR = LOWER_ALNUM + "-"
LABEL_INTERIOR = ASCII_ALNUM + "-_."
CONTEXT_INTERIOR = ASCII_ALNUM + "_.:-/@"
SAFE_IDENTIFIER_CHARS = ASCII_ALNUM + "._-"
UNSAFE_PATH_CHARS = "~${}|&;<>`"
CONTROL_PATH_CHARS = "\x01\t\n\r"
OVERLONG_PATH_COMPONENT_SIZE = 300


@dataclass(frozen=True)
class OperationIdentityCase:
    """Semantic inputs and independent expected output for one operation identity."""

    hubs: dict
    operation: dict
    collection_version: str | None
    hub_identities: dict
    kubeconfig_canaries: tuple[str, str]
    expected_identity: dict


@dataclass(frozen=True)
class LegacyIdentityCase:
    """A normalized identity augmented with legacy secrets and safe extensions."""

    identity: dict
    normalized: dict
    kubeconfig_canaries: tuple[str, str]


@dataclass(frozen=True)
class IdentityMismatchCase:
    """Two normalized identities differing in exactly one named field."""

    checkpoint: dict
    expected_identity: dict
    field: str


@dataclass(frozen=True)
class StateOperation:
    """One readable operation in a bounded StateManager command sequence."""

    name: str
    phase: Phase | None = None
    key: str | None = None
    value: Any = None


@dataclass(frozen=True)
class ContextPairCase:
    """Stored/current context pairs and the role intentionally changed, if any."""

    stored: dict[str, str]
    current: dict[str, str]
    changed_role: str | None


@dataclass(frozen=True)
class HubIdentityCase:
    """Verified primary/secondary contexts and live cluster UIDs."""

    identities: dict[str, dict[str, str]]


@dataclass(frozen=True)
class ReportStateCase:
    """Python report inputs plus canaries that must stay outside artifacts."""

    snapshot: dict
    secret_canaries: tuple[str, str, str]


@dataclass(frozen=True)
class ReportArgsCase:
    """Args-like report values and their independently normalized operation."""

    values: dict
    expected_operation: dict


@dataclass(frozen=True)
class CollectionPreflightCase:
    """Collection preflight inputs with an independent sanitized-hub oracle."""

    phase: str
    results: list[dict]
    hubs: dict
    hub_identities: dict
    expected_hubs: dict
    secret_canaries: tuple[str, ...]


@dataclass(frozen=True)
class ValidFileModeCase:
    """One supported artifact mode and its expected integer value."""

    value: str | int
    expected: int


@dataclass(frozen=True)
class CheckModeCase:
    """One explicit existing/desired artifact state for check-mode prediction."""

    scenario: str
    existing_report: object
    desired_report: object
    existing_mode: int
    desired_mode: int
    initially_exists: bool
    expected_changed: bool


@dataclass(frozen=True)
class AcmVersionCase:
    """A parseable ACM version paired with its independent numeric meaning."""

    value: str
    components: tuple[int, int, int]


@dataclass(frozen=True)
class ArgocdApplicationCase:
    """One semantic Argo CD Application and its independent safety oracle."""

    app: dict[str, Any]
    autosync_enabled: bool
    resource_state: str
    acm_resource_count: int
    applicationset_owned: bool


@dataclass(frozen=True)
class ArgocdResumeCase:
    """One resume target with explicit marker and auto-sync state."""

    namespace: str
    name: str
    run_id: str
    current_app: dict[str, Any]
    original_sync_policy: dict[str, Any]
    marker_mode: str
    autosync_enabled: bool


@st.composite
def _bounded_token(
    draw: st.DrawFn,
    *,
    first_alphabet: str,
    interior_alphabet: str,
    last_alphabet: str,
    max_size: int,
) -> str:
    """Build a token whose first and last characters come from a safe set."""
    size = draw(st.integers(min_value=1, max_value=max_size))
    first = draw(st.sampled_from(tuple(first_alphabet)))
    if size == 1:
        return first

    middle = draw(st.text(alphabet=interior_alphabet, min_size=size - 2, max_size=size - 2))
    last = draw(st.sampled_from(tuple(last_alphabet)))
    return first + middle + last


def _dns_label(*, max_size: int = 63, leading_letter: bool = False) -> SearchStrategy[str]:
    boundary = string.ascii_lowercase if leading_letter else LOWER_ALNUM
    return _bounded_token(
        first_alphabet=boundary,
        interior_alphabet=DNS_LABEL_INTERIOR,
        last_alphabet=LOWER_ALNUM,
        max_size=max_size,
    )


@st.composite
def _dns_subdomain(draw: st.DrawFn) -> str:
    labels = draw(st.lists(_dns_label(), min_size=1, max_size=3))
    return ".".join(labels)


@st.composite
def _invalid_kubernetes_name(draw: st.DrawFn) -> str:
    base = draw(_dns_subdomain())
    mutation = draw(
        st.sampled_from(
            (
                "leading_hyphen",
                "trailing_hyphen",
                "leading_dot",
                "trailing_dot",
                "uppercase",
                "whitespace",
                "unicode",
                "empty_segment",
            )
        )
    )
    if mutation == "leading_hyphen":
        return "-" + base
    if mutation == "trailing_hyphen":
        return base + "-"
    if mutation == "leading_dot":
        return "." + base
    if mutation == "trailing_dot":
        return base + "."
    if mutation == "uppercase":
        return "A" + base
    if mutation == "whitespace":
        return base[0] + " " + base
    if mutation == "unicode":
        return base[0] + "é" + base
    return base + "..segment"


def kubernetes_name_candidates() -> SearchStrategy[str]:
    """Generate valid DNS-like names and focused invalid mutations."""
    boundaries = st.sampled_from(("", "a", "1", "a" * 63, "a" * 253, "a" * 254))
    return st.one_of(_dns_subdomain(), _invalid_kubernetes_name(), boundaries)


@st.composite
def _invalid_namespace(draw: st.DrawFn) -> str:
    base = draw(_dns_label(max_size=30, leading_letter=True))
    mutation = draw(
        st.sampled_from(
            (
                "leading_hyphen",
                "trailing_hyphen",
                "digit_prefix",
                "uppercase",
                "dot",
                "whitespace",
                "unicode",
            )
        )
    )
    if mutation == "leading_hyphen":
        return "-" + base
    if mutation == "trailing_hyphen":
        return base + "-"
    if mutation == "digit_prefix":
        return "1" + base
    if mutation == "uppercase":
        return "A" + base
    if mutation == "dot":
        return base + ".segment"
    if mutation == "whitespace":
        return base[0] + "\t" + base
    return base[0] + "λ" + base


def kubernetes_namespace_candidates() -> SearchStrategy[str]:
    """Generate namespace labels with the project's leading-letter rule."""
    boundaries = st.sampled_from(("", "a", "a" * 63, "a" * 64, "1namespace"))
    return st.one_of(_dns_label(leading_letter=True), _invalid_namespace(), boundaries)


def _label_component(*, max_size: int = 63) -> SearchStrategy[str]:
    return _bounded_token(
        first_alphabet=ASCII_ALNUM,
        interior_alphabet=LABEL_INTERIOR,
        last_alphabet=ASCII_ALNUM,
        max_size=max_size,
    )


@st.composite
def _valid_label_key(draw: st.DrawFn) -> str:
    if draw(st.booleans()):
        return draw(_label_component())

    prefix = draw(_label_component(max_size=31))
    name = draw(_label_component(max_size=31))
    return f"{prefix}/{name}"


@st.composite
def _invalid_label_key(draw: st.DrawFn) -> str:
    base = draw(_label_component(max_size=30))
    mutation = draw(
        st.sampled_from(
            (
                "leading_punctuation",
                "trailing_punctuation",
                "whitespace",
                "shell_character",
                "unicode",
                "extra_slash",
            )
        )
    )
    if mutation == "leading_punctuation":
        return "_" + base
    if mutation == "trailing_punctuation":
        return base + "."
    if mutation == "whitespace":
        return base[0] + " " + base
    if mutation == "shell_character":
        return base[0] + "@" + base
    if mutation == "unicode":
        return base[0] + "é" + base
    return f"prefix/{base}/extra"


def kubernetes_label_key_candidates() -> SearchStrategy[str]:
    """Generate label keys under the implementation's total-length contract."""
    boundaries = st.sampled_from(("", "a", "A", "1", "a" * 63, "a" * 64, "prefix/name"))
    return st.one_of(_valid_label_key(), _invalid_label_key(), boundaries)


@st.composite
def _invalid_label_value(draw: st.DrawFn) -> str:
    base = draw(_label_component(max_size=30))
    mutation = draw(st.sampled_from(("leading", "trailing", "whitespace", "shell_character", "unicode")))
    if mutation == "leading":
        return "-" + base
    if mutation == "trailing":
        return base + "_"
    if mutation == "whitespace":
        return base[0] + "\n" + base
    if mutation == "shell_character":
        return base[0] + "@" + base
    return base[0] + "λ" + base


def kubernetes_label_value_candidates() -> SearchStrategy[str | None]:
    """Generate valid, empty, over-length, invalid, and ``None`` label values."""
    boundaries: SearchStrategy[str | None] = st.sampled_from((None, "", "a", "A", "1", "a" * 63, "a" * 64))
    return st.one_of(_label_component(), _invalid_label_value(), boundaries)


def _valid_context_name(*, max_size: int = 128) -> SearchStrategy[str]:
    generated = _bounded_token(
        first_alphabet=ASCII_ALNUM,
        interior_alphabet=CONTEXT_INTERIOR,
        last_alphabet=ASCII_ALNUM,
        max_size=max_size,
    )
    realistic = st.sampled_from(
        (
            "primary-hub",
            "cluster/user",
            "admin/api.example.com:6443",
            "default/api.example.com:6443/admin",
            "user_name@cluster-1",
        )
    )
    return st.one_of(generated, realistic)


@st.composite
def _invalid_context_name(draw: st.DrawFn) -> str:
    base = draw(_valid_context_name(max_size=64))
    mutation = draw(
        st.sampled_from(
            (
                "leading_punctuation",
                "trailing_punctuation",
                "shell_character",
                "control_character",
                "whitespace",
            )
        )
    )
    if mutation == "leading_punctuation":
        return "/" + base
    if mutation == "trailing_punctuation":
        return base + "@"
    if mutation == "shell_character":
        return base[0] + ";" + base
    if mutation == "control_character":
        return base[0] + "\x00" + base
    return base[0] + " " + base


def context_name_candidates() -> SearchStrategy[str]:
    """Generate realistic context names and targeted unsafe variants."""
    boundaries = st.sampled_from(("", "a", "Z", "9", "a" * 128, "a" * 129))
    return st.one_of(_valid_context_name(), _invalid_context_name(), boundaries)


def non_empty_string_candidates() -> SearchStrategy[str]:
    """Generate bounded human strings plus empty and whitespace-only values."""
    meaningful = st.text(
        alphabet=ASCII_ALNUM + " -_./:@",
        min_size=1,
        max_size=80,
    ).map(lambda value: "x" + value)
    whitespace = st.sampled_from(("", " ", "\t", "\n", " \t\n "))
    return st.one_of(meaningful, whitespace)


def context_identifier_inputs() -> SearchStrategy[str]:
    """Generate bounded sanitizer inputs from safe and deliberately unsafe characters."""
    alphabet = SAFE_IDENTIFIER_CHARS + " /:@;\t\n" + "éλ"
    examples = st.sampled_from(("", "normal-context", "cluster/user", "my context", "a@b!"))
    return st.one_of(st.text(alphabet=alphabet, min_size=0, max_size=128), examples)


def _choice_candidates(valid: tuple[str, ...], misspellings: tuple[str, ...]) -> SearchStrategy[str]:
    variants = [""]
    for value in valid:
        variants.extend((value, value.upper(), value.capitalize(), f"{value}x"))
    variants.extend(misspellings)
    return st.sampled_from(tuple(dict.fromkeys(variants)))


def method_candidates() -> SearchStrategy[str]:
    """Generate documented methods plus readable invalid alternatives."""
    return _choice_candidates(VALIDATION_METHOD_CHOICES, ("passive-sync", "restore", "invalid"))


def activation_method_candidates() -> SearchStrategy[str]:
    """Generate documented activation methods plus readable invalid alternatives."""
    return _choice_candidates(VALIDATION_ACTIVATION_METHOD_CHOICES, ("patched", "restored", "invalid"))


def old_hub_action_candidates() -> SearchStrategy[str]:
    """Generate documented old-hub actions plus readable invalid alternatives."""
    return _choice_candidates(
        VALIDATION_OLD_HUB_ACTION_CHOICES,
        ("keep", "remove", "decommision", "invalid"),
    )


def safe_path_components() -> SearchStrategy[str]:
    """Generate short, readable path components with no hostile syntax."""
    return _bounded_token(
        first_alphabet=ASCII_ALNUM,
        interior_alphabet=SAFE_IDENTIFIER_CHARS,
        last_alphabet=ASCII_ALNUM,
        max_size=16,
    )


def safe_relative_paths() -> SearchStrategy[str]:
    """Generate bounded relative paths composed only of safe components."""
    return st.lists(safe_path_components(), min_size=1, max_size=4).map("/".join)


@st.composite
def _relative_traversal_path_candidates(draw: st.DrawFn) -> str:
    """Generate relative paths with ``..`` as an exact path component."""
    prefix = draw(safe_path_components())
    suffix = draw(safe_path_components())
    template = draw(
        st.sampled_from(
            (
                "../{suffix}",
                "{prefix}/../{suffix}",
                "{prefix}/..",
                "tmp/../{suffix}",
                "{prefix}//..//{suffix}",
                "{prefix}/../{suffix}/",
                "../../{suffix}",
            )
        )
    )
    return template.format(prefix=prefix, suffix=suffix)


def traversal_path_candidates() -> SearchStrategy[str]:
    """Generate relative and absolute paths containing exact traversal components."""
    absolute = st.sampled_from(("/tmp/../etc", "/tmp//../etc/"))
    return st.one_of(_relative_traversal_path_candidates(), absolute)


@st.composite
def report_artifact_traversal_paths(draw: st.DrawFn) -> str:
    """Generate writer traversal attempts whose lexical target stays under ``tmp_path``."""
    prefix = draw(safe_path_components())
    suffix = draw(safe_path_components())
    template = draw(st.sampled_from(("../{suffix}.json", "{prefix}/../../{suffix}.json")))
    return template.format(prefix=prefix, suffix=suffix)


@st.composite
def unsafe_metacharacter_paths(draw: st.DrawFn) -> str:
    """Generate paths with one shipped shell metacharacter and no traversal."""
    prefix = draw(safe_path_components())
    suffix = draw(safe_path_components())
    unsafe = draw(st.sampled_from(tuple(UNSAFE_PATH_CHARS)))
    template = draw(st.sampled_from(("{unsafe}{prefix}", "{prefix}{unsafe}{suffix}", "{prefix}/{unsafe}{suffix}")))
    return template.format(prefix=prefix, suffix=suffix, unsafe=unsafe)


def broad_path_syntax_candidates() -> SearchStrategy[str]:
    """Generate bounded syntax-only candidates, including OS-unrepresentable values."""
    safe = safe_relative_paths()
    controls = st.tuples(safe_path_components(), st.sampled_from(tuple(CONTROL_PATH_CHARS))).map(
        lambda pair: pair[0] + pair[1] + "tail"
    )
    overlong = st.sampled_from(
        (
            "a" * OVERLONG_PATH_COMPONENT_SIZE,
            f"prefix/{'b' * OVERLONG_PATH_COMPONENT_SIZE}/suffix",
        )
    )
    return st.one_of(
        st.just(""),
        safe,
        safe.map(lambda value: f"/tmp/{value}"),
        safe.map(lambda value: f"./{value}"),
        safe.map(lambda value: f"prefix/./{value}"),
        safe.map(lambda value: value.replace("/", "//", 1) if "/" in value else f"{value}//tail"),
        safe.map(lambda value: value + "/"),
        safe.map(lambda value: f"~/{value}"),
        traversal_path_candidates(),
        unsafe_metacharacter_paths(),
        safe.map(lambda value: value + "\x00tail"),
        controls,
        overlong,
    )


def filesystem_resolvable_relative_paths() -> SearchStrategy[str]:
    """Generate host-representable relative candidates without NUL or long names."""
    structural = st.sampled_from(("", ".", "./safe", "safe//nested", "safe/nested/"))
    return st.one_of(
        safe_relative_paths(),
        _relative_traversal_path_candidates(),
        unsafe_metacharacter_paths(),
        structural,
    )


def missing_descendant_suffixes() -> SearchStrategy[str]:
    """Generate bounded safe suffixes below an existing directory ancestor."""
    return st.lists(safe_path_components(), min_size=1, max_size=3).map("/".join)


@st.composite
def artifact_relative_paths(draw: st.DrawFn) -> str:
    """Generate safe relative JSON artifact destinations with bounded nesting."""
    directories = draw(st.lists(safe_path_components(), min_size=0, max_size=3))
    filename = draw(safe_path_components()) + ".json"
    return "/".join([*directories, filename])


@st.composite
def operation_identity_cases(draw: st.DrawFn) -> OperationIdentityCase:
    """Generate bounded hub/operation inputs with kubeconfig canaries and defaults."""
    primary_context = draw(_valid_context_name(max_size=32))
    secondary_context = draw(_valid_context_name(max_size=32))
    primary_hub_uid = "uid-primary-hub-" + draw(safe_path_components())
    secondary_hub_uid = "uid-secondary-hub-" + draw(safe_path_components())
    primary_fallback_uid = "uid-primary-fallback-" + draw(safe_path_components())
    secondary_fallback_uid = "uid-secondary-fallback-" + draw(safe_path_components())
    primary_canary = "pbt-primary-kubeconfig-canary-" + draw(safe_path_components())
    secondary_canary = "pbt-secondary-kubeconfig-canary-" + draw(safe_path_components())
    primary_uid_in_hub = draw(st.booleans())
    secondary_uid_in_hub = draw(st.booleans())

    hubs = {
        "primary": {
            "context": primary_context,
            "kubeconfig": primary_canary,
            **({"cluster_uid": primary_hub_uid} if primary_uid_in_hub else {}),
        },
        "secondary": {
            "context": secondary_context,
            "kubeconfig": secondary_canary,
            **({"cluster_uid": secondary_hub_uid} if secondary_uid_in_hub else {}),
        },
    }
    hub_identities = {
        "primary": {"cluster_uid": primary_fallback_uid},
        "secondary": {"cluster_uid": secondary_fallback_uid},
    }
    method = draw(st.one_of(st.none(), st.just(""), st.sampled_from(VALIDATION_METHOD_CHOICES)))
    activation_method = draw(st.one_of(st.none(), st.just(""), st.sampled_from(VALIDATION_ACTIVATION_METHOD_CHOICES)))
    restore_only = draw(st.one_of(st.none(), st.booleans()))
    old_hub_action = draw(st.one_of(st.none(), st.just(""), st.sampled_from(VALIDATION_OLD_HUB_ACTION_CHOICES)))
    collection_version = draw(
        st.one_of(
            st.none(),
            st.just(""),
            st.tuples(
                st.integers(min_value=0, max_value=9),
                st.integers(min_value=0, max_value=20),
                st.integers(min_value=0, max_value=20),
            ).map(lambda version: ".".join(map(str, version))),
        )
    )
    operation = {
        "method": method,
        "activation_method": activation_method,
        "restore_only": restore_only,
        "old_hub_action": old_hub_action,
    }
    normalized_restore_only = False if restore_only is None else restore_only
    expected_identity = {
        "primary_context": primary_context,
        "secondary_context": secondary_context,
        "primary_cluster_uid": primary_hub_uid if primary_uid_in_hub else primary_fallback_uid,
        "secondary_cluster_uid": secondary_hub_uid if secondary_uid_in_hub else secondary_fallback_uid,
        "method": method or ("full" if normalized_restore_only else "passive"),
        "activation_method": activation_method or "patch",
        "restore_only": normalized_restore_only,
        "old_hub_action": old_hub_action or ("none" if normalized_restore_only else "secondary"),
        "collection_version": collection_version or "",
    }
    return OperationIdentityCase(
        hubs=hubs,
        operation=operation,
        collection_version=collection_version,
        hub_identities=hub_identities,
        kubeconfig_canaries=(primary_canary, secondary_canary),
        expected_identity=expected_identity,
    )


def json_native_values() -> SearchStrategy[Any]:
    """Generate bounded values whose shape survives JSON serialization unchanged."""
    scalar = st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-1000, max_value=1000),
        st.floats(min_value=-1000, max_value=1000, allow_nan=False, allow_infinity=False),
        st.text(alphabet=ASCII_ALNUM + " _-./", min_size=0, max_size=24),
    )
    keys = st.text(alphabet=string.ascii_lowercase + "_", min_size=1, max_size=12)
    return st.recursive(
        scalar,
        lambda children: st.one_of(
            st.lists(children, min_size=0, max_size=4),
            st.dictionaries(keys, children, min_size=0, max_size=4),
        ),
        max_leaves=10,
    )


@st.composite
def parseable_acm_versions(draw: st.DrawFn) -> AcmVersionCase:
    """Generate readable accepted ACM versions with a retained numeric oracle."""
    representative_pairs = st.sampled_from(tuple((2, minor) for minor in range(10, 18)))
    bounded_pairs = st.tuples(
        st.integers(min_value=0, max_value=4),
        st.integers(min_value=0, max_value=20),
    )
    major, minor = draw(st.one_of(representative_pairs, bounded_pairs))
    include_patch = draw(st.booleans())
    patch = draw(st.integers(min_value=0, max_value=999)) if include_patch else 0
    core = f"{major}.{minor}.{patch}" if include_patch else f"{major}.{minor}"
    suffix = draw(
        st.sampled_from(
            (
                "",
                "-rc1",
                "-beta.2",
                "-candidate-3",
                "+build",
                "+build.7",
                "-rc1+build.7",
            )
        )
    )
    leading, trailing = draw(st.sampled_from((("", ""), (" ", ""), ("", " "), (" \t", "\n"))))
    return AcmVersionCase(
        value=f"{leading}{core}{suffix}{trailing}",
        components=(major, minor, patch),
    )


def unparseable_acm_versions() -> SearchStrategy[str]:
    """Generate small targeted malformed versions that both parsers must reject."""
    fixed = st.sampled_from(
        (
            "",
            " ",
            "\t\n",
            "2",
            "x.14",
            "2.x",
            "2.14.x",
            "2.14.3.1",
            "2.14.3rc1",
            "2.14.-rc1",
            "2.14.3-",
            "2.14.3+",
            "2..14",
            ".2.14",
            "v2.14.3",
            "2.14.-3",
            "2.14.3 rc1",
        )
    )
    numeric = st.integers(min_value=0, max_value=20).map(str)
    generated = st.one_of(
        numeric,
        st.tuples(numeric, numeric, numeric, numeric).map(".".join),
        st.tuples(numeric, numeric, numeric).map(lambda parts: ".".join(parts) + "rc1"),
        st.tuples(numeric, numeric).map(lambda parts: f"{parts[0]}.x{parts[1]}"),
    )
    return st.one_of(fixed, generated)


def _backup_schedule_names() -> SearchStrategy[str]:
    """Generate bounded Kubernetes-like BackupSchedule names."""
    return _dns_label(max_size=24)


def _backup_schedule_specs() -> SearchStrategy[dict[str, Any]]:
    """Generate a small real-shaped BackupSchedule spec."""
    schedules = st.sampled_from(("0 */6 * * *", "*/30 * * * *", "15 2 * * 1", "0 0 * * *"))
    return st.fixed_dictionaries(
        {"veleroSchedule": schedules},
        optional={"paused": st.one_of(st.none(), st.booleans())},
    )


@st.composite
def _backup_schedule_resources(draw: st.DrawFn) -> dict[str, Any]:
    """Generate named and unnamed BackupSchedules without arbitrary blobs."""
    metadata_shape = draw(st.sampled_from(("named", "missing_name", "empty_name", "null_name", "missing_metadata")))
    schedule: dict[str, Any] = {
        "apiVersion": f"{CLUSTER_BACKUP_API_GROUP}/{CLUSTER_BACKUP_API_VERSION}",
        "kind": "BackupSchedule",
        "spec": draw(_backup_schedule_specs()),
    }
    if metadata_shape == "named":
        schedule["metadata"] = {"name": draw(_backup_schedule_names())}
    elif metadata_shape == "missing_name":
        schedule["metadata"] = {"labels": {"pbt-purpose": "multiplicity"}}
    elif metadata_shape == "empty_name":
        schedule["metadata"] = {"name": ""}
    elif metadata_shape == "null_name":
        schedule["metadata"] = {"name": None}
    return schedule


def backup_schedule_lists(*, min_size: int = 0) -> SearchStrategy[list[dict[str, Any]]]:
    """Generate bounded BackupSchedule lists spanning every multiplicity class."""
    return st.lists(_backup_schedule_resources(), min_size=min_size, max_size=5)


def _string_map(*, max_size: int = 3) -> SearchStrategy[dict[str, str]]:
    """Generate bounded harmless metadata maps."""
    keys = _dns_label(max_size=16)
    values = st.text(alphabet=ASCII_ALNUM + " ._-", min_size=0, max_size=24)
    return st.dictionaries(keys, values, min_size=0, max_size=max_size)


@st.composite
def saved_backup_schedule_bodies(draw: st.DrawFn) -> dict[str, Any]:
    """Generate semantic saved BackupSchedules with runtime and extension fields."""
    metadata: dict[str, Any] = {
        "name": draw(_backup_schedule_names()),
        "labels": draw(_string_map()),
        "annotations": draw(_string_map()),
        "uid": "uid-" + draw(safe_path_components()),
        "resourceVersion": str(draw(st.integers(min_value=1, max_value=9999))),
        "creationTimestamp": draw(
            st.sampled_from(("2024-01-02T03:04:05Z", "2025-06-15T12:30:00Z", "2026-12-31T23:59:59Z"))
        ),
        "generation": draw(st.integers(min_value=1, max_value=100)),
        "managedFields": draw(
            st.lists(
                st.fixed_dictionaries(
                    {
                        "manager": safe_path_components(),
                        "operation": st.sampled_from(("Apply", "Update")),
                    }
                ),
                min_size=0,
                max_size=3,
            )
        ),
        "pbtMetadataExtension": draw(json_native_values()),
    }
    if draw(st.booleans()):
        metadata["namespace"] = draw(_dns_label(max_size=24))

    body: dict[str, Any] = {
        "apiVersion": f"{CLUSTER_BACKUP_API_GROUP}/{CLUSTER_BACKUP_API_VERSION}",
        "kind": "BackupSchedule",
        "metadata": metadata,
        "pbtTopLevelExtension": draw(json_native_values()),
    }
    if draw(st.booleans()):
        spec = draw(_backup_schedule_specs())
        spec["pbtSpecExtension"] = draw(json_native_values())
        body["spec"] = spec
    if draw(st.booleans()):
        body["status"] = {
            "phase": draw(st.sampled_from(("Enabled", "Paused", "Unknown"))),
            "pbtStatusExtension": draw(json_native_values()),
        }
    return body


def no_saved_backup_schedule_values() -> SearchStrategy[Any]:
    """Generate every representative falsey value treated as no saved schedule."""
    return st.one_of(
        st.none(),
        st.just(False),
        st.just(0),
        st.just(""),
        st.just([]),
        st.just({}),
    )


@st.composite
def normalized_operation_identities(draw: st.DrawFn) -> dict:
    """Generate complete normalized identities with one retained extension field."""
    case = draw(operation_identity_cases())
    extension_value = draw(json_native_values())
    return {
        **case.expected_identity,
        "retained_extension": extension_value,
    }


@st.composite
def legacy_operation_identities(draw: st.DrawFn) -> LegacyIdentityCase:
    """Generate identities proving legacy secret removal is exact."""
    normalized = draw(normalized_operation_identities())
    primary_canary = "pbt-legacy-primary-canary-" + draw(safe_path_components())
    secondary_canary = "pbt-legacy-secondary-canary-" + draw(safe_path_components())
    identity = {
        **normalized,
        "primary_kubeconfig": primary_canary,
        "secondary_kubeconfig": secondary_canary,
    }
    return LegacyIdentityCase(
        identity=identity,
        normalized=normalized,
        kubeconfig_canaries=(primary_canary, secondary_canary),
    )


IDENTITY_MISMATCH_FIELDS = (
    "primary_context",
    "secondary_context",
    "primary_cluster_uid",
    "secondary_cluster_uid",
    "method",
    "activation_method",
    "restore_only",
    "old_hub_action",
    "collection_version",
    "retained_extension",
)


def retained_extension_mismatch_value(expected: Any, generated_component: str) -> Any:
    """Return a readable retained-extension value guaranteed to differ from expected."""
    candidate = {"mismatch": generated_component}
    if candidate == expected:
        return "forced-different-type-mismatch"
    return candidate


@st.composite
def mismatched_operation_identities(draw: st.DrawFn, field: str) -> IdentityMismatchCase:
    """Generate normalized identities differing in exactly ``field``."""
    expected = draw(normalized_operation_identities())
    actual = dict(expected)
    if field == "restore_only":
        actual[field] = not bool(expected[field])
    elif field == "retained_extension":
        actual[field] = retained_extension_mismatch_value(expected[field], draw(safe_path_components()))
    else:
        actual[field] = f"{expected[field]}-mismatch"
    return IdentityMismatchCase(
        checkpoint={"operation_identity": actual},
        expected_identity=expected,
        field=field,
    )


def completed_phase_lists(*, duplicates: bool = False, min_size: int = 0) -> SearchStrategy[list[str]]:
    """Generate bounded phase lists from the collection's real workflow order domain."""
    return st.lists(
        st.sampled_from(KNOWN_PHASES),
        min_size=min_size,
        max_size=8 if duplicates else len(KNOWN_PHASES),
        unique=not duplicates,
    )


@st.composite
def semantic_checkpoints(draw: st.DrawFn) -> dict:
    """Generate shaped checkpoint records without corrupt or arbitrary blob cases."""
    schema_version = draw(st.sampled_from(("1.0", "2.0", "3.0")))
    phase = draw(st.sampled_from(KNOWN_PHASES))
    completed_phases = draw(completed_phase_lists())
    identity = draw(st.one_of(st.none(), normalized_operation_identities()))
    operational_data = draw(st.dictionaries(safe_path_components(), json_native_values(), max_size=4))
    errors = draw(
        st.lists(
            st.fixed_dictionaries({"phase": st.sampled_from(KNOWN_PHASES), "error": safe_path_components()}),
            max_size=3,
        )
    )
    report_refs = draw(
        st.lists(
            st.fixed_dictionaries(
                {
                    "phase": st.sampled_from(KNOWN_PHASES),
                    "path": artifact_relative_paths(),
                    "kind": st.just("json-report"),
                }
            ),
            max_size=3,
        )
    )
    return {
        "schema_version": schema_version,
        "phase": phase,
        "completed_phases": completed_phases,
        "operation_identity": identity,
        "operational_data": operational_data,
        "errors": errors,
        "report_refs": report_refs,
    }


def readable_step_names() -> SearchStrategy[str]:
    """Generate short workflow-like StateManager step names."""
    return st.sampled_from(
        (
            "validate_hubs",
            "pause_backups",
            "disable_auto_import",
            "activate_restore",
            "verify_clusters",
            "enable_backups",
        )
    )


def _state_mutations() -> SearchStrategy[StateOperation]:
    config_keys = st.sampled_from(("method", "versions", "flags", "retry_count"))
    return st.one_of(
        st.sampled_from(tuple(Phase)).map(lambda phase: StateOperation("set_phase", phase=phase)),
        readable_step_names().map(lambda step: StateOperation("mark_step", key=step)),
        readable_step_names().map(lambda step: StateOperation("clear_step", key=step)),
        st.tuples(config_keys, json_native_values()).map(
            lambda item: StateOperation("set_config", key=item[0], value=item[1])
        ),
    )


@st.composite
def state_manager_operation_sequences(draw: st.DrawFn) -> list[StateOperation]:
    """Generate readable valid histories, optionally including snapshot rollback."""
    prefix = draw(st.lists(_state_mutations(), min_size=0, max_size=6))
    suffix = draw(st.lists(_state_mutations(), min_size=0, max_size=4))
    if not draw(st.booleans()):
        return prefix + suffix
    after_snapshot = draw(st.lists(_state_mutations(), min_size=1, max_size=5))
    return [*prefix, StateOperation("capture_snapshot"), *after_snapshot, StateOperation("restore_snapshot"), *suffix]


@st.composite
def context_pair_cases(draw: st.DrawFn) -> ContextPairCase:
    """Generate matching pairs or a pair differing in exactly one hub role."""
    stored = {
        "primary": draw(_valid_context_name(max_size=24)),
        "secondary": draw(_valid_context_name(max_size=24)),
    }
    changed_role = draw(st.one_of(st.none(), st.sampled_from(("primary", "secondary"))))
    current = dict(stored)
    if changed_role is not None:
        current[changed_role] = stored[changed_role] + "-changed"
    return ContextPairCase(stored=stored, current=current, changed_role=changed_role)


@st.composite
def hub_identity_cases(draw: st.DrawFn) -> HubIdentityCase:
    """Generate verified live identity bindings for both hub roles."""
    primary_context = draw(_valid_context_name(max_size=24))
    secondary_context = draw(_valid_context_name(max_size=24))
    primary_uid = "uid-primary-" + draw(safe_path_components())
    secondary_uid = "uid-secondary-" + draw(safe_path_components())
    return HubIdentityCase(
        identities={
            "primary": {"context": primary_context, "cluster_uid": primary_uid},
            "secondary": {"context": secondary_context, "cluster_uid": secondary_uid},
        }
    )


def report_text(*, min_size: int = 0, max_size: int = 32) -> SearchStrategy[str]:
    """Generate bounded readable report text without credential-like material."""
    return st.text(alphabet=ASCII_ALNUM + " _-./:@", min_size=min_size, max_size=max_size)


def legacy_validation_results() -> SearchStrategy[dict]:
    """Generate the legacy Python ValidationReporter result shape."""
    return st.fixed_dictionaries(
        {
            "check": report_text(max_size=32),
            "passed": st.booleans(),
            "critical": st.booleans(),
            "message": report_text(max_size=64),
        },
        optional={
            "errors": st.lists(report_text(max_size=24), max_size=3),
            "warnings": st.lists(report_text(max_size=24), max_size=3),
            "unsupported_extension": json_native_values(),
        },
    )


def structured_validation_results(*, include_extensions: bool = False) -> SearchStrategy[dict]:
    """Generate complete structured validation findings with JSON-native detail."""
    optional = {"structured_extension": json_native_values()} if include_extensions else None
    return st.fixed_dictionaries(
        {
            "id": report_text(min_size=1, max_size=32),
            "severity": st.sampled_from(("critical", "warning", "info")),
            "status": st.sampled_from(("pass", "fail", "error", "warning")),
            "message": report_text(max_size=64),
            "details": st.dictionaries(
                report_text(min_size=1, max_size=12),
                json_native_values(),
                max_size=4,
            ),
            "recommended_action": st.one_of(st.none(), report_text(max_size=48)),
        },
        optional=optional,
    )


def preflight_result_lists() -> SearchStrategy[list[dict]]:
    """Generate small structured collection preflight result lists."""
    return st.lists(structured_validation_results(), min_size=0, max_size=8)


@st.composite
def report_state_cases(draw: st.DrawFn) -> ReportStateCase:
    """Generate state snapshots consumed by the Python operation report builder."""
    errors = draw(st.one_of(st.none(), st.lists(json_native_values(), max_size=5)))
    completed_steps = draw(
        st.one_of(
            st.none(),
            st.lists(
                st.fixed_dictionaries({"name": readable_step_names()}),
                max_size=6,
            ),
        )
    )
    preflight_results = draw(
        st.lists(
            st.one_of(
                legacy_validation_results(),
                structured_validation_results(include_extensions=True),
            ),
            max_size=6,
        )
    )
    argocd_run_id = draw(st.one_of(st.none(), st.just(""), report_text(min_size=1, max_size=24)))
    paused_apps = draw(
        st.one_of(
            st.none(),
            st.lists(
                st.fixed_dictionaries(
                    {
                        "name": report_text(min_size=1, max_size=24),
                        "namespace": report_text(min_size=1, max_size=24),
                    }
                ),
                max_size=5,
            ),
        )
    )
    kubeconfig_canary = "PBT_KUBECONFIG_CANARY_" + draw(safe_path_components())
    token_canary = "PBT_TOKEN_CANARY_" + draw(safe_path_components())
    secret_canary = "PBT_SECRET_CANARY_" + draw(safe_path_components())
    snapshot = {
        "current_phase": draw(st.sampled_from(tuple(phase.value for phase in Phase))),
        "errors": errors,
        "completed_steps": completed_steps,
        "config": {
            "preflight_results": preflight_results,
            "argocd_run_id": argocd_run_id,
            "argocd_paused_apps": paused_apps,
            "kubeconfig": kubeconfig_canary,
            "token": token_canary,
            "secret_path": secret_canary,
        },
    }
    return ReportStateCase(
        snapshot=snapshot,
        secret_canaries=(kubeconfig_canary, token_canary, secret_canary),
    )


@st.composite
def report_args_cases(draw: st.DrawFn) -> ReportArgsCase:
    """Generate Python CLI args-like fields and their bool-normalized report form."""
    primary_context = draw(st.one_of(st.none(), st.just(""), _valid_context_name(max_size=32)))
    secondary_context = draw(st.one_of(st.none(), st.just(""), _valid_context_name(max_size=32)))
    method = draw(st.one_of(st.none(), st.sampled_from(VALIDATION_METHOD_CHOICES)))
    old_hub_action = draw(st.one_of(st.none(), st.sampled_from(VALIDATION_OLD_HUB_ACTION_CHOICES)))
    restore_only = draw(json_native_values())
    decommission = draw(json_native_values())
    values = {
        "primary_context": primary_context,
        "secondary_context": secondary_context,
        "method": method,
        "old_hub_action": old_hub_action,
        "restore_only": restore_only,
        "decommission": decommission,
    }
    return ReportArgsCase(
        values=values,
        expected_operation={
            "method": method,
            "old_hub_action": old_hub_action,
            "restore_only": bool(restore_only),
            "decommission": bool(decommission),
        },
    )


def phase_summary_dictionaries() -> SearchStrategy[dict[str, dict]]:
    """Generate bounded phase-to-summary mappings for Python reports."""
    phase_names = st.sampled_from(tuple(phase.value for phase in Phase))
    summary = st.fixed_dictionaries(
        {
            "status": st.sampled_from(("pass", "fail", "skipped")),
            "count": st.integers(min_value=0, max_value=20),
        }
    )
    return st.dictionaries(phase_names, summary, min_size=0, max_size=4)


@st.composite
def collection_preflight_cases(draw: st.DrawFn) -> CollectionPreflightCase:
    """Generate collection report inputs, including sensitive hub-only canaries."""
    phase = draw(st.sampled_from(("preflight", "restore-only-preflight", "decommission-preflight")))
    results = draw(preflight_result_lists())
    primary_context = draw(report_text(min_size=1, max_size=24))
    secondary_context = draw(report_text(min_size=1, max_size=24))
    primary_hub_uid = "uid-primary-hub-" + draw(safe_path_components())
    secondary_hub_uid = "uid-secondary-hub-" + draw(safe_path_components())
    primary_identity_uid = "uid-primary-identity-" + draw(safe_path_components())
    secondary_identity_uid = "uid-secondary-identity-" + draw(safe_path_components())
    kubeconfig_canary = "PBT_KUBECONFIG_CANARY_" + draw(safe_path_components())
    token_canary = "PBT_TOKEN_CANARY_" + draw(safe_path_components())
    secret_canary = "PBT_SECRET_CANARY_" + draw(safe_path_components())
    additional_canary = "PBT_ADDITIONAL_SECRET_CANARY_" + draw(safe_path_components())
    secondary_kubeconfig_canary = kubeconfig_canary + "-secondary"
    secondary_token_canary = token_canary + "-secondary"
    additional_identity_canary = additional_canary + "-identity"
    additional_role = draw(st.sampled_from(("standby", "recovery", "observer")))
    additional_context = draw(report_text(min_size=1, max_size=24))
    additional_hub_uid = "uid-additional-hub-" + draw(safe_path_components())
    additional_identity_uid = "uid-additional-identity-" + draw(safe_path_components())
    primary_context_in_hub = draw(st.booleans())
    secondary_context_in_hub = draw(st.booleans())
    primary_uid_in_hub = draw(st.booleans())
    secondary_uid_in_hub = draw(st.booleans())
    hubs = {
        "primary": {
            **({"context": primary_context} if primary_context_in_hub else {}),
            **({"cluster_uid": primary_hub_uid} if primary_uid_in_hub else {}),
            "kubeconfig": kubeconfig_canary,
            "token": token_canary,
            "secret_path": secret_canary,
        },
        "secondary": {
            **({"context": secondary_context} if secondary_context_in_hub else {}),
            **({"cluster_uid": secondary_hub_uid} if secondary_uid_in_hub else {}),
            "kubeconfig": secondary_kubeconfig_canary,
            "token": secondary_token_canary,
            "secret": secret_canary,
        },
        additional_role: {
            **({"context": additional_context} if draw(st.booleans()) else {}),
            **({"cluster_uid": additional_hub_uid} if draw(st.booleans()) else {}),
            "kubeconfig": additional_canary,
        },
    }
    hub_identities = {
        "primary": {"context": primary_context + "-identity", "cluster_uid": primary_identity_uid},
        "secondary": {"context": secondary_context + "-identity", "cluster_uid": secondary_identity_uid},
        additional_role: {
            **({"context": additional_context + "-identity"} if draw(st.booleans()) else {}),
            **({"cluster_uid": additional_identity_uid} if draw(st.booleans()) else {}),
            "secret_path": additional_identity_canary,
        },
    }
    expected_hubs = {
        "primary": {
            "context": primary_context if primary_context_in_hub else primary_context + "-identity",
            "cluster_uid": primary_hub_uid if primary_uid_in_hub else primary_identity_uid,
        },
        "secondary": {
            "context": secondary_context if secondary_context_in_hub else secondary_context + "-identity",
            "cluster_uid": secondary_hub_uid if secondary_uid_in_hub else secondary_identity_uid,
        },
    }
    additional_hub = hubs[additional_role]
    additional_identity = hub_identities[additional_role]
    expected_hubs[additional_role] = {
        "context": additional_hub.get("context") or additional_identity.get("context") or "",
        "cluster_uid": additional_hub.get("cluster_uid") or additional_identity.get("cluster_uid") or "",
    }
    return CollectionPreflightCase(
        phase=phase,
        results=results,
        hubs=hubs,
        hub_identities=hub_identities,
        expected_hubs=expected_hubs,
        secret_canaries=(
            kubeconfig_canary,
            token_canary,
            secret_canary,
            additional_canary,
            secondary_kubeconfig_canary,
            secondary_token_canary,
            additional_identity_canary,
        ),
    )


@st.composite
def valid_file_modes(draw: st.DrawFn) -> ValidFileModeCase:
    """Generate owner-manageable modes in every supported representation."""
    expected = draw(st.integers(min_value=0o600, max_value=0o777))
    representation = draw(st.sampled_from(("integer", "plain", "zero_padded", "prefixed")))
    if representation == "integer":
        value: str | int = expected
    elif representation == "plain":
        value = format(expected, "o")
    elif representation == "zero_padded":
        value = format(expected, "04o")
    else:
        value = "0o" + format(expected, "o")
    return ValidFileModeCase(value=value, expected=expected)


@st.composite
def check_mode_cases(draw: st.DrawFn) -> CheckModeCase:
    """Generate each documented check-mode state with deliberate differences."""
    scenario = draw(st.sampled_from(("absent", "identical", "content-only", "mode-only", "combined")))
    existing_report = draw(json_native_values())
    changed_report = {"pbt_changed": existing_report}
    existing_mode = draw(st.integers(min_value=0o600, max_value=0o777))
    changed_mode = existing_mode ^ 0o040
    desired_report = changed_report if scenario in {"content-only", "combined"} else existing_report
    desired_mode = changed_mode if scenario in {"mode-only", "combined"} else existing_mode
    return CheckModeCase(
        scenario=scenario,
        existing_report=existing_report,
        desired_report=desired_report,
        existing_mode=existing_mode,
        desired_mode=desired_mode,
        initially_exists=scenario != "absent",
        expected_changed=scenario != "identical",
    )


def invalid_file_modes() -> SearchStrategy[str | int]:
    """Generate non-manageable, malformed, negative, and out-of-range modes."""
    malformed = st.sampled_from(("", " ", "not-octal", "08", "0o", "0x1ff", "+-1"))
    negative = st.one_of(st.integers(max_value=-1, min_value=-4096), st.integers(-4096, -1).map(str))
    above_range = st.one_of(
        st.integers(min_value=0o1000, max_value=0o7777),
        st.integers(min_value=0o1000, max_value=0o7777).map(lambda value: format(value, "o")),
    )
    non_manageable_integers = st.tuples(
        st.sampled_from((0o000, 0o200, 0o400)),
        st.integers(min_value=0, max_value=0o177),
    ).map(lambda parts: parts[0] | parts[1])
    non_manageable = st.one_of(
        non_manageable_integers,
        non_manageable_integers.map(lambda value: format(value, "o")),
        non_manageable_integers.map(lambda value: format(value, "04o")),
        non_manageable_integers.map(lambda value: "0o" + format(value, "o")),
        st.sampled_from((0, "0000", 0o200, "0200", 0o400, "0400", 0o444, "0444", 0o066, "0066")),
    )
    return st.one_of(non_manageable, malformed, negative, above_range)


ARGOCD_ACM_NAMESPACES = (
    "open-cluster-management",
    "open-cluster-management-agent-addon",
    "open-cluster-management-backups",
    "open-cluster-management-observability",
    "open-cluster-management-global-set",
    "multicluster-engine",
    "local-cluster",
)
ARGOCD_ACM_KINDS = (
    "MultiClusterHub",
    "ManagedCluster",
    "Policy",
    "BackupSchedule",
    "Restore",
)
ARGOCD_UNRELATED_NAMESPACES = ("default", "tenant-a", "team-platform")
ARGOCD_UNRELATED_KINDS = ("ConfigMap", "Deployment", "Secret", "Service")


def argocd_run_ids() -> SearchStrategy[str]:
    """Generate compact run identifiers suitable for pause-marker tests."""
    return _bounded_token(
        first_alphabet=LOWER_ALNUM,
        interior_alphabet=DNS_LABEL_INTERIOR,
        last_alphabet=LOWER_ALNUM,
        max_size=24,
    )


def argocd_sync_policies() -> SearchStrategy[dict[str, Any]]:
    """Generate bounded valid-shaped syncPolicy dictionaries."""
    automated = st.one_of(
        st.none(),
        st.fixed_dictionaries(
            {},
            optional={
                "prune": st.booleans(),
                "selfHeal": st.booleans(),
                "allowEmpty": st.booleans(),
            },
        ),
    )
    return st.fixed_dictionaries(
        {},
        optional={
            "automated": automated,
            "syncOptions": st.lists(
                st.sampled_from(("CreateNamespace=true", "PruneLast=true", "ApplyOutOfSyncOnly=true")),
                max_size=3,
                unique=True,
            ),
            "retry": st.fixed_dictionaries({"limit": st.integers(min_value=0, max_value=5)}),
        },
    )


@st.composite
def _argocd_resource(draw: st.DrawFn, impact_mode: str) -> dict[str, str]:
    """Build one valid-shaped Argo CD status.resources entry."""
    if impact_mode == "acm_namespace":
        namespace = draw(st.sampled_from(ARGOCD_ACM_NAMESPACES))
        kind = draw(st.sampled_from(ARGOCD_UNRELATED_KINDS))
    elif impact_mode == "acm_kind":
        namespace = draw(st.sampled_from(ARGOCD_UNRELATED_NAMESPACES))
        kind = draw(st.sampled_from(ARGOCD_ACM_KINDS))
    else:
        namespace = draw(st.sampled_from(ARGOCD_UNRELATED_NAMESPACES))
        kind = draw(st.sampled_from(ARGOCD_UNRELATED_KINDS))
    return {
        "group": draw(st.sampled_from(("", "apps", "cluster.open-cluster-management.io"))),
        "version": draw(st.sampled_from(("v1", "v1beta1", "v1alpha1"))),
        "kind": kind,
        "namespace": namespace,
        "name": draw(_dns_label(max_size=24)),
    }


@st.composite
def argocd_application_cases(
    draw: st.DrawFn,
    *,
    namespace: str | None = None,
    name: str | None = None,
    autosync_mode: str | None = None,
    resource_state: str | None = None,
    applicationset_owned: bool | None = None,
    impact_mode: str | None = None,
) -> ArgocdApplicationCase:
    """Generate a small semantic Application with an independent safety oracle."""
    namespace = namespace or draw(_dns_label(max_size=24))
    name = name or draw(_dns_label(max_size=24))
    autosync_mode = autosync_mode or draw(st.sampled_from(("missing", "null", "enabled")))
    resource_state = resource_state or draw(st.sampled_from(("missing", "empty", "stale", "current")))
    if applicationset_owned is None:
        applicationset_owned = draw(st.booleans())

    sync_policy = draw(argocd_sync_policies())
    if autosync_mode == "missing":
        sync_policy.pop("automated", None)
    elif autosync_mode == "null":
        sync_policy["automated"] = None
    else:
        sync_policy["automated"] = draw(
            st.fixed_dictionaries(
                {},
                optional={"prune": st.booleans(), "selfHeal": st.booleans()},
            )
        )

    generation = draw(st.integers(min_value=1, max_value=20))
    metadata: dict[str, Any] = {
        "namespace": namespace,
        "name": name,
        "annotations": draw(_string_map(max_size=2)),
        "generation": generation,
        "resourceVersion": str(draw(st.integers(min_value=1, max_value=10_000))),
    }
    owner_references = []
    if draw(st.booleans()):
        owner_references.append({"apiVersion": "v1", "kind": "ConfigMap", "name": "unrelated-owner"})
    if applicationset_owned:
        owner_references.append(
            {
                "apiVersion": "argoproj.io/v1alpha1",
                "kind": "ApplicationSet",
                "name": draw(_dns_label(max_size=24)),
            }
        )
    if owner_references:
        metadata["ownerReferences"] = owner_references

    app: dict[str, Any] = {"metadata": metadata, "spec": {"syncPolicy": sync_policy}}
    acm_resource_count = 0
    if resource_state == "missing":
        if draw(st.booleans()):
            app["status"] = {}
    elif resource_state == "empty":
        app["status"] = {"observedGeneration": generation, "resources": []}
    else:
        if impact_mode in {"acm_namespace", "acm_kind"}:
            impact_modes = [impact_mode] + draw(
                st.lists(st.sampled_from(("acm_namespace", "acm_kind", "unrelated")), max_size=4)
            )
        elif impact_mode == "unrelated":
            impact_modes = ["unrelated"] * draw(st.integers(min_value=1, max_value=5))
        else:
            impact_modes = draw(
                st.lists(
                    st.sampled_from(("acm_namespace", "acm_kind", "unrelated")),
                    min_size=1,
                    max_size=5,
                )
            )
        resources = [draw(_argocd_resource(mode)) for mode in impact_modes]
        acm_resource_count = sum(mode != "unrelated" for mode in impact_modes)
        observed_generation = generation - 1 if resource_state == "stale" else generation
        app["status"] = {
            "observedGeneration": observed_generation,
            "resources": resources,
        }

    return ArgocdApplicationCase(
        app=app,
        autosync_enabled=autosync_mode == "enabled",
        resource_state=resource_state,
        acm_resource_count=acm_resource_count,
        applicationset_owned=applicationset_owned,
    )


@st.composite
def argocd_application_lists(draw: st.DrawFn) -> list[ArgocdApplicationCase]:
    """Generate Applications with unique namespace/name identities."""
    identities = draw(
        st.lists(
            st.tuples(_dns_label(max_size=16), _dns_label(max_size=16)),
            min_size=0,
            max_size=8,
            unique=True,
        )
    )
    return [draw(argocd_application_cases(namespace=namespace, name=name)) for namespace, name in identities]


@st.composite
def argocd_resume_cases(
    draw: st.DrawFn,
    *,
    marker_mode: str | None = None,
) -> ArgocdResumeCase:
    """Generate resume inputs spanning matching, foreign, and missing markers."""
    namespace = draw(_dns_label(max_size=24))
    name = draw(_dns_label(max_size=24))
    run_id = draw(argocd_run_ids())
    marker_mode = marker_mode or draw(st.sampled_from(("matches", "mismatches", "missing")))
    autosync_enabled = draw(st.booleans())

    annotations = draw(_string_map(max_size=2))
    if marker_mode == "matches":
        annotations["acm-switchover.argoproj.io/paused-by"] = run_id
    elif marker_mode == "mismatches":
        foreign_run_id = draw(argocd_run_ids().filter(lambda candidate: candidate != run_id))
        annotations["acm-switchover.argoproj.io/paused-by"] = foreign_run_id

    current_sync_policy = draw(argocd_sync_policies())
    if autosync_enabled:
        current_sync_policy["automated"] = draw(
            st.fixed_dictionaries({}, optional={"prune": st.booleans(), "selfHeal": st.booleans()})
        )
    else:
        current_sync_policy.pop("automated", None)

    current_app = {
        "metadata": {
            "namespace": namespace,
            "name": name,
            "annotations": annotations,
            "resourceVersion": str(draw(st.integers(min_value=1, max_value=10_000))),
        },
        "spec": {"syncPolicy": current_sync_policy},
    }
    original_sync_policy = draw(argocd_sync_policies())
    original_sync_policy["automated"] = draw(
        st.fixed_dictionaries({}, optional={"prune": st.booleans(), "selfHeal": st.booleans()})
    )
    return ArgocdResumeCase(
        namespace=namespace,
        name=name,
        run_id=run_id,
        current_app=current_app,
        original_sync_policy=original_sync_policy,
        marker_mode=marker_mode,
        autosync_enabled=autosync_enabled,
    )


@st.composite
def gitops_marker_metadata(draw: st.DrawFn) -> dict[str, dict[str, str]]:
    """Generate metadata containing the generic unreliable instance marker."""
    source = draw(st.sampled_from(("labels", "annotations")))
    metadata = {"labels": draw(_string_map(max_size=2)), "annotations": draw(_string_map(max_size=2))}
    metadata[source]["app.kubernetes.io/instance"] = draw(_dns_label(max_size=24))
    if draw(st.booleans()):
        metadata["labels"]["app.kubernetes.io/managed-by"] = draw(
            st.sampled_from(("argocd", "flux", "helm", "custom-controller"))
        )
    if draw(st.booleans()):
        metadata["annotations"]["argocd.argoproj.io/tracking-id"] = draw(_dns_label(max_size=24))
    return metadata
