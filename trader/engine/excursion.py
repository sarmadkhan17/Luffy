"""Maximum favourable / adverse excursion, recorded when a trade closes.

The journal stores `close_reason` and nothing else about the path a trade
took, so "did the entry find anything, or did the exit hand it back" cannot
be answered from the journal at all. The 2026-09-20 Donchian audit had to
reconstruct every excursion from `data/candles.db` by hand, twice — once for
the attribution table and once to resolve the FIL trailing question.

This module is OBSERVATION ONLY. It reads closed bars and writes two numbers
plus their provenance. It never touches a stop, a size, a strategy state or a
lifecycle rule, and it is never on the path of a decision.

Three properties the audit needs and this guarantees:

  * INTRABAR. MFE/MAE are extreme *intrabar* excursions — bar highs and lows,
    not closes. They measure the opportunity the trade had, which is NOT the
    price the exit engine evaluated: production arms the trail on the last
    closed 15m close (kernel.py:752). Reading an intrabar MFE as "the trail
    should have fired here" is exactly the error the audit's §10 corrected.
  * POINT-IN-TIME. Only bars that both OPENED at or after entry and CLOSED at
    or before exit are read. A bar straddling either edge is excluded rather
    than truncated, so no excursion can be attributed to a bar the trade did
    not fully live through.
  * UNKNOWN STAYS UNKNOWN. Thin or missing candle coverage yields NULL with a
    recorded reason, never a number. A fabricated zero would read as "this
    trade never went favourable", which is a claim about the market rather
    than about the data.

See docs/superpowers/reports/2026-09-20-donchian-retirement-attribution-audit.md
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ..core.types import TF_MS

log = logging.getLogger(__name__)

#: fraction of the bars the window should contain that must actually be on
#: disk before an excursion is considered measured. Matches the coverage bar
#: the audit's own reconstruction held itself to.
MIN_COVERAGE = 0.9

#: state_kv key holding the ISO timestamp from which the live sweep measures.
#: Trades that closed before it are left alone: this feature does not rewrite
#: history, and anything earlier belongs to `backfill()`.
EPOCH_KEY = "excursion_epoch"


@dataclass
class Excursion:
    """Two numbers and the story of where they came from."""
    mfe_r: float | None = None
    mae_r: float | None = None
    provenance: dict = field(default_factory=dict)

    @property
    def measured(self) -> bool:
        return self.mfe_r is not None and self.mae_r is not None


def _ms(value) -> int | None:
    """ISO string, datetime or epoch-ms → epoch ms."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        import datetime as _dt
        if isinstance(value, str):
            v = _dt.datetime.fromisoformat(value)
        else:
            v = value
        if v.tzinfo is None:
            v = v.replace(tzinfo=_dt.timezone.utc)
        return int(v.timestamp() * 1000)
    except Exception:
        return None


def _bars_in_window(bars, tf_ms: int, start_ms: int, end_ms: int) -> list:
    """Bars fully contained in [start, end] — PIT-safe, never truncated.

    A bar qualifies when it opened at or after entry AND had closed by exit.
    `bars` is any iterable of (ts_ms, high, low).
    """
    out = []
    for ts, high, low in bars:
        if ts >= start_ms and ts + tf_ms <= end_ms:
            out.append((ts, float(high), float(low)))
    out.sort(key=lambda b: b[0])
    return out


def _expected_bars(tf_ms: int, start_ms: int, end_ms: int) -> int:
    """How many tf-aligned bars fit entirely inside the window."""
    if tf_ms <= 0 or end_ms <= start_ms:
        return 0
    first = -(-start_ms // tf_ms) * tf_ms            # ceil to grid
    last = ((end_ms - tf_ms) // tf_ms) * tf_ms       # floor, must close by end
    if last < first:
        return 0
    return int((last - first) // tf_ms) + 1


def compute(side: str, entry_price: float, initial_risk: float,
            bars, timeframe: str, opened_at, closed_at,
            min_coverage: float = MIN_COVERAGE,
            timeframe_source: str = "spec",
            exit_price: float | None = None) -> Excursion:
    """Intrabar MFE/MAE in R, or an explicit unknown.

    long :  mfe_r = (max(high) - entry) / initial_risk
            mae_r = (min(low)  - entry) / initial_risk
    short:  mfe_r = (entry - min(low))  / initial_risk
            mae_r = (entry - max(high)) / initial_risk

    Both are SIGNED and neither is clamped: a trade that never traded in its
    favour has a negative mfe_r, and one that never traded against itself has
    a positive mae_r. Clamping either to zero would assert an excursion that
    did not happen. mfe_r >= mae_r always holds.

    KNOWN LIMITATION, recorded in provenance rather than papered over: the
    bar a trade exits DURING straddles the close and is excluded by the PIT
    rule, so for a stop-filled trade the fatal bar is not counted and
    `mae_r` is a LOWER BOUND on the true adverse excursion. `exit_price`,
    when supplied, is not used in the figures but is reported as `exit_r`
    so the size of that gap is visible to anyone reading the row.
    """
    prov: dict = {"timeframe": timeframe, "timeframe_source": timeframe_source,
                  "basis": "intrabar_high_low", "pit": "bars_fully_inside_hold",
                  "min_coverage": min_coverage}

    start_ms, end_ms = _ms(opened_at), _ms(closed_at)
    if start_ms is None or end_ms is None:
        return Excursion(provenance={**prov, "status": "unknown",
                                     "reason": "unreadable_hold_window"})
    prov["window"] = {"opened_ms": start_ms, "closed_ms": end_ms}

    tf_ms = TF_MS.get(timeframe or "")
    if not tf_ms:
        return Excursion(provenance={**prov, "status": "unknown",
                                     "reason": "unknown_timeframe"})

    try:
        risk = float(initial_risk)
        entry = float(entry_price)
    except (TypeError, ValueError):
        risk, entry = 0.0, 0.0
    if not (risk > 0) or risk != risk or entry <= 0 or entry != entry:
        # R is the unit; without a positive frozen risk there is no R to
        # express an excursion in, and inventing one is worse than a null.
        return Excursion(provenance={**prov, "status": "unknown",
                                     "reason": "invalid_initial_risk"})

    if side not in ("long", "short"):
        return Excursion(provenance={**prov, "status": "unknown",
                                     "reason": f"unknown_side:{side}"})

    window = _bars_in_window(bars or [], tf_ms, start_ms, end_ms)
    expected = _expected_bars(tf_ms, start_ms, end_ms)
    coverage = (len(window) / expected) if expected else 0.0
    prov.update({"bars": len(window), "expected_bars": expected,
                 "coverage": round(coverage, 4)})
    if window:
        prov["first_bar_ms"] = window[0][0]
        prov["last_bar_ms"] = window[-1][0]

    if not window:
        return Excursion(provenance={**prov, "status": "unknown",
                                     "reason": "no_bars_in_window"})
    if expected == 0:
        return Excursion(provenance={**prov, "status": "unknown",
                                     "reason": "hold_shorter_than_one_bar"})
    if coverage < min_coverage:
        return Excursion(provenance={**prov, "status": "unknown",
                                     "reason": "insufficient_coverage"})

    hi = max(b[1] for b in window)
    lo = min(b[2] for b in window)
    if side == "long":
        mfe, mae = (hi - entry) / risk, (lo - entry) / risk
    else:
        mfe, mae = (entry - lo) / risk, (entry - hi) / risk
    prov.update({"status": "measured", "high": hi, "low": lo,
                 "entry_price": entry, "initial_risk": risk, "side": side,
                 # the exit bar straddles `closed_at` and is excluded, so a
                 # stop fill's own spike is NOT in mae_r — see the docstring
                 "partial_edge_bars_excluded": True})
    try:
        if exit_price is not None and float(exit_price) > 0:
            d = 1.0 if side == "long" else -1.0
            prov["exit_r"] = round((float(exit_price) - entry) * d / risk, 6)
    except (TypeError, ValueError):
        pass
    return Excursion(mfe_r=round(mfe, 6), mae_r=round(mae, 6), provenance=prov)


# ── persistence ──────────────────────────────────────────────────────────

def _already_recorded(row: dict) -> bool:
    return bool(row.get("excursion_json"))


def record(journal, trade: dict, bars_for, timeframe_for=None,
           min_coverage: float = MIN_COVERAGE, force: bool = False) -> bool:
    """Measure one closed trade and persist it. Returns True if it wrote.

    Idempotent: a trade that already carries provenance — measured OR an
    explicit unknown — is left exactly as it is unless `force` is set, so a
    replay cannot change a stored number.

    `bars_for(symbol, timeframe)` returns an iterable of (ts_ms, high, low).
    `timeframe_for(strategy_id)` returns the spec's declared timeframe.
    """
    if trade.get("status") != "closed":
        return False
    if _already_recorded(trade) and not force:
        return False

    tf_source = "spec"
    tf = None
    if timeframe_for is not None:
        try:
            tf = timeframe_for(trade.get("strategy_id") or "")
        except Exception as e:
            log.warning(f"excursion tf lookup {trade.get('id')}: {e}")
    if not tf:
        # no spec owns this trade (a legacy genome); say so rather than
        # silently presenting an execution-frame figure as the spec's own
        tf, tf_source = "15m", "execution_fallback"

    try:
        bars = bars_for(trade["symbol"], tf)
    except Exception as e:
        log.warning(f"excursion bars {trade.get('id')}: {e}")
        bars = []

    ex = compute(trade.get("side"), trade.get("entry_price"),
                 trade.get("initial_risk"), bars, tf,
                 trade.get("opened_at"), trade.get("closed_at"),
                 min_coverage=min_coverage, timeframe_source=tf_source,
                 exit_price=trade.get("exit_price"))

    with journal._tx() as c:
        c.execute("UPDATE trades SET mfe_r=?, mae_r=?, excursion_json=? "
                  "WHERE id=? AND status='closed'",
                  (ex.mfe_r, ex.mae_r, json.dumps(ex.provenance),
                   trade["id"]))
    return True


def sweep(journal, bars_for, timeframe_for=None, limit: int = 50,
          min_coverage: float = MIN_COVERAGE) -> int:
    """Measure newly closed trades. Never reaches behind the epoch.

    On first run the epoch is set to now, so every trade that closed before
    this feature existed is left untouched — historical rows are evidence,
    and reaching back into them is `backfill()`'s job, invoked deliberately.
    """
    epoch = journal.kv_get(EPOCH_KEY)
    if not epoch:
        from ..core.types import now_utc
        epoch = now_utc().isoformat()
        journal.kv_set(EPOCH_KEY, epoch)
        return 0
    rows = journal.query(
        "SELECT * FROM trades WHERE status='closed' AND closed_at>=? "
        "AND (excursion_json IS NULL OR excursion_json='') "
        "ORDER BY closed_at LIMIT ?", (epoch, limit))
    n = 0
    for t in rows:
        try:
            if record(journal, t, bars_for, timeframe_for,
                      min_coverage=min_coverage):
                n += 1
        except Exception as e:                  # never disturb the caller
            log.warning(f"excursion record {t.get('id')}: {e}")
    return n


def backfill(journal, bars_for, timeframe_for=None, trade_ids=None,
             since: str | None = None, force: bool = False,
             min_coverage: float = MIN_COVERAGE) -> dict:
    """Explicit, operator-invoked measurement of historical trades.

    Deliberately NOT called by the kernel. `sweep()` will not touch anything
    that closed before the epoch; this is the only way in, and it must be
    asked for by name.
    """
    sql = "SELECT * FROM trades WHERE status='closed'"
    args: list = []
    if trade_ids:
        sql += f" AND id IN ({','.join('?' * len(trade_ids))})"
        args += list(trade_ids)
    if since:
        sql += " AND closed_at>=?"
        args.append(since)
    if not force:
        sql += " AND (excursion_json IS NULL OR excursion_json='')"
    rows = journal.query(sql + " ORDER BY closed_at", tuple(args))
    out = {"considered": len(rows), "written": 0, "measured": 0,
           "unknown": 0, "errors": 0}
    for t in rows:
        try:
            if record(journal, t, bars_for, timeframe_for,
                      min_coverage=min_coverage, force=force):
                out["written"] += 1
                fresh = journal.query(
                    "SELECT mfe_r FROM trades WHERE id=?", (t["id"],))[0]
                out["measured" if fresh["mfe_r"] is not None
                    else "unknown"] += 1
        except Exception as e:
            out["errors"] += 1
            log.warning(f"excursion backfill {t.get('id')}: {e}")
    return out
