"""QNT-01 — the explicit quantitative contract behind every backtest number.

Pure and additive: nothing here changes a fill, a cost or a verdict. It states
what `vector_backtest._trade` / `simulate` compute, and binds a result to the
exact data, config and engine code that produced it so a number can be
re-derived rather than trusted.

Every cost below is an ESTIMATE from a model. Backtest costs are never venue
actuals; journal/venue figures carry their own labels elsewhere.
"""
from __future__ import annotations

import hashlib
import inspect
import json

import numpy as np

CONTRACT_ID = "quant-contract.v1"

COST_BASIS = "ESTIMATED_MODEL"          # never "ACTUAL"

DEFINITIONS = {
    "entry": "at the CLOSE of the signal bar plus slippage; the signal uses only bars <= that bar",
    "label": "exit is decided by bars strictly after entry only (stop before target when both touch); "
             "an open position at dataset end is censored, not scored",
    "slippage": "slippage_atr_frac x ATR(signal bar), paid on entry and again on exit",
    "fees": "taker_fee_pct/100 x (entry price + exit price) per unit; both sides",
    "funding": "signed real 8h series where known (long pays, short is paid); abs(funding_rate_8h) flat "
               "charge on bars without a settlement; scaled by bars_held x bar_minutes/480",
    "unit_pnl": "(exit - entry) x side - fees - funding x exit price, per unit of size",
    "return": "R multiple = net pnl / (equity at entry x risk_per_trade_pct); costs are inside it",
    "expectancy": "mean net pnl per scored trade (usdt) and mean R; zero trades is None, not zero",
    "profit_factor": "gross winning pnl / gross losing pnl, net of the costs above",
    "max_drawdown": "peak-to-trough of CLOSED-trade equity, percent of peak, peak starting at equity; "
                    "open-trade mark-to-market is not included",
    "cost_basis": COST_BASIS,
}

#: risk_cfg keys that change a simulated number
_CFG_KEYS = ("taker_fee_pct", "slippage_atr_frac", "risk_per_trade_pct", "funding_rate_8h",
             "bar_minutes", "real_funding", "stop_loss_atr_mult", "take_profit_atr_mult")


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()


def cost_model(risk_cfg: dict) -> dict:
    """The cost assumptions in force, defaults included, labelled as estimates."""
    return {"basis": COST_BASIS,
            "taker_fee_pct": float(risk_cfg.get("taker_fee_pct", 0.05)),
            "slippage_atr_frac": float(risk_cfg.get("slippage_atr_frac", 0.06)),
            "funding_rate_8h_flat_fallback": float(risk_cfg.get("funding_rate_8h", 0.0001)),
            "real_funding": bool(risk_cfg.get("real_funding", True))}


def expectancy(res) -> dict:
    """Expectancy of a BacktestResult under DEFINITIONS['expectancy']."""
    n = res.trades
    return {"trades": n, "usdt_per_trade": (res.pnl_usdt / n) if n else None}


def data_digest(df) -> str:
    """Content hash of the OHLCV the engine reads (timestamps and prices)."""
    h = hashlib.sha256()
    for c in ("ts", "open", "high", "low", "close", "volume"):
        if c in df:
            col = df[c]
            h.update(c.encode())
            h.update(np.ascontiguousarray(
                col.astype("int64").to_numpy() if str(col.dtype).startswith("datetime")
                else col.to_numpy(float)).tobytes())
    return h.hexdigest()


def engine_digest() -> str:
    """Hash of the engine source that defines a trade, so a code change is visible."""
    from . import vector_backtest as vb
    src = "".join(inspect.getsource(f) for f in (vb._trade, vb.simulate, vb.walk_table, vb.trade_table))
    return hashlib.sha256(src.encode()).hexdigest()


def run_identity(spec, frames: dict, risk_cfg: dict) -> dict:
    """Everything a result depends on, pinned: same identity => same numbers."""
    data = {k: data_digest(v) for k, v in sorted(frames.items()) if v is not None and hasattr(v, "columns")}
    cfg = {k: risk_cfg.get(k) for k in _CFG_KEYS}
    ident = {"contract": CONTRACT_ID, "spec_id": getattr(spec, "id", None),
             "spec_digest": _digest(spec.to_dict() if hasattr(spec, "to_dict") else repr(spec)),
             "data_digest": _digest(data), "config": cfg, "config_digest": _digest(cfg),
             "engine_digest": engine_digest()}
    ident["identity"] = _digest({k: ident[k] for k in ("contract", "spec_digest", "data_digest",
                                                       "config_digest", "engine_digest")})
    return ident


def contract(spec, frames: dict, risk_cfg: dict) -> dict:
    return {"id": CONTRACT_ID, "definitions": DEFINITIONS, "cost_model": cost_model(risk_cfg),
            "run": run_identity(spec, frames, risk_cfg)}
