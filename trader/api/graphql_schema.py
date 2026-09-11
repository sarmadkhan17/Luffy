"""GraphQL schema — the API contract serving dashboard AND Luffy's assistant.

Queries read the journal; mutations write control intents (state_kv /
control_events) that the running kernel picks up next cycle. Single writer
(kernel) discipline preserved; dashboard/assistant only leave intent.
"""
from __future__ import annotations

import json
from typing import Optional
from datetime import datetime, timezone

import strawberry
from strawberry.fastapi import GraphQLRouter
from strawberry.types import Info
from strawberry.schema.config import StrawberryConfig

from ..core.journal import Journal


def _rows(j: Journal, sql: str, params: tuple = ()) -> list:
    return j.query(sql, params)


# ── types ────────────────────────────────────────────────────────────────
@strawberry.type
class EquityPoint:
    ts: str
    equity: float
    open_positions: int


@strawberry.type
class VoteType:
    agent: str
    side: str
    conviction: float
    confidence: float
    rationale: str
    raw_conviction: Optional[float] = None
    calibrated: bool = False
    htf: Optional[float] = None
    news_blackout: bool = False


def _vote_from_row(v) -> VoteType:
    try:
        meta = json.loads(v["meta"] or "{}")
    except Exception:
        meta = {}
    return VoteType(
        agent=v["agent"], side=v["side"], conviction=v["conviction"],
        confidence=v["confidence"], rationale=v["rationale"] or "",
        raw_conviction=meta.get("raw_conviction"),
        calibrated=bool(meta.get("calibrated")),
        htf=meta.get("htf"), news_blackout=bool(meta.get("news_blackout")))


@strawberry.type
class NewsGuardType:
    active: bool
    why: str
    checked_at: str


@strawberry.type
class RentWeekType:
    week_start: str
    verdict: str
    net: Optional[float]


@strawberry.type
class RentType:
    week_start: str
    net: Optional[float]
    bar: float
    days_left: float
    status: str
    updated_at: str
    history: list[RentWeekType]


@strawberry.type
class BrainEventType:
    ts: str
    kind: str
    subject: str
    detail: str


@strawberry.type
class TvHealthType:
    state: str
    runs_today: int
    budget: int
    budget_enabled: bool = True


@strawberry.type
class DecisionType:
    id: str
    ts: str
    symbol: str
    action: str
    score: float
    threshold: float
    confidence: float
    executed: bool
    skip_reason: str
    votes: list[VoteType]


@strawberry.type
class TradeType:
    id: str
    symbol: str
    side: str
    amount: float
    entry_price: float
    exit_price: str
    notional_usdt: float
    leverage: int
    strategy_name: str
    status: str
    realized_pnl: str
    close_reason: str
    opened_at: str
    closed_at: str


@strawberry.type
class StrategyType:
    id: str
    name: str
    kind: str
    state: str
    origin: str
    hypothesis: str


@strawberry.type
class AgentStatType:
    agent: str
    n: int
    accuracy: str


@strawberry.type
class StrategyFullType(StrategyType):
    params: str
    state_detail: str
    generation: int = 0
    retire_reason: str = ""
    trades: int = 0
    wins: int = 0
    pnl_usdt: float = 0.0
    profit_factor: float = 0.0
    winrate: float = 0.0


@strawberry.type
class StatusType:
    control_state: str
    market_type: str
    heartbeat_age_s: str
    equity: str
    drawdown_pct: str
    daily_pnl_pct: str
    open_trades: int
    strategies_active: int


# ── queries ──────────────────────────────────────────────────────────────
def build_query(journal: Journal):

    @strawberry.type
    class Query:
        @strawberry.field
        def status(self) -> StatusType:
            kv = journal.kv_get
            hb = _rows(journal, "SELECT ts FROM equity ORDER BY ts DESC LIMIT 1")
            eq = _rows(journal, "SELECT * FROM equity ORDER BY ts DESC LIMIT 1")
            import time as _t
            hb_age = "never"
            try:
                from pathlib import Path
                p = Path(__file__).resolve().parents[2] / "data" / "heartbeat_luffy.json"
                hb_age = f"{_t.time() - json.loads(p.read_text())['timestamp']:.0f}s"
            except Exception:
                pass
            return StatusType(
                control_state=kv("control_state", "ACTIVE"),
                market_type=kv("market_type", "futures"),
                heartbeat_age_s=hb_age,
                equity=str(eq[0]["equity"]) if eq else "0",
                drawdown_pct="0", daily_pnl_pct="0",
                open_trades=len(journal.open_trades()),
                strategies_active=len(
                    journal.list_strategies(["paper", "active"])))

        @strawberry.field
        def equity_curve(self, limit: int = 500) -> list[EquityPoint]:
            rows = _rows(journal,
                         "SELECT * FROM equity ORDER BY ts DESC LIMIT ?",
                         (limit,))
            return [EquityPoint(ts=r["ts"], equity=r["equity"],
                                open_positions=r["open_positions"])
                    for r in reversed(rows)]

        @strawberry.field
        def trades_total(self) -> int:
            return journal.query(
                "SELECT COUNT(*) n FROM trades")[0]["n"]

        @strawberry.field
        def decisions_total(self, symbol: Optional[str] = None,
                            directional_only: bool = False) -> int:
            if symbol:
                return journal.query(
                    "SELECT COUNT(*) n FROM decisions WHERE symbol=?",
                    (symbol,))[0]["n"]
            if directional_only:
                return journal.query(
                    "SELECT COUNT(*) n FROM decisions WHERE action!='HOLD'"
                    )[0]["n"]
            return journal.query("SELECT COUNT(*) n FROM decisions")[0]["n"]

        @strawberry.field
        def decisions(self, executed_only: bool = False,
                      symbol: Optional[str] = None,
                      directional_only: bool = False,
                      limit: int = 100, offset: int = 0) -> list[DecisionType]:
            conds, params = [], []
            if executed_only:
                conds.append("executed=1")
            if symbol:
                conds.append("symbol=?")
                params.append(symbol)
            if directional_only:
                conds.append("action!='HOLD'")
            if conds:
                conds.append("1=1")
            where = ("WHERE " + " AND ".join(conds)) if conds else ""
            rows = _rows(journal,
                         f"SELECT * FROM decisions {where} "
                         f"ORDER BY ts DESC LIMIT ? OFFSET ?",
                         tuple(params) + (limit, offset))
            out = []
            for r in rows:
                vrows = _rows(journal,
                              "SELECT * FROM votes WHERE cycle_id=?",
                              (r["cycle_id"],))
                out.append(DecisionType(
                    id=r["id"], ts=r["ts"], symbol=r["symbol"],
                    action=r["action"], score=r["score"],
                    threshold=r["threshold"], confidence=r["confidence"],
                    executed=bool(r["executed"]), skip_reason=r["skip_reason"] or "",
                    votes=[_vote_from_row(v) for v in vrows]))
            return out

        @strawberry.field
        def news_guard(self) -> NewsGuardType:
            raw = journal.kv_get("news_guard_state", "")
            try:
                d = json.loads(raw)
                return NewsGuardType(active=bool(d.get("active")),
                                     why=d.get("why", ""),
                                     checked_at=str(d.get("ts", "")))
            except Exception:
                return NewsGuardType(active=False, why="no data yet",
                                     checked_at="")

        @strawberry.field
        def rent(self) -> RentType:
            from ..engine.rent_keeper import snapshot
            snap = snapshot(journal)
            s = snap["state"]
            return RentType(
                week_start=str(s.get("week_start", "")),
                net=s.get("net"),
                bar=float(s.get("bar", 50)),
                days_left=float(s.get("days_left") or 0),
                status=str(s.get("status", "no reading yet")),
                updated_at=str(s.get("updated_at", "")),
                history=[RentWeekType(week_start=h["week_start"],
                                      verdict=str(h["verdict"]), net=h["net"])
                         for h in snap["history"]])

        @strawberry.field
        def recent_vetoes(self, limit: int = 10) -> list[DecisionType]:
            rows = _rows(journal,
                         "SELECT * FROM decisions WHERE skip_reason "
                         "LIKE '%veto%' ORDER BY ts DESC LIMIT ?", (limit,))
            return [DecisionType(
                id=r["id"], ts=r["ts"], symbol=r["symbol"],
                action=r["action"], score=r["score"],
                threshold=r["threshold"], confidence=r["confidence"],
                executed=bool(r["executed"]),
                skip_reason=r["skip_reason"] or "", votes=[]) for r in rows]

        @strawberry.field
        def brain_events(self, kinds: str = "brain_judgement,pine_forged",
                         limit: int = 20) -> list[BrainEventType]:
            kind_list = [k.strip() for k in kinds.split(",") if k.strip()]
            placeholders = ",".join("?" for _ in kind_list)
            rows = _rows(journal,
                         f"SELECT ts, kind, subject, detail FROM brain_events "
                         f"WHERE kind IN ({placeholders}) "
                         f"ORDER BY ts DESC LIMIT ?",
                         tuple(kind_list) + (limit,))
            return [BrainEventType(ts=r["ts"], kind=r["kind"],
                                   subject=r["subject"],
                                   detail=(r["detail"] or "")[:900])
                    for r in rows]

        @strawberry.field
        def tv_health(self) -> TvHealthType:
            from ..core.config import load_config as _lc
            from ..brain.tv_harness import TVHarness
            h = TVHarness(journal, {"tv_harness": _lc().get("tv_harness", {})})
            st = h.health()
            return TvHealthType(state=st["state"],
                                runs_today=st["runs_today"],
                                budget=st["budget"],
                                budget_enabled=h.budget_enabled)

        @strawberry.field
        def trades(self, open_only: bool = False,
                  limit: int = 200, offset: int = 0) -> list[TradeType]:
            where = "WHERE status='open'" if open_only else ""
            rows = _rows(journal,
                         f"SELECT * FROM trades {where} "
                         f"ORDER BY opened_at DESC LIMIT ? OFFSET ?",
                         (limit, offset))
            return [TradeType(
                id=r["id"], symbol=r["symbol"], side=r["side"],
                amount=r["amount"], entry_price=r["entry_price"],
                exit_price=str(r["exit_price"]),
                notional_usdt=r["notional_usdt"], leverage=r["leverage"],
                strategy_name=r["strategy_name"] or "", status=r["status"],
                realized_pnl=str(r["realized_pnl"]),
                close_reason=r["close_reason"] or "",
                opened_at=r["opened_at"], closed_at=r["closed_at"] or "")
                for r in rows]

        @strawberry.field
        def agents_accuracy(self, since_hours: int = 336) -> list[AgentStatType]:
            rows = journal.agent_accuracy(since_hours=float(since_hours))
            return [AgentStatType(r["agent"], r["n"],
                                  f"{(r['accuracy'] or 0):.2f}") for r in rows]

        @strawberry.field
        def strategy_full(self) -> list[StrategyFullType]:
            out = []
            for r in journal.list_strategies():
                try:
                    s = json.loads(r.get("stats_json") or "{}")
                except Exception:
                    s = {}
                trades = int(s.get("trades") or 0)
                wins = int(s.get("wins") or 0)
                pf = float(s.get("pf") or 0.0)
                wr = float(s.get("winrate") or
                           ((wins / trades) if trades else 0.0))
                out.append(StrategyFullType(
                    id=r["id"], name=r["name"], kind=r["kind"],
                    state=r["state"], origin=r["origin"],
                    hypothesis=r["hypothesis"] or "", params=r["params"] or "{}",
                    state_detail=r["state"],
                    generation=int(r.get("generation") or 0),
                    retire_reason=r.get("retire_reason") or "",
                    trades=trades, wins=wins,
                    pnl_usdt=float(s.get("pnl_usdt") or 0.0),
                    profit_factor=min(pf, 99.0), winrate=wr))
            return out

        @strawberry.field
        def strategies(self) -> list[StrategyType]:
            return [StrategyType(r["id"], r["name"], r["kind"], r["state"],
                                 r["origin"], r["hypothesis"] or "")
                    for r in journal.list_strategies()]

    return Query


# ── mutations ────────────────────────────────────────────────────────────
def build_mutation(journal: Journal):

    @strawberry.type
    class Mutation:
        @strawberry.mutation
        def set_control_state(self, state: str, actor: str = "dashboard") -> bool:
            from ..core.types import ControlState
            from ..engine.state import ControlStateMachine
            sm = ControlStateMachine(journal)
            sm.set(ControlState(state.upper()), actor)
            return True

        @strawberry.mutation
        def panic(self, actor: str = "dashboard") -> bool:
            journal.kv_set("panic_requested", "1")
            journal.log_control_event("panic", actor, detail="via graphql")
            return True

        @strawberry.mutation
        def close_trade(self, trade_id: str, actor: str = "dashboard") -> bool:
            """Queue one open position for the kernel to market-close.

            /panic flattens the whole book and freezes it, which is far too
            blunt for a single trade. Intent only — the browser places no
            orders. A list, not a scalar key: two positions closed in the
            same cycle must not overwrite each other.
            """
            rows = journal.query(
                "SELECT id FROM trades WHERE id=? AND status='open'",
                (trade_id,))
            if not rows:
                return False
            try:
                pending = json.loads(journal.kv_get("close_requests", "[]"))
                if not isinstance(pending, list):
                    pending = []
            except Exception:
                pending = []
            if trade_id not in pending:
                pending.append(trade_id)
                journal.kv_set("close_requests", json.dumps(pending))
            journal.log_control_event("manual_close", actor, detail=trade_id)
            return True

        @strawberry.mutation
        def set_market_type(self, market: str, actor: str = "dashboard") -> bool:
            if market.lower() not in ("spot", "futures"):
                return False
            journal.kv_set("market_type", market.lower())
            journal.log_control_event("mode_switch", actor,
                                      detail=f"new entries → {market}")
            return True

    return Mutation


def make_graphql_router(journal: Journal) -> GraphQLRouter:
    Query = build_query(journal)
    Mutation = build_mutation(journal)
    schema = strawberry.Schema(
        query=Query, mutation=Mutation,
        config=StrawberryConfig(auto_camel_case=False))
    return GraphQLRouter(schema, path="/graphql")
