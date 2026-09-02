"""Donchian breakout + ATR trail on liquid non-crypto instruments.

Same mechanism, same engine, same rotation null as the crypto side.
Nothing here is tuned: N is reported for both 20 and 100, and every cost
scenario is reported side by side rather than picked.
"""
import sys, warnings
sys.path.insert(0, "/home/sarmad/.claude/jobs/55bdb341/tmp")
warnings.filterwarnings("ignore")

import numpy as np, pandas as pd
import mech, nq, divs
from universe import U
sys.path.insert(0, "/home/sarmad/trader")
from trader.strategy import null_baseline

COSTS = {                         # (taker_fee_pct per side, slippage_atr_frac)
    "cheap_2bp":  (0.005, 0.004),
    "mid_6bp":    (0.015, 0.012),
    "rich_20bp":  (0.05,  0.04),
    "crypto_like":(0.05,  0.06),
}


def build(sym, ac):
    """Dividend-adjusted daily OHLC + a quality report."""
    df = nq.ohlc(sym, ac).copy()
    q = {"sym": sym, "rows_raw": len(df)}
    # only these four are Nasdaq-listed, so only they carry full-history
    # distribution data; everything else falls back to stockanalysis (~5y)
    NASDAQ_DIV = {"QQQ", "TLT", "IEF", "SHY"}
    d = divs.sa(sym) if ac == "etf" else pd.DataFrame(columns=["date","amount"])
    dn = nq.dividends(sym) if sym in NASDAQ_DIV else pd.DataFrame(columns=["date","amount"])
    src = "nasdaq" if len(dn) >= len(d) and len(dn) else ("sa" if len(d) else "none")
    dv = dn if src == "nasdaq" else d
    f = np.ones(len(df)); pos = {v: i for i, v in enumerate(df["date"])}
    applied = 0
    for _, r in dv.iterrows():
        i = pos.get(r["date"])
        if i is None or i == 0:
            continue
        prev = float(df["close"].iloc[i - 1])
        if prev > 0 and 0 < r["amount"] < prev:
            f[i] = (prev - r["amount"]) / prev; applied += 1
    cum = np.append(np.cumprod(f[::-1])[::-1][1:], 1.0)
    for c in ("open", "high", "low", "close"):
        df[c] = df[c] * cum
    q.update(div_src=src, divs_applied=applied,
             div_from=str(dv["date"].min().date()) if len(dv) else "-",
             adj_oldest=round(float(cum[0]), 4))

    # ── quality ──────────────────────────────────────────────────────────
    bad = ((df[["open","high","low","close"]] <= 0).any(axis=1)
           | df[["open","high","low","close"]].isna().any(axis=1)
           | (df["high"] < df["low"])
           | (df["close"] > df["high"] + 1e-9) | (df["close"] < df["low"] - 1e-9)
           | (df["open"] > df["high"] + 1e-9) | (df["open"] < df["low"] - 1e-9))
    q["bad_bars"] = int(bad.sum())
    df = df[~bad].reset_index(drop=True)
    r = df["close"].pct_change()
    q["moves_gt25pct"] = int((r.abs() > 0.25).sum())
    q["max_abs_move"] = round(float(r.abs().max() * 100), 1)
    q["dupe_dates"] = int(df["date"].duplicated().sum())
    gaps = df["date"].diff().dt.days
    q["gaps_gt10d"] = int((gaps > 10).sum())
    q["max_gap_d"] = int(np.nanmax(gaps.to_numpy()[1:])) if len(df) > 1 else 0
    q["rows"] = len(df)
    q["start"] = str(df["date"].iloc[0].date()); q["end"] = str(df["date"].iloc[-1].date())
    q["med_vol"] = float(df["volume"].tail(250).median())
    q["atr_pct"] = round(float(((df["high"]-df["low"])/df["close"]).median()*100), 2)
    df["ts"] = df["date"].astype("int64") // 10**6
    return df.reset_index(drop=True), q


def main(draws=200):
    frames, quality = {}, []
    for sym, ac, sec in U:
        try:
            df, q = build(sym, ac)
        except Exception as e:
            print("SKIP %s: %s" % (sym, str(e)[:70])); continue
        q["sector"] = sec
        quality.append(q)
        if q["rows"] < 600 or q["bad_bars"] > 20:
            print("DROP %s: rows=%d bad=%d" % (sym, q["rows"], q["bad_bars"]))
            continue
        frames[sym] = (df, sec)
    pd.DataFrame(quality).to_csv("quality.csv", index=False)
    print(pd.DataFrame(quality)[["sym","sector","rows","start","end","bad_bars",
                                 "moves_gt25pct","max_abs_move","dupe_dates",
                                 "gaps_gt10d","max_gap_d","div_src",
                                 "divs_applied","adj_oldest","med_vol","atr_pct"]
          ].to_string(index=False))

    out = []
    for cname, (fee, slip) in COSTS.items():
        cfg = mech.risk_cfg(fee, slip, 1440, funding_8h=0.0)
        for N in (20, 100):
            for sym, (df, sec) in frames.items():
                r = mech.run_one(df, N, cfg, draws=draws, seed=7, symbol=sym)
                r.update(sym=sym, sector=sec, N=N, cost=cname)
                out.append(r)
            print("done", cname, N, flush=True)
    pd.DataFrame(out).to_csv("results.csv", index=False)
    print("WROTE results.csv")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 200)
