"""The rent ledger: the week's net income, read off the venue.

The journal booked 8 closes at +$46.33 on 2026-09-11 where the venue's
income ledger reads +$11.83, so the weekly verdict never reads `trades`.
"""
from datetime import datetime, timezone

import pytest

from trader.engine import rent


def _dt(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


MON = int(_dt("2026-09-14T00:00:00").timestamp() * 1000)


def test_a_week_starts_on_monday_midnight_utc():
    assert rent.week_bounds(_dt("2026-09-14T00:00:00")) == (MON, MON + rent.WEEK_MS)


def test_one_millisecond_before_monday_is_the_previous_week():
    s, e = rent.week_bounds(datetime.fromtimestamp((MON - 1) / 1000, timezone.utc))
    assert (s, e) == (MON - rent.WEEK_MS, MON)


def test_mid_week_belongs_to_its_monday():
    assert rent.week_bounds(_dt("2026-09-17T13:45:00"))[0] == MON


def _row(kind, amt, t=MON + 1000, tid=1):
    return {"incomeType": kind, "income": str(amt), "time": t,
            "tranId": tid, "symbol": "BTCUSDT"}


def test_only_trading_income_counts():
    s = rent.summarize([
        _row("REALIZED_PNL", 60, tid=1), _row("COMMISSION", -12.5, tid=2),
        _row("FUNDING_FEE", -1.5, tid=3), _row("TRANSFER", 1000, tid=4),
        _row("WELCOME_BONUS", 5, tid=5)])
    assert s["net"] == pytest.approx(46.0)
    assert s["uncounted"] == {"TRANSFER": 1000.0, "WELCOME_BONUS": 5.0}
    assert s["rows"] == 5


@pytest.mark.parametrize("net,expected", [
    (50.0, "PASS"), (49.99, "FAIL"), (0.0, "FAIL"), (-30.0, "FAIL")])
def test_the_verdict_against_the_bar(net, expected):
    assert rent.verdict({"net": net}, 50) == expected


def test_a_week_with_no_rows_is_a_fail_not_unknown():
    assert rent.verdict(rent.summarize([]), 50) == "FAIL"


def test_an_unreadable_ledger_is_unknown_never_pass():
    class Down:
        def fapiPrivateGetIncome(self, params):
            raise RuntimeError("503 Service Unavailable")

    s, err = rent.read_week(Down(), MON, MON + rent.WEEK_MS)
    assert s is None and "503" in err
    assert rent.verdict(s, 50) == "UNKNOWN"


class Ledger:
    """Binance semantics: startTime/endTime inclusive, oldest first, capped at limit."""

    def __init__(self, rows):
        self.rows = sorted(rows, key=lambda r: r["time"])

    def fapiPrivateGetIncome(self, params):
        lo, hi, n = params["startTime"], params["endTime"], params["limit"]
        return [r for r in self.rows if lo <= r["time"] <= hi][:n]


def test_paging_keeps_rows_that_share_a_millisecond_across_a_page_break():
    rows = [_row("REALIZED_PNL", 1, t=MON, tid=0),
            _row("REALIZED_PNL", 1, t=MON + 1, tid=1),
            _row("REALIZED_PNL", 1, t=MON + 1, tid=2),
            _row("REALIZED_PNL", 1, t=MON + 2, tid=3)]
    got = rent.fetch_income(Ledger(rows), MON, MON + rent.WEEK_MS, page=2)
    assert sorted(r["tranId"] for r in got) == [0, 1, 2, 3]


def test_rows_outside_the_week_are_not_counted():
    rows = [_row("REALIZED_PNL", 1, t=MON - 1, tid=1),
            _row("REALIZED_PNL", 2, t=MON, tid=2),
            _row("REALIZED_PNL", 4, t=MON + rent.WEEK_MS, tid=3)]
    got = rent.fetch_income(Ledger(rows), MON, MON + rent.WEEK_MS)
    assert [r["tranId"] for r in got] == [2]
