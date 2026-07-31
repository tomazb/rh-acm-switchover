"""Path filters for the ACM switchover collection."""

from __future__ import annotations

from ansible.errors import AnsibleFilterError


def acm_abs_path(path, base_dir):
    """Return path unchanged when absolute; otherwise join it to base_dir.

    Reproduces the historical inline expression byte-for-byte:
    path if path.startswith('/') else f"{base_dir}/{path}" -- no
    normalization, no trailing-slash handling.
    """
    if not isinstance(path, str) or not path:
        raise AnsibleFilterError("acm_abs_path requires a non-empty string path")
    if not isinstance(base_dir, str):
        raise AnsibleFilterError("acm_abs_path requires a string base_dir")
    return path if path.startswith("/") else f"{base_dir}/{path}"


class FilterModule(object):
    def filters(self):
        return {"acm_abs_path": acm_abs_path}
