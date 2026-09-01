"""MacroGuard — hard-freeze during US high-impact macro events.

Fetches a US high-impact economic calendar and freezes entry when the current
time falls within [event - pre_min, event + post_min].

Source order: Finnhub if a key is set (its calendar needs a paid plan, so the
free tier 403s), otherwise the free keyless ForexFactory JSON feed. A good
fetch is cached to disk so a restart is not a blind week.

Fail-open: any network/parse error means "no freeze" — trading continues. But
a guard that cannot see the calendar reports that explicitly rather than
reading as a quiet week, which is how one stops guarding unnoticed.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Eastern Time is UTC-4 (EDT) / UTC-5 (EST). Fixed -4 offset (EDT) because
# the bulk of US data releases occur March–November. Off by one hour in
# winter — acceptable for a 30-min pre-window.
_ET_OFFSET = timedelta(hours=4)
#: both sources dead → wait this long before trying again
_RETRY_BACKOFF = 900.0
#: where a good fetch is parked so a restart is not a blind week
_CACHE_PATH = Path("data/macro_calendar.json")
#: a cached calendar older than this is discarded; past events never fire,
#: and future ones rarely move, so a stale file is still a real guard
_CACHE_MAX_AGE = 7 * 86400.0
_CACHE_STALE = 48 * 3600.0
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120 Safari/537.36")
_FINNHUB_URL = "https://finnhub.io/api/v1/calendar/economic"
#: Free, keyless, and already ISO-8601 with a real UTC offset — so unlike the
#: Finnhub path it needs no hand-rolled Eastern-Time guess. Finnhub's calendar
#: is a paid endpoint, which is why this is the default source.
_FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
#: how each source spells "the United States"
_US = {"US", "USD"}


def _scrub(text: str, token: str) -> str:
    """Never let an API key reach a log line."""
    out = text.replace(token, "***") if token else text
    return re.sub(r"token=[^&\s]+", "token=***", out)


class MacroGuard:
    def __init__(self, cfg: dict, journal=None):
        g = (cfg or {}).get("scouts", {}).get("macro_guard", {})
        self.enabled = bool(g.get("enabled", True))
        self.pre_min = float(g.get("pre_event_min", 30))
        self.post_min = float(g.get("post_event_min", 120))
        self.min_impact = str(g.get("min_impact", "high"))
        self._refresh_s = 300.0   # 5-minute state cache
        # A week's calendar is published days ahead and rarely changes, so
        # refetching hourly buys nothing and risks the feed's rate limit.
        self._cal_ttl = 6 * 3600.0
        self.journal = journal
        self._token: str = (str(g.get("finnhub_token") or "")
                            or os.environ.get("FINNHUB_API_KEY", ""))
        self._state: dict = {"active": False, "event": "", "until": None,
                             "why": "", "checked": 0.0}
        self._calendar: list[dict] = []
        self._cal_fetched: float = 0.0
        #: both sources dead → don't retry until this timestamp
        self._retry_after = 0.0
        #: did any source actually answer? An empty calendar means "quiet
        #: week" only if it does; otherwise it means the guard is blind.
        self._sources_ok = False
        #: one good fetch covers the week, restarts included
        self._cache_path = _CACHE_PATH
        #: set when the venue refuses the key outright (401/403). Distinct
        #: from "the calendar is empty this week" — a guard that cannot see
        #: the calendar must say so rather than read as a quiet week.
        self._access_error: str = ""

    # ── calendar fetch ────────────────────────────────────────────────────

    def _fetch_calendar(self) -> list[dict]:
        """High-impact US events for the week ahead, newest source first.

        Both sources are normalised to `{"event": str, "when": aware UTC}` so
        the window check never has to know which one answered.
        """
        now = time.time()
        if now - self._cal_fetched < self._cal_ttl and self._calendar:
            return self._calendar
        if now < self._retry_after:
            return self._calendar
        if not self._calendar:
            self._load_cached()
            if now - self._cal_fetched < self._cal_ttl and self._calendar:
                return self._calendar

        events = []
        self._sources_ok = False
        if self._token and not self._access_error:
            events = self._from_finnhub()
        if not events:
            events = self._from_forexfactory()
        if events:
            self._calendar = events
            self._cal_fetched = now
            self._retry_after = 0.0
            self._save_cached(events, now)
            log.debug(f"macro_guard: {len(events)} high-impact US events")
        else:
            # Both sources are down. Back off rather than hammering them on
            # every cycle — self-inflicted 429s are how this guard ends up
            # fail-open for hours.
            self._retry_after = now + _RETRY_BACKOFF
        return self._calendar

    def _save_cached(self, events: list[dict], fetched: float) -> None:
        """One good fetch should cover the week, restarts included."""
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps({
                "fetched": fetched,
                "events": [{"event": e["event"], "when": e["when"].isoformat()}
                           for e in events]}))
        except Exception as e:
            log.debug(f"macro_guard: could not cache calendar: {e}")

    def _load_cached(self) -> None:
        """A day-old calendar still lists today's events; nothing beats it
        except a fresh one, and everything beats no guard at all."""
        try:
            raw = json.loads(self._cache_path.read_text())
            fetched = float(raw["fetched"])
        except Exception:
            return
        age = time.time() - fetched
        if age > _CACHE_MAX_AGE:
            return
        # A cache whose every event is already behind us guards nothing.
        # Treating it as a live source would report "quiet week" while the
        # guard is actually blind — the exact failure this class exists to
        # avoid — so it is discarded instead.
        horizon = datetime.now(timezone.utc) - timedelta(minutes=self.post_min)
        events = []
        for e in raw.get("events", []):
            try:
                when = datetime.fromisoformat(e["when"])
            except Exception:
                continue
            if when >= horizon:
                events.append({"event": e["event"], "when": when})
        if not events:
            return
        self._calendar = events
        self._cal_fetched = fetched
        self._sources_ok = True
        if age > _CACHE_STALE:
            log.warning("macro_guard: using a %.1f-hour-old cached calendar; "
                        "no source has answered since", age / 3600)

    def _is_high_us(self, country: str, impact: str) -> bool:
        return (str(country).upper() in _US
                and str(impact).lower() == self.min_impact.lower())

    def _from_finnhub(self) -> list[dict]:
        """Paid endpoint. Date and time are separate, and in Eastern Time."""
        try:
            import requests
            today = date.today()
            r = requests.get(
                _FINNHUB_URL,
                params={"from": str(today),
                        "to": str(today + timedelta(days=7)),
                        "token": self._token},
                timeout=8)
            r.raise_for_status()
            raw = r.json().get("economicCalendar", [])
            self._sources_ok = True
        except Exception as e:
            self._note_fetch_error(e)
            return []
        out = []
        for e in raw:
            if not self._is_high_us(e.get("country", ""), e.get("impact", "")):
                continue
            try:
                naive = datetime.strptime(
                    f"{e.get('date','')} {e.get('time') or '00:00:00'}",
                    "%Y-%m-%d %H:%M:%S")
                out.append({"event": e.get("event", "unknown"),
                            "when": naive.replace(tzinfo=timezone.utc)
                            + _ET_OFFSET})
            except Exception:
                continue
        return out

    def _from_forexfactory(self) -> list[dict]:
        """Keyless. One ISO field carrying its own offset — no ET guessing."""
        try:
            import requests
            r = requests.get(_FF_URL, headers={"User-Agent": _UA}, timeout=10)
            if r.status_code == 429:
                # The feed publishes its own cooldown. Respecting it is the
                # difference between one slow fetch and a day of fail-open.
                wait = float(r.headers.get("Retry-After", _RETRY_BACKOFF))
                self._retry_after = time.time() + max(wait, 60.0)
                log.warning("macro_guard: calendar feed rate-limited, "
                            "retrying in %.0fs", wait)
                return []
            r.raise_for_status()
            raw = r.json()
            self._sources_ok = True
        except Exception as e:
            log.warning(f"macro_guard: forexfactory calendar unavailable "
                        f"(fail-open): {_scrub(str(e), self._token)}")
            return []
        out = []
        for e in raw or []:
            if not self._is_high_us(e.get("country", ""), e.get("impact", "")):
                continue
            try:
                when = datetime.fromisoformat(e["date"])
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                out.append({"event": e.get("title", "unknown"),
                            "when": when.astimezone(timezone.utc)})
            except Exception:
                continue
        return out

    def _note_fetch_error(self, e: Exception) -> None:
        msg = _scrub(str(e), self._token)
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status is None:
            status = 403 if "403" in msg else 401 if "401" in msg else None
        if status in (401, 403):
            if not self._access_error:
                log.warning(
                    "macro_guard: this Finnhub key cannot read the economic "
                    "calendar (HTTP %s) — that endpoint needs a paid plan. "
                    "Falling back to the free ForexFactory feed.", status)
            self._access_error = f"HTTP {status}"
        else:
            log.warning(f"macro_guard finnhub fetch failed: {msg}")

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

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _find_active_event(self) -> dict | None:
        """Return the first calendar event within the freeze window, or None."""
        now = self._now()
        pre = timedelta(minutes=self.pre_min)
        post = timedelta(minutes=self.post_min)
        for e in self._fetch_calendar():
            ev_utc = e.get("when")
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
        now = time.time()
        if now - self._state["checked"] < self._refresh_s:
            return {k: self._state[k]
                    for k in ("active", "event", "until", "why")}
        self._state["checked"] = now
        try:
            hit = self._find_active_event()
        except Exception as e:
            log.warning(f"macro_guard check error (fail-open): "
                        f"{_scrub(str(e), self._token)}")
            hit = None
        if not hit and not self._sources_ok and not self._calendar:
            # No source answered. That is not the same as a week with no
            # events, and saying "no active event window" here is precisely
            # how a guard stops guarding without anyone noticing.
            detail = (f"finnhub {self._access_error}, " if self._access_error
                      else "")
            self._state.update(
                active=False, event="", until=None,
                why=f"no calendar source reachable ({detail}"
                    f"forexfactory unavailable)")
            return {k: self._state[k]
                    for k in ("active", "event", "until", "why")}
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
