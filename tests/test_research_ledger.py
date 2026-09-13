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


def test_the_ledger_migrates_a_gauges_table_created_before_min_max(tmp_path):
    """The live database already held `research_gauges` rows written before
    `min`/`max` existed. `CREATE TABLE IF NOT EXISTS` cannot widen that
    table, so `ensure()` must ALTER it rather than silently keep serving the
    old shape."""
    j = Journal(tmp_path / "old.db")
    with j._tx() as c:
        c.executescript(
            "CREATE TABLE research_gauges (tf TEXT, expr TEXT, p10 REAL, "
            "p25 REAL, p75 REAL, p90 REAL, n INTEGER, finite_frac REAL, "
            "usable INTEGER, measured_at TEXT, PRIMARY KEY (tf, expr));"
            "CREATE TABLE research_controls (tf TEXT, window TEXT, "
            "consistency_p REAL, powered INTEGER, detail TEXT, "
            "measured_at TEXT, PRIMARY KEY (tf, window));")
        c.execute(
            "INSERT INTO research_gauges (tf, expr, p10, p90, n, "
            "finite_frac, usable, measured_at) VALUES "
            "('4h', 'ret(24)', -0.1, 0.1, 90000, 0.99, 1, 'x')")
        c.execute(
            "INSERT INTO research_controls (tf, window, consistency_p, "
            "powered, detail, measured_at) VALUES "
            "('4h', 'ohlcv', 0.001, 1, '{}', 'x')")
    lg = Ledger(j)
    lg.ensure()                               # must not raise
    g = lg.gauges("4h")
    assert g["ret(24)"]["p90"] == 0.1
    assert g["ret(24)"]["min"] is None        # pre-existing row, unmeasured
    c = lg.control("4h", "ohlcv")
    assert c["powered"] is True and c["status"] == "measured"
    # the new columns are now writable too
    lg.record_gauges("4h", {"ret(24)": {"p10": -0.2, "p90": 0.2,
                                        "min": -3.0, "max": 3.0, "n": 1,
                                        "finite_frac": 1.0, "usable": True}})
    assert lg.gauges("4h")["ret(24)"]["min"] == -3.0


def test_a_control_carries_a_status_distinct_from_powered(led):
    led.record_control("4h", "ohlcv", {"consistency_p": None}, False,
                       status="untestable")
    c = led.control("4h", "ohlcv")
    assert c["status"] == "untestable" and c["powered"] is False


def test_tied_rows_break_the_tie_by_hash_so_the_order_is_deterministic(led):
    """`_grow_children` takes `parents[:beam]` off this ordering; two rows
    tied on both sort keys must still order the SAME way every time, or the
    beam — and the search path it drives — is not reproducible."""
    led.record_result(_res(h="bbbb", p=0.01, pct=10.0), "grow", "", {})
    led.record_result(_res(h="aaaa", p=0.01, pct=10.0), "grow", "", {})
    rows = led.rows("4h", "trail")
    assert [r["hash"] for r in rows] == ["aaaa", "bbbb"]


def test_measure_may_be_refused_until_the_horizon_is_archived(led):
    led.record_result(_res(h="a"), "grow", "", {})
    led.record_control("4h", "ohlcv", {"consistency_p": 0.001}, True)
    assert led.combos_exist("4h") == 1
    moved = led.archive_horizon("4h", "2026-09-13T00:00:00+00:00")
    assert moved == {"combos": 1, "controls": 1}
    assert led.combos_exist("4h") == 0
    assert led.control("4h", "ohlcv") is None
    archived = led.journal.query(
        "SELECT * FROM research_archive WHERE tf='4h'")
    assert {r["table_name"] for r in archived} == \
        {"research_combos", "research_controls"}


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
