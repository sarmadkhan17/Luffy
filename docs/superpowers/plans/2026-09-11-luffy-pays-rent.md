# Luffy Pays Rent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every Monday, Luffy reports whether it netted $50 over the past week, read from the venue's income ledger. The journal is fixed to book real fills so it agrees with the venue.

**Architecture:**
- **`trader/engine/rent.py`** holds pure functions over the venue income ledger.
- **`trader/engine/rent_keeper.py`** holds the stateful hourly tick: the tally in `state_kv`, a daily Telegram line, and the weekly `rent_verdict` brain event.
- **The kernel** runs the tick on a daemon thread and answers `/rent`.
- **GraphQL and the dashboard's Manager card** read the same snapshot.
- **`executor.close()` / `close_partial()`** book the order's own fills and the venue's P&L, not the order's missing `average` or the caller's price hint.

**Tech Stack:** Python 3, ccxt (Binance USDM), SQLite journal, strawberry GraphQL, FastAPI, pytest. Always run through `./venv/bin/python`.

**Spec:** `docs/superpowers/specs/2026-09-11-luffy-pays-rent-design.md`

## Global Constraints

- **The bar is $50 net per week**, and a week runs **Monday 00:00 UTC to the next Monday 00:00 UTC**.
- **The first judged week starts 2026-09-14 00:00 UTC.** Earlier weeks get a tally, never a verdict.
- **Counted income types:** `REALIZED_PNL`, `COMMISSION`, `FUNDING_FEE`. `TRANSFER` never counts, and every other type is listed as `uncounted`.
- **An unreadable ledger is `UNKNOWN`, never `PASS`.** A readable week with zero rows is `FAIL`.
- **One verdict per week, restart-safe.** An `UNKNOWN` verdict is superseded by a later `rent_verdict` row marked `correction: true`. The latest row per week wins.
- **Luffy never freezes, halts, stops or deletes anything because of a verdict.**
- **Journal writes go through `_tx()`** (directly, or via `kv_set` / `log_brain_event` / `close_trade`), **never `Journal.query()`**, which does not commit.
- **`journal.close_trade(id, exit_price, pnl, reason)` ADDS `pnl` to `realized_pnl`.** It is a leg, not a total.
- **Invariants:** the full suite passes except the known, date-dependent `tests/test_macro_guard.py::test_a_restart_reuses_the_cached_calendar`. `scripts/backtest_equivalence.py` prints `ENGINE EQUIVALENCE: PASS`, and `scripts/bench_vector_backtest.py` stays above 20x.
- **Commit messages end with:**
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01AftKNT6vqJENZG9vJVZVxP
  ```

---

### Task 1: The rent ledger (pure)

**Files:**
- Create: `trader/engine/rent.py`
- Test: `tests/test_rent.py`

**Interfaces:**
- Consumes: nothing. Takes any object with `fapiPrivateGetIncome(params: dict) -> list[dict]`. Binance rows carry `incomeType`, `income` (a string), `time` (int ms), `tranId` and `symbol`.
- Produces:
  - `rent.COUNTED: tuple[str, ...]`
  - `rent.WEEK_MS: int`
  - `rent.week_bounds(now: datetime) -> tuple[int, int]`
  - `rent.fetch_income(ex, start_ms: int, end_ms: int, page: int = 1000) -> list[dict]`
  - `rent.summarize(rows: list[dict]) -> dict` with keys `net` (float, unrounded), `by_type`, `uncounted` (both `dict[str, float]`) and `rows` (int)
  - `rent.read_week(ex, start_ms: int, end_ms: int) -> tuple[dict | None, str]`
  - `rent.verdict(summary: dict | None, bar: float) -> str`, one of `"PASS" | "FAIL" | "UNKNOWN"`

- [ ] **Step 1: Write the failing tests** in `tests/test_rent.py`

```python
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
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `./venv/bin/python -m pytest tests/test_rent.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'trader.engine.rent'`

- [ ] **Step 3: Implement** `trader/engine/rent.py`

```python
"""Luffy pays rent: the week's net income, read off the venue, against a bar.

Pure functions only. The venue's income ledger (`/fapi/v1/income`) is the
source. On 2026-09-11 the journal booked 8 closes at +$46.33 where the
ledger reads +$11.83, so nothing here reads `trades`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

#: Trading income. TRANSFER and any deposit or withdrawal is money moved in,
#: not money earned, and never counts.
COUNTED = ("REALIZED_PNL", "COMMISSION", "FUNDING_FEE")
WEEK_MS = 7 * 86_400_000


def week_bounds(now: datetime) -> tuple[int, int]:
    """[Monday 00:00 UTC, next Monday 00:00 UTC) containing `now`, in ms."""
    now = now.astimezone(timezone.utc)
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    start = int(monday.timestamp() * 1000)
    return start, start + WEEK_MS


def fetch_income(ex, start_ms: int, end_ms: int,
                 page: int = 1000) -> list[dict]:
    """Every income row with start_ms <= time < end_ms. Raises if the venue does.

    Pages forward from the last row's own timestamp, not one past it, so rows
    sharing a millisecond across a page break are kept; duplicates are
    dropped by (tranId, incomeType, symbol, time).
    """
    rows, seen, since = [], set(), start_ms
    while since < end_ms:
        batch = ex.fapiPrivateGetIncome(
            {"startTime": since, "endTime": end_ms - 1, "limit": page}) or []
        for r in batch:
            t = int(r["time"])
            key = (r.get("tranId"), r.get("incomeType"), r.get("symbol"), t)
            if key in seen or not (start_ms <= t < end_ms):
                continue
            seen.add(key)
            rows.append(r)
        if len(batch) < page:
            break
        last = int(batch[-1]["time"])
        since = last if last > since else since + 1
    return rows


def summarize(rows: list[dict]) -> dict:
    by_type: dict[str, float] = {}
    uncounted: dict[str, float] = {}
    for r in rows:
        kind = str(r.get("incomeType") or "")
        amt = float(r.get("income") or 0.0)
        bucket = by_type if kind in COUNTED else uncounted
        bucket[kind] = bucket.get(kind, 0.0) + amt
    return {"net": sum(by_type.values()), "by_type": by_type,
            "uncounted": uncounted, "rows": len(rows)}


def read_week(ex, start_ms: int, end_ms: int) -> tuple[dict | None, str]:
    """(summary, "") or (None, error). None means UNKNOWN, never zero."""
    try:
        return summarize(fetch_income(ex, start_ms, end_ms)), ""
    except Exception as e:
        log.warning(f"rent: income ledger unreadable: {e}")
        return None, f"{type(e).__name__}: {e}"[:200]


def verdict(summary: dict | None, bar: float) -> str:
    if summary is None:
        return "UNKNOWN"
    return "PASS" if summary["net"] >= bar else "FAIL"
```

- [ ] **Step 4: Run them and confirm they pass**

Run: `./venv/bin/python -m pytest tests/test_rent.py -q`
Expected: `12 passed`

- [ ] **Step 5: Commit**

```bash
git add trader/engine/rent.py tests/test_rent.py
git commit -F - <<'EOF'
feat(rent): the week's net, read off the venue's income ledger

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AftKNT6vqJENZG9vJVZVxP
EOF
```

---

### Task 2: The executor books the fill, not a guess

**Files:**
- Modify: `trader/engine/executor.py`
  - imports (lines 10-19)
  - `Executor.__init__` (lines 46-59)
  - `close_partial()` (lines 243-251)
  - `close()` (lines 278-312)
- Test: `tests/test_exit_books_the_fill.py`

**Interfaces:**
- Consumes: `reconcile.venue_realized_pnl(exchange, symbol: str, since_iso: str) -> float | None` (already exists, `trader/engine/reconcile.py:26`). It returns Σ `info.realizedPnl` − Σ `info.commission` over `fetch_my_trades(symbol, since=...)`.
- Produces:
  - `Executor.fill_retry_s: float` (default `1.0`; tests set `0`)
  - `Executor._order_fills(symbol, order_id, since_ms) -> list[dict]`
  - a `pnl_estimated` brain event, whose subject is the trade id, whenever an estimate is booked

**Why:** A Binance Demo market order comes back with neither `average` nor `price`, so `order["average"] or order["price"] or hint` booked the hint. On 2026-09-11, 8 of 8 exits were journalled better than they filled (AVAX short: 7.447 booked, 7.484 filled).

The entry fill lands a few seconds *before* `opened_at` is stamped, so reading from `opened_at` would drop the entry commission. The P&L window therefore starts 30s earlier, clamped so it never reaches back into the previous trade on the same symbol.

Existing executor test stubs have no `fetch_my_trades`. They must fall straight through to the estimate, with no retry sleeps, and book exactly what they book today.

- [ ] **Step 1: Write the failing tests** in `tests/test_exit_books_the_fill.py`

```python
"""An exit is booked at the price it FILLED at, and a trade's P&L is the venue's.

On 2026-09-11 the journal booked 8 closes at +$46.33; the venue's ledger
says +$11.83. Entries matched to the tick. Every exit was booked better than
it filled (AVAX short: 7.447 booked, 7.484 filled), because a demo market
order returns neither `average` nor `price` and the hint was booked instead.
"""
import time
from datetime import datetime, timedelta

import pytest

from trader.core.config import load_config
from trader.core.journal import Journal
from trader.core.types import MarketType, Position, Side
from trader.engine.executor import Executor

OPENED_AT = "2026-09-11T01:03:01.754364+00:00"
OPEN_MS = int(datetime.fromisoformat(OPENED_AT).timestamp() * 1000)


def _fill(side, price, amount, commission, realized, order=None, t=0):
    return {"order": order, "side": side, "price": price, "amount": amount,
            "timestamp": t,
            "info": {"commission": str(commission), "realizedPnl": str(realized)}}


class DemoEx:
    """Binance Demo's shape: a market order returns no average and no price."""

    def __init__(self, fills):
        self.fills = list(fills)
        self.next_fills = []          # fills the NEXT create_order produces
        self.fetch_raises = False

    def amount_to_precision(self, symbol, amount):
        return str(float(amount))

    def create_order(self, symbol, typ, side, amount, params=None):
        oid = f"c{len(self.fills)}"
        now = int(time.time() * 1000)
        self.fills += [{**f, "order": oid, "timestamp": now} for f in self.next_fills]
        self.next_fills = []
        return {"id": oid, "average": None, "price": None,
                "filled": None, "status": "NEW"}

    def fetch_my_trades(self, symbol, since=None, limit=None):
        if self.fetch_raises:
            raise RuntimeError("venue down")
        return [f for f in self.fills if f["timestamp"] >= (since or 0)]

    def cancel_order(self, oid, symbol):
        return {}


ENTRY = _fill("sell", 7.453, 273.0, 0.81387, 0.0, order="o_entry",
              t=OPEN_MS - 2000)       # lands 2s before opened_at is stamped


@pytest.fixture
def book(tmp_path):
    j = Journal(tmp_path / "j.db")
    ex = DemoEx([ENTRY])
    e = Executor(ex, j, load_config(), MarketType.FUTURES)
    e.fill_retry_s = 0
    j.add_trade(Position(
        id="pos_avax", symbol="AVAX/USDT", side=Side.SHORT, amount=273.0,
        entry_price=7.453, notional_usdt=round(273 * 7.453, 2),
        stop_loss=7.56964, market_type="futures", exec_mode="live",
        strategy_id="s", strategy_name="t", opened_at=OPENED_AT))
    return ex, j, e


def _row(j, tid="pos_avax"):
    return dict(j.query("SELECT * FROM trades WHERE id=?", (tid,))[0])


def test_the_exit_is_booked_at_the_fill_not_the_hint(book):
    ex, j, e = book
    ex.next_fills = [_fill("buy", 7.484, 273.0, 0.81725, -8.463)]
    assert e.close(_row(j), exit_price_hint=7.447, reason="manual")
    r = _row(j)
    assert r["exit_price"] == pytest.approx(7.484)
    assert r["realized_pnl"] == pytest.approx(-8.463 - 0.81387 - 0.81725, abs=1e-6)


def test_a_partial_then_a_close_totals_to_the_venue(book):
    ex, j, e = book
    ex.next_fills = [_fill("buy", 7.30, 136.0, 0.3971, 20.808)]
    assert e.close_partial(_row(j), 136.0, 7.20, "tp1")
    r = _row(j)
    assert r["realized_pnl"] == pytest.approx(20.808 - 0.3971, abs=1e-6)
    assert r["amount"] == pytest.approx(137.0)

    ex.next_fills = [_fill("buy", 7.40, 137.0, 0.4055, 7.261)]
    assert e.close(_row(j), 7.35, "sl_fill")
    venue_total = -0.81387 + (20.808 - 0.3971) + (7.261 - 0.4055)
    r = _row(j)
    assert r["realized_pnl"] == pytest.approx(venue_total, abs=1e-6)
    assert r["exit_price"] == pytest.approx(7.40)


def test_the_previous_trade_on_the_symbol_is_not_counted(book):
    ex, j, e = book
    prev_closed = (datetime.fromisoformat(OPENED_AT)
                   - timedelta(seconds=10)).isoformat()
    j.add_trade(Position(
        id="pos_prev", symbol="AVAX/USDT", side=Side.LONG, amount=100.0,
        entry_price=7.0, notional_usdt=700.0, stop_loss=6.9,
        market_type="futures", exec_mode="live", strategy_id="s",
        strategy_name="t", opened_at="2026-09-11T00:00:00+00:00"))
    j.close_trade("pos_prev", 7.2, 19.0, "tp", closed_at=prev_closed)
    ex.fills.append(_fill("sell", 7.2, 100.0, 1.0, 20.0, order="o_prev",
                          t=OPEN_MS - 12000))   # inside 30s, before prev close
    ex.next_fills = [_fill("buy", 7.484, 273.0, 0.81725, -8.463)]
    assert e.close(_row(j), exit_price_hint=7.447, reason="manual")
    assert _row(j)["realized_pnl"] == pytest.approx(
        -8.463 - 0.81387 - 0.81725, abs=1e-6)


def test_an_unreadable_venue_books_an_estimate_and_says_so(book):
    ex, j, e = book
    ex.fetch_raises = True
    assert e.close(_row(j), exit_price_hint=7.447, reason="manual")
    assert _row(j)["exit_price"] == pytest.approx(7.447)
    ev = j.query("SELECT subject FROM brain_events WHERE kind='pnl_estimated'")
    assert [x["subject"] for x in ev] == ["pos_avax"]
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `./venv/bin/python -m pytest tests/test_exit_books_the_fill.py -q`
Expected: 4 failed. The first three fail on the booked price or P&L, since the hint is booked. The fourth fails because it finds no `pnl_estimated` event.

- [ ] **Step 3: Add the imports.** After `from . import protective` (line 19) in `trader/engine/executor.py`, add:

```python
from .reconcile import venue_realized_pnl
```

After `import threading` (line 14), add:

```python
from datetime import datetime, timedelta
```

- [ ] **Step 4: Add the retry knob.** In `Executor.__init__`, directly after `self._reg = threading.Lock()`, add:

```python
        #: backoff between fill-history reads after an exit; tests set 0
        self.fill_retry_s = 1.0
```

- [ ] **Step 5: Add the venue-truth helpers.** Insert them directly above `def close_partial(`:

```python
    # ── venue truth for exits ────────────────────────────────────────────
    def _order_fills(self, symbol: str, order_id: str,
                     since_ms: int) -> list[dict]:
        """This order's own fills as the venue reports them; [] if unavailable.

        A demo market order returns neither `average` nor `price`, so booking
        `order["average"] or order["price"] or hint` booked the hint: 8 of 8
        exits on 2026-09-11 were journalled better than they filled.
        """
        fetch = getattr(self.ex, "fetch_my_trades", None)
        if fetch is None or not order_id:
            return []
        for attempt in range(3):
            try:
                fills = [
                    f for f in (fetch(symbol, since=since_ms, limit=100) or [])
                    if str(f.get("order")
                           or (f.get("info") or {}).get("orderId")
                           or "") == str(order_id)]
                if fills:
                    return fills
            except Exception as e:
                log.warning(f"fill history {symbol}: {e}")
            if attempt < 2:
                time.sleep(self.fill_retry_s)
        return []

    @staticmethod
    def _vwap(fills: list[dict]) -> tuple[float, float]:
        qty = sum(float(f["amount"]) for f in fills)
        px = sum(float(f["price"]) * float(f["amount"]) for f in fills) / qty
        return px, qty

    @staticmethod
    def _fills_net(fills: list[dict]) -> float:
        """The venue's realized P&L on these fills, net of their commission."""
        return sum(float((f.get("info") or {}).get("realizedPnl") or 0)
                   - float((f.get("info") or {}).get("commission") or 0)
                   for f in fills)

    def _pnl_window_start(self, trade: dict) -> str:
        """Where this trade's fills begin, as an ISO timestamp.

        The entry fill lands seconds before `opened_at` is stamped, so reading
        from `opened_at` drops the entry commission. Look back 30s, but never
        into the previous trade on the same symbol.
        """
        start = datetime.fromisoformat(trade["opened_at"]) - timedelta(seconds=30)
        prev = self.journal.query(
            "SELECT MAX(closed_at) t FROM trades WHERE symbol=? AND id<>? "
            "AND status='closed' AND closed_at <= ?",
            (trade["symbol"], trade["id"], trade["opened_at"]))
        if prev and prev[0]["t"]:
            start = max(start, datetime.fromisoformat(prev[0]["t"])
                        + timedelta(milliseconds=1))
        return start.isoformat()

    def _book_estimate(self, trade: dict, why: str) -> None:
        log.warning(f"P&L ESTIMATED {trade['symbol']} {trade['id']}: {why}")
        self.journal.log_brain_event("pnl_estimated", trade["id"],
                                     {"symbol": trade["symbol"], "why": why})
```

- [ ] **Step 6: Make `close_partial()` book its own fills.** Replace this block:

```python
        try:
            order = self.ex.create_order(sym, "market", side_close, amount,
                                         params={"reduceOnly": True})
            fill = float(order.get("average") or order.get("price")
                         or price_hint or 0)
            direction = 1.0 if trade["side"] == "long" else -1.0
            gross = ((fill - float(trade["entry_price"])) * direction * amount)
            fees = self.taker_fee * (amount * fill + amount * float(trade["entry_price"]))
            pnl = gross - fees
```

with:

```python
        try:
            sent_ms = int(time.time() * 1000) - 60_000
            order = self.ex.create_order(sym, "market", side_close, amount,
                                         params={"reduceOnly": True})
            fills = self._order_fills(sym, str(order.get("id") or ""), sent_ms)
            if fills:
                # this leg's own fills; the entry commission is settled when
                # the final close re-bases the trade to the venue's total
                fill, amount = self._vwap(fills)
                pnl = self._fills_net(fills)
            else:
                fill = float(order.get("average") or order.get("price")
                             or price_hint or 0)
                direction = 1.0 if trade["side"] == "long" else -1.0
                gross = ((fill - float(trade["entry_price"])) * direction * amount)
                fees = self.taker_fee * (amount * fill + amount * float(trade["entry_price"]))
                pnl = gross - fees
                self._book_estimate(trade, "venue fills unavailable at partial")
```

- [ ] **Step 7: Make `close()` book the fill and the venue's P&L.** Replace everything from `order = self.ex.create_order(` down to and including the `log.info(f"TRADE CLOSE ...")` call with:

```python
            sent_ms = int(time.time() * 1000) - 60_000
            order = self.ex.create_order(sym, "market", side_close, amount,
                                         params={"reduceOnly": True})
            fills = self._order_fills(sym, str(order.get("id") or ""), sent_ms)
            if fills:
                fill, amount = self._vwap(fills)
            else:
                fill = float(order.get("average") or order.get("price")
                             or exit_price_hint)
                # reduce-only fills only what the venue still holds. Sending 169
                # against a 1-coin residue closes 1, and pricing the exit over
                # the journalled 169 invents 168 coins of P&L out of rounding.
                amount = float(order.get("filled") or 0) or amount
            entry = float(trade["entry_price"])
            direction = 1.0 if trade["side"] == "long" else -1.0
            gross = ((fill - entry) * direction * amount)
            fees = self.taker_fee * (amount * entry + amount * fill)
            pnl = gross - fees
            venue = (venue_realized_pnl(self.ex, sym, self._pnl_window_start(trade))
                     if fills else None)
            if venue is not None:
                # close_trade ADDS this leg, so pass venue total minus what
                # the partials already banked: the trade then totals the venue
                banked = self.journal.query(
                    "SELECT realized_pnl FROM trades WHERE id=?", (trade["id"],))
                pnl = venue - float((banked[0]["realized_pnl"] if banked else 0) or 0)
            else:
                self._book_estimate(trade, "venue fills unavailable at close"
                                    if not fills else "venue P&L unavailable")
            self.journal.close_trade(trade["id"], fill, round(pnl, 8), reason)
            log.info(f"TRADE CLOSE {sym} @{fill} reason={reason} "
                     f"gross={gross:+.2f} fees={fees:.2f} pnl={pnl:+.2f} "
                     f"({'venue' if venue is not None else 'estimate'})")
```

- [ ] **Step 8: Run the new tests and every existing executor test**

Run: `./venv/bin/python -m pytest tests/test_exit_books_the_fill.py tests/test_realized_pnl_accumulates.py tests/test_partial_close_shrinks_notional.py tests/test_manual_close.py tests/test_lot_step_quantization.py tests/test_exchange_exit_is_size_aware.py tests/test_exits.py -q`
Expected: all pass. The old stubs have no `fetch_my_trades`, so they take the estimate path and book what they booked before.

- [ ] **Step 9: Commit**

```bash
git add trader/engine/executor.py tests/test_exit_books_the_fill.py
git commit -F - <<'EOF'
fix(executor): an exit is booked at its fill, and a trade's P&L is the venue's

A demo market order returns neither average nor price, so close() and
close_partial() booked the caller's hint: 8 of 8 exits on 2026-09-11 were
journalled better than they filled (+$46.33 booked, +$11.83 at the venue).
Both now read the order's own fills; the final close re-bases the trade to
venue_realized_pnl over a window that includes the entry commission and
excludes the previous trade on the symbol. An estimate is still booked
when the venue will not answer, and says so in a pnl_estimated event.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AftKNT6vqJENZG9vJVZVxP
EOF
```

---

### Task 3: The rent keeper (tally, daily line, weekly verdict)

**Files:**
- Create: `trader/engine/rent_keeper.py`
- Test: `tests/test_rent_keeper.py`

**Interfaces:**
- Consumes: from Task 1, `rent.week_bounds`, `rent.read_week`, `rent.verdict` and `rent.WEEK_MS`. From the journal, `kv_get`, `kv_set`, `log_brain_event` and `query` (reads only). The notifier needs `send(text: str, silent: bool = False) -> bool`.
- Produces:
  - `RentKeeper(exchange, journal, notifier, cfg: dict)`, which reads `cfg["rent"]["weekly_usdt"]` and `cfg["rent"]["first_week_start"]`
  - `RentKeeper.tick(now: datetime | None = None) -> dict`, which returns the state it wrote to `state_kv["rent_state"]`
  - `rent_keeper.snapshot(journal, limit: int = 8) -> {"state": dict, "history": [{"week_start": str, "verdict": str, "net": float | None}]}`, newest first, latest row per week
  - `rent_keeper.status_text(journal) -> str`
  - `state_kv` keys: `rent_state` (JSON) and `rent_tally_day` (`YYYY-MM-DD`)
  - brain event `rent_verdict`, whose subject is the week-start date. Its detail holds `week_start`, `week_end`, `net`, `bar`, `verdict`, `by_type`, `uncounted`, `error` and `correction`.

- [ ] **Step 1: Write the failing tests** in `tests/test_rent_keeper.py`

```python
"""The rent check: an hourly tally, one line a day, one verdict a week."""
import json
from datetime import datetime, timedelta, timezone

from trader.core.journal import Journal
from trader.engine.rent_keeper import RentKeeper, snapshot, status_text

MON = datetime(2026, 9, 14, tzinfo=timezone.utc)          # first judged week
MON_MS = int(MON.timestamp() * 1000)
CFG = {"rent": {"weekly_usdt": 50, "first_week_start": "2026-09-14"}}
NEXT_MON = MON + timedelta(days=7, minutes=5)


class Ledger:
    def __init__(self, rows=None):
        self.rows, self.down = list(rows or []), False

    def fapiPrivateGetIncome(self, p):
        if self.down:
            raise RuntimeError("503 Service Unavailable")
        return [r for r in self.rows
                if p["startTime"] <= r["time"] <= p["endTime"]][:p["limit"]]


class Notes:
    def __init__(self):
        self.sent = []

    def send(self, text, silent=False):
        self.sent.append(text)
        return True


def _income(kind, amt, t, tid):
    return {"incomeType": kind, "income": str(amt), "time": t,
            "tranId": tid, "symbol": "BTCUSDT"}


def _keeper(tmp_path, rows=None):
    j, led, n = Journal(tmp_path / "j.db"), Ledger(rows), Notes()
    return RentKeeper(led, j, n, CFG), j, led, n


def _verdicts(j):
    return [json.loads(r["detail"]) for r in j.query(
        "SELECT detail FROM brain_events WHERE kind='rent_verdict' ORDER BY id")]


def test_a_week_that_paid_is_a_pass(tmp_path):
    k, j, led, n = _keeper(tmp_path, [
        _income("REALIZED_PNL", 70, MON_MS + 3_600_000, 1),
        _income("COMMISSION", -12, MON_MS + 3_600_000, 2)])
    k.tick(NEXT_MON)
    v = _verdicts(j)
    assert [x["verdict"] for x in v] == ["PASS"]
    assert v[0]["net"] == 58.0 and v[0]["week_start"] == "2026-09-14"
    assert any("PASS" in s for s in n.sent)


def test_a_week_that_missed_is_a_fail(tmp_path):
    k, j, led, n = _keeper(tmp_path, [_income("REALIZED_PNL", 30, MON_MS + 1, 1)])
    k.tick(NEXT_MON)
    assert _verdicts(j)[0]["verdict"] == "FAIL"


def test_a_deposit_does_not_pay_the_rent(tmp_path):
    k, j, led, n = _keeper(tmp_path, [_income("TRANSFER", 1000, MON_MS + 1, 1)])
    k.tick(NEXT_MON)
    v = _verdicts(j)[0]
    assert v["verdict"] == "FAIL" and v["uncounted"] == {"TRANSFER": 1000.0}


def test_a_restart_does_not_judge_the_week_twice(tmp_path):
    k, j, led, n = _keeper(tmp_path, [_income("REALIZED_PNL", 70, MON_MS + 1, 1)])
    k.tick(NEXT_MON)
    k.tick(NEXT_MON + timedelta(hours=1))
    RentKeeper(led, j, n, CFG).tick(NEXT_MON + timedelta(hours=2))
    assert len(_verdicts(j)) == 1
    assert sum("PASS" in s for s in n.sent) == 1


def test_an_unreadable_week_is_unknown_then_corrected(tmp_path):
    k, j, led, n = _keeper(tmp_path, [_income("REALIZED_PNL", 70, MON_MS + 1, 1)])
    led.down = True
    k.tick(NEXT_MON)
    k.tick(NEXT_MON + timedelta(hours=1))
    assert [v["verdict"] for v in _verdicts(j)] == ["UNKNOWN"]   # said once
    led.down = False
    k.tick(NEXT_MON + timedelta(hours=2))
    v = _verdicts(j)
    assert [x["verdict"] for x in v] == ["UNKNOWN", "PASS"]
    assert v[-1]["correction"] is True
    assert snapshot(j)["history"][0]["verdict"] == "PASS"         # latest wins


def test_the_week_before_the_first_judged_week_gets_no_verdict(tmp_path):
    k, j, led, n = _keeper(tmp_path)
    k.tick(MON + timedelta(minutes=5))        # would finalise week of 2026-09-07
    assert _verdicts(j) == []


def test_the_tally_is_written_hourly_and_sent_once_a_day(tmp_path):
    k, j, led, n = _keeper(tmp_path, [_income("REALIZED_PNL", 12.5, MON_MS + 1, 1)])
    t = MON + timedelta(days=2, hours=9)
    k.tick(t)
    k.tick(t + timedelta(hours=1))
    s = json.loads(j.kv_get("rent_state"))
    assert s["net"] == 12.5 and s["week_start"] == "2026-09-14"
    assert s["status"] == "IN_PROGRESS" and s["judged"] is True
    assert sum("so far" in x for x in n.sent) == 1
    k.tick(t + timedelta(days=1))
    assert sum("so far" in x for x in n.sent) == 2


def test_status_text_reads_the_state_and_the_history(tmp_path):
    k, j, led, n = _keeper(tmp_path, [_income("REALIZED_PNL", 70, MON_MS + 1, 1)])
    k.tick(NEXT_MON)
    txt = status_text(j)
    assert "2026-09-14" in txt and "PASS" in txt and "+70.00" in txt
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `./venv/bin/python -m pytest tests/test_rent_keeper.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'trader.engine.rent_keeper'`

- [ ] **Step 3: Implement** `trader/engine/rent_keeper.py`

```python
"""The rent check's state: an hourly tally, one line a day, one verdict a week.

Reads the venue through `rent`, writes `state_kv["rent_state"]` and
`rent_verdict` brain events, and talks to Sarmad through the notifier. It
never stops, freezes or deletes anything: the verdict is his to act on.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..core.journal import Journal
from . import rent

log = logging.getLogger(__name__)


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _rounded(d: dict) -> dict:
    return {k: round(v, 4) for k, v in d.items()}


class RentKeeper:
    def __init__(self, exchange, journal: Journal, notifier, cfg: dict):
        r = cfg.get("rent", {}) or {}
        self.ex, self.journal, self.notifier = exchange, journal, notifier
        self.bar = float(r.get("weekly_usdt", 50))
        first = datetime.fromisoformat(str(r.get("first_week_start", "2026-09-14")))
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)
        self.first_ms = int(first.timestamp() * 1000)

    def tick(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        now_ms = int(now.timestamp() * 1000)
        start, end = rent.week_bounds(now)
        summary, err = rent.read_week(self.ex, start, now_ms + 1)
        state = {
            "week_start": _day(start),
            "net": None if summary is None else round(summary["net"], 2),
            "bar": self.bar,
            "by_type": {} if summary is None else _rounded(summary["by_type"]),
            "uncounted": {} if summary is None else _rounded(summary["uncounted"]),
            "days_left": round((end - now_ms) / 86_400_000, 1),
            "judged": start >= self.first_ms,
            "status": "IN_PROGRESS" if summary is not None else "UNKNOWN",
            "error": err,
            "updated_at": now.isoformat(),
        }
        self.journal.kv_set("rent_state", json.dumps(state))
        self._daily_line(now, state)
        self._finalise(start - rent.WEEK_MS)
        return state

    def _daily_line(self, now: datetime, state: dict) -> None:
        day = now.strftime("%Y-%m-%d")
        if self.journal.kv_get("rent_tally_day", "") == day:
            return
        self.journal.kv_set("rent_tally_day", day)
        net = "unreadable" if state["net"] is None else f"{state['net']:+.2f}"
        tag = "" if state["judged"] else " (not judged: before the first week)"
        self._send(f"🏠 Rent, week of {state['week_start']}: {net} of "
                   f"${self.bar:.0f} so far, {state['days_left']:.1f} days left{tag}")

    def _last_verdict(self, week: str) -> dict | None:
        rows = self.journal.query(
            "SELECT detail FROM brain_events WHERE kind='rent_verdict' "
            "AND subject=? ORDER BY id DESC LIMIT 1", (week,))
        return json.loads(rows[0]["detail"]) if rows else None

    def _finalise(self, week_start_ms: int) -> None:
        if week_start_ms < self.first_ms:
            return                       # before the first judged week
        week = _day(week_start_ms)
        prior = self._last_verdict(week)
        if prior and prior.get("verdict") != "UNKNOWN":
            return                       # already judged; restart-safe
        summary, err = rent.read_week(self.ex, week_start_ms,
                                      week_start_ms + rent.WEEK_MS)
        v = rent.verdict(summary, self.bar)
        if prior and v == "UNKNOWN":
            return                       # still unreadable, and already said so
        self.journal.log_brain_event("rent_verdict", week, {
            "week_start": week,
            "week_end": _day(week_start_ms + rent.WEEK_MS),
            "net": None if summary is None else round(summary["net"], 2),
            "bar": self.bar, "verdict": v,
            "by_type": {} if summary is None else _rounded(summary["by_type"]),
            "uncounted": {} if summary is None else _rounded(summary["uncounted"]),
            "error": err, "correction": bool(prior)})
        net = "unreadable" if summary is None else f"{summary['net']:+.2f}"
        fix = " (correction: the ledger now answers)" if prior else ""
        self._send(f"🏠 Rent, week of {week}: {v}, {net} against "
                   f"${self.bar:.0f}{fix}")

    def _send(self, text: str) -> None:
        if not self.notifier:
            return
        try:
            self.notifier.send(text)
        except Exception as e:
            log.warning(f"rent: notify failed: {e}")


def snapshot(journal: Journal, limit: int = 8) -> dict:
    """This week's tally and the last `limit` verdicts, newest first."""
    try:
        state = json.loads(journal.kv_get("rent_state", "") or "{}")
    except Exception:
        state = {}
    history, seen = [], set()
    for r in journal.query("SELECT subject, detail FROM brain_events "
                           "WHERE kind='rent_verdict' ORDER BY id DESC"):
        if r["subject"] in seen:
            continue                     # an older row for a corrected week
        seen.add(r["subject"])
        d = json.loads(r["detail"])
        history.append({"week_start": r["subject"],
                        "verdict": d.get("verdict"), "net": d.get("net")})
        if len(history) >= limit:
            break
    history.sort(key=lambda h: h["week_start"], reverse=True)
    return {"state": state, "history": history}


def status_text(journal: Journal) -> str:
    snap = snapshot(journal)
    s = snap["state"]
    if not s:
        return "🏠 Rent: no reading yet"
    net = "unreadable" if s.get("net") is None else f"{s['net']:+.2f}"
    lines = [f"🏠 Rent, week of {s.get('week_start')}: {net} of "
             f"${float(s.get('bar', 50)):.0f}, {s.get('days_left')} days left"]
    for h in snap["history"]:
        n = "?" if h["net"] is None else f"{h['net']:+.2f}"
        lines.append(f"  {h['week_start']}  {h['verdict']}  {n}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run them and confirm they pass**

Run: `./venv/bin/python -m pytest tests/test_rent_keeper.py tests/test_rent.py -q`
Expected: `20 passed`

- [ ] **Step 5: Commit**

```bash
git add trader/engine/rent_keeper.py tests/test_rent_keeper.py
git commit -F - <<'EOF'
feat(rent): an hourly tally, one line a day, one verdict a week

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AftKNT6vqJENZG9vJVZVxP
EOF
```

---

### Task 4: Wire it in: config, kernel thread, `/rent`, GraphQL, Manager card

**Files:**
- Modify: `config.yaml` (after the `notify:` block, before `logging:`)
- Modify: `trader/kernel.py`
  - the daemon-thread block (lines 245-247)
  - after `_brain_judge_loop` (line 509)
  - `_handle_tg_command`, before `elif msg.startswith("/scouts"):`
- Modify: `trader/api/graphql_schema.py`
  - after `class NewsGuardType` (line 63)
  - after the `news_guard` field (line 255)
- Modify: `trader/dashboard/server.py`, `manager(e)` inside `build_company` (line 825)
- Test: `tests/test_rent_wiring.py`

**Interfaces:**
- Consumes: from Task 3, `RentKeeper`, `snapshot` and `status_text`. From the kernel, `self.exchange`, `self.journal`, `self.notifier`, `self.cfg` and `self._stop`.
- Produces:
  - GraphQL field `rent`, with snake_case fields: `week_start`, `net`, `bar`, `days_left`, `status`, `updated_at`, and `history { week_start verdict net }`
  - Manager card `core` pairs `["Rent", "<net> / <bar>, <days>d left"]` and `["Weeks", "<P|F|? per verdict, oldest first>"]`
  - the kernel thread `rent-check`
  - the Telegram command `/rent`

- [ ] **Step 1: Write the failing tests** in `tests/test_rent_wiring.py`

```python
"""The rent check reaches Sarmad: kernel thread, /rent, GraphQL, Manager card."""
import inspect
import json

from trader.core.config import load_config
from trader.core.journal import Journal


def _seed(j):
    j.kv_set("rent_state", json.dumps({
        "week_start": "2026-09-14", "net": 12.5, "bar": 50.0,
        "days_left": 4.6, "status": "IN_PROGRESS",
        "updated_at": "2026-09-16T09:00:00+00:00"}))
    j.log_brain_event("rent_verdict", "2026-09-07",
                      {"verdict": "FAIL", "net": -3.0})


def test_config_carries_the_bar_and_the_first_week():
    r = load_config()["rent"]
    assert r["weekly_usdt"] == 50 and str(r["first_week_start"]) == "2026-09-14"


def test_graphql_rent_reads_the_keeper_state(tmp_path):
    from trader.api.graphql_schema import make_graphql_router
    j = Journal(tmp_path / "j.db")
    _seed(j)
    res = make_graphql_router(j).schema.execute_sync(
        "{ rent { week_start net bar status history { week_start verdict net } } }")
    assert res.errors is None, res.errors
    r = res.data["rent"]
    assert r["week_start"] == "2026-09-14" and r["net"] == 12.5 and r["bar"] == 50.0
    assert r["history"] == [{"week_start": "2026-09-07", "verdict": "FAIL", "net": -3.0}]


def test_the_manager_card_shows_the_rent(tmp_path):
    from trader.dashboard.server import build_company
    j = Journal(tmp_path / "j.db")
    _seed(j)
    blob = json.dumps(build_company(j, load_config()))
    assert '["Rent", "+12 / 50, 4.6d left"]' in blob
    assert '["Weeks", "F"]' in blob          # last verdicts, oldest first


def test_the_kernel_runs_the_rent_check_and_answers_rent():
    from trader import kernel as K
    src = inspect.getsource(K)
    assert 'name="rent-check"' in src
    assert 'msg.startswith("/rent")' in src
    assert "RentKeeper(" in src
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `./venv/bin/python -m pytest tests/test_rent_wiring.py -q`
Expected: 4 failed. The config test fails on `KeyError: 'rent'`, the GraphQL test finds no field `rent`, the Manager-card test finds no `Rent` pair, and the source test's assertions fail.

- [ ] **Step 3: Add the config block.** Insert it in `config.yaml` between the `notify:` block and `logging:`:

```yaml
rent:                            # Luffy pays rent. See
                                 # docs/superpowers/specs/2026-09-11-luffy-pays-rent-design.md
  weekly_usdt: 50                # net trading income per Monday-to-Monday UTC week
  first_week_start: "2026-09-14" # earlier weeks get a tally, never a verdict
  check_minutes: 60              # tally refresh; the verdict lands on the first
                                 # check after Monday 00:00 UTC
```

- [ ] **Step 4: Start the thread and add the loop.** In `trader/kernel.py`, directly after the `brain-judge` thread start (the line `name="brain-judge").start()`), add:

```python
        if self.cfg.get("rent", {}).get("weekly_usdt"):
            threading.Thread(target=self._rent_loop, daemon=True,
                             name="rent-check").start()
```

Directly after the end of `_brain_judge_loop` (its last line is `            _t.sleep(interval)`), add:

```python

    def _rent_loop(self) -> None:
        """Luffy pays rent: the week's net off the venue ledger, hourly.
        Reports only. The verdict is Sarmad's to act on."""
        from .engine.rent_keeper import RentKeeper
        keeper = RentKeeper(self.exchange, self.journal, self.notifier, self.cfg)
        every = float(self.cfg.get("rent", {}).get("check_minutes", 60)) * 60
        while not self._stop:
            try:
                keeper.tick()
            except Exception as e:
                log.warning(f"rent check failed: {e}")
            time.sleep(every)
```

- [ ] **Step 5: Add `/rent`.** In `_handle_tg_command`, directly before `        elif msg.startswith("/scouts"):`, add:

```python
        elif msg.startswith("/rent"):
            from .engine.rent_keeper import status_text
            reply(status_text(self.journal))
```

- [ ] **Step 6: Add the GraphQL types and field.** In `trader/api/graphql_schema.py`, directly after `class NewsGuardType` and its three fields, add:

```python


@strawberry.type
class RentWeekType:
    week_start: str
    verdict: str
    net: Optional[float]


@strawberry.type
class RentType:
    week_start: str
    net: Optional[float]
    bar: float
    days_left: float
    status: str
    updated_at: str
    history: list[RentWeekType]
```

In `build_query`, directly after the `news_guard` field's final `return NewsGuardType(active=False, why="no data yet", checked_at="")`, add:

```python

        @strawberry.field
        def rent(self) -> RentType:
            from ..engine.rent_keeper import snapshot
            snap = snapshot(journal)
            s = snap["state"]
            return RentType(
                week_start=str(s.get("week_start", "")),
                net=s.get("net"),
                bar=float(s.get("bar", 50)),
                days_left=float(s.get("days_left") or 0),
                status=str(s.get("status", "no reading yet")),
                updated_at=str(s.get("updated_at", "")),
                history=[RentWeekType(week_start=h["week_start"],
                                      verdict=str(h["verdict"]), net=h["net"])
                         for h in snap["history"]])
```

- [ ] **Step 7: Add the Rent pair to the Manager card.** In `trader/dashboard/server.py`, inside `def manager(e):` directly after the line `heat_str = f"{heat_pct:.1f}%" if heat_pct is not None else "—"`, add:

```python
        from ..engine.rent_keeper import snapshot as _rent_snapshot
        _rent = _rent_snapshot(journal)
        rs = _rent["state"]
        rent_str = ("—" if not rs else
                    "unreadable" if rs.get("net") is None else
                    f"{rs['net']:+.0f} / {float(rs.get('bar', 50)):.0f}, "
                    f"{float(rs.get('days_left') or 0):.1f}d left")
        # last verdicts, oldest first: P pass, F fail, ? unknown
        weeks_str = "".join({"PASS": "P", "FAIL": "F"}.get(h["verdict"], "?")
                            for h in reversed(_rent["history"])) or "—"
```

In the same function's `"core": [...]` list, add `["Rent", rent_str]` and `["Weeks", weeks_str]` after `["Heat", heat_str]`, so the list reads:

```python
                "core": [["Equity", f"${equity_now:,.0f}" if equity_now else "—"],
                         ["Control", control],
                         ["Cycle", _age_str(ts)],
                         ["Heat", heat_str],
                         ["Rent", rent_str],
                         ["Weeks", weeks_str]]}
```

- [ ] **Step 8: Run the wiring tests and the neighbours they touch**

Run: `./venv/bin/python -m pytest tests/test_rent_wiring.py tests/test_rent_keeper.py tests/test_rent.py tests/test_company.py tests/test_org.py tests/test_single_creation_path.py -q`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add config.yaml trader/kernel.py trader/api/graphql_schema.py trader/dashboard/server.py tests/test_rent_wiring.py
git commit -F - <<'EOF'
feat(rent): the weekly verdict reaches Sarmad: thread, /rent, GraphQL, Manager card

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AftKNT6vqJENZG9vJVZVxP
EOF
```

---

### Task 5: Verify on the real system, and record it

**Files:**
- Modify: `CLAUDE.md`
  - the daemon-thread table
  - the `state_kv` line
  - the Telegram command line
  - the Open-faults entry for the exit-booking fault, which Task 2 closed

- [ ] **Step 1: Run the full suite and the invariants**

Run: `./venv/bin/python -m pytest tests/ -q -p no:cacheprovider 2>&1 | tail -3`
Expected: everything passes except `tests/test_macro_guard.py::test_a_restart_reuses_the_cached_calendar`, the known failure that depends on the date.

Run: `./venv/bin/python scripts/backtest_equivalence.py 2>&1 | tail -1`
Expected: `ENGINE EQUIVALENCE: PASS`

Run: `./venv/bin/python scripts/bench_vector_backtest.py 2>&1 | tail -3`
Expected: a speedup above 20x.

- [ ] **Step 2: Read the real ledger once, without the kernel**

Run:
```bash
./venv/bin/python - <<'EOF'
import sys, warnings; warnings.filterwarnings("ignore"); sys.path.insert(0, ".")
from datetime import datetime, timezone
from trader.core.config import load_config
from trader.data.feed import make_exchange
from trader.engine import rent
ex = make_exchange(load_config())
s, e = rent.week_bounds(datetime.now(timezone.utc))
summary, err = rent.read_week(ex, s, int(datetime.now(timezone.utc).timestamp() * 1000) + 1)
print(summary, err)
EOF
```
Expected: a summary whose `net` matches the venue's week-to-date income. This week holds the 8 m237 closes, so it should include net +11.83 unless newer trades have closed since. `err` is empty.

- [ ] **Step 3: Restart the kernel and confirm the thread ran**

Run: `./restart.sh kernel`, then after about 60s run:
`grep -a "rent check failed" /tmp/opencode/luffy_kernel.log | tail -3; ./venv/bin/python -c "import sqlite3,json;c=sqlite3.connect('file:data/luffy.db?mode=ro',uri=True);print(json.loads(c.execute(\"select value from state_kv where key='rent_state'\").fetchone()[0]))"`
Expected: no `rent check failed` lines, and a `rent_state` with this week's `week_start`, `status: IN_PROGRESS` and a numeric `net`.

Run: `curl -s -X POST localhost:8080/graphql -H 'Content-Type: application/json' -d '{"query":"{ rent { week_start net bar status } }"}'`
Expected: the same numbers, with no `errors` key.

Run `./restart.sh dashboard` first if the dashboard predates Task 4. Then run `curl -s localhost:8080/api/org | grep -o '\["Rent", "[^"]*"\]'`
Expected: `["Rent", "<net> / 50, <days>d left"]`. `/api/org` caches the company blob, so allow one cache period after a restart.

- [ ] **Step 4: Update `CLAUDE.md`**
  - In the daemon-thread table, add the row `| \`rent-check\` | 1h | the week's net off the venue ledger against the $50 bar; Monday verdict |`.
  - In the `state_kv` sentence, add `rent_state` and `rent_tally_day`.
  - In the Telegram command line, add `/rent`.
  - In the Open-faults entry for the 2026-09-11 incident, replace the sentence that starts "Unfixed — any P&L read off `trades` is flattered" with "Fixed the same day: `close()`/`close_partial()` book the order's own fills and re-base the trade to `venue_realized_pnl`; an estimate is booked only when the venue will not answer, and says so in a `pnl_estimated` event."

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -F - <<'EOF'
docs: the rent check, and the exit-booking fault closed

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AftKNT6vqJENZG9vJVZVxP
EOF
```
