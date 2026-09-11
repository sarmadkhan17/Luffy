"""The rent check's state: an hourly tally, one line a day, one verdict a week.

Reads the venue through `rent`, writes `state_kv["rent_state"]` and
`rent_verdict` brain events, and talks to Sarmad through the notifier. It
never stops, freezes or deletes anything: the verdict is his to act on.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..core.journal import Journal
from . import rent

log = logging.getLogger(__name__)


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _rounded(d: dict) -> dict:
    return {k: round(v, 4) for k, v in d.items()}


class RentKeeper:
    def __init__(self, exchange, journal: Journal, notifier, cfg: dict):
        r = cfg.get("rent", {}) or {}
        self.ex, self.journal, self.notifier = exchange, journal, notifier
        self.bar = float(r.get("weekly_usdt", 50))
        first = datetime.fromisoformat(str(r.get("first_week_start", "2026-09-14")))
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)
        self.first_ms = int(first.timestamp() * 1000)

    def tick(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        now_ms = int(now.timestamp() * 1000)
        start, end = rent.week_bounds(now)
        summary, err = rent.read_week(self.ex, start, now_ms + 1)
        state = {
            "week_start": _day(start),
            "net": None if summary is None else round(summary["net"], 2),
            "bar": self.bar,
            "by_type": {} if summary is None else _rounded(summary["by_type"]),
            "uncounted": {} if summary is None else _rounded(summary["uncounted"]),
            "days_left": round((end - now_ms) / 86_400_000, 1),
            "judged": start >= self.first_ms,
            "status": "IN_PROGRESS" if summary is not None else "UNKNOWN",
            "error": err,
            "updated_at": now.isoformat(),
        }
        self.journal.kv_set("rent_state", json.dumps(state))
        self._daily_line(now, state)
        self._finalise(start - rent.WEEK_MS)
        return state

    def _daily_line(self, now: datetime, state: dict) -> None:
        day = now.strftime("%Y-%m-%d")
        if self.journal.kv_get("rent_tally_day", "") == day:
            return
        self.journal.kv_set("rent_tally_day", day)
        net = "unreadable" if state["net"] is None else f"{state['net']:+.2f}"
        tag = "" if state["judged"] else " (not judged: before the first week)"
        self._send(f"🏠 Rent, week of {state['week_start']}: {net} of "
                   f"${self.bar:.0f} so far, {state['days_left']:.1f} days left{tag}")

    def _last_verdict(self, week: str) -> dict | None:
        rows = self.journal.query(
            "SELECT detail FROM brain_events WHERE kind='rent_verdict' "
            "AND subject=? ORDER BY id DESC LIMIT 1", (week,))
        return json.loads(rows[0]["detail"]) if rows else None

    def _finalise(self, week_start_ms: int) -> None:
        if week_start_ms < self.first_ms:
            return                       # before the first judged week
        week = _day(week_start_ms)
        prior = self._last_verdict(week)
        if prior and prior.get("verdict") != "UNKNOWN":
            return                       # already judged; restart-safe
        summary, err = rent.read_week(self.ex, week_start_ms,
                                      week_start_ms + rent.WEEK_MS)
        v = rent.verdict(summary, self.bar)
        if prior and v == "UNKNOWN":
            return                       # still unreadable, and already said so
        self.journal.log_brain_event("rent_verdict", week, {
            "week_start": week,
            "week_end": _day(week_start_ms + rent.WEEK_MS),
            "net": None if summary is None else round(summary["net"], 2),
            "bar": self.bar, "verdict": v,
            "by_type": {} if summary is None else _rounded(summary["by_type"]),
            "uncounted": {} if summary is None else _rounded(summary["uncounted"]),
            "error": err, "correction": bool(prior)})
        net = "unreadable" if summary is None else f"{summary['net']:+.2f}"
        fix = " (correction: the ledger now answers)" if prior else ""
        self._send(f"🏠 Rent, week of {week}: {v}, {net} against "
                   f"${self.bar:.0f}{fix}")

    def _send(self, text: str) -> None:
        if not self.notifier:
            return
        try:
            self.notifier.send(text)
        except Exception as e:
            log.warning(f"rent: notify failed: {e}")


def snapshot(journal: Journal, limit: int = 8) -> dict:
    """This week's tally and the last `limit` verdicts, newest first."""
    try:
        state = json.loads(journal.kv_get("rent_state", "") or "{}")
    except Exception:
        state = {}
    history, seen = [], set()
    for r in journal.query("SELECT subject, detail FROM brain_events "
                           "WHERE kind='rent_verdict' ORDER BY id DESC"):
        if r["subject"] in seen:
            continue                     # an older row for a corrected week
        seen.add(r["subject"])
        d = json.loads(r["detail"])
        history.append({"week_start": r["subject"],
                        "verdict": d.get("verdict"), "net": d.get("net")})
        if len(history) >= limit:
            break
    history.sort(key=lambda h: h["week_start"], reverse=True)
    return {"state": state, "history": history}


def status_text(journal: Journal) -> str:
    snap = snapshot(journal)
    s = snap["state"]
    if not s:
        return "🏠 Rent: no reading yet"
    net = "unreadable" if s.get("net") is None else f"{s['net']:+.2f}"
    lines = [f"🏠 Rent, week of {s.get('week_start')}: {net} of "
             f"${float(s.get('bar', 50)):.0f}, {s.get('days_left')} days left"]
    for h in snap["history"]:
        n = "?" if h["net"] is None else f"{h['net']:+.2f}"
        lines.append(f"  {h['week_start']}  {h['verdict']}  {n}")
    return "\n".join(lines)
