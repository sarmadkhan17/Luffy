"""Run identical health-observation fixtures in one source tree; dump JSON.

usage: python compat_dump.py <tree> <out.json>
Clock and uuid are pinned so sweep ids and timestamps are deterministic.
"""
import itertools
import json
import sys
import uuid as _uuid
from datetime import datetime, timezone

tree, out = sys.argv[1], sys.argv[2]
sys.path.insert(0, tree)
import trader  # noqa: E402
assert trader.__file__.startswith(tree), trader.__file__

from trader.brain.analyst import Analyst  # noqa: E402
from trader.strategy import health_observation as ho  # noqa: E402
from trader.strategy import rolling  # noqa: E402
sys.path.insert(0, tree + "/tests")
from test_strategy_health_observation import (  # noqa: E402
    CFG, DOWN, RARE, UP, _Journal, _analyst, _frame, _spec)

FIXED = datetime(2026, 9, 27, 0, 0, 0, tzinfo=timezone.utc)


class _Clock:
    @staticmethod
    def now(tz=None):
        return FIXED

    fromisoformat = staticmethod(datetime.fromisoformat)


_ids = itertools.count(1)
ho.uuid.uuid4 = lambda: _uuid.UUID(int=next(_ids))
ho.datetime = _Clock

BROKEN = DOWN.drop(columns=["high"])


def run(name, frames, specs, cfg=None, ctx=None, patch=None):
    a = Analyst(_Journal(), cfg or CFG)
    a.frames = lambda tf, extra=(): frames
    if ctx:
        real = a._ctx
        a._ctx = lambda tf, spec: ctx(real, tf, spec)
    saved = {}
    for k, v in (patch or {}).items():
        saved[k] = getattr(rolling, k)
        setattr(rolling, k, v)
    raised = None
    try:
        actions = a.review_deployed(specs)
    except Exception as e:
        actions, raised = None, f"{type(e).__name__}: {e}"
    finally:
        a.flush_health_observations()
        for k, v in saved.items():
            setattr(rolling, k, v)
    events = [{"kind": k, "subject": s,
               "detail": {**d, "ts": None} if k == "spec_decayed" else d}
              for k, s, d in a.journal.events]
    return {"name": name, "actions": actions, "raised": raised,
            "events": json.loads(json.dumps(events, default=repr))}


def ctx_fail(real, tf, spec):
    if spec.id == "s2":
        raise RuntimeError("feed down")
    return real(tf, spec)


def no_derivs(real, tf, spec):
    fr, btc, _dv, risk = real(tf, spec)
    return fr, btc, (lambda s: {}), risk


def diag_boom(*a, **k):
    raise RuntimeError("injected diagnostics failure")


short = _frame(500, seed=3)
tiny = _frame(150, seed=4)
SCEN = [
    ("idle", {"BTC/USDT": UP, "_btc_1h": UP}, [_spec(entry=RARE)]),
    ("still_working", {"BTC/USDT": UP, "_btc_1h": UP}, [_spec()]),
    ("decayed", {"BTC/USDT": DOWN, "_btc_1h": DOWN},
     [_spec("d1"), _spec("d2", entry=RARE), _spec("d3")]),
    ("compile_failed", {"BTC/USDT": DOWN, "_btc_1h": DOWN},
     [_spec("c1", entry="close >>> nope("), _spec("c2")]),
    ("evaluation_exception", {"BTC/USDT": UP, "_btc_1h": UP},
     [_spec("s1"), _spec("s2"), _spec("s3")], None, ctx_fail),
    ("coverage_faults", {"BTC/USDT": UP, "ETH/USDT": UP.drop(columns=["high"]),
                         "XRP/USDT": short, "NIL/USDT": None, "_btc_1h": UP},
     [_spec(universe={"include": ["BTC/USDT", "ETH/USDT", "XRP/USDT",
                                  "NIL/USDT", "ADA/USDT"]})]),
    ("insufficient_history", {"BTC/USDT": short, "ETH/USDT": short,
                              "_btc_1h": short}, [_spec()]),
    ("no_scorable_bars", {"BTC/USDT": tiny, "_btc_1h": tiny}, [_spec()],
     {**CFG, "strategies": {**CFG["strategies"], "decay_recent_days": 5}}),
    ("missing_context", {"BTC/USDT": UP, "_btc_1h": UP},
     [_spec(entry="funding > 0.0005")], None, no_derivs),
    ("errored_decayed", {"BTC/USDT": DOWN, "ETH/USDT": BROKEN,
                         "_btc_1h": DOWN}, [_spec()]),
    # failure-only difference: a diagnostics write fails inside the
    # per-symbol error handler (the review finding)
    ("diag_failure", {"BTC/USDT": DOWN, "ETH/USDT": BROKEN, "_btc_1h": DOWN},
     [_spec("d1"), _spec("d2", entry=RARE), _spec("d3")], None, None,
     {"_diag_error": diag_boom}),
    # same inputs, no injection: the authoritative reference for diag_failure
    ("diag_reference", {"BTC/USDT": DOWN, "ETH/USDT": BROKEN, "_btc_1h": DOWN},
     [_spec("d1"), _spec("d2", entry=RARE), _spec("d3")]),
]

results = [run(*s) for s in SCEN]
json.dump({"tree": tree, "results": results,
           "manifest": ho.code_manifest()}, open(out, "w"), indent=1,
          sort_keys=True)
print("ok", len(results))
