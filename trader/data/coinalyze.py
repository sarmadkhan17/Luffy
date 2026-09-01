"""Coinalyze — deep history for the three series Binance truncates.

Binance retains open interest, taker ratio and long/short ratio for about 30
days. The Analyst scores a spec over a frame of ~83 days and demands 90%
coverage, so from Binance alone those three series can NEVER clear the gate:
every mechanism built on positioning data is refused for coverage rather than
tested, permanently. Waiting for the recorder to accumulate forward pushes
that to roughly 2026-11-01 and does nothing for the history already lost.

Coinalyze aggregates the same series across venues and serves them far
deeper, on a free key (40 calls/minute). Set `COINALYZE_API_KEY` in `.env`.

VERIFICATION STATUS: the endpoint paths and the `api_key` header are taken
from Coinalyze's published API documentation, but this client has NOT yet
been exercised against a live key — we do not have one. Rather than guess the
venue-suffixed symbol format ("BTCUSDT_PERP.A" and friends), `resolve()` asks
`/future-markets` what exists and matches on base/quote, so the one detail
most likely to be wrong is discovered at runtime instead of hardcoded. Every
call degrades to an empty frame on any error, so a wrong guess costs a log
line, never a crash or a false series.
"""
from __future__ import annotations

import logging
import os
import time

import pandas as pd
import requests

log = logging.getLogger(__name__)

BASE = "https://api.coinalyze.net/v1"
#: our series name -> (endpoint, field in the response points)
ENDPOINTS = {
    "oi": ("/open-interest-history", "c"),
    "funding": ("/funding-rate-history", "c"),
    "ls_ratio": ("/long-short-ratio-history", "r"),
}


def api_key() -> str:
    return (os.getenv("COINALYZE_API_KEY") or "").strip()


class Coinalyze:
    """Read-only client. Absent key => `available` is False and every
    fetch returns an empty frame, so callers need no special-casing."""

    def __init__(self, key: str | None = None, timeout: int = 20):
        self.key = key if key is not None else api_key()
        self.timeout = timeout
        self._markets: dict[str, str] | None = None

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
    def markets(self) -> dict[str, str]:
        """{'BTC/USDT': '<coinalyze symbol>'} for perpetuals.

        Discovered, not guessed: the venue suffix scheme is the detail most
        likely to drift, and asking costs one call per process.
        """
        if self._markets is not None:
            return self._markets
        out: dict[str, str] = {}
        raw = self._get("/future-markets", {}) or []
        for m in raw if isinstance(raw, list) else []:
            try:
                if not m.get("is_perpetual", True):
                    continue
                base = (m.get("base_asset") or "").upper()
                quote = (m.get("quote_asset") or "").upper()
                sym = m.get("symbol")
                if base and quote and sym:
                    out.setdefault(f"{base}/{quote}", sym)
            except Exception:
                continue
        self._markets = out
        return out

    def resolve(self, symbol: str) -> str:
        return self.markets().get(symbol.split(":")[0].upper(), "")

    # ── series ───────────────────────────────────────────────────────────
    def history(self, symbol: str, series: str, years: float = 2.0,
                interval: str = "4h") -> pd.DataFrame:
        """One series, paged back `years`, in the DerivFeed frame shape."""
        empty = pd.DataFrame({"ts": pd.to_datetime([], utc=True),
                              "value": []})
        path_field = ENDPOINTS.get(series)
        if not path_field or not self.available:
            return empty
        path, field = path_field
        sym = self.resolve(symbol)
        if not sym:
            log.warning(f"coinalyze: no market for {symbol}")
            return empty
        now = int(time.time())
        raw = self._get(path, {"symbols": sym, "interval": interval,
                               "from": now - int(years * 365.25 * 86400),
                               "to": now})
        rows = []
        for entry in raw if isinstance(raw, list) else []:
            for pt in entry.get("history", []) or []:
                try:
                    rows.append((int(pt["t"]) * 1000, float(pt[field])))
                except (KeyError, TypeError, ValueError):
                    continue
        if not rows:
            return empty
        df = pd.DataFrame(rows, columns=["ts", "value"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df.dropna().sort_values("ts").reset_index(drop=True)
