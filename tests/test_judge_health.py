"""The 6h review must see whether a strategy still matches its evidence.

The dossier carried live trades, win rate and pnl, and the brain compared
them against fixed thresholds. That cannot distinguish a mechanism performing
exactly as validated from one that has broken, because the comparison has no
reference to what the strategy was admitted on.

Donchian Breakout Trail was admitted at a 38.85% out-of-sample win rate over
487 trades. Three losses in a row is an ordinary Tuesday for it. Without the
validated rate in the dossier, the reviewer sees "0 wins from 3" and has
nothing to weigh it against.
"""
import json

from trader.brain.judge import BrainJudge


class _Journal:
    def __init__(self, rows, trades):
        self._rows, self._trades = rows, trades
    def list_strategies(self, states): return list(self._rows)
    def query(self, sql, args=()):
        sid = args[0] if args else ""
        w, n = self._trades.get(sid, (0, 0))
        return [{"n": n, "w": w, "pnl": 0.0}]


def _judge(rows, trades):
    j = object.__new__(BrainJudge)
    j.journal = _Journal(rows, trades)
    return j


def _row(sid, expected_wr=None, kind="spec"):
    prov = {"expected_winrate": expected_wr} if expected_wr is not None else {}
    return {"id": sid, "name": sid, "state": "paper", "kind": kind,
            "origin": "measured", "hypothesis": "h", "stats_json": "{}",
            "spec_json": json.dumps({"provenance": prov})}


def test_a_spec_with_a_validated_rate_gets_a_health_verdict():
    j = _judge([_row("s1", 0.3885)], {"s1": (2, 60)})
    row = j._strategy_rows()[0]
    assert row["health"]["verdict"] == "DIVERGED"
    assert row["health"]["expected_winrate"] == 0.3885


def test_a_young_strategy_reads_insufficient_not_broken():
    """0 from 3 at a 39% win rate is ordinary, and must not look like failure."""
    j = _judge([_row("s1", 0.3885)], {"s1": (0, 3)})
    assert j._strategy_rows()[0]["health"]["verdict"] == "INSUFFICIENT"


def test_a_strategy_performing_as_validated_reads_consistent():
    j = _judge([_row("s1", 0.3885)], {"s1": (39, 61)})
    assert j._strategy_rows()[0]["health"]["verdict"] == "CONSISTENT"


def test_a_strategy_with_no_validated_rate_reports_no_health():
    """Legacy genomes carry no admitted envelope; say so rather than invent one."""
    j = _judge([_row("g1", None, kind="ema_trend")], {"g1": (5, 10)})
    assert j._strategy_rows()[0]["health"] is None


def test_health_does_not_disturb_the_existing_dossier_fields():
    j = _judge([_row("s1", 0.3885)], {"s1": (4, 10)})
    row = j._strategy_rows()[0]
    assert row["live_trades"] == 10 and row["win_rate"] == 0.4
    assert row["id"] == "s1" and row["state"] == "paper"


def test_unparseable_spec_json_does_not_break_the_review():
    r = _row("s1")
    r["spec_json"] = "{not json"
    assert _judge([r], {"s1": (1, 2)})._strategy_rows()[0]["health"] is None
