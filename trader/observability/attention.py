"""Bounded snapshot adapter around the offline attention evaluator.

Only allowlisted numeric candle fields cross the process boundary. Availability
means first observed by this collector, never inferred from a candle's close.
"""
from __future__ import annotations

import math
from dataclasses import asdict
from pathlib import Path
import hashlib
import time

from trader.cognition.attention import CognitionConfig, evaluate
from trader.cognition.contracts import INPUT_SCHEMA, TF_MS, load_input, to_dict
from trader.world.model import WorldModel


SCHEMA = "attention.telemetry.v1"
FIELDS = ("open", "high", "low", "close", "volume")


def settings(raw=None):
    raw = raw or {}
    out = {"timeframe": raw.get("timeframe", "4h")}
    if out["timeframe"] not in TF_MS:
        raise ValueError("unsupported attention timeframe")
    for name, default, lo, hi in (
        ("max_symbols", 16, 2, 64), ("queue_size", 16, 1, 128),
        ("max_causes", 64, 1, 256), ("max_scans", 512, 1, 10080),
        ("max_bytes", 64 * 1024 * 1024, 65536, 1024 * 1024 * 1024),
        ("max_age_seconds", 604800, 60, 2592000),
        ("timeout_seconds", 10, 1, 60), ("stale_seconds", 300, 10, 86400),
    ):
        value = raw.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise ValueError(f"invalid attention {name}")
        out[name] = value
    return out


def _number(value):
    value = float(value)
    return value if math.isfinite(value) else None


def capture(frames, members, scan_id, cfg, as_of_ms=None, supplemental=None):
    """Detach a bounded primitive snapshot; never pass mutable DataFrames on.

    `supplemental` is at most one observation-only SupplementalResult for a
    symbol outside the strategy scan. It sits outside the strategy-subset
    cap; a usable window joins the capture input, any other status is
    recorded as an issue without candles or membership.
    """
    started = time.perf_counter()
    tf = cfg["timeframe"]
    tf_ms = TF_MS[tf]
    as_of = int(time.time() * 1000) if as_of_ms is None else as_of_ms
    need = CognitionConfig().window + CognitionConfig().short + 1
    symbols = list(dict.fromkeys(members))
    included = symbols[:cfg["max_symbols"]]
    candles, issues = [], []
    sources = [(symbol, (frames.get(symbol) or {}).get(tf), "kernel.universe_frames")
               for symbol in included]
    extra = None
    if supplemental is not None:
        if supplemental.timeframe != tf:
            raise ValueError("supplemental timeframe does not match attention")
        if supplemental.symbol in symbols:
            raise ValueError("supplemental symbol is already a scan member")
        extra = supplemental.summary()
        if supplemental.usable:
            sources.append((supplemental.symbol, supplemental.frame, supplemental.source))
        else:
            issues.append({"symbol": supplemental.symbol,
                           "reason": "supplemental_" + supplemental.status,
                           "detail": supplemental.reason})
    for symbol, df, source in sources:
        if df is None or not len(df):
            issues.append({"symbol": symbol, "reason": "missing_timeframe"})
            continue
        try:
            # DataFeed frames are chronological. A malformed tail is refused,
            # not sorted over an unbounded history on the execution thread.
            tail_n = need + 2
            # Slice backing arrays before detaching values: constructing six
            # short pandas Series per symbol dominates producer overhead.
            opens = df["ts"].array[-tail_n:].to_numpy(dtype="datetime64[ms]").astype("int64").tolist()
            columns = [df[field].array[-tail_n:].tolist() for field in FIELDS]
            rows = []
            previous = -1
            for ms, *values in zip(opens, *columns):
                if ms <= previous:
                    raise ValueError("unordered candle tail")
                previous = ms
                if ms + tf_ms > as_of:
                    continue
                rows.append({"symbol": symbol, "open_ms": ms,
                             **dict(zip(FIELDS, map(_number, values))),
                             "available_ms": as_of, "source": source})
            candles.extend(rows[-need:])
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            issues.append({"symbol": symbol, "reason": "invalid_frame"})
    if as_of_ms is None:
        # Availability is when this process holds the detached values, not
        # the earlier instant at which copying began.
        as_of = int(time.time() * 1000)
        for candle in candles:
            candle["available_ms"] = as_of
    membership = [{"symbol": sym, "from_ms": as_of, "to_ms": None,
                   "available_ms": as_of, "source": "kernel.scan_symbols"}
                  for sym in included]
    scope = {"kind": "strategy_volume_subset", "candidate_count": len(symbols),
             "included_count": len(included), "cap": cfg["max_symbols"],
             "excluded_count": max(0, len(symbols) - len(included)),
             "exclusions": "trailing candidates beyond cap; not venue-wide",
             "universe_membership_before_capture": "unknown",
             "upstream_endpoint_and_cache_layer": "not_recorded_by_DataFeed"}
    if extra is not None:
        scope["supplemental"] = extra
        if supplemental.usable:
            membership.append({"symbol": supplemental.symbol, "from_ms": as_of,
                               "to_ms": None, "available_ms": as_of,
                               "source": supplemental.source})
    return {
        "kind": "scan", "schema_version": SCHEMA, "scan_id": scan_id,
        "as_of_ms": as_of, "capture_settings": dict(cfg), "capture_ms": (time.perf_counter() - started) * 1000,
        "scope": scope,
        "issues": issues,
        "input": {"schema": INPUT_SCHEMA, "timeframe": tf,
                  "decision_times": [as_of], "candles": candles,
                  "membership": membership, "participation": []},
    }


def evaluate_snapshot(event, world_model: WorldModel | None = None):
    if world_model is not None:
        if not isinstance(world_model, WorldModel):
            raise TypeError("world_model must be a WorldModel or None")
        if world_model.as_of_ms != event["as_of_ms"]:
            raise ValueError("world_model cut does not match as_of")
    cfg = CognitionConfig(max_symbols=len(event["input"]["membership"]) or 1)
    ds = load_input(event["input"])
    result = evaluate(ds, event["as_of_ms"], cfg, event["scan_id"], {}, world_model)
    return {"schema_version": SCHEMA, "scan_id": event["scan_id"],
            "as_of_ms": event["as_of_ms"], "scope": event["scope"],
            "capture_ms": event["capture_ms"], "issues": event["issues"],
            "capture_settings": event["capture_settings"],
            "capture_settings_id": digest(event["capture_settings"]),
            "rows": result["universe"], "market": result["market"],
            "observations": [to_dict(o) for o in result["observations"]],
            "config": asdict(cfg), "config_id": digest(asdict(cfg)),
            "rejected_inputs": ds.rejected,
            "limitations": ["Attention is an observation, not a trading signal.",
                            "Availability before first capture is unknown.",
                            "First-seen and revision lineage extend only over retained snapshots.",
                            "Participation inputs are not captured in this milestone.",
                            "No hypotheses, edge claims or learned ranking are produced."]}


def digest(value):
    import json
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def code_manifest():
    base = Path(__file__).resolve().parents[1]
    names = ("observability/attention.py", "observability/supplemental.py",
             "observability/_supplemental_child.py",
             "observability/store.py",
             "observability/collector.py", "observability/worker.py",
             "observability/declared.py",
             "cognition/attention.py", "cognition/contracts.py",
             "observability/diagnostics.py", "strategy/library.py", "strategy/compile.py",
             "engine/orchestrator.py", "kernel.py")
    return {name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in names}
