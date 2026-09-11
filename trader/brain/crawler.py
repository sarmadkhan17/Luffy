"""Deep Crawler — Luffy reads the internet's finance material properly.

The Scraper skims headlines and idea captions. This module goes where
the substance lives: it walks a bounded frontier of finance knowledge
sites (Investopedia articles, QuantStart, BabyPips, arXiv abstracts,
Medium essays — plus whatever links this cycle's RSS items point to),
extracts readable text, screens passages token-free for strategy
density, then queues the densest ones whole for the Strategist — the
only path that turns mined text into a strategy.

Politeness: robots.txt honored (std. library parser), per-host delay,
page + depth caps, assets skipped. Fail-open when robots is unreachable.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

from . import ideas
from ..core.journal import Journal
from .scraper import (DEFAULT_RSS_FEEDS, STRATEGY_TERMS, UA, Scraper,
                        scrape_feed)

log = logging.getLogger(__name__)

SEEDS = [
    # hosts verified to serve machine readers (2026-08); walls change,
    # so the crawler degrades gracefully when a seed dies
    "https://chartschool.stockcharts.com/table-of-contents/market-indicators",
    "https://en.wikipedia.org/wiki/Mean_reversion_(finance)",
    "https://en.wikipedia.org/wiki/Momentum_(finance)",
    "https://en.wikipedia.org/wiki/Pair_trade",
    "https://en.wikipedia.org/wiki/Moving_average_crossover",
    "https://en.wikipedia.org/wiki/Relative_strength_index",
    "https://en.wikipedia.org/wiki/Bollinger_Bands",
    "https://towardsdatascience.com/",
]
ALLOW_DOMAINS = {
    "wikipedia.org", "stockcharts.com", "towardsdatascience.com",
    "arxiv.org", "medium.com", "investopedia.com", "quantstart.com",
    "babypips.com",          # kept: if they unblock us someday, we resume
    "oxfordstrat.com", "quantpedia.com", "federalreserve.gov",
    "bis.org", "elitetrader.com", "tradingview.com", "xueqiu.com",
}
#: aggregators whose outbound links are worth one hop anywhere
#: (robots.txt still governs; those targets' own links are never recursed)
TRUSTED_AGGREGATORS = {
    "quantocracy.com", "habr.com",
}
SKIP_EXT = re.compile(
    r"\.(pdf|jpg|jpeg|png|gif|svg|css|js|zip|gz|mp4|webp|ico|woff2?)($|\?)",
    re.I)
_ASSET_TAGS = ("script", "style", "nav", "footer", "header", "aside",
               "form", "noscript")

#: rule LANGUAGE — sentences stating when to act beat mere concept mentions
RULE_TERMS = re.compile(
    r"(buy|sell|go long|go short"
    r"|enter (a |the )?(trade|position|long|short)"
    r"|exit (a |the )?(trade|position)"
    r"|(buy|sell) signal(s)?|signal(s)? (a |an )?(buy|sell|reversal)"
    r"|trigger\w*|confirm\w*|cross(es|ed|ing)? (above|below|over|under)"
    r"|overbought|oversold|fail(s|ed)? below|hold(s)? above)", re.I)
_CITE = re.compile(r"\s*\[(edit|[a-z]{1,2} \d+|\d+)\]", re.I)
_DISPLAY = re.compile(r"\{\\displaystyle[^{}]*\}")


def normalize(url: str) -> str:
    p = urlparse(url.strip())
    return f"https://{p.netloc.lower()}{p.path.rstrip('/')}"


def host_of(url: str) -> str:
    return urlparse(url).netloc.lower().replace("www.", "")


def allowed_domain(url: str) -> bool:
    h = host_of(url)
    return any(h == d or h.endswith("." + d) for d in ALLOW_DOMAINS)


def extract_links(html: str, base_url: str) -> list[str]:
    """Absolute, non-asset links. Allowlisting happens at enqueue time
    (push) so trusted aggregators can vouch for off-allowlist targets."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "javascript:", "#")):
            continue
        absu = normalize(urljoin(base_url, href))
        if not absu.startswith("http") or SKIP_EXT.search(absu):
            continue
        out.append(absu)
    return sorted(set(out))


def html_to_text(html: str, cap: int = 14_000) -> str:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for t in soup.find_all(_ASSET_TAGS):
            t.decompose()
        title = soup.title.string.strip() if soup.title and \
            soup.title.string else ""
        body = soup.get_text(separator="\n")
    except Exception:
        return ""
    text = _DISPLAY.sub(" ", body)
    text = _CITE.sub("", text)
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines
             if ln and not re.fullmatch(r"[=\[\]|/+·•vte\d\s]{1,12}", ln)]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return f"{title}\n{text[:cap]}"


def passages_from(text: str, target_len: int = 600) -> list[str]:
    """Sentence-boundary chunks, scored later; keeps tables/lists intact."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, cur = [], ""
    for s in sentences:
        if len(cur) + len(s) > target_len and cur:
            chunks.append(cur.strip())
            cur = s
        else:
            cur += f" {s}" if cur else s
    if cur.strip():
        chunks.append(cur.strip())
    return [c for c in chunks if len(c) > 120]


def chunk_score(chunk: str) -> int:
    """Concept mentions + double weight for rule language."""
    return (len(STRATEGY_TERMS.findall(chunk))
            + 2 * len(RULE_TERMS.findall(chunk)))


def is_nav_like(chunk: str) -> bool:
    """Menus/TOCs are keyword-dense but fragmented — kill on sight."""
    lines = [l for l in chunk.splitlines() if l.strip()]
    if len(lines) < 3:
        return False
    avg_len = sum(len(l) for l in lines) / len(lines)
    return avg_len < 32


class DeepCrawler:
    def __init__(self, journal: Journal, cfg: dict, feed, notifier=None):
        self.journal = journal
        self.cfg = cfg
        self.feed = feed
        self.notifier = notifier
        c = cfg.get("crawler", {})
        self.max_pages = int(c.get("max_pages_per_cycle", 25))
        self.max_depth = int(c.get("max_depth", 2))
        self.seeds = list(c.get("seeds", SEEDS))
        self.mine_budget = int(c.get("passages_per_mine", 10))
        self.queue_budget = int(c.get("passages_per_queue", 40))
        self.per_host_delay = float(c.get("per_host_delay_s", 1.5))
        self.scraper = Scraper(journal, cfg, feed, notifier)
        self._robots: dict[str, tuple[float, RobotFileParser | None]] = {}
        self._last_hit: dict[str, float] = {}

    # ── politeness ───────────────────────────────────────────────────────
    def _can_fetch(self, url: str) -> bool:
        h = host_of(url)
        ts, rp = self._robots.get(h, (0.0, None))
        if time.time() - ts > 86_400:
            rp = RobotFileParser()
            try:
                r = requests.get(f"https://{h}/robots.txt", headers=UA,
                                 timeout=8)
                rp.parse(r.text.splitlines() if r.status_code == 200
                         else [])
            except Exception:
                rp = None                 # fail-open
            self._robots[h] = (time.time(), rp)
        try:
            return rp is None or rp.can_fetch(UA["User-Agent"], url)
        except Exception:
            return True

    def _throttle(self, url: str) -> None:
        h = host_of(url)
        wait = self.per_host_delay - (time.time() - self._last_hit.get(h, 0))
        if wait > 0:
            time.sleep(wait)
        self._last_hit[h] = time.time()

    # ── dedupe ───────────────────────────────────────────────────────────
    #: a page already read is skipped for this long, then re-read. Without a
    #: TTL the frontier is permanently poisoned: the 12 static seeds were
    #: consumed on day one and every crawl since reported pages=0, docs=0.
    RECRAWL_AFTER_DAYS = 30

    def _seen_doc(self, url: str) -> bool:
        n = self.journal.query(
            "SELECT COUNT(*) n FROM brain_events WHERE kind='crawl_doc' "
            "AND subject=? AND ts > ?",
            (hashlib.md5(normalize(url).encode()).hexdigest()[:16],
             (datetime.now(timezone.utc)
              - timedelta(days=self.RECRAWL_AFTER_DAYS)).isoformat()))[0]["n"]
        return n > 0

    def _fetch(self, url: str, via_trusted: bool = False) -> \
            tuple[str, list[str]] | None:
        if not (allowed_domain(url) or via_trusted) or \
                not self._can_fetch(url):
            return None
        self._throttle(url)
        try:
            r = requests.get(url, headers=UA, timeout=15)
            ctype = r.headers.get("content-type", "")
            if r.status_code != 200 or not ctype.startswith("text/"):
                return None
            html = r.text
        except Exception as e:
            log.debug(f"crawl fetch {url}: {e}")
            return None
        return html_to_text(html), extract_links(html, url)

    # ── the cycle ────────────────────────────────────────────────────────
    def crawl_once(self) -> dict:
        stats = {"pages": 0, "docs": 0, "passages": 0, "mined": 0,
                 "extracted": 0, "accepted": 0, "rejected": 0}

        frontier: deque[tuple[str, int, bool]] = deque()   # (url, depth, via_trusted)
        enqueued: set[str] = set()

        def push(url: str, depth: int, via_trusted: bool = False):
            u = normalize(url)
            if u in enqueued or self._seen_doc(u):
                return
            if not (allowed_domain(u) or via_trusted):
                return
            enqueued.add(u)
            frontier.append((u, depth, via_trusted))

        for s in self.seeds:
            push(s, 0)
        # synergy: deep-read what this window's RSS surfaced
        for feed_url in (self.cfg.get("scraper", {})
                         .get("rss_feeds", DEFAULT_RSS_FEEDS))[:4]:
            for item in scrape_feed(feed_url):
                link = item.get("link") or ""
                if allowed_domain(link):
                    push(link, 1)

        mined: list[dict] = []
        while frontier and stats["pages"] < self.max_pages:
            url, depth, via_trusted = frontier.popleft()
            got = self._fetch(url, via_trusted)
            if got is None:
                continue
            text, links = got
            stats["pages"] += 1
            trusted_page = host_of(url) in TRUSTED_AGGREGATORS
            if len(text) < 400 and not trusted_page:
                continue
            uid = hashlib.md5(url.encode()).hexdigest()[:16]
            chunks = [c for c in passages_from(text)
                      if chunk_score(c) >= 3 and not is_nav_like(c)]
            self.journal.log_brain_event("crawl_doc", uid,
                                         {"url": url,
                                          "chars": len(text),
                                          "hot_passages": len(chunks)})
            stats["docs"] += 1
            seen_first: set = set()
            for c in sorted(chunks, key=chunk_score, reverse=True):
                key = c[:80].lower()
                if key in seen_first:
                    continue
                seen_first.add(key)
                mined.append({"pid": f"{uid}_{len(mined)}", "url": url,
                              "score": chunk_score(c), "text": c})
            if depth < self.max_depth:
                for l in links[:20]:
                    # aggregator outbound links get one hop anywhere;
                    # their targets' own links are never recursed into
                    push(l, depth + 1,
                         via_trusted=trusted_page and depth + 1 <= 1)

        # top passages only — tokens go to the densest material
        mined.sort(key=lambda p: p["score"], reverse=True)
        batch = mined[:self.mine_budget]
        stats["passages"] = len(mined)
        stats["mined"] = len(batch)

        # Queue the densest passages for the Strategist — the only path
        # that turns mined text into a strategy. The crawler's job ends
        # at the queue; it keeps the text whole and lets the Strategist
        # write a spec from the mechanism.
        stats["queued"] = 0
        for p in mined[:self.queue_budget]:
            if ideas.record(self.journal, {
                    "idea_id": p["pid"], "title": p["text"][:120],
                    "text": p["text"], "url": p["url"],
                    "source": f"crawled:{host_of(p['url'])}"}):
                stats["queued"] += 1

        self.journal.log_brain_event("crawl_cycle", "crawler", stats)
        log.info(f"crawl: {stats}")
        return stats
