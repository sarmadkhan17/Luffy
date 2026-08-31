"""Read-only analyst tools. Each takes a Journal + typed kwargs and returns
JSON-serializable data. No writes, no arbitrary SQL from callers."""
from __future__ import annotations

from datetime import datetime, timezone

from ..core.journal import Journal


def _today_like() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d") + "%"


def get_positions(journal: Journal) -> list[dict]:
    out = []
    for t in journal.open_trades():
        out.append({k: t.get(k) for k in
                    ("symbol", "side", "amount", "entry_price",
                     "stop_loss", "take_profit", "leverage", "strategy_name")})
    return out


def get_pnl(journal: Journal, period: str = "today") -> dict:
    if period == "today":
        rows = journal.query(
            "SELECT realized_pnl FROM trades WHERE status='closed' "
            "AND closed_at LIKE ?", (_today_like(),))
    else:
        rows = journal.query(
            "SELECT realized_pnl FROM trades WHERE status='closed'")
    pnls = [float(r["realized_pnl"] or 0) for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    n = len(pnls)
    return {
        "period": period,
        "realized_pnl": round(sum(pnls), 2),
        "trades": n,
        "wins": wins, "losses": losses,
        "win_rate": round(100 * wins / n, 1) if n else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
    }


def get_trades(journal: Journal, symbol: str | None = None,
               strategy: str | None = None, status: str | None = None,
               limit: int = 20) -> list[dict]:
    clauses, params = [], []
    if symbol:
        clauses.append("symbol=?"); params.append(symbol)
    if strategy:
        clauses.append("strategy_name=?"); params.append(strategy)
    if status:
        clauses.append("status=?"); params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    limit = max(1, min(int(limit), 100))
    return journal.query(
        f"SELECT symbol,side,amount,entry_price,realized_pnl,status,"
        f"strategy_name,closed_at FROM trades {where} "
        f"ORDER BY rowid DESC LIMIT {limit}", tuple(params))


def get_decisions(journal: Journal, symbol: str | None = None,
                  action: str | None = None, limit: int = 10) -> list[dict]:
    clauses, params = ["action!='HOLD'"], []
    if symbol:
        clauses.append("symbol=?"); params.append(symbol)
    if action:
        clauses.append("action=?"); params.append(action)
    where = "WHERE " + " AND ".join(clauses)
    limit = max(1, min(int(limit), 50))
    return journal.query(
        f"SELECT ts,symbol,action,score,executed,skip_reason "
        f"FROM decisions {where} ORDER BY rowid DESC LIMIT {limit}",
        tuple(params))


def get_strategy_performance(journal: Journal,
                             name: str | None = None) -> list[dict]:
    if name:
        return journal.query(
            "SELECT name,state,kind,params FROM strategies WHERE name=?",
            (name,))
    return journal.query(
        "SELECT name,state,kind FROM strategies "
        "WHERE state IN ('paper','active','demoted') ORDER BY state LIMIT 20")


def get_agent_stats(journal: Journal, since_hours: float = 168.0) -> list[dict]:
    rows = journal.agent_accuracy(since_hours=since_hours)
    return [{"agent": a["agent"], "n": a["n"],
             "accuracy": round(a["accuracy"] or 0, 2)} for a in rows]


def get_equity_curve(journal: Journal, limit: int = 50) -> list[dict]:
    limit = max(1, min(int(limit), 200))
    rows = journal.query(
        f"SELECT ts,equity FROM equity ORDER BY ts DESC LIMIT {limit}")
    return list(reversed(rows))


TOOLS = {
    "get_positions": get_positions,
    "get_pnl": get_pnl,
    "get_trades": get_trades,
    "get_decisions": get_decisions,
    "get_strategy_performance": get_strategy_performance,
    "get_agent_stats": get_agent_stats,
    "get_equity_curve": get_equity_curve,
}


def _schema(name, desc, props=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object",
                       "properties": props or {},
                       "required": required or []}}}


TOOL_SCHEMAS = [
    _schema("get_positions",
            "Current open positions with entry, size, SL/TP, strategy."),
    _schema("get_pnl", "Realized P&L, win-rate, profit factor.",
            {"period": {"type": "string", "enum": ["today", "all"],
                        "description": "today or all-time"}}),
    _schema("get_trades", "Recent trades, optionally filtered.",
            {"symbol": {"type": "string"}, "strategy": {"type": "string"},
             "status": {"type": "string", "enum": ["open", "closed"]},
             "limit": {"type": "integer"}}),
    _schema("get_decisions",
            "Recent entry/exit decisions incl. skip_reason "
            "(why a trade was or wasn't taken).",
            {"symbol": {"type": "string"}, "action": {"type": "string"},
             "limit": {"type": "integer"}}),
    _schema("get_strategy_performance",
            "Strategy states/kinds; pass name for one.",
            {"name": {"type": "string"}}),
    _schema("get_agent_stats", "Analyst accuracy over a window.",
            {"since_hours": {"type": "number"}}),
    _schema("get_equity_curve",
            "Equity points over time for trend questions.",
            {"limit": {"type": "integer"}}),
]
