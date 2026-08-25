"""Positioning scout — derivatives crowding: funding + open interest.

Theory: perps funding is the rental price of leverage conviction. When
price grinds up, funding goes deeply positive AND open interest builds,
the marginal long is over-leveraged → squeeze fuel (fade). The mirror
image holds for crowded shorts. OI unwinding into a move means the move
is short-covering — it exhausts fast.

Contrarian at extremes, mildly confirmatory when balanced-but-building.
"""
from __future__ import annotations

from .base import Analyst
from ..core.types import Snapshot, Vote

CROWDED = 0.00045          # |funding| per interval that marks a crowd
EXTREME = 0.00100          # violent disagreement — strongest fade


class PositioningAnalyst(Analyst):
    name = "positioning"
    regime_affinity = ("TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE")

    def __init__(self, exchange=None):
        self.exchange = exchange
        self._ctx = (None, None, None)

    def set_context(self, symbol: str, funding_rate: float | None,
                    oi: dict | None) -> None:
        """Injected per-cycle by the kernel: funding + OI snapshot."""
        self._ctx = (symbol, funding_rate, oi)

    def evaluate(self, snap: Snapshot) -> Vote:
        _, funding, oi = self._ctx
        if funding is None and not oi:
            return self._vote(self.name, snap, 0.0, 0.25, "no positioning data")
        conv, conf, notes = 0.0, 0.3, []
        fr = float(funding) if funding is not None else 0.0

        oi_now = oi.get("now") if oi else None
        oi_chg = oi.get("chg_24h") if oi else None     # fractional, e.g. 0.08
        price_chg = 0.0
        df = snap.df("1h")
        if df is not None and len(df) >= 24:
            price_chg = float(df["close"].iloc[-1] / df["close"].iloc[-24] - 1)

        if fr >= EXTREME:
            conv -= 0.40; conf += 0.12
            notes.append(f"funding {fr:+.3%} extreme long crowding")
        elif fr <= -EXTREME:
            conv += 0.40; conf += 0.12
            notes.append(f"funding {fr:+.3%} extreme short crowding")
        elif fr >= CROWDED:
            base = -0.18 if price_chg > 0 else -0.10
            conv += base; conf += 0.06
            notes.append(f"funding {fr:+.3%} rich — paying to be long")
        elif fr <= -CROWDED:
            base = 0.18 if price_chg < 0 else 0.10
            conv += base; conf += 0.06
            notes.append(f"funding {fr:+.3%} rich — paying to be short")
        elif abs(fr) < 0.0001:
            conf += 0.03
            notes.append(f"funding neutral {fr:+.4%}")

        # OI confirmation / contradiction
        if oi_now is not None and oi_chg is not None:
            if oi_chg > 0.05 and abs(fr) >= CROWDED:
                conv *= 1.7                            # crowd still building into the extreme
                conf += 0.04
                notes.append(f"OI building +{oi_chg:+.0%} into the crowd")
            elif oi_chg > 0.05 and abs(fr) < CROWDED:
                with_dir = 1.0 if price_chg > 0 else (-1.0 if price_chg < 0 else 0.0)
                conv += 0.12 * with_dir; conf += 0.04
                notes.append(f"OI building +{oi_chg:+.0%} w/ fresh flow")
            elif oi_chg < -0.05 and abs(price_chg) > 0.02:
                against_move = -1.0 if price_chg > 0 else 1.0
                conv += 0.15 * against_move; conf += 0.04
                note = ("short-covering rally" if price_chg > 0
                        else "long liquidation flush")
                notes.append(f"OI −{abs(oi_chg):.0%} — {note}, weak hands driving")

        return self._vote(self.name, snap, max(-0.7, min(0.7, conv)),
                          min(conf, 0.85), "; ".join(notes) or "positioning quiet",
                          funding=funding, oi_chg=oi_chg)
