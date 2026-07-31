"""Tests for the acm_safe_path_validate collection module."""

from __future__ import annotations

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_safe_path_validate import (
    main,
)


def _run_module(monkeypatch, *, path: str, path_type: str = "safe", check_mode: bool = False) -> dict:
    captured: dict = {}

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {"path": path, "path_type": path_type}
            self.check_mode = check_mode

        def exit_json(self, **kwargs):
            captured["exit"] = kwargs

        def fail_json(self, **kwargs):
            captured["fail"] = kwargs

    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_safe_path_validate.AnsibleModule",
        FakeModule,
    )

    main()
    return captured


def test_run_module_rejects_path_traversal_attempt(monkeypatch):
    result = _run_module(monkeypatch, path="/allowed/dir/../../etc/passwd")

    assert "Path traversal attempt" in result["fail"]["msg"]


def test_run_module_rejects_symlink_escape_outside_allowed_root(tmp_path, monkeypatch):
    escape_link = tmp_path / "escape.json"
    escape_link.symlink_to("/etc/passwd")

    result = _run_module(monkeypatch, path=str(escape_link), path_type="artifact")

    assert "outside allowed directories" in result["fail"]["msg"]


def test_run_module_rejects_missing_parent_directory(tmp_path, monkeypatch):
    missing_parent_path = tmp_path / "missing-parent" / "report.json"

    result = _run_module(monkeypatch, path=str(missing_parent_path))

    assert "Parent directory" in result["fail"]["msg"]
    assert "does not exist" in result["fail"]["msg"]


def test_run_module_safe_allows_in_root_symlink_but_artifact_rejects_it(tmp_path, monkeypatch):
    target = tmp_path / "report.json"
    target.write_text("{}\n", encoding="utf-8")
    symlink_path = tmp_path / "report-link.json"
    symlink_path.symlink_to(target)

    safe_result = _run_module(monkeypatch, path=str(symlink_path), path_type="safe")
    artifact_result = _run_module(monkeypatch, path=str(symlink_path), path_type="artifact")

    assert safe_result["exit"] == {"changed": False}
    assert "must not be a symlink" in artifact_result["fail"]["msg"]
