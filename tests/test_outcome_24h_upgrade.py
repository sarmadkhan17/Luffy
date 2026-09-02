"""The 24-hour horizon must actually be reachable.

An outcome row was written as soon as the 4h horizon could be graded, and
`resolved_at` was stamped at the same moment. `candidates()` excludes any
decision that already has an outcome row, so the row was never revisited —
and at 4h old, no candle exists yet for the 24h horizon, so `correct_24h`
was written NULL and stayed NULL forever.

In the live journal that is 191 resolved outcomes carrying only 54 24h
grades: 72% of the learning signal at that horizon was unreachable by
construction, while the schema, the theorist and the agent-weighting all
carry a column for it.

An ungraded horizon is honest; a permanently ungradeable one is a bug.
"""
import pandas as pd
import pytest

from trader.engine.outcome_backfill import upgrade_24h


class _Journal:
    def __init__(self, rows):
        self.rows = rows
        self.written = []
    def query(self, sql, args=()):
        if "FROM outcomes" in sql:
            return [r for r in self.rows if r["correct_24h"] is None]
        return []
    def _tx(self):
        j = self
        class _C:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def execute(self_, sql, args): j.written.append(args)
        return _C()


def _frame(n=200, start="2026-01-01", up=True):
    step = 1.0 if up else -1.0
    close = [100.0 + i * step * 0.1 for i in range(n)]
    return pd.DataFrame({
        "ts": pd.date_range(start, periods=n, freq="1h", tz="UTC"),
        "open": close, "high": [c * 1.01 for c in close],
        "low": [c * 0.99 for c in close], "close": close,
        "volume": [1.0] * n})


def _row(ts="2026-01-01T00:00:00+00:00", sym="BTC/USDT"):
    return {"decision_id": "d1", "symbol": sym, "ts": ts, "action": "BUY",
            "entry_price": 100.0, "correct_24h": None}


def test_a_row_old_enough_gets_its_24h_grade():
    j = _Journal([_row()])
    n = upgrade_24h(j, {"BTC/USDT": _frame()},
                    now=pd.Timestamp("2026-01-05", tz="UTC").to_pydatetime())
    assert n == 1 and j.written, "the 24h horizon must be fillable later"


def test_a_row_younger_than_24h_is_left_alone():
    j = _Journal([_row()])
    n = upgrade_24h(j, {"BTC/USDT": _frame()},
                    now=pd.Timestamp("2026-01-01T06:00", tz="UTC").to_pydatetime())
    assert n == 0 and not j.written


def test_a_row_that_already_has_a_grade_is_not_requeried():
    j = _Journal([{**_row(), "correct_24h": 1}])
    assert upgrade_24h(j, {"BTC/USDT": _frame()},
                       now=pd.Timestamp("2026-01-05", tz="UTC").to_pydatetime()) == 0


def test_a_missing_frame_is_skipped_not_crashed():
    j = _Journal([_row(sym="GONE/USDT")])
    assert upgrade_24h(j, {}, now=pd.Timestamp("2026-01-05", tz="UTC").to_pydatetime()) == 0


def test_a_frame_that_does_not_reach_the_horizon_stays_ungraded():
    """An ungraded horizon is honest; a guessed one is poison."""
    j = _Journal([_row()])
    short = _frame(n=6)          # only 6 hours of candles
    assert upgrade_24h(j, {"BTC/USDT": short},
                       now=pd.Timestamp("2026-01-05", tz="UTC").to_pydatetime()) == 0


def test_direction_is_respected_when_grading():
    """A SELL that preceded a fall is correct, not incorrect."""
    j = _Journal([{**_row(), "action": "SELL"}])
    upgrade_24h(j, {"BTC/USDT": _frame(up=False)},
                now=pd.Timestamp("2026-01-05", tz="UTC").to_pydatetime())
    assert j.written and j.written[0][1] == 1
