"""Descriptive statistics must never govern spec or genome lifecycle.

The historical six-loss genome rule once retired spec rows as well. Current
promotion.evaluate_population only refreshes closed-trade statistics. Both row
kinds retain their state, clocks and reasons; retired records remain untouched.
Registered learning and the Governor own lifecycle changes.
"""
import sys
import json
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal
from trader.core.types import Position, Side
from trader.strategy import promotion


def _mk(j, sid, state, kind="ema_trend", retire_reason="", stats="{}"):
    j.query("INSERT INTO strategies (id,name,kind,params,state,description,"
            "origin,hypothesis,invalidation,regime_filter,markets,generation,"
            "parent_id,created_at,stats_json,retire_reason,state_changed_at) "
            "VALUES (?,?,?,'{}',?,'d','seed','hyp','inv','[]',"
            "'[\"futures\"]',0,'',?,?,?,?)",
            (sid, sid, kind, state, datetime.now(timezone.utc).isoformat(),
             stats, retire_reason, "2026-09-20T09:04:33+00:00"))


def _losses(j, sid, n):
    """Six-loss fixtures retain arithmetic without granting authority."""
    for i in range(n):
        p = Position(id=f"{sid}_t{i}", symbol="X/USDT", side=Side.LONG,
                     amount=1, entry_price=100, notional_usdt=100,
                     strategy_id=sid, market_type="futures")
        j.add_trade(p)
        j.close_trade(f"{sid}_t{i}", 95, -5.0, "sl_fill")


def _row(j, sid):
    return j.query("SELECT * FROM strategies WHERE id=?", (sid,))[0]


def test_mixed_spec_and_genome_statistics_never_authorize_state_changes(tmp_path):
    j = Journal(tmp_path / 'j.db')
    for sid, kind in [('spec', 'spec'), ('genome', 'ema_trend')]:
        _mk(j, sid, 'paper', kind=kind)
        _losses(j, sid, 6)
    before = {sid: {k: v for k, v in _row(j, sid).items() if k != 'stats_json'}
              for sid in ('spec', 'genome')}
    assert promotion.evaluate_population(j) == []
    for sid in before:
        row = _row(j, sid)
        assert {k: v for k, v in row.items() if k != 'stats_json'} == before[sid]
        assert json.loads(row['stats_json']) == {
            'trades': 6, 'wins': 0, 'pnl_usdt': -30.0, 'pf': 0.0}
    assert not j.query("SELECT * FROM brain_events WHERE kind='statistical_transition'")


def test_existing_retired_spec_rows_are_not_mutated(tmp_path):
    """A spec already retired keeps its row byte-for-byte.

    Guards the Donchian row itself: its (empty) retire_reason, its recorded
    state_changed_at and its stats blob are historical evidence of HOW it was
    retired, and this fix must not rewrite them.
    """
    j = Journal(tmp_path / "j.db")
    stats = '{"trades": 6, "wins": 0, "pnl_usdt": -69.3361, "pf": 0.0}'
    _mk(j, "auth_donchian_breakout_trail", "retired", kind="spec", stats=stats)
    _losses(j, "auth_donchian_breakout_trail", 6)
    before = _row(j, "auth_donchian_breakout_trail")

    promotion.evaluate_population(j)

    assert _row(j, "auth_donchian_breakout_trail") == before


def test_retired_genome_rows_are_also_left_alone(tmp_path):
    """Unchanged pre-existing behaviour: retired is terminal for genomes too."""
    j = Journal(tmp_path / "j.db")
    _mk(j, "g_dead", "retired", retire_reason="measured: PF 0.41")
    _losses(j, "g_dead", 6)
    before = _row(j, "g_dead")

    promotion.evaluate_population(j)

    assert _row(j, "g_dead") == before
