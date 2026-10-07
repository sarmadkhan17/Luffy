"""Persisted TEST-ONLY authority, delivered by the unchanged normal entrypoint.

No candidate/allocator input, intent or Risk answer is injected downstream.
Missing production calibrations remain missing outside these tests.
"""
from dataclasses import replace
from datetime import datetime, timezone
import json
import pandas as pd
import pytest
from trader.portfolio import allocator as A, opportunity_live as L, economics as E, candidate_bridge as B
from trader.portfolio import current, runtime
from trader.strategy import factory_handoff as F
from trader.strategy.signal_occurrence import spec_fingerprint
from trader.strategy.spec import StrategySpec
from trader.core.types import Snapshot
from trader.engine.evidence_capture import record_snapshot, margin_observation
from tests.test_universal_strategy_authority import approved_world, established_capacity
from tests.authority_factory_fixtures import cfg, INPUTS
from tests.authority_capacity_fixtures import NOW, H4
from tests.test_opportunity_live_integration import snapshot, IID, SYMBOL
from tests.economics_fixtures import install_models, frozen, binding as fixture_binding
from scripts import opportunity_context_shadow as shadow
from scripts.opportunity_context_shadow import DIMENSIONS


def compiled_params(spec):
    """The exact compiler identity a StrategyVersion's live signal carries (STR-02)."""
    from trader.engine.trade_provenance import spec_version
    from trader.strategy import compile as C
    sha = spec_version(StrategySpec.from_dict(spec))['spec_sha256']
    return dict(spec_sha256=sha, compiler_version=C.COMPILER_VERSION, feature_version=C.FEATURE_VERSION,
                compile_identity=C.compile_identity(sha, C.COMPILER_VERSION, C.FEATURE_VERSION,
                                                    C.feature_contract_sha256()))


@pytest.fixture
def publications(tmp_path, cfg, monkeypatch):
    j, v, _, _ = approved_world(tmp_path, cfg, monkeypatch)
    cut = NOW
    book = snapshot(None, cut)
    record_snapshot(j, book, at_ms=cut)
    # Account observation and existing capacity inputs remain unchanged.
    # Actual margin fields are separately recorded for runtime measurements.
    margin = margin_observation(b'{"totalMarginBalance":"10000","availableBalance":"10000","totalInitialMargin":"0"}',
        request_url='https://demo-fapi.binance.com/fapi/v3/account',request_start_ms=cut-100,received_ms=cut-50)
    # Record before re-evaluating capacity, since the gate binds exact account KV.
    j.kv_set('account_margin_observation', A.canonical(margin))
    cap = established_capacity(j, cfg, v, monkeypatch)
    from trader.strategy import capacity as C
    capacity = C.load(j, cap)
    row = dict(j.query('SELECT * FROM strategy_versions WHERE version_id=?',(v['version_id'],))[0])
    close = cut - cut % H4
    sig = dict(symbol=SYMBOL, action='BUY', params=dict(spec_id=v['strategy_id'],
        spec_fingerprint=spec_fingerprint(StrategySpec.from_dict(v['spec'])),
        signal_timeframe='4h',signal_bar_close_ms=close,**compiled_params(v['spec'])))
    # Decision-grade (DEC-01): Attention scan at the cut and the decision cycle's analyst bundle.
    from contextlib import ExitStack
    import time
    from tests.test_attention_telemetry import frames, event
    from trader.cognition import decision_analysts as DA
    from trader.observability.attention import settings
    from trader.observability.scan_source import latest
    from trader.observability.store import Store
    ident = dict(schema='attention-scan-identity.v1', instance_id='2'*32, seq=cut)
    store = Store(tmp_path/'fixture-attention.db', settings())
    store.write(dict(event('fixture-scan', cut, {SYMBOL: next(iter(frames(1, cut).values()))}), identity=ident))
    store.write(dict(kind='causes', scan_id='fixture-scan', as_of_ms=cut, identity=ident,
                     items=[dict(symbol=SYMBOL, decision_id='normal-setup')]))
    store.close()
    with ExitStack() as stack:
        scan = latest(shadow._read(stack, tmp_path/'fixture-attention.db', time.monotonic()+5),
                      time.monotonic()+5, max_bytes=settings()['max_bytes']//2)
    analysts = DA.bundle(decision_id='normal-setup', cycle_id='normal-cycle', symbol=SYMBOL,
                         cut_ts=datetime.fromtimestamp(cut/1000, timezone.utc).isoformat(),
                         roster=DA.roster({}), packets=[])
    args = dict(as_of_ms=cut,symbol=SYMBOL,instrument_id=IID,cycle_id='normal-cycle',
        candidate_id='normal-setup:'+v['version_id'],
        sources=(L.source('attention_scan',scan,scan['persisted_at_ms'],cut+1000),
                 L.source('strategy_version',row,row['recorded_at_ms'],cut+1000),
                 L.source('signals',[sig],cut,cut+1000),L.source('portfolio',book,cut,cut+1000),
                 L.source('analysts',analysts,cut,cut+1000)),
        required_roles=('strategy_version','signals','portfolio'),decision_grade=True)
    receipt = L.produce(**args)
    authority = B.freeze_authority(j,receipt,cfg,{'ohlcv'})
    raw = dict(schema=B.ALLOCATION_SCHEMA,method='TEST-ONLY-normal-ready',version='1',test_only=True,
        binding=B.allocation_binding(receipt,authority),captured_ms=cut,valid_until_ms=cut+1000,
        provenance={'TEST-ONLY':True},bounds=[dict(authority=a,maximum='.01',unit='base_quantity')
        for a in ('strategy_requested','risk_permitted','account_funding','capacity','portfolio_constraints')],
        established_dimensions=['probation','account_instrument','risk_compatibility','evidence_quality'])
    ready = A.Source.freeze('TEST-ONLY-normal-allocation',raw)
    monkeypatch.setitem(B.ALLOCATION_AUTHORITY_ADAPTERS,('TEST-ONLY-normal-ready','1'),
                        lambda raw,*a:raw.get('test_only') is True)
    install_models(monkeypatch)
    basis = fixture_binding()
    dimensions = {k: getattr(basis,k) for k in DIMENSIONS}
    ei = frozen('2',L.economic_binding(receipt,**dimensions))
    def retime(s):
        raw=json.loads(s.payload_json);raw.update(captured_ms=cut,valid_until_ms=cut+1000)
        return A.Source.freeze(s.source_id,raw)
    gross,cost=retime(ei.gross),retime(ei.costs)
    reserve=json.loads(retime(ei.uncertainty).payload_json)
    reserve.update(gross_source_sha256=gross.sha256,cost_source_sha256=cost.sha256)
    ei=replace(ei,gross=gross,costs=cost,uncertainty=A.Source.freeze(ei.uncertainty.source_id,reserve),
               context=(*ei.context,receipt.as_source(),authority,ready))
    er=E.build(ei)
    L.persist(receipt,tmp_path/'contexts');E.persist(er,tmp_path/'economics')
    monkeypatch.setattr(shadow.time,'time_ns',lambda:(cut+100)*1000000)
    monkeypatch.setattr(shadow.time,'time',lambda:(cut+100)/1000)
    bars=pd.DataFrame([dict(ts=close-(40-i)*H4,open=60000.,high=60600.,low=59400.,close=60000.,volume=10.) for i in range(40)])
    market=Snapshot(price=60000.,symbol=SYMBOL,ts=datetime.fromtimestamp(cut/1000,timezone.utc).isoformat(),dfs={'4h':bars})
    return j,cfg,v,receipt,er,market,capacity


def read(p):
    j,cfg,_,_,_,market,_=p
    from tests.admission_cycle_fixture import bind_market
    receipt=bind_market(market,cfg,int(__import__('datetime').datetime.fromisoformat(market.ts).timestamp()*1000))
    return current.freeze(j.db_path,j.db_path.parent/'attention.db',j.db_path.parent/'investigation.db',cfg,
                          market_snapshots={'normal-setup':market},admission_receipt=receipt)


def test_complete_normal_path_to_risk_and_restart(publications,tmp_path):
    inputs,detail=read(publications)
    assert detail['normal_persisted_contexts']==1
    c=inputs.candidates[0]
    assert c.capacity.status==A.Status.ESTABLISHED
    assert c.economics.evidence.status==A.Status.ESTABLISHED
    decision=[json.loads(x.payload_json) for x in inputs.sources if x.source_id.startswith('decision-context:')]
    assert len(decision)==1 and decision[0]['status']=='BOUND' and decision[0]['authority']=='NONE'
    assert decision[0]['economics_receipt_id']==publications[4].receipt_id
    assert c.valid_until_ms==NOW+1000
    assert any(s.source_id.startswith('strategy-lineage:') for s in inputs.sources)
    before=publications[0].query('SELECT * FROM state_kv')
    ledger=tmp_path/'ledger.db'
    result=runtime.Consumer(ledger).consume(inputs,processed_at=NOW+100)
    assert result['proposal']['result']['selected']
    assert len(result['trade_intents'])==len(result['risk_decisions'])==1
    intent=json.loads(result['trade_intents'][0]['payload_json'])
    decision=json.loads(result['risk_decisions'][0]['payload_json'])
    assert intent['status']=='COMPLETE'
    assert decision['result'] in ('APPROVE','REFUSE')
    assert decision['risk_sizing'] is not None or decision.get('risk_reason')
    assert not decision['execution_routed'] and result['real_order_submissions']==0
    assert runtime.replay(result)==result
    assert runtime.Consumer(ledger).consume(inputs,processed_at=NOW+100)['duplicate']
    assert publications[0].query('SELECT * FROM state_kv')==before


def test_missing_economics_normal_reader(publications,tmp_path):
    for p in (tmp_path/'economics').glob('*.json'):p.unlink()
    inputs,detail=read(publications)
    # Real (empty-registry) economics: bound to this context, explicitly blocked, never defaulted.
    assert inputs.candidates==()
    assert any(m.startswith('CONTEXT_BLOCKED:normal-setup:') and 'REQUIRED_COST_EVIDENCE_UNAVAILABLE' in m
               for m in detail['missing_sources'])
    blocked,=detail['blocked_decisions']
    assert blocked['status']=='BLOCKED' and blocked['economics_status']=='UNAVAILABLE'
    assert blocked['cost_binding'] is None and blocked['authority']=='NONE'
    assert all(c['value'] is None and c['status']=='UNAVAILABLE' for c in blocked['cost_components'].values())
    assert not any(x.source_id.startswith('decision-context:') for x in inputs.sources)
    result=runtime.Consumer(tmp_path/'ledger.db').consume(inputs,processed_at=NOW+100)
    assert result['proposal']['result']['decision']=='NO_ALLOCATION'
    assert result['real_order_submissions']==0


def test_unknown_expiry_incomplete_not_checkpoint_crash(publications,tmp_path,monkeypatch):
    j,cfg,v,receipt,er,market,cap=publications
    args=json.loads(receipt.payload_json)
    sources=[]
    for raw in args['sources']:
        s=A.Source(**raw);body=json.loads(s.payload_json)
        if s.source_id in ('signals','strategy_version'):body['valid_until_ms']=None
        sources.append(A.Source.freeze(s.source_id,body))
    request={k:args[k] for k in ('as_of_ms','symbol','instrument_id','cycle_id','candidate_id','required_roles')}
    request.update(sources=sources,decision_grade=True)
    for p in (tmp_path/'contexts').glob('*.json'):p.unlink()
    unknown=L.produce(**request)
    L.persist(unknown,tmp_path/'contexts')
    for p in (tmp_path/'economics').glob('*.json'):p.unlink()
    inputs,detail=read(publications)
    assert inputs.candidates==() and detail['blocked_decisions']
    assert not any(s.source_id.startswith('strategy-lineage:') for s in inputs.sources)
    result=runtime.Consumer(tmp_path/'incomplete.db').consume(inputs,processed_at=NOW+100)
    assert result['proposal']['result']['decision']=='NO_ALLOCATION'
    assert result['real_order_submissions']==0
    assert all(json.loads(unknown.payload_json)['source_status'][r]['freshness']=='UNKNOWN' for r in ('signals','strategy_version'))


def test_mixed_economic_cut_refused(publications,tmp_path):
    _,_,_,receipt,er,_,_=publications
    ei=E.from_inputs(json.loads(er.inputs_json))
    bad=E.build(replace(ei,binding=replace(ei.binding,as_of_ms=NOW+1)))
    for p in (tmp_path/'economics').glob('*.json'):p.unlink()
    E.persist(bad,tmp_path/'economics')
    with pytest.raises(ValueError,match='EXACT_CUT_DIFFERS'):read(publications)


def test_journal_decision_without_expiry_normal_checkpoint(publications,tmp_path):
    """The original capture -> bridge -> freeze failure, using actual stores."""
    j,cfg,v,receipt,_,market,_=publications
    for role in ('contexts','economics'):
        for p in (tmp_path/role).glob('*.json'):p.unlink()
    record_snapshot(j,snapshot('long',NOW),at_ms=NOW)
    from tests.test_attention_telemetry import frames, event
    from trader.observability.attention import settings
    from trader.observability.store import Store
    from trader.core.types import Decision, Action
    data={SYMBOL:next(iter(frames(1,NOW).values()))}
    store=Store(tmp_path/'attention.db',settings())
    ident=dict(schema='attention-scan-identity.v1',instance_id='1'*32,seq=NOW)
    store.write(dict(event('journal-scan',NOW,data),identity=ident))
    store.write(dict(kind='causes',scan_id='journal-scan',as_of_ms=NOW,identity=ident,
                     items=[dict(symbol=SYMBOL,decision_id='normal-decision')]))
    store.close()
    sig=next(json.loads(s['payload_json'])['data'][0] for s in json.loads(receipt.payload_json)['sources'] if s['source_id']=='signals')
    j.log_cycle(market,'journal-cycle','paper')
    j.log_decision(Decision('normal-decision','journal-cycle',SYMBOL,Action.BUY,1.,.5,.8,[],
                           strategy_signals=[sig],ts=market.ts))
    from tests.admission_cycle_fixture import bind_market
    admission_receipt=bind_market(market,cfg,NOW)
    result,detail=current.checkpoint(j.db_path,cfg,ledger=tmp_path/'checkpoint.db',
                                    market_snapshots={'normal-decision':market},admission_receipt=admission_receipt)
    assert detail['normal_persisted_contexts']==0 and detail['candidate_count']==0
    blocked,=detail['blocked_decisions']
    assert blocked['status']=='BLOCKED' and blocked['economics_status']=='UNAVAILABLE'
    assert blocked['candidate_id'].startswith('normal-decision:')
    assert result['real_order_submissions']==0
    assert result['proposal']['result']['decision']=='NO_ALLOCATION'


def test_lineage_refuses_mixed_cut_and_source_identity(publications):
    j,cfg,_,receipt,er,_,_=publications
    inputs,_=read(publications)
    c=inputs.candidates[0]
    with pytest.raises(ValueError,match='CANDIDATE_CUT_DIFFERS'):
        B.freeze_lineage(j,c.version_id,as_of_ms=c.as_of_ms+1,valid_until_ms=c.valid_until_ms,
                        candidate_input=c,receipt=receipt,economic_receipt=er)
    lineage=next(s for s in inputs.sources if s.source_id.startswith('strategy-lineage:'))
    body=json.loads(lineage.payload_json)
    body['candidate_cut']['context_id']='wrong'
    forged=A.Source.freeze('strategy-lineage:'+A.digest(body),body)
    with pytest.raises(ValueError,match='CANDIDATE_CUT_DIFFERS'):B.replay_lineage(forged,NOW)


def test_admission_omission_preserves_whole_book_holdings(publications):
    j,cfg,_,_,_,market,_=publications
    for path in (j.db_path.parent/'contexts').glob('*.json'):
        path.unlink()  # remove the obsolete TEST-ONLY zero-holding publication
    book=snapshot('long',NOW)
    record_snapshot(j,book,at_ms=NOW)
    inputs,detail=current.freeze(j.db_path,j.db_path.parent/'attention.db',
        j.db_path.parent/'investigation.db',cfg,market_snapshots={},
        holdings_market_snapshots={market.symbol:market},admission_receipt=None)
    assert inputs.candidates==()
    assert len(inputs.portfolio.positions)==1
    assert inputs.portfolio.positions[0].instrument==IID
    assert detail['holdings_count']==1
    assert market.symbol in detail['holdings_market_observations']
    assert any(s.source_id=='held-market-observations' for s in inputs.sources)
