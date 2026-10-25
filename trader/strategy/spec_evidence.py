"""Evidence for specs — the Analyst's measuring instrument.

`evidence.py` is the equivalent for the legacy Genome path and stays
untouched. This one loads the derivative series a spec's `data_requires`
actually asks for, refuses to score a spec whose data is missing, and reports
the same worst-symbol out-of-sample criterion the old gauntlet used.

Refusing is the important part. A funding strategy evaluated with no funding
data does not fail — every comparison against NaN is False, so it silently
scores zero trades and reads as 'no edge' rather than 'not tested'.
"""
from __future__ import annotations

import logging

from ..data.derivatives import DerivFeed
from .compile import compile_spec
from . import null_baseline, quant_contract
from .vector_backtest import funding_for, vector_walk_forward

log = logging.getLogger(__name__)

#: spec data_requires name -> series name in derivs.db. A name missing here
#: is invisible everywhere: load_derivs skips it (features go NaN, zero
#: trades) and missing_data does not count it as needed (scored, not
#: UNTESTED). `ls_account_ratio` was absent from 2026-09-03 to 2026-09-11.
_SERIES_FOR = {"funding": "funding", "open_interest": "oi",
               "taker_ratio": "taker_ratio", "ls_ratio": "ls_ratio",
               "ls_account_ratio": "ls_account_ratio",
               "basis": "basis"}


def dependency_store(frame, kind, supplied=None):
    """Bind existing stores to the selected retrospective dataset, never globals."""
    research_paths = getattr(frame, 'attrs', {}).get('research_read_paths')
    if research_paths is not None:
        if kind == 'derivs':
            return DerivFeed(db_path=research_paths.get('derivs'), read_only=True)
        from ..data.references import RefStore
        return RefStore(db_path=research_paths.get('refs') or research_paths.get('candles'), read_only=True)
    d = getattr(frame, 'attrs', {}).get('retrospective')
    if d is None:
        return supplied
    paths = d.get('paths', {})
    path = (paths.get('refs') or paths.get('candles')) if kind == 'candles' else paths.get(kind)
    if not path:
        raise ValueError('retrospective dependency requires dataset path: ' + kind)
    if supplied is not None:
        from pathlib import Path
        if (getattr(supplied, 'retrospective', None) != d or
                Path(supplied._db_path).resolve() != Path(path).resolve()):
            raise ValueError('retrospective dependency dataset mismatch: ' + kind)
        return supplied
    if kind == 'derivs':
        return DerivFeed(db_path=path, retrospective=d)
    from ..data.references import RefStore
    return RefStore(db_path=path, retrospective=d)


_DEFAULT_STORES = {}


def _default_store(kind):
    """One initialised store per data root: current lookups reuse its handles
    instead of opening (and initialising) a fresh store on every call."""
    from ..core.config import ROOT
    key = (kind, str(ROOT))
    store = _DEFAULT_STORES.get(key)
    if store is None:
        if kind == 'derivs':
            store = DerivFeed(read_only=True)
        else:
            from ..data.references import RefStore
            store = RefStore(read_only=True)
        _DEFAULT_STORES[key] = store
    return store


def load_derivs(symbol: str, requires, feed: DerivFeed | None = None, *, as_of_ms=None, window_start_ms=None, frame=None) -> dict:
    """{feature-series-name: raw observation frame} for one symbol."""
    feed = dependency_store(frame, 'derivs', feed) if any(r in _SERIES_FOR for r in requires) else feed
    feed = feed or _default_store('derivs')
    out = {}
    for req in requires:
        series = _SERIES_FOR.get(req)
        if series is None:                      # "ohlcv" and anything unknown
            continue
        extra = {}
        start = window_start_ms.get(req) if isinstance(window_start_ms, dict) else window_start_ms
        if start is not None:
            extra['window_start_ms'] = start
            if frame is None and as_of_ms is not None and isinstance(feed, DerivFeed):
                from ..core.instrument_registry import InstrumentId
                from ..core.types import MarketType
                from ..data.derivatives import to_binance
                extra['instrument_id'] = InstrumentId('binanceusdm', MarketType.FUTURES, to_binance(symbol)).value
        df = feed.load(symbol, series, as_of_ms=as_of_ms, **extra)
        if df is not None and len(df):
            out[series] = df
    return out


def load_refs(requires, store=None, *, as_of_ms=None, frame=None, window_start_ms=None) -> dict:
    """{reference key: frame} for every `ref:<key>` a spec requires."""
    keys = [r.split(":", 1)[1] for r in requires or ()
            if isinstance(r, str) and r.startswith("ref:")]
    if not keys:
        return {}
    store = dependency_store(frame, 'candles', store)
    if store is None:
        store = _default_store('refs')
    out = {}
    for k in keys:
        start = window_start_ms.get('ref:' + k) if isinstance(window_start_ms, dict) else window_start_ms
        df = store.load(k, as_of_ms=as_of_ms, **({'window_start_ms': start} if start is not None else {}))
        if df is not None and len(df):
            out[k] = df
    return out


def funding_series(symbol: str, df, feed: DerivFeed | None = None):
    """The venue's SIGNED 8-hourly funding rate, aligned onto `df`'s bars.

    Point-in-time, on the same discipline as `dsl._eval_htf`: a bar may only
    carry a settlement that had already happened by its own close, so this is
    searchsorted(side="right") - 1. A bar before the first settlement gets
    NaN, and the backtester charges its conservative flat rate there rather
    than a fabricated zero.

    Returns None when the store has nothing for this symbol, which keeps the
    caller on the flat-rate path it has always used.
    """
    import numpy as np

    feed = dependency_store(df, 'derivs', feed) or DerivFeed()
    try:
        f = feed.load(symbol, "funding")
    except Exception as e:
        if df.attrs.get('read_mode') == 'retrospective':
            raise
        log.warning(f"funding_series {symbol}: {e}")
        return None
    if f is None or not len(f) or df is None or not len(df):
        return None
    if 'available_at_ms' in f:
        from .features_deriv import align
        from ..data.market_provenance import ms
        from ..core.types import TF_MS
        import pandas as pd
        tf = df.attrs.get('timeframe') or '15m'
        arr = align(f,pd.to_datetime(ms(df['ts'])+TF_MS[tf],unit='ms',utc=True)).to_numpy(float)
        if df.attrs.get('read_mode') == 'retrospective' and not np.isfinite(arr).all():
            raise ValueError('retrospective funding requires trustworthy settlement coverage for every scored bar; flat substitution forbidden')
        return arr
    fts = f["ts"].to_numpy("datetime64[ns]")
    bts = df["ts"].to_numpy("datetime64[ns]")
    idx = np.searchsorted(fts, bts, side="right") - 1
    vals = f["value"].to_numpy(float)
    out = np.full(len(bts), np.nan)
    seen = idx >= 0
    out[seen] = vals[idx[seen]]
    return out


def funding_coverage(arr) -> float:
    """Fraction of bars carrying a real settlement. 0.0 when arr is None."""
    import numpy as np
    if arr is None or not len(arr):
        return 0.0
    return float(np.isfinite(arr).mean())


#: a derivative series must cover at least this fraction of the candle frame
#: for a walk-forward to mean anything. Below it the training half is partly
#: or wholly blank, so the split is not a split.
MIN_COVERAGE = 0.9


def missing_data(spec, symbols: list, feed: DerivFeed | None = None,
                 frames: dict | None = None, ref_store=None, world=None) -> dict:
    """{symbol: [problems]}. Empty = honestly testable.

    Presence is not enough. Binance retains open interest, taker ratio and
    long/short ratio for only ~30 days while the candle frame spans ~83, so a
    series can be present and still leave the entire TRAINING half blank. A
    spec scored that way reports a test-only profit factor as if it were
    walk-forward evidence — which is precisely the kind of false confidence
    this pipeline exists to prevent.
    """
    needed = [r for r in spec.data_requires if r in _SERIES_FOR]
    # a reference market is a series like any other: two years of hourly
    # S&P cannot score a five-year frame
    ref_needed = [r for r in spec.data_requires
                  if isinstance(r, str) and r.startswith("ref:")]
    world_needed = "world" in spec.data_requires
    if not needed and not ref_needed and not world_needed:
        return {}
    retrospective_frame = next((f for k, f in (frames or {}).items()
        if not k.startswith('_') and getattr(f, 'attrs', {}).get('retrospective')), None)
    if retrospective_frame is not None and world_needed:
        return {sym: ['world: explicit dataset-bound historical context required'] for sym in symbols}
    if world_needed:
        import ast
        from . import dsl
        from .features import FeatureCtx
        from ..world.context import load_context
        world = world if world is not None else load_context()
        world_queries = set()
        for expr in (spec.entry_long, spec.entry_short, *spec.filters, spec.exit.signal_exit):
            if not expr:
                continue
            for node in ast.walk(dsl.parse(expr)):
                if isinstance(node, ast.Call) and node.func.id == 'world_observation':
                    world_queries.add(tuple(a.value for a in node.args))
    if needed:
        feed = dependency_store(retrospective_frame, 'derivs', feed) or DerivFeed()
    refs = load_refs(ref_needed, ref_store, frame=retrospective_frame) if ref_needed else {}
    gaps: dict = {}
    for sym in symbols:
        have = load_derivs(sym, spec.data_requires, feed) if needed else {}
        problems = [f"{r}: absent" for r in needed
                    if _SERIES_FOR[r] not in have]
        problems += [f"{r}: absent" for r in ref_needed
                     if r.split(":", 1)[1] not in refs]
        frame = (frames or {}).get(sym)
        if world_needed:
            if frame is None or not len(frame):
                problems.append("world: historical context UNKNOWN")
            else:
                import numpy as np
                sf = frames_for(frame, spec.timeframe)
                context = FeatureCtx(sf, spec.timeframe, symbol=sym, world=world)
                for args in sorted(world_queries):
                    values = context.get('world_observation', args)
                    coverage = float(np.isfinite(values.to_numpy()).mean())
                    if coverage < MIN_COVERAGE:
                        problems.append(f"world:{args}: historical context UNKNOWN on {1-coverage:.0%} of bars")
        if frame is not None and len(frame) and "ts" in frame.columns:
            import pandas as pd
            f0 = pd.to_datetime(frame["ts"], utc=True).iloc[0]
            f1 = pd.to_datetime(frame["ts"], utc=True).iloc[-1]
            span = (f1 - f0).total_seconds()
            series = [(r, have.get(_SERIES_FOR[r])) for r in needed] + \
                     [(r, refs.get(r.split(":", 1)[1])) for r in ref_needed]
            for r, df in series:
                if df is None or span <= 0:
                    continue
                d0 = pd.to_datetime(df["ts"], utc=True).iloc[0]
                if frame.attrs.get('read_mode') == 'retrospective':
                    import numpy as np
                    from ..core.types import TF_MS
                    from ..data.market_provenance import ms
                    from .features_deriv import align
                    if r in _SERIES_FOR:
                        cuts = pd.to_datetime(ms(frame['ts']) + TF_MS[frame.attrs['timeframe']], unit='ms', utc=True)
                        cov = float(np.isfinite(align(df, cuts).to_numpy()).mean())
                    else:
                        from .features import FeatureCtx
                        from . import dsl
                        key = r.split(':', 1)[1]
                        tf = frame.attrs['timeframe']
                        ctx = FeatureCtx({tf: frame}, tf, market={key: df})
                        vals = dsl.evaluate(dsl.parse(f'ref("{key}", close)'), ctx)
                        cov = float(np.isfinite(vals.to_numpy()).mean())
                else:
                    cov = max(0.0, (f1 - max(d0, f0)).total_seconds()) / span
                if cov < MIN_COVERAGE:
                    problems.append(
                        f"{r}: covers {cov:.0%} of the frame "
                        f"(from {d0.date()}, frame starts {f0.date()})")
        if problems:
            gaps[sym] = problems
    return gaps


#: minutes per bar, for the backtester's funding and hold accounting
_TF_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}


def detect_tf(df) -> str | None:
    """Prefer the declared interval so gaps cannot imply a coarser timeframe."""
    import pandas as pd
    if df is not None and df.attrs.get('timeframe') in _TF_MINUTES:
        return df.attrs['timeframe']
    if df is None or len(df) < 3 or "ts" not in df.columns:
        return None
    ts = pd.to_datetime(df["ts"], utc=True)
    mins = float(ts.diff().dt.total_seconds().median() or 0) / 60.0
    for tf, m in _TF_MINUTES.items():
        if abs(mins - m) < m * 0.25:
            return tf
    return None


def frames_for(df, timeframe: str) -> dict:
    """Frames a spec of `timeframe` needs.

    If `df` is already at that timeframe it is used as-is; otherwise it is
    resampled down from a finer one. Resampling is the fallback, not the
    plan: the candle store now holds ~5 years natively at 4h and ~3 at 1h,
    while resampling from the 15m store would cap both at one year.

    A spec's timeframe is a gene, and it matters — a round trip costs ~18.5%
    of the risk staked at 15m (13.7% in fees alone) versus ~7.8% at 4h,
    because ATR/price scales with the square root of horizon while the fee
    does not.
    """
    from .backtest import resample
    own = detect_tf(df) or "15m"
    out = {own: df}
    if timeframe != own:
        if _TF_MINUTES.get(timeframe, 0) < _TF_MINUTES.get(own, 15):
            raise KeyError(f"cannot build '{timeframe}' from coarser '{own}'")
        r = resample(df, timeframe)
        if r is None or not len(r):
            raise KeyError(f"cannot build '{timeframe}' frame from '{own}'")
        out[timeframe] = r
    # context frames a spec may reach for with htf(), never finer than base
    for tf in ("1h", "4h"):
        if tf not in out and _TF_MINUTES[tf] > _TF_MINUTES.get(own, 15):
            r = resample(df, tf)
            if r is not None and len(r):
                out[tf] = r
    return out


def offline_strategy_db(feed):
    """Read registered specs without opening an implicit writable journal."""
    import sqlite3
    from pathlib import Path
    descriptor = getattr(feed, 'retrospective', None)
    path = descriptor.get('paths', {}).get('journal') if descriptor else 'data/luffy.db'
    if not path:
        raise ValueError('offline registered strategy requires explicit dataset paths.journal')
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def offline_feed(cfg=None, argv=None):
    """Existing offline programs select one explicit DataFeed dataset manifest.

    --dataset-manifest JSON declares dataset_id, cut_ms and paths.candles;
    paths.derivs must be supplied when measured dependent data is required.
    No manifest preserves the existing caller behavior.
    """
    from ..data.feed import DataFeed
    descriptor = (cfg or {}).get('offline_dataset')
    if argv is not None and '--dataset-manifest' in argv:
        import json
        from pathlib import Path
        i = argv.index('--dataset-manifest')
        if i + 1 >= len(argv):
            raise ValueError('--dataset-manifest requires a JSON path')
        descriptor = json.loads(Path(argv[i+1]).read_text())
        del argv[i:i+2]
    if descriptor is None:
        return DataFeed()
    path = descriptor.get('paths', {}).get('candles')
    if not path:
        raise ValueError('offline dataset requires paths.candles')
    return DataFeed(db_path=path, retrospective=descriptor)


def load_frames(cfg: dict, timeframe: str = "15m", feed=None,
                symbols: list | None = None) -> dict:
    """{symbol: df} read NATIVELY at `timeframe` from the candle store.

    evidence.load_frames is 15m-only. Reading natively is what makes higher
    timeframes testable: 4h has ~11000 stored bars (5 years) against the
    ~2200 a resample of the 15m store could produce.
    """
    from ..data.feed import DataFeed
    from .evidence import backtest_bars, backtest_symbols
    feed = feed or offline_feed(cfg)
    syms = symbols or backtest_symbols(cfg)
    limit = max(backtest_bars(cfg), 40000)
    out = {}
    for sym in syms:
        try:
            df = feed.cached_ohlcv(sym, timeframe, limit=limit)
        except Exception as e:
            log.warning(f"load_frames {sym} {timeframe}: {e}")
            continue
        if df is not None and len(df) >= 400:
            out[sym] = df
    btc = out.get("BTC/USDT")
    if btc is None:
        btc = feed.cached_ohlcv("BTC/USDT", timeframe, limit=limit)
    out["_btc_1h"] = btc
    return out


def risk_for(risk_cfg: dict, timeframe: str) -> dict:
    """`bar_minutes` drives funding accrual per bar held; a 4h spec holding 32
    bars holds for 128 hours, not 8."""
    out = dict(risk_cfg)
    out["bar_minutes"] = _TF_MINUTES.get(timeframe, 15)
    return out


def run_gauntlet(spec, frames: dict, cfg: dict, min_pf: float | None = None,
                 min_trades: int | None = None, require_robust: bool = True,
                 feed: DerivFeed | None = None) -> tuple[bool, dict]:
    """Walk-forward a spec across every symbol in `frames`.

    Same gate shape as evidence.run_gauntlet: worst-symbol out-of-sample
    profit factor, a minimum total OOS trade count, and train+test
    robustness on every symbol.

    Plus a null hypothesis. Profit factor alone is a statement about
    arithmetic: TP 4.5 ATR over SL 2.5 ATR pays a coin flip 35.7% of the
    time whatever the entry says, and an ALWAYS-LONG rule measured on real
    candles scored PF 1.28 purely on market drift. `null_percentile` is the
    share of the spec's OWN entry signals, circularly rotated, that it
    beats — the one number that separates timing from beta.
    """
    s = cfg.get("strategies", {}) or {}
    min_pf = float(s.get("gauntlet_min_test_pf", 1.15)) if min_pf is None \
        else min_pf
    min_trades = int(s.get("gauntlet_min_test_trades", 20)) \
        if min_trades is None else min_trades
    null_draws = int(s.get("gauntlet_null_draws", 60))
    min_null = float(s.get("gauntlet_min_null_percentile", 0.0))

    compiled = compile_spec(spec)
    syms = [k for k in frames if not k.startswith("_")]
    btc = frames.get("_btc_1h")
    btcd = {detect_tf(btc) or "15m": btc} if btc is not None else None
    retrospective_frame = next((f for k, f in frames.items()
        if not k.startswith('_') and getattr(f, 'attrs', {}).get('retrospective')), None)
    if any(r in _SERIES_FOR for r in spec.data_requires):
        feed = dependency_store(retrospective_frame, 'derivs', feed) or DerivFeed()

    gaps = missing_data(spec, syms, feed, frames=frames)
    ev = {"stage": "spec_walk_forward", "spec": spec.id,
          "data_requires": list(spec.data_requires), "per_symbol": {}}
    # QNT-01: definitions, estimated-cost basis and a pinned run identity
    identity_frames = dict(frames)
    if retrospective_frame is not None and not gaps:
        for sym in syms:
            for series, dependency in load_derivs(sym, spec.data_requires, feed, frame=frames[sym]).items():
                identity_frames[f'_dependency:{sym}:{series}'] = dependency
        for key, dependency in load_refs(spec.data_requires, frame=retrospective_frame).items():
            identity_frames[f'_reference:{key}'] = dependency
        if cfg['risk'].get('real_funding', True):
            for sym in syms:
                fund_store = dependency_store(frames[sym], 'derivs', feed)
                identity_frames[f'_cost:{sym}:funding'] = fund_store.load(sym, 'funding')
    ev["quant_contract"] = quant_contract.contract(spec, identity_frames, risk_for(cfg["risk"], spec.timeframe))
    if retrospective_frame is not None:
        ev.update(evidence_scope='retrospective_observation', factory_validated=False)
    if gaps:
        # NEVER score a spec whose inputs are absent: NaN comparisons are all
        # False, so it would report 0 trades and read as 'no edge'.
        return False, {**ev, "reason": f"missing data: {gaps}",
                       "untested": True}

    # Resolve every symbol to spec.timeframe ONCE, up front, so the universe
    # map handed to cross-sectional features carries the same granularity
    # the base symbol trades at rather than each symbol's native store tf.
    sym_frames_map: dict = {}
    for sym in syms:
        try:
            sym_frames_map[sym] = frames_for(frames[sym], spec.timeframe)
        except Exception as e:
            log.warning(f"spec gauntlet {spec.id} {sym}: frames_for: {e}")
    universe = {sy: {spec.timeframe: sf[spec.timeframe]}
                for sy, sf in sym_frames_map.items() if spec.timeframe in sf}

    results, per_symbol = [], {}
    null_pcts: dict = {}
    null_meds: dict = {}
    for sym in syms:
        sym_frames = sym_frames_map.get(sym)
        if sym_frames is None:
            continue
        derivs = load_derivs(sym, spec.data_requires, feed, frame=frames[sym])
        refs = load_refs(spec.data_requires, frame=frames[sym])
        risk = risk_for(cfg["risk"], spec.timeframe)
        try:
            r = vector_walk_forward(compiled, sym_frames, risk,
                                    btc=btcd, derivs=derivs, symbol=sym,
                                    universe=universe, market=refs)
        except Exception as e:
            log.warning(f"spec gauntlet {spec.id} {sym}: {e}")
            continue
        results.append(r)
        if null_draws > 0 and r["test"].trades >= 2:
            try:
                nb = null_baseline.assess(
                    compiled, sym_frames, risk, r["test"].profit_factor,
                    btc=btcd, derivs=derivs, universe=universe, market=refs, symbol=sym,
                    draws=null_draws, split=0.7, part="test",
                    funding=funding_for(sym, sym_frames[spec.timeframe], risk))
                null_pcts[sym] = nb.get("percentile")
                null_meds[sym] = nb.get("null_median")
            except Exception as e:
                log.warning(f"null baseline {spec.id} {sym}: {e}")
        per_symbol[sym] = {
            "train_pf": round(r["train"].profit_factor, 3),
            "test_pf": round(r["test"].profit_factor, 3),
            "test_trades": r["test"].trades,
            "test_wr": round(r["test"].winrate, 3),
            "max_dd_pct": round(r["test"].max_dd_pct, 2),
            "robust": r["robust"],
        }
    for sym, v in per_symbol.items():
        v["null_percentile"] = null_pcts.get(sym)
        v["null_median_pf"] = null_meds.get(sym)
    ev["per_symbol"] = per_symbol
    seen = [p for p in null_pcts.values() if p is not None]
    ev["null_percentile"] = (round(min(seen), 3) if seen else None)

    if not results:
        return False, {**ev, "reason": "no candle data", "untested": True}
    total = sum(r["test"].trades for r in results)
    ev["test_trades"] = total
    pfs = [r["test"].profit_factor for r in results if r["test"].trades >= 2]
    ev["worst_test_pf"] = round(min(pfs), 3) if pfs else 0.0
    if total < min_trades:
        # QNT-06: too little evidence is UNTESTED, never a FAIL
        return False, {**ev, "outcome": "UNTESTED",
                       "untested_code": "insufficient_trades",
                       "required_trades": min_trades,
                       "observed_trades": total,
                       "reason": f"only {total} out-of-sample trades "
                                 f"(<{min_trades})"}
    if not pfs:
        return False, {**ev, "outcome": "UNTESTED",
                       "untested_code": "insufficient_trades",
                       "required_trades": 2, "observed_trades": 0,
                       "reason": "no symbol reached 2 test trades"}
    if min(pfs) < min_pf:
        return False, {**ev, "reason": f"worst out-of-sample PF "
                                       f"{min(pfs):.2f} < {min_pf}"}
    if min_null > 0 and seen and min(seen) < min_null:
        return False, {**ev, "reason":
                       f"worst-symbol null percentile {min(seen):.0%} "
                       f"< {min_null:.0%} — entry timing is not "
                       f"distinguishable from firing the same signals at a "
                       f"random offset"}
    if require_robust and not all(r["robust"] for r in results):
        weak = [s for s, v in per_symbol.items() if not v["robust"]]
        return False, {**ev, "reason": f"not robust across train+test on {weak}"}
    return True, ev
