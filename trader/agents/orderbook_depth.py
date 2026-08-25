"""Depth scout — live order-book microstructure.

Reads resting liquidity, not trades: imbalance of size within ±1% of mid
(who is defending), and walls — single levels ≥5× the median level that
act as short-term support/resistance. Confidence scales with spread
tightness (a wide book is an unreliable read).

This is a fast, decaying signal: it informs entries, never holds a
position. Kept deliberately stateless.
"""
from __future__ import annotations

import math

from .base import Analyst
from ..core.types import Snapshot, Vote


class DepthScout(Analyst):
    name = "depth"
    regime_affinity = ("TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE")

    def __init__(self, band_frac: float = 0.01):
        self.band_frac = band_frac
        self._ctx = (None, None)

    def set_context(self, symbol: str, book: dict | None) -> None:
        self._ctx = (symbol, book)

    def evaluate(self, snap: Snapshot) -> Vote:
        _, book = self._ctx
        if not book or not book.get("bids") or not book.get("asks"):
            return self._vote(self.name, snap, 0.0, 0.25, "no book data")
        bids = [(float(p), float(q)) for p, q in book["bids"]]
        asks = [(float(p), float(q)) for p, q in book["asks"]]
        mid = (bids[0][0] + asks[0][0]) / 2
        spread = (asks[0][0] - bids[0][0]) / mid

        lo, hi = mid * (1 - self.band_frac), mid * (1 + self.band_frac)
        bid_d = sum(p * q for p, q in bids if p >= lo)
        ask_d = sum(p * q for p, q in asks if p <= hi)
        if bid_d + ask_d <= 0:
            return self._vote(self.name, snap, 0.0, 0.25, "empty band")
        imb = (bid_d - ask_d) / (bid_d + ask_d)          # -1..+1

        conv, conf, notes = 0.45 * math.tanh(1.8 * imb), 0.30 + 0.25 * abs(imb), []
        notes.append(f"band imbalance {imb:+.2f}")

        # wall detection: level ≥5× median level in its side's top-10
        for side, levels in (("bid", bids[:10]), ("ask", asks[:10])):
            if len(levels) < 4:
                continue
            sizes = [q for _p, q in levels]
            med = sorted(sizes)[len(sizes) // 2] + 1e-12
            wp, wq = max(levels, key=lambda x: x[1])
            if wq >= 5 * med:
                wall_dir = 1.0 if side == "bid" else -1.0
                conv += 0.15 * wall_dir
                conf += 0.05
                notes.append(f"{side} wall {wq:.0f} @ {wp:.4g}")

        if spread > 0.0012:
            conf *= 0.6                                   # thin book, trust less
            notes.append(f"wide spread {spread:.3%}")
        return self._vote(self.name, snap, max(-0.65, min(0.65, conv)),
                          min(conf, 0.85), "; ".join(notes),
                          spread=round(spread, 6))
