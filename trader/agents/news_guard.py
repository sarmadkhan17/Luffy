"""News blackout guard — risk-off when macro headlines are hot.

Reuses the Scraper's RSS parser. When enough fresh, high-impact
headlines are circulating (Fed/CPI/hack/ETF-decision class events), the
orchestrator raises its threshold and dents conviction: entering minutes
around regime-moving news is paying spread for a coin-flip.

Fail-open: any fetch/parse problem means "no blackout" — trading
continues, just without this protection. Fail-open is not "clear": every
check records its truth explicitly (see TRUTHS), with the attempt, last
success, assessment and publication times, so a failed or unreadable feed
can never be reported as a quiet one. A cached result keeps the times of
the check that produced it; it is never re-aged.
"""
from __future__ import annotations

import logging
import math
import re
import time
from datetime import datetime, timezone

from ..brain.scraper import fetch_feed
from ..core.journal import Journal

log = logging.getLogger(__name__)

IMPACT = re.compile(
    r"\b(fed|fomc|powell|cpi|inflation|rate (cut|hike|decision)|interest rate"
    r"|treasury yields?|emergency meeting|etf (approval|decision|delay)"
    r"|sec sues|lawsuit|hacked?|exploit|drain|depeg"
    r"|liquidation[s]? (cascade|wave)|war|tariff)\b", re.I)
SEVERE = re.compile(r"\b(fomc|cpi|emergency|hack(ed)?|exploit|depeg|war)\b", re.I)

FEEDS = ["https://cointelegraph.com/rss"]

#: What the orchestrator does while armed (engine/orchestrator.py::decide,
#: `if news.get("active")`). Reported, not applied, here; a test pins them.
THRESHOLD_ADD = 0.08
SCORE_MULT = 0.75
ENFORCEMENT = "dampen_only"          # raises the entry threshold; never a veto

#: ARMED        blackout rule met by dated, current, non-future headlines
#: UNCERTAIN    rule met only via undated/malformed/future-dated items (still
#:              dampened, exactly as before), or not met while such items exist
#: QUIET        every feed read, >=1 dated in-window item, none uncertain, rule
#:              not met — the only state that proves "clear"
#: FEED_STALE   read ok, items exist, but none published inside the window
#: EMPTY_FEED   read ok, the feed carried no entries at all
#: FETCH_FAILED / PARSE_FAILED  a feed could not be fetched / read (fail-open)
#: ASSESSMENT_FAILED  the feed was read but assessing it raised (fail-open)
#: DISABLED     guard switched off in config
TRUTHS = ("ARMED", "UNCERTAIN", "QUIET", "FEED_STALE", "EMPTY_FEED", "FETCH_FAILED",
          "PARSE_FAILED", "ASSESSMENT_FAILED", "DISABLED")


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


class NewsGuard:
    def __init__(self, cfg: dict, journal: Journal | None = None):
        g = (cfg or {}).get("scouts", {}).get("news_guard", {})
        self.enabled = bool(g.get("enabled", True))
        self.window_h = float(g.get("window_hours", 3))
        self.min_headlines = int(g.get("min_headlines", 2))
        self.refresh_s = float(g.get("refresh_minutes", 10)) * 60
        self.journal = journal
        self._state: dict = {"active": False, "checked": 0.0, "why": ""}
        #: the full record of the last check (returned, unchanged, from cache)
        self._record: dict | None = None
        self._succeeded: float | None = None

    @property
    def stale_after_s(self) -> float:
        """A check older than this is not current (two missed refreshes)."""
        return 2 * self.refresh_s + 60

    def check(self) -> dict:
        """{'active': bool, 'why': str, 'truth': ..., times, counts} —
        refreshed at most every refresh_s; a cached answer is returned as it
        was recorded (same assessed_at), never re-timed.

        `active` (the orchestrator's dampening switch) is computed exactly as
        before this record existed; `truth` only says what is known."""
        if not self.enabled:
            now = time.time()
            return self._build(now, now, None, "DISABLED", False, "disabled", {})
        now = time.time()
        if now - self._state["checked"] < self.refresh_s and self._record:
            return dict(self._record)
        self._state["checked"] = now
        failures: list[dict] = []
        feeds: list[dict] = []
        for url in FEEDS:
            try:
                res = fetch_feed(url)
            except Exception as e:                    # fetch layer bug: fail-open
                log.warning(f"news guard fetch raised (fail-open): {e}")
                failures.append({"url": url, "code": "fetch_exception", "http_status": None})
                continue
            if not res.get("ok"):
                failures.append({"url": url, "code": res.get("error_code") or "fetch_error",
                                 "http_status": res.get("http_status")})
                continue
            feeds.append(res)
        if feeds:
            self._succeeded = max(float(f.get("completed_at") or time.time()) for f in feeds)
        fetched = self._succeeded if feeds else None
        c = {"hits": [], "dated_hits": 0, "dated_severe": 0, "pubs": [], "total": 0,
             "considered": 0, "dated_in_window": 0, "future": 0, "undated": 0,
             "malformed_dates": 0}
        try:
            for res in feeds:
                done = float(res.get("completed_at") or time.time())
                for it in res.get("items") or []:
                    c["total"] += 1
                    pub = it.get("published_ts")
                    dated = isinstance(pub, (int, float)) and not isinstance(pub, bool) \
                        and math.isfinite(pub)
                    future = False
                    if dated:
                        c["pubs"].append(float(pub))
                        future = pub > done          # published after the fetch finished
                        c["future"] += future
                    elif it.get("published_status") == "malformed":
                        c["malformed_dates"] += 1
                    else:
                        c["undated"] += 1
                    age = it.get("age_h")
                    if age is not None and age > self.window_h:
                        continue
                    c["considered"] += 1
                    certain = dated and not future
                    c["dated_in_window"] += certain
                    hay = f"{it['title']} {it['text'][:200]}"
                    if IMPACT.search(hay):
                        tag = "severe" if SEVERE.search(it["title"]) else "impact"
                        c["hits"].append(f"[{tag}] {it['title'][:90]}")
                        if certain:
                            c["dated_hits"] += 1
                            c["dated_severe"] += tag == "severe"
        except Exception as e:
            log.warning(f"news guard assessment failed (fail-open): {e}")
            self._state.update(active=False, why=f"assessment error: {e}")
            return self._build(now, time.time(), fetched, "ASSESSMENT_FAILED", False,
                               f"assessment error: {type(e).__name__}", c,
                               failures + [{"url": None, "code": "assessment_exception",
                                            "http_status": None}])
        hits = c["hits"]
        severe_n = sum(1 for h in hits if h.startswith("[severe]"))
        active = len(hits) >= self.min_headlines or severe_n >= 1   # unchanged rule
        certain_rule = c["dated_hits"] >= self.min_headlines or c["dated_severe"] >= 1
        uncertain = c["future"] or c["undated"] or c["malformed_dates"]
        if active:
            truth = "ARMED" if certain_rule else "UNCERTAIN"
            why = f"{len(hits)} impact headlines ({severe_n} severe)"
        elif failures:
            truth = "PARSE_FAILED" if failures[0]["code"].startswith("parse") \
                else "FETCH_FAILED"
            why = f"{'parse' if truth == 'PARSE_FAILED' else 'fetch'} error: " + \
                ", ".join(f["code"] for f in failures)
        else:
            why = f"{len(hits)} impact headlines ({severe_n} severe)" \
                if hits else "feed quiet"
            if uncertain:
                truth = "UNCERTAIN"
            elif c["total"] == 0:
                truth, why = "EMPTY_FEED", "feed empty"
            elif c["dated_in_window"] == 0:
                truth, why = "FEED_STALE", "no headline inside the window"
            else:
                truth = "QUIET"
        if active != self._state["active"] and self.journal:
            try:
                event = ("news_blackout" if active else
                         "news_clear" if truth == "QUIET" else
                         "news_guard_fail_open" if failures else "news_guard_disarmed_unproven")
                self.journal.log_control_event(
                    event, "news_guard",
                    detail={"hits": hits[:5], "truth": truth,
                            "failures": [f["code"] for f in failures]})
            except Exception:
                pass
        self._state.update(active=active, why=why)
        if active:
            log.info(f"NEWS BLACKOUT armed: {why}")
        return self._build(now, time.time(), fetched, truth, active, why, c, failures,
                           severe=severe_n)

    def _build(self, attempted: float, assessed: float, fetched: float | None, truth: str,
               active: bool, why: str, c: dict, failures=(), severe: int = 0) -> dict:
        hits = c.get("hits") or []
        pubs = c.get("pubs") or []
        rec = {"active": bool(active), "why": why, "truth": truth,
               "attempted_at": _iso(attempted), "assessed_at": _iso(assessed),
               "fetched_at": _iso(fetched),
               "succeeded_at": _iso(self._succeeded) if truth != "DISABLED" else None,
               "newest_publication_at": _iso(max(pubs)) if pubs else None,
               "oldest_publication_at": _iso(min(pubs)) if pubs else None,
               "hits": len(hits), "severe": int(severe),
               "dated_hits": int(c.get("dated_hits", 0)),
               "dated_severe": int(c.get("dated_severe", 0)),
               "headlines": list(hits)[:5], "items_total": int(c.get("total", 0)),
               "items_considered": int(c.get("considered", 0)),
               "dated_in_window": int(c.get("dated_in_window", 0)),
               "undated_items": int(c.get("undated", 0)),
               "malformed_dates": int(c.get("malformed_dates", 0)),
               "future_dated_items": int(c.get("future", 0)),
               "failure_codes": [f["code"] for f in failures],
               "failures": list(failures)[:5],
               "min_headlines": self.min_headlines, "window_hours": self.window_h,
               "refresh_s": self.refresh_s, "stale_after_s": self.stale_after_s,
               "dampening": {"applied": bool(active), "threshold_add": THRESHOLD_ADD,
                             "score_mult": SCORE_MULT, "enforcement": ENFORCEMENT}}
        if truth != "DISABLED":
            self._record = rec
        return dict(rec)
