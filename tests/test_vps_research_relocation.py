import json
import sqlite3
import pytest
from trader.research import brain_intake as intake, predictive_bridge as bridge


def journal(path):
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE brain_events(id INTEGER,ts TEXT,kind TEXT,subject TEXT,detail TEXT)')


@pytest.mark.parametrize('compatibility_link', [True, False])
def test_relocated_source_keeps_binding_only_with_proven_same_file(tmp_path, compatibility_link):
    old = tmp_path/'old.db'; new = tmp_path/'new.db'
    journal(old)
    bank = bridge.store(tmp_path/'bank.db')
    bridge.append(bank, 'bridge_runtime_bindings', 'brain-source', dict(path=str(old)))
    bank.commit()
    before = bank.execute("SELECT payload FROM bridge_runtime_bindings WHERE id='brain-source'").fetchone()[0]
    old.rename(new)
    if compatibility_link:
        old.symlink_to(new)
        assert intake.intake(new, bank, bridge.append, now_ms=1800000000000, limit=0) == dict(accepted=0,refused=0,deferred=0)
    else:
        with pytest.raises(ValueError, match='checkpoint_conflict'):
            intake.intake(new, bank, bridge.append, now_ms=1800000000000, limit=0)
    assert bank.execute("SELECT payload FROM bridge_runtime_bindings WHERE id='brain-source'").fetchone()[0] == before
    bank.close()


def test_different_database_cannot_reuse_original_source_binding(tmp_path):
    old=tmp_path/'old.db';new=tmp_path/'new.db';journal(old);journal(new)
    bank=bridge.store(tmp_path/'bank.db')
    bridge.append(bank,'bridge_runtime_bindings','brain-source',dict(path=str(old)));bank.commit()
    with pytest.raises(ValueError,match='checkpoint_conflict'):
        intake.intake(new,bank,bridge.append,now_ms=1800000000000,limit=0)
    bank.close()
