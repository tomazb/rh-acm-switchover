# ACM Switchover

Automation for switching the active Red Hat ACM hub from a primary cluster to a secondary, including backup verification, cluster activation, and GitOps coordination.

## Language

**Pause register**:
The durable record of Argo CD Applications paused for a switchover run, together with the pause/resume/status operations that maintain it. Invariant: entries are exactly the Applications currently paused by this tool.
_Avoid_: paused apps list, pause state, argocd state

**Run marker**:
The annotation stamped on a paused Application identifying which switchover run paused it. Resume only touches Applications whose run marker matches the register's run id.
_Avoid_: paused-by annotation (when meaning the concept), run_id (outside code)
