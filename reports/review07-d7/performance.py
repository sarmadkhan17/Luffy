"""Synthetic preflight/transition microbenchmark; no trading throughput claim."""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from trader.engine.exits import SpecExit
from trader.strategy import exit_policy as E
from trader.strategy.spec import ExitSpec
from tests.test_compile import _spec

spec = _spec(exit=ExitSpec(time={'max_bars':500}))
n=25000
start=time.perf_counter()
for _ in range(n):
    assert SpecExit.frozen_unsupported(spec) is None
preflight=time.perf_counter()-start
p,s=E.initialize(spec.exit,100,1,'long',0,900000)
start=time.perf_counter()
for _ in range(50):
    state=s
    for i in range(1,500):
        state=E.advance(p,state,E.Observation(i*900000,100,100.1,99.9,100),1).state
elapsed=time.perf_counter()-start
print(json.dumps(dict(preflight_calls=n,preflight_seconds=preflight,
    preflight_microseconds_per_call=preflight/n*1e6,transitions=50*499,
    transition_seconds=elapsed,transitions_per_second=50*499/elapsed,
    scope='synthetic fixed shared policy; no backtest speedup or live claim'),indent=2))
