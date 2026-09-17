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
from . import protective

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def venue_realized_pnl(exchange, symbol: str,
                       since_iso: str, observation: dict | None = None) -> float | None:
    """What the venue says this position has realized, net of commission.

    The only honest answer when the journal and the venue disagree about a
    position's size: the fills happened, at prices nobody has to guess at.
    Marking a hours-old exit to the boot-time price booked -2.51 against a
    true +117.84 on 2026-09-02 — the wrong sign on the only trade of the
    session, feeding straight into the PF the Analyst selects on.

    None when the venue will not answer; the caller falls back to the mark.
    """
    from .accounting import safe_fill
    from .booking import monetary_total
    import time
    observation = observation if observation is not None else {}
    observation.update(basis="symbol_time_window_subtotal_unattributed", symbol=symbol,
                       since_iso=since_iso, observed_ms=int(time.time()*1000), fills=[],
                       funding_usdt=None, learning_eligible=False)
    try:
        since = int(datetime.fromisoformat(since_iso).timestamp() * 1000)
        fills = exchange.fetch_my_trades(symbol, since=since, limit=1000)
        if not isinstance(fills, list):
            raise ValueError("invalid_fill_response")
        observation['fills'] = [safe_fill(f) for f in fills]
    except Exception as exc:
        observation['reason'] = "fill_history_unavailable:"+type(exc).__name__
        log.warning("venue fill history unavailable for %s (%s)", symbol, type(exc).__name__)
        return None
    if len(fills) >= 1000:
        observation['reason'] = "history_page_full_retry_required"
        return None
    total = monetary_total(observation['fills'])
    observation['reason'] = ("numeric_subtotal_not_trade_attribution" if total is not None
                             else "missing_or_invalid_fee_pnl_identity_currency")
    observation['net_excluding_funding'] = total
    return total


def _mark(exchange, symbol: str, fallback: float) -> float:
    """Last trade price, or the entry when the venue will not answer."""
    try:
        px = float((exchange.fetch_ticker(symbol) or {}).get("last") or 0)
        return px if px > 0 else fallback
    except Exception:
        return fallback


def reconcile_futures(exchange, journal: Journal, exclude_symbols=()) -> dict:
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

    excluded = set(exclude_symbols)
    j_open = {t["symbol"]: t for t in journal.open_trades() if t["symbol"] not in excluded}
    adopted = ghosts = aligned = 0

    # 1. adopt orphans
    for sym, p in ex_positions.items():
        if sym in excluded:
            continue  # durable entry recovery owns this exposure and its risk intent
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
            jamt = float(jt["amount"])
            if abs(jamt - contracts) > max(contracts * 0.01, 1e-9):
                # Shrinking to meet the venue means the venue closed part of
                # the line while we were down — a stop or TP that filled
                # short. That P&L is the strategy's; dropping it makes the
                # decay gate mismeasure a book it never saw win. The fill is
                # gone, so it is priced at the mark, as the ghost path does.
                # Growing is an accounting error, never a realized gain.
                pnl = 0.0
                total = None
                observation = {"basis": "position_alignment_without_fill_attribution"}
                if contracts < jamt:
                    total = venue_realized_pnl(
                        exchange, sym, str(jt["opened_at"] or ""), observation=observation)
                    if total is None:
                        entry = float(jt["entry_price"])
                        px = _mark(exchange, sym, entry)
                        direction = 1.0 if jt["side"] == "long" else -1.0
                        pnl = (px - entry) * direction * (jamt - contracts)
                # through _tx, not query(): query() runs unwrapped and its
                # UPDATE only lands if some later _tx on this thread happens
                # to commit it — until then the row is invisible and the
                # write lock is held across the ticker fetch and the stop
                # sweep below, both of which are REST round-trips.
                journal.align_trade_amount(
                    jt["id"], contracts, float(p.get("notional") or 0),
                    pnl_delta=round(pnl, 8),
                    pnl_total=None if total is None else round(total, 8), accounting=observation)
                booked = total if total is not None else pnl
                log.info(f"ALIGNED {sym}: amount {jt['amount']} → {contracts}"
                         + (f" | realized {booked:+.2f}"
                            f" ({'venue' if total is not None else 'marked'})"
                            if booked else ""))
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
               * float(jt["amount"]))
        journal.close_trade(jt["id"], exit_px, round(pnl, 8), "reconciled_ghost")
        log.warning(f"GHOST closed: {sym} was open in journal, absent on "
                    f"exchange → closed @{exit_px} pnl={pnl:+.2f}")
        ghosts += 1

    # 3. orphaned protective stops — see protective.py. Binance books a
    # reduceOnly stop as an ALGO order, so every cancel through
    # /fapi/v1/order failed and every trail ratchet leaked its predecessor.
    # 24 were live on this account, including nine BUY stops on SUI from a
    # short closed days earlier — inert while flat, but exactly the closing
    # side of the next short opened in that symbol.
    swept = failed_sweep = 0
    try:
        open_now = journal.open_trades()
        keep = {str(t["sl_order_id"]) for t in open_now if t.get("sl_order_id")}
        protected = {t["symbol"] for t in open_now if t.get("sl_order_id")}
        if not excluded:
            swept, failed_sweep = protective.sweep_orphans(
                exchange, keep, set(ex_positions.keys()), protected)
    except Exception as e:
        log.warning(f"orphan stop sweep failed: {e}")

    # 4. the reverse of the orphan sweep — a POSITION with no stop.
    # sweep_orphans covers a protective order whose position is gone. Nothing
    # covered the dangerous direction: a STOP_MARKET that partially fills
    # leaves a residue holding no protection, and aligning the journal's
    # amount down to that residue reports success while the position stands
    # naked. Observed live 2026-09-02 on UNI/USDT, where the residue was also
    # under the venue's minimum notional and so could not be re-armed at all.
    #
    # Detecting this is not protecting it. The original note here said the
    # UNI residue "was under the venue's minimum notional and so could not be
    # re-armed at all" — that was wrong. UNI's filters are MIN_NOTIONAL 5 and
    # LOT_SIZE minQty 1 step 1, and the residue was $5.74 on 1.0 coin; both
    # clear. `min_notional_usdt: 10` is OUR entry-sizing floor in
    # `risk.check_entry`, not the venue's, and the two were conflated.
    # Nothing ever attempted a stop.
    #
    # Two rules the venue forces:
    #   - size from the VENUE's position, never the journal's amount. The
    #     journal said 303.87 while 1.0 remained, and a stop for 303.87 is
    #     refused — which is how a residue comes to hold no protection.
    #   - a stop already through its level cannot be armed: Binance answers
    #     -2021 "order would immediately trigger". A long whose stop sits
    #     above the market needs CLOSING, and closing is the executor's
    #     call, not reconcile's. Those are counted as `unarmable` so the
    #     state stays visible rather than reading as a silent success.
    naked = None
    rearmed = unarmable = 0
    try:
        stops = protective.open_stops(exchange)
        # algo rows come back as UNIUSDT while positions read UNI/USDT:USDT;
        # `venue_key` is the one spelling both collapse to
        covered = {protective.venue_key(str(o.get("symbol") or ""))
                   for o in stops}
        bare = [sym for sym in ex_positions if sym not in excluded
                if protective.venue_key(sym) not in covered]
        naked = len(bare)
        j_now = {t["symbol"]: t for t in journal.open_trades()}
        for sym in bare:
            log.error(f"NAKED POSITION {sym}: no protective order on the "
                      f"venue — the exchange is not holding a stop for it")
            t = j_now.get(sym)
            sl = float((t or {}).get("stop_loss") or 0)
            amount = float(ex_positions[sym].get("contracts") or 0)
            if t is None or sl <= 0 or amount <= 0:
                unarmable += 1
                log.error(f"  {sym}: no journalled stop level to restore "
                          f"— it cannot be armed without inventing one")
                continue
            side = (t.get("side") or "long").lower()
            mark = _mark(exchange, sym, float(t.get("entry_price") or 0))
            through = sl >= mark if side == "long" else sl <= mark
            if through:
                unarmable += 1
                log.error(f"  {sym}: stop {sl:.6g} is already through the "
                          f"market {mark:.6g} — the venue refuses this with "
                          f"-2021. This position needs CLOSING, not a stop")
                continue
            close_side = "sell" if side == "long" else "buy"
            try:
                oid = protective.place_stop(exchange, sym, close_side,
                                            amount, sl)
            except Exception as e:
                unarmable += 1
                log.error(f"  {sym}: re-arm FAILED at {sl:.6g} on {amount}: "
                          f"{str(e)[:160]}")
                continue
            if not oid:
                unarmable += 1
                log.error(f"  {sym}: re-arm returned no order id")
                continue
            rearmed += 1
            log.warning(f"  {sym}: RE-ARMED stop {oid} at {sl:.6g} on "
                        f"{amount} (venue size, not the journal's)")
            try:
                journal.record_stop_order(t["id"], oid, sl)
            except Exception as e:
                log.error(f"  {sym}: stop {oid} is LIVE but not journalled "
                          f"({e}) — the orphan sweep may cancel it")
    except Exception as e:
        # if the venue will not say, the answer is unknown, never "protected"
        log.warning(f"protective coverage unreadable: {e}")

    summary = {"adopted": adopted, "ghosts": ghosts, "aligned": aligned,
               "stops_swept": swept, "stops_stuck": failed_sweep}
    if naked is not None:
        summary["naked"] = naked
        summary["rearmed"] = rearmed
        summary["unarmable"] = unarmable
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
                protective.cancel_stop(exchange, t["sl_order_id"], sym)
            order = exchange.create_order(
                sym, "market", side_close, float(t["amount"]),
                params={"reduceOnly": True})
            fill = float(order.get("average") or order.get("price") or 0)
            if fill <= 0:                      # async fills: confirm via ticker
                try:
                    tk = exchange.fetch_ticker(sym)
                    fill = float(tk.get("last") or 0)
                except Exception:
                    pass
            direction = 1.0 if t["side"] == "long" else -1.0
            pnl = ((fill - float(t["entry_price"])) * direction
                   * float(t["amount"]))
            journal.close_trade(t["id"], fill, round(pnl, 8), "panic")
            closed += 1
            log.warning(f"PANIC close {sym}: {t['amount']} @ ~{fill} "
                        f"(pnl {pnl:+.2f})")
        except Exception as e:
            log.error(f"PANIC close FAILED {sym}: {e} — will retry next cycle")
    if notifier and closed:
        notifier.send(f"🚨 PANIC: flattened {closed} position(s)")
    return closed
