"""Outcome resolution — closes the learning loop on skipped AND taken trades.

For every decision (executed or not) with a scheduled outcome, fetch forward
candles at resolution time and record 1h/4h/24h returns + directional
correctness. This is what later proves which agents were right.
"""
from __future__ import annotations

import logging

from ..core.journal import Journal

log = logging.getLogger(__name__)

HORIZONS = {"1h": 60, "4h": 240, "24h": 1440}


def resolve_pending(journal: Journal, feed, now_ms: int | None = None) -> int:
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

        try:
            df = feed.fetch_ohlcv(o["symbol"], "5m", limit=300, force=True)
        except Exception as e:
            log.warning(f"outcome fetch failed {o['symbol']}: {e}")
            continue
        if df is None or df.empty:
            continue
        df = df[df["ts"] >= pd_to_dt(since_ms)]
        elapsed_min = minutes_since(since_ms, now_ms)

        upd: dict = {}
        for label, mins in HORIZONS.items():
            # first candle at/after the horizon
            target = df[df["ts"] >= pd_to_dt(since_ms + mins * 60_000)]
            col = f"fwd_ret_{label}"
            ok_col = f"correct_{label}"
            if target.empty:
                upd[col], upd[ok_col] = None, None
                continue
            px = float(target.iloc[0]["close"])
            ret = (px - entry) / entry * direction
            upd[col] = round(ret, 6)
            upd[ok_col] = int(ret > 0) if abs(ret) > 1e-9 else 0
            if label == "24h":
                pass

        resolved_at = iso_now(now_ms)
        with journal._tx() as c:
            c.execute(
                "UPDATE outcomes SET resolved_at=?, fwd_ret_1h=?, correct_1h=?, "
                "fwd_ret_4h=?, correct_4h=?, fwd_ret_24h=?, correct_24h=? "
                "WHERE decision_id=?",
                (resolved_at,
                 upd.get("fwd_ret_1h"), upd.get("correct_1h"),
                 upd.get("fwd_ret_4h"), upd.get("correct_4h"),
                 upd.get("fwd_ret_24h"), upd.get("correct_24h"),
                 o["decision_id"]))
        resolved += 1
    if resolved:
        log.info(f"outcomes resolved: {resolved}")
    return resolved


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
