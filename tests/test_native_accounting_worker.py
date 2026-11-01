"""Leg completion cannot become an individual financial outcome."""
import json
import sqlite3

pytest_plugins = ('tests.test_native_group_cashflows',)
from trader.observability import accounting as W


def prepared(tmp_path, group):
    db = sqlite3.connect(tmp_path/'queue.db'); W.initialize(db)
    for i, part in enumerate(group['components']):
        tid = part['capture']['bookings']['trade']['id']
        path = tmp_path/(str(i)+'.json'); path.write_text(json.dumps(part['capture']))
        db.execute('INSERT INTO jobs(trade_id,status,due_ms,attempts) VALUES(?,?,?,?)', (tid,'running',0,1))
        db.execute('INSERT INTO attempts(id,trade_id,path,status,started_ms) VALUES(?,?,?,?,?)',
            (str(i), tid, str(path), 'running', 0))
        W.finish(db, (str(i),tid,str(path)), 9000)
    return db


def test_verified_native_leg_is_explicitly_incomplete_for_learning(native_group, tmp_path):
    with prepared(tmp_path, native_group) as db:
        assert db.execute('SELECT DISTINCT status FROM jobs').fetchall() == [('leg_complete',)]
        assert db.execute('SELECT DISTINCT status FROM attempts').fetchall() == [('leg_complete',)]


def test_pending_group_funding_refreshes_captures_without_an_outcome(native_group, tmp_path, monkeypatch):
    from trader.learning import producers
    db = prepared(tmp_path, native_group)
    monkeypatch.setattr(W, 'group_source', lambda *a:(True, native_group))
    def pending(*args):raise ValueError('native_group_funding_publication_pending')
    monkeypatch.setattr(producers, 'deliver_group_accounting', pending)
    before = {p.name:p.read_bytes() for p in tmp_path.glob('*.json')}
    with db:
        result = W.deliver_completed(db, tmp_path/'unused.db')
        assert result['delivered'] == result['unbound'] == 0 and result['retry'] == 2
        assert db.execute('SELECT DISTINCT status FROM jobs').fetchall() == [('retry',)]
        assert db.execute('SELECT DISTINCT status FROM attempts').fetchall() == [('retry',)]
        assert not db.execute('SELECT * FROM learning_deliveries').fetchall()
    assert before == {p.name:p.read_bytes() for p in tmp_path.glob('*.json')}
    db.close()
