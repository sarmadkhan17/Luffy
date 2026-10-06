"""Outcome resolution — closes the learning loop on skipped AND taken trades.

For every decision (executed or not) with a scheduled outcome, fetch forward
candles at resolution time and record 1h/4h/24h returns + directional
correctness. This is what later proves which agents were right.
"""
from __future__ import annotations

import logging
import math

from ..core.journal import Journal

log = logging.getLogger(__name__)

HORIZONS = {"1h": 60, "4h": 240, "24h": 1440}


def resolve_pending(journal: Journal, feed, now_ms: int | None = None) -> int:
    """Resolve outcomes only for horizons whose window has actually elapsed.

    A row is consumed (resolved_at set) ONLY once correct_4h is available —
    resolving early poisoned every sample with NULL correctness and starved
    the whole learning loop (agents_accuracy / calibration / theorist).
    """
    rows = journal.query(
        "SELECT * FROM outcomes WHERE resolved_at IS NULL")
    resolved = 0
    for o in rows:
        ts = o["ts"]                                  # ISO with tz
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(ts)
            since_ms = int(dt.timestamp() * 1000)
        except Exception:
            continue
        entry = float(o["entry_price"])
        action = (o["action"] or "").upper()
        direction = 1 if action == "BUY" else -1

        elapsed_min = minutes_since(since_ms, now_ms)
        # nothing new computable yet — don't burn a candle fetch:
        # 1h filled & 4h window not elapsed → wait; 4h filled & 24h not → wait
        filled_1h = o.get("fwd_ret_1h") is not None
        filled_4h = o.get("fwd_ret_4h") is not None
        if filled_1h and not filled_4h and elapsed_min < 240:
            continue
        if filled_4h and elapsed_min < 1440:
            continue
        # beyond usefulness (≥7 days old, pre-fix leftovers): consume quietly
        if elapsed_min > 60 * 24 * 7:
            with journal._tx() as c:
                c.execute("UPDATE outcomes SET resolved_at=? "
                          "WHERE decision_id=?",
                          (iso_now(now_ms), o["decision_id"]))
            resolved += 1
            continue

        try:
            if now_ms is None:
                df = feed.fetch_ohlcv(o["symbol"], "5m", limit=650, force=True)
            else:
                df = feed.fetch_ohlcv(o["symbol"], "5m", limit=650, as_of_ms=now_ms)
        except Exception as e:
            log.warning(f"outcome fetch failed {o['symbol']}: {e}")
            continue
        if df is None or df.empty:
            continue
        from ..data import market_provenance as mp
        from ..cognition.outcomes import timestamp
        # Current acquisition is measured after receipt; explicit historical
        # calls stay at the requested cut and never acquire newer evidence.
        measured_ms = timestamp(iso_now(now_ms))
        df = mp.eligible_frame(df, '5m', measured_ms,
            require_provenance=any(k in df for k in mp.META))
        if df is None or df.empty:
            continue
        df = df[df["ts"] >= pd_to_dt(since_ms)]

        upd: dict = {}
        capture_targets = {}
        for label, mins in HORIZONS.items():
            if elapsed_min < mins:                    # window not elapsed yet
                continue
            # first candle at/after the horizon
            target = df[df["ts"] >= pd_to_dt(since_ms + mins * 60_000)]
            col = f"fwd_ret_{label}"
            ok_col = f"correct_{label}"
            if target.empty:
                upd[col], upd[ok_col] = None, None
                continue
            # Pandas turns absent optional receipt metadata (notably the
            # predecessor revision) into NaN in mixed revision frames. Restore
            # the producer's nullable contract before authoritative JSON.
            capture_targets[label] = {k:(v.isoformat() if hasattr(v,'isoformat') else v.item() if hasattr(v,'item') else v)
                                     for k,v in target.iloc[0].to_dict().items()}
            px = float(target.iloc[0]["close"])
            if not math.isfinite(px):
                capture_targets.pop(label, None)
                continue
            ret = (px - entry) / entry * direction
            upd[col] = round(ret, 6)
            upd[ok_col] = int(ret > 0) if abs(ret) > 1e-9 else 0

        # nothing measurable yet → leave unconsumed, try again next cycle
        if not upd:
            continue

        consumed = upd.get("correct_4h") is not None   # 4h = the learning coin
        resolved_at = iso_now(measured_ms) if consumed else None
        with journal._tx() as c:
            c.execute(
                "UPDATE outcomes SET fwd_ret_1h=?, correct_1h=?, "
                "fwd_ret_4h=?, correct_4h=?, fwd_ret_24h=?, correct_24h=?, "
                "resolved_at=COALESCE(?, resolved_at) "
                "WHERE decision_id=?",
                (upd.get("fwd_ret_1h"), upd.get("correct_1h"),
                 upd.get("fwd_ret_4h"), upd.get("correct_4h"),
                 upd.get("fwd_ret_24h"), upd.get("correct_24h"),
                 resolved_at, o["decision_id"]))
            from ..learning import capture as lc, capture_runtime as lr
            from ..cognition.outcomes import timestamp
            lc.safely(c, 'decision:'+o['decision_id'], lr.forward, o, upd, capture_targets, measured_ms)
        resolved += 1
    if resolved:
        log.info(f"outcomes updated: {resolved}")
    return resolved


def target_bar(row):
    import math
    import pandas as pd
    from trader.data.market_provenance import META, VALUES, receipt_metadata
    metadata = receipt_metadata(row) if all(k in row for k in META) else {}
    result = {}
    for key, value in row.items():
        if key in metadata:
            value = metadata[key]
        elif key in VALUES and key not in ('open','high','low','close','volume') and pd.isna(value):
            value = None  # existing optional value contract, not fabricated zero
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError('forward_target_nonfinite_required_value')
        result[key] = (value.isoformat() if hasattr(value,'isoformat') else
                       value.item() if hasattr(value,'item') else value)
    return result


# ── helpers ──────────────────────────────────────────────────────────────
def pd_to_dt(ms_or_ts):
    """DataFrame['ts'] is datetime64[ns, UTC]; compare via Timestamp."""
    import pandas as pd
    ms = ms_or_ts
    if isinstance(ms_or_ts, int) and ms_or_ts > 1e14:   # µs guard
        ms = ms_or_ts / 1000
    return pd.Timestamp(ms, unit="ms", tz="UTC")


def minutes_since(since_ms: int, now_ms: int | None) -> float:
    import time
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    return (now - since_ms) / 60_000


def iso_now(now_ms: int | None) -> str:
    from datetime import datetime, timezone
    import time
    ms = now_ms if now_ms is not None else int(time.time() * 1000)
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
