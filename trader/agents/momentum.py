"""Momentum analyst — behavioral under/over-reaction cycles.

Measures trend QUALITY, not just direction: EMA alignment, ADX strength,
and whether recent movement is impulsive (worth joining) or corrective
(fade bait).
"""
from __future__ import annotations

from .base import Analyst
from .indicators import adx, anchored_vwap, ema, rsi, zscore
from ..core.types import Snapshot, Vote


class MomentumAnalyst(Analyst):
    name = "momentum"
    regime_affinity = ("TRENDING_UP", "TRENDING_DOWN")

    def evaluate(self, snap: Snapshot) -> Vote:
        df = snap.df("15m")
        if df is None or len(df) < 210:
            return self._vote(self.name, snap, 0.0, 0.2, "no data")
        c = df["close"]
        price = float(c.iloc[-1])
        e20, e50, e200 = float(ema(c, 20).iloc[-1]), float(ema(c, 50).iloc[-1]), float(ema(c, 200).iloc[-1])
        adx_v = adx(df)
        rsi_v = float(rsi(c).iloc[-1])
        conv, conf, notes = 0.0, 0.3, []

        # stack quality: full alignment beats partial
        if price > e20 > e50 > e200:
            conv += 0.35; conf += 0.12; notes.append("full bull stack")
        elif price > e20 > e50:
            conv += 0.20; notes.append("partial bull stack")
        elif price < e20 < e50 < e200:
            conv -= 0.35; conf += 0.12; notes.append("full bear stack")
        elif price < e20 < e50:
            conv -= 0.20; notes.append("partial bear stack")

        # ADX quality scaling — strong trend amplifies, weak discounts
        if adx_v >= 25:
            conf += 0.10
            conv *= 1.25 if abs(conv) > 0.05 else 1.0
            notes.append(f"ADX {adx_v:.0f} strong")
        elif adx_v < 18:
            conv *= 0.5
            notes.append(f"ADX {adx_v:.0f} weak")

        # RSI extremes inside trend = exhaustion warning against continuation
        if rsi_v > 78 and conv > 0:
            conv *= 0.6; notes.append(f"RSI {rsi_v:.0f} hot")
        elif rsi_v < 22 and conv < 0:
            conv *= 0.6; notes.append(f"RSI {rsi_v:.0f} washed")

        return self._vote(self.name, snap, conv, min(conf, 0.9),
                          "; ".join(notes) or "neutral", adx=round(adx_v, 1),
                          rsi=round(rsi_v, 1))


class ValueAnalyst(Analyst):
    """Stat-arb view: extremes vs anchored VWAP revert absent trend regime."""
    name = "value"
    regime_affinity = ("RANGING",)

    def evaluate(self, snap: Snapshot) -> Vote:
        df = snap.df("15m")
        if df is None or len(df) < 106:
            return self._vote(self.name, snap, 0.0, 0.2, "no data")
        vw = anchored_vwap(df, 96)
        dev_series = (df["close"] - vw) / vw
        z = zscore(dev_series, 96)
        if abs(z) < 1.8:
            return self._vote(self.name, snap, 0.0, 0.35,
                              f"near fair value (z={z:+.2f})")
        side = -1.0 if z > 0 else 1.0                      # fade the extreme
        conv = side * min(0.2 + 0.15 * (abs(z) - 1.8), 0.65)
        conf = min(0.3 + 0.08 * (abs(z) - 1.8), 0.7)
        return self._vote(self.name, snap, conv, conf,
                          f"{abs(z):.1f}σ {'above' if z > 0 else 'below'} VWAP anchor — fading",
                          zscore=round(z, 2))


class RotationAnalyst(Analyst):
    """Cross-asset cascade: BTC impulse drags lagging alts within hours."""
    name = "rotation"
    regime_affinity = ("TRENDING_UP", "TRENDING_DOWN")

    def evaluate(self, snap: Snapshot) -> Vote:
        btc = snap.dfs.get("BTC_1h")
        me = snap.df("1h")
        if btc is None or me is None or len(btc) < 24 or len(me) < 24:
            return self._vote(self.name, snap, 0.0, 0.2, "no cross data")
        btc_ret = float(btc["close"].iloc[-1] / btc["close"].iloc[-4] - 1)
        my_ret = float(me["close"].iloc[-1] / me["close"].iloc[-4] - 1)
        beta_note = ""
        if abs(btc_ret) < 0.005:
            return self._vote(self.name, snap, 0.0, 0.3,
                              f"BTC flat ({btc_ret:+.2%}) — no cascade driver")

        if my_ret * btc_ret >= 0 and abs(my_ret) < abs(btc_ret) * 0.7:
            # same direction, lagging → catch-up candidate
            conv = (0.4 if btc_ret > 0 else -0.4) * min(abs(btc_ret) / 0.01, 1.5)
            conf = 0.55
            beta_note = f"lagging BTC ({my_ret:+.2%} vs {btc_ret:+.2%})"
        elif my_ret * btc_ret < 0:
            # diverging hard from BTC leader — either alpha or trap; low conviction
            conv = 0.15 * (1 if my_ret > 0 else -1)
            conf = 0.35
            beta_note = f"diverging from BTC ({my_ret:+.2%} vs {btc_ret:+.2%})"
        else:
            return self._vote(self.name, snap, 0.0, 0.35,
                              "already led the move — no edge")
        sign = 1.0 if btc_ret > 0 else -1.0
        return self._vote(self.name, snap, max(-0.7, min(0.7, conv)), conf,
                          f"BTC 1h {btc_ret:+.2%}; {beta_note}",
                          btc_ret=round(btc_ret, 4))
