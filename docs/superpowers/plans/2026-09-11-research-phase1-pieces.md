# Research pipeline — Phase 1: the pieces — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the rule language market-wide context: a `ref(key, expr)` operator that evaluates any existing indicator on a reference market (S&P, DXY, gold, 10y yield, VIX, oil, BTC dominance, an alt index, stablecoin supply, CoinGecko dominance) and aligns it point-in-time, backed by a recorded, closed-bars-only reference store — usable in evidence and live.

**Architecture:** `trader/data/references.py` holds the registry (`REFS`: source, native bar, when a bar becomes known, when a silent feed goes stale) and `RefStore` (its own `refs` table in `data/candles.db`). `trader/data/ref_sources.py` fetches each source and builds the alt index; `refresh_all` is what the kernel's new `ref-recorder` thread runs hourly. The DSL gains `ref()` as a marker like `htf()`; evidence carries the reference frames as `frames["_market"]` (the way `_btc_1h` already travels), and the live path carries them on `Snapshot.market`.

**Tech Stack:** Python 3.12, pandas, numpy, requests, ccxt (production public data via `DataFeed.ex`), SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-09-11-luffy-research-pipeline-design.md` — Part 2 "the pieces".

## Deviations from the spec, with the measurement behind each

1. **Storage is a `refs` table, not the `candles` table under `ref:<key>`.** `core.types.norm_symbol` splits on `:`, so every `ref:<key>` would collapse to the one key `ref`; and `scripts/repair_partial_bars.py` and the mechanism screens walk every symbol in `candles` and would try to audit a reference against the venue. The DSL requirement name stays `ref:<key>`.
2. **`max_stale` follows each market's calendar.** The spec's "6 hours for hourly" would read the hourly S&P as NaN every night (NYSE is shut ~17.5h a day, ~66h over a weekend). Staleness exists to catch a *dead feed*, not a closed market — the last close *was* known. Yahoo 1h 80h, Yahoo 1d 100h, crypto 4h 12h, stables 1d 72h, CoinGecko snapshots 12h.
3. **CoinGecko is recorded forward only.** The 365-day per-coin backfill cannot produce a dominance: the free API has no total-market-cap history to divide by (`/global/market_cap_chart` answers 401, PRO only). DefiLlama already gives the USDT.D numerator back to 2017.
4. **Every daily bar is known 24h after its stamp.** Yahoo stamps an S&P daily bar at the 13:30 UTC open and it is final at 20:00; per-source offsets would need a trading calendar per ticker. One conservative rule (known at stamp + 1 day) can never peek; it delays the S&P daily close by ~17.5h. Hourly bars are known at stamp + 1h.
5. **`alts` is close-only.** An equal-weight index of closes has no honest high/low; indicators that need them read NaN.

## Global Constraints

- Run everything through the venv: `./venv/bin/python …`.
- **Missing information is NaN, never a fabricated default.** **Point-in-time:** a bar may only use information that had closed by its own close.
- `scripts/backtest_equivalence.py` must stay PASS; `scripts/bench_vector_backtest.py` above 20x.
- `spec_evidence` reports UNTESTED, never a score, when data is absent or does not span the frame (`MIN_COVERAGE = 0.9`).
- Journal writes via `_tx()` / the journal's own writers. The kernel is restarted only through `./restart.sh kernel` (cron watchdog is live).
- Tests never touch the network: every fetcher takes an injectable `get`/`fetch`.
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
  ```

---

### Task 1: The reference registry and its store

**Files:**
- Create: `trader/data/references.py`
- Test: `tests/test_reference_store.py`

**Interfaces:**
- Produces: `HOUR = 3_600_000`, `DAY = 86_400_000`; `@dataclass(frozen=True) Ref(key: str, source: str, tf: str, symbol: str, close_after_ms: int, max_stale_ms: int, close_only: bool = False)`; `REFS: dict[str, Ref]`; `to_ms(ts: pd.Series) -> np.ndarray` (int64 epoch ms); `RefStore(db_path=None)` with `.save(key, df, now_ms=None) -> int`, `.load(key, since_ms=0) -> pd.DataFrame | None` (columns `ts, open, high, low, close, volume`; `ts` UTC datetime; missing values NaN), `.last_ts(key) -> int | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reference_store.py`:

```python
"""Reference markets live in their own table, closed bars only.

Not the `candles` table: norm_symbol() splits on ':' so `ref:spx` would
collapse to `ref`, and repair_partial_bars.py walks every candle symbol and
would try to audit the S&P against Binance.
"""
import sqlite3

import numpy as np
import pandas as pd

from trader.data.references import DAY, HOUR, REFS, RefStore


def _frame(start_ms, n, step_ms, close0=100.0, ohlc=True):
    ts = pd.to_datetime([start_ms + i * step_ms for i in range(n)],
                        unit="ms", utc=True)
    c = [close0 + i for i in range(n)]
    d = {"ts": ts, "close": c}
    if ohlc:
        d.update(open=c, high=[x + 1 for x in c], low=[x - 1 for x in c],
                 volume=[10.0] * n)
    return pd.DataFrame(d)


def test_the_registry_names_every_source():
    for k in ("spx", "spx_1h", "dxy", "dxy_1h", "gold", "us10y", "vix",
              "oil", "btcdom", "alts", "stables", "cg_btc_d", "cg_usdt_d",
              "cg_total", "cg_total2"):
        assert k in REFS
    assert REFS["spx"].close_after_ms == DAY
    assert REFS["spx_1h"].close_after_ms == HOUR
    assert REFS["stables"].close_only and REFS["alts"].close_only


def test_a_forming_bar_is_not_stored(tmp_path):
    s = RefStore(tmp_path / "c.db")
    now = 1_789_000_000_000 - (1_789_000_000_000 % (4 * HOUR))
    df = _frame(now - 3 * 4 * HOUR, 4, 4 * HOUR)   # last bar opens at `now`
    assert s.save("btcdom", df, now_ms=now + 1) == 3
    assert len(s.load("btcdom")) == 3
    assert s.last_ts("btcdom") == now - 4 * HOUR


def test_references_do_not_touch_the_candles_table(tmp_path):
    s = RefStore(tmp_path / "c.db")
    s.save("btcdom", _frame(0, 2, 4 * HOUR), now_ms=10 ** 13)
    con = sqlite3.connect(tmp_path / "c.db")
    tabs = {r[0] for r in con.execute(
        "select name from sqlite_master where type='table'")}
    assert "refs" in tabs and "candles" not in tabs


def test_a_close_only_series_reads_nan_high_and_low(tmp_path):
    s = RefStore(tmp_path / "c.db")
    s.save("stables", _frame(0, 3, DAY, ohlc=False), now_ms=10 ** 13)
    df = s.load("stables")
    assert df["high"].isna().all() and df["low"].isna().all()
    assert df["close"].tolist() == [100.0, 101.0, 102.0]


def test_an_unmeasured_close_is_not_stored(tmp_path):
    s = RefStore(tmp_path / "c.db")
    df = _frame(0, 3, DAY)
    df.loc[1, "close"] = np.nan
    assert s.save("spx", df, now_ms=10 ** 13) == 2


def test_an_empty_store_reads_none(tmp_path):
    s = RefStore(tmp_path / "c.db")
    assert s.load("spx") is None and s.last_ts("spx") is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_reference_store.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'trader.data.references'`.

- [ ] **Step 3: Implement `trader/data/references.py`**

```python
"""Reference markets: what `ref(key, expr)` can read, and where it lives.

A reference is a market-wide series the book does not trade but reads —
the S&P, the dollar, gold, the 10y yield, VIX, oil, BTC dominance, an
equal-weight alt index, total stablecoin supply, CoinGecko's own dominance
figures. Each declares:

- `tf`            its native bar ("1h", "4h", "1d", or "snap" for a point
                  snapshot);
- `close_after_ms` how long after its stamp the value is FINAL and known.
                  Yahoo stamps an S&P daily bar at the 13:30 UTC open; the
                  close exists at 20:00. Aligning on the stamp would let every
                  S&P signal see 6.5 hours ahead, so every daily bar here is
                  known one full day after its stamp — conservative, and it
                  can never peek;
- `max_stale_ms`  how long after its last known bar a silent series still
                  counts as live. Staleness catches a DEAD FEED, not a closed
                  market: the S&P's Friday close was known all weekend.

Stored in their own `refs` table of data/candles.db. Not `candles`:
`norm_symbol` splits on ':' (`ref:spx` would collapse to `ref`), and every
tool that walks candle symbols would try to audit the S&P against Binance.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..core.config import ROOT
from ..core.types import TF_MS, closed_bars

HOUR = 3_600_000
DAY = 86_400_000


@dataclass(frozen=True)
class Ref:
    key: str
    source: str            # yahoo | binance | defillama | coingecko | computed
    tf: str                # native bar: 1h | 4h | 1d | snap
    symbol: str            # the source's own identifier (ticker, pair, field)
    close_after_ms: int    # stamp -> the moment the value is final and known
    max_stale_ms: int      # silent longer than this after known -> NaN
    close_only: bool = False


def _yahoo(key: str, ticker: str) -> list:
    return [Ref(key, "yahoo", "1d", ticker, DAY, 100 * HOUR),
            Ref(f"{key}_1h", "yahoo", "1h", ticker, HOUR, 80 * HOUR)]


REFS: dict[str, Ref] = {r.key: r for r in [
    *_yahoo("spx", "^GSPC"), *_yahoo("dxy", "DX-Y.NYB"),
    *_yahoo("gold", "GC=F"), *_yahoo("us10y", "^TNX"),
    *_yahoo("vix", "^VIX"), *_yahoo("oil", "CL=F"),
    Ref("btcdom", "binance", "4h", "BTCDOM/USDT:USDT", 4 * HOUR, 12 * HOUR),
    Ref("alts", "computed", "4h", "", 4 * HOUR, 12 * HOUR, close_only=True),
    Ref("stables", "defillama", "1d", "", DAY, 72 * HOUR, close_only=True),
    Ref("cg_btc_d", "coingecko", "snap", "btc", 0, 12 * HOUR, close_only=True),
    Ref("cg_usdt_d", "coingecko", "snap", "usdt", 0, 12 * HOUR,
        close_only=True),
    Ref("cg_total", "coingecko", "snap", "total", 0, 12 * HOUR,
        close_only=True),
    Ref("cg_total2", "coingecko", "snap", "total2", 0, 12 * HOUR,
        close_only=True),
]}


def to_ms(ts) -> np.ndarray:
    """Epoch milliseconds of a timestamp Series, whatever its resolution."""
    t = pd.to_datetime(ts, utc=True).dt.tz_localize(None)
    return np.asarray(t, dtype="datetime64[ms]").astype("int64")


def _num(v):
    return None if v is None or v != v else float(v)


class RefStore:
    """Closed reference bars, one row per (key, ts)."""

    def __init__(self, db_path=None):
        self._db_path = str(db_path) if db_path else \
            str(ROOT / "data" / "candles.db")
        self._local = threading.local()

    @property
    def db(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS refs (key TEXT NOT NULL, "
                "ts INTEGER NOT NULL, open REAL, high REAL, low REAL, "
                "close REAL, volume REAL, PRIMARY KEY (key, ts))")
            conn.commit()
            self._local.conn = conn
        return conn

    def save(self, key: str, df, now_ms: int | None = None) -> int:
        """Persist CLOSED bars with a measured close. Returns rows written."""
        ref = REFS[key]
        if df is None or not len(df):
            return 0
        if ref.tf in TF_MS:
            df = closed_bars(df, ref.tf, now_ms)
            if df is None or not len(df):
                return 0
        n = len(df)

        def col(c):
            return df[c].tolist() if c in df else [None] * n

        rows = [(key, int(t), _num(o), _num(h), _num(lo), _num(c), _num(v))
                for t, o, h, lo, c, v in zip(
                    to_ms(df["ts"]), col("open"), col("high"), col("low"),
                    col("close"), col("volume"))]
        rows = [r for r in rows if r[5] is not None]  # no close, no bar
        if rows:
            self.db.executemany(
                "INSERT OR REPLACE INTO refs VALUES (?,?,?,?,?,?,?)", rows)
            self.db.commit()
        return len(rows)

    def load(self, key: str, since_ms: int = 0):
        rows = self.db.execute(
            "SELECT ts, open, high, low, close, volume FROM refs "
            "WHERE key=? AND ts>=? ORDER BY ts", (key, int(since_ms))).fetchall()
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low",
                                         "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df.astype({c: float for c in
                          ("open", "high", "low", "close", "volume")})

    def last_ts(self, key: str) -> int | None:
        r = self.db.execute("SELECT MAX(ts) FROM refs WHERE key=?",
                            (key,)).fetchone()
        return int(r[0]) if r and r[0] is not None else None
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_reference_store.py -q -p no:cacheprovider`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add trader/data/references.py tests/test_reference_store.py
git commit -m "feat(refs): a registry of reference markets and a closed-bars store" -m "…trailer…"
```
(Commit body: the registry's close-after/max-stale semantics and why refs have their own table. End with the trailer from Global Constraints.)

---

### Task 2: The source fetchers

**Files:**
- Create: `trader/data/ref_sources.py`
- Test: `tests/test_ref_sources.py`

**Interfaces:**
- Consumes: `COLS` order `["ts","open","high","low","close","volume"]`; `TF_MS` from `trader.core.types`.
- Produces: `get_json(url, params=None, timeout=25.0) -> Any`; `yahoo_frame(ticker, interval, rng, get=get_json) -> DataFrame`; `defillama_stables(get=get_json) -> DataFrame`; `coingecko_global(get=get_json) -> dict[str, float]` (keys `cg_btc_d, cg_usdt_d, cg_total, cg_total2`; dominance in percent); `binance_klines(fetch, symbol, tf, since_ms, now_ms, page=1500) -> DataFrame` where `fetch` has ccxt's `fetch_ohlcv(symbol, tf, since=, limit=)` signature. Every frame: columns `COLS`, `ts` UTC datetime, sorted, de-duplicated, float columns, NaN where unmeasured.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ref_sources.py`:

```python
"""Each reference source, parsed from a recorded payload shape — no network."""
import numpy as np
import pytest

from trader.data import ref_sources as S


def test_yahoo_parses_bars_and_drops_an_unmeasured_close():
    seen = {}

    def get(url, params=None):
        seen.update(url=url, params=params)
        return {"chart": {"result": [{
            "timestamp": [1_788_000_000, 1_788_003_600, 1_788_007_200],
            "indicators": {"quote": [{
                "open": [1.0, 2.0, 3.0], "high": [1.5, 2.5, 3.5],
                "low": [0.5, 1.5, 2.5], "close": [1.2, None, 3.2],
                "volume": [10, 20, 30]}]}}]}}

    df = S.yahoo_frame("^GSPC", "1h", "5d", get)
    assert list(df.columns) == S.COLS
    assert df["close"].tolist() == [1.2, 3.2]
    assert "%5EGSPC" in seen["url"]
    assert seen["params"] == {"range": "5d", "interval": "1h"}


def test_yahoo_with_no_result_is_an_empty_frame_not_an_error():
    df = S.yahoo_frame("^GSPC", "1d", "1mo",
                       lambda url, params=None: {"chart": {"result": None}})
    assert df.empty and list(df.columns) == S.COLS


def test_defillama_stables_is_close_only_and_skips_bad_rows():
    rows = [{"date": "1511913600", "totalCirculatingUSD": {"peggedUSD": 1.5e9}},
            {"date": "1512000000", "totalCirculatingUSD": {}},
            {"date": "1512086400", "totalCirculatingUSD": {"peggedUSD": 1.6e9}}]
    df = S.defillama_stables(lambda url, params=None: rows)
    assert df["close"].tolist() == [1.5e9, 1.6e9]
    assert df["high"].isna().all() and df["open"].isna().all()


def test_coingecko_global_derives_total2_from_btc_dominance():
    payload = {"data": {"total_market_cap": {"usd": 3.0e12},
                        "market_cap_percentage": {"btc": 60.0, "usdt": 5.0}}}
    snap = S.coingecko_global(lambda url, params=None: payload)
    assert snap["cg_btc_d"] == 60.0 and snap["cg_usdt_d"] == 5.0
    assert snap["cg_total"] == 3.0e12
    assert snap["cg_total2"] == pytest.approx(1.2e12)


def test_binance_klines_pages_forward_until_the_venue_runs_dry():
    step = 4 * 3_600_000
    calls = []

    def fetch(symbol, tf, since=None, limit=None):
        calls.append(since)
        if since >= 4 * step:
            return []
        return [[since + i * step, 1.0, 2.0, 0.5, 1.5, 9.0]
                for i in range(2)]

    df = S.binance_klines(fetch, "BTCDOM/USDT:USDT", "4h", 0, 10 ** 13, page=2)
    assert len(df) == 4 and df["close"].tolist() == [1.5] * 4
    assert calls == [0, 2 * step, 4 * step]
```

- [ ] **Step 2: Run to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_ref_sources.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'trader.data.ref_sources'`.

- [ ] **Step 3: Implement the fetchers in `trader/data/ref_sources.py`**

```python
"""Fetch each reference market, and build the alt index.

Every fetcher takes an injectable `get` (or ccxt-style `fetch`) so tests
never touch the network, and every fetcher returns the same frame shape —
COLS, `ts` UTC, sorted, floats, NaN where nothing was measured — so the
store and the DSL never special-case a source.

Yahoo's chart API is unofficial and can change without notice. A failure
there must degrade to a stale reference (→ NaN → UNTESTED), never to a
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

    return _finish(pd.DataFrame({
        "ts": pd.to_datetime(r["timestamp"], unit="s", utc=True),
        "open": col("open"), "high": col("high"), "low": col("low"),
        "close": col("close"), "volume": col("volume")}))


def defillama_stables(get=get_json) -> pd.DataFrame:
    ts, close = [], []
    for row in get(DEFILLAMA_STABLES) or []:
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
    return _finish(df)


def coingecko_global(get=get_json) -> dict:
    d = (get(COINGECKO_GLOBAL) or {}).get("data") or {}
    total = float((d.get("total_market_cap") or {})["usd"])
    pct = d.get("market_cap_percentage") or {}
    btc, usdt = float(pct["btc"]), float(pct["usdt"])
    return {"cg_btc_d": btc, "cg_usdt_d": usdt, "cg_total": total,
            "cg_total2": total * (1.0 - btc / 100.0)}


def binance_klines(fetch, symbol: str, tf: str, since_ms: int, now_ms: int,
                   page: int = 1500) -> pd.DataFrame:
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
        if len(batch) < page:
            break
    if not rows:
        return _empty()
    df = pd.DataFrame([r[:6] for r in rows], columns=COLS)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return _finish(df)
```

Note on the paging test: the fake returns 2 rows per call (`page=2`), so the loop continues past each full page and stops on the empty reply at `4*step`.

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_ref_sources.py -q -p no:cacheprovider`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add trader/data/ref_sources.py tests/test_ref_sources.py
git commit -m "feat(refs): fetch Yahoo, Binance BTCDOM, DefiLlama stables and CoinGecko" -m "…trailer…"
```

---

### Task 3: The alt index and `refresh_all`

**Files:**
- Modify: `trader/data/ref_sources.py` (append)
- Test: `tests/test_alts_and_refresh.py`

**Interfaces:**
- Consumes: `RefStore`, `REFS`, `HOUR` (Task 1); fetchers (Task 2); `feed.ex.fetch_ohlcv` and `feed.cached_ohlcv(symbol, tf, limit=)` (`trader.data.feed.DataFeed`).
- Produces: `alts_index(frames: dict[str, DataFrame], min_members: int = 5) -> DataFrame` (close-only, 4h grid); `BTCDOM_FROM_MS = 1_622_505_600_000` (2021-06-01); `refresh_all(feed, members: list, store=None, get=get_json, now_ms=None, cg_every_ms=4*HOUR) -> dict[str, int | str]` — rows written per key, or `"error: …"`; never raises.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_alts_and_refresh.py`:

```python
"""The alt index, and one refresh of every reference.

A source that fails must cost only itself: Yahoo's API is unofficial, and a
refresh that died on its first error would leave every other series stale.
"""
import types

import numpy as np
import pandas as pd
import pytest

from trader.data import ref_sources as S
from trader.data.references import HOUR, RefStore

STEP = 4 * HOUR


def _px(closes, start=0):
    return pd.DataFrame({
        "ts": pd.to_datetime([start + i * STEP for i in range(len(closes))],
                             unit="ms", utc=True),
        "close": closes})


def test_the_index_is_the_equal_weight_mean_return():
    idx = S.alts_index({"A": _px([100, 110]), "B": _px([100, 120])},
                       min_members=2)
    assert idx["close"].tolist() == pytest.approx([115.0])
    assert idx["high"].isna().all()


def test_opposite_moves_cancel():
    idx = S.alts_index({"A": _px([100, 110, 121]), "B": _px([100, 90, 81])},
                       min_members=2)
    assert idx["close"].tolist() == pytest.approx([100.0, 100.0])


def test_too_few_members_is_nan_not_a_level():
    idx = S.alts_index({"A": _px([100, 110, 121])}, min_members=2)
    assert idx.empty


def test_a_member_missing_a_bar_does_not_fake_its_return():
    a = _px([100, 110, 121])
    b = _px([100, 120, 144]).drop(index=1)       # B has no middle bar
    c = _px([100, 100, 100])
    idx = S.alts_index({"A": a, "B": b, "C": c}, min_members=2)
    # bar 1: A +10%, C 0%, B unknown -> +5%; bar 2: A +10%, C 0%, B's return
    # spans the gap and is unknown -> +5% again
    assert idx["close"].tolist() == pytest.approx([105.0, 110.25])


class _Ex:
    def fetch_ohlcv(self, symbol, tf, since=None, limit=None):
        # one bar per call: a short page ends the paging loop
        return [[since, 1.0, 2.0, 0.5, 1.5, 9.0]]


def _feed():
    frames = {s: _px([100.0 + i for i in range(3)])
              for s in ("A", "B", "C", "D", "E")}
    return types.SimpleNamespace(
        ex=_Ex(), cached_ohlcv=lambda s, tf, limit=None: frames.get(s))


def _get(fail_yahoo=False):
    def get(url, params=None):
        if "yahoo" in url:
            if fail_yahoo:
                raise ConnectionError("yahoo down")
            return {"chart": {"result": [{"timestamp": [1_600_000_000],
                    "indicators": {"quote": [{"open": [1.0], "high": [1.0],
                                              "low": [1.0], "close": [1.0],
                                              "volume": [1.0]}]}}]}}
        if "llama" in url:
            return [{"date": "1600000000",
                     "totalCirculatingUSD": {"peggedUSD": 2.0e11}}]
        if "coingecko" in url:
            return {"data": {"total_market_cap": {"usd": 3e12},
                             "market_cap_percentage": {"btc": 55.0,
                                                       "usdt": 5.0}}}
        raise AssertionError(url)
    return get


def test_one_refresh_writes_every_source(tmp_path):
    store = RefStore(tmp_path / "c.db")
    out = S.refresh_all(_feed(), ["A", "B", "C", "D", "E"], store,
                        _get(), now_ms=10 ** 13)
    assert out["spx"] == 1 and out["vix_1h"] == 1
    assert out["btcdom"] >= 1 and out["stables"] == 1
    assert out["alts"] == 2
    assert out["cg_btc_d"] == 1 and store.load("cg_total2") is not None


def test_a_failing_source_costs_only_itself(tmp_path):
    store = RefStore(tmp_path / "c.db")
    out = S.refresh_all(_feed(), ["A", "B", "C", "D", "E"], store,
                        _get(fail_yahoo=True), now_ms=10 ** 13)
    assert str(out["spx"]).startswith("error:")
    assert out["stables"] == 1 and out["cg_btc_d"] == 1


def test_coingecko_is_snapshotted_at_most_every_four_hours(tmp_path):
    store = RefStore(tmp_path / "c.db")
    S.refresh_all(_feed(), [], store, _get(), now_ms=10 ** 13)
    out = S.refresh_all(_feed(), [], store, _get(), now_ms=10 ** 13 + HOUR)
    assert "cg_btc_d" not in out
```

- [ ] **Step 2: Run to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_alts_and_refresh.py -q -p no:cacheprovider`
Expected: FAIL with `AttributeError: module 'trader.data.ref_sources' has no attribute 'alts_index'`.

- [ ] **Step 3: Append to `trader/data/ref_sources.py`**

```python
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
    return _finish(df)


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
            out[key] = store.save(key, fn(), now_ms=now)
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
        {s: feed.cached_ohlcv(s, "4h", limit=40000) for s in members}))

    last = store.last_ts("cg_btc_d")
    if last is None or now - last >= cg_every:
        try:
            snap = coingecko_global(get)
            ts = pd.to_datetime([now], unit="ms", utc=True)
            for k, v in snap.items():
                out[k] = store.save(k, pd.DataFrame({"ts": ts, "close": [v]}),
                                    now_ms=now)
        except Exception as e:
            out["coingecko"] = f"error: {e}"
            log.warning(f"reference coingecko: {e}")
    return out
```

(With an empty member list, `alts_index({})` returns an empty frame and `save` writes 0 — not an error.)

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_alts_and_refresh.py tests/test_ref_sources.py tests/test_reference_store.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add trader/data/ref_sources.py tests/test_alts_and_refresh.py
git commit -m "feat(refs): an equal-weight alt index, and one isolated refresh of every source" -m "…trailer…"
```

---

### Task 4: `ref(key, expr)` in the rule language

**Files:**
- Modify: `trader/strategy/features.py:345` (register the marker beside `htf`)
- Modify: `trader/strategy/dsl.py` (`parse`, `data_requires`, `_eval`, new `_check_ref`, `_eval_ref`)
- Modify: `trader/strategy/pine_spec.py:31` (`_NO_PINE`)
- Test: `tests/test_dsl_ref.py`

**Interfaces:**
- Consumes: `REFS`, `to_ms` (Task 1); `FeatureCtx.market: dict[str, DataFrame] | None`.
- Produces: `ref("key", expr)` parses when `key in REFS` and `expr` uses only OHLCV features (none of `ref, htf, xs_rank, breadth, dispersion, btc_ret, btc_ema_dist, corr_btc, rel_strength_btc`); `data_requires` includes `"ref:<key>"` for every `ref()`; evaluation returns a float Series on the base index — the reference value KNOWN at each base bar's close, NaN when absent, before the first known bar, or when the last known bar is older than `max_stale_ms`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dsl_ref.py`:

```python
"""ref(key, expr): any indicator, on any reference market, point-in-time.

A base bar is decided at its own close, so it may read a reference value
only if that value was KNOWN by then — the reference bar's stamp plus its
declared close_after. Yahoo stamps an S&P daily bar at the 13:30 open; the
close is not known until later, so reading it at the stamp would peek.
"""
import numpy as np
import pandas as pd
import pytest

from trader.data.references import DAY, HOUR
from trader.strategy import dsl
from trader.strategy.features import FeatureCtx

T0 = 1_788_000_000_000 - (1_788_000_000_000 % DAY)   # a UTC midnight


def _bars(start_ms, n, step_ms, close0=100.0):
    c = [close0 + i for i in range(n)]
    return pd.DataFrame({
        "ts": pd.to_datetime([start_ms + i * step_ms for i in range(n)],
                             unit="ms", utc=True),
        "open": c, "high": c, "low": c, "close": c, "volume": [1.0] * n})


def _eval(expr, base, market):
    ctx = FeatureCtx(frames={"4h": base}, tf="4h", market=market)
    return dsl.evaluate(dsl.parse(expr), ctx)


def test_a_same_frame_reference_is_read_bar_for_bar():
    base = _bars(T0, 6, 4 * HOUR)
    ref = _bars(T0, 6, 4 * HOUR, close0=1000.0)
    out = _eval('ref("btcdom", close)', base, {"btcdom": ref})
    assert out.tolist() == [1000.0, 1001.0, 1002.0, 1003.0, 1004.0, 1005.0]


def test_a_daily_close_is_invisible_until_it_is_known():
    """S&P bar stamped D0 13:30 is known at D1 13:30 — not before."""
    spx = _bars(T0 + 13 * HOUR + 30 * 60_000, 2, DAY, close0=5000.0)
    base = _bars(T0 + DAY, 6, 4 * HOUR)          # D1 00:00 .. D1 20:00
    out = _eval('ref("spx", close)', base, {"spx": spx})
    # base bars close at D1 04,08,12,16,20,24h
    assert np.isnan(out.iloc[0]) and np.isnan(out.iloc[2])  # 12:00 < 13:30
    assert out.iloc[3] == 5000.0                  # 16:00: D0's close known
    assert 5001.0 not in out.tolist()             # D1's close not yet known


def test_a_dead_feed_reads_nan():
    base = _bars(T0 + 10 * DAY, 3, 4 * HOUR)
    ref = _bars(T0, 3, 4 * HOUR)                  # ten days silent
    out = _eval('ref("btcdom", close)', base, {"btcdom": ref})
    assert out.isna().all()


def test_no_reference_frame_reads_nan():
    base = _bars(T0, 3, 4 * HOUR)
    assert _eval('ref("btcdom", close)', base, None).isna().all()


def test_indicators_run_on_the_reference_not_the_base():
    base = _bars(T0, 30, 4 * HOUR, close0=1.0)
    ref = _bars(T0, 30, 4 * HOUR, close0=1000.0)
    out = _eval('ref("btcdom", ema(5))', base, {"btcdom": ref})
    assert out.iloc[-1] > 1000.0


def test_a_close_only_reference_has_no_high():
    base = _bars(T0 + DAY, 3, 4 * HOUR)
    st = _bars(T0, 1, DAY)
    st[["open", "high", "low"]] = np.nan
    out = _eval('ref("stables", high)', base, {"stables": st})
    assert out.isna().all()


def test_the_requirement_is_derived():
    tree = dsl.parse('ref("spx", close > ema(50)) and ref("dxy", ret(5)) < 0')
    assert set(dsl.data_requires(tree)) >= {"ref:spx", "ref:dxy"}


@pytest.mark.parametrize("expr", [
    'ref("nope", close)',
    'ref("spx", ref("dxy", close))',
    'ref("spx", funding)',
    'ref("spx", btc_ret(4))',
])
def test_what_cannot_mean_anything_on_a_reference_is_refused(expr):
    with pytest.raises(dsl.SpecError):
        dsl.parse(expr)


def test_a_ref_spec_is_not_translatable_to_pine():
    from trader.strategy.pine_spec import PineUnsupported, _emit
    with pytest.raises(PineUnsupported):
        _emit(dsl.parse('ref("spx", close)').body, set())
```

- [ ] **Step 2: Run to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_dsl_ref.py -q -p no:cacheprovider`
Expected: FAIL (`unknown feature 'ref'`).

- [ ] **Step 3: Register the marker**

In `trader/strategy/features.py`, directly after the `register("htf", …)(lambda ctx, tf, expr: expr)` statement, add:

```python

# ── reference markets ────────────────────────────────────────────────────
# A MARKER, like htf: dsl.evaluate() special-cases `ref(key, expr)`. `expr`
# is evaluated on the reference market's own frame (FeatureCtx.market[key])
# and aligned onto the base bars by the moment each reference bar became
# KNOWN — see trader.data.references.REFS for each source's clock.
register("ref", arg_specs=((str, None, None), SERIES_ARG))(
    lambda ctx, key, expr: expr)
```

- [ ] **Step 4: Teach the DSL**

In `trader/strategy/dsl.py`:

(a) In `parse`, directly after the line `            _check_args(name, node, spec)` add:

```python
            if name == "ref":
                _check_ref(node)
```

(b) Add these definitions directly above `def features_used(`:

```python
#: what cannot mean anything on a reference series: other frames, the
#: book, BTC context, the traded symbol's derivatives
_NOT_IN_REF = {"ref", "htf", "xs_rank", "breadth", "dispersion",
               "btc_ret", "btc_ema_dist", "corr_btc", "rel_strength_btc"}


def _check_ref(node: ast.Call) -> None:
    from ..data.references import REFS
    key = node.args[0].value
    if key not in REFS:
        raise SpecError(f"ref(): unknown reference '{key}' "
                        f"(known: {', '.join(sorted(REFS))})")
    for sub in ast.walk(node.args[1]):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
            name = sub.func.id
        elif isinstance(sub, ast.Name):
            name = sub.id
        else:
            continue
        if name not in FEATURES:
            continue
        if name in _NOT_IN_REF or tuple(FEATURES[name].requires) != ("ohlcv",):
            raise SpecError(f"ref(): '{name}' cannot be evaluated on a "
                            f"reference series")
```

(c) Replace the body of `data_requires` (the `req: set[str] = set()` line through the `return`) with:

```python
    req: set[str] = set()
    for tree in trees:
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "ref"):
                req.add(f"ref:{node.args[0].value}")
        for name in features_used(tree):
            req.update(FEATURES[name].requires)
    return tuple(sorted(req or {"ohlcv"}))
```

(d) In `_eval`, directly above `        if name == "htf":` add:

```python
        if name == "ref":
            return _eval_ref(node, ctx)
```

(e) Add `_eval_ref` directly after `_eval_htf`:

```python
def _eval_ref(node: ast.Call, ctx):
    """`ref(key, expr)` — evaluate `expr` on reference market `key`, then
    give each base bar the value that was KNOWN when that bar closed.

    A reference bar is known at its stamp plus its source's close_after
    (trader.data.references.REFS); a base bar closes at its stamp plus its
    own bar length. searchsorted side='right' minus one — the htf rule — on
    those two clocks, so a bar never reads a value from after its own close.
    Past the source's max_stale the feed is treated as dead: NaN, never the
    last value carried forever.
    """
    from ..core.types import TF_MS
    from ..data.references import REFS, to_ms
    from .features import FeatureCtx
    key = node.args[0].value
    ref = REFS[key]
    frame = (ctx.market or {}).get(key)
    if frame is None or not len(frame):
        return pd.Series(np.nan, index=ctx.index)
    sub = FeatureCtx(frames={ref.tf: frame}, tf=ref.tf, market=None,
                     symbol=key, _cache={})
    vals = _eval(node.args[1], sub)
    if not isinstance(vals, pd.Series):
        return pd.Series(vals, index=ctx.index)
    known = to_ms(frame["ts"]) + ref.close_after_ms
    base_close = to_ms(ctx.df["ts"]) + TF_MS.get(ctx.tf, 0)
    pos = np.searchsorted(known, base_close, side="right") - 1
    safe = np.clip(pos, 0, None)
    arr = vals.to_numpy(dtype=float)
    fresh = (pos >= 0) & (base_close - known[safe] <= ref.max_stale_ms)
    return pd.Series(np.where(fresh, arr[safe], np.nan), index=ctx.index)
```

- [ ] **Step 5: Pine cannot see a reference**

In `trader/strategy/pine_spec.py`, add this entry inside the `_NO_PINE = {` dict (line 31):

```python
    "ref": "reference markets (S&P, DXY, BTC dominance, …) are not on the "
           "symbol's Pine chart",
```

- [ ] **Step 6: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_dsl_ref.py tests/test_series_map_covers_registry.py $(ls tests/test_dsl*.py tests/test_pine*.py tests/test_compile*.py 2>/dev/null | sort -u) -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add trader/strategy/features.py trader/strategy/dsl.py trader/strategy/pine_spec.py tests/test_dsl_ref.py
git commit -m "feat(dsl): ref(key, expr) — any indicator on any reference market, point-in-time" -m "…trailer…"
```

---

### Task 5: Evidence can see references

**Files:**
- Modify: `trader/strategy/spec_evidence.py` (`load_refs`, `missing_data`)
- Modify: `trader/strategy/rolling.py` (`rolling_windows`, `_score_window`, `regime_windows`)
- Modify: `trader/brain/analyst.py` (`_ctx`, `redundancy`, `_null_percentiles`)
- Test: `tests/test_ref_evidence.py`

**Interfaces:**
- Consumes: `RefStore` (Task 1); `ref:<key>` requirements (Task 4).
- Produces: `spec_evidence.load_refs(requires, store=None) -> dict[str, DataFrame]`; `missing_data(spec, symbols, feed=None, frames=None, ref_store=None)` reports `"ref:<key>: absent"` or `"ref:<key>: covers N% of the frame (…)"`; evidence frames may carry `frames["_market"] = {key: df}`, which every `rolling` function forwards as `market=` to `compiled.entries`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ref_evidence.py`:

```python
"""A spec built on a reference must be scored with it, or refused UNTESTED.

The failure this prevents has happened three times with other series: a
requirement the evidence path does not load evaluates to NaN, takes zero
trades, and reads as "no edge" instead of "could not be tested".
"""
import types

import numpy as np
import pandas as pd

from trader.core.config import load_config
from trader.strategy import rolling, spec_evidence
from trader.strategy.spec import ExitSpec, StrategySpec


class _Store:
    def __init__(self, have):
        self.have = have

    def load(self, key, since_ms=0):
        return self.have.get(key)


def _days(start, n):
    return pd.DataFrame({"ts": pd.date_range(start, periods=n, freq="D",
                                             tz="UTC"),
                         "close": np.arange(n, dtype=float) + 1})


def test_load_refs_returns_only_what_exists():
    out = spec_evidence.load_refs(["ohlcv", "ref:spx", "ref:dxy"],
                                  _Store({"spx": _days("2024-01-01", 3)}))
    assert set(out) == {"spx"}


def test_an_absent_reference_is_untested():
    spec = types.SimpleNamespace(data_requires=["ohlcv", "ref:spx"])
    gaps = spec_evidence.missing_data(spec, ["BTC/USDT"],
                                      frames={"BTC/USDT": _days("2021-01-01", 900)},
                                      ref_store=_Store({}))
    assert gaps == {"BTC/USDT": ["ref:spx: absent"]}


def test_a_short_reference_is_untested_with_its_coverage():
    spec = types.SimpleNamespace(data_requires=["ohlcv", "ref:spx_1h"])
    gaps = spec_evidence.missing_data(
        spec, ["BTC/USDT"], frames={"BTC/USDT": _days("2021-01-01", 1800)},
        ref_store=_Store({"spx_1h": _days("2024-09-01", 700)}))
    assert "covers" in gaps["BTC/USDT"][0]


def test_a_reference_spanning_the_frame_is_testable():
    spec = types.SimpleNamespace(data_requires=["ohlcv", "ref:spx"])
    gaps = spec_evidence.missing_data(
        spec, ["BTC/USDT"], frames={"BTC/USDT": _days("2021-01-01", 900)},
        ref_store=_Store({"spx": _days("2016-01-01", 4000)}))
    assert gaps == {}


def test_rolling_forwards_the_market_frames(monkeypatch):
    seen = {}

    class _C:
        spec = types.SimpleNamespace(id="x", exit=ExitSpec())

        def entries(self, frames, **kw):
            seen.update(kw)
            n = len(next(iter(frames.values())))
            return np.zeros(n, bool), np.zeros(n, bool)

    monkeypatch.setattr(rolling, "funding_for", lambda *a, **k: None)
    monkeypatch.setattr(rolling, "simulate", lambda *a, **k: types.SimpleNamespace(
        trades=0, profit_factor=0.0, pnl_usdt=0.0, winrate=0.0,
        gross_win=0.0, gross_loss=0.0, wins=0))
    df = pd.DataFrame({"ts": pd.date_range("2025-01-01", periods=50,
                                           freq="4h", tz="UTC"),
                       "close": 1.0})
    rolling._score_window(_C(), {"AAA/USDT": df, "_market": {"spx": "F"}},
                          {}, "4h", 1)
    assert seen["market"] == {"spx": "F"}


def _spec(expr):
    return StrategySpec(
        id="p", name="p", thesis="t", invalidation="i", provenance={},
        universe={"include": []}, timeframe="4h", direction="long",
        entry_long=expr, entry_short="", filters=[], exit=ExitSpec(),
        regime_filter=[], markets=["futures"])


def test_the_analyst_loads_a_spec_s_references(monkeypatch):
    from trader.brain.analyst import Analyst
    a = Analyst.__new__(Analyst)
    a.cfg = {"risk": load_config()["risk"], "strategy": {}}
    monkeypatch.setattr(Analyst, "frames",
                        lambda self, tf, extra=(): {"X": None, "_btc_1h": None})
    monkeypatch.setattr(spec_evidence, "load_refs",
                        lambda req, store=None: {"spx": "F"}
                        if "ref:spx" in req else {})
    frames = a._ctx("4h", _spec('ref("spx", close) > 0'))[0]
    assert frames["_market"] == {"spx": "F"}
    assert "_market" not in a._ctx("4h", _spec("close > 0"))[0]
```

- [ ] **Step 2: Run to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_ref_evidence.py -q -p no:cacheprovider`
Expected: FAIL (`load_refs` missing; `missing_data` has no `ref_store`).

- [ ] **Step 3: `spec_evidence.load_refs` and `missing_data`**

In `trader/strategy/spec_evidence.py`, add after `load_derivs`:

```python
def load_refs(requires, store=None) -> dict:
    """{reference key: frame} for every `ref:<key>` a spec requires."""
    keys = [r.split(":", 1)[1] for r in requires or ()
            if isinstance(r, str) and r.startswith("ref:")]
    if not keys:
        return {}
    if store is None:
        from ..data.references import RefStore
        store = RefStore()
    out = {}
    for k in keys:
        df = store.load(k)
        if df is not None and len(df):
            out[k] = df
    return out
```

Replace the whole `missing_data` function with:

```python
def missing_data(spec, symbols: list, feed: DerivFeed | None = None,
                 frames: dict | None = None, ref_store=None) -> dict:
    """{symbol: [problems]}. Empty = honestly testable.

    Presence is not enough. Binance retains open interest, taker ratio and
    long/short ratio for only ~30 days while the candle frame spans ~83, so a
    series can be present and still leave the entire TRAINING half blank. A
    spec scored that way reports a test-only profit factor as if it were
    walk-forward evidence — which is precisely the kind of false confidence
    this pipeline exists to prevent. The same holds for a reference market:
    two years of hourly S&P cannot score a five-year frame.
    """
    needed = [r for r in spec.data_requires if r in _SERIES_FOR]
    ref_needed = [r for r in spec.data_requires
                  if isinstance(r, str) and r.startswith("ref:")]
    if not needed and not ref_needed:
        return {}
    if needed:
        feed = feed or DerivFeed()
    refs = load_refs(ref_needed, ref_store) if ref_needed else {}
    gaps: dict = {}
    for sym in symbols:
        have = load_derivs(sym, spec.data_requires, feed) if needed else {}
        problems = [f"{r}: absent" for r in needed
                    if _SERIES_FOR[r] not in have]
        problems += [f"{r}: absent" for r in ref_needed
                     if r.split(":", 1)[1] not in refs]
        frame = (frames or {}).get(sym)
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
                cov = max(0.0, (f1 - max(d0, f0)).total_seconds()) / span
                if cov < MIN_COVERAGE:
                    problems.append(
                        f"{r}: covers {cov:.0%} of the frame "
                        f"(from {d0.date()}, frame starts {f0.date()})")
        if problems:
            gaps[sym] = problems
    return gaps
```

- [ ] **Step 4: `rolling` forwards `frames["_market"]`**

In `trader/strategy/rolling.py`, in each of `rolling_windows`, `_score_window` and `regime_windows`, add `market=frames.get("_market"),` to the `compiled.entries(` call, directly after its `universe=universe,` argument. The three calls become:

```python
            lo, sh = compiled.entries({timeframe: df}, btc=btc, derivs=derivs,
                                      universe=universe,
                                      market=frames.get("_market"),
                                      symbol=sym)
```
(`rolling_windows` and `regime_windows`), and in `_score_window`:

```python
            lo, sh = compiled.entries({timeframe: recent}, btc=btc,
                                      derivs=derivs, universe=universe,
                                      market=frames.get("_market"),
                                      symbol=sym)
```

(`recent_verdict` and `has_decayed` go through `_score_window`; every loop already skips keys starting with `_`.)

- [ ] **Step 5: The Analyst loads a spec's references**

In `trader/brain/analyst.py` `_ctx`, replace:

```python
        frames = self.frames(tf, self._declared(spec))
        btc = frames.get("_btc_1h")
```

with:

```python
        frames = self.frames(tf, self._declared(spec))
        # reference markets travel the way _btc_1h does: as an underscore
        # key every symbol loop already skips. Copied, never written into
        # the cached dict, because the references differ per spec.
        try:
            req = compile_spec(spec).data_requires
        except Exception:
            req = tuple(spec.data_requires or ())
        market = spec_evidence.load_refs(req)
        if market:
            frames = {**frames, "_market": market}
        btc = frames.get("_btc_1h")
```

In `redundancy`, add `market=frames.get("_market"),` to the candidate's `cand.entries(` call (after `universe=universe,`), and `market=spec_evidence.load_refs(oc.data_requires) or None,` to the book member's `oc.entries(` call (after its `universe=` argument).

In `_null_percentiles`, add `market=frames.get("_market"),` to the `vector_walk_forward(` call (after `universe=universe`) and to the `null_baseline.assess(` call (after `universe=universe,`).

- [ ] **Step 6: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_ref_evidence.py tests/test_series_map_covers_registry.py tests/test_admission_null_gate.py tests/test_analyst.py tests/test_rolling.py tests/test_evaluation_context.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add trader/strategy/spec_evidence.py trader/strategy/rolling.py trader/brain/analyst.py tests/test_ref_evidence.py
git commit -m "feat(evidence): a spec on a reference is scored with it, or refused UNTESTED" -m "…trailer…"
```

---

### Task 6: Live — a reference spec can signal

**Files:**
- Modify: `trader/core/types.py:84-98` (`Snapshot.market`)
- Modify: `trader/strategy/compile.py` (`to_evaluator`'s `self.entries(` call)
- Modify: `trader/kernel.py` (`_market_for`, Snapshot build, cache reset in `_load_spec_population`)
- Test: `tests/test_ref_live.py`

**Interfaces:**
- Consumes: `spec_evidence.load_refs` (Task 5).
- Produces: `Snapshot.market: dict | None`; `Kernel._market_for() -> dict | None` (TTL 300 s, keyed on the book's `ref:*` requirements).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ref_live.py`:

```python
"""Live, a ref() spec reads the same reference frames the backtest did.

Every requirement the live path forgot has meant a spec that sat in paper
forever reading NaN (derivatives, 2026-09-02; the account ratio, 09-11).
"""
import inspect

import pandas as pd

from trader.core.types import Action, Snapshot
from trader.strategy.compile import compile_spec
from trader.strategy.spec import ExitSpec, StrategySpec

H4 = 4 * 3_600_000


def _bars(close0, n=6, start=1_700_000_000_000 - (1_700_000_000_000 % H4)):
    c = [close0 + i for i in range(n)]
    return pd.DataFrame({
        "ts": pd.to_datetime([start + i * H4 for i in range(n)], unit="ms",
                             utc=True),
        "open": c, "high": c, "low": c, "close": c, "volume": [1.0] * n})


def _spec():
    return StrategySpec(
        id="refspec", name="refspec", thesis="t", invalidation="i",
        provenance={}, universe={"include": []}, timeframe="4h",
        direction="long", entry_long='ref("btcdom", close) > 1000',
        entry_short="", filters=[], exit=ExitSpec(), regime_filter=[],
        markets=["futures"])


def _snap(market):
    return Snapshot(symbol="AAA/USDT", ts="", price=1.0,
                    dfs={"4h": _bars(1.0)}, market=market)


def test_a_ref_spec_fires_when_the_snapshot_carries_its_reference():
    ev = compile_spec(_spec()).to_evaluator()
    sig = ev(None, _snap({"btcdom": _bars(1000.0)}))
    assert sig is not None and sig.action == Action.BUY


def test_without_the_reference_it_cannot_fire():
    ev = compile_spec(_spec()).to_evaluator()
    assert ev(None, _snap(None)) is None


def test_the_kernel_hands_references_to_every_snapshot():
    from trader import kernel as K
    src = inspect.getsource(K)
    assert "market=self._market_for()" in src


def test_market_for_loads_only_what_the_book_needs(monkeypatch):
    from trader import kernel as K
    from trader.strategy import spec_evidence
    calls = []
    monkeypatch.setattr(spec_evidence, "load_refs",
                        lambda req, store=None: calls.append(tuple(req))
                        or {"spx": "F"})
    k = object.__new__(K.Kernel)
    k._spec_requires = ("ohlcv",)
    assert k._market_for() is None and calls == []
    k._spec_requires = ("ohlcv", "ref:spx")
    assert k._market_for() == {"spx": "F"}
    assert k._market_for() == {"spx": "F"} and len(calls) == 1   # cached
```

- [ ] **Step 2: Run to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_ref_live.py -q -p no:cacheprovider`
Expected: FAIL (`Snapshot` has no `market`).

- [ ] **Step 3: `Snapshot.market`**

In `trader/core/types.py`, after the line `    derivs: dict | None = None        # {series: obs frame} for funding/OI/taker specs` add:

```python
    market: dict | None = None        # {ref key: frame} for ref() specs
```

- [ ] **Step 4: The evaluator passes it**

In `trader/strategy/compile.py` `to_evaluator`, change the `self.entries(` call to:

```python
                lo, sh = self.entries(frames, btc=btc,
                                      derivs=getattr(snap, "derivs", None),
                                      universe=getattr(snap, "universe", None),
                                      market=getattr(snap, "market", None),
                                      symbol=snap.symbol)
```

- [ ] **Step 5: The kernel supplies it**

In `trader/kernel.py`:

(a) Directly after the `_derivs_for` method, add:

```python
    _MARKET_TTL = 300.0                # the recorder refreshes hourly

    def _market_for(self) -> dict | None:
        """The reference frames the book's specs read through ref().

        Keyed on the book, not the symbol: the S&P is the same series for
        every coin, so it is loaded once per TTL and shared by every
        snapshot.
        """
        reqs = [r for r in getattr(self, "_spec_requires", ())
                if isinstance(r, str) and r.startswith("ref:")]
        if not reqs:
            return None
        hit = getattr(self, "_market_cache", None)
        now = time.time()
        if hit and now - hit[0] < self._MARKET_TTL:
            return hit[1]
        try:
            from .strategy.spec_evidence import load_refs
            out = load_refs(reqs) or None
        except Exception as e:
            log.warning(f"reference series unavailable: {e}")
            out = None
        self._market_cache = (now, out)
        return out
```

(b) In the `Snapshot(` build, change `derivs=self._derivs_for(symbol))` to:

```python
                        derivs=self._derivs_for(symbol),
                        market=self._market_for())
```

(c) In `_load_spec_population`, directly after `self._derivs_cache = {}` add `self._market_cache = None`.

- [ ] **Step 6: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_ref_live.py tests/test_spec_signals_on_closed_bars.py $(ls tests/test_kernel*.py tests/test_compile*.py 2>/dev/null | sort -u) -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add trader/core/types.py trader/strategy/compile.py trader/kernel.py tests/test_ref_live.py
git commit -m "feat(live): snapshots carry the reference frames a ref() spec reads" -m "…trailer…"
```

---

### Task 7: The recorder, its config, and the first backfill

**Files:**
- Modify: `trader/kernel.py` (`_reference_recorder`, thread start)
- Modify: `config.yaml` (new `references:` block)
- Create: `scripts/backfill_references.py`
- Test: `tests/test_ref_recorder.py`

**Interfaces:**
- Consumes: `refresh_all(feed, members, store=None, …)` (Task 3); `REFS`, `RefStore` (Task 1).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ref_recorder.py`:

```python
"""A reference nobody records is a reference that goes stale, then NaN."""
import inspect

from trader.core.config import load_config


def test_the_kernel_runs_a_reference_recorder():
    from trader import kernel as K
    src = inspect.getsource(K)
    assert "_reference_recorder" in src and '"ref-recorder"' in src


def test_the_alt_index_members_exclude_btc():
    r = load_config()["references"]
    assert r["enabled"] is True
    members = r["alts_members"]
    assert len(members) == 35 and "BTC/USDT" not in members
```

- [ ] **Step 2: Run to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_ref_recorder.py -q -p no:cacheprovider`
Expected: FAIL.

- [ ] **Step 3: Config**

Append to `config.yaml` (top level):

```yaml

references:                     # reference markets for ref(key, expr) — trader/data/references.py
  enabled: true
  interval_minutes: 60          # Yahoo, BTCDOM, stables and the alt index; CoinGecko snapshots every 4h
  # the alt index: the 36 crypto perps the mechanism screens use, minus BTC
  alts_members: [ETH/USDT, SOL/USDT, XRP/USDT, BNB/USDT, DOGE/USDT, ADA/USDT,
                 LINK/USDT, AVAX/USDT, LTC/USDT, 1000PEPE/USDT, APT/USDT,
                 BCH/USDT, DASH/USDT, FET/USDT, INJ/USDT, T/USDT, WLD/USDT,
                 XMR/USDT, UNI/USDT, SUI/USDT, TAO/USDT, ZEC/USDT, NEAR/USDT,
                 FIL/USDT, AAVE/USDT, HYPE/USDT, TRUMP/USDT, 1000SHIB/USDT,
                 ARB/USDT, CRV/USDT, DOT/USDT, ICP/USDT, OP/USDT, TRX/USDT,
                 XLM/USDT]
```

- [ ] **Step 4: The kernel thread**

In `trader/kernel.py`, add the method directly before `    def _strategy_mechanism_loop(self) -> None:`:

```python
    def _reference_recorder(self) -> None:
        """Keep the reference markets current: S&P, DXY, gold, the 10y, VIX,
        oil, BTC dominance, the alt index, stablecoin supply, CoinGecko.

        A reference nobody records goes stale, and a stale reference reads
        NaN — so a ref() spec would go silent rather than wrong, which is
        the right failure, but still a failure. Each source is isolated
        inside refresh_all: Yahoo's API is unofficial and may break alone.
        """
        import time as _t
        rcfg = self.cfg.get("references", {}) or {}
        if not rcfg.get("enabled", True):
            log.info("reference recorder disabled")
            return
        from .data.ref_sources import refresh_all
        every = float(rcfg.get("interval_minutes", 60)) * 60
        members = list(rcfg.get("alts_members") or [])
        _t.sleep(60)                        # let boot settle
        while not self._stop:
            try:
                rep = refresh_all(self.feed, members)
                errs = {k: v for k, v in rep.items() if isinstance(v, str)}
                log.info(f"references refreshed: {len(rep) - len(errs)} ok"
                         + (f", errors {errs}" if errs else ""))
            except Exception as e:
                log.warning(f"reference recorder: {e}")
            for _ in range(int(every)):
                if self._stop:
                    return
                _t.sleep(1)
```

And in the thread starts, directly after the `derivs-recorder` thread start, add:

```python
        if (self.cfg.get("references", {}) or {}).get("enabled", True):
            threading.Thread(target=self._reference_recorder, daemon=True,
                             name="ref-recorder").start()
```

- [ ] **Step 5: The one-shot backfill script**

Create `scripts/backfill_references.py`:

```python
"""Backfill every reference market once, then print what is stored.

The kernel's ref-recorder does the same on its first pass; this exists so
the depth of every series can be checked without waiting for it.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

from trader.core.config import load_config          # noqa: E402
from trader.data.feed import DataFeed               # noqa: E402
from trader.data.ref_sources import refresh_all     # noqa: E402
from trader.data.references import REFS, RefStore   # noqa: E402


def main() -> int:
    cfg = load_config()
    store = RefStore()
    members = (cfg.get("references") or {}).get("alts_members") or []
    rep = refresh_all(DataFeed(), members, store)
    print(f"{'key':11s} {'source':10s} {'tf':5s} {'rows':>6s}  span")
    for key, ref in REFS.items():
        df = store.load(key)
        n = 0 if df is None else len(df)
        span = "" if not n else (f"{df['ts'].iloc[0]:%Y-%m-%d %H:%M} -> "
                                 f"{df['ts'].iloc[-1]:%Y-%m-%d %H:%M}")
        note = rep.get(key, "")
        print(f"{key:11s} {ref.source:10s} {ref.tf:5s} {n:6d}  {span}"
              + (f"   [{note}]" if isinstance(note, str) and note else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the tests, then the real backfill**

Run: `./venv/bin/python -m pytest tests/test_ref_recorder.py -q -p no:cacheprovider`
Expected: PASS.

Run: `timeout 900 ./venv/bin/python scripts/backfill_references.py`
Expected, approximately: `spx` ~2,510 daily rows from 2016; `spx_1h` ~3,500 hourly from 2024; `dxy`/`gold`/`oil` likewise (hourly counts higher — those trade ~23h); `btcdom` ~11,000 4h rows from 2021-06; `stables` ~3,200 daily from 2017-11; `alts` ~11,000 4h rows; each `cg_*` 1 row. Any `[error: …]` is a finding: record it, do not paper over it.

- [ ] **Step 7: Commit**

```bash
git add trader/kernel.py config.yaml scripts/backfill_references.py tests/test_ref_recorder.py
git commit -m "feat(refs): the kernel records every reference hourly; backfill script" -m "…trailer…"
```
(Put the backfill's printed depth table in the commit body.)

---

### Task 8: Record it, verify everything, restart

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md**

- Daemon table: add the row `| `ref-recorder` | 1h | reference markets for `ref()`: Yahoo (S&P, DXY, gold, 10y, VIX, oil — daily and hourly), Binance BTCDOM, the alt index, DefiLlama stablecoin supply, CoinGecko dominance every 4h |`.
- Data files table: add `| `data/candles.db` → `refs` table | reference markets, closed bars only, keyed by `REFS` (`trader/data/references.py`) — NOT the `candles` table (norm_symbol splits on ':'; the repair script walks candle symbols) |`.
- "The strategy language": after the cross-sectional paragraph add: "**`ref(key, expr)`** evaluates any OHLCV indicator on a reference market (`REFS`) and gives each base bar the value KNOWN at its close: reference stamp + `close_after_ms` against base stamp + bar length, `searchsorted(side="right") - 1`; past `max_stale_ms` it is NaN (a dead feed, not a closed market). Every daily reference is known one day after its stamp. `data_requires` carries `ref:<key>`; evidence carries the frames as `frames["_market"]`, live as `Snapshot.market`."
- In the bullet "The registry was never the ceiling", change "cross-asset context beyond BTC;" to "cross-asset context beyond BTC — **CLOSED 2026-09-11** by `ref()`;".

- [ ] **Step 2: Full verification**

```bash
timeout 2400 ./venv/bin/python -m pytest tests/ -q --color=no -p no:cacheprovider 2>&1 | grep -aE "^FAILED|^ERROR|passed|failed" | tail -30
timeout 900 ./venv/bin/python scripts/backtest_equivalence.py 2>&1 | tail -1
timeout 900 ./venv/bin/python scripts/bench_vector_backtest.py 2>&1 | tail -4
```
Expected: only the known `test_macro_guard::test_a_restart_reuses_the_cached_calendar` failure; `PASS`; speedup above 20x.

- [ ] **Step 3: Restart and watch the recorder's first pass**

```bash
./restart.sh kernel && ./restart.sh dashboard
```
Then, after ~2 minutes:
```bash
grep -a "" logs/luffy.log | tail -400 | grep -a "BOOT\|cycle #\|references refreshed\|reference \|Traceback\|\[ERROR\]" | tail -10
./venv/bin/python -m trader.kernel --status
```
Expected: `LUFFY BOOT`, a `cycle #`, `references refreshed: N ok`, heartbeat under 120 s.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: phase 1 — ref() and the reference markets" -m "…trailer…"
```
