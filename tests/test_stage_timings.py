import threading
import pytest
from trader.scheduling.timing import StageTimings, stage, stage_scope


def test_nested_timings_separate_inclusive_and_exclusive():
    now=[0.]
    t=StageTimings(clock=lambda:now[0],cpu=lambda:now[0]/2)
    with t.run('market'):
        now[0]=1
        with stage_scope('history'):
            now[0]=4
        now[0]=5
    r=t.snapshot()['last']['market']
    assert r['elapsed_s']==5
    assert r['stages']['total']['wall_s']==5
    assert r['stages']['total']['exclusive_wall_s']==2
    assert r['stages']['total']['exclusive_cpu_s']==1
    assert r['stages']['history']['wall_s']==3
    assert r['active']==[]


def test_return_values_and_failures_are_unchanged():
    t=StageTimings()
    result=object()
    @stage('success')
    def good(): return result
    error=ValueError('private data must never be logged')
    @stage('failure')
    def bad(): raise error
    assert good() is result
    with pytest.raises(ValueError) as caught:
        with t.run('market'):
            assert good() is result
            bad()
    assert caught.value is error
    r=t.snapshot()['last']['market']
    assert r['status']=='failed'
    assert r['stages']['failure']['last_error_type']=='ValueError'
    assert 'private data' not in str(r)


def test_running_stage_is_visible_and_other_threads_are_isolated():
    t=StageTimings();entered=threading.Event();release=threading.Event()
    def work():
        with t.run('market'):
            with stage_scope('history',symbol='BTC/USDT'):
                entered.set();release.wait(3)
    thread=threading.Thread(target=work);thread.start()
    try:
        assert entered.wait(1)
        with stage_scope('unrelated_background_work'): pass
        r=t.snapshot()['current']['market']
        assert r['active'][-1]['stage']=='history'
        assert r['active'][-1]['symbol']=='BTC/USDT'
        assert r['active'][-1]['elapsed_s']>=0
        assert 'unrelated_background_work' not in r['stages']
    finally:
        release.set();thread.join(2)
    assert not t.snapshot()['current']


def test_per_symbol_detail_and_run_retention_are_bounded():
    t=StageTimings()
    for _ in range(3):
        with t.run('market'):
            for i in range(100):
                with stage_scope('history',symbol=str(i)): pass
    s=t.snapshot()
    assert len(s['last'])==1
    r=s['last']['market']['stages']['history']
    assert r['calls']==100 and len(r['slowest'])==5
