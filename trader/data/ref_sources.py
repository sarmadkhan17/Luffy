"""Fetch each reference market, and build the alt index.

Every fetcher takes an injectable `get` (or ccxt-style `fetch`) so tests
never touch the network, and every fetcher returns the same frame shape —
COLS, `ts` UTC, sorted, floats, NaN where nothing was measured — so the
store and the DSL never special-case a source.

Yahoo's chart API is unofficial and can change without notice. A failure
there must degrade to a stale reference (-> NaN -> UNTESTED), never to a
fabricated value; `refresh_all` isolates each source for exactly that.
"""
from __future__ import annotations

import logging
import time
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests

from ..core.types import TF_MS

log = logging.getLogger(__name__)

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
DEFILLAMA_STABLES = "https://stablecoins.llama.fi/stablecoincharts/all"
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"
UA = {"User-Agent": "Mozilla/5.0 (luffy reference recorder)"}
COLS = ["ts", "open", "high", "low", "close", "volume"]


def get_json(url, params=None, timeout: float = 25.0):
    r = requests.get(url, params=params, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLS)


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    df = df.astype({c: float for c in COLS[1:]})
    return (df.dropna(subset=["close"]).drop_duplicates("ts", keep="last")
              .sort_values("ts").reset_index(drop=True))[COLS]


def yahoo_frame(ticker: str, interval: str, rng: str,
                get=get_json) -> pd.DataFrame:
    d = get(YAHOO + quote(ticker, safe=""),
            {"range": rng, "interval": interval})
    res = ((d or {}).get("chart") or {}).get("result") or []
    if not res or not res[0].get("timestamp"):
        return _empty()
    r = res[0]
    n = len(r["timestamp"])
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0] or {}

    def col(k):
        v = q.get(k)
        return v if v is not None and len(v) == n else [None] * n

    frame = _finish(pd.DataFrame({
        "ts": pd.to_datetime(r["timestamp"], unit="s", utc=True),
        "open": col("open"), "high": col("high"), "low": col("low"),
        "close": col("close"), "volume": col("volume")}))
    frame.attrs.update(source=YAHOO+quote(ticker, safe=''), raw_source_record=d)
    return frame


def defillama_stables(get=get_json) -> pd.DataFrame:
    ts, close = [], []
    raw = get(DEFILLAMA_STABLES) or []
    for row in raw:
        try:
            v = float((row.get("totalCirculatingUSD") or {})["peggedUSD"])
            ts.append(int(row["date"]))
            close.append(v)
        except (TypeError, ValueError, KeyError):
            continue
    if not ts:
        return _empty()
    df = pd.DataFrame({"ts": pd.to_datetime(ts, unit="s", utc=True),
                       "close": close})
    for c in ("open", "high", "low", "volume"):
        df[c] = np.nan
    df = _finish(df)
    df.attrs.update(source=DEFILLAMA_STABLES, raw_source_record=raw)
    return df


def coingecko_global(get=get_json) -> dict:
    raw = get(COINGECKO_GLOBAL) or {}
    d = raw.get("data") or {}
    total = float((d.get("total_market_cap") or {})["usd"])
    pct = d.get("market_cap_percentage") or {}
    btc, usdt = float(pct["btc"]), float(pct["usdt"])
    class SourceSnapshot(dict):
        pass
    result = SourceSnapshot(cg_btc_d=btc, cg_usdt_d=usdt, cg_total=total,
                            cg_total2=total * (1.0 - btc / 100.0))
    result.raw_source_record = raw
    return result


def binance_klines(fetch, symbol: str, tf: str, since_ms: int, now_ms: int,
                   page: int = 1000) -> pd.DataFrame:
    """Page forward until the venue returns nothing new.

    A short page is NOT the end: asked for 1,500 bars the venue answered
    1,000, and a loop that stopped on the first short page stored BTCDOM
    from 2021-06-21 to 2021-12-04 and nothing after. Stop only on an empty
    page or one that makes no progress.
    """
    rows, since, step = [], int(since_ms), TF_MS[tf]
    while since < now_ms:
        batch = fetch(symbol, tf, since=since, limit=page) or []
        if not batch:
            break
        rows.extend(batch)
        nxt = int(batch[-1][0]) + step
        if nxt <= since:
            break
        since = nxt
    if not rows:
        return _empty()
    df = pd.DataFrame([r[:6] for r in rows], columns=COLS)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = _finish(df)
    df.attrs.update(source='binance:public:klines', raw_source_record=rows)
    return df


# ── the alt index ─────────────────────────────────────────────────────────
def alts_index(frames: dict, min_members: int = 5) -> pd.DataFrame:
    """Equal-weight index of the alt perps, on a 4h grid, close-only.

    Each bar's move is the mean of the members' own bar returns. A member
    with no bar at t (or t-1) has no return there — its gap is not filled —
    and a bar with fewer than `min_members` measured returns is NaN rather
    than a level. The caller's member list excludes BTC and stablecoins.
    """
    closes = {}
    for sym, df in frames.items():
        if df is None or len(df) < 2:
            continue
        s = pd.Series(df["close"].to_numpy(dtype=float),
                      index=pd.to_datetime(df["ts"], utc=True))
        closes[sym] = s[~s.index.duplicated(keep="last")].sort_index()
    if not closes:
        return _empty()
    panel = pd.DataFrame(closes).sort_index()
    grid = pd.date_range(panel.index.min(), panel.index.max(), freq="4h")
    rets = panel.reindex(grid).pct_change(fill_method=None)
    measured = rets.notna().sum(axis=1)
    r = rets.mean(axis=1).where(measured >= min_members)
    level = ((1.0 + r.fillna(0.0)).cumprod() * 100.0).where(r.notna())
    df = pd.DataFrame({"ts": grid, "close": level.to_numpy()})
    for c in ("open", "high", "low", "volume"):
        df[c] = np.nan
    result = _finish(df)
    from . import market_provenance as mp
    qualified = all(all(key in member for key in mp.META) for member in frames.values()
                    if member is not None and len(member))
    if qualified:
        result.attrs.update(source='derived:equal_weight_alt_index.v1',
                            raw_source_record={'derivation':'equal_weight_alt_index.v1',
                              'min_members':min_members,
                              'constituents':{sym:member['revision_id'].tolist()
                                for sym,member in frames.items() if member is not None and len(member)}})
    return result


# ── one refresh of everything ─────────────────────────────────────────────
#: BTCDOM/USDT was listed on 2021-06-17; ask from the start of that month
BTCDOM_FROM_MS = 1_622_505_600_000


def refresh_all(feed, members: list, store=None, get=get_json,
                now_ms: int | None = None,
                cg_every_ms: int | None = None) -> dict:
    """Bring every reference current. Each source is isolated: its failure
    is recorded as "error: …" and costs nothing else. Never raises."""
    from .references import HOUR, REFS, RefStore
    store = store or RefStore()
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    cg_every = 4 * HOUR if cg_every_ms is None else int(cg_every_ms)
    out: dict = {}

    def run(key, fn):
        try:
            started=store._clock_ms()
            frame=fn()
            frame.attrs['request_started_ms']=started
            out[key] = store.save(key, frame, now_ms=store._clock_ms())
        except Exception as e:
            out[key] = f"error: {e}"
            log.warning(f"reference {key}: {e}")

    for key, ref in REFS.items():
        if ref.source == "yahoo":
            first = store.last_ts(key) is None
            if ref.tf == "1h":
                interval, rng = "1h", ("730d" if first else "5d")
            else:
                interval, rng = "1d", ("10y" if first else "1mo")
            run(key, lambda t=ref.symbol, i=interval, g=rng:
                yahoo_frame(t, i, g, get))
        elif ref.source == "binance":
            last = store.last_ts(key)
            since = BTCDOM_FROM_MS if last is None \
                else last - 4 * TF_MS[ref.tf]      # re-read the tail
            run(key, lambda s=ref.symbol, tf=ref.tf, a=since:
                binance_klines(feed.ex.fetch_ohlcv, s, tf, a, now))
        elif ref.source == "defillama":
            run(key, lambda: defillama_stables(get))

    run("alts", lambda: alts_index(
        {s: feed.cached_ohlcv(s, "4h", limit=40000, as_of_ms=now) for s in members}))

    last = store.last_ts("cg_btc_d")
    if last is None or now - last >= cg_every:
        try:
            started=store._clock_ms()
            snap = coingecko_global(get)
            received = store._clock_ms()
            ts = pd.to_datetime([received], unit="ms", utc=True)
            for k, v in snap.items():
                frame = pd.DataFrame({"ts": ts, "close": [v]})
                frame.attrs.update(source=COINGECKO_GLOBAL,
                                   request_started_ms=started,
                                   raw_source_record={'response':snap.raw_source_record,
                                     'event_time_basis':'local_capture', 'field':k,
                                     'derivation':'coingecko_global.v1'})
                out[k] = store.save(k, frame, now_ms=received)
        except Exception as e:
            out["coingecko"] = f"error: {e}"
            log.warning(f"reference coingecko: {e}")
    return out
