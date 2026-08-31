"""Prove the throughput claim.

The old engine costs ~1.9 ms/bar/symbol, which is why config.yaml carries
`gauntlet_max_full_runs: 2` and a 300 s in-tick budget: a 5-symbol x 8000-bar
gauntlet takes ~76 s, so the brain can afford two candidates an hour. No
search strategy matters at two evaluations per hour.

Target >= 20x; the spec's design point is ~100x.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.config import load_config                       # noqa: E402
from trader.data.feed import DataFeed                            # noqa: E402
from trader.strategy import evidence                             # noqa: E402
from trader.strategy.backtest import backtest as legacy_backtest  # noqa: E402
from trader.strategy.backtest import context_frames              # noqa: E402
from trader.strategy.compile import compile_spec                 # noqa: E402
from trader.strategy.library import build_seed_population        # noqa: E402
from trader.strategy.seed_specs import load_seed_specs           # noqa: E402
from trader.strategy.vector_backtest import vector_backtest      # noqa: E402

MIN_SPEEDUP = 20.0

cfg = load_config()
frames = evidence.load_frames(DataFeed(), cfg)
syms = [k for k in frames if not k.startswith("_")]
btc = frames.get("_btc_1h")
if not syms:
    print("no candle data")
    sys.exit(1)
print(f"symbols={syms}")
print(f"bars={ {s: len(frames[s]) for s in syms} }\n")

genomes = [g for _s, g in build_seed_population()]
t0 = time.perf_counter()
for g in genomes:
    for s in syms:
        legacy_backtest(g, frames[s], cfg["risk"],
                        ctx=context_frames(frames[s], btc))
t_old = time.perf_counter() - t0

compiled = [compile_spec(s) for s in load_seed_specs()[:len(genomes)]]
t0 = time.perf_counter()
for c in compiled:
    for s in syms:
        vector_backtest(c, {c.spec.timeframe: frames[s]}, cfg["risk"],
                        btc={"15m": btc} if btc is not None else None,
                        symbol=s)
t_new = time.perf_counter() - t0

runs = len(genomes) * len(syms)
speedup = t_old / max(t_new, 1e-9)
print(json.dumps({
    "runs": runs,
    "old_total_s": round(t_old, 2), "old_per_run_s": round(t_old / runs, 3),
    "new_total_s": round(t_new, 3), "new_per_run_s": round(t_new / runs, 4),
    "speedup": round(speedup, 1),
}, indent=2))
print("\nBENCH:", "PASS" if speedup >= MIN_SPEEDUP else "FAIL")
sys.exit(0 if speedup >= MIN_SPEEDUP else 1)
