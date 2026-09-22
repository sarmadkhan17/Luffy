"""Intrabar MFE/MAE booking — observation only.

The journal recorded `close_reason` and nothing else about the path a trade
took, so the 2026-09-20 Donchian audit had to reconstruct every excursion
from candles by hand. These pin the contract of the replacement.

See docs/superpowers/reports/2026-09-20-donchian-retirement-attribution-audit.md
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal
from trader.core.types import Position, Side
from trader.engine import excursion

T0 = datetime(2026, 9, 13, 16, 0, tzinfo=timezone.utc)
TF_MS = 900_000                                   # 15m


def _bars(n, highs, lows, start=T0, step_ms=TF_MS):
    """n bars on the 15m grid from `start`, cycling the given extremes."""
    base = int(start.timestamp() * 1000)
    return [(base + i * step_ms, highs[i % len(highs)], lows[i % len(lows)])
            for i in range(n)]


def _iso(minutes):
    return (T0 + timedelta(minutes=minutes)).isoformat()


# ── formulas: direction, sign, intrabar semantics ────────────────────────

def test_long_excursions():
    """MFE from bar HIGHS, MAE from bar LOWS, both in R off frozen entry."""
    bars = _bars(8, highs=[102, 110, 104, 101], lows=[99, 98, 95, 100])
    ex = excursion.compute("long", entry_price=100, initial_risk=5,
                           bars=bars, timeframe="15m",
                           opened_at=_iso(0), closed_at=_iso(120))
    assert ex.measured
    assert ex.mfe_r == (110 - 100) / 5          # +2.0R from the high
    assert ex.mae_r == (95 - 100) / 5           # -1.0R from the low
    assert ex.provenance["basis"] == "intrabar_high_low"


def test_short_excursions():
    """Direction inverts both: favourable is the LOW, adverse is the HIGH."""
    bars = _bars(8, highs=[102, 110, 104, 101], lows=[99, 98, 95, 100])
    ex = excursion.compute("short", entry_price=100, initial_risk=5,
                           bars=bars, timeframe="15m",
                           opened_at=_iso(0), closed_at=_iso(120))
    assert ex.measured
    assert ex.mfe_r == (100 - 95) / 5           # +1.0R from the low
    assert ex.mae_r == (100 - 110) / 5          # -2.0R from the high


def test_negative_mfe_when_never_favourable():
    """A trade that never traded up has a NEGATIVE mfe_r, not zero.

    Clamping would assert an excursion that never happened.
    """
    bars = _bars(8, highs=[99, 98], lows=[97, 90])
    ex = excursion.compute("long", entry_price=100, initial_risk=5,
                           bars=bars, timeframe="15m",
                           opened_at=_iso(0), closed_at=_iso(120))
    assert ex.mfe_r == (99 - 100) / 5 == -0.2
    assert ex.mae_r == (90 - 100) / 5 == -2.0


def test_positive_mae_when_never_adverse():
    """Symmetrically, a trade that never went against itself has mae_r > 0."""
    bars = _bars(8, highs=[115, 120], lows=[105, 108])
    ex = excursion.compute("long", entry_price=100, initial_risk=5,
                           bars=bars, timeframe="15m",
                           opened_at=_iso(0), closed_at=_iso(120))
    assert ex.mfe_r == 4.0 and ex.mae_r == 1.0
    assert ex.mfe_r >= ex.mae_r


def test_mfe_never_below_mae():
    for side in ("long", "short"):
        ex = excursion.compute(side, 100, 5, _bars(8, [103], [97]), "15m",
                               _iso(0), _iso(120))
        assert ex.mfe_r >= ex.mae_r, side


# ── point-in-time safety ─────────────────────────────────────────────────

def test_bars_outside_the_hold_are_excluded():
    """A spike before entry or after exit must not become an excursion."""
    pre = [(int(T0.timestamp() * 1000) - TF_MS, 999.0, 1.0)]
    post = [(int(T0.timestamp() * 1000) + 8 * TF_MS, 999.0, 1.0)]
    bars = pre + _bars(8, [103], [97]) + post
    ex = excursion.compute("long", 100, 5, bars, "15m", _iso(0), _iso(120))
    assert ex.measured
    assert ex.mfe_r == 0.6 and ex.mae_r == -0.6      # the 999/1 never counted


def test_straddling_bar_is_excluded_not_truncated():
    """A bar still open at exit is not a bar — it is dropped."""
    bars = _bars(9, [103], [97])                 # 9th bar closes after exit
    ex = excursion.compute("long", 100, 5, bars, "15m", _iso(0), _iso(120))
    assert ex.provenance["bars"] == 8
    assert ex.provenance["last_bar_ms"] == int(T0.timestamp() * 1000) + 7 * TF_MS


# ── unknown stays unknown ────────────────────────────────────────────────

def test_incomplete_coverage_is_unknown_not_a_number():
    ex = excursion.compute("long", 100, 5, _bars(3, [103], [97]), "15m",
                           _iso(0), _iso(120))          # 3 of 8 bars
    assert not ex.measured
    assert ex.mfe_r is None and ex.mae_r is None
    assert ex.provenance["status"] == "unknown"
    assert ex.provenance["reason"] == "insufficient_coverage"
    assert ex.provenance["coverage"] == 0.375


def test_no_bars_is_unknown():
    ex = excursion.compute("long", 100, 5, [], "15m", _iso(0), _iso(120))
    assert ex.mfe_r is None
    assert ex.provenance["reason"] == "no_bars_in_window"


def test_zero_and_invalid_initial_risk_are_unknown():
    for bad in (0, 0.0, -1, None, float("nan"), "x"):
        ex = excursion.compute("long", 100, bad, _bars(8, [110], [95]), "15m",
                               _iso(0), _iso(120))
        assert ex.mfe_r is None and ex.mae_r is None, bad
        assert ex.provenance["reason"] == "invalid_initial_risk", bad


def test_unknown_timeframe_and_side_are_unknown():
    assert excursion.compute("long", 100, 5, _bars(8, [110], [95]), "7m",
                             _iso(0), _iso(120)).provenance["reason"] \
        == "unknown_timeframe"
    assert excursion.compute("sideways", 100, 5, _bars(8, [110], [95]), "15m",
                             _iso(0), _iso(120)).provenance["reason"] \
        == "unknown_side:sideways"


def test_hold_shorter_than_one_bar_is_unknown():
    ex = excursion.compute("long", 100, 5, _bars(1, [110], [95]), "15m",
                           _iso(0), _iso(5))
    assert ex.mfe_r is None
    assert ex.provenance["reason"] in ("hold_shorter_than_one_bar",
                                       "no_bars_in_window")


# ── persistence, provenance, idempotency ─────────────────────────────────

def _journal_with_closed_trade(tmp_path, side="long", entry=100.0, risk=5.0):
    j = Journal(tmp_path / "j.db")
    p = Position(id="t1", symbol="X/USDT",
                 side=Side.LONG if side == "long" else Side.SHORT,
                 amount=1, entry_price=entry, notional_usdt=100,
                 strategy_id="spec_x", market_type="futures")
    j.add_trade(p)
    j.query("UPDATE trades SET initial_risk=?, opened_at=? WHERE id='t1'",
            (risk, _iso(0)))
    j.close_trade("t1", entry, -1.0, "sl_fill", closed_at=_iso(120))
    return j


def _bars_for(symbol, tf):
    return _bars(8, [110], [95])


def test_record_persists_values_and_provenance(tmp_path):
    j = _journal_with_closed_trade(tmp_path)
    assert excursion.record(j, j.query("SELECT * FROM trades")[0],
                            _bars_for, lambda sid: "4h" if False else "15m")
    row = j.query("SELECT * FROM trades WHERE id='t1'")[0]
    assert row["mfe_r"] == 2.0 and row["mae_r"] == -1.0
    prov = json.loads(row["excursion_json"])
    assert prov["status"] == "measured"
    assert prov["timeframe"] == "15m" and prov["timeframe_source"] == "spec"
    assert prov["bars"] == 8 and prov["expected_bars"] == 8
    assert prov["coverage"] == 1.0
    assert prov["first_bar_ms"] and prov["last_bar_ms"]
    assert prov["basis"] == "intrabar_high_low"
    assert prov["entry_price"] == 100 and prov["initial_risk"] == 5


def test_unknown_still_writes_provenance(tmp_path):
    j = _journal_with_closed_trade(tmp_path)
    excursion.record(j, j.query("SELECT * FROM trades")[0],
                     lambda s, tf: [], lambda sid: "15m")
    row = j.query("SELECT * FROM trades WHERE id='t1'")[0]
    assert row["mfe_r"] is None and row["mae_r"] is None
    assert json.loads(row["excursion_json"])["status"] == "unknown"


def test_missing_spec_records_fallback_frame(tmp_path):
    """No spec owns the trade — say which frame was used, don't pretend."""
    j = _journal_with_closed_trade(tmp_path)
    excursion.record(j, j.query("SELECT * FROM trades")[0], _bars_for,
                     lambda sid: None)
    prov = json.loads(j.query("SELECT * FROM trades")[0]["excursion_json"])
    assert prov["timeframe_source"] == "execution_fallback"


def test_idempotent_on_replay(tmp_path):
    j = _journal_with_closed_trade(tmp_path)
    t = j.query("SELECT * FROM trades")[0]
    assert excursion.record(j, t, _bars_for, lambda sid: "15m")
    first = j.query("SELECT * FROM trades WHERE id='t1'")[0]

    for _ in range(3):      # replay, including with DIFFERENT candles
        fresh = j.query("SELECT * FROM trades WHERE id='t1'")[0]
        assert not excursion.record(j, fresh, lambda s, tf: _bars(8, [999], [1]),
                                    lambda sid: "15m")
    assert j.query("SELECT * FROM trades WHERE id='t1'")[0] == first


def test_open_trades_are_never_measured(tmp_path):
    j = Journal(tmp_path / "j.db")
    j.add_trade(Position(id="o1", symbol="X/USDT", side=Side.LONG, amount=1,
                         entry_price=100, notional_usdt=100,
                         strategy_id="s", market_type="futures"))
    assert not excursion.record(j, j.query("SELECT * FROM trades")[0],
                                _bars_for, lambda sid: "15m")
    assert j.query("SELECT * FROM trades")[0]["excursion_json"] is None


# ── the sweep does not reach behind its epoch ────────────────────────────

def test_sweep_first_run_only_sets_the_epoch(tmp_path):
    j = _journal_with_closed_trade(tmp_path)
    assert excursion.sweep(j, _bars_for, lambda sid: "15m") == 0
    assert j.kv_get(excursion.EPOCH_KEY)
    assert j.query("SELECT * FROM trades")[0]["excursion_json"] is None


def test_sweep_leaves_pre_epoch_trades_untouched(tmp_path):
    j = _journal_with_closed_trade(tmp_path)
    excursion.sweep(j, _bars_for, lambda sid: "15m")        # sets epoch = now
    before = j.query("SELECT * FROM trades WHERE id='t1'")[0]
    assert excursion.sweep(j, _bars_for, lambda sid: "15m") == 0
    assert j.query("SELECT * FROM trades WHERE id='t1'")[0] == before


def test_sweep_measures_trades_closed_after_the_epoch(tmp_path):
    j = _journal_with_closed_trade(tmp_path)
    excursion.sweep(j, _bars_for, lambda sid: "15m")        # epoch = now

    # a trade whose whole hold sits after the epoch: 2h on the 15m grid
    now = datetime.now(timezone.utc)
    grid = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    start = grid - timedelta(hours=2)        # 8 full 15m bars, then close NOW
    j.query("UPDATE trades SET opened_at=?, closed_at=? WHERE id='t1'",
            (start.isoformat(), now.isoformat()))
    recent = _bars(8, [110], [95], start=start)

    assert excursion.sweep(j, lambda s, tf: recent, lambda sid: "15m") == 1
    row = j.query("SELECT * FROM trades WHERE id='t1'")[0]
    assert row["mfe_r"] == 2.0 and row["mae_r"] == -1.0
    assert json.loads(row["excursion_json"])["status"] == "measured"


def test_backfill_is_the_only_way_into_history(tmp_path):
    j = _journal_with_closed_trade(tmp_path)
    excursion.sweep(j, _bars_for, lambda sid: "15m")
    assert j.query("SELECT * FROM trades")[0]["excursion_json"] is None
    rep = excursion.backfill(j, _bars_for, lambda sid: "15m")
    assert rep == {"considered": 1, "written": 1, "measured": 1,
                   "unknown": 0, "errors": 0}
    assert j.query("SELECT * FROM trades")[0]["mfe_r"] == 2.0


# ── no trading behaviour change ──────────────────────────────────────────

def test_nothing_but_the_three_columns_is_written(tmp_path):
    j = _journal_with_closed_trade(tmp_path)
    before = j.query("SELECT * FROM trades WHERE id='t1'")[0]
    excursion.record(j, before, _bars_for, lambda sid: "15m")
    after = j.query("SELECT * FROM trades WHERE id='t1'")[0]
    new = {"mfe_r", "mae_r", "excursion_json"}
    assert {k for k in after if after[k] != before[k]} == new
    for k in ("stop_loss", "take_profit", "amount", "initial_risk",
              "realized_pnl", "status", "close_reason", "exit_price",
              "tp1_done", "sl_order_id"):
        assert after[k] == before[k], k


def test_recording_never_raises_into_the_caller(tmp_path):
    """A broken candle source degrades to unknown; it does not propagate."""
    j = _journal_with_closed_trade(tmp_path)
    def boom(symbol, tf):
        raise RuntimeError("store unavailable")
    excursion.record(j, j.query("SELECT * FROM trades")[0], boom,
                     lambda sid: "15m")
    row = j.query("SELECT * FROM trades WHERE id='t1'")[0]
    assert row["mfe_r"] is None
    assert json.loads(row["excursion_json"])["status"] == "unknown"


def test_exit_bar_exclusion_is_declared_and_exit_r_reported():
    """The PIT rule drops the exit bar; say so, and show the gap.

    For a stop-filled trade the fatal bar straddles `closed_at`, so mae_r is
    a lower bound. Provenance must make that inspectable rather than leave a
    reader wondering why mae_r is less adverse than the exit.
    """
    ex = excursion.compute("long", 100, 5, _bars(8, [103], [97]), "15m",
                           _iso(0), _iso(120), exit_price=94.0)
    assert ex.provenance["partial_edge_bars_excluded"] is True
    assert ex.provenance["exit_r"] == (94 - 100) / 5        # -1.2R
    assert ex.mae_r == -0.6                                 # bars only
    assert ex.mae_r > ex.provenance["exit_r"]               # the gap is visible


def test_exit_r_is_direction_aware():
    ex = excursion.compute("short", 100, 5, _bars(8, [103], [97]), "15m",
                           _iso(0), _iso(120), exit_price=106.0)
    assert ex.provenance["exit_r"] == (100 - 106) / 5


def test_exit_r_absent_when_unavailable():
    for bad in (None, 0, -1, "x"):
        ex = excursion.compute("long", 100, 5, _bars(8, [103], [97]), "15m",
                               _iso(0), _iso(120), exit_price=bad)
        assert "exit_r" not in ex.provenance, bad
