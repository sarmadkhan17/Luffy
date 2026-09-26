import sys, json
tree, fx = sys.argv[1], sys.argv[2]
sys.path.insert(0, tree); sys.path.insert(0, fx)
import trader; assert trader.__file__.startswith(tree)
from trader.strategy import rolling
from trader.strategy.compile import compile_spec
from _fx import CFG, UP, DOWN, RARE, _frame, _spec
BROKEN = DOWN.drop(columns=["high"])
FR = [{"BTC/USDT": UP, "_btc_1h": UP}, {"BTC/USDT": DOWN, "_btc_1h": DOWN},
      {"BTC/USDT": DOWN, "ETH/USDT": BROKEN, "_btc_1h": DOWN},
      {"A": _frame(500)}, {"A": _frame(150, seed=4)}]
SP = [_spec(), _spec(entry=RARE), _spec(entry="funding > 0.0005")]
out = []
for f in FR:
    for s in SP:
        c = compile_spec(s)
        for days in (5, 60, 90):
            out.append(rolling.has_decayed(c, f, CFG["risk"], "1h", recent_days=days, min_trades=3, floor_pf=0.85))
            out.append(rolling.recent_verdict(c, f, CFG["risk"], "1h", recent_days=days, min_trades=20, min_pf=1.15, max_days=365))
open(sys.argv[3], "w").write(json.dumps(out, default=repr, allow_nan=True))
