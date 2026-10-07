"""DEC-01 analyst evidence for one decision-grade Opportunity Context.

Pure and read-only: no clock, network, Journal write or authority. Takes the
typed `analyst.measurement.v1` packets the Orchestrator already persisted for
ONE decision cycle and the declared required/optional roster, verifies them
and partitions them against the candidate's direction.

- A REQUIRED analyst that is absent or has no usable measurement BLOCKS.
- An OPTIONAL analyst that is absent/unusable is recorded explicitly as
  absent and never blocks, and never becomes required through any other path.
- All packets must share the decision cycle's single analyst cut; a packet
  from another cut REFUSES (no nearest/latest fallback).
- Supporting and opposing measurements are both retained, individually
  attributed. Conflict is flagged, never collapsed into one narrative.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from trader.agents.measurement import Measurement
from trader.engine.protective import venue_key

SCHEMA = "decision-analysts.v1"
#: the analyst roster the Kernel instantiates (pinned to kernel.py by test)
ENABLED = ("structure", "momentum", "flow", "value", "rotation", "positioning", "depth")
#: the Orchestrator's own FLAT band for a measured strength
STANCE_BAND = 0.05
_FIELDS = frozenset(("schema", "decision_id", "cycle_id", "symbol", "cut_ms", "cut_ts",
                     "roster", "packets"))


class ContextRefused(ValueError):
    """Evidence cannot be bound coherently (wrong identity, mixed/future cut)."""


class ContextBlocked(ValueError):
    """A REQUIRED input is missing/stale; no context or decision may proceed."""


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256(value) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def iso_ms(ts) -> int:
    """Exact epoch milliseconds of a timezone-aware ISO timestamp."""
    if not isinstance(ts, str):
        raise ContextRefused("ANALYST_CUT_INVALID")
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        raise ContextRefused("ANALYST_CUT_INVALID") from None
    if dt.tzinfo is None:
        raise ContextRefused("ANALYST_CUT_INVALID")
    return (dt.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)) // timedelta(milliseconds=1)


def roster(config) -> dict:
    """Declared roster: `decision.context.required_analysts` (default none);
    every other enabled analyst is optional."""
    declared = (((config or {}).get("decision") or {}).get("context") or {}).get("required_analysts") or []
    if (not isinstance(declared, (list, tuple)) or len(set(declared)) != len(declared)
            or not set(declared) <= set(ENABLED)):
        raise ContextRefused("ANALYST_ROSTER_INVALID")
    return {"required": sorted(declared), "optional": sorted(set(ENABLED) - set(declared))}


def bundle(*, decision_id, cycle_id, symbol, cut_ts, roster, packets) -> dict:
    """The frozen source payload. `packets` are Measurement.to_dict() records."""
    return {"schema": SCHEMA, "decision_id": decision_id, "cycle_id": cycle_id, "symbol": symbol,
            "cut_ms": iso_ms(cut_ts), "cut_ts": cut_ts, "roster": roster,
            "packets": list(packets)}


def _entry(name, role, m: Measurement) -> dict:
    return {"analyst": name, "role": role, "record_id": m.record_id, "quality": m.quality.value,
            "strength": m.strength, "confidence": m.uncertainty["confidence"],
            "uncertainty_basis": m.uncertainty["basis"], "horizon": m.horizon,
            "limitations": list(m.limitations), "inputs": [dict(i) for i in m.inputs]}


def analyze(data, *, symbol, as_of_ms, direction) -> dict:
    """Verify one analyst bundle and partition its evidence.

    `direction` is +1 (BUY), -1 (SELL) or None (cannot classify)."""
    if not isinstance(data, dict) or set(data) != _FIELDS or data["schema"] != SCHEMA:
        raise ContextRefused("ANALYST_BUNDLE_INVALID")
    ros = data["roster"]
    if (not isinstance(ros, dict) or set(ros) != {"required", "optional"}
            or not all(isinstance(ros[k], list) and all(isinstance(n, str) and n for n in ros[k])
                       for k in ros)
            or set(ros["required"]) & set(ros["optional"])
            or len(set(ros["required"])) != len(ros["required"])
            or len(set(ros["optional"])) != len(ros["optional"])
            or not (ros["required"] or ros["optional"])):
        raise ContextRefused("ANALYST_ROSTER_INVALID")
    if not all(isinstance(data[k], str) and data[k] for k in ("decision_id", "cycle_id", "symbol")):
        raise ContextRefused("ANALYST_BUNDLE_IDENTITY_INVALID")
    if venue_key(data["symbol"]) != venue_key(symbol):
        raise ContextRefused("ANALYST_INSTRUMENT_MISMATCH")
    cut = data["cut_ms"]
    if type(cut) is not int or cut != iso_ms(data["cut_ts"]):
        raise ContextRefused("ANALYST_CUT_INVALID")
    if cut > as_of_ms:
        raise ContextRefused("ANALYST_CUT_AFTER_DECISION_CUT")
    if not isinstance(data["packets"], list):
        raise ContextRefused("ANALYST_BUNDLE_INVALID")
    declared = {n: "required" for n in ros["required"]} | {n: "optional" for n in ros["optional"]}
    seen: dict[str, Measurement] = {}
    for raw in data["packets"]:
        try:
            m = Measurement.from_dict(raw)
        except (TypeError, ValueError, KeyError, AttributeError):
            raise ContextRefused("ANALYST_PACKET_INVALID") from None
        if m.analyst not in declared:
            raise ContextRefused("ANALYST_NOT_IN_ROSTER:" + m.analyst)
        if m.analyst in seen:
            raise ContextRefused("ANALYST_PACKET_DUPLICATE:" + m.analyst)
        if venue_key(m.instrument) != venue_key(symbol):
            raise ContextRefused("ANALYST_INSTRUMENT_MISMATCH")
        if iso_ms(m.observed_at) != cut:
            raise ContextRefused("ANALYST_MIXED_CUTS:" + m.analyst)
        seen[m.analyst] = m
    evidence = {"supporting": [], "opposing": [], "neutral": [], "unclassified": []}
    absent, unavailable = [], []
    for name in sorted(declared):
        role, m = declared[name], seen.get(name)
        if m is None:
            absent.append({"analyst": name, "role": role, "status": "ABSENT",
                           "reason": "analyst_packet_not_recorded"})
        elif m.strength is None:
            unavailable.append({**_entry(name, role, m), "status": "UNAVAILABLE",
                                "reason": m.rationale})
        elif direction is None:
            evidence["unclassified"].append(_entry(name, role, m))
        else:
            signed = m.strength * direction
            key = ("supporting" if signed > STANCE_BAND else
                   "opposing" if signed < -STANCE_BAND else "neutral")
            evidence[key].append(_entry(name, role, m))
    blocked = sorted(e["analyst"] for e in absent + unavailable if e["role"] == "required")
    if blocked:
        raise ContextBlocked("REQUIRED_ANALYST_UNAVAILABLE:" + ",".join(blocked))
    evidence["conflict"] = bool(evidence["supporting"] and evidence["opposing"])
    return {"schema": SCHEMA, "decision_id": data["decision_id"], "cycle_id": data["cycle_id"],
            "cut_ms": cut, "cut_ts": data["cut_ts"], "roster": ros, "evidence": evidence,
            "absence": {"absent": absent, "unavailable": unavailable},
            "bundle_sha256": sha256(data)}
