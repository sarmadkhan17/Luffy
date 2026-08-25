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
FAMILY_DOCS = {
    "ema_trend": "trend continuation via EMA alignment + pullback entry",
    "vwap_fade": "fade statistical extremes of an anchored VWAP",
    "breakout_retest": "trade confirmed range breakouts on first retest hold",
    "sweep_reversal": "trade stop-hunt reversals when swept levels reclaim fast",
    "rotation_momo": "ride BTC-led catch-up flows into lagging alts",
}


def scrape_feed(feed_url: str, timeout: int = 12) -> list[dict]:
    """Fetch + parse an RSS feed → [{source, idea_id, title, text, age_h}].

    Tolerant of CDATA, whitespace and embedded HTML; shared by the
    Harvester and the NewsGuard.
    """
    try:
        r = requests.get(feed_url, headers=UA, timeout=timeout)
    except Exception as e:
        log.warning(f"rss fetch failed {feed_url}: {e}")
        return []
    items = []
    for m in re.finditer(
            r"<item>[\s\S]*?</item>", r.text):
        block = m.group(0)
        tm = re.search(r"<title>(?:<!\[CDATA\[)?([\s\S]+?)(?:\]\]>)?</title>",
                       block)
        dm = re.search(r"<description>(?:<!\[CDATA\[)?([\s\S]{40,1200}?)"
                       r"(?:\]\]>)?</description>", block)
        pm = re.search(r"<pubDate>([^<]+)</pubDate>", block)
        if not tm or not dm:
            continue
        desc = re.sub(r"\s+", " ",
                      re.sub(r"<[^>]+>", " ", dm.group(1))).strip()
        if len(desc) < 40:
            continue
        age_h = None
        if pm:
            try:
                from email.utils import parsedate_to_datetime
                age_h = (time.time() - parsedate_to_datetime(
                    pm.group(1).strip()).timestamp()) / 3600
            except Exception:
                pass
        title = re.sub(r"\s+", " ", tm.group(1)).strip()
        items.append({"source": feed_url,
                      "idea_id": "rss_" + hashlib.md5(
                          title.encode()).hexdigest()[:10],
                      "title": title,
                      "text": desc[:600],
                      "age_h": round(age_h, 2) if age_h is not None else None})
    seen, out = set(), []
    for it in items:
        if it["idea_id"] in seen:
            continue
        seen.add(it["idea_id"])
        out.append(it)
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
        self.tags = h.get("tags", ["btcusdt", "ethusdt", "solusdt"])
        self.ideas_per_cycle = int(h.get("ideas_per_cycle", 6))
        self.tv = TVClient(
            interval=cfg["strategies"].get("tv_interval", "1h"),
            period=cfg["strategies"].get("tv_period", "1y"),
            min_oos_trades=int(
                cfg["strategies"].get("tv_min_oos_trades", 5)),
            require_positive_oos=bool(
                cfg["strategies"].get("tv_require_positive_oos", True)))

    # ── SCRAPE ───────────────────────────────────────────────────────────
    def scrape_tv_ideas(self, tag: str) -> list[dict]:
        try:
            r = requests.get(f"https://www.tradingview.com/ideas/{tag}/",
                             headers=UA, timeout=12)
        except Exception as e:
            log.warning(f"tv ideas fetch {tag}: {e}")
            return []
        items = []
        for m in re.finditer(
                r'\{"id":(\d+),"image_url":"[^"]*","name":"((?:[^"\\]|\\.)*)",'
                r'"description":"((?:[^"\\]|\\.)*)"', r.text):
            try:
                name = m.group(2).encode().decode("unicode_escape", "ignore")
                desc = m.group(3).encode().decode("unicode_escape", "ignore")
            except Exception:
                continue
            items.append({"source": f"tradingview.com/ideas/{tag}",
                          "idea_id": f"tv_{m.group(1)}",
                          "title": name.strip(),
                          "text": desc.strip()[:600]})
        # dedupe within page
        seen, out = set(), []
        for it in items:
            if it["idea_id"] in seen:
                continue
            seen.add(it["idea_id"])
            out.append(it)
        return out

    def scrape_rss(self, feed_url: str = "https://cointelegraph.com/rss") -> list[dict]:
        return scrape_feed(feed_url)

    # ── dedupe ───────────────────────────────────────────────────────────
    def _already_processed(self, idea_id: str) -> bool:
        n = self.journal.query(
            "SELECT COUNT(*) n FROM brain_events WHERE kind='harvest_idea' "
            "AND subject=?", (idea_id,))[0]["n"]
        return n > 0

    # ── EXTRACT ──────────────────────────────────────────────────────────
    def idea_to_genome(self, idea: dict, tv_context: dict):
        """DeepSeek turns human idea-speak into a genome, or None to skip."""
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
        family = raw.get("family")
        if family not in FAMILY_GENE_SPECS or \
                not isinstance(raw.get("params"), dict):
            return None
        g = Genome(strategy_id=f"hav_{family}_{hashlib.md5(
                       idea['idea_id'].encode()).hexdigest()[:6]}",
                   family=family,
                   hypothesis=(raw.get("hypothesis") or
                               f"harvested from {idea['source']}")[:300],
                   invalidation="Demote on PF<0.85/20 trades or 6 straight losses.",
                   regime_filter=frozenset(["TRENDING_UP", "TRENDING_DOWN",
                                            "RANGING", "VOLATILE"]),
                   markets=frozenset({"futures"}), params=raw["params"])
        if Genome.validate(g):
            return None
        return g

    # ── TEST + DEPLOY ────────────────────────────────────────────────────
    def _dual_gauntlet(self, g: Genome) -> tuple[bool, dict]:
        tv = self.tv.walk_forward("BTC/USDT", g.family)
        if not tv.get("valid"):
            return False, {"stage": "tradingview", "tv": {
                k: tv.get(k) for k in ("verdict", "oos_return_pct",
                                       "oos_trades", "oos_sharpe",
                                       "checks")}}
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
        return True, {"tv": {k: tv.get(k) for k in
                             ("oos_return_pct", "oos_trades", "oos_sharpe",
                              "oos_win_rate", "beats_buy_hold")},
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

    # ── the cycle ────────────────────────────────────────────────────────
    def harvest_once(self) -> dict:
        stats = {"scraped": 0, "new": 0, "extracted": 0, "accepted": 0,
                 "rejected": 0, "skipped": 0}
        ideas: list[dict] = []
        for tag in self.tags:
            ideas += self.scrape_tv_ideas(tag)
        ideas += self.scrape_rss()
        stats["scraped"] = len(ideas)

        fresh = [i for i in ideas if not self._already_processed(i["idea_id"])]
        stats["new"] = len(fresh)
        if not fresh:
            self.journal.log_brain_event("harvest_cycle", "harvester", stats)
            return stats

        tv_context = {}
        try:
            tv_context = self.tv.compare_families("BTC/USDT")
        except Exception:
            pass

        budget = self.ideas_per_cycle
        for idea in fresh[:budget]:
            self.journal.log_brain_event("harvest_idea", idea["idea_id"],
                                         {"title": idea["title"][:120],
                                          "source": idea["source"]})
            g = self.idea_to_genome(idea, tv_context)
            if g is None:
                stats["skipped"] += 1
                continue
            stats["extracted"] += 1
            ok, evidence = self._dual_gauntlet(g)
            if ok:
                self._deploy(g, idea, evidence)
                stats["accepted"] += 1
            else:
                stats["rejected"] += 1
                self.journal.log_brain_event(
                    "harvest_rejected", idea["idea_id"],
                    {"title": idea["title"][:100], **evidence})

        self.journal.log_brain_event("harvest_cycle", "harvester", stats)
        log.info(f"harvest: {stats}")
        return stats
