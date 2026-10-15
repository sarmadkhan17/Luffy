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

import numpy as np

from trader.cognition.attention import CognitionConfig, evaluate
from trader.cognition.contracts import (CORRELATION_CAPTURE_CLOSES, INPUT_SCHEMA, TF_MS,
                                       load_input, to_dict)
from trader.world.model import WorldModel


SCHEMA = "attention.telemetry.v1"
# Shared with entry_authority.capability: retain the existing freshness policy
# even though the supplemental registry fetch workflow has been retired.
MAX_SNAPSHOT_AGE_MS = 28_800_000
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
    # Shadow WorldModel input to Attention (observability/world_producer.py).
    # Recorded in capture settings only when on, so legacy settings ids hold.
    world = raw.get("world_model", False)
    if type(world) is not bool:
        raise ValueError("invalid attention world_model")
    if world:
        out["world_model"] = True
    return out


def _number(value):
    value = float(value)
    return value if math.isfinite(value) else None


def _history(symbol, df, tf_ms, as_of):
    """Bounded close-only history for correlation_change: the last
    CORRELATION_CAPTURE_CLOSES fully closed bars of the frame, verified
    consecutive on the exact grid. Anything else is an explicit status with
    no closes; nothing is filled or interpolated."""
    def rec(status, first=None, last=None, closes=()):
        return {"symbol": symbol, "status": status, "first_open_ms": first,
                "last_open_ms": last, "closes": list(closes)}

    if df is None or not len(df):
        return rec("missing_timeframe")
    n = CORRELATION_CAPTURE_CLOSES
    try:
        # Vectorized over backing arrays: this runs on the producer thread.
        opens = df["ts"].array[-(n + 2):].to_numpy(dtype="datetime64[ms]").astype("int64")
        closes = np.asarray(df["close"].array[-(n + 2):], dtype="float64")
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return rec("invalid_frame")
    if (np.diff(opens) <= 0).any():
        return rec("malformed")
    # A still-forming bar never enters the history.
    closed = opens + tf_ms <= as_of
    opens, closes = opens[closed][-n:], closes[closed][-n:]
    if len(opens) < n:
        return rec("warmup")
    if (np.diff(opens) != tf_ms).any():
        return rec("gap")
    if not (np.isfinite(closes) & (closes > 0)).all():
        return rec("non_finite")
    return rec("ok", int(opens[0]), int(opens[-1]), closes.tolist())


def capture(frames, members, scan_id, cfg, as_of_ms=None, *, membership_receipts=None):
    """Detach a bounded telemetry snapshot of the supplied membership.

    Runtime admission uses its separate full-cohort capture and frozen receipt.
    Historical supplemental events replay through evaluate_snapshot from their
    retained inputs; there is no supplemental acquisition path.
    """
    started = time.perf_counter()
    tf = cfg["timeframe"]
    tf_ms = TF_MS[tf]
    as_of = int(time.time() * 1000) if as_of_ms is None else as_of_ms
    need = CognitionConfig().window + CognitionConfig().short + 1
    symbols = list(dict.fromkeys(members))
    membership_issues = []
    if membership_receipts is not None:
        safe = []
        for symbol in symbols:
            r = membership_receipts.get(symbol)
            if (r and r.get('quality') == 'VALID' and
                    r.get('available_at_ms', as_of+1) <= as_of and
                    r.get('observed_at_ms', as_of+1) <= as_of):
                safe.append(symbol)
            else:
                membership_issues.append(dict(symbol=symbol, reason='unavailable_membership'))
        symbols = safe
    included = symbols[:cfg["max_symbols"]]
    candles, issues, histories = [], membership_issues, []
    sources = [(symbol, (frames.get(symbol) or {}).get(tf), "kernel.universe_frames")
               for symbol in included]
    for symbol, df, source in sources:
        if df is not None:
            from trader.data.market_provenance import META, cut, ms
            import pandas as pd
            cut(as_of)
            if not all(key in df for key in META):
                df = None
            else:
                # Capture keeps only VALID final rows. Apply the same temporal
                # boundary directly, without copying the entire frame three
                # times or assigning NaN to rows that capture discards.
                known = (pd.to_numeric(df['available_at_ms'], errors='coerce').le(as_of) &
                         pd.to_numeric(df['observed_at_ms'], errors='coerce').le(as_of) &
                         pd.to_numeric(df['event_time_ms'], errors='coerce').le(as_of) &
                         df['bar_state'].eq('FINAL') & df['quality'].eq('VALID') &
                         (ms(df['ts']) + tf_ms <= as_of))
                df = df.loc[known]
        histories.append(_history(symbol, df, tf_ms, as_of))
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
            proven = df is not None and 'revision_id' in df
            receipts = df['available_at_ms'].array[-tail_n:].tolist() if proven else [as_of]*len(opens)
            # Only receipt columns are needed. Avoid per-row pandas Series and
            # their attrs copies; OHLCV values are detached above.
            retained = [dict(zip(META, values)) for values in zip(
                *(df[key].array[-tail_n:].tolist() for key in META))]
            refs = [(str(row['source']) + '|instrument=' + str(row['instrument_id']) +
                     '|revision=' + str(row['revision_id'])) for row in retained] if proven else [source]*len(opens)
            for index, (ms, available, source_ref, *values) in enumerate(zip(opens, receipts, refs, *columns)):
                if ms <= previous:
                    raise ValueError("unordered candle tail")
                previous = ms
                if ms + tf_ms > as_of:
                    continue
                rows.append({"symbol": symbol, "open_ms": ms,
                             **dict(zip(FIELDS, map(_number, values))),
                             "available_ms": int(available), "source": source_ref})
                from trader.data.market_provenance import receipt_metadata
                receipt = retained[index]
                rows[-1]['source_receipt'] = receipt_metadata(receipt)
            candles.extend(rows[-need:])
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            issues.append({"symbol": symbol, "reason": "invalid_frame"})
    if as_of_ms is None:
        # Availability is when this process holds the detached values, not
        # the earlier instant at which copying began.
        as_of = int(time.time() * 1000)
        for candle in candles:
            candle["available_ms"] = max(candle["available_ms"], as_of)
    membership = [{"symbol": sym, "from_ms": as_of, "to_ms": None,
                   "available_ms": as_of, "source": "kernel.scan_symbols"}
                  for sym in included]
    if membership_receipts is not None:
        import copy
        for row in membership:
            row['source_receipt'] = copy.deepcopy(membership_receipts[row['symbol']])
    scope = {"kind": "strategy_volume_subset", "candidate_count": len(symbols),
             "included_count": len(included), "cap": cfg["max_symbols"],
             "excluded_count": max(0, len(symbols) - len(included)),
             "exclusions": "trailing candidates beyond cap; not venue-wide",
             "universe_membership_before_capture": "unknown",
             "upstream_endpoint_and_cache_layer": "retained_market_revision_when_supplied"}
    return {
        "kind": "scan", "schema_version": SCHEMA, "scan_id": scan_id,
        "as_of_ms": as_of, "capture_settings": dict(cfg), "capture_ms": (time.perf_counter() - started) * 1000,
        "scope": scope,
        "issues": issues,
        "input": {"schema": INPUT_SCHEMA, "timeframe": tf,
                  "decision_times": [as_of], "candles": candles,
                  "membership": membership, "participation": [],
                  "correlation_history": histories},
    }


def evaluate_snapshot(event, world_model: WorldModel | None = None, learning_journal=None, *, _dataset=None):
    if world_model is not None:
        if not isinstance(world_model, WorldModel):
            raise TypeError("world_model must be a WorldModel or None")
        if world_model.as_of_ms != event["as_of_ms"]:
            raise ValueError("world_model cut does not match as_of")
    cfg = CognitionConfig(max_symbols=len(event["input"]["membership"]) or 1)
    # The producer may pass the same detached Dataset to share exact-window
    # measurements. This private seam changes no captured/replay contract.
    ds = load_input(event["input"]) if _dataset is None else _dataset
    result = evaluate(ds, event["as_of_ms"], cfg, event["scan_id"], {}, world_model, learning_journal, event.get('governed_attention_state'), event.get('governed_world_claim_state'))
    learned = {'governed_attention_state':result['governed_attention_state']}
    if result['governed_world_claim_state']:
        learned['governed_world_claim_state'] = result['governed_world_claim_state']
    return {**learned, "schema_version": SCHEMA, "scan_id": event["scan_id"],
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
                            "No hypotheses, edge claims or learned ranking are produced."]
            + (["Positioning uses retained revisions eligible at the exact as-of cut."]
               if "positioning" in event["input"] else [])
            + (["Correlation change is a deterministic Fisher-scaled correlation-change feature of "
                "each asset against its captured leave-one-out peer basket (recent 30 vs prior 120 "
                "returns; on 4h roughly 5 vs 20 days); it is not a calibrated test, the basket is "
                "not the crypto market, and a later scan's basket may differ."]
               if "correlation_history" in event["input"] else [])}


def digest(value):
    import json
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def code_manifest():
    base = Path(__file__).resolve().parents[1]
    names = ("observability/attention.py",
             "data/registry_provider.py", "data/binance_usdm_registry.py",
             "core/instrument_registry.py", "observability/registry_selector.py",
             "observability/selection_persistence.py", "core/journal.py",
             "observability/store.py", "data/market_provenance.py",
             "observability/collector.py", "observability/worker.py",
             "observability/declared.py",
             "cognition/attention.py", "cognition/contracts.py",
             "observability/positioning.py", "observability/world_producer.py",
             "observability/diagnostics.py", "strategy/library.py", "strategy/compile.py",
             "engine/orchestrator.py", "kernel.py", "learning/targets.py", "learning/consumers.py")
    return {name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in names}
