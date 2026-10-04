"""Historical consumers must qualify learning at the captured decision cut."""
from dataclasses import replace
import json
import pytest
from trader.core.journal import Journal
from trader.learning import consumers as C, foundation as L, targets as T
from trader.observability.attention import evaluate_snapshot
from trader.portfolio.allocator import attach_learning,allocate,Source
from tests.test_learning_foundation import chain  # noqa: F401
from tests.test_real_learning_integration import isolated,apply  # noqa: F401
from tests.test_attention_telemetry import frames,event
from tests.test_portfolio_allocator import candidate,inputs,test_only_models  # noqa: F401


def scan(cut):
    market=frames(6,cut)
    for item in market.values():item['4h'].loc[29,'volume']=100000
    return event('historical-attention',cut,market)


def test_attention_future_revision_exact_cut_replay_and_restart(isolated):
    j,cfg,ev,o,_=isolated
    cut=max(1800000000000,o.observed_ms)+1000
    ctx=C.attention_context('S5/USDT','14400000');e=scan(cut)
    before=evaluate_snapshot(e,learning_journal=j)
    apply(j,cfg,ev,cut+1,L.Target.ATTENTION,ctx,{'priority':'HIGH'})
    assert evaluate_snapshot(e,learning_journal=j)['rows']==before['rows']
    assert evaluate_snapshot(e,learning_journal=Journal(j.db_path))['rows']==before['rows']
    available=evaluate_snapshot(scan(cut+1),learning_journal=j)
    assert any(r['symbol']=='S5/USDT' and r['selected'] for r in available['rows'])
    frozen=available['governed_attention_state']
    apply(j,cfg,ev,cut+1,L.Target.ATTENTION,ctx,{'priority':'LOW'})
    assert evaluate_snapshot(dict(scan(cut+1),governed_attention_state=frozen))['rows']==available['rows']
    assert evaluate_snapshot(dict(scan(cut+1),governed_attention_state=frozen),learning_journal=j)['rows']==available['rows']
    assert evaluate_snapshot(dict(e,governed_attention_state={}),learning_journal=j)['rows']==before['rows']
    with pytest.raises(ValueError,match='future|temporal'):
        evaluate_snapshot(dict(e,governed_attention_state=frozen))
    assert T.read(j,L.Target.ATTENTION,ctx)['value']['priority']=='LOW'


def test_allocation_future_revision_and_frozen_replay_are_cut_qualified(isolated):
    j,cfg,ev,o,_=isolated
    original=inputs(candidate());ctx=C.allocation_context(original.candidates[0])
    before=allocate(attach_learning(original,j))
    cut=max(1800000000000,o.observed_ms)+1000
    apply(j,cfg,ev,cut,L.Target.ALLOCATION,ctx,{'max_share':0})
    assert allocate(attach_learning(original,j))==before
    assert allocate(attach_learning(original,Journal(j.db_path)))==before
    # The adaptation remains available exactly at application. Other market
    # inputs retain their own expiry checks; this never fabricates fresh economics.
    at_cut=replace(original,as_of_ms=cut)
    captured=attach_learning(at_cut,j)
    source=next(s for s in captured.sources if s.source_id=='governed-allocation-context')
    state=json.loads(source.payload_json)[original.candidates[0].candidate_id]
    assert state['value']=={'max_share':0} and state['revision']==1
    proposal=allocate(captured)
    apply(j,cfg,ev,cut+1,L.Target.ALLOCATION,ctx,{'max_share':.5})
    assert allocate(captured)==proposal
    assert attach_learning(at_cut,j)==captured
    tampered=json.loads(source.payload_json)
    tampered[original.candidates[0].candidate_id]['value']={'max_share':.9}
    bad=Source.freeze('governed-allocation-context',tampered)
    with pytest.raises(ValueError,match='projection_mismatch'):
        allocate(replace(at_cut,sources=original.sources+(bad,)))
    leaked=replace(original,sources=original.sources+(source,))
    with pytest.raises(ValueError,match='future|temporal'):
        allocate(leaked)
