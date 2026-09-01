"""Core shared types — the vocabulary of the Trading OS.

Design rule: agents VOTE, strategies SIGNAL, the orchestrator DECIDES,
the executor ACTS, and the journal RECORDS everything. Nothing vetoes;
risk limits are the only hard constraints.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class MarketType(str, Enum):
    SPOT = "spot"
    FUTURES = "futures"


class ExecMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class ControlState(str, Enum):
    """Operator-controlled autonomy states (REQUIREMENTS §3)."""
    ACTIVE = "ACTIVE"      # full autonomy
    FROZEN = "FROZEN"      # no NEW entries; open positions managed to natural close
    HALTED = "HALTED"      # nothing new; exits manual-only; exchange stops stay armed


class StrategyState(str, Enum):
    PROPOSED = "proposed"      # born from brain, awaiting backtest
    BACKTEST = "backtesting"
    PAPER = "paper"            # paper probation
    ACTIVE = "active"          # eligible to trade real (or live-paper) money
    DEMOTED = "demoted"        # statistically demoted, cooling off
    RETIRED = "retired"        # killed by brain or drawdown


@dataclass
class Vote:
    """An analyst agent's opinion. Never a veto — always a scored contribution."""
    agent: str
    symbol: str
    side: Side                 # LONG / SHORT / FLAT
    conviction: float          # -1.0..+1.0 signed strength of opinion
    confidence: float          # 0.0..1.0 self-assessed reliability
    rationale: str
    meta: dict = field(default_factory=dict)
    ts: str = field(default_factory=lambda: now_utc().isoformat())

    def as_dict(self) -> dict:
        return {
            "agent": self.agent, "symbol": self.symbol,
            "side": self.side.value, "conviction": round(self.conviction, 4),
            "confidence": round(self.confidence, 4),
            "rationale": self.rationale, "meta": self.meta, "ts": self.ts,
        }


@dataclass
class Snapshot:
    """Everything an agent/strategy may see for one symbol at one moment."""
    symbol: str
    ts: str
    price: float
    dfs: dict                  # timeframe -> pandas DataFrame (ohlcv)
    regime: str = "UNKNOWN"    # TRENDING_UP / TRENDING_DOWN / RANGING / VOLATILE
    adx: float = 0.0
    btc_trend: str = "NEUTRAL"
    macro_note: str = ""
    market_type: str = "futures"
    btc_ctx: dict = field(default_factory=dict)   # leader context (see regime.btc_context)
    universe: dict | None = None      # {symbol: {tf: df}} for cross-sectional
    derivs: dict | None = None        # {series: obs frame} for funding/OI/taker specs

    def df(self, tf: str):
        return self.dfs.get(tf)


@dataclass
class StrategySignal:
    """A strategy's evaluation of a snapshot."""
    strategy_id: str
    strategy_name: str
    symbol: str
    action: Action
    confidence: float          # 0..1
    rationale: str
    params: dict = field(default_factory=dict)


@dataclass
class Decision:
    """The orchestrator's final call for one symbol in one cycle."""
    id: str
    cycle_id: str
    symbol: str
    action: Action
    score: float               # net weighted conviction
    threshold: float
    confidence: float
    votes: list                # [Vote.as_dict()]
    strategy_signals: list     # [StrategySignal.as_dict()]
    executed: bool = False
    skip_reason: str = ""
    size_usdt: float = 0.0
    ts: str = field(default_factory=lambda: now_utc().isoformat())
    meta_p: float = 0.0         # meta-label P(win) — 0 = not judged
    meta_size: float = 1.0      # meta sizing multiplier (shrink-only ≤1)


@dataclass
class Strategy:
    """An evolving trading strategy — the unit the brain breeds and kills."""
    id: str
    name: str
    kind: str                  # seed family key, e.g. "ema_trend", "range_fade"
    params: dict
    state: StrategyState = StrategyState.PROPOSED
    description: str = ""
    origin: str = "seed"       # seed | brain | mutation
    created_at: str = field(default_factory=lambda: now_utc().isoformat())
    activated_at: str = ""
    retired_at: str = ""
    retire_reason: str = ""
    stats: dict = field(default_factory=lambda: {
        "trades": 0, "wins": 0, "losses": 0, "pnl_usdt": 0.0,
        "gross_win": 0.0, "gross_loss": 0.0, "max_dd_pct": 0.0,
        "consecutive_losses": 0,
    })

    @property
    def winrate(self) -> float:
        s = self.stats
        closed = s["wins"] + s["losses"]
        return s["wins"] / closed if closed else 0.0

    @property
    def profit_factor(self) -> float:
        s = self.stats
        if s["gross_loss"] == 0:
            return float("inf") if s["gross_win"] > 0 else 0.0
        return s["gross_win"] / s["gross_loss"]

    @property
    def is_trade_eligible(self) -> bool:
        return self.state in (StrategyState.PAPER, StrategyState.ACTIVE)

    def as_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "kind": self.kind,
            "params": self.params, "state": self.state.value,
            "description": self.description, "origin": self.origin,
            "created_at": self.created_at, "activated_at": self.activated_at,
            "retired_at": self.retired_at, "retire_reason": self.retire_reason,
            "stats": self.stats,
            "winrate": round(self.winrate, 3),
            "profit_factor": (round(self.profit_factor, 3)
                              if self.profit_factor != float("inf") else None),
        }


@dataclass
class Position:
    """An open trade (paper or live)."""
    id: str
    symbol: str
    side: Side                 # long/short
    amount: float              # base units
    entry_price: float
    notional_usdt: float
    leverage: int = 1
    stop_loss: float = 0.0
    take_profit: float = 0.0
    strategy_id: str = ""
    strategy_name: str = ""
    decision_id: str = ""
    market_type: str = "futures"
    exec_mode: str = "paper"
    opened_at: str = field(default_factory=lambda: now_utc().isoformat())
    confidence: float = 0.0
    sl_order_id: str = ""       # exchange-native stop order to cancel on close

    def unrealized_pnl(self, mark: float) -> float:
        # linear perps: P&L = price delta × base-asset size. Leverage scales
        # MARGIN, never absolute P&L (verified against exchange uPnL).
        direction = 1.0 if self.side == Side.LONG else -1.0
        return (mark - self.entry_price) * direction * self.amount

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["side"] = self.side.value
        return d


@dataclass
class ClosedTrade(Position):
    exit_price: float = 0.0
    realized_pnl: float = 0.0
    close_reason: str = ""     # sl / tp / trail / manual / brain
    closed_at: str = ""

    def finalize(self) -> "ClosedTrade":
        direction = 1.0 if self.side == Side.LONG else -1.0
        self.realized_pnl = ((self.exit_price - self.entry_price) * direction
                             * self.amount)
        return self


class RiskError(Exception):
    pass


def norm_symbol(sym: str) -> str:
    """'BTC/USDT:USDT' -> 'BTC/USDT' (ccxt linear-perp suffix)."""
    return sym.split(":")[0] if sym else sym
