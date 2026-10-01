"""Universal creation fence and exact Governor activation, all TEST-ONLY.

No real venue client, runtime boot or production database writes.
"""
import copy
import inspect
import sqlite3
import time

import pytest

from trader.core.config import load_config
from trader.core.journal import Journal
from trader.core.types import Action, Decision, MarketType
from trader.engine.executor import Executor
from trader.engine.risk import RiskManager
from trader.kernel import Kernel
from trader.strategy import factory_handoff as F, capacity as C
from trader.strategy.spec import StrategySpec, ExitSpec
from tests.authority_factory_fixtures import cfg, _journal, _approved, _install, T0, INPUTS
from tests.authority_capacity_fixtures import _publish, _inputs, NOW
from tests.authority_legacy_fixtures import seed, grant
from tests.test_entry_recovery import Venue


def proposal(sid, name):
    return StrategySpec(id=sid, name=name, thesis='TEST-ONLY historical mechanism describes a testable market inefficiency with sufficient detail.', invalidation='TEST-ONLY retire when this synthetic mechanism fails its preregistered evidence.',
        provenance={}, universe={'include':['BTC/USDT']}, timeframe='4h', direction='long',
        entry_long='close > 0', entry_short='', filters=[], exit=ExitSpec(),
        regime_filter=[], markets=['futures'])


def activate(j, config, vid, **kw):
    return F.govern_version(j, config, vid, 'ACTIVE', actor='operator',
        reason_code='TEST-ONLY exact activation', at_ms=NOW, allocation=.1, **kw)


def approved_world(tmp_path, config, monkeypatch):
    j, _ = _journal(tmp_path)
    v, p, d = _approved(j, config)
    monkeypatch.setattr(time, 'time', lambda: NOW / 1000)
    j.kv_set('control_state', 'ACTIVE')
    risk = RiskManager(config, j)
    risk.update_equity(10000)
    proof = risk.release_check(10000)
    return j, F.load_version(j, v['version_id']), risk, proof


def established_capacity(j, config, v, monkeypatch):
    """Complete missing capacity sources ONLY inside this synthetic test."""
    _publish(j, config)
    real_venue = C._venue
    def venue(reg, price):
        out = real_venue(reg, price)
        out['maximums'] = C._dim(C.ESTABLISHED, None, max_quantity=5.)
        out['leverage'] = C._dim(C.ESTABLISHED, None)
        out['account_eligibility'] = C._dim(C.ESTABLISHED, None, account_eligibility='ELIGIBLE')
        return out
    monkeypatch.setattr(C, '_venue', venue)
    monkeypatch.setattr(C, '_funding', lambda i: C._dim(C.ESTABLISHED, None, max_quantity=1.))
    monkeypatch.setattr(C, '_liquidity', lambda i: C._dim(C.ESTABLISHED, None, max_quantity=.5))
    receipt = C.build(_inputs(j, config, v))
    assert receipt['status'] == C.ESTABLISHED
    C.record(j, receipt, at_ms=NOW)
    return receipt['receipt_id']


def test_original_mechanism_admission_bypass_submits_nothing(tmp_path, monkeypatch):
    j = Journal(tmp_path/'mechanism.db')
    spec = proposal('dynamic','Dynamic')
    class Analyst:
        def __init__(self, *a): pass
        def review_deployed(self, book): return []
        def admit(self, proposed, book): return True, {'recent':{'pooled_pf':2,'trades':30}}
        def set_measured_regimes(self, spec): return []
    class Writer:
        def __init__(self, *a): pass
        def write(self, **kw): return spec, {}
    monkeypatch.setattr('trader.brain.analyst.Analyst', Analyst)
    monkeypatch.setattr('trader.brain.spec_writer.SpecWriter', Writer)
    monkeypatch.setattr('trader.brain.llm.BrainLLM', lambda *a: None)
    k = Kernel.__new__(Kernel)
    k.journal, k.cfg, k.feed, k.notifier = j, load_config(), None, None
    monkeypatch.setattr(k, '_strategist_knowledge', lambda: {})
    monkeypatch.setattr(k, '_load_population', lambda: [])
    F.ensure(j)
    j.kv_set('control_state', 'ACTIVE')
    rep = k._mechanism_once()
    assert rep['added'] == ['Dynamic']
    assert j.list_specs()[0][0]['state'] == 'paper'
    assert not F.versioned(j, spec.id)
    venue = Venue()
    ex = Executor(venue, j, k.cfg, MarketType.FUTURES)
    d = Decision('d','c','BTC/USDT',Action.BUY,1,.5,.8,[],[])
    assert ex.open(d,2,2,95,110,spec.id,spec.name) is None
    assert 'VERSIONED_AUTHORITY_REQUIRED' in d.skip_reason
    assert venue.sent == []
    # Even direct state mutation cannot create authority.
    with j._tx() as db: db.execute("UPDATE strategies SET state='active' WHERE id=?", (spec.id,))
    assert ex.open(d,2,2,95,110,spec.id,spec.name) is None
    assert venue.sent == []


def test_unknown_and_new_genome_fail_closed(tmp_path):
    j = Journal(tmp_path/'j.db')
    seed(j)
    assert F.live_entry_block(j,'strategy') is None  # explicit grandfather only
    assert F.live_entry_block(j,'unknown') == 'VERSIONED_AUTHORITY_REQUIRED'
    with j._tx() as db: db.execute("UPDATE strategies SET params='{} changed' WHERE id='strategy'")
    assert F.live_entry_block(j,'strategy') == 'VERSIONED_AUTHORITY_REQUIRED'


def test_unversioned_spec_requires_explicit_exact_grandfather(tmp_path):
    j=Journal(tmp_path/'j.db')
    spec=proposal('historical','Historical')
    j.upsert_spec(spec)
    assert F.live_entry_block(j,spec.id) == 'VERSIONED_AUTHORITY_REQUIRED'
    grant(j,spec.id)
    assert F.live_entry_block(j,spec.id) is None
    spec.entry_long='close > 1'
    j.upsert_spec(spec)
    assert F.live_entry_block(j,spec.id) == 'VERSIONED_AUTHORITY_REQUIRED'
    with pytest.raises(sqlite3.IntegrityError):
        with j._tx() as db: db.execute('DELETE FROM strategy_legacy_authorities')


def test_exact_complete_test_only_evidence_activates_without_execution(tmp_path,cfg,monkeypatch):
    j,v,risk,proof=approved_world(tmp_path,cfg,monkeypatch)
    rid=established_capacity(j,cfg,v,monkeypatch)
    proof=risk.release_check(10000)
    before=j.kv_get('control_state')
    event=activate(j,cfg,v['version_id'], available_inputs=INPUTS,
        capacity_receipt_id=rid,risk_manager=risk,risk_release=proof)['event']
    assert event['from_state'] == F.APPROVED_FIRST_LIVE
    assert F.state_of(j,v['version_id']) == 'ACTIVE'
    assert event['owner_decision_id'] and event['risk_release']['allowed']
    assert j.kv_get('control_state') == before
    assert F.live_entry_block(j,v['strategy_id']) == 'version_first_live_execution_not_enabled'
    venue=Venue(); ex=Executor(venue,j,cfg,MarketType.FUTURES)
    d=Decision('d','c','BTC/USDT',Action.BUY,1,.5,.8,[],[])
    assert ex.open(d,2,2,95,110,v['strategy_id'],'version') is None
    assert venue.sent == []


@pytest.mark.parametrize('gap', ['capacity','inputs','control','risk','stale_risk','clock','install','validation','probation','economic','owner','lifecycle'])
def test_activation_refuses_each_missing_or_changed_prerequisite(tmp_path,cfg,monkeypatch,gap):
    j,v,risk,proof=approved_world(tmp_path,cfg,monkeypatch)
    rid=established_capacity(j,cfg,v,monkeypatch)
    proof=risk.release_check(10000)
    kw=dict(available_inputs=INPUTS,capacity_receipt_id=rid,risk_manager=risk,risk_release=proof)
    if gap=='capacity': kw['capacity_receipt_id']=None
    if gap=='inputs': kw['available_inputs']=None
    if gap=='control': j.kv_set('control_state','FROZEN')
    if gap=='risk': kw['risk_release']=None
    if gap=='stale_risk': risk.release_check(10000)
    if gap=='clock': monkeypatch.setattr(time,'time',lambda: NOW/1000+100)
    if gap=='install':
        spec=StrategySpec.from_dict(v['spec']); spec.entry_long='close > 1'; j.upsert_spec(spec)
    if gap in ('validation','probation','owner'):
        # Inject corrupt persisted evidence; normal updates are immutable.
        table={'validation':'strategy_validation_receipts','probation':'strategy_probation_receipts','owner':'strategy_approval_decisions'}[gap]
        with j._tx() as db:
            db.execute(f'DROP TRIGGER {table}_no_update')
            db.execute(f"UPDATE {table} SET canonical_sha256='corrupt'")
    if gap=='economic':
        from trader.engine import paper_cost_evidence as P
        monkeypatch.delitem(P.SOURCE_VALIDATORS, 'TEST-ONLY.authority.v1')
    if gap=='lifecycle': F.retire_version(j,v['version_id'],F.RETIRED,actor='strategy_governor',reason_code='TEST-ONLY',at_ms=NOW)
    with pytest.raises(F.HandoffRefused): activate(j,cfg,v['version_id'],**kw)
    assert F.state_of(j,v['version_id']) != 'ACTIVE'


def test_current_unestablished_capacity_is_refused(tmp_path,cfg,monkeypatch):
    j,v,risk,proof=approved_world(tmp_path,cfg,monkeypatch)
    _publish(j,cfg)
    receipt=C.build(_inputs(j,cfg,v)); C.record(j,receipt,at_ms=NOW)
    assert receipt['status'] != C.ESTABLISHED
    proof=risk.release_check(10000)
    with pytest.raises(F.HandoffRefused,match='capacity'):
        activate(j,cfg,v['version_id'],available_inputs=INPUTS,capacity_receipt_id=receipt['receipt_id'],risk_manager=risk,risk_release=proof)
    assert F.state_of(j,v['version_id']) == F.APPROVED_FIRST_LIVE


def test_direct_database_lifecycle_and_replace_are_refused(tmp_path,cfg):
    j,h=_journal(tmp_path)
    vid=F.create_version(j,cfg,{'kind':'research_candidate','hash':h},at_ms=T0)['version_id']
    v=_install(j,vid)
    for sql,args in [
        ("UPDATE strategies SET state='active' WHERE id=?", (v['strategy_id'],)),
        ("INSERT INTO strategy_governor_events(version_id,to_state,canonical_json,canonical_sha256) VALUES(?,'ACTIVE','{}','bad')",(vid,)),
        ("INSERT INTO strategy_version_events(version_id,to_state,reason_code,actor,at_ms) VALUES(?,'ACTIVE','bypass','kernel',1)",(vid,))]:
        with pytest.raises(sqlite3.IntegrityError,match='STRATEGY_GOVERNOR_REQUIRED'):
            with j._tx() as db: db.execute(sql,args)
    with pytest.raises(sqlite3.IntegrityError,match='STRATEGY_GOVERNOR_REQUIRED'):
        j.upsert_spec(StrategySpec.from_dict(v['spec']),state='active')
    F.retire_version(j,vid,F.RETIRED,reason_code='TEST-ONLY',actor='strategy_governor',at_ms=T0+1)
    assert F.state_of(j,vid) == F.RETIRED
    assert j.list_strategies()[0]['state']=='retired'


def test_no_automatic_activation_caller():
    from pathlib import Path
    for path in Path('trader').rglob('*.py'):
        if path.name=='factory_handoff.py': continue
        assert 'govern_version(' not in path.read_text(), str(path)


def test_legacy_authority_cannot_be_minted_by_current_database_writer(tmp_path):
    j=Journal(tmp_path/'j.db')
    with pytest.raises(sqlite3.IntegrityError,match='LEGACY_AUTHORITY_IMPORT_REQUIRED'):
        with j._tx() as db:
            db.execute("INSERT INTO strategy_legacy_authorities VALUES('new','{}','bad')")
    assert F.live_entry_block(j,'new') == 'VERSIONED_AUTHORITY_REQUIRED'


def test_governor_owns_pause_degrade_reactivate_and_retire(tmp_path,cfg,monkeypatch):
    j,v,risk,proof=approved_world(tmp_path,cfg,monkeypatch)
    rid=established_capacity(j,cfg,v,monkeypatch)
    activate(j,cfg,v['version_id'],available_inputs=INPUTS,
        capacity_receipt_id=rid,risk_manager=risk,risk_release=risk.release_check(10000))
    for state in ('PAUSED','REACTIVATED',F.DEGRADED,'REACTIVATED',F.RETIRED):
        kw={}
        if state=='REACTIVATED':
            kw=dict(allocation=.1,available_inputs=INPUTS,capacity_receipt_id=rid,
                risk_manager=risk,risk_release=risk.release_check(10000))
        F.govern_version(j,cfg,v['version_id'],state,actor='strategy_governor',
            reason_code='TEST-ONLY lifecycle',at_ms=NOW,**kw)
        assert F.state_of(j,v['version_id'])==state
    assert F.retire_version(j,v['version_id'],F.RETIRED,actor='strategy_governor',reason_code='retry',at_ms=NOW)['status']=='duplicate'
    with pytest.raises(F.HandoffRefused):
        activate(j,cfg,v['version_id'],available_inputs=INPUTS,capacity_receipt_id=rid,
            risk_manager=risk,risk_release=risk.release_check(10000))


def test_legacy_promotion_statistics_cannot_grant_authority(tmp_path):
    from trader.strategy import promotion
    j=Journal(tmp_path/'j.db')
    with j._tx() as db:
        db.execute("INSERT INTO strategies(id,name,kind,params,state,regime_filter,markets) VALUES('new','new','ema_trend','{}','paper','[]','[]')")
        for i in range(15):
            db.execute("INSERT INTO trades(id,symbol,side,amount,entry_price,strategy_id,status,realized_pnl,opened_at) VALUES(?,'BTC/USDT','long',1,100,'new','closed',10,'2026-10-01')",(str(i),))
    assert promotion.evaluate_population(j)==[]
    assert j.list_strategies()[0]['state']=='paper'
    assert F.live_entry_block(j,'new')=='VERSIONED_AUTHORITY_REQUIRED'
