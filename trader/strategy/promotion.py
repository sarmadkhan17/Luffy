"""Closed-trade statistics for strategies: arithmetic and observation only.

This module does not promote, demote or retire anything. Promotion is the
Factory lifecycle: Backtest PASS -> the configured paper
probation (`strategies.paper_probation_trades` / `paper_min_winrate` /
`paper_min_profit_factor`, counted per exact installed version) -> exact owner
approval -> Governor. Retirement of a deployed spec is `analyst.review_deployed`
and the Governor. The legacy genome rules that used to live here (15 trades,
6 consecutive losses, PF 0.85 over 20, 14 days demoted) could not mutate any
state — every transition was refused for lack of replay-verified learning
authority (`refuse_legacy_learning`) — and a second, different probation
threshold beside the configured one is a conflict rather than a safeguard, so
they are removed.

What remains is consumed: `stats_of` is the probation arithmetic;
`evaluate_population` keeps the persisted `stats_json` blob that the dashboard
and the vault read fresh.
"""
from __future__ import annotations

import json
import logging

from ..core.journal import Journal

log = logging.getLogger(__name__)


def _stats_for(journal: Journal, strategy_id: str) -> dict:
    trades = journal.trades_for_strategy(strategy_id)
    return stats_of([t for t in trades if t["status"] == "closed"])


def stats_of(closed: list) -> dict:
    """Probation arithmetic over closed trade rows, in journal order."""
    wins = [t for t in closed if float(t.get("realized_pnl") or 0) > 0]
    losses = [t for t in closed if float(t.get("realized_pnl") or 0) <= 0]
    gross_win = sum(float(t["realized_pnl"]) for t in wins)
    gross_loss = sum(abs(float(t["realized_pnl"])) for t in losses)
    consec = 0
    for t in reversed(closed):
        if float(t.get("realized_pnl") or 0) <= 0:
            consec += 1
        else:
            break
    pf = gross_win / gross_loss if gross_loss > 0 else \
        (99.0 if gross_win > 0 else 0.0)
    return {"trades": len(closed), "wins": len(wins),
            "winrate": len(wins) / len(closed) if closed else 0.0,
            "pf": pf, "consecutive_losses": consec,
            "pnl": sum(float(t.get("realized_pnl") or 0) for t in closed)}


def evaluate_population(journal: Journal, notifier=None) -> list[dict]:
    """Refresh each live strategy's persisted statistics. Never changes state.

    Returns no actions; the signature is kept for the brain tick and the
    Strategist's deterministic fallback, which report whatever it returns."""
    for row in journal.list_strategies():
        if row["state"] == "retired":
            continue
        st = _stats_for(journal, row["id"])
        # `query()` does not commit, so the refresh goes through `_tx()`.
        with journal._tx() as c:
            c.execute("UPDATE strategies SET stats_json=? WHERE id=?",
                      (json.dumps({"trades": st["trades"], "wins": st["wins"],
                                   "pnl_usdt": round(st["pnl"], 4),
                                   "pf": round(min(st["pf"], 99), 3)}),
                       row["id"]))
    return []


def population_snapshot(journal: Journal) -> list[dict]:
    out = []
    for row in journal.list_strategies():
        st = _stats_for(journal, row["id"])
        out.append({"id": row["id"], "name": row["name"], "kind": row["kind"],
                    "state": row["state"], **st})
    return out
