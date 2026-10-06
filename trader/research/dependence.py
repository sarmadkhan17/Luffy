"""Receipts for one joint timing hypothesis; no independence fallback.

Return correlation from WRLD-07 is context, not calibrated correlation of
null-percentile votes. Effective n uses matched null ranks; common rotation
preserves *all* symbol dependence, including relationships not yet measured.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json

import numpy as np
import pandas as pd

from . import portfolio_null as pn, referee
from .evaluate import _clock, _TF_SECONDS


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                            allow_nan=False).encode()).hexdigest()


def _array(value):
    if value is None:
        return None
    a = np.ascontiguousarray(value)
    return {"dtype": a.dtype.str, "shape": list(a.shape),
            "sha256": sha256(a.tobytes()).hexdigest()}


def source_lineage(value):
    """Fingerprint named upstream sources without converting shared refs into votes."""
    if isinstance(value, pd.DataFrame):
        return {"columns": list(value.columns),
                "dtypes": [str(t) for t in value.dtypes],
                "sha256": sha256(pd.util.hash_pandas_object(
                    value, index=False).to_numpy().tobytes()).hexdigest()}
    if isinstance(value, np.ndarray):
        return _array(value)
    if isinstance(value, dict):
        return {str(k): source_lineage(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [source_lineage(v) for v in value]
    return value


def contributor(leg):
    # Include every input column, plus its column order/dtype contract.
    frame = {"columns": list(leg.df.columns),
             "dtypes": [str(t) for t in leg.df.dtypes],
             "sha256": sha256(pd.util.hash_pandas_object(
                 leg.df, index=False).to_numpy().tobytes()).hexdigest()}
    inputs = {"frame": frame, "long": _array(np.asarray(leg.long, bool)),
              "short": _array(np.asarray(leg.short, bool)),
              "funding": _array(leg.funding), "first_bar": leg.first_bar}
    return {"symbol": leg.symbol, "evidence_id": _digest(inputs), "inputs": inputs}


def relationship_controls(world, cuts):
    """Use exact bar cuts only. Retain accepted and rejected relationship wires.

    Endpoint identities remain source identities: no guessed mapping from an
    exchange instrument ID to a strategy symbol. These receipts select the
    measured dependency context for the common control, not independent votes.
    """
    out = {"source": None if world is None else list(world.context_id),
           "cuts": [], "control": "common-offset-all-contributors"}
    if world is None:
        out["status"] = "SOURCE_NOT_RETAINED; common control still required"
        return out
    # Every exact retained cut in the scored grid is considered, never latest.
    for cut in cuts:
        if world.as_of_ms is not None and cut > world.as_of_ms:
            continue
        try:
            model = world.history.get_exact(int(cut))
        except LookupError:
            continue
        rels = () if model.relationships is None else model.relationships.relationships
        records = [{"current": r.is_current_at(int(cut)), "record": r.to_dict(),
                    "evidence_records": [o.to_dict() for o in r.evidence]}
                   for r in rels]
        groups = [r.to_dict()["measurement"]["scope"] for r in rels
                  if r.coordinate.kind == "rolling-correlation" and r.is_current_at(int(cut))]
        out["cuts"].append({"as_of_ms": int(cut), "model_id": model.model_id,
                            "relationships": records, "measured_control_groups": groups})
    out["status"] = "EXACT" if out["cuts"] else "EXACT_CONTEXT_UNAVAILABLE"
    return out


def assess_joint(legs, exit_spec, risk, tf, *, hypothesis, world=None, sources=None,
                 equity=2000.0, risk_pct=.5, max_open=8, seed=17,
                 consistency_draws=40, dependence_draws=80, rotation_draws=399):
    """Frozen-input dependence receipt for a timing claim on a shared grid.

    Alias copies of exact outcome inputs contribute once; all contributors are
    retained. Different outcomes sharing a market are handled by the joint
    null, rather than by an assumed permanent return-correlation coefficient.
    """
    legs = sorted(legs, key=lambda l: l.symbol)
    lineage = [contributor(l) for l in legs]
    protocol = {"version": "joint-timing.v1", "hypothesis": hypothesis,
                "risk": risk, "exit": asdict(exit_spec), "tf": tf,
                "exit_semantics_id": getattr(exit_spec, "_exit_semantics_id", None),
                "equity": equity, "risk_pct": risk_pct, "max_open": max_open,
                "seed": seed, "consistency_draws": consistency_draws,
                "dependence_draws": dependence_draws, "rotation_draws": rotation_draws,
                "statistic": "dependence-corrected symbol consistency AND portfolio total_pct",
                "preserves": "calendar, costs, funding, signal spacing and joint symbol alignment",
                "changes": "common entry/market time alignment",
                "relationship_use": "exact-cut context; return correlation is not null-vote rho",
                "independent_shuffles": "not an admissible substitute"}
    out = {"protocol": protocol, "contributors": lineage,
           "upstream_sources": source_lineage(sources or {}), "percentiles": {},
           "consistency_p_dep": None, "common_rotation": {"p": None}}
    def finish(reason=None):
        if reason:
            out["reason"] = reason
        out["receipt_id"] = _digest(out)
        return out
    if len({l.symbol for l in legs}) != len(legs):
        return finish("duplicate contributor symbols")
    unique = {}
    for l, ref in zip(legs, lineage):
        unique.setdefault(ref["evidence_id"], l)
    legs = list(unique.values())
    out["unique_outcome_inputs"] = len(legs)
    if not legs:
        return finish("no contributor evidence")
    clocks = [_clock(l.df) for l in legs]
    if any(not np.array_equal(c, clocks[0]) or l.first_bar != legs[0].first_bar
           for c, l in zip(clocks, legs)):
        return finish("unaligned symbol calendars")
    if len(clocks[0]) < 2 or np.any(np.diff(clocks[0]) != _TF_SECONDS[tf]):
        return finish("irregular common calendar")
    out["relationships"] = relationship_controls(world,
        clocks[0][legs[0].first_bar:] * 1000)
    if rotation_draws < pn.MIN_DRAWS:
        return finish("insufficient common-control draws")
    # No upstream cached trade tables may substitute for these frozen inputs.
    for l in legs:
        l.table = None
        l._prep_key = None
    reading = referee.consistency(legs, exit_spec, risk, tf, draws=consistency_draws,
        seed=seed, dependence_draws=dependence_draws)
    out.update({k: v for k, v in reading.items() if k != "contributors"})
    out["percentiles"] = {s: r["null_pctile"] for s, r in reading["symbols"].items()
                          if "null_pctile" in r}
    out["common_rotation"] = pn.common_rotation(legs, exit_spec, risk, tf,
        equity, risk_pct, max_open, draws=rotation_draws, seed=seed)
    return finish()
