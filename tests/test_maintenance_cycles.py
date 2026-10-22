from pathlib import Path
import sqlite3

import pytest
import yaml

from trader.core.journal import Journal
from trader.core.types import Position, Side
from trader.scheduling.maintenance import Maintenance


def test_close_event_observes_committed_trade_once(tmp_path):
    journal = Journal(tmp_path / 'book.db')
    journal.add_trade(Position(id='t', symbol='BTC/USDT', side=Side.LONG,
        amount=1, entry_price=100, notional_usdt=100))
    seen = []
    def closed():
        with sqlite3.connect(journal.db_path) as db:
            seen.append(db.execute("SELECT status FROM trades WHERE id='t'").fetchone()[0])
    journal.on_trade_closed = closed
    journal.close_trade('t', 101, 1, 'test')
    journal.close_trade('t', 101, 1, 'test')
    assert seen == ['closed']


def test_failed_booking_emits_no_close_event(tmp_path, monkeypatch):
    journal = Journal(tmp_path / 'book.db')
    journal.add_trade(Position(id='t', symbol='BTC/USDT', side=Side.LONG,
        amount=1, entry_price=100, notional_usdt=100))
    seen = []
    journal.on_trade_closed = lambda: seen.append(1)
    def fail(*args, **kwargs):
        raise OSError('write refused')
    monkeypatch.setattr('trader.engine.booking.persist', fail)
    with pytest.raises(OSError):
        journal.close_trade('t', 101, 1, 'test')
    assert seen == []
    assert journal.open_trades()[0]['id'] == 't'


def test_notification_failure_does_not_reverse_committed_close(tmp_path):
    journal = Journal(tmp_path / 'book.db')
    journal.add_trade(Position(id='t', symbol='BTC/USDT', side=Side.LONG,
        amount=1, entry_price=100, notional_usdt=100))
    def fail():
        raise RuntimeError('listener unavailable')
    journal.on_trade_closed = fail
    journal.close_trade('t', 101, 1, 'test')
    assert journal.open_trades() == []


def test_kernel_application_precedes_decisions_and_dispatch_is_off_cycle(tmp_path, monkeypatch):
    from tests.test_hierarchical_admission import full_cycle_kernel
    cfg = yaml.safe_load(Path('config.yaml').read_text())
    k = full_cycle_kernel(tmp_path, cfg, monkeypatch)
    k._maintenance = Maintenance(k.journal, k.cfg, k.feed)
    events = []
    original = k.orchestrator.decide
    def decide(*args, **kwargs):
        events.append('decision')
        return original(*args, **kwargs)
    k.orchestrator.decide = decide
    monkeypatch.setattr('trader.learning.runtime.checkpoint',
                        lambda *a, **kw: events.append('apply') or {})
    monkeypatch.setattr('trader.learning.dispatch.dispatch_pending',
                        lambda *a, **kw: pytest.fail('dispatch ran on decision thread'))
    result = k.cycle()
    assert events[0] == 'apply'
    assert 'decision' in events
    assert result['learning_dispatch'] == []
    assert result['maintenance_cycles']['outcomes']['runs'] == 0
