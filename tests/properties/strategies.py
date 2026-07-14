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
    primary_uid = "uid-primary-" + draw(safe_path_components())
    secondary_uid = "uid-secondary-" + draw(safe_path_components())
    primary_canary = "pbt-primary-kubeconfig-canary-" + draw(safe_path_components())
    secondary_canary = "pbt-secondary-kubeconfig-canary-" + draw(safe_path_components())
    primary_uid_in_hub = draw(st.booleans())
    secondary_uid_in_hub = draw(st.booleans())

    hubs = {
        "primary": {
            "context": primary_context,
            "kubeconfig": primary_canary,
            **({"cluster_uid": primary_uid} if primary_uid_in_hub else {}),
        },
        "secondary": {
            "context": secondary_context,
            "kubeconfig": secondary_canary,
            **({"cluster_uid": secondary_uid} if secondary_uid_in_hub else {}),
        },
    }
    hub_identities = {
        "primary": {"cluster_uid": primary_uid},
        "secondary": {"cluster_uid": secondary_uid},
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
        "primary_cluster_uid": primary_uid,
        "secondary_cluster_uid": secondary_uid,
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


@st.composite
def mismatched_operation_identities(draw: st.DrawFn, field: str) -> IdentityMismatchCase:
    """Generate normalized identities differing in exactly ``field``."""
    expected = draw(normalized_operation_identities())
    actual = dict(expected)
    if field == "restore_only":
        actual[field] = not bool(expected[field])
    elif field == "retained_extension":
        actual[field] = {"mismatch": draw(safe_path_components())}
        if actual[field] == expected[field]:
            actual[field] = {"mismatch": "forced-different"}
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
