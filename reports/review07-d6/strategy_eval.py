"""D6 real-data evaluation of one existing supported strategy, read-only.

usage: strategy_eval.py CODE_ROOT CANDLES_COPY DERIVS_COPY OUT_JSON

Strategy: data/seed_specs/bollinger_band_fade.json (15m, ohlcv only, ATR stop,
rr target, no trail -- inside the exit set D7 does not touch). The production
gauntlet is called unchanged on the genuine retained replay frames; the only
substitutions are an isolated read-only DataFeed/DerivFeed over database COPIES.
"""
import json, os, sys
root, candles, derivs, out = sys.argv[1:5]
sys.path.insert(0, root)
import numpy as np
from trader.core.config import load_config
from trader.data.feed import DataFeed
from trader.data.derivatives import DerivFeed
from trader.data import market_provenance as mp
from trader.strategy import spec_evidence as SE, vector_backtest as VB
try:
    from trader.strategy import fill_timing
except ImportError:          # the pre-D6 tree has no fill-timing module
    fill_timing = None
from trader.strategy.spec import StrategySpec
from trader.strategy.compile import compile_spec

NOW = int(os.path.getmtime(candles) * 1000)
cfg = load_config(os.path.join(root, 'config.yaml'))
feed = DataFeed(exchange=None, db_path=candles, clock_ms=lambda: NOW, read_only=True)
dfeed = DerivFeed(db_path=derivs, clock_ms=lambda: NOW, read_only=True)
spec = StrategySpec.from_dict(json.load(open(os.path.join(root, 'data/seed_specs/bollinger_band_fade.json'))))
result = dict(code_root=root, spec=spec.id, timeframe=spec.timeframe, now_ms=NOW)

# 1. the production path, unchanged: load_frames (>=400 bars) -> run_gauntlet
symbols = [s for s in cfg['strategies'].get('backtest_symbols') or []] or ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
frames = SE.load_frames(cfg, spec.timeframe, feed=feed, symbols=symbols)
ok, ev = SE.run_gauntlet(spec, frames, cfg, feed=dfeed)
result['gauntlet'] = dict(passed=ok, symbols_loaded=[k for k in frames if not k.startswith('_')],
                          outcome=ev.get('outcome'), untested=ev.get('untested'), reason=ev.get('reason'),
                          test_trades=ev.get('test_trades'), per_symbol=ev.get('per_symbol'))

# 2. why: the retained replay frames available to this strategy, per symbol
per = {}
for sym in symbols:
    df = feed.replay_ohlcv(sym, spec.timeframe)
    row = dict(stored=feed.replay_metadata(sym, spec.timeframe).attrs['stored_bars'],
               replay_eligible=0 if df is None else len(df))
    if df is not None and len(df):
        ts = mp.ms(df['ts']); step = mp.TF_MS[spec.timeframe]
        runs = np.split(np.arange(len(df)), np.flatnonzero(np.diff(ts) != step) + 1)
        row.update(longest_contiguous_run=int(max(len(r) for r in runs)), runs=len(runs),
                   timing=fill_timing.summary(df, VB.WARMUP) if fill_timing else None,
                   earliest_decision_ms=int(mp.decision_ms(df, spec.timeframe).min()))
    per[sym] = row
result['frames'] = per
result['requirements'] = dict(load_frames_min_bars=400, engine_warmup_bars=VB.WARMUP,
                              min_oos_trades=cfg['strategies'].get('gauntlet_min_test_trades'))

# 3. the engine on the best retained frame, costed, as a diagnostic (never evidence)
best = max(per, key=lambda s: per[s]['replay_eligible'])
df = feed.replay_ohlcv(best, spec.timeframe)
engine = dict(symbol=best, bars=0 if df is None else len(df))
if df is None:
    engine['error'] = 'no replay-eligible bars'
else:
    compiled = compile_spec(spec)
    try:      # the production context builder resamples 1h/4h from the sparse frame
        VB._require_replay(SE.frames_for(df, spec.timeframe))
    except Exception as exc:
        engine['context_frames'] = type(exc).__name__ + ': ' + str(exc)[:120]
    fr = {spec.timeframe: df}
    VB.funding_for = lambda symbol, d, risk: SE.funding_series(symbol, d, feed=dfeed)
    try:
        res = VB.vector_backtest(compiled, fr, SE.risk_for(cfg['risk'], spec.timeframe), symbol=best)
        engine.update(trades=res.trades, pnl_usdt=res.pnl_usdt, profit_factor=res.profit_factor)
    except Exception as exc:
        engine['error'] = type(exc).__name__ + ': ' + str(exc)[:200]
result['engine_on_best_frame'] = engine
json.dump(result, open(out, 'w'), indent=1, default=str)
print(json.dumps(result, indent=1, default=str)[:3000])
