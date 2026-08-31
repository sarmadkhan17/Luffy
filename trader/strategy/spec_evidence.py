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
from .vector_backtest import vector_walk_forward

log = logging.getLogger(__name__)

#: spec data_requires name -> series name in derivs.db
_SERIES_FOR = {"funding": "funding", "open_interest": "oi",
               "taker_ratio": "taker_ratio", "ls_ratio": "ls_ratio",
               "basis": "basis"}


def load_derivs(symbol: str, requires, feed: DerivFeed | None = None) -> dict:
    """{feature-series-name: raw observation frame} for one symbol."""
    feed = feed or DerivFeed()
    out = {}
    for req in requires:
        series = _SERIES_FOR.get(req)
        if series is None:                      # "ohlcv" and anything unknown
            continue
        df = feed.load(symbol, series)
        if df is not None and len(df):
            out[series] = df
    return out


#: a derivative series must cover at least this fraction of the candle frame
#: for a walk-forward to mean anything. Below it the training half is partly
#: or wholly blank, so the split is not a split.
MIN_COVERAGE = 0.9


def missing_data(spec, symbols: list, feed: DerivFeed | None = None,
                 frames: dict | None = None) -> dict:
    """{symbol: [problems]}. Empty = honestly testable.

    Presence is not enough. Binance retains open interest, taker ratio and
    long/short ratio for only ~30 days while the candle frame spans ~83, so a
    series can be present and still leave the entire TRAINING half blank. A
    spec scored that way reports a test-only profit factor as if it were
    walk-forward evidence — which is precisely the kind of false confidence
    this pipeline exists to prevent.
    """
    needed = [r for r in spec.data_requires if r in _SERIES_FOR]
    if not needed:
        return {}
    feed = feed or DerivFeed()
    gaps: dict = {}
    for sym in symbols:
        have = load_derivs(sym, spec.data_requires, feed)
        problems = [f"{r}: absent" for r in needed
                    if _SERIES_FOR[r] not in have]
        frame = (frames or {}).get(sym)
        if frame is not None and len(frame) and "ts" in frame.columns:
            import pandas as pd
            f0 = pd.to_datetime(frame["ts"], utc=True).iloc[0]
            f1 = pd.to_datetime(frame["ts"], utc=True).iloc[-1]
            span = (f1 - f0).total_seconds()
            for r in needed:
                df = have.get(_SERIES_FOR[r])
                if df is None or span <= 0:
                    continue
                d0 = pd.to_datetime(df["ts"], utc=True).iloc[0]
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
    """Infer a frame's timeframe from its own bar spacing."""
    import pandas as pd
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


def load_frames(cfg: dict, timeframe: str = "15m", feed=None,
                symbols: list | None = None) -> dict:
    """{symbol: df} read NATIVELY at `timeframe` from the candle store.

    evidence.load_frames is 15m-only. Reading natively is what makes higher
    timeframes testable: 4h has ~11000 stored bars (5 years) against the
    ~2200 a resample of the 15m store could produce.
    """
    from ..data.feed import DataFeed
    from .evidence import backtest_bars, backtest_symbols
    feed = feed or DataFeed()
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
    """
    s = cfg.get("strategies", {}) or {}
    min_pf = float(s.get("gauntlet_min_test_pf", 1.15)) if min_pf is None \
        else min_pf
    min_trades = int(s.get("gauntlet_min_test_trades", 20)) \
        if min_trades is None else min_trades

    compiled = compile_spec(spec)
    syms = [k for k in frames if not k.startswith("_")]
    btc = frames.get("_btc_1h")
    btcd = {"15m": btc} if btc is not None else None
    feed = feed or DerivFeed()

    gaps = missing_data(spec, syms, feed, frames=frames)
    ev = {"stage": "spec_walk_forward", "spec": spec.id,
          "data_requires": list(spec.data_requires), "per_symbol": {}}
    if gaps:
        # NEVER score a spec whose inputs are absent: NaN comparisons are all
        # False, so it would report 0 trades and read as 'no edge'.
        return False, {**ev, "reason": f"missing data: {gaps}",
                       "untested": True}

    results, per_symbol = [], {}
    for sym in syms:
        derivs = load_derivs(sym, spec.data_requires, feed)
        sym_frames = frames_for(frames[sym], spec.timeframe)
        risk = risk_for(cfg["risk"], spec.timeframe)
        try:
            r = vector_walk_forward(compiled, sym_frames, risk,
                                    btc=btcd, derivs=derivs, symbol=sym)
        except Exception as e:
            log.warning(f"spec gauntlet {spec.id} {sym}: {e}")
            continue
        results.append(r)
        per_symbol[sym] = {
            "train_pf": round(r["train"].profit_factor, 3),
            "test_pf": round(r["test"].profit_factor, 3),
            "test_trades": r["test"].trades,
            "test_wr": round(r["test"].winrate, 3),
            "max_dd_pct": round(r["test"].max_dd_pct, 2),
            "robust": r["robust"],
        }
    ev["per_symbol"] = per_symbol

    if not results:
        return False, {**ev, "reason": "no candle data", "untested": True}
    total = sum(r["test"].trades for r in results)
    ev["test_trades"] = total
    pfs = [r["test"].profit_factor for r in results if r["test"].trades >= 2]
    ev["worst_test_pf"] = round(min(pfs), 3) if pfs else 0.0
    if total < min_trades:
        return False, {**ev,
                       "reason": f"only {total} out-of-sample trades "
                                 f"(<{min_trades})"}
    if not pfs:
        return False, {**ev, "reason": "no symbol reached 2 test trades"}
    if min(pfs) < min_pf:
        return False, {**ev, "reason": f"worst out-of-sample PF "
                                       f"{min(pfs):.2f} < {min_pf}"}
    if require_robust and not all(r["robust"] for r in results):
        weak = [s for s, v in per_symbol.items() if not v["robust"]]
        return False, {**ev, "reason": f"not robust across train+test on {weak}"}
    return True, ev
