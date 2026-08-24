"""Boot-time reconciliation — Luffy never trusts its own book blindly.

On startup, exchange truth wins:
- position on exchange, absent in journal  → ADOPT (marked 'adopted')
- open trade in journal, gone on exchange  → GHOST → close at market
- both present, amount drifted             → align journal to exchange

Spot mode: no short positions; reconcile only journal ghosts via price feed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..core.types import ClosedTrade, Position, Side, new_id, norm_symbol
from ..core.journal import Journal

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def reconcile_futures(exchange, journal: Journal) -> dict:
    """Align journal open trades with live exchange positions."""
    try:
        ex_positions = {
            norm_symbol(p["symbol"]): p
            for p in (exchange.fetch_positions() or [])
            if float(p.get("contracts") or 0) > 0
        }
    except Exception as e:
        log.error(f"reconcile: cannot fetch positions ({e}) — keeping journal as-is")
        return {"adopted": 0, "ghosts": 0, "aligned": 0, "error": str(e)}

    j_open = {t["symbol"]: t for t in journal.open_trades()}
    adopted = ghosts = aligned = 0

    # 1. adopt orphans
    for sym, p in ex_positions.items():
        contracts = float(p.get("contracts") or 0)
        side_raw = (p.get("side") or "long").lower()
        entry = float(p.get("entryPrice") or p.get("markPrice") or 0)
        if sym not in j_open:
            pos = Position(
                id=new_id("adopt"), symbol=sym,
                side=Side.LONG if side_raw == "long" else Side.SHORT,
                amount=contracts, entry_price=entry,
                notional_usdt=float(p.get("notional") or contracts * entry),
                leverage=int(p.get("leverage") or 1),
                market_type="futures", exec_mode="live",
                strategy_id="adopted", strategy_name="pre-existing position",
                decision_id="")
            journal.add_trade(pos)
            log.warning(f"ADOPTED orphaned exchange position {sym} "
                        f"{side_raw} {contracts} @ {entry}")
            adopted += 1
        else:
            jt = j_open[sym]
            if abs(float(jt["amount"]) - contracts) > max(contracts * 0.01, 1e-9):
                journal.query("UPDATE trades SET amount=?, notional_usdt=? WHERE id=?",
                              (contracts, float(p.get("notional") or 0), jt["id"]))
                log.info(f"ALIGNED {sym}: amount {jt['amount']} → {contracts}")
                aligned += 1

    # 2. ghost cleanup — journal says open, exchange disagrees
    mark_cache: dict[str, float] = {}
    for sym, jt in j_open.items():
        if sym in ex_positions:
            continue
        if jt["market_type"] != "futures":
            continue   # spot handled separately (balances ≠ positions)
        if sym not in mark_cache:
            try:
                t = exchange.fetch_ticker(sym)
                mark_cache[sym] = float(t.get("last") or 0)
            except Exception:
                mark_cache[sym] = float(jt["entry_price"])
        exit_px = mark_cache[sym]
        direction = 1.0 if jt["side"] == "long" else -1.0
        pnl = ((exit_px - float(jt["entry_price"])) * direction
               * float(jt["amount"]) * int(jt["leverage"] or 1))
        journal.close_trade(jt["id"], exit_px, round(pnl, 8), "reconciled_ghost")
        log.warning(f"GHOST closed: {sym} was open in journal, absent on "
                    f"exchange → closed @{exit_px} pnl={pnl:+.2f}")
        ghosts += 1

    summary = {"adopted": adopted, "ghosts": ghosts, "aligned": aligned}
    if any(summary.values()):
        journal.log_control_event("reconcile", "luffy", detail=summary)
    log.info(f"reconcile: {summary}")
    return summary


def flatten_all(exchange, journal: Journal, notifier=None) -> int:
    """PANIC: cancel protective orders then market-close every open position.

    Returns count of positions closed. Best-effort per symbol — one failure
    doesn't stop the rest.
    """
    open_trades = [t for t in journal.open_trades()
                   if t["market_type"] == "futures"]
    closed = 0
    for t in open_trades:
        sym = t["symbol"]
        side_close = "sell" if t["side"] == "long" else "buy"
        try:
            if t.get("sl_order_id"):
                try:
                    exchange.cancel_order(t["sl_order_id"], sym)
                except Exception:
                    pass
            order = exchange.create_order(
                sym, "market", side_close, float(t["amount"]),
                params={"reduceOnly": True})
            fill = float(order.get("average") or order.get("price") or 0)
            direction = 1.0 if t["side"] == "long" else -1.0
            pnl = ((fill - float(t["entry_price"])) * direction
                   * float(t["amount"]) * int(t["leverage"] or 1))
            journal.close_trade(t["id"], fill, round(pnl, 8), "panic")
            closed += 1
            log.warning(f"PANIC close {sym}: {t['amount']} @ ~{fill} "
                        f"(pnl {pnl:+.2f})")
        except Exception as e:
            log.error(f"PANIC close FAILED {sym}: {e} — will retry next cycle")
    if notifier and closed:
        notifier.send(f"🚨 PANIC: flattened {closed} position(s)")
    return closed
