"""Offline owner-policy admission proofs. Network and runtime launches forbidden."""
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock
import json
import socket
from pathlib import Path

import pytest
import yaml

from trader import attention_admission as A
from trader.core.journal import Journal
from trader.core.types import Snapshot, MarketType, ControlState, Action, Decision
from trader.data.broad_crypto import project, observe
from trader.kernel import Kernel

CUT = 1_800_000_000_000


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket.socket,'connect',Mock(side_effect=AssertionError('network forbidden')))


@pytest.fixture
def cfg():
    return yaml.safe_load(Path('config.yaml').read_text())


@pytest.fixture
def policy(cfg):
    return A.AdmissionPolicy.from_config(cfg)


def observation(n=30, scores=True, relevant=True):
    symbols=[f'S{i:02}/USDT' for i in range(n)]
    return dict(schema='broad-crypto.v1',source_cut_ms=CUT,source_identity='bulk-v1',
        rows={s:dict(symbol=s,asset_class='CRYPTO',quality='VALID',available_at_ms=CUT,quote_volume=1e8,
                    missing_reasons=[]) for s in symbols},
        peer_cohort=symbols,strategy_relevance={s:['spec'] if relevant else [] for s in symbols},
        context_anchors=['BTC/USDT','ETH/USDT'],exposure_required=['HELD/USDT'],
        salience_rows={s:(dict(eligible=True,salience=float(n-i+2),components={'volume_anomaly':float(n-i+2)},status='ok')
                         if scores else dict(eligible=False,status='warmup',reason='warmup')) for i,s in enumerate(symbols)},
        salience_config={'min_salience':2.0},salience_anchor_ms=CUT,status='VALID' if scores else 'DEGRADED_CACHED_HISTORY')


def receipt(policy,n=30,**kw):
    return A.admit(observation(n,**kw),policy,A.initial_state())


def test_canonical_owner_values(policy):
    assert policy.max_deep_slots==12 and policy.event_reserve==2 and policy.exploration_reserve==2 and policy.minimum_slots==0


def test_capacity_reservations_and_broad_cohort(policy):
    r=receipt(policy)
    assert r['capacity_used']==12 and len(r['roles']['event'])==2 and len(r['roles']['exploration'])==2
    assert len(r['roles']['broad_crypto'])==len(r['roles']['peers'])==30
    assert len(set(r['admitted_symbols']))==12
    assert 'BTC/USDT' not in r['admitted_symbols'] and r['roles']['anchors']==('BTC/USDT','ETH/USDT')
    assert len(r['rejected_deferred'])==18


def test_unused_reserves_not_backfilled(policy):
    o=observation(scores=False)
    r=A.admit(o,policy,A.initial_state())
    assert r['capacity_used']==10 and not r['roles']['event']
    assert r['reserved_unused']['event']==2 and r['unused_capacity']==2
    assert all(x['score'] is None for x in r['rows'])
    assert {x['category'] for x in r['rows']}=={'exploration','warmup_relevant','deferred'}


@pytest.mark.parametrize('n', [0,1,2,3,8,11])
def test_small_empty_and_zero_candidates(policy,n):
    r=receipt(policy,n)
    assert r['capacity_used']<=min(n,policy.max_deep_slots)
    assert len(set(r['admitted_symbols']))==r['capacity_used']


def test_zero_admission_valid_not_forced(policy):
    o=observation(8,scores=False,relevant=False)
    for row in o['rows'].values():row['quality']='STALE'
    r=A.admit(o,policy,A.initial_state())
    assert r['admitted_symbols']==[] and r['unused_capacity']==12
    assert all(x['reason']=='broad_input_unusable' for x in r['rows'])


def test_deterministic_replay_ties_and_event_overflow(policy):
    o=observation()
    for x in o['salience_rows'].values():x['salience']=3.0
    r=A.admit(o,policy,A.initial_state())
    assert r==A.admit(o,policy,A.initial_state())
    assert list(r['roles']['event'])==['S00/USDT','S01/USDT']
    assert r['tie_break_order']==sorted(o['rows'])
    assert A.verify_receipt(json.loads(A.canonical(r)))


def test_same_event_is_not_new_next_cycle(policy):
    o=observation();a=A.admit(o,policy,A.initial_state());b=A.admit(o,policy,a['next_state'])
    assert not b['roles']['event'] and b['reserved_unused']['event']==2


def test_rotation_collisions_restart_and_failed_acquisition(policy,tmp_path):
    j=Journal(tmp_path/'j.db');o=observation()
    a=A.persist(j,o,policy)
    assert not set(a['roles']['event'])&set(a['roles']['exploration'])
    # Acquisition failure follows commit. A restarted producer uses committed
    # rotation, rather than repeatedly acquiring the same failed candidate.
    b=A.persist(Journal(j.db_path),o,policy)
    assert a['roles']['exploration']!=b['roles']['exploration']
    assert A.latest(j)['receipt_id']==b['receipt_id']
    assert b==A.admit(o,policy,a['next_state'])


def test_exploration_no_starvation_in_stable_cohort(policy):
    state=A.initial_state(); seen=set();o=observation(30,scores=False,relevant=False)
    for _ in range(15):
        r=A.admit(o,policy,state);state=r['next_state'];seen.update(r['roles']['exploration'])
    assert seen==set(o['rows'])


def test_relevance_no_starvation_and_required_deferred(policy):
    state=A.initial_state();o=observation(scores=False)
    o['required_symbols']=list(o['rows']);o['required_by_strategy']={s:['spec'] for s in o['rows']}
    seen=set()
    for _ in range(10):
        r=A.admit(o,policy,state);state=r['next_state'];seen.update(r['admitted_symbols'])
        assert r['required_deferred'] and r['capacity_used']<=12
        assert any(x['category']=='strategy_required' for x in r['rows'])
    assert seen==set(o['rows'])


@pytest.mark.parametrize('mutation', ['cursor','receipt','peer','state_hash','missing'])
def test_corruption_negative_controls(policy,tmp_path,mutation):
    r=receipt(policy);bad=deepcopy(r)
    if mutation=='cursor': bad['prior_state']['exploration_cursor']='forged'
    elif mutation=='receipt': bad['capacity_used']=13
    elif mutation=='peer': bad['roles']['peers']=bad['admitted_symbols']
    elif mutation=='state_hash': bad['next_state']['sha256']='0'*64
    else: bad.pop('receipt_id')
    with pytest.raises(ValueError):A.verify_receipt(bad)


def test_persisted_rotation_corruption_refuses_without_reset(policy,tmp_path):
    j=Journal(tmp_path/'j.db');A.persist(j,observation(),policy)
    j.kv_set(A.STATE_KEY,'{"exploration_cursor":"forged"}')
    with pytest.raises(ValueError):A.persist(j,observation(),policy)
    assert j.kv_get(A.STATE_KEY)=='{"exploration_cursor":"forged"}'


def markets(symbols):
    return {s:dict(id=s.replace('/',''),active=True,quote='USDT',spot=False,
        info=dict(symbol=s.replace('/',''),underlyingType='COIN',status='TRADING',contractType='PERPETUAL',
                  onboardDate=CUT-100*86400000)) for s in symbols}


def ticker(last=10,volume=2e8):
    return dict(last=last,quoteVolume=volume,timestamp=CUT,percentage=5)


def test_crypto_eligibility_and_pit():
    syms=['BTC/USDT','XAU/USDT','NEW/USDT','STALE/USDT','BAD/USDT','DENIED/USDT','MISSING/USDT']
    ms=markets(syms);ms['XAU/USDT']['info']['underlyingType']='COMMODITY';ms['MISSING/USDT']['info'].pop('underlyingType')
    ts={s:ticker() for s in syms};ts['NEW/USDT']=ticker(.001,10);ts['STALE/USDT']['timestamp']=CUT-400000
    ts['BAD/USDT']['timestamp']=CUT+1
    rows,excluded=project(ts,ms,cut=CUT,blacklist=set(),stale_ms=300000,capability_exclusions=['DENIED/USDT'])
    assert set(rows)=={'BTC/USDT','NEW/USDT'}  # no price/volume/top-N observation shrink
    assert excluded['XAU/USDT']==excluded['MISSING/USDT']=='non_crypto_or_unknown_classification'
    assert excluded['DENIED/USDT']=='account_symbol_ineligible'
    assert excluded['STALE/USDT']==excluded['BAD/USDT']=='stale_or_invalid_ticker_clock'


def broad_kernel(tmp_path,cfg,n=30):
    k=object.__new__(Kernel);k.cfg=deepcopy(cfg);k.population=[];k.journal=Journal(tmp_path/'j.db')
    syms=list(observation(n)['rows']);ms=markets(syms)
    k.exchange=NS(markets=ms,id='binanceusdm',urls={'api':'https://offline.example/fapi'});k.universe=NS(ex=NS(fetch_tickers=Mock(return_value={s:ticker() for s in syms})),blacklist=set())
    import pandas as pd
    from trader.data import market_provenance as mp
    metadata=mp.annotate(pd.DataFrame(dict(ts=pd.to_datetime([CUT],unit='ms',utc=True),close=[float(len(ms))])),
        instrument_id='ref:venue_metadata',source=mp.venue_source(k.exchange),kind='reference',received_ms=CUT,
        raw=[{'metadata':ms,'event_time_basis':'local_snapshot'}],transform_version='venue_metadata.v1')
    k._venue_metadata_key='context:venue_metadata:TEST_ONLY'
    k._venue_metadata_revision=metadata['revision_id'].iloc[0]
    with k.journal._tx() as conn:
        mp.init(conn);mp.append(conn,k._venue_metadata_key,metadata)
    k.feed=NS(cached_ohlcv=Mock(return_value=None),fetch_ohlcv=Mock(side_effect=AssertionError('deep fetch before admission')))
    k._attention=None;k._spec_rows=[]
    return k


def test_normal_broad_scan_is_offline_per_symbol_and_worker_independent(tmp_path,cfg,policy,monkeypatch):
    monkeypatch.setattr('trader.data.broad_crypto.time.time',lambda:CUT/1000)
    k=broad_kernel(tmp_path,cfg)
    o,_,_=observe(k,[])
    assert len(o['rows'])==30 and k.universe.ex.fetch_tickers.call_count==1
    assert k.feed.fetch_ohlcv.call_count==0 and k.feed.cached_ohlcv.call_count==30
    a=A.admit(o,policy,A.initial_state())
    k._attention=NS(cfg=cfg['attention'])
    b=A.admit(observe(k,[])[0],policy,A.initial_state())
    assert A.canonical(a)==A.canonical(b)
    assert len(a['roles']['exploration'])==2 and a['degraded_state']=='DEGRADED_CACHED_HISTORY'


def test_strategy_include_exclude_and_required_semantics(tmp_path,cfg,policy,monkeypatch):
    from trader.strategy.spec import StrategySpec
    monkeypatch.setattr('trader.data.broad_crypto.time.time',lambda:CUT/1000)
    k=broad_kernel(tmp_path,cfg)
    sp=NS(id='spec',timeframe='4h',universe=dict(include=list(observation()['rows']),exclude=['S00/USDT'],
        required_evaluation=['S20/USDT']),to_dict=lambda:{'id':'spec'})
    k._spec_rows=[({},sp)]
    o,_,_=observe(k,[]);r=A.admit(o,policy,A.initial_state())
    assert o['strategy_relevance']['S00/USDT']==[]
    assert len(o['strategy_relevance'])==30 and r['capacity_used']<=12
    assert next(x for x in r['rows'] if x['symbol']=='S20/USDT')['category']=='strategy_required'
    from trader.engine.orchestrator import symbol_allows
    assert not symbol_allows(NS(eligible_symbols=frozenset()),'S00/USDT')
    assert not symbol_allows(NS(eligible_symbols=frozenset({'S01/USDT'})),'S00/USDT')


def test_peer_data_and_cross_section_do_not_follow_admission(tmp_path,cfg,policy):
    k=broad_kernel(tmp_path,cfg);r=receipt(policy)
    k._admission_receipt=r;k._scan_timeframes=('4h',)
    k._peer_frames(r['roles']['peers'])
    assert {call.args[0] for call in k.feed.cached_ohlcv.call_args_list}==set(r['roles']['peers'])
    assert len(r['roles']['peers'])>len(r['admitted_symbols'])
    assert r['peer_cohort_id']==A.digest([A.PEER_VERSION,r['roles']['peers']])


def safety_kernel(tmp_path,cfg):
    k=broad_kernel(tmp_path,cfg)
    trades=[dict(id=str(i),symbol=f'HELD{i:02}/USDT') for i in range(15)]
    k.journal.open_trades=lambda:trades
    k._portfolio_market={};k._portfolio_holdings_market={}
    k._snapshot_for=Mock(side_effect=lambda s,**kw:NS(symbol=s,ts='2026-01-01',price=100))
    k._detect_exchange_exits=Mock(return_value=0);k._manage_one=Mock();k._manages_exits=lambda:True
    return k


def test_cap_12_to_1_never_limits_position_13_and_beyond(tmp_path,cfg,policy):
    k=safety_kernel(tmp_path,cfg)
    for cap in [policy.max_deep_slots,1]:
        p=A.AdmissionPolicy(cap,policy.event_reserve,policy.exploration_reserve,0)
        r=receipt(p)
        assert r['capacity_used']==cap
        assert k._service_exposure(set(r['admitted_symbols']),{})==15
    assert k._manage_one.call_count==30 and k._detect_exchange_exits.call_count==30
    assert len(k._portfolio_holdings_market)==15
    k._manage_one.assert_any_call({'id':'14','symbol':'HELD14/USDT'},k._portfolio_holdings_market['HELD14/USDT'],None)


def test_failed_safety_acquisition_does_not_drop_reconciliation_or_other_holdings(tmp_path,cfg):
    k=safety_kernel(tmp_path,cfg)
    k._snapshot_for.side_effect=lambda s,**kw: None if s=='HELD00/USDT' else NS(symbol=s,ts='2026-01-01',price=100)
    assert k._service_exposure(set(),{})==14
    k._detect_exchange_exits.assert_any_call('HELD00/USDT')
    assert k._manage_one.call_count==14


def test_all_trades_of_orphan_symbol_get_exits(tmp_path,cfg):
    k=safety_kernel(tmp_path,cfg)
    k.journal.open_trades=lambda:[dict(id='a',symbol='HELD/USDT'),dict(id='b',symbol='HELD/USDT')]
    assert k._service_exposure(set(),{})==1 and k._manage_one.call_count==2


def test_receipt_required_before_deep_acquisition(tmp_path,cfg,policy):
    k=broad_kernel(tmp_path,cfg);k._snapshot_for=Mock(return_value=NS())
    r=receipt(policy)
    with pytest.raises(ValueError):k._candidate_snapshot_for('S29/USDT',r,{})
    with pytest.raises(ValueError):k._candidate_snapshot_for('S00/USDT',None,{})
    assert k._snapshot_for.call_count==0
    snap=k._candidate_snapshot_for('S00/USDT',r,{})
    assert snap.admission_context['receipt_id']==r['receipt_id']


def test_portfolio_candidates_require_admission_but_holdings_are_independent(policy):
    from trader.portfolio.current import admitted_requests
    r=receipt(policy)
    good=NS(symbol='S00/USDT',admission_context={'receipt_id':r['receipt_id']})
    bad=NS(symbol='S29/USDT',admission_context={'receipt_id':r['receipt_id']})
    requests=[dict(candidate_id='d1:v'),dict(candidate_id='d2:v')]
    assert admitted_requests(requests,{'d1':good,'d2':bad},r)==requests[:1]
    assert admitted_requests(requests,{'d1':good},None)==[]


def test_learning_retains_actual_admission_category(tmp_path,policy,cfg):
    from trader.learning.capture_runtime import frame_chunks
    j=Journal(tmp_path/'j.db');r=receipt(policy)
    snap=Snapshot('S00/USDT','2027-01-15T08:00:00+00:00',10,{},admission_context={'receipt_id':r['receipt_id'],'category':'event'})
    with j._tx() as db:
        got=frame_chunks(db,snap,CUT)
    assert got['admission_context']==snap.admission_context
    A.persist(j,observation(),policy)
    assert A.latest(j)['rejected_deferred']
    assert j.query("SELECT count(*) n FROM brain_events WHERE kind='attention_admission'")[0]['n']==1


def test_admission_corruption_is_visible_in_owner_view(tmp_path,policy):
    j=Journal(tmp_path/'j.db');A.persist(j,observation(),policy)
    view=A.owner_view(j,now_ms=CUT,stale_seconds=300)
    assert view['deep_admitted']==12 and view['broad_observed']==30 and view['exposure_required']==1
    j.kv_set(A.RECEIPT_KEY,'{"receipt_id":"forged"}')
    assert A.owner_view(j,now_ms=CUT,stale_seconds=300)['status']=='refused'


def full_cycle_kernel(tmp_path,cfg,monkeypatch,n=30):
    from datetime import datetime,timezone
    k=safety_kernel(tmp_path,cfg)
    monkeypatch.setattr('trader.data.broad_crypto.time.time',lambda:CUT/1000)
    k.market_type=MarketType.FUTURES
    k.state_machine=NS(state=ControlState.FROZEN,refresh=lambda:ControlState.FROZEN,can_enter=lambda:False)
    k.executor=NS(recovery_pending=lambda:False,recover_entries=Mock())
    k._risk_step=lambda:(1000,{'equity':1000,'drawdown_pct':0,'risk_state':'ok'})
    k._record_risk_assessment=Mock();k._macro_step=Mock();k._publish_news_guard=Mock()
    k._drain_close_requests=lambda:0;k._drain_panic=lambda:None
    k.macro_guard=k.news_guard=NS(check=lambda:{'active':False})
    k._refresh_btc_context=Mock();k._refresh_context_anchors=Mock()
    k._funding_map=Mock(return_value={});k._oi_map=Mock(return_value={})
    k._snapshot_for=Mock(side_effect=lambda s,**kw:Snapshot(s,datetime.fromtimestamp(CUT/1000,timezone.utc).isoformat(),10,{},universe=kw.get('universe')))
    k.positioning_agent=k.depth_agent=NS(set_context=Mock());k._order_book=Mock(return_value={})
    k.orchestrator=NS(decide=Mock(side_effect=lambda snap,*a,**kw:Decision('d:'+snap.symbol,'c:'+snap.symbol,
        snap.symbol,Action.HOLD,0,.2,.5,[],[],ts=snap.ts)),journalize=Mock())
    k._maybe_resolve_outcomes=Mock();k._record_excursions=Mock();k._equity_provenance=lambda _:None
    def portfolio():
        return dict(candidate_symbols=sorted(s.symbol for s in k._portfolio_market.values()),
                    held_symbols=sorted(k._portfolio_holdings_market))
    k._portfolio_checkpoint=portfolio;k.heartbeat=NS(beat=Mock())
    return k


def test_actual_cycle_admits_before_deep_and_keeps_all_holdings(tmp_path,cfg,monkeypatch):
    k=full_cycle_kernel(tmp_path,cfg,monkeypatch)
    stats=k.cycle()
    admitted=set(k._admission_receipt['admitted_symbols']);held=set(k._exposure_symbols())
    assert stats['admission']['broad_observed']==30 and stats['admission']['deep_admitted']==2
    assert stats['deep_processed']==stats['decisions']==2
    assert {c.args[0] for c in k._order_book.call_args_list}==admitted
    assert {c.args[0] for c in k._snapshot_for.call_args_list}==admitted|held
    assert k.orchestrator.decide.call_count==len(admitted)
    assert k._manage_one.call_count==15 and k._detect_exchange_exits.call_count==17
    assert set(stats['portfolio_runtime']['held_symbols'])==held
    assert set(stats['portfolio_runtime']['candidate_symbols'])==admitted
    k._funding_map.assert_called_once_with(list(k._symbol_roles.deep))
    k._oi_map.assert_called_once_with(symbols=list(k._symbol_roles.deep))
    # Rejected discretionary symbols never cross the expensive boundary.
    assert not (set(k._admission_receipt['rejected_deferred'])&{c.args[0] for c in k._snapshot_for.call_args_list})


@pytest.mark.parametrize('failure',['empty_tickers','stale_tickers','cursor_corrupt','admission_storage','snapshot_failure','decision_failure'])
def test_actual_cycle_failure_never_strands_held_symbol_15(tmp_path,cfg,monkeypatch,failure):
    k=full_cycle_kernel(tmp_path,cfg,monkeypatch)
    if failure=='empty_tickers':k.universe.ex.fetch_tickers.return_value={}
    elif failure=='stale_tickers':
        for r in k.universe.ex.fetch_tickers.return_value.values():r['timestamp']=CUT-400000
    elif failure=='cursor_corrupt':k.journal.kv_set(A.STATE_KEY,'{}')
    elif failure=='admission_storage':monkeypatch.setattr(A,'persist',Mock(side_effect=ValueError('store unavailable')))
    elif failure=='snapshot_failure':
        k._snapshot_for.side_effect=lambda s,**kw:None if s.startswith('S') else NS(symbol=s,ts='2027-01-15T08:00:00+00:00',price=10)
    else:k.orchestrator.decide.side_effect=ValueError('decision failed')
    if failure=='decision_failure':
        with pytest.raises(ValueError,match='decision failed'):k.cycle()
    else:
        stats=k.cycle()
        assert not stats['entries']
    assert k._manage_one.call_count==15
    k._detect_exchange_exits.assert_any_call('HELD14/USDT')


def test_actual_cross_section_changes_if_peer_cohort_is_wrong(tmp_path,cfg,policy):
    import numpy as np
    from tests.test_features_xs import _df
    from trader.strategy.features import FeatureCtx
    from trader.strategy import dsl
    k=broad_kernel(tmp_path,cfg);r=receipt(policy);k._admission_receipt=r;k._scan_timeframes=('15m',)
    k.feed.cached_ohlcv=Mock(side_effect=lambda s,tf,**kw:_df(np.array([1.,float(int(s[1:3])+1)])))
    peers=k._peer_frames(r['roles']['peers']);symbol='S11/USDT';mine=peers[symbol]
    def rank(pop):
        ctx=FeatureCtx(frames=mine,tf='15m',universe=pop,symbol=symbol)
        return dsl.evaluate(dsl.parse('xs_rank(close)'),ctx).iloc[-1]
    full=rank(peers);wrong=rank({s:peers[s] for s in r['admitted_symbols']})
    assert full==pytest.approx(11/29) and full!=wrong


def test_cached_salience_uses_broad_cohort_beyond_twelve(tmp_path,cfg,policy,monkeypatch):
    from tests.test_attention_telemetry import frames
    monkeypatch.setattr('trader.data.broad_crypto.time.time',lambda:CUT/1000)
    k=broad_kernel(tmp_path,cfg);data=frames(30,CUT)
    # Fixture symbols use S0 while market symbols use S00.
    k.feed.cached_ohlcv=Mock(side_effect=lambda s,tf,**kw:data['S'+str(int(s[1:3]))+'/USDT'][tf])
    o,_,_=observe(k,[]);r=A.admit(o,policy,A.initial_state())
    assert o['market']['cohort_size']==30
    assert len(o['salience_rows'])==30 and r['capacity_used']<=12
    assert all(row['eligible'] for row in o['salience_rows'].values())


def test_learning_sees_rejected_warmup_and_exploration(tmp_path,policy):
    from trader.learning.admission import population
    j=Journal(tmp_path/'j.db');r=A.persist(j,observation(scores=False),policy)
    pop=population(j,r['receipt_id'])
    assert len(pop['population'])==30
    assert sum(x['deep_admitted'] for x in pop['population'])==10
    assert sum(x['exploration_admitted'] for x in pop['population'])==2
    assert sum(x['rejected_deferred'] for x in pop['population'])==20
    assert all(x['missing_warmup']=='warmup' for x in pop['population'])
    with pytest.raises(ValueError):population(j,'unknown')


def test_deleted_rotation_is_corruption_not_restart_reset(tmp_path,policy):
    j=Journal(tmp_path/'j.db');A.persist(j,observation(),policy)
    with j._tx() as db:db.execute('DELETE FROM state_kv WHERE key=?',(A.STATE_KEY,))
    with pytest.raises(ValueError,match='missing'):A.persist(Journal(j.db_path),observation(),policy)


def test_valid_but_mismatched_rotation_refuses(tmp_path,policy):
    j=Journal(tmp_path/'j.db');A.persist(j,observation(),policy)
    j.kv_set(A.STATE_KEY,A.canonical(A.initial_state()))
    with pytest.raises(ValueError,match='mismatch'):A.persist(j,observation(),policy)


def test_missing_context_anchor_does_not_force_a_slot(policy):
    o=observation(0);r=A.admit(o,policy,A.initial_state())
    assert not r['admitted_symbols'] and r['roles']['anchors']==('BTC/USDT','ETH/USDT')


def test_pending_and_venue_only_exposure_are_outside_admission(tmp_path,cfg,policy):
    k=safety_kernel(tmp_path,cfg)
    k.journal.kv_set('execution_recovery',json.dumps(dict(symbol='PENDING/USDT')))
    k.journal.kv_set('venue_position_snapshot',json.dumps(dict(positions=[{'instrument_id':'binance_usdm:futures:VENUEUSDT'}])))
    assert {'PENDING/USDT','VENUEUSDT'}.issubset(k._exposure_symbols())
    r=receipt(policy)
    k._service_exposure(set(r['admitted_symbols']),{})
    k._detect_exchange_exits.assert_any_call('PENDING/USDT')
    k._detect_exchange_exits.assert_any_call('VENUEUSDT')


def test_strategy_suffix_cannot_defeat_include_exclude():
    from trader.strategy.scan_plan import plan_scan
    sp=NS(id='spec',timeframe='4h',universe={'include':['BTC/USDT:USDT','ETH/USDT'],'exclude':['ETH/USDT:USDT']})
    p=plan_scan([sp],['BTC/USDT','ETH/USDT'],{'BTC/USDT':1,'ETH/USDT':1})
    assert p.symbols==('BTC/USDT',) and p.wants('spec','BTC/USDT')


def test_failed_exploration_learning_keeps_admission_reason(tmp_path,policy):
    from trader.learning.capture_runtime import missed_snapshot
    j=Journal(tmp_path/'failure.db');r=A.persist(j,observation(),policy)
    symbol=r['roles']['exploration'][0]
    context=dict(receipt_id=r['receipt_id'],category='exploration',acquisition='FAILED_SNAPSHOT')
    with j._tx() as db:
        from trader.learning.capture import ensure
        ensure(db)
        missed_snapshot(db,None,symbol,CUT,admission_context=context)
        blobs=[json.loads(row[0]) for row in db.execute('SELECT payload FROM learning_source_blobs')]
    assert any(b.get('admission')==context for b in blobs)
    assert A.latest(j)['next_state']['exploration_cursor']==r['next_state']['exploration_cursor']


def test_owner_view_never_uses_old_receipt_as_latest_refused_admission(tmp_path,policy):
    j=Journal(tmp_path/'view.db');A.persist(j,observation(),policy)
    j.log_brain_event('attention_admission_refused','attention',{'reason':'test_missing_input'})
    v=A.owner_view(j,now_ms=CUT,stale_seconds=1800)
    assert v['status']=='refused' and v['deep_admitted']==0
