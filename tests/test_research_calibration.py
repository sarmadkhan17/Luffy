"""Does the search find an edge that IS there, and refuse one that is not?

A search that returns "nothing works" is a claim about the tool first. Four
independent faults produced that verdict in screen_mechanisms for its whole
life, each failing silently and each in the same direction. So the pipeline
is run against two known answers:

KNOWN-GOOD — candles built with a real continuation edge (price trends after
a breakout). The breakout part must score, beat its own rotation, and a part
that adds nothing must fail to earn its place beside it.

KNOWN-BAD — random-walk candles. Essentially nothing may survive: with a
measured false-positive rate of 0.1-0.4% for consistency_p, a couple of dozen
combinations should yield no more than one at p <= 0.01, and the rest must
read as no-edge rather than as an opportunity.
"""
import sqlite3

import numpy as np
import pandas as pd
import pytest

from trader.research import growth, job
from trader.research.combo import Combination
from trader.research.vocab import Part

TF_MS = 14_400_000
BREAK = Part("ev:donch20", "event", "ev:donch20",
             "close > donchian_hi(20)", "close < donchian_lo(20)")
TREND = Part("st:above_ema50", "state", "st:above_ema50",
             "close > ema(50)", "close < ema(50)")
# an irrelevant condition: true on about half the bars, by construction
COIN = Part("dow>p50", "gauge", "dow", "dow() > 3", "dow() > 3")


def _store(tmp_path, kind, n=1100, symbols=6):
    db = tmp_path / f"{kind}.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE candles (symbol TEXT NOT NULL, tf TEXT NOT NULL, "
        "ts INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, "
        "volume REAL, taker_buy REAL, PRIMARY KEY (symbol, tf, ts))")
    for i in range(symbols):
        rng = np.random.default_rng(100 + i)
        px, rows = 100.0, []
        for b in range(n):
            if kind == "good":
                drift = 0.007 if (b // 60) % 2 == 0 else -0.001
            else:
                drift = 0.0
            px *= 1.0 + rng.normal(drift, 0.009)
            rows.append((f"S{i}/USDT", "4h", b * TF_MS, px, px * 1.004,
                         px * 0.996, px, 10.0, 5.0))
        con.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)",
                        rows)
    con.commit()
    con.close()
    return db


def _run(db, combos, symbols=6, draws=25):
    payload = {
        "tf": "4h", "geo": "trail",
        "symbols": [f"S{i}/USDT" for i in range(symbols)],
        "heldout_symbols": [], "requires": ["ohlcv"],
        "cfg": {"risk": {"risk_per_trade_pct": 0.5, "taker_fee_pct": 0.04,
                         "slippage_atr_frac": 0.015,
                         "funding_rate_8h": 0.0001, "real_funding": False,
                         "max_open_trades": 8},
                "research": {}},
        "paths": {"candles": str(db), "derivs": str(db)},
        "combos": [c.as_dict() for c in combos],
        "draws": draws, "seed": 11, "soft_deadline_s": 600.0}
    out = job.evaluate_job(payload)
    return {r["hash"]: r for r in out["results"]}


def test_known_good_the_planted_edge_is_found(tmp_path):
    db = _store(tmp_path, "good")
    c = Combination((BREAK,), "4h", "trail")
    r = _run(db, [c])[c.hash]
    assert r["verdict"] == "scored", r
    assert r["median_pf"] > 1.0
    assert r["consistency_p"] is not None and r["consistency_p"] < 0.05
    assert r["portfolio"]["total_pct"] > 0


def test_known_good_a_part_that_adds_nothing_does_not_earn_its_place(tmp_path):
    db = _store(tmp_path, "good")
    base = Combination((BREAK,), "4h", "trail")
    plus = Combination((BREAK, COIN), "4h", "trail", parent=base.hash)
    res = _run(db, [base, plus])
    ok, why = growth.earns_place(res[plus.hash], res[base.hash])
    assert not ok, why


def test_known_bad_a_random_walk_yields_no_edge(tmp_path):
    """Twenty-four rules on pure noise. consistency_p's measured
    false-positive rate is 0.1-0.4%, so one survivor is bad luck and three
    would be a broken gate."""
    db = _store(tmp_path, "bad")
    combos = []
    for n in (10, 20, 30, 40, 50, 60):
        for op, key in ((">", "hi"), ("<", "lo")):
            p = Part(f"ev:d{n}{key}", "event", f"ev:d{n}{key}",
                     f"close {op} donchian_{'hi' if op == '>' else 'lo'}({n})",
                     f"close {'<' if op == '>' else '>'} "
                     f"donchian_{'lo' if op == '>' else 'hi'}({n})")
            combos.append(Combination((p,), "4h", "trail"))
            combos.append(Combination((p, TREND), "4h", "trail"))
    res = _run(db, combos, draws=25)
    scored = [r for r in res.values() if r["verdict"] == "scored"]
    assert scored, "nothing was scoreable at all — that is a harness fault"
    hits = [r for r in scored
            if r["consistency_p"] is not None and r["consistency_p"] <= 0.01]
    assert len(hits) <= 1, (
        f"{len(hits)} of {len(scored)} noise rules cleared p<=0.01: "
        f"{[ (r['parts'], r['consistency_p']) for r in hits ]}")


def test_known_bad_the_median_noise_rule_sits_at_the_coin_flip(tmp_path):
    db = _store(tmp_path, "bad")
    combos = [Combination((Part(f"ev:d{n}", "event", f"ev:d{n}",
                                f"close > donchian_hi({n})",
                                f"close < donchian_lo({n})"),),
                          "4h", "trail")
              for n in (20, 30, 40, 50)]
    res = _run(db, combos, draws=25)
    import statistics as st
    pcts = [v["null_pctile"] for r in res.values()
            for v in r["symbols"].values() if v.get("null_pctile") is not None]
    assert pcts, "no percentiles at all"
    assert 0.2 < st.median(pcts) < 0.8, st.median(pcts)
