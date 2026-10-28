"""Real adapters retain missing-input evidence without weakening evaluation."""
import pandas as pd
import pytest
from trader.research.closure.development import _evaluation_failure
from trader.strategy import exit_policy as E, vector_backtest as V
from trader.strategy.compile import DependencyUnavailable, compile_spec
from tests.test_d6_d7_vector_handoff import RISK, signal_case
from tests.test_strategy_retrospective import dataset, spec  # noqa: F401


def classified(exc):
    return _evaluation_failure(lambda outcome,reason,**kw:dict(outcome=outcome,reason=reason,**kw),exc,'1h')


def test_real_candle_gap_remains_refused_and_is_reported_as_missing_data():
    df,ex,lo,sh,signal,available=signal_case('long',max_bars=6)
    signal[:]=False
    df.loc[212:,'ts']+=pd.Timedelta(days=20)
    with pytest.raises(E.ExitObservationUnavailable) as caught:
        V.simulate(lo,sh,df,ex,RISK,exit_sig=signal,exit_sig_available=available)
    result=classified(caught.value)
    assert result['outcome']=='BLOCKED_DATA'
    assert result['context_gaps'][0]['observed_ms']>result['context_gaps'][0]['expected_ms']


def test_actual_retrospective_compiler_dependency_error_remains_a_data_gap(dataset):
    frame,_=dataset
    with pytest.raises(DependencyUnavailable) as caught:
        compile_spec(spec('btc_ret(1) > 0')).entries({'1h':frame})
    result=classified(caught.value)
    assert result['outcome']=='BLOCKED_DATA'
    assert any(g['requirement']=='btc:1h' for g in result['context_gaps'])


def test_unexpected_errors_with_similar_text_still_report_code_failure():
    result=classified(ValueError('exit_observation_not_consecutive_closed_bar'))
    assert result['outcome']=='CODE_FAILURE'
