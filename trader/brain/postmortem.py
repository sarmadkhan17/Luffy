"""Change-only book reports backed by the Factory's exact paper assessment.

This is presentation and historical observation. Factory owns attribution,
exit/cost completeness and probation arithmetic; diagnostics never advance
probation, write approvals or change strategy lifecycle.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from ..strategy import factory_handoff as factory


def book_health(journal, cfg: dict) -> list[dict]:
    at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    out = []
    for row in journal.query("SELECT id,name,state FROM strategies WHERE "
                             "kind='spec' AND state IN ('paper','active')"):
        entry = {**row, "version_id": None, "install_id": None,
                 "basis": "exact installed version; attributable paper outcomes"}
        try:
            inst = factory.resolve_current_install(journal, row["id"])
            entry.update(version_id=inst["version_id"], install_id=inst["install_id"],
                         spec_hash=inst["spec_hash"])
            version = factory.load_version(journal, inst["version_id"])
            assessment = factory._assess(journal, cfg, version,
                                         inst["installed_at_ms"], at_ms)
            # Guard against a replacement while this read-only report was built.
            if factory.resolve_current_install(journal, row["id"]) != inst:
                raise ValueError("installed_version_changed")
            stats = assessment.get("stats")
            summary = assessment["status"]
            if stats is not None:
                summary += (f": {stats['trades']} attributable closed paper trades, "
                            f"net P&L {stats['pnl']:.2f} USDT")
            else:
                summary += ": attributable net outcome evidence is incomplete"
            health = {"verdict": assessment["status"], "summary": summary,
                      "stats": stats, "excluded_trades": assessment["excluded_trades"],
                      "missing_cost_evidence": assessment.get("missing_cost_evidence", [])}
        except (ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            health = {"verdict": "UNAVAILABLE", "stats": None,
                      "summary": f"exact-version paper health unavailable: {exc}"}
        out.append({**entry, "health": health})
    return out


def run_postmortem(journal, vault=None, *, cfg: dict) -> dict:
    rows = book_health(journal, cfg)
    verdicts = {r["id"]: r["health"]["verdict"] for r in rows}
    identities = {r["id"]: [r["version_id"], r["install_id"]] for r in rows}
    last = journal.query(
        "SELECT detail FROM brain_events WHERE kind='postmortem' "
        "ORDER BY id DESC LIMIT 1")
    try:
        prev = json.loads(last[0]["detail"]) if last else {}
    except (ValueError, TypeError):
        prev = {}
    changed = {k: v for k, v in verdicts.items()
               if (prev.get("verdicts", {}).get(k) != v
                   or prev.get("identities", {}).get(k) != identities[k])}
    rep = {"ts": datetime.now(timezone.utc).isoformat(), "authority": "DIAGNOSTIC_ONLY",
           "verdicts": verdicts, "identities": identities, "changed": changed, "book": rows}
    journal.log_brain_event("postmortem", "book", rep)
    note = ""
    if changed and vault is not None:
        body = "\n".join(f"- **{r['name']}** — {r['health']['summary']}"
                         for r in rows if r["id"] in changed)
        vault.incident_note("Book health", body)
        note = "Book health"
    return {"ran": True, **rep, "note": note}


def summary_text(journal, cfg: dict) -> str:
    """Existing /judge report; exact paper evidence plus recorded rent."""
    lines = ["🧠 exact-version paper health (data, no LLM)"]
    rows = book_health(journal, cfg)
    lines.extend(f"· {r['name'][:28]}: {r['health']['summary']}" for r in rows)
    if not rows:
        lines.append("· no strategies in the book")
    try:
        rent = json.loads(journal.kv_get("rent_state") or "{}")
    except (ValueError, TypeError):
        rent = {}
    if rent:
        lines.append(f"rent: week of {rent.get('week_start', '?')} net "
                     f"${float(rent.get('net') or 0):.2f} vs "
                     f"${float(rent.get('bar') or 0):.0f} · "
                     f"{rent.get('status', '')}")
    return "\n".join(lines)
