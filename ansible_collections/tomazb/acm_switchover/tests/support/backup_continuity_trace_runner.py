"""Test-only instrumentation around the real ansible-playbook entrypoint.

No shipped task/action/module is replaced. Record the loaded source and every
checkpoint transition/save attempt; optionally interrupt after real proof returns.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import runpy
import shutil
import sys
from pathlib import Path

from ansible.plugins.loader import PluginLoader, module_loader

TRACE = Path(os.environ["ACM_BACKUP_TRACE_PATH"])
original_get = PluginLoader.get


def emit(event, **details):
    with TRACE.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": event, **details}) + "\n")


def source_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def instrument_get(plugin_loader, name, *args, **kwargs):
    action = original_get(plugin_loader, name, *args, **kwargs)
    if action is None or isinstance(action, type):
        return action
    task = getattr(action, "_task", None)
    task_action = getattr(task, "action", "")
    if task_action == "tomazb.acm_switchover.checkpoint_phase":
        params = task.args
        source = inspect.getfile(type(action))
        emit(
            "checkpoint_action",
            phase=params.get("phase"),
            status=params.get("status"),
            read_facts=params.get("read_facts", False),
            check_mode=action._play_context.check_mode,
            identity_barrier=params.get("identity_barrier", False),
            source_sha256=source_hash(source),
        )

        def forbid_save(*a, **k):
            emit("checkpoint_save_attempt")
            raise AssertionError("Completed scenario attempted checkpoint save")

        action._save_checkpoint = forbid_save
    elif task_action == "tomazb.acm_switchover.acm_backup_continuity":
        original_run = action.run
        module_path = module_loader.find_plugin_with_context(task_action).plugin_resolved_path

        def observe_run(*a, **k):
            result = original_run(*a, **k)
            emit(
                "backup_module_returned",
                proof=result.get("proof"),
                failed=result.get("failed", False),
                source_sha256=source_hash(module_path),
            )
            if result.get("proof") and os.environ.get("ACM_BACKUP_INTERRUPT_AFTER_PROOF") == "1":
                emit("interrupt_after_actual_proof")
                raise KeyboardInterrupt()
            return result

        action.run = observe_run
    return action


setattr(PluginLoader, "get", instrument_get)
program = shutil.which("ansible-playbook")
if program is None:
    raise RuntimeError("ansible-playbook missing from test lane")
sys.argv = [program, *sys.argv[1:]]
runpy.run_path(program, run_name="__main__")
