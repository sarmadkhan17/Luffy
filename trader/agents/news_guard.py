"""News blackout guard — risk-off when macro headlines are hot.

Reuses the Scraper's RSS parser. When enough fresh, high-impact
headlines are circulating (Fed/CPI/hack/ETF-decision class events), the
orchestrator raises its threshold and dents conviction: entering minutes
around regime-moving news is paying spread for a coin-flip.

Fail-open: any fetch/parse problem means "no blackout" — trading
continues, just without this protection.
"""
from __future__ import annotations

import logging
import re
import time

from ..brain.scraper import scrape_feed
from ..core.journal import Journal

log = logging.getLogger(__name__)

IMPACT = re.compile(
    r"\b(fed|fomc|powell|cpi|inflation|rate (cut|hike|decision)|interest rate"
    r"|treasury yields?|emergency meeting|etf (approval|decision|delay)"
    r"|sec sues|lawsuit|hacked?|exploit|drain|depeg"
    r"|liquidation[s]? (cascade|wave)|war|tariff)\b", re.I)
SEVERE = re.compile(r"\b(fomc|cpi|emergency|hack(ed)?|exploit|depeg|war)\b", re.I)

FEEDS = ["https://cointelegraph.com/rss"]


class NewsGuard:
    def __init__(self, cfg: dict, journal: Journal | None = None):
        g = (cfg or {}).get("scouts", {}).get("news_guard", {})
        self.enabled = bool(g.get("enabled", True))
        self.window_h = float(g.get("window_hours", 3))
        self.min_headlines = int(g.get("min_headlines", 2))
        self.refresh_s = float(g.get("refresh_minutes", 10)) * 60
        self.journal = journal
        self._state: dict = {"active": False, "checked": 0.0, "why": ""}

    def check(self) -> dict:
        """{'active': bool, 'why': str} — refreshed at most refresh_s."""
        if not self.enabled:
            return {"active": False, "why": "disabled"}
        now = time.time()
        if now - self._state["checked"] < self.refresh_s:
            return {"active": self._state["active"], "why": self._state["why"]}
        self._state["checked"] = now
        hits: list[str] = []
        try:
            for url in FEEDS:
                for it in scrape_feed(url):
                    age = it.get("age_h")
                    if age is not None and age > self.window_h:
                        continue
                    hay = f"{it['title']} {it['text'][:200]}"
                    if IMPACT.search(hay):
                        tag = "severe" if SEVERE.search(it["title"]) else "impact"
                        hits.append(f"[{tag}] {it['title'][:90]}")
        except Exception as e:
            log.warning(f"news guard check failed (fail-open): {e}")
            self._state.update(active=False, why=f"fetch error: {e}")
            return dict(self._state)
        severe_n = sum(1 for h in hits if h.startswith("[severe]"))
        active = len(hits) >= self.min_headlines or severe_n >= 1
        why = f"{len(hits)} impact headlines ({severe_n} severe)" \
            if hits else "feed quiet"
        if active != self._state["active"] and self.journal:
            try:
                self.journal.log_control_event(
                    "news_blackout" if active else "news_clear", "news_guard",
                    detail={"hits": hits[:5]})
            except Exception:
                pass
        self._state.update(active=active, why=why)
        if active:
            log.info(f"NEWS BLACKOUT armed: {why}")
        return {"active": active, "why": why}
