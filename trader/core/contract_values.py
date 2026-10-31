"""Contract units shared by research and venue-cashflow reconciliation.

Inverse PnL is native coin, not USD. A reporting mark is an explicit second
operation; this module supplies neither an FX observation nor order authority.
"""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Contract:
    kind: str = 'linear'
    multiplier: float = 1.0  # inverse: quote currency face value per contract

    def qty_for(self, notional_usd: float, price: float) -> float:
        return notional_usd / price if self.kind == 'linear' else float(np.floor(notional_usd / self.multiplier))

    def notional(self, qty: float, price: float) -> float:
        return abs(qty) * price if self.kind == 'linear' else abs(qty) * self.multiplier

    def pnl_native(self, qty: float, p0: float, p1: float) -> float:
        """Signed position quantity. Linear returns quote; inverse returns base."""
        if self.kind == 'linear':
            return qty * (p1 - p0)
        if qty == 0:
            return 0.
        return qty * self.multiplier * (1.0 / p0 - 1.0 / p1)

    def pnl_usd(self, qty: float, p0: float, p1: float) -> float:
        # Preserve the research model's original operation order exactly.
        if self.kind == 'linear':
            return qty * (p1 - p0)
        return qty * self.multiplier * (1.0 / p0 - 1.0 / p1) * p1

    def cashflow_native(self, qty: float, price: float, side: str) -> float:
        """Price-leg subtotal, excluding actual commissions and signed funding."""
        if side not in ('BUY', 'SELL'):
            raise ValueError('contract_cashflow_side_invalid')
        if qty == 0:
            return 0.
        if self.kind == 'linear':
            return qty * price * (1 if side == 'SELL' else -1)
        return qty * self.multiplier / price * (1 if side == 'BUY' else -1)

    def settlement_notional(self, qty: float, price: float) -> float:
        """Gross exposure expressed in the contract's own collateral currency."""
        return self.notional(qty, price) if self.kind == 'linear' else self.notional(qty, price) / price
