"""Boot-time reconciliation — Luffy never trusts its own book blindly.

On startup, exchange truth wins:
- position on exchange, absent in journal  → ADOPT (marked 'adopted')
- open trade in journal, gone on exchange  → GHOST → close at market
- both present, amount drifted             → align journal to exchange

Spot mode: no short positions; reconcile only journal ghosts via price feed.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

from ..core.types import ClosedTrade, Position, Side, new_id, norm_symbol
from ..core.journal import Journal
from . import protective, position_mode as PM

log = logging.getLogger(__name__)
REARM_KEY = "reconcile_rearm_submitted"


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


_WINDOW_MS = 7 * 86_400_000 - 1_000     # Binance serves at most 7 days per history call
_FUNDING_HOURS = (0, 8, 16)             # UTC settlement hours


def _paged(fetch, since_ms: int, until_ms: int):
    """Every distinct row in [since, until], one <=7-day call at a time.

    None when a call fills its page or two rows share an id with different
    content: a truncated history is not evidence of completeness.
    """
    rows: dict = {}
    start = since_ms
    while start <= until_ms:
        end = min(start + _WINDOW_MS, until_ms)
        page = fetch(start, end)
        if not isinstance(page, list) or len(page) >= 1000:
            return None
        for row in page:
            key = str(row.get("id"))
            if not row.get("id") or (key in rows and rows[key] != row):
                return None
            rows[key] = row
        start = end + 1
    return list(rows.values())


def _funding_settlements(after_ms: int, through_ms: int) -> int:
    """Funding instants the venue owes a position held across (after, through]."""
    n, day = 0, after_ms // 86_400_000
    while day * 86_400_000 <= through_ms:
        for hour in _FUNDING_HOURS:
            at = day * 86_400_000 + hour * 3_600_000
            n += after_ms < at <= through_ms
        day += 1
    return n


def external_close_attribution(exchange, journal: Journal, trade: dict, *, now_ms: int | None = None):
    """The venue's own record of how an open journal trade was closed elsewhere.

    Returns (attribution, None) or (None, reason). Nothing here is estimated: the
    entry order is the one the journal recorded, the exit is the venue's reduce
    fills that bring that quantity back to zero, and P&L is the sum of the
    venue's per-fill realizedPnl less commission (funding is carried as separate
    evidence, as everywhere else in the booking contract). Anything ambiguous --
    an extra same-side fill, several exit orders, a history page that may be
    truncated, a non-USDT fee -- is refused so the caller keeps its fail-closed
    behaviour.
    """
    from .accounting import number, safe_fill
    from .booking import monetary_total, order_evidence
    try:
        symbol = trade["symbol"]
        entry_oid = None
        for row in journal.query("SELECT payload FROM trade_accounting_bookings "
                                 "WHERE trade_id=? ORDER BY id", (trade["id"],)):
            receipt = json.loads(row["payload"])
            if receipt.get("kind") == "entry":
                entry_oid = str((receipt.get("evidence") or {}).get("order_id") or "") or None
                break
        if not entry_oid:
            return None, "entry_order_id_not_recorded"
        now_ms = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
        opened_ms = int(datetime.fromisoformat(trade["opened_at"]).timestamp() * 1000)
        fills = _paged(lambda a, b: exchange.fetch_my_trades(symbol, since=a, limit=1000,
                                                             params={"until": b}),
                       opened_ms - 120_000, now_ms)
        if fills is None:
            return None, "fill_history_unavailable_or_truncated"
        fills.sort(key=lambda f: (int(f["timestamp"]), int(f["id"])))
        entry_side = "buy" if trade["side"] == "long" else "sell"
        exit_side = "sell" if entry_side == "buy" else "buy"
        entry = [f for f in fills if str(f.get("order")) == entry_oid]
        amount = float(trade["amount"])
        if (not entry or any(f["side"] != entry_side for f in entry)
                or not math.isclose(sum(float(f["amount"]) for f in entry), amount, rel_tol=1e-9)):
            return None, "entry_fills_do_not_match_journal"
        entry_ms = int(entry[0]["timestamp"])
        held, exits = amount, []
        for f in fills:
            if str(f.get("order")) == entry_oid or int(f["timestamp"]) < entry_ms:
                continue
            if f["side"] != exit_side:
                return None, "additional_same_side_fill_after_entry"
            held -= float(f["amount"])
            exits.append(f)
            if abs(held) <= 1e-9 * amount:
                break
        if not exits or abs(held) > 1e-9 * amount:
            return None, "position_not_closed_exactly_by_fills"
        orders = {str(f.get("order")) for f in exits}
        if len(orders) != 1:
            return None, "multiple_exit_orders_unsupported"
        closed_ms = int(exits[-1]["timestamp"])
        for other in journal.query("SELECT id,opened_at,closed_at FROM trades WHERE symbol=? AND id<>?",
                                   (symbol, trade["id"])):
            o0 = int(datetime.fromisoformat(other["opened_at"]).timestamp() * 1000)
            o1 = (int(datetime.fromisoformat(other["closed_at"]).timestamp() * 1000)
                  if other["closed_at"] else None)
            if o0 <= closed_ms and (o1 is None or o1 >= entry_ms):
                return None, "overlapping_journal_trade_on_symbol"
        evidence_fills = [safe_fill(f) for f in entry + exits]
        total = monetary_total(evidence_fills)
        if total is None:
            return None, "missing_or_invalid_pnl_fee_currency"
        exit_qty = sum(float(f["amount"]) for f in exits)
        exit_px = round(sum(float(f["price"]) * float(f["amount"]) for f in exits) / exit_qty, 10)
        funding = _funding_evidence(exchange, symbol, entry_ms, closed_ms)
        evidence = order_evidence(trade, {"id": orders.copy().pop()}, exits, exit_qty,
                                  int(exits[0]["timestamp"]) - 1, "venue_order_fills")
        evidence.update(
            purpose="external_close", requested_quantity=exit_qty,
            booked_pnl_basis="attributed_entry_and_exit_fills_excluding_funding",
            entry_order_id=entry_oid, entry_fills=[safe_fill(f) for f in entry],
            attributed_net_excluding_funding=total, funding=funding,
            attribution={"method": "journal_entry_order_then_reduce_fills_to_zero",
                         "exit_order_id": next(iter(orders)), "fill_count": len(exits)})
        return {"pnl": total, "exit_price": exit_px, "closed_ms": closed_ms,
                "closed_at": datetime.fromtimestamp(closed_ms / 1000, timezone.utc).isoformat(),
                "evidence": evidence}, None
    except Exception as exc:
        return None, "attribution_failed:" + type(exc).__name__


def _funding_evidence(exchange, symbol: str, after_ms: int, through_ms: int) -> dict:
    """Venue FUNDING_FEE income for the holding period; UNKNOWN unless complete."""
    out = {"basis": "venue_income_funding_fee", "status": "UNKNOWN", "funding_usdt": None,
           "expected_settlements": _funding_settlements(after_ms, through_ms), "rows": []}
    try:
        rows = _paged(lambda a, b: exchange.fetch_funding_history(symbol, since=a, limit=1000,
                                                                  params={"until": b}),
                      after_ms + 1, through_ms)
        if rows is None:
            out["reason"] = "funding_history_unavailable_or_truncated"
            return out
        kept = [r for r in rows if (r.get("info") or {}).get("incomeType", "FUNDING_FEE") == "FUNDING_FEE"
                and after_ms < int(r["timestamp"]) <= through_ms]
        if any((r.get("info") or {}).get("asset", "USDT") != "USDT" for r in kept):
            out["reason"] = "non_usdt_funding"
            return out
        out["rows"] = [{"id": str(r["id"]), "timestamp": int(r["timestamp"]),
                        "amount": float(r["amount"])} for r in kept]
        if len(kept) == out["expected_settlements"]:
            out.update(status="COMPLETE", funding_usdt=sum(r["amount"] for r in out["rows"]))
        else:
            out["reason"] = "funding_row_count_differs_from_settlements"
    except Exception as exc:
        out["reason"] = "funding_failed:" + type(exc).__name__
    return out


def reconcile_external_closes(exchange, journal: Journal, *, apply: bool = False, trade_ids=None) -> list:
    """Close open futures trades the venue shows flat, from the venue's own fills.

    Dry-run unless apply. Only symbols with no venue position, no open order and
    no protective stop are considered; every other open trade is left alone.
    Re-running is harmless (close_trade ignores a trade that is already closed).
    """
    report = []
    rows = exchange.fetch_positions()
    held = {norm_symbol(p["symbol"]) for p in rows if float(p.get("contracts") or 0) != 0}
    for trade in journal.open_trades():
        if trade["market_type"] != "futures" or (trade_ids and trade["id"] not in trade_ids):
            continue
        item = {"trade_id": trade["id"], "symbol": trade["symbol"], "status": "SKIPPED"}
        report.append(item)
        if trade["symbol"] in held:
            item["reason"] = "venue_position_present"
            continue
        if exchange.fetch_open_orders(trade["symbol"]) or protective.open_stops(
                exchange, trade["symbol"], strict=True):
            item["reason"] = "venue_orders_or_stops_present"
            continue
        attribution, why = external_close_attribution(exchange, journal, trade)
        if attribution is None:
            item["reason"] = why
            continue
        item.update(status="WOULD_CLOSE", pnl=attribution["pnl"], exit_price=attribution["exit_price"],
                    closed_at=attribution["closed_at"], funding=attribution["evidence"]["funding"])
        if apply:
            journal.close_trade(trade["id"], attribution["exit_price"], round(attribution["pnl"], 8),
                                "external_close", closed_at=attribution["closed_at"],
                                accounting=attribution["evidence"])
            item["status"] = "CLOSED"
    return report


def _last(exchange, symbol: str) -> float | None:
    """Venue last price, or None — a missing quote is never a price of zero."""
    try:
        px = float((exchange.fetch_ticker(symbol) or {}).get("last") or 0)
    except Exception:
        return None
    return px if math.isfinite(px) and px > 0 else None


def _mark(exchange, symbol: str, fallback: float) -> float:
    """Last trade price, or the entry when the venue will not answer."""
    px = _last(exchange, symbol)
    return fallback if px is None else px


def reconcile_futures(exchange, journal: Journal, exclude_symbols=(), *, verify=False) -> dict:
    """Align journal open trades with live exchange positions."""
    try:
        rows = exchange.fetch_positions()
        if not isinstance(rows, list):
            raise ValueError("invalid position snapshot")
        from . import position_mode as PM
        if (any(PM.venue_side(p) != 'BOTH' for p in rows)
                or any(PM.position_side(t) != 'BOTH' for t in journal.open_trades())):
            from .reconcile_native import run
            return run(exchange, journal, rows, exclude_symbols)
        if verify:
            seen = set()
            for p in rows:
                amount = float(p["contracts"])
                if not math.isfinite(amount) or amount < 0:
                    raise ValueError("invalid venue position size")
                if amount > 0:
                    key = norm_symbol(p["symbol"])
                    if key in seen or p.get("side") not in ("long", "short"):
                        raise ValueError("contradictory venue position ownership")
                    seen.add(key)
        ex_positions = {
            norm_symbol(p["symbol"]): p
            for p in rows
            if float(p.get("contracts") or 0) > 0
        }
    except Exception as e:
        log.error(f"reconcile: cannot fetch positions ({e}) — keeping journal as-is")
        if verify and str(e) == "contradictory venue position ownership":
            return {"adopted": 0, "ghosts": 0, "aligned": 0,
                    "positions_readable": True,
                    "safety_issues": ["contradictory_venue_ownership"]}
        return {"adopted": 0, "ghosts": 0, "aligned": 0,
                "error": type(e).__name__, "positions_readable": False}

    excluded = set(exclude_symbols)
    journal_rows = [t for t in journal.open_trades() if t["symbol"] not in excluded]
    j_open = {t["symbol"]: t for t in journal_rows}
    # Exact identities already recorded by the entry boundary cannot be
    # silently projected onto another account or loaded venue instrument.
    for row in journal_rows:
        try:
            binding = json.loads(row.get('entry_identity_json') or '{}').get('execution_binding')
            if binding:
                from ..data.feed import execution_account_scope
                cap = binding['capability']
                market = exchange.market(row['symbol'])
                if (getattr(exchange, 'id', None) != 'binanceusdm'
                        or execution_account_scope(exchange) != cap['account_scope']
                        or market.get('id') != cap['record']['instrument_id']['venue_symbol']):
                    raise ValueError('canonical_binding_mismatch')
        except (ValueError, TypeError, KeyError, AttributeError):
            return {'adopted':0,'ghosts':0,'aligned':0,'positions_readable':True,
                    'safety_issues':['canonical_binding_mismatch']}
    exact_open = {}
    for row in journal_rows:
        binding = json.loads(row.get('entry_identity_json') or '{}').get('execution_binding')
        key = norm_symbol(row['symbol'])
        if binding:
            expected = binding['capability']['record']['instrument_id']['venue_symbol']
            try:
                venue_ids = {}
                for sym,p in ex_positions.items():
                    loaded_id = exchange.market(p['symbol']).get('id')
                    if not loaded_id or (p.get('info') or {}).get('symbol', loaded_id) != loaded_id:
                        raise ValueError('contradictory venue instrument identity')
                    venue_ids[sym] = loaded_id
                matches = [sym for sym,venue_id in venue_ids.items() if venue_id == expected]
            except Exception:
                return {'adopted':0,'ghosts':0,'aligned':0,'positions_readable':True,
                        'safety_issues':['canonical_position_identity_unverified']}
            if len(matches) > 1:
                return {'adopted':0,'ghosts':0,'aligned':0,'positions_readable':True,
                        'safety_issues':['contradictory_venue_ownership']}
            if matches:
                key = matches[0]
            elif key in ex_positions:
                # A symbol alias collision is not a canonical position match.
                return {'adopted':0,'ghosts':0,'aligned':0,'positions_readable':True,
                        'safety_issues':['canonical_position_identity_unverified']}
        if key in exact_open:
            return {'adopted':0,'ghosts':0,'aligned':0,'positions_readable':True,
                    'safety_issues':['contradictory_journal_ownership']}
        exact_open[key] = row
    j_open = exact_open
    if verify:
        ownership = []
        if len(j_open) != len(journal_rows):
            ownership.append("contradictory_journal_ownership")
        for sym, p in ex_positions.items():
            jt = j_open.get(sym)
            if jt and jt["side"] != p["side"]:
                ownership.append("contradictory_position_side:" + sym)
        if ownership:
            return {"adopted": 0, "ghosts": 0, "aligned": 0,
                    "positions_readable": True, "position_count": len(ex_positions),
                    "safety_issues": ownership}
    adopted = ghosts = aligned = 0
    pending_rearms = {}
    if verify:
        try:
            pending_rearms = json.loads(journal.kv_get(REARM_KEY, "{}"))
            if not isinstance(pending_rearms, dict) or any(
                    not isinstance(sym, str) or not isinstance(record, dict)
                    or not isinstance(record.get("trade_id"), str)
                    for sym, record in pending_rearms.items()):
                raise ValueError("invalid rearm evidence")
        except Exception as e:
            return {"adopted": 0, "ghosts": 0, "aligned": 0,
                    "positions_readable": True, "position_count": len(ex_positions),
                    "safety_issues": ["protection_rearm_evidence_unreadable"],
                    "error": type(e).__name__}

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
            journal.add_trade(pos, accounting={
                "basis": "reconcile_adoption", "purpose": "adopted",
                "venue_entry_price": entry if entry > 0 else None,
                "entry_price_status": "VENUE_REPORTED" if entry > 0 else "UNKNOWN",
                "exposure_source": "venue_position_snapshot",
                "venue_contracts": contracts})
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
                        quote = _last(exchange, sym)
                        px = entry if quote is None else quote
                        direction = 1.0 if jt["side"] == "long" else -1.0
                        pnl = (px - entry) * direction * (jamt - contracts)
                        # A local estimate, not a venue fact: say so and say
                        # what price it was priced from.
                        observation.update(
                            pnl_value_class="DERIVED_ESTIMATE" if quote is not None else "UNKNOWN",
                            pnl_status="UNKNOWN" if quote is None else "ESTIMATE",
                            pnl_price_source=("journal_entry_not_a_fill" if quote is None
                                              else "ticker_last_not_a_fill"),
                            estimated_pnl_usdt=round(pnl, 8))
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
        # The venue's own fills for this trade, when they attribute exactly, beat
        # any mark: nothing below is used then.
        attribution, _why = external_close_attribution(exchange, journal, jt)
        if attribution is not None:
            journal.close_trade(jt["id"], attribution["exit_price"], round(attribution["pnl"], 8),
                                "external_close", closed_at=attribution["closed_at"],
                                accounting=attribution["evidence"])
            log.warning(f"EXTERNAL CLOSE booked from venue fills: {sym} "
                        f"pnl={attribution['pnl']:+.2f}")
            ghosts += 1
            continue
        if sym not in mark_cache:
            mark_cache[sym] = _last(exchange, sym)
        quote = mark_cache[sym]
        # No quote: the venue-flat fact stands, but the exit price and P&L
        # are UNKNOWN. Entry is a placeholder for the NOT NULL column, not a
        # fill, and a zero price must never be booked as a total loss.
        exit_px = float(jt["entry_price"]) if quote is None else quote
        direction = 1.0 if jt["side"] == "long" else -1.0
        pnl = 0.0 if quote is None else ((exit_px - float(jt["entry_price"])) * direction
                                         * float(jt["amount"]))
        journal.close_trade(jt["id"], exit_px, round(pnl, 8), "reconciled_ghost", accounting={
            "basis": "reconcile_ghost_unpriced" if quote is None else "reconcile_ghost_mark_estimate",
            "purpose": "ghost_close", "exposure_source": "venue_position_snapshot",
            "pnl_status": "UNKNOWN" if quote is None else "ESTIMATE",
            "pnl_value_class": "UNKNOWN" if quote is None else "DERIVED_ESTIMATE",
            "exit_price_source": "journal_entry_not_a_fill" if quote is None else "ticker_last_not_a_fill",
            "estimated_pnl_usdt": None if quote is None else round(pnl, 8)})
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
    sweep_error = None
    try:
        open_now = journal.open_trades()
        keep = {str(t["sl_order_id"]) for t in open_now if t.get("sl_order_id")}
        keep.update(str(v.get("order_id")) for sym, v in pending_rearms.items()
                    if sym in ex_positions and isinstance(v, dict) and v.get("order_id"))
        protected = {t["symbol"] for t in open_now if t.get("sl_order_id")}
        if not excluded:
            if verify:
                swept, failed_sweep = protective.sweep_orphans(
                    exchange, keep, set(ex_positions.keys()), protected, strict=True)
            else:
                swept, failed_sweep = protective.sweep_orphans(
                    exchange, keep, set(ex_positions.keys()), protected)
    except Exception as e:
        log.warning(f"orphan stop sweep failed: {e}")
        sweep_error = getattr(e, "reason", type(e).__name__)

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
    protection_error = None
    rearmed = unarmable = 0
    bare = []
    newly_rearmed = set()
    try:
        stops = protective.open_stops(exchange, strict=verify)
        if not stops.complete:
            raise protective.ProtectiveSnapshotIncomplete(
                stops.reason or "global_stop_listing_incomplete")
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
            if verify and sym in pending_rearms:
                continue  # submitted evidence forbids a duplicate placement
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
            if verify:
                pending_rearms[sym] = {"trade_id": t["id"], "order_id": None,
                                     "amount": amount, "stop_price": sl}
                journal.kv_set(REARM_KEY, json.dumps(pending_rearms, allow_nan=False))
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
            if verify:
                pending_rearms[sym]["order_id"] = oid
                journal.kv_set(REARM_KEY, json.dumps(pending_rearms, allow_nan=False))
            rearmed += 1
            newly_rearmed.add(sym)
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
        protection_error = getattr(e, "reason", type(e).__name__)

    summary = {"adopted": adopted, "ghosts": ghosts, "aligned": aligned,
               "stops_swept": swept, "stops_stuck": failed_sweep}
    if sweep_error:
        summary["orphan_sweep_reason"] = sweep_error
    if protection_error:
        summary["protection_snapshot_reason"] = protection_error
    if verify:
        issues = []
        listed = None
        try:
            listed = protective.open_stops(exchange, strict=True)
        except Exception:
            issues.append("protection_snapshot_unreadable")
        if sweep_error or failed_sweep:
            issues.append("orphan_stop_sweep_unresolved")
        if protection_error:
            issues.append("protection_snapshot_unreadable")
        if unarmable:
            issues.append("protection_cannot_restore")
        if listed is not None and any(
                protective.venue_key(s.get("symbol", "")) not in {
                    protective.venue_key(sym) for sym in ex_positions}
                for s in listed):
            issues.append("orphan_stop_sweep_unresolved")
        # A placement response is not confirmation. Only a listed adequate
        # stop proves protection; a first-pass submission stays transient.
        for sym, p in ex_positions.items():
            if sym in excluded:
                continue
            try:
                matches = protective.open_stops(exchange, sym, strict=True)
                amount = float(p["contracts"])
                expected = float(next((t.get("stop_loss") or 0 for t in journal.open_trades()
                                       if t["symbol"] == sym), 0))
                adequate_stops = [s for s in matches if protective.protection_match(
                    exchange, sym, p["side"], amount, expected, s).matches]
                adequate = bool(adequate_stops)
                if adequate:
                    submitted = pending_rearms.get(sym)
                    if not submitted:
                        continue
                    submitted_id = str(submitted.get("order_id") or "")
                    journalled = any(str(t.get("sl_order_id") or "") == submitted_id
                                     for t in journal.open_trades() if t["symbol"] == sym)
                    listed_submitted = any(str(s["id"]) == submitted_id
                                           for s in adequate_stops)
                    if submitted_id and journalled and listed_submitted:
                        pending_rearms.pop(sym, None)
                    elif not submitted_id or not journalled:
                        # Otherwise a later sweep could cancel the stop after
                        # this submission record is cleared.
                        issues.append("protection_cannot_restore")
                    elif sym in newly_rearmed:
                        issues.append("protection_awaiting_verification")
                    else:
                        issues.append("protection_cannot_restore")
                elif sym in pending_rearms and pending_rearms[sym].get("order_id") and sym in bare:
                    if sym in newly_rearmed:
                        issues.append("protection_awaiting_verification")
                    else:
                        issues.append("position_unprotected:" + sym)
                else:
                    issues.append("position_unprotected:" + sym)
            except Exception:
                issues.append("protection_snapshot_unreadable:" + sym)
        for sym in list(pending_rearms):
            if sym not in ex_positions:
                pending_rearms.pop(sym)
        journal.kv_set(REARM_KEY, json.dumps(pending_rearms, allow_nan=False))
        summary.update(positions_readable=True,
                       position_count=len(ex_positions), safety_issues=issues)
    if naked is not None:
        summary["naked"] = naked
        summary["rearmed"] = rearmed
        summary["unarmable"] = unarmable
    if any(summary.values()):
        journal.log_control_event("reconcile", "luffy", detail=summary)
    log.info(f"reconcile: {summary}")
    return summary


class FlattenResult(int):
    """`flatten_all` outcome. Equal to the number of positions confirmed closed,
    so callers that only count keep working; `complete` is the part that
    matters: False means exposure may remain and the panic must stay pending.
    """
    complete: bool
    remaining: tuple
    unjournaled: tuple

    def __new__(cls, closed, remaining=(), unjournaled=()):
        self = super().__new__(cls, closed)
        self.remaining = tuple(remaining)
        self.unjournaled = tuple(unjournaled)
        self.complete = not self.remaining and not self.unjournaled
        return self


FLATTEN_ATTEMPTS = 3


def _venue_amounts(exchange) -> dict | None:
    """{normalised symbol: open contracts} from the venue, or None if the
    snapshot is unreadable or invalid. Flat symbols are simply absent."""
    try:
        rows = exchange.fetch_positions()
        if not isinstance(rows, list):
            return None
        out: dict = {}
        for p in rows:
            amount = float(p.get("contracts") or 0)
            if not math.isfinite(amount) or amount < 0:
                return None
            if amount > 0:
                key = PM.position_key(p['symbol'], PM.venue_side(p))
                if key in out:return None
                out[key] = amount
        return out
    except Exception:
        return None


def flatten_all(exchange, journal: Journal, notifier=None) -> FlattenResult:
    """PANIC: reduce-only market-close every journal-open futures position.

    The venue protective stop is released only after the venue itself shows
    the position flat; a refused, timed-out or partial close leaves the stop
    standing. Ambiguous submissions are never assumed to have failed or
    succeeded: the venue position is re-read and the remaining size, not the
    request outcome, drives the next attempt (reduce-only bounds every retry,
    so a retry cannot add or flip exposure). Venue positions with no journal
    row are reported, never closed here (reconcile adopts them).

    Returns a FlattenResult: the count confirmed closed plus `.complete` /
    `.remaining` / `.unjournaled`. A caller must not treat panic as served
    while `.complete` is False.
    """
    open_trades = [t for t in journal.open_trades()
                   if t["market_type"] == "futures"]
    closed = 0
    remaining: list = []
    for t in open_trades:
        sym = t["symbol"]
        key = PM.position_key(sym, PM.position_side(t))
        side_close = "sell" if t["side"] == "long" else "buy"
        order = None
        flat = False
        last_error = None
        try:
            for _ in range(FLATTEN_ATTEMPTS):
                venue = _venue_amounts(exchange)
                size = float(t["amount"])
                if PM.position_side(t) != 'BOTH':
                    if venue is None:
                        break
                    if venue.get(key, 0.) <= 0:
                        flat = True; break
                if venue is not None and venue.get(key, 0.0) > 0:
                    size = venue[key]          # venue truth, not the journal
                elif venue is not None and order is not None:
                    flat = True                # our order left nothing behind
                    break
                if venue is None and order is not None:
                    break                      # sent, cannot read back: never resend blind
                try:
                    order = exchange.create_order(
                        sym, "market", side_close, size,
                        params=PM.order_params(t, reduce_only=True))
                except Exception as e:          # rejected / timeout / ambiguous
                    last_error = e
                    venue = _venue_amounts(exchange)
                    if venue is not None and venue.get(key, 0.0) <= 0:
                        # nothing left: either filled despite the error or
                        # already flat. Either way no exposure and no order
                        # response to book from.
                        flat = True
                        break
                    continue                    # still exposed: re-read, retry
                venue = _venue_amounts(exchange)
                if venue is not None and venue.get(key, 0.0) <= 0:
                    flat = True
                    break
                # venue unreadable or still exposed (partial): loop re-reads
            if not flat:
                remaining.append(sym)
                log.error(f"PANIC close INCOMPLETE {sym}: venue exposure not "
                          f"confirmed flat ({type(last_error).__name__ if last_error else 'unconfirmed'}) "
                          f"— protective stop left standing")
                continue
            if order is None:
                # Venue shows flat without an order of ours: the position was
                # already gone. No fill to book; reconcile closes the journal
                # row from venue P&L. Not exposure, so not "remaining".
                log.warning(f"PANIC {sym}: venue already flat; journal row "
                            f"left to reconcile")
                continue
            fill = float(order.get("average") or order.get("price") or 0)
            source = "order_response_price"
            if fill <= 0:                      # async fills: confirm via ticker
                fill = _last(exchange, sym) or 0.0
                source = "ticker_last_not_a_fill"
            if fill <= 0:
                # The order went out; its price is UNKNOWN, never zero.
                fill, source = float(t["entry_price"]), "journal_entry_not_a_fill"
                pnl, pnl_status = 0.0, "UNKNOWN"
            else:
                direction = 1.0 if t["side"] == "long" else -1.0
                pnl = ((fill - float(t["entry_price"])) * direction
                       * float(t["amount"]))
                pnl_status = "ESTIMATE"
            journal.close_trade(t["id"], fill, round(pnl, 8), "panic", accounting={
                "basis": "panic_order_unconfirmed", "purpose": "panic_exit",
                "order_id": str(order.get("id") or ""), "side": side_close,
                "requested_quantity": float(t["amount"]),
                "exit_price_source": source, "pnl_status": pnl_status,
                "pnl_value_class": "UNKNOWN" if pnl_status == "UNKNOWN" else "DERIVED_ESTIMATE",
                "protective_algo_id": t.get("sl_order_id") or None})
            closed += 1
            log.warning(f"PANIC close {sym}: {t['amount']} @ ~{fill} "
                        f"(pnl {pnl:+.2f})")
            if t.get("sl_order_id"):           # flat now: the stop is an orphan
                protective.cancel_stop(exchange, t["sl_order_id"], sym)
        except Exception as e:
            remaining.append(sym)
            log.error(f"PANIC close FAILED {sym}: {type(e).__name__}: {e}")
    journal_keys = {PM.position_key(t['symbol'], PM.position_side(t)) for t in open_trades}
    after = _venue_amounts(exchange)
    unjournaled = sorted((k for k in (after or {}) if k not in journal_keys), key=str)
    result = FlattenResult(closed, remaining, unjournaled)
    if not result.complete:
        try:
            journal.log_control_event("panic_flatten_incomplete", "luffy", detail={
                "closed": int(result), "remaining": list(result.remaining),
                "unjournaled_venue_positions": list(result.unjournaled)})
        except Exception:
            log.exception("panic incomplete event not recorded")
    if notifier and (closed or not result.complete):
        msg = f"🚨 PANIC: flattened {closed} position(s)"
        if not result.complete:
            msg += (f"; NOT FLAT: {list(result.remaining) + list(result.unjournaled)}"
                    " — protective stops left standing")
        notifier.send(msg)
    return result
