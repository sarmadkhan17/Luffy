"""Stage-5 strategy capacity: one versioned, replayable capacity receipt.

"How much real exposure can this exact immutable strategy version safely
request RIGHT NOW?" — answered only from sources that already exist, each
kept separate, never substituted for another:

- RISK       the owner Risk rule: `RiskManager.check_entry` itself, run on a
             database-less RiskManager whose effective policy digest must
             equal the running policy the kernel recorded
             (`state_kv.risk_assessment`, read by `current_truth.read_risk`)
             and the configured one. No sizing arithmetic is re-typed here.
- ACCOUNT    equity from `state_kv.account_observation` via
             `current_truth.read_account` (FRESH / VENUE_FALLBACK and
             authoritative only). Available margin is not recorded anywhere,
             so "the account can fund it" is UNAVAILABLE.
- PORTFOLIO  the journal open-trade book Risk sizes against, used only when a
             complete, fresh venue position observation
             (`portfolio.observation.v1`) confirms it instrument by
             instrument under reconcile's 1% size rule. Unconfirmed = UNKNOWN.
- STRATEGY   the exact version's own stop geometry (`SpecExit.from_spec`):
             ATR multiple on the spec's declared timeframe, or a percent stop;
             ATR over fully closed bars with `indicators.atr`, the 0.4% venue
             noise floor the Kernel applies. No stop declared = UNAVAILABLE
             (the Kernel's config fallback is not this version's geometry).
             Price basis: close of the last closed spec-timeframe bar (the
             backtest close-fill basis) — not an achievable live fill.
- VENUE      the instrument registry record: status, minimum quantity, step,
             minimum notional. Venue maxima (MARKET_LOT_SIZE / max notional),
             leverage brackets and per-symbol account eligibility are not
             captured by the registry, so those sub-bounds are UNAVAILABLE.
- LIQUIDITY  no registered liquidity / market-impact capacity model exists.
             Volume, a backtest slippage charge or a slippage reference are
             evidence, not a model: supplied evidence is recorded and ignored.

Effective capacity is ESTABLISHED only when every mandatory dimension is
(SDD v3.2 §14.1 "capacity estimate", §16.3 spread/depth/volume/impact/
slippage/venue limits). Otherwise it is UNAVAILABLE with no number: known
bounds are recorded per dimension, and no conservative figure is invented.

A receipt is immutable and replayable: `inputs` holds every observed value
(the raw state_kv records, the book, the venue observations, the bars) and
`result` is `compute(inputs)`, which re-runs the same readers and the same
Risk call. `check_current` decides whether a stored receipt still describes
now; anything superseded, stale or unprovable fails closed.

Boundaries: this module writes only `strategy_capacity_receipts`, places no
order, changes no Risk limit, leverage or control state, and is not imported
by the Kernel or the engine. Capacity grants nothing; it is one input to the
first-live eligibility predicate.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from enum import Enum
from types import SimpleNamespace

from ..core.types import TF_MS

SCHEMA = "strategy-capacity-receipt.v1"
TABLE = "strategy_capacity_receipts"

# the reference a StrategyVersion / approval request carries instead of a
# frozen number: capacity is re-evaluated from current conditions
CONTRACT_REF = {"status": "EVALUATED_AT_USE", "contract": SCHEMA,
                "binding": "capacity is a current strategy-capacity-receipt.v1 "
                           "for this exact version; neither the version nor its "
                           "approval freezes a capacity number"}

ESTABLISHED, PARTIAL, UNAVAILABLE = "ESTABLISHED", "PARTIAL", "UNAVAILABLE"

# every mandatory sub-dimension, by group; effective needs all ESTABLISHED
MANDATORY = {
    "risk": ("risk_rule",),
    "account": ("equity", "funding"),
    "portfolio": ("venue_confirmed_book",),
    "strategy": ("stop_geometry",),
    "venue": ("instrument_status", "minimums", "maximums", "leverage",
              "account_eligibility"),
    "liquidity": ("liquidity_capacity_model",),
}

# liquidity/market-impact capacity models with a registered authority. None
# exists; a model is added here only with its exact authority and tests.
REGISTERED_LIQUIDITY_MODELS: dict = {}
NO_LIQUIDITY_MODEL = "NO_REGISTERED_LIQUIDITY_CAPACITY_MODEL"
LIQUIDITY_MISSING = (
    "a registered size -> expected market impact / executable-size model per "
    "instrument (SDD §16.3: spread, depth, volume, expected impact, turnover, "
    "historical slippage) with its own authority and validation",
    "current order-book depth observations persisted with source time",
    "measured slippage vs order size on real fills, per instrument, recorded "
    "as a queryable artifact (the 2026-09-02 book-walk figures exist only as "
    "a config.yaml comment)")
NON_MODEL_EVIDENCE = (
    "config.yaml risk.slippage_atr_frac: a backtest cost charge, not a size bound",
    "trade provenance entry reference price: per-trade slippage basis, no "
    "size -> impact relation",
    "agents.orderbook_depth DepthScout: a directional signal, not capacity",
    "exchange 24h volume: participation rates are not registered policy")

ATR_PERIOD = 14                 # indicators.atr default, used by the Kernel
BARS_KEPT = ATR_PERIOD + 1      # the last TR needs the previous close
STOP_FLOOR_FRAC = 0.004         # Kernel._protection_for venue noise floor
RECONCILE_REL_TOL = 0.01        # reconcile.py: max(contracts * 0.01, 1e-9)


def _bounds() -> dict:
    """Source staleness bounds this receipt applies — all pre-existing."""
    from ..dashboard import current_truth as ct
    from ..engine import protection_snapshot as ps
    return {"account_stale_s": ct.ACCOUNT_STALE_S,
            "risk_assessment_stale_s": ct.RISK_STALE_S,
            "venue_positions_stale_s": ps.STALE_AFTER_S,
            "reconcile_rel_tol": RECONCILE_REL_TOL,
            "stop_floor_frac": STOP_FLOOR_FRAC, "atr_period": ATR_PERIOD,
            "sources": {
                "account_stale_s": "dashboard.current_truth.ACCOUNT_STALE_S",
                "risk_assessment_stale_s": "dashboard.current_truth.RISK_STALE_S",
                "venue_positions_stale_s":
                    "engine.protection_snapshot.STALE_AFTER_S (venue position "
                    "observation freshness)",
                "reconcile_rel_tol": "engine.reconcile size tolerance",
                "stop_floor_frac": "Kernel._protection_for venue noise floor",
                "atr_period": "agents.indicators.atr default"}}


class CapacityRefused(ValueError):
    """The request is refused; `.code` is the stable reason code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _refuse(code: str):
    raise CapacityRefused(code)


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _jsha(obj) -> str:
    return _sha(canonical(obj))


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def _finite_pos(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) and f > 0 else None


def _dim(status: str, reason: str | None = None, **kw) -> dict:
    return {"status": status, "reason": reason, **kw}


def _plain(obj):
    """dataclass/enum tree -> JSON values."""
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


# ── readers over recorded raw state (live journal or stored receipt) ────
class _KV:
    """The two current-truth readers need `kv_get` (and `query` only for the
    legacy equity-table fallback). Replay feeds them the stored raw records;
    the fallback is never current, so it is answered empty in both paths."""

    def __init__(self, kv: dict):
        self._kv = kv

    def kv_get(self, key, default=None):
        v = self._kv.get(key)
        return default if v is None else v

    def query(self, sql, args=()):
        return []


KV_KEYS = ("account_observation", "risk_assessment", "risk_state")


def _account(raw_kv: dict, at_ms: int) -> dict:
    from ..dashboard.current_truth import read_account
    value, err = read_account(_KV(raw_kv), _dt(at_ms))
    if value is None:
        return _dim(UNAVAILABLE, "ACCOUNT_EQUITY_" + (
            "MISSING" if err in (None, "no_equity_records") else "MALFORMED"),
            detail=err)
    eq = _finite_pos(value.get("equity"))
    if value["status"] not in ("FRESH", "VENUE_FALLBACK") \
            or value.get("authoritative") is not True or eq is None:
        return _dim(UNAVAILABLE, f"ACCOUNT_EQUITY_{value['status']}",
                    detail=value.get("reasons"))
    return _dim(ESTABLISHED, None, equity_usdt=eq, basis=value.get("basis"),
                source_status=value["status"],
                observed_at=value.get("observed_at"),
                source="state_kv.account_observation via "
                       "current_truth.read_account")


def _risk_record(raw_kv: dict, at_ms: int, policy_effective: dict) -> dict:
    """The kernel's recorded Risk assessment, judged current at `at_ms`, and
    whether its running policy is the configured one."""
    from ..dashboard.current_truth import read_risk
    out, err = read_risk(_KV(raw_kv), _dt(at_ms), None)
    cur = out.get("current") or {}
    running = (cur.get("running_policy") or {}).get("digest")
    configured = _jsha(policy_effective)
    base = {"status_now": cur.get("status"), "reasons": cur.get("reasons"),
            "risk_state": cur.get("risk_state"),
            "drawdown_pct": cur.get("drawdown_pct"),
            "daily_pnl_pct": cur.get("daily_pnl_pct"),
            "running_policy_digest": running,
            "configured_policy_digest": configured,
            "control_state": (cur.get("control") or {}).get("state"),
            "equity_value": (cur.get("equity") or {}).get("value"),
            "blocking": sorted(c["name"] for c in cur.get("constraints") or []
                               if c.get("result") == "block")}
    if err is not None or cur.get("status") not in ("PASS", "BLOCK"):
        return {**base, "usable": False,
                "why": "RISK_ASSESSMENT_" + str(cur.get("status") or "MISSING")}
    if running != configured:
        return {**base, "usable": False, "why": "RISK_POLICY_NOT_PROVEN_RUNNING"}
    if cur.get("risk_state") != "ok":
        return {**base, "usable": False, "why": "RISK_BASELINE_NOT_OK"}
    return {**base, "usable": True, "why": None}


# ── the portfolio: journal book confirmed by the venue ──────────────────
def _venue_symbol(sym: str) -> str:
    return sym.split(":")[0].replace("/", "")


def _positions_payload(obs: dict) -> dict:
    return {"schema": obs["schema"], "venue": obs["venue"],
            "source": obs["source"], "environment": obs["environment"],
            "source_ref": obs["source_ref"], "market_type": obs["market_type"],
            "request_start_ms": obs["request_start_ms"],
            "response_received_ms": obs["response_received_ms"],
            "as_of_ms": obs["as_of_ms"], "complete": obs["complete"],
            "positions": obs["positions"]}


def _portfolio(book: list, venue: dict | None, as_of_ms: int,
               bounds: dict) -> dict:
    if venue is None:
        return _dim(UNAVAILABLE, "VENUE_POSITIONS_NOT_OBSERVED")
    if _jsha(_positions_payload(venue)) != venue.get("observation_id"):
        return _dim(UNAVAILABLE, "VENUE_POSITIONS_OBSERVATION_CORRUPT")
    if venue.get("complete") is not True:
        return _dim(UNAVAILABLE, "VENUE_POSITIONS_INCOMPLETE")
    age = as_of_ms - int(venue["as_of_ms"])
    if age < 0:
        return _dim(UNAVAILABLE, "VENUE_POSITIONS_AFTER_AS_OF")
    if age > bounds["venue_positions_stale_s"] * 1000:
        return _dim(UNAVAILABLE, "VENUE_POSITIONS_STALE", age_ms=age)
    held = {}
    for iid, present, side, qty in venue["positions"]:
        if present:
            held[iid.split(":")[-1]] = (side, float(qty))
    journal = {}
    for t in book:
        if t["market_type"] not in (None, "futures"):
            return _dim(UNAVAILABLE, "BOOK_HAS_UNOBSERVED_MARKET",
                        trade_id=t["id"])
        vals = [_finite_pos(t.get(k)) for k in ("amount", "entry_price",
                                                "notional_usdt")]
        stop = t.get("stop_loss")
        if None in vals or (stop is not None and (
                isinstance(stop, bool) or not isinstance(stop, (int, float))
                or not math.isfinite(stop) or stop < 0)):
            return _dim(UNAVAILABLE, "BOOK_MALFORMED", trade_id=t["id"])
        vs = _venue_symbol(t["symbol"])
        side, qty = journal.get(vs, (t["side"], 0.0))
        if side != t["side"]:
            return _dim(UNAVAILABLE, "BOOK_SIDES_CONFLICT", symbol=t["symbol"])
        journal[vs] = (side, qty + float(t["amount"]))
    if set(journal) != set(held):
        return _dim(UNAVAILABLE, "JOURNAL_VENUE_BOOK_DISAGREES",
                    journal_only=sorted(set(journal) - set(held)),
                    venue_only=sorted(set(held) - set(journal)))
    tol = bounds["reconcile_rel_tol"]
    for vs, (side, qty) in journal.items():
        vside, vqty = held[vs]
        if side != vside or abs(qty - vqty) > max(vqty * tol, 1e-9):
            return _dim(UNAVAILABLE, "JOURNAL_VENUE_BOOK_DISAGREES", symbol=vs)
    return _dim(ESTABLISHED, None, open_positions=len(book),
                venue_observation_id=venue["observation_id"],
                venue_environment=venue["environment"], age_ms=age,
                source="journal open trades (Risk's book) confirmed by "
                       "portfolio.observation.v1")


# ── the strategy's own stop geometry ────────────────────────────────────
def _bars_frame(bars: list):
    import pandas as pd
    return pd.DataFrame(bars, columns=["ts", "high", "low", "close"])


def _strategy(spec: dict, market: dict | None, as_of_ms: int,
              bounds: dict) -> dict:
    from .spec import StrategySpec
    from ..engine.exits import SpecExit
    se = SpecExit.from_spec(StrategySpec.from_dict(spec))
    geo = {"timeframe": se.timeframe, "stop_atr_mult": se.stop_atr_mult,
           "stop_pct": se.stop_pct}
    if se.stop_atr_mult <= 0 and se.stop_pct <= 0:
        return _dim(UNAVAILABLE, "SPEC_DECLARES_NO_STOP_GEOMETRY", geometry=geo)
    if market is None:
        return _dim(UNAVAILABLE, "MARKET_BARS_NOT_SUPPLIED", geometry=geo)
    tf_ms = TF_MS.get(se.timeframe)
    bars = market.get("bars") or []
    if market.get("timeframe") != se.timeframe or not tf_ms:
        return _dim(UNAVAILABLE, "MARKET_BARS_WRONG_TIMEFRAME", geometry=geo)
    if len(bars) < BARS_KEPT:
        return _dim(UNAVAILABLE, "MARKET_BARS_INSUFFICIENT", geometry=geo)
    for i, b in enumerate(bars):
        if any(_finite_pos(b.get(k)) is None for k in ("high", "low", "close")) \
                or not isinstance(b.get("ts"), int) \
                or (i and b["ts"] - bars[i - 1]["ts"] != tf_ms):
            return _dim(UNAVAILABLE, "MARKET_BARS_MALFORMED", geometry=geo)
    last = bars[-1]["ts"]
    if last + tf_ms > as_of_ms:
        return _dim(UNAVAILABLE, "MARKET_BAR_NOT_CLOSED", geometry=geo)
    if last + 2 * tf_ms <= as_of_ms:
        return _dim(UNAVAILABLE, "MARKET_BARS_NOT_LATEST_CLOSED", geometry=geo)
    price = float(bars[-1]["close"])
    if se.stop_atr_mult > 0:
        from ..agents.indicators import atr
        a = atr(_bars_frame(bars), bounds["atr_period"])
        if not math.isfinite(a) or a <= 0:
            return _dim(UNAVAILABLE, "STOP_ATR_UNAVAILABLE", geometry=geo)
        dist, basis = se.stop_atr_mult * a, "atr"
    else:
        a, dist, basis = None, se.stop_pct * price, "pct"
    dist = max(dist, price * bounds["stop_floor_frac"])
    return _dim(ESTABLISHED, None, geometry=geo, stop_basis=basis, atr=a,
                price=price, stop_distance=dist, stop_frac=dist / price,
                price_basis="close of the last closed spec-timeframe bar "
                            "(backtest close-fill basis; not a live fill)",
                last_bar_open_ms=last)


# ── the owner Risk rule, exactly ────────────────────────────────────────
def _risk_bound(policy: dict, risk: dict, account: dict, portfolio: dict,
                strategy: dict, book: list, symbol: str,
                closed_trades: int) -> dict:
    """`RiskManager.check_entry` on a database-less RiskManager whose
    effective policy digest must equal the recorded running policy."""
    missing = [n for n, d in (("account", account), ("portfolio", portfolio),
                              ("strategy", strategy)) if d["status"] != ESTABLISHED]
    if not risk["usable"]:
        missing.insert(0, risk["why"])
    if missing:
        return _dim(UNAVAILABLE, "RISK_INPUTS_UNAVAILABLE", missing=missing)
    equity = account["equity_usdt"]
    if risk["equity_value"] != equity:
        return _dim(UNAVAILABLE, "RISK_ACCOUNT_INPUTS_DISAGREE")
    from ..core.types import ControlState, Position, RiskError, Side
    from ..engine.risk import RiskManager
    rm = RiskManager({"risk": {
        "risk_per_trade_pct": 1, "portfolio_heat_cap_pct": 1,
        "per_symbol_risk_cap_pct": 1, "max_open_positions": 1,
        "max_daily_loss_pct": 1, "halt_drawdown_pct": 1, "leverage": 1,
        "stop_loss_atr_mult": 1, "min_notional_usdt": 1}}, None)
    e = policy["effective"]
    for k in ("risk_pct", "heat_cap", "symbol_cap", "max_positions",
              "daily_loss_block", "halt_dd", "leverage", "sl_atr_mult",
              "min_notional", "max_pos_margin", "max_total_margin",
              "proving_trades", "proving_mult", "taker_fee"):
        setattr(rm, k, e[k])
    rm.derisk_steps = [tuple(s) for s in e["derisk_steps"]]
    if rm.policy()["digest"] != risk["running_policy_digest"]:
        return _dim(UNAVAILABLE, "RISK_POLICY_NOT_REPRODUCIBLE")
    dd, day = risk["drawdown_pct"], risk["daily_pnl_pct"]
    rm.update_equity = lambda _eq, **_k: {
        "equity": equity, "drawdown_pct": dd, "daily_pnl_pct": day,
        "halt_breached": dd is not None and dd >= e["halt_dd"] * 100,
        "risk_state": "ok"}
    positions = [Position(id=t["id"], symbol=t["symbol"], side=Side(t["side"]),
                          amount=float(t["amount"]),
                          entry_price=float(t["entry_price"]),
                          notional_usdt=float(t["notional_usdt"]),
                          leverage=int(t.get("leverage") or 1),
                          stop_loss=float(t.get("stop_loss") or 0))
                 for t in book]
    common = {"rule": "engine.risk.RiskManager.check_entry",
              "policy_digest": risk["running_policy_digest"],
              "equity_usdt": equity, "drawdown_pct": dd, "daily_pnl_pct": day,
              "closed_trades_count": closed_trades,
              "open_risk_usdt": sum(rm._position_risk(p, 0.0) for p in positions),
              "open_margin_usdt": sum(p.notional_usdt for p in positions)
              / e["leverage"],
              "blocking_constraints": risk["blocking"]}
    if risk["status_now"] == "BLOCK":
        return _dim(ESTABLISHED, "RISK_ASSESSMENT_BLOCK", max_notional_usdt=0.0,
                    max_quantity=0.0, max_margin_usdt=0.0, risk_usdt=0.0,
                    **common)
    try:
        s = rm.check_entry(ControlState.ACTIVE, symbol, strategy["price"],
                           strategy["atr"] or 0.0, strategy["stop_frac"],
                           open_positions=positions, equity=equity,
                           closed_trades_count=closed_trades,
                           market_type="futures")
    except RiskError:
        return _dim(ESTABLISHED, "risk_halt", max_notional_usdt=0.0,
                    max_quantity=0.0, max_margin_usdt=0.0, risk_usdt=0.0,
                    **common)
    if not s.ok:
        return _dim(ESTABLISHED, s.code or "risk_refused", max_notional_usdt=0.0,
                    max_quantity=0.0, max_margin_usdt=0.0, risk_usdt=0.0,
                    refusal=s.reason, **common)
    return _dim(ESTABLISHED, None, max_quantity=s.amount,
                max_notional_usdt=s.amount * strategy["price"],
                max_margin_usdt=s.size_usdt, risk_usdt=s.risk_usdt,
                size_mult=s.size_mult,
                control_state_not_applied="entry permission is control "
                                          "state's, recorded separately",
                **common)


# ── the venue ───────────────────────────────────────────────────────────
def _venue(reg: dict | None, price: float | None) -> dict:
    if reg is None:
        return {k: _dim(UNAVAILABLE, "INSTRUMENT_REGISTRY_NOT_SUPPLIED")
                for k in MANDATORY["venue"]}
    rec = reg["record"]
    c = rec["constraints"]
    out = {}
    trading = rec["venue_status"] == "TRADING" and \
        rec["venue_listing"] == "PRESENT"
    out["instrument_status"] = _dim(
        ESTABLISHED, None if trading else "VENUE_STATUS_NOT_TRADING",
        venue_allows_order=trading, venue_status=rec["venue_status"])
    mq, mn, st = c.get("minimum_quantity"), c.get("minimum_notional"), \
        c.get("quantity_step")
    if mq is None or mn is None or st is None:
        out["minimums"] = _dim(UNAVAILABLE, "VENUE_MINIMUMS_NOT_CAPTURED")
    else:
        m = {"minimum_quantity": mq, "minimum_notional_usdt": mn,
             "quantity_step": st}
        if price:
            m["minimum_order_notional_at_price"] = max(
                float(mn), float(Decimal(mq) * Decimal(str(price))))
        out["minimums"] = _dim(ESTABLISHED, None, **m)
    out["maximums"] = _dim(
        UNAVAILABLE, "VENUE_MAXIMUM_NOT_CAPTURED",
        detail="the instrument registry records LOT_SIZE minimum/step and "
               "MIN_NOTIONAL only; MARKET_LOT_SIZE maxQty and max notional "
               "are not captured")
    out["leverage"] = _dim(
        UNAVAILABLE, "VENUE_LEVERAGE_BRACKET_NOT_CAPTURED",
        leverage_bracket_presence=rec["leverage_bracket"])
    elig = rec["account_eligibility"]
    out["account_eligibility"] = (
        _dim(ESTABLISHED, None if elig == "ELIGIBLE" else "ACCOUNT_INELIGIBLE",
             account_eligibility=elig) if elig in ("ELIGIBLE", "INELIGIBLE")
        else _dim(UNAVAILABLE, "ACCOUNT_SYMBOL_ELIGIBILITY_UNKNOWN",
                  account_trading=reg["account_trading"]))
    return out


def _floor_step(qty: float, step: str) -> float:
    d = Decimal(step)
    if d <= 0:
        return qty
    return float((Decimal(str(qty)) / d).to_integral_value(ROUND_FLOOR) * d)


def _group(subs: dict) -> str:
    st = [d["status"] for d in subs.values()]
    return ESTABLISHED if all(s == ESTABLISHED for s in st) else \
        PARTIAL if any(s == ESTABLISHED for s in st) else UNAVAILABLE


def _funding(inputs: dict) -> dict:
    return _dim(UNAVAILABLE, "NO_RECORDED_AVAILABLE_MARGIN_OBSERVATION",
                detail="account_observation records totalMarginBalance "
                       "(equity) only; available balance is not recorded")


def _liquidity(inputs: dict) -> dict:
    return _dim(UNAVAILABLE, NO_LIQUIDITY_MODEL,
                supplied_evidence_ignored=len(inputs["liquidity_evidence"]),
                missing=list(LIQUIDITY_MISSING),
                existing_non_model_evidence=list(NON_MODEL_EVIDENCE))


# ── the pure computation ────────────────────────────────────────────────
def compute(inputs: dict) -> dict:
    """Every bound from `inputs` alone. Deterministic: replay re-runs it."""
    as_of = inputs["as_of_ms"]
    bounds = inputs["bounds"]
    kv = inputs["state_kv"]
    policy = inputs["risk_policy"]
    account = _account(kv, as_of)
    risk = _risk_record(kv, as_of, policy["effective"])
    portfolio = _portfolio(inputs["book"], inputs["venue_positions"], as_of,
                           bounds)
    strategy = _strategy(inputs["version"]["spec"], inputs["market"], as_of,
                         bounds)
    risk_b = _risk_bound(policy, risk, account, portfolio, strategy,
                         inputs["book"], inputs["symbol"],
                         inputs["closed_trades_count"])
    venue = _venue(inputs["instrument"], strategy.get("price"))
    dims = {
        "risk": {"risk_rule": risk_b},
        "account": {"equity": account, "funding": _funding(inputs)},
        "portfolio": {"venue_confirmed_book": portfolio},
        "strategy": {"stop_geometry": strategy},
        "venue": venue,
        "liquidity": {"liquidity_capacity_model": _liquidity(inputs)},
    }
    groups = {g: _group(s) for g, s in dims.items()}
    missing = sorted(f"{g}.{k}:{d['reason']}" for g, s in dims.items()
                     for k, d in s.items() if d["status"] != ESTABLISHED)
    if not missing:
        caps = [d.get("max_quantity") for s in dims.values()
                for d in s.values() if "max_quantity" in d]
        qty = _floor_step(min(caps), venue["minimums"]["quantity_step"])
        mins = venue["minimums"]
        ok = (venue["instrument_status"]["venue_allows_order"]
              and qty >= float(mins["minimum_quantity"])
              and qty * strategy["price"] >= float(mins["minimum_notional_usdt"]))
        effective = _dim(ESTABLISHED, None if ok else "BELOW_VENUE_MINIMUM",
                         max_quantity=qty if ok else 0.0,
                         max_notional_usdt=qty * strategy["price"] if ok else 0.0)
    else:
        effective = _dim(UNAVAILABLE, "MANDATORY_DIMENSION_UNAVAILABLE",
                         missing=missing)
    status = ESTABLISHED if effective["status"] == ESTABLISHED else \
        PARTIAL if any(v != UNAVAILABLE for v in groups.values()) \
        else UNAVAILABLE
    return {"status": status, "groups": groups, "dimensions": dims,
            "risk_assessment": risk, "effective": effective}


# ── gathering the evidence ──────────────────────────────────────────────
def _registry_input(snapshot, instrument_id: str, as_of_ms: int):
    if snapshot is None:
        return None
    rec = next((r for r in snapshot.records
                if r.instrument_id.value == instrument_id), None)
    if rec is None:
        _refuse("instrument_not_in_registry_snapshot")
    if snapshot.as_of_ms > as_of_ms:
        _refuse("registry_snapshot_after_as_of")
    return {"snapshot_id": snapshot.snapshot_id,
            "as_of_ms": snapshot.as_of_ms, "source": snapshot.source,
            "account_scope": snapshot.account_scope,
            "account_trading": snapshot.account_trading.value,
            "freshness": "NO_REGISTERED_REGISTRY_STALENESS_BOUND",
            "age_ms": as_of_ms - snapshot.as_of_ms,
            "record": _plain(asdict(rec))}


def _positions_input(obs):
    if obs is None:
        return None
    d = _plain({k: getattr(obs, k) for k in (
        "schema", "venue", "source", "environment", "source_ref",
        "request_start_ms", "response_received_ms", "as_of_ms", "complete",
        "observation_id")})
    d["market_type"] = _plain(obs.market_type)
    d["positions"] = [[p.instrument_id.value, p.position_present, p.side,
                       p.absolute_quantity] for p in obs.positions]
    return d


BOOK_KEYS = ("id", "symbol", "side", "amount", "entry_price", "notional_usdt",
             "leverage", "stop_loss", "market_type")


def read_book(journal) -> tuple[list, int]:
    """Risk's book and the proving count, read exactly as the Kernel reads
    them for `check_entry` (read-only)."""
    book = [{k: t.get(k) for k in BOOK_KEYS} for t in journal.query(
        "SELECT * FROM trades WHERE status='open' ORDER BY id")]
    closed = int(journal.query(
        "SELECT COUNT(*) AS n FROM trades WHERE status='closed'")[0]["n"])
    return book, closed


def gather(journal, cfg: dict, version: dict, *, instrument_id: str,
           market_type: str, as_of_ms: int, registry=None, positions=None,
           market: dict | None = None, liquidity_evidence=()) -> dict:
    """Read-only: every observed value the receipt binds. `version` is a
    re-verified StrategyVersion record; `registry` a RegistrySnapshot;
    `positions` a PortfolioObservation; `market` {"timeframe", "bars":
    [{ts (open ms), high, low, close}, ...]} of fully closed bars."""
    from ..core.instrument_registry import is_canonical_instrument_id
    from ..engine.risk import policy_from_config
    if not isinstance(as_of_ms, int) or isinstance(as_of_ms, bool) \
            or as_of_ms <= 0:
        _refuse("as_of_ms_invalid")
    if not is_canonical_instrument_id(instrument_id):
        _refuse("instrument_id_not_canonical")
    venue, mtype, vsym = instrument_id.split(":")
    if mtype != market_type or market_type != "futures":
        _refuse("market_type_mismatch")
    spec = version["spec"]
    if registry is None:
        symbol = None
    else:
        reg = _registry_input(registry, instrument_id, as_of_ms)
        symbol = f"{reg['record']['base_asset']}/{reg['record']['quote_asset']}"
    include = list((spec.get("universe") or {}).get("include") or [])
    exclude = set((spec.get("universe") or {}).get("exclude") or [])
    if symbol is None:
        symbol = next((s for s in include if _venue_symbol(s) == vsym), None)
    if symbol is None or (include and symbol not in include) \
            or symbol in exclude:
        _refuse("instrument_outside_spec_universe")
    if "futures" not in (spec.get("markets") or ["futures"]):
        _refuse("spec_market_excludes_futures")
    try:
        policy = policy_from_config(cfg)
    except (KeyError, TypeError, ValueError):
        _refuse("risk_policy_unreadable")
    book, closed = read_book(journal)
    bars = None
    if market is not None:
        rows = [{"ts": int(b["ts"]), "high": b["high"], "low": b["low"],
                 "close": b["close"]} for b in (market.get("bars") or [])]
        bars = {"timeframe": market.get("timeframe"),
                "source": market.get("source"), "bars": rows[-BARS_KEPT:]}
    if positions is not None and positions.as_of_ms > as_of_ms:
        _refuse("venue_positions_after_as_of")
    return {
        "as_of_ms": as_of_ms, "instrument_id": instrument_id,
        "market_type": market_type, "symbol": symbol,
        "version": {"version_id": version["version_id"],
                    "strategy_id": version["strategy_id"],
                    "spec_hash": version["spec_hash"], "spec": spec},
        "risk_policy": {"digest": policy["digest"],
                        "effective": policy["effective"],
                        "source": "config.yaml risk via "
                                  "engine.risk.policy_from_config"},
        "state_kv": {k: journal.kv_get(k) for k in KV_KEYS},
        "book": book, "closed_trades_count": closed,
        "book_source": "journal trades status='open' (Kernel check_entry book)",
        "venue_positions": _positions_input(positions),
        "instrument": None if registry is None else reg,
        "market": bars,
        "liquidity_evidence": [_plain(x) for x in liquidity_evidence],
        "bounds": _bounds(),
    }


def build(inputs: dict) -> dict:
    ident = {"schema": SCHEMA,
             "strategy_id": inputs["version"]["strategy_id"],
             "version_id": inputs["version"]["version_id"],
             "spec_hash": inputs["version"]["spec_hash"],
             "instrument_id": inputs["instrument_id"],
             "market_type": inputs["market_type"],
             "as_of_ms": inputs["as_of_ms"],
             "inputs_sha256": _jsha(inputs)}
    result = compute(inputs)
    return {**ident, "receipt_id": _jsha(ident), "inputs": inputs,
            "result": result, "status": result["status"],
            "semantics": "capacity of one new entry of this exact version on "
                         "this instrument at as_of_ms; grants nothing"}


# ── persistence (append-only) ───────────────────────────────────────────
DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    receipt_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL,
    spec_hash TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    as_of_ms INTEGER NOT NULL,
    status TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS {TABLE}_no_update BEFORE UPDATE ON {TABLE}
BEGIN SELECT RAISE(ABORT, '{TABLE} is immutable'); END;
CREATE TRIGGER IF NOT EXISTS {TABLE}_no_delete BEFORE DELETE ON {TABLE}
BEGIN SELECT RAISE(ABORT, '{TABLE} is immutable'); END;
"""


def ensure(journal) -> None:
    with journal._tx() as c:
        c.executescript(DDL)


def record(journal, receipt: dict, *, at_ms: int) -> dict:
    """insert / duplicate / conflict; never overwrites."""
    ensure(journal)
    text = canonical(receipt)
    row = {"receipt_id": receipt["receipt_id"],
           "version_id": receipt["version_id"],
           "spec_hash": receipt["spec_hash"],
           "instrument_id": receipt["instrument_id"],
           "as_of_ms": receipt["as_of_ms"], "status": receipt["status"],
           "canonical_sha256": _sha(text), "canonical_json": text,
           "recorded_at_ms": int(at_ms)}
    with journal._tx() as c:
        if not c.in_transaction:
            c.execute("BEGIN IMMEDIATE")
        old = c.execute(f"SELECT canonical_json FROM {TABLE} WHERE "
                        "receipt_id=?", (row["receipt_id"],)).fetchone()
        if old is not None:
            if old[0] != text:
                _refuse("capacity_receipt_conflict")
            return {"status": "duplicate", "receipt_id": row["receipt_id"]}
        c.execute(f"INSERT INTO {TABLE}({','.join(row)}) VALUES "
                  f"({','.join('?' * len(row))})", tuple(row.values()))
    return {"status": "inserted", "receipt_id": row["receipt_id"]}


def _table_exists(journal) -> bool:
    return bool(journal.query("SELECT 1 FROM sqlite_master WHERE "
                              "type='table' AND name=?", (TABLE,)))


def verify(receipt: dict) -> dict:
    """Identity and replay: the stored result must be `compute(inputs)`."""
    inputs = receipt.get("inputs")
    if not isinstance(inputs, dict):
        _refuse("capacity_receipt_malformed")
    ident = {"schema": SCHEMA,
             "strategy_id": inputs["version"]["strategy_id"],
             "version_id": inputs["version"]["version_id"],
             "spec_hash": inputs["version"]["spec_hash"],
             "instrument_id": inputs["instrument_id"],
             "market_type": inputs["market_type"],
             "as_of_ms": inputs["as_of_ms"], "inputs_sha256": _jsha(inputs)}
    if any(receipt.get(k) != v for k, v in ident.items()) \
            or receipt.get("receipt_id") != _jsha(ident):
        _refuse("capacity_receipt_id_mismatch")
    if canonical(compute(inputs)) != canonical(receipt.get("result")) \
            or receipt.get("status") != receipt["result"]["status"]:
        _refuse("capacity_replay_mismatch")
    return receipt


def load(journal, receipt_id: str) -> dict:
    if not _table_exists(journal):
        _refuse("capacity_receipt_missing")
    rows = journal.query(f"SELECT * FROM {TABLE} WHERE receipt_id=?",
                         (receipt_id,))
    if not rows:
        _refuse("capacity_receipt_missing")
    row = rows[0]
    if _sha(row["canonical_json"]) != row["canonical_sha256"]:
        _refuse("capacity_receipt_corrupt")
    rec = json.loads(row["canonical_json"])
    if (rec.get("receipt_id"), rec.get("version_id"), rec.get("spec_hash"),
            rec.get("status")) != (row["receipt_id"], row["version_id"],
                                   row["spec_hash"], row["status"]):
        _refuse("capacity_receipt_corrupt")
    return verify(rec)


# ── is a stored receipt still the answer now? ───────────────────────────
def check_current(journal, cfg: dict, version: dict, receipt_id: str, *,
                  now_ms: int) -> dict:
    """Read-only. Current only if the receipt re-verifies, binds this exact
    version, its Risk policy is still configured and running, none of its
    inputs has been superseded or gone stale by `now_ms`, and effective
    capacity is ESTABLISHED. Every failure is a reason; none is waived."""
    from ..engine.risk import policy_from_config
    out = {"receipt_id": receipt_id, "current": False, "reasons": [],
           "status": None, "effective": None, "contract": SCHEMA}
    r = out["reasons"]
    try:
        rec = load(journal, receipt_id)
    except CapacityRefused as e:
        r.append(e.code)
        return out
    inp = rec["inputs"]
    out.update(status=rec["status"], effective=rec["result"]["effective"],
               instrument_id=rec["instrument_id"], as_of_ms=rec["as_of_ms"])
    if (rec["version_id"], rec["spec_hash"], rec["strategy_id"]) != (
            version["version_id"], version["spec_hash"],
            version["strategy_id"]) or canonical(inp["version"]["spec"]) != \
            canonical(version["spec"]):
        r.append("capacity_receipt_wrong_version")
    if now_ms < rec["as_of_ms"]:
        r.append("capacity_receipt_from_future")
    try:
        now_policy = policy_from_config(cfg)["digest"]
    except (KeyError, TypeError, ValueError):
        now_policy = None
    if now_policy != inp["risk_policy"]["digest"]:
        r.append("capacity_risk_policy_changed")
    if canonical(inp["bounds"]) != canonical(_bounds()):
        r.append("capacity_bounds_changed")
    for k in KV_KEYS:
        if journal.kv_get(k) != inp["state_kv"][k]:
            r.append(f"capacity_inputs_superseded:{k}")
    book, closed = read_book(journal)
    if canonical(book) != canonical(inp["book"]) \
            or closed != inp["closed_trades_count"]:
        r.append("capacity_inputs_superseded:trades")
    kv = inp["state_kv"]
    if _account(kv, now_ms)["status"] != ESTABLISHED:
        r.append("capacity_account_evidence_stale")
    if not _risk_record(kv, now_ms, inp["risk_policy"]["effective"])["usable"]:
        r.append("capacity_risk_assessment_not_current")
    vp = inp["venue_positions"]
    if vp is None or now_ms - vp["as_of_ms"] > \
            inp["bounds"]["venue_positions_stale_s"] * 1000:
        r.append("capacity_venue_positions_stale")
    m = inp["market"]
    tf_ms = TF_MS.get((m or {}).get("timeframe"))
    if not m or not m["bars"] or not tf_ms \
            or now_ms >= m["bars"][-1]["ts"] + 2 * tf_ms:
        r.append("capacity_market_bar_superseded")
    if rec["result"]["effective"]["status"] != ESTABLISHED:
        r.append("capacity_not_established")
    out["reasons"] = list(dict.fromkeys(r))
    out["current"] = not out["reasons"]
    return out
