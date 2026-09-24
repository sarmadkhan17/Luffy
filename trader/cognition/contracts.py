"""Versioned records and the input loader.

Timestamps are integer epoch milliseconds (UTC). A candle is keyed by its
OPEN time; it closes at `open_ms + tf_ms`, and that close is its event time.
It is usable at `as_of` only when `close_ms <= as_of` AND
`available_ms <= as_of` — the closed-bar rule of
`trader.core.types.closed_bars`, plus arrival time, so a bar that landed late
does not exist before it landed. Input bars flagged `"closed": false` are
rejected outright.

Membership intervals are identified by (symbol, source, from_ms). Rows with
the same identity are revisions of one interval (e.g. a later row that sets
`to_ms` to close it); at `as_of` only the latest revision available then
counts. Same identity and same `available_ms` with different `to_ms` raises.

Participation series are (symbol, kind, source); same series, event_ms and
available_ms with a different value raises.

Positioning (optional key `positioning`) is a list of
{symbol, series, ts, value} derivatives samples captured from the recorder's
store, series in POSITIONING_SERIES. `ts` is the sample's own timestamp
(funding settlement time; ratio period END). An absent key yields
`Dataset.positioning is None` and changes nothing. A rejected record marks its
(symbol, series) unusable in `Dataset.positioning_invalid` instead of raising;
an identical repeat is rejected as `duplicate` without invalidating the series.

Missing is `None`, never 0. Invalid records are rejected with a reason and
kept in `Dataset.rejected`; structural errors raise ValueError.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field

from trader.cognition import SCHEMA_VERSION

INPUT_SCHEMA = "cognition.input.v1"
TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def stable_id(prefix: str, *parts) -> str:
    blob = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}_{hashlib.sha256(blob.encode()).hexdigest()[:16]}"


def rnd(x):
    """12 significant digits for a platform-stable trace. Significant, not
    decimal: a nonzero micro value never rounds to 0. None stays None."""
    return None if x is None else float(f"{float(x):.12g}")


@dataclass(frozen=True)
class Candle:
    symbol: str
    open_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    available_ms: int
    source: str
    close_ms: int


@dataclass(frozen=True)
class Membership:
    symbol: str
    from_ms: int
    to_ms: int | None          # exclusive; None = still a member
    available_ms: int
    source: str


@dataclass(frozen=True)
class Participation:
    symbol: str
    kind: str
    event_ms: int
    available_ms: int
    value: float
    source: str


POSITIONING_SERIES = ("funding", "ls_ratio")


@dataclass(frozen=True)
class PositioningPoint:
    symbol: str
    series: str
    ts: int
    value: float


@dataclass
class Dataset:
    timeframe: str
    tf_ms: int
    candles: dict            # symbol -> {open_ms: [Candle sorted by available_ms]}
    membership: list
    participation: dict      # symbol -> [Participation sorted by event_ms]
    decision_times: list
    rejected: list = field(default_factory=list)
    positioning: dict | None = None          # symbol -> series -> [PositioningPoint by ts]
    positioning_invalid: dict = field(default_factory=dict)   # (symbol, series) -> reason

    def bar_asof(self, symbol: str, open_ms: int, as_of: int) -> Candle | None:
        """Latest revision of one closed bar known at `as_of`, or None."""
        best = None
        for c in self.candles.get(symbol, {}).get(open_ms, ()):
            if c.available_ms <= as_of and c.close_ms <= as_of:
                best = c
        return best

    def members_asof(self, as_of: int) -> list:
        latest: dict = {}
        for m in self.membership:                     # sorted by available_ms
            if m.available_ms <= as_of:
                latest[(m.symbol, m.source, m.from_ms)] = m
        return sorted({m.symbol for m in latest.values()
                       if m.from_ms <= as_of and (m.to_ms is None or as_of < m.to_ms)})

    def participation_asof(self, symbol: str, as_of: int) -> list:
        return [p for p in self.participation.get(symbol, ())
                if p.available_ms <= as_of and p.event_ms <= as_of]


@dataclass
class Observation:
    obs_id: str
    kind: str
    symbol: str              # "*" for market-wide observations
    as_of_ms: int
    event_ms: int | None
    available_ms: int | None
    source: str
    status: str              # ok|missing|stale|warmup|gap|degenerate|insufficient_cohort
    value: float | None
    detail: dict
    schema_version: str = SCHEMA_VERSION


@dataclass
class Hypothesis:
    hyp_id: str
    episode_id: str
    template: str            # market_continuation | asset_divergence | unknown
    statement: str
    supporting_ids: list
    contradicting_ids: list
    prediction: dict
    deadline_ms: int
    falsification: str
    probability: float | None = None     # unset until calibrated
    schema_version: str = SCHEMA_VERSION


@dataclass
class Outcome:
    outcome_id: str
    subject_id: str
    subject_kind: str        # hypothesis | baseline_raw | baseline_relative
    deadline_ms: int
    resolved_at_ms: int | None
    status: str              # confirmed | falsified | not_testable | measured | unresolved
    measurement: dict
    schema_version: str = SCHEMA_VERSION


def to_dict(rec) -> dict:
    return asdict(rec)


def is_timestamp(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


_is_ts = is_timestamp


def _is_num(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def load_input(raw: dict) -> Dataset:
    if not isinstance(raw, dict) or raw.get("schema") != INPUT_SCHEMA:
        raise ValueError(f"input schema must be {INPUT_SCHEMA!r}")
    tf = raw.get("timeframe")
    if tf not in TF_MS:
        raise ValueError(f"timeframe must be one of {sorted(TF_MS)}")
    tf_ms = TF_MS[tf]
    rejected: list = []

    def reject(section, i, reason):
        rejected.append({"section": section, "index": i, "reason": reason})

    decisions = raw.get("decision_times")
    if not isinstance(decisions, list) or not all(_is_ts(t) for t in decisions):
        raise ValueError("decision_times must be a list of non-negative int ms")

    candles: dict = {}
    seen: dict = {}
    for i, c in enumerate(raw.get("candles", [])):
        try:
            sym, o_ms = c["symbol"], c["open_ms"]
            ohlcv = [c[k] for k in ("open", "high", "low", "close", "volume")]
        except (KeyError, TypeError):
            reject("candles", i, "missing_field")
            continue
        if not isinstance(sym, str) or not sym:
            reject("candles", i, "bad_symbol")
            continue
        if not _is_ts(o_ms) or o_ms % tf_ms:
            reject("candles", i, "bad_open_ms")
            continue
        if c.get("closed", True) is not True:
            reject("candles", i, "incomplete_bar")
            continue
        if not all(_is_num(v) for v in ohlcv):
            reject("candles", i, "non_finite")
            continue
        o, h, lo, cl, v = (float(x) for x in ohlcv)
        if min(o, h, lo, cl) <= 0 or v < 0 or h < max(o, cl) or lo > min(o, cl):
            reject("candles", i, "inconsistent_ohlcv")
            continue
        close_ms = o_ms + tf_ms
        avail = c.get("available_ms", close_ms)
        if not _is_ts(avail):
            reject("candles", i, "bad_available_ms")
            continue
        if avail < close_ms:
            reject("candles", i, "available_before_close")
            continue
        rec = Candle(sym, o_ms, o, h, lo, cl, v, avail,
                     str(c.get("source", "input")), close_ms)
        key = (sym, o_ms, avail)
        if key in seen:
            if seen[key] != rec:
                raise ValueError(f"conflicting candle revisions for {key}")
            continue                                   # exact duplicate
        seen[key] = rec
        candles.setdefault(sym, {}).setdefault(o_ms, []).append(rec)
    for bars in candles.values():
        for versions in bars.values():
            versions.sort(key=lambda r: r.available_ms)

    membership = []
    mseen: dict = {}
    for i, m in enumerate(raw.get("membership", [])):
        try:
            sym, f, t = m["symbol"], m["from_ms"], m.get("to_ms")
        except (KeyError, TypeError, AttributeError):
            reject("membership", i, "missing_field")
            continue
        avail = m.get("available_ms", f)
        if (not isinstance(sym, str) or not sym or not _is_ts(f) or not _is_ts(avail)
                or (t is not None and (not _is_ts(t) or t <= f))):
            reject("membership", i, "bad_interval")
            continue
        rec = Membership(sym, f, t, avail, str(m.get("source", "input")))
        key = (sym, rec.source, f, avail)
        clash = mseen.get(key)
        if clash is None:
            mseen[key] = rec
            membership.append(rec)
        elif clash != rec:
            raise ValueError(f"conflicting membership revisions for {key}")
    membership.sort(key=lambda r: (r.available_ms, r.symbol, r.source, r.from_ms))

    participation: dict = {}
    pseen: dict = {}
    for i, p in enumerate(raw.get("participation", [])):
        try:
            sym, ev, val = p["symbol"], p["event_ms"], p["value"]
        except (KeyError, TypeError):
            reject("participation", i, "missing_field")
            continue
        avail = p.get("available_ms", ev)
        if not isinstance(sym, str) or not _is_ts(ev) or not _is_ts(avail) or avail < ev:
            reject("participation", i, "bad_timestamp")
            continue
        if not _is_num(val) or val <= 0:
            reject("participation", i, "non_finite")
            continue
        rec = Participation(sym, str(p.get("kind", "open_interest")), ev, avail,
                            float(val), str(p.get("source", "input")))
        key = (sym, rec.kind, rec.source, ev, avail)
        clash = pseen.get(key)
        if clash is None:
            pseen[key] = rec
            participation.setdefault(sym, []).append(rec)
        elif clash != rec:
            raise ValueError("conflicting participation revisions for "
                             f"{(sym, rec.kind, rec.source, ev, avail)}")
    for lst in participation.values():
        lst.sort(key=lambda r: (r.event_ms, r.available_ms, r.kind, r.source))

    positioning, invalid = _positioning(raw, reject)
    return Dataset(tf, tf_ms, candles, membership, participation,
                   sorted(set(decisions)), rejected, positioning, invalid)


def _positioning(raw, reject):
    if "positioning" not in raw:
        return None, {}
    records = raw["positioning"]
    if not isinstance(records, list):
        raise ValueError("positioning must be a list")
    out: dict = {}
    invalid: dict = {}
    seen: dict = {}

    def bad(i, reason, sym=None, series=None):
        reject("positioning", i, reason)
        if isinstance(sym, str) and series in POSITIONING_SERIES:
            invalid.setdefault((sym, series), reason)

    for i, p in enumerate(records):
        sym = p.get("symbol") if isinstance(p, dict) else None
        series = p.get("series") if isinstance(p, dict) else None
        if not isinstance(p, dict) or any(k not in p for k in ("symbol", "series", "ts", "value")):
            bad(i, "missing_field", sym, series)
            continue
        if not isinstance(sym, str) or series not in POSITIONING_SERIES:
            bad(i, "unknown_series")
            continue
        ts, val = p["ts"], p["value"]
        if not _is_ts(ts):
            bad(i, "bad_timestamp", sym, series)
            continue
        if not _is_num(val):
            bad(i, "non_finite", sym, series)
            continue
        rec = PositioningPoint(sym, series, ts, float(val))
        clash = seen.get((sym, series, ts))
        if clash is None:
            seen[(sym, series, ts)] = rec
            out.setdefault(sym, {}).setdefault(series, []).append(rec)
        elif clash == rec:
            reject("positioning", i, "duplicate")
        else:
            bad(i, "conflicting_revision", sym, series)
    for by_series in out.values():
        for lst in by_series.values():
            lst.sort(key=lambda r: r.ts)
    return out, invalid
