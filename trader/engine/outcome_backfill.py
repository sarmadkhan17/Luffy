"""Grade the HOLDs the sampler threw away.

The learning loop is supply-starved, not design-broken. Every directional
decision is scheduled for grading, but a HOLD reaches the journal only if it
leans near the threshold AND survives a 15% dice roll AND its symbol is off a
90-minute cooldown (`orchestrator._maybe_shadow_outcome`). The result is 162
graded outcomes against ~111k decisions — while calibration wants 60 samples
per agent, the meta-label model wants 40, and the online weights want a
stream. Each of those is reading one part in seven hundred.

The rest of the evidence is not gone. Every decision carries its full vote
stack, score, threshold and regime, and `candles.db` holds the prices needed
to grade it. This recovers the near-threshold leaners from disk.

Two things it deliberately does NOT do:

- **It will not grade every HOLD.** A score near zero is not a prediction,
  and 100k coin flips would bury the real signal rather than feed it. Only
  decisions that leaned — the same bar the live sampler applies — are graded.
- **It does not go through `resolve_pending`.** That resolver consumes any
  row older than 7 days by marking it resolved with NULL correctness, so a
  historical backfill routed through it would be silently discarded. These
  rows are written already resolved.

Grading matches `resolve_pending` exactly: the first candle at or after each
horizon, return signed by the lean's own direction, and a flat move graded 0
rather than 1.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

#: horizon label -> minutes, the same three the resolver fills
HORIZONS = {"1h": 60, "4h": 240, "24h": 1440}
LEAN_FRAC = 0.6          # |score| >= this * threshold counts as a lean
EPS = 1e-9               # below this the move is flat, not a win


def grade(df, since, entry: float, action: str) -> dict:
    """{fwd_ret_*, correct_*} for one decision, or Nones where unmeasurable.

    `df` is an OHLCV frame with a tz-aware `ts` column; `since` the decision
    timestamp. A horizon with no candle at or after it stays None — an
    ungraded horizon is honest, a guessed one is poison.
    """
    import pandas as pd
    out: dict = {}
    direction = 1 if (action or "").upper() == "BUY" else -1
    since = pd.Timestamp(since)
    for label, mins in HORIZONS.items():
        col, ok_col = f"fwd_ret_{label}", f"correct_{label}"
        out[col] = out[ok_col] = None
        if df is None or not len(df):
            continue
        target = df[df["ts"] >= since + pd.Timedelta(minutes=mins)]
        if target.empty:
            continue
        px = float(target.iloc[0]["close"])
        if entry <= 0:
            continue
        ret = (px - entry) / entry * direction
        out[col] = round(ret, 6)
        out[ok_col] = int(ret > 0) if abs(ret) > EPS else 0
    return out


def _elapsed_minutes(ts: str, now=None) -> float:
    try:
        dt = datetime.fromisoformat(ts)
    except Exception:
        return -1.0
    now = now or datetime.now(timezone.utc)
    return (now - dt).total_seconds() / 60.0


def upgrade_24h(journal, frames: dict, now=None) -> int:
    """Fill the 24h grade on outcomes that were too young when first written.

    A row was written the moment the 4h horizon could be graded, and
    `candidates()` excludes any decision that already has an outcome row — so
    it was never revisited. At 4h old no candle exists yet for the 24h
    horizon, so `correct_24h` was written NULL and stayed NULL for good: 191
    resolved outcomes in the live journal carried only 54 24h grades, while
    the theorist and the agent weighting both read that column.

    A horizon with no candle stays None. An ungraded horizon is honest; a
    permanently ungradeable one is a bug.
    """
    rows = journal.query(
        "SELECT decision_id, symbol, ts, action, entry_price, correct_24h "
        "FROM outcomes WHERE correct_24h IS NULL")
    n = 0
    for r in rows:
        if _elapsed_minutes(r["ts"], now) < HORIZONS["24h"]:
            continue
        df = frames.get(r["symbol"])
        if df is None or not len(df):
            continue
        entry = float(r["entry_price"] or 0)
        if entry <= 0:
            continue
        g = grade(df, r["ts"], entry, r["action"])
        if g.get("correct_24h") is None:
            continue                     # the frame does not reach it yet
        with journal._tx() as c:
            c.execute("UPDATE outcomes SET fwd_ret_24h=?, correct_24h=? "
                      "WHERE decision_id=?",
                      (g["fwd_ret_24h"], g["correct_24h"], r["decision_id"]))
        n += 1
    if n:
        log.info(f"outcome upgrade: {n} decisions graded at 24h")
    return n


def candidates(journal, lean_frac: float = LEAN_FRAC,
               limit: int | None = None) -> list[dict]:
    """Ungraded HOLD decisions that leaned far enough to be a prediction."""
    q = ("SELECT d.id, d.symbol, d.ts, d.score, d.threshold, d.cycle_id, "
         "       d.entry_price, c.price AS cycle_price "
         "FROM decisions d LEFT JOIN cycles c ON c.id = d.cycle_id "
         "WHERE d.action = 'HOLD' "
         "  AND ABS(d.score) >= ? * d.threshold "
         "  AND NOT EXISTS (SELECT 1 FROM outcomes o "
         "                  WHERE o.decision_id = d.id) "
         "ORDER BY d.ts")
    params: tuple = (float(lean_frac),)
    if limit:
        q += " LIMIT ?"
        params = (float(lean_frac), int(limit))
    return journal.query(q, params)


def backfill_holds(journal, frames: dict, lean_frac: float = LEAN_FRAC,
                   limit: int | None = None, now=None) -> int:
    """Write resolved outcomes for leaning HOLDs. Returns rows written.

    `frames` is {symbol: ohlcv DataFrame} covering the decision window —
    passed in rather than fetched so this is testable offline and so one
    read of `candles.db` serves thousands of decisions.
    """
    written = 0
    for d in candidates(journal, lean_frac, limit):
        if _elapsed_minutes(d["ts"], now) < HORIZONS["4h"]:
            continue                       # not evidence yet
        df = frames.get(d["symbol"])
        if df is None or not len(df):
            continue
        entry = float(d["entry_price"] or d["cycle_price"] or 0)
        if entry <= 0:
            continue
        action = "BUY" if float(d["score"]) > 0 else "SELL"
        g = grade(df, d["ts"], entry, action)
        if g.get("correct_4h") is None:
            continue                       # 4h is the learning coin
        with journal._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO outcomes(decision_id,cycle_id,symbol,"
                "ts,action,entry_price,resolved_at,fwd_ret_1h,fwd_ret_4h,"
                "fwd_ret_24h,correct_1h,correct_4h,correct_24h) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (d["id"], d["cycle_id"], d["symbol"], d["ts"], action, entry,
                 datetime.now(timezone.utc).isoformat(),
                 g["fwd_ret_1h"], g["fwd_ret_4h"], g["fwd_ret_24h"],
                 g["correct_1h"], g["correct_4h"], g["correct_24h"]))
        written += 1
    if written:
        log.info(f"outcome backfill: {written} HOLDs graded")
    return written
