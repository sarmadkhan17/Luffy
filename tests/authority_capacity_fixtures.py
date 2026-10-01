"""Stage-5 strategy capacity receipt (strategy-capacity-receipt.v1).

Every test runs on a temporary journal built through the real factory path
(referee candidate -> version -> exact paper install -> probation -> owner
approval). The account / Risk records are written in the exact shape the
Kernel publishes them (`_record_account_observation`,
`_record_risk_assessment`) and read back through `dashboard.current_truth`.
"""
import copy
import json
import math
import pathlib
import re
from datetime import datetime, timezone

import pytest

from trader.core.journal import Journal
from trader.core.types import ControlState, MarketType, Position, Side
from trader.data.binance_usdm_registry import from_binance_usdm_responses
from trader.engine.risk import RiskManager, policy_from_config
from trader.observability.portfolio_observation import observe_positions
from trader.strategy import capacity as C
from trader.strategy import factory_handoff as F
from tests.authority_factory_fixtures import INPUTS, _approved, _journal, cfg

H4 = 14_400_000
NOW = 1_794_000_000_000 - (1_794_000_000_000 % H4) + 3_600_000  # 1h into a bar
BTC = "binance_usdm:futures:BTCUSDT"


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


# ── fixtures in the Kernel's own record shapes ───────────────────────────
def _account_obs(equity, observed_ms):
    return {"schema": 2, "currency": "USDT", "attempted_at": _iso(observed_ms - 200),
            "recorded_at": _iso(observed_ms + 100), "risk_input": equity,
            "authoritative": True, "attempt_errors": [],
            "source": "kernel _risk_step (Risk's equity input this cycle)",
            "status": "FRESH", "value": equity,
            "basis": "venue_total_margin_balance",
            "observed_at": _iso(observed_ms), "successful_read_at": _iso(observed_ms),
            "successful_value": equity, "successful_basis": "venue_total_margin_balance",
            "fallback": None, "consecutive_failures": 0,
            "completion_relation": "ok", "reason": None}


def _risk_assessment(cfg, equity, observed_ms, *, peak=None, day_start=None,
                     status="PASS", blocking=(), policy=None):
    pol = policy or policy_from_config(cfg)
    peak = peak or equity
    day_start = day_start or equity
    dd = round(max(0.0, (peak - equity) / peak) * 100, 2)
    day = round((equity - day_start) / day_start * 100, 2)
    cons = []
    for name in ("risk_baseline", "halt_drawdown", "daily_loss_breaker",
                 "max_open_positions", "portfolio_heat", "total_margin"):
        res = "block" if name in blocking else "pass"
        cons.append({"name": name, "limit": 1.0 if name != "risk_baseline" else "ok",
                     "observed": 0.0 if name != "risk_baseline" else "ok",
                     "result": res, "unit": "x", "basis": "fixture"})
    for name in ("per_symbol_risk_cap", "per_position_margin"):
        cons.append({"name": name, "limit": 1.0, "observed": None,
                     "result": "applies_at_entry", "unit": "pct", "basis": "fixture"})
    return {"schema": 2, "assessed_at": _iso(observed_ms + 500), "status": status,
            "reasons": [f"{b}_block" for b in blocking], "constraints": cons,
            "risk_state": "ok", "drawdown_pct": dd, "daily_pnl_pct": day,
            "halt_breached": False,
            "baseline": {"peak_equity": peak, "day_start_equity": day_start,
                         "day_key": _iso(observed_ms)[:10]},
            "equity": {"value": equity, "status": "FRESH",
                       "basis": "venue_total_margin_balance",
                       "observed_at": _iso(observed_ms), "age_at_assessment_s": 0.5,
                       "fresh_at_assessment": True, "authoritative": True},
            "book": {"source": "journal open trades", "read_at": _iso(observed_ms),
                     "positions": 0, "malformed": []},
            "policy": {"digest": pol["digest"], "risk_manager_identity": "x",
                       "effective": pol["effective"], "limits": pol["limits"]},
            "control": {"state": "ACTIVE", "entries_permitted_by_control": True,
                        "applicability": "entries"},
            "entry_gate": {"allowed": True, "blocked_reason": None, "basis": "x"},
            "source": "fixture"}


def _publish(j, cfg, equity=10_000.0, observed_ms=NOW - 30_000, *, account=True,
             **kw):
    if account:
        j.kv_set("account_observation",
                 json.dumps(_account_obs(equity, observed_ms)))
    ra = _risk_assessment(cfg, equity, observed_ms, **kw)
    j.kv_set("risk_assessment", json.dumps(ra))
    j.kv_set("risk_state", json.dumps(ra["baseline"]))


def _open(j, tid, symbol, side, amount, entry, stop, lev=5):
    with j._tx() as c:
        c.execute("INSERT INTO trades (id, symbol, side, amount, entry_price, "
                  "notional_usdt, leverage, stop_loss, strategy_id, market_type, "
                  "exec_mode, opened_at, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,"
                  "'open')", (tid, symbol, side, amount, entry, amount * entry, lev,
                              stop, "other", "futures", "live", _iso(NOW - H4)))


def _positions(rows, as_of_ms=NOW - 10_000):
    return observe_positions(
        [{"info": {"symbol": s.replace("/", "")}, "symbol": f"{s}:USDT",
          "contracts": q, "side": side} for s, side, q in rows],
        exchange_id="binanceusdm", market_type=MarketType.FUTURES,
        environment="demo", source_ref="https://demo-fapi.binance.com",
        request_start_ms=as_of_ms - 50, response_received_ms=as_of_ms)


def _registry(as_of_ms=NOW - 60_000, status="TRADING"):
    info = {"symbols": [{
        "symbol": "BTCUSDT", "status": status, "baseAsset": "BTC",
        "quoteAsset": "USDT", "marginAsset": "USDT", "contractType": "PERPETUAL",
        "pricePrecision": 1, "quantityPrecision": 3,
        "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001",
                     "minQty": "0.001", "maxQty": "1000"},
                    {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001",
                     "minQty": "0.001", "maxQty": "120"},
                    {"filterType": "MIN_NOTIONAL", "notional": "100"}]}]}
    return from_binance_usdm_responses(
        exchange_info=info, as_of_ms=as_of_ms,
        account_scope="unauthenticated-public-metadata")


def _market(n=30, now=NOW, price=60_000.0, rng=600.0):
    last = now - (now % H4) - H4                 # the last fully closed bar
    bars = []
    for i in range(n):
        ts = last - (n - 1 - i) * H4
        c = price + (i % 5) * 10.0
        bars.append({"ts": ts, "high": c + rng / 2, "low": c - rng / 2, "close": c})
    return {"timeframe": "4h", "source": "candles.db ohlcv (fixture)", "bars": bars}


@pytest.fixture
def world(tmp_path, cfg):
    j, _h = _journal(tmp_path)
    v, _p, _d = _approved(j, cfg)
    return j, F.load_version(j, v["version_id"])


def _inputs(j, cfg, version, **kw):
    args = dict(instrument_id=BTC, market_type="futures", as_of_ms=NOW,
                registry=_registry(), positions=_positions([]), market=_market())
    args.update(kw)
    return C.gather(j, cfg, version, **args)
