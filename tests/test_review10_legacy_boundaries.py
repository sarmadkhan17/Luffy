"""Offline legacy evidence remains descriptive and correctly measures loss."""
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from trader.brain.tv import validate_population
from trader.core.journal import Journal
from trader.core.types import Action
from trader.strategy import backtest as legacy
from trader.strategy.genome import Genome


def test_tv_duplicates_are_advisory_and_respect_full_trading_definition(tmp_path):
    journal = Journal(tmp_path / 'legacy.db')
    for sid, regimes, markets, params in (
        ('first', '["RANGING","VOLATILE"]', '["futures"]', '{"adx_min":22}'),
        ('duplicate', '["VOLATILE","RANGING"]', '["futures"]', '{ "adx_min": 22 }'),
        ('other_regime', '["TRENDING_UP"]', '["futures"]', '{"adx_min":22}'),
        ('other_market', '["RANGING","VOLATILE"]', '["spot"]', '{"adx_min":22}'),
    ):
        with journal._tx() as conn:
            conn.execute('INSERT INTO strategies(id,name,kind,params,state,regime_filter,markets,created_at,state_changed_at) '
                         'VALUES(?,?,\'ema_trend\',?,\'paper\',?,?,\'2026-01-01\',\'2026-01-01\')',
                         (sid, sid, params, regimes, markets))
    before = journal.list_strategies()
    calls = []
    tv = SimpleNamespace(walk_forward=lambda symbol, family:
                         calls.append((symbol, family)) or {'valid': False})
    result = validate_population(journal, tv)
    assert result['duplicates'] == ['duplicate']
    assert len(calls) == 3
    assert journal.list_strategies() == before
    assert Journal(journal.db_path).list_strategies() == before
    events = journal.query("SELECT * FROM brain_events WHERE kind LIKE 'duplicate%'")
    assert len(events) == 1 and events[0]['kind'] == 'duplicate_advisory'
    assert json.loads(events[0]['detail'])['of'] == 'first'


def test_initial_loss_counts_toward_legacy_drawdown_gate(monkeypatch):
    frame = pd.DataFrame(dict(
        ts=pd.date_range('2026-01-01', periods=214, freq='15min', tz='UTC'),
        open=100.0, high=101.0, low=99.0, close=100.0, volume=100.0))
    frame.loc[211, 'low'] = 96.0
    genome = Genome('synthetic', 'ema_trend', 'x' * 60, 'y' * 20,
                    frozenset({'RANGING'}), frozenset({'futures'}))
    monkeypatch.setattr(legacy, '_atr', lambda _: 1.0)
    monkeypatch.setattr(legacy.strat_lib, 'evaluate', lambda _, snap:
                        SimpleNamespace(action=Action.BUY) if len(snap.dfs['15m']) == 211 else None)
    result = legacy.backtest(genome, frame, dict(stop_loss_atr_mult=2.5,
        take_profit_atr_mult=4.5, taker_fee_pct=0, slippage_atr_frac=0,
        funding_rate_8h=0, risk_per_trade_pct=5))
    assert result.trades == 1 and result.losses == 1
    assert result.pnl_usdt == pytest.approx(-100)
    assert result.max_dd_pct == pytest.approx(5)
    assert any('DD' in failure for failure in result.passes(max_dd=4)[1])


@pytest.mark.parametrize('field,value', [
    ('regime_filter', None), ('markets', None),
    ('regime_filter', 'null'), ('markets', 'null'),
    ('regime_filter', '"RANGING"'), ('markets', '[1]'),
    ('params', 'not-json'), ('params', 'null'), ('markets', '[]'),
])
def test_incomplete_historical_definitions_still_receive_advisory(tmp_path, field, value):
    journal = Journal(tmp_path / 'historical.db')
    for sid in ('first', 'second'):
        row = dict(params='{}', regime_filter='[]', markets='["futures"]')
        row[field] = value
        with journal._tx() as conn:
            conn.execute('INSERT INTO strategies(id,name,kind,state,params,regime_filter,markets) '
                         'VALUES(?,?,\'ema_trend\',\'paper\',?,?,?)',
                         (sid, sid, row['params'], row['regime_filter'], row['markets']))
    before = journal.list_strategies()
    calls = []
    tv = SimpleNamespace(walk_forward=lambda symbol, family:
                         calls.append((symbol, family)) or {'valid': False})
    result = validate_population(journal, tv)
    assert result['duplicates'] == []
    assert len(calls) == 2 and len(result['advisory_fail']) == 2
    assert journal.list_strategies() == before
