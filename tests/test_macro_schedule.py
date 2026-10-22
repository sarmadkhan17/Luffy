from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import json
import pytest
from trader.agents.macro_guard import MacroGuard
from trader.agents.macro_schedule import MacroSchedule


def setup(tmp_path, monkeypatch, at='2026-10-22T06:00:00+00:00', events=()):
    clock=[datetime.fromisoformat(at)]
    guard=MacroGuard({'scouts':{'macro_guard':{'finnhub_token':''}}})
    guard._token=''; guard._cache_path=tmp_path/'calendar.json'
    monkeypatch.setattr(guard,'_now',lambda:clock[0])
    fetches=[]
    def fetch():
        fetches.append(clock[0]);guard._sources_ok=True
        return list(events)
    monkeypatch.setattr(guard,'_from_forexfactory',fetch)
    applied=[]
    scheduler=MacroSchedule(guard,lambda state:applied.append(state))
    return clock,guard,scheduler,fetches,applied


def event(at, title='CPI'):
    return {'event':title,'when':datetime.fromisoformat(at)}


def test_empty_day_only_refreshes_at_bahrain_midnight(tmp_path,monkeypatch):
    clock,g,s,calls,out=setup(tmp_path,monkeypatch)
    deadline=s.step()
    assert deadline==datetime.fromisoformat('2026-10-22T21:00:00+00:00').timestamp()
    for hour in (9,12,20):
        clock[0]=clock[0].replace(hour=hour);s.step()
    assert len(calls)==len(out)==1
    clock[0]=clock[0].replace(hour=21);s.step()
    assert len(calls)==len(out)==2


def test_event_jobs_do_not_fetch_and_overlapping_windows_do_not_release(tmp_path,monkeypatch):
    events=[event('2026-10-22T10:00:00+00:00'),event('2026-10-22T11:00:00+00:00','FOMC')]
    clock,g,s,calls,out=setup(tmp_path,monkeypatch,events=events)
    s.step(); assert not out[-1]['active']
    for at,active in [('09:30:00',True),('12:00:01',True),('13:00:01',False)]:
        clock[0]=datetime.fromisoformat('2026-10-22T'+at+'+00:00');s.step()
        assert out[-1]['active'] is active
    assert len(calls)==1


@pytest.mark.parametrize('events',[(),(event('2026-10-22T10:00:00+00:00'),)])
def test_restart_rebuilds_jobs_from_today_without_fetch(tmp_path,monkeypatch,events):
    clock,g,s,calls,out=setup(tmp_path,monkeypatch,events=events);s.step()
    clock2,g2,s2,calls2,out2=setup(tmp_path,monkeypatch,at='2026-10-22T09:45:00+00:00')
    s2.step()
    assert not calls2
    assert out2[-1]['active'] is bool(events)


def test_failed_daily_refresh_keeps_known_window_but_not_fake_clear(tmp_path,monkeypatch):
    clock,g,s,calls,out=setup(tmp_path,monkeypatch,events=[event('2026-10-23T00:00:00+00:00')]);s.step()
    def fail():g._sources_ok=False;return []
    monkeypatch.setattr(g,'_from_forexfactory',fail)
    clock[0]=datetime.fromisoformat('2026-10-22T21:00:00+00:00');s.step()
    clock[0]=datetime.fromisoformat('2026-10-22T23:30:00+00:00');s.step()
    assert out[-1]['active']
    clock,g,s,calls,out=setup(tmp_path/'empty',monkeypatch)
    monkeypatch.setattr(g,'_from_forexfactory',lambda:[])
    s.step();assert 'no calendar source' in out[-1]['why']


def test_backward_clock_discards_future_evidence(tmp_path,monkeypatch):
    clock,g,s,calls,out=setup(tmp_path,monkeypatch,events=[event('2026-10-22T10:00:00+00:00')]);s.step()
    clock[0]-=timedelta(days=1)
    monkeypatch.setattr(g,'_from_forexfactory',lambda:[])
    s.step(); assert not g._calendar and not out[-1]['active']


def test_disabled_guard_never_fetches(tmp_path,monkeypatch):
    clock,g,s,calls,out=setup(tmp_path,monkeypatch);g.enabled=False;s.step()
    assert not calls and out[-1]['why']=='disabled'


def test_delayed_job_reconciles_current_window_not_missed_start(tmp_path,monkeypatch):
    clock,g,s,calls,out=setup(tmp_path,monkeypatch,events=[event('2026-10-22T10:00:00+00:00')]);s.step()
    clock[0]=datetime.fromisoformat('2026-10-22T14:00:00+00:00');s.step()
    assert not out[-1]['active'] and len(calls)==1


def test_busy_release_retries_without_calendar_fetch(tmp_path,monkeypatch):
    clock,g,s,calls,out=setup(tmp_path,monkeypatch)
    s.release_pending=lambda:True
    assert s.step()==clock[0].timestamp()+30
    clock[0]+=timedelta(seconds=30);s.step()
    assert len(out)==2 and len(calls)==1


def test_scheduled_clear_preserves_operator_hold(tmp_path,monkeypatch):
    from trader.core.journal import Journal
    from tests.test_owner_recovery import HookVenue
    from tests.test_activation_risk_baseline import _macro_frozen
    journal=Journal(tmp_path/'journal.db');venue=HookVenue()
    k,_=_macro_frozen(journal,venue,monkeypatch)
    journal.kv_set('macro_guard_operator_hold','1')
    clock,g,s,calls,out=setup(tmp_path,monkeypatch)
    s.apply=k._macro_step
    s.step()
    assert journal.kv_get('control_state')=='FROZEN'
    assert journal.kv_get('macro_guard_operator_hold')=='1'
    assert journal.kv_get('macro_guard_froze')=='1'
