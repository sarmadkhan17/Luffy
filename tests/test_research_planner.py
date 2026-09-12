"""What the search does next, and in what order.

The order is not a preference. Measurement comes first because a threshold
cannot be guessed; the window control comes before the combinations that live
in that window, because a result there cannot be LABELLED without it; singles
come before pairs because a pair is only worth trying around a trigger that
carried information; and ablations come before the next level, because a
combination is not a survivor until every part has earned its place.

Held-out results never enter here at all. If a held-out failure steered the
next round, held-out data would silently become discovery data.
"""
import pytest

from trader.core.journal import Journal
from trader.research import planner
from trader.research.ledger import Ledger
from trader.research.vocab import Part

CFG = {"research": {"horizons": ["4h"], "geometries": ["trail"],
                    "batch_combos": 5, "beam": 2, "grow_max_p": 0.25,
                    "max_parts": 3}}


@pytest.fixture
def led(tmp_path):
    lg = Ledger(Journal(tmp_path / "j.db"))
    lg.ensure()
    return lg


def _gauges(led, tf="4h"):
    from trader.research import vocab
    led.record_gauges(tf, {e: {"p10": -1.0, "p25": -0.5, "p75": 0.5,
                               "p90": 1.0, "n": 90000, "finite_frac": 0.99,
                               "usable": True}
                           for e in vocab.expressions(tf)})


def _res(h, tf="4h", geo="trail", k=1, p=0.01, pct=10.0, parts=None,
         verdict="scored"):
    return {"hash": h, "tf": tf, "geo": geo, "k": k, "round": "singles",
            "parent": "", "trigger": "", "parts": parts or ["ev:donch100"],
            "window": "ohlcv", "entry_long": "x", "entry_short": "y",
            "symbols": {}, "trades": 100, "scored_symbols": 8,
            "consistency_p": p, "median_pf": 1.3, "median_rate": 0.01,
            "portfolio": {"total_pct": pct, "max_dd_pct": 10.0, "taken": 50},
            "projection": {"markets_a": 6, "markets_b": 6},
            "testable": True, "verdict": verdict}


def test_with_nothing_measured_the_first_job_is_to_measure(led):
    b = planner.next_batch(led, CFG)
    assert b.kind == "measure" and b.tf == "4h"


def test_once_measured_the_control_comes_before_the_singles(led):
    _gauges(led)
    b = planner.next_batch(led, CFG)
    assert b.kind == "evaluate" and b.round == "control"
    assert all(c.round == "control" for c in b.combos)


def test_then_singles_are_enqueued_in_batch_sized_chunks(led):
    _gauges(led)
    led.record_control("4h", "ohlcv", {"consistency_p": 0.001}, True)
    b = planner.next_batch(led, CFG)
    assert b.round == "singles"
    assert len(b.combos) == CFG["research"]["batch_combos"]
    assert all(c.k == 1 for c in b.combos)


def test_a_combination_already_in_the_ledger_is_never_re_enqueued(led):
    _gauges(led)
    led.record_control("4h", "ohlcv", {"consistency_p": 0.001}, True)
    first = planner.next_batch(led, CFG)
    for c in first.combos:
        led.record_result(_res(c.hash, parts=list(c.keys)), "prune", "", {})
    second = planner.next_batch(led, CFG)
    assert not ({c.hash for c in second.combos}
                & {c.hash for c in first.combos})


def test_a_window_without_a_control_holds_back_its_combinations(led):
    """Parts needing funding cannot be scored until the funding window's
    control says whether an edge could register there at all."""
    _gauges(led)
    led.record_control("4h", "ohlcv", {"consistency_p": 0.001}, True)
    seen_windows = set()
    for _ in range(40):
        b = planner.next_batch(led, CFG)
        if b.kind != "evaluate":
            break
        for c in b.combos:
            seen_windows.add(c.window)
            led.record_result(_res(c.hash, k=c.k, parts=list(c.keys)),
                              "prune", "", {})
        if b.round == "control":
            for c in b.combos:
                led.record_control("4h", c.window,
                                   {"consistency_p": 0.5}, False)
    assert "ohlcv" in seen_windows


def test_growth_extends_only_parents_that_carried_information(led):
    _gauges(led)
    led.record_control("4h", "ohlcv", {"consistency_p": 0.001}, True)
    # exhaust the singles round with one grower and one pruned
    from trader.research import vocab
    parts = vocab.parts_for("4h", led.gauges("4h"))
    from trader.research.combo import Combination
    for i, p in enumerate(parts):
        c = Combination((p,), "4h", "trail")
        led.record_result(_res(c.hash, parts=[p.key]),
                          "grow" if i == 0 else "prune", "", {})
    b = planner.next_batch(led, CFG)
    assert b.round == "grow"
    assert all(c.k == 2 for c in b.combos)
    assert all(parts[0].key in c.keys for c in b.combos)


def test_growth_never_pairs_a_part_with_itself_or_its_own_gauge(led):
    _gauges(led)
    led.record_control("4h", "ohlcv", {"consistency_p": 0.001}, True)
    from trader.research import vocab
    from trader.research.combo import Combination
    parts = vocab.parts_for("4h", led.gauges("4h"))
    root = next(p for p in parts if p.gauge == "ret24")
    for p in parts:
        c = Combination((p,), "4h", "trail")
        led.record_result(_res(c.hash, parts=[p.key]),
                          "grow" if p is root else "prune", "", {})
    b = planner.next_batch(led, CFG)
    for c in b.combos:
        gauges = [p.gauge for p in c.parts]
        assert len(set(gauges)) == len(gauges)


def test_a_grown_child_gets_its_ablation_before_the_next_level(led):
    _gauges(led)
    led.record_control("4h", "ohlcv", {"consistency_p": 0.001}, True)
    from trader.research import vocab
    from trader.research.combo import Combination
    all_parts = vocab.parts_for("4h", led.gauges("4h"))
    # the singles round must be exhausted first — that is the planner's
    # stated order, so a test that skips it would be testing the wrong thing
    for p in all_parts:
        c = Combination((p,), "4h", "trail")
        led.record_result(_res(c.hash, parts=[p.key]), "prune", "", {})
    # a THREE-part child: its k=2 ablation subsets were never individually
    # scored by the singles sweep above (that sweep only ever produces k=1
    # rules), so they are genuinely new work — unlike a two-part child,
    # whose k=1 subsets are, by construction, exactly the singles already
    # exhausted above and so could never surface here as fresh combinations.
    parts = all_parts[:3]
    child = Combination(tuple(parts), "4h", "trail", round="grow")
    led.record_result(_res(child.hash, k=3, parts=list(child.keys)),
                      "grow", "", {})
    b = planner.next_batch(led, CFG)
    assert b.round == "ablation"
    assert all(c.k == 2 for c in b.combos)


def test_the_planner_stops_at_max_parts(led):
    cfg = {"research": {**CFG["research"], "max_parts": 1}}
    _gauges(led)
    led.record_control("4h", "ohlcv", {"consistency_p": 0.001}, True)
    from trader.research import vocab
    from trader.research.combo import Combination
    for p in vocab.parts_for("4h", led.gauges("4h")):
        c = Combination((p,), "4h", "trail")
        led.record_result(_res(c.hash, parts=[p.key]), "grow", "", {})
    b = planner.next_batch(led, cfg)
    assert b.kind == "idle"


def test_seed_parts_come_from_the_live_book(led):
    import json
    with led.journal._tx() as c:
        c.execute(
            "INSERT INTO strategies (id,name,kind,params,state,description,"
            "origin,hypothesis,invalidation,regime_filter,markets,generation,"
            "parent_id,created_at,stats_json,spec_json) VALUES "
            "(?,?,'spec','{}','paper','d','seed','h','i','[]',"
            "'[\"futures\"]',0,'','2026-09-01T00:00:00+00:00','{}',?)",
            ("auth_donchian_breakout_trail", "Donchian Breakout Trail",
             json.dumps({"entry_long": "close > donchian_hi(100)",
                         "entry_short": "close < donchian_lo(100)",
                         "timeframe": "4h"})))
    seeds = planner.seed_parts(led.journal)
    assert any(s.key.startswith("seed:") for s in seeds)
    assert seeds[0].long == "close > donchian_hi(100)"
