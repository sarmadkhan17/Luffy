"""Strategy Scraper — continuous discovery from the internet.

Loop: SCRAPE (TradingView scripts pages + crypto RSS) → SCREEN (cheap,
token-free relevance filter) → QUEUE the surviving ideas whole for the
Strategist, which is the only path that turns raw text into a strategy.

Everything is journaled: scraped-idea ids (dedupe), per-cycle yield by
source. No LLM: screening is the token-free `ideas.score()` heuristic. The
BrainLLM handle this class used to hold was never called and was removed
on 2026-09-11.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from urllib.parse import urlparse

import requests

from . import ideas
from ..core.journal import Journal

log = logging.getLogger(__name__)

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120 Safari/537.36"}

DEFAULT_TV_TAGS = ["btcusdt", "ethusdt", "solusdt", "altcoin",
                   "scalping", "breakout", "trend-trading"]
DEFAULT_RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
    "https://newsbtc.com/feed/",
    "https://www.cryptopotato.com/feed/",
    "https://medium.com/feed/tag/algorithmic-trading",
    "https://dev.to/feed/tag/trading",
    "http://arxiv.org/rss/q-fin.TR",
    "http://arxiv.org/rss/q-fin.PM",
    "https://www.reddit.com/r/algotrading/.rss",
]

#: cheap token-free relevance screen: does this text speak strategy?
STRATEGY_TERMS = re.compile(
    r"\b(strategy|setup|entry|exit|backtest|signal|indicator"
    r"|rsi|ema|sma|macd|bollinger|fibonacci?|support|resistance"
    r"|breakout|breakdown|divergence|vwap|scalp\w*|swing|mean[- ]reversion"
    r"|momentum|liquidity|order.?block|fair.value.gap|imbalance"
    r"|wyckoff|elliott|harmonic|candlestick|pattern|stop[- ]loss|take[- ]profit"
    r"|risk.reward|rr ratio|leverage|long position|short position)\b", re.I)
# The queue and feed screen use the same research relevance contract.
research_score = ideas.research_score


def streams_for(item: dict) -> set[str]:
    """Which consumers should see this. An item may serve both."""
    out = set()
    if idea_score(item) >= 1:
        out.add(ideas.STRATEGY)
    if research_score(item) >= 4:
        out.add(ideas.RESEARCH)
    return out


def idea_score(item: dict) -> int:
    """Cheap systematic-vs-hype screen. Higher = more likely a real,
    expressible trading idea; negative = pure hype/news."""
    hay = f"{item.get('title', '')} {item.get('text', '')[:400]}"
    score = 2 * len(STRATEGY_TERMS.findall(hay))
    if ideas.HYPE.search(hay):
        score -= 4
    return score


def scrape_feed(feed_url: str, timeout: int = 15) -> list[dict]:
    """Fetch + parse an RSS *or Atom* feed → [{source, idea_id, title,
    text, link, age_h, published_ts}]. Tolerant of CDATA, whitespace,
    embedded HTML and XML entities; shared by the Scraper and the crawler.

    Any failure is an empty list (the idea harvesters degrade silently).
    Callers that must distinguish an empty feed from a failed one use
    fetch_feed(), which reports the failure explicitly."""
    return fetch_feed(feed_url, timeout)["items"]


#: fetch_feed failure codes (a failure is never a quiet feed)
FEED_FAILURES = ("fetch_timeout", "fetch_connection", "fetch_error", "http_status",
                 "empty_body", "parse_unrecognized_document", "parse_no_usable_items",
                 "parse_error")
_FEED_ROOT = re.compile(r"<(rss|feed|rdf:RDF|channel)[\s>]", re.I)


def fetch_feed(feed_url: str, timeout: int = 15) -> dict:
    """One feed read with explicit truth.

    {url, ok, error_code, error, http_status, attempted_at, completed_at (epoch s),
     blocks, items}. ok=True only for a 2xx response whose body is a recognised
    RSS/Atom document and, when it has entries, at least one usable entry. A
    well-formed feed with no entries is ok with items=[] (genuinely quiet)."""
    out = {"url": feed_url, "ok": False, "error_code": None, "error": None,
           "http_status": None, "attempted_at": time.time(), "completed_at": None,
           "blocks": 0, "items": []}

    def fail(code, err=""):
        out.update(ok=False, error_code=code, error=str(err)[:200],
                   completed_at=time.time(), items=[])
        log.warning(f"rss {code} {feed_url}: {str(err)[:120]}")
        return out
    try:
        r = requests.get(feed_url, headers=UA, timeout=timeout)
    except requests.Timeout as e:
        return fail("fetch_timeout", e)
    except requests.ConnectionError as e:
        return fail("fetch_connection", e)
    except Exception as e:
        return fail("fetch_error", e)
    status = getattr(r, "status_code", None)
    out["http_status"] = status if isinstance(status, int) else None
    if isinstance(status, int) and not 200 <= status < 300:
        return fail("http_status", f"HTTP {status}")
    text = getattr(r, "text", None)
    if not isinstance(text, str) or not text.strip():
        return fail("empty_body", "empty response body")
    try:
        items, blocks = _parse_feed(feed_url, text)
    except Exception as e:
        return fail("parse_error", e)
    out["blocks"] = blocks
    if blocks == 0 and not _FEED_ROOT.search(text[:4000]):
        return fail("parse_unrecognized_document", "no RSS/Atom root or entries")
    if blocks > 0 and not items:
        return fail("parse_no_usable_items", f"0 of {blocks} entries parsed")
    out.update(ok=True, items=items, completed_at=time.time())
    return out


def _parse_feed(feed_url: str, text: str) -> tuple[list[dict], int]:
    """(deduplicated usable items, number of <item>/<entry> blocks seen)."""
    items = []
    blocks = re.findall(r"<item>[\s\S]*?</item>", text) + \
        re.findall(r"<entry>[\s\S]*?</entry>", text)
    for block in blocks:
        tm = re.search(r"<title>(?:<!\[CDATA\[)?([\s\S]+?)(?:\]\]>)?</title>",
                       block)
        dm = re.search(
            r"<description(?:\s[^>]*)?>(?:<!\[CDATA\[)?([\s\S]{40,8000}?)"
            r"(?:\]\]>)?</description>", block)
        if dm is None:
            dm = re.search(r"<content(?:\s[^>]*)?>(?:<!\[CDATA\[)?"
                           r"([\s\S]{40,8000}?)(?:\]\]>)?</content>", block)
        if dm is None:
            dm = re.search(r"<summary(?:\s[^>]*)?>(?:<!\[CDATA\[)?"
                           r"([\s\S]{40,8000}?)(?:\]\]>)?</summary>", block)
        pm = re.search(r"<pubDate>([^<]+)</pubDate>"
                       r"|<published>([^<]+)</published>"
                       r"|<updated>([^<]+)</updated>"
                       r"|<dc:date>([^<]+)</dc:date>", block)
        lm = re.search(r"<link(?:\s[^>]*)?(?:/>|>(?:<!\[CDATA\[)?([\s\S]+?)"
                       r"(?:\]\]>)?</link>)", block)
        if not tm or not dm:
            continue
        import html as _html
        desc = re.sub(r"\s+", " ", _html.unescape(
            re.sub(r"<[^>]+>", " ", dm.group(1)))).strip()
        if len(desc) < 40:
            continue
        age_h = None
        pub_ts = None
        pub_status = "missing"
        if pm:
            raw_date = next((g for g in pm.groups() if g), None)
            if raw_date:
                import datetime as _dt
                from email.utils import parsedate_to_datetime
                raw_date = raw_date.strip()
                ts = None
                try:
                    ts = parsedate_to_datetime(raw_date).timestamp()
                except Exception:
                    try:
                        ts = _dt.datetime.fromisoformat(
                            raw_date.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        pass
                pub_status = "malformed"
                if ts:
                    pub_ts = ts
                    pub_status = "ok"
                    age_h = max(0.0, (time.time() - ts) / 3600)
        title = re.sub(r"\s+", " ", _html.unescape(tm.group(1))).strip()
        link = ""
        if lm:
            link = (lm.group(1) or lm.group(0)).split('"')[0].strip()
            if link.startswith("<"):
                link = re.sub(r"</?link[^>]*>", "", link).strip()
        items.append({"source": feed_url,
                      "idea_id": "rss_" + hashlib.md5(
                          title.encode()).hexdigest()[:10],
                      "title": title,
                      "text": desc[:600],
                      "link": link,
                      "age_h": round(age_h, 2) if age_h is not None else None,
                      "published_ts": pub_ts, "published_status": pub_status})
    seen, out = set(), []
    for it in items:
        if it["idea_id"] in seen:
            continue
        seen.add(it["idea_id"])
        out.append(it)
    return out, len(blocks)


class Scraper:
    def __init__(self, journal: Journal, cfg: dict, feed,
                 notifier=None):
        self.journal = journal
        self.cfg = cfg
        self.feed = feed
        self.notifier = notifier
        h = cfg.get("scraper", cfg.get("harvester", {}))   # old key still honoured
        self.tags = h.get("tv_tags", h.get("tags", DEFAULT_TV_TAGS))
        self.tv_pages = int(h.get("tv_pages", 2))
        self.feeds = list(h.get("rss_feeds", DEFAULT_RSS_FEEDS))
        self.ideas_per_cycle = int(h.get("ideas_per_cycle", 12))

    # ── SCRAPE ───────────────────────────────────────────────────────────
    def scrape_tv_scripts(self, tag: str) -> list[dict]:
        """TradingView's public Pine library — published strategies.

        NOT /ideas/, which is chart commentary: 452 of 452 items queued from
        there were bare headlines with no mechanism in them.
        """
        out: list[dict] = []
        seen: set[str] = set()
        for page in range(1, max(1, self.tv_pages) + 1):
            suffix = f"?page={page}" if page > 1 else ""
            try:
                r = requests.get(
                    f"https://www.tradingview.com/scripts/{tag}/{suffix}",
                    headers=UA, timeout=12)
            except Exception as e:
                log.warning(f"tv scripts fetch {tag} p{page}: {e}")
                break
            page_items = []
            for m in re.finditer(
                    r'\{"id":(\d+),"image_url":"[^"]*","name":"((?:[^"\\]|\\.)*)",'
                    r'"description":"((?:[^"\\]|\\.)*)"', r.text):
                try:
                    name = m.group(2).encode().decode(
                        "unicode_escape", "ignore")
                    desc = m.group(3).encode().decode(
                        "unicode_escape", "ignore")
                except Exception:
                    continue
                idea_id = f"tv_{m.group(1)}"
                if idea_id in seen:
                    continue
                seen.add(idea_id)
                page_items.append({
                    "source": f"tradingview.com/scripts/{tag}",
                    "idea_id": idea_id,
                    "title": name.strip(),
                    "text": desc.strip()[:4000]})
            if not page_items:          # empty page → no deeper pages
                break
            out.extend(page_items)
        return out

    def scrape_feeds(self) -> list[dict]:
        """All configured feeds; per-feed failures degrade silently."""
        items: list[dict] = []
        for url in self.feeds:
            items.extend(scrape_feed(url))
        return items

    # ── dedupe ───────────────────────────────────────────────────────────
    def _already_processed(self, idea_id: str) -> bool:
        n = self.journal.query(
            "SELECT COUNT(*) n FROM brain_events WHERE kind='harvest_idea' "
            "AND subject=?", (idea_id,))[0]["n"]
        return n > 0

    # ── ranking ──────────────────────────────────────────────────────────
    @staticmethod
    def _source(source: str) -> str:
        return urlparse(source if "://" in source else "//" + source).netloc.lower()

    def _rank_key(self, item: dict, src_scores: dict[str, float]) -> tuple:
        return (src_scores.get(self._source(item.get("source", "")), 0.05),
                idea_score(item), min(len(item.get("text", "")), 500))

    def _source_scores(self) -> dict[str, float]:
        """Accepted queue items / (genuine fetched items + 5).

        Pre-correction cycle rows counted selected items as fetched. They
        cannot establish this measurement and are intentionally not reused.
        This is intake yield, never strategy performance or economic value.
        """
        totals = {}
        for row in self.journal.query(
                "SELECT detail FROM brain_events WHERE kind='harvest_cycle' "
                "ORDER BY ts DESC LIMIT 30"):
            try:
                detail = json.loads(row["detail"])
                if detail.get("source_intake") != "fetched_selected_queued.v1":
                    continue
                for source, counts in detail["per_source"].items():
                    fetched, selected, queued = (counts[k] for k in
                                                  ("fetched", "selected", "queued"))
                    if (any(type(n) is not int for n in (fetched, selected, queued))
                            or not 0 <= queued <= selected <= fetched):
                        continue
                    total = totals.setdefault(self._source(source), [0, 0])
                    total[0] += fetched
                    total[1] += queued
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
        return {source: queued / (fetched + 5)
                for source, (fetched, queued) in totals.items()}

    # ── the cycle ────────────────────────────────────────────────────────
    def harvest_once(self) -> dict:
        # Build stats keys from ideas.STREAMS so adding a new stream doesn't break
        stats = {"scraped": 0, "new": 0, "screened": 0, "skipped": 0}
        for stream in ideas.STREAMS:
            stats[f"queued_{stream}"] = 0
        live = ideas.live_streams(self.cfg)
        if not live:
            # No consumer can act on a queued idea (provider blocked): do not fetch or queue anything.
            stats["skipped_reason"] = "no_live_idea_consumer"
            self._log_cycle(stats, {})
            log.info(f"harvest skipped: {stats['skipped_reason']}")
            return stats
        per_source: dict[str, dict] = {}
        # NOT `ideas` — that is the queue module imported at the top of this
        # file, and shadowing it made `ideas.record()` below call .record on a
        # list. The queue then never received a single item.
        scraped: list[dict] = []
        for tag in self.tags:
            got = self.scrape_tv_scripts(tag)
            scraped += got
        scraped += self.scrape_feeds()
        stats["scraped"] = len(scraped)
        stats["source_intake"] = "fetched_selected_queued.v1"
        for item in scraped:
            counts = per_source.setdefault(item["source"],
                {"fetched": 0, "selected": 0, "queued": 0})
            counts["fetched"] += 1

        fresh = [i for i in scraped
                 if not self._already_processed(i["idea_id"])]
        stats["new"] = len(fresh)
        # cheap screen BEFORE any tokens are spent
        candidates = [i for i in fresh if streams_for(i) & live]
        dropped = len(fresh) - len(candidates)
        stats["screened_out"] = dropped

        if not candidates:
            self._log_cycle(stats, per_source)
            return stats

        # rank: productive sources first, then strategy-speak density,
        # then textual substance (a 2000-char essay beats a chart caption)
        src_scores = self._source_scores()
        candidates.sort(key=lambda i: self._rank_key(i, src_scores), reverse=True)

        # source mix: TradingView captions dominate the ranked pool but are
        # mostly discretionary chart-art; reserve seats for systematic
        # sources (arxiv, quant blogs) which formalize far better
        budget = self.ideas_per_cycle
        tv = [c for c in candidates if "tradingview.com" in c.get("source", "")]
        rest = [c for c in candidates
                if "tradingview.com" not in c.get("source", "")]
        batch = rest[:int(budget * 0.6)] + tv[:budget - int(budget * 0.6)]
        # Use unfilled source seats without exceeding the same total budget.
        chosen = {item["idea_id"] for item in batch}
        batch += [item for item in candidates if item["idea_id"] not in chosen][
            :max(0, budget - len(batch))]
        for idea in batch[:budget]:
            per_source[idea["source"]]["selected"] += 1
            # Queue only selected intake; no LLM or provider tokens are spent.
            # Record the idea WHOLE, once, with every stream it qualifies
            # for: the text is the part that holds the mechanism, and the
            # Strategist/Researcher both read it out of this one row.
            item_streams = sorted(streams_for(idea) & live)
            if ideas.record(self.journal, idea, streams=item_streams):
                for stream in item_streams:
                    stats[f"queued_{stream}"] += 1
                per_source[idea["source"]]["queued"] += 1

        self._log_cycle(stats, per_source)
        log.info(f"harvest: {stats}")
        return stats

    def _log_cycle(self, stats: dict, per_source: dict) -> None:
        self.journal.log_brain_event("harvest_cycle", "harvester",
                                     {**stats, "per_source": per_source})
