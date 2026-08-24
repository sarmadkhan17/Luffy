"""Flow analyst — market microstructure: aggressor vs passive liquidity.

Reads (futures): order-book imbalance, taker-flow proxy from klines
(taker buy volume ratio), funding crowding. Confirms or questions what
structure/momentum claim; on spot it degrades to kline-taker flow only.
"""
from __future__ import annotations

import pandas as pd

from .base import Analyst
from ..core.types import Snapshot, Vote


class FlowAnalyst(Analyst):
    name = "flow"
    regime_affinity = ("TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE")

    def __init__(self, exchange=None, book_depth_frac: float = 0.02):
        self.exchange = exchange
        self.book_depth_frac = book_depth_frac
        self._book_cache: dict[str, tuple[float, dict]] = {}

    # exchange-fed inputs are injected per-cycle by the kernel
    def set_context(self, symbol: str, book: dict | None,
                    funding_rate: float | None) -> None:
        self._ctx = (symbol, book, funding_rate)

    def evaluate(self, snap: Snapshot) -> Vote:
        df = snap.df("15m")
        if df is None or len(df) < 30:
            return self._vote(self.name, snap, 0.0, 0.2, "no data")
        conv, conf, notes = 0.0, 0.3, []

        # ── taker flow from klines (works everywhere incl. spot) ─────────
        # ccxt binance kline col 9 = taker-buy base volume
        if "taker_buy" in df.columns:
            tb = df["taker_buy"].tail(12).sum()
            tot = df["volume"].tail(12).sum() + 1e-9
            buy_ratio = tb / tot                          # 0.5 = balanced
            flow_edge = (buy_ratio - 0.5) * 2             # ±1
            conv += 0.35 * max(-1.0, min(1.0, flow_edge))
            conf += 0.10 * abs(flow_edge)
            notes.append(f"taker buy {buy_ratio:.0%} (12×15m)")

        # ── order-book imbalance (futures/spot w/ depth access) ──────────
        ctx = getattr(self, "_ctx", (snap.symbol, None, None))
        _, book, funding = ctx
        if book:
            mid = (float(book["bids"][0][0]) + float(book["asks"][0][0])) / 2
            bid_d = sum(float(b[0]) * float(b[1]) for b in book["bids"]
                        if float(b[0]) > mid * (1 - self.book_depth_frac))
            ask_d = sum(float(a[0]) * float(a[1]) for a in book["asks"]
                        if float(a[0]) < mid * (1 + self.book_depth_frac))
            imb = (bid_d - ask_d) / (bid_d + ask_d + 1e-9)
            conv += 0.30 * imb
            conf += 0.08 * abs(imb)
            notes.append(f"book {'+' if imb >= 0 else ''}{imb:.2f}")

        # ── funding crowding (futures) — fade extremes ───────────────────
        if funding is not None:
            fr = float(funding)
            if fr >= 0.0006:                              # ≥0.06% per interval
                conv -= 0.20; conf += 0.05
                notes.append(f"funding {fr:+.3%} crowded longs")
            elif fr <= -0.0006:
                conv += 0.20; conf += 0.05
                notes.append(f"funding {fr:+.3%} crowded shorts")

        return self._vote(self.name, snap, max(-0.85, min(0.85, conv)),
                          min(conf, 0.88), "; ".join(notes) or "flow balanced",
                          funding=funding)
