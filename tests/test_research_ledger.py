"""The ledger is what keeps the search honest.

Every combination it ever evaluated is recorded under its canonical hash,
kept or not, so: a restart resumes instead of re-testing; the same set
reached by two growth paths counts as ONE look; and phase 3's error budget
can price the looks because the count of them is true.

It is written by the kernel thread through Journal._tx(). NEVER through
Journal.query(): that runs on a thread-local connection with no transaction
wrapper, so an INSERT through it stays uncommitted — invisible to other
connections and holding a write lock — until some later _tx() on the same
thread happens to commit it. That is exactly how an unvalidated strategy got
into the book on 2026-09-11.
"""
import json

import pytest

from trader.core.journal import Journal
from trader.research.ledger import Ledger


@pytest.fixture
def led(tmp_path):
    j = Journal(tmp_path / "j.db")
    lg = Ledger(j)
    lg.ensure()
    return lg


def _res(h="abc123", tf="4h", geo="trail", k=1, p=0.01, pct=12.5,
         verdict="scored"):
    return {"hash": h, "tf": tf, "geo": geo, "k": k, "round": "singles",
            "parent": "", "trigger": "", "parts": ["ev:donch100"],
            "window": "ohlcv", "entry_long": "close > donchian_hi(100)",
            "entry_short": "close < donchian_lo(100)",
            "symbols": {"BTC/USDT": {"trades": 30, "pf": 1.4,
                                     "null_pctile": 0.9}},
            "trades": 300, "scored_symbols": 8, "consistency_p": p,
            "median_pf": 1.4, "median_rate": 0.01,
            "portfolio": {"total_pct": pct, "max_dd_pct": 20.0, "taken": 100,
                          "order": []},
            "projection": {"markets_a": 6, "markets_b": 6},
            "testable": True, "verdict": verdict}


def test_the_tables_are_created_once_and_again_is_harmless(led):
    led.ensure()
    names = {r["name"] for r in led.journal.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("research_combos", "research_gauges", "research_controls",
              "research_slices", "research_batches"):
        assert t in names


def test_a_result_round_trips(led):
    led.record_result(_res(), "grow", "carries information", {})
    assert led.has("abc123")
    back = led.result("abc123")
    assert back["consistency_p"] == 0.01
    assert back["portfolio"]["total_pct"] == 12.5


def test_recording_the_same_hash_twice_keeps_one_row(led):
    led.record_result(_res(), "grow", "first", {})
    led.record_result(_res(p=0.02), "prune", "second", {})
    rows = led.rows("4h", "trail")
    assert len(rows) == 1
    assert rows[0]["verdict"] == "prune"


def test_rows_filter_by_level_and_verdict(led):
    led.record_result(_res(h="a", k=1), "grow", "", {})
    led.record_result(_res(h="b", k=2), "grow", "", {})
    led.record_result(_res(h="c", k=2), "prune", "", {})
    assert {r["hash"] for r in led.rows("4h", "trail", k=2)} == {"b", "c"}
    assert {r["hash"] for r in led.rows("4h", "trail", k=2,
                                        verdict="grow")} == {"b"}


def test_known_hashes_are_scoped_to_the_horizon_and_geometry(led):
    led.record_result(_res(h="a", tf="4h", geo="trail"), "grow", "", {})
    led.record_result(_res(h="b", tf="1h", geo="trail"), "grow", "", {})
    assert led.known("4h", "trail") == {"a"}
    assert led.known("1h", "trail") == {"b"}


def test_the_ablation_is_stored_with_the_row(led):
    abl = {"ev:donch100": {"p": 0.4, "total_pct": 3.0, "drop_p": -0.39,
                           "drop_pct": 9.5, "verdict": "scored"}}
    led.record_result(_res(k=2), "survivor", "every part earns its place",
                      abl)
    row = led.rows("4h", "trail")[0]
    assert json.loads(row["ablation"])["ev:donch100"]["drop_pct"] == 9.5


def test_gauges_round_trip_and_replace(led):
    led.record_gauges("4h", {"ret(24)": {"p10": -0.1, "p25": -0.02,
                                         "p75": 0.02, "p90": 0.1,
                                         "n": 90000, "finite_frac": 0.99,
                                         "usable": True}})
    g = led.gauges("4h")
    assert g["ret(24)"]["p90"] == 0.1 and g["ret(24)"]["usable"] is True
    led.record_gauges("4h", {"ret(24)": {"p10": -0.2, "p25": -0.03,
                                         "p75": 0.03, "p90": 0.2,
                                         "n": 1, "finite_frac": 0.0,
                                         "usable": False}})
    assert led.gauges("4h")["ret(24)"]["usable"] is False
    assert len(led.gauges("4h")) == 1


def test_a_control_records_whether_the_window_has_power(led):
    led.record_control("4h", "open_interest",
                       {"consistency_p": 0.25, "verdict": "scored"}, False)
    c = led.control("4h", "open_interest")
    assert c["powered"] is False and c["consistency_p"] == 0.25
    assert led.control("4h", "ohlcv") is None


def test_slices_record_the_cut_and_the_bar_counts(led):
    led.record_slices("4h", 1_700_000_000_000,
                      {"discovery": {"BTC/USDT": 7700},
                       "heldout_a": {"UNI/USDT": 11000},
                       "heldout_b": {"BTC/USDT": 3300}})
    s = led.slices("4h")
    assert s["cut_ms"] == 1_700_000_000_000
    assert s["heldout_b"]["BTC/USDT"] == 3300


def test_a_batch_records_its_outcome_even_when_it_failed(led):
    bid = led.start_batch("4h", "trail", "singles", 40)
    led.finish_batch(bid, ok=False, elapsed_s=612.0,
                     error="timed out after 600s")
    row = led.journal.query(
        "SELECT * FROM research_batches WHERE id=?", (bid,))[0]
    assert row["ok"] == 0 and "timed out" in row["error"]


def test_counts_summarise_the_search(led):
    led.record_result(_res(h="a", k=1), "grow", "", {})
    led.record_result(_res(h="b", k=2), "survivor", "", {})
    led.record_result(_res(h="c", k=2, verdict="empty"), "prune", "", {})
    c = led.counts()
    assert c["combos"] == 3
    assert c["by_verdict"]["survivor"] == 1
    assert c["by_tf"]["4h"] == 3


def test_the_ledger_never_writes_the_strategies_table():
    """Phase 2 proposes nothing. The handoff into _mechanism_once is phase 3,
    and there is exactly ONE admission gate."""
    import inspect

    from trader.research import ledger
    src = inspect.getsource(ledger)
    assert "strategies" not in src
