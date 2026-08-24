"""Structure analyst — Auction Market Theory / Wyckoff.

Reads the continuous two-way auction:
- liquidity sweeps: price pierces prior swing extreme then closes back
  inside → stops harvested, inventory transferred (spring/upthrust)
- BOS/CHoCH: break of structure = auction accepting new prices;
  change of character = auction failing to extend
- effort vs result: high volume with no progress = absorption
"""
from __future__ import annotations

import numpy as np

from .base import Analyst
from .indicators import swing_highs_lows
from ..core.types import Snapshot, Vote


class StructureAnalyst(Analyst):
    name = "structure"
    regime_affinity = ("TRENDING_UP", "TRENDING_DOWN", "RANGING")

    def evaluate(self, snap: Snapshot) -> Vote:
        df = snap.df("15m")
        if df is None or len(df) < 80:
            return self._vote(self.name, snap, 0.0, 0.2, "no data", )

        h, l, c, v = df["high"].values, df["low"].values, df["close"].values, df["volume"].values
        ph, pl = swing_highs_lows(df)
        conv, conf, notes = 0.0, 0.3, []

        # ── liquidity sweep (last 12 bars vs prior swings) ──────────────
        if len(ph) >= 2 and len(pl) >= 2:
            last_hi, prev_hi = float(h[ph[-1]]), float(h[ph[-2]])
            last_lo, prev_lo = float(l[pl[-1]]), float(l[pl[-2]])
            recent = slice(max(0, len(df) - 12), len(df))
            swept_high = float(np.max(h[recent])) > last_hi and c[-1] < last_hi
            swept_low = float(np.min(l[recent])) < last_lo and c[-1] > last_lo
            if swept_low:
                conv += 0.45; conf += 0.15
                notes.append(f"spring: swept low {last_lo:.4g} & reclaimed")
                # confluence: sweep of an OLD low is stronger than of the last
                if prev_lo > last_lo * 1.005:
                    conf += 0.05
            if swept_high:
                conv -= 0.45; conf += 0.15
                notes.append(f"upthrust: swept high {last_hi:.4g} & rejected")

        # ── break of structure / change of character ─────────────────────
        if len(ph) >= 2 and c[-1] > float(h[ph[-1]]) and c[-2] <= float(h[ph[-1]]):
            vol_ok = v[-6:].mean() > v.mean() * 1.15
            conv += 0.35 if vol_ok else 0.18
            conf += 0.10
            notes.append("BOS up" + (" (vol-confirmed)" if vol_ok else ""))
        if len(pl) >= 2 and c[-1] < float(l[pl[-1]]) and c[-2] >= float(l[pl[-1]]):
            vol_ok = v[-6:].mean() > v.mean() * 1.15
            conv -= 0.35 if vol_ok else 0.18
            conf += 0.10
            notes.append("BOS down" + (" (vol-confirmed)" if vol_ok else ""))

        # ── effort vs result (Wyckoff absorption) ────────────────────────
        if len(v) >= 30:
            e = v[-3:].mean() / (v[-30:-3].mean() + 1e-12)
            move = abs(c[-1] - c[-4]) / (c[-4] + 1e-12)
            if e > 1.8 and move < 0.0015:
                # heavy effort, no result → position against the last push
                push_up = c[-1] >= c[-4]
                conv += (-0.25 if push_up else 0.25)
                conf += 0.08
                notes.append(f"absorption ({'selling' if push_up else 'buying'}), effort={e:.1f}x")

        rationale = "; ".join(notes) if notes else "auction balanced, no edge"
        return self._vote(self.name, snap, conv, min(conf, 0.9),
                          rationale, swings_hi=len(ph), swings_lo=len(pl))
