"""Research outage handling and economics/readiness separation, no venue actions."""
from types import SimpleNamespace
from unittest.mock import Mock

from trader.brain.spec_writer import SpecWriter
from trader.research.closure import development as D


def test_missing_provider_reply_defers_instead_of_exhausting_an_idea():
    llm = SimpleNamespace(available=True, chat_json=Mock(return_value=None))
    spec, trace = SpecWriter(llm).write()
    assert spec is None and trace['deferred']
    assert llm.chat_json.call_count == 1


def test_unavailable_brain_does_not_consume_queued_idea(monkeypatch):
    from trader.kernel import Kernel
    from trader.brain import analyst, ideas, llm
    journal = Mock()
    journal.list_specs.return_value = []
    fake = SimpleNamespace(review_deployed=lambda _: [], flush_health_observations=lambda: None)
    monkeypatch.setattr(analyst, 'Analyst', lambda *a: fake)
    monkeypatch.setattr(llm, 'BrainLLM', lambda *a: SimpleNamespace(available=False))
    monkeypatch.setattr(ideas, 'next_idea', lambda *a, **k: {'idea_id': 'retained-idea'})
    monkeypatch.setattr(ideas, 'pending', lambda *a: ['retained-idea'])
    consumed = Mock()
    monkeypatch.setattr(ideas, 'mark_consumed', consumed)
    kernel = SimpleNamespace(journal=journal, cfg={}, feed=None, notifier=None,
                             _research_handoff=lambda *a: None, _strategist_knowledge=lambda: {},
                             _compositional_paper_intake=lambda *a, **k: [])
    result = Kernel._mechanism_once(kernel)
    assert result['ideas_used'] == []
    consumed.assert_not_called()
    assert journal.log_brain_event.call_args.args[0] == 'spec_write_deferred'


def test_retrospective_economics_does_not_claim_missing_final_evidence():
    scored = dict(verdict='scored', scored_symbols=23, trades=382, testable=False,
                  consistency_p=.001, portfolio={'total_pct': 8.1})
    assert D.classify(scored, {})[0] == 'INCONCLUSIVE_POWER'  # compatibility for external strict callers
    assert D.classify(scored, {}, development_only=True)[0] == 'DEVELOPMENT_POSITIVE'
    scored['portfolio']['total_pct'] = -8
    assert D.classify(scored, {}, development_only=True)[0] == 'NEGATIVE_ECONOMICS'
    scored['scored_symbols'] = 1
    assert D.classify(scored, {}, development_only=True)[0] == 'INCONCLUSIVE_POWER'


def test_managed_research_reports_preparation_failure_as_process_failure(monkeypatch,capsys):
    from trader.research.closure import __main__ as cli
    monkeypatch.setattr(cli,'scheduled_once',lambda *a,**k:dict(status='PREP_FAILED',detail='fixture'))
    assert cli.main(['scheduled','--expected-revision','fixture'])==3
    assert 'PREP_FAILED' in capsys.readouterr().out


def test_calibration_freezes_actual_compositional_search_settings(tmp_path,monkeypatch):
    from trader.research.closure import calibration as C
    from trader.core import config
    seen=[]
    monkeypatch.setattr(config,'load_config',lambda:dict(research=dict(closure=dict(auto=dict(
        max_candidates=7,search=dict(max_attempts=13,max_fits=20))))))
    monkeypatch.setattr(C,'_replicate',lambda p,c,i,seed,d,run_id='': seen.append(p) or dict(
        run_id=run_id,case=c['name'],index=i,seed=seed,outcome='NOT_REACHED',trials=0,
        process_status='COMPLETED'))
    report=C.run(dict(C.PROTOCOL,**C.SMOKE),tmp_path,replicates=1)
    assert report['search_scope']=='COMPOSITIONAL_SEARCH'
    assert seen[0]['search']['max_attempts']==13 and seen[0]['max_candidates']==7
    assert seen[0]['max_revisions']==4
    assert not seen[0].get('families')
    assert report['calibration_status']=='INCONCLUSIVE' and not report['authority_granted']


def test_old_search_calibration_does_not_authorize_a_different_search():
    from trader.research.closure import calibration as C
    rep=dict(schema='closure-calibration-report.v3',search_scope='COMPOSITIONAL_SEARCH',
             search_contract_id='search-a',calibration_status='CALIBRATED',kind='FINAL',
             numerics='EXACT_CONFIGURED',authority_granted=False,numeric_id='n',evaluator_id='e',cost_id='cost')
    rep['report_id']=C.digest(rep)
    env=dict(search_contract_id='search-a',numeric_id='n',evaluator_id='e',cost_id='cost')
    assert C.report_authorizes(rep,{'report_id':rep['report_id']},env)
    assert not C.report_authorizes(rep,{'report_id':rep['report_id']},dict(env,search_contract_id='search-b'))
    assert not C.report_authorizes(rep,{'report_id':rep['report_id']},{k:v for k,v in env.items() if k!='search_contract_id'})
