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
            decision = dict(d[0], signals=_load(d[0].pop("signals_json"), []))
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
    missing.append(_unavailable("strategy_version_at_entry",
                                "the trade records the strategy id only; no version or spec "
                                "hash was stored when it opened"))

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
    return {"generated_at": _iso(_now()), "trade": trade, "decision": decision,
            "strategy": strategy, "accounting": {
                "receipts": receipts, "fills_verified": fills_verified,
                "verified_legs": verified_legs,
                "replay": ("trader.engine.booking.export(db, trade_id) re-verifies every "
                           "receipt's sha256 and assessment") if receipts else None},
            "outcome": outcome, "unavailable": missing,
            "source": "journal trades/decisions/strategies/outcomes and "
                      "trade_accounting_bookings (joins only by recorded ids)"}


# ── Research ──────────────────────────────────────────────────────────────────
_COMBO_COLS = ("hash, tf, geo, k, round, parent, trigger, window, parts, status, label, "
               "verdict, reason, consistency_p, median_pf, total_pct, max_dd_pct, trades, "
               "scored_symbols, testable, created_at")


def research(journal, limit: int = 50) -> dict:
    limit = max(1, min(int(limit), 200))
    out: dict = {"generated_at": _iso(_now()),
                 "source": "journal research ledger (trader/research/ledger.py)"}
    if not _table_exists(journal, "research_combos"):
        out.update(available=False, reason="research ledger tables are absent")
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
    results = _rows(journal, f"SELECT {_COMBO_COLS} FROM research_combos "
                             f"ORDER BY created_at DESC LIMIT ?", (limit,))
    for r in results:
        r["parts"] = _load(r["parts"], [])
    out["results"] = results
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
    out["unavailable"] = [
        _unavailable("questions", "no persisted research-question store"),
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
    detail["lifecycle"].sort(key=lambda e: e["at"] or "")
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
        _unavailable("health", "no rolling health or decay record is stored per strategy"),
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


def operations(journal) -> dict:
    # rowid order is journal insertion order: the newest rows without a full
    # sort of the decisions table (there is no ts index)
    rows = _rows(journal, "SELECT id, cycle_id, ts, symbol, action, score, threshold, "
                          "executed, skip_reason, strategy_ids, signals_json, scan_id, meta_p "
                          "FROM decisions ORDER BY rowid DESC LIMIT ?", (ACTIVITY_WINDOW,))
    blocks: dict[str, int] = {}
    signals = []
    for r in rows:
        sig = _load(r.pop("signals_json"), [])
        r["signals"] = sig if isinstance(sig, list) else []
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
              "newest": rows[0]["ts"] if rows else None}
    orders = _rows(journal, "SELECT id, decision_id, symbol, side, status, exec_mode, "
                            "opened_at, closed_at, strategy_id, amount, entry_price, "
                            "stop_loss, sl_order_id IS NOT NULL AND sl_order_id!='' "
                            "stop_ref_recorded FROM trades ORDER BY opened_at DESC LIMIT 20")
    events = _rows(journal, "SELECT id, ts, event, from_state, to_state, actor, detail "
                            "FROM control_events ORDER BY id DESC LIMIT 30")
    for e in events:
        e["detail"] = _load(e["detail"])
    recovery = _load(journal.kv_get("execution_recovery", None))
    return {"generated_at": _iso(_now()), "window": window,
            "risk_blocks": sorted(({"reason": k, "n": n} for k, n in blocks.items()),
                                  key=lambda b: -b["n"]),
            "decisions": rows[:ACTIVITY_ROWS], "signals": signals, "scans": scans,
            "orders": orders, "control_events": events, "execution_recovery": recovery,
            "unavailable": [
                _unavailable("candidates", "the scan's candidate list is served by "
                                           "/api/attention/latest when attention is enabled"),
                _unavailable("venue_orders", "orders are journal trades; open venue orders "
                                             "are read only by the kernel's protection "
                                             "snapshot")],
            "source": f"journal: newest {ACTIVITY_WINDOW} decisions by insertion order, 20 "
                      "newest trades, 30 newest control events, execution_recovery"}


# ── Knowledge ─────────────────────────────────────────────────────────────────
def knowledge_note(vault: Path, root: Path, note_id: str) -> tuple[dict | None, str | None]:
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
    return {"generated_at": _iso(_now()), "id": note_id, "frontmatter": fm, "body": body,
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


# ── HTTP ──────────────────────────────────────────────────────────────────────
def install(app, *, journal, root: Path, vault: Path) -> None:
    """Mount the read contracts. Callers install the auth Guard."""

    @app.get(PREFIX + "/trades/{trade_id}/lineage")
    def owner_trade_lineage(trade_id: str):
        data = trade_lineage(journal, trade_id)
        return _json(data) if data else _json({"error": "trade_not_found"}, 404)

    @app.get(PREFIX + "/research")
    def owner_research(limit: int = 50):
        return _json(research(journal, limit))

    @app.get(PREFIX + "/strategies/{sid}")
    def owner_strategy(sid: str):
        data = strategy_detail(journal, sid)
        return _json(data) if data else _json({"error": "strategy_not_found"}, 404)

    @app.get(PREFIX + "/operations/activity")
    def owner_operations():
        return _json(operations(journal))

    @app.get(PREFIX + "/knowledge/note")
    def owner_note(id: str):                                # noqa: A002
        try:
            data, err = knowledge_note(Path(vault), root, id)
        except OSError:
            return _json({"error": "note_unreadable"}, 503)
        return _json(data) if data else _json({"error": err}, 404)

    @app.get(PREFIX + "/diagnostics")
    def owner_diagnostics():
        return _json(diagnostics(journal, root))
