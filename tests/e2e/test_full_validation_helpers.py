"""Fast tests for full-validation helper failure handling."""

import pytest
from _pytest.outcomes import Failed

from tests.e2e.full_validation_helpers import RunResult, get_argocd_apps


def test_get_argocd_apps_fails_on_kubectl_error(mocker):
    """Auth/API failures must not be downgraded to an empty app list."""
    mocker.patch(
        "tests.e2e.full_validation_helpers.kubectl",
        return_value=RunResult(
            1,
            "",
            "error: You must be logged in to the server\n",
            ["kubectl", "--context", "hub-a", "get", "applications.argoproj.io"],
        ),
    )

    with pytest.raises(Failed, match="hub-a"):
        get_argocd_apps("hub-a")


def test_get_argocd_apps_fails_on_invalid_json(mocker):
    """Parse failures must stop the test instead of pretending no Argo CD apps exist."""
    mocker.patch(
        "tests.e2e.full_validation_helpers.kubectl",
        return_value=RunResult(
            0,
            "warning...{not-json}",
            "",
            ["kubectl", "--context", "hub-a", "get", "applications.argoproj.io"],
        ),
    )

    with pytest.raises(Failed, match="Invalid Argo CD Applications JSON"):
        get_argocd_apps("hub-a")
