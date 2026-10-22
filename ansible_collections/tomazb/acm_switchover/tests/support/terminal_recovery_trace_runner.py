"""Test-only process loss after a real successful BackupSchedule write."""

import inspect
import json
import os
import runpy
import shutil
import sys
from pathlib import Path

from ansible.plugins.loader import PluginLoader

original_get = PluginLoader.get


def instrument_get(plugin_loader, name, *args, **kwargs):
    action = original_get(plugin_loader, name, *args, **kwargs)
    if action is None or isinstance(action, type):
        return action
    task = getattr(action, "_task", None)
    if getattr(task, "action", "") != "kubernetes.core.k8s":
        return action
    original_run = action.run

    def interrupt_after_actual_write(*a, **k):
        result = original_run(*a, **k)
        if (
            (result.get("result") or {}).get("kind") == "BackupSchedule"
            and result.get("changed")
            and not result.get("failed")
        ):
            state = json.loads(Path(os.environ["ACM_TERMINAL_CHECKPOINT"]).read_bytes())
            Path(os.environ["ACM_TERMINAL_TRACE"]).write_text(
                json.dumps(
                    {
                        "event": "process_loss_after_actual_enable",
                        "action_source": inspect.getfile(type(action)),
                        "durable_checkpoint": state,
                        "result": result,
                    },
                    indent=2,
                )
            )
            os._exit(73)
        return result

    action.run = interrupt_after_actual_write
    return action


PluginLoader.get = instrument_get
program = shutil.which("ansible-playbook")
if program is None:
    raise RuntimeError("ansible-playbook missing from test lane")
sys.argv = [program, *sys.argv[1:]]
runpy.run_path(program, run_name="__main__")
