"""Real shipped completed entrypoints and module against local Kubernetes HTTP.

Checkpoint fixtures supply unrelated completed migration/phase prerequisites. API reads,
Ansible action/module dispatch and checkpoint loading are real; no live certification.
"""
from __future__ import annotations

import copy
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.tests.conftest import _ansible_env
from ansible_collections.tomazb.acm_switchover.tests.integration.argocd_fake_api import write_kubeconfig
from ansible_collections.tomazb.acm_switchover.tests.integration.migration_fake_api import FakeMigrationHub

ROOT=Path(__file__).resolve().parents[5]
COLLECTION=ROOT/'ansible_collections/tomazb/acm_switchover'
BACKUPS='/apis/velero.io/v1/namespaces/open-cluster-management-backup/backups'

@pytest.fixture
def run_backup_entry(tmp_path):
    hubs=[]
    def run(*,route='role',restore=False,good=False,reset=None,wrong_identity=False,boundary='valid',mode='execute',check=False,extra=None):
        now=datetime.now(timezone.utc)
        enabled=(now-timedelta(minutes=2)).isoformat()
        created=(now-timedelta(minutes=1 if good else 10)).isoformat()
        backup={'apiVersion':'velero.io/v1','kind':'Backup','metadata':{'name':'acm-managed-clusters-current','uid':'backup-uid','resourceVersion':'12','creationTimestamp':created},'status':{'phase':'Completed','errors':0}}
        if not good:backup['metadata'].pop('creationTimestamp')
        primary=FakeMigrationHub(cluster_uid='uid-primary')
        secondary=FakeMigrationHub(cluster_uid='uid-secondary',backups=[backup])
        hubs.extend([primary,secondary])
        contexts={'primary':'primary-hub','secondary':'secondary-hub'}
        config={}
        for role,hub in [('primary',primary),('secondary',secondary)]:
            path=tmp_path/(role+'.kubeconfig');write_kubeconfig(path,context=contexts[role],server=hub.url,token='fixture-token');config[role]={'context':contexts[role],'kubeconfig':str(path)}
        if restore:config.pop('primary')
        operation={'restore_only':restore,'method':'full' if restore else 'passive','activation_method':'patch','old_hub_action':'none' if restore else 'secondary'}
        identity={'primary_context':'' if restore else 'primary-hub','secondary_context':'secondary-hub','primary_cluster_uid':'' if restore else 'uid-primary','secondary_cluster_uid':'wrong-uid' if wrong_identity else 'uid-secondary',**operation,'collection_version':''}
        # Exact existing format, without any new proof/cache key.
        vectors=json.loads((ROOT/'tests/fixtures/r4_04_migration_evidence_vectors.json').read_text())
        journal=next(c['input']['candidate'] for c in vectors['cases'] if c['id']=='journal-cleanup-completed')
        data={'backup_schedule_enabled_at':enabled,'expected_managed_cluster_names':['cluster-a'],'expected_managed_cluster_count':1,'primary_has_observability':False,'secondary_has_observability':False,'migration_backups':journal}
        if boundary=='missing':data.pop('backup_schedule_enabled_at')
        elif boundary!='valid':data['backup_schedule_enabled_at']=boundary
        state={'schema_version':'2.0','phase':'finalization','phase_status':'pass','completed_phases':['preflight','primary_prep','activation','post_activation','finalization'],'operational_data':data,'operation_identity':identity,'errors':[],'report_refs':[]}
        checkpoint=tmp_path/'checkpoint.json';checkpoint.write_text(json.dumps(state));before=checkpoint.read_bytes();stat=checkpoint.stat()
        cp={'enabled':True,'backend':'file','path':str(checkpoint)}
        if reset is not None:cp.update(reset)
        variables={'acm_switchover_hubs':config,'acm_switchover_operation':operation,'acm_switchover_execution':{'mode':mode,'checkpoint':cp,'report_dir':str(tmp_path/'artifacts')},'acm_switchover_features':{'argocd':{'manage':False,'resume_on_failure':False}},**(extra or {})}
        vf=tmp_path/'vars.yml';vf.write_text(yaml.safe_dump(variables))
        if route=='role':
            playbook=tmp_path/'role.yml';playbook.write_text(yaml.safe_dump([{'hosts':'localhost','connection':'local','gather_facts':False,'tasks':[{'ansible.builtin.include_role':{'name':'tomazb.acm_switchover.finalization'}}]}]))
        else:playbook=COLLECTION/'playbooks'/('restore_only.yml' if restore else 'switchover.yml')
        command=['ansible-playbook',str(playbook),'-i','localhost,','-e','@'+str(vf)]
        if check:command.append('--check')
        completed=subprocess.run(command,cwd=ROOT,text=True,capture_output=True,env=_ansible_env(ROOT,tmp_path),timeout=90)
        return completed,primary,secondary,checkpoint,before,stat
    yield run
    for hub in hubs:hub.close()

@pytest.mark.parametrize('route',['role','playbook'])
@pytest.mark.parametrize('restore',[False,True])
def test_shipped_completed_skip_requires_current_backup_proof(run_backup_entry,route,restore):
    result,p,s,path,before,stat=run_backup_entry(route=route,restore=restore)
    output=result.stdout+result.stderr
    assert result.returncode!=0, 'Historical completed skip falsely succeeded: '+output
    assert 'Backup continuity refused' in output, output
    assert any(r['path']==BACKUPS for r in s.requests),s.requests
    assert path.read_bytes()==before and path.stat().st_mtime_ns==stat.st_mtime_ns
    assert not p.writes and not s.writes
