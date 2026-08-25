"""Flow analyst — realized aggressor flow from klines.

Measures WHO traded, not who is QUOTED: the taker-buy share of recent
volume. Book imbalance and funding crowding live in the dedicated
depth/positioning scouts so no microstructure input is double-counted.
Works on spot and futures alike.
"""
from __future__ import annotations

from .base import Analyst
from ..core.types import Snapshot, Vote


class FlowAnalyst(Analyst):
    name = "flow"
    regime_affinity = ("TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE")

    def evaluate(self, snap: Snapshot) -> Vote:
        df = snap.df("15m")
        if df is None or len(df) < 30 or "taker_buy" not in df.columns:
            return self._vote(self.name, snap, 0.0, 0.2, "no data")
        tb = df["taker_buy"].tail(12).sum()
        tot = df["volume"].tail(12).sum() + 1e-9
        buy_ratio = tb / tot                          # 0.5 = balanced
        flow_edge = max(-1.0, min(1.0, (buy_ratio - 0.5) * 2))
        conv = 0.45 * flow_edge
        conf = min(0.3 + 0.14 * abs(flow_edge), 0.85)
        note = f"taker buy {buy_ratio:.0%} (12×15m)"
        btc = snap.btc_ctx or {}
        if conv != 0.0 and abs(btc.get("ret_1h") or 0) > 0.01:
            aligned = (conv > 0) == (btc["ret_1h"] > 0)
            if not aligned:
                conv *= 0.7
                note += "; against BTC impulse"
        return self._vote(self.name, snap, conv, conf, note,
                          buy_ratio=round(buy_ratio, 3))
