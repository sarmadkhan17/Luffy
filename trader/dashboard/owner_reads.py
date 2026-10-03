"""Owner frontend read contracts for Trades, Research, Strategies, Operations,
Knowledge and Diagnostics (/owner-api/v1).

Read-only, bounded, and only from stores the kernel already writes: the
journal (trades, decisions, strategies, research ledger, control events,
accounting receipts), the knowledge vault, health files and log files.
Nothing here joins by inference: a link is reported only where the store
records it, and whatever a store does not record is listed under
``unavailable`` with a reason instead of being filled in.

Every handler is a plain ``def``: FastAPI runs it in its worker pool, never
on the event loop that also serves the frontend's JS chunks.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi.responses import JSONResponse

PREFIX = "/owner-api/v1"
#: newest decisions examined for current activity (journal insertion order)
ACTIVITY_WINDOW = 500
ACTIVITY_ROWS = 50
NOTE_MAX_BYTES = 64 * 1024
HEALTH_STALE_S = 600.0
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
_CODE_SUFFIXES = (".py", ".ts", ".tsx", ".sh", ".yaml", ".yml", ".sql")
#: analyst votes are read through idx_votes_symbol over this window around the
#: decision's cycle, then kept only on exact cycle_id equality (votes has no
#: cycle_id index; a cycle_id-only read is a full scan of millions of rows)
VOTE_WINDOW_S = 300
#: newest brain_events rows examined per read (small table; bounds growth)
BRAIN_WINDOW = 20000
RESULT_MAX_BYTES = 16 * 1024
#: brain_events kinds that record a strategy's lifecycle (subject = strategy id)
LIFECYCLE_KINDS = ("spec_admitted", "spec_rejected", "spec_decayed", "promote", "demote",
                   "retire", "statistical_transition", "owner_reinstated",
                   "duplicate_retired", "retire_reason_corrected", "harvest_accepted",
                   "proposal_accepted", "seed_tv_demoted", "seed_tv_passed",
                   "seed_tv_advisory_fail", "session_error_corrected")
#: identifier-shaped tokens looked up by primary key (never partially matched)
_IDENT = re.compile(r"[A-Za-z0-9_-]{6,64}")
_HEX16 = re.compile(r"^[0-9a-f]{16}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _json(data, status: int = 200) -> JSONResponse:
    return JSONResponse(json.loads(json.dumps(data, default=str)), status_code=status,
                        headers={"Cache-Control": "no-store"})


def _load(raw, default=None):
    if raw is None or raw == "":
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {"_unparseable": True}


def _sha(text: str | None) -> str | None:
    return hashlib.sha256(text.encode()).hexdigest() if text else None


def _rows(journal, sql: str, params: tuple = ()) -> list[dict]:
    return journal.query(sql, params)


def _table_exists(journal, name: str) -> bool:
    return bool(_rows(journal, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (name,)))


def _unavailable(field: str, reason: str) -> dict:
    return {"field": field, "reason": reason}


def _ref(kind: str, rid) -> dict | None:
    """A navigable reference to a stored record, by its recorded id only."""
    return {"kind": kind, "id": str(rid)} if rid not in (None, "") else None


def _strategy_ids(raw) -> list[str]:
    """decisions.strategy_ids as recorded (comma-separated ids)."""
    return [x.strip() for x in str(raw or "").split(",") if x.strip()]


def _registry(journal, ids) -> dict[str, dict]:
    ids = sorted({i for i in ids if isinstance(i, str) and i})[:100]
    if not ids:
        return {}
    return {r["id"]: r for r in _rows(
        journal, f"SELECT id, name, kind, state FROM strategies WHERE id IN "
                 f"({','.join('?' * len(ids))})", tuple(ids))}


def _signals(raw) -> tuple[list[dict], str | None]:
    """decisions.signals_json as signal records, and why any were not usable.
    Malformed content is reported, never passed off as an empty list."""
    sig = _load(raw, [])
    if not isinstance(sig, list):
        return [], "signals_json is not a readable list"
    good = [x for x in sig if isinstance(x, dict)]
    bad = len(sig) - len(good)
    return good, (f"{bad} of {len(sig)} signals_json entries are not signal records"
                  if bad else None)


def _parse_ts(ts) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _cycle(journal, cycle_id) -> dict | None:
    if not cycle_id:
        return None
    c = _rows(journal, "SELECT id, ts, symbol, price, regime, adx, btc_trend, market_type, "
                       "mode FROM cycles WHERE id=?", (cycle_id,))
    return c[0] if c else None


def _votes(journal, decision: dict, cycle: dict | None) -> tuple[list[dict], dict]:
    """Analyst votes of the decision's cycle: an indexed (symbol, ts) window read,
    kept only where votes.cycle_id equals the decision's cycle_id exactly."""
    start = _parse_ts((cycle or {}).get("ts") or decision.get("ts"))
    end = _parse_ts(decision.get("ts"))
    basis = {"join": "votes.cycle_id = decisions.cycle_id (exact)",
             "window_s": VOTE_WINDOW_S,
             "note": "read through idx_votes_symbol within the window around the cycle; "
                     "a vote recorded outside it is not shown"}
    if start is None or end is None or not decision.get("cycle_id"):
        return [], dict(basis, status="unavailable", reason="decision time or cycle missing")
    lo = datetime.fromtimestamp(start.timestamp() - VOTE_WINDOW_S, timezone.utc).isoformat(
        timespec="microseconds")
    hi = datetime.fromtimestamp(end.timestamp() + VOTE_WINDOW_S, timezone.utc).isoformat(
        timespec="microseconds")
    rows = _rows(journal, "SELECT agent, side, conviction, confidence, rationale, ts FROM votes "
                          "WHERE symbol=? AND ts BETWEEN ? AND ? AND cycle_id=? LIMIT 50",
                 (decision["symbol"], lo, hi, decision["cycle_id"]))
    return rows, dict(basis, status="recorded" if rows else "none_in_window")


# ── Trades ────────────────────────────────────────────────────────────────────
#: trader.engine.booking.assess's status for exact venue order-fill evidence
FILLS_VERIFIED = "verified_leg_fills_only"


def _receipt(row: dict, trade_id: str) -> dict:
    """One booking receipt as evidence. `assessment` is the one booking.replay
    recomputed from the receipt's own evidence, and only when the receipt
    replays and is bound to this trade (row, receipt and booked snapshot);
    otherwise it is None and `integrity` says why."""
    from ..engine import booking
    rec = _load(row["payload"], None)
    shown = rec if isinstance(rec, dict) else {}
    assessment = None
    try:
        if not isinstance(rec, dict) or rec.get("_unparseable"):
            raise ValueError("booking_malformed")
        replayed = booking.replay(rec)
        if not (row["trade_id"] == rec.get("trade_id") == trade_id
                and (rec.get("after") or {}).get("id") == trade_id):
            raise ValueError("booking_trade_mismatch")
        if not isinstance(replayed, dict) or not isinstance(replayed.get("status"), str):
            raise ValueError("booking_assessment_invalid")
        integrity, assessment = "verified", replayed
    except Exception as e:                                  # noqa: BLE001
        reason = str(e) if isinstance(e, ValueError) else type(e).__name__
        integrity = f"failed:{reason}"
    evidence = shown.get("evidence")
    return {"receipt_id": row["id"], "kind": shown.get("kind"),
            "observed_ms": shown.get("observed_ms"),
            "evidence_basis": evidence.get("basis") if isinstance(evidence, dict) else None,
            "assessment": assessment, "sha256": shown.get("sha256"),
            "integrity": integrity}


def trade_lineage(journal, trade_id: str) -> dict | None:
    rows = _rows(journal, "SELECT * FROM trades WHERE id=?", (trade_id,))
    if not rows:
        return None
    trade = rows[0]
    missing: list[dict] = []
    decision = None
    if trade.get("decision_id"):
        d = _rows(journal, "SELECT id, cycle_id, ts, symbol, action, score, threshold, "
                           "confidence, executed, skip_reason, size_usdt, entry_price, "
                           "strategy_ids, signals_json, meta_p, scan_id "
                           "FROM decisions WHERE id=?", (trade["decision_id"],))
        if d:
            sigs, why = _signals(d[0].pop("signals_json"))
            decision = dict(d[0], signals=sigs)
            if why:
                missing.append(_unavailable("signals", why))
        else:
            missing.append(_unavailable("decision", "decision_id recorded on the trade has "
                                                    "no journal decision row"))
    else:
        missing.append(_unavailable("decision", "trade row records no decision_id"))
    if not (decision or {}).get("scan_id"):
        missing.append(_unavailable("opportunity", "no attention scan id is recorded for "
                                                   "this trade's decision"))

    strategy = None
    if trade.get("strategy_id"):
        s = _rows(journal, "SELECT id, name, kind, state, origin, generation, parent_id, "
                           "created_at, state_changed_at, spec_json FROM strategies WHERE id=?",
                  (trade["strategy_id"],))
        if s:
            spec = s[0].pop("spec_json")
            strategy = dict(s[0], spec_sha256=_sha(spec),
                            spec_sha256_basis="current registry row, not the version at entry")
        else:
            missing.append(_unavailable("strategy", "strategy_id is not in the registry"))
    else:
        missing.append(_unavailable("strategy", "trade row records no strategy_id"))
    from ..engine import trade_provenance
    provenance = trade_provenance.read(lambda q, a=(): _rows(journal, q, a), trade_id)
    trade.pop("entry_identity_json", None)
    identity = provenance["strategy_entry_identity"]
    if identity.get("status") != trade_provenance.VERIFIED:
        missing.append(_unavailable(
            "strategy_version_at_entry",
            "the trade records the strategy id only; no version or spec hash was stored "
            "when it opened" if "schema_version" not in identity else
            f"entry identity recorded as {identity.get('status')}: {identity.get('reason')}"))
    terminal = provenance["terminal_close"]
    if terminal["status"] not in (trade_provenance.VERIFIED, "NOT_APPLICABLE"):
        missing.append(_unavailable(
            "terminal_close_provenance",
            f"terminal close recorded as {terminal['status']}: "
            f"{terminal.get('reason') or ', '.join(terminal.get('gaps') or [])}"))

    receipts, booked = [], 0
    if _table_exists(journal, "trade_accounting_bookings"):
        for r in _rows(journal, "SELECT id, trade_id, payload FROM trade_accounting_bookings "
                                "WHERE trade_id=? ORDER BY id LIMIT 50", (trade_id,)):
            receipts.append(_receipt(r, trade_id))
        booked = _rows(journal, "SELECT COUNT(*) n FROM trade_accounting_bookings "
                                "WHERE trade_id=?", (trade_id,))[0]["n"]
    if not receipts:
        missing.append(_unavailable("accounting", "no booking receipts recorded for this "
                                                  "trade (legacy booking)"))
    # a leg counts only when its receipt replays, is bound to this trade and its
    # recomputed assessment is the booking module's success status (a receipt's
    # own claimed assessment is never trusted). The trade's fills are verified
    # only when every booked leg is; one failed or unverified receipt is enough
    # to withhold it.
    verified_legs = [r["kind"] for r in receipts if r["integrity"] == "verified"
                     and (r["assessment"] or {}).get("status") == FILLS_VERIFIED]
    fills_verified = bool(receipts) and len(verified_legs) == len(receipts) == booked
    if not fills_verified:
        missing.append(_unavailable(
            "venue_fills", f"{len(verified_legs)} of {booked} booked receipt(s) "
                           "verified by exact venue order fills"
                           + (f" ({', '.join(map(str, verified_legs))})" if verified_legs
                              else "")))
    missing.append(_unavailable("funding", "booking receipts do not attribute funding"))
    outcome = None
    if trade.get("decision_id"):
        o = _rows(journal, "SELECT * FROM outcomes WHERE decision_id=?",
                  (trade["decision_id"],))
        outcome = o[0] if o else None
    if outcome is None:
        missing.append(_unavailable("outcome", "no forward-return outcome recorded for the "
                                               "decision"))
    trade["excursion"] = _load(trade.pop("excursion_json", None))
    cycle = _cycle(journal, decision.get("cycle_id")) if decision else None
    votes, votes_basis = _votes(journal, decision, cycle) if decision else ([], None)
    if decision and cycle is None:
        missing.append(_unavailable("market_context", "the decision's cycle_id has no "
                                                      "journal cycle row"))
    signals = (decision or {}).get("signals") or []
    signals_bad = any(u["field"] == "signals" for u in missing)
    closed = trade.get("status") == "closed"
    chain = [
        {"step": "opportunity", "status": "recorded" if (decision or {}).get("scan_id")
         else "unavailable", "at": None, "ref": _ref("scan", (decision or {}).get("scan_id")),
         "summary": "attention scan id recorded on the decision"
         if (decision or {}).get("scan_id") else None},
        {"step": "market_context", "status": "recorded" if cycle else "unavailable",
         "at": (cycle or {}).get("ts"), "ref": None,
         "summary": (f"{cycle.get('regime')} · ADX {cycle.get('adx')} · BTC trend "
                     f"{cycle.get('btc_trend')}") if cycle else None},
        {"step": "decision", "status": "recorded" if decision else "unavailable",
         "at": (decision or {}).get("ts"), "ref": _ref("decision", trade.get("decision_id")),
         "summary": (f"{decision.get('action')} · score {decision.get('score')} vs threshold "
                     f"{decision.get('threshold')}") if decision else None},
        {"step": "signals", "status": "malformed" if signals_bad else
         "recorded" if signals or votes else "unavailable", "at": None, "ref": None,
         "summary": f"{len(signals)} strategy signal(s) · {len(votes)} analyst vote(s)"
                    + (" · signals_json malformed" if signals_bad else "")},
        {"step": "strategy", "status": "recorded" if strategy else "unavailable", "at": None,
         "ref": _ref("strategy", trade.get("strategy_id")),
         "summary": (strategy or {}).get("name")},
        {"step": "execution", "status": "recorded", "at": trade.get("opened_at"),
         "ref": _ref("trade", trade_id),
         "summary": f"{trade.get('side')} {trade.get('amount')} {trade.get('symbol')} @ "
                    f"{trade.get('entry_price')} · exec_mode {trade.get('exec_mode') or 'not recorded'} "
                    "(journal record)"},
        {"step": "accounting", "status": ("verified" if fills_verified else "recorded")
         if receipts else "unavailable", "at": None, "ref": None,
         "summary": f"{len(receipts)} receipt(s) · {len(verified_legs)} of {booked} leg(s) "
                    "venue-fill verified"},
        {"step": "outcome", "status": "recorded" if closed else "open",
         "at": trade.get("closed_at"), "ref": None,
         "summary": (f"closed ({trade.get('close_reason')}) · realized "
                     f"{trade.get('realized_pnl')} USDT · MFE {trade.get('mfe_r')}R / "
                     f"MAE {trade.get('mae_r')}R") if closed
         else "position open; no realized outcome yet"},
    ]
    return {"generated_at": _iso(_now()), "trade": trade, "decision": decision,
            "cycle": cycle, "votes": votes, "votes_basis": votes_basis, "chain": chain,
            "links": {"decision": _ref("decision", trade.get("decision_id")),
                      "strategy": _ref("strategy", trade.get("strategy_id")),
                      "scan": _ref("scan", (decision or {}).get("scan_id"))},
            "strategy": strategy, "accounting": {
                "receipts": receipts, "fills_verified": fills_verified,
                "verified_legs": verified_legs,
                "replay": ("trader.engine.booking.export(db, trade_id) re-verifies every "
                           "receipt's sha256 and assessment") if receipts else None},
            "outcome": outcome, "provenance": provenance, "unavailable": missing,
            "source": "journal trades/decisions/cycles/votes/strategies/outcomes and "
                      "trade_accounting_bookings (joins only by recorded ids)"}


def decision_detail(journal, decision_id: str) -> dict | None:
    d = _rows(journal, "SELECT id, cycle_id, ts, symbol, action, score, threshold, confidence, "
                       "executed, skip_reason, size_usdt, entry_price, strategy_ids, "
                       "signals_json, meta_p, scan_id FROM decisions WHERE id=?",
              (decision_id,))
    if not d:
        return None
    decision = dict(d[0])
    missing: list[dict] = []
    decision["signals"], why = _signals(decision.pop("signals_json"))
    if why:
        missing.append(_unavailable("signals", why))
    cycle = _cycle(journal, decision["cycle_id"])
    if cycle is None:
        missing.append(_unavailable("market_context", "no journal cycle row for cycle_id"))
    votes, basis = _votes(journal, decision, cycle)
    outcome = (_rows(journal, "SELECT * FROM outcomes WHERE decision_id=?", (decision_id,))
               or [None])[0]
    if outcome is None:
        missing.append(_unavailable("outcome", "no forward-return outcome recorded"))
    # trades has no decision_id index; it holds one row per entry (small table)
    trades = _rows(journal, "SELECT id, symbol, side, status, opened_at, closed_at, "
                            "realized_pnl, strategy_id FROM trades WHERE decision_id=? "
                            "LIMIT 10", (decision_id,))
    ids = list(dict.fromkeys(
        [*_strategy_ids(decision.get("strategy_ids")),
         *(s.get("strategy_id") for s in decision["signals"])]))
    reg = _registry(journal, ids)
    strategies = [{"id": i, "in_registry": i in reg,
                   **{k: (reg.get(i) or {}).get(k) for k in ("name", "kind", "state")}}
                  for i in ids if isinstance(i, str) and i]
    if not decision.get("scan_id"):
        missing.append(_unavailable("opportunity", "no attention scan id recorded"))
    return {"generated_at": _iso(_now()), "decision": decision, "cycle": cycle,
            "votes": votes, "votes_basis": basis, "outcome": outcome, "trades": trades,
            "strategies": strategies, "unavailable": missing,
            "source": "journal decisions/cycles/votes/outcomes/trades (exact ids)"}


# ── Research ──────────────────────────────────────────────────────────────────
_COMBO_COLS = ("hash, tf, geo, k, round, parent, trigger, window, parts, status, label, "
               "verdict, reason, consistency_p, median_pf, total_pct, max_dd_pct, trades, "
               "scored_symbols, testable, created_at")


def _seed(trigger) -> str | None:
    """research_combos.trigger 'seed:<strategy id>' records the seeding strategy."""
    t = str(trigger or "")
    return t[5:] if t.startswith("seed:") and len(t) > 5 else None


def _with_links(journal, combos: list[dict]) -> list[dict]:
    reg = _registry(journal, [_seed(r.get("trigger")) for r in combos])
    for r in combos:
        sid = _seed(r.get("trigger"))
        r["seed_strategy"] = ({"id": sid, "in_registry": sid in reg,
                               "name": (reg.get(sid) or {}).get("name")} if sid else None)
    return combos


def research_item(journal, combo_hash: str) -> dict | None:
    rows = _rows(journal, f"SELECT {_COMBO_COLS}, entry_long, entry_short, ablation, "
                          "result FROM research_combos WHERE hash=?", (combo_hash,))
    if not rows:
        return None
    item = rows[0]
    missing: list[dict] = []
    raw = item.pop("result")
    item["result_bytes"] = len(raw) if raw else 0
    if raw and len(raw) > RESULT_MAX_BYTES:
        item["result"], item["result_truncated"] = None, True
        missing.append(_unavailable("result", f"full evaluation is {len(raw)} bytes; over the "
                                              f"{RESULT_MAX_BYTES}-byte read bound"))
    else:
        item["result"], item["result_truncated"] = _load(raw), False
    item["parts"] = _load(item["parts"], [])
    item["ablation"] = _load(item["ablation"])
    _with_links(journal, [item])
    parent = None
    if item.get("parent"):
        p = _rows(journal, "SELECT hash, label, verdict, status, round, created_at FROM "
                           "research_combos WHERE hash=?", (item["parent"],))
        parent = p[0] if p else None
        if parent is None:
            missing.append(_unavailable("parent", "parent hash is not in the ledger"))
    # research_combos has no parent index; bounded (the ledger holds thousands of rows)
    children = _rows(journal, "SELECT hash, label, verdict, status, round, created_at FROM "
                              "research_combos WHERE parent=? ORDER BY rowid DESC LIMIT 50",
                     (combo_hash,))
    candidate = (_rows(journal, "SELECT hash, tf, geo, state, twin_of, rank, gate1, gate3, "
                                "reason, updated_at FROM research_candidates WHERE hash=?",
                       (combo_hash,)) or [None])[0]
    tests = _rows(journal, "SELECT seq, gate, p, alpha_t, rejected, braked, at FROM "
                           "research_tests WHERE hash=? ORDER BY seq DESC LIMIT 50",
                  (combo_hash,))
    if candidate is None:
        missing.append(_unavailable("candidate", "not carried into the candidate bank"))
    if not tests:
        missing.append(_unavailable("registrations", "no registered gate look for this hash"))
    if item["seed_strategy"] is None:
        missing.append(_unavailable("strategy", "the combination records no seeding strategy "
                                                "(trigger is not seed:<id>)"))
    return {"generated_at": _iso(_now()), "item": item, "parent": parent,
            "children": children, "candidate": candidate, "registrations": tests,
            "unavailable": missing,
            "notes": ["A search result is a ranking, not admission"],
            "source": "journal research ledger (research_combos/candidates/tests; exact "
                      "hash joins)"}


def _brain(journal, where: str, params: tuple, limit: int) -> list[dict]:
    """Newest brain_events matching `where`, within the newest BRAIN_WINDOW rows."""
    return _rows(journal, f"SELECT id, ts, kind, subject, detail FROM brain_events WHERE "
                          f"id > (SELECT COALESCE(MAX(id), 0) FROM brain_events) - ? AND "
                          f"({where}) ORDER BY id DESC LIMIT ?",
                 (BRAIN_WINDOW, *params, limit))


def assessed_ideas(journal, limit: int = 30) -> list[dict]:
    """Harvested ideas the strategy writer consumed, with the recorded outcome and
    the spec id it produced (brain_events idea_consumed; subject = idea id)."""
    if not _table_exists(journal, "brain_events"):
        return []
    out = []
    rows = _brain(journal, "kind='idea_consumed'", (), limit)
    specs = []
    for r in rows:
        d = _load(r["detail"], {})
        d = d if isinstance(d, dict) and not d.get("_unparseable") else {}
        spec = d.get("spec") if isinstance(d.get("spec"), str) else None
        specs.append(spec)
        out.append({"event_id": r["id"], "ts": r["ts"], "idea_id": r["subject"],
                    "outcome": d.get("outcome"), "stream": d.get("stream"),
                    "name": d.get("name"), "spec": spec,
                    "detail_readable": bool(d)})
    reg = _registry(journal, specs)
    for o in out:
        o["spec_in_registry"] = bool(o["spec"]) and o["spec"] in reg
    return out


def research(journal, limit: int = 50, offset: int = 0) -> dict:
    limit = max(1, min(int(limit), 200))
    offset = max(0, min(int(offset), 100000))
    out: dict = {"generated_at": _iso(_now()),
                 "source": "journal research ledger (trader/research/ledger.py)"}
    from trader.owner.queries import rows as question_rows, record as question_record
    questions = question_rows(journal, 'research_questions', 'question_id', limit=limit+1, offset=offset)
    out['questions'] = [question_record('question', r['question_id'], json.loads(r['canonical_json']),
                        source='research_questions', at=r['recorded_at_ms'], available=r['recorded_at_ms'])
                        for r in questions[:limit]]
    out['questions_page'] = dict(offset=offset, limit=limit, has_more=len(questions)>limit,
                                order='journal insertion order, newest first')
    question_gaps = [] if _table_exists(journal, 'research_questions') else [
        _unavailable('questions', 'research-question store is unavailable')]
    if not _table_exists(journal, "research_combos"):
        out.update(available=bool(out['questions']), partial=True,
                   reason="quantitative research ledger tables are absent",
                   unavailable=question_gaps+[_unavailable('results', 'quantitative result store is unavailable')])
        return out
    out["available"] = True
    out["counts"] = {
        "results_by_verdict": _rows(journal, "SELECT verdict, status, COUNT(*) n FROM "
                                             "research_combos GROUP BY verdict, status "
                                             "ORDER BY n DESC"),
        "candidates_by_state": _rows(journal, "SELECT state, COUNT(*) n FROM "
                                              "research_candidates GROUP BY state"),
        "registered_tests": _rows(journal, "SELECT COUNT(*) n FROM research_tests")[0]["n"],
        "runs": _rows(journal, "SELECT COUNT(*) n, SUM(ok=0) failed FROM research_batches")[0],
    }
    # rowid (insertion) order: newest first without sorting the ledger
    results = _rows(journal, f"SELECT {_COMBO_COLS} FROM research_combos "
                             f"ORDER BY rowid DESC LIMIT ? OFFSET ?", (limit + 1, offset))
    has_more = len(results) > limit
    results = results[:limit]
    for r in results:
        r["parts"] = _load(r["parts"], [])
    out["results"] = _with_links(journal, results)
    out["results_page"] = {"offset": offset, "limit": limit, "has_more": has_more,
                           "order": "journal insertion order, newest first"}
    out["runs"] = _rows(journal, "SELECT id, started, finished, tf, geo, round, n, ok, "
                                 "elapsed_s, error FROM research_batches "
                                 "ORDER BY id DESC LIMIT ?", (limit,))
    out["evidence"] = {
        # the control's detail blob (per-symbol arrays) is not sent: ~10 KB a row
        "controls": _rows(journal, "SELECT tf, window, consistency_p, powered, status, "
                                   "measured_at, LENGTH(detail) detail_bytes FROM "
                                   "research_controls ORDER BY measured_at DESC LIMIT ?",
                          (limit,)),
        "slices": _rows(journal, "SELECT tf, cut_ms, measured_at FROM research_slices"),
        "gauges": _rows(journal, "SELECT tf, COUNT(*) n, SUM(usable) usable, "
                                 "MAX(measured_at) measured_at FROM research_gauges "
                                 "GROUP BY tf"),
    }
    out["candidates"] = _rows(journal, "SELECT hash, tf, geo, state, twin_of, rank, gate1, "
                                       "gate3, entries, reason, updated_at FROM "
                                       "research_candidates ORDER BY updated_at DESC LIMIT ?",
                              (limit,))
    out["registrations"] = _rows(journal, "SELECT seq, hash, tf, geo, gate, p, alpha_t, "
                                          "rejected, braked, at FROM research_tests "
                                          "ORDER BY seq DESC LIMIT ?", (limit,))
    out["ideas"] = assessed_ideas(journal, limit=min(limit, 50))
    out["unavailable"] = question_gaps + [
        _unavailable("plans", "frozen protocols are not persisted as records the API can read"),
        _unavailable("costs", "no attributed research cost records; missing is not zero"),
        _unavailable("shadow_reports", "no shadow-report store"),
        _unavailable("prior_recall", "no prior-evidence retrieval log"),
    ]
    out["notes"] = ["Results are search rankings, not admission",
                    "Investigations are served by /api/investigations/latest"]
    return out


# ── Strategies ────────────────────────────────────────────────────────────────
def _strategy_row(r: dict) -> dict:
    spec_raw = r.pop("spec_json", None)
    spec = _load(spec_raw)
    stats = _load(r.pop("stats_json", None), {})
    r["spec_sha256"] = _sha(spec_raw)
    r["params_sha256"] = _sha(r.pop("params", None))
    r["markets"] = _load(r.get("markets"), r.get("markets"))
    r["spec"] = ({k: spec.get(k) for k in ("timeframe", "direction", "universe",
                                            "regime_filter", "data_requires", "exit",
                                            "provenance")}
                 if isinstance(spec, dict) and not spec.get("_unparseable") else None)
    r["registry_stats"] = stats or None
    return r


def strategies(journal) -> list[dict]:
    rows = _rows(journal, "SELECT id, name, kind, state, origin, generation, parent_id, "
                          "created_at, state_changed_at, retire_reason, markets, params, "
                          "spec_json, stats_json FROM strategies ORDER BY CASE state "
                          "WHEN 'active' THEN 0 WHEN 'paper' THEN 1 ELSE 2 END, "
                          "created_at DESC LIMIT 200")
    booked = {r["strategy_id"]: r for r in _rows(
        journal, "SELECT strategy_id, COUNT(*) trades, SUM(status='open') open, "
                 "SUM(CASE WHEN status='closed' THEN realized_pnl END) realized_pnl, "
                 "SUM(status='closed' AND realized_pnl>0) wins, "
                 "SUM(status='closed') closed, MAX(opened_at) last_opened "
                 "FROM trades WHERE strategy_id IS NOT NULL GROUP BY strategy_id")}
    out = []
    for r in rows:
        r = _strategy_row(r)
        r["journal_economics"] = booked.get(r["id"])
        out.append(r)
    return out


def _family(journal, r: dict) -> dict:
    """Registry relationships by recorded ids: kind (family), parent_id, children."""
    sid, parent_id = r["id"], r.get("parent_id") or None
    parent = None
    if parent_id:
        p = _rows(journal, "SELECT id, name, kind, state FROM strategies WHERE id=?",
                  (parent_id,))
        parent = dict(p[0], in_registry=True) if p else {"id": parent_id, "in_registry": False}
    return {"kind": r.get("kind"), "generation": r.get("generation"), "parent": parent,
            "children": _rows(journal, "SELECT id, name, kind, state, generation FROM "
                                       "strategies WHERE parent_id=? LIMIT 50", (sid,)),
            "siblings": _rows(journal, "SELECT id, name, state, generation FROM strategies "
                                       "WHERE kind=? AND id!=? ORDER BY created_at DESC "
                                       "LIMIT 50", (r.get("kind"), sid)),
            "basis": "registry kind, parent_id and generation as recorded"}


def _source_idea(journal, sid: str, spec) -> dict | None:
    """The harvested idea that produced this spec: the spec's own provenance
    idea_id, and/or an idea_consumed event whose recorded spec is this id."""
    prov = spec.get("provenance") if isinstance(spec, dict) else None
    prov = prov if isinstance(prov, dict) else {}
    idea_id = prov.get("idea_id") if isinstance(prov.get("idea_id"), str) else None
    consumed = None
    if _table_exists(journal, "brain_events"):
        for e in _brain(journal, "kind='idea_consumed'", (), 500):
            d = _load(e["detail"], {})
            if isinstance(d, dict) and d.get("spec") == sid:
                consumed = {"event_id": e["id"], "at": e["ts"], "idea_id": e["subject"],
                            "outcome": d.get("outcome"), "stream": d.get("stream")}
                break
    idea_id = idea_id or (consumed or {}).get("idea_id")
    if not idea_id and not prov:
        return None
    harvested = None
    if idea_id and _table_exists(journal, "brain_events"):
        h = _brain(journal, "kind='harvest_idea' AND subject=?", (idea_id,), 1)
        if h:
            d = _load(h[0]["detail"], {})
            d = d if isinstance(d, dict) else {}
            harvested = {"event_id": h[0]["id"], "at": h[0]["ts"], "title": d.get("title"),
                         "source": d.get("source"), "url": d.get("url")}
    return {"idea_id": idea_id, "provenance": {k: prov.get(k) for k in (
                "source_kind", "author", "mechanism", "source_url", "note") if k in prov},
            "consumed": consumed, "harvested": harvested,
            "basis": "spec provenance idea_id and brain_events idea_consumed.spec (exact ids)"}


def _postmortem(journal, sid: str) -> dict | None:
    """This strategy's entry in the newest recorded postmortem (brain_events
    kind postmortem): a verdict at that event's time, not current health."""
    if not _table_exists(journal, "brain_events"):
        return None
    rows = _brain(journal, "kind='postmortem'", (), 1)
    if not rows:
        return None
    d = _load(rows[0]["detail"], {})
    if not isinstance(d, dict) or d.get("_unparseable"):
        return {"event_id": rows[0]["id"], "at": rows[0]["ts"], "readable": False}
    verdict = (d.get("verdicts") or {}).get(sid) if isinstance(d.get("verdicts"), dict) else None
    entry = next((b for b in d.get("book") or [] if isinstance(b, dict) and b.get("id") == sid),
                 None)
    if verdict is None and entry is None:
        return None
    return {"event_id": rows[0]["id"], "at": rows[0]["ts"], "readable": True,
            "verdict": verdict, "book_entry": entry,
            "basis": "newest brain_events postmortem; keyed by exact strategy id"}


def strategy_detail(journal, sid: str) -> dict | None:
    rows = _rows(journal, "SELECT * FROM strategies WHERE id=?", (sid,))
    if not rows:
        return None
    r = rows[0]
    spec = _load(r.get("spec_json"))
    detail = _strategy_row(dict(r))
    detail["hypothesis"] = r.get("hypothesis")
    detail["invalidation"] = r.get("invalidation")
    detail["spec_full"] = spec if isinstance(spec, dict) else None
    detail["lifecycle"] = [
        {"at": r.get("created_at"), "event": "registry_created", "source": "strategies"}]
    if r.get("state_changed_at") and r.get("state_changed_at") != r.get("created_at"):
        detail["lifecycle"].append({"at": r["state_changed_at"],
                                    "event": f"state:{r.get('state')}",
                                    "source": "strategies.state_changed_at"})
    unattributed = 0
    for e in _rows(journal, "SELECT ts, event, detail FROM control_events WHERE event LIKE "
                            "'strategy\\_%' ESCAPE '\\' ORDER BY id DESC LIMIT 500"):
        # the event's recorded strategy identifier, compared exactly; an event
        # that records none is never attributed to any strategy
        d = _load(e["detail"])
        ident = d.get("id") if isinstance(d, dict) else None
        if not isinstance(ident, str):
            unattributed += 1
            continue
        if ident == sid:
            detail["lifecycle"].append({"at": e["ts"], "event": e["event"], "detail": d,
                                        "source": "control_events"})
    # brain_events whose subject is exactly this strategy id
    brain = []
    if _table_exists(journal, "brain_events"):
        for e in _brain(journal, "subject=?", (sid,), 100):
            d = _load(e["detail"])
            brain.append({"event_id": e["id"], "at": e["ts"], "kind": e["kind"],
                          "detail": d if isinstance(d, (dict, list)) else e["detail"]})
            if e["kind"] in LIFECYCLE_KINDS:
                detail["lifecycle"].append({"at": e["ts"], "event": e["kind"],
                                            "detail": d, "source": "brain_events"})
    detail["lifecycle"].sort(key=lambda e: e["at"] or "")
    detail["brain_events"] = brain
    detail["family"] = _family(journal, r)
    detail["source_idea"] = _source_idea(journal, sid, spec)
    detail["postmortem"] = _postmortem(journal, sid)
    seeded = _rows(journal, "SELECT hash, label, verdict, status, round, created_at FROM "
                            "research_combos WHERE trigger=? ORDER BY rowid DESC LIMIT 20",
                   ("seed:" + sid,)) if _table_exists(journal, "research_combos") else []
    detail["research_seeded"] = seeded
    detail["recent_trades"] = _rows(journal, "SELECT id, symbol, side, status, opened_at, "
                                             "closed_at, realized_pnl, close_reason FROM "
                                             "trades WHERE strategy_id=? ORDER BY opened_at "
                                             "DESC LIMIT 20", (sid,))
    detail["journal_economics"] = (_rows(
        journal, "SELECT COUNT(*) trades, SUM(status='open') open, SUM(status='closed') closed,"
                 " SUM(CASE WHEN status='closed' THEN realized_pnl END) realized_pnl,"
                 " SUM(status='closed' AND realized_pnl>0) wins FROM trades "
                 "WHERE strategy_id=?", (sid,)) or [None])[0]
    detail["unavailable"] = [
        _unavailable("health", "no continuously maintained health state is stored; the "
                               "newest recorded postmortem verdict is shown with its own "
                               "time" if detail["postmortem"] else
                     "no health record or postmortem verdict is stored for this strategy"),
        _unavailable("capacity", "no capacity estimate is stored"),
        _unavailable("allocation", "risk sizes each entry from config; no per-strategy "
                                   "allocation record is stored"),
        _unavailable("version_history", "the registry keeps only the current row; earlier "
                                         "spec versions are not retained"),
    ]
    if unattributed:
        detail["unavailable"].append(_unavailable(
            "unattributed_lifecycle_events",
            f"{unattributed} strategy control event(s) record no strategy id and are not "
            "attributed to any strategy"))
    detail["generated_at"] = _iso(_now())
    detail["source"] = ("journal strategies (current registry row), control_events "
                        "strategy_* entries and journal-booked trades")
    return detail


# ── Operations ────────────────────────────────────────────────────────────────
def _reason_class(reason: str) -> str:
    return re.split(r"[:(\s]", reason.strip(), 1)[0] if reason else ""


TIMELINE_ROWS = 80


def _timeline(journal, decisions: list[dict], orders: list[dict],
              events: list[dict]) -> list[dict]:
    """What Luffy recorded doing, newest first: directional/executed decisions,
    journal trade entries and exits, non-cooldown control events and strategy
    lifecycle events. Each item carries the recorded id of its record."""
    items = []
    for r in decisions:
        if r["action"] == "HOLD" and not r["executed"]:
            continue
        items.append({"at": r["ts"], "kind": "decision", "ref": _ref("decision", r["id"]),
                      "symbol": r["symbol"], "title": f"{r['action']} {r['symbol']}",
                      "status": "executed" if r["executed"] else "skipped",
                      "detail": r["skip_reason"] or None,
                      "strategy_ids": _strategy_ids(r.get("strategy_ids"))})
    for t in orders:
        items.append({"at": t["opened_at"], "kind": "trade_opened", "ref": _ref("trade", t["id"]),
                      "symbol": t["symbol"], "title": f"Opened {t['side']} {t['symbol']}",
                      "status": t["status"], "detail": f"exec_mode {t['exec_mode'] or 'not recorded'}",
                      "strategy_ids": [t["strategy_id"]] if t.get("strategy_id") else []})
        if t.get("closed_at"):
            items.append({"at": t["closed_at"], "kind": "trade_closed",
                          "ref": _ref("trade", t["id"]), "symbol": t["symbol"],
                          "title": f"Closed {t['side']} {t['symbol']}", "status": "closed",
                          "detail": t.get("close_reason"),
                          "strategy_ids": [t["strategy_id"]] if t.get("strategy_id") else []})
    for e in events:
        if e["event"] == "signal_cooldown":
            continue
        d = e["detail"] if isinstance(e["detail"], dict) else {}
        ident = d.get("id") if isinstance(d.get("id"), str) else None
        items.append({"at": e["ts"], "kind": "control_event", "ref": _ref("control_event", e["id"]),
                      "symbol": None, "title": e["event"], "status": e.get("to_state"),
                      "detail": f"actor {e['actor']}", "strategy_ids": [ident] if ident else []})
    if _table_exists(journal, "brain_events"):
        kinds = LIFECYCLE_KINDS
        for b in _brain(journal, f"kind IN ({','.join('?' * len(kinds))})", kinds, 20):
            items.append({"at": b["ts"], "kind": "strategy_lifecycle",
                          "ref": _ref("strategy", b["subject"]), "symbol": None,
                          "title": f"{b['kind']} · {b['subject']}", "status": b["kind"],
                          "detail": None, "strategy_ids": [b["subject"]]})
    reg = _registry(journal, [s for i in items for s in i["strategy_ids"]])
    for i in items:
        # a strategy reference is navigable only when the id is in the registry
        i["strategies"] = [{"id": s, "in_registry": s in reg} for s in i.pop("strategy_ids")]
        if i["kind"] == "strategy_lifecycle" and not i["strategies"][0]["in_registry"]:
            i["ref"] = None
    items.sort(key=lambda i: i["at"] or "", reverse=True)
    return items[:TIMELINE_ROWS]


def operations(journal) -> dict:
    # rowid order is journal insertion order: the newest rows without a full
    # sort of the decisions table (there is no ts index)
    rows = _rows(journal, "SELECT id, cycle_id, ts, symbol, action, score, threshold, "
                          "executed, skip_reason, strategy_ids, signals_json, scan_id, meta_p "
                          "FROM decisions ORDER BY rowid DESC LIMIT ?", (ACTIVITY_WINDOW,))
    blocks: dict[str, int] = {}
    signals = []
    malformed_signals = 0
    for r in rows:
        r["signals"], why = _signals(r.pop("signals_json"))
        r["signals_malformed"] = why
        malformed_signals += bool(why)
        if r["skip_reason"]:
            k = _reason_class(r["skip_reason"])
            blocks[k] = blocks.get(k, 0) + 1
        for s in r["signals"][:5]:
            if isinstance(s, dict) and len(signals) < ACTIVITY_ROWS:
                signals.append({"decision_id": r["id"], "ts": r["ts"],
                                "symbol": s.get("symbol") or r["symbol"],
                                "strategy_id": s.get("strategy_id"), "action": s.get("action"),
                                "confidence": s.get("confidence"),
                                "rationale": s.get("rationale"),
                                "signal_bar_age_min": (s.get("params") or {})
                                .get("signal_bar_age_min"),
                                "executed": bool(r["executed"]),
                                "skip_reason": r["skip_reason"]})
    scans = []
    for r in rows:
        if r["scan_id"] and (not scans or scans[-1]["scan_id"] != r["scan_id"]):
            if len(scans) >= 10:
                break
            scans.append({"scan_id": r["scan_id"], "latest_decision_at": r["ts"]})
    window = {"decisions": len(rows),
              "directional": sum(1 for r in rows if r["action"] != "HOLD"),
              "executed": sum(1 for r in rows if r["executed"]),
              "oldest": rows[-1]["ts"] if rows else None,
              "newest": rows[0]["ts"] if rows else None,
              "malformed_signals": malformed_signals}
    orders = _rows(journal, "SELECT id, decision_id, symbol, side, status, exec_mode, "
                            "opened_at, closed_at, strategy_id, amount, entry_price, "
                            "stop_loss, close_reason, realized_pnl, sl_order_id IS NOT NULL AND "
                            "sl_order_id!='' stop_ref_recorded FROM trades ORDER BY opened_at "
                            "DESC LIMIT 20")
    # signal_cooldown rows are ~all control events; they are counted, not listed
    events = _rows(journal, "SELECT id, ts, event, from_state, to_state, actor, detail "
                            "FROM control_events WHERE event!='signal_cooldown' "
                            "ORDER BY id DESC LIMIT 30")
    for e in events:
        e["detail"] = _load(e["detail"])
    cooldowns = _rows(journal, "SELECT COUNT(*) n, MAX(ts) newest FROM (SELECT event, ts FROM "
                               "control_events ORDER BY id DESC LIMIT 500) "
                               "WHERE event='signal_cooldown'")[0]
    recovery = _load(journal.kv_get("execution_recovery", None))
    return {"generated_at": _iso(_now()), "window": window,
            "latest": latest_activity(journal),
            "timeline": _timeline(journal, rows, orders, events),
            "risk_blocks": sorted(({"reason": k, "n": n} for k, n in blocks.items()),
                                  key=lambda b: -b["n"]),
            "decisions": rows[:ACTIVITY_ROWS], "signals": signals, "scans": scans,
            "orders": orders, "control_events": events, "execution_recovery": recovery,
            "signal_cooldowns": {"in_newest_500_control_events": cooldowns["n"],
                                 "newest_at": cooldowns["newest"]},
            "unavailable": ([_unavailable(
                "signals", f"{malformed_signals} decision(s) in the window have malformed "
                           "signals_json; their signals are not listed")]
                if malformed_signals else []) + [
                _unavailable("candidates", "the scan's candidate list is served by "
                                           "/api/attention/latest when attention is enabled"),
                _unavailable("venue_orders", "orders are journal trades; open venue orders "
                                             "are read only by the kernel's protection "
                                             "snapshot")],
            "source": f"journal: newest {ACTIVITY_WINDOW} decisions by insertion order, 20 "
                      "newest trades, 30 newest non-cooldown control events, strategy "
                      "lifecycle brain_events, execution_recovery"}


# ── Knowledge ─────────────────────────────────────────────────────────────────
def knowledge_note(vault: Path, root: Path, note_id: str,
                   journal=None) -> tuple[dict | None, str | None]:
    base = vault.resolve()
    target = (base / note_id).resolve()
    if (not note_id.endswith(".md") or not str(target).startswith(str(base) + os.sep)
            or not target.is_file()):
        return None, "note_not_found"
    raw = target.read_bytes()
    text = raw[:NOTE_MAX_BYTES].decode("utf-8", "replace")
    from .owner_api import _frontmatter
    fm, body = _frontmatter(text)
    links = sorted({m.split("|")[0].split("#")[0].strip() for m in _WIKILINK.findall(text)})
    sources = []
    paths = fm.get("source_paths") if isinstance(fm.get("source_paths"), list) else []
    for p in paths[:50]:
        p = str(p)
        rp = (root / p).resolve()
        inside = str(rp).startswith(str(root.resolve()) + os.sep)
        sources.append({"path": p,
                        "kind": "code" if p.endswith(_CODE_SUFFIXES) else "document",
                        "exists": bool(inside and rp.exists())})
    chronology = []
    for key in ("day", "time", "updated", "created"):
        if fm.get(key) is not None:
            chronology.append({"at": str(fm[key]), "event": f"frontmatter:{key}",
                               "source": "note frontmatter"})
    mtime = datetime.fromtimestamp(target.stat().st_mtime, timezone.utc)
    chronology.append({"at": _iso(mtime), "event": "file_modified", "source": "filesystem"})
    try:
        log = subprocess.run(["git", "log", "-n", "20", "--format=%H%x09%cI%x09%s", "--",
                              str(target)], cwd=root, capture_output=True, text=True,
                             timeout=3)
        for line in log.stdout.splitlines():
            h, at, subject = (line.split("\t", 2) + ["", ""])[:3]
            chronology.append({"at": at, "event": "commit", "commit": h[:12],
                               "detail": subject, "source": "git history"})
        git_ok = log.returncode == 0
    except (OSError, subprocess.SubprocessError):
        git_ok = False
    chronology.sort(key=lambda e: e["at"] or "")
    mentions, family, records_error = None, None, None
    if journal is not None:
        try:
            mentions = resolve_mentions(journal, text)
            kind = fm.get("family")
            if isinstance(kind, str) and kind:
                family = {"kind": kind, "basis": "note frontmatter family equals registry "
                                                  "kind (exact)",
                          "strategies": _rows(journal, "SELECT id, name, state FROM "
                                                       "strategies WHERE kind=? LIMIT 50",
                                              (kind,))}
        except Exception as e:                              # noqa: BLE001
            mentions, records_error = None, type(e).__name__
    return {"generated_at": _iso(_now()), "id": note_id, "frontmatter": fm, "body": body,
            "records": mentions["links"] if mentions else [],
            "family": family, "records_error": records_error,
            "records_unresolved": mentions["unresolved"] if mentions else None,
            "records_resolved_count": mentions["resolved_count"] if mentions else None,
            "records_truncated_count": mentions["truncated_count"] if mentions else None,
            "records_unresolved_count": mentions["unresolved_count"] if mentions else None,
            "records_unexamined_tokens": mentions["unexamined_tokens"] if mentions else None,
            "records_basis": "identifiers in the note text that exist exactly in the journal "
                             "(strategy, trade, decision, research hash). Strategy notes "
                             "record the strategy name, not its id; names are not linked.",
            "truncated": len(raw) > NOTE_MAX_BYTES, "bytes": len(raw),
            "links": links, "sources": sources, "chronology": chronology,
            "git_history": "available" if git_ok else "unavailable",
            "observed_at": _iso(mtime), "time_basis": "file_modified",
            "source": f"knowledge/{note_id}"}, None


# ── Diagnostics ───────────────────────────────────────────────────────────────
def _proc_kv(path: str) -> dict:
    out = {}
    try:
        for line in Path(path).read_text().splitlines():
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def _kb(v: str | None) -> int | None:
    try:
        return int(str(v).split()[0]) * 1024
    except (TypeError, ValueError, IndexError):
        return None


def _health_file(p: Path, now: float) -> dict:
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return {"file": p.name, "status": "unavailable"}
    upd = d.get("updated_ms") if isinstance(d, dict) else None
    age = (now - float(upd) / 1000.0) if isinstance(upd, (int, float)) else None
    return {"file": p.name, "status": d.get("status") if isinstance(d, dict) else None,
            "updated_at": _iso(datetime.fromtimestamp(upd / 1000.0, timezone.utc))
            if age is not None else None, "age_s": age,
            "freshness": "unavailable" if age is None else
            ("fresh" if age <= HEALTH_STALE_S else "stale"),
            "last_error": d.get("last_error") if isinstance(d, dict) else None}


def diagnostics(journal, root: Path) -> dict:
    started = time.perf_counter()
    now = time.time()
    data = root / "data"
    storage = []
    for name in ("luffy.db", "luffy.db-wal", "candles.db", "derivs.db", "attention.db",
                 "investigation.db", "attention_learning.db"):
        p = data / name
        try:
            st = p.stat()
            storage.append({"file": f"data/{name}", "bytes": st.st_size,
                            "modified_at": _iso(datetime.fromtimestamp(st.st_mtime,
                                                                       timezone.utc))})
        except OSError:
            storage.append({"file": f"data/{name}", "bytes": None, "modified_at": None})
    try:
        du = shutil.disk_usage(data if data.exists() else root)
        disk = {"total": du.total, "used": du.used, "free": du.free}
    except OSError:
        disk = None
    try:
        load = [float(x) for x in Path("/proc/loadavg").read_text().split()[:3]]
    except (OSError, ValueError):
        load = None
    mem = _proc_kv("/proc/meminfo")
    me = _proc_kv("/proc/self/status")
    resources = {"host": {"cpus": os.cpu_count(), "loadavg": load,
                          "mem_total": _kb(mem.get("MemTotal")),
                          "mem_available": _kb(mem.get("MemAvailable"))},
                 "dashboard_process": {"pid": os.getpid(), "rss": _kb(me.get("VmRSS")),
                                       "threads": int(me["Threads"]) if me.get("Threads")
                                       else None}}
    wd_log = root / "logs" / "watchdog.log"
    try:
        with wd_log.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 16 * 1024))
            tail = f.read().replace(b"\x00", b"").decode("utf-8", "replace").splitlines()[-20:]
        wd_mtime = _iso(datetime.fromtimestamp(wd_log.stat().st_mtime, timezone.utc))
    except OSError:
        tail, wd_mtime = None, None
    off = data / "watchdog.off"
    watchdog = {"disabled_flag": off.exists(),
                "disabled_since": _iso(datetime.fromtimestamp(off.stat().st_mtime,
                                                              timezone.utc))
                if off.exists() else None,
                "log_tail": tail, "log_modified_at": wd_mtime,
                "note": "The flag stops scripts/watchdog.sh restarting processes; the log "
                        "records restarts it performed."}
    incidents = _rows(journal, "SELECT id, ts, event, from_state, to_state, actor, detail "
                               "FROM control_events WHERE event NOT IN ('signal_cooldown') "
                               "ORDER BY id DESC LIMIT 30")
    for e in incidents:
        e["detail"] = _load(e["detail"])
    audit = (_rows(journal, "SELECT id, ts, operation, channel, principal, status, reasons, "
                            "state_before, state_after FROM owner_audit ORDER BY id DESC "
                            "LIMIT 20") if _table_exists(journal, "owner_audit") else [])
    try:
        recovery = _load(journal.kv_get("execution_recovery", None))
    except Exception as e:                                  # noqa: BLE001
        recovery = {"error": type(e).__name__}
    collectors = [_health_file(data / n, now) for n in (
        "attention_health.json", "investigation_health.json",
        "attention_learning_health.json")]
    return {"generated_at": _iso(_now()), "storage": {"files": storage, "disk": disk},
            "resources": resources, "watchdog": watchdog, "collectors": collectors,
            "incidents": incidents, "owner_audit": audit, "execution_recovery": recovery,
            "api": {"diagnostics_read_ms": round((time.perf_counter() - started) * 1000, 1)},
            "unavailable": [
                _unavailable("kernel_resources", "the kernel heartbeat records no CPU or "
                                                 "memory figures"),
                _unavailable("incident_ledger", "no structured incident store; control "
                                                "events and owner audit are shown instead"),
                _unavailable("api_latency_history", "the dashboard keeps no request timing "
                                                    "history")],
            "source": "filesystem (data/, logs/watchdog.log, /proc), journal control_events, "
                      "owner_audit and execution_recovery, collector health files"}


def control_events_page(journal, before: int | None = None, limit: int = 50,
                        include_cooldown: bool = False) -> dict:
    """Keyset pages of control_events (id descending)."""
    limit = max(1, min(int(limit), 200))
    where, params = [], []
    if before is not None:
        where.append("id < ?")
        params.append(int(before))
    if not include_cooldown:
        where.append("event != 'signal_cooldown'")
    sql = ("SELECT id, ts, event, from_state, to_state, actor, detail FROM control_events "
           + (f"WHERE {' AND '.join(where)} " if where else "") + "ORDER BY id DESC LIMIT ?")
    rows = _rows(journal, sql, (*params, limit + 1))
    more = len(rows) > limit
    rows = rows[:limit]
    for e in rows:
        e["detail"] = _load(e["detail"])
    return {"generated_at": _iso(_now()), "events": rows, "before": before, "limit": limit,
            "include_cooldown": include_cooldown, "has_more": more,
            "next_before": rows[-1]["id"] if more and rows else None,
            "source": "journal control_events (keyset by id, newest first)"}


def probes(journal, root: Path, vault: Path) -> list[dict]:
    """Measured availability of the stores this API reads, at request time."""
    out = []

    def probe(name, fn):
        t = time.perf_counter()
        try:
            detail, ok = fn()
        except Exception as e:                              # noqa: BLE001
            detail, ok = type(e).__name__, False
        out.append({"probe": name, "ok": ok, "ms": round((time.perf_counter() - t) * 1000, 1),
                    "detail": detail})
    probe("journal_read", lambda: ("SELECT 1", _rows(journal, "SELECT 1 ok")[0]["ok"] == 1))
    probe("journal_newest_decision", lambda: (lambda r: (
        r[0]["ts"] if r else "no decisions", bool(r)))(
        _rows(journal, "SELECT ts FROM decisions ORDER BY rowid DESC LIMIT 1")))
    probe("knowledge_vault", lambda: (str(vault), Path(vault).is_dir()))
    probe("frontend_build", lambda: ("frontend/dist/index.html",
                                     (root / "frontend" / "dist" / "index.html").is_file()))
    return out


_DEC_COLS = ("id, cycle_id, ts, symbol, action, score, threshold, executed, skip_reason, "
             "size_usdt, entry_price, strategy_ids")
#: exact latest-match predicates; each matches a Journal partial index on ts
#: (idx_decisions_executed_ts / _directional_ts / _skipped_ts / _rejected_ts)
EXECUTED = "executed=1"
DIRECTIONAL = "action!='HOLD'"
SKIPPED = "action!='HOLD' AND executed=0"
REJECTED = ("action!='HOLD' AND executed=0 AND skip_reason IS NOT NULL AND "
            "skip_reason!=''")
#: each predicate is pinned to its partial index (INDEXED BY fails loudly
#: rather than silently falling back to a sort/scan)
_INDEX_FOR = {EXECUTED: "idx_decisions_executed_ts", DIRECTIONAL: "idx_decisions_directional_ts",
              SKIPPED: "idx_decisions_skipped_ts", REJECTED: "idx_decisions_rejected_ts"}


class ExactIndexMissing(LookupError):
    """The partial index an exact latest read needs does not exist (a journal
    not yet migrated by normal Journal initialization). Exact reads refuse
    instead of scanning; callers report UNAVAILABLE."""


def _require_index(journal, where: str) -> str:
    name = _INDEX_FOR[where]
    if not _rows(journal, "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
                 (name,)):
        raise ExactIndexMissing(name)
    return name


def latest_decisions(journal, where: str, limit: int = 1,
                     cols: str = _DEC_COLS) -> list[dict]:
    """Newest decisions matching one of the indexed predicates: exact, over
    the whole table (no lookback window), newest by ts then insertion.
    Raises ExactIndexMissing on an unmigrated journal."""
    return _rows(journal, f"SELECT {cols} FROM decisions INDEXED BY "
                          f"{_require_index(journal, where)} WHERE {where} "
                          "ORDER BY ts DESC, rowid DESC LIMIT ?", (int(limit),))


def latest_activity(journal) -> dict:
    """Exact latest records, not a newest-500 window.

    - latest_decision: newest decision row (insertion order).
    - latest_requested: newest directional (non-HOLD) decision — an entry was
      proposed; not that an order was sent.
    - latest_executed: newest decision the executor reported as entered
      (decisions.executed=1).
    - latest_rejection: newest directional, not executed decision with a
      recorded skip_reason.
    - latest_journal_execution: newest journal trade with exec_mode='live'
      (an order was sent to the demo venue) — a journal booking, with its
      fill evidence status.
    - latest_proven_fill: that trade only when a booking receipt verifies its
      exact venue fills; otherwise UNKNOWN (a newer unverified execution means
      the latest fill cannot be established from receipts).

    All filtered reads use partial indexes: exact, no lookback bound."""
    def one(rows):
        return {"found": bool(rows), "record": rows[0] if rows else None,
                "ref": _ref("decision", rows[0]["id"]) if rows else None}

    def exact(where):
        try:
            return one(latest_decisions(journal, where))
        except ExactIndexMissing as e:     # unknown, never "none found"
            return {"found": None, "record": None, "ref": None, "status": "UNAVAILABLE",
                    "reason": f"exact_index_missing:{e}"}
    out = {
        "latest_decision": one(_rows(journal, f"SELECT {_DEC_COLS} FROM decisions "
                                              "ORDER BY rowid DESC LIMIT 1")),
        "latest_requested": exact(DIRECTIONAL),
        "latest_executed": exact(EXECUTED),
        "latest_rejection": exact(REJECTED),
    }
    trade = _rows(journal, "SELECT id, decision_id, symbol, side, status, exec_mode, opened_at, "
                           "amount, entry_price FROM trades WHERE exec_mode='live' "
                           "ORDER BY opened_at DESC, id DESC LIMIT 1")
    execution = {"found": bool(trade), "record": trade[0] if trade else None,
                 "ref": _ref("trade", trade[0]["id"]) if trade else None,
                 "fill_evidence": None}
    proven = {"status": "UNKNOWN", "record": None, "ref": None,
              "reason": "no_journal_execution"}
    if trade:
        verified, receipts = False, 0
        if _table_exists(journal, "trade_accounting_bookings"):
            for r in _rows(journal, "SELECT id, trade_id, payload FROM "
                                    "trade_accounting_bookings WHERE trade_id=? ORDER BY id "
                                    "LIMIT 50", (trade[0]["id"],)):
                receipts += 1
                a = _receipt(r, trade[0]["id"])["assessment"]
                verified = verified or (isinstance(a, dict)
                                        and a.get("status") == FILLS_VERIFIED)
        execution["fill_evidence"] = ("VENUE_FILL_VERIFIED" if verified else
                                      "JOURNAL_BOOKED_UNVERIFIED")
        execution["receipts"] = receipts
        proven = ({"status": "PROVEN", "record": trade[0], "ref": execution["ref"],
                   "reason": None} if verified else
                  {"status": "UNKNOWN", "record": None, "ref": None,
                   "reason": "latest_journal_execution_not_verified"})
    out["latest_journal_execution"] = execution
    out["latest_proven_fill"] = proven
    return {"generated_at": _iso(_now()), **out,
            "semantics": {"requested": "directional decision recorded",
                          "executed": "executor reported the entry (decisions.executed=1)",
                          "journal_execution": "journal trade with exec_mode=live (order "
                                               "sent); not proof of a venue fill",
                          "proven_fill": "a booking receipt verifies exact venue fills"},
            "source": "journal decisions and trades; exact latest matches via indexes"}


def overview_activity(journal) -> dict:
    """Owner summary of recorded activity. Every item carries its recorded id."""
    missing: list[dict] = []
    rows = _rows(journal, "SELECT id, ts, symbol, action, executed, skip_reason, strategy_ids "
                          "FROM decisions ORDER BY rowid DESC LIMIT ?", (ACTIVITY_WINDOW,))
    blocks: dict[str, int] = {}
    for r in rows:
        if r["skip_reason"]:
            k = _reason_class(r["skip_reason"])
            blocks[k] = blocks.get(k, 0) + 1
    # exact newest executed/skipped over the whole journal (indexed), not the
    # newest-500 window; the window still feeds the block counts only
    cols = "id, ts, symbol, action, executed, skip_reason, strategy_ids"
    try:
        executed = latest_decisions(journal, EXECUTED, 5, cols)
        skipped = latest_decisions(journal, SKIPPED, 5, cols)
    except ExactIndexMissing as e:        # unmigrated journal: unknown, not empty
        executed = skipped = None
        missing.append(_unavailable("decisions.executed/skipped",
                                    f"exact_index_missing:{e}"))
    strategies = _rows(journal, "SELECT id, name, kind, state, state_changed_at FROM strategies "
                                "WHERE state IN ('active', 'paper') ORDER BY state, name "
                                "LIMIT 50")
    econ = {r["strategy_id"]: r for r in _rows(
        journal, "SELECT strategy_id, COUNT(*) trades, SUM(status='open') open, "
                 "SUM(CASE WHEN status='closed' THEN realized_pnl END) realized_pnl, "
                 "MAX(opened_at) last_opened FROM trades WHERE strategy_id IS NOT NULL "
                 "GROUP BY strategy_id")}
    for s in strategies:
        s["journal_economics"] = econ.get(s["id"])
    lifecycle = []
    if _table_exists(journal, "brain_events"):
        kinds = LIFECYCLE_KINDS
        ev = _brain(journal, f"kind IN ({','.join('?' * len(kinds))})", kinds, 8)
        reg = _registry(journal, [e["subject"] for e in ev])
        lifecycle = [{"event_id": e["id"], "at": e["ts"], "kind": e["kind"],
                      "strategy_id": e["subject"], "in_registry": e["subject"] in reg}
                     for e in ev]
    research_out = None
    if _table_exists(journal, "research_combos"):
        research_out = {
            "latest_run": (_rows(journal, "SELECT id, started, finished, tf, geo, round, n, ok, "
                                          "error FROM research_batches ORDER BY id DESC "
                                          "LIMIT 1") or [None])[0],
            "newest_results": _with_links(journal, _rows(
                journal, "SELECT hash, label, verdict, status, trigger, created_at FROM "
                         "research_combos ORDER BY rowid DESC LIMIT 5")),
            "survivors": _rows(journal, "SELECT COUNT(*) n FROM research_combos "
                                        "WHERE verdict='survivor'")[0]["n"],
            "ideas": assessed_ideas(journal, limit=5)}
    else:
        missing.append(_unavailable("research", "research ledger tables are absent"))

    def kv(key):
        v = _load(journal.kv_get(key, None))
        if v is None:
            missing.append(_unavailable(key, f"state_kv.{key} is not recorded"))
        elif not isinstance(v, dict) or v.get("_unparseable"):
            missing.append(_unavailable(key, f"state_kv.{key} is not a readable record"))
            return None
        return v
    requests = (_rows(journal, "SELECT request_id, operation, channel, state, created_at FROM "
                               "owner_requests WHERE state != 'DONE' ORDER BY created_at DESC "
                               "LIMIT 5") if _table_exists(journal, "owner_requests") else [])
    missing += [
        _unavailable("approvals", "no approval-object store exists; Needs You lists only the "
                                  "Supervisor's needs_owner and unresolved owner requests"),
        _unavailable("news_guard", "News Guard is not shown until its source state is "
                                   "trustworthy")]
    return {"generated_at": _iso(_now()),
            "decisions_basis": {"executed_skipped": "exact newest over all decisions",
                                "risk_blocks": f"newest {ACTIVITY_WINDOW} decisions"},
            "decisions": {"window": len(rows), "executed": executed, "skipped": skipped,
                          "risk_blocks": sorted(({"reason": k, "n": n} for k, n in
                                                 blocks.items()), key=lambda b: -b["n"])[:6]},
            "strategies": strategies, "lifecycle": lifecycle, "research": research_out,
            "risk_state": kv("risk_state"),
            "risk_state_note": "Risk baseline (high-water mark/day start), not a current "
                               "assessment; see /owner-api/v1/risk",
            "rent_state": kv("rent_state"),
            "latest": latest_activity(journal),
            "unresolved_owner_requests": requests, "unavailable": missing,
            "source": f"journal: newest {ACTIVITY_WINDOW} decisions, strategies (active/paper) "
                      "with journal-booked economics, brain_events, research ledger, "
                      "state_kv risk_state/rent_state, owner_requests"}


def observed_flows(journal, root: Path, declared: set[tuple[str, str]]) -> dict:
    """Records that prove data moved between components, over stated windows.

    A flow is listed only from records a component wrote; a declared
    architecture edge without such records is reported as unobserved, never
    as traffic."""
    now = _now()
    since15 = _iso(datetime.fromtimestamp(now.timestamp() - 900, timezone.utc))
    since24 = _iso(datetime.fromtimestamp(now.timestamp() - 86400, timezone.utc))
    flows = []

    def add(src, tgt, record, sql, params, window_s, cap=None, basis=""):
        try:
            r = _rows(journal, sql, params)[0]
            n, newest, err = r["n"], r["newest"], None
        except Exception as e:                              # noqa: BLE001
            n, newest, err = None, None, type(e).__name__
        flows.append({"source": src, "target": tgt, "record": record, "count": n,
                      "newest_at": newest, "window_s": window_s,
                      "saturated": cap is not None and n is not None and n >= cap,
                      "declared_edge": (src, tgt) in declared, "error": err,
                      "status": "unavailable" if err else ("observed" if n else "none_in_window"),
                      "basis": basis})
    add("market-data", "orchestrator", "journal cycles",
        "SELECT COUNT(*) n, MAX(ts) newest FROM cycles WHERE ts >= ?", (since15,), 900,
        basis="market cycles written (idx_cycles_ts)")
    add("orchestrator", "journal", "journal decisions",
        "SELECT COUNT(*) n, MAX(ts) newest FROM (SELECT ts FROM decisions ORDER BY rowid "
        "DESC LIMIT 500) WHERE ts >= ?", (since15,), 900, cap=500,
        basis="decisions among the newest 500 rows; saturated means at least 500")
    add("risk", "execution", "journal trades opened from a decision",
        "SELECT COUNT(*) n, MAX(opened_at) newest FROM trades WHERE opened_at >= ? AND "
        "decision_id IS NOT NULL", (since24,), 86400,
        basis="a trade row records the decision that passed Risk")
    add("execution", "journal", "journal trades closed",
        "SELECT COUNT(*) n, MAX(closed_at) newest FROM trades WHERE closed_at >= ?",
        (since24,), 86400, basis="exit bookings (journal records, not venue fills)")
    add("kernel", "journal", "journal equity",
        "SELECT COUNT(*) n, MAX(ts) newest FROM equity WHERE ts >= ?", (since15,), 900,
        basis="equity rows written each kernel cycle")
    if _table_exists(journal, "owner_audit"):
        add("owner-interface", "journal", "owner_audit",
            "SELECT COUNT(*) n, MAX(ts) newest FROM owner_audit WHERE ts >= ?", (since24,),
            86400, basis="owner requests audited by the Owner Interface")
    if _table_exists(journal, "research_batches"):
        add("research", "journal", "research_batches",
            "SELECT COUNT(*) n, MAX(started) newest FROM (SELECT started FROM research_batches "
            "ORDER BY id DESC LIMIT 1000) WHERE started >= ?", (since24,), 86400, cap=1000,
            basis="search batches run")
    if _table_exists(journal, "brain_events"):
        add("research", "journal", "brain_events",
            "SELECT COUNT(*) n, MAX(ts) newest FROM (SELECT ts FROM brain_events ORDER BY id "
            "DESC LIMIT 2000) WHERE ts >= ?", (since24,), 86400, cap=2000,
            basis="brain/research events (harvest, specs, postmortems)")
    add("supervisor", "journal", "control_events supervisor/reconcile",
        "SELECT COUNT(*) n, MAX(ts) newest FROM (SELECT ts, event FROM control_events ORDER BY "
        "id DESC LIMIT 2000) WHERE ts >= ? AND event IN ('supervisor_recovery', 'reconcile')",
        (since24,), 86400, cap=2000, basis="Supervisor recovery and reconcile events")
    # only a flow with records in its window proves a declared edge; a zero
    # count or a failed read proves nothing
    seen = {(f["source"], f["target"]) for f in flows
            if f["status"] == "observed" and f["count"]}
    unobserved = [{"source": s, "target": t,
                   "reason": "no record written by these components proves this connection"}
                  for s, t in sorted(declared) if (s, t) not in seen]
    return {"generated_at": _iso(now), "flows": flows, "unobserved_declared_edges": unobserved,
            "declared_edges": [{"source": a, "target": b} for a, b in sorted(declared)],
            "note": "Counts are records written in the window. They prove that data moved, "
                    "not rate, latency or health, and nothing is animated from them.",
            "source": "journal cycles/decisions/trades/equity/owner_audit/research_batches/"
                      "brain_events/control_events"}


#: identifier shapes the journal uses (trade pos_*, decision dec_*, research
#: 16-hex hash, strategy prefixes). Used only to report an id-shaped token that
#: resolves to no record; never to link anything.
_ID_SHAPE = re.compile(r"^(?:pos_[0-9a-f]{6,}|dec_[0-9a-f]{6,}|[0-9a-f]{16}|"
                       r"(?:spec|auth|seed|hav|strat|anl)_[A-Za-z0-9_]+|[a-z]+_g_[0-9a-f]{4})$")


def _shape_kind(token: str) -> str:
    return ("trade" if token.startswith("pos_") else "decision" if token.startswith("dec_")
            else "research" if _HEX16.match(token) else "strategy")


#: most distinct identifier-shaped tokens examined per text
MAX_TOKENS = 2000
#: most record links (and unresolved ids) returned for display
DISPLAY_LINKS = 50


def _tokens(text: str) -> tuple[list[str], int]:
    """(examined tokens, count of tokens not examined past MAX_TOKENS)."""
    every = sorted(set(_IDENT.findall((text or "")[:NOTE_MAX_BYTES])))
    return every[:MAX_TOKENS], max(0, len(every) - MAX_TOKENS)


def recognize(journal, text: str) -> list[dict]:
    """Every stored record whose exact identifier appears in `text`, by
    primary-key lookup (strategies, trades, decisions, research combos). Not
    capped for display: existence is decided on the complete result. Names and
    titles are never matched: a record is linked only when its id is present."""
    tokens, _ = _tokens(text)
    found: list[dict] = []
    if not tokens:
        return found

    def lookup(table, cols, kind, label, only=None):
        ids = [t for t in tokens if only is None or only(t)]
        key = cols.split(",")[0].strip()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in _rows(journal, f"SELECT {cols} FROM {table} WHERE {key} IN "
                                    f"({','.join('?' * len(chunk))})", tuple(chunk)):
                found.append({"kind": kind, "id": r[key], "label": label(r),
                              "basis": "exact_id"})
    lookup("strategies", "id, name, state", "strategy", lambda r: f"{r['name']} ({r['state']})")
    lookup("trades", "id, symbol, side, status", "trade",
           lambda r: f"{r['symbol']} {r['side']} ({r['status']})")
    lookup("decisions", "id, symbol, action, ts", "decision",
           lambda r: f"{r['action']} {r['symbol']} at {r['ts']}")
    if _table_exists(journal, "research_combos"):
        lookup("research_combos", "hash, label, verdict", "research",
               lambda r: f"{r['label']} ({r['verdict']})", only=lambda t: bool(_HEX16.match(t)))
    return found


def unresolved_ids(text: str, found: list[dict]) -> list[dict]:
    """Id-shaped tokens in `text` that no stored record has. `found` must be
    the COMPLETE exact-id result of recognize(), never a display-capped list."""
    have = {f["id"] for f in found}
    tokens, _ = _tokens(text)
    return [{"token": t, "kind_hint": _shape_kind(t)} for t in tokens
            if t not in have and _ID_SHAPE.match(t)]


def resolve_mentions(journal, text: str) -> dict:
    """Exact-id mentions in `text`: existence is classified on the complete
    lookup first, and only then are the displayed lists capped. A record cut by
    the display cap is counted as truncated, never reported as not found."""
    found = recognize(journal, text)
    unresolved = unresolved_ids(text, found)
    _, unexamined = _tokens(text)
    return {"links": found[:DISPLAY_LINKS], "resolved_count": len(found),
            "truncated_count": max(0, len(found) - DISPLAY_LINKS),
            "unresolved": unresolved[:DISPLAY_LINKS], "unresolved_count": len(unresolved),
            "unexamined_tokens": unexamined}


# ── HTTP ──────────────────────────────────────────────────────────────────────
def install(app, *, journal, root: Path, vault: Path) -> None:
    """Mount the read contracts. Callers install the auth Guard."""

    def found(data, missing: str):
        return _json(data) if data else _json({"error": missing}, 404)

    @app.get(PREFIX + "/trades/{trade_id}/lineage")
    def owner_trade_lineage(trade_id: str):
        return found(trade_lineage(journal, trade_id), "trade_not_found")

    @app.get(PREFIX + "/decisions/{decision_id}")
    def owner_decision(decision_id: str):
        return found(decision_detail(journal, decision_id), "decision_not_found")

    @app.get(PREFIX + "/research")
    def owner_research(limit: int = 50, offset: int = 0):
        return _json(research(journal, limit, offset))

    @app.get(PREFIX + "/research/combos/{combo_hash}")
    def owner_research_item(combo_hash: str):
        if not _table_exists(journal, "research_combos"):
            return _json({"error": "research_ledger_absent"}, 404)
        return found(research_item(journal, combo_hash), "research_item_not_found")

    @app.get(PREFIX + "/strategies/{sid}")
    def owner_strategy(sid: str):
        return found(strategy_detail(journal, sid), "strategy_not_found")

    @app.get(PREFIX + "/operations/activity")
    def owner_operations():
        return _json(operations(journal))

    @app.get(PREFIX + "/activity/latest")
    def owner_latest_activity():
        return _json(latest_activity(journal))

    @app.get(PREFIX + "/overview/activity")
    def owner_overview_activity():
        return _json(overview_activity(journal))

    @app.get(PREFIX + "/system/observed")
    def owner_system_observed():
        from .owner_api import SYSTEM_EDGES
        return _json(observed_flows(journal, root, set(SYSTEM_EDGES)))

    @app.get(PREFIX + "/knowledge/note")
    def owner_note(id: str):                                # noqa: A002
        try:
            data, err = knowledge_note(Path(vault), root, id, journal)
        except OSError:
            return _json({"error": "note_unreadable"}, 503)
        return _json(data) if data else _json({"error": err}, 404)

    @app.get(PREFIX + "/diagnostics")
    def owner_diagnostics():
        data = diagnostics(journal, root)
        data["probes"] = probes(journal, root, Path(vault))
        return _json(data)

    @app.get(PREFIX + "/diagnostics/events")
    def owner_control_events(before: int | None = None, limit: int = 50,
                             include_cooldown: bool = False):
        return _json(control_events_page(journal, before, limit, include_cooldown))
