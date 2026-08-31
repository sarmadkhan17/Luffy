"""Shared internal gauntlet — one definition of "what counts as evidence".

Before this module the proposer and the harvester each hand-rolled their own
walk-forward loop over a hardcoded 2-symbol, 2900-bar (~30 day) window, then
applied different and much looser pass criteria (PF ≥ 0.5). Worse, the
proposer computed its walk-forward and then ranked candidates by a
*family-level* TradingView score that was identical for every candidate — so
parameter choice was effectively arbitrary.

Everything here is venue-exact: real Binance candles, the genome's real
genes, higher-timeframe context attached, train/test split enforced.
"""
from __future__ import annotations

import logging

import pandas as pd

from .backtest import context_frames, resample, score_results, walk_forward
from .genome import Genome

log = logging.getLogger(__name__)

DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "SUI/USDT"]


def _scfg(cfg: dict) -> dict:
    return cfg.get("strategies", {}) or {}


def backtest_symbols(cfg: dict) -> list[str]:
    return list(_scfg(cfg).get("backtest_symbols") or DEFAULT_SYMBOLS)


def backtest_bars(cfg: dict) -> int:
    """0/absent means 'everything cached' — we pass a large limit and let the
    feed return what it has, instead of the old hardcoded 30-day window."""
    n = int(_scfg(cfg).get("backtest_bars", 0) or 0)
    return n if n > 0 else 20000


#: bars to pull over the network when the local store is too thin to test on
COLD_FETCH_BARS = 2900


def _load_one(feed, symbol: str, limit: int):
    """Cache-first candle load for backtesting.

    The candle store is the right source here: it already holds ~87 days of
    15m bars, and a gauntlet must not spend minutes paging the REST API. Only
    a genuinely cold symbol falls through to a bounded network fetch.
    """
    df = None
    try:
        if hasattr(feed, "cached_ohlcv"):
            df = feed.cached_ohlcv(symbol, "15m", limit=limit)
    except Exception as e:
        log.warning(f"evidence: store read {symbol} failed: {e}")
    if df is not None and len(df) >= 400:
        return df
    try:
        return feed.fetch_ohlcv(symbol, "15m",
                                limit=min(limit, COLD_FETCH_BARS))
    except Exception as e:
        log.warning(f"evidence: fetch {symbol} failed: {e}")
        return None


def load_frames(feed, cfg: dict, symbols: list[str] | None = None) -> dict:
    """{symbol: 15m DataFrame} plus a shared 'BTC_1h' context frame.

    BTC 1h is RESAMPLED from BTC 15m rather than fetched, because
    candles.db holds far more 15m history (~87d) than 1h (~21d).
    """
    syms = symbols or backtest_symbols(cfg)
    limit = backtest_bars(cfg)
    out: dict[str, pd.DataFrame] = {}
    for sym in syms:
        df = _load_one(feed, sym, limit)
        if df is not None and len(df) >= 400:
            out[sym] = df
    btc = out.get("BTC/USDT")          # never `or` a DataFrame — ambiguous truth
    if btc is None:
        btc = _load_one(feed, "BTC/USDT", limit)
    out["_btc_1h"] = resample(btc, "1h") if btc is not None and len(btc) else None
    return out


#: families whose logic compares the symbol against another instrument and
#: therefore cannot be screened ON that instrument. rotation_momo needs
#: `myRet < btcRet * 0.7` while `myRet > 0`; on BTC itself myRet == btcRet, so
#: it emits zero signals and looks dead rather than untested.
_AVOID_SYMBOL = {"rotation_momo": "BTC/USDT"}


def _prescreen_symbol(family: str, syms: list[str]) -> str:
    avoid = _AVOID_SYMBOL.get(family)
    if avoid:
        for s in syms:
            if s != avoid:
                return s
    return syms[0]


def prescreen(genome: Genome, feed, cfg: dict,
              frames: dict | None = None) -> tuple[bool, tuple, dict]:
    """Cheap single-symbol filter run before the full multi-symbol gauntlet.

    The full gauntlet costs ~2 ms/bar/symbol; at 8k bars x 5 symbols x 24
    candidates that is over an hour, which an hourly brain tick cannot spend.
    This screens each candidate on the primary symbol over a shorter window
    and only survivors earn the expensive run.

    Returns (survived, rank_score, evidence). Deliberately lenient — it is a
    filter, not a verdict; the real bar is applied by run_gauntlet.
    """
    s = _scfg(cfg)
    bars = int(s.get("prescreen_bars", 4000))
    frames = frames if frames is not None else load_frames(feed, cfg)
    syms = [k for k in frames if not k.startswith("_")]
    if not syms:
        return False, (0, 0.0, 0), {"reason": "no candle data"}
    sym = _prescreen_symbol(genome.family, syms)
    df = frames[sym].iloc[-bars:]
    try:
        r = walk_forward(genome, df, cfg["risk"],
                         ctx=context_frames(df, frames.get("_btc_1h")))
    except Exception as e:
        return False, (0, 0.0, 0), {"reason": f"prescreen error: {e}"}
    score = score_results([r])
    ev = {"stage": "prescreen", "symbol": sym, "bars": len(df),
          "test_pf": round(r["test"].profit_factor, 3),
          "test_trades": r["test"].trades, "score": list(score)}
    # must at least trade and not be an outright disaster
    ok = r["test"].trades >= 2 and r["test"].profit_factor >= 0.8
    return ok, score, ev


def run_gauntlet(genome: Genome, feed, cfg: dict,
                 frames: dict | None = None,
                 min_pf: float | None = None,
                 min_trades: int | None = None,
                 require_robust: bool = True) -> tuple[bool, dict]:
    """Walk-forward the genome across the configured symbols.

    Returns (passed, evidence). Evidence always carries the per-symbol
    train/test profit factors and the rank `score`, so callers can both gate
    AND order candidates from the same run.
    """
    s = _scfg(cfg)
    min_pf = float(s.get("gauntlet_min_test_pf", 1.15)) if min_pf is None \
        else min_pf
    min_trades = int(s.get("gauntlet_min_test_trades", 20)) \
        if min_trades is None else min_trades

    frames = frames if frames is not None else load_frames(feed, cfg)
    btc_1h = frames.get("_btc_1h")

    avoid = _AVOID_SYMBOL.get(genome.family)
    results, per_symbol = [], {}
    for sym, df in frames.items():
        if sym.startswith("_") or df is None or len(df) < 400:
            continue
        if sym == avoid:
            # the family compares against this instrument, so on it the
            # comparison is degenerate — a guaranteed 0-trade result that
            # would drag the aggregate down as if the strategy had failed
            continue
        try:
            r = walk_forward(genome, df, cfg["risk"],
                             ctx=context_frames(df, btc_1h))
        except Exception as e:
            log.warning(f"evidence: walk_forward {genome.family} {sym}: {e}")
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

    score = score_results(results)
    ev = {"stage": "internal_walk_forward", "bars": backtest_bars(cfg),
          "symbols": list(per_symbol), "per_symbol": per_symbol,
          "score": list(score)}

    if not results:
        return False, {**ev, "reason": "no candle data"}

    total_trades = sum(r["test"].trades for r in results)
    if total_trades < min_trades:
        return False, {**ev, "reason": f"only {total_trades} out-of-sample "
                                       f"trades (<{min_trades})"}
    test_pfs = [r["test"].profit_factor for r in results
                if r["test"].trades >= 2]
    if not test_pfs:
        return False, {**ev, "reason": "no symbol reached 2 test trades"}
    if min(test_pfs) < min_pf:
        return False, {**ev, "reason": f"worst out-of-sample PF "
                                       f"{min(test_pfs):.2f} < {min_pf}"}
    # the train/test discipline that walk_forward already computed and that
    # nothing in production had ever read
    if require_robust and not all(r["robust"] for r in results):
        weak = [s for s, v in per_symbol.items() if not v["robust"]]
        return False, {**ev, "reason": f"not robust across train+test on {weak}"}
    return True, ev
