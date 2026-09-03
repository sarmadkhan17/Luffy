"""Coinalyze — deep history for the series Binance truncates.

Binance retains open interest, taker ratio and long/short ratio for about 30
days. The Analyst scores a spec over a frame of ~83 days and demands 90%
coverage, so from Binance alone those series can NEVER clear the gate: every
mechanism built on positioning data is refused for coverage rather than
tested, permanently.

Coinalyze aggregates the same series across venues and serves them deeper, on
a free key (40 calls/minute). Set `COINALYZE_API_KEY` in `.env`.

VERIFIED against a live key on 2026-09-03. What that established, all of it
the difference between a right answer and a plausible wrong one:

* **Retention is a ~2000-bar rolling buffer per interval, not a horizon.**
  1hour 84d, 4hour 334d, 12hour 1000d, daily 2190d. Requesting older than the
  buffer returns EMPTY, not a truncated page, so there is nothing to page
  toward — one request per series is the whole history there is. At our 4h
  frame that is 334 days: 4.5x the 75-day coverage floor, and one regime.
* **`interval` is an enum of names** ("4hour"), not our timeframe strings
  ("4h"). Sending ours answers 400, and a 400 degrades to an empty frame —
  which reads downstream as "no history exists", not as "we asked wrongly".
* **`/future-markets` returns one row per exchange per market.** Taking the
  first match resolved BTC/USDT to Bybit while SOL/LINK/UNI happened to land
  on Binance, so the store would have mixed venues under one key. Binance is
  exchange code "A"; nothing else is acceptable, because the whole point is
  depth on the book we actually trade.
* **Units and definitions do not transfer.** Measured against what
  `derivs.db` already holds for BTC: funding is served in PERCENT where we
  store a fraction (ratio exactly 100.0000), open interest agrees as served
  (0.9998, p10-p90 within 0.6%), and the long/short series is Binance's
  `globalLongShortAccountRatio` — corr +1.0000, max abs difference 0.00000
  over 179 points — while our recorder stores `topLongShortPositionRatio`,
  which is ANTI-correlated with it at -0.64. Those two are different
  measurements of different populations; merging them under one series name
  would fabricate a signal, so the account ratio gets its own series.

`deepen()` deliberately does not fetch funding: Binance serves it natively
over 4-5 years and `DerivFeed.save` is INSERT OR REPLACE keyed by
(symbol, series, ts), so fetching it here could only overwrite good rows with
334 shallower ones. The scale below exists so that a deliberate future use is
correct, not so that the harvester may call it.

Every call degrades to an empty frame on any error, so an upstream change
costs a log line, never a crash or a false series.
"""
from __future__ import annotations

import logging
import os
import time

import pandas as pd
import requests

log = logging.getLogger(__name__)

BASE = "https://api.coinalyze.net/v1"

#: Binance. `/future-markets` carries one row per exchange and the venue we
#: trade is the only one whose book is worth storing under our symbol keys.
BINANCE = "A"

#: our timeframe names -> Coinalyze's documented `interval` enum
_INTERVALS = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
              "1h": "1hour", "2h": "2hour", "4h": "4hour", "6h": "6hour",
              "12h": "12hour", "1d": "daily"}
_CANON = set(_INTERVALS.values())

#: how deep each interval's rolling buffer actually reaches, measured
#: 2026-09-03. Callers use this to ask for what exists.
RETENTION_DAYS = {"1hour": 84, "4hour": 334, "12hour": 1000, "daily": 2190}


def to_interval(tf: str) -> str:
    """Our timeframe name -> Coinalyze's enum. Raises rather than sending an
    unmappable value, because the API answers 400 and a 400 looks like
    'no data' by the time it reaches the coverage brief."""
    tf = (tf or "").strip()
    if tf in _CANON:
        return tf
    try:
        return _INTERVALS[tf]
    except KeyError:
        raise ValueError(
            f"interval {tf!r} has no Coinalyze equivalent; "
            f"valid: {sorted(_CANON)}") from None


#: our series name -> (endpoint, point field, scale, market flag it needs)
#:
#: `scale` converts the served units into what `derivs.db` stores; it is 1.0
#: wherever they already agree, and anything else is a measured correction.
ENDPOINTS = {
    "oi":               ("/open-interest-history",    "c", 1.0,  None),
    "funding":          ("/funding-rate-history",     "c", 0.01, None),
    "ls_account_ratio": ("/long-short-ratio-history", "r", 1.0,
                         "has_long_short_ratio_data"),
}


def api_key() -> str:
    return (os.getenv("COINALYZE_API_KEY") or "").strip()


class Coinalyze:
    """Read-only client. Absent key => `available` is False and every
    fetch returns an empty frame, so callers need no special-casing."""

    def __init__(self, key: str | None = None, timeout: int = 20):
        self.key = key if key is not None else api_key()
        self.timeout = timeout
        self._markets: dict[str, dict] | None = None

    @property
    def available(self) -> bool:
        return bool(self.key)

    def _get(self, path: str, params: dict):
        if not self.available:
            return None
        try:
            r = requests.get(f"{BASE}{path}", params=params,
                             headers={"api_key": self.key},
                             timeout=self.timeout)
            if r.status_code == 401:
                log.warning("coinalyze: key rejected")
                return None
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.warning(f"coinalyze {path}: {e}")
            return None

    # ── symbol discovery ─────────────────────────────────────────────────
    def markets(self) -> dict[str, dict]:
        """{'BTC/USDT': <the Binance market row>} for perpetuals.

        Binance-only by construction. The venue suffix scheme is discovered
        rather than guessed, and the row is kept whole so a caller can ask
        whether a series exists before requesting it.
        """
        if self._markets is not None:
            return self._markets
        out: dict[str, dict] = {}
        raw = self._get("/future-markets", {}) or []
        for m in raw if isinstance(raw, list) else []:
            try:
                if not m.get("is_perpetual", True):
                    continue
                if m.get("exchange") != BINANCE:
                    continue
                base = (m.get("base_asset") or "").upper()
                quote = (m.get("quote_asset") or "").upper()
                if base and quote and m.get("symbol"):
                    out.setdefault(f"{base}/{quote}", m)
            except Exception:
                continue
        self._markets = out
        return out

    def market(self, symbol: str) -> dict:
        return self.markets().get(symbol.split(":")[0].upper(), {})

    def resolve(self, symbol: str) -> str:
        return self.market(symbol).get("symbol", "")

    # ── series ───────────────────────────────────────────────────────────
    def history(self, symbol: str, series: str, years: float = 2.0,
                interval: str = "4hour") -> pd.DataFrame:
        """One series in the DerivFeed frame shape, in OUR units.

        `years` is a request, not a promise: retention is a rolling bar
        buffer, so asking for more than `RETENTION_DAYS[interval]` returns
        what exists and no error.
        """
        empty = pd.DataFrame({"ts": pd.to_datetime([], utc=True), "value": []})
        spec = ENDPOINTS.get(series)
        if not spec or not self.available:
            return empty
        path, field, scale, needs_flag = spec
        try:
            interval = to_interval(interval)
        except ValueError as e:
            log.warning(f"coinalyze: {e}")
            return empty
        market = self.market(symbol)
        sym = market.get("symbol")
        if not sym:
            log.warning(f"coinalyze: {symbol} is not a Binance perpetual here")
            return empty
        if needs_flag and not market.get(needs_flag, False):
            log.info(f"coinalyze: {symbol} carries no {series}")
            return empty
        now = int(time.time())
        raw = self._get(path, {"symbols": sym, "interval": interval,
                               "from": now - int(years * 365.25 * 86400),
                               "to": now})
        rows = []
        for entry in raw if isinstance(raw, list) else []:
            for pt in entry.get("history", []) or []:
                try:
                    rows.append((int(pt["t"]) * 1000, float(pt[field]) * scale))
                except (KeyError, TypeError, ValueError):
                    continue
        if not rows:
            return empty
        df = pd.DataFrame(rows, columns=["ts", "value"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df.dropna().sort_values("ts").reset_index(drop=True)
