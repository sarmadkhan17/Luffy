"""Shadow WorldModel producer for one Attention scan — world-model-live.v1.

Builds the existing typed ``WorldModel`` (trader/world) at the scan's exact cut
from the scan's own detached, closed-bar candle input, read through the same
point-in-time ``Dataset.bar_asof`` Attention uses. Nothing else is read: no
network, clock, journal, DataFeed, LLM, Risk, Execution or order path.

Per scan member it records, in one ``WorldState``:

- ``closed_bar_volume_window`` — the N+1 closed-bar volumes ending at the
  anchor bar (N = Attention's baseline window), an ordinary Observation whose
  ``source_ref`` hashes the exact candle rows it copied;
- ``volume_anomaly`` (INTRADAY, unit ``z_score``) — derived by ``perceive``
  with PERCEPTION_NAME, whose transform is Attention's own volume_z formula
  (``cognition/attention.py``). It is therefore the SAME existing justified
  component, not a new salience number: when it is accepted it ties the
  candle ``volume_anomaly`` component exactly and never changes a rank, a
  selection or the dominant family.

A member whose window cannot be read gets one explicit non-VALID
``volume_anomaly`` instead, never a filled value: STALE (anchor bar absent),
MISSING (hole or short history), INVALID (malformed volume or undefined z).

``evaluate`` wraps the existing ``evaluate_snapshot(event, world_model)``
seam. Any producer failure or a model whose cut is not the scan cut degrades
to the legacy evaluation with an explicit receipt status; it never raises
into the Attention worker. The receipt carries the exact ``WorldModelRecord``
JSON so consumers replay the identical model.

Observational only: no trade direction, sizing, order, Risk, allocation or
control authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics

from trader.cognition.attention import CognitionConfig
from trader.cognition.contracts import load_input
from trader.world import (HierarchyNode, Horizon, InputRequirement, Measurement,
                          Observation, PerceptionSpec, Quality, Scope, ScopeLevel,
                          WorldModel, WorldModelRecord, WorldState, perceive)

RECEIPT_SCHEMA = "world-model-live-receipt.v1"
PRODUCER_ID = "attention-scan-world-producer.v1"
AUTHORITY = "NONE"
WINDOW_KIND = "closed_bar_volume_window"
OUTPUT_KIND = "volume_anomaly"          # cognition.attention.WORLD_KIND
PERCEPTION_NAME = "attention_volume_z"
PERCEPTION_VERSION = "1"
SOURCE = "attention.capture"
GLOBAL = Scope(ScopeLevel.GLOBAL, "world")
ASSET_CLASS = Scope(ScopeLevel.ASSET_CLASS, "crypto")

OK, UNAVAILABLE, REFUSED = "ok", "unavailable", "refused"


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _volume_z(inputs) -> Measurement:
    """Attention's volume_z on the same N+1 volumes, same arithmetic order."""
    volumes = inputs["window"].value
    lv = [math.log1p(v) for v in volumes]
    hist = lv[:-1]
    v_sd = statistics.stdev(hist)
    z = (lv[-1] - statistics.fmean(hist)) / v_sd if v_sd > 0 else None
    if z is None or not math.isfinite(z):
        return Measurement(None, Quality.INVALID,
                           uncertainty={"reason": "undefined_zero_or_nonfinite_variance"})
    return Measurement(z)


def perception_spec(timeframe: str, tf_ms: int) -> PerceptionSpec:
    return PerceptionSpec(
        name=PERCEPTION_NAME, version=PERCEPTION_VERSION,
        inputs=(InputRequirement("window", WINDOW_KIND, timeframe),),
        output_kind=OUTPUT_KIND, output_timeframe=timeframe, output_unit="z_score",
        output_horizon=Horizon.INTRADAY.value, output_max_age_ms=tf_ms,
        transform=_volume_z)


def _unusable(sym, as_of, timeframe, quality, reason, event_ms=None):
    return Observation(instrument=sym, timestamp_ms=as_of if event_ms is None else event_ms,
                       observed_at_ms=as_of, timeframe=timeframe,
                       horizon=Horizon.INTRADAY.value, kind=OUTPUT_KIND, value=None,
                       unit="z_score", source=SOURCE, source_ref=f"{PRODUCER_ID}:{reason}",
                       quality=quality, uncertainty={"reason": reason},
                       transform_version=PERCEPTION_VERSION)


def _member_state(ds, sym, as_of, scan_id, spec, window):
    tf = ds.tf_ms
    anchor_close = (as_of // tf) * tf
    last_open = anchor_close - tf
    opens = [last_open - (window - j) * tf for j in range(window + 1)]
    bars = [ds.bar_asof(sym, t, as_of) for t in opens]
    if bars[-1] is None:
        obs = _unusable(sym, as_of, ds.timeframe, Quality.STALE, "anchor_bar_unavailable")
        return WorldState(sym, as_of, (obs,)), obs
    if any(b is None for b in bars):
        obs = _unusable(sym, as_of, ds.timeframe, Quality.MISSING, "volume_window_incomplete",
                        bars[-1].close_ms)
        return WorldState(sym, as_of, (obs,)), obs
    volumes = [b.volume for b in bars]
    if not all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in volumes):
        obs = _unusable(sym, as_of, ds.timeframe, Quality.INVALID, "malformed_volume",
                        bars[-1].close_ms)
        return WorldState(sym, as_of, (obs,)), obs
    rows = [[b.open_ms, b.volume, b.available_ms, b.source] for b in bars]
    ref = hashlib.sha256(_canonical({"scan_id": scan_id, "symbol": sym,
                                     "rows": rows}).encode()).hexdigest()
    win = Observation(instrument=sym, timestamp_ms=bars[-1].close_ms, observed_at_ms=as_of,
                      available_at_ms=max(b.available_ms for b in bars),
                      timeframe=ds.timeframe, kind=WINDOW_KIND, value=volumes,
                      unit="venue_volume", source=SOURCE,
                      source_ref=f"attention-scan:{scan_id}:closed_bars:{ref}",
                      quality=Quality.VALID, max_age_ms=tf)
    derived = perceive(spec, WorldState(sym, as_of, (win,)))
    return WorldState(sym, as_of, (win, derived)), derived


def build(ds, as_of_ms: int, scan_id: str) -> WorldModel:
    """The WorldModel at exactly `as_of_ms` for every scan member. Pure."""
    window = CognitionConfig().window
    spec = perception_spec(ds.timeframe, ds.tf_ms)
    members = ds.members_asof(as_of_ms)
    states = [_member_state(ds, sym, as_of_ms, scan_id, spec, window)[0] for sym in members]
    nodes = [HierarchyNode(GLOBAL), HierarchyNode(ASSET_CLASS, GLOBAL),
             *(HierarchyNode(Scope(ScopeLevel.INSTRUMENT, s), ASSET_CLASS) for s in members)]
    return WorldModel(as_of_ms, nodes, states)


def receipt(status, reason, as_of_ms, record=None, counts=None) -> dict:
    return {"schema": RECEIPT_SCHEMA, "producer_id": PRODUCER_ID, "authority": AUTHORITY,
            "status": status, "reason": reason, "as_of_ms": as_of_ms,
            "perception": {"name": PERCEPTION_NAME, "version": PERCEPTION_VERSION,
                           "output_kind": OUTPUT_KIND, "input_kind": WINDOW_KIND},
            "model_id": record.model_id if record else None,
            "record_id": record.record_id if record else None,
            "record_json": record.to_json() if record else None,
            "observation_counts": counts or {}}


def _counts(model: WorldModel) -> dict:
    out: dict = {}
    for state in model.states:
        for obs in state.observations:
            if obs.kind == OUTPUT_KIND:
                out[obs.quality.value] = out.get(obs.quality.value, 0) + 1
    return dict(sorted(out.items()))


def produce(event) -> tuple[WorldModel | None, dict]:
    """(model, receipt) for one captured scan event; never raises."""
    as_of = event.get("as_of_ms")
    try:
        ds = load_input(event["input"])
        model = build(ds, as_of, event["scan_id"])
        record = WorldModelRecord.from_model(model)
    except Exception as exc:        # degrade to legacy Attention, by reason code
        return None, receipt(UNAVAILABLE, "producer_failed:" + type(exc).__name__, as_of)
    return model, receipt(OK, None, as_of, record, _counts(model))


def evaluate(event, *, model=None, learning_journal=None):
    """Attention payload through the existing WorldModel seam, plus receipt.

    `model` is injectable for tests (e.g. a stale cut); by default it is
    produced from the event. A refused model yields the legacy payload."""
    from trader.observability.attention import evaluate_snapshot
    if model is None:
        model, rec = produce(event)
    else:
        rec = receipt(OK, None, event["as_of_ms"], WorldModelRecord.from_model(model),
                      _counts(model))
    if model is None:
        return dict(evaluate_snapshot(event, learning_journal=learning_journal), world_model=rec)
    if model.as_of_ms != event["as_of_ms"]:
        stale = receipt(REFUSED, "world_model_cut_mismatch", event["as_of_ms"])
        stale.update(model_id=model.model_id, refused_as_of_ms=model.as_of_ms)
        return dict(evaluate_snapshot(event, learning_journal=learning_journal), world_model=stale)
    try:
        return dict(evaluate_snapshot(event, model, learning_journal), world_model=rec)
    except Exception as exc:        # never lose the scan to the shadow input
        failed = receipt(REFUSED, "attention_world_evaluation_failed:" + type(exc).__name__,
                         event["as_of_ms"])
        failed.update(model_id=model.model_id)
        return dict(evaluate_snapshot(event, learning_journal=learning_journal), world_model=failed)


class ReplayRefused(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def replay(scan, ds) -> WorldModel | None:
    """The exact model a persisted scan was evaluated with, or None when the
    scan carries no accepted model. The stored record must verify, match its
    receipt ids and cut, and be re-derived byte-for-byte from the replayed
    candles; anything else is refused, never substituted."""
    rec = scan.get("world_model")
    if rec is None:
        return None
    if not isinstance(rec, dict) or rec.get("schema") != RECEIPT_SCHEMA:
        raise ReplayRefused("world_model_receipt_invalid")
    if rec.get("status") != OK:
        return None
    try:
        record = WorldModelRecord.from_json(rec["record_json"])
        model = record.reconstruct()
    except (TypeError, KeyError, ValueError) as exc:
        raise ReplayRefused("world_model_record_corrupt") from exc
    if (record.model_id, record.record_id) != (rec.get("model_id"), rec.get("record_id")) \
            or model.as_of_ms != scan["as_of_ms"]:
        raise ReplayRefused("world_model_receipt_mismatch")
    try:
        again = build(ds, scan["as_of_ms"], scan["scan_id"])
    except Exception as exc:
        raise ReplayRefused("world_model_rederivation_failed") from exc
    if again.model_id != model.model_id:
        raise ReplayRefused("world_model_rederivation_mismatch")
    return model


def record_of(scan) -> WorldModelRecord | None:
    """The persisted WorldModelRecord of an accepted receipt, else None."""
    rec = scan.get("world_model")
    if not isinstance(rec, dict) or rec.get("status") != OK:
        return None
    return WorldModelRecord.from_json(rec["record_json"])
