"""TEST-ONLY sources; no synthetic pass claims real allocation readiness."""
from dataclasses import asdict, replace
import json
import sqlite3
import pytest

from trader.core.config import load_config
from trader.portfolio import allocator as A, common_factor as C, runtime_metrics as M, runtime as R
from trader.engine import risk_intent as I
from trader.engine.risk import RiskManager
from trader.portfolio.trade_intent import build
from tests.test_portfolio_allocator import candidate, inputs, test_only_models
from tests.test_portfolio_common_factor import setup
from tests.test_candidate_bridge import exact
from tests.test_stage6_closure import ready_candidate


@pytest.fixture
def book():
    def make(*cs, held=False, account=True, reconcile=True, geometry=True, control='ACTIVE'):
        cfg=load_config()
        i,v,_=setup(*cs,positions=held)
        snap=json.loads(v.payload_json)
        for p in snap['positions']:p['mark_price_text']='12'
        snap['snapshot_id']=A.digest({k:x for k,x in snap.items() if k!='snapshot_id'})
        v=A.Source.freeze('venue_position_snapshot',snap)
        p=replace(i.portfolio,snapshot_id=snap['snapshot_id'])
        policy=A.Source.freeze('owner-risk-policy',{'risk':cfg['risk']})
        from trader.engine.evidence_capture import margin_observation
        a=margin_observation(b'{"totalMarginBalance":"1000","availableBalance":"975","totalInitialMargin":"25"}',
            request_url='https://demo-fapi.binance.com/fapi/v3/account',request_start_ms=980,received_ms=1000) if account else None
        rows=[dict(id='held',symbol='BTC/USDT',side='long',amount=1,entry_price=10,notional_usdt=10,
                   leverage=cfg['risk']['leverage'],stop_loss=9,market_type='futures')] if held and reconcile else []
        contexts=[dict(opportunity_id=c.opportunity_id,version_id=c.version_id,spec_hash=c.spec_hash,
            instrument=c.instrument,direction=c.direction,portfolio_snapshot_id=p.snapshot_id,as_of_ms=i.as_of_ms,
            source_ids=[v.source_id],price=100,atr=1,side_risk_frac=.02) for c in cs] if geometry else []
        metric=M.capture(snapshot=snap,account_margin=a,journal_positions=rows,risk_kv={
            'risk_state':A.canonical(dict(peak_equity=1000,day_start_equity=1000,day_key='1970-01-01')),
            'risk_baseline_marker':'TEST-ONLY'},config=cfg,as_of_ms=i.as_of_ms,closed_count=100,
            entry_contexts=contexts)
        i=replace(i,portfolio=p,control_state=control,sources=tuple(s for s in i.sources if s.source_id!=v.source_id)+(v,policy))
        return C.attach(i,v,policy,measurement_sources=(metric,))[0]
    return make


def result(i):
    return C.from_allocator(i)


def test_established_heat_margin_and_exposure_reach_common_factor(book):
    i=book(held=True)
    r=result(i)
    m=r['portfolio']['metrics']
    assert m['gross_exposure']['value']=='12.0'
    assert m['per_instrument_exposure']['value']=={'binance_usdm:futures:BTCUSDT':'12.0'}
    assert m['risk_heat_pct']['value']==pytest.approx(.1)
    assert m['actual_margin_utilization_pct']['value']=='2.500'
    assert m['total_margin_pct']['status']=='ESTABLISHED'
    for v in m.values():
        assert v['portfolio_snapshot_id']==i.portfolio.snapshot_id
        assert v['source_hashes'] and v['as_of_ms']==1500
    assert all(v['value'] is not None for v in r['concentration']['checks'])


def test_missing_metric_independent_and_unknown_not_zero(book):
    r=result(book(held=True,account=False))
    assert r['portfolio']['metrics']['gross_exposure']['value']=='12.0'
    for name in ('risk_heat_pct','total_margin_pct','actual_margin_utilization_pct'):
        assert r['portfolio']['metrics'][name]['status']=='UNAVAILABLE'
        assert r['portfolio']['metrics'][name]['value'] is None
    assert r['concentration']['status']=='UNAVAILABLE'


def test_unreconciled_journal_cannot_supply_heat(book):
    m=result(book(held=True,reconcile=False))['portfolio']['metrics']
    assert m['risk_heat_pct']['value'] is None
    assert m['actual_margin_utilization_pct']['value']=='2.500'
    assert m['gross_exposure']['value']=='12.0'


def test_reconciled_book_preserves_risk_recorded_entry_basis(book):
    i=book(held=True)
    source=next(s for s in i.sources if s.source_id==M.SOURCE_ID)
    raw=json.loads(source.payload_json)
    venue=json.loads(next(s for s in i.sources if s.source_id=='venue_position_snapshot').payload_json)
    venue['positions'][0]['entry_price_text']='10.000000000000001'
    held=M.positions(raw['journal_positions'],venue)
    assert held[0].entry_price==10
    assert RiskManager(raw['config'],None).portfolio_metrics(held,1000)['risk_heat_pct']==pytest.approx(.1)
    venue['positions'][0]['quantity']=2
    with pytest.raises(ValueError,match='RISK_BOOK_NOT_RECONCILED'):
        M.positions(raw['journal_positions'],venue)


def test_only_owner_policy_limits_and_no_correlation_adjustment(book):
    i=book(held=True);r=result(i)
    cfg=load_config()['risk']
    expected={cfg[k] for k in ('max_open_positions','portfolio_heat_cap_pct','per_symbol_risk_cap_pct','max_position_margin_pct','max_total_margin_pct')}
    assert {v['limit'] for v in r['concentration']['checks']}==expected
    assert r['numeric_adjustments']=='NONE' and r['factor_model']=='NOT_JUSTIFIED'
    assert not any('gross' in v['metric'] or 'correlation' in v['metric'] for v in r['concentration']['checks'])


def test_metric_source_another_cut_refused(book):
    i=book()
    v=next(s for s in i.sources if s.source_id==M.SOURCE_ID)
    raw=json.loads(v.payload_json);raw['portfolio_snapshot_id']='other-cut'
    i=replace(i,sources=tuple(A.Source.freeze(v.source_id,raw) if s.source_id==v.source_id else s for s in i.sources))
    with pytest.raises(ValueError):C.from_allocator(i)


def test_existing_risk_formulas_are_shared(book):
    i=book(held=True)
    raw=json.loads(next(s for s in i.sources if s.source_id==M.SOURCE_ID).payload_json)
    venue=json.loads(next(s for s in i.sources if s.source_id=='venue_position_snapshot').payload_json)
    manager=RiskManager(load_config(),None)
    expected=manager.portfolio_metrics(M.positions(raw['journal_positions'],venue),1000)
    for name in ('risk_heat_pct','total_margin_pct','position_margin_pct'):
        assert result(i)['portfolio']['metrics'][name]['value']==expected[name]


def test_exact_event_once_restart_checkpoint_and_replay(tmp_path,book):
    path=tmp_path/'runtime.db'
    before=book(); after=book(candidate(instrument='binance_usdm:futures:ETHUSDT'))
    consumer=R.Consumer(path)
    assert not consumer.consume(before,processed_at=1500)['triggered']
    r=consumer.consume(after,processed_at=1500)
    assert r['triggered'] and R.replay(r)==r
    again=R.Consumer(path).consume(after,processed_at=1600)
    assert again['duplicate'] and again['proposal']==r['proposal']
    with sqlite3.connect(path) as db:
        rows=[json.loads(v[0]) for v in db.execute('SELECT payload FROM checkpoints')]
    events=[v for v in rows if v['triggered']]
    assert len(events)==1
    checkpoint=events[0]
    assert checkpoint['event_id'] and checkpoint['portfolio_cut_id']==r['portfolio_cut_id']
    assert checkpoint['candidate_set_sha256'] and checkpoint['processed_at']==1500
    assert checkpoint['resulting_proposal_id']==r['proposal']['proposal_id']
    assert 'previous_proposal_id' in checkpoint


def test_exact_position_open_closed_and_risk_transition(tmp_path,book):
    consumer=R.Consumer(tmp_path/'events.db')
    consumer.consume(book(),processed_at=1500)
    assert consumer.consume(book(held=True),processed_at=1500)['triggered']
    out=consumer.consume(book(control='FROZEN'),processed_at=1500)
    kinds={e['kind'] for e in out['event_receipt']['result']['events'] if e['trigger']}
    assert kinds=={'POSITION_EXIT','RISK_STATE_CHANGE'}


def test_materiality_unavailable_does_not_trigger(tmp_path,book):
    i=book();consumer=R.Consumer(tmp_path/'gate.db');consumer.consume(i,processed_at=1500)
    changed=replace(i,relationships=A.Evidence(A.Status.UNKNOWN,(),'{"context":"changed"}'))
    r=consumer.consume(changed,processed_at=1500)
    assert not r['triggered'] and r['proposal'] is None
    event=next(e for e in r['event_receipt']['result']['events'] if e['kind']=='RELATIONSHIP_CHANGE')
    assert event['reason_codes']==['MATERIALITY_POLICY_UNAVAILABLE']


def test_atomic_checkpoint_rollback_on_risk_failure(tmp_path,book,monkeypatch):
    consumer=R.Consumer(tmp_path/'atomic.db');consumer.consume(book(),processed_at=1500)
    current=book(candidate(instrument='binance_usdm:futures:ETHUSDT'))
    monkeypatch.setattr(R,'risk_evaluate',lambda *a: (_ for _ in ()).throw(ValueError('TEST-ONLY-failure')))
    with pytest.raises(ValueError):consumer.consume(current,processed_at=1500)
    with sqlite3.connect(consumer.path) as db:
        assert db.execute('SELECT COUNT(*) FROM evaluations').fetchone()[0]==1
        assert not any(json.loads(v[0])['triggered'] for v in db.execute('SELECT payload FROM checkpoints'))


def test_intent_reaches_risk_preserves_rejection_and_approval_stops(exact,monkeypatch):
    from trader.engine.executor import Executor
    monkeypatch.setattr(Executor,'open',lambda *a,**k:pytest.fail('Execution reached'))
    i,receipt,authority,readiness=ready_candidate(exact,monkeypatch)
    cfg=exact[1];c=i.candidates[0];cut=i.as_of_ms
    from tests.test_opportunity_live_integration import snapshot
    from trader.engine.evidence_capture import margin_observation
    from datetime import datetime,timezone
    snap=snapshot(None,cut)
    v=A.Source.freeze('venue_position_snapshot',snap)
    policy=A.Source.freeze('owner-risk-policy',{'risk':cfg['risk']})
    account=margin_observation(b'{"totalMarginBalance":"1000","availableBalance":"1000","totalInitialMargin":"0"}',
        request_url='https://demo-fapi.binance.com/fapi/v3/account',request_start_ms=cut-10,received_ms=cut)
    metric=M.capture(snapshot=snap,account_margin=account,journal_positions=[],risk_kv={
        'risk_state':A.canonical(dict(peak_equity=1000,day_start_equity=1000,
            day_key=datetime.fromtimestamp(cut/1000,timezone.utc).strftime('%Y-%m-%d'))),
        'risk_baseline_marker':'TEST-ONLY'},config=cfg,as_of_ms=cut,closed_count=100,
        entry_contexts=[dict(opportunity_id=c.opportunity_id,version_id=c.version_id,spec_hash=c.spec_hash,
            instrument=c.instrument,direction=c.direction,portfolio_snapshot_id=i.portfolio.snapshot_id,
            as_of_ms=cut,source_ids=[v.source_id],price=100,atr=1,side_risk_frac=.02)])
    i=replace(i,sources=(*i.sources,v,policy,metric))
    proposal=A.allocate(i);intent=build(proposal,i)[0]
    assert intent.requested_action.value=='OPEN' and intent.status.value=='COMPLETE'
    calls=[];original=RiskManager.check_entry
    def check(self,*a,**k):
        calls.append(self.policy()['digest']);return original(self,*a,**k)
    monkeypatch.setattr(RiskManager,'check_entry',check)
    answer=I.evaluate(intent,proposal,i)
    r=answer.payload()
    assert r['result']=='APPROVE' and r['execution_authority'] is False and r['execution_routed'] is False
    assert r['risk_policy_id']==calls[0]
    assert r['trade_intent_id']==intent.intent_id and r['permitted_size'] is not None
    assert I.replay(answer)==answer
    from trader.engine.risk import SizingResult
    monkeypatch.setattr(RiskManager,'check_entry',lambda *a,**k:SizingResult(False,'max positions reached',0,0,0,0))
    assert I.evaluate(intent,proposal,i).payload()['reason_codes']==['max positions reached']


def test_missing_geometry_or_size_stays_not_orderable(book):
    c=candidate(instrument='binance_usdm:futures:ETHUSDT')
    i=book(c,geometry=False);p=A.allocate(i);intent=build(p,i)[0]
    r=I.evaluate(intent,p,i).payload()
    assert r['result']=='INCOMPLETE' and r['permitted_size'] is None
    c=replace(c,bounds=())
    i=book(c);p=A.allocate(i);intent=build(p,i)[0]
    assert I.evaluate(intent,p,i).payload()['result'] in ('INCOMPLETE','REFUSE')
    assert I.evaluate(intent,p,i).payload()['permitted_size'] is None


def test_intent_from_another_cut_refused(book):
    i=book(candidate(instrument='binance_usdm:futures:ETHUSDT'));p=A.allocate(i);intent=build(p,i)[0]
    with pytest.raises(ValueError):I.evaluate(intent,p,replace(i,portfolio=replace(i.portfolio,snapshot_id='different')))


def test_normal_kernel_checkpoint_has_no_execution_capability(tmp_path,book,monkeypatch):
    from trader.kernel import Kernel
    from trader.portfolio import current
    from trader.engine.executor import Executor
    calls=[]
    def checkpoint(*args,**kw):
        calls.append(args)
        out=R.Consumer(tmp_path/'normal.db').consume(book(),processed_at=1500)
        return out,{}
    monkeypatch.setattr(current,'checkpoint',checkpoint)
    monkeypatch.setattr(Executor,'open',lambda *a,**kw:pytest.fail('order'))
    kernel=Kernel.__new__(Kernel)
    kernel.journal=type('JournalPath',(),{'db_path':tmp_path/'source.db'})()
    kernel.cfg=load_config()
    from trader.core.types import MarketType
    kernel.market_type=MarketType.SPOT
    assert kernel._portfolio_checkpoint()['status']=='PASS' and len(calls)==1
    import inspect
    assert 'self._portfolio_checkpoint()' in inspect.getsource(Kernel.cycle)


def test_heat_uses_current_equity_without_margin_field(book):
    i=book(held=True,account=False)
    source=next(s for s in i.sources if s.source_id==M.SOURCE_ID)
    raw=json.loads(source.payload_json)
    from datetime import datetime,timezone
    raw['account_observation']=dict(currency='USDT',status='FRESH',authoritative=True,
        completion_relation='ok',value=1000,risk_input=1000,
        observed_at=datetime.fromtimestamp(1,timezone.utc).isoformat())
    raw['source_hashes']['account_observation']=A.digest(raw['account_observation'])
    metric=A.Source.freeze(source.source_id,raw)
    venue=next(s for s in i.sources if s.source_id=='venue_position_snapshot')
    policy=next(s for s in i.sources if s.source_id=='owner-risk-policy')
    i=replace(i,sources=tuple(s for s in i.sources if s.source_id not in (metric.source_id,i.exposure_source_id)),
              exposure_source_id=None)
    i,_=C.attach(i,venue,policy,measurement_sources=(metric,))
    m=result(i)['portfolio']['metrics']
    assert m['risk_heat_pct']['status']=='ESTABLISHED'
    assert m['actual_margin_utilization_pct']['value'] is None
    assert m['account_equity']['evidence_id']==A.digest(raw['account_observation'])


def test_exact_lifecycle_transition_is_consumed_once(tmp_path,book):
    before=book();after=book()
    def inventory(i,state):
        src=A.Source.freeze('current-shadow-inventory',dict(strategy_authority_inventory={
            'TEST-ONLY-version':{'events':[{'to_state':state,'event_id':state}],'governor':[]}}))
        return replace(i,sources=(*i.sources,src))
    consumer=R.Consumer(tmp_path/'lifecycle.db')
    consumer.consume(inventory(before,'SHADOW'),processed_at=1500)
    after=inventory(after,'RETIRED')
    r=consumer.consume(after,processed_at=1500)
    assert r['triggered']
    assert any(e['kind']=='STRATEGY_LIFECYCLE_CHANGE' and e['trigger'] for e in r['event_receipt']['result']['events'])
    assert R.Consumer(consumer.path).consume(after,processed_at=1500)['duplicate']


def test_restart_in_new_process_replays_exact_cut(tmp_path,book):
    import subprocess,sys
    i=book();path=tmp_path/'restart.db'
    first=R.Consumer(path).consume(i,processed_at=1500)
    frozen=tmp_path/'cut.json';frozen.write_text(A.canonical(A._ordered(i)))
    code=('import json,sys; from trader.portfolio.allocator import inputs_from_payload,canonical; '
          'from trader.portfolio.runtime import Consumer; '
          'print(canonical(Consumer(sys.argv[1]).consume(inputs_from_payload(json.load(open(sys.argv[2]))),processed_at=1500)))')
    restarted=json.loads(subprocess.check_output([sys.executable,'-c',code,str(path),str(frozen)],text=True))
    assert restarted['duplicate'] and restarted['event_receipt']==first['event_receipt']


def test_unknown_mark_preserves_each_instrument_independently(book):
    i=book(held=True)
    m=result(i)['portfolio']['metrics']['per_instrument_exposure']
    assert m['components']['binance_usdm:futures:BTCUSDT']['status']=='ESTABLISHED'
    assert m['components']['binance_usdm:futures:BTCUSDT']['value']=='12.0'


def test_frozen_checkpoint_refreshes_verified_book_without_market_snapshots(tmp_path, monkeypatch):
    from trader.kernel import Kernel
    from trader.core.journal import Journal
    from trader.core.types import MarketType
    from trader.portfolio import current
    from trader.engine.evidence_capture import verify_snapshot
    from trader.engine.executor import Executor
    journal = Journal(tmp_path / 'luffy.db')
    rows = [dict(info={'symbol':'SOLUSDT', 'entryPrice':'100', 'markPrice':'101'},
                 symbol='SOL/USDT:USDT', contracts=5.77, side='long')]
    class Venue:
        id = 'binanceusdm'
        apiKey = 'test-only'
        urls = {'api': {'fapiPrivateV3':'https://demo-fapi.binance.com/fapi/v3'}}
        def fetch_positions(self): return rows
    kernel = Kernel.__new__(Kernel)
    kernel.journal, kernel.cfg = journal, load_config()
    kernel.exchange, kernel.market_type = Venue(), MarketType.FUTURES
    def checkpoint(*args, **kw):
        snap = json.loads(journal.kv_get('venue_position_snapshot'))
        assert verify_snapshot(snap)
        assert snap['positions'][0]['quantity'] == 5.77
        assert kw['market_snapshots'] == {}
        return dict(triggered=False, portfolio_cut_id='test-cut', trade_intents=[], risk_decisions=[]), {}
    monkeypatch.setattr(current, 'checkpoint', checkpoint)
    monkeypatch.setattr(Executor, 'open', lambda *a, **kw: pytest.fail('order'))
    assert kernel._portfolio_checkpoint()['status'] == 'PASS'
    first = json.loads(journal.kv_get('venue_position_snapshot'))['snapshot_id']
    assert kernel._portfolio_checkpoint()['status'] == 'PASS'
    assert json.loads(journal.kv_get('venue_position_snapshot'))['snapshot_id'] != first
    rows[0]['symbol'] = 'BTC/USDT:USDT'
    assert kernel._portfolio_checkpoint()['status'] == 'BLOCKED'
    assert json.loads(journal.kv_get('venue_position_snapshot'))['snapshot_id'] != first
