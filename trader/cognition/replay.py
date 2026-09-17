"""Chronological point-in-time replay, exported as one JSON trace.

    ./venv/bin/python -m trader.cognition.replay --input FIXTURE.json --output TRACE.json

There is no default path for either. Evaluation times are the input's
`decision_times` (deduplicated, sorted) plus `--end-ms` if given. At each
time T, in order:

1. Pending outcomes whose deadline <= T are attempted with data available at
   T. A resolved outcome is final and never recomputed; one still missing its
   exact-horizon bar (or cohort median) stays pending.
2. If T is a decision time: attention, market state, episodes, hypotheses
   and baseline samples are built from data available at T and appended.
   Nothing already in the trace is touched.

So appending future bars or late arrivals cannot change a decision made
before they were available, and outcomes still pending at the end are
exported as `unresolved` with their reason.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from trader.cognition import SCHEMA_VERSION
from trader.cognition.attention import CognitionConfig, evaluate
from trader.cognition.contracts import (INPUT_SCHEMA, Outcome, is_timestamp, load_input,
                                        stable_id, to_dict)
from trader.cognition.hypotheses import (TOLERANCE_MS, forward_measure, frame_episode,
                                         pending_reason, resolve_baseline_raw,
                                         resolve_baseline_relative, resolve_episode)

RESOLVERS = {
    "episode": lambda ep, dec, fwd, t, cfg: resolve_episode(ep, ep["hypotheses"], dec, fwd, t, cfg),
    "baseline_raw": resolve_baseline_raw,
    "baseline_relative": resolve_baseline_relative,
}

LIMITATIONS = [
    "Offline replay over an explicit input file only; verified by fixture tests.",
    "No shadow, live, edge or profitability claim is made or implied.",
    "Hypothesis templates are illustrative, not discovered strategies or causal facts.",
    "Hypothesis probabilities are unset (null) until calibrated.",
    "Outcomes are exact-horizon (tolerance 0); a missing deadline bar leaves them unresolved.",
    "Nothing here can place orders, admit strategies or change runtime state.",
]


def run(raw: dict, cfg: CognitionConfig | None = None, end_ms: int | None = None) -> dict:
    cfg = cfg or CognitionConfig()
    if end_ms is not None and not is_timestamp(end_ms):
        raise ValueError(f"end_ms must be a non-negative int ms timestamp, got {end_ms!r}")
    ds = load_input(raw)
    if len(ds.decision_times) > cfg.max_decisions:
        raise ValueError(f"{len(ds.decision_times)} decisions exceeds max_decisions")
    config_id = stable_id("cfg", asdict(cfg))
    decision_set = set(ds.decision_times)
    times = sorted(decision_set | ({end_ms} if end_ms is not None else set()))

    decisions: list = []
    pending: list = []        # (kind, subject, decision)
    outcomes: list = []
    open_episodes: dict = {}  # symbol -> (episode_id, deadline_ms)

    for t in times:
        still = []
        fwd_cache: dict = {}
        for kind, subject, dec in pending:
            if dec["decision_id"] not in fwd_cache:
                fwd_cache[dec["decision_id"]] = forward_measure(ds, dec, t)
            got = RESOLVERS[kind](subject, dec, fwd_cache[dec["decision_id"]], t, cfg)
            if got is None:
                still.append((kind, subject, dec))
            else:
                outcomes.extend(to_dict(o) for o in (got if isinstance(got, list) else [got]))
        pending = still

        if t not in decision_set:
            continue
        decision_id = stable_id("dec", SCHEMA_VERSION, config_id, ds.timeframe, t)
        live = {s: e for s, (e, d) in open_episodes.items() if d > t}
        att = evaluate(ds, t, cfg, decision_id, live)
        deadline = att["anchor_close_ms"] + cfg.horizon * ds.tf_ms
        dec = {
            "decision_id": decision_id, "as_of_ms": t,
            "anchor_close_ms": att["anchor_close_ms"], "deadline_ms": deadline,
            "members": att["members"], "universe": att["universe"],
            "market_state": att["market"], "cohort_ok": att["market"]["status"] == "ok",
            "cohort": {s: {"anchor_close": att["features"][s]["anchor_close"],
                           "sigma": att["features"][s]["sigma"]}      # full precision
                       for s in att["cohort"]},
            "selected": att["selected"],
            "observations": [to_dict(o) for o in att["observations"]],
            "episodes": [], "baseline_samples": [],
        }
        for sym in att["selected"]:
            episode, hyps = frame_episode(decision_id, sym, att, cfg, deadline)
            episode["hypotheses"] = [to_dict(h) for h in hyps]
            dec["episodes"].append(episode)
            open_episodes[sym] = (episode["episode_id"], deadline)
            pending.append(("episode", episode, dec))
        for sym in att["ranked"]:
            row = att["rows"][sym]
            sample = {"sample_id": stable_id("base", decision_id, sym), "symbol": sym,
                      "selected": row["selected"], "rank": row["rank"],
                      "reason": row["reason"], "salience": row["salience"]}
            dec["baseline_samples"].append(sample)
            pending.append(("baseline_raw", sample, dec))
            pending.append(("baseline_relative", sample, dec))
        decisions.append(dec)

    for kind, subject, dec in pending:
        fwd = forward_measure(ds, dec, times[-1]) if times else None
        why = pending_reason(kind, subject["symbol"], fwd)
        if kind == "episode":
            ids = [(h["hyp_id"], "hypothesis") for h in subject["hypotheses"]]
        else:
            ids = [(f"{subject['sample_id']}:{kind.split('_')[1]}", kind)]
        for sid, skind in ids:
            outcomes.append(to_dict(Outcome(stable_id("out", sid), sid, skind,
                                            dec["deadline_ms"], None, "unresolved",
                                            dict(why, symbol=subject["symbol"],
                                                 tolerance_ms=TOLERANCE_MS))))
    outcomes.sort(key=lambda o: (o["deadline_ms"], o["subject_kind"], o["outcome_id"]))

    return {"schema_version": SCHEMA_VERSION, "input_schema": INPUT_SCHEMA,
            "config_id": config_id, "config": asdict(cfg), "timeframe": ds.timeframe,
            "evaluation_times": times, "rejected_inputs": ds.rejected,
            "decisions": decisions, "outcomes": outcomes, "limitations": LIMITATIONS}


def dump(trace: dict) -> str:
    return json.dumps(trace, indent=2, sort_keys=True, allow_nan=False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m trader.cognition.replay",
                                 description="Offline point-in-time cognition replay.")
    ap.add_argument("--input", required=True, type=Path, help="input JSON (cognition.input.v1)")
    ap.add_argument("--output", required=True, type=Path, help="trace JSON to write")
    ap.add_argument("--end-ms", type=int, default=None,
                    help="extra final evaluation time for resolving outcomes")
    ap.add_argument("--k", type=int, default=CognitionConfig.k)
    ap.add_argument("--min-salience", type=float, default=CognitionConfig.min_salience)
    args = ap.parse_args(argv)
    if args.input.resolve() == args.output.resolve():
        ap.error("--output must differ from --input")
    if args.end_ms is not None and not is_timestamp(args.end_ms):
        ap.error("--end-ms must be a non-negative integer ms timestamp")
    try:
        cfg = CognitionConfig(k=args.k, min_salience=args.min_salience)
    except ValueError as e:
        ap.error(str(e))
    raw = json.loads(args.input.read_text())
    trace = run(raw, cfg, args.end_ms)
    args.output.write_text(dump(trace))
    resolved = sum(1 for o in trace["outcomes"] if o["status"] != "unresolved")
    print(f"decisions={len(trace['decisions'])} "
          f"episodes={sum(len(d['episodes']) for d in trace['decisions'])} "
          f"outcomes={len(trace['outcomes'])} resolved={resolved} -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
