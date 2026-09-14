"""The referee: held-out slices, gate 1, gate 3, and who gets a look."""
import sqlite3

import numpy as np
import pandas as pd
import pytest

from trader.research import portfolio_null as pn
from trader.research import referee as rf
from trader.strategy.portfolio_evidence import Fill
from trader.strategy.vector_backtest import WARMUP

TF_MS = 14_400_000
BARS = 1600
CUT_BAR = 1000
CFG = {"risk": {"risk_per_trade_pct": 0.5, "taker_fee_pct": 0.04,
                "slippage_atr_frac": 0.015, "bar_minutes": 240,
                "real_funding": False}}


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "candles.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE candles (symbol TEXT NOT NULL, tf TEXT NOT NULL, "
        "ts INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, "
        "volume REAL, taker_buy REAL, PRIMARY KEY (symbol, tf, ts))")
    rng = np.random.default_rng(3)
    for sym, start in (("A1/USDT", 0), ("A2/USDT", 0), ("D1/USDT", 0),
                       ("LATE/USDT", CUT_BAR + 50)):
        px, rows = 100.0, []
        for b in range(start, BARS):
            px *= 1.0 + rng.normal(0, 0.008)
            rows.append((sym, "4h", b * TF_MS, px, px * 1.004, px * 0.996,
                         px, 10.0, 5.0))
        con.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)",
                        rows)
    con.commit()
    con.close()
    return str(db)


def _ms(df):
    return pd.to_datetime(df["ts"], utc=True).astype("datetime64[ms, UTC]") \
        .astype("int64").to_numpy()


def test_held_out_a_is_only_bars_before_the_cut(store):
    cut = CUT_BAR * TF_MS
    b = rf.load_heldout("4h", "a", ["A1/USDT", "A2/USDT", "LATE/USDT"], cut,
                        CFG, paths={"candles": store})
    assert set(b.frames) == {"A1/USDT", "A2/USDT"}      # LATE has no A era
    for df in b.frames.values():
        assert _ms(df).max() < cut
    assert all(v == 0 for v in b.first_bars.values())


def test_held_out_b_trades_from_the_cut_with_warmup_from_before_it(store):
    cut = CUT_BAR * TF_MS
    b = rf.load_heldout("4h", "b", ["A1/USDT", "D1/USDT", "LATE/USDT"], cut,
                        CFG, paths={"candles": store})
    # LATE listed after the cut: no context before it, so no B reading
    assert set(b.frames) == {"A1/USDT", "D1/USDT"}
    for sym, df in b.frames.items():
        first = b.first_bars[sym]
        assert first >= WARMUP
        assert _ms(df)[first] == cut
        assert _ms(df)[first - 1] < cut


def test_gate1_spends_one_p_the_worst_of_three():
    a = {"trades": 200, "consistency_p": 1e-6, "consistency_p_dep": 0.001,
         "scored_symbols": 10}
    b = {"trades": 200, "consistency_p": 1e-6, "consistency_p_dep": 0.002,
         "scored_symbols": 12}
    g = rf.gate1(a, b, {"p": 0.004})
    assert g["p"] == 0.004 and g["worst"] == "B common rotation"
    g = rf.gate1(a, b, {"p": 0.02})
    assert g["p"] == 0.02 and "B common rotation" in g["reason"]


def test_gate1_charges_the_dependence_corrected_consistency():
    """Raw consistency reads 9e-05 for a shared-market effect that is worth
    four symbols, not fifteen; the gate must charge the corrected p."""
    a = {"trades": 200, "consistency_p": 9e-05, "consistency_p_dep": 0.001,
         "scored_symbols": 15}
    b = {"trades": 540, "consistency_p": 9e-05, "consistency_p_dep": 0.24,
         "scored_symbols": 15}
    g = rf.gate1(a, b, {"p": 0.049})
    assert g["p"] == 0.24 and g["worst"] == "B consistency"
    b.pop("consistency_p_dep")
    assert rf.gate1(a, b, {"p": 0.049})["reason"].startswith("untestable")


def test_gate1_untestable_is_charged_as_p_one():
    ok = {"trades": 200, "consistency_p": 0.001, "scored_symbols": 10}
    thin = {"trades": 12, "consistency_p": 0.001, "scored_symbols": 10}
    silent = {"trades": 200, "consistency_p": None, "scored_symbols": 2}
    assert rf.gate1(ok, thin, {"p": 0.001})["p"] == 1.0
    assert "untestable" in rf.gate1(silent, ok, {"p": 0.001})["reason"]
    assert rf.gate1(ok, ok, {"p": None})["p"] == 1.0


def _fills(sym, starts, r):
    return [Fill(entry_i=s, exit_i=s + 10, r_multiple=r, symbol=sym)
            for s in starts]


def test_gate3_refuses_a_duplicate_of_the_book():
    book = _fills("X", range(0, 400, 20), 0.5)
    g = rf.gate3(list(book), book, 2000.0, 0.5, 1)   # one slot: all refused
    assert not g["passed"]


def test_gate3_accepts_uncorrelated_positive_fills():
    book = _fills("X", range(0, 400, 20), 0.5)
    cand = _fills("Y", range(15, 400, 20), 0.5)
    g = rf.gate3(cand, book, 2000.0, 0.5, 8)
    assert g["passed"], g


def test_gate3_refuses_return_bought_with_a_worse_drawdown_ratio():
    book = _fills("X", range(0, 400, 20), 0.4)
    # the candidate wins big early, then gives back a long losing streak
    cand = _fills("Y", range(5, 100, 20), 3.0) + \
        _fills("Y", range(105, 400, 20), -0.9)
    g = rf.gate3(cand, book, 2000.0, 0.5, 8)
    assert g["with"]["total_pct"] > g["without"]["total_pct"] or \
        not g["passed"]
    if g["with"]["total_pct"] > g["without"]["total_pct"]:
        assert not g["passed"] and "drawdown" in g["reason"]


def _leg(sym, idx, n=50):
    ts = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    a = np.zeros(n, bool)
    a[list(idx)] = True
    return pn.Leg(sym, a, np.zeros(n, bool), pd.DataFrame({"ts": ts}), None)


def test_twins_are_not_looked_at():
    base = rf.entry_set([_leg("S", range(0, 40, 2))])
    rows = [{"hash": f"h{i}", "entries": set(base)} for i in range(10)]
    queued, twins = rf.pick(rows, {}, m=5)
    assert queued == ["h0"]
    assert len(twins) == 9 and all(t[1] == "h0" for t in twins)


def test_a_previous_look_makes_a_new_candidate_a_twin():
    old = rf.entry_set([_leg("S", range(0, 40, 2))])
    new = rf.entry_set([_leg("S", range(1, 40, 2))])      # disjoint
    queued, twins = rf.pick([{"hash": "n", "entries": new},
                             {"hash": "o2", "entries": set(old)}],
                            {"o": old}, m=5)
    assert queued == ["n"] and twins == [("o2", "o")]


def test_entries_round_trip():
    ent = rf.entry_set([_leg("S", [1, 5]), _leg("T", [2])])
    assert rf.decode_entries(rf.encode_entries(ent)) == ent


def test_examine_runs_end_to_end_on_a_real_store(store):
    """No mocks: load both held-out slices, both gates, and the book."""
    from trader.research.combo import Combination
    from trader.research.vocab import Part
    donch = Part("ev:donch20", "event", "ev:donch20",
                 "close > donchian_hi(20)", "close < donchian_lo(20)")
    c = Combination((donch,), "4h", "trail")
    out = rf.examine(c, {
        "cfg": {**CFG, "research": {"equity": 2000.0}},
        "paths": {"candles": store}, "cut_ms": CUT_BAR * TF_MS,
        "discovery_symbols": ["D1/USDT"],
        "heldout_symbols": ["A1/USDT", "A2/USDT", "LATE/USDT"],
        "draws": 19, "seed": 3, "book": []})
    assert out["looked"] is True
    assert out["cut_ms"] >= CUT_BAR * TF_MS
    assert "p" in out["gate1"] and out["gate1"]["p"] == 1.0   # 2-3 symbols
    assert "untestable" in out["gate1"]["reason"]
    assert set(out["gate3"]) >= {"passed", "reason", "with", "without"}
