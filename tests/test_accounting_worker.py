"""Synthetic off-path lifecycle tests; no observed performance claims."""
import json
import sqlite3
from pathlib import Path

import pytest

from trader.observability import accounting as W
from trader.engine import trade_accounting as A
from tests.test_whole_trade_accounting import source, Venue


@pytest.fixture
def setup(tmp_path, monkeypatch):
    journal = tmp_path / 'journal.db'
    s = source()
    with sqlite3.connect(journal) as db:
        cols = ','.join(k + ' ' + ('REAL' if isinstance(v, (int,float)) else 'TEXT') for k,v in s['trade'].items())
        db.execute('CREATE TABLE trades (' + cols + ')')
        db.execute('INSERT INTO trades VALUES (' + ','.join('?' for _ in s['trade']) + ')', list(s['trade'].values()))
        db.execute('CREATE TABLE trade_accounting_bookings(id INTEGER PRIMARY KEY,trade_id TEXT,payload TEXT)')
        db.executemany('INSERT INTO trade_accounting_bookings(trade_id,payload) VALUES (?,?)',
                       [('trade',json.dumps(r)) for r in s['receipts']])
    monkeypatch.setattr(A.time, 'time', lambda: 5)
    return journal, tmp_path / 'worker'


def run(setup, factory=Venue, now=5000):
    return W.step(*setup, factory, now_ms=now)


def attempts(setup):
    with sqlite3.connect(setup[1] / 'queue.db') as db:
        return db.execute('SELECT id,trade_id,path,status,reasons FROM attempts ORDER BY rowid').fetchall()


def test_complete_terminal_reused_without_network_or_journal_write(setup):
    before = setup[0].read_bytes()
    assert run(setup)['jobs'] == {'complete':1}
    artifact = Path(attempts(setup)[0][2])
    raw = artifact.read_bytes()
    assert A.replay(json.loads(raw))['net_trade_pnl_usdt'] == 25
    assert run(setup, lambda: pytest.fail('terminal fetched again'), now=999999)['attempted'] == 0
    assert setup[0].read_bytes() == before
    assert artifact.read_bytes() == raw


def test_missing_funding_retries_new_file_with_backoff(setup):
    venue = Venue()
    venue.events.pop()
    first = run(setup, lambda: venue)
    assert first['jobs'] == {'retry':1}
    old = Path(attempts(setup)[0][2])
    raw = old.read_bytes()
    assert run(setup, lambda: pytest.fail('not due'), now=5001)['attempted'] == 0
    assert run(setup, now=305000)['jobs'] == {'complete':1}
    assert len(attempts(setup)) == 2
    assert old.read_bytes() == raw


def test_open_trade_deferred_without_venue(setup):
    with sqlite3.connect(setup[0]) as db:
        db.execute("UPDATE trades SET status='open'")
    assert run(setup, lambda: pytest.fail('open fetched'))['jobs'] == {'waiting_close':1}
    assert attempts(setup) == []


def test_empty_queue_does_not_initialize_exchange(setup):
    with sqlite3.connect(setup[0]) as db:
        db.execute('DELETE FROM trade_accounting_bookings')
    assert run(setup, lambda: pytest.fail('empty fetched'))['jobs'] == {}


def test_crash_after_file_before_queue_commit_recovers_terminal(setup, monkeypatch):
    real = W.finish
    monkeypatch.setattr(W, 'finish', lambda *args: (_ for _ in ()).throw(W.Deadline()))
    with pytest.raises(W.Deadline): run(setup)
    assert attempts(setup)[0][3] == 'running'
    monkeypatch.setattr(W, 'finish', real)
    assert run(setup, lambda: pytest.fail('recovery fetched'))['jobs'] == {'complete':1}
    assert len(attempts(setup)) == 1


def test_crash_during_capture_stays_retryable(setup, monkeypatch):
    real = A.capture
    monkeypatch.setattr(A, 'capture', lambda *args: (_ for _ in ()).throw(W.Deadline()))
    with pytest.raises(W.Deadline):run(setup)
    monkeypatch.setattr(A, 'capture', real)
    assert run(setup, lambda: pytest.fail('recovery backoff'))['jobs'] == {'retry':1}
    assert run(setup, now=305000)['jobs'] == {'complete':1}


def test_initialization_error_is_redacted_and_retryable(setup):
    def broken(): raise RuntimeError('secret credential')
    assert run(setup, broken)['jobs'] == {'retry':1}
    assert 'secret' not in str(attempts(setup))
    assert run(setup, now=305000)['jobs'] == {'complete':1}


@pytest.mark.parametrize('cap', ['MAX_ATTEMPTS','MAX_BYTES','MAX_JOBS'])
def test_capacity_pauses_without_discarding_or_network(setup, monkeypatch, cap):
    monkeypatch.setattr(W, cap, 0)
    result = run(setup, lambda: pytest.fail('capacity fetched'))
    assert result['status'] == 'capacity_retry'
    assert attempts(setup) == []
    monkeypatch.undo()
    # No cursor loss when queue capacity was reached.
    assert run(setup)['attempted'] == 1


def test_invalid_batch_refused(setup):
    with pytest.raises(ValueError, match='batch'):
        W.step(*setup, Venue, max_trades=3)


def test_cli_lock_does_not_run_work(setup, monkeypatch, capsys):
    import fcntl
    from trader.core.config import Env
    setup[1].mkdir()
    monkeypatch.setattr('sys.argv', ['worker','--once','--enable','--directory',str(setup[1])])
    monkeypatch.setattr(Env,'get',lambda *args:'true')
    monkeypatch.setattr(W,'step',lambda *args:pytest.fail('locked worker ran'))
    with (setup[1]/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        W.main()
    assert json.loads(capsys.readouterr().out)['status']=='lock_busy'


def test_cli_deadline_health_and_no_import(setup, monkeypatch, capsys):
    from trader.core.config import Env
    monkeypatch.setattr('sys.argv', ['worker','--once','--enable','--directory',str(setup[1])])
    monkeypatch.setattr(Env,'get',lambda *args:'true')
    monkeypatch.setattr(W,'step',lambda *args:(_ for _ in ()).throw(W.Deadline()))
    W.main()
    assert json.loads((setup[1]/'health.json').read_text())['status']=='deadline_retry'
    assert json.loads(capsys.readouterr().out)['status']=='deadline_retry'


def test_cli_rejects_non_demo(setup, monkeypatch):
    from trader.core.config import Env
    monkeypatch.setattr('sys.argv',['worker','--once','--enable','--directory',str(setup[1])])
    monkeypatch.setattr(Env,'get',lambda *args:'false')
    with pytest.raises(SystemExit):W.main()
    assert not setup[1].exists()


def test_no_kernel_integration_or_order_write_api():
    import inspect
    text=inspect.getsource(W)
    assert 'import_memory' not in text
    assert 'create_order' not in text
    assert 'cancel_order' not in text
    assert 'observability.accounting' not in Path('trader/kernel.py').read_text()
