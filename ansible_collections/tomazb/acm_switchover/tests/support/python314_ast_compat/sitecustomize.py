"""Minimal Python 3.14 AST compatibility shim for ansible-core 2.15 scenario subprocesses."""

from __future__ import annotations

import ast

for legacy_name in ("Str", "Num", "Bytes", "NameConstant", "Ellipsis"):
    if not hasattr(ast, legacy_name):
        setattr(ast, legacy_name, ast.Constant)
