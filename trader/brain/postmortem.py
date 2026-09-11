"""The Theorist, as arithmetic: is each live strategy still inside the
envelope it was admitted on?

Replaces the LLM autopsy (removed 2026-09-11; ~55% of all tokens, output
was prose doctrine drawn from 2-9-trade samples). A spec carries the
out-of-sample win rate it was admitted on in `provenance.expected_winrate`;
`strategy.health.assess_health` asks whether the live record is consistent
with it. A note reaches the vault only when a verdict changes, so a quiet
book stays quiet.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..strategy.health import assess_health


def book_health(journal) -> list[dict]:
    out = []
    for row, spec in journal.list_specs(["paper", "active"]):
        t = journal.query(
            "SELECT COUNT(*) n, "
            "SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) w, "
            "SUM(realized_pnl) pnl FROM trades "
            "WHERE strategy_id=? AND status='closed'", (spec.id,))[0]
        n, wins = int(t["n"] or 0), int(t["w"] or 0)
        exp = (spec.provenance or {}).get("expected_winrate")
        health = None
        if isinstance(exp, (int, float)) and exp > 0:
            h = assess_health(float(exp), wins, max(0, n - wins))
            health = {"verdict": h.verdict, "expected_winrate": float(exp),
                      "observed_winrate": round(h.observed_winrate, 4),
                      "p_underperform": round(h.p_underperform, 4),
                      "trades": h.trades, "summary": h.summary}
        out.append({"id": spec.id, "name": spec.name, "state": row["state"],
                    "live_trades": n, "wins": wins,
                    "pnl_usdt": round(float(t["pnl"] or 0), 2),
                    "health": health})
    return out


def _verdict(r: dict) -> str:
    return (r["health"] or {}).get("verdict", "NO_ENVELOPE")


def run_postmortem(journal, vault=None) -> dict:
    rows = book_health(journal)
    verdicts = {r["id"]: _verdict(r) for r in rows}
    last = journal.query(
        "SELECT detail FROM brain_events WHERE kind='postmortem' "
        "ORDER BY id DESC LIMIT 1")
    try:
        prev = json.loads(last[0]["detail"]).get("verdicts", {}) if last else {}
    except Exception:
        prev = {}
    changed = {k: v for k, v in verdicts.items() if prev.get(k) != v}
    rep = {"ts": datetime.now(timezone.utc).isoformat(),
           "verdicts": verdicts, "changed": changed, "book": rows}
    journal.log_brain_event("postmortem", "book", rep)
    note = ""
    if changed and vault is not None:
        body = "\n".join(
            f"- **{r['name']}** — " + (r["health"]["summary"] if r["health"]
                                       else f"{r['live_trades']} closed "
                                            f"trades, no validated envelope")
            for r in rows if r["id"] in changed)
        vault.incident_note("Book health", body)
        note = "Book health"
    return {"ran": True, **rep, "note": note}


def summary_text(journal) -> str:
    """What `/judge` answers: the book's health and the week's rent, from
    data. No model is asked anything."""
    lines = ["🧠 book health (data, no LLM)"]
    rows = book_health(journal)
    for r in rows:
        h = r["health"]
        lines.append(f"· {r['name'][:28]}: " + (
            h["summary"] if h else
            f"{r['live_trades']} closed trades, no validated envelope"))
    if not rows:
        lines.append("· no strategies in the book")
    try:
        rent = json.loads(journal.kv_get("rent_state") or "{}")
    except Exception:
        rent = {}
    if rent:
        lines.append(f"rent: week of {rent.get('week_start', '?')} net "
                     f"${float(rent.get('net') or 0):.2f} vs "
                     f"${float(rent.get('bar') or 0):.0f} · "
                     f"{rent.get('status', '')}")
    return "\n".join(lines)
