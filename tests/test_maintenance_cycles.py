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


def test_no_rules_means_no_learning_scan_or_startup_work(monkeypatch):
    from unittest.mock import Mock
    m = Maintenance(Mock(), {}, Mock())
    monkeypatch.setattr('trader.learning.dispatch.production_registry', lambda: {})
    scan = Mock(side_effect=AssertionError('unnecessary scan'))
    monkeypatch.setattr('trader.learning.dispatch.dispatch_pending', scan)
    m.watermark._top = scan
    assert m.jobs['learning'].interval is None
    assert m.jobs['learning']._pending is None
    m.learning_check()
    m.learning()
    assert m.jobs['learning']._pending is None
    scan.assert_not_called()


@pytest.mark.parametrize('resolved,upgraded,expected', [(0,0,False),(1,0,True),(0,1,True)])
def test_only_changed_outcomes_wake_learning(monkeypatch, resolved, upgraded, expected):
    from unittest.mock import Mock
    j = Mock()
    j.query.return_value = [{'symbol':'BTC/USDT'}]
    feed = Mock()
    feed.fetch_ohlcv.return_value = [1]
    m = Maintenance(j, {}, feed)
    monkeypatch.setattr('trader.engine.outcomes.resolve_pending', lambda *a, **kw: resolved)
    monkeypatch.setattr('trader.engine.outcome_backfill.upgrade_24h', lambda *a, **kw: upgraded)
    m.outcomes()
    assert (m.jobs['learning']._pending is not None) is expected


def test_recovery_check_recovers_new_evidence_and_restart_without_payload_scan(monkeypatch):
    from unittest.mock import Mock
    m = Maintenance(Mock(), {}, Mock())
    monkeypatch.setattr('trader.learning.dispatch.production_registry', lambda: {'r':object()})
    monkeypatch.setattr('trader.learning.dispatch.dispatchable', lambda r: True)
    monkeypatch.setattr('trader.learning.dispatch.registry_digest', lambda r: 'digest')
    m.learning_check()  # restart: existing evidence may be pending
    assert m.jobs['learning']._pending is not None
    m.jobs['learning']._pending = None
    m.watermark.digest = 'digest'
    m.watermark.floor = 20
    m.watermark.swept_at = m.watermark.clock()
    m.watermark._top = lambda j: 20
    m.learning_check()
    assert m.jobs['learning']._pending is None
    m.watermark._top = lambda j: 21
    m.learning_check()
    assert m.jobs['learning']._pending is not None


def test_learning_backlog_cannot_retrigger_a_busy_loop(tmp_path,monkeypatch):
    import trader.learning.dispatch as D
    from trader.learning.research_policy import rule
    monkeypatch.setattr(D,'production_registry',lambda:{rule().rule_id:rule()})
    seen=[]
    monkeypatch.setattr(D,'dispatch_pending',lambda *a,**kw:seen.append(kw) or [{}]*8)
    clock=[10.0]
    monkeypatch.setattr('trader.scheduling.maintenance.time.monotonic',lambda:clock[0])
    m=Maintenance(Journal(tmp_path/'maintenance.db'),{},None)
    assert len(m.learning())==8
    assert m.learning()==[] and len(seen)==1
    clock[0]+=60
    assert len(m.learning())==8 and len(seen)==2
