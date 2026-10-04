import json
from datetime import datetime,timezone
from types import SimpleNamespace
import pytest
from trader.agents.macro_guard import MacroGuard


@pytest.fixture
def guard(tmp_path,monkeypatch):
    now=[1000.]
    g=MacroGuard({'scouts':{'macro_guard':{'finnhub_token':''}}});g._token=''
    g._cache_path=tmp_path/'calendar.json'
    monkeypatch.setattr(g,'_now',lambda:datetime.fromtimestamp(now[0],timezone.utc))
    monkeypatch.setattr('trader.agents.macro_guard.time.time',lambda:now[0])
    calls=[]
    def offline():calls.append('refresh');return []
    monkeypatch.setattr(g,'_from_forexfactory',offline)
    return g,now,calls


def event(at=1000):return {'event':'macro-event','when':datetime.fromtimestamp(at,timezone.utc)}


@pytest.mark.parametrize('disk',[True,False])
def test_future_calendar_failed_refresh_never_uses_future_receipt(guard,disk):
    g,now,calls=guard
    if disk:
        g._cache_path.write_text(json.dumps({'fetched':2000,'events':[dict(event='macro-event',when=event()['when'].isoformat())]}))
    else:
        g._calendar=[event()];g._cal_fetched=2000;g._sources_ok=True
    state=g.check()
    assert calls and not state['active']
    assert 'no calendar source' in state['why']
    assert state['received_at'] is None
    assert g._calendar==[]


def test_future_assessment_cache_is_recomputed(guard):
    g,now,calls=guard
    g._state.update(active=True,event='future-assessment',until='later',why='cached',checked=2000)
    assert not g.check()['active']
    assert calls


def test_valid_calendar_and_state_keep_original_receipt_when_republished(guard,tmp_path):
    from trader.kernel import Kernel
    from trader.core.journal import Journal
    from trader.core.types import ControlState
    g,now,calls=guard
    g._calendar=[event()];g._cal_fetched=900;g._sources_ok=True
    state=g.check()
    assert state['active'] and calls==[] and state['received_at']==900
    now[0]=1100
    again=g.check()
    assert again['received_at']==900 and again['checked_at']==1000
    k=Kernel.__new__(Kernel);k.journal=Journal(tmp_path/'journal.db')
    k.state_machine=SimpleNamespace(refresh=lambda:ControlState.FROZEN)
    k._macro_step(again)
    published=json.loads(k.journal.kv_get('macro_guard_state'))
    assert published['received_at']==900 and published['checked_at']==1000
    assert published['why']==again['why']


def test_clock_rollback_during_successful_refresh_does_not_publish_calendar(guard,monkeypatch):
    g,now,calls=guard
    def rollback():now[0]=999.;g._sources_ok=True;return [event()]
    monkeypatch.setattr(g,'_from_forexfactory',rollback)
    state=g.check()
    assert not state['active'] and state['received_at'] is None


def test_successful_empty_calendar_retains_truthful_receipt(guard,monkeypatch):
    g,now,calls=guard
    def quiet():g._sources_ok=True;return []
    monkeypatch.setattr(g,'_from_forexfactory',quiet)
    state=g.check()
    assert not state['active'] and state['why']=='no active event window'
    assert state['received_at']==1000 and state['request_started_at']==1000
    now[0]=1100
    assert g.check()['received_at']==1000
