import json
import hashlib
import pytest
from trader.persistence.backup import critical_inventory,BackupSets,Inventory,Refused
from tests.test_stage1_recovery_monitoring import backup  # noqa: F401
from trader.agents import weights_online


def retained(root):
    path=root/'data/ewa_state.json'
    raw=b'{"agents":{"structure":{"w":3.0,"n":100},"flow":{"w":0.2,"n":100}},"processed":["exact-outcome"],"updated":1234.0}'
    path.write_bytes(raw)
    return path,raw


def test_default_inventory_restores_exact_consumed_ewa_input(backup,tmp_path,monkeypatch):
    root,j,old=backup
    path,raw=retained(root)
    monkeypatch.setattr(weights_online,'PATH',path)
    expected=weights_online.blended_weights({'structure':.5,'flow':.5})
    inventory=critical_inventory(root)
    assert any(a.path=='data/ewa_state.json' for a in inventory.assets)
    sets=BackupSets(root,old.destination,inventory,repository_version='offline')
    generation=sets.capture()
    asset=next(a for a in sets.verify(generation)['assets'] if a['path']=='data/ewa_state.json')
    assert asset['sha256']==hashlib.sha256(raw).hexdigest()
    assert asset['kind']=='file' and 'retained' in asset['role']
    target=tmp_path/'restored'
    receipt=sets.restore(generation,target)
    assert receipt['restore']=='PUBLISHED'
    from trader.core.journal import Journal
    assert Journal(target/'data/luffy.db').kv_get('control_state')=='RECOVERY'
    assert (target/'data/ewa_state.json').read_bytes()==raw
    monkeypatch.setattr(weights_online,'PATH',target/'data/ewa_state.json')
    assert weights_online.blended_weights({'structure':.5,'flow':.5})==expected


def test_absent_consumed_retained_input_refuses_default_backup(backup):
    root,_,_=backup
    path,_=retained(root);path.unlink()
    with pytest.raises(Refused,match='retained.*missing'):
        critical_inventory(root)


def test_manual_or_later_inventory_omission_cannot_bypass_dependency(backup):
    root,_,old=backup;retained(root)
    inventory=critical_inventory(root)
    reduced=Inventory(tuple(a for a in inventory.assets if a.path!='data/ewa_state.json'))
    with pytest.raises(Refused,match='critical_inventory_incomplete'):
        BackupSets(root,old.destination,reduced,repository_version='offline')
    sets=BackupSets(root,old.destination,inventory,repository_version='offline')
    sets.inventory=reduced
    with pytest.raises(Refused,match='retained.*incomplete'):
        sets.capture()
