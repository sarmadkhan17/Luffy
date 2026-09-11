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
