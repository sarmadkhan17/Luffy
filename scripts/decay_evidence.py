"""What the Analyst's decay and admission checks see for each live spec.

Read-only against a data directory (default: this checkout's `data/`): the
candle, derivative and journal stores are opened `mode=ro`, so it is safe to
point at the live checkout while the kernel runs. It never constructs a
`Journal` (whose constructor writes) and never fetches from the venue.

    ./venv/bin/python -m scripts.decay_evidence [--data DIR]

Written for the WARMUP fix of 2026-09-14: at 4h a 30-day decay window is 180
bars, under `simulate`'s old 211-bar floor, so the check could never see a
trade. Run it before and after a change to the evaluation engine.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _ro(path: Path):
    conns = threading.local()

    def db(self):
        c = getattr(conns, "c", None)
        if c is None:
            c = conns.c = sqlite3.connect(f"file:{path}?mode=ro", uri=True,
                                          check_same_thread=False)
        return c
    return property(db)


def live_specs(data: Path) -> list:
    from trader.strategy.spec import StrategySpec
    c = sqlite3.connect(f"file:{data / 'luffy.db'}?mode=ro", uri=True)
    rows = c.execute("SELECT id, state, spec_json FROM strategies "
                     "WHERE state IN ('active','paper') AND spec_json IS NOT NULL"
                     ).fetchall()
    return [(sid, status, StrategySpec.from_json(js)) for sid, status, js in rows]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default=str(ROOT / "data"))
    args = ap.parse_args(argv)
    data = Path(args.data).resolve()

    from trader.data import derivatives, feed as feed_mod
    feed_mod.DataFeed.db = _ro(data / "candles.db")
    derivatives.DerivFeed.db = _ro(data / "derivs.db")

    from trader.brain.analyst import Analyst
    from trader.core.config import load_config
    from trader.strategy import rolling
    from trader.strategy.compile import compile_spec

    cfg = load_config()
    s = cfg.get("strategies", {}) or {}
    an = Analyst(None, cfg, feed=feed_mod.DataFeed())
    out = {}
    for sid, status, spec in live_specs(data):
        tf = spec.timeframe
        frames, btc, derivs_for, risk = an._ctx(tf, spec)
        compiled = compile_spec(spec)
        dead, decay = rolling.has_decayed(
            compiled, frames, risk, tf,
            recent_days=float(s.get("decay_recent_days", 30)),
            min_trades=int(s.get("decay_min_trades", 10)),
            floor_pf=float(s.get("decay_floor_pf", 0.85)),
            btc=btc, derivs_for=derivs_for)
        ok, admit = rolling.recent_verdict(
            compiled, frames, risk, tf,
            recent_days=float(s.get("select_recent_days", 90)),
            min_trades=int(s.get("select_min_trades", 20)),
            min_pf=float(s.get("select_min_pf", 1.15)),
            btc=btc, derivs_for=derivs_for,
            max_days=float(s.get("select_max_days", 365)))
        pick = ("verdict", "trades", "pooled_pf", "recent_days", "window_widened")
        out[sid] = {"status": status, "timeframe": tf,
                    "symbols": len([k for k in frames if not k.startswith("_")]),
                    "decayed": dead,
                    "decay": {k: decay.get(k) for k in pick if k in decay},
                    "admission_ok": ok,
                    "admission": {k: admit.get(k) for k in pick if k in admit}}
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
