"""Unit tests for the collection's path filters."""

import pytest
from ansible.errors import AnsibleFilterError

from ansible_collections.tomazb.acm_switchover.plugins.filter.paths import FilterModule, acm_abs_path


def test_absolute_path_passes_through():
    assert acm_abs_path("/tmp/summary.json", "/work") == "/tmp/summary.json"


def test_relative_path_joins_base_dir_without_normalization():
    assert acm_abs_path("artifacts/summary.json", "/work") == "/work/artifacts/summary.json"
    # exact concatenation semantics of the historical inline expression:
    # base_dir ~ '/' ~ path, so a trailing slash on base_dir doubles up
    assert acm_abs_path("./summary.json", "/work/") == "/work//./summary.json"


@pytest.mark.parametrize("bad", ["", None, 7])
def test_non_string_or_empty_path_raises(bad):
    with pytest.raises(AnsibleFilterError):
        acm_abs_path(bad, "/work")


def test_non_string_base_dir_raises():
    with pytest.raises(AnsibleFilterError):
        acm_abs_path("summary.json", None)


def test_filter_module_exposes_acm_abs_path():
    assert FilterModule().filters()["acm_abs_path"] is acm_abs_path
