"""
Finalization and rollback module for ACM switchover.
"""

import logging
import time
from typing import Optional

from lib.kube_client import KubeClient
from lib.utils import StateManager, is_acm_version_ge

logger = logging.getLogger("acm_switchover")


class Finalization:
    """Handles finalization steps on secondary hub."""
    
    def __init__(
        self,
        secondary_client: KubeClient,
        state_manager: StateManager,
        acm_version: str
    ):
        self.secondary = secondary_client
        self.state = state_manager
        self.acm_version = acm_version
        
    def finalize(self) -> bool:
        """
        Execute finalization steps.
        
        Returns:
            True if finalization completed successfully
        """
        logger.info("Starting finalization...")
        
        try:
            # Step 10: Enable BackupSchedule on new hub
            if not self.state.is_step_completed("enable_backup_schedule"):
                self._enable_backup_schedule()
                self.state.mark_step_completed("enable_backup_schedule")
            else:
                logger.info("Step already completed: enable_backup_schedule")
            
            # Verify new backups are being created
            if not self.state.is_step_completed("verify_new_backups"):
                self._verify_new_backups()
                self.state.mark_step_completed("verify_new_backups")
            else:
                logger.info("Step already completed: verify_new_backups")
            
            logger.info("Finalization completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Finalization failed: {e}")
            self.state.add_error(str(e), "finalization")
            return False
    
    def _enable_backup_schedule(self):
        """Enable BackupSchedule on new hub (version-aware)."""
        logger.info("Enabling BackupSchedule on new hub...")
        
        # Get BackupSchedule
        backup_schedules = self.secondary.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            namespace="open-cluster-management-backup"
        )
        
        if not backup_schedules:
            # Check if we have a saved one from ACM 2.11 delete
            saved_bs = self.state.get_config("saved_backup_schedule")
            
            if saved_bs:
                logger.info("Restoring saved BackupSchedule from state (ACM 2.11)")
                
                # Clean up metadata for recreation
                if 'metadata' in saved_bs:
                    saved_bs['metadata'].pop('uid', None)
                    saved_bs['metadata'].pop('resourceVersion', None)
                    saved_bs['metadata'].pop('creationTimestamp', None)
                    saved_bs['metadata'].pop('generation', None)
                    saved_bs['metadata'].pop('managedFields', None)
                
                # Remove status
                saved_bs.pop('status', None)
                
                # Ensure not paused
                if 'spec' in saved_bs:
                    saved_bs['spec']['paused'] = False
                
                self.secondary.create_custom_resource(
                    group="cluster.open-cluster-management.io",
                    version="v1beta1",
                    plural="backupschedules",
                    body=saved_bs,
                    namespace="open-cluster-management-backup"
                )
                
                logger.info("BackupSchedule restored successfully")
            else:
                logger.warning("No BackupSchedule found and none saved in state")
            
            return
        
        # BackupSchedule exists - unpause it
        bs = backup_schedules[0]
        bs_name = bs.get('metadata', {}).get('name', 'schedule-rhacm')
        
        # Check if already enabled
        if bs.get('spec', {}).get('paused') is False or 'paused' not in bs.get('spec', {}):
            logger.info(f"BackupSchedule {bs_name} is already enabled")
            return
        
        # ACM 2.12+ supports pausing via spec.paused
        if is_acm_version_ge(self.acm_version, "2.12.0"):
            logger.info(f"Unpausing BackupSchedule via spec.paused (ACM {self.acm_version})")
            
            patch = {"spec": {"paused": False}}
            self.secondary.patch_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="backupschedules",
                name=bs_name,
                patch=patch,
                namespace="open-cluster-management-backup"
            )
            
            logger.info(f"BackupSchedule {bs_name} enabled successfully")
        else:
            logger.info("BackupSchedule management for ACM 2.11 handled via restore")
    
    def _verify_new_backups(self, timeout: int = 600):
        """
        Verify new backups are being created.
        
        Args:
            timeout: Maximum wait time in seconds (default 10 minutes)
        """
        logger.info("Verifying new backups are being created...")
        
        # Get current backup list
        initial_backups = self.secondary.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backups",
            namespace="open-cluster-management-backup"
        )
        
        initial_backup_names = {b.get('metadata', {}).get('name') for b in initial_backups}
        
        logger.info(f"Found {len(initial_backups)} existing backup(s)")
        logger.info("Waiting for new backup to appear (this may take 5-10 minutes)...")
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            current_backups = self.secondary.list_custom_resources(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="backups",
                namespace="open-cluster-management-backup"
            )
            
            current_backup_names = {b.get('metadata', {}).get('name') for b in current_backups}
            
            # Check for new backups
            new_backups = current_backup_names - initial_backup_names
            
            if new_backups:
                logger.info(f"New backup(s) detected: {', '.join(new_backups)}")
                
                # Verify at least one is in progress or completed
                for backup_name in new_backups:
                    backup = next(
                        (b for b in current_backups if b.get('metadata', {}).get('name') == backup_name),
                        None
                    )
                    
                    if backup:
                        phase = backup.get('status', {}).get('phase', 'unknown')
                        logger.info(f"Backup {backup_name} phase: {phase}")
                        
                        if phase in ('InProgress', 'Finished'):
                            logger.info("New backup is being created successfully!")
                            return
                
            elapsed = int(time.time() - start_time)
            logger.debug(f"Waiting for new backup... (elapsed: {elapsed}s)")
            time.sleep(30)
        
        logger.warning(
            f"No new backups detected after {timeout}s. "
            "BackupSchedule may take time to create first backup."
        )


class Rollback:
    """Handles rollback to primary hub."""
    
    def __init__(
        self,
        primary_client: KubeClient,
        secondary_client: KubeClient,
        state_manager: StateManager,
        acm_version: str,
        has_observability: bool
    ):
        self.primary = primary_client
        self.secondary = secondary_client
        self.state = state_manager
        self.acm_version = acm_version
        self.has_observability = has_observability
        
    def rollback(self) -> bool:
        """
        Execute rollback to primary hub.
        
        Returns:
            True if rollback completed successfully
        """
        logger.info("Starting rollback to primary hub...")
        
        try:
            # Step 1: Delete/pause activation restore on secondary
            self._deactivate_secondary()
            
            # Step 2: Remove disable-auto-import annotations on primary
            self._enable_auto_import()
            
            # Step 3: Restart Thanos compactor on primary (if Observability)
            if self.has_observability:
                self._restart_thanos_compactor()
            
            # Step 4: Unpause BackupSchedule on primary
            self._unpause_backup_schedule()
            
            logger.info("Rollback completed. Waiting for clusters to reconnect to primary...")
            logger.info("Allow 5-10 minutes for ManagedClusters to reconnect.")
            
            return True
            
        except Exception as e:
            logger.error(f"Rollback failed: {e}")
            return False
    
    def _deactivate_secondary(self):
        """Delete or pause activation restore on secondary."""
        logger.info("Deactivating secondary hub...")
        
        # Try to delete restore-acm-full if it exists
        deleted = self.secondary.delete_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name="restore-acm-full",
            namespace="open-cluster-management-backup"
        )
        
        if deleted:
            logger.info("Deleted restore-acm-full")
        else:
            logger.debug("restore-acm-full not found")
        
        # For passive sync, we could delete and recreate without activation
        # but it's simpler to just leave it as-is
        logger.info("Secondary hub deactivated")
    
    def _enable_auto_import(self):
        """Remove disable-auto-import annotations from ManagedClusters."""
        logger.info("Re-enabling auto-import on ManagedClusters...")
        
        managed_clusters = self.primary.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1",
            plural="managedclusters"
        )
        
        count = 0
        for mc in managed_clusters:
            mc_name = mc.get('metadata', {}).get('name')
            
            if mc_name == "local-cluster":
                continue
            
            annotations = mc.get('metadata', {}).get('annotations', {})
            if 'import.open-cluster-management.io/disable-auto-import' in annotations:
                # Remove annotation by setting to null
                patch = {
                    "metadata": {
                        "annotations": {
                            "import.open-cluster-management.io/disable-auto-import": None
                        }
                    }
                }
                
                self.primary.patch_custom_resource(
                    group="cluster.open-cluster-management.io",
                    version="v1",
                    plural="managedclusters",
                    name=mc_name,
                    patch=patch
                )
                
                count += 1
        
        logger.info(f"Removed disable-auto-import annotation from {count} ManagedCluster(s)")
    
    def _restart_thanos_compactor(self):
        """Scale Thanos compactor back up."""
        logger.info("Restarting Thanos compactor...")
        
        try:
            self.primary.scale_statefulset(
                name="observability-thanos-compact",
                namespace="open-cluster-management-observability",
                replicas=1
            )
            
            logger.info("Thanos compactor scaled back to 1 replica")
        except Exception as e:
            logger.error(f"Failed to restart Thanos compactor: {e}")
    
    def _unpause_backup_schedule(self):
        """Unpause BackupSchedule on primary."""
        logger.info("Unpausing BackupSchedule on primary...")
        
        backup_schedules = self.primary.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            namespace="open-cluster-management-backup"
        )
        
        if not backup_schedules:
            # Restore from saved state if ACM 2.11
            saved_bs = self.state.get_config("saved_backup_schedule")
            
            if saved_bs:
                logger.info("Restoring BackupSchedule from state")
                
                # Clean metadata
                if 'metadata' in saved_bs:
                    saved_bs['metadata'].pop('uid', None)
                    saved_bs['metadata'].pop('resourceVersion', None)
                    saved_bs['metadata'].pop('creationTimestamp', None)
                    saved_bs['metadata'].pop('generation', None)
                    saved_bs['metadata'].pop('managedFields', None)
                
                saved_bs.pop('status', None)
                
                self.primary.create_custom_resource(
                    group="cluster.open-cluster-management.io",
                    version="v1beta1",
                    plural="backupschedules",
                    body=saved_bs,
                    namespace="open-cluster-management-backup"
                )
                
                logger.info("BackupSchedule restored")
            
            return
        
        # Unpause existing BackupSchedule
        bs = backup_schedules[0]
        bs_name = bs.get('metadata', {}).get('name')
        
        if is_acm_version_ge(self.acm_version, "2.12.0"):
            patch = {"spec": {"paused": False}}
            self.primary.patch_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="backupschedules",
                name=bs_name,
                patch=patch,
                namespace="open-cluster-management-backup"
            )
            
            logger.info(f"BackupSchedule {bs_name} unpaused")
