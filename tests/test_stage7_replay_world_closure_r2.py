"""R4 reproductions through persisted normal adapters, never source priming."""
from dataclasses import replace
from datetime import datetime, timezone
import json
import socket
import pandas as pd
import pytest
from trader.learning import capture as C, capture_runtime as CR, foundation as F
from trader.portfolio import current, opportunity_live as OL, economics as E, allocator as AL, candidate_bridge as B
from trader.core.types import Decision, Action
from trader.engine.outcomes import resolve_pending
from tests.test_stage6_normal_sources import publications, cfg  # noqa: F401
from tests.test_learning_foundation import chain  # noqa: F401
from tests.test_real_learning_integration import isolated  # noqa: F401
from tests.test_normal_learning_loop_closure import make_rule


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', lambda *a: pytest.fail('network forbidden'))


def prepare(publications, tmp_path, monkeypatch, value, blocked=False):
    """Persist TEST-ONLY authorities upstream of the unchanged current reader."""
    j, cfg, v, old, old_er, market, cap = publications
    cut = json.loads(old.payload_json)['as_of_ms']
    if blocked:
        # Legitimately wide TEST-ONLY closed-bar ATR: positive exit geometry,
        # but Risk's own affordable margin falls below its minimum notional.
        stop=v['spec']['exit']['stop']
        assert stop['kind']=='atr'
        width=market.price*.75/stop['mult']
        market.dfs['4h']=market.dfs['4h'].assign(high=market.price+width/2,low=market.price-width/2)
    from trader.observability import attention, world_producer
    from tests.test_attention_telemetry import frames
    from tests.test_opportunity_live_integration import SYMBOL
    event = attention.capture({SYMBOL: frames(1,cut)['S0/USDT']},[SYMBOL],'world-cut',attention.settings({'world_model':True}),cut)
    model, receipt = world_producer.produce(event)
    raw = json.loads(old.payload_json)
    sources=[]
    for entry in raw['sources']:
        source=AL.Source(**entry)   # decision-grade signal already carries the exact compiler identity
        sources.append(source)
    sources=(*sources,OL.source('world_model',receipt['record_json'],cut,cut+1000))
    live = OL.produce(**{k:raw[k] for k in ('as_of_ms','symbol','instrument_id','cycle_id','candidate_id','required_roles')},sources=sources,decision_grade=True)
    authority = B.freeze_authority(j,live,cfg,{'ohlcv'})
    # Rebind the fixture's TEST-ONLY calibration to this exact prospective context.
    from tests.economics_fixtures import frozen
    old_inputs = E.from_inputs(json.loads(old_er.inputs_json))
    dims = {k:getattr(old_inputs.binding,k) for k in __import__('scripts.opportunity_context_shadow',fromlist=['DIMENSIONS']).DIMENSIONS}
    ei = frozen(value,OL.economic_binding(live,**dims))
    old_ready = next(s for s in old_inputs.context if s.source_id=='TEST-ONLY-normal-allocation')
    ready_raw = json.loads(old_ready.payload_json)
    ready_raw['binding'] = B.allocation_binding(live,authority)
    ready = AL.Source.freeze(old_ready.source_id,ready_raw)
    def retime(source):
        body=json.loads(source.payload_json);body.update(captured_ms=cut,valid_until_ms=cut+1000)
        return AL.Source.freeze(source.source_id,body)
    gross,cost=retime(ei.gross),retime(ei.costs)
    reserve=json.loads(retime(ei.uncertainty).payload_json)
    reserve.update(gross_source_sha256=gross.sha256,cost_source_sha256=cost.sha256)
    ei=replace(ei,gross=gross,costs=cost,uncertainty=AL.Source.freeze(ei.uncertainty.source_id,reserve),
        context=(*ei.context,live.as_source(),authority,ready))
    for folder in ('contexts','economics'):
        for path in (tmp_path/folder).glob('*.json'): path.unlink()
    OL.persist(live,tmp_path/'contexts'); E.persist(E.build(ei),tmp_path/'economics')
    # Real scan producer: only scan sources, no allocation or manual manifests.
    d=Decision('normal-setup','normal-cycle',market.symbol,Action.HOLD if float(value)<0 else Action.BUY,0,.2,.5,[],[],ts=market.ts)
    inputs=CR.runtime_inputs(j,market,cfg,cut_ms=cut,control_state='ACTIVE')
    d.strategy_signals=[dict(strategy_id=v['strategy_id'],strategy_name='TEST_ONLY',action='BUY',confidence=.8,
        params=json.loads(next(s.payload_json for s in sources if s.source_id=='signals'))['data'][0]['params'])]
    d.learning_inputs=inputs
    if d.action==Action.HOLD:
        from trader.core.types import Action as SideAction
        d.gated_lean=SideAction.BUY
    import trader.core.journal as JM
    monkeypatch.setattr(JM,'now_utc',lambda:datetime.fromtimestamp(cut/1000,timezone.utc))
    from trader.engine.orchestrator import Orchestrator
    Orchestrator([],j).journalize(market,d,'futures','paper')
    return j,cfg,v,market,cut,live


@pytest.mark.parametrize('value,decision',[('-20','NO_ALLOCATION'),('2','OPEN'),('2','APPROVED')])
def test_normal_hold_and_risk_block_to_learning(publications,tmp_path,monkeypatch,value,decision):
    j,cfg,v,market,cut,live=prepare(publications,tmp_path,monkeypatch,value,blocked=decision=='OPEN')
    # With no original opportunity a SCAN hold cannot require one. Astra's
    # portfolio HOLD failure is reproduced by the unfixed root selection.
    assert C.registration(j._conn(),'decision:normal-setup')[1]['lineage']['opportunity_id'] is None
    from tests.admission_cycle_fixture import bind_market
    admission_receipt=bind_market(market,cfg,cut)
    result,detail=current.checkpoint(j.db_path,cfg,ledger=tmp_path/'runtime.db',market_snapshots={'normal-setup':market},available_inputs={'ohlcv'},admission_receipt=admission_receipt)
    assert result['real_order_submissions']==0 and not result['execution_routed']
    event,=detail['learning_source_delivery']
    assert not j.query("SELECT * FROM learning_capture_failures WHERE event_key LIKE '%normal-setup%' OR event_key LIKE 'portfolio:%'")
    reg=C.registration(j._conn(),event)[1]
    # R4's earlier root cannot prove a portfolio opportunity.
    from trader.learning.producers import opportunity
    root=C.registration(j._conn(),'decision:normal-setup')[1]
    assert root['lineage']['version_id']==v['version_id']
    assert root['lineage']['spec_hash']==v['spec_hash']
    with pytest.raises(ValueError,match='exact_registered_opportunity_required'):
        opportunity(root,{})
    baseline_required=C.required(root['kind'],root['lineage']) | {'derivative_identity'}
    original_roles={d['role'] for d in root['dependencies'] if d['status']=='AVAILABLE'}
    assert baseline_required-original_roles-{'action','outcome'}=={'world','context','portfolio','derivative_identity'}
    assert reg['lineage']['opportunity_id']==json.loads(live.payload_json)['opportunity_id']
    if decision in ('OPEN','APPROVED'):
        assert result['proposal']['result']['selected']
        intent=json.loads(result['trade_intents'][0]['payload_json'])
        risk=json.loads(result['risk_decisions'][0]['payload_json'])
        assert intent['requested_action']=='OPEN'
        assert risk['result']==('REFUSE' if decision=='OPEN' else 'APPROVE'),risk
        assert C.chain_event(j._conn(),'normal-setup',result['trade_intents'][0]['intent_id'])==event
    else:
        assert result['proposal']['result']['decision'] in ('CASH','NO_ALLOCATION')
    now=cut+4*3600000+300000
    class Feed:
        def fetch_ohlcv(self,*a,**kw):
            return pd.DataFrame([dict(ts=pd.Timestamp(cut+h*3600000,unit='ms',tz='UTC'),close=market.price*1.1) for h in (1,4)])
    assert resolve_pending(j,Feed(),now)==1
    assert not j.query("SELECT * FROM learning_capture_failures WHERE event_key LIKE '%normal-setup%' OR event_key LIKE 'portfolio:%'")
    row=j.query("SELECT outcome_id FROM learning_outcome_captures WHERE outcome_key LIKE 'forward:%'")[0]
    # attach() materializes the real replay/evidence chain automatically. Read
    # that artifact rather than repeating its expensive retained-source replay.
    produced=json.loads(j.query('SELECT payload FROM learning_produced_chains WHERE outcome_id=?',(row['outcome_id'],))[0]['payload'])
    m=json.loads(produced['replay']['reconstructed_json'])['capture_manifest']
    assert m['capture']['registration']['event_key']==event
    assert m['status']=='REPLAY_COMPLETE', (m['decision_source_faults'],[(d['role'],d['reason']) for d in m['dependencies'] if d['required'] and d['status']!='AVAILABLE'])
    assert produced['authoritative'] and produced['replay']['status']=='COMPLETE'
    assert produced['evidence']['quality']=='VERIFIED_REPLAY'
    expected=F.Kind.RISK_BLOCKED if decision=='OPEN' else F.Kind.REJECTED if decision=='APPROVED' else F.Kind.CASH
    assert produced['outcome']['kind']==expected.value
    assert j.query("SELECT * FROM trades WHERE decision_id='normal-setup'")==[]



@pytest.mark.parametrize('mismatch',[None,'asset_scope','horizon','regime','evidence_type','direction','strategy_family'])
def test_world_producer_to_normal_attention_consumer(isolated,monkeypatch,mismatch):
    from trader.observability import attention,world_producer
    from trader.world import ClaimCollection,ClaimCoordinate,ClaimEvidenceRef,WorldClaim,Horizon,Scope,ScopeLevel,Quality
    from trader.learning import consumers as K,targets as T,application as A
    from tests.test_attention_telemetry import frames
    j,cfg,ev,o,v=isolated
    cut=1800000000000
    event=attention.capture(frames(1,cut),['S0/USDT'],'normal-world',attention.settings({'world_model':True}),cut)
    baseline=world_producer.evaluate(event,learning_journal=j)
    model,receipt=world_producer.produce(event)
    sym=model.states[0].instrument
    observation=next(obs for obs in model.states[0].observations if obs.kind==world_producer.OUTPUT_KIND)
    claim=WorldClaim(ClaimCoordinate(Scope(ScopeLevel.INSTRUMENT,sym),Horizon.INTRADAY,world_producer.OUTPUT_KIND),cut,
        'descriptive',Quality.SUSPECT,.6,{'reason':'TEST_ONLY'},(ClaimEvidenceRef.from_observation(observation),),(),'attention.capture','TEST_ONLY')
    claimed=replace(model,claims=ClaimCollection(cut,(claim,)))
    # Only the upstream TEST-ONLY emitter differs; normal producer/evaluation/
    # Attention query boundary remains the real caller.
    monkeypatch.setattr(world_producer,'build',lambda *a:claimed)
    frozen=claimed.to_json()
    base=world_producer.evaluate(event,learning_journal=j)
    view=next(r for r in base['rows'] if r['symbol']==sym)['world_model']['claims'][0]
    assert view['confidence']==.6 and view['claim']==claim.to_dict()
    context=K.claim_context(claim,regime='NOT_APPLICABLE',direction='NOT_APPLICABLE',family='NOT_APPLICABLE')
    governed_context=replace(context,**{mismatch:'OTHER_CONTEXT'}) if mismatch else context
    rules=make_rule(F.Target.WORLD,governed_context,{'confidence':.9},kinds=(F.Kind.RESEARCH,))
    state=T.read(j,F.Target.WORLD,governed_context)
    p=T.propose(ev,F.Target.WORLD,state,next(iter(rules)),rules)
    assert A.apply(j,cfg,ev,p,A.request(ev,p,state,A.source_versions(cfg)),at_ms=o.observed_ms,test_registry=rules)['result']=='APPLIED'
    learned=world_producer.evaluate(event,learning_journal=j)
    got=next(r for r in learned['rows'] if r['symbol']==sym)['world_model']['claims'][0]
    assert got['confidence']==(.6 if mismatch else .9) and got['claim']==view['claim'] and got['base_confidence']==.6
    assert (got['learned'] is None)==bool(mismatch)
    assert claimed.to_json()==frozen
    # Replay consumes frozen governed revisions, not current owner state.
    replay_event=dict(event,governed_world_claim_state=learned.get('governed_world_claim_state',{}))
    replayed=attention.evaluate_snapshot(replay_event,claimed)
    assert replayed['rows']==learned['rows']
    # Another instrument/dimension/horizon/source cannot inherit this overlay.
    wrong=replace(claim,source='OTHER_EVIDENCE_TYPE')
    mismatch=replace(claimed,claims=ClaimCollection(cut,(wrong,)))
    monkeypatch.setattr(world_producer,'build',lambda *a:mismatch)
    mismatched=world_producer.evaluate(event,learning_journal=j)
    wrong_view=next(r for r in mismatched['rows'] if r['symbol']==sym)['world_model']['claims'][0]
    assert wrong_view['confidence']==.6 and wrong_view['learned'] is None
    assert [r['salience'] for r in learned['rows']]==[r['salience'] for r in baseline['rows']]


@pytest.mark.parametrize('control',['ACTIVE',None])
def test_scan_hold_has_no_fabricated_opportunity_and_missing_used_source_stays_partial(tmp_path,monkeypatch,control):
    from trader.core.journal import Journal
    from trader.core.types import Snapshot
    from trader.engine.orchestrator import Orchestrator
    j=Journal(tmp_path/'scan.db')
    cut=1800000000000
    ts=datetime.fromtimestamp(cut/1000,timezone.utc).isoformat()
    snap=Snapshot('BTCUSDT',ts,100,{'1h':pd.DataFrame([dict(ts=pd.Timestamp(cut-3600000,unit='ms',tz='UTC'),close=100.)])})
    cfg={'risk':{'limit':.01}}
    d=Decision('hold','cycle','BTCUSDT',Action.HOLD,0,.2,.5,[],[],ts=ts)
    d.gated_lean=Action.BUY
    d.learning_inputs=CR.runtime_inputs(j,snap,cfg,cut_ms=cut,control_state=control)
    import trader.core.journal as JM
    monkeypatch.setattr(JM,'now_utc',lambda:datetime.fromtimestamp(cut/1000,timezone.utc))
    Orchestrator([],j).journalize(snap,d,'futures','paper')
    class Feed:
        def fetch_ohlcv(self,*a,**kw):
            return pd.DataFrame([dict(ts=pd.Timestamp(cut+h*3600000,unit='ms',tz='UTC'),close=110.) for h in (1,4)])
    resolve_pending(j,Feed(),cut+4*3600000+300000)
    assert not j.query('SELECT * FROM learning_capture_failures')
    oid=j.query("SELECT outcome_id FROM learning_outcome_captures WHERE outcome_key LIKE 'forward:%'")[0]['outcome_id']
    m=C.manifest(j._conn(),oid)
    assert m['capture']['registration']['lineage']['opportunity_id'] is None
    assert all(dep['status']=='NOT_APPLICABLE' for dep in m['dependencies'] if dep['role'] in ('world','context','portfolio','proposal','intent'))
    assert m['status']==('REPLAY_COMPLETE' if control else 'REPLAY_PARTIAL')
    if control is None:
        assert 'missing:control' in m['decision_source_faults']


def test_failed_terminal_delivery_cannot_fall_back_to_scan(tmp_path):
    from trader.core.journal import Journal
    j=Journal(tmp_path/'chain.db')
    with j._tx() as db:
        C.register(db,'decision:root',F.Kind.CASH.value,{'decision_id':'root','cycle_id':'cycle'},100,[],decision_stage='SCAN')
        from types import SimpleNamespace
        source=AL.Source.freeze('live-context:TEST_ONLY',dict(candidate_id='root:version',context_json='{}'))
        failed=SimpleNamespace(sources=(source,),candidates=(SimpleNamespace(version_id='version',opportunity_context_json='{}'),))
        CR.failed_allocation_delivery(db,failed,{'proposal':{'proposal_id':'proposal'}})
        with pytest.raises(ValueError,match='terminal_allocation_registration_unavailable_no_scan_fallback'):
            C.forward_event(db,'root',200)


def test_parent_provenance_is_verified_beyond_the_outer_manifest_hash(tmp_path):
    from trader.core.journal import Journal
    from trader.learning import decision_sources as D
    j=Journal(tmp_path/'parents.db')
    with j._tx() as db:
        C.ensure(db)
        dep=CR.freeze(db,'decision',dict(id='root'),100,'TEST_ONLY')
        parent_id=C.register(db,'decision:root',F.Kind.CASH.value,{'decision_id':'root','cycle_id':'cycle'},100,[dep],decision_stage='SCAN')
        parent=C.registration(db,'decision:root')[1]
        child=dict(lineage=parent['lineage'],decision_ms=101,chain=dict(parent_event_key='decision:root',parent_registration_id=parent_id))
        retained=dict(registration_id=parent_id,registration=parent,decision_source_manifest=D.load(db,parent['decision_source_manifest_id']),
            sources={'decision':{'id':'other'}},data_blobs={})
        assert 'parent:decision:source_hash_differs' in C.verify_ancestors(child,[retained])
