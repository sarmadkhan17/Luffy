"""Owner frontend read API (/owner-api/v1) and the isolated React preview route.

Read-only. Nothing here sets control state, places orders or talks to Risk or
Execution: owner controls stay on the GraphQL mutations, which go to the
kernel's Owner Interface. This module only reads local stores (journal,
heartbeat, supervisor status, knowledge vault) and reports what it cannot see
as missing or stale instead of defaulting it:

- a missing value is ``None`` plus a reason in ``errors``, never ``0``;
- every section carries its source and the source's own time;
- venue protection is only ever what the kernel's last read-only protection
  snapshot recorded (table ``protection_evidence``), with its own age; no
  request here reaches the venue, and a journal stop id is labelled as a
  journal record, not venue verification.

Network lookups (mark prices) are a separate enrichment endpoint so the
overview never waits on the venue.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import re
import secrets
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response

from . import current_truth, owner_reads, trade_history, valuation
from ..core import truth

log = logging.getLogger("dashboard.owner_api")

PREFIX = "/owner-api/v1"
HEARTBEAT_STALE_S = 240.0          # engine.watchdog.start_stall_monitor default
EQUITY_STALE_S = 300.0             # equity is logged every kernel cycle
PROTECTION_STALE_S = 180.0         # 3 × Supervisor interval (60 s)
# Protection snapshot evidence: 2 × its 60 s fixed-start cadence. A failed
# publication therefore leaves the previous result looking current for at most
# this long after its own checked_at. Mirrors protection_snapshot.STALE_AFTER_S.
SNAPSHOT_STALE_S = 120.0
# Any source time later than the reader's clock is invalid (core.truth): no skew.
ACTIVITY_STALE_S = 900.0
EQUITY_WINDOW_H = 168
CHAT_MAX_CHARS = 2000
CHAT_MAX_HISTORY = 20
CHAT_CONCURRENCY = 2
CHAT_LINK_TIMEOUT_S = 5.0
KNOWLEDGE_MAX_NODES = 500
_RELATION = re.compile(r"^[a-z][a-z_]{0,40}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse(ts) -> datetime | None:
    """A source time, or None when missing or malformed (bool, NaN/inf,
    pre-2020, unparseable) — see core.truth.parse_time."""
    return truth.parse_time(ts)[0]


def _age(dt: datetime | None, now: datetime) -> float | None:
    # a future age stays unrounded: +0.01 s must not round to a fresh 0.0
    return truth.display_age((now - dt).total_seconds()) if dt else None


def _freshness(age: float | None, stale_after: float) -> str:
    """fresh | stale | unavailable (no time) | invalid (source time in the future)."""
    return truth.classify_age(age, stale_after)


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _nocache(resp: Response) -> Response:
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


def _json(data, status: int = 200) -> Response:
    return _nocache(JSONResponse(data, status_code=status))


def _sym_key(symbol: str) -> str:
    """BTC/USDT, BTC/USDT:USDT and BTCUSDT → BTCUSDT (for matching reasons)."""
    s = str(symbol).upper().split(":")[0]
    return re.sub(r"[^A-Z0-9]", "", s)


def backend_version(root: Path) -> dict:
    sha = None
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                             text=True, timeout=2).stdout.strip() or None
    except Exception:
        pass
    return {"commit": sha, "started_at": _iso(_now())}


# ── sections (each returns (value, error)) ───────────────────────────────────
def read_control(journal, now):
    raw = journal.kv_get("control_state")          # no default: missing is missing
    ev = journal.query("SELECT id, ts, event, from_state, to_state, actor FROM control_events "
                       "ORDER BY id DESC LIMIT 1")
    if raw is None:
        return None, "control_state_missing"
    return {"state": str(raw), "source": "journal state_kv.control_state",
            "observed_at": _iso(now),
            "last_event": ev[0] if ev else None}, None


def read_heartbeat(root: Path, now):
    try:
        data = json.loads((root / "data" / "heartbeat_luffy.json").read_text())
        stamp = data["timestamp"]
    except (OSError, ValueError, KeyError, TypeError):
        return None, "heartbeat_unreadable"
    at = _parse(stamp)
    age = _age(at, now)
    return {"observed_at": _iso(at), "age_s": age,
            # a present but malformed time is an invalid clock, not "no data"
            "freshness": _freshness(age, HEARTBEAT_STALE_S) if at else "invalid",
            "stale_after_s": HEARTBEAT_STALE_S,
            "reported_state": data.get("state"),
            "source": "data/heartbeat_luffy.json (kernel cycle heartbeat)"}, None


def read_account(journal, now):
    """Equity with its provenance (current_truth.read_account): the age is the
    venue read that produced the value, never the cycle that reused it."""
    return current_truth.read_account(journal, now)


def read_equity_series(journal, now):
    since = (now.timestamp() - EQUITY_WINDOW_H * 3600)
    since_iso = datetime.fromtimestamp(since, timezone.utc).isoformat(timespec="seconds")
    rows = journal.query(current_truth.equity_rows_sql(
        "WHERE e.ts >= ?", "e.ts", limit=False,
        provenance=current_truth.has_provenance_table(journal)), (since_iso,))
    buckets: dict[int, dict] = {}
    for r in rows:
        at, eq = _parse(r["ts"]), _num(r["equity"])
        if at is None or eq is None:
            continue
        hour = int(at.timestamp()) // 3600 * 3600
        # last value in the hour; `time` is the row WRITE time, the value's
        # own source time is source_observed_at (a reused fallback keeps it)
        buckets[hour] = {"time": int(at.timestamp()), "value": eq,
                         **current_truth.row_provenance(r["provenance"], r["equity"])}
    points = [buckets[h] for h in sorted(buckets)]
    return {"points": points, "window_hours": EQUITY_WINDOW_H,
            "bucket": "last journal equity record per UTC hour (time = row write time)",
            "raw_records": len(rows),
            "source": "journal equity table"}, None


def read_realized_today(journal, now):
    day = now.strftime("%Y-%m-%d")
    r = journal.query("SELECT COUNT(*) n, SUM(realized_pnl) s FROM trades "
                      "WHERE status='closed' AND closed_at LIKE ?", (f"{day}%",))[0]
    # no closed trade today is a real zero; a closed trade with a NULL sum is missing
    return {"value": _num(r["s"]) if r["n"] else 0.0,
            "closed_trades": int(r["n"] or 0), "day_utc": day,
            "source": "journal trades closed today (UTC), journal-booked realized P&L"}, None


def read_supervisor(journal, now):
    raw = journal.kv_get("supervisor_status")
    if not raw:
        return None, "supervisor_status_missing"
    try:
        s = json.loads(raw)
        checks = s.get("checks") or {}
        reasons = [str(x) for x in (s.get("reasons") or [])]
    except (TypeError, ValueError, AttributeError):
        return None, "supervisor_status_unreadable"
    at = _parse(s.get("updated_at"))
    age = _age(at, now)
    freshness = _freshness(age, PROTECTION_STALE_S)
    if at is None and s.get("updated_at") not in (None, ""):
        freshness = "invalid"
    reported = {k: checks.get(k) is True
                for k in ("venue_positions", "reconciliation", "venue_protection")}
    current = freshness == "fresh"
    return {"observed_at": _iso(at), "age_s": age,
            "freshness": freshness,
            "stale_after_s": PROTECTION_STALE_S,
            "outcome": s.get("outcome"), "stage": s.get("stage"),
            "control_state_observed": s.get("control_state_observed"),
            # a check result is current only while the pass itself is fresh;
            # an old pass's True is last_reported, never a current proof
            "venue_positions": reported["venue_positions"] if current else None,
            "reconciliation": reported["reconciliation"] if current else None,
            "venue_protection": reported["venue_protection"] if current else None,
            "checks_are_current": current,
            "last_reported_checks": reported,
            "needs_owner": bool(s.get("needs_owner")),
            "needs_owner_since_control_event_id": s.get("needs_owner_since_control_event_id"),
            "reasons": reasons,
            "source": "journal state_kv.supervisor_status (Supervisor venue reconciliation pass)"}, None


SNAPSHOT_SOURCE = ("journal protection_evidence (kernel read-only venue "
                   "protection check; never triggered by this server)")


def read_protection_snapshot(journal, now):
    """The kernel's latest read-only protection snapshot, with its own age.

    Reader states: missing (no snapshot yet) → error ``protection_snapshot_missing``;
    malformed row → ``protection_snapshot_unreadable``; ``freshness`` fresh |
    stale | invalid (future/unparseable time), always from the stored
    snapshot's own ``checked_at`` — never from this request's time or from
    when the row was written; ``status`` VERIFIED | PARTIAL | UNREADABLE.
    """
    try:
        rows = journal.query("SELECT boot, seq, value FROM protection_evidence WHERE slot = 1")
    except Exception as e:
        if "no such table" in str(e):
            return None, "protection_snapshot_missing"
        raise
    if not rows:
        return None, "protection_snapshot_missing"
    try:
        row = rows[0]
        s = json.loads(row["value"])
        checks = s["checks"]
        status = s["status"]
        gen = s["generation"]
        if (status not in ("VERIFIED", "PARTIAL", "UNREADABLE") or not isinstance(checks, dict)
                or s.get("schema") != 2 or not isinstance(gen, dict)
                or type(row["boot"]) is not int or type(row["seq"]) is not int
                or gen.get("boot") != row["boot"] or gen.get("seq") != row["seq"]):
            raise ValueError
        reasons = [str(x) for x in (s.get("reasons") or [])]
        symbols = [x for x in (s.get("symbols") or []) if isinstance(x, dict)]
        cleanliness = s.get("cleanliness") if isinstance(s.get("cleanliness"), dict) else {}
    except (TypeError, ValueError, KeyError, AttributeError, IndexError):
        return None, "protection_snapshot_unreadable"
    at = _parse(s.get("checked_at"))
    age = _age(at, now)
    if at is None or age is None or age < 0:
        freshness = "invalid"
    else:
        freshness = _freshness(age, SNAPSHOT_STALE_S)
    items = [x for x in (cleanliness.get("items") or []) if isinstance(x, dict)]
    return {"observed_at": _iso(at), "age_s": age, "freshness": freshness,
            "stale_after_s": SNAPSHOT_STALE_S, "status": status,
            "generation": {"boot": row["boot"], "seq": row["seq"]},
            "completed_at": s.get("completed_at"),
            "control_state_observed": s.get("control_state_observed"),
            "venue_positions": checks.get("venue_positions") is True,
            "observation_consistent": checks.get("observation_consistent") is True,
            "reconciliation": checks.get("reconciliation") is True,
            "venue_protection": checks.get("venue_protection") is True,
            "precision_known": checks.get("precision_known") is True,
            "complete_listing": s.get("complete_listing") is True,
            "position_count": s.get("position_count"),
            "symbols": symbols,
            "cleanliness": {"status": str(cleanliness.get("status") or "UNKNOWN"),
                            "coverage": cleanliness.get("coverage"),
                            "items": items,
                            "items_truncated": cleanliness.get("items_truncated") is True,
                            "unread_symbols": cleanliness.get("unread_symbols") or []},
            "reasons": reasons, "duration_ms": s.get("duration_ms"),
            "venue_requests": s.get("venue_requests"),
            "source": SNAPSHOT_SOURCE}, None


def _all_checks(snap: dict) -> bool:
    return (snap["status"] == "VERIFIED" and snap["venue_positions"] is True
            and snap["observation_consistent"] is True
            and snap["reconciliation"] is True and snap["venue_protection"] is True
            and snap["precision_known"] is True and snap["complete_listing"] is True)


def protection_for(trade: dict, snap: dict | None) -> dict:
    """Venue protection for one journal position, from the protection snapshot only.

    VERIFIED    a fresh snapshot read the venue position, the complete stop
                listing, found an adequate stop for this position and nothing a
                reconciliation would change (every check true)
    STALE       the snapshot is older than stale_after_s (or has an invalid
                time); its result is kept as ``last_reported``, never current
    UNREADABLE  the snapshot could not read positions or the full stop listing
    UNPROTECTED the snapshot found no stop at all for this venue position
    PARTIAL     readable, but this position or the book is not fully verified
    UNVERIFIED  the snapshot has no venue position for this journal row, or the
                position opened after the snapshot
    UNAVAILABLE no readable snapshot at all (not verified)
    """
    if snap is None:
        return {"status": "UNAVAILABLE", "observed_at": None, "last_reported": None,
                "reasons": ["no_protection_snapshot"], "venue": None}
    key = _sym_key(trade.get("symbol", ""))
    rec = next((r for r in snap["symbols"] if _sym_key(r.get("symbol", "")) == key), None)
    opened = _parse(trade.get("opened_at"))
    at = _parse(snap["observed_at"])
    mine = [str(r) for r in (rec or {}).get("reasons") or []]
    if snap["status"] == "UNREADABLE":
        reported, mine = "UNREADABLE", mine + snap["reasons"]
    elif at is not None and opened is not None and opened > at:
        reported, mine = "UNVERIFIED", mine + ["opened_after_last_verification"]
    elif rec is None:
        reported, mine = "UNVERIFIED", mine + ["no_venue_position_in_snapshot"]
    elif rec.get("stop_present") is not True:
        reported = "UNPROTECTED"
    elif rec.get("verified") is True and _all_checks(snap) and opened is not None:
        reported = "VERIFIED"
    else:
        reported = "PARTIAL"
        others = [r for r in snap["reasons"] if r not in mine]
        mine = mine + others or ["complete_verification_evidence_missing"]
    if snap["freshness"] != "fresh":
        why = "snapshot_time_invalid" if snap["freshness"] == "invalid" else "verification_stale"
        return {"status": "STALE", "observed_at": snap["observed_at"],
                "last_reported": reported, "reasons": list(dict.fromkeys(mine + [why])),
                "venue": rec}
    return {"status": reported, "observed_at": snap["observed_at"],
            "last_reported": None, "reasons": list(dict.fromkeys(mine)), "venue": rec}


def book_protection(snap: dict | None, positions: list[dict]) -> str:
    """Worst of the positions, and the book itself (untracked positions, orphans)."""
    order = ["UNAVAILABLE", "UNREADABLE", "UNPROTECTED", "STALE", "PARTIAL",
             "UNVERIFIED", "VERIFIED", "NO_POSITIONS"]
    if snap is None:
        book = "UNAVAILABLE"
    elif snap["freshness"] != "fresh":
        book = "STALE"
    elif snap["status"] == "UNREADABLE":
        book = "UNREADABLE"
    elif not _all_checks(snap):
        book = "PARTIAL"
    else:
        book = "VERIFIED" if positions else "NO_POSITIONS"
    return min([book] + [p["protection"]["status"] for p in positions], key=order.index)


def read_positions(journal, snap):
    out = []
    for t in journal.open_trades():
        notional = _num(t.get("notional_usdt"))
        if notional is None:
            a, p = _num(t.get("amount")), _num(t.get("entry_price"))
            notional = a * p if a is not None and p is not None else None
        stop = _num(t.get("stop_loss"))
        out.append({
            "id": str(t["id"]), "symbol": t["symbol"], "side": t["side"],
            "amount": _num(t.get("amount")), "entry_price": _num(t.get("entry_price")),
            "notional_usdt": notional, "leverage": t.get("leverage"),
            "opened_at": t.get("opened_at"), "strategy_name": t.get("strategy_name") or None,
            "exec_mode": t.get("exec_mode"),
            "journal_stop": {"price": stop if stop else None,
                             "order_ref_recorded": bool(t.get("sl_order_id")),
                             "source": "journal trades row (not venue verification)"},
            "protection": protection_for(t, snap),
        })
    return out, None


def naked_exposure(snap: dict | None) -> dict:
    """Venue positions without a stop, from a fresh complete snapshot only.
    No snapshot, a stale/invalid one or an incomplete listing is UNAVAILABLE
    (value None) — an old zero is never a current zero."""
    if snap is None:
        return {"value": None, "status": "UNAVAILABLE", "reasons": ["no_protection_snapshot"],
                "observed_at": None}
    if snap["freshness"] != "fresh":
        return {"value": None, "status": "STALE" if snap["freshness"] == "stale"
                else "UNAVAILABLE", "reasons": ["verification_" + snap["freshness"]],
                "observed_at": snap["observed_at"]}
    if (snap["status"] == "UNREADABLE" or snap["venue_positions"] is not True
            or snap["complete_listing"] is not True):
        return {"value": None, "status": "UNAVAILABLE",
                "reasons": ["venue_listing_incomplete"], "observed_at": snap["observed_at"]}
    naked = sum(1 for r in snap["symbols"] if r.get("stop_present") is not True)
    return {"value": naked, "status": "OBSERVED", "reasons": [],
            "observed_at": snap["observed_at"], "source": SNAPSHOT_SOURCE}


def overview(journal, root: Path, cfg: dict | None = None) -> dict:
    now = _now()
    errors: dict[str, str] = {}

    def section(name, fn, *args):
        try:
            value, err = fn(*args)
        except Exception as e:                       # a read never breaks the page
            log.debug("owner overview %s failed: %s", name, e)
            value, err = None, f"{name}_unreadable:{type(e).__name__}"
        if err:
            errors[name] = err
        return value

    control = section("control", read_control, journal, now)
    heartbeat = section("heartbeat", read_heartbeat, root, now)
    account = section("account", read_account, journal, now)
    series = section("equity_series", read_equity_series, journal, now)
    realized = section("realized_today", read_realized_today, journal, now)
    sup = section("supervisor", read_supervisor, journal, now)
    snap = section("protection_snapshot", read_protection_snapshot, journal, now)
    positions = section("positions", read_positions, journal, snap)
    news = section("news_guard", current_truth.read_news_guard, journal, now)
    risk = section("risk", current_truth.read_risk, journal, now, cfg, root)
    exposure = None
    if positions is not None and account and account.get("equity"):
        notionals = [p["notional_usdt"] for p in positions]
        if all(n is not None for n in notionals):
            gross = sum(notionals)
            exposure = {"gross_entry_notional": round(gross, 2),
                        "pct_of_equity": round(gross / account["equity"] * 100, 2),
                        "source": "journal open-trade entry notional ÷ journal equity"}
        else:
            errors["exposure"] = "position_notional_missing"
    elif "exposure" not in errors:
        errors["exposure"] = "requires_positions_and_equity"
    if positions is None:
        protection = None
    else:
        protection = {"status": book_protection(snap, positions), "snapshot": snap,
                      "supervisor": sup, "naked_exposure": naked_exposure(snap)}
    needs = None
    if sup is not None:
        needs = {"needs_owner": sup["needs_owner"], "reasons": sup["reasons"],
                 "since_control_event_id": sup["needs_owner_since_control_event_id"],
                 "observed_at": sup["observed_at"], "freshness": sup["freshness"],
                 "source": sup["source"]}
    return {"mode": "LIVE", "generated_at": _iso(now), "control": control,
            "heartbeat": heartbeat, "account": account, "equity_series": series,
            "realized_today": realized, "exposure": exposure, "positions": positions,
            "protection": protection, "needs_you": needs, "news_guard": news,
            "risk": risk, "errors": errors}


# ── knowledge (vault) ─────────────────────────────────────────────────────────
def _frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        import yaml
        data = yaml.safe_load(parts[1]) or {}
    except Exception:
        return {"_frontmatter_error": True}, parts[2]
    return (data if isinstance(data, dict) else {}), parts[2]


def knowledge(vault: Path, *, limit: int = KNOWLEDGE_MAX_NODES, strict: bool = False) -> dict:
    now = _now()
    files = sorted(vault.rglob("*.md")) if vault.exists() else []
    notes = []
    for p in files:
        try:
            text = p.read_text(errors="replace")
            mtime = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
        except OSError:
            if strict:
                raise
            continue
        fm, body = _frontmatter(text)
        title = next((ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")), None)
        rel = str(p.relative_to(vault))
        notes.append({"rel": rel, "stem": p.stem, "title": title or p.stem, "fm": fm,
                      "text": text, "mtime": mtime,
                      "folder": str(p.parent.relative_to(vault)) if p.parent != vault else ""})

    def norm(s: str) -> str:
        return re.sub(r"[\s_-]+", " ", str(s)).strip().casefold()

    by_name: dict[str, str] = {}
    for n in notes:
        for key in (n["stem"], n["title"]):
            by_name.setdefault(norm(key), n["rel"])

    edges, seen = [], set()
    for n in notes:
        rels = n["fm"].get("relations") if isinstance(n["fm"].get("relations"), dict) else {}
        for relation, targets in rels.items():
            if not _RELATION.match(str(relation)) or not isinstance(targets, list):
                continue
            for t in targets:
                tgt = by_name.get(norm(t))
                eid = hashlib.sha1(f"typed|{n['rel']}|{relation}|{t}".encode()).hexdigest()[:16]
                if eid in seen:
                    continue
                seen.add(eid)
                edges.append({"id": f"kt-{eid}", "source": n["rel"],
                              "target": tgt or f"unresolved:{t}", "relation": str(relation),
                              "kind": "typed", "resolved": tgt is not None,
                              "evidence": f"vault:{n['rel']}#relations.{relation}"})
        for m in re.findall(r"\[\[([^\]]+)\]\]", n["text"]):
            link = m.split("|")[0].split("#")[0].strip()
            tgt = by_name.get(norm(link))
            if tgt == n["rel"]:
                continue
            eid = hashlib.sha1(f"link|{n['rel']}|{link}".encode()).hexdigest()[:16]
            if eid in seen:
                continue
            seen.add(eid)
            edges.append({"id": f"kl-{eid}", "source": n["rel"],
                          "target": tgt or f"unresolved:{link}", "relation": "wikilink",
                          "kind": "link", "resolved": tgt is not None,
                          "evidence": f"vault:{n['rel']}"})
    degree: dict[str, int] = {}
    for e in edges:
        for k in (e["source"], e["target"]):
            degree[k] = degree.get(k, 0) + 1
    notes.sort(key=lambda n: (-degree.get(n["rel"], 0), n["rel"]))
    total = len(notes)
    kept = notes[:limit]
    nodes = []
    for n in kept:
        fm = n["fm"]
        kind = str(fm.get("type") or n["folder"] or "Note")
        status = fm.get("status")
        claim = fm.get("claim")
        nodes.append({
            "id": n["rel"], "label": n["title"], "kind": kind,
            "folder": n["folder"], "status": str(status) if status is not None else None,
            "degree": degree.get(n["rel"], 0),
            "provenance": {
                "id": f"vault:{n['rel']}", "source": f"knowledge/{n['rel']}",
                "observed_at": _iso(n["mtime"]), "time_basis": "file_modified",
                "summary": str(claim) if claim else None,
                "classification": " · ".join(x for x in (
                    kind, f"status {status}" if status is not None else None,
                    "context_only") if x)}})
    return {"generated_at": _iso(now), "source": "knowledge vault markdown (read-only)",
            "nodes": nodes, "edges": edges,
            "total_nodes": total, "returned_nodes": len(nodes), "truncated": total > len(nodes),
            "unresolved_edges": sum(1 for e in edges if not e["resolved"]),
            "lenses": {
                "Knowledge": {"available": True, "note": "All vault notes"},
                "Timeline": {"available": True,
                             "note": "Ordered by file modification time — not event chronology"},
                "Evidence": {"available": False,
                             "note": "No evidence-record store is exposed to the owner API yet"},
                "Code": {"available": False,
                         "note": "Code references are not part of the knowledge backend"}},
            "limits": ["No paging: at most %d notes per response" % limit,
                       "Relations are as written in note frontmatter/wikilinks; "
                       "not verified evidence or causality",
                       "Source time is file modification time"]}


class KnowledgeReadCache:
    """One bounded encoded read snapshot per app; no TTL or stale-error fallback.

    Stat fingerprint includes additions/deletions, renames, nanosecond mtime,
    ctime, inode and size. Recheck after parsing to reject a moving source.
    Original generated_at and note mtimes remain the snapshot's timestamps.
    """
    MAX_BYTES = 2 * 1024 * 1024
    MAX_FILES = 20000

    def __init__(self, vault: Path):
        self.vault = vault
        self.lock = threading.Lock()
        self.key = None
        self.encoded = None

    def fingerprint(self):
        if not self.vault.is_dir():
            raise FileNotFoundError("knowledge_vault_unavailable")
        def fail(error):
            raise error
        files = []
        for directory, _, names in os.walk(self.vault, onerror=fail, followlinks=False):
            for name in names:
                if name.endswith(".md"):
                    p = Path(directory) / name
                    st = p.stat()
                    files.append((str(p.relative_to(self.vault)), st.st_dev, st.st_ino,
                                  st.st_size, st.st_mtime_ns, st.st_ctime_ns))
        return tuple(sorted(files))

    def read(self) -> bytes:
        with self.lock:
            try:
                key = self.fingerprint()
                if key == self.key and self.encoded is not None:
                    return self.encoded
                self.key = self.encoded = None
                for _ in range(2):
                    payload = knowledge(self.vault, strict=True)
                    after = self.fingerprint()
                    if key == after:
                        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                                             separators=(",", ":")).encode()
                        if len(encoded) <= self.MAX_BYTES and len(key) <= self.MAX_FILES:
                            self.key, self.encoded = key, encoded
                        return encoded
                    key = after
                raise OSError("knowledge_source_changed_during_read")
            except Exception:
                self.key = self.encoded = None
                raise


# ── system topology ───────────────────────────────────────────────────────────
#: declared architecture (repository source map). Not observed traffic.
SYSTEM_COMPONENTS = (
    ("market-data", "Market Data", "trader/data/feed.py", 0, 0),
    ("attention", "Attention", "trader/observability/attention.py", 320, 0),
    ("orchestrator", "Orchestrator & analysts", "trader/engine/orchestrator.py", 640, 0),
    ("risk", "Risk", "trader/engine/risk.py", 960, 0),
    ("execution", "Execution", "trader/engine/executor.py", 960, 250),
    ("supervisor", "Supervisor", "trader/engine/supervisor.py", 640, 250),
    ("owner-interface", "Owner Interface", "trader/owner/service.py", 320, 250),
    ("journal", "Journal", "trader/core/journal.py", 960, 500),
    ("kernel", "Kernel cycle", "trader/kernel.py", 0, 250),
    ("research", "Research", "trader/research/", 640, 500),
    ("dashboard", "Dashboard server", "trader/dashboard/server.py", 320, 500),
)
SYSTEM_EDGES = (
    ("market-data", "attention"), ("attention", "orchestrator"),
    ("orchestrator", "risk"), ("risk", "execution"), ("execution", "journal"),
    ("supervisor", "execution"), ("owner-interface", "supervisor"),
    ("kernel", "owner-interface"), ("journal", "research"), ("journal", "dashboard"),
    ("dashboard", "owner-interface"),
)


def system(journal, root: Path, cfg: dict) -> dict:
    now = _now()
    tele: dict[str, dict] = {}

    def put(cid, at, health, summary, source, stale_after=ACTIVITY_STALE_S):
        age = _age(at, now)
        tele[cid] = {"observed_at": _iso(at), "age_s": age,
                     "freshness": _freshness(age, stale_after), "health": health,
                     "summary": summary, "source": source}

    hb, _ = _safe(read_heartbeat, root, now)
    if hb:
        put("kernel", _parse(hb["observed_at"]), "active",
            f"Heartbeat {hb['age_s']:.0f}s old; kernel reported state {hb.get('reported_state')}",
            hb["source"], HEARTBEAT_STALE_S)
    sup, _ = _safe(read_supervisor, journal, now)
    if sup:
        health = {"SAFE": "active", "RECOVERING": "degraded", "DEGRADED": "degraded",
                  "NEEDS_OWNER": "degraded"}.get(sup.get("outcome"), "unknown")
        put("supervisor", _parse(sup["observed_at"]), health,
            f"Last pass outcome {sup.get('outcome')}; stage {sup.get('stage')}; "
            f"reasons {', '.join(sup['reasons'][:4]) or 'none'}", sup["source"],
            PROTECTION_STALE_S)
    # newest by insertion order: decisions has no ts index, and a full sort of
    # the table for one row cost ~1.5 s per read on the production journal
    rows = _safe_rows(journal, "SELECT MAX(ts) ts FROM (SELECT ts FROM decisions "
                               "ORDER BY rowid DESC LIMIT 50)")
    rows = [r for r in rows if r["ts"]]
    if rows:
        put("orchestrator", _parse(rows[0]["ts"]), "unknown",
            "Latest journal decision record (activity, not health)", "journal decisions")
    rows = _safe_rows(journal, "SELECT ts FROM equity ORDER BY ts DESC LIMIT 1")
    if rows:
        put("journal", _parse(rows[0]["ts"]), "unknown",
            "Latest equity record written (activity, not health)", "journal equity")
    rows = _safe_rows(journal, "SELECT ts FROM brain_events ORDER BY id DESC LIMIT 1")
    if rows:
        put("research", _parse(rows[0]["ts"]), "unknown",
            "Latest brain/research event (activity, not health)", "journal brain_events")
    try:
        data = json.loads((root / "data" / "attention_health.json").read_text())
        if isinstance(data, dict) and data.get("updated_ms"):
            put("attention", _parse(float(data["updated_ms"]) / 1000.0), "unknown",
                f"Attention health status {data.get('status')}",
                "data/attention_health.json", float(
                    ((cfg.get("attention") or {}).get("stale_seconds")) or ACTIVITY_STALE_S))
    except (OSError, ValueError, TypeError):
        pass
    put("dashboard", now, "active", "This server answered the request", "dashboard process")
    nodes = []
    for cid, label, src, x, y in SYSTEM_COMPONENTS:
        t = tele.get(cid)
        nodes.append({"id": cid, "label": label, "kind": "Component", "source_file": src,
                      "x": x, "y": y,
                      "telemetry": t or {"observed_at": None, "age_s": None,
                                         "freshness": "unavailable", "health": None,
                                         "summary": "No telemetry source is exposed for this "
                                                    "component",
                                         "source": None}})
    edges = [{"id": f"arch-{s}-{t}", "source": s, "target": t, "kind": "architecture",
              "relation": "declared architectural connection"} for s, t in SYSTEM_EDGES]
    return {"generated_at": _iso(now), "nodes": nodes, "edges": edges, "events": [],
            "events_note": "No component activity event stream exists; no activity is animated",
            "architecture_source": "Repository source map (declared), not observed traffic"}


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        return None, type(e).__name__


def _safe_rows(journal, sql):
    try:
        return journal.query(sql)
    except Exception:
        return []


# ── HTTP ──────────────────────────────────────────────────────────────────────
def install(app, *, journal, cfg: dict, root: Path, auth, gateway, vault: Path | None = None,
            dist: Path | None = None, chat_factory=None, marks=None,
            quotes=None) -> None:
    """Mount /owner-api/v1 and the React owner frontend at / (both behind the
    dashboard's auth Guard, installed by the caller)."""
    from ..owner.authz import Authorizer
    version = backend_version(root)
    vault = vault or (root / "knowledge")          # knowledge.vault.VAULT for the real ROOT
    dist = dist or (root / "frontend" / "dist")
    knowledge_cache = KnowledgeReadCache(Path(vault))
    chat_slots = asyncio.Semaphore(CHAT_CONCURRENCY)
    section = (cfg or {}).get("owner_interface") or {}
    try:
        principal = Authorizer.from_config(cfg).resolve("dashboard", "session")
    except Exception:
        principal = None

    @app.get(PREFIX + "/bootstrap")
    def bootstrap(request: Request):
        method, expires = auth.session_info(request.scope)
        dist_index = dist / "index.html"
        try:
            build = hashlib.sha256(dist_index.read_bytes()).hexdigest()[:12]
        except OSError:
            build = None
        return _json({
            "mode": "LIVE",
            "principal": principal,
            "session": {"authenticated": True, "method": method,
                        "expires_at": _iso(_parse(expires)) if expires else None},
            "backend": {**version, "source_time": _iso(_now()), "frontend_build": build},
            "owner_interface": {
                "configured": section.get("enabled") is True and gateway is not None,
                "check": PREFIX + "/owner-interface",
                "note": "Availability is established by a live health read, not by configuration"},
            "providers": {"telegram": "CONFIGURED" if ((section.get("identities") or {})
                                                        .get("telegram")) else "NOT_CONFIGURED",
                          "openclaw": "NOT_CONFIGURED" if not ((section.get("identities") or {})
                                                              .get("openclaw")) else "CONFIGURED",
                          "whatsapp": "NOT_CONFIGURED" if not ((section.get("identities") or {})
                                                              .get("whatsapp")) else "CONFIGURED"},
            "capabilities": {"voice": False, "chat": "text", "controls":
                             ["freeze", "halt", "resume", "unhalt", "panic"]},
        })

    @app.get(PREFIX + "/overview")
    def owner_overview():
        return _json(overview(journal, root, cfg))

    @app.get(PREFIX + "/risk")
    def owner_risk():
        # configured limits vs the kernel's latest recorded assessment; a
        # journal/config read only, never Risk itself
        now = _now()
        data, err = _safe(current_truth.read_risk, journal, now, cfg, root)
        return _json({"generated_at": _iso(now), "risk": data, "error": err})

    @app.get(PREFIX + "/news-guard")
    def owner_news_guard():
        now = _now()
        data, err = _safe(current_truth.read_news_guard, journal, now)
        return _json({"generated_at": _iso(now), "news_guard": data, "error": err})

    @app.get(PREFIX + "/valuation")
    async def owner_valuation():
        """Unrealized P&L estimate (network enrichment; never on /overview)."""
        now = _now()
        if quotes is None:
            return _json({"generated_at": _iso(now), "valuation": None,
                          "error": "not_configured"})
        try:
            trades, q = await asyncio.wait_for(asyncio.to_thread(quotes, journal),
                                               timeout=10)
            data = valuation.estimate(trades, q, _now())
        except Exception as e:
            return _json({"generated_at": _iso(now), "valuation": None,
                          "error": f"valuation_unavailable:{type(e).__name__}"})
        return _json({"generated_at": _iso(now), "valuation": data, "error": None})

    @app.get(PREFIX + "/protection")
    def owner_protection():
        # a journal read of the kernel's last snapshot; never a venue call
        now = _now()
        snap, err = _safe(read_protection_snapshot, journal, now)
        return _json({"generated_at": _iso(now), "snapshot": snap,
                      "error": err, "source": SNAPSHOT_SOURCE})

    @app.get(PREFIX + "/enrichment/marks")
    async def owner_marks():
        now = _now()
        if quotes is not None:
            try:
                trades, q = await asyncio.wait_for(asyncio.to_thread(quotes, journal),
                                                   timeout=10)
                est = valuation.estimate(trades, q, _now())
            except Exception as e:
                return _json({"observed_at": None, "marks": {},
                              "error": f"marks_unavailable:{type(e).__name__}"})
            # Positions are per journal trade: `by_trade` is the per-position
            # truth. `marks`/`unvalued` stay keyed by symbol for existing
            # consumers; a symbol held by several trades publishes its shared
            # quote but no per-symbol upnl (one trade must not overwrite another).
            by_trade, by_sym = {}, {}
            for p in est["positions"]:
                qv = p["quote"] or {}
                ok = p["status"] == "OK"           # never a mark without a valid quote
                by_trade[p["trade_id"]] = {
                    "symbol": p["symbol"], "status": p["status"],
                    "mark": qv.get("price") if ok else None,
                    "upnl": p["upnl_estimate"] if ok else None,
                    "quote_basis": qv.get("basis") if ok else None,
                    "quote_observed_at": (qv.get("source_time") or qv.get("received_at"))
                    if ok else None,
                    "quote_age_s": qv.get("age_s") if ok else None,
                    "reasons": p["reasons"]}
                by_sym.setdefault(p["symbol"], []).append(p["trade_id"])
            out, unvalued = {}, {}
            for sym, ids in by_sym.items():
                rows = [by_trade[i] for i in ids]
                bad = [r["reasons"] for r in rows if r["status"] != "OK"]
                if bad:
                    unvalued[sym] = list(dict.fromkeys(x for b in bad for x in b))
                    continue
                r0 = rows[0]
                out[sym] = {k: r0[k] for k in ("mark", "upnl", "quote_basis",
                                               "quote_observed_at", "quote_age_s")}
                if len(rows) > 1:
                    out[sym].update(upnl=None, trade_ids=ids,
                                    upnl_scope="per_trade_only:multiple_positions")
            oldest = [m["quote_observed_at"] for m in out.values() if m["quote_observed_at"]]
            return _json({"observed_at": min(oldest) if oldest else None, "marks": out,
                          "by_trade": by_trade, "unvalued": unvalued,
                          "valuation_status": est["status"],
                          "total_upnl": est["total_upnl"],
                          "source": valuation.BASIS + "; " + valuation.EXCLUSIONS})
        if marks is None:
            return _json({"observed_at": None, "marks": {}, "error": "not_configured"})
        try:
            data = await asyncio.wait_for(asyncio.to_thread(marks, journal), timeout=10)
        except Exception as e:
            return _json({"observed_at": None, "marks": {},
                          "error": f"marks_unavailable:{type(e).__name__}"})
        # this provider records no quote time: its prices have no provenance, so
        # nothing is published as a current valuation (never the request time)
        unproven = sorted(str(sym) for sym in (data or {}))
        return _json({"observed_at": None, "marks": {}, "valuation_status": "UNAVAILABLE",
                      "total_upnl": None,
                      "unvalued": {sym: ["quote_time_unrecorded"] for sym in unproven},
                      "error": "marks_provenance_unavailable",
                      "source": "legacy mark provider without quote times; not published"})

    @app.get(PREFIX + "/owner-interface")
    async def owner_interface(request: Request):
        if gateway is None:
            return _json({"availability": "UNAVAILABLE", "reasons": ["not_configured"]})
        rid = secrets.token_hex(16)
        issued = time.time() * 1000
        try:
            result = await asyncio.to_thread(gateway, "health", rid, issued, request, {})
        except Exception as e:
            return _json({"availability": "UNAVAILABLE",
                          "reasons": [f"gateway_error:{type(e).__name__}"]})
        ok = result.status == "ACCEPTED"
        data = result.data or {}
        return _json({"availability": "AVAILABLE" if ok else "UNAVAILABLE",
                      "status": result.status, "reasons": list(result.reasons),
                      "principal": result.principal, "observed_at": result.ts,
                      "control_state": data.get("control_state") if ok else None,
                      "supervisor": data.get("supervisor") if ok else None,
                      "heartbeat_age_s": data.get("heartbeat_age_s") if ok else None,
                      "recovery_in_progress": ((data.get("owner_interface") or {})
                                               .get("recovery_in_progress")) if ok else None,
                      "source": "kernel Owner Interface health read (IPC)"})

    @app.post(PREFIX + "/chat")
    async def owner_chat(request: Request):
        """Conversation only: ChatEngine.handle never issues an owner request."""
        rid = secrets.token_hex(12)
        try:
            body = await request.json()
            message = body.get("message")
            history = body.get("history") or []
        except (ValueError, AttributeError):
            return _json({"error": "malformed_request", "request_id": rid}, 400)
        if not isinstance(message, str) or not message.strip():
            return _json({"error": "empty_message", "request_id": rid}, 400)
        if len(message) > CHAT_MAX_CHARS:
            return _json({"error": "message_too_long", "request_id": rid}, 413)
        if (not isinstance(history, list) or len(history) > CHAT_MAX_HISTORY or any(
                not isinstance(h, dict) or h.get("who") not in ("Luffy", "owner")
                or not isinstance(h.get("text"), str) for h in history)):
            return _json({"error": "malformed_history", "request_id": rid}, 400)
        if chat_slots.locked():
            return _json({"error": "chat_busy", "request_id": rid}, 429)
        async with chat_slots:
            started = time.time()
            try:
                if chat_factory is not None:
                    engine = chat_factory()
                else:
                    from ..chat.engine import ChatEngine
                    engine = ChatEngine(journal, cfg)
                reply = await asyncio.to_thread(engine.handle, message.strip(),
                                                [{"who": h["who"], "text": h["text"][:400]}
                                                 for h in history])
            except Exception as e:
                log.warning("owner chat failed: %s", type(e).__name__)
                return _json({"error": "chat_failed", "detail": type(e).__name__,
                              "request_id": rid}, 502)
        from ..chat.agent import FALLBACK
        if not isinstance(reply, str) or not reply.strip() or reply == FALLBACK:
            return _json({"error": "llm_unavailable", "request_id": rid,
                          "detail": reply if isinstance(reply, str) else None}, 503)
        consulted = getattr(engine, "consulted", None)
        consulted = ([c for c in consulted if isinstance(c, dict)][:20]
                     if isinstance(consulted, list) else None)
        # a failed or slow lookup is reported as unavailable, never as "none"
        mentions, links_error = None, None
        try:
            mentions = await asyncio.wait_for(
                asyncio.to_thread(owner_reads.resolve_mentions, journal, reply),
                timeout=CHAT_LINK_TIMEOUT_S)
        except asyncio.TimeoutError:
            links_error = "lookup_timeout"
        except Exception as e:                              # noqa: BLE001
            links_error = f"lookup_failed:{type(e).__name__}"
        return _json({"request_id": rid, "reply": reply, "evidence": [],
                      "evidence_note": "The chat backend supplies no evidence identifiers",
                      "links": mentions["links"] if mentions else None,
                      "links_error": links_error,
                      "unresolved": mentions["unresolved"] if mentions else None,
                      **{k: mentions[k] if mentions else None for k in (
                          "resolved_count", "truncated_count", "unresolved_count",
                          "unexamined_tokens")},
                      "links_basis": "Records whose exact stored id appears in the reply text. "
                                     "They are what the reply mentions, not the sources the "
                                     "reply was derived from. Names are never linked.",
                      "consulted": consulted,
                      "consulted_note": None if consulted is not None else
                      "This chat engine does not report which reads it consulted",
                      "elapsed_s": round(time.time() - started, 2),
                      "operational": False})

    @app.get(PREFIX + "/knowledge")
    def owner_knowledge():
        try:
            return Response(knowledge_cache.read(), media_type="application/json",
                            headers={"Cache-Control": "no-store"})
        except (OSError, ValueError):
            return _json({"error": "knowledge_source_unavailable",
                          "detail": "No cached snapshot substituted; retry after the source is readable and stable."}, 503)

    @app.get(PREFIX + "/system")
    def owner_system():
        return _json(system(journal, root, cfg))

    @app.get(PREFIX + "/trades")
    def owner_trades(limit: int = trade_history.DEFAULT_LIMIT, status: str = "all",
                     cursor: str | None = None):
        """Keyset pages of the journal's trades, newest first (trade_history)."""
        generated = _iso(_now())
        try:
            page = trade_history.trade_page(journal.db_path, limit=limit, status=status,
                                            cursor=cursor)
        except trade_history.CursorError as e:
            return _json({"error": e.code, "detail": "Return to the newest page; no rows "
                          "were substituted."}, 400)
        except ValueError as e:
            return _json({"error": str(e)}, 400)
        return _json({"generated_at": generated, **page,
                      "consistency": "rows, preceding, total and the history check read in one "
                                     "read-only journal transaction",
                      "source": "journal trades (journal-booked; venue fills not re-verified "
                                "here)"})

    @app.get(PREFIX + "/strategies")
    def owner_strategies():
        return _json({"generated_at": _iso(_now()), "strategies": owner_reads.strategies(journal),
                      "source": "journal strategies (current registry row: identity, spec "
                                "hash, registry stats) and journal-booked trades per strategy "
                                "id; not venue-verified, not health"})

    owner_reads.install(app, journal=journal, root=root, vault=Path(vault))

    @app.get(PREFIX + "/logs")
    def owner_logs(lines: int = 80):
        lines = max(1, min(int(lines), 200))
        p = root / "logs" / "luffy.log"
        try:
            with p.open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 256 * 1024))
                tail = f.read().replace(b"\x00", b"").decode("utf-8", "replace").splitlines()
            mtime = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
        except OSError:
            return _json({"tail": None, "error": "log_unavailable"})
        return _json({"tail": tail[-lines:], "observed_at": _iso(mtime),
                      "source": "logs/luffy.log (last lines)"})

    # ── the React owner frontend, served at / (hash-routed: one index) ──
    def _frontend(path: str):
        base = dist.resolve()
        index = base / "index.html"
        if not index.exists():
            return _json({"error": "frontend_build_missing"}, 503)
        target = (base / path).resolve() if path else index
        if path and (not str(target).startswith(str(base) + "/") or not target.is_file()):
            return _json({"error": "not found"}, 404)
        resp = FileResponse(target)
        if target == index:
            _nocache(resp)
        elif target.parent.name == "assets":
            resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"
        return resp

    @app.get("/")
    def frontend_index():
        return _frontend("")

    @app.get("/assets/{path:path}")
    def frontend_asset(path: str):
        return _frontend("assets/" + path)
