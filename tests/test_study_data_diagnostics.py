"""Missing inputs are visible even when they need no owner-only export."""
from types import SimpleNamespace
import json
from trader.research.closure.study import study_status


def status(results, failures=0):
    units=[dict(unit_id=f'develop:{i}',kind='develop',status='DONE',result=json.dumps(r))
           for i,r in enumerate(results)]
    units.append(dict(unit_id='freeze',kind='freeze',status='DONE',
                      result=json.dumps({'freeze_id':None,'reason':'NO_FINALIST'})))
    units.extend(dict(unit_id=f'develop:failed-{i}',kind='develop',status='FAILED',
                      result=None,note='develop_worker_failed: snapshot_not_quiescent') for i in range(failures))
    store=SimpleNamespace(study=lambda _:dict(status='COMPLETED',stop_reason='COMPLETE',
        last_progress_ms=1,next_work=None,steps=1),units=lambda _:units)
    return study_status(store,'study')


def test_automatic_provider_gaps_are_visible_without_creating_owner_alerts():
    result=status([dict(outcome='BLOCKED_DATA',context_gaps=[{'requirement':'funding'},
                                                          {'requirement':'ref:cg_total_daily'}])])
    assert result['process_status']=='COMPLETED'
    assert result['research_outcome']=='NO_FINALIST'
    assert result['development_outcomes']=={'BLOCKED_DATA':1}
    assert set(result['blocked_requirements'])=={'funding','ref:cg_total_daily'}
    assert result['waiting_for_data']==[]
    assert 'no economic verdict' in result['research_reason']


def test_gaps_do_not_erase_actual_negative_economics_on_other_candidates():
    result=status([dict(outcome='NEGATIVE_ECONOMICS'),dict(outcome='BLOCKED_DATA')])
    assert result['development_outcomes']=={'NEGATIVE_ECONOMICS':1,'BLOCKED_DATA':1}
    assert '1/2' in result['research_reason']


def test_many_distinct_requirements_have_a_visible_bound():
    result=status([dict(outcome='BLOCKED_DATA',context_gaps=[{'requirement':f'ref:{i}'} for i in range(40)])])
    assert len(result['blocked_requirements'])==32
    assert result['blocked_requirements_omitted']==8


def test_no_data_failure_is_invented_for_an_evaluated_negative_candidate():
    result=status([dict(outcome='NEGATIVE_ECONOMICS')])
    assert result['research_reason']=='NO_FINALIST'
    assert result['blocked_requirements']==[]


def test_worker_failures_are_visible_separately_from_data_and_economics():
    result=status([dict(outcome='BLOCKED_DATA')],failures=2)
    assert result['process_status']=='COMPLETED'
    assert len(result['failed_development_units'])==2
    assert '2 development units failed to run' in result['research_reason']
    assert result['development_outcomes']=={'BLOCKED_DATA':1}
