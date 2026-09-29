"""GraphQL schema — the API contract serving dashboard AND Luffy's assistant.

Queries read the journal. Mutations are typed Owner Interface requests the
kernel executes (trader/owner); the dashboard writes no control state.
"""
from __future__ import annotations

import asyncio
import functools
import json
from typing import Optional
from datetime import datetime, timezone

import strawberry
from strawberry.dataloader import DataLoader
from strawberry.fastapi import GraphQLRouter
from strawberry.types import Info
from strawberry.schema.config import StrawberryConfig

from ..core.journal import Journal


def _rows(j: Journal, sql: str, params: tuple = ()) -> list:
    return j.query(sql, params)


#: Upper bound on any list a query returns. Callers asking for more get this.
MAX_ROWS = 1000
#: SQLite bound-parameter budget per votes batch.
_VOTE_BATCH = 500


def _bound(n: int, cap: int = MAX_ROWS) -> int:
    return max(0, min(int(n), cap))


def _offloop(fn):
    """Run a synchronous journal-reading resolver in a worker thread.

    Strawberry calls a sync resolver inline on the ASGI event loop, so one slow
    SQLite read (or a 30 s busy-wait on a write lock) stalled every other
    request the dashboard serves — static JS chunks included. Journal
    connections are thread-local, so a worker thread reads on its own
    connection."""
    @functools.wraps(fn)
    async def run(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
    return run


def votes_for_cycles(j: Journal, cycle_ids) -> dict:
    """{cycle_id: [vote rows]} in one pass per batch of cycle ids.

    votes has no cycle_id index, so each statement is one scan of the table;
    batching makes that one scan per request instead of one per decision."""
    ids = list(dict.fromkeys(c for c in cycle_ids if c))
    out: dict = {c: [] for c in ids}
    for i in range(0, len(ids), _VOTE_BATCH):
        chunk = ids[i:i + _VOTE_BATCH]
        marks = ",".join("?" for _ in chunk)
        for v in _rows(j, f"SELECT * FROM votes WHERE cycle_id IN ({marks}) "
                          f"ORDER BY rowid", tuple(chunk)):
            out[v["cycle_id"]].append(v)
    return out


def _votes_loader(journal: Journal) -> DataLoader:
    async def load(keys):
        found = await asyncio.to_thread(votes_for_cycles, journal, keys)
        return [[_vote_from_row(v) for v in found.get(k, [])] for k in keys]
    return DataLoader(load_fn=load)


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
    cycle_id: strawberry.Private[Optional[str]] = None

    @strawberry.field
    async def votes(self, info: Info) -> list[VoteType]:
        """The cycle's analyst votes. Read only when selected, and batched
        across every decision in the response (one votes read per request)."""
        if not self.cycle_id:
            return []
        return await info.context["votes_loader"].load(self.cycle_id)


def _decision(r) -> "DecisionType":
    return DecisionType(
        id=r["id"], ts=r["ts"], symbol=r["symbol"], action=r["action"],
        score=r["score"], threshold=r["threshold"], confidence=r["confidence"],
        executed=bool(r["executed"]), skip_reason=r["skip_reason"] or "",
        cycle_id=r["cycle_id"])


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
        @_offloop
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
        @_offloop
        def equity_curve(self, limit: int = 500) -> list[EquityPoint]:
            rows = _rows(journal,
                         "SELECT * FROM equity ORDER BY ts DESC LIMIT ?",
                         (_bound(limit, 5000),))
            return [EquityPoint(ts=r["ts"], equity=r["equity"],
                                open_positions=r["open_positions"])
                    for r in reversed(rows)]

        @strawberry.field
        @_offloop
        def trades_total(self) -> int:
            return journal.query(
                "SELECT COUNT(*) n FROM trades")[0]["n"]

        @strawberry.field
        @_offloop
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
        @_offloop
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
                         tuple(params) + (_bound(limit), max(0, int(offset))))
            return [_decision(r) for r in rows]

        @strawberry.field
        @_offloop
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
        @_offloop
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
        @_offloop
        def recent_vetoes(self, limit: int = 10) -> list[DecisionType]:
            rows = _rows(journal,
                         "SELECT * FROM decisions WHERE skip_reason "
                         "LIKE '%veto%' ORDER BY ts DESC LIMIT ?", (_bound(limit),))
            return [_decision(r) for r in rows]

        @strawberry.field
        @_offloop
        def brain_events(self, kinds: str = "brain_judgement,pine_forged",
                         limit: int = 20) -> list[BrainEventType]:
            kind_list = [k.strip() for k in kinds.split(",") if k.strip()]
            placeholders = ",".join("?" for _ in kind_list)
            rows = _rows(journal,
                         f"SELECT ts, kind, subject, detail FROM brain_events "
                         f"WHERE kind IN ({placeholders}) "
                         f"ORDER BY ts DESC LIMIT ?",
                         tuple(kind_list) + (_bound(limit),))
            return [BrainEventType(ts=r["ts"], kind=r["kind"],
                                   subject=r["subject"],
                                   detail=(r["detail"] or "")[:900])
                    for r in rows]

        @strawberry.field
        @_offloop
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
        @_offloop
        def trades(self, open_only: bool = False,
                  limit: int = 200, offset: int = 0) -> list[TradeType]:
            where = "WHERE status='open'" if open_only else ""
            rows = _rows(journal,
                         f"SELECT * FROM trades {where} "
                         f"ORDER BY opened_at DESC LIMIT ? OFFSET ?",
                         (_bound(limit), max(0, int(offset))))
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
        @_offloop
        def agents_accuracy(self, since_hours: int = 336) -> list[AgentStatType]:
            rows = journal.agent_accuracy(since_hours=float(since_hours))
            return [AgentStatType(r["agent"], r["n"],
                                  f"{(r['accuracy'] or 0):.2f}") for r in rows]

        @strawberry.field
        @_offloop
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
        @_offloop
        def strategies(self) -> list[StrategyType]:
            return [StrategyType(r["id"], r["name"], r["kind"], r["state"],
                                 r["origin"], r["hypothesis"] or "")
                    for r in journal.list_strategies()]

    return Query


# ── mutations ────────────────────────────────────────────────────────────
@strawberry.type
class OwnerControlResult:
    """The kernel Owner Interface's typed answer (trader.owner.contract.OwnerResult)."""
    request_id: Optional[str]
    operation: Optional[str]
    status: str
    control_state_before: Optional[str]
    control_state_after: Optional[str]
    reasons: list[str]
    supervisor_outcome: Optional[str]
    audit_event_ids: list[int]
    replayed: bool
    message: str
    disposition: str = "COMPLETED"   # COMPLETED | REFUSED | IN_PROGRESS | OUTCOME_UNKNOWN | UNAVAILABLE


def _owner_control_result(result) -> OwnerControlResult:
    from ..owner.adapters.render import render
    return OwnerControlResult(
        request_id=result.request_id, operation=result.operation, status=result.status,
        control_state_before=result.control_state_before,
        control_state_after=result.control_state_after,
        reasons=[str(r) for r in result.reasons],
        supervisor_outcome=result.supervisor_outcome,
        audit_event_ids=[int(i) for i in result.audit_event_ids],
        replayed=bool(result.replayed), message=render(result),
        disposition=result.disposition)


def build_mutation(journal: Journal, owner_gateway=None):
    """Every owner mutation is a typed Owner Interface request executed by the
    kernel. The dashboard writes no control state or intent itself, and no
    mutation accepts a caller-chosen actor: the kernel derives it."""

    async def submit(info: Info, operation: str, request_id, issued_at_ms,
                     args: dict | None = None) -> OwnerControlResult:
        import asyncio
        from ..owner.contract import Status, refused
        if owner_gateway is None:
            return _owner_control_result(
                refused(None, "owner_interface_not_configured", Status.UNAVAILABLE))
        request = (info.context or {}).get("request")
        result = await asyncio.to_thread(owner_gateway, operation, request_id,
                                         issued_at_ms, request, args or {})
        return _owner_control_result(result)

    @strawberry.type
    class Mutation:
        @strawberry.mutation
        async def owner_control(self, info: Info, operation: str, request_id: str,
                                issued_at_ms: float) -> OwnerControlResult:
            """freeze | halt | resume | unhalt | panic, executed by the kernel only.

            `request_id` and `issued_at_ms` are created once per intended action
            in the browser and reused on every retry. Unreachable kernel →
            UNAVAILABLE; lost answer → ERROR outcome unknown; never a local
            fallback or a queue.
            """
            from ..owner.contract import (CONTAINMENT_OPERATIONS, RECOVERY_OPERATIONS,
                                          refused)
            op = (operation or "").strip().lower()
            if op not in CONTAINMENT_OPERATIONS + RECOVERY_OPERATIONS:
                return _owner_control_result(refused(None, "unsupported_operation"))
            return await submit(info, op, request_id, issued_at_ms)

        @strawberry.mutation
        async def panic(self, info: Info, request_id: str,
                        issued_at_ms: float) -> OwnerControlResult:
            """Flatten everything and freeze — queued in the kernel via the gateway."""
            return await submit(info, "panic", request_id, issued_at_ms)

        @strawberry.mutation
        async def close_trade(self, info: Info, trade_id: str, request_id: str,
                              issued_at_ms: float) -> OwnerControlResult:
            """Queue one open position for the kernel to market-close.

            /panic flattens the whole book and freezes it, which is far too
            blunt for a single trade. The kernel validates the trade is open
            and appends it to its close queue; the browser places no orders.
            """
            return await submit(info, "close_trade", request_id, issued_at_ms,
                                {"trade_id": trade_id})

        @strawberry.mutation
        async def set_market_type(self, info: Info, market: str, request_id: str,
                                  issued_at_ms: float) -> OwnerControlResult:
            return await submit(info, "set_market_type", request_id, issued_at_ms,
                                {"market": (market or "").lower()})

    return Mutation


def make_graphql_router(journal: Journal, owner_gateway=None) -> GraphQLRouter:
    Query = build_query(journal)
    Mutation = build_mutation(journal, owner_gateway)
    schema = strawberry.Schema(
        query=Query, mutation=Mutation,
        config=StrawberryConfig(auto_camel_case=False))

    async def context():
        # merged with strawberry's default {request, response, background_tasks}
        return {"votes_loader": _votes_loader(journal)}
    return GraphQLRouter(schema, path="/graphql", context_getter=context)
