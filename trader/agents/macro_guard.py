"""MacroGuard — hard-freeze during US high-impact macro events.

Fetches the Finnhub economic calendar (US, high-impact) and freezes
entry when the current time falls within [event - pre_min, event + post_min].
Fail-open: any network/parse error means "no freeze" — trading continues.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta, timezone

log = logging.getLogger(__name__)

# Eastern Time is UTC-4 (EDT) / UTC-5 (EST). Fixed -4 offset (EDT) because
# the bulk of US data releases occur March–November. Off by one hour in
# winter — acceptable for a 30-min pre-window.
_ET_OFFSET = timedelta(hours=4)
_FINNHUB_URL = "https://finnhub.io/api/v1/calendar/economic"


class MacroGuard:
    def __init__(self, cfg: dict, journal=None):
        g = (cfg or {}).get("scouts", {}).get("macro_guard", {})
        self.enabled = bool(g.get("enabled", True))
        self.pre_min = float(g.get("pre_event_min", 30))
        self.post_min = float(g.get("post_event_min", 120))
        self.min_impact = str(g.get("min_impact", "high"))
        self._refresh_s = 300.0   # 5-minute state cache
        self._cal_ttl = 3600.0    # 1-hour calendar cache
        self.journal = journal
        self._token: str = (str(g.get("finnhub_token") or "")
                            or os.environ.get("FINNHUB_API_KEY", ""))
        self._state: dict = {"active": False, "event": "", "until": None,
                             "why": "", "checked": 0.0}
        self._calendar: list[dict] = []
        self._cal_fetched: float = 0.0

    # ── calendar fetch ────────────────────────────────────────────────────

    def _fetch_calendar(self) -> list[dict]:
        """Pull next 7 days of high-impact US events from Finnhub."""
        if not self._token:
            return []
        now = time.time()
        if now - self._cal_fetched < self._cal_ttl and self._calendar:
            return self._calendar
        try:
            import requests
            today = date.today()
            end = today + timedelta(days=7)
            r = requests.get(
                _FINNHUB_URL,
                params={"from": str(today), "to": str(end),
                        "token": self._token},
                timeout=8,
            )
            r.raise_for_status()
            raw = r.json().get("economicCalendar", [])
            self._calendar = [
                e for e in raw
                if e.get("country") == "US"
                and e.get("impact") == self.min_impact
            ]
            self._cal_fetched = now
            log.debug(f"macro_guard: fetched {len(self._calendar)} "
                      f"high-impact US events")
        except Exception as e:
            log.warning(f"macro_guard calendar fetch failed (fail-open): {e}")
        return self._calendar

    # ── window check ─────────────────────────────────────────────────────

    def _event_utc(self, e: dict) -> datetime | None:
        """Parse Finnhub date+time (ET) → UTC datetime, or None if unparseable."""
        try:
            date_str = e.get("date", "")
            time_str = e.get("time", "00:00:00") or "00:00:00"
            dt_et = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
            return dt_et.replace(tzinfo=timezone.utc) + _ET_OFFSET
        except Exception:
            return None

    def _find_active_event(self) -> dict | None:
        """Return the first calendar event within the freeze window, or None."""
        now = datetime.now(timezone.utc)
        pre = timedelta(minutes=self.pre_min)
        post = timedelta(minutes=self.post_min)
        for e in self._fetch_calendar():
            ev_utc = self._event_utc(e)
            if ev_utc is None:
                continue
            if (ev_utc - pre) <= now <= (ev_utc + post):
                return {
                    "event": e.get("event", "unknown"),
                    "until": (ev_utc + post).isoformat(),
                    "event_utc": ev_utc.isoformat(),
                }
        return None

    # ── public interface ──────────────────────────────────────────────────

    def check(self) -> dict:
        """Return {"active": bool, "event": str, "until": str|None, "why": str}.

        Cached for _refresh_s seconds. Fail-open on any error.
        """
        if not self.enabled:
            return {"active": False, "event": "", "until": None,
                    "why": "disabled"}
        if not self._token:
            return {"active": False, "event": "", "until": None,
                    "why": "no_token"}
        now = time.time()
        if now - self._state["checked"] < self._refresh_s:
            return {k: self._state[k]
                    for k in ("active", "event", "until", "why")}
        self._state["checked"] = now
        try:
            hit = self._find_active_event()
        except Exception as e:
            log.warning(f"macro_guard check error (fail-open): {e}")
            hit = None
        was_active = self._state["active"]
        if hit:
            self._state.update(active=True, event=hit["event"],
                               until=hit["until"],
                               why=f"event window: {hit['event']}")
            if not was_active and self.journal:
                try:
                    self.journal.log_control_event(
                        "macro_freeze", "macro_guard",
                        detail={"event": hit["event"], "until": hit["until"]})
                except Exception:
                    pass
            log.warning(f"MACRO FREEZE: {hit['event']} until {hit['until']}")
        else:
            self._state.update(active=False, event="", until=None,
                               why="no active event window")
            if was_active and self.journal:
                try:
                    self.journal.log_control_event(
                        "macro_clear", "macro_guard",
                        detail={"cleared_at": datetime.now(
                            timezone.utc).isoformat()})
                except Exception:
                    pass
        return {k: self._state[k]
                for k in ("active", "event", "until", "why")}
