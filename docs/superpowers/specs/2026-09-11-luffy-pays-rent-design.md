# Luffy pays rent — a weekly verdict read off the venue

**Status:** design approved 2026-09-11 (Sarmad + Claude). Choices are marked
DECIDED with who made them, so a later reader can tell a decision from an
assumption.

## Why this exists

Sarmad's rule: **Luffy must earn its living cost every week, or he deletes
it.**

- DECIDED (Sarmad) — the unit is **Luffy as a whole**, not each strategy.
- DECIDED (Sarmad) — the bar is **$50 net per week**, judged **strictly
  week by week**. A good week does not carry a bad one.
- DECIDED (Sarmad) — **death is a human act.** Luffy reports the verdict and
  never shuts itself down or deletes anything.

The system's whole job here is to tell the truth about the week, on time,
from a source that cannot flatter it.

### Why the journal cannot be that source

On 2026-09-11 the journal booked 8 closed trades at **+$46.33**. The venue's
income ledger for the same window reads **+$11.83**. Entries matched the venue
to the tick. Every exit was booked better than it filled (AVAX short: 7.447
booked, 7.484 filled), because `executor.close()` and `close_partial()` book
`order["average"] or order["price"] or price_hint`, and a demo market order
returns neither of the first two. So the verdict reads the venue, and the
journal is fixed to agree with it (component 4).

### What to expect — recorded once, not an argument against the rule

Measured on Donchian Breakout Trail, the book's only tradeable strategy:
1,723 validated fills over 4.9 years, replayed at the live settings (0.5%
risk, 8 slots, $4,900), 259 weeks:

| | per week |
|---|---|
| entries | 5.6 average; 5% of weeks have none |
| net | average **+$14.4**, typical week (median) **-$25**, p10 -$136, p90 +$222 |
| P(week ≥ $50) | **22%** |
| P(4 weeks in a row ≥ $50) | 0.4% |

Break-even on average against $50 needs about $17k at 0.5% risk. Raising risk
instead scales drawdown with return and trips `halt_drawdown_pct: 20`. These
numbers set the prior the verdicts will be read against. Nothing in this
design tries to beat them by changing strategies, sizing or risk.

## Components

### 1. The rent ledger — `trader/engine/rent.py` (new, pure)

- `week_bounds(now) -> (start_ms, end_ms)` — Monday 00:00 UTC to the next
  Monday 00:00 UTC.
- `fetch_income(ex, start_ms, end_ms) -> list[dict]` —
  `fapiPrivateGetIncome`, paged forward by `startTime` at `limit=1000` until
  a page comes back empty or passes `end_ms`. Verified live on the demo key:
  it returns `REALIZED_PNL` and `COMMISSION` rows (29 in the last 7 days).
- `summarize(rows) -> dict` — sums by `incomeType`. **Net counts only
  trading income:** `REALIZED_PNL`, `COMMISSION`, `FUNDING_FEE`. `TRANSFER`
  and any deposit or withdrawal never count, because moving money in is not
  earning it. Any other type is listed under `uncounted` so it is visible,
  never silently added or dropped.
- `verdict(summary, bar) -> "PASS" | "FAIL" | "UNKNOWN"`:
  - ledger read, net ≥ bar → **PASS**
  - ledger read, net < bar (including a week with zero rows) → **FAIL**
  - ledger could not be read → **UNKNOWN**, never PASS. The same rule as
    features: missing information is not a fabricated value.

Config: `rent: {weekly_usdt: 50, first_week_start: "2026-09-14"}`.

### 2. The rent thread — `Kernel._rent_loop`, daemon `rent-check`

Started beside the other daemon threads (`kernel.py:230-246`). Every hour:

1. Compute week-to-date from the ledger and write
   `state_kv["rent_state"]` as JSON: week start, net, bar, per-type sums,
   `uncounted`, `updated_at`, status `IN_PROGRESS`.
2. On the first run after 00:00 UTC each day, send one Telegram line:
   `🏠 Rent, week of <Mon>: +$X of $50 so far, N days left`.
3. On the first run after Monday 00:00 UTC, **finalise the previous week**:
   write a `rent_verdict` brain event (week start and end, net, bar, verdict,
   per-type sums) and send
   `🏠 Rent, week of <Mon>: PASS/FAIL/UNKNOWN, +$X against $50`.
   - Idempotent: a week that already has a `rent_verdict` event is not
     re-finalised or re-sent, so a restart cannot double-report.
   - UNKNOWN is retried hourly until the ledger answers. The event is then
     updated to the real verdict and the message is re-sent, marked as a
     correction.

DECIDED (Claude, override in review): the **first judged week starts Monday
2026-09-14 00:00 UTC.** The current week began while the machine was off, so
it gets the daily tally but no verdict.

### 3. Where Sarmad sees it

- **Telegram `/rent`** prints week-to-date and the last 8 verdicts.
- **Dashboard card.** A GraphQL field `rent` reads `state_kv["rent_state"]`
  plus the last 8 `rent_verdict` events, the same pattern as
  `news_guard_state` (`graphql_schema.py:246`, `server.py:799`). The card
  shows this week's net against $50, days left, and the last 8 verdicts as
  PASS/FAIL marks.

### 4. The journal books the fill, not a guess — `executor.py`

- **`close()`** books the exit price as the volume-weighted price of **this
  order's own fills**, from `fetch_my_trades(symbol, since=order_ts - 60s)`
  filtered to the order id. The trade's final `realized_pnl` becomes
  `reconcile.venue_realized_pnl(ex, symbol, opened_at)`, the venue's
  realized P&L minus commission over every fill since the open. That covers
  both legs and any partial, and reuses code that already exists and is
  tested.
- **`close_partial()`** books the partial's price and commission from that
  order's own fills the same way. The final close's venue total supersedes
  the running sum.
- **Fills can lag the order.** Retry `fetch_my_trades` 3 times with 1s
  backoff. If the venue still will not answer, book the current estimate,
  log a WARNING, and write a `pnl_estimated` brain event naming the trade, so
  an estimate is never indistinguishable from a fill.

## Invariants

- The verdict comes from the venue's income ledger. Nothing in it is read
  from `trades`.
- An unreadable ledger is UNKNOWN, never PASS.
- Transfers never count as income.
- One verdict per week, restart-safe.
- Luffy never stops or deletes itself because of a verdict.

## Testing

- `rent.summarize`:
  - counts `REALIZED_PNL`, `COMMISSION` and `FUNDING_FEE`;
  - excludes `TRANSFER`;
  - surfaces unknown types under `uncounted`.
- `rent.verdict`:
  - PASS at exactly $50;
  - FAIL at $49.99;
  - FAIL on zero rows;
  - UNKNOWN when the fetch raised.
- `week_bounds`: Monday 00:00:00 UTC exactly, one millisecond before it, and
  mid-week.
- Rent loop:
  - a finalised week is not re-sent after a restart;
  - an UNKNOWN week is corrected once the ledger answers;
  - the partial week before `first_week_start` gets no verdict.
- `executor.close` against a stub exchange that returns the demo shape (no
  `average`, no `price`): the journal books the fill VWAP and the venue's
  P&L, not the hint. Regression values from AVAX: hint 7.447, fill 7.484,
  booked 7.484.
- `close_partial`: the same shape, the same assertion.
- The full suite, `scripts/backtest_equivalence.py` and
  `scripts/bench_vector_backtest.py` stay green.

## Out of scope

- Any automatic shutdown or deletion. Sarmad decides.
- Changing strategies, sizing, risk or capital to chase the bar.
- A search for a second mechanism (offered, declined 2026-09-11).
- The open path's fill confirmation (`_confirm_fill`), whose last resort is
  the ticker's last price. Entries matched the venue to the tick on all 8
  trades examined, so it is noted but not changed here.
