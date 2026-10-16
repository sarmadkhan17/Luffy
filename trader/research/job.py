"""What the child process does, and nothing else.

Two entry points, both module-level so `multiprocessing` spawn can import
them, both taking and returning plain dicts so everything crosses the pipe.

The child READS the candle, derivative and reference stores and returns
results. It never writes a database: the kernel thread writes through the
journal's own transaction wrapper, so the kernel remains the only writer of
truth.

It also watches a SOFT deadline of its own, ahead of the hard one
`run_child` enforces. A batch that runs long returns what it finished
instead of being killed with everything lost; the combinations it did not
reach are simply not in the ledger, and the planner offers them again.
"""
from __future__ import annotations

import time

from ..strategy.features import FeatureCtx
from ..strategy.portfolio_evidence import accounting_provenance, RESEARCH_CLOCK
from . import evaluate as ev
from . import slices, thresholds
from .combo import Combination


def _ctxs(b) -> list:
    """One FeatureCtx per discovery symbol, carrying the same context an
    evaluation gets — otherwise a threshold is measured on a quantity that
    evaluates to NaN when it is actually used."""
    out = []
    for sym in b.frames:
        out.append(FeatureCtx(
            frames=b.sym_frames[sym], tf=b.tf, btc=b.btc,
            derivs=b.derivs.get(sym), universe=b.universe, market=b.market,
            symbol=sym, world=b.world))
    return out


def _coverage(b, requires) -> dict:
    """How much of each non-candle requirement the discovery slice holds:
    {requirement: {have, of, first_ms}}. `have` counts loaded symbols with at
    least one observation inside the slice (a reference is one series);
    `first_ms` is each symbol's first observation before the cut, so the
    caller can tell "not yet collected" from "collected, but after the cut".
    Facts the bundle already holds; nothing is read here."""
    from ..strategy.spec_evidence import _SERIES_FOR
    out = {}
    for req in requires:
        if req.startswith("ref:"):
            have = 1 if (b.market or {}).get(req.split(":", 1)[1]) is not None \
                and len(b.market[req.split(":", 1)[1]]) else 0
            of = 1
        elif req in _SERIES_FOR:
            series = _SERIES_FOR[req]
            have = sum(1 for d in b.derivs.values() if len(d.get(series, ())))
            of = len(b.derivs)
        else:
            continue
        out[req] = {"have": have, "of": of,
                    "first_ms": sorted(b.first_obs.get(req, []))}
    return out


#: how a bundle was judged against the symbol floor the runner passed in
ADMITTED = "admitted"
INSUFFICIENT_REPLAY_HISTORY = "insufficient_replay_history"
INSUFFICIENT_SYMBOL_COVERAGE = "insufficient_symbol_coverage"
TRANSIENT_READ_FAILURE = "transient_read_failure"
READ_ERROR = "read_error"
#: per-symbol drop reasons that ARE a statement about the history itself
DETERMINISTIC_DROPS = ("short_history", "short_before_cut")


def admit_bundle(b, floor: int) -> dict:
    """Judge a loaded bundle against `floor`, before anything is scored.

    Pure: reads only what `load_bundle` already holds. The verdict is a
    diagnosis, never a different number of symbols — `loaded_symbols` is the
    bundle's own count, exactly what the runner's guard has always read.

    - `transient_read_failure`: store reads failed with a busy/locked error
      and, had they succeeded, the floor would have been met. Retry-able.
    - `read_error`: the same, but the failures are not busy/locked errors.
    - `insufficient_symbol_coverage`: fewer symbols were requested than the
      floor, or some loaded but not enough.
    - `insufficient_replay_history`: the request was large enough and not one
      symbol held replay-eligible history before the cut.
    """
    loaded, requested = len(b.frames), len(b.requested or b.frames)
    floor = int(floor)
    dropped = dict(getattr(b, "dropped", None) or {})
    transient = sorted(s for s, d in dropped.items()
                       if d.get("reason") == "read_error" and d.get("transient"))
    # Anything that is neither a clean "this series holds too little
    # replay-eligible history" nor a recognised busy/locked read is UNKNOWN:
    # a failed frame build, a corrupt revision, an exception type nobody
    # classified. Unknown is never evidence of missing history.
    errored = sorted(s for s, d in dropped.items()
                     if d.get("reason") in ("read_error", "frames_error")
                     and not d.get("transient"))
    unknown = sorted(s for s, d in dropped.items()
                     if d.get("reason") not in DETERMINISTIC_DROPS
                     and not (d.get("reason") == "read_error"
                              and d.get("transient")))
    # No diagnosis for a missing requested symbol is not proof of absent
    # history. A partial/older loader must remain retryable.
    unknown = sorted(set(unknown) | (set(b.requested or b.frames)
                                    - set(b.frames) - set(dropped)))
    if loaded >= floor:
        reason = ADMITTED
    elif transient and loaded + len(transient) >= floor:
        reason = TRANSIENT_READ_FAILURE
    elif errored and loaded + len(transient) + len(errored) >= floor:
        reason = READ_ERROR
    elif requested < floor or loaded > 0:
        reason = INSUFFICIENT_SYMBOL_COVERAGE
    else:
        reason = INSUFFICIENT_REPLAY_HISTORY
    # `deterministic`: the refusal is fully explained by replay-eligible
    # history that is absent at this cut. Any transient or unknown drop in
    # the bundle withholds it, even when the floor was unreachable anyway:
    # the evidence for that symbol is incomplete and must stay retryable.
    deterministic = (reason in (INSUFFICIENT_REPLAY_HISTORY,
                                INSUFFICIENT_SYMBOL_COVERAGE)
                     and not transient and not unknown)
    return {"admitted": reason == ADMITTED, "reason": reason,
            "deterministic": deterministic,
            "loaded_symbols": loaded, "requested_symbols": requested,
            "required_symbols": floor, "tf": b.tf, "cut_ms": int(b.cut),
            "transient_symbols": transient, "unknown_symbols": unknown,
            "dropped": dropped}


def measure_job(payload: dict) -> dict:
    """Percentiles for every gauge, and how much of the slice it exists on."""
    t0 = time.monotonic()
    b = ev.load_bundle(payload["tf"], payload["symbols"], payload["cfg"],
                       requires=tuple(payload.get("requires") or ("ohlcv",)),
                       heldout_symbols=tuple(
                           payload.get("heldout_symbols") or ()),
                       paths=payload.get("paths"),
                       cap_ms=payload.get("spent_cut_ms"))
    exprs = payload.get("exprs")
    if not exprs:
        from .vocab import expressions
        exprs = expressions(payload["tf"])
    rcfg = (payload["cfg"].get("research") or {})
    gauges = thresholds.measure(
        exprs, _ctxs(b),
        min_samples=int(rcfg.get("min_threshold_samples",
                                 thresholds.MIN_SAMPLES)),
        min_finite_frac=float(rcfg.get("min_finite_frac",
                                       thresholds.MIN_FINITE_FRAC)))
    return {"tf": b.tf, "cut_ms": int(b.cut), "gauges": gauges,
            **({"evidence": b.evidence} if b.evidence else {}),
            "counts": {"discovery": slices.bar_counts(b.frames),
                       "heldout_a": b.heldout_bars.get("a", {}),
                       "heldout_b": b.heldout_bars.get("b", {})},
            "elapsed_s": round(time.monotonic() - t0, 2)}


def evaluate_job(payload: dict) -> dict:
    """Score a batch of combinations that share one horizon and geometry.

    One combination never costs the batch. A rule that raises is recorded
    as a LOOK that failed, with its reason, and the batch carries on --
    the time axis and the failure axis get the same treatment.
    """
    t0 = time.monotonic()
    combos = [Combination.from_dict(d) for d in payload.get("combos") or []]
    soft = float(payload.get("soft_deadline_s", 600.0))
    if not combos:
        return {"results": [], "done": 0, "skipped": 0, "elapsed_s": 0.0}

    b = ev.load_bundle(payload["tf"], payload["symbols"], payload["cfg"],
                       requires=tuple(payload.get("requires") or ("ohlcv",)),
                       heldout_symbols=tuple(
                           payload.get("heldout_symbols") or ()),
                       paths=payload.get("paths"),
                       cap_ms=payload.get("spent_cut_ms"),
                       min_symbols=payload.get("min_symbols"))
    admission = None
    if payload.get("min_symbols") is not None:
        admission = admit_bundle(b, payload["min_symbols"])
        if not admission["admitted"]:
            # Refuse BEFORE any combination is scored: the runner would
            # discard the whole batch on this same count anyway.
            return {"results": [], "done": 0, "skipped": 0,
                    "loaded_symbols": len(b.frames), "cut_ms": int(b.cut),
                    "coverage": {}, "admission": admission,
                    "elapsed_s": round(time.monotonic() - t0, 2)}
    results, skipped = [], 0
    for c in combos:
        if time.monotonic() - t0 >= soft:
            skipped += 1
            continue
        try:
            results.append(ev.evaluate(
                c, b, draws=int(payload.get("draws", 30)),
                seed=int(payload.get("seed", 17))))
        except Exception as exc:
            # A failure is a LOOK with a reason, never a deferral.
            # `skipped` means "the deadline hit, come back to it", so a
            # failure routed there would be re-offered by the planner
            # forever and one poisonous rule would stall its window,
            # re-burning the whole child timeout on every attempt.
            # `error` is the key growth.decide reads; `window` keeps the
            # row filed under its own window instead of defaulting to ohlcv.
            results.append({"hash": c.hash, "tf": c.tf, "geo": c.geo,
                            "k": c.k, "window": c.window,
                            "parts": list(c.keys), "round": c.round,
                            "parent": c.parent, "trigger": c.trigger,
                            "trades": 0, "verdict": "error",
                            "portfolio": {"accounting": accounting_provenance(
                                b.equity, b.risk_pct, b.max_open, RESEARCH_CLOCK)},
                            "error": f"{type(exc).__name__}: {exc}"[:300]})
    out = {"results": results, "done": len(results), "skipped": skipped,
           "loaded_symbols": len(b.frames), "cut_ms": int(b.cut),
           "coverage": _coverage(b, payload.get("requires") or ()),
           **({"evidence": b.evidence} if b.evidence else {}),
           "elapsed_s": round(time.monotonic() - t0, 2)}
    if admission is not None:
        out["admission"] = admission
    return out


def select_job(payload: dict) -> dict:
    """Discovery entry sets for survivors about to be queued — twin
    detection's input. Reads the discovery slice only."""
    from . import referee
    t0 = time.monotonic()
    combos = [Combination.from_dict(d) for d in payload.get("combos") or []]
    reqs = {"ohlcv"}
    for c in combos:
        reqs.update(c.requires)
    b = ev.load_bundle(payload["tf"], payload["symbols"], payload["cfg"],
                       requires=tuple(sorted(reqs)),
                       paths=payload.get("paths"),
                       cap_ms=payload.get("spent_cut_ms"))
    return {"entries": referee.discovery_entries(combos, b),
            "elapsed_s": round(time.monotonic() - t0, 2)}


def referee_job(payload: dict) -> dict:
    """Gates 1 and 3 for ONE candidate. The only job that reads held-out
    prices; a failure after that point still reports `looked`."""
    from . import referee
    t0 = time.monotonic()
    c = Combination.from_dict(payload["combo"])
    try:
        out = referee.examine(c, payload)
    except Exception as exc:                            # noqa: BLE001
        out = {"hash": c.hash, "looked": True,
               "error": f"{type(exc).__name__}: {exc}"[:300]}
    out["elapsed_s"] = round(time.monotonic() - t0, 2)
    return out
