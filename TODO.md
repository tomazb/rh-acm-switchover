# Code Smell Remediation Checklist

1. [x] **Reinstate secure TLS defaults in `lib/kube_client.py`**  
   - Remove the blanket `configuration.assert_hostname = False` setting, or guard it behind an explicit opt-in flag so hostname verification stays on by default.

2. [x] **Track primary vs. secondary Observability state separately**  
   - Update `ObservabilityDetector` to return both flags.  
   - Ensure post-activation verification only restarts/validates Observability when the secondary hub actually runs it.

3. [x] **Enhance Observability pod health checks (`modules/post_activation.py`)**  
   - Inspect container `waiting`/`terminated` states so CrashLoopBackOff pods are reported instead of being counted as healthy.

4. [x] **Make bash scripts honor the detected CLI (`scripts/preflight-check.sh`, `scripts/postflight-check.sh`)**  
   - After detecting whether `oc` or `kubectl` is available, run every subsequent command with that binary (or explicitly require `oc`).

5. [x] **Prevent stale state leakage across runs**  
   - Derive the default state file path from the primary/secondary context pair or store the contexts inside the state file and validate the match before reuse.  
   - Consider auto-resetting when contexts change.

6. [x] **Harden `StateManager.get_current_phase`**  
   - Catch `ValueError` when unknown strings are stored and fall back to `Phase.INIT` (or instruct the user to reset), instead of crashing.

7. [x] **Handle Kubernetes list pagination in `lib/kube_client.list_custom_resources`**  
   - Loop on `metadata.continue` so large fleets don’t silently drop items, ensuring pre-flight and verification steps see every resource.

8. [x] **Loosen `wait_for_pods_ready` pod count handling**  
   - Allow extra pods during rollouts (e.g., accept `len(pods) >= expected_count`) so transient replica mismatches don’t time out when the desired number of pods are already ready.

9. [x] **Port CLI/context prerequisite checks into Python preflight**  
   - Mirror the bash script’s validation that `oc`/`kubectl`/`jq` are available and both contexts resolve before deeper checks run.  
   - Ensure missing Observability prerequisites such as the Thanos object-storage secret on the secondary hub are surfaced in `PreflightValidator`.

10. [x] **Bring postflight Observability/Grafana checks into Python workflow**  
    - Extend `PostActivationVerification` to flag pods in `CrashLoopBackOff` or other error states, capture observatorium-api restart info, and surface Grafana route availability guidance.

11. [x] **Add backup schedule + MultiClusterHub status validation to finalization**  
    - After enabling the backup schedule, re-read the CR to ensure it is unpaused and recent backups exist.  
    - Confirm the new hub’s MultiClusterHub is `Running` and all ACM pods are healthy, similar to `postflight-check.sh`.

12. [x] **Introduce “old hub” regression checks post-switchover**  
    - Optionally verify the old hub shows clusters as disconnected, BackupSchedule paused, and Thanos compactor still scaled down, paralleling script section 7.

13. [x] **Reconfirm disable-auto-import cleanup on the new hub**  
    - After activation, ensure no ManagedClusters retain the `disable-auto-import` annotation unless explicitly expected.

14. [x] **Update/extend tests affected by these changes**  
    - Add or refresh unit/integration tests (Python modules and bash script harness) so new validations and flows are covered.
