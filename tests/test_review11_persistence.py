"""Review 11: synthetic offline inventory path regression."""
import json
import sqlite3

from trader.persistence.backup import BackupSets, critical_inventory
from tests.test_stage1_recovery_monitoring import backup, isolated  # noqa: F401


def test_relative_root_accepts_contained_absolute_configured_replay_store(backup, tmp_path, monkeypatch):  # noqa: F811
    root, _, old = backup
    custom = root / 'data/custom-candles.db'
    with sqlite3.connect(custom) as db:
        db.execute('CREATE TABLE fixture (value INTEGER)')
        db.execute('INSERT INTO fixture VALUES (11)')
    (root / 'config.yaml').write_text(json.dumps({'research': {'paths': {'candles': str(custom)}}}))
    monkeypatch.chdir(tmp_path)
    inventory = critical_inventory(root.name)
    assert 'data/custom-candles.db' in {asset.path for asset in inventory.assets}
    sets = BackupSets(root.name, old.destination, inventory, repository_version='offline-fixture')
    generation = sets.capture()
    assert sets.verify(generation)['completion'] == 'COMPLETE'
    target = tmp_path / 'restored-relative-root'
    receipt = sets.restore(generation, target)
    assert receipt['restore'] == 'PUBLISHED'
    with sqlite3.connect(target / 'data/custom-candles.db') as db:
        assert db.execute('SELECT value FROM fixture').fetchall() == [(11,)]


def test_relative_root_still_refuses_configured_store_outside_source(backup, tmp_path, monkeypatch):  # noqa: F811
    import pytest
    from trader.persistence.backup import Refused

    root, _, _ = backup
    # Deliberately absent outside path: containment must refuse before opening it.
    outside = tmp_path / 'outside-candles.db'
    (root / 'config.yaml').write_text(json.dumps({'research': {'paths': {'candles': str(outside)}}}))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(Refused, match='configured_store_outside_inventory_root'):
        critical_inventory(root.name)
    assert not outside.exists()
