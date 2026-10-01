"""Production paper path acceptance; no inserted trade fixtures."""
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from trader.core.config import load_config
from trader.core.journal import Journal
from trader.core.types import ControlState, Decision, Snapshot, TF_MS
from trader.engine.paper import PaperExecutor, TABLE, unsupported
from trader.engine.risk import RiskManager
from trader.kernel import Kernel
from trader.strategy import factory_handoff as F
from trader.strategy.compile import compile_spec
from trader.strategy.spec import StrategySpec
from tests.test_cross_stage_authority import _candidate
from tests.test_strategy_factory_handoff import T0, DAY, _install


@pytest.fixture(autouse=True)
def fixed_market_clock(monkeypatch):
    monkeypatch.setattr('time.time', lambda: (T0+120*DAY)/1000)


def setup(tmp_path, geo='fixed'):
    j = Journal(tmp_path / 'j.db')
    _candidate(j, geo, state='referee_passed', p10=-0.01)
    h = j.query('SELECT hash FROM research_candidates')[0]['hash']
    cfg = load_config()
    v = F.create_version(j,cfg,{'kind':'research_candidate','hash':h},at_ms=T0-DAY)
    v = _install(j,v['version_id'])
    return j,cfg,v


def frames(v):
    # The registered candidate uses a negative momentum quantile. A falling
    # deterministic history fires its REAL compiled long/short evaluator.
    step = TF_MS['4h']
    times = T0-300*step + np.arange(300)*step
    prices = 100 + np.arange(300)[::-1]*0.5
    return pd.DataFrame(dict(ts=pd.to_datetime(times,unit='ms',utc=True),
        open=prices, high=prices+0.5, low=prices-0.5,close=prices,volume=1000.0))


def snap(df, price=None):
    at = int(df.ts.iloc[-1].timestamp()*1000)+TF_MS['4h']
    return Snapshot('BTC/USDT',pd.Timestamp(at,unit='ms',tz='UTC').isoformat(),
                    float(df.close.iloc[-1] if price is None else price),{'4h':df})


def decision(v,s):
    sig = compile_spec(StrategySpec.from_dict(v['spec'])).to_evaluator()(None,s)
    assert sig is not None, v['spec']['entry_long']
    return Decision(str(s.ts),str(s.ts),'BTC/USDT',sig.action,0.8,0.1,0.6,[],
                    [{'strategy_id':sig.strategy_id,'action':sig.action.value,
                      'confidence':sig.confidence,'params':sig.params}])


def append(df, price, *, high=None,low=None):
    ts=df.ts.iloc[-1]+pd.Timedelta(hours=4)
    return pd.concat([df,pd.DataFrame([dict(ts=ts,open=price,close=price,
        high=price+0.5 if high is None else high,
        low=price-0.5 if low is None else low,volume=1000.0)])],ignore_index=True)


def forbidden_tables(j):
    return {t:j.query('SELECT * FROM '+t) for t in (
        'trades','state_kv','control_events','trade_legs','trade_fills',
        'trade_accounting_bookings','equity')}


@pytest.mark.parametrize('complete_costs', [False, True], ids=['unknown-costs', 'complete-TEST-ONLY-costs'])
def test_end_to_end_15_actual_signal_entries_and_closes(tmp_path, monkeypatch, complete_costs):
    if complete_costs:
        from tests.paper_cost_evidence_fixture import register_test_cost_evidence
        register_test_cost_evidence(monkeypatch)
    j,cfg,v=setup(tmp_path)
    before=forbidden_tables(j)
    k=Kernel.__new__(Kernel);k.journal=j;k.cfg=cfg
    k.state_machine=SimpleNamespace(state=ControlState.ACTIVE,manages_exits=lambda:True)
    from trader.engine.executor import Executor
    from trader.core.types import MarketType
    from tests.test_trade_provenance import Venue
    venue=Venue();k.executor=Executor(venue,j,cfg,MarketType.FUTURES)
    runner=k._paper_executor()
    df=frames(v)
    # Entry history must start after exact install, so shift calendar only.
    df.ts += pd.Timedelta(hours=4)
    for i in range(15):
        s=snap(df)
        d=decision(v,s)
        assert k._try_enter(d,s,5000,0) is False
        p=runner.open_positions()[0]
        tid=p['id']
        df=append(df,p['take_profit'],high=max(p['entry_price'],p['take_profit'])+0.1,
                  low=min(p['entry_price'],p['take_profit'])-0.1)
        # This bar stays strictly clear of the stop; no invented exit rows.
        assert k._manage_paper(snap(df))==[tid]
        assert runner.manage(snap(df))==[]
        t=j.query(f'SELECT * FROM {TABLE} WHERE id=?',(tid,))[0]
        assert t['close_reason']=='target'
        # A new declining history retains exact market timestamps and provides
        # another genuine signal, rather than a manually supplied action.
        for _ in range(8):
            df=append(df,float(df.close.iloc[-1])*0.99)
    trades=j.query(f'SELECT * FROM {TABLE}')
    assert len(trades)==15 and all(t['status']=='closed' for t in trades)
    assert venue.sent==[]
    for t in trades:
        ident=json.loads(t['entry_identity_json'])
        assert t['exec_mode']=='paper' and t['spec_hash']==v['spec_hash']
        assert ident['version_id']==v['version_id'] and ident['spec_sha256']==v['spec_hash']
        assert ident['install_id']==t['install_id'] and t['commission'] is None
    now=int(df.ts.iloc[-1].timestamp()*1000)+TF_MS['4h']
    probation=F.evaluate_probation(j,cfg,v['version_id'],at_ms=now)
    rec = F._load(j.query('SELECT * FROM strategy_probation_receipts')[0])
    assert rec['gross_stats']['basis'] == 'GROSS_ONLY'
    assert rec['gross_stats']['economic_validation'] == 'NOT_ECONOMICALLY_VALIDATED'
    if complete_costs:
        assert probation['status']==F.P_SATISFIED and probation['request_id']
        assert rec['stats']['paper_cost_basis'] == 'NET_WITH_COMPLETE_COST_EVIDENCE'
        assert rec['stats']['pnl'] == pytest.approx(rec['gross_stats']['pnl'] - 15 * 0.06)
        F.record_owner_decision(j,cfg,probation['request_id'],'APPROVED',actor='operator',decided_at_ms=now+1)
    else:
        assert probation['status']==F.P_COST_INCOMPLETE and probation['request_id'] is None
        assert rec['stats'] is None and len(rec['trades']) == 15
        assert F.approval_request(j, v['version_id']) is None
        assert F.state_of(j, v['version_id']) == F.SHADOW
    assert forbidden_tables(j)==before
    from tests.test_strategy_capacity import _receipt, NOW
    receipt=_receipt(j,cfg,v)
    assert receipt['status_capacity']!='ESTABLISHED'
    eligibility=F.eligible_for_first_live(j,v['version_id'],cfg=cfg,now_ms=NOW+1,available_inputs={'ohlcv'},capacity_receipt_id=receipt['receipt_id'])
    assert not eligibility.eligible
    assert 'capacity_not_established' in eligibility.reasons
    approved_before=forbidden_tables(j)
    assert k._try_enter(decision(v,snap(df)),snap(df),5000,15) is False
    assert len(runner.open_positions())==1
    assert forbidden_tables(j)==approved_before


def test_restart_replay_and_entry_retry(tmp_path):
    j,cfg,v=setup(tmp_path)
    r=PaperExecutor(j,cfg); df=frames(v); df.ts+=pd.Timedelta(hours=4)
    s=snap(df);d=decision(v,s)
    tid=r.enter(d,s,v['strategy_id'],reference_equity=5000,state=ControlState.ACTIVE)
    p=r.open_positions()[0]
    j2=Journal(tmp_path/'j.db'); r2=PaperExecutor(j2,cfg)
    assert r2.open_positions()==[p]
    assert r2.enter(d,s,v['strategy_id'],reference_equity=99999,state=ControlState.ACTIVE)==tid
    assert r2.store.kv_get('seed_equity')=='5000'
    df=append(df,p['stop_loss']-1 if p['side']=='long' else p['stop_loss']+1)
    assert r2.manage(snap(df))==[tid]
    r3=PaperExecutor(Journal(tmp_path/'j.db'),cfg)
    assert r3.manage(snap(df))==[]
    assert len(j2.query(f"SELECT * FROM {TABLE} WHERE status='closed'"))==1
    assert j2.open_trades()==[]


def test_kernel_paper_entry_preserves_live_f1_and_real_state(tmp_path):
    j,cfg,v=setup(tmp_path);df=frames(v);df.ts+=pd.Timedelta(hours=4)
    s=snap(df);d=decision(v,s)
    k=Kernel.__new__(Kernel);k.journal=j;k.cfg=cfg
    k.state_machine=SimpleNamespace(state=ControlState.ACTIVE)
    class NoVenue:
        def __getattr__(self,name):
            raise AssertionError('venue/executor access: '+name)
    k.executor=NoVenue();k.ex=NoVenue()
    before=forbidden_tables(j)
    assert k._try_enter(d,s,5000,0) is False
    assert len(k._paper.open_positions())==1 and not d.executed
    assert forbidden_tables(j)==before and j.open_trades()==[]
    assert F.live_entry_block(j,v['strategy_id']).startswith('version_not_live_authorized:')


@pytest.mark.parametrize('state',[ControlState.FROZEN,ControlState.HALTED,ControlState.RECOVERY])
def test_paper_risk_control_refusal(tmp_path,state):
    j,cfg,v=setup(tmp_path);r=PaperExecutor(j,cfg)
    df=frames(v);df.ts+=pd.Timedelta(hours=4);s=snap(df)
    with pytest.raises(ValueError,match='paper_risk:'):
        r.enter(decision(v,s),s,v['strategy_id'],reference_equity=5000,state=state)
    assert not r.open_positions()


def test_unsupported_signal_exit_refuses_exact_probation(tmp_path,monkeypatch):
    import copy
    from trader.strategy.geometries import GEOS
    geometry=copy.deepcopy(GEOS['fixed'])
    geometry.signal_exit='ret(1) > 0'
    monkeypatch.setitem(GEOS,'fixed',geometry)
    j=Journal(tmp_path/'j.db'); cfg=load_config()
    _candidate(j,'fixed',state='referee_passed',p10=-0.01)
    h=j.query('SELECT hash FROM research_candidates')[0]['hash']
    v=F.create_version(j,cfg,{'kind':'research_candidate','hash':h},at_ms=T0-DAY)
    with pytest.raises(F.HandoffRefused,match='unsupported_paper_exit:signal_exit:runtime_parity_unavailable'):
        _install(j,v['version_id'])
    assert F.state_of(j,v['version_id'])==F.VALIDATED
    assert j.query('SELECT * FROM strategy_version_installs')==[]
    assessment=F._assess(j,cfg,F.load_version(j,v['version_id']),T0,T0+DAY)
    assert assessment['status']=='UNSUPPORTED_PAPER_EXIT'
    assert assessment['reason']=='signal_exit:runtime_parity_unavailable'


def test_paper_position_invisible_to_actual_reconciliation(tmp_path):
    from trader.engine.reconcile import reconcile_futures
    j,cfg,v=setup(tmp_path);r=PaperExecutor(j,cfg)
    df=frames(v);df.ts+=pd.Timedelta(hours=4);s=snap(df)
    r.enter(decision(v,s),s,v['strategy_id'],reference_equity=5000,state=ControlState.ACTIVE)
    before=forbidden_tables(j); positions=r.open_positions()
    class ReadOnlyVenue:
        def fetch_positions(self): return []
        def fapiPrivateGetOpenAlgoOrders(self): return []
        def __getattr__(self,name): raise AssertionError('unexpected venue API: '+name)
    out=reconcile_futures(ReadOnlyVenue(),j)
    assert out['ghosts']==out['adopted']==out['aligned']==0
    assert r.open_positions()==positions
    assert forbidden_tables(j)==before


@pytest.mark.parametrize('reason',['stop','target','time','both'])
def test_frozen_exit_levels_and_bar_deadline(tmp_path,reason):
    j,cfg,v=setup(tmp_path);r=PaperExecutor(j,cfg)
    df=frames(v);df.ts+=pd.Timedelta(hours=4);s=snap(df)
    tid=r.enter(decision(v,s),s,v['strategy_id'],reference_equity=5000,state=ControlState.ACTIVE)
    p=r.open_positions()[0]
    if reason=='time':
        for _ in range(95): df=append(df,p['entry_price']+0.1)
        assert r.manage(snap(df))==[]
        df=append(df,p['entry_price']+0.1)
        want='time';px=p['entry_price']+0.1
    else:
        px=p['stop_loss'] if reason=='stop' else p['take_profit']
        high=max(p['entry_price'],px)+0.1;low=min(p['entry_price'],px)-0.1
        if reason=='both':
            high=max(p['stop_loss'],p['take_profit'])+1
            low=min(p['stop_loss'],p['take_profit'])-1
            want='stop';px=p['stop_loss']
        else: want=reason
        df=append(df,px,high=high,low=low)
    assert r.manage(snap(df))==[tid]
    row=j.query(f'SELECT * FROM {TABLE} WHERE id=?',(tid,))[0]
    assert row['close_reason']==want and row['exit_price']==px
    assert row['realized_pnl']==pytest.approx((px-p['entry_price'])*p['amount']*(1 if p['side']=='long' else -1))
    assert row['bars_held']==(96 if reason=='time' else 1)


def test_replay_missing_bar_fails_closed(tmp_path):
    j,cfg,v=setup(tmp_path);r=PaperExecutor(j,cfg)
    df=frames(v);df.ts+=pd.Timedelta(hours=4);s=snap(df)
    r.enter(decision(v,s),s,v['strategy_id'],reference_equity=5000,state=ControlState.ACTIVE)
    p=r.open_positions()[0]
    full=append(df,p['entry_price']);full=append(full,p['stop_loss']-1)
    missing=full.drop(full.index[-2]).reset_index(drop=True)
    assert r.manage(snap(missing))==[]
    assert r.open_positions()==[p]
    assert r.manage(snap(full))==[p['id']]


def test_paper_risk_limits_and_wrong_decision_hash(tmp_path):
    j,cfg,v=setup(tmp_path);r=PaperExecutor(j,cfg)
    df=frames(v);df.ts+=pd.Timedelta(hours=4);s=snap(df);d=decision(v,s)
    d.strategy_signals[0]['params']['spec_sha256']='f'*64
    with pytest.raises(ValueError,match='paper_decision_identity_unverified'):
        r.enter(d,s,v['strategy_id'],reference_equity=5000,state=ControlState.ACTIVE)
    d=decision(v,s)
    tid=r.enter(d,s,v['strategy_id'],reference_equity=5000,state=ControlState.ACTIVE)
    p=r.open_positions()[0]
    assert p['notional_usdt']/r.risk.leverage <= 5000*r.risk.max_pos_margin
    assert p['amount']*p['initial_risk'] <= 5000*r.risk.risk_pct
    # A new signal cannot open a second paper exposure on this symbol.
    df=append(df,float(df.close.iloc[-1])*0.99);s=snap(df)
    with pytest.raises(ValueError,match='paper_risk:already exposed'):
        r.enter(decision(v,s),s,v['strategy_id'],reference_equity=5000,state=ControlState.ACTIVE)
    assert r.open_positions()[0]['id']==tid


@pytest.mark.parametrize('geo',['fixed','trail'])
def test_paper_frozen_exit_matches_registered_vector_geometry(tmp_path,geo):
    from trader.agents.indicators import atr_series
    from trader.strategy.vector_backtest import _trade
    j,cfg,v=setup(tmp_path,geo);r=PaperExecutor(j,cfg)
    df=frames(v);df.ts+=pd.Timedelta(hours=4);s=snap(df)
    tid=r.enter(decision(v,s),s,v['strategy_id'],reference_equity=5000,state=ControlState.ACTIVE)
    p=r.open_positions()[0];entry_i=len(df)-1
    ex=StrategySpec.from_dict(v['spec']).exit
    if geo=='fixed':
        df=append(df,p['take_profit'])
    else:
        # Trail arms on one bar and ratchets; next bar hits that trail.
        df=append(df,p['entry_price']+5,high=p['entry_price']+6,low=p['entry_price']+4)
        df=append(df,p['entry_price']-5)
    assert r.manage(snap(df))==[tid]
    result=_trade(entry_i,p['side'],df,df.close.to_numpy(),df.high.to_numpy(),
        df.low.to_numpy(),atr_series(df).to_numpy(),ex,0.0,0.0,
        ex.time['max_bars'],float(ex.trail.get('mult',0)),
        float(ex.trail.get('arm_at_r',1)),None,None,0.0,240)
    row=j.query(f'SELECT * FROM {TABLE} WHERE id=?',(tid,))[0]
    # Compare the existing trade definition with explicitly zero costs, not
    # its registered paid backtest model. Paper cost realism stays unavailable.
    assert row['realized_pnl']==pytest.approx(result[2]*p['amount'])
    assert row['closed_at']==(df.ts.iloc[result[0]]+pd.Timedelta(hours=4)).isoformat()


def test_actual_short_signal_paper_stop(tmp_path):
    j,cfg,v=setup(tmp_path);r=PaperExecutor(j,cfg)
    df=frames(v);df.ts+=pd.Timedelta(hours=4)
    # The mirrored frozen ret(6) entry fires on this observed price rise.
    for i in range(7): df=append(df,120+20*i)
    s=snap(df);d=decision(v,s)
    assert d.action.value=='SELL'
    tid=r.enter(d,s,v['strategy_id'],reference_equity=5000,state=ControlState.ACTIVE)
    p=r.open_positions()[0];assert p['side']=='short'
    df=append(df,p['stop_loss']+1)
    assert r.manage(snap(df))==[tid]
    row=j.query(f'SELECT * FROM {TABLE} WHERE id=?',(tid,))[0]
    assert row['close_reason']=='stop' and row['realized_pnl']<0
