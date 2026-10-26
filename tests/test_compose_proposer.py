import copy
import pytest
from trader.research.compose.proposer import GraphProposer
from trader.research.compose import graph, search
from trader.research.closure.store import Store
from trader.research.closure import consumer, digest, ClosureRefused


def proposal():
    return dict(question='Does return plus volume predict a rise?', rationale='two complementary inputs',
        expected='positive return', failure_conditions='no out-of-sample support', tf='1h',
        structure='long_only', geo='fixed', short=None,
        long={'all':[{'cmp':'gt','a':{'f':'ret6'},'b':{'lit':0}},
                     {'cmp':'gt','a':{'f':'relvol50'},'b':{'lit':1}}]})


class LLM:
    available=True
    def __init__(self, replies): self.replies, self.calls = list(replies), []
    def chat_json(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return self.replies.pop(0)


Q = dict(question_id='test-q', source_bank_id='test-source', text='a retained question', passage={'text':'test idea'})


def test_proposal_is_graph_with_exact_source_and_restart_reuses_reply(tmp_path):
    store=Store(tmp_path/'research.db'); llm=LLM([dict(proposal=proposal())])
    p=GraphProposer(llm,store,tf='1h')
    first=p(Q,'BRAIN',Q); second=p(Q,'BRAIN',Q)
    assert first==second and first[0]=='COMPILED' and graph.is_graph(first[2])
    assert first[2]['tf']=='1h' and first[3]['source_id']=='test-source'
    assert len(llm.calls)==1 and llm.calls[0][1]['purpose']=='research'
    assert 'protected' not in llm.calls[0][0].lower()
    store.close()


def test_bad_proposal_has_bounded_repair_no_family_fallback(tmp_path):
    store=Store(tmp_path/'research.db'); bad=proposal();bad['tf']='4h'
    llm=LLM([dict(proposal=bad),dict(proposal=proposal())])
    r=GraphProposer(llm,store,tf='1h')(Q,'BRAIN',Q)
    assert r[0]=='COMPILED' and len(llm.calls)==2
    assert 'proposal_horizon_differs_from_study' in llm.calls[1][0]
    store.close()


def test_unavailable_proposer_never_substitutes_old_family(tmp_path):
    store=Store(tmp_path/'research.db')
    r=GraphProposer(None,store,tf='1h')(Q,'BRAIN',Q)
    assert r[0]=='WAITING_DATA' and r[2] is None and not store.all_once('candidates')
    store.close()


def test_verified_consumer_uses_new_proposer_before_legacy_templates(tmp_path):
    store=Store(tmp_path/'research.db');p=GraphProposer(LLM([dict(proposal=proposal())]),store,tf='1h')
    r=consumer._resolve(Q,'BRAIN',Q,{},proposer=p)
    assert r[0]=='COMPILED' and graph.is_graph(r[2])
    store.close()


def test_search_honours_declared_factor_timeframe_horizon_and_direction():
    b=dict(search.DEFAULT_BUDGET,tf='1h',prediction_horizon=12,structures=['long_only'],
           factors=['ret6','volz96','ref_spx_ret30'],question='Does volume modify the equity relationship?')
    stream=search.candidate_stream(b,{k:'EXECUTABLE' for k in b['factors']})
    rows=[r for rs in stream.values() for r in rs]
    assert rows and all(r['tf']=='1h' and r['structure']=='long_only' and r['short'] is None for r in rows)
    assert all(r['provenance']['prediction_horizon']==12 for r in rows)
    assert all(r['question'].startswith(b['question']) for r in rows)
    for r in rows: graph.make_candidate(r)
    with pytest.raises(ClosureRefused,match='search_factors_invalid'):
        search.candidate_stream(dict(b,factors=['made_up']),{})


def test_relative_value_question_gets_a_validated_two_leg_declaration(tmp_path):
    from trader.research.compose import hedge
    spec=proposal();spec.update(structure='pair',hedge=dict(primary={'symbol':'A/USDT'},hedge={'symbol':'B/USDT'},
        purpose='relative_value',account_mode='netting',ratio={'method':'fixed','value':1.},spec_id='untrusted-derived-id'))
    store=Store(tmp_path/'r.db');llm=LLM([{'proposal':spec}])
    out=GraphProposer(llm,store,tf='1h',max_attempts=1)(Q,'BRAIN',Q)
    assert out[0]=='COMPILED',out
    declared=out[2]['hedge']
    assert declared['purpose']=='relative_value' and declared['objective']['metric']=='net_return_pct'
    assert declared['spec_id']!='untrusted-derived-id' and hedge.validate(declared)==declared
    assert 'net_return_pct' in llm.calls[0][0]
    store.close()


def test_generated_negative_fees_are_refused_instead_of_becoming_profit(tmp_path):
    spec=proposal();spec.update(structure='hedged',hedge=dict(primary={'symbol':'A/USDT'},hedge={'symbol':'B/USDT'},purpose='protective_beta',fee_bps=-5))
    store=Store(tmp_path/'r.db');llm=LLM([{'proposal':spec}])
    out=GraphProposer(llm,store,tf='1h',max_attempts=1)(Q,'BRAIN',Q)
    assert out[0]=='BLOCKED' and 'hedge_numeric_invalid' in out[1] and out[2] is None
    store.close()


def test_missing_economic_observable_creates_requirement_without_indicator_substitution(tmp_path):
    store=Store(tmp_path/'economic-gap.db')
    raw=proposal();raw['roles']={'asset_size':'exposure'}
    client=LLM([dict(proposal=raw)])
    proposer=GraphProposer(client,store,tf='1h')
    first=proposer(Q,'BRAIN',Q);second=proposer(Q,'BRAIN',Q)
    assert first[0]==second[0]=='WAITING_DATA'
    assert 'asset_size' in first[3]['required_data']
    assert first[2] is None and len(client.calls)==1
    assert not store.all_once('candidates')
    store.close()
