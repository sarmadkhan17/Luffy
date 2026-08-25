"""Strategy Harvester — continuous discovery from the internet.

Loop: SCRAPE (TradingView ideas pages + crypto RSS) → EXTRACT (DeepSeek
turns human idea-speak into genome JSON, or skips) → TEST (dual gauntlet:
TradingView walk-forward primary + internal sanity) → DEPLOY survivors to
paper probation with source provenance.

Everything is journaled: scraped-idea ids (dedupe), rejections with
reasons, acceptances with source. Budget-guarded by BrainLLM.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time

import requests

from ..brain.llm import BrainLLM
from ..brain.tv import TVClient
from ..core.journal import Journal
from ..strategy.backtest import walk_forward
from ..strategy.genome import FAMILY_GENE_SPECS, Genome

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


def idea_score(item: dict) -> int:
    """Cheap systematic-vs-hype screen. Higher = more likely a real,
    expressible trading idea; negative = pure hype/news."""
    hay = f"{item.get('title', '')} {item.get('text', '')[:400]}"
    score = 2 * len(STRATEGY_TERMS.findall(hay))
    if HYPE.search(hay):
        score -= 4
    return score


FAMILY_DOCS = {
    "ema_trend": "trend continuation via EMA alignment + pullback entry",
    "vwap_fade": "fade statistical extremes of an anchored VWAP",
    "breakout_retest": "trade confirmed range breakouts on first retest hold",
    "sweep_reversal": "trade stop-hunt reversals when swept levels reclaim fast",
    "rotation_momo": "ride BTC-led catch-up flows into lagging alts",
    "rsi_extreme": "fade momentum exhaustion: enter when RSI crosses back "
                   "out of an overbought/oversold extreme",
    "ma_cross": "classic trend-following: fast MA crossing slow MA starts "
                "a position in the cross direction",
    "bb_fade": "fade overextension: price pierces a Bollinger band then "
               "closes back inside",
}


def scrape_feed(feed_url: str, timeout: int = 15) -> list[dict]:
    """Fetch + parse an RSS *or Atom* feed → [{source, idea_id, title,
    text, age_h}]. Tolerant of CDATA, whitespace, embedded HTML and XML
    entities; shared by the Harvester and the NewsGuard."""
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


class Harvester:
    def __init__(self, journal: Journal, cfg: dict, feed,
                 notifier=None):
        self.journal = journal
        self.cfg = cfg
        self.feed = feed
        self.notifier = notifier
        self.llm = BrainLLM(cfg)
        h = cfg.get("harvester", {})
        self.tags = h.get("tv_tags", h.get("tags", DEFAULT_TV_TAGS))
        self.tv_pages = int(h.get("tv_pages", 2))
        self.feeds = list(h.get("rss_feeds", DEFAULT_RSS_FEEDS))
        self.ideas_per_cycle = int(h.get("ideas_per_cycle", 12))
        self.tv = TVClient(
            interval=cfg["strategies"].get("tv_interval", "1h"),
            period=cfg["strategies"].get("tv_period", "1y"),
            min_oos_trades=int(
                cfg["strategies"].get("tv_min_oos_trades", 5)),
            require_positive_oos=bool(
                cfg["strategies"].get("tv_require_positive_oos", True)))

    # ── SCRAPE ───────────────────────────────────────────────────────────
    def scrape_tv_ideas(self, tag: str) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for page in range(1, max(1, self.tv_pages) + 1):
            suffix = f"?page={page}" if page > 1 else ""
            try:
                r = requests.get(
                    f"https://www.tradingview.com/ideas/{tag}/{suffix}",
                    headers=UA, timeout=12)
            except Exception as e:
                log.warning(f"tv ideas fetch {tag} p{page}: {e}")
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
                    "source": f"tradingview.com/ideas/{tag}",
                    "idea_id": idea_id,
                    "title": name.strip(),
                    "text": desc.strip()[:600]})
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

    # ── EXTRACT ──────────────────────────────────────────────────────────
    def _genome_from(self, idea: dict, raw: dict) -> Genome | None:
        family = raw.get("family")
        if family not in FAMILY_GENE_SPECS:
            return None
        hyp = (raw.get("hypothesis") or "").strip()
        hyp = f"{hyp} — harvested from {idea['source']}: {idea['title'][:90]}"
        g = Genome(strategy_id=f"hav_{family}_{hashlib.md5(
                       idea['idea_id'].encode()).hexdigest()[:6]}",
                   family=family,
                   hypothesis=hyp[:300],
                   invalidation="Demote on PF<0.85/20 trades or 6 straight losses.",
                   regime_filter=frozenset(["TRENDING_UP", "TRENDING_DOWN",
                                            "RANGING", "VOLATILE"]),
                   markets=frozenset({"futures"}),
                   params=repair_params(family, raw.get("params")))
        if Genome.validate(g):
            return None
        return g

    def extract_batch(self, ideas: list[dict], tv_context: dict) -> dict:
        """DeepSeek evaluates a shortlist in ONE call.

        Returns {idea_id: genome-or-None}. Falls back to per-idea calls
        when the batch reply is unusable."""
        if not (self.llm and self.llm.available) or not ideas:
            return {i["idea_id"]: None for i in ideas}
        families = {k: FAMILY_DOCS[k] for k in FAMILY_GENE_SPECS}
        genes_doc = {fam: {k: f"{v[1]}..{v[2]}" for k, v in spec.items()}
                     for fam, spec in FAMILY_GENE_SPECS.items()}
        listing = "\n".join(
            f'{i+1}. id={idea["idea_id"]} | {idea["title"]} | '
            f'{idea["text"][:450]}' for i, idea in enumerate(ideas))
        prompt = (
            "Below are trading ideas published by humans.\n"
            + listing + "\n\n"
            "Our system can only express these strategy families "
            f"(with their gene schemas):\n{json.dumps(genes_doc, indent=1)}\n\n"
            f"Current TradingView walk-forward context: "
            f"{json.dumps(tv_context)[:500]}\n\n"
            'For EACH idea output {"id", then either a genome '
            '{"family","params",{...within schema},"hypothesis":"one '
            'sentence"} or {"skip":true,"reason":"..."}}. Skip opinions, '
            "chart art, news and hype — only systematic, expressible "
            "strategies. Never invent gene names.\n\n"
            'Reply as one JSON object: {"results":[...one entry per idea, '
            "same order...]}")
        raw = self.llm.chat_json(prompt, deep=False)
        out: dict = {}
        results = raw.get("results") if isinstance(raw, dict) else None
        by_pos: dict[int, dict] = {}
        if isinstance(results, list):
            for entry in results:
                if not isinstance(entry, dict):
                    continue
                eid = entry.get("id")
                idx = next((i for i, idea in enumerate(ideas)
                            if idea["idea_id"] == eid), None)
                if idx is None:
                    idx = len(by_pos)          # positional fallback
                by_pos[idx] = entry
        for i, idea in enumerate(ideas):
            entry = by_pos.get(i)
            if not entry or entry.get("skip"):
                out[idea["idea_id"]] = None
                continue
            out[idea["idea_id"]] = self._genome_from(idea, entry)
        return out

    def idea_to_genome(self, idea: dict, tv_context: dict):
        """Single-idea extraction (fallback path)."""
        if not (self.llm and self.llm.available):
            return None
        families = {k: FAMILY_DOCS[k] for k in FAMILY_GENE_SPECS}
        genes_doc = {fam: {k: f"{v[1]}..{v[2]}"
                           for k, v in spec.items()}
                     for fam, spec in FAMILY_GENE_SPECS.items()}
        prompt = (
            "A human trader published this trading idea:\n"
            f'TITLE: {idea["title"]}\nTEXT: {idea["text"]}\n\n'
            "Our system can only express these strategy families "
            f"(with their gene schemas):\n{json.dumps(genes_doc, indent=1)}\n\n"
            f"Current TradingView walk-forward context: "
            f"{json.dumps(tv_context)[:500]}\n\n"
            "If the idea maps cleanly onto ONE family, output its genome "
            'JSON: {"family","params",{...within schema},"hypothesis":'
            '"one sentence connecting the idea\'s logic to the family"}.\n'
            "If it is not a systematic strategy (just an opinion/chart "
            'art/hype), output {"skip":true,"reason":"..."}. Never invent '
            "gene names.")
        raw = self.llm.chat_json(prompt, deep=False)
        if not raw or raw.get("skip"):
            return None
        return self._genome_from(idea, raw)

    # ── TEST + DEPLOY ────────────────────────────────────────────────────
    def _dual_gauntlet(self, g: Genome) -> tuple[bool, dict]:
        """Stage order: Yahoo prefilter (cheap) → internal sanity (cheap)
        → real-TV Strategy Tester (expensive, budget-aware)."""
        if not hasattr(self, "_strategy_judge"):
            from .judge import StrategyJudge
            self._strategy_judge = StrategyJudge(
                self.journal, self.cfg, self.notifier)
        j = self._strategy_judge
        pre_ok, pre_ev = j.prefilter(g)
        if not pre_ok:
            return False, {"stage": "yahoo_prefilter", "tv": pre_ev}
        results = []
        for sym in ("BTC/USDT", "SOL/USDT"):
            df = self.feed.fetch_ohlcv(sym, "15m", limit=2900)
            if df is None or len(df) < 400:
                continue
            results.append(walk_forward(g, df, self.cfg["risk"]))
        test_pfs = [r["test"].profit_factor for r in results
                    if r["test"].trades >= 2]
        if test_pfs and min(test_pfs) < 0.5:
            return False, {"stage": "internal_sanity",
                           "test_pfs": test_pfs}
        tv_ok, tv_ev = j.final_verdict(g)
        return tv_ok, {"stage": tv_ev.get("stage", "yahoo"),
                       "tv": tv_ev,
                       "internal": {"test_pfs": test_pfs}}

    def _deploy(self, g: Genome, idea: dict, evidence: dict):
        from ..core.types import Strategy, StrategyState, new_id
        st = Strategy(id=new_id("hav"), name=f"{g.family} harvested",
                      kind=g.family, params=g.params,
                      state=StrategyState.PAPER,
                      description=f"from {idea['source']}: {idea['title'][:90]}",
                      origin="harvested")
        st.hypothesis, st.invalidation = g.hypothesis, g.invalidation
        st.regime_filter, st.markets = set(g.regime_filter), set(g.markets)
        st.generation, st.parent_id = 1, idea["idea_id"]
        self.journal.upsert_strategy(st)
        self.journal.log_brain_event("harvest_accepted", st.id,
                                     {"source": idea["source"],
                                      "title": idea["title"][:120],
                                      **evidence})
        if self.notifier:
            self.notifier.send(
                f"🌐 Harvested strategy: {st.name}\n"
                f"from {idea['title'][:70]}\n"
                f"TV OOS {evidence['tv'].get('oos_return_pct'):+.1f}% "
                f"(sharpe {evidence['tv'].get('oos_sharpe')}) → paper probation")

    # ── source yield learning ────────────────────────────────────────────
    def _source_scores(self) -> dict[str, float]:
        """Yield per source from past cycles: (accepted×3 + extracted)
        / (scraped + 5). Laplace-smoothed; >0.3 is a productive source."""
        scores: dict[str, float] = {}
        try:
            rows = self.journal.query(
                "SELECT detail FROM brain_events WHERE kind='harvest_cycle' "
                "ORDER BY ts DESC LIMIT 30")
            agg: dict[str, dict] = {}
            for r in rows:
                for src, s in (json.loads(r["detail"]).get(
                        "per_source") or {}).items():
                    a = agg.setdefault(src, {"scraped": 0, "extracted": 0,
                                             "accepted": 0})
                    for k in a:
                        a[k] += s.get(k, 0)
            for src, a in agg.items():
                scores[src] = (a["accepted"] * 3 + a["extracted"]) / \
                              (a["scraped"] + 5)
        except Exception:
            pass
        return scores

    # ── the cycle ────────────────────────────────────────────────────────
    def harvest_once(self) -> dict:
        stats = {"scraped": 0, "new": 0, "screened": 0, "extracted": 0,
                 "accepted": 0, "rejected": 0, "skipped": 0}
        per_source: dict[str, dict] = {}
        ideas: list[dict] = []
        for tag in self.tags:
            got = self.scrape_tv_ideas(tag)
            ideas += got
        ideas += self.scrape_feeds()
        stats["scraped"] = len(ideas)

        fresh = [i for i in ideas if not self._already_processed(i["idea_id"])]
        stats["new"] = len(fresh)
        # cheap screen BEFORE any tokens are spent
        candidates = [i for i in fresh if idea_score(i) >= 1]
        dropped = len(fresh) - len(candidates)
        stats["screened_out"] = dropped

        if not candidates:
            self._log_cycle(stats, per_source)
            return stats

        tv_context = {}
        try:
            tv_context = self.tv.compare_families("BTC/USDT")
        except Exception:
            pass

        # rank: productive sources first, then strategy-speak density,
        # then textual substance (a 2000-char essay beats a chart caption)
        src_scores = self._source_scores()
        def _rank(i):
            base = i.get("source", "")
            key = ".".join(base.split("//")[-1].split("/")[:1]) or base
            return (src_scores.get(key, 0.05), idea_score(i),
                    min(len(i.get("text", "")), 500))
        candidates.sort(key=_rank, reverse=True)

        budget = self.ideas_per_cycle
        batch = candidates[:budget]
        for idea in batch:
            per_source.setdefault(idea["source"], {"scraped": 0,
                                                   "extracted": 0,
                                                   "accepted": 0})
            per_source[idea["source"]]["scraped"] += 1
            # mark processed only what we actually spend tokens on —
            # the rest of the pool stays fresh for later cycles
            self.journal.log_brain_event("harvest_idea", idea["idea_id"],
                                         {"title": idea["title"][:120],
                                          "source": idea["source"]})

        genomes = self.extract_batch(batch, tv_context)
        for idea in batch:
            g = genomes.get(idea["idea_id"])
            if g is None:
                stats["skipped"] += 1
                continue
            stats["extracted"] += 1
            src = per_source[idea["source"]]
            src["extracted"] += 1
            ok, evidence = self._dual_gauntlet(g)
            if ok:
                self._deploy(g, idea, evidence)
                stats["accepted"] += 1
                src["accepted"] += 1
            else:
                stats["rejected"] += 1
                self.journal.log_brain_event(
                    "harvest_rejected", idea["idea_id"],
                    {"title": idea["title"][:100], **evidence})

        self._log_cycle(stats, per_source)
        log.info(f"harvest: {stats}")
        return stats

    def _log_cycle(self, stats: dict, per_source: dict) -> None:
        self.journal.log_brain_event("harvest_cycle", "harvester",
                                     {**stats, "per_source": per_source})
