"""The Harvester — the numeric half of research.

The Scraper reads prose: ideas, essays, papers. The Harvester reads numbers.
Between them they are the only two ways a strategy enters the firm.

Until now this role had no code. `DerivFeed` recorded four Binance series on a
timer inside the kernel and nobody owned the question the Strategist actually
needs answered: *which numeric series are covered, over what span, and what
can therefore be tested today.* Three of the last four specs the Strategist
wrote were rejected for `MIN_COVERAGE` on data that was never going to be
there — tokens spent writing mechanisms the Analyst had to refuse.

So the Harvester does two jobs:

1. **Acquire.** Pull every numeric series from every source that serves it,
   preferring the source with real history. Binance caps open interest, taker
   ratio and long/short at roughly 30 days; funding and basis are not capped
   at all, and Coinalyze serves the capped three far deeper when a key is
   configured.
2. **Report.** Publish a coverage brief — the Strategist reads it before
   writing, so it proposes mechanisms the data can actually support.
"""
from __future__ import annotations

import logging
import time

from ..data.coinalyze import Coinalyze
from ..data.derivatives import SERIES, DerivFeed
from ..strategy.spec_evidence import MIN_COVERAGE
from ..data.mcp_client import from_config

log = logging.getLogger(__name__)

#: minutes per execution bar, for turning `backtest_bars` into a span
_TF_MIN = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}


class Harvester:
    """Numeric data acquisition and the coverage brief."""

    def __init__(self, journal, cfg: dict, feed=None, notifier=None):
        self.journal = journal
        self.cfg = cfg
        self.feed = feed
        self.notifier = notifier
        h = cfg.get("harvester", {}) or {}
        self.symbols = list(h.get("symbols")
                            or cfg.get("derivatives", {}).get("symbols", []))
        self.delay = float(h.get("request_delay_s", 0.3))
        self.deriv = DerivFeed()
        self.coinalyze = Coinalyze()
        self._ccfg = h.get("coinalyze", {}) or {}
        self._mcp = None
        self.required_days = self._frame_days() * MIN_COVERAGE

    def _frame_days(self) -> float:
        """How long the Analyst's backtest frame actually is.

        The coverage gate is not a fixed number of days — it is 90% of the
        frame a spec is scored over. Hardcoding a day count here would let
        the brief promise data the Analyst then refuses, which is the exact
        failure this class exists to prevent.
        """
        st = self.cfg.get("strategies", {}) or {}
        bars = int(st.get("backtest_bars", 8000)) or 8000
        tf = (self.cfg.get("timeframes", {}) or {}).get("execution", "15m")
        return bars * _TF_MIN.get(tf, 15) / 1440.0

    # ── MCP ──────────────────────────────────────────────────────────────
    @property
    def mcp(self) -> dict:
        """Lazily built MCP clients — a dead server must not stall boot."""
        if self._mcp is None:
            try:
                self._mcp = from_config(self.cfg)
            except Exception as e:
                log.warning(f"mcp clients unavailable: {e}")
                self._mcp = {}
        return self._mcp

    def mcp_probe(self) -> dict:
        """{server: [tool names]} — what the configured MCP servers offer."""
        out = {}
        for name, client in self.mcp.items():
            try:
                out[name] = [t.get("name") for t in client.tools()]
            except Exception as e:
                log.warning(f"mcp probe {name}: {e}")
                out[name] = []
        return out

    # ── acquire ──────────────────────────────────────────────────────────
    def harvest_once(self, backfill: bool = False) -> dict:
        """One acquisition pass over every symbol and series."""
        stats: dict = {"symbols": len(self.symbols), "rows": 0}
        if not self.symbols:
            return stats
        try:
            counts = (self.deriv.backfill(self.symbols, delay=self.delay)
                      if backfill else
                      self.deriv.record_all(self.symbols, delay=self.delay))
            stats["rows"] = sum(counts.values())
            stats["per_series"] = counts
        except Exception as e:
            log.warning(f"harvest acquire failed: {e}")
            stats["error"] = str(e)
        stats["deep"] = self.deepen()
        stats["brief"] = self.brief()
        self.journal.log_brain_event("harvest_numeric", "harvester", stats)
        log.info(f"harvest(numeric): rows={stats['rows']} "
                 f"usable={stats['brief']['usable']}")
        return stats

    def deepen(self) -> dict:
        """Fill the series Binance truncates, from a source that keeps them.

        No-op without a Coinalyze key, which is the honest default: the three
        positioning series then stay `thin` in the brief and the Strategist
        is told not to build on them.
        """
        out: dict = {}
        if not (self._ccfg.get("enabled", True) and self.coinalyze.available):
            return out
        years = float(self._ccfg.get("history_years", 2.0))
        interval = self._ccfg.get("interval", "4h")
        for sym in self.symbols:
            for series in ("oi", "ls_ratio", "funding"):
                try:
                    df = self.coinalyze.history(sym, series, years=years,
                                                interval=interval)
                except Exception as e:
                    log.warning(f"coinalyze {sym}/{series}: {e}")
                    continue
                if df is not None and len(df):
                    self.deriv.save(sym, series, df)
                    out[series] = out.get(series, 0) + len(df)
                time.sleep(self.delay)      # 40 calls/min on the free tier
        return out

    # ── report ───────────────────────────────────────────────────────────
    def coverage(self) -> dict:
        """{series: {symbols, rows, days}} folded across symbols."""
        out: dict = {}
        try:
            raw = self.deriv.coverage()
        except Exception as e:
            log.warning(f"coverage read failed: {e}")
            return out
        for (sym, series), (n, lo, hi) in raw.items():
            rec = out.setdefault(series, {"symbols": 0, "rows": 0,
                                          "days": 0.0})
            rec["symbols"] += 1
            rec["rows"] += int(n or 0)
            try:
                days = (int(hi) - int(lo)) / 86400_000.0
            except (TypeError, ValueError):
                days = 0.0
            rec["days"] = round(max(rec["days"], days), 1)
        return out

    def brief(self) -> dict:
        """What the Strategist is allowed to build on.

        `usable` are the series with enough span to survive the Analyst's
        coverage check; `thin` are present but too short; `missing` have no
        rows at all. Writing a mechanism on anything but `usable` produces a
        spec that will be refused, not tested.
        """
        cov = self.coverage()
        need = round(self.required_days, 1)
        usable, thin, missing = [], [], []
        for series in SERIES:
            rec = cov.get(series)
            if not rec or not rec["rows"]:
                missing.append(series)
            elif rec["days"] >= need:
                usable.append(series)
            else:
                thin.append(f"{series}({rec['days']}d/{need}d)")
        return {"usable": usable, "thin": thin, "missing": missing,
                "required_days": need, "detail": cov, "ohlcv": "always"}

    def brief_text(self) -> str:
        """The brief as one prompt-sized paragraph for the Strategist."""
        b = self.brief()
        lines = [f"Numeric data actually available to test against "
                 f"(a series must span {b['required_days']} days to be "
                 f"scored, not merely exist):",
                 "  ohlcv: full history for every symbol",
                 f"  usable: {', '.join(b['usable']) or 'none'}"]
        if b["thin"]:
            lines.append(f"  too short to test yet: {', '.join(b['thin'])}")
        if b["missing"]:
            lines.append(f"  not collected: {', '.join(b['missing'])}")
        lines.append("A mechanism built on anything not listed as usable "
                     "will be refused for coverage, not tested — prefer a "
                     "mechanism you can prove today.")
        return "\n".join(lines)
