"""Shared current-truth helpers: source-time parsing and freshness.

One rule for every owner-facing "is this current?" answer:

- a missing source time is ``unavailable`` (never fresh);
- a present but unparseable, non-finite or implausible time is ``invalid``;
- a source time later than the reader's now — by any amount — is ``invalid``
  (an impossible relationship; there is no positive skew allowance and the
  comparison is made on unrounded times);
- otherwise ``fresh`` while ``age <= stale_after`` and ``stale`` after.

The age is always measured from the source's own time, never from the time
the record was published, copied or requested.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone

#: no Luffy record predates this; an earlier time is a malformed value
EARLIEST_PLAUSIBLE = datetime(2020, 1, 1, tzinfo=timezone.utc)

FRESH, STALE, INVALID, UNAVAILABLE = "fresh", "stale", "invalid", "unavailable"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def parse_time(value) -> tuple[datetime | None, str | None]:
    """(time, None) | (None, "source_time_missing") | (None, "source_time_malformed").

    Accepts an ISO-8601 string (naive = UTC) or finite epoch seconds. Bools,
    NaN/inf, and times before 2020 are malformed, not missing."""
    if value is None or value == "":
        return None, "source_time_missing"
    if isinstance(value, bool):
        return None, "source_time_malformed"
    try:
        if isinstance(value, (int, float)):
            if not math.isfinite(float(value)):
                return None, "source_time_malformed"
            t = datetime.fromtimestamp(float(value), timezone.utc)
        elif isinstance(value, str):
            t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        else:
            return None, "source_time_malformed"
    except (TypeError, ValueError, OverflowError, OSError):
        return None, "source_time_malformed"
    if t < EARLIEST_PLAUSIBLE:
        return None, "source_time_malformed"
    return t, None


def freshness_of(value, now: datetime, stale_after: float) -> dict:
    """{observed_at, age_s, freshness, reason} for one source time."""
    t, err = parse_time(value)
    if t is None:
        return {"observed_at": None, "age_s": None,
                "freshness": UNAVAILABLE if err == "source_time_missing" else INVALID,
                "reason": err}
    raw = (now - t).total_seconds()
    age = display_age(raw)
    if raw < 0:
        return {"observed_at": iso(t), "age_s": age, "freshness": INVALID,
                "reason": "source_time_in_future"}
    if raw > stale_after:
        return {"observed_at": iso(t), "age_s": age, "freshness": STALE,
                "reason": "stale"}
    return {"observed_at": iso(t), "age_s": age, "freshness": FRESH, "reason": None}


def display_age(raw: float) -> float:
    """Age for display, rounded to 0.1 s — except a future (negative) age,
    which is kept unrounded so it can never round to a fresh 0.0."""
    return raw if raw < 0 else round(raw, 1)


def is_after(a: datetime | None, b: datetime | None) -> bool:
    """a strictly later than b (unrounded; no skew)."""
    return a is not None and b is not None and a > b


def classify_age(age: float | None, stale_after: float) -> str:
    """Freshness for an unrounded age in seconds (negative = future)."""
    if age is None or isinstance(age, bool) or not isinstance(age, (int, float)) \
            or not math.isfinite(age):
        return UNAVAILABLE
    if age < 0:
        return INVALID
    return FRESH if age <= stale_after else STALE


def finite(v) -> float | None:
    """A finite float, or None (bools, NaN, inf, strings that are not numbers)."""
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def digest(obj) -> str:
    """Stable SHA-256 of a JSON-able object (sorted keys)."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str,
                                     separators=(",", ":")).encode()).hexdigest()
