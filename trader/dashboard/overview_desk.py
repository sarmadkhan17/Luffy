"""Overview desk reads: realized-P&L curve, recent closes, latest scan, research counts.

Read-only journal queries behind GET /owner-api/v1/desk. Journal-booked values only:
a trade whose P&L is UNKNOWN is counted and left out of the curve, never zeroed.
"""
from __future__ import annotations

from .economics import booking_receipts, journal_pnl

RECENT_CLOSES = 10
SCAN_LIMIT = 60
RESEARCH_TABLES = ("research_combos", "research_candidates", "research_tests", "research_questions")


def realized_curve(journal) -> dict:
    rows = journal.query("SELECT id, symbol, side, realized_pnl, closed_at, close_reason, "
                         "strategy_name FROM trades WHERE status='closed' AND closed_at IS NOT NULL "
                         "ORDER BY closed_at, id")
    points, unknown, estimated = [], 0, 0
    total = 0.0
    for r in rows:
        t = dict(r)
        p = journal_pnl(t, booking_receipts(journal, t["id"]))
        if p["value"] is None:
            unknown += 1
            continue
        estimated += p["status"] == "DERIVED_ESTIMATE"
        total += p["value"]
        points.append({"trade_id": str(t["id"]), "closed_at": t["closed_at"], "symbol": t["symbol"],
                       "side": t["side"], "pnl": round(p["value"], 4), "cumulative": round(total, 4),
                       "status": p["status"], "close_reason": t.get("close_reason"),
                       "strategy_name": t.get("strategy_name") or None})
    return {"points": points, "closed_trades": len(rows), "unknown_pnl_trades": unknown,
            "estimated_pnl_trades": estimated, "total": round(total, 4) if points else None,
            "last_closes": points[-RECENT_CLOSES:],
            "source": "journal closed trades, journal-booked realized P&L (not venue net accounting)"}


def latest_scan(journal) -> dict | None:
    head = journal.query("SELECT scan_id, ts FROM decisions WHERE scan_id IS NOT NULL AND scan_id<>'' "
                         "ORDER BY ts DESC LIMIT 1")
    if not head:
        return None
    scan_id = head[0]["scan_id"]
    rows = journal.query("SELECT id, symbol, action, score, threshold, confidence, executed, skip_reason, "
                         "ts FROM decisions WHERE scan_id=? ORDER BY symbol LIMIT ?", (scan_id, SCAN_LIMIT))
    coins = [{"decision_id": str(r["id"]), "symbol": r["symbol"], "action": r["action"],
              "score": r["score"], "threshold": r["threshold"], "confidence": r["confidence"],
              "executed": bool(r["executed"]), "skip_reason": r["skip_reason"] or None} for r in rows]
    return {"scan_id": scan_id, "observed_at": max(r["ts"] for r in rows) if rows else head[0]["ts"],
            "coins": coins, "traded": sum(c["executed"] for c in coins),
            "source": "journal decisions of the newest scan (score vs the recorded threshold)"}


def research_counts(journal, cfg: dict | None) -> dict:
    counts = {}
    for table in RESEARCH_TABLES:
        try:
            counts[table.removeprefix("research_")] = journal.query(f"SELECT COUNT(*) n FROM {table}")[0]["n"]
        except Exception:
            counts[table.removeprefix("research_")] = None
    states = {}
    try:
        for r in journal.query("SELECT state, COUNT(*) n FROM research_candidates GROUP BY state"):
            states[r["state"]] = r["n"]
    except Exception:
        states = None
    research = (cfg or {}).get("research") or {}
    return {"counts": counts, "candidates_by_state": states,
            "referee_enabled": research.get("referee"), "handoff_enabled": research.get("handoff"),
            "source": "journal research ledger tables and config.research flags"}


def desk(journal, cfg: dict | None, now_iso: str) -> dict:
    errors: dict[str, str] = {}
    out = {"generated_at": now_iso, "errors": errors}
    for name, fn in (("realized", lambda: realized_curve(journal)),
                     ("scan", lambda: latest_scan(journal)),
                     ("research", lambda: research_counts(journal, cfg))):
        try:
            out[name] = fn()
        except Exception as e:                      # a read never breaks the page
            out[name] = None
            errors[name] = f"{name}_unreadable:{type(e).__name__}"
    return out


def research_board(journal, cfg: dict | None) -> dict:
    """Everything the Research tab shows, from the research ledger. Discovery
    evidence only: nothing here is an admission or a held-out result."""
    def q(sql, args=()):
        try:
            return [dict(r) for r in journal.query(sql, args)]
        except Exception:
            return None
    combos = q("SELECT hash, tf, geo, k, round, parts, status, verdict, label, reason, consistency_p, "
               "median_pf, total_pct, max_dd_pct, trades, scored_symbols, testable, "
               "(entry_long IS NOT NULL AND entry_long<>'') AS has_long, "
               "(entry_short IS NOT NULL AND entry_short<>'') AS has_short, created_at "
               "FROM research_combos ORDER BY rowid DESC LIMIT 5000")
    total = q("SELECT COUNT(*) n FROM research_combos")
    verdicts = q("SELECT status, verdict, COUNT(*) n FROM research_combos GROUP BY status, verdict")
    batches = q("SELECT COUNT(*) n, SUM(ok=0) failed, MAX(started) last_started FROM research_batches")
    last = q("SELECT started, finished, tf, geo, round, n, ok, error FROM research_batches ORDER BY started DESC LIMIT 1")
    controls = q("SELECT tf, \"window\" AS win, consistency_p, powered, status FROM research_controls")
    cand = q("SELECT state, COUNT(*) n FROM research_candidates GROUP BY state")
    tests = q("SELECT COUNT(*) n, SUM(rejected) rejected FROM research_tests")
    research = (cfg or {}).get("research") or {}
    stores = {}
    for name in ("research_questions", "research_runs", "research_results", "research_bank_objects", "research_registrations", "research_evidence"):
        n = q(f"SELECT COUNT(*) n FROM {name}")
        stores[name.removeprefix("research_")] = n[0]["n"] if n else None
    return {"stores": stores, "combos": combos, "total": total[0]["n"] if total else None,
            "truncated": bool(combos and total and len(combos) < total[0]["n"]),
            "verdicts": verdicts, "batches": batches[0] if batches else None,
            "last_batch": last[0] if last else None,
            "controls": {"total": len(controls), "powered": sum(1 for c in controls if c["powered"])} if controls is not None else None,
            "candidates_by_state": {c["state"]: c["n"] for c in cand} if cand is not None else None,
            "held_out_looks": tests[0] if tests else None,
            "referee_enabled": research.get("referee"), "handoff_enabled": research.get("handoff"),
            "horizons": research.get("horizons"),
            "source": "journal research ledger (research_combos, batches, controls, candidates, tests); "
                      "discovery evidence only"}


def _describe(spec: dict | None, kind: str) -> str:
    """A factual sentence built only from the stored spec fields."""
    if not spec:
        return f"Legacy {kind} strategy; no declarative spec is stored."
    exit_ = spec.get("exit") or {}
    stop, trail = exit_.get("stop") or {}, exit_.get("trail") or {}
    d = spec.get("direction")
    way = "both directions" if d == "both" else f"{d} only" if d else "an unrecorded direction"
    bits = [f"Trades {way} on {spec.get('timeframe', 'unknown')} bars"]
    if spec.get("regime_filter"):
        bits.append("only in " + ", ".join(spec["regime_filter"]).lower().replace("_", " "))
    if stop.get("kind") and stop["kind"] != "none":
        bits.append(f"stop {stop.get('mult', '?')}× {stop['kind']}")
    if trail.get("kind") and trail["kind"] != "none":
        bits.append(f"trail {trail.get('mult', '?')}× {trail['kind']} armed at {trail.get('arm_at_r', '?')}R")
    return "; ".join(bits) + "."


def strategy_board(journal, now_ts: float) -> dict:
    from . import owner_reads
    registry = owner_reads.strategies(journal)
    trades: dict[str, list] = {}
    for r in journal.query("SELECT id, strategy_id, symbol, side, status, opened_at, closed_at, "
                           "realized_pnl, close_reason FROM trades ORDER BY opened_at"):
        trades.setdefault(r["strategy_id"] or "", []).append(dict(r))
    out = []
    for s in registry:
        ts = trades.get(s["id"], [])
        closed, unknown = [], 0
        for t in ts:
            if t["status"] != "closed" or not t["closed_at"]:
                continue
            p = journal_pnl(t, booking_receipts(journal, t["id"]))
            if p["value"] is None:
                unknown += 1
                continue
            closed.append({"trade_id": str(t["id"]), "at": t["closed_at"], "symbol": t["symbol"], "side": t["side"],
                           "pnl": round(p["value"], 4), "status": p["status"]})
        closed.sort(key=lambda c: c["at"])
        cum, curve = 0.0, []
        for c in closed:
            cum += c["pnl"]
            curve.append(round(cum, 4))
        wins = [c["pnl"] for c in closed if c["pnl"] > 0]
        losses = [-c["pnl"] for c in closed if c["pnl"] < 0]
        from datetime import datetime, timezone
        cutoff = datetime.fromtimestamp(now_ts - 30 * 86400, timezone.utc).isoformat()
        spec = s.get("spec") if isinstance(s.get("spec"), dict) else None
        out.append({
            "id": s["id"], "name": s["name"], "kind": s["kind"], "state": s["state"], "origin": s.get("origin"),
            "generation": s.get("generation"), "parent_id": s.get("parent_id") or None,
            "created_at": s.get("created_at"), "state_changed_at": s.get("state_changed_at"),
            "retire_reason": s.get("retire_reason") or None, "spec_sha256": s.get("spec_sha256"),
            "description": _describe(spec, s["kind"]),
            "provenance": (spec or {}).get("provenance"), "timeframe": (spec or {}).get("timeframe"),
            "direction": (spec or {}).get("direction"),
            "open_trades": sum(1 for t in ts if t["status"] != "closed"),
            "closed_trades": len(closed), "unknown_pnl_trades": unknown,
            "wins": len(wins), "pnl_total": round(sum(c["pnl"] for c in closed), 4) if closed else None,
            "pnl_30d": round(sum(c["pnl"] for c in closed if c["at"] >= cutoff), 4) if closed else None,
            "trades_30d": sum(1 for c in closed if c["at"] >= cutoff),
            "profit_factor": round(sum(wins) / sum(losses), 3) if losses else None,
            "first_trade_at": ts[0]["opened_at"] if ts else None,
            "last_trade_at": (ts[-1]["closed_at"] or ts[-1]["opened_at"]) if ts else None,
            "curve": curve[-120:], "recent": list(reversed(closed[-5:])),
            "registry_stats": s.get("registry_stats"),
        })
    return {"strategies": out, "source": "journal strategies registry + journal-booked trades per strategy id; "
            "not venue-verified, not an admission"}


# ── Operations activity log ───────────────────────────────────────────────────
LOG_DAYS = 7
LEARNING_KINDS = ("postmortem", "rent_verdict", "observation_window")
SAFETY_TONE = {"supervisor_recovery": "warn", "reconcile": "grey", "news_blackout": "warn", "news_clear": "ok",
               "macro_freeze": "warn", "macro_clear": "ok"}


def _load_json(raw):
    import json
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None


def operations_log(journal, now_ts: float, days: int = LOG_DAYS) -> dict:
    """Everything LUFFY recorded doing, newest first: scans rolled up per UTC
    hour (a pass every minute or two would bury everything else), orders,
    safety/control events (consecutive same-hour repeats merged), strategy
    lifecycle and learning events. Read-only journal queries."""
    from datetime import datetime, timezone
    from . import owner_reads
    cutoff = datetime.fromtimestamp(now_ts - days * 86400, timezone.utc).isoformat()
    events: list[dict] = []

    def hour(ts: str) -> str:
        return ts[:13]

    # scans
    rows = journal.query("SELECT id, ts, symbol, action, score, threshold, confidence, executed, skip_reason, "
                         "scan_id FROM decisions WHERE ts >= ? ORDER BY ts", (cutoff,))
    by_hour: dict[str, dict] = {}
    for r in rows:
        h = by_hour.setdefault(hour(r["ts"]), {"scans": {}, "rows": 0})
        h["rows"] += 1
        h["scans"].setdefault(r["scan_id"] or r["id"], []).append(r)
    for hk, h in by_hour.items():
        scans = sorted(h["scans"].values(), key=lambda g: g[-1]["ts"])
        last = scans[-1]
        flat = [r for g in scans for r in g]
        directional = sum(1 for r in flat if r["action"] not in ("HOLD", None))
        executed = sum(1 for r in flat if r["executed"])
        blocked = sorted({r["skip_reason"] for r in flat if r["skip_reason"]})
        events.append({
            "id": f"scan:{hk}", "at": last[-1]["ts"], "type": "Scan", "n": len(scans),
            "what": f"{len(scans)} scan pass{'es' if len(scans) != 1 else ''} · {len(last)} coins each · "
                    f"{directional} directional · {executed} traded",
            "who": "Attention + analysts",
            "result": "Traded" if executed else ("Blocked" if blocked and directional else "All HOLD"),
            "tone": "ok" if executed else "warn" if blocked and directional else "grey",
            "facts": {"hour_utc": hk + ":00", "passes": len(scans), "decisions": len(flat),
                      "directional": directional, "executed": executed, "block_reasons": blocked[:6]},
            "coins": [[g["symbol"], g["score"], g["threshold"], g["action"], bool(g["executed"]), g["skip_reason"] or None]
                      for g in sorted(last, key=lambda g: g["symbol"])],
            "scan_id": last[0]["scan_id"], "last_scan_at": last[-1]["ts"]})

    # orders
    for t in journal.query("SELECT id, symbol, side, status, opened_at, closed_at, strategy_id, strategy_name, amount, "
                           "entry_price, exit_price, stop_loss, close_reason, realized_pnl, leverage FROM trades "
                           "WHERE opened_at >= ? OR closed_at >= ? ORDER BY opened_at", (cutoff, cutoff)):
        base = t["symbol"].split("/")[0]
        who = t["strategy_name"] or t["strategy_id"] or "LUFFY"
        facts = {"trade_id": str(t["id"]), "symbol": t["symbol"], "side": t["side"], "amount": t["amount"],
                 "entry_price": t["entry_price"], "leverage": t["leverage"], "journal_stop": t["stop_loss"]}
        if t["opened_at"] and t["opened_at"] >= cutoff:
            events.append({"id": f"open:{t['id']}", "at": t["opened_at"], "type": "Order", "n": 1,
                           "what": f"Opened {str(t['side']).upper()} {base}-USDT · {t['amount']} @ {t['entry_price']}",
                           "who": who, "result": "Entered", "tone": "ok", "facts": facts})
        if t["closed_at"] and t["closed_at"] >= cutoff:
            pnl = t["realized_pnl"]
            events.append({"id": f"close:{t['id']}", "at": t["closed_at"], "type": "Order", "n": 1,
                           "what": f"Closed {str(t['side']).upper()} {base}-USDT @ {t['exit_price']} · {t['close_reason'] or 'reason not recorded'}",
                           "who": who, "result": "Won" if (pnl or 0) > 0 else "Lost" if (pnl or 0) < 0 else "Flat",
                           "tone": "ok" if (pnl or 0) > 0 else "bad" if (pnl or 0) < 0 else "grey",
                           "facts": {**facts, "exit_price": t["exit_price"], "close_reason": t["close_reason"],
                                     "realized_pnl_journal": pnl}})

    # safety / control events, merged per (hour, event)
    merged: dict[tuple, dict] = {}
    cooldowns = 0
    for e in journal.query("SELECT id, ts, event, from_state, to_state, actor, detail FROM control_events "
                           "WHERE ts >= ? ORDER BY id", (cutoff,)):
        if e["event"] == "signal_cooldown":
            cooldowns += 1
            continue
        key = (hour(e["ts"]), e["event"], e["actor"], e["from_state"], e["to_state"])
        m = merged.get(key)
        d = _load_json(e["detail"])
        if m is None:
            merged[key] = m = {"id": f"ctl:{e['id']}", "at": e["ts"], "type": "Safety", "n": 0,
                               "event": e["event"], "actor": e["actor"], "from": e["from_state"], "to": e["to_state"],
                               "detail": d, "first_id": e["id"]}
        m["n"] += 1
        m["at"] = e["ts"]
        m["detail"] = d
        m["last_id"] = e["id"]
    for m in merged.values():
        d = m["detail"] if isinstance(m["detail"], dict) else {}
        change = f" · {m['from'] or '?'} → {m['to']}" if m["to"] else ""
        outcome = d.get("outcome")
        events.append({"id": m["id"], "at": m["at"], "type": "Safety", "n": m["n"],
                       "what": f"{m['event'].replace('_', ' ')}{change}{f' · ×{m['n']} this hour' if m['n'] > 1 else ''}",
                       "who": m["actor"] or "system", "result": outcome or "Recorded",
                       "tone": "bad" if outcome == "NEEDS_OWNER" else "warn" if outcome in ("DEGRADED", "RECOVERING") else SAFETY_TONE.get(m["event"], "grey"),
                       "facts": {"event": m["event"], "control_event_ids": f"{m['first_id']}–{m.get('last_id', m['first_id'])}",
                                 "from_state": m["from"], "to_state": m["to"], "actor": m["actor"], "occurrences": m["n"]},
                       "detail": d})

    # strategy lifecycle + learning
    kinds = owner_reads.LIFECYCLE_KINDS + LEARNING_KINDS
    for b in journal.query(f"SELECT id, ts, kind, subject, detail FROM brain_events WHERE ts >= ? AND kind IN "
                           f"({','.join('?' * len(kinds))}) ORDER BY id", (cutoff, *kinds)):
        learning = b["kind"] in LEARNING_KINDS
        events.append({"id": f"brain:{b['id']}", "at": b["ts"], "type": "Learning" if learning else "Strategy", "n": 1,
                       "what": f"{b['kind'].replace('_', ' ')} · {b['subject']}", "who": "Brain", "result": b["kind"].replace("_", " "),
                       "tone": "bad" if b["kind"] in ("retire", "demote", "spec_rejected", "spec_decayed") else "ok" if b["kind"] in ("promote", "spec_admitted", "owner_reinstated") else "grey",
                       "facts": {"kind": b["kind"], "subject": b["subject"]}, "detail": _load_json(b["detail"])})

    events.sort(key=lambda e: e["at"] or "", reverse=True)
    return {"events": events, "window_days": days, "signal_cooldowns_not_listed": cooldowns,
            "newest": events[0]["at"] if events else None,
            "source": f"journal decisions (rolled up per UTC hour), trades, control_events (same-hour repeats merged), "
                      f"strategy lifecycle and learning brain_events over the last {days} days; "
                      "signal_cooldown control events are counted, not listed"}
