"""Bounded semantic strategies shared by validation property tests.

The generators start with valid-shaped domain values, then apply small,
targeted mutations.  This keeps counterexamples readable and exercises the
interesting boundaries instead of spending examples on arbitrary blobs.
"""

from __future__ import annotations

import string

from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

from lib.constants import (
    VALIDATION_ACTIVATION_METHOD_CHOICES,
    VALIDATION_METHOD_CHOICES,
    VALIDATION_OLD_HUB_ACTION_CHOICES,
)

ASCII_ALNUM = string.ascii_letters + string.digits
LOWER_ALNUM = string.ascii_lowercase + string.digits
DNS_LABEL_INTERIOR = LOWER_ALNUM + "-"
LABEL_INTERIOR = ASCII_ALNUM + "-_."
CONTEXT_INTERIOR = ASCII_ALNUM + "_.:-/@"
SAFE_IDENTIFIER_CHARS = ASCII_ALNUM + "._-"


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
