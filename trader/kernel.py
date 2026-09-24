"""Luffy kernel — full Phase 1 trading loop.

cycle():
  universe → snapshots (+BTC context) → analyst votes → strategy signals
  → orchestrate → journal EVERYTHING → risk-gate → execute w/ native stops
  → detect exchange-side exits (SL/TP fills) → resolve outcomes

Controls honored every cycle: ACTIVE/FROZEN/HALTED, panic flag, proving sizing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import re
import signal
import sys
import threading
import time

from .agents.calibration import maybe_refit as maybe_refit_calibration
from .agents.flow import FlowAnalyst
from .agents.momentum import MomentumAnalyst, RotationAnalyst, ValueAnalyst
from .agents.macro_guard import MacroGuard
from .agents.news_guard import NewsGuard
from .agents.orderbook_depth import DepthScout
from .agents.positioning import PositioningAnalyst
from .agents.regime import btc_context
from .agents.structure import StructureAnalyst
from .core.config import ROOT, load_config
from .core.journal import Journal
from .core.types import (Action, ControlState, MarketType, RiskError,
                         Snapshot, norm_symbol)
from .data.feed import DataFeed, Universe, make_exchange
from .engine.executor import Executor
from .engine.outcomes import resolve_pending
from .engine.reconcile import flatten_all, reconcile_futures
from .engine.risk import RiskManager
from .engine.state import ControlStateMachine
from .engine.supervisor import Supervisor
from .engine.watchdog import Heartbeat, start_stall_monitor
from .notify.telegram import Telegram
from .strategy.genome import Genome

log = logging.getLogger("luffy")
_ATTENTION_FUTURES_SCAN_SYMBOL = re.compile(r"[A-Z0-9]{1,24}/USDT")

AGENTS = {"structure": StructureAnalyst, "flow": FlowAnalyst,
          "momentum": MomentumAnalyst, "value": ValueAnalyst,
          "rotation": RotationAnalyst, "positioning": PositioningAnalyst,
          "depth": DepthScout}


def setup_logging(cfg: dict) -> None:
    from logging.handlers import RotatingFileHandler
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=cfg["logging"].get("level", "INFO"), format=fmt,
        handlers=[RotatingFileHandler(
            logs / "luffy.log",
            maxBytes=int(cfg["logging"].get("max_bytes", 10 << 20)),
            backupCount=int(cfg["logging"].get("backup_count", 5))),
        logging.StreamHandler(sys.stdout)])


class Kernel:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.journal = Journal(str(ROOT / "data" / "luffy.db"))
        self.state_machine = ControlStateMachine(self.journal)
        self.risk = RiskManager(cfg, self.journal)
        self.heartbeat = Heartbeat()
        self.notifier = Telegram()
        self.market_type = MarketType(
            self.journal.kv_get("market_type", cfg["mode"]["market"]))
        self.exchange = make_exchange(self.market_type.value)
        # NOT DataFeed(self.exchange): that is the demo trading venue, and
        # its klines carry simulated volume. Market data comes from the
        # production public API; orders still go to self.exchange via the
        # Executor.
        self.feed = DataFeed()
        self.universe = Universe(cfg, self.exchange)
        self.positioning_agent = PositioningAnalyst(self.exchange)
        self.depth_agent = DepthScout()
        order = ("structure", "momentum", "flow", "value", "rotation",
                 "positioning", "depth")
        self.analysts = [self._make_agent(k) for k in order]
        from .engine.orchestrator import Orchestrator
        self.news_guard = NewsGuard(cfg, self.journal)
        self.macro_guard = MacroGuard(cfg, self.journal)
        self.orchestrator = Orchestrator(self.analysts, self.journal,
                                         news_guard=self.news_guard, cfg=cfg)
        self.executor = Executor(self.exchange, self.journal, cfg,
                                 self.market_type)
        self.supervisor = Supervisor(self.journal, self.state_machine,
                                     self.executor, self.exchange)
        from .engine.exits import ExitEngine

        self.population = self._load_population()
        self._pop_sig = [(st.id, st.state, st.params)
                         for st, _g in self.population]
        from .engine.exits import ExitEngine
        self.exits = ExitEngine(self.exchange, self.journal, self.executor,
                                cfg, genomes={st.id: st.params
                                              for st, _g in self.population},
                                spec_exits=self._spec_exits)
        self._stop = False
        #: how long the last trade cycle took. The research thread stands
        #: down when this runs long: two cores, and the trade loop wins.
        self._last_cycle_s = 0.0
        self._book_cache: dict[str, tuple[float, dict]] = {}
        self._funding_cache: dict[str, float] | None = None
        self._oi_cache: tuple[float, dict[str, dict]] = (0.0, {})
        self._btc_ctx: dict = {}
        self._attention = None
        self._attention_error = None
        #: one explicitly configured observation-only symbol; never traded
        self._attention_extra = None
        #: killable, isolated fetcher for that symbol; never the shared DataFeed
        self._attention_source = None
        #: off | manual | registry; exactly one supplemental slot either way
        self._attention_mode = "off"
        #: registry mode: locked-order selector slot and its background refresh
        self._attention_registry = None
        self._attention_refresher = None
        if (cfg.get("attention") or {}).get("enabled") is True:
            try:
                from .observability.collector import Collector
                self._attention = Collector(ROOT / "data", cfg["attention"])
            except Exception as exc:
                self._attention_error = type(exc).__name__
                log.warning("attention startup failed: %s", self._attention_error)
            self._attention_supplemental_setup(cfg["attention"])

    def _attention_supplemental_setup(self, raw) -> None:
        """Resolve the one supplemental slot: off, manual or registry.

        A misconfiguration disables supplemental observation only; trading
        and the scan collector continue. Registry mode observes production
        Binance USD-M named explicitly in config (never BINANCE_DEMO), only in
        FUTURES mode, and grants no trading permission.
        """
        from .observability.attention import supplemental_mode
        try:
            mode = supplemental_mode(raw)
        except ValueError as exc:
            self._attention_error = ("supplemental_mode_conflict"
                                     if str(exc) == "supplemental_mode_conflict"
                                     else "supplemental_refused")
            log.warning("attention supplemental disabled: %s", exc)
            return
        try:
            from .observability.supplemental import (
                DEFAULT_TIMEOUT_S, IsolatedSource, one_symbol)
            timeout = raw.get("supplemental_timeout_seconds", DEFAULT_TIMEOUT_S)
            if mode == "manual":
                extra = one_symbol(raw.get("supplemental_symbol"))
                if extra is not None:
                    self._attention_source = IsolatedSource(timeout)
                    self._attention_extra = extra
            elif mode == "registry":
                if self.market_type != MarketType.FUTURES:
                    raise ValueError("registry supplemental mode requires FUTURES")
                from .data.registry_provider import (
                    BinanceUsdmRegistryProvider, VenueTarget)
                from .observability.attention import (
                    RegistryAttention, RegistryRefresher, registry_settings)
                rc = registry_settings(raw)
                target = VenueTarget.production()
                provider = BinanceUsdmRegistryProvider(target)
                source = IsolatedSource(timeout, url=target.base_url + "/fapi/v1/klines")
                self._attention_registry = RegistryAttention(
                    provider, source, self.journal,
                    max_snapshot_age_ms=rc["registry_max_snapshot_age_ms"])
                self._attention_refresher = RegistryRefresher(
                    provider, refresh_s=rc["registry_refresh_seconds"],
                    retry_s=rc["registry_retry_seconds"])
            self._attention_mode = mode
        except (ValueError, TypeError) as exc:
            self._attention_error = "supplemental_refused"
            log.warning("attention supplemental refused: %s", exc)

    def _attention_call(self, method, *args):
        """Telemetry failure must never prevent entry checks or exit management."""
        collector = getattr(self, "_attention", None)
        if collector is None:
            return None
        try:
            return getattr(collector, method)(*args)
        except Exception as exc:
            self._attention_error = type(exc).__name__
            log.warning("attention %s failed: %s", method, self._attention_error)
            return None

    def _attention_supplemental(self, scan_symbols):
        """One bounded data attempt for the configured observation-only symbol.

        The result goes to Attention capture and nowhere else: it is not added
        to the scan, universe frames, the Orchestrator, Risk or Execution. A
        symbol already in the scan needs no second fetch. The fetch runs in a
        child process killed at the source's deadline, so it bounds this
        cycle's wait and cannot touch the shared DataFeed, cache or store.
        """
        symbol = getattr(self, "_attention_extra", None)
        source = getattr(self, "_attention_source", None)
        collector = getattr(self, "_attention", None)
        if symbol is None or source is None or collector is None or symbol in scan_symbols:
            return None
        try:
            from .observability.supplemental import fetch
            return fetch(source, symbol, collector.cfg["timeframe"],
                         int(time.time() * 1000))
        except Exception as exc:
            self._attention_error = type(exc).__name__
            log.warning("attention supplemental failed: %s", self._attention_error)
            return None

    def _attention_registry_scan(self, scan_symbols) -> list[str]:
        """FUTURES scan symbols in the selector's unambiguous USD-M spelling.

        The Kernel knows its scan is USD-M linear perps, so 'BASE/USDT'
        becomes 'BASE/USDT:USDT' for the comparison only; _scan_symbols()
        and the trading scan are untouched.
        """
        out = []
        for sym in scan_symbols:
            if isinstance(sym, str) and _ATTENTION_FUTURES_SCAN_SYMBOL.fullmatch(sym):
                sym += ":USDT"
            out.append(sym)
        return out

    def _attention_slot(self, scan_symbols, stats):
        """The single supplemental slot: (result, provenance) or (None, None).

        Registry mode never falls back to the manual path. Persistence runs
        synchronously on this thread through the Journal; the registry itself
        is only read (provider.latest()), never refreshed here.
        """
        registry = getattr(self, "_attention_registry", None)
        if registry is None:
            return self._attention_supplemental(scan_symbols), None
        collector = getattr(self, "_attention", None)
        if collector is None or self.market_type != MarketType.FUTURES:
            return None, None
        try:
            result, provenance, tel = registry.run(
                self._attention_registry_scan(scan_symbols), collector.cfg["timeframe"])
        except Exception as exc:
            result, provenance = None, None
            tel = {"mode": "registry", "stage": "error", "reason": type(exc).__name__}
        refresher = getattr(self, "_attention_refresher", None)
        if refresher is not None:
            try:
                tel["refresh"] = refresher.health()
            except Exception as exc:
                tel["refresh"] = {"error": type(exc).__name__}
        stats["attention_registry"] = tel
        return result, provenance

    def _make_agent(self, key: str):
        if key == "positioning":
            return self.positioning_agent
        if key == "depth":
            return self.depth_agent
        return AGENTS[key]()

    # ── boot ─────────────────────────────────────────────────────────────
    def _load_population(self) -> list[tuple]:
        pop = []
        for row in self.journal.list_strategies(["paper", "active", "demoted"]):
            if row.get("kind") == "spec":
                # Specs enter through the compiled path below. Treating the
                # same row as a legacy genome produces a second, evaluatorless
                # entry and misleading missing-evaluator receipts.
                continue
            st = type("S", (), {})()          # light strategy shim over row
            st.id, st.name, st.state = row["id"], row["name"], row["state"]
            st.params = json.loads(row["params"])
            g = Genome(strategy_id=row["id"], family=row["kind"],
                       hypothesis=row["hypothesis"] or "seed",
                       invalidation=row["invalidation"] or "",
                       regime_filter=frozenset(json.loads(row["regime_filter"] or "[]")),
                       markets=frozenset(json.loads(row["markets"] or '["futures"]')),
                       params=json.loads(row["params"]),
                       generation=row["generation"] or 0)
            st.is_trade_eligible = row["state"] in ("paper", "active")
            pop.append((st, g))
        pop.extend(self._load_spec_population())
        return pop

    def _load_spec_population(self) -> list[tuple]:
        """Compile stored StrategySpecs into the same (strategy, genome)
        shape the orchestrator already consumes.

        library.evaluate() dispatches on genome.family (library.py:293), so
        registering a compiled spec under f"spec:{id}" makes the orchestrator
        find it with no change at all — including its markets and
        regime_filter guards at orchestrator.py:205-207.
        """
        from .strategy.compile import compile_spec
        from .strategy.library import register_evaluator
        pop = []
        requires: set = set()
        spec_exits: dict = {}
        self._spec_exits = spec_exits
        self._spec_rows = []
        try:
            rows = self.journal.list_specs(["paper", "active"])
        except Exception as e:
            log.warning(f"spec population unavailable: {e}")
            return pop
        self._spec_rows = rows
        for row, spec in rows:
            try:
                compiled = compile_spec(spec)
            except Exception as e:
                log.warning(f"spec {spec.id} will not compile: {e}")
                continue
            family = f"spec:{spec.id}"
            requires.update(compiled.data_requires)
            # the geometry this spec was validated with must be the geometry
            # the ExitEngine runs, or the backtest evidenced a different
            # strategy than the one trading
            from .engine.exits import SpecExit
            spec_exits[spec.id] = SpecExit.from_spec(spec)
            register_evaluator(family, compiled.to_evaluator())
            st = type("S", (), {})()
            st.id, st.name, st.state = spec.id, spec.name, row["state"]
            st.params = {}
            st.is_trade_eligible = True
            g = type("G", (), {})()
            g.strategy_id, g.family = spec.id, family
            g.params = {}
            g.markets = frozenset(spec.markets)
            g.regime_filter = frozenset(spec.regime_filter)
            # the universe the spec was ADMITTED over is the universe it may
            # trade; empty means it named none and accepts the venue scan
            g.symbols = frozenset((spec.universe or {}).get("include") or ())
            pop.append((st, g))
        self._spec_requires = tuple(sorted(requires))
        self._spec_exits = spec_exits
        self._derivs_cache = {}
        self._market_cache = None
        if pop:
            log.info(f"loaded {len(pop)} compiled spec(s) into the population"
                     + (f", needing {list(self._spec_requires)}"
                        if self._spec_requires != ("ohlcv",) else ""))
        return pop

    # Derivative series for the live path. Without these, a funding/OI/taker
    # spec evaluates against an all-NaN column and can never fire — which is
    # exactly how three authored specs sat in `paper` with zero trades.
    _DERIVS_TTL = 300.0                # recorder writes every 15m

    def _derivs_for(self, symbol: str) -> dict | None:
        reqs = getattr(self, "_spec_requires", ())
        if not any(r != "ohlcv" for r in reqs):
            return None
        hit = getattr(self, "_derivs_cache", {}).get(symbol)
        now = time.time()
        if hit and now - hit[0] < self._DERIVS_TTL:
            return hit[1]
        try:
            from .strategy.spec_evidence import load_derivs
            out = load_derivs(symbol, reqs)
        except Exception as e:
            log.warning(f"derivs unavailable for {symbol}: {e}")
            out = None
        self._derivs_cache[symbol] = (now, out)
        return out

    _MARKET_TTL = 300.0                # the recorder refreshes hourly

    def _market_for(self) -> dict | None:
        """The reference frames the book's specs read through ref().

        Keyed on the book, not the symbol: the S&P is the same series for
        every coin, so it is loaded once per TTL and shared by every
        snapshot.
        """
        reqs = [r for r in getattr(self, "_spec_requires", ())
                if isinstance(r, str) and r.startswith("ref:")]
        if not reqs:
            return None
        hit = getattr(self, "_market_cache", None)
        now = time.time()
        if hit and now - hit[0] < self._MARKET_TTL:
            return hit[1]
        try:
            from .strategy.spec_evidence import load_refs
            out = load_refs(reqs) or None
        except Exception as e:
            log.warning(f"reference series unavailable: {e}")
            out = None
        self._market_cache = (now, out)
        return out

    def boot(self) -> None:
        log.info(f"LUFFY BOOT | market={self.market_type.value} "
                 f"state={self.state_machine.state.value} "
                 f"population={len(self.population)}")
        self._filter_universe_to_venue()
        recovery = (self.supervisor.pass_once(boot=True)
                    if self.market_type == MarketType.FUTURES else None)
        report = (recovery.actions.get("reconcile", {}) if recovery is not None
                  else reconcile_futures(self.exchange, self.journal))
        if any(report.get(k) for k in ("adopted", "ghosts")):
            self.notifier.send(f"🔧 boot reconciliation: {report}")
        if recovery is not None and recovery.reasons:
            self.notifier.send(f"🔒 recovery {recovery.status}: {', '.join(recovery.reasons)}")
        start_stall_monitor(
            self.heartbeat,
            stale_after=float(self.cfg["timeframes"]["scan_interval_seconds"]) * 4)
        signal.signal(signal.SIGTERM, self._graceful)
        signal.signal(signal.SIGINT, self._graceful)
        threading.Thread(target=self._telegram_listener, daemon=True,
                         name="tg-listener").start()
        threading.Thread(target=self._derivatives_recorder, daemon=True,
                         name="derivs-recorder").start()
        if (self.cfg.get("references", {}) or {}).get("enabled", True):
            threading.Thread(target=self._reference_recorder, daemon=True,
                             name="ref-recorder").start()
        threading.Thread(target=self._strategy_mechanism_loop, daemon=True,
                         name="strategy-mechanism").start()
        threading.Thread(target=self._maybe_validate_agents, daemon=True,
                         name="agent-validator").start()
        if self.cfg.get("scraper", {}).get("enabled", False):
            threading.Thread(target=self._harvest_loop, daemon=True,
                             name="scraper").start()
        if self.cfg.get("crawler", {}).get("enabled", False):
            threading.Thread(target=self._crawl_loop, daemon=True,
                             name="crawler").start()
        if self.cfg.get("rent", {}).get("weekly_usdt"):
            threading.Thread(target=self._rent_loop, daemon=True,
                             name="rent-check").start()
        if (self.cfg.get("research", {}) or {}).get("enabled", False):
            threading.Thread(target=self._research_loop, daemon=True,
                             name="research").start()
        if getattr(self, "_attention_refresher", None) is not None:
            # registry refresh never runs in the trading cycle
            self._attention_refresher.start()

    def _filter_universe_to_venue(self) -> None:
        """Universe comes from production data; drop symbols the trading
        venue (demo) cannot actually trade."""
        try:
            self.exchange.load_markets()
            tradable = set()
            for m in self.exchange.markets.values():
                if not m.get("active", True) or m.get("spot"):
                    continue
                base = (m.get("base") or "")
                quote = (m.get("quote") or "")
                if quote == "USDT":
                    tradable.add(f"{base}/USDT")
            before = self.universe.symbols()
            kept = [s for s in before if s in tradable]
            dropped = set(before) - set(kept)
            self.universe._alts = [s for s in self.universe._alts
                                   if s in kept]
            self.universe.majors = [s for s in self.universe.majors
                                    if s in kept]
            if dropped:
                log.warning(f"universe trimmed to venue: dropped {sorted(dropped)}")
        except Exception as e:
            log.warning(f"universe venue-filter failed: {e}")

    def _graceful(self, signum, _frame) -> None:
        self._attention_call("close")
        if getattr(self, "_attention_refresher", None) is not None:
            self._attention_refresher.close()
        log.warning(f"signal {signum} — shutting down")
        self._stop = True

    def _derivatives_recorder(self) -> None:
        """Record funding / open interest / taker ratio / long-short every N
        minutes.

        Runs from day one even though nothing reads it yet. The OI, taker and
        long-short endpoints are hard-limited to ~30 days of retention, so
        history not recorded now is permanently unavailable to any future
        backtest. A coarse backfill at start makes those ~30 days usable
        immediately instead of after two months of forward recording.
        """
        import time as _t
        dcfg = self.cfg.get("derivatives", {}) or {}
        if not dcfg.get("enabled", True):
            log.info("derivatives recorder disabled")
            return
        from .brain.harvester import Harvester
        harvester = Harvester(self.journal, self.cfg, self.feed, self.notifier)
        feed = harvester.deriv
        symbols = harvester.symbols or self.cfg["universe"]["majors"]
        interval = float(dcfg.get("record_interval_minutes", 15)) * 60
        delay = float(dcfg.get("request_delay_s", 0.3))
        _t.sleep(30)                        # let boot settle
        if dcfg.get("backfill_on_start", True):
            try:
                log.info(f"harvest backfill: {harvester.harvest_once(backfill=True)['rows']} rows")
            except Exception as e:
                log.warning(f"derivatives backfill failed: {e}")
        while not self._stop:
            try:
                counts = feed.record_all(symbols, delay=delay)
                if counts:
                    log.info(f"derivatives recorded: {counts}")
            except Exception as e:
                log.warning(f"derivatives recorder: {e}")
            # sleep in slices so shutdown is not delayed a full interval
            for _ in range(int(interval)):
                if self._stop:
                    return
                _t.sleep(1)

    def _reference_recorder(self) -> None:
        """Keep the reference markets current: S&P, DXY, gold, the 10y, VIX,
        oil, BTC dominance, the alt index, stablecoin supply, CoinGecko.

        A reference nobody records goes stale, and a stale reference reads
        NaN — so a ref() spec would go silent rather than wrong, which is
        the right failure, but still a failure. Each source is isolated
        inside refresh_all: Yahoo's API is unofficial and may break alone.
        """
        import time as _t
        rcfg = self.cfg.get("references", {}) or {}
        if not rcfg.get("enabled", True):
            log.info("reference recorder disabled")
            return
        from .data.ref_sources import refresh_all
        every = float(rcfg.get("interval_minutes", 60)) * 60
        members = list(rcfg.get("alts_members") or [])
        _t.sleep(60)                        # let boot settle
        while not self._stop:
            try:
                rep = refresh_all(self.feed, members)
                errs = {k: v for k, v in rep.items() if isinstance(v, str)}
                log.info(f"references refreshed: {len(rep) - len(errs)} ok"
                         + (f", errors {errs}" if errs else ""))
            except Exception as e:
                log.warning(f"reference recorder: {e}")
            for _ in range(int(every)):
                if self._stop:
                    return
                _t.sleep(1)

    def _strategy_mechanism_loop(self) -> None:
        """The mechanism: retire what has stopped working, generate more.

        No edge lasts, so this is the part that matters more than any single
        strategy. Every cycle: sweep deployed specs for decay, then ask the
        Strategist for a new one and let the Analyst decide whether it is
        working NOW and whether it adds anything the book lacks.
        """
        import time as _t
        mcfg = self.cfg.get("mechanism", {}) or {}
        if not mcfg.get("enabled", True):
            log.info("strategy mechanism disabled")
            return
        interval = float(mcfg.get("interval_minutes", 180)) * 60
        per_cycle = int(mcfg.get("specs_per_cycle", 1))
        max_book = int(mcfg.get("max_specs", 8))
        _t.sleep(300)                      # let boot and the first cycles settle
        while not self._stop:
            try:
                self._mechanism_once(per_cycle, max_book)
            except Exception as e:
                log.warning(f"strategy mechanism cycle failed: {e}")
            for _ in range(int(interval)):
                if self._stop:
                    return
                _t.sleep(1)

    def _research_loop(self) -> None:
        """The search: generate combinations, score them, keep what pays.

        This thread owns the schedule and every write; the arithmetic runs in
        a spawned child at nice 19 with a wall-clock limit, because the
        kernel's pandas work holds the GIL and a CPU-heavy search on a thread
        would slow the trade loop directly.
        """
        import time as _t
        rcfg = self.cfg.get("research", {}) or {}
        if not rcfg.get("enabled", False):
            log.info("research search disabled")
            return
        from .research.runner import ResearchRunner
        runner = None
        every = float(rcfg.get("interval_seconds", 60))
        _t.sleep(300)                      # let boot and the first cycles settle
        while not self._stop:
            try:
                # Constructed lazily, inside the loop: `ResearchRunner.__init__`
                # calls `Ledger.ensure()`, and building it once outside the
                # loop would let a single failure there (a locked database at
                # boot) kill this thread silently for the rest of the
                # process's life. Retrying here means a transient failure is
                # just another skipped cycle.
                if runner is None:
                    runner = ResearchRunner(self.journal, self.cfg)
                rep = runner.step(cycle_seconds=self._last_cycle_s)
                if rep.get("skipped") not in (None, "idle"):
                    log.debug(f"research: {rep}")
            except Exception as e:         # noqa: BLE001
                log.warning(f"research step failed: {e}")
            for _ in range(int(every)):
                if self._stop:
                    return
                _t.sleep(1)

    def _mechanism_once(self, per_cycle: int = 1, max_book: int = 8) -> dict:
        from .brain import ideas as idea_queue
        from .brain.analyst import Analyst
        from .brain.llm import BrainLLM
        from .brain.spec_writer import SpecWriter

        analyst = Analyst(self.journal, self.cfg, self.feed, self.notifier)
        rows = self.journal.list_specs(["paper", "active"])
        book = [spec for _r, spec in rows]

        # 1. retire what has stopped working
        retired = analyst.review_deployed(book)
        for a in retired:
            self.journal.query(
                "UPDATE strategies SET state='retired', retire_reason=? "
                "WHERE id=?", (a["evidence"]["verdict"][:200], a["spec"]))
        if retired:
            book = [s for s in book
                    if s.id not in {a["spec"] for a in retired}]

        # 2. generate replacements — from what the Researcher actually read
        added = []
        consumed = []
        # 2a. the search's candidates go first, through the SAME admit().
        # Closed unless research.handoff is on; and even then it reads only
        # candidates that passed the reasoning test (phase 4).
        if len(book) < max_book:
            spec = self._research_handoff(analyst, book)
            if spec is not None:
                book.append(spec)
                added.append(spec.name)
        if len(book) < max_book:
            writer = SpecWriter(BrainLLM(self.cfg))
            # what the Strategist is allowed to build on: the firm's operating
            # beliefs, plus the Harvester's brief on which numeric series are
            # deep enough to score. Without the brief the Strategist writes
            # mechanisms the Analyst must refuse for coverage — three of the
            # last four rejections were exactly that.
            knowledge = self._strategist_knowledge()
            for _ in range(per_cycle):
                # the handoff: harvester/crawler queue mechanisms, the
                # Strategist expresses one. A dry queue is not an error —
                # the model then invents unprompted, as it always did.
                idea = idea_queue.next_idea(self.journal, stream="strategy")
                iid = (idea or {}).get("idea_id", "")
                spec, trace = writer.write(
                    idea=idea, doctrine=knowledge.get("doctrine"),
                    data=knowledge.get("data", ""),
                    avoid=[s.name for s in book])
                if spec is None:
                    self.journal.log_brain_event("spec_write_failed", iid,
                                                 {**trace, "idea": iid})
                    if iid:
                        idea_queue.mark_consumed(self.journal, iid,
                                                 "write_failed", stream="strategy",
                                                 detail={"trace": trace})
                        consumed.append(iid)
                    break
                ok, ev = analyst.admit(spec, book)
                if iid:
                    idea_queue.mark_consumed(
                        self.journal, iid,
                        "admitted" if ok else "rejected", stream="strategy", spec_id=spec.id,
                        detail={"name": spec.name})
                    consumed.append(iid)
                if not ok:
                    self.journal.log_brain_event(
                        "spec_rejected", spec.id,
                        {"name": spec.name, "evidence": ev, "idea": iid})
                    continue
                self._install_spec(spec, ev, analyst, {"idea": iid})
                book.append(spec)
                added.append(spec.name)

        if retired or added:
            self.population = self._load_population()   # hot reload
        rep = {"retired": [a["name"] for a in retired], "added": added,
               "book": len(book), "ideas_used": consumed,
               "ideas_pending": len(idea_queue.pending(self.journal))}
        log.info(f"mechanism: {rep}")
        return rep

    def _install_spec(self, spec, ev: dict, analyst, extra: dict) -> None:
        """Everything that follows an Analyst admission, whoever proposed."""
        # regime_filter as written is a guess; measure it before the
        # orchestrator starts gating live signals on it
        spec.timeframe = ev.get("chosen_timeframe", spec.timeframe)
        rf = analyst.set_measured_regimes(spec)
        # TradingView second opinion — ADVISORY. It is recorded next
        # to the spec and never blocks admission; see
        # Analyst.confirm_on_tv for why a TV gate would be wrong.
        if (self.cfg.get("tv_harness", {}) or {}).get("enabled", False):
            try:
                ev["tv"] = analyst.confirm_on_tv(spec)
            except Exception as e:
                log.debug(f"tv confirmation skipped: {e}")
        self.journal.upsert_spec(spec, state="paper")
        self.journal.log_brain_event(
            "spec_admitted", spec.id,
            {"name": spec.name, "evidence": ev, "regimes": rf, **extra})
        if self.notifier:
            self.notifier.send(
                f"\U0001f9ec New strategy: {spec.name} "
                f"({spec.timeframe}, {'/'.join(spec.regime_filter)})\n"
                f"recent PF {ev['recent']['pooled_pf']} over "
                f"{ev['recent']['trades']} trades \u2192 paper")

    def _research_handoff(self, analyst, book):
        """One `reason_passed` search candidate through `analyst.admit`.

        The search never writes `strategies`; this is its only way in, and
        it is the same gate every other proposer faces. Returns the admitted
        spec, or None.
        """
        rcfg = self.cfg.get("research") or {}
        if not rcfg.get("handoff", False):
            return None
        try:
            from .research.ledger import Ledger
            from .research.runner import ResearchRunner
            from .research.universe import DISCOVERY, HELDOUT
            led = Ledger(self.journal)
            led.ensure()
            rows = led.candidates(state="reason_passed")
            if not rows:
                return None
            cand = rows[0]
            h, tf, geo = cand["hash"], cand["tf"], cand["geo"]
            combo_rows = self.journal.query(
                "SELECT * FROM research_combos WHERE hash=?", (h,))
            runner = ResearchRunner(self.journal, self.cfg,
                                    run=lambda *a, **k: None)
            c = runner._combo(combo_rows[0], tf, geo) if combo_rows else None
            if c is None:
                led.set_candidate(h, tf, geo, "refused",
                                  reason="cannot be rebuilt at handoff")
                return None
            spec = c.to_spec()
            # it was examined on discovery + held-out; that is what it has
            # evidence for, so that is the universe it declares
            spec.universe = {"include": list(DISCOVERY) + list(HELDOUT),
                             "exclude": []}
            spec.provenance = {**(spec.provenance or {}), "research_hash": h}
            ok, ev = analyst.admit(spec, book)
        except Exception as e:                          # noqa: BLE001
            log.warning(f"research handoff failed: {e}")
            return None
        if not ok:
            led.set_candidate(h, tf, geo, "refused",
                              reason=str(ev.get("reason", ""))[:400])
            self.journal.log_brain_event(
                "spec_rejected", spec.id,
                {"name": spec.name, "evidence": ev, "research_hash": h})
            return None
        self._install_spec(spec, ev, analyst, {"research_hash": h})
        led.set_candidate(h, tf, geo, "admitted", reason="analyst admitted")
        return spec

    def _strategist_knowledge(self) -> dict:
        """Doctrine + the numeric coverage brief, as one prompt payload."""
        out: dict = {}
        try:
            from .brain.doctrine import load_doctrine
            out["doctrine"] = load_doctrine()
        except Exception as e:
            log.debug(f"doctrine unavailable: {e}")
        try:
            from .brain.harvester import Harvester
            out["data"] = Harvester(self.journal, self.cfg,
                                    self.feed).brief_text()
        except Exception as e:
            log.debug(f"data brief unavailable: {e}")
        return out

    def _harvest_loop(self) -> None:
        """Continuous strategy discovery from the internet."""
        import time as _t
        interval = float(self.cfg["scraper"].get("interval_minutes", 240)) * 60
        _t.sleep(180)                       # let boot settle
        while not self._stop:
            try:
                from .brain.scraper import Scraper
                h = Scraper(self.journal, self.cfg, self.feed,
                              self.notifier)
                stats = h.harvest_once()
            except Exception as e:
                log.warning(f"harvest cycle failed: {e}")
            _t.sleep(interval)

    def _crawl_loop(self) -> None:
        """Deep-reading crawler: walks finance knowledge sites, mines
        tradeable rules from full-text material."""
        import time as _t
        c = self.cfg.get("crawler", {})
        interval = float(c.get("interval_minutes", 360)) * 60
        _t.sleep(600)                    # let boot + first harvest settle
        while not self._stop:
            try:
                from .brain.crawler import DeepCrawler
                DeepCrawler(self.journal, self.cfg, self.feed,
                            self.notifier).crawl_once()
            except Exception as e:
                log.warning(f"crawl cycle failed: {e}")
            _t.sleep(interval)

    def _rent_loop(self) -> None:
        """Luffy pays rent: the week's net off the venue ledger, hourly.
        Reports only. The verdict is Sarmad's to act on."""
        from .engine.rent_keeper import RentKeeper
        keeper = RentKeeper(self.exchange, self.journal, self.notifier, self.cfg)
        every = float(self.cfg.get("rent", {}).get("check_minutes", 60)) * 60
        while not self._stop:
            try:
                keeper.tick()
            except Exception as e:
                log.warning(f"rent check failed: {e}")
            time.sleep(every)

    def _maybe_validate_agents(self) -> None:
        """Re-run analyst validation weekly (or at boot if stale/missing)."""
        import json as _json
        from .agents.validate import run as validate_run
        from .agents.structure import StructureAnalyst
        from .agents.flow import FlowAnalyst
        from .agents.momentum import (MomentumAnalyst, ValueAnalyst,
                                      RotationAnalyst)
        from .core.config import ROOT
        wpath = ROOT / "data" / "agent_weights.json"
        age_days = 999.0
        if wpath.exists():
            age_days = (time.time() - wpath.stat().st_mtime) / 86400
        if age_days < 7:
            log.info(f"agent weights fresh ({age_days:.1f}d) — skip validation")
            return
        try:
            time.sleep(90)                     # let boot settle
            analysts = {"structure": StructureAnalyst(),
                        "flow": FlowAnalyst(),
                        "momentum": MomentumAnalyst(),
                        "value": ValueAnalyst(),
                        "rotation": RotationAnalyst()}
            rep = validate_run(analysts, self.universe.symbols()[:8],
                               self.feed, days=20)
            self.journal.log_brain_event(
                "agent_validation", "theorist",
                {a: {"acc": i["overall_acc_1h"], "n": i["samples"]}
                 for a, i in rep["agents"].items()})
            try:
                from .knowledge.vault import Vault
                Vault(self.journal).agent_ledger()
            except Exception:
                pass
            log.info(f"agent validation complete: "
                     f"{ {a: round(i['overall_acc_1h'],3) for a,i in rep['agents'].items()} }")
            self.notifier.send("🔬 Analyst validation refreshed — weights "
                               "updated from measured accuracy.")
        except Exception as e:
            log.warning(f"agent validation failed: {e}")

    # ── per-symbol pipeline ───────────────────────────────────────────────
    def _snapshot_for(self, symbol: str,
                      universe: dict | None = None) -> Snapshot | None:
        dfs = self.feed.fetch_multi(
            symbol, self.cfg["timeframes"]["context"] + [self.cfg["timeframes"]["execution"]])
        exec_tf = self.cfg["timeframes"]["execution"]
        if exec_tf not in dfs:
            return None
        btc = self.feed.fetch_ohlcv("BTC/USDT", "1h")
        if btc is not None:
            dfs["BTC_1h"] = btc
        price = float(dfs[exec_tf]["close"].iloc[-1])
        return Snapshot(symbol=symbol,
                        ts=dt.datetime.now(dt.timezone.utc).isoformat(),
                        price=price, dfs=dfs,
                        market_type=self.market_type.value,
                        btc_ctx=self._btc_ctx,
                        universe=universe,
                        derivs=self._derivs_for(symbol),
                        market=self._market_for())

    def _refresh_btc_context(self) -> None:
        """Leader context computed once per cycle, shared by all scouts."""
        b15 = self.feed.fetch_ohlcv("BTC/USDT", "15m")
        b1h = self.feed.fetch_ohlcv("BTC/USDT", "1h")
        self._btc_ctx = btc_context(b15, b1h)

    def _order_book(self, symbol: str) -> dict | None:
        hit = self._book_cache.get(symbol)
        now = time.time()
        if hit and now - hit[0] < 45:
            return hit[1]
        try:
            ob = self.exchange.fetch_order_book(symbol, limit=20)
            self._book_cache[symbol] = (now, ob)
            return ob
        except Exception:
            return hit[1] if hit else None

    def _funding_map(self) -> dict[str, float]:
        if self._funding_cache is not None:
            return self._funding_cache
        out: dict[str, float] = {}
        if self.market_type == MarketType.FUTURES:
            for sym in self.universe.symbols():
                try:
                    fr = self.exchange.fetch_funding_rate(sym)
                    out[sym] = float(fr.get("fundingRate") or 0)
                except Exception:
                    continue
        self._funding_cache = out
        return out

    def _oi_map(self, ttl: float = 900.0) -> dict[str, dict]:
        """Open interest now + 24h change per symbol (TTL-cached).

        OI history lives on production fapiData — the demo venue has no
        route for it, so a public no-key instance serves the reads.
        """
        now = time.time()
        if now - self._oi_cache[0] < ttl:
            return self._oi_cache[1]
        out: dict[str, dict] = {}
        try:
            if getattr(self, "_oi_ex", None) is None:
                self._oi_ex = make_exchange("futures", demo=False,
                                            with_keys=False)
            for sym in self.universe.symbols():
                try:
                    hist = self._oi_ex.fetch_open_interest_history(
                        sym, timeframe="1h", limit=25) or []
                    points = [float(p.get("openInterestAmount") or
                                    p.get("openInterestValue") or 0)
                              for p in hist if isinstance(p, dict)]
                    points = [p for p in points if p > 0]
                    if len(points) >= 6:
                        chg = (points[-1] - points[0]) / points[0]
                        out[sym] = {"now": points[-1], "chg_24h": round(chg, 4)}
                except Exception:
                    continue
        except Exception as e:
            log.warning(f"OI map failed: {e}")
        self._oi_cache = (now, out)
        return out

    # ── main loop ─────────────────────────────────────────────────────────
    def cycle(self) -> dict:
        stats = {"scanned": 0, "decisions": 0, "entries": 0,
                 "skips": 0, "exits_detected": 0}
        self.state_machine.refresh()
        balance = self._fetch_balance()
        status = self.risk.update_equity(balance)
        if status.get("halt_breached"):
            self.state_machine.set(ControlState.HALTED, "risk_engine",
                                   f"drawdown {status['drawdown_pct']}%")

        stats["manual_closed"] = self._drain_close_requests()

        panic_requested = self.journal.kv_get("panic_requested") == "1"
        if panic_requested:
            n = flatten_all(self.exchange, self.journal, self.notifier)
            self.journal.kv_set("panic_requested", "0")
            self.state_machine.set(ControlState.FROZEN, "operator",
                                   f"panic flattened {n}")
            stats["panic_closed"] = n

        # MacroGuard: hard-freeze during scheduled high-impact US events.
        # Only auto-resumes if MacroGuard owns the current freeze, not operator.
        macro = self.macro_guard.check()
        macro_owns_freeze = self.journal.kv_get("macro_guard_froze", "0") == "1"
        operator_hold = self.journal.kv_get(
            "macro_guard_operator_hold", "0") == "1"
        _cur = self.state_machine.refresh()
        if macro.get("active") and _cur == ControlState.ACTIVE:
            self.state_machine.set(ControlState.FROZEN, "macro_guard",
                                   macro.get("event", "macro event"))
            self.journal.kv_set("macro_guard_froze", "1")
            self.notifier.send(
                f"🔒 MacroGuard FREEZE: {macro.get('event', 'macro event')} "
                f"until {macro.get('until', '?')}")
        elif (not macro.get("active") and macro_owns_freeze
              and not operator_hold and _cur == ControlState.FROZEN):
            self.state_machine.set(ControlState.ACTIVE, "macro_guard",
                                   "macro event cleared")
            self.journal.kv_set("macro_guard_froze", "0")
            self.notifier.send("✅ MacroGuard: event cleared — resuming ACTIVE")
        self.journal.kv_set("macro_guard_state", json.dumps({
            "active": bool(macro.get("active")),
            "event": macro.get("event", ""),
            "until": macro.get("until"),
            "ts": dt.datetime.now(dt.timezone.utc).isoformat()}))

        state = self.state_machine.refresh()
        supervisor = getattr(self, "supervisor", None)
        if (supervisor is not None and self.market_type == MarketType.FUTURES
                and state == ControlState.ACTIVE and self.executor.recovery_pending()):
            supervisor.trigger("entry_recovery_pending")
            state = self.state_machine.refresh()
        if self.market_type == MarketType.FUTURES and state != ControlState.HALTED:
            self.executor.recover_entries()
        entry_allowed = state == ControlState.ACTIVE
        blocked = "" if entry_allowed else f"state={state.value}"
        if self.market_type == MarketType.FUTURES and self.executor.recovery_pending():
            entry_allowed = False
            blocked = "execution_recovery_pending"
        if entry_allowed and status.get("daily_pnl_pct", 0) <= \
                -self.risk.daily_loss_block * 100:
            entry_allowed = False
            blocked = f"daily breaker {status['daily_pnl_pct']:.1f}%"

        funding = self._funding_map() if self.market_type == MarketType.FUTURES else {}
        oi = self._oi_map() if self.market_type == MarketType.FUTURES else {}
        self._refresh_btc_context()
        news = self.news_guard.check()
        self.journal.kv_set("news_guard_state", json.dumps(
            {"active": bool(news.get("active")), "why": news.get("why", ""),
             "ts": dt.datetime.now(dt.timezone.utc).isoformat()}))
        if news.get("active"):
            log.info(f"news guard: {news['why']} — thresholds raised")
        closed_count = int(self.journal.query(
            "SELECT COUNT(*) AS n FROM trades WHERE status='closed'")[0]["n"])

        exec_tf = self.cfg["timeframes"]["execution"]
        # What to scan is a question the strategies answer. The plan can only
        # narrow the venue candidates (a spec's exclude or liquidity floor) or
        # add a symbol a spec explicitly named, so the scan tracks the book
        # instead of a list hardcoded beside it.
        scan_symbols = self._scan_symbols()
        universe_frames = self._universe_frames(scan_symbols)
        supplemental, provenance = self._attention_slot(scan_symbols, stats)
        if supplemental is None:
            scan_id = self._attention_call("begin", universe_frames, scan_symbols)
        else:
            stats["attention_supplemental"] = {"symbol": supplemental.symbol,
                                               "status": supplemental.status,
                                               "reason": supplemental.reason}
            if provenance is None:
                scan_id = self._attention_call("begin", universe_frames, scan_symbols,
                                               None, supplemental)
            else:
                stats["attention_supplemental"]["provenance"] = provenance
                scan_id = self._attention_call("begin", universe_frames, scan_symbols,
                                               None, supplemental, provenance)
        attention_causes = []
        # Bound producer work even if the trading universe is much larger.
        attention_cap = getattr(getattr(self, "_attention", None), "cfg", {}).get("max_symbols", 0)

        scanned: set = set()
        for symbol in scan_symbols:
            if (entry_allowed and self.market_type == MarketType.FUTURES
                    and self.executor.recovery_pending()):
                entry_allowed = False
                blocked = "execution_recovery_pending"
            snap = self._snapshot_for(symbol, universe=universe_frames)
            if snap is None:
                if scan_id and len(attention_causes) < attention_cap:
                    attention_causes.append({"symbol": symbol, "decision_id": None,
                                             "reason": "missing_snapshot"})
                continue
            stats["scanned"] += 1
            self.positioning_agent.set_context(symbol, funding.get(symbol),
                                               oi.get(symbol))
            self.depth_agent.set_context(symbol, self._order_book(symbol))
            d = self.orchestrator.decide(snap, self.population,
                                         entry_allowed=entry_allowed,
                                         blocked_reason=blocked)
            d.scan_id = scan_id
            self.orchestrator.journalize(snap, d, self.market_type.value,
                                         mode="live")
            stats["decisions"] += 1

            if d.action != Action.HOLD:
                if d.skip_reason:
                    stats["skips"] += 1
                    log.info(f"SKIP {symbol} {d.action} score={d.score:+.3f} "
                             f"| {d.skip_reason}")
                elif entry_allowed:
                    try:
                        ok = self._try_enter(d, snap, balance, closed_count)
                    except RiskError as e:
                        self.state_machine.set(ControlState.HALTED,
                                               "risk_engine", str(e))
                        d.skip_reason = f"risk halt: {e}"
                        ok = False
                        entry_allowed = False
                        blocked = "state=HALTED"
                        log.error(f"RISK HALT {d.symbol}: {e}")
                    self.journal.update_decision_outcome(
                        d.id, d.executed, d.size_usdt, d.skip_reason)
                    if ok:
                        stats["entries"] += 1
                        self.notifier.send(
                            f"🎯 <b>{d.action}</b> {symbol} @ {snap.price:.4g} "
                            f"score {d.score:+.2f} conf {d.confidence:.0%}")

            if scan_id and len(attention_causes) < attention_cap:
                attention_causes.append({"symbol": symbol, "decision_id": d.id,
                    "cycle_id": d.cycle_id, "action": d.action.value,
                    "executed": d.executed, "entry_allowed": entry_allowed,
                    "blocked": bool(d.skip_reason),
                    "reason": "decision_recorded", "decision_detail": "see decisions.skip_reason",
                    "evaluations": d.evaluation_causes, "omitted_causes": d.omitted_causes})
            stats["exits_detected"] += self._detect_exchange_exits(symbol)
            scanned.add(symbol)
            for t in self.journal.open_trades():
                if t["symbol"] != symbol:
                    continue
                score = d.score if d.symbol == symbol else None
                if self._manages_exits():
                    r = self._manage_one(t, snap, score)
                    if r:
                        stats["exit_action"] = r

        # Positions whose symbol has left the universe are still positions.
        # Both calls above live inside the scan loop, so before this pass a
        # rotated-out symbol got no trail, no time exit and no fill detection.
        stats["orphans_managed"] = self._manage_orphan_positions(scanned)

        self._maybe_resolve_outcomes()
        self._record_excursions()
        if supervisor is not None and self.market_type == MarketType.FUTURES:
            supervisor.cycle()
            state = self.state_machine.refresh()
        self._attention_call("causes", scan_id, attention_causes)
        attention_health = self._attention_call("health")
        attention_error = getattr(self, "_attention_error", None)
        if attention_health or attention_error:
            stats["attention"] = attention_health or {"enabled": True, "status": "error"}
            stats["attention"]["kernel_error"] = attention_error
        self.heartbeat.beat({"equity": round(balance, 2),
                             "state": state.value, **stats})
        self.journal.log_equity(status["equity"], balance,
                                len(self.journal.open_trades()))
        return {**stats, "equity": status["equity"],
                "dd_pct": status["drawdown_pct"]}

    def _top_strategy(self, d) -> str:
        """The strategy this trade belongs to: highest confidence AMONG the
        signals that actually proposed the direction being taken.

        `max(confidence)` over every signal could hand the trade — and its
        P&L, and therefore its decay and promotion arithmetic — to a strategy
        that signalled the other way.
        """
        agree = [s for s in (d.strategy_signals or [])
                 if s.get("action") == d.action.value]
        if not agree:
            return ""
        return max(agree, key=lambda s: s.get("confidence", 0)).get(
            "strategy_id", "")

    def _try_enter(self, d, snap, equity, closed_count) -> bool:
        from .agents.indicators import atr as _atr
        exec_tf = self.cfg["timeframes"]["execution"]
        side = "long" if d.action == Action.BUY else "short"
        top_strategy = self._top_strategy(d)

        # The stop must be measured on the frame the spec was validated on.
        # A 2.0x ATR stop means nothing until you say which bar's ATR: 4h ATR
        # runs 5-8x the 15m figure, so reading every ATR off the execution
        # frame put a 4h trend spec behind a 0.40% stop where it was validated
        # at 2.12% (BTC, 2026-09-02), sized to match, and stopped out by noise
        # before its mechanism could resolve.
        se = self._spec_exits.get(top_strategy)
        atr_tf = se.timeframe if se is not None else exec_tf
        a = _atr(snap.df(atr_tf)) if snap.df(atr_tf) is not None else 0.0
        if a <= 0:                       # spec frame absent — do not guess
            if se is not None:
                d.skip_reason = f"no {atr_tf} bars to size the stop on"
                log.info(f"ENTRY DENY {d.symbol}: {d.skip_reason}")
                return False
            a = _atr(snap.df(exec_tf))
        sl, tp = self._protection_for(se, snap.price, a, side)
        stop_frac = abs(snap.price - sl) / snap.price
        sizing = self.risk.check_entry(
            self.state_machine.state, d.symbol, snap.price, a, stop_frac,
            open_positions=[self._as_position(t) for t in
                            self.journal.open_trades()],
            equity=equity, closed_trades_count=closed_count,
            market_type=self.market_type.value)
        if not sizing.ok:
            d.skip_reason = f"risk: {sizing.reason}"
            log.info(f"RISK DENY {d.symbol}: {sizing.reason}")
            return False
        # meta-label sizing: shrink-only multiplier from P(win) — bounded
        # [0.25, 1.0]; skipped outright if it would drop below min notional
        if getattr(d, "meta_size", 1.0) < 1.0:
            m = float(d.meta_size)
            sizing.amount *= m
            sizing.size_usdt *= m
            sizing.risk_usdt = (sizing.risk_usdt or 0.0) * m
            if sizing.amount * snap.price < float(
                    self.cfg["risk"].get("min_notional_usdt", 10)):
                d.skip_reason = "risk: meta-sized below min notional"
                log.info(f"META SIZE DENY {d.symbol}: {m:.2f}× too small")
                return False
            log.info(f"meta size {d.symbol}: {m:.2f}× "
                     f"(p={getattr(d, 'meta_p', 0):.2f})")
        pos = self.executor.open(
            d, sizing.amount, a, sl, tp,
            strategy_id=top_strategy or "orchestrator",
            strategy_name=top_strategy and next(
                (s.get("strategy_name", "") for s in d.strategy_signals
                 if s.get("strategy_id") == top_strategy), "consensus"),
            exec_mode="live")
        return pos is not None

    def _protection_for(self, se, price: float, atr: float,
                        side: str) -> tuple[float, float]:
        """Stop and target for one entry, from the spec that proposed it.

        Falls back to the config geometry whenever no spec owns the trade —
        which is every legacy genome, tuned on the execution frame.
        """
        if se is None:
            return self.risk.protection_levels(
                price, atr, side, self.executor.tp_atr_mult)
        if se.stop_atr_mult > 0:
            dist = se.stop_atr_mult * atr
        elif se.stop_pct > 0:
            dist = se.stop_pct * price
        else:
            return self.risk.protection_levels(
                price, atr, side, self.executor.tp_atr_mult)
        dist = max(dist, price * 0.004)        # venue noise floor
        # A spec with no target rides the trail to the end. 0.0 is this
        # schema's "no target" — inventing a far-away price would read as a
        # real level the strategy never chose.
        if not se.has_target:
            return (price - dist, 0.0) if side == "long" \
                else (price + dist, 0.0)
        tp_dist = self.executor.tp_atr_mult * atr
        if side == "long":
            return price - dist, price + tp_dist
        return price + dist, price - tp_dist

    @staticmethod
    def _as_position(t: dict):
        from .core.types import Position, Side
        return Position(id=t["id"], symbol=t["symbol"],
                        side=Side(t["side"]), amount=float(t["amount"]),
                        entry_price=float(t["entry_price"]),
                        notional_usdt=float(t["notional_usdt"] or 0),
                        leverage=int(t.get("leverage") or 1),
                        stop_loss=float(t.get("stop_loss") or 0))

    def _universe_frames(self, symbols: list[str]) -> dict:
        """{symbol: {tf: df}} for cross-sectional features.

        Built at the execution timeframe ONLY, whatever the strategies
        traded. FeatureCtx.for_symbol requires the spec's own timeframe in a
        peer's frames and returns None otherwise, so for a 4h spec every peer
        resolved to None, xs_rank produced an all-NaN column, and the spec
        silently never fired — reading as "no edge" when it was never
        evaluated. The same shape as funding specs scoring zero because no
        derivatives reached them.
        """
        exec_tf = self.cfg["timeframes"]["execution"]
        tfs = list(dict.fromkeys(
            [exec_tf, *getattr(self, "_scan_timeframes", ())]))
        out: dict = {}
        for sym in symbols:
            frames = {}
            for tf in tfs:
                try:
                    df = self.feed.fetch_ohlcv(sym, tf)
                except Exception as e:
                    log.warning(f"universe frame {sym} {tf}: {e}")
                    continue
                if df is not None and len(df):
                    frames[tf] = df
            if exec_tf in frames:
                out[sym] = frames
        return out

    def _scan_symbols(self) -> list[str]:
        """Venue candidates, filtered and extended by what the book asks for.

        Falls back to the plain universe whenever no spec expresses a
        preference, so a book of legacy genomes behaves exactly as before.
        """
        base = self.universe.symbols()
        specs = [sp for _row, sp in getattr(self, "_spec_rows", [])]
        if not specs:
            return base
        try:
            from .strategy.scan_plan import plan_scan
            plan = plan_scan(specs, base, self.universe.volumes())
        except Exception as e:
            log.warning(f"scan plan failed, using plain universe: {e}")
            return base
        self._scan_plan = plan
        self._scan_timeframes = plan.timeframes
        merged = list(dict.fromkeys(list(plan.symbols) + self.universe.majors))
        return merged or base

    def _manage_one(self, t: dict, snap, score) -> str | None:
        """Run the exit engine over one open trade. Returns an exit reason."""
        from .agents.indicators import atr as _atr
        exec_tf = self.cfg["timeframes"]["execution"]
        # trail on the spec's own frame, for the same reason the stop is set
        # there: a 4.0 ATR trail on 15m bars is ~11x tighter than on 4h.
        tf = self.exits.atr_timeframe(t) or exec_tf
        df = snap.df(tf)
        if df is None and tf != exec_tf:
            log.warning(f"exit manage {t['symbol']}: no {tf} bars, "
                        f"holding the stop where it is")
            return None
        a = _atr(df) if df is not None else 0
        if a <= 0:
            return None
        try:
            reason = self.exits.manage(t, snap.price, a, score)
        except Exception as e:
            log.warning(f"exit manage {t['symbol']}: {e}")
            return None
        if reason:
            self.notifier.send(
                f"↪ {t['symbol']} exit: {reason} @ {snap.price:.4g}")
        return reason

    def _record_excursions(self) -> int:
        """Book intrabar MFE/MAE for trades that have just closed.

        Pure observation, appended after the cycle's decisions are already
        made: it reads the local candle store and writes two columns plus
        provenance. Nothing here can move a stop, a size or a state, and any
        failure is swallowed so accounting can never disturb trading.
        """
        try:
            from .engine import excursion

            def bars_for(symbol: str, tf: str):
                df = self.feed.cached_ohlcv(symbol, tf)
                if df is None or not len(df):
                    return []
                ts = df["ts"]
                ms = (ts.astype("int64") // 10 ** 6
                      if str(ts.dtype).startswith("datetime64") else ts)
                return list(zip(ms.tolist(), df["high"].tolist(),
                                df["low"].tolist()))

            specs = {sp.id: sp.timeframe
                     for _row, sp in getattr(self, "_spec_rows", [])}
            return excursion.sweep(self.journal, bars_for, specs.get)
        except Exception as e:
            log.warning(f"excursion sweep: {e}")
            return 0

    def _manages_exits(self) -> bool:
        sm = getattr(self, "state_machine", None)
        return sm.manages_exits() if sm is not None else True

    def _manage_orphan_positions(self, scanned: set) -> int:
        """Manage open positions whose symbol was not scanned this cycle.

        The universe rotates its alts every few hours; a position outlives
        that rotation. Donchian Breakout Trail holds for up to 83 days, so a
        stranded position is the expected case rather than an edge case, and
        a stranded position is one with no trailing stop and no way to notice
        its exchange stop already fired.

        One unavailable symbol must not strand the rest, so each is isolated.
        """
        n = 0
        for t in self.journal.open_trades():
            sym = t["symbol"]
            if sym in scanned:
                continue
            scanned.add(sym)          # one snapshot per symbol per cycle
            try:
                snap = self._snapshot_for(sym)
                if snap is None:
                    log.warning(f"orphan position {sym}: no snapshot — "
                                f"cannot manage this cycle")
                    continue
                self._detect_exchange_exits(sym)
                if self._manages_exits():
                    self._manage_one(t, snap, None)
                n += 1
            except Exception as e:
                log.warning(f"orphan position {sym}: {e}")
        return n

    def _drain_close_requests(self) -> int:
        """Market-close whatever the operator asked for from the dashboard.

        Runs at the top of the cycle, before the scan loop, so the button
        reaches a position whose symbol has rotated out of the universe —
        exit management used to sit inside `for symbol in scan_symbols` and
        stranded exactly those. A close the venue refuses is reported, never
        requeued: an id retrying forever against a standing rejection is
        worse than one loud error the operator can act on.
        """
        raw = self.journal.kv_get("close_requests", "[]")
        if raw in (None, "", "[]"):
            return 0
        self.journal.kv_set("close_requests", "[]")     # drained on read
        try:
            ids = json.loads(raw)
            if not isinstance(ids, list):
                raise ValueError(raw)
        except Exception:
            log.warning(f"close_requests unreadable ({raw!r}) — discarded")
            return 0

        open_by_id = {t["id"]: t for t in self.journal.open_trades()}
        n = 0
        for tid in ids:
            t = open_by_id.get(tid)
            if t is None:
                log.info(f"manual close {tid}: already closed — skipped")
                continue
            sym = t["symbol"]
            px = self.feed.price(sym) or float(t["entry_price"])
            if self.executor.close(t, px, reason="manual"):
                n += 1
                log.info(f"MANUAL CLOSE {sym} ({tid}) @~{px}")
                self.notifier.send(f"✋ {sym} closed manually @~{px:.6g}")
            else:
                log.error(f"MANUAL CLOSE FAILED {sym} ({tid}) — not retried")
                self.notifier.send(
                    f"⚠️ {sym} manual close REJECTED by the venue — "
                    f"still open, press again or use /panic")
        return n

    def _detect_exchange_exits(self, symbol: str) -> int:
        """Reconcile one symbol's journalled size against the venue's.

        The venue closes positions without telling us — a native stop or TP
        fires between cycles. This used to ask only whether the symbol had
        vanished from fetch_positions(), which reads presence, not size. On
        2026-09-02 UNI's trailing stop sold 303 of 304 coins; the one left
        behind kept the symbol present, so the journal carried a 303.87-coin,
        $1,791 position that no longer existed for the rest of the session —
        $89 unattributed, a trade slot and the heat budget spent on $6.

        So: gone → closed. Materially smaller → book the part that filled and
        align the rest. The tolerance matches reconcile's, wide enough that
        lot-step rounding is never mistaken for an exit.
        """
        try:
            ex_amt = {norm_symbol(p["symbol"]): float(p.get("contracts") or 0)
                      for p in self.exchange.fetch_positions()
                      if float(p.get("contracts") or 0) > 0}
        except Exception:
            return 0
        n = 0
        for t in self.journal.open_trades():
            if t["symbol"] != symbol or t["market_type"] != "futures":
                continue
            held = ex_amt.get(t["symbol"], 0.0)
            amount = float(t["amount"])
            if held >= amount - max(held * 0.01, 1e-9):
                continue                      # the venue still has it all
            px = self.feed.price(symbol) or float(t["entry_price"])
            entry = float(t["entry_price"])
            sl, tp = float(t.get("stop_loss") or 0), float(t.get("take_profit") or 0)
            direction = 1.0 if t["side"] == "long" else -1.0
            sold = amount - held
            gross = (px - entry) * direction * sold
            fees = self.executor.taker_fee * (sold * entry + sold * px)
            pnl = gross - fees
            reason = "tp_fill" if tp and abs(px - tp) <= abs(px - sl) else "sl_fill"
            if held <= 0:
                self.journal.close_trade(t["id"], px, round(pnl, 8), reason)
                log.info(f"EXCHANGE EXIT {symbol}: {reason} @{px} pnl={pnl:+.2f}")
            else:
                # part of the line filled; what remains is a real position
                self.journal.align_trade_amount(
                    t["id"], held, held * entry, pnl_delta=pnl)
                log.info(f"EXCHANGE PARTIAL {symbol}: {reason} @{px} "
                         f"-{sold:g} pnl={pnl:+.2f} remaining={held:g}")
            self.notifier.send(
                f"{'✅' if pnl > 0 else '🛑'} {symbol} "
                f"{'closed' if held <= 0 else f'reduced to {held:g}'} "
                f"({reason}) pnl {pnl:+.2f} USDT")
            n += 1
        return n

    _outcome_tick = 0

    def _maybe_resolve_outcomes(self):
        Kernel._outcome_tick += 1
        if Kernel._outcome_tick % 12 == 1:      # ~every 12 cycles
            try:
                resolve_pending(self.journal, self.feed)
            except Exception as e:
                log.warning(f"outcome resolution failed: {e}")
        if Kernel._outcome_tick % 120 == 7:     # ~every 2h: the 24h horizon
            # An outcome is written as soon as 4h can be graded, and is never
            # revisited, so correct_24h was written NULL and stayed NULL —
            # 191 resolved rows carried 54 24h grades. This pass fills them in
            # once the candles exist.
            try:
                from .engine.outcome_backfill import upgrade_24h
                syms = {r["symbol"] for r in self.journal.query(
                    "SELECT DISTINCT symbol FROM outcomes "
                    "WHERE correct_24h IS NULL")}
                frames = {}
                for sy in syms:
                    df = self.feed.fetch_ohlcv(sy, "1h", limit=400)
                    if df is not None and len(df):
                        frames[sy] = df
                if frames:
                    upgrade_24h(self.journal, frames)
            except Exception as e:
                log.warning(f"24h outcome upgrade failed: {e}")
        if Kernel._outcome_tick % 360 == 5:     # ~every 6h: book post-mortem
            try:
                from .brain.postmortem import run_postmortem
                from .knowledge.vault import Vault
                rep = run_postmortem(self.journal, Vault(self.journal))
                if rep.get("changed"):
                    log.info(f"postmortem: verdicts changed {rep['changed']}")
            except Exception as e:
                log.warning(f"postmortem failed: {e}")
        if Kernel._outcome_tick % 60 == 1:      # ~hourly brain/vault tick
            try:
                maybe_refit_calibration(
                    self.journal,
                    min_samples=int((self.cfg.get("scouts", {})
                                     .get("calibration", {})
                                     .get("min_samples", 60))),
                    refit_hours=float((self.cfg.get("scouts", {})
                                       .get("calibration", {})
                                       .get("refit_hours", 6))))
            except Exception as e:
                log.warning(f"calibration tick failed: {e}")
            try:
                from .agents import weights_online
                from .brain import meta_label
                ewa_cfg = self.cfg.get("scouts", {}).get("ewa", {})
                weights_online.maybe_update(
                    self.journal,
                    eta=float(ewa_cfg.get("eta", 0.30)))
                meta_label.maybe_refit(self.journal)
            except Exception as e:
                log.warning(f"ewa/meta tick failed: {e}")
            try:
                # Lifecycle rules for the legacy genomes. The LLM strategist
                # review that used to follow was removed on 2026-09-11: its
                # "mutate" verdict, Proposer and invention pass were three
                # creation paths that bypassed the Analyst, and one of them
                # resurrected a retired genome into paper and traded it.
                from .strategy.promotion import evaluate_population
                evaluate_population(self.journal, self.notifier)
            except Exception as e:
                log.warning(f"brain tick failed: {e}")
            try:
                # hot reload: promote/demote/mutate/harvest writes take
                # effect within one brain tick — no restart required
                fresh = self._load_population()
                sig = [(st.id, st.state, st.params) for st, _g in fresh]
                if sig != self._pop_sig:
                    self.population = fresh
                    self._pop_sig = sig
                    self.exits.genomes = {st.id: st.params
                                          for st, _g in fresh}
                    self.orchestrator.reload_weights()
                    elig = sum(1 for s in sig
                               if s[1] in ("paper", "active"))
                    log.info(f"population hot-reloaded: {len(fresh)} "
                             f"strategies ({elig} trade-eligible)")
            except Exception as e:
                log.warning(f"population hot-reload failed: {e}")
            try:
                from .knowledge.vault import Vault
                v = Vault(self.journal)
                v.refresh_strategy_notes()
                v.daily_review()
                v.agent_ledger()
                v.write_moc()
            except Exception as e:
                log.warning(f"vault refresh failed: {e}")

    # ── infra ────────────────────────────────────────────────────────────
    def account_snapshot(self) -> dict:
        """{margin_equity, assets_total, assets:{}} — display truth vs risk truth."""
        try:
            import hashlib, hmac as _hmac
            import requests
            from .core.config import Env
            key, secret = Env.binance_keys()
            q = f"timestamp={int(time.time()*1000)}&recvWindow=10000"
            sig = _hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()
            base = self.exchange.urls.get("api", {}).get(
                "fapiPrivate", "").rsplit("/", 1)[0]
            r = requests.get(f"{base}/v3/account",
                             params=q + f"&signature={sig}",
                             headers={"X-MBX-APIKEY": key}, timeout=10)
            a = r.json()
            assets = {x["asset"]: float(x["walletBalance"])
                      for x in a.get("assets", [])
                      if abs(float(x.get("walletBalance") or 0)) > 1e-9}
            px_btc = self.feed.price("BTC/USDT") or 0
            usd = {k: (v if k in ("USDT", "USDC", "BUSD") else
                       v * px_btc if k == "BTC" else None) for k, v in assets.items()}
            return {"margin_equity": float(a.get("totalMarginBalance") or 0),
                    "assets_total": round(sum(v for v in usd.values() if v), 2),
                    "assets": {k: round(v, 4) if v else f"{assets[k]:.6f}"
                               for k, v in usd.items()}}
        except Exception as e:
            log.debug(f"account snapshot failed: {e}")
            return {}

    def _fetch_balance(self) -> float:
        """Collateral margin equity — the number risk sizing is allowed to use."""
        try:
            import hashlib, hmac as _hmac
            import requests
            from .core.config import Env
            key, secret = Env.binance_keys()
            q = f"timestamp={int(time.time()*1000)}&recvWindow=10000"
            sig = _hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()
            base = self.exchange.urls.get("api", {}).get("fapiPrivate",
                   "https://demo-fapi.binance.com/fapi/v1").rsplit("/", 1)[0]
            r = requests.get(
                f"{base}/fapi/v3/account" if "/v1" in base
                else f"{base}/v3/account",
                params=q + f"&signature={sig}",
                headers={"X-MBX-APIKEY": key}, timeout=10)
            tmb = float(r.json().get("totalMarginBalance") or 0)
            if tmb > 0:
                return tmb
        except Exception as e:
            log.debug(f"v3 account fetch failed ({e}); falling back")
        try:
            total = 0.0
            # demo-fapi.binance.com intermittently answers 5xx
            # ("upstream request failed") for seconds at a time — retry
            # once before falling back to the last known equity
            for attempt in range(2):
                try:
                    total = float(self.exchange.fetch_balance()
                                  .get("USDT", {}).get("total") or 0)
                    if total > 0:
                        break
                except Exception as inner:
                    if attempt == 1:
                        raise
                    log.debug(f"balance retry after: {inner}")
                    time.sleep(3)
            return total if total > 0 else self._last_equity_fallback()
        except Exception as e:
            log.warning(f"balance fetch failed: {e}")
            return self._last_equity_fallback()

    def _last_equity_fallback(self) -> float:
        rows = self.journal.query(
            "SELECT equity FROM equity ORDER BY ts DESC LIMIT 1")
        return float(rows[0]["equity"]) if rows else 0.0

    # ── telegram minimal control (/panic, /status, /freeze, /resume) ─────
    def _telegram_listener(self) -> None:
        if not self.notifier.configured:
            log.info("telegram unconfigured — listener off")
            return
        offset = 0
        base = f"https://api.telegram.org/bot{self.notifier.token}"
        while not self._stop:
            time.sleep(4)
            try:
                r = __import__("requests").get(
                    f"{base}/getUpdates", params={"timeout": 3, "offset": offset},
                    timeout=8)
                for u in (r.json().get("result") or []):
                    offset = u["update_id"] + 1
                    msg = (u.get("message") or {}).get("text", "").strip().lower()
                    chat = (u.get("message") or {}).get("chat", {}).get("id")
                    if chat != int(self.notifier.chat_id):
                        continue
                    self._handle_tg_command(msg, base)
            except Exception as e:
                log.debug(f"tg poll error: {e}")

    def _handle_tg_command(self, msg: str, base: str) -> None:
        def reply(text):
            try:
                __import__("requests").post(
                    f"{base}/sendMessage",
                    json={"chat_id": int(self.notifier.chat_id), "text": text},
                    timeout=8)
            except Exception:
                pass
        if msg.startswith("/panic"):
            self.journal.kv_set("panic_requested", "1")
            reply("🚨 PANIC queued — flattening on next cycle.")
        elif msg.startswith("/freeze"):
            self.state_machine.set(ControlState.FROZEN, "operator", "telegram")
            reply("🥶 FROZEN — no new entries; managing existing to close.")
        elif msg.startswith("/resume"):
            self.state_machine.set(ControlState.ACTIVE, "operator", "telegram")
            reply("🙂 ACTIVE — full autonomy restored.")
        elif msg.startswith("/halt"):
            self.state_machine.set(ControlState.HALTED, "operator", "telegram")
            reply("😴 HALTED.")
        elif msg.startswith("/status"):
            hb_age = self.heartbeat.age_seconds()
            reply(f"state={self.state_machine.state.value} "
                  f"open={len(self.journal.open_trades())} "
                  f"heartbeat={hb_age and f'{hb_age:.0f}s'}")
        elif msg.startswith("/news"):
            st = self.news_guard.check()
            emoji = "📰🚨" if st.get("active") else "📰✅"
            reply(f"{emoji} news guard: "
                  f"{'ARMED — entering suppressed' if st.get('active') else 'quiet — trading normal'}"
                  f"\n{st.get('why', '')}")
        elif msg.startswith("/rent"):
            from .engine.rent_keeper import status_text
            reply(status_text(self.journal))
        elif msg.startswith("/scouts"):
            try:
                rows = self.journal.agent_accuracy(since_hours=336)
                acc = {r["agent"]: r["accuracy"] for r in rows}
                from .agents import calibration
                fits = {k for k in calibration.load()
                        if not k.startswith("_")}
                w = self.orchestrator.base_weights
                lines = []
                for a in self.orchestrator.analysts:
                    acc_s = f"{acc[a]:.0%}" if a in acc and \
                        acc[a] is not None else "—"
                    cal = " ·cal" if a in fits else ""
                    lines.append(f"{a:12s} w={w.get(a, 0):.2f} "
                                 f"acc={acc_s}{cal}")
                reply("🔭 scouts:\n" + "\n".join(lines))
            except Exception as e:
                reply(f"/scouts failed: {e}")
        elif msg.startswith("/judge"):
            try:
                from .brain.postmortem import summary_text
                reply(summary_text(self.journal))
            except Exception as e:
                reply(f"/judge failed: {e}")
        elif msg.startswith("/tv"):
            try:
                from .brain.tv_harness import TVHarness
                h = TVHarness(self.journal, self.cfg)
                st = h.health()
                emoji = {"healthy": "✅", "degraded": "⚠️",
                         "down": "❌"}.get(st["state"], "❔")
                login_hint = "" if (ROOT / "data" / "tv_profile").exists() \
                    else "\n(never logged in — run: ./venv/bin/python -m trader.brain.tv_harness --login)"
                reply(f"{emoji} TV harness: {st['state']} · "
                      f"{st['runs_today']}/{st['budget']} runs today"
                      f"{login_hint}")
            except Exception as e:
                reply(f"/tv failed: {e}")

    # ── run ──────────────────────────────────────────────────────────────
    def run(self) -> None:
        self.boot()
        interval = float(self.cfg["timeframes"]["scan_interval_seconds"])
        n = 0
        while not self._stop:
            n += 1
            t0 = time.time()
            try:
                info = self.cycle()
                dur = time.time() - t0
                bits = [f"cycle #{n}",
                        f"{info['scanned']} symbols",
                        f"{info['decisions']} decisions"]
                if info.get("entries"):
                    bits.append(f"{info['entries']} ENTRIES")
                if info.get("exits_detected"):
                    bits.append(f"{info['exits_detected']} exits")
                if info.get("panic_closed"):
                    bits.append(f"panic x{info['panic_closed']}")
                bits.append(f"eq ${info['equity']:,.0f}")
                if info.get("dd_pct"):
                    bits.append(f"dd {info['dd_pct']}%")
                bits.append(f"{dur:.1f}s")
                log.info(" · ".join(bits))
            except Exception as e:
                log.exception(f"cycle #{n} failed: {e}")
            finally:
                # ALWAYS updated, success or failure: the research thread
                # reads this to stand down while the kernel is slow, and a
                # cycle that raises must not leave the previous fast value
                # in place — a kernel that is slow AND failing would then
                # never be recognised as busy.
                self._last_cycle_s = time.time() - t0
            self._funding_cache = None                 # refresh funding hourly-ish
            time.sleep(max(1.0, interval - (time.time() - t0)))
        log.info("kernel stopped cleanly")


def main() -> None:
    ap = argparse.ArgumentParser(prog="luffy")
    ap.add_argument("--config", default=None)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--panic", action="store_true")
    args = ap.parse_args()
    cfg = load_config(args.config)
    setup_logging(cfg)
    k = Kernel(cfg)
    if args.status:
        print(json.dumps({
            "control_state": k.state_machine.state.value,
            "market": k.market_type.value,
            "heartbeat_age_s": k.heartbeat.age_seconds(),
            "open_trades": len(k.journal.open_trades()),
            "strategies": len(k.population),
        }, indent=2))
        return
    if args.panic:
        k.journal.kv_set("panic_requested", "1")
        print("panic requested — running kernel will flatten and FROZEN")
        return
    k.run()


if __name__ == "__main__":
    main()
