"""Strategy Scraper — continuous discovery from the internet.

Loop: SCRAPE (TradingView scripts pages + crypto RSS) → SCREEN (cheap,
token-free relevance filter) → QUEUE the surviving ideas whole for the
Strategist, which is the only path that turns raw text into a strategy.

Everything is journaled: scraped-idea ids (dedupe), per-cycle yield by
source. Budget-guarded by BrainLLM (used only for downstream consumers,
not for genome extraction here).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time

import requests

from . import ideas
from ..brain.llm import BrainLLM
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
HYPE = re.compile(
    r"(price prediction|price target|to hit \$|\d+x potential|moon\b"
    r"|buy now|giveaway|airdrop|1000x|next 100x|get rich)", re.I)

#: research prose asserts that an observable predicts something, in a scope,
#: ideally with a magnitude. It rarely contains a single word from
#: STRATEGY_TERMS, which is why a second screen is needed rather than a
#: looser one.
CLAIM_TERMS = re.compile(
    r"\b(predict\w*|forecast\w*|correlat\w+|regress\w+|significan\w+"
    r"|hypothes\w+|evidence|out[- ]of[- ]sample|in[- ]sample|p[- ]value"
    r"|t[- ]stat\w*|sharpe|information ratio|anomal\w+|risk premium|factor"
    r"|cross[- ]section\w*|autocorrelat\w+|persist\w+|decay|half[- ]life"
    r"|microstructure|adverse selection|inventory|order flow|toxicity"
    r"|decile|quantile|percentile|basis points?|bps)\b", re.I)
#: a claim with a number attached is worth more than one without
MAGNITUDE = re.compile(r"\b\d+(\.\d+)?\s?(%|bps|basis points|x)\b", re.I)
#: does it say WHERE the claim holds?
SCOPE_TERMS = re.compile(
    r"\b(regime|horizon|timeframe|intraday|daily|weekly|hours?|days?"
    r"|bull|bear|trending|ranging|volatil\w+)\b", re.I)


def research_score(item: dict) -> int:
    """Screen for a market CLAIM rather than a trade setup."""
    hay = f"{item.get('title', '')} {item.get('text', '')[:1200]}"
    score = 2 * len(CLAIM_TERMS.findall(hay))
    if MAGNITUDE.search(hay):
        score += 4
    if SCOPE_TERMS.search(hay):
        score += 2
    if HYPE.search(hay):
        score -= 8
    return score


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
    if HYPE.search(hay):
        score -= 4
    return score


def scrape_feed(feed_url: str, timeout: int = 15) -> list[dict]:
    """Fetch + parse an RSS *or Atom* feed → [{source, idea_id, title,
    text, age_h}]. Tolerant of CDATA, whitespace, embedded HTML and XML
    entities; shared by the Scraper and the NewsGuard."""
    try:
        r = requests.get(feed_url, headers=UA, timeout=timeout)
    except Exception as e:
        log.warning(f"rss fetch failed {feed_url}: {e}")
        return []
    items = []
    blocks = re.findall(r"<item>[\s\S]*?</item>", r.text) + \
        re.findall(r"<entry>[\s\S]*?</entry>", r.text)
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
                if ts:
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
                      "age_h": round(age_h, 2) if age_h is not None else None})
    seen, out = set(), []
    for it in items:
        if it["idea_id"] in seen:
            continue
        seen.add(it["idea_id"])
        out.append(it)
    return out


def repair_params(family: str, raw: dict) -> dict:
    """Coerce sloppy LLM gene output into valid params.

    Models copy range strings ('5..50'), emit ints for floats, or drift
    outside bounds — every case maps onto the spec's clamped value or
    its default instead of losing the whole genome."""
    from ..strategy.genome import FAMILY_GENE_SPECS
    spec = FAMILY_GENE_SPECS.get(family, {})
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for k, (typ, lo, hi, default) in spec.items():
        v = raw.get(k, default)
        if isinstance(v, str):
            m = re.fullmatch(r"\s*(-?[\d.]+)\s*(?:\.\.|-|to)\s*(-?[\d.]+)\s*",
                             v)
            try:
                v = float(m.group(2)) if m else float(v)
            except (TypeError, ValueError):
                out[k] = default
                continue
        try:
            v = typ(v)
        except (TypeError, ValueError):
            out[k] = default
            continue
        if lo is not None and hi is not None:
            v = min(max(v, lo), hi)
        out[k] = v
    return out


class Scraper:
    def __init__(self, journal: Journal, cfg: dict, feed,
                 notifier=None):
        self.journal = journal
        self.cfg = cfg
        self.feed = feed
        self.notifier = notifier
        self.llm = BrainLLM(cfg)
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
    def _rank_key(self, item: dict, src_scores: dict[str, float]) -> tuple:
        """Ranking key for candidates: productive sources first, then
        strategy-speak density, then textual substance.

        Returns a tuple (source_score, idea_score, text_length) suitable for
        sorting in descending order: highest source yield, highest idea score,
        longest text.
        """
        base = item.get("source", "")
        key = ".".join(base.split("//")[-1].split("/")[:1]) or base
        return (src_scores.get(key, 0.05), idea_score(item),
                min(len(item.get("text", "")), 500))

    # ── source yield learning ────────────────────────────────────────────
    def _source_scores(self) -> dict[str, float]:
        """Yield per source from past cycles: queued / (scraped + 5).
        Laplace-smoothed; >0.3 is a productive source.

        Sources are normalized to hostname only (e.g. "arxiv.org") so that
        multiple feeds from the same domain are learned together, and this
        matches the normalization done in _rank().
        """
        scores: dict[str, float] = {}
        try:
            rows = self.journal.query(
                "SELECT detail FROM brain_events WHERE kind='harvest_cycle' "
                "ORDER BY ts DESC LIMIT 30")
            agg: dict[str, dict] = {}
            for r in rows:
                for src, s in (json.loads(r["detail"]).get(
                        "per_source") or {}).items():
                    # Normalize source to hostname, matching _rank() logic
                    norm_src = ".".join(src.split("//")[-1].split("/")[:1]) or src
                    a = agg.setdefault(norm_src, {"scraped": 0, "queued": 0})
                    for k in a:
                        a[k] += s.get(k, 0)
            for src, a in agg.items():
                scores[src] = a["queued"] / (a["scraped"] + 5)
        except Exception:
            pass
        return scores

    # ── the cycle ────────────────────────────────────────────────────────
    def harvest_once(self) -> dict:
        # Build stats keys from ideas.STREAMS so adding a new stream doesn't break
        stats = {"scraped": 0, "new": 0, "screened": 0, "skipped": 0}
        for stream in ideas.STREAMS:
            stats[f"queued_{stream}"] = 0
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

        fresh = [i for i in scraped
                 if not self._already_processed(i["idea_id"])]
        stats["new"] = len(fresh)
        # cheap screen BEFORE any tokens are spent
        candidates = [i for i in fresh if streams_for(i)]
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
        batch = batch[:budget]
        for idea in batch:
            per_source.setdefault(idea["source"], {"scraped": 0, "queued": 0})
            per_source[idea["source"]]["scraped"] += 1
            # mark processed only what we actually spend tokens on —
            # the rest of the pool stays fresh for later cycles.
            # Record the idea WHOLE, once, with every stream it qualifies
            # for: the text is the part that holds the mechanism, and the
            # Strategist/Researcher both read it out of this one row.
            item_streams = sorted(streams_for(idea))
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
