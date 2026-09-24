"""Attention: which assets deserve investigation at `as_of`, and why.

Everything is computed from bars usable at `as_of` (see contracts). Let
N = `window`, S = `short`. The anchor is the newest bar that has closed by
`as_of`; an asset needs the N+S+1 consecutive closed bars ending there
(warmup). Missing the anchor bar is `stale`; missing leading bars is
`warmup`; a hole inside the window is `gap`. None of those are eligible, and
none are filled.

On the N+S log returns r (baseline = first N, recent = last S):

    sigma_long  = stdev(baseline)          sigma_short = stdev(recent)
    R_S         = sum(recent)
    volume_z    = (ln(1+v_anchor) - mean(ln(1+v) of prior N bars)) / stdev(same)
    vol_trans_z = ln(sigma_short / sigma_long) / sqrt(1/(2(S-1)) + 1/(2(N-1)))
    div_z       = (R_S - median_cohort(R_S)) / (sigma_long * sqrt(S))

`vol_trans_z` divides by the large-sample standard error of a log ratio of two
sample standard deviations, so all three read on a z-like scale. `div_z` needs
at least `min_cohort` eligible assets, otherwise it is None (not 0).
sigma_long is kept at full precision (the trace carries the exact float); an
asset whose scale is not finite and positive gets `degenerate`, and a
non-finite component is None — never a silent 0 or inf.

    salience = max(|volume_z|, |vol_trans_z|, |div_z|) over available components

With an optional `world_model` (its cut must equal `as_of`), one exact
INSTRUMENT/INTRADAY `volume_anomaly` observation per asset may add |value| as
a competing `world_volume_anomaly` component: it must be the only match, share
the dataset timeframe, sit at the anchor close, be VALID with known
availability <= as_of, carry a finite numeric value and unit `z_score`.
Anything else is recorded with an explicit status/reason and contributes
nothing. Without a model the output is unchanged.

With optional `positioning` input (Dataset.positioning not None), each
eligible asset gets one `positioning` observation per series in
POSITIONING_SERIES: anchor = latest sample with ts <= as_of, reference = the
POSITIONING_REFERENCE samples before it, z = (anchor - mean) / stdev, signed.
|z| of the usable series competes as `positioning_extreme` (max over series).
A series is unusable, with an explicit status and None, when it is missing,
invalid in the input, short of history, breaks its continuity rule, has a
stale anchor, or has zero/non-finite variance. Without the input the output is
unchanged.

Salience is direction-independent: an asset falling hard and one rising hard
score alike. Ranking is (-salience at 12 significant digits, symbol) so ties are
stable. An asset is selected if salience >= `min_salience`, it has no open
episode (dedup), and fewer than `k` are already selected. Every member keeps a
row with its reason; every eligible asset is a baseline sample.

Market state over the eligible cohort: breadth_up = share with R_S > 0,
market_z = median(R_S) / (median(sigma_long) * sqrt(S)); the move is `broad`
when |market_z| >= broad_z and max(breadth_up, 1 - breadth_up) >= broad_breadth.

CPU bound per decision: O(members * (N+S+1) * revisions), members capped at
`max_symbols`; the replay caps decisions at `max_decisions`.
"""
from __future__ import annotations

import math
import statistics
import sys
from dataclasses import dataclass
from hashlib import sha256

from trader.cognition.contracts import POSITIONING_SERIES, Observation, rnd, stable_id


@dataclass(frozen=True)
class CognitionConfig:
    window: int = 20
    short: int = 5
    horizon: int = 5                 # bars after the anchor to the deadline
    k: int = 3
    min_salience: float = 2.0
    min_cohort: int = 4
    broad_z: float = 1.5
    broad_breadth: float = 0.75
    divergence_z: float = 2.0
    persist_z: float = 1.0
    participation_change: float = 0.05
    participation_stale_bars: int = 2
    max_symbols: int = 500
    max_decisions: int = 5000

    def __post_init__(self):
        for name, lo in _INT_BOUNDS.items():
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool) or v < lo:
                raise ValueError(f"{name} must be an int >= {lo}, got {v!r}")
        for name, (lo, hi, lo_open) in _FLOAT_BOUNDS.items():
            v = getattr(self, name)
            if (not isinstance(v, (int, float)) or isinstance(v, bool)
                    or not math.isfinite(v) or v < lo or v > hi or (lo_open and v == lo)):
                raise ValueError(f"{name} must be finite in "
                                 f"{'(' if lo_open else '['}{lo}, {hi}], got {v!r}")


_INT_BOUNDS = {"window": 3, "short": 2, "horizon": 1, "k": 1, "min_cohort": 2,
               "participation_stale_bars": 0, "max_symbols": 1, "max_decisions": 1}
_FLOAT_BOUNDS = {                      # name: (low, high, low is exclusive)
    "min_salience": (0.0, 1e6, False), "broad_z": (0.0, 1e6, False),
    "broad_breadth": (0.5, 1.0, False), "divergence_z": (0.0, 1e6, True),
    "persist_z": (0.0, 1e6, True), "participation_change": (0.0, 1e6, False)}

COMPONENTS = ("volume_anomaly", "volatility_transition", "relative_return_divergence")


POSITIONING_COMPONENT = "positioning_extreme"
# Package-owned implementation policy (SDD-STAGE-3-ATTENTION-POSITIONING-
# EXTREMES-V1), not SDD-derived constants. Continuity and freshness follow the
# kernel derivatives recorder: 15-minute cadence; ls_ratio rows are 15m
# periods stamped at period END; funding rows are settlement times.
POSITIONING_REFERENCE = 20
_MIN_MS = 60_000
LS_RATIO_STEP_MS = 900_000
LS_RATIO_MAX_AGE_MS = 2_700_000           # recorder interval + period + slack
FUNDING_AGE_SLACK_MS = 1_800_000          # two recorder intervals past settlement


def _finite(x):
    return x if x is not None and math.isfinite(x) else None


WORLD_KIND = "volume_anomaly"
WORLD_COMPONENT = "world_volume_anomaly"
_QUALITY_STATUS = {"MISSING": "absent", "STALE": "stale", "UNSUPPORTED": "unsupported"}


def _world_module(world_model):
    """Exact WorldModel type check without importing trader.world: cognition
    imports only stdlib and itself, and a real instance implies its module is
    already loaded."""
    mod = sys.modules.get("trader.world.model")
    if mod is None or not isinstance(world_model, mod.WorldModel):
        raise TypeError("world_model must be a WorldModel or None")
    return mod


def _world_volume(model, w, sym, timeframe, anchor_close, as_of):
    """One accepted world z-score for `sym`, or an explicit no-contribution."""
    valid = sys.modules["trader.world.observation"].Quality.VALID
    out = {"model_id": model.model_id, "observation_id": None, "record_sha256": None,
           "value": None, "status": "absent", "reason": "no_observation"}
    try:
        found = model.get_observations(w.Scope(w.ScopeLevel.INSTRUMENT, sym),
                                       w.Horizon.INTRADAY, kind=WORLD_KIND)
    except LookupError:
        return dict(out, reason="unknown_scope")
    except ValueError:
        return dict(out, status="unsupported", reason="horizon_not_declared")
    if not found:
        return out
    if len(found) > 1:
        return dict(out, status="ambiguous", reason=f"{len(found)}_observations")
    (o,) = found
    v = o.value
    out.update(observation_id=o.observation_id,
               record_sha256=sha256(o.to_json().encode("utf-8")).hexdigest(),
               value=v if type(v) in (int, float) else None)
    if o.timeframe != timeframe:
        return dict(out, status="unsupported", reason="timeframe_mismatch")
    if o.unit != "z_score":
        return dict(out, status="unsupported", reason="unit_not_z_score")
    if o.quality is not valid:
        return dict(out, status=_QUALITY_STATUS.get(o.quality.value, "invalid"),
                    reason=f"quality_{o.quality.value.lower()}")
    if o.available_at_ms is None or o.available_at_ms > as_of or o.observed_at_ms > as_of:
        return dict(out, status="invalid", reason="availability_unknown")
    if o.timestamp_ms != anchor_close:
        return dict(out, status="stale" if o.timestamp_ms < anchor_close else "invalid",
                    reason="not_anchor_timestamp")
    if o.max_age_ms is not None and as_of - o.timestamp_ms > o.max_age_ms:
        return dict(out, status="stale", reason="max_age_exceeded")
    if type(v) not in (int, float) or not math.isfinite(v):
        return dict(out, status="invalid", reason="non_numeric_value")
    return dict(out, status="ok", reason="accepted")


def evaluate(ds, as_of: int, cfg: CognitionConfig, decision_id: str,
             open_episodes: dict, world_model: WorldModel | None = None) -> dict:
    if world_model is not None:
        w = _world_module(world_model)
        if world_model.as_of_ms != as_of:
            raise ValueError("world_model cut does not match as_of")
    tf = ds.tf_ms
    anchor_close = (as_of // tf) * tf
    last_open = anchor_close - tf
    need = cfg.window + cfg.short + 1
    N, S = cfg.window, cfg.short
    members = ds.members_asof(as_of)
    capped, members = members[cfg.max_symbols:], members[:cfg.max_symbols]

    observations: list = []
    index: dict = {}
    rows: dict = {}
    feats: dict = {}

    def add(kind, sym, status, value, detail, event_ms=None, available_ms=None,
            source="candles", series=None):
        parts = (kind, sym) if series is None else (kind, sym, series)
        o = Observation(stable_id("obs", decision_id, *parts), kind, sym, as_of,
                        event_ms, available_ms, source, status, rnd(value), detail)
        observations.append(o)
        index[(kind, sym)] = o
        return o

    for sym in members:
        opens = [last_open - (need - 1 - j) * tf for j in range(need)]
        bars = [ds.bar_asof(sym, t, as_of) for t in opens]
        present = [b for b in bars if b is not None]
        if bars[-1] is None:
            status = "stale" if present else "missing"
        elif len(present) < need:
            first = next(i for i, b in enumerate(bars) if b is not None)
            status = "warmup" if all(b is not None for b in bars[first:]) else "gap"
        else:
            status = "ok"
        win = add("candle_window", sym, status, len(present),
                  {"need": need, "first_open_ms": opens[0], "anchor_open_ms": last_open},
                  max((b.close_ms for b in present), default=None),
                  max((b.available_ms for b in present), default=None),
                  ",".join(sorted({b.source for b in present})) or "candles")
        if status != "ok":
            rows[sym] = {"symbol": sym, "status": status, "eligible": False,
                         "reason": status, "evidence": [win.obs_id]}
            continue
        closes = [b.close for b in bars]
        r = [math.log(closes[i] / closes[i - 1]) for i in range(1, need)]
        s_long, s_short = statistics.stdev(r[:N]), statistics.stdev(r[N:])
        scales = (s_long, s_short, s_long * math.sqrt(S), s_long * math.sqrt(cfg.horizon))
        if not all(math.isfinite(x) and x > 0 for x in scales):
            win.status = "degenerate"
            rows[sym] = {"symbol": sym, "status": "degenerate", "eligible": False,
                         "reason": "unusable_return_scale", "evidence": [win.obs_id]}
            continue
        lv = [math.log1p(b.volume) for b in bars]
        hist = lv[-(N + 1):-1]
        v_sd = statistics.stdev(hist)
        feats[sym] = {
            "anchor_close": closes[-1], "sigma": s_long, "ret_short": math.fsum(r[N:]),
            "volume_z": _finite((lv[-1] - statistics.fmean(hist)) / v_sd) if v_sd > 0 else None,
            "vol_trans_z": _finite(math.log(s_short / s_long)
                                   / math.sqrt(1 / (2 * (S - 1)) + 1 / (2 * (N - 1)))),
            "event_ms": win.event_ms, "available_ms": win.available_ms,
        }

    cohort = sorted(feats)
    market = {"status": "insufficient_cohort", "cohort_size": len(cohort),
              "min_cohort": cfg.min_cohort, "median_ret_short": None,
              "market_z": None, "breadth_up": None, "dispersion": None, "broad": None}
    if len(cohort) >= cfg.min_cohort:
        rets = [feats[s]["ret_short"] for s in cohort]
        med = statistics.median(rets)
        mz = med / (statistics.median(feats[s]["sigma"] for s in cohort) * math.sqrt(S))
        up = sum(1 for x in rets if x > 0) / len(rets)
        market.update(status="ok", median_ret_short=rnd(med), market_z=rnd(mz),
                      breadth_up=rnd(up), dispersion=rnd(statistics.pstdev(rets)),
                      broad=abs(mz) >= cfg.broad_z and max(up, 1 - up) >= cfg.broad_breadth)
        for s in cohort:
            feats[s]["div_z"] = _finite(
                (feats[s]["ret_short"] - med) / (feats[s]["sigma"] * math.sqrt(S)))
    else:
        for s in cohort:
            feats[s]["div_z"] = None
    breadth = add("market_breadth", "*", market["status"], market["market_z"],
                  dict(market, cohort=cohort),
                  anchor_close if cohort else None,
                  max((feats[s]["available_ms"] for s in cohort), default=None))
    market["obs_id"] = breadth.obs_id

    for sym in cohort:
        f = feats[sym]
        ev, av = f["event_ms"], f["available_ms"]
        comps = {"volume_anomaly": f["volume_z"],
                 "volatility_transition": f["vol_trans_z"],
                 "relative_return_divergence": f["div_z"]}
        ids = []
        for kind in COMPONENTS:
            v = comps[kind]
            st = "ok" if v is not None else (
                "insufficient_cohort" if kind == "relative_return_divergence"
                and market["status"] != "ok" else "degenerate")
            ids.append(add(kind, sym, st, v, {}, ev, av).obs_id)
        parts = _participation(ds, sym, as_of, cfg, add)
        index[("participation", sym)] = parts
        ids.extend(o.obs_id for o in parts)
        avail = {k: abs(v) for k, v in comps.items() if v is not None}
        world = None
        if world_model is not None:
            world = _world_volume(world_model, w, sym, ds.timeframe, anchor_close, as_of)
            if world["status"] == "ok":
                avail[WORLD_COMPONENT] = abs(world["value"])
        positioning = None
        if ds.positioning is not None:
            positioning = {series: _positioning(ds, sym, series, as_of, add)
                           for series in POSITIONING_SERIES}
            ids.extend(o.obs_id for o in positioning.values())
            usable = [abs(o.value) for o in positioning.values() if o.status == "ok"]
            if usable:
                avail[POSITIONING_COMPONENT] = max(usable)
        salience = max(avail.values()) if avail else None
        dominant = min(avail, key=lambda k: (-avail[k], k)) if avail else None
        rows[sym] = {"symbol": sym, "status": "ok", "eligible": salience is not None,
                     "reason": None if salience is not None else "no_components",
                     "salience": rnd(salience), "dominant": dominant,
                     "components": {k: rnd(v) for k, v in comps.items()},
                     "evidence": [index[("candle_window", sym)].obs_id] + ids}
        if world is not None:
            rows[sym]["components"][WORLD_COMPONENT] = (
                rnd(world["value"]) if world["status"] == "ok" else None)
            rows[sym]["world_model"] = dict(world, value=rnd(world["value"]))
        if positioning is not None:
            rows[sym]["components"][POSITIONING_COMPONENT] = rnd(avail.get(POSITIONING_COMPONENT))
            rows[sym]["positioning"] = {k: {"status": o.status, "z": o.value, "obs_id": o.obs_id}
                                        for k, o in positioning.items()}

    ranked = sorted((s for s in cohort if rows[s]["eligible"]),
                    key=lambda s: (-rows[s]["salience"], s))
    selected = []
    for rank, sym in enumerate(ranked, 1):
        row = rows[sym]
        row["rank"] = rank
        if row["salience"] < cfg.min_salience:
            row["reason"] = "below_min_salience"
        elif sym in open_episodes:
            row["reason"] = "open_episode"
            row["open_episode_id"] = open_episodes[sym]
        elif len(selected) >= cfg.k:
            row["reason"] = "beyond_top_k"
        else:
            row["reason"] = "selected"
            selected.append(sym)
        row["selected"] = row["reason"] == "selected"
    for sym in capped:
        rows[sym] = {"symbol": sym, "status": "not_evaluated", "eligible": False,
                     "reason": "universe_cap"}

    return {"anchor_close_ms": anchor_close, "members": members + capped,
            "universe": [rows[s] for s in sorted(rows)], "rows": rows, "market": market,
            "features": feats, "cohort": cohort, "ranked": ranked,
            "selected": selected, "observations": observations, "index": index}


def _participation(ds, sym, as_of, cfg, add):
    """One observation per (kind, source) series; a change is only ever taken
    between two points of the same series. Per event, the latest revision
    available at `as_of` is used. A derived change exists only once both of
    its endpoints are available: its available_ms is the max of the two."""
    series: dict = {}
    for p in ds.participation_asof(sym, as_of):     # sorted by (event, available)
        series.setdefault((p.kind, p.source), {})[p.event_ms] = p
    if not series:
        return [add("participation", sym, "missing", None, {}, source="participation")]

    def endpoint(p):
        return {"record_id": stable_id("pt", p.symbol, p.kind, p.source, p.event_ms,
                                       p.available_ms),
                "event_ms": p.event_ms, "available_ms": p.available_ms, "value": rnd(p.value)}

    out = []
    for kind, source in sorted(series):
        by_event = series[(kind, source)]
        pts = [by_event[t] for t in sorted(by_event)]
        last, prev = pts[-1], (pts[-2] if len(pts) > 1 else None)
        change = None
        if last.event_ms < as_of - cfg.participation_stale_bars * ds.tf_ms:
            status = "stale"
        elif prev is None:
            status = "warmup"
        else:
            change = _finite(math.log(last.value / prev.value))
            status = "ok" if change is not None else "degenerate"
        out.append(add(
            "participation", sym, status, change,
            {"kind": kind, "derivation": "ln(current/previous)",
             "current": endpoint(last), "previous": endpoint(prev) if prev else None},
            last.event_ms,
            max(last.available_ms, prev.available_ms) if prev else last.available_ms,
            source, series=f"{kind}|{source}"))
    return out


def _positioning(ds, sym, series, as_of, add):
    """One observation for (sym, series); value is the signed z or None."""
    def out(status, value=None, detail=None, event_ms=None):
        return add("positioning", sym, status, value,
                   dict(detail or {}, series=series, reference=POSITIONING_REFERENCE),
                   event_ms, None, "positioning", series=series)

    reason = ds.positioning_invalid.get((sym, series))
    if reason is not None:
        return out("invalid", detail={"reason": reason})
    pts = [p for p in ds.positioning.get(sym, {}).get(series, ()) if p.ts <= as_of]
    if not pts:
        return out("missing")
    need = POSITIONING_REFERENCE + 1
    if len(pts) < need:
        return out("insufficient_history", detail={"samples": len(pts), "need": need},
                   event_ms=pts[-1].ts)
    pts = pts[-need:]
    anchor, ref = pts[-1], pts[:-1]
    steps = [b.ts - a.ts for a, b in zip(pts, pts[1:])]
    if series == "ls_ratio":
        step, max_age = LS_RATIO_STEP_MS, LS_RATIO_MAX_AGE_MS
        continuous = all(d == step for d in steps)
    else:
        minutes = [(d + _MIN_MS // 2) // _MIN_MS for d in steps]
        step = minutes[0] * _MIN_MS
        max_age = step + FUNDING_AGE_SLACK_MS
        continuous = minutes[0] > 0 and all(m == minutes[0] for m in minutes)
    detail = {"anchor_ts": anchor.ts, "anchor_value": rnd(anchor.value),
              "reference_first_ts": ref[0].ts, "step_ms": step, "max_age_ms": max_age,
              "age_ms": as_of - anchor.ts}
    if not continuous:
        return out("gap", detail=detail, event_ms=anchor.ts)
    if as_of - anchor.ts > max_age:
        return out("stale", detail=detail, event_ms=anchor.ts)
    values = [p.value for p in ref]
    mean, sd = statistics.fmean(values), statistics.stdev(values)
    detail.update(mean=rnd(mean) if math.isfinite(mean) else None,
                  stdev=rnd(sd) if math.isfinite(sd) else None)
    if not (math.isfinite(mean) and math.isfinite(sd)) or sd <= 0:
        return out("zero_variance" if sd == 0 else "degenerate", detail=detail,
                   event_ms=anchor.ts)
    z = _finite((anchor.value - mean) / sd)
    return out("ok" if z is not None else "degenerate", z, detail, anchor.ts)
